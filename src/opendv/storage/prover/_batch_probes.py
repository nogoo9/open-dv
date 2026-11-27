"""Batch-3 & Batch-4 DuckDB runtime probes and counterexample PII masking (`V6`).

Strictly Layer 2a: imports only opendv.models, duckdb, stdlib, and sql_compiler.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import duckdb

from opendv.models.diagnostics import DiagnosticSeverity, VerifierDiagnostic
from opendv.models.ir_kernel import execute_hash_recipe
from opendv.models.pii_tokenizer import MASKED_PII_TOKEN
from opendv.models.policy import CEGAR_POLICY_SPECS, CegarPolicy
from opendv.models.vault_ir import DecimalType, PortableLogicalType
from opendv.models.vault_ir_certified import CertifiedVaultIR, HashRecipeSpec
from opendv.storage.sql_compiler._ddl_common import (
    planned_exclusion_sets,
    qualify_name,
    resolve_pit_bridge_columns,
)
from opendv.storage.sql_compiler._sat_dml import build_sat_load_dml
from opendv.storage.sql_compiler._scalar_exprs import predicate_to_sqlglot
from opendv.storage.sql_compiler._z3_z4_ddl import build_pit_bridge_ddl
from opendv.storage.sql_compiler.compiler import CompiledSqlBundle
from opendv.storage.sql_compiler.dialect_hashing import compile_hash_recipe_sql, zero_key_hex_sql

_TECH_CHURN_RE = re.compile(
    r"(?i)(?:^|_)(?:etl_snapshot_ts|snapshot_ts|ingest_ts|ingestion_ts|load_ts|"
    r"etl_batch_id|batch_id|source_row_etag|etag|row_hash|updated_at|modified_at|created_at)$"
)


def mask_counterexamples(
    rows: Sequence[Mapping[str, Any]],
    pii_columns: set[str],
    *,
    limit: int = 5,
) -> list[dict[str, str | int | float | None]]:
    """Mask PII fields in diagnostic counterexample rows and normalize JSON types."""
    out: list[dict[str, str | int | float | None]] = []
    for row in list(rows)[:limit]:
        masked: dict[str, str | int | float | None] = {}
        for k, v in row.items():
            if k in pii_columns and v is not None:
                masked[k] = MASKED_PII_TOKEN
            elif v is None or isinstance(v, (str, int, float)):
                masked[k] = v
            elif isinstance(v, datetime):
                dt = v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
                masked[k] = dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            elif isinstance(v, (bytes, bytearray, memoryview)):
                masked[k] = bytes(v).hex()
            else:
                masked[k] = str(v)
        out.append(masked)
    return out


def exec_statements(conn: duckdb.DuckDBPyConnection, stmts: Sequence[str]) -> None:
    """Execute a sequence of SQL statements against DuckDB."""
    for s in stmts:
        conn.execute(s)


def _scalar_count(conn: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row else 0


def _bump_staging_clock(
    conn: duckdb.DuckDBPyConnection,
    ir: CertifiedVaultIR,
    aliases: Mapping[str, str],
    source_table_id: str,
) -> str:
    stg = next((s for s in ir.staging_views if s.source_table_id == source_table_id), None)
    if stg is None:
        return ""
    stg_full = qualify_name("Z1", stg.view_name, aliases)
    row_def = conn.execute(
        "SELECT sql FROM duckdb_views() WHERE schema_name = 'staging' AND view_name = ?",
        [stg_full.split(".")[-1]],
    ).fetchone()
    orig_sql = str(row_def[0]) if row_def else ""
    if "CURRENT_TIMESTAMP" in orig_sql:
        bumped = orig_sql.replace("CURRENT_TIMESTAMP", "(CURRENT_TIMESTAMP + INTERVAL '1 hour')")
        conn.execute(bumped.replace("CREATE VIEW ", "CREATE OR REPLACE VIEW ", 1))
        return orig_sql
    return ""


def _restore_staging_clock(conn: duckdb.DuckDBPyConnection, orig_sql: str) -> None:
    if orig_sql:
        conn.execute(orig_sql.replace("CREATE VIEW ", "CREATE OR REPLACE VIEW ", 1))


def detect_batch3_churn(
    conn: duckdb.DuckDBPyConnection,
    ir: CertifiedVaultIR,
    bundle: CompiledSqlBundle,
    pii_cols: set[str],
    *,
    policy: CegarPolicy = "balanced",
) -> list[VerifierDiagnostic]:
    """Batch 3 DV-AP-011 probe: advance technical columns with unchanged business attrs."""
    smells: list[VerifierDiagnostic] = []
    _, _, excluded_sats = planned_exclusion_sets(ir)
    bronze_by_tbl = {b.source_table_id: b for b in ir.bronze_tables}
    stg_by_tbl = {s.source_table_id: s for s in ir.staging_views}
    is_blocking = "DV-AP-011" in CEGAR_POLICY_SPECS[policy].blocking_v6b_rules
    churn_sev: DiagnosticSeverity = "WARN" if is_blocking else "ADVISORY"

    for sat in ir.satellites:
        if sat.sat_name in excluded_sats or sat.sat_kind == "ESAT" or sat.hash_recipe is None:
            continue
        tech_cols = [c for c in sat.hash_recipe.source_columns if _TECH_CHURN_RE.search(c)]
        if not tech_cols:
            continue
        bronze = bronze_by_tbl.get(sat.source_table_id)
        stg = stg_by_tbl.get(sat.source_table_id)
        if bronze is None or stg is None:
            continue
        b_full = qualify_name("Z0", bronze.table_name, bundle.physical_aliases)
        s_full = qualify_name(sat.zone_id, sat.sat_name, bundle.physical_aliases)

        before_cnt = _scalar_count(conn, f"SELECT COUNT(*) FROM {s_full}")
        staged_cnt = _scalar_count(conn, f"SELECT COUNT(*) FROM {b_full}")
        if staged_cnt == 0:
            continue

        for tc in tech_cols:
            fspec = next((c for c in bronze.columns if c.name == tc), None)
            if fspec is not None:
                expr = (
                    _bump_time_sql(
                        tc,
                        fspec.logical_type,
                        interval_sql="INTERVAL '1 hour'",
                        int_delta=1,
                        fallback_suffix="_v2",
                    )
                    if fspec.logical_type in ("TIMESTAMP_UTC_US", "INT32", "INT64")
                    else f"COALESCE({tc}, '') || '_v2'"
                )
                conn.execute(f"UPDATE {b_full} SET {tc} = {expr}")

        has_z0_dts = (
            any(c.name == "load_dts" for c in bronze.columns) and "load_dts" not in tech_cols
        )
        orig_sql = ""
        if has_z0_dts:
            ld_col = next(c for c in bronze.columns if c.name == "load_dts")
            ld_expr = _bump_time_sql(
                "load_dts",
                ld_col.logical_type,
                interval_sql="INTERVAL '1 hour'",
                int_delta=1,
                fallback_suffix="_v2",
            )
            conn.execute(f"UPDATE {b_full} SET load_dts = {ld_expr}")
        else:
            orig_sql = _bump_staging_clock(conn, ir, bundle.physical_aliases, sat.source_table_id)

        sat_dml = build_sat_load_dml(
            sat, stg, ir, bundle.dialect_profile, bundle.physical_aliases, bundle.stg_hd_col_map
        )
        exec_statements(conn, [sat_dml])
        _restore_staging_clock(conn, orig_sql)

        after_cnt = _scalar_count(conn, f"SELECT COUNT(*) FROM {s_full}")
        delta = max(0, after_cnt - before_cnt)
        ratio = delta / staged_cnt
        if ratio > 0.95:
            rel_s = conn.sql(f"SELECT * FROM {b_full} LIMIT 5")
            proj_s = [
                (
                    f'STRFTIME("{c}" AT TIME ZONE \'UTC\', \'%Y-%m-%dT%H:%M:%S.%fZ\') AS "{c}"'
                    if (dt_u := str(dt).upper()) in ("TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ")
                    else (
                        f'CAST("{c}" AS VARCHAR) AS "{c}"'
                        if "TIME ZONE" in dt_u or "TIMESTAMP" in dt_u
                        else f'"{c}"'
                    )
                )
                for c, dt in zip(rel_s.columns, rel_s.dtypes, strict=True)
            ]
            sample_q = conn.execute(
                f"SELECT {', '.join(proj_s)} FROM (SELECT * FROM {b_full} LIMIT 5) AS _sq"
            )
            col_names = [d[0] for d in (sample_q.description or ())]
            raw_samples = [dict(zip(col_names, r, strict=True)) for r in sample_q.fetchall()]
            smells.append(
                VerifierDiagnostic(
                    gate="V6",
                    rule_id="DV-AP-011",
                    severity=churn_sev,
                    entity_name=sat.sat_name,
                    waivable=True,
                    suggested_waiver_id=f"WAIV-AP011-{sat.sat_name}",
                    message=(
                        f"Technical churn probe on {sat.sat_name}: columns {tech_cols} caused "
                        f"{delta}/{staged_cnt} ({ratio * 100:.1f}%) spurious inserts (>95%)."
                    ),
                    counterexample_rows=mask_counterexamples(raw_samples, pii_cols),
                )
            )
    return smells


def probe_delimiter_and_utf8_parity(conn: duckdb.DuckDBPyConnection) -> bool:
    """Verify Batch-3 boundary-shift, NULL-shift, leading-zero, and UTF-8 vectors in DuckDB SQL."""
    recipe = HashRecipeSpec(
        target_col="hk_probe",
        recipe_kind="HUB_HK",
        bkcc_literal="GLOBAL",
        source_columns=["col_a", "col_b"],
        sort_alphabetically=False,
        key_columns=["col_a", "col_b"],
    )
    col_types: dict[str, PortableLogicalType] = {"col_a": "UTF8_STRING", "col_b": "UTF8_STRING"}
    expr_sql = compile_hash_recipe_sql(
        recipe, "duckdb", key_tier="TIER_1_GLOBAL_STANDARD", col_types=col_types
    )
    vectors: list[tuple[str | None, str | None]] = [
        ("ANNE||O", "BRIEN"),
        ("ANNE", "O||BRIEN"),
        ("AB", "C"),
        ("A", "BC"),
        (None, "X"),
        ("-1:^^", "X"),
        ("0000909100", None),
        ("909100", None),
        ("Nordisk Suverän Fond", None),
    ]
    values_sql = ", ".join(["(CAST(? AS VARCHAR), CAST(? AS VARCHAR))"] * len(vectors))
    params: list[str | None] = [val for pair in vectors for val in pair]
    sql = f"SELECT LOWER(HEX({expr_sql})) FROM (VALUES {values_sql}) AS _v(col_a, col_b)"
    sql_hashes = [str(r[0]).lower() for r in conn.execute(sql, params).fetchall()]
    expected = [
        execute_hash_recipe(
            recipe,
            {"col_a": a, "col_b": b},
            key_tier="TIER_1_GLOBAL_STANDARD",
            column_types=col_types,
        )
        for a, b in vectors
    ]
    return (
        len(sql_hashes) == len(vectors)
        and len(set(sql_hashes)) == len(vectors)
        and sql_hashes == expected
    )


def _bump_time_sql(
    expr: str,
    ltype: PortableLogicalType,
    *,
    interval_sql: str,
    int_delta: int,
    fallback_suffix: str,
) -> str:
    """Return SQL expression advancing a date, timestamp, integer, or ISO string time value."""
    if ltype == "DATE32":
        return f"CAST(COALESCE({expr}, DATE '2026-01-01') + {interval_sql} AS DATE)"
    if ltype == "TIMESTAMP_UTC_US":
        return f"COALESCE({expr}, TIMESTAMPTZ '2026-01-01 00:00:00Z') + {interval_sql}"
    if ltype in ("INT32", "INT64") or isinstance(ltype, DecimalType):
        return f"COALESCE({expr}, 0) + {int_delta}"
    return (
        f"(CASE WHEN POSITION(':' IN COALESCE(CAST({expr} AS VARCHAR), '')) = 0 "
        f"AND TRY_CAST({expr} AS DATE) IS NOT NULL "
        f"THEN STRFTIME(TRY_CAST({expr} AS DATE) + {interval_sql}, '%Y-%m-%d') "
        f"WHEN TRY_CAST({expr} AS TIMESTAMP) IS NOT NULL "
        f"THEN CAST(TRY_CAST({expr} AS TIMESTAMP) + {interval_sql} AS VARCHAR) "
        f"ELSE COALESCE(CAST({expr} AS VARCHAR), '2026-01-01') || '{fallback_suffix}' END)"
    )


def _mutate_col_sql(col_name: str, ltype: PortableLogicalType) -> str:
    if ltype in ("INT32", "INT64") or isinstance(ltype, DecimalType):
        return f"COALESCE({col_name}, 0) + 1"
    if ltype == "UTF8_STRING":
        return f"COALESCE({col_name}, '') || '_restated'"
    if ltype == "DATE32":
        return f"CAST(COALESCE({col_name}, DATE '2026-01-01') + INTERVAL '1 day' AS DATE)"
    if ltype == "TIMESTAMP_UTC_US":
        return f"COALESCE({col_name}, TIMESTAMPTZ '2026-01-01 00:00:00Z') + INTERVAL '1 day'"
    return f"NOT COALESCE({col_name}, FALSE)"


def probe_batch4_bitemporal(
    conn: duckdb.DuckDBPyConnection,
    ir: CertifiedVaultIR,
    bundle: CompiledSqlBundle,
) -> bool:
    """Batch 4: insert +31d applied_dts restatement in Z0, rebuild PIT DDL, re-run DML & verify."""
    planned_hubs, _, excluded_sats = planned_exclusion_sets(ir)
    bitemp_sats = [
        s
        for s in ir.satellites
        if (
            s.sat_name not in excluded_sats
            and s.sat_kind != "ESAT"
            and s.is_bitemporal
            and s.applied_dts_col
        )
    ]
    if not bitemp_sats:
        return True

    protected_cols = {bk.col_name for h in ir.hubs for bk in h.business_keys} | {
        dk for lk in ir.links for dk in lk.degenerate_keys
    }
    bronze_by_tbl = {b.source_table_id: b for b in ir.bronze_tables}
    zero_00 = zero_key_hex_sql("duckdb", is_error=False)

    for sat in bitemp_sats:
        bronze = bronze_by_tbl.get(sat.source_table_id)
        app_col = sat.applied_dts_col
        if bronze is None or app_col is None:
            return False
        col_by_name = {c.name: c for c in bronze.columns}
        if app_col not in col_by_name:
            return False

        b_full = qualify_name("Z0", bronze.table_name, bundle.physical_aliases)
        s_full = qualify_name(sat.zone_id, sat.sat_name, bundle.physical_aliases)
        p_hk = sat.columns[0].name
        if _scalar_count(conn, f"SELECT COUNT(*) FROM {b_full}") == 0:
            continue

        before_cnt = _scalar_count(conn, f"SELECT COUNT(*) FROM {s_full}")

        cand_names = (
            list(sat.hash_recipe.source_columns)
            if sat.hash_recipe is not None
            else [a.name for a in sat.attributes]
        )
        mut_col = next(
            (
                cn
                for cn in cand_names
                if cn in col_by_name
                and cn not in protected_cols
                and cn not in (app_col, "load_dts")
                and (
                    col_by_name[cn].logical_type
                    in ("INT32", "INT64", "UTF8_STRING", "DATE32", "TIMESTAMP_UTC_US", "BOOLEAN")
                    or isinstance(col_by_name[cn].logical_type, DecimalType)
                )
            ),
            None,
        )

        sel_exprs: list[str] = []
        for c in bronze.columns:
            if c.name == app_col:
                sel_exprs.append(
                    _bump_time_sql(
                        f"(SELECT MAX({app_col}) FROM {b_full})",
                        c.logical_type,
                        interval_sql="INTERVAL '31 days'",
                        int_delta=31,
                        fallback_suffix="_next",
                    )
                )
            elif c.name == "load_dts":
                sel_exprs.append(
                    _bump_time_sql(
                        f"(SELECT MAX(load_dts) FROM {b_full})",
                        c.logical_type,
                        interval_sql="INTERVAL '1 hour'",
                        int_delta=1,
                        fallback_suffix="_v2",
                    )
                )
            elif c.name == mut_col:
                sel_exprs.append(_mutate_col_sql(c.name, c.logical_type))
            else:
                sel_exprs.append(c.name)

        sat_sb = next(
            (b for b in sat.source_bindings if b.table_id == sat.source_table_id),
            sat.source_bindings[0] if sat.source_bindings else None,
        )
        sem = (sat_sb.load_semantics if sat_sb else None) or sat.load_semantics
        b4_filters: list[str] = []
        if sem and sem.filter_ast is not None:
            b4_filters.append(predicate_to_sqlglot(sem.filter_ast).sql(dialect="duckdb"))
        if sem and sem.require_review_status == "APPROVED" and "steward_gate_status" in col_by_name:
            b4_filters.append("steward_gate_status = 'APPROVED'")
        b4_where = f" WHERE {' AND '.join(b4_filters)}" if b4_filters else ""

        col_csv = ", ".join(c.name for c in bronze.columns)
        conn.execute(
            f"INSERT INTO {b_full} ({col_csv}) SELECT {', '.join(sel_exprs)} "
            f"FROM (SELECT * FROM {b_full}{b4_where} ORDER BY {app_col} ASC LIMIT 1) AS _b4_base"
        )

        orig_sql = ""
        if "load_dts" not in col_by_name:
            orig_sql = _bump_staging_clock(conn, ir, bundle.physical_aliases, sat.source_table_id)

        for pb in ir.pit_bridges:
            if (
                pb.object_kind == "PIT"
                and pb.root_hub not in planned_hubs
                and sat.sat_name in pb.target_satellites_or_links
            ):
                exec_statements(
                    conn,
                    build_pit_bridge_ddl(pb, ir, bundle.dialect_profile, bundle.physical_aliases),
                )

        exec_statements(conn, bundle.dml_statements)
        _restore_staging_clock(conn, orig_sql)

        after_cnt = _scalar_count(conn, f"SELECT COUNT(*) FROM {s_full}")
        if mut_col is not None and after_cnt <= before_cnt:
            return False

        sat_hit = _scalar_count(
            conn,
            f"SELECT COUNT(*) FROM {s_full} WHERE {p_hk} <> {zero_00} "
            f"AND {app_col} = (SELECT MAX({app_col}) FROM {b_full})",
        )
        if sat_hit < 1:
            return False

        for pb in ir.pit_bridges:
            if (
                pb.object_kind != "PIT"
                or pb.root_hub in planned_hubs
                or sat.sat_name not in pb.target_satellites_or_links
            ):
                continue
            pf = qualify_name("Z3", pb.object_name, bundle.physical_aliases)
            _, _, targets = resolve_pit_bridge_columns(
                pb, ir, bundle.dialect_profile.max_ident_bytes
            )
            tgt_info = next((t for t in targets if t[0] == sat.sat_name), None)
            if tgt_info is None:
                return False
            _, hk_col, ldts_col, pit_app_col, app_ltype = tgt_info
            cast_ts = app_ltype in ("DATE32", "UTF8_STRING")
            snap_max = (
                f"TRY_CAST((SELECT MAX({app_col}) FROM {b_full}) AS TIMESTAMPTZ)"
                if cast_ts
                else f"(SELECT MAX({app_col}) FROM {b_full})"
            )
            s_app_expr = f"TRY_CAST(s.{app_col} AS TIMESTAMPTZ)" if cast_ts else f"s.{app_col}"
            pit_app_chk = f" AND p.{pit_app_col} = s.{app_col}" if pit_app_col else ""
            pit_hit = _scalar_count(
                conn,
                f"SELECT COUNT(*) FROM {pf} AS p INNER JOIN {s_full} AS s "
                f"ON s.{p_hk} = p.{hk_col} AND s.load_dts = p.{ldts_col} "
                f"WHERE p.snapshot_dts = {snap_max} AND p.{hk_col} <> {zero_00} "
                f"AND {s_app_expr} = p.snapshot_dts{pit_app_chk}",
            )
            if pit_hit < 1:
                return False

    return True


__all__ = [
    "MASKED_PII_TOKEN",
    "detect_batch3_churn",
    "exec_statements",
    "mask_counterexamples",
    "probe_batch4_bitemporal",
    "probe_delimiter_and_utf8_parity",
]
