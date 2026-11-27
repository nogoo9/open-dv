"""Isolated Gate V6 real-data runtime prover (`real_data_proof`).

Strictly Layer 2a: imports only opendv.models, duckdb, stdlib, and prover/compiler helpers.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import duckdb

from opendv.models.diagnostics import RealDataCheckStatus, RealDataRuleResult
from opendv.models.modeling_preferences import RemediationHint
from opendv.models.pii_tokenizer import PII_TOKEN_RE
from opendv.models.sources import ColumnProfile, SourceProfile
from opendv.models.vault_ir import ArrayType, DecimalType, MapType, PortableLogicalType
from opendv.models.vault_ir_certified import (
    CertifiedHubSpec,
    CertifiedLinkSpec,
    CertifiedVaultIR,
)
from opendv.storage.prover._batch_probes import exec_statements
from opendv.storage.prover._dyn_sat_checks import (
    _sat_sem_and_where,
    find_candidate_cdk,
    query_counterexamples,
)
from opendv.storage.prover.dyn_invariants import run_all_dyn_invariants
from opendv.storage.prover.edge_batches import (
    AdversarialBatchSummary,
    _insert_rows,
    _table_counts,
)
from opendv.storage.sql_compiler._ddl_common import planned_exclusion_sets, qualify_name
from opendv.storage.sql_compiler.compiler import CompiledSqlBundle
from opendv.storage.sql_compiler.dialect_hashing import zero_key_hex_sql


def project_pii_token_for_type(val: Any, ltype: PortableLogicalType | str) -> Any:
    """Map `<PII:16hex>` deterministically to a valid scalar matching unmodified IR type."""
    if not isinstance(val, str) or not PII_TOKEN_RE.fullmatch(val):
        return val
    h = int.from_bytes(bytes.fromhex(val[5:21]), "big")
    if ltype == "UTF8_STRING":
        return val
    if ltype == "INT32":
        return 100_000 + (h % 2_000_000_000)
    if ltype == "INT64":
        return 100_000_000 + (h % 9_000_000_000_000)
    if isinstance(ltype, DecimalType):
        return Decimal(h % (10 ** min(ltype.precision, 12))).scaleb(-ltype.scale)
    if ltype == "FLOAT64":
        return float((h % 100_000) / 100.0)
    if ltype == "DATE32":
        return date(1970, 1, 1) + timedelta(days=h % 20_000)
    if ltype == "TIMESTAMP_UTC_US":
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=h % 1_700_000_000)
    if ltype == "BOOLEAN":
        return bool(h & 1)
    if ltype == "JSON_DOCUMENT":
        return f'"{val}"'
    if ltype == "FIXED_BINARY_32":
        return hashlib.sha256(val.encode("utf-8")).digest()
    if isinstance(ltype, ArrayType):
        return [project_pii_token_for_type(val, ltype.element_type)]
    if isinstance(ltype, MapType):
        return {
            project_pii_token_for_type(val, ltype.key_type): project_pii_token_for_type(
                val, ltype.value_type
            )
        }
    return val


def _all_complete(tids: Sequence[str], flags: Mapping[str, bool]) -> bool:
    return bool(tids) and all(flags.get(t, False) is True for t in tids)


def _eval_004(
    conn: duckdb.DuckDBPyConnection,
    ir: CertifiedVaultIR,
    aliases: Mapping[str, str],
    hd_map: Mapping[tuple[str, str], str],
    profs: Mapping[str, dict[str, ColumnProfile]],
    pii_cols: set[str],
    pass_st: RealDataCheckStatus,
    counts: dict[str, int],
) -> tuple[RealDataRuleResult, list[dict[str, str | int | float | None]]]:
    _, _, excluded_sats = planned_exclusion_sets(ir)
    stg_by_tbl = {s.source_table_id: s for s in ir.staging_views}
    z00, zff = zero_key_hex_sql("duckdb", is_error=False), zero_key_hex_sql("duckdb", is_error=True)
    ce: list[dict[str, str | int | float | None]] = []
    hints: list[str] = []

    for sat in ir.satellites:
        if sat.sat_name in excluded_sats or sat.sat_kind == "MAS" or bool(sat.child_dependent_keys):
            continue
        if sat.sat_kind not in ("STANDARD", "PII", "EXT"):
            continue
        sf, p_hk = qualify_name(sat.zone_id, sat.sat_name, aliases), sat.columns[0].name
        bcol = f", {sat.applied_dts_col}" if (sat.is_bitemporal and sat.applied_dts_col) else ""
        g_cols = f"{p_hk}, load_dts{bcol}"
        sat_dups = query_counterexamples(
            conn,
            f"SELECT {g_cols}, COUNT(*) AS cnt FROM {sf} GROUP BY {g_cols} HAVING COUNT(*) > 1",
            pii_cols,
        )

        stg = stg_by_tbl.get(sat.source_table_id)
        stg_dups: list[dict[str, str | int | float | None]] = []
        stg_f = qualify_name("Z1", stg.view_name, aliases) if stg else ""
        hd_c = hd_map.get((stg.view_name, sat.sat_name), "hash_diff") if stg else "hash_diff"
        stg_g = f"src.{p_hk}, src.load_dts" + (f", src.{sat.applied_dts_col}" if bcol else "")
        stg_w = f"src.{p_hk} <> {z00} AND src.{p_hk} <> {zff}"
        if stg is not None and sat.hash_recipe is not None:
            _, sem, stg_w = _sat_sem_and_where(sat, stg, p_hk, z00, zff)
            if not (sem and sem.dedup_order_cols):
                stg_dups = query_counterexamples(
                    conn,
                    f"SELECT src.{p_hk} AS parent_hk, COUNT(DISTINCT src.{hd_c}) "
                    f"AS distinct_variants FROM {stg_f} AS src WHERE {stg_w} "
                    f"GROUP BY {stg_g} HAVING COUNT(DISTINCT src.{hd_c}) > 1",
                    pii_cols,
                )
        if sat_dups or stg_dups:
            ce.extend({"rule_id": "DV-DYN-004", **r} for r in (sat_dups or stg_dups))
            cdk = find_candidate_cdk(
                conn,
                sat,
                sf,
                g_cols,
                stg,
                (stg_f, stg_g, stg_w, hd_c),
                (bool(sat_dups), bool(stg_dups)),
                profs.get(sat.source_table_id, {}),
            )
            if cdk is not None:
                hint = RemediationHint(
                    gate="V6",
                    rule_id="DV-DYN-004",
                    entity_name=sat.sat_name,
                    waivable=False,
                    suggested_fix=(
                        f"Promote {sat.sat_name} to sat_kind='MAS' with "
                        f"child_dependent_keys=['{cdk}'] (restores uniqueness per {p_hk})."
                    ),
                )
                hints.append(hint.suggested_fix)
                ce.append(
                    {
                        "rule_id": "DV-DYN-004",
                        "entity_name": sat.sat_name,
                        "candidate_cdk": cdk,
                        "remediation_hint": hint.suggested_fix,
                    }
                )

    if ce:
        detail = "Satellite duplicate parent_hk/load_dts detected on real rows." + (
            f" Suggested fix: {hints[0]}" if hints else ""
        )
        return _rr("FAIL", detail, counts), ce
    return _rr(pass_st, "Satellite uniqueness verified across STANDARD/PII/EXT.", counts), []


def _rr(
    status: RealDataCheckStatus,
    detail: str,
    counts: dict[str, int],
    consequence: str | None = None,
) -> RealDataRuleResult:
    return RealDataRuleResult(
        status=status, detail=detail, table_row_counts=counts, consequence=consequence
    )


def _edge_res(
    ex: int,
    sk: int,
    fl: int,
    ok: bool,
    pass_st: RealDataCheckStatus,
    counts: dict[str, int],
    label: str,
    tag: str,
    conseq: str,
) -> RealDataRuleResult:
    if fl > 0:
        return _rr("FAIL", f"{label} failed on {fl}/{ex} edge(s) ({sk} skipped).", counts)
    if sk > 0:
        return _rr(
            "SKIPPED_PARTIAL_SAMPLE", f"{ex} edge(s) passed ({tag}), {sk} skipped.", counts, conseq
        )
    st: RealDataCheckStatus = pass_st if ex == 0 else ("PASS_FULL" if ok else "PASS_SAMPLE_BOUNDED")
    return _rr(st, f"All {ex} edge(s) passed ({tag}).", counts)


def _eval_ri_gates(
    conn: duckdb.DuckDBPyConnection,
    ir: CertifiedVaultIR,
    aliases: Mapping[str, str],
    hubs: Mapping[str, CertifiedHubSpec],
    links: Mapping[str, CertifiedLinkSpec],
    flags: Mapping[str, bool],
    pii_cols: set[str],
    pass_st: RealDataCheckStatus,
    counts: dict[str, int],
) -> tuple[RealDataRuleResult, RealDataRuleResult, list[dict[str, str | int | float | None]]]:
    _, _, excluded_sats = planned_exclusion_sets(ir)
    ce: list[dict[str, str | int | float | None]] = []

    d7_ex, d7_sk, d7_fl, d7_ok = 0, 0, 0, True
    for lk in links.values():
        lf, hk = qualify_name(lk.zone_id, lk.link_name, aliases), lk.columns[0].name
        c_ok = _all_complete([sb.table_id for sb in lk.source_bindings], flags)
        for p in lk.participants:
            if (hub := hubs.get(p.hub_name)) is None:
                continue
            if not _all_complete([sb.table_id for sb in hub.source_bindings], flags):
                d7_sk += 1
                continue
            d7_ex += 1
            d7_ok = d7_ok and c_ok
            hf, h_hk = qualify_name(hub.zone_id, hub.hub_name, aliases), hub.columns[0].name
            orphans = query_counterexamples(
                conn,
                f"SELECT l.{hk} AS link_hk, l.{p.fk_col_name} AS missing_fk FROM {lf} AS l "
                f"LEFT JOIN {hf} AS h ON l.{p.fk_col_name} = h.{h_hk} WHERE h.{h_hk} IS NULL",
                pii_cols,
            )
            if orphans:
                d7_fl += 1
                hint = (
                    f"Link '{lk.link_name}' -> Hub '{p.hub_name}' ({p.fk_col_name}) has "
                    f"orphan FKs: bind every Link source table to Hub '{p.hub_name}' with "
                    "matching BK/BKCC."
                )
                ce.extend(
                    {
                        "rule_id": "DV-DYN-007",
                        "entity_name": lk.link_name,
                        "hub_name": p.hub_name,
                        "fk_col": p.fk_col_name,
                        "remediation_hint": hint,
                        **r,
                    }
                    for r in orphans
                )

    res007 = _edge_res(
        d7_ex,
        d7_sk,
        d7_fl,
        d7_ok,
        pass_st,
        counts,
        "DV-DYN-007 Link->Hub",
        "partial parent sample" if d7_sk else "Link->Hub",
        "Orphan Link FKs referencing unsampled parent Hub rows will only be caught in Step 7.",
    )

    d6_s, d6_c, d6_sk, d6_fl, d6_ok = 0, 0, 0, 0, True
    for sat in ir.satellites:
        if sat.sat_name in excluded_sats:
            continue
        p_h, p_l = hubs.get(sat.parent_name), links.get(sat.parent_name)
        if p_h is not None:
            p_zone, p_name, pk_c = p_h.zone_id, p_h.hub_name, p_h.columns[0].name
            p_tids = [sb.table_id for sb in p_h.source_bindings]
        elif p_l is not None:
            p_zone, p_name, pk_c = p_l.zone_id, p_l.link_name, p_l.columns[0].name
            p_tids = [sb.table_id for sb in p_l.source_bindings]
        else:
            continue
        is_same, p_ok = sat.source_table_id in set(p_tids), _all_complete(p_tids, flags)
        if not is_same and not p_ok:
            d6_sk += 1
            continue
        pf = qualify_name(p_zone, p_name, aliases)
        sf, p_hk = qualify_name(sat.zone_id, sat.sat_name, aliases), sat.columns[0].name
        orphans = query_counterexamples(
            conn,
            f"SELECT s.{p_hk} AS orphan_hk FROM {sf} AS s "
            f"LEFT JOIN {pf} AS p ON s.{p_hk} = p.{pk_c} WHERE p.{pk_c} IS NULL",
            pii_cols,
        )
        if orphans:
            d6_fl += 1
            hint = (
                f"Satellite '{sat.sat_name}' -> '{sat.parent_name}' has orphan parent keys: "
                f"bind '{sat.source_table_id}' to '{sat.parent_name}' with matching BK/BKCC."
            )
            ce.extend(
                {
                    "rule_id": "DV-DYN-006",
                    "entity_name": sat.sat_name,
                    "parent_name": sat.parent_name,
                    "remediation_hint": hint,
                    **r,
                }
                for r in orphans
            )
        elif is_same:
            d6_s += 1
            d6_ok = d6_ok and (flags.get(sat.source_table_id, False) is True)
        else:
            d6_c += 1
            d6_ok = d6_ok and (flags.get(sat.source_table_id, False) is True) and p_ok

    res006 = _edge_res(
        d6_s + d6_c,
        d6_sk,
        d6_fl,
        d6_ok,
        pass_st,
        counts,
        "DV-DYN-006 Satellite->Parent",
        f"{d6_s} same-source, {d6_c} cross-source",
        "Cross-source Satellite->Parent RI on partial samples is deferred to Step 7.",
    )
    return res006, res007, ce


def run_real_data_proof_pass(
    conn: duckdb.DuckDBPyConnection,
    ir: CertifiedVaultIR,
    profiles: Sequence[SourceProfile],
    bundle: CompiledSqlBundle,
    *,
    source_rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    table_sample_flags: Mapping[str, bool] | None = None,
) -> tuple[dict[str, RealDataRuleResult], list[dict[str, str | int | float | None]]]:
    """Execute isolated Gate V6 real-data proof pass against unmodified CertifiedVaultIR."""
    pii_cols: set[str] = {c.name for b in ir.bronze_tables for c in b.columns if c.is_pii}
    col_profs: dict[str, dict[str, ColumnProfile]] = {}
    for prof in profiles:
        for tbl in prof.tables:
            col_profs[tbl.table_id] = {c.col_name: c for c in tbl.columns}
            pii_cols.update(cp.col_name for cp in tbl.columns if cp.pii_tags)

    exec_statements(conn, bundle.ddl_statements)
    counts: dict[str, int] = {}
    for b in ir.bronze_tables:
        raw_rows = source_rows_by_table.get(b.source_table_id, ())
        counts[b.source_table_id] = len(raw_rows)
        if raw_rows:
            b_full = qualify_name("Z0", b.table_name, bundle.physical_aliases)
            proj = [
                {
                    c.name: project_pii_token_for_type(r.get(c.name), c.logical_type)
                    for c in b.columns
                }
                for r in raw_rows
            ]
            _insert_rows(conn, b_full, [c.name for c in b.columns], proj)

    exec_statements(conn, bundle.dml_statements)
    cnt_before = _table_counts(conn, ir, bundle.physical_aliases)
    exec_statements(conn, bundle.dml_statements)
    cnt_after = _table_counts(conn, ir, bundle.physical_aliases)
    deltas = {k: cnt_after.get(k, 0) - cnt_before.get(k, 0) for k in cnt_before}

    summary = AdversarialBatchSummary(
        batch2_deltas=deltas,
        boundary_shift_ok=True,
        bitemporal_proof_ok=True,
        brk02b_ok=True,
        pii_columns=pii_cols,
    )
    dyn_res, base_ce = run_all_dyn_invariants(
        conn,
        ir,
        bundle.physical_aliases,
        bundle.stg_hd_col_map,
        summary,
        skip_rules=frozenset({"DV-DYN-004", "DV-DYN-006", "DV-DYN-007"}),
    )

    flags = dict(table_sample_flags or {})
    pass_st: RealDataCheckStatus = (
        "PASS_FULL"
        if _all_complete([b.source_table_id for b in ir.bronze_tables], flags)
        else "PASS_SAMPLE_BOUNDED"
    )
    results: dict[str, RealDataRuleResult] = {
        rid: RealDataRuleResult(
            status=pass_st if ok else "FAIL",
            detail=f"{rid} {'verified' if ok else 'failed'} on real rows.",
            table_row_counts=counts,
        )
        for rid, ok in sorted(dyn_res.items())
        if rid not in ("DV-DYN-004", "DV-DYN-006", "DV-DYN-007")
    }

    results["DV-DYN-004"], ce004 = _eval_004(
        conn,
        ir,
        bundle.physical_aliases,
        bundle.stg_hd_col_map,
        col_profs,
        pii_cols,
        pass_st,
        counts,
    )
    planned_hubs, excluded_links, _ = planned_exclusion_sets(ir)
    hubs = {h.hub_name: h for h in ir.hubs if h.hub_name not in planned_hubs}
    links = {lk.link_name: lk for lk in ir.links if lk.link_name not in excluded_links}
    results["DV-DYN-006"], results["DV-DYN-007"], ri_ce = _eval_ri_gates(
        conn, ir, bundle.physical_aliases, hubs, links, flags, pii_cols, pass_st, counts
    )
    return results, [*ce004, *ri_ce, *(c for c in base_ce if c.get("rule_id") != "DV-DYN-004")][:25]


__all__ = ["project_pii_token_for_type", "run_real_data_proof_pass"]
