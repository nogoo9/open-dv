"""Generalized 4-batch adversarial shadow driver and PII de-collision (`V6`).

Zero PE hardcoding; strictly Layer 2a (opendv.models, duckdb, stdlib).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import duckdb

from opendv.models.diagnostics import VerifierDiagnostic
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
from opendv.models.vault_ir import ArrayType, DecimalType, MapType, PortableLogicalType
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.storage.prover._batch_probes import (
    MASKED_PII_TOKEN,
    detect_batch3_churn,
    exec_statements,
    mask_counterexamples,
    probe_batch4_bitemporal,
    probe_delimiter_and_utf8_parity,
)
from opendv.storage.sql_compiler.compiler import CompiledSqlBundle
from opendv.storage.sql_compiler.ddl_builders import planned_exclusion_sets, qualify_name


def decollide_masked_pii(
    rows: Sequence[Mapping[str, Any]],
    *,
    start_index: int = 1,
) -> tuple[list[dict[str, Any]], int]:
    """Rewrite `<MASKED_PII>` occurrences into distinct `<MASKED_PII_#i>` tokens."""
    idx = start_index
    out: list[dict[str, Any]] = []
    for row in rows:
        new_row: dict[str, Any] = {}
        for k, v in row.items():
            if isinstance(v, str) and MASKED_PII_TOKEN in v:
                new_row[k] = v.replace(MASKED_PII_TOKEN, f"<MASKED_PII_#{idx}>")
                idx += 1
            else:
                new_row[k] = v
        out.append(new_row)
    return out, idx


def _coerce_sample_val(
    raw: str | None,
    ltype: PortableLogicalType,
    col_name: str,
    row_idx: int,
    *,
    is_pk: bool,
) -> Any:
    if raw is not None:
        if ltype == "BOOLEAN":
            return raw.strip().lower() in ("true", "1", "t", "yes")
        if ltype in ("INT32", "INT64"):
            try:
                return int(raw)
            except ValueError:
                return row_idx + 1
        if isinstance(ltype, DecimalType):
            try:
                return Decimal(raw)
            except Exception:  # noqa: BLE001
                return Decimal(f"{row_idx + 1}.00")
        if ltype == "DATE32":
            return date.fromisoformat(raw[:10])
        if ltype == "TIMESTAMP_UTC_US":
            s = raw.strip().replace(" ", "T")
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            try:
                return datetime.fromisoformat(s)
            except ValueError:
                return datetime(2026, 1, 1 + (row_idx % 20), 12, 0, 0, tzinfo=UTC)
        if isinstance(ltype, ArrayType):
            if isinstance(raw, list):
                return raw
            return [_coerce_sample_val(raw, ltype.element_type, col_name, row_idx, is_pk=False)]
        if isinstance(ltype, MapType):
            return raw if isinstance(raw, dict) else {}
        return raw

    if isinstance(ltype, ArrayType):
        return [
            _coerce_sample_val(None, ltype.element_type, f"{col_name}_el", row_idx, is_pk=False)
        ]
    if isinstance(ltype, MapType):
        k = _coerce_sample_val(None, ltype.key_type, f"{col_name}_k", row_idx, is_pk=False)
        v = _coerce_sample_val(None, ltype.value_type, f"{col_name}_v", row_idx, is_pk=False)
        return {k: v}
    if ltype == "BOOLEAN":
        return row_idx % 2 == 0
    if ltype in ("INT32", "INT64"):
        return row_idx + 1
    if isinstance(ltype, DecimalType):
        return Decimal(f"{100 + row_idx}.25")
    if ltype == "DATE32":
        return date(2026, 1, 1 + (row_idx % 20))
    if ltype == "TIMESTAMP_UTC_US":
        return datetime(2026, 1, 1 + (row_idx % 20), 12, 0, 0, tzinfo=UTC)
    if ltype == "FIXED_BINARY_32":
        return bytes([row_idx + 1]) * 32
    if ltype == "JSON_DOCUMENT":
        return f'{{"row": {row_idx}}}'
    if col_name == "steward_gate_status":
        return "APPROVED"
    if is_pk:
        return f"{col_name.upper()}_{row_idx + 1:03d}"
    return f"{col_name}_v{row_idx + 1}"


def _synthesize_rows_for_table(
    tbl_prof: TableProfile | None,
    col_defs: list[tuple[str, PortableLogicalType, bool]],
    *,
    pii_start_idx: int,
) -> tuple[list[dict[str, Any]], int]:
    prof_cols: dict[str, ColumnProfile] = (
        {c.col_name: c for c in tbl_prof.columns} if tbl_prof else {}
    )
    sample_len = max((len(c.sample_values) for c in prof_cols.values()), default=0)
    num_rows = max(3, sample_len)
    raw_rows: list[dict[str, Any]] = []
    for i in range(num_rows):
        row: dict[str, Any] = {}
        for cname, ltype, nullable in col_defs:
            cp = prof_cols.get(cname)
            if cp is not None and i < len(cp.sample_values):
                sv = cp.sample_values[i]
                if sv is None and nullable:
                    row[cname] = None
                elif sv == MASKED_PII_TOKEN and ltype == "UTF8_STRING":
                    row[cname] = MASKED_PII_TOKEN
                elif sv == MASKED_PII_TOKEN and ltype == "JSON_DOCUMENT":
                    row[cname] = f'"{MASKED_PII_TOKEN}"'
                elif sv == MASKED_PII_TOKEN:
                    row[cname] = _coerce_sample_val(None, ltype, cname, i, is_pk=cp.is_pk)
                else:
                    row[cname] = _coerce_sample_val(sv, ltype, cname, i, is_pk=cp.is_pk)
            else:
                row[cname] = _coerce_sample_val(None, ltype, cname, i, is_pk=not nullable)
        raw_rows.append(row)
    return decollide_masked_pii(raw_rows, start_index=pii_start_idx)


def _duckdb_sql_literal(val: Any) -> str:
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        if val != val or val in (float("inf"), float("-inf")):
            raise ValueError(f"Non-finite float {val!r} cannot be inserted into DuckDB")
        return repr(val)
    if isinstance(val, Decimal):
        if not val.is_finite():
            raise ValueError(f"Non-finite Decimal {val!r} cannot be inserted into DuckDB")
        return format(val, "f")
    if isinstance(val, datetime):
        dt = val.replace(tzinfo=UTC) if val.tzinfo is None else val.astimezone(UTC)
        return f"TIMESTAMPTZ '{dt.isoformat()}'"
    if isinstance(val, date):
        return f"DATE '{val.isoformat()}'"
    if isinstance(val, (bytes, bytearray, memoryview)):
        return f"FROM_HEX('{bytes(val).hex()}')"
    if isinstance(val, Mapping):
        pairs = ", ".join(
            f"{_duckdb_sql_literal(k)}: {_duckdb_sql_literal(v)}" for k, v in val.items()
        )
        return f"MAP {{{pairs}}}"
    if isinstance(val, (list, tuple)):
        return f"[{', '.join(_duckdb_sql_literal(x) for x in val)}]"
    return "'" + str(val).replace("'", "''") + "'"


def _duckdb_cell_sql(val: Any, dtype_str: str) -> str:
    if val is None:
        return "NULL"
    if isinstance(val, (list, tuple, Mapping)):
        return f"CAST({_duckdb_sql_literal(val)} AS {dtype_str})"
    dt_u = dtype_str.upper()
    if isinstance(val, str) and dt_u.startswith(
        ("DECIMAL", "TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE", "DATE", "JSON", "BLOB")
    ):
        return f"CAST({_duckdb_sql_literal(val)} AS {dtype_str})"
    if isinstance(val, Decimal):
        return f"CAST({_duckdb_sql_literal(val)} AS {dtype_str})"
    return _duckdb_sql_literal(val)


def _insert_rows(
    conn: duckdb.DuckDBPyConnection,
    full_table: str,
    cols: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    if not rows or not cols:
        return
    col_csv = ", ".join(cols)
    if any(isinstance(r.get(c), (list, tuple, Mapping)) for r in rows for c in cols):
        sel_cols = ", ".join(f'"{c}"' for c in cols)
        rel = conn.sql(f"SELECT {sel_cols} FROM {full_table} LIMIT 0")
        dtypes = [str(dt) for dt in rel.dtypes]
        for start in range(0, len(rows), 200):
            chunk = rows[start : start + 200]
            tuples_sql = ", ".join(
                "("
                + ", ".join(
                    _duckdb_cell_sql(r.get(c), dt) for c, dt in zip(cols, dtypes, strict=True)
                )
                + ")"
                for r in chunk
            )
            conn.execute(f"INSERT INTO {full_table} ({col_csv}) VALUES {tuples_sql}")
        return
    placeholders = ", ".join(["?"] * len(cols))
    sql = f"INSERT INTO {full_table} ({col_csv}) VALUES ({placeholders})"
    params = [[r.get(c) for c in cols] for r in rows]
    conn.executemany(sql, params)


def _table_counts(
    conn: duckdb.DuckDBPyConnection,
    ir: CertifiedVaultIR,
    aliases: Mapping[str, str],
) -> dict[str, int]:
    planned_hubs, excluded_links, excluded_sats = planned_exclusion_sets(ir)
    counts: dict[str, int] = {}
    for h in ir.hubs:
        if h.hub_name not in planned_hubs:
            ft = qualify_name(h.zone_id, h.hub_name, aliases)
            res = conn.execute(f"SELECT COUNT(*) FROM {ft}").fetchone()
            counts[h.hub_name] = int(res[0]) if res else 0
    for lk in ir.links:
        if lk.link_name not in excluded_links:
            ft = qualify_name(lk.zone_id, lk.link_name, aliases)
            res = conn.execute(f"SELECT COUNT(*) FROM {ft}").fetchone()
            counts[lk.link_name] = int(res[0]) if res else 0
    for sat in ir.satellites:
        if sat.sat_name not in excluded_sats:
            ft = qualify_name(sat.zone_id, sat.sat_name, aliases)
            res = conn.execute(f"SELECT COUNT(*) FROM {ft}").fetchone()
            counts[sat.sat_name] = int(res[0]) if res else 0
    return counts


@dataclass(slots=True)
class AdversarialBatchSummary:
    """Captured runtime batch observations passed into DV-DYN evaluators."""

    batch2_deltas: dict[str, int] = field(default_factory=dict)
    empirical_smells: list[VerifierDiagnostic] = field(default_factory=list)
    boundary_shift_ok: bool = True
    bitemporal_proof_ok: bool = True
    pii_columns: set[str] = field(default_factory=set)


def run_adversarial_batches(
    conn: duckdb.DuckDBPyConnection,
    ir: CertifiedVaultIR,
    profiles: Sequence[SourceProfile],
    bundle: CompiledSqlBundle,
    *,
    source_rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> AdversarialBatchSummary:
    """Execute Batches 1..4 inside isolated DuckDB and return observations."""
    summary = AdversarialBatchSummary()
    for b in ir.bronze_tables:
        for c in b.columns:
            if c.is_pii:
                summary.pii_columns.add(c.name)
    for prof in profiles:
        for tbl in prof.tables:
            for cp in tbl.columns:
                if cp.pii_tags:
                    summary.pii_columns.add(cp.col_name)

    prof_tables: dict[str, TableProfile] = {
        tbl.table_id: tbl for prof in profiles for tbl in prof.tables
    }

    # Batch 1: Execute Z0..Z4 DDL, load Z0 (real/profile rows with PII de-collision), run Z2/Z3 DML
    exec_statements(conn, bundle.ddl_statements)

    pii_idx = 1
    for b in ir.bronze_tables:
        b_full = qualify_name("Z0", b.table_name, bundle.physical_aliases)
        col_defs = [(c.name, c.logical_type, c.nullable) for c in b.columns]
        if source_rows_by_table and b.source_table_id in source_rows_by_table:
            rows, pii_idx = decollide_masked_pii(
                source_rows_by_table[b.source_table_id], start_index=pii_idx
            )
        else:
            rows, pii_idx = _synthesize_rows_for_table(
                prof_tables.get(b.source_table_id), col_defs, pii_start_idx=pii_idx
            )
        _insert_rows(conn, b_full, [c.name for c in b.columns], rows)

    exec_statements(conn, bundle.dml_statements)

    # Batch 2: Replay idempotency at t2 > t1 (re-execute DML without changing business payloads)
    counts_before = _table_counts(conn, ir, bundle.physical_aliases)
    exec_statements(conn, bundle.dml_statements)
    counts_after = _table_counts(conn, ir, bundle.physical_aliases)
    summary.batch2_deltas = {
        k: counts_after.get(k, 0) - counts_before.get(k, 0) for k in counts_before
    }

    # Batch 3: DuckDB SQL boundary-shift parity check + technical churn probe (DV-AP-011)
    summary.boundary_shift_ok = probe_delimiter_and_utf8_parity(conn)
    summary.empirical_smells = detect_batch3_churn(conn, ir, bundle, summary.pii_columns)

    # Batch 4: Real DuckDB bi-temporal applied_dts restatement probe & PIT rebuild
    summary.bitemporal_proof_ok = probe_batch4_bitemporal(conn, ir, bundle)

    return summary


__all__ = [
    "MASKED_PII_TOKEN",
    "AdversarialBatchSummary",
    "decollide_masked_pii",
    "mask_counterexamples",
    "run_adversarial_batches",
]
