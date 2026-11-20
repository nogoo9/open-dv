"""Algorithm A.9a: Intra-Source Schema Drift Detector & Additive Satellite Evolution Codemod."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from typing import Any

from opendv_axi_verifier.codemods.alg_a9a_similarity import (
    are_types_compatible,
    best_rename_match,
    is_lossless_type_widening,
    levenshtein_distance,
    levenshtein_similarity,
    merge_satellite_column_aliases,
)
from opendv_core.config import get_core_modeling_config

_CORE_CFG = get_core_modeling_config()


@dataclass(frozen=True)
class DriftFinding:
    category: str
    source_table: str
    target_entity: str
    column_name: str
    new_column_name: str | None = None
    old_type: str | None = None
    new_type: str | None = None
    similarity: float | None = None
    action: str = "AUTO-FIX"
    staging_sql_rewrite: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def reconstruct_baseline_metadata_from_contract(
    contract: dict[str, Any] | Any,
    source_table: str,
    source_system: str | None = None,
) -> tuple[set[str], dict[str, str], set[str]]:
    c = contract.model_dump(mode="json") if hasattr(contract, "model_dump") else (contract or {})
    dep_cols: set[str] = set()
    old_types: dict[str, str] = {}
    key_cols: set[str] = set()
    tbl_l = source_table.lower()
    tbl_b = tbl_l.split(".")[-1]

    def _m(t: Any) -> bool:
        if not t or not isinstance(t, str):
            return False
        tl = t.lower()
        if tl == tbl_l:
            return True
        if source_system and "." in tl:
            sys_part, base_part = tl.split(".", 1)
            if sys_part == source_system.lower() and base_part == tbl_b:
                return True
            if sys_part != source_system.lower() and base_part == tbl_b:
                return False
        return tl.split(".")[-1] == tbl_b

    for e in c.get("column_triage_ledger") or []:
        if isinstance(e, dict) and _m(e.get("bronze_table") or e.get("source_table")):
            col = str(e.get("column_name", ""))
            if col:
                dep_cols.add(col)
                if e.get("source_sql_type"):
                    old_types[col] = str(e["source_sql_type"])
                if str(e.get("elm_classification") or e.get("dv_target_role") or "") in {
                    "BUSINESS_KEY",
                    "FOREIGN_KEY_REF",
                    "OPTIONAL_FOREIGN_KEY_REF",
                    "DEGENERATE_KEY",
                }:
                    key_cols.add(col)
    for h in c.get("hubs") or []:
        if isinstance(h, dict):
            for bks in h.get("business_key_sources") or []:
                if isinstance(bks, dict) and _m(bks.get("bronze_table")):
                    for k in bks.get("key_columns") or []:
                        dep_cols.add(str(k))
                        key_cols.add(str(k))
    for lnk in c.get("links") or []:
        if isinstance(lnk, dict) and _m(lnk.get("source_bronze_table")):
            for hr in lnk.get("hub_references") or []:
                if isinstance(hr, dict):
                    for fk in hr.get("source_fk_columns") or []:
                        dep_cols.add(str(fk))
                        key_cols.add(str(fk))
            for d in lnk.get("degenerate_columns") or []:
                dep_cols.add(str(d))
                key_cols.add(str(d))
    for s in c.get("satellites") or []:
        if isinstance(s, dict) and _m(s.get("source_bronze_table")):
            for p in s.get("payload_columns") or []:
                dep_cols.add(str(p))
            for sd in s.get("soft_deprecated_columns") or []:
                dep_cols.add(str(sd))
            if isinstance(s.get("column_types"), dict):
                for ck, cv in s["column_types"].items():
                    old_types[str(ck)] = str(cv)
    return dep_cols, old_types, key_cols


def detect_schema_drift_from_contract(
    contract: dict[str, Any] | Any,
    source_table: str,
    candidate_columns: list[str] | set[str],
    candidate_types: dict[str, str] | None = None,
    *,
    target_entity: str | None = None,
    similarity_threshold: float | None = None,
    value_jaccard: dict[tuple[str, str], float] | None = None,
) -> list[DriftFinding]:
    dep_cols, old_types, key_cols = reconstruct_baseline_metadata_from_contract(
        contract, source_table
    )
    tgt = target_entity or f"sat_{source_table.split('.')[-1]}"
    return detect_intra_source_schema_drift(
        deployed_columns=dep_cols,
        candidate_columns=candidate_columns,
        source_table=source_table,
        target_entity=tgt,
        key_columns=key_cols,
        old_types=old_types,
        new_types=candidate_types or {},
        value_jaccard=value_jaccard,
        similarity_threshold=similarity_threshold,
    )


def detect_intra_source_schema_drift(
    deployed_columns: list[str] | set[str],
    candidate_columns: list[str] | set[str],
    *,
    source_table: str = "unknown",
    target_entity: str = "sat_unknown",
    key_columns: set[str] | None = None,
    old_types: dict[str, str] | None = None,
    new_types: dict[str, str] | None = None,
    value_jaccard: dict[tuple[str, str], float] | None = None,
    similarity_threshold: float | None = None,
) -> list[DriftFinding]:
    core_cfg = get_core_modeling_config()
    sim_thresh = (
        similarity_threshold
        if similarity_threshold is not None
        else core_cfg.levenshtein_rename_threshold
    )
    jaccard_thresh = core_cfg.jaccard_similarity_threshold
    dep_set, cand_set = set(deployed_columns), set(candidate_columns)
    keys = set(key_columns or set())
    old_t, new_t = old_types or {}, new_types or {}
    jmap = value_jaccard or {}
    out: list[DriftFinding] = []

    def _df(cat: str, col: str, **kw: Any) -> None:
        out.append(DriftFinding(cat, source_table, target_entity, col, **kw))

    for k in sorted(keys):
        if k in dep_set and k not in cand_set:
            _df(
                "KEY_DRIFT",
                k,
                old_type=old_t.get(k),
                action="ASK-USER",
                message=f"Key column '{k}' missing from '{source_table}'.",
            )
        elif (
            k in dep_set
            and k in cand_set
            and k in old_t
            and k in new_t
            and not are_types_compatible(old_t[k], new_t[k])
        ):
            _df(
                "KEY_DRIFT",
                k,
                old_type=old_t[k],
                new_type=new_t[k],
                action="ASK-USER",
                message=f"Key column '{k}' drifted: {old_t[k]} -> {new_t[k]}.",
            )

    for col in sorted((dep_set & cand_set) - keys):
        if col in old_t and col in new_t and old_t[col].upper() != new_t[col].upper():
            if is_lossless_type_widening(old_t[col], new_t[col]):
                _df(
                    "TYPE_WIDENED",
                    col,
                    old_type=old_t[col],
                    new_type=new_t[col],
                    staging_sql_rewrite=f"TRIM(CAST({col} AS VARCHAR))",
                    message=f"Lossless widening on '{col}': {old_t[col]} -> {new_t[col]}.",
                )
            else:
                _df(
                    "KEY_DRIFT",
                    col,
                    old_type=old_t[col],
                    new_type=new_t[col],
                    action="ASK-USER",
                    message=f"Incompatible type change on '{col}': {old_t[col]} -> {new_t[col]}.",
                )

    struct_thresh = core_cfg.structural_rename_similarity_threshold
    raw_rem = sorted((dep_set - cand_set) - keys)
    raw_add = sorted((cand_set - dep_set) - keys)
    m_rem, m_add = set(), set()

    for oc in raw_rem:
        b_new, b_sc = best_rename_match(
            oc,
            raw_add,
            matched_new=m_add,
            old_types=old_t,
            new_types=new_t,
            value_jaccard=jmap,
            lev_threshold=sim_thresh,
            jaccard_threshold=jaccard_thresh,
            structural_threshold=struct_thresh,
        )
        if b_new is not None:
            m_rem.add(oc)
            m_add.add(b_new)
            _df(
                "RENAMED",
                oc,
                new_column_name=b_new,
                old_type=old_t.get(oc),
                new_type=new_t.get(b_new),
                similarity=round(b_sc, 4),
                staging_sql_rewrite=f"{b_new} AS {oc}",
                message=f"Column '{oc}' renamed to '{b_new}' (sim={b_sc:.2f}).",
            )

    for oc in raw_rem:
        if oc not in m_rem:
            dt = old_t.get(oc, "VARCHAR")
            _df(
                "SOFT_DEPRECATED",
                oc,
                old_type=dt,
                staging_sql_rewrite=f"CAST(NULL AS {dt}) AS {oc}",
                message=f"Column '{oc}' missing from '{source_table}'; soft-deprecated.",
            )

    for nc in raw_add:
        if nc not in m_add:
            _df(
                "ADDITIVE",
                nc,
                new_type=new_t.get(nc),
                message=f"Additive column '{nc}' detected on '{source_table}'.",
            )

    return out


def _sync_ledger_target(
    entry: dict[str, Any], entity: str, classification: str, sql_type: str | None = None
) -> None:
    if "target_dv_entity" in entry or "target_entity" not in entry:
        entry["target_dv_entity"] = entity
    if "target_entity" in entry:
        entry["target_entity"] = entity
    if "elm_classification" in entry or "dv_target_role" not in entry:
        entry["elm_classification"] = classification
    if "dv_target_role" in entry:
        entry["dv_target_role"] = (
            "SATELLITE_PAYLOAD" if classification == "DESCRIPTIVE_PAYLOAD" else classification
        )
    if sql_type and not entry.get("source_sql_type"):
        entry["source_sql_type"] = sql_type


def _make_ledger_item(bt: str, col: str, cname: str, is_v5: bool, ct: str | None) -> dict[str, Any]:
    if is_v5:
        res: dict[str, Any] = {
            "bronze_table": bt,
            "column_name": col,
            "elm_classification": "DESCRIPTIVE_PAYLOAD",
            "target_dv_entity": cname,
        }
        if ct:
            res["source_sql_type"] = ct
        return res
    return {
        "source_table": bt,
        "column_name": col,
        "dv_target_role": "SATELLITE_PAYLOAD",
        "target_entity": cname,
        "disposition_rationale": f"Auto-routed additive column to {cname}.",
    }


def codemod_intra_source_schema_drift(
    deployed_contract: dict[str, Any],
    candidate_contract: dict[str, Any],
    schema_epoch_tag: str = "v2",
    *,
    similarity_threshold: float | None = None,
    companion_name_override: str | None = None,
) -> dict[str, Any]:
    core_cfg = get_core_modeling_config()
    sim_thresh = (
        similarity_threshold
        if similarity_threshold is not None
        else core_cfg.levenshtein_rename_threshold
    )
    ir = copy.deepcopy(candidate_contract)
    dep_sats = {str(s["name"]): s for s in deployed_contract.get("satellites", []) if "name" in s}
    updated_satellites: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = ir.get("column_triage_ledger", [])
    use_v5 = str(ir.get("contract_version", "")) in {"5.0", "5.0.0"} or any(
        "bronze_table" in e or "target_dv_entity" in e for e in ledger if isinstance(e, dict)
    )
    escalations: list[dict[str, Any]] = list(ir.get("unmapped_escalations") or [])
    dep_hubs = {str(h["name"]): h for h in deployed_contract.get("hubs", []) if "name" in h}
    for hub in ir.get("hubs", []):
        hn = str(hub.get("name", ""))
        if hn in dep_hubs:
            d_sources = {
                (str(s.get("bronze_table", "")), str(s.get("record_source_code", ""))): list(
                    s.get("key_columns", [])
                )
                for s in dep_hubs[hn].get("business_key_sources", [])
            }
            for src in hub.get("business_key_sources", []):
                skey = (str(src.get("bronze_table", "")), str(src.get("record_source_code", "")))
                if skey in d_sources and d_sources[skey] != list(src.get("key_columns", [])):
                    dr = sorted(set(d_sources[skey]) ^ set(src.get("key_columns", []))) or list(
                        src.get("key_columns", [])
                    )
                    escalations.append(
                        {
                            "bronze_table": skey[0],
                            "columns": [str(c) for c in dr],
                            "escalation_reason": "COMPETING_NATURAL_KEYS",
                            "architect_question": (
                                f"Key columns on '{hn}' ({skey[0]}) drifted: "
                                f"{d_sources[skey]} -> {src.get('key_columns')}."
                            ),
                        }
                    )

    for sat in ir.get("satellites", []):
        sn = str(sat.get("name", ""))
        if sn not in dep_sats:
            updated_satellites.append(sat)
            continue

        dep_sat = dep_sats[sn]
        dep_cols = set(dep_sat.get("payload_columns", []))
        cand_cols = set(sat.get("payload_columns", []))
        bt = str(sat.get("source_bronze_table", ""))
        old_types = dep_sat.get("column_types")
        if not old_types:
            _, old_types, _ = reconstruct_baseline_metadata_from_contract(deployed_contract, bt)
        new_types = sat.pop("column_types", None)
        val_jaccard = sat.pop("value_jaccard", None)
        staging_aliases = sat.pop("staging_column_aliases", None)

        findings = detect_intra_source_schema_drift(
            deployed_columns=dep_cols,
            candidate_columns=cand_cols,
            source_table=bt,
            target_entity=sn,
            old_types=old_types,
            new_types=new_types,
            value_jaccard=val_jaccard,
            similarity_threshold=sim_thresh,
        )

        added_cols = sorted(f.column_name for f in findings if f.category == "ADDITIVE")
        removed_cols = sorted(f.column_name for f in findings if f.category == "SOFT_DEPRECATED")
        renamed_pairs = {
            f.new_column_name: f.column_name
            for f in findings
            if f.category == "RENAMED" and f.new_column_name is not None
        }

        for kd in findings:
            if kd.category == "KEY_DRIFT" or kd.action == "ASK-USER":
                escalations.append(
                    {
                        "bronze_table": bt,
                        "columns": [kd.column_name],
                        "escalation_reason": "COMPETING_NATURAL_KEYS",
                        "architect_question": kd.message,
                    }
                )
                for entry in ledger:
                    if (
                        str(entry.get("bronze_table") or entry.get("source_table") or "") == bt
                        and entry.get("column_name") == kd.column_name
                    ):
                        _sync_ledger_target(entry, "ESCALATED", "ESCALATED_AMBIGUOUS")

        sat["payload_columns"] = sorted(dep_cols)
        merged_aliases = merge_satellite_column_aliases(dep_sat, sat, staging_aliases, findings)
        if merged_aliases:
            sat["column_aliases"] = merged_aliases
        if removed_cols:
            sat["soft_deprecated_columns"] = sorted(
                set(sat.get("soft_deprecated_columns") or ()).union(removed_cols)
            )
        if renamed_pairs:
            for entry in ledger:
                et = str(entry.get("bronze_table") or entry.get("source_table") or "")
                ec = str(entry.get("column_name", ""))
                if et == bt and (ec in renamed_pairs or ec in renamed_pairs.values()):
                    _sync_ledger_target(entry, sn, "DESCRIPTIVE_PAYLOAD")

        updated_satellites.append(sat)

        if added_cols:
            cname = companion_name_override or f"{sn}_ext_{schema_epoch_tag}"
            updated_satellites.append(
                {
                    "name": cname,
                    "satellite_type": sat.get("satellite_type", "STANDARD_SATELLITE"),
                    "parent_entity": sat["parent_entity"],
                    "source_bronze_table": bt,
                    "security_classification": sat.get("security_classification", "CONFIDENTIAL"),
                    "volatility_tier": "ADDITIVE_DRIFT_COMPANION",
                    "payload_columns": added_cols,
                    "soft_deprecated_columns": [],
                }
            )

            existing_ledger = {
                (
                    str(e.get("bronze_table") or e.get("source_table") or ""),
                    str(e.get("column_name", "")),
                ): e
                for e in ledger
            }
            for col in added_cols:
                col_type = new_types.get(col) if new_types else None
                entry = existing_ledger.get((bt, col))
                if entry is not None:
                    _sync_ledger_target(entry, cname, "DESCRIPTIVE_PAYLOAD", col_type)
                else:
                    item = _make_ledger_item(bt, col, cname, use_v5, col_type)
                    ledger.append(item)
                    existing_ledger[(bt, col)] = item

    if escalations or "unmapped_escalations" in ir:
        ir["unmapped_escalations"] = escalations
    ir["satellites"] = updated_satellites
    return ir


codemod_6_additive_schema_drift_evolution = codemod_intra_source_schema_drift

__all__ = [
    "DriftFinding",
    "are_types_compatible",
    "codemod_6_additive_schema_drift_evolution",
    "codemod_intra_source_schema_drift",
    "detect_intra_source_schema_drift",
    "detect_schema_drift_from_contract",
    "is_lossless_type_widening",
    "levenshtein_distance",
    "levenshtein_similarity",
    "reconstruct_baseline_metadata_from_contract",
]
