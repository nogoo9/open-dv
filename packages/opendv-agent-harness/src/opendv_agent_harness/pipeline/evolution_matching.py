"""Cross-source wave evolution matching, Jaccard similarity & drift merging."""

from __future__ import annotations

from typing import Any

import duckdb

from opendv_agent_harness.pipeline.tabular_helpers import (
    _is_pii_col,
    extract_non_pii_value_signatures,
    quote_ident,
)
from opendv_axi_verifier.codemods.alg_a9a_schema_drift import (
    detect_intra_source_schema_drift,
    reconstruct_baseline_metadata_from_contract,
)
from opendv_contracts.dtos.pipeline import TableExecutionActionDTO
from opendv_core.config import get_core_modeling_config

_FLOAT_EQUIV_TYPES: frozenset[str] = frozenset(
    {"REAL", "FLOAT", "FLOAT4", "FLOAT8", "DOUBLE", "DOUBLE PRECISION"}
)
_KEY_ROLES: frozenset[str] = frozenset(
    {"BUSINESS_KEY", "FOREIGN_KEY_REF", "OPTIONAL_FOREIGN_KEY_REF", "DEGENERATE_KEY"}
)


def _as_dict(contract: Any) -> dict[str, Any]:
    if hasattr(contract, "model_dump"):
        return contract.model_dump(mode="json")
    return contract if isinstance(contract, dict) else {}


def _collect_deployed_table_records(contract: Any) -> list[dict[str, Any]]:
    c_dict = _as_dict(contract)
    records: dict[str, dict[str, Any]] = {}

    def _rec(bt: str) -> dict[str, Any]:
        bt_clean = bt.strip()
        bl = bt_clean.lower()
        if bl not in records:
            ss = bl.split(".", 1)[0] if "." in bl else ""
            tb = bl.split(".")[-1]
            records[bl] = {
                "bronze_table": bt_clean,
                "dt_lower": bl,
                "source_system": ss,
                "table_base": tb,
                "columns": set(),
                "key_columns": set(),
                "sat_names": [],
            }
        return records[bl]

    for e in c_dict.get("column_triage_ledger") or []:
        if not isinstance(e, dict):
            continue
        bt = str(e.get("bronze_table") or e.get("source_table") or "").strip()
        col = str(e.get("column_name") or "").strip()
        if not bt or not col:
            continue
        r = _rec(bt)
        r["columns"].add(col.lower())
        role = str(e.get("elm_classification") or e.get("dv_target_role") or "")
        if role in _KEY_ROLES:
            r["key_columns"].add(col.lower())
        tgt = str(e.get("target_dv_entity") or e.get("target_entity") or "")
        if tgt.startswith("sat_") and tgt not in r["sat_names"]:
            r["sat_names"].append(tgt)

    for h in c_dict.get("hubs") or []:
        if not isinstance(h, dict):
            continue
        for bks in h.get("business_key_sources") or []:
            if not isinstance(bks, dict):
                continue
            bt = str(bks.get("bronze_table") or "").strip()
            if not bt:
                continue
            r = _rec(bt)
            for k in bks.get("key_columns") or []:
                r["columns"].add(str(k).lower())
                r["key_columns"].add(str(k).lower())

    for lnk in c_dict.get("links") or []:
        if not isinstance(lnk, dict):
            continue
        bt = str(lnk.get("source_bronze_table") or "").strip()
        if not bt:
            continue
        r = _rec(bt)
        for hr in lnk.get("hub_references") or []:
            if isinstance(hr, dict):
                for fk in hr.get("source_fk_columns") or []:
                    r["columns"].add(str(fk).lower())
                    r["key_columns"].add(str(fk).lower())

    for sat in c_dict.get("satellites") or []:
        if not isinstance(sat, dict):
            continue
        bt = str(sat.get("source_bronze_table") or "").strip()
        if not bt:
            continue
        r = _rec(bt)
        sname = str(sat.get("name") or "")
        if sname and sname not in r["sat_names"]:
            r["sat_names"].append(sname)
        for p in sat.get("payload_columns") or []:
            r["columns"].add(str(p).lower())
        for sd in sat.get("soft_deprecated_columns") or []:
            r["columns"].add(str(sd).lower())

    return list(records.values())


def _pick_primary_sat(rec: dict[str, Any], t_base: str) -> str:
    sats = rec.get("sat_names") or []
    non_ext = [s for s in sats if "_ext_" not in s and not s.endswith("_pii")]
    if non_ext:
        return str(non_ext[0])
    if sats:
        return str(sats[0])
    return f"sat_{t_base}"


def match_deployed_table_cross_source(
    contract: Any,
    source_system: str,
    table_name: str,
    incoming_columns: list[str] | set[str] = (),
) -> dict[str, Any] | None:
    """Resolve deployed table match for same-source or cross-source wave evolution."""
    if not contract or not table_name:
        return None
    records = _collect_deployed_table_records(contract)
    if not records:
        return None

    t_key = table_name.strip().lower()
    t_base = t_key.split(".")[-1]
    ss_lower = (source_system or "").strip().lower()
    qual_key = f"{ss_lower}.{t_base}" if ss_lower else t_base

    for rec in records:
        same_sys = not rec["source_system"] or rec["source_system"] == ss_lower
        if rec["dt_lower"] in (qual_key, t_key) or (rec["table_base"] == t_base and same_sys):
            return {
                "bronze_table": rec["bronze_table"],
                "source_system": rec["source_system"],
                "matched_entity": _pick_primary_sat(rec, t_base),
                "sat_names": list(rec["sat_names"]),
                "is_cross_source": False,
            }

    inc_set = {str(c).strip().lower() for c in (incoming_columns or ()) if str(c).strip()}
    if not inc_set:
        return None

    best_rec: dict[str, Any] | None = None
    best_score = (-1, -1)
    for rec in records:
        if rec["table_base"] != t_base:
            continue
        bk_hits = len(inc_set & rec["key_columns"])
        col_hits = len(inc_set & rec["columns"])
        min_cols = max(1, min(len(inc_set), len(rec["columns"])))
        has_overlap = bk_hits > 0 or (col_hits >= min(2, min_cols) and col_hits * 2 >= min_cols)
        if has_overlap and (bk_hits, col_hits) > best_score:
            best_score = (bk_hits, col_hits)
            best_rec = rec

    if best_rec is None:
        return None
    return {
        "bronze_table": best_rec["bronze_table"],
        "source_system": best_rec["source_system"],
        "matched_entity": _pick_primary_sat(best_rec, t_base),
        "sat_names": list(best_rec["sat_names"]),
        "is_cross_source": True,
    }


def harmonize_cross_source_types(
    old_types: dict[str, str],
    new_types: dict[str, str],
    value_jaccard: dict[tuple[str, str], float] | None = None,
) -> dict[str, str]:
    """Normalize dialect-equivalent SQL types (e.g. SQLite REAL vs DuckDB DOUBLE) across waves."""
    harmonized = dict(new_types or {})
    if not old_types or not harmonized:
        return harmonized
    for col, nt in list(harmonized.items()):
        ot = old_types.get(col)
        if (
            ot
            and ot.strip().upper() in _FLOAT_EQUIV_TYPES
            and nt.strip().upper() in _FLOAT_EQUIV_TYPES
        ):
            harmonized[col] = ot
    if value_jaccard:
        j_thresh = get_core_modeling_config().jaccard_similarity_threshold
        for (oc, nc), score in value_jaccard.items():
            if score >= j_thresh and oc in old_types and nc in harmonized:
                ot_u = old_types[oc].strip().upper()
                nt_u = harmonized[nc].strip().upper()
                ot_num = ot_u in _FLOAT_EQUIV_TYPES or ot_u.startswith(("DECIMAL", "NUMERIC"))
                if ot_num and nt_u in _FLOAT_EQUIV_TYPES:
                    harmonized[nc] = old_types[oc]
    return harmonized


def infer_deployed_table_action(
    source_system: str,
    table_name: str,
    incoming_columns: list[str] | set[str],
    incoming_types: dict[str, str] | None,
    contract: Any,
    *,
    value_jaccard: dict[tuple[str, str], float] | None = None,
) -> TableExecutionActionDTO:
    """Classify incoming table against deployed contract using cross-source wave matching."""
    match = match_deployed_table_cross_source(
        contract, source_system, table_name, incoming_columns
    )
    if match is None:
        return TableExecutionActionDTO(
            source_system=source_system,
            table_name=table_name,
            action="INCREMENTAL_ONBOARDING",
            matched_existing_entity=None,
            drift_findings=[],
        )

    matched_entity = match["matched_entity"]
    drift_findings: list[dict[str, Any]] = []
    if incoming_columns:
        dep_cols, old_types, key_cols = reconstruct_baseline_metadata_from_contract(
            contract, match["bronze_table"]
        )
        eff_new_types = harmonize_cross_source_types(
            old_types, incoming_types or {}, value_jaccard
        )
        findings = detect_intra_source_schema_drift(
            deployed_columns=dep_cols,
            candidate_columns=list(incoming_columns),
            source_table=table_name,
            target_entity=matched_entity,
            key_columns=key_cols,
            old_types=old_types,
            new_types=eff_new_types,
            value_jaccard=value_jaccard,
        )
        drift_findings = [f.to_dict() for f in findings]

    return TableExecutionActionDTO(
        source_system=source_system,
        table_name=table_name,
        action="INTRA_SOURCE_DRIFT" if drift_findings else "NO_OP",
        matched_existing_entity=matched_entity,
        drift_findings=drift_findings,
    )


def _sigs_from_profile(
    profile_summaries: dict[str, Any] | None,
    keys: tuple[str, ...] | list[str],
    cols: list[str] | set[str] | tuple[str, ...],
    pii_cols: set[str] | tuple[str, ...] | list[str] = (),
) -> dict[str, set[str]]:
    if not profile_summaries:
        return {}
    entry = next(
        (profile_summaries[k] for k in keys if k and isinstance(profile_summaries.get(k), dict)),
        None,
    )
    if entry is None:
        return {}
    stats = entry.get("column_stats") if isinstance(entry.get("column_stats"), dict) else entry
    out: dict[str, set[str]] = {}
    for c in cols:
        if _is_pii_col(c, pii_cols):
            continue
        cs = stats.get(c)
        if isinstance(cs, dict) and isinstance(cs.get("value_signatures"), (list, set, tuple)):
            sigs = {str(x) for x in cs["value_signatures"] if x}
            if sigs:
                out[c] = sigs
    return out


def compute_cross_wave_value_jaccard(
    conn: Any = None,
    baseline_table: str | None = None,
    candidate_table: str | None = None,
    old_cols: list[str] | set[str] | tuple[str, ...] = (),
    new_cols: list[str] | set[str] | tuple[str, ...] = (),
    *,
    pii_cols: set[str] | tuple[str, ...] | list[str] = (),
    cfg: Any = None,
    profile_summaries: dict[str, Any] | None = None,
    baseline_keys: tuple[str, ...] | list[str] = (),
    candidate_keys: tuple[str, ...] | list[str] = (),
) -> dict[tuple[str, str], float]:
    """Compute pairwise non-PII value Jaccard similarity across baseline and candidate columns."""
    old_sigs: dict[str, set[str]] = {}
    new_sigs: dict[str, set[str]] = {}

    if profile_summaries:
        old_sigs = _sigs_from_profile(profile_summaries, baseline_keys, old_cols, pii_cols)
        new_sigs = _sigs_from_profile(profile_summaries, candidate_keys, new_cols, pii_cols)

    if conn is not None:
        if not old_sigs and baseline_table:
            raw_old = extract_non_pii_value_signatures(
                conn, baseline_table, list(old_cols), pii_cols=pii_cols, cfg=cfg
            )
            old_sigs = {k: set(v) for k, v in raw_old.items()}
        if not new_sigs and candidate_table:
            raw_new = extract_non_pii_value_signatures(
                conn, candidate_table, list(new_cols), pii_cols=pii_cols, cfg=cfg
            )
            new_sigs = {k: set(v) for k, v in raw_new.items()}

    jmap: dict[tuple[str, str], float] = {}
    for oc, osigs in old_sigs.items():
        if not osigs:
            continue
        for nc, nsigs in new_sigs.items():
            if not nsigs:
                continue
            union = osigs | nsigs
            if union:
                jmap[(oc, nc)] = len(osigs & nsigs) / len(union)
    return jmap


def _find_duckdb_view(conn: Any, keys: tuple[str, ...]) -> str | None:
    if conn is None:
        return None
    for k in keys:
        if not k:
            continue
        cand = f"bronze_{k.replace('.', '_').strip('_')}"
        try:
            conn.execute(f"SELECT 1 FROM {quote_ident(cand)} LIMIT 0")
        except duckdb.Error:
            continue
        return cand
    return None


def resolve_feed_value_jaccard(
    feed: Any,
    match: dict[str, Any],
    dep_cols: set[str],
    cand_cols: list[str],
    *,
    profile_summaries: dict[str, Any] | None = None,
    conn: Any = None,
) -> dict[tuple[str, str], float]:
    """Resolve value_jaccard map for a drifted feed via hints, profiles, or DuckDB."""
    jmap: dict[tuple[str, str], float] = {}
    descs = (
        feed.get("column_descriptors", [])
        if isinstance(feed, dict)
        else getattr(feed, "column_descriptors", []) or []
    )
    for cd in descs:
        if isinstance(cd, dict) and isinstance(cd.get("_cross_wave_jaccard"), dict):
            nc = str(cd.get("name") or "")
            for oc, score in cd["_cross_wave_jaccard"].items():
                if nc and oc:
                    jmap[(str(oc), nc)] = float(score)
    if jmap:
        return jmap
    if isinstance(feed, dict) and isinstance(feed.get("value_jaccard"), dict):
        return dict(feed["value_jaccard"])

    bt = match["bronze_table"]
    bt_base = bt.split(".")[-1]
    is_d = isinstance(feed, dict)
    ss = str(feed.get("source_system") if is_d else getattr(feed, "source_system", ""))
    tname = str(feed.get("table_name") if is_d else getattr(feed, "table_name", ""))
    t_base = tname.split(".")[-1]
    pii_cols = list((feed.get("pii_columns") if is_d else getattr(feed, "pii_columns", [])) or [])

    m_ss = match["source_system"]
    base_keys = (bt, f"{m_ss}.{bt_base}" if m_ss else bt_base, bt_base)
    cand_keys = (
        f"{ss}.{tname}" if ss else tname,
        f"{ss}.{t_base}" if ss else t_base,
        tname,
        t_base,
    )

    return compute_cross_wave_value_jaccard(
        conn=conn,
        baseline_table=_find_duckdb_view(conn, base_keys),
        candidate_table=_find_duckdb_view(conn, cand_keys),
        old_cols=sorted(dep_cols - set(cand_cols)),
        new_cols=sorted(set(cand_cols) - dep_cols),
        pii_cols=pii_cols,
        profile_summaries=profile_summaries,
        baseline_keys=base_keys,
        candidate_keys=cand_keys,
    )


def prepare_wave_drift_context(
    curr_contract: dict[str, Any] | None,
    s1_out: Any,
    conn: Any = None,
) -> list[dict[str, Any]]:
    """Compute Stage 01 projected drift and attach compact cross-wave Jaccard hints onto feeds."""
    from opendv_agent_harness.pipeline.drift_projection import project_stage_drift_findings

    feeds = list(getattr(s1_out, "discovered_feeds", []) or [])
    profiles = getattr(s1_out, "profile_summaries", None)
    if not curr_contract or not feeds:
        return []

    core_cfg = get_core_modeling_config()
    for feed in feeds:
        ss = str(getattr(feed, "source_system", "") or "")
        tname = str(getattr(feed, "table_name", "") or "")
        descs = getattr(feed, "column_descriptors", []) or []
        cand_cols = [str(cd.get("name")) for cd in descs if isinstance(cd, dict) and cd.get("name")]
        match = match_deployed_table_cross_source(curr_contract, ss, tname, cand_cols)
        if not match:
            continue
        dep_cols, _, key_cols = reconstruct_baseline_metadata_from_contract(
            curr_contract, match["bronze_table"]
        )
        jmap = resolve_feed_value_jaccard(
            feed, match, dep_cols - key_cols, cand_cols, profile_summaries=profiles, conn=conn
        )
        for (oc, nc), score in jmap.items():
            if score >= core_cfg.jaccard_similarity_threshold:
                for cd in descs:
                    if isinstance(cd, dict) and cd.get("name") == nc:
                        cd.setdefault("_cross_wave_jaccard", {})[oc] = round(score, 4)

    return project_stage_drift_findings(
        curr_contract, feeds, profile_summaries=profiles, conn=conn
    )


def merge_and_format_drift_findings(
    contract_findings: list[dict[str, Any]] | None,
    projected_drift: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge contract-evolution and Stage-01 projected drift findings with UF-1..UF-5 enrichment."""
    from opendv_agent_harness.pipeline.drift_projection import enrich_drift_findings

    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for src in (contract_findings or (), projected_drift or ()):
        for item in enrich_drift_findings(list(src)):
            t_base = str(item.get("source_table") or "").split(".")[-1].lower()
            col = str(item.get("column_name") or "").strip().lower()
            cat = str(item.get("category") or "").strip().upper()
            dedup_key = (t_base, col, cat)
            if dedup_key not in seen:
                seen.add(dedup_key)
                merged.append(item)
    return merged


def filter_satellite_candidate_columns(
    contract: Any,
    bronze_table: str,
    target_sat_name: str,
    cand_cols: list[str],
    key_cols: set[str],
) -> list[str]:
    """Exclude key columns and sibling-satellite columns from candidate payload columns."""
    c_dict = _as_dict(contract)
    sibling_cols: set[str] = set()
    bt_lower = bronze_table.lower()
    for sat in c_dict.get("satellites") or []:
        if not isinstance(sat, dict) or sat.get("name") == target_sat_name:
            continue
        sbt = str(sat.get("source_bronze_table") or "").lower()
        if sbt == bt_lower or sbt.split(".")[-1] == bt_lower.split(".")[-1]:
            sibling_cols.update(str(c) for c in (sat.get("payload_columns") or ()))
    excluded = set(key_cols) | sibling_cols
    return [c for c in cand_cols if c and c not in excluded]


__all__ = [
    "compute_cross_wave_value_jaccard",
    "filter_satellite_candidate_columns",
    "harmonize_cross_source_types",
    "infer_deployed_table_action",
    "match_deployed_table_cross_source",
    "merge_and_format_drift_findings",
    "prepare_wave_drift_context",
    "resolve_feed_value_jaccard",
]
