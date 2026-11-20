"""Stage 05 sparse FK promotion & Stage 03/04 SAL/ESAT emission helpers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from opendv_axi_verifier.codemods.alg_a4_sal_inference import (
    _AUDIT_COLS,
    detect_crosswalk_sal_spec,
    infer_ontology_fks_for_feed,
)
from opendv_core.config import get_core_modeling_config


def _col_non_null_ratio(
    table: str, col: str, col_metrics: dict[str, dict[str, dict[str, Any]]]
) -> float | None:
    short = table.split(".")[-1]
    for tbl_key in (table, short):
        tbl_cols = col_metrics.get(tbl_key) or {}
        if col in tbl_cols and tbl_cols[col].get("non_null_ratio") is not None:
            return float(tbl_cols[col]["non_null_ratio"])
    return None


def emit_crosswalk_sal_entities(
    sal_spec: dict[str, Any],
    q_table: str,
    col_descs: Sequence[dict[str, Any]],
    links_by_name: dict[str, dict[str, Any]],
    sats_by_name: dict[str, dict[str, Any]],
    ledger: list[dict[str, Any]],
) -> None:
    """Populate `sal_*` (SAME_AS_LINK), `esat_sal_*` (EFFECTIVITY_SATELLITE), and ledger."""
    sal_name = str(sal_spec["sal_name"])
    m_col = str(sal_spec["master_col"])
    d_col = str(sal_spec["dup_col"])
    h_name = str(sal_spec["target_hub"])
    esat_name = f"esat_{sal_name}"
    type_map = {
        str(cd["name"]): str(cd.get("physical_type") or "VARCHAR(255)")
        for cd in col_descs
        if isinstance(cd, dict) and cd.get("name")
    }
    rem_payload = [
        cn for cn in type_map if cn not in {m_col, d_col} and cn.lower() not in _AUDIT_COLS
    ]

    if sal_name not in links_by_name:
        links_by_name[sal_name] = {
            "name": sal_name,
            "link_type": "SAME_AS_LINK",
            "ontology_concept_uri": str(sal_spec["rel_uri"]),
            "source_bronze_table": q_table,
            "driving_key_hub": h_name,
            "hub_references": [
                {"target_hub": h_name, "role_alias": "master", "source_fk_columns": [m_col]},
                {"target_hub": h_name, "role_alias": "duplicate", "source_fk_columns": [d_col]},
            ],
            "degenerate_columns": [],
        }

    if esat_name not in sats_by_name:
        sats_by_name[esat_name] = {
            "name": esat_name,
            "satellite_type": "EFFECTIVITY_SATELLITE",
            "parent_entity": sal_name,
            "source_bronze_table": q_table,
            "security_classification": "INTERNAL",
            "volatility_tier": "STANDARD",
            "driving_key_column": m_col,
            "payload_columns": rem_payload,
            "soft_deprecated_columns": [],
        }

    def _has_entry(col: str, ent: str) -> bool:
        return any(
            e.get("bronze_table") == q_table
            and e.get("column_name") == col
            and e.get("target_dv_entity") == ent
            for e in ledger
        )

    if not _has_entry(d_col, sal_name):
        ledger.append(
            {
                "bronze_table": q_table,
                "column_name": d_col,
                "elm_classification": "FOREIGN_KEY_REF",
                "target_dv_entity": sal_name,
                "source_sql_type": type_map.get(d_col, "VARCHAR(64)"),
            }
        )
    for col in rem_payload:
        if not _has_entry(col, esat_name):
            ledger.append(
                {
                    "bronze_table": q_table,
                    "column_name": col,
                    "elm_classification": "DESCRIPTIVE_PAYLOAD",
                    "target_dv_entity": esat_name,
                    "source_sql_type": type_map.get(col, "VARCHAR(255)"),
                }
            )


def _parent_hub_bks(p_hub_obj: dict[str, Any], b_table: str) -> list[str]:
    for src in p_hub_obj.get("business_key_sources", []):
        sb = str(src.get("bronze_table", ""))
        if not b_table or sb == b_table or sb.split(".")[-1] == b_table.split(".")[-1]:
            bks = list(src.get("source_key_columns") or src.get("key_columns") or [])
            if bks:
                return bks
    if p_hub_obj.get("business_key_sources"):
        src0 = p_hub_obj["business_key_sources"][0]
        return list(src0.get("source_key_columns") or src0.get("key_columns") or [])
    return []


def promote_sparse_fks_and_sal_in_contract(
    ir: dict[str, Any],
    ontology_graph: Any,
    col_metrics: dict[str, dict[str, dict[str, Any]]],
    deployed_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Promote unmodeled ontology/sparse FKs out of Satellites and repair Same-As Links."""
    sparse_thresh = float(get_core_modeling_config().sparse_fk_null_threshold)
    deployed_sat_names = {
        str(s.get("name", ""))
        for s in (deployed_contract or {}).get("satellites", [])
        if isinstance(s, dict)
    }
    hubs_by_name = {
        str(h.get("name", "")): h
        for h in ir.get("hubs", [])
        if isinstance(h, dict) and h.get("name")
    }
    links: list[dict[str, Any]] = list(ir.get("links", []))
    links_by_name = {
        str(lnk.get("name", "")): lnk
        for lnk in links
        if isinstance(lnk, dict) and lnk.get("name")
    }
    sats: list[dict[str, Any]] = list(ir.get("satellites", []))
    ledger: list[dict[str, Any]] = list(ir.get("column_triage_ledger", []))
    kept_sats: list[dict[str, Any]] = []

    for sat in sats:
        s_name = str(sat.get("name", ""))
        s_type = str(sat.get("satellite_type", "STANDARD_SATELLITE")).upper()
        parent_hub = str(sat.get("parent_entity", ""))
        if (
            s_name in deployed_sat_names
            or s_type != "STANDARD_SATELLITE"
            or parent_hub not in hubs_by_name
        ):
            kept_sats.append(sat)
            continue

        b_table = str(sat.get("source_bronze_table", ""))
        p_hub_obj = hubs_by_name[parent_hub]
        parent_uri = str(p_hub_obj.get("ontology_concept_uri", ""))
        parent_bks = _parent_hub_bks(p_hub_obj, b_table)
        payload = list(sat.get("payload_columns", []))

        sal_spec = detect_crosswalk_sal_spec(
            [{"name": c} for c in dict.fromkeys([*parent_bks, *payload])],
            parent_uri,
            parent_hub,
            ontology_graph,
        )
        if sal_spec is not None and sal_spec["sal_name"] not in links_by_name:
            sal_name, m_col, d_col = (
                str(sal_spec["sal_name"]),
                str(sal_spec["master_col"]),
                str(sal_spec["dup_col"]),
            )
            esat_name = f"esat_{sal_name}"
            rem_payload = [c for c in payload if c not in {m_col, d_col}]
            sal_obj = {
                "name": sal_name,
                "link_type": "SAME_AS_LINK",
                "ontology_concept_uri": sal_spec["rel_uri"],
                "source_bronze_table": b_table,
                "driving_key_hub": parent_hub,
                "hub_references": [
                    {
                        "target_hub": parent_hub,
                        "role_alias": "master",
                        "source_fk_columns": [m_col],
                    },
                    {
                        "target_hub": parent_hub,
                        "role_alias": "duplicate",
                        "source_fk_columns": [d_col],
                    },
                ],
                "degenerate_columns": [],
            }
            links_by_name[sal_name] = sal_obj
            links.append(sal_obj)
            kept_sats.append(
                {
                    "name": esat_name,
                    "satellite_type": "EFFECTIVITY_SATELLITE",
                    "parent_entity": sal_name,
                    "source_bronze_table": b_table,
                    "security_classification": "INTERNAL",
                    "volatility_tier": "STANDARD",
                    "driving_key_column": m_col,
                    "payload_columns": rem_payload,
                    "soft_deprecated_columns": [],
                }
            )
            for entry in ledger:
                eb = str(entry.get("bronze_table") or entry.get("source_table") or "")
                if eb != b_table and eb.split(".")[-1] != b_table.split(".")[-1]:
                    continue
                if entry.get("column_name") == d_col:
                    entry["target_dv_entity"] = sal_name
                    entry["elm_classification"] = "FOREIGN_KEY_REF"
                elif entry.get("column_name") in rem_payload:
                    entry["target_dv_entity"] = esat_name
            continue

        inferred_fks = infer_ontology_fks_for_feed(
            [{"name": c} for c in payload],
            parent_bks,
            [],
            parent_uri,
            parent_hub,
            ontology_graph,
            hubs_by_name,
        )
        promoted_cols: set[str] = set()
        for fk in inferred_fks:
            fk_col = str(fk["fk_column"])
            nn = _col_non_null_ratio(b_table, fk_col, col_metrics)
            is_sparse = nn is not None and nn < sparse_thresh
            tgt_hub, tgt_uri = str(fk["target_hub"]), str(fk["target_concept_uri"])
            p_slug = parent_hub.removeprefix("hub_")
            t_slug = tgt_hub.removeprefix("hub_")
            lnk_name = str(fk.get("target_link") or f"lnk_{p_slug}_{t_slug}")
            rel_uri = str(
                fk.get("relationship_uri") or f"urn:ontology:relationship:{p_slug}_{t_slug}"
            )
            if tgt_hub not in hubs_by_name:
                rec_src = str(
                    (p_hub_obj.get("business_key_sources") or [{}])[0].get(
                        "record_source_code", "SRC"
                    )
                )
                new_hub = {
                    "name": tgt_hub,
                    "ontology_concept_uri": tgt_uri,
                    "business_key_sources": [
                        {
                            "bronze_table": b_table,
                            "record_source_code": rec_src,
                            "key_columns": [fk_col],
                            "key_authority_tier": "TIER_2_ENTERPRISE_SOR",
                            "bkcc_prefix": "GLOBAL",
                            "require_src_qualifier": False,
                        }
                    ],
                }
                hubs_by_name[tgt_hub] = new_hub
                ir.setdefault("hubs", []).append(new_hub)

            if lnk_name not in links_by_name:
                lnk_obj: dict[str, Any] = {
                    "name": lnk_name,
                    "link_type": "STANDARD_LINK",
                    "ontology_concept_uri": rel_uri,
                    "source_bronze_table": b_table,
                    "hub_references": [
                        {
                            "target_hub": parent_hub,
                            "role_alias": parent_hub.removeprefix("hub_"),
                            "source_fk_columns": parent_bks
                            or [f"{parent_hub.removeprefix('hub_')}_id"],
                        },
                        {
                            "target_hub": tgt_hub,
                            "role_alias": tgt_hub.removeprefix("hub_"),
                            "source_fk_columns": [fk_col],
                        },
                    ],
                    "degenerate_columns": [],
                }
                if is_sparse:
                    lnk_obj["where_filter_condition"] = f"{fk_col} IS NOT NULL"
                links_by_name[lnk_name] = lnk_obj
                links.append(lnk_obj)
            elif is_sparse and not links_by_name[lnk_name].get("where_filter_condition"):
                links_by_name[lnk_name]["where_filter_condition"] = f"{fk_col} IS NOT NULL"

            promoted_cols.add(fk_col)
            role = "OPTIONAL_FOREIGN_KEY_REF" if is_sparse else "FOREIGN_KEY_REF"
            updated_ledger = False
            for entry in ledger:
                eb = str(entry.get("bronze_table") or entry.get("source_table") or "")
                if (eb == b_table or eb.split(".")[-1] == b_table.split(".")[-1]) and entry.get(
                    "column_name"
                ) == fk_col:
                    entry["target_dv_entity"] = lnk_name
                    if "elm_classification" in entry or "dv_target_role" not in entry:
                        entry["elm_classification"] = role
                    if "dv_target_role" in entry:
                        entry["dv_target_role"] = role
                    updated_ledger = True
            if not updated_ledger:
                ledger.append(
                    {
                        "bronze_table": b_table,
                        "column_name": fk_col,
                        "elm_classification": role,
                        "target_dv_entity": lnk_name,
                        "source_sql_type": "VARCHAR(64)",
                    }
                )

        remaining = [c for c in payload if c not in promoted_cols]
        if remaining:
            sat["payload_columns"] = remaining
            kept_sats.append(sat)

    ir["links"] = links
    ir["satellites"] = kept_sats
    ir["column_triage_ledger"] = ledger
    return ir
