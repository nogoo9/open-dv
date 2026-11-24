"""15/15 Dynamic Data Vault 2.0 invariants (`DV-DYN-001..015` per docs/03 §2.1).

Strictly Layer 2a: imports only opendv.models, duckdb, ddl_builders, _dyn_sat_checks, stdlib.
"""

from __future__ import annotations

from collections.abc import Mapping

import duckdb

from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.storage.prover._dyn_sat_checks import check_satellite_invariants, query_counterexamples
from opendv.storage.prover.edge_batches import AdversarialBatchSummary
from opendv.storage.sql_compiler._scalar_exprs import predicate_to_sqlglot
from opendv.storage.sql_compiler.ddl_builders import (
    planned_exclusion_sets,
    qualify_name,
    resolve_pit_bridge_columns,
)
from opendv.storage.sql_compiler.dialect_hashing import zero_key_hex_sql


def run_all_dyn_invariants(
    conn: duckdb.DuckDBPyConnection,
    ir: CertifiedVaultIR,
    aliases: Mapping[str, str],
    hd_col_map: Mapping[tuple[str, str], str],
    batch_summary: AdversarialBatchSummary,
) -> tuple[dict[str, bool], list[dict[str, str | int | float | None]]]:
    """Evaluate all 15 docs/03 §2.1 DV-DYN-001..015 invariants against :memory: DuckDB."""
    planned_hubs, excluded_links, excluded_sats = planned_exclusion_sets(ir)
    pii_cols = batch_summary.pii_columns
    zero_00 = zero_key_hex_sql("duckdb", is_error=False)
    zero_ff = zero_key_hex_sql("duckdb", is_error=True)

    results: dict[str, bool] = {f"DV-DYN-{i:03d}": True for i in range(1, 16)}
    counterexamples: list[dict[str, str | int | float | None]] = []

    def _fail(rule_id: str, rows: list[dict[str, str | int | float | None]]) -> None:
        results[rule_id] = False
        for r in rows:
            if len(counterexamples) < 25:
                counterexamples.append({"rule_id": rule_id, **r})

    hubs_by_name = {h.hub_name: h for h in ir.hubs if h.hub_name not in planned_hubs}
    links_by_name = {lk.link_name: lk for lk in ir.links if lk.link_name not in excluded_links}
    stg_by_tbl = {s.source_table_id: s for s in ir.staging_views}

    # DV-DYN-001 & DV-DYN-002: Hub HK/BK uniqueness & hash collision check
    if not batch_summary.boundary_shift_ok:
        _fail("DV-DYN-002", [{"issue": "boundary_shift_hash_collision"}])

    for h in hubs_by_name.values():
        hf = qualify_name(h.zone_id, h.hub_name, aliases)
        hk = h.columns[0].name
        bk_cols = [bk.col_name for bk in h.business_keys]
        dups = query_counterexamples(
            conn,
            f"SELECT {hk} AS hk_hex, COUNT(*) AS cnt FROM {hf} GROUP BY {hk} HAVING COUNT(*) > 1",
            pii_cols,
        )
        if dups:
            _fail("DV-DYN-001", dups)
        if bk_cols:
            bk_csv = ", ".join(["bkcc", *bk_cols])
            bk_dups = query_counterexamples(
                conn,
                f"SELECT {bk_csv}, COUNT(*) AS cnt FROM {hf} "
                f"WHERE {hk} <> {zero_00} AND {hk} <> {zero_ff} "
                f"GROUP BY {bk_csv} HAVING COUNT(*) > 1",
                pii_cols,
            )
            if bk_dups:
                _fail("DV-DYN-001", bk_dups)
            col_q = query_counterexamples(
                conn,
                f"SELECT {hk} AS hk_hex, COUNT(DISTINCT ({bk_csv})) AS distinct_bks FROM {hf} "
                f"GROUP BY {hk} HAVING COUNT(DISTINCT ({bk_csv})) > 1",
                pii_cols,
            )
            if col_q:
                _fail("DV-DYN-002", col_q)

    # DV-DYN-003 & DV-DYN-007: Link grain uniqueness & Link-to-Hub FK RI
    for lk in links_by_name.values():
        lf = qualify_name(lk.zone_id, lk.link_name, aliases)
        hk = lk.columns[0].name
        fks = [p.fk_col_name for p in lk.participants]
        tuple_cols = [*fks, *lk.degenerate_keys]
        dups = query_counterexamples(
            conn,
            f"SELECT {hk} AS hk_hex, COUNT(*) AS cnt FROM {lf} GROUP BY {hk} HAVING COUNT(*) > 1",
            pii_cols,
        )
        if dups:
            _fail("DV-DYN-003", dups)
        if tuple_cols:
            t_csv = ", ".join(tuple_cols)
            t_dups = query_counterexamples(
                conn,
                f"SELECT {t_csv}, COUNT(*) AS cnt FROM {lf} "
                f"WHERE {hk} <> {zero_00} AND {hk} <> {zero_ff} "
                f"GROUP BY {t_csv} HAVING COUNT(*) > 1",
                pii_cols,
            )
            if t_dups:
                _fail("DV-DYN-003", t_dups)
            col_l = query_counterexamples(
                conn,
                f"SELECT {hk} AS hk_hex, COUNT(DISTINCT ({t_csv})) AS distinct_tuples FROM {lf} "
                f"GROUP BY {hk} HAVING COUNT(DISTINCT ({t_csv})) > 1",
                pii_cols,
            )
            if col_l:
                _fail("DV-DYN-002", col_l)
        for p in lk.participants:
            hub = hubs_by_name.get(p.hub_name)
            if hub is None:
                continue
            hf = qualify_name(hub.zone_id, hub.hub_name, aliases)
            h_hk = hub.columns[0].name
            orphans = query_counterexamples(
                conn,
                f"SELECT l.{hk} AS link_hk, l.{p.fk_col_name} AS missing_fk FROM {lf} AS l "
                f"LEFT JOIN {hf} AS h ON l.{p.fk_col_name} = h.{h_hk} WHERE h.{h_hk} IS NULL",
                pii_cols,
            )
            if orphans:
                _fail("DV-DYN-007", orphans)

    # DV-DYN-004, 005, 006, 008, 011, 013: Satellite checks
    check_satellite_invariants(
        conn, ir, aliases, hd_col_map, hubs_by_name, links_by_name, pii_cols, _fail
    )

    # DV-DYN-009: Zero Keys (-1, -2) and Ghost Record presence
    for h in hubs_by_name.values():
        hf = qualify_name(h.zone_id, h.hub_name, aliases)
        hk = h.columns[0].name
        cnt_r = conn.execute(
            f"SELECT COUNT(*) FROM {hf} WHERE {hk} = {zero_00} OR {hk} = {zero_ff}"
        ).fetchone()
        if not cnt_r or int(cnt_r[0]) < 2:
            _fail("DV-DYN-009", [{"entity": h.hub_name, "issue": "missing_zero_keys"}])
    for lk in links_by_name.values():
        lf = qualify_name(lk.zone_id, lk.link_name, aliases)
        hk = lk.columns[0].name
        cnt_r = conn.execute(
            f"SELECT COUNT(*) FROM {lf} WHERE {hk} = {zero_00} OR {hk} = {zero_ff}"
        ).fetchone()
        if not cnt_r or int(cnt_r[0]) < 2:
            _fail("DV-DYN-009", [{"entity": lk.link_name, "issue": "missing_zero_keys"}])
    for sat in ir.satellites:
        if sat.sat_name in excluded_sats:
            continue
        sf = qualify_name(sat.zone_id, sat.sat_name, aliases)
        p_hk = sat.columns[0].name
        cnt_r = conn.execute(
            f"SELECT COUNT(*) FROM {sf} WHERE {p_hk} = {zero_00} "
            "AND load_dts = CAST('1900-01-01T00:00:00Z' AS TIMESTAMPTZ)"
        ).fetchone()
        if not cnt_r or int(cnt_r[0]) < 1:
            _fail("DV-DYN-009", [{"entity": sat.sat_name, "issue": "missing_ghost_record"}])

    # DV-DYN-010: Staging-to-Hub & Staging-to-Link completeness
    for h in hubs_by_name.values():
        hf = qualify_name(h.zone_id, h.hub_name, aliases)
        hk = h.columns[0].name
        for sb in h.source_bindings:
            stg = stg_by_tbl.get(sb.table_id)
            if stg is None:
                continue
            stg_f = qualify_name("Z1", stg.view_name, aliases)
            missing = query_counterexamples(
                conn,
                f"SELECT s.{hk} AS missing_hk FROM {stg_f} AS s LEFT JOIN {hf} AS h "
                f"ON s.{hk} = h.{hk} "
                f"WHERE s.{hk} <> {zero_00} AND s.{hk} <> {zero_ff} AND h.{hk} IS NULL",
                pii_cols,
            )
            if missing:
                _fail("DV-DYN-010", missing)
    for lk in links_by_name.values():
        if lk.link_kind == "SAME_AS":
            continue
        lf = qualify_name(lk.zone_id, lk.link_name, aliases)
        hk = lk.columns[0].name
        for sb in lk.source_bindings:
            stg = stg_by_tbl.get(sb.table_id)
            if stg is None:
                continue
            stg_f = qualify_name("Z1", stg.view_name, aliases)
            sem = sb.load_semantics or lk.load_semantics
            extra_filter = ""
            if (sem and sem.require_review_status == "APPROVED") or (
                lk.link_kind == "UNSTRUCTURED_EXTRACTION_LINK"
            ):
                extra_filter += " AND s.steward_gate_status = 'APPROVED'"
            if sem and sem.filter_ast is not None:
                pred_sql = predicate_to_sqlglot(sem.filter_ast, table_alias="s").sql(
                    dialect="duckdb"
                )
                extra_filter += f" AND {pred_sql}"
            missing = query_counterexamples(
                conn,
                f"SELECT s.{hk} AS missing_hk FROM {stg_f} AS s LEFT JOIN {lf} AS l "
                f"ON s.{hk} = l.{hk} "
                f"WHERE s.{hk} <> {zero_00} AND s.{hk} <> {zero_ff}{extra_filter} "
                f"AND l.{hk} IS NULL",
                pii_cols,
            )
            if missing:
                _fail("DV-DYN-010", missing)

    # DV-DYN-012: Two-run replay idempotency
    nonzero_deltas = {k: v for k, v in batch_summary.batch2_deltas.items() if v != 0}
    if nonzero_deltas:
        _fail("DV-DYN-012", [{"entity": k, "delta_rows": v} for k, v in nonzero_deltas.items()])

    # DV-DYN-014: Ghost/Zero-key business metric leakage guard in Z3/Z4
    for pb in ir.pit_bridges:
        if pb.root_hub in planned_hubs:
            continue
        pf = qualify_name("Z3", pb.object_name, aliases)
        _, pit_root_hk, _ = resolve_pit_bridge_columns(pb, ir, 255)
        leaks = query_counterexamples(
            conn,
            f"SELECT {pit_root_hk} AS leaked_hk FROM {pf} "
            f"WHERE {pit_root_hk} = {zero_00} OR {pit_root_hk} = {zero_ff}",
            pii_cols,
        )
        if leaks:
            _fail("DV-DYN-014", leaks)

    for mart in ir.information_marts:
        if mart.root_entity in planned_hubs:
            continue
        mf = qualify_name("Z4", mart.view_name, aliases)
        root_hk = f"hk_{mart.root_entity.removeprefix('hub_')}"
        leaks = query_counterexamples(
            conn,
            f"SELECT {root_hk} AS leaked_hk FROM {mf} "
            f"WHERE {root_hk} = {zero_00} OR {root_hk} = {zero_ff}",
            pii_cols,
        )
        if leaks:
            _fail("DV-DYN-014", leaks)

    # DV-DYN-015: PIT table completeness, non-null pointers, & bitemporal applied_dts resolution
    if not batch_summary.bitemporal_proof_ok:
        _fail("DV-DYN-015", [{"issue": "bitemporal_restatement_proof_failed"}])

    sats_by_name = {s.sat_name: s for s in ir.satellites}
    for pb in ir.pit_bridges:
        if pb.root_hub in planned_hubs or pb.object_kind != "PIT":
            continue
        pf = qualify_name("Z3", pb.object_name, aliases)
        src_root_hk, pit_root_hk, targets = resolve_pit_bridge_columns(pb, ir, 255)
        hub_obj = hubs_by_name.get(pb.root_hub)
        if hub_obj is not None:
            hf = qualify_name(hub_obj.zone_id, hub_obj.hub_name, aliases)
            missing_pit = query_counterexamples(
                conn,
                f"SELECT h.{src_root_hk} AS missing_pit_hk FROM {hf} AS h "
                f"LEFT JOIN {pf} AS p ON h.{src_root_hk} = p.{pit_root_hk} "
                f"WHERE h.{src_root_hk} <> {zero_00} AND h.{src_root_hk} <> {zero_ff} "
                f"AND p.{pit_root_hk} IS NULL",
                pii_cols,
            )
            if missing_pit:
                _fail("DV-DYN-015", missing_pit)

        for tgt, hk_col, ldts_col, pit_app_col, app_ltype in targets:
            sat_obj = sats_by_name.get(tgt)
            if sat_obj is None or sat_obj.sat_name in excluded_sats:
                continue
            sf = qualify_name(sat_obj.zone_id, sat_obj.sat_name, aliases)
            s_phk = sat_obj.columns[0].name
            app_join = ""
            app_null_chk = ""
            if pit_app_col is not None and sat_obj.applied_dts_col:
                app_join = f" AND p.{pit_app_col} = s.{sat_obj.applied_dts_col}"
                app_null_chk = f" OR p.{pit_app_col} IS NULL"
            bad_pit = query_counterexamples(
                conn,
                f"SELECT p.{pit_root_hk} AS pit_hk, p.snapshot_dts FROM {pf} AS p "
                f"LEFT JOIN {sf} AS s ON p.{hk_col} = s.{s_phk} AND p.{ldts_col} = s.load_dts"
                f"{app_join} "
                f"WHERE p.{hk_col} IS NULL OR p.{ldts_col} IS NULL{app_null_chk} "
                f"OR s.{s_phk} IS NULL",
                pii_cols,
            )
            if bad_pit:
                _fail("DV-DYN-015", bad_pit)

            if sat_obj.is_bitemporal and sat_obj.applied_dts_col:
                app_col = sat_obj.applied_dts_col
                s_app_cmp = (
                    f"CAST(s2.{app_col} AS TIMESTAMPTZ)"
                    if app_ltype == "DATE32"
                    else f"s2.{app_col}"
                )
                s3_app_cmp = (
                    f"CAST(s3.{app_col} AS TIMESTAMPTZ)"
                    if app_ltype == "DATE32"
                    else f"s3.{app_col}"
                )
                bad_bitemp = query_counterexamples(
                    conn,
                    f"SELECT p.{pit_root_hk} AS pit_hk, p.snapshot_dts FROM {pf} AS p "
                    f"INNER JOIN {sf} AS s ON s.{s_phk} = p.{hk_col} AND s.load_dts = p.{ldts_col}"
                    f"{app_join} "
                    f"WHERE p.{hk_col} <> {zero_00} "
                    f"AND EXISTS (SELECT 1 FROM {sf} AS s2 "
                    f"WHERE s2.{s_phk} = p.{pit_root_hk} AND {s_app_cmp} <= p.snapshot_dts) "
                    f"AND s.{app_col} <> (SELECT MAX(s3.{app_col}) FROM {sf} AS s3 "
                    f"WHERE s3.{s_phk} = p.{pit_root_hk} AND {s3_app_cmp} <= p.snapshot_dts)",
                    pii_cols,
                )
                if bad_bitemp:
                    _fail("DV-DYN-015", bad_bitemp)

    return results, counterexamples


__all__ = ["run_all_dyn_invariants"]
