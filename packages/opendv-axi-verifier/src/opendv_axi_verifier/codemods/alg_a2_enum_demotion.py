"""Algorithm A.2: Low-Cardinality Enum Hub-to-Ref Demotion.

Detects Hub entities representing static low-cardinality enumerations (`distinct_count <= 250`
with zero key growth or `taxonomy_class in {"ENUMERATION", "REFERENCE_CODE"}`), demotes them
to `ref_<slug>` in `reference_tables`, dissolves their attached Satellites into
`description_columns`, and rewires any Links referencing the demoted Hub.
"""

from __future__ import annotations

import copy
from typing import Any


def _extract_enum_uris(ontology_graph: dict[str, Any]) -> set[str]:
    enum_uris: set[str] = set()
    if not isinstance(ontology_graph, dict):
        return enum_uris

    for ref_enum in ontology_graph.get("reference_enumerations", []):
        if isinstance(ref_enum, dict) and "enum_uri" in ref_enum:
            enum_uris.add(str(ref_enum["enum_uri"]))

    for concept in ontology_graph.get("canonical_business_concepts", []):
        if (
            isinstance(concept, dict)
            and str(concept.get("taxonomy_class", "")).upper() in {"ENUMERATION", "REFERENCE_CODE"}
            and "concept_uri" in concept
        ):
            enum_uris.add(str(concept["concept_uri"]))

    for uri, meta in ontology_graph.items():
        if isinstance(meta, dict) and str(meta.get("taxonomy_class", "")).upper() in {
            "ENUMERATION",
            "REFERENCE_CODE",
        }:
            enum_uris.add(str(uri))

    return enum_uris


def _extract_col_metrics(bronze_profile: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    col_metrics: dict[str, dict[str, dict[str, Any]]] = {}
    if not isinstance(bronze_profile, dict):
        return col_metrics

    if "source_systems" in bronze_profile and isinstance(bronze_profile["source_systems"], list):
        for sys_entry in bronze_profile["source_systems"]:
            if not isinstance(sys_entry, dict):
                continue
            for tbl in sys_entry.get("tables", []):
                if not isinstance(tbl, dict):
                    continue
                b_table = str(tbl.get("bronze_table", ""))
                if not b_table:
                    continue
                t_map: dict[str, dict[str, Any]] = {}
                for col in tbl.get("columns", []):
                    if isinstance(col, dict) and "column_name" in col:
                        c_name = str(col["column_name"])
                        t_map[c_name] = {
                            "distinct_count": int(col.get("distinct_count", 9999)),
                            "key_growth_30d": float(
                                col.get(
                                    "key_growth_30d",
                                    0.0
                                    if col.get("candidate_role") == "ENUM_REFERENCE_CODE"
                                    else 1.0,
                                )
                            ),
                            "candidate_role": str(col.get("candidate_role", "")),
                        }
                col_metrics[b_table] = t_map
        return col_metrics

    for b_table, raw_tbl in bronze_profile.items():
        if not isinstance(raw_tbl, dict):
            continue
        t_map = {}
        for col_name, metrics in raw_tbl.items():
            if isinstance(metrics, dict):
                t_map[col_name] = {
                    "distinct_count": int(metrics.get("distinct_count", 9999)),
                    "key_growth_30d": float(metrics.get("key_growth_30d", 1.0)),
                    "candidate_role": str(metrics.get("candidate_role", "")),
                }
        col_metrics[b_table] = t_map
    return col_metrics


def codemod_demote_enum_hubs_to_ref(
    contract: dict[str, Any],
    ontology_graph: dict[str, Any],
    bronze_profile: dict[str, Any],
) -> dict[str, Any]:
    """Algorithm A.2: Demote Low-Cardinality Enum Hubs to Reference Tables (`ref_*`)."""
    ir = copy.deepcopy(contract)
    enum_uris = _extract_enum_uris(ontology_graph)
    col_metrics = _extract_col_metrics(bronze_profile)

    demoted_hubs: dict[str, dict[str, Any]] = {}
    retained_hubs: list[dict[str, Any]] = []

    for hub in ir.get("hubs", []):
        uri = str(hub.get("ontology_concept_uri", ""))
        is_enum_concept = (
            uri in enum_uris
            or uri.startswith("urn:ontology:enum:")
            or uri.startswith("urn:ontology:ref:")
        )

        bk_sources = list(hub.get("business_key_sources", []))
        all_sources_static_low_card = bool(bk_sources) and all(
            all(
                col_metrics.get(str(src.get("bronze_table", "")), {})
                .get(c, {})
                .get("distinct_count", 9999)
                <= 250
                and col_metrics.get(str(src.get("bronze_table", "")), {})
                .get(c, {})
                .get("key_growth_30d", 1.0)
                == 0.0
                for c in src.get("key_columns", [])
            )
            for src in bk_sources
        )

        if is_enum_concept or all_sources_static_low_card:
            demoted_hubs[str(hub["name"])] = hub
        else:
            retained_hubs.append(hub)

    if not demoted_hubs:
        return ir

    ir["hubs"] = retained_hubs
    ref_tables: list[dict[str, Any]] = list(ir.get("reference_tables", []))
    existing_ref_names = {str(r.get("name", "")) for r in ref_tables}

    # 1. Remove Satellites attached to demoted Hubs and collect description columns
    retained_sats: list[dict[str, Any]] = []
    demoted_sat_descriptions: dict[str, list[str]] = {h: [] for h in demoted_hubs}
    demoted_sat_to_ref: dict[str, str] = {}

    for sat in ir.get("satellites", []):
        parent = str(sat.get("parent_entity", ""))
        if parent in demoted_hubs:
            ref_name = f"ref_{parent.removeprefix('hub_')}"
            demoted_sat_to_ref[str(sat.get("name", ""))] = ref_name
            demoted_sat_descriptions[parent].extend(sat.get("payload_columns", []))
        else:
            retained_sats.append(sat)

    # 2. Emit Reference Tables (`ref_<slug>`)
    for h_name, hub in demoted_hubs.items():
        slug = h_name.removeprefix("hub_")
        ref_name = f"ref_{slug}"
        if ref_name in existing_ref_names:
            continue
        first_src = (
            hub["business_key_sources"][0]
            if hub.get("business_key_sources")
            else {"bronze_table": "unknown", "key_columns": ["code"]}
        )
        ref_tables.append(
            {
                "name": ref_name,
                "source_bronze_table": first_src["bronze_table"],
                "natural_code_column": first_src["key_columns"][0],
                "description_columns": sorted(set(demoted_sat_descriptions[h_name])),
            }
        )
        existing_ref_names.add(ref_name)

    ir["reference_tables"] = ref_tables

    # 3. Rewire Links referencing demoted Hubs
    retained_links: list[dict[str, Any]] = []
    for lnk in ir.get("links", []):
        refs = list(lnk.get("hub_references", []))
        real_refs = [r for r in refs if str(r.get("target_hub", "")) not in demoted_hubs]
        enum_refs = [r for r in refs if str(r.get("target_hub", "")) in demoted_hubs]

        if not enum_refs:
            retained_links.append(lnk)
        elif len(real_refs) >= 2:
            lnk["hub_references"] = real_refs
            degen: list[str] = list(lnk.get("degenerate_columns", []))
            for er in enum_refs:
                for col in er.get("source_fk_columns", []):
                    if col not in degen:
                        degen.append(col)
            lnk["degenerate_columns"] = degen
            retained_links.append(lnk)
        elif len(real_refs) == 1:
            surviving_hub = str(real_refs[0]["target_hub"])
            b_table = str(lnk.get("source_bronze_table", ""))
            folded_cols = [c for er in enum_refs for c in er.get("source_fk_columns", [])]
            matched_sat = False
            for sat in retained_sats:
                if (
                    sat.get("parent_entity") == surviving_hub
                    and sat.get("source_bronze_table") == b_table
                ):
                    sat["payload_columns"] = sorted(
                        set(list(sat.get("payload_columns", [])) + folded_cols)
                    )
                    matched_sat = True
                    break
            if not matched_sat and folded_cols:
                surviving_slug = surviving_hub.removeprefix("hub_")
                retained_sats.append(
                    {
                        "name": f"sat_{surviving_slug}_core",
                        "parent_entity": surviving_hub,
                        "satellite_type": "STANDARD_SATELLITE",
                        "source_bronze_table": b_table,
                        "security_classification": "PUBLIC",
                        "volatility_tier": "CORE",
                        "sub_sequence_column": None,
                        "driving_key_column": None,
                        "payload_columns": sorted(set(folded_cols)),
                        "tracked_columns": None,
                        "soft_deprecated_columns": [],
                    }
                )

    ir["links"] = retained_links
    ir["satellites"] = retained_sats

    # 4. Update column_triage_ledger
    for entry in ir.get("column_triage_ledger", []):
        target = str(entry.get("target_entity", ""))
        if target in demoted_hubs:
            slug = target.removeprefix("hub_")
            entry["dv_target_role"] = "REFERENCE_CODE"
            entry["target_entity"] = f"ref_{slug}"
        elif target in demoted_sat_to_ref:
            entry["dv_target_role"] = "REFERENCE_CODE"
            entry["target_entity"] = demoted_sat_to_ref[target]

    return ir


codemod_3_demote_enum_hub_to_reference = codemod_demote_enum_hubs_to_ref
