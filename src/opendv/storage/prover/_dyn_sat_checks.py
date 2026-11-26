"""Satellite & ESAT & MAS dynamic invariant checks (`DV-DYN-004, 005, 006, 008, 011, 013`).

Strictly Layer 2a: imports only opendv.models, duckdb, ddl_builders, dialect_hashing, _scalar_exprs.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

import duckdb

from opendv.models.modeling_preferences import SURROGATE_BK_RE
from opendv.models.sources import ColumnProfile
from opendv.models.vault_ir import ArrayType, MapType
from opendv.models.vault_ir_certified import (
    CertifiedHubSpec,
    CertifiedLinkSpec,
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    LoadSemanticsSpec,
    SourceBindingSpec,
    StagingViewSpec,
)
from opendv.storage.prover.edge_batches import mask_counterexamples
from opendv.storage.sql_compiler._scalar_exprs import predicate_to_sqlglot
from opendv.storage.sql_compiler.ddl_builders import planned_exclusion_sets, qualify_name
from opendv.storage.sql_compiler.dialect_hashing import zero_key_hex_sql


def _sat_sem_and_where(
    sat: CertifiedSatelliteSpec,
    stg: StagingViewSpec,
    p_hk: str,
    zero_00: str,
    zero_ff: str,
    alias: str = "src",
) -> tuple[SourceBindingSpec | None, LoadSemanticsSpec | None, str]:
    sat_sb = next(
        (b for b in sat.source_bindings if b.table_id == stg.source_table_id),
        sat.source_bindings[0] if sat.source_bindings else None,
    )
    sem = (sat_sb.load_semantics if sat_sb else None) or sat.load_semantics
    where_parts = [f"{alias}.{p_hk} <> {zero_00}", f"{alias}.{p_hk} <> {zero_ff}"]
    if sem and sem.filter_ast is not None:
        where_parts.append(
            predicate_to_sqlglot(sem.filter_ast, table_alias=alias).sql(dialect="duckdb")
        )
    if sem and sem.require_review_status == "APPROVED":
        where_parts.append(f"{alias}.steward_gate_status = 'APPROVED'")
    return sat_sb, sem, " AND ".join(where_parts)


def query_counterexamples(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    pii_cols: set[str],
) -> list[dict[str, str | int | float | None]]:
    """Execute counterexample query and mask PII columns."""
    rel = conn.sql(sql)
    proj = [
        (
            f"STRFTIME(\"{c}\" AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS \"{c}\""
            if (dt_u := str(dt).upper()) in ("TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ")
            else (f'CAST("{c}" AS VARCHAR) AS "{c}"' if "TIME ZONE" in dt_u else f'"{c}"')
        )
        for c, dt in zip(rel.columns, rel.dtypes, strict=True)
    ]
    cur = conn.execute(f"SELECT {', '.join(proj)} FROM ({sql}) AS _ce_q LIMIT 5")
    cols = [d[0] for d in (cur.description or ())]
    rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    return mask_counterexamples(rows, pii_cols, limit=5)


def check_satellite_invariants(
    conn: duckdb.DuckDBPyConnection,
    ir: CertifiedVaultIR,
    aliases: Mapping[str, str],
    hd_col_map: Mapping[tuple[str, str], str],
    hubs_by_name: Mapping[str, CertifiedHubSpec],
    links_by_name: Mapping[str, CertifiedLinkSpec],
    pii_cols: set[str],
    fail_fn: Callable[[str, list[dict[str, str | int | float | None]]], None],
    *,
    skip_rules: frozenset[str] = frozenset(),
) -> None:
    """Evaluate DV-DYN-004, 005, 006, 008, 011, 013 across all non-planned Satellites."""
    _, _, excluded_sats = planned_exclusion_sets(ir)
    stg_by_tbl = {s.source_table_id: s for s in ir.staging_views}
    zero_00 = zero_key_hex_sql("duckdb", is_error=False)
    zero_ff = zero_key_hex_sql("duckdb", is_error=True)

    for sat in ir.satellites:
        if sat.sat_name in excluded_sats:
            continue
        sf = qualify_name(sat.zone_id, sat.sat_name, aliases)
        p_hk = sat.columns[0].name
        eff_order = (
            f"{sat.applied_dts_col} ASC, load_dts ASC"
            if (sat.is_bitemporal and sat.applied_dts_col)
            else "load_dts ASC"
        )
        eff_desc = (
            f"{sat.applied_dts_col} DESC, load_dts DESC"
            if (sat.is_bitemporal and sat.applied_dts_col)
            else "load_dts DESC"
        )

        is_mas = sat.sat_kind == "MAS" or bool(sat.child_dependent_keys)
        if is_mas:
            cdks = sat.child_dependent_keys or ["load_dts"]
            g_cols = ", ".join([p_hk, "load_dts", *cdks])
            dups = query_counterexamples(
                conn,
                f"SELECT {g_cols}, COUNT(*) AS cnt FROM {sf} GROUP BY {g_cols} HAVING COUNT(*) > 1",
                pii_cols,
            )
            if dups:
                fail_fn("DV-DYN-005", dups)
        else:
            if "DV-DYN-004" not in skip_rules:
                bitemp_c = (
                    f", {sat.applied_dts_col}"
                    if (sat.is_bitemporal and sat.applied_dts_col)
                    else ""
                )
                g_cols = f"{p_hk}, load_dts{bitemp_c}"
                dups = query_counterexamples(
                    conn,
                    f"SELECT {g_cols}, COUNT(*) AS cnt FROM {sf} "
                    f"GROUP BY {g_cols} HAVING COUNT(*) > 1",
                    pii_cols,
                )
                if dups:
                    fail_fn("DV-DYN-004", dups)
                stg = stg_by_tbl.get(sat.source_table_id)
                if sat.sat_kind == "STANDARD" and stg is not None and sat.hash_recipe is not None:
                    _, sem, stg_where = _sat_sem_and_where(sat, stg, p_hk, zero_00, zero_ff)
                    if not (sem and sem.dedup_order_cols):
                        stg_f = qualify_name("Z1", stg.view_name, aliases)
                        hd_c = hd_col_map.get((stg.view_name, sat.sat_name), "hash_diff")
                        stg_g_cols = f"src.{p_hk}, src.load_dts" + (
                            f", src.{sat.applied_dts_col}"
                            if (sat.is_bitemporal and sat.applied_dts_col)
                            else ""
                        )
                        conflicts = query_counterexamples(
                            conn,
                            f"SELECT src.{p_hk} AS parent_hk, "
                            f"COUNT(DISTINCT src.{hd_c}) AS distinct_variants "
                            f"FROM {stg_f} AS src WHERE {stg_where} "
                            f"GROUP BY {stg_g_cols} HAVING COUNT(DISTINCT src.{hd_c}) > 1",
                            pii_cols,
                        )
                        if conflicts:
                            fail_fn("DV-DYN-004", conflicts)

        # DV-DYN-006: Sat-to-Parent RI
        p_ent_h = hubs_by_name.get(sat.parent_name)
        p_ent_l = links_by_name.get(sat.parent_name)
        if "DV-DYN-006" not in skip_rules:
            if p_ent_h is not None:
                pf, pk_c = (
                    qualify_name(p_ent_h.zone_id, p_ent_h.hub_name, aliases),
                    p_ent_h.columns[0].name,
                )
            elif p_ent_l is not None:
                pf, pk_c = (
                    qualify_name(p_ent_l.zone_id, p_ent_l.link_name, aliases),
                    p_ent_l.columns[0].name,
                )
            else:
                pf, pk_c = "", ""
            if pf:
                orphans = query_counterexamples(
                    conn,
                    f"SELECT s.{p_hk} AS orphan_hk FROM {sf} AS s LEFT JOIN {pf} AS p "
                    f"ON s.{p_hk} = p.{pk_c} WHERE p.{pk_c} IS NULL",
                    pii_cols,
                )
                if orphans:
                    fail_fn("DV-DYN-006", orphans)

        # DV-DYN-008: Consecutive hash_diff true-delta historization
        if sat.sat_kind != "ESAT" and sat.hash_recipe is not None:
            part_cols = [p_hk, *sat.child_dependent_keys] if is_mas else [p_hk]
            part_csv = ", ".join(part_cols)
            red = query_counterexamples(
                conn,
                f"SELECT {p_hk} AS parent_hk, load_dts FROM ("
                f"SELECT {p_hk}, load_dts, hash_diff, "
                f"LAG(hash_diff) OVER (PARTITION BY {part_csv} ORDER BY {eff_order}) AS _prev "
                f"FROM {sf} WHERE {p_hk} <> {zero_00}) AS w WHERE _prev = hash_diff",
                pii_cols,
            )
            if red:
                fail_fn("DV-DYN-008", red)

        # DV-DYN-013: ESAT single active relationship per driving key
        if sat.sat_kind == "ESAT":
            dks = list(sat.driving_keys)
            if not dks:
                fail_fn("DV-DYN-013", [{"entity": sat.sat_name, "issue": "missing_driving_keys"}])
            else:
                sat_cols = {c.name for c in sat.columns}
                missing_dks = [dk for dk in dks if dk not in sat_cols]
                lnk_cols = {c.name for c in p_ent_l.columns} if p_ent_l is not None else set()
                if missing_dks and (
                    p_ent_l is None or any(dk not in lnk_cols for dk in missing_dks)
                ):
                    fail_fn(
                        "DV-DYN-013",
                        [
                            {
                                "entity": sat.sat_name,
                                "issue": f"unresolved_driving_keys:{missing_dks}",
                            }
                        ],
                    )
                else:
                    dk_csv = ", ".join(dks)
                    if missing_dks and p_ent_l is not None:
                        lf = qualify_name(p_ent_l.zone_id, p_ent_l.link_name, aliases)
                        l_hk = p_ent_l.columns[0].name
                        dk_sel = ", ".join(
                            f"s.{dk}" if dk in sat_cols else f"l.{dk} AS {dk}" for dk in dks
                        )
                        dk_part = ", ".join(
                            f"s.{dk}" if dk in sat_cols else f"l.{dk}" for dk in dks
                        )
                        s_eff_desc = (
                            f"s.{sat.applied_dts_col} DESC, s.load_dts DESC"
                            if (sat.is_bitemporal and sat.applied_dts_col)
                            else "s.load_dts DESC"
                        )
                        src_sql = (
                            f"SELECT {dk_sel}, s.{p_hk}, "
                            f"RANK() OVER (PARTITION BY {dk_part} ORDER BY {s_eff_desc}) AS _rn "
                            f"FROM {sf} AS s INNER JOIN {lf} AS l ON l.{l_hk} = s.{p_hk} "
                            f"WHERE s.{p_hk} <> {zero_00} AND s.{p_hk} <> {zero_ff}"
                        )
                    else:
                        src_sql = (
                            f"SELECT {dk_csv}, {p_hk}, "
                            f"RANK() OVER (PARTITION BY {dk_csv} ORDER BY {eff_desc}) AS _rn "
                            f"FROM {sf} WHERE {p_hk} <> {zero_00} AND {p_hk} <> {zero_ff}"
                        )
                    bad_esat = query_counterexamples(
                        conn,
                        f"SELECT {dk_csv}, COUNT(DISTINCT {p_hk}) AS active_links "
                        f"FROM ({src_sql}) AS cur "
                        f"WHERE cur._rn = 1 GROUP BY {dk_csv} HAVING COUNT(DISTINCT {p_hk}) > 1",
                        pii_cols,
                    )
                    if bad_esat:
                        fail_fn("DV-DYN-013", bad_esat)

        # DV-DYN-011: Staging-to-Satellite current-state reconciliation
        if sat.sat_kind != "ESAT" and sat.hash_recipe is not None:
            stg = stg_by_tbl.get(sat.source_table_id)
            if stg is None:
                continue
            stg_f = qualify_name("Z1", stg.view_name, aliases)
            hd_c = hd_col_map.get((stg.view_name, sat.sat_name), "hash_diff")
            part_cols = [p_hk, *sat.child_dependent_keys] if is_mas else [p_hk]
            join_cond = " AND ".join(f"stg.{c} = sat.{c}" for c in part_cols)
            part_csv = ", ".join(part_cols)
            sat_sb, sem, stg_where = _sat_sem_and_where(sat, stg, p_hk, zero_00, zero_ff)
            sat_col_maps = sat_sb.col_mappings if sat_sb else {}
            dedup_cols = (
                [sat_col_maps.get(c, c) for c in sem.dedup_order_cols]
                if (sem and sem.dedup_order_cols)
                else []
            )
            stg_order = (
                f"{eff_desc}, {', '.join(f'{c} DESC' for c in dedup_cols)}"
                if dedup_cols
                else eff_desc
            )
            mismatch = query_counterexamples(
                conn,
                f"SELECT stg.{p_hk} AS parent_hk FROM ("
                f"SELECT {part_csv}, {hd_c} AS stg_hd, "
                f"ROW_NUMBER() OVER (PARTITION BY {part_csv} ORDER BY {stg_order}) AS _rn "
                f"FROM {stg_f} AS src WHERE {stg_where}) AS stg "
                f"LEFT JOIN ("
                f"SELECT {part_csv}, hash_diff AS sat_hd, "
                f"ROW_NUMBER() OVER (PARTITION BY {part_csv} ORDER BY {eff_desc}) AS _rn "
                f"FROM {sf} WHERE {p_hk} <> {zero_00}) AS sat "
                f"ON {join_cond} AND sat._rn = 1 "
                f"WHERE stg._rn = 1 AND (sat.sat_hd IS NULL OR sat.sat_hd <> stg.stg_hd)",
                pii_cols,
            )
            if mismatch:
                fail_fn("DV-DYN-011", mismatch)


_TEMPORAL_COL_RE = re.compile(
    r"(?i)(?:^|_)(?:load_dts|applied_dts|effective_from|effective_to|"
    r"valid_from|valid_to|created_at|updated_at|modified_at|deleted_at|ts|dts|date|timestamp)$"
)
_SKIP_CDK_TYPES = ("DATE32", "TIMESTAMP_UTC_US", "FIXED_BINARY_32", "BOOLEAN", "JSON_DOCUMENT")


def _scalar_count(conn: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row else 0


def _is_unique_with_col(
    conn: duckdb.DuckDBPyConnection, table_sql: str, col: str, grp_sql: str, agg_expr: str
) -> bool:
    if _scalar_count(conn, f"SELECT COUNT({col}) FROM {table_sql}") == 0:
        return False
    q = (
        f"SELECT COUNT(*) FROM (SELECT 1 FROM {table_sql} "
        f"GROUP BY {grp_sql}, {col} HAVING {agg_expr} > 1)"
    )
    return _scalar_count(conn, q) == 0


def find_candidate_cdk(
    conn: duckdb.DuckDBPyConnection,
    sat: CertifiedSatelliteSpec,
    sf: str,
    g_cols: str,
    stg: StagingViewSpec | None,
    stg_ctx: tuple[str, str, str, str],
    dup_flags: tuple[bool, bool],
    col_profs: Mapping[str, ColumnProfile],
) -> str | None:
    """Find a non-temporal, non-surrogate column restoring Satellite uniqueness."""
    stg_f, stg_g, stg_w, hd_c = stg_ctx
    sat_dup, stg_dup = dup_flags
    sat_sb = (
        next((b for b in sat.source_bindings if b.table_id == stg.source_table_id), None)
        if stg
        else None
    )
    cmaps = sat_sb.col_mappings if sat_sb else {}
    stg_names = (
        {f.name for f in stg.projected_fields}
        | set(stg.col_lineage_mappings)
        | {r.target_col for r in stg.hash_recipes}
        if stg
        else set()
    )
    sys_cols = {sat.columns[0].name, "load_dts", "rec_src", "hash_diff"} | (
        {sat.applied_dts_col} if sat.applied_dts_col else set()
    )
    sat_cols = {c.name for c in sat.columns}
    for a in sat.attributes:
        if (
            a.name in sys_cols
            or _TEMPORAL_COL_RE.search(a.name)
            or a.logical_type in _SKIP_CDK_TYPES
            or isinstance(a.logical_type, (ArrayType, MapType))
            or SURROGATE_BK_RE.match(a.name)
            or ((cp := col_profs.get(a.name)) is not None and cp.surrogate_score >= 0.75)
        ):
            continue
        if stg_dup and stg is not None:
            sc = a.name if a.name in stg_names else cmaps.get(a.name, a.name)
            if (sc not in stg_names and sc not in cmaps.values()) or not _is_unique_with_col(
                conn,
                f"{stg_f} AS src WHERE {stg_w}",
                f"src.{sc}",
                stg_g,
                f"COUNT(DISTINCT src.{hd_c})",
            ):
                continue
        if sat_dup and (
            a.name not in sat_cols or not _is_unique_with_col(conn, sf, a.name, g_cols, "COUNT(*)")
        ):
            continue
        return a.name
    return None


__all__ = ["check_satellite_invariants", "find_candidate_cdk", "query_counterexamples"]
