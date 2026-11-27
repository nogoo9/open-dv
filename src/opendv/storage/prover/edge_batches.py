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
from opendv.models.pii_tokenizer import PII_TOKEN_RE
from opendv.models.policy import CegarPolicy
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
from opendv.models.vault_ir import (
    ArrayType,
    DecimalType,
    MapType,
    PortableLogicalType,
    PredicateBranch,
    PredicateLeaf,
    PredicateNode,
)
from opendv.models.vault_ir_certified import CertifiedLinkSpec, CertifiedVaultIR, LoadSemanticsSpec
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
from opendv.storage.sql_compiler.dialect_hashing import zero_key_hex_sql


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
            if isinstance(v, str) and not PII_TOKEN_RE.fullmatch(v) and MASKED_PII_TOKEN in v:
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


def _collect_eq_hints(node: PredicateNode | None, out: dict[str, Any]) -> None:
    if node is None:
        return
    if isinstance(node, PredicateLeaf):
        if node.op == "EQ" and node.literal is not None:
            out.setdefault(node.column, node.literal)
        return
    if isinstance(node, PredicateBranch) and node.op == "AND":
        for child in node.children:
            _collect_eq_hints(child, out)


def _table_eq_hints(ir: CertifiedVaultIR) -> dict[str, dict[str, Any]]:
    hints: dict[str, dict[str, Any]] = {}
    sems: list[tuple[str, LoadSemanticsSpec | None]] = [
        (sat.source_table_id, sat.load_semantics) for sat in ir.satellites
    ]
    for sat in ir.satellites:
        for sb in sat.source_bindings:
            sems.append((sb.table_id, sb.load_semantics))
    for lnk in ir.links:
        for sb in lnk.source_bindings:
            sems.append((sb.table_id, sb.load_semantics or lnk.load_semantics))
    for tid, sem in sems:
        if sem is None:
            continue
        tbl_h = hints.setdefault(tid, {})
        if sem.require_review_status == "APPROVED":
            tbl_h.setdefault("steward_gate_status", "APPROVED")
        _collect_eq_hints(sem.filter_ast, tbl_h)
    return hints


def _synthesize_rows_for_table(
    tbl_prof: TableProfile | None,
    col_defs: list[tuple[str, PortableLogicalType, bool]],
    *,
    pii_start_idx: int,
    eq_hints: Mapping[str, Any] | None = None,
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
            if eq_hints and cname in eq_hints:
                row[cname] = eq_hints[cname]
                continue
            cp = prof_cols.get(cname)
            if cp is not None and i < len(cp.sample_values):
                sv = cp.sample_values[i]
                if sv is None and nullable:
                    row[cname] = None
                elif sv == MASKED_PII_TOKEN and ltype == "UTF8_STRING":
                    row[cname] = MASKED_PII_TOKEN
                elif sv == MASKED_PII_TOKEN and ltype == "JSON_DOCUMENT":
                    row[cname] = f'"{MASKED_PII_TOKEN}"'
                elif sv == MASKED_PII_TOKEN or (
                    isinstance(sv, str) and PII_TOKEN_RE.fullmatch(sv) and ltype != "UTF8_STRING"
                ):
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
    brk02b_ok: bool = True
    brk02b_counterexamples: list[dict[str, str | int | float | None]] = field(default_factory=list)
    pii_columns: set[str] = field(default_factory=set)


def _inject_brk02b_edge_rows(
    ir: CertifiedVaultIR,
    table_rows: dict[str, list[dict[str, Any]]],
) -> list[tuple[CertifiedLinkSpec, str]]:
    """Inject one partial-NULL BRK-02b row per nullable-optional STANDARD Link."""
    _, excluded_links, _ = planned_exclusion_sets(ir)
    bronze_by_tbl = {b.source_table_id: b for b in ir.bronze_tables}
    stg_by_tbl = {s.source_table_id: s for s in ir.staging_views}
    targets: list[tuple[CertifiedLinkSpec, str]] = []

    for lnk in ir.links:
        if lnk.link_name in excluded_links or lnk.link_kind != "STANDARD":
            continue
        hk_col = lnk.columns[0].name
        for sb in lnk.source_bindings:
            bronze = bronze_by_tbl.get(sb.table_id)
            rows = table_rows.get(sb.table_id)
            if bronze is None or not rows:
                continue
            b_cols = {c.name: c for c in bronze.columns}
            stg = stg_by_tbl.get(sb.table_id)
            recipe = sb.hash_recipe or lnk.hash_recipe
            if recipe is None and stg is not None:
                recipe = next((r for r in stg.hash_recipes if r.target_col == hk_col), None)
            specs_by_role = (
                {p.role_name: p for p in recipe.participant_specs if not p.is_degenerate}
                if recipe is not None
                else {}
            )
            opt_lp = None
            opt_cols: list[str] = []
            has_valid_other = False
            for lp in lnk.participants:
                p_spec = specs_by_role.get(lp.role_name)
                is_opt = (
                    (p_spec.is_nullable is True) if p_spec is not None else (lp.is_nullable is True)
                )
                src_cols = (
                    list(p_spec.source_columns) if p_spec is not None else list(lp.source_col_names)
                )
                if (
                    is_opt
                    and opt_lp is None
                    and src_cols
                    and all((bc := b_cols.get(c)) is not None and bc.nullable for c in src_cols)
                ):
                    opt_lp = lp
                    opt_cols = src_cols
                elif src_cols:
                    has_valid_other = True
            if opt_lp is None or not has_valid_other:
                continue

            edge_row = dict(rows[0])
            row_idx = len(rows)
            for h in ir.hubs:
                if any(hsb.table_id == sb.table_id for hsb in h.source_bindings):
                    for bk in h.business_keys:
                        if (
                            bk.table_id == sb.table_id
                            and bk.col_name in b_cols
                            and bk.col_name not in opt_cols
                        ):
                            edge_row[bk.col_name] = _coerce_sample_val(
                                None,
                                b_cols[bk.col_name].logical_type,
                                bk.col_name,
                                row_idx,
                                is_pk=True,
                            )
            for c in opt_cols:
                edge_row[c] = None
            rows.append(edge_row)
            targets.append((lnk, opt_lp.fk_col_name))
            break
    return targets


def run_adversarial_batches(
    conn: duckdb.DuckDBPyConnection,
    ir: CertifiedVaultIR,
    profiles: Sequence[SourceProfile],
    bundle: CompiledSqlBundle,
    *,
    source_rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    policy: CegarPolicy = "balanced",
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

    eq_hints_by_tbl = _table_eq_hints(ir)
    pii_idx = 1
    table_rows: dict[str, list[dict[str, Any]]] = {}
    for b in ir.bronze_tables:
        col_defs = [(c.name, c.logical_type, c.nullable) for c in b.columns]
        if source_rows_by_table and b.source_table_id in source_rows_by_table:
            rows, pii_idx = decollide_masked_pii(
                source_rows_by_table[b.source_table_id], start_index=pii_idx
            )
        else:
            rows, pii_idx = _synthesize_rows_for_table(
                prof_tables.get(b.source_table_id),
                col_defs,
                pii_start_idx=pii_idx,
                eq_hints=eq_hints_by_tbl.get(b.source_table_id),
            )
        table_rows[b.source_table_id] = rows

    brk02b_targets = (
        _inject_brk02b_edge_rows(ir, table_rows) if source_rows_by_table is None else []
    )
    for b in ir.bronze_tables:
        b_full = qualify_name("Z0", b.table_name, bundle.physical_aliases)
        _insert_rows(conn, b_full, [c.name for c in b.columns], table_rows[b.source_table_id])

    exec_statements(conn, bundle.dml_statements)

    if brk02b_targets:
        zero_00 = zero_key_hex_sql("duckdb", is_error=False)
        zero_ff = zero_key_hex_sql("duckdb", is_error=True)
        for lnk, opt_fk_col in brk02b_targets:
            lf = qualify_name(lnk.zone_id, lnk.link_name, bundle.physical_aliases)
            hk = lnk.columns[0].name
            hit = conn.execute(
                f"SELECT COUNT(*) FROM {lf} "
                f"WHERE {hk} <> {zero_00} AND {hk} <> {zero_ff} AND {opt_fk_col} = {zero_ff}"
            ).fetchone()
            if not hit or int(hit[0]) < 1:
                summary.brk02b_ok = False
                summary.brk02b_counterexamples.append(
                    {
                        "link_name": lnk.link_name,
                        "missing_fk_col": opt_fk_col,
                        "issue": "brk02b_partial_null_missing_or_zero_hk",
                    }
                )

    # Batch 2: Replay idempotency at t2 > t1 (re-execute DML without changing business payloads)
    counts_before = _table_counts(conn, ir, bundle.physical_aliases)
    exec_statements(conn, bundle.dml_statements)
    counts_after = _table_counts(conn, ir, bundle.physical_aliases)
    summary.batch2_deltas = {
        k: counts_after.get(k, 0) - counts_before.get(k, 0) for k in counts_before
    }

    # Batch 3: DuckDB SQL boundary-shift parity check + technical churn probe (DV-AP-011)
    summary.boundary_shift_ok = probe_delimiter_and_utf8_parity(conn)
    summary.empirical_smells = detect_batch3_churn(
        conn, ir, bundle, summary.pii_columns, policy=policy
    )

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
