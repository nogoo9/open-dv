"""Algorithm A.4: 3-Tier BKCC Authority & Nullable-FK Link Splitter (`U_nn >= 0.95`).

Enforces two cold-start extensibility invariants:
  1. 3-Tier Business Key Collision Code (`BKCC`) Authority:
     - `TIER_1_GLOBAL_STANDARD`: `require_src_qualifier=False`, `bkcc_prefix=None`
     - `TIER_2_ENTERPRISE_SOR`: `require_src_qualifier=False`, `bkcc_prefix=None`
     - `TIER_3_LOCAL_SCOPED`: `require_src_qualifier=True`, `bkcc_prefix=<rec_src>`
       (e.g., `'LEGACY_GP_ADMIN'` on `legacy_gp.src_legacy_lp_roster`)
  2. Nullable-FK Link Arity Splitter (`U_nn >= 0.95`):
     - Splits sparse/optional foreign keys (`U_nn < 0.95`, e.g., `co_invest_portco_lei`
       at `U_nn = 0.286 < 0.95`) out of N-ary Links (`lnk_lp_commitment`) into dedicated
       filtered Links (`lnk_lp_commitment_co_invest` with
       `where_filter_condition = 'co_invest_portco_lei IS NOT NULL'`).
"""

from __future__ import annotations

import copy
from typing import Any

GLOBAL_STANDARD_COLUMN_HINTS: frozenset[str] = frozenset(
    {"lei_code", "isin_code", "iban", "cusip", "figi_code", "swift_bic"}
)


def _extract_ontology_authority(
    ontology_graph: dict[str, Any],
) -> tuple[dict[str, dict[str, set[str]]], set[str]]:
    """Extract per-concept authority tiers and relationship URIs from Graph A."""
    authority_map: dict[str, dict[str, set[str]]] = {}
    relationship_uris: set[str] = set()
    if not isinstance(ontology_graph, dict):
        return authority_map, relationship_uris

    for rel in ontology_graph.get("canonical_relationships", []):
        if isinstance(rel, dict) and "relationship_uri" in rel:
            relationship_uris.add(str(rel["relationship_uri"]))

    for concept in ontology_graph.get("canonical_business_concepts", []):
        if not isinstance(concept, dict) or "concept_uri" not in concept:
            continue
        uri = str(concept["concept_uri"])
        tiers = concept.get("key_authority_tiers") or {}
        authority_map[uri] = {
            "tier1": {str(x) for x in tiers.get("tier_1_global_standards", [])},
            "tier2": {
                str(x)
                for x in (
                    tiers.get("tier_2_enterprise_sor", []) or concept.get("authoritative_sor", [])
                )
            },
            "tier3": {str(x) for x in tiers.get("tier_3_local_scoped_requires_bkcc", [])},
        }

    for uri, meta in ontology_graph.items():
        if isinstance(meta, dict) and uri not in authority_map:
            authority_map[str(uri)] = {
                "tier1": set(),
                "tier2": {str(x) for x in meta.get("authoritative_sor", [])},
                "tier3": set(),
            }

    return authority_map, relationship_uris


def _extract_bronze_cold_start_metrics(
    bronze_profile: dict[str, Any],
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Normalize column metrics and table collision diagnostics from Graph B."""
    col_metrics: dict[str, dict[str, dict[str, Any]]] = {}
    table_collisions: dict[str, dict[str, Any]] = {}
    if not isinstance(bronze_profile, dict):
        return col_metrics, table_collisions

    if "source_systems" in bronze_profile and isinstance(bronze_profile["source_systems"], list):
        for sys_entry in bronze_profile["source_systems"]:
            if not isinstance(sys_entry, dict):
                continue
            rec_src_code = str(sys_entry.get("record_source_code", ""))
            for tbl in sys_entry.get("tables", []):
                if not isinstance(tbl, dict):
                    continue
                b_table = str(tbl.get("bronze_table", ""))
                if not b_table:
                    continue
                if "collision_diagnostics" in tbl and isinstance(
                    tbl["collision_diagnostics"], dict
                ):
                    table_collisions[b_table] = dict(tbl["collision_diagnostics"])
                t_cols: dict[str, dict[str, Any]] = {}
                for col in tbl.get("columns", []):
                    if not isinstance(col, dict) or "column_name" not in col:
                        continue
                    c_name = str(col["column_name"])
                    non_null_ratio = col.get("non_null_ratio")
                    if non_null_ratio is None:
                        non_null_ratio = col.get("u_non_null")
                    if non_null_ratio is None and "null_rate" in col:
                        non_null_ratio = 1.0 - float(col["null_rate"])
                    if non_null_ratio is None:
                        non_null_ratio = 1.0
                    cand_role = str(col.get("candidate_role", ""))
                    pat_class = str(col.get("pattern_class", ""))
                    if not pat_class:
                        if (
                            cand_role == "TIER_1_BUSINESS_KEY"
                            or c_name in GLOBAL_STANDARD_COLUMN_HINTS
                        ):
                            pat_class = "GLOBAL_STANDARD_CODE"
                        elif cand_role == "TIER_3_LOCAL_SCOPED_KEY":
                            pat_class = "LOCAL_INTEGER_SEQ"
                    t_cols[c_name] = {
                        "pattern_class": pat_class,
                        "candidate_role": cand_role,
                        "non_null_ratio": float(non_null_ratio),
                        "record_source_code": rec_src_code,
                    }
                col_metrics[b_table] = t_cols
        return col_metrics, table_collisions

    for b_table, raw_tbl in bronze_profile.items():
        if not isinstance(raw_tbl, dict):
            continue
        t_cols = {}
        for col_name, metrics in raw_tbl.items():
            if not isinstance(metrics, dict):
                continue
            non_null_ratio = metrics.get("non_null_ratio")
            if non_null_ratio is None and "null_rate" in metrics:
                non_null_ratio = 1.0 - float(metrics["null_rate"])
            if non_null_ratio is None:
                non_null_ratio = 1.0
            t_cols[col_name] = {
                "pattern_class": str(metrics.get("pattern_class", "LOCAL_SEQUENCE")),
                "candidate_role": str(metrics.get("candidate_role", "")),
                "non_null_ratio": float(non_null_ratio),
            }
        col_metrics[b_table] = t_cols
    return col_metrics, table_collisions


def codemod_enforce_cold_start_extensibility(
    contract: dict[str, Any],
    ontology_graph: dict[str, Any],
    bronze_profile: dict[str, Any],
) -> dict[str, Any]:
    """Algorithm A.4: 3-Tier `BKCC` Authority & Nullable-FK Link Splitter (`U_nn >= 0.95`)."""
    ir = copy.deepcopy(contract)
    authority_map, relationship_uris = _extract_ontology_authority(ontology_graph)
    col_metrics, table_collisions = _extract_bronze_cold_start_metrics(bronze_profile)
    ledger: list[dict[str, Any]] = ir.get("column_triage_ledger", [])

    # 1. Enforce 3-Tier BKCC Authority on Hubs
    for hub in ir.get("hubs", []):
        uri = str(hub.get("ontology_concept_uri", ""))
        auth_info = authority_map.get(uri, {"tier1": set(), "tier2": set(), "tier3": set()})
        has_tier3_source = False

        for src in hub.get("business_key_sources", []):
            b_table = str(src.get("bronze_table", ""))
            key_cols = list(src.get("key_columns", []))
            if not key_cols:
                continue
            key_col = str(key_cols[0])
            rec_src = str(src.get("record_source_code", ""))
            c_prof = col_metrics.get(b_table, {}).get(key_col, {})
            pat_class = str(c_prof.get("pattern_class", "LOCAL_SEQUENCE"))
            cand_role = str(c_prof.get("candidate_role", ""))
            tbl_collision = table_collisions.get(b_table, {})

            is_tier1 = (
                pat_class == "GLOBAL_STANDARD_CODE"
                or cand_role == "TIER_1_BUSINESS_KEY"
                or rec_src in auth_info["tier1"]
                or key_col in GLOBAL_STANDARD_COLUMN_HINTS
            )
            is_tier3_forced = (
                cand_role == "TIER_3_LOCAL_SCOPED_KEY"
                or rec_src in auth_info["tier3"]
                or bool(tbl_collision.get("requires_tier3_bkcc_prefix"))
            )
            is_tier2 = not is_tier3_forced and (
                rec_src in auth_info["tier2"] or cand_role == "TIER_2_BUSINESS_KEY"
            )

            tier_key = (
                "key_authority_tier"
                if ("key_authority_tier" in src or ir.get("contract_version") == "5.0")
                else "bk_authority_tier"
            )
            if is_tier1:
                src[tier_key] = "TIER_1_GLOBAL_STANDARD"
                src["require_src_qualifier"] = False
                src["bkcc_prefix"] = None
            elif is_tier2:
                src[tier_key] = "TIER_2_ENTERPRISE_SOR"
                src["require_src_qualifier"] = False
                src["bkcc_prefix"] = None
            else:
                src[tier_key] = "TIER_3_LOCAL_SCOPED"
                src["require_src_qualifier"] = True
                src["bkcc_prefix"] = rec_src or "LEGACY_GP_ADMIN"
                if not src.get("system_scoped_bk_justification"):
                    src["system_scoped_bk_justification"] = (
                        f"Auto-qualified Tier-3 local-scoped business key on {hub.get('name')} "
                        "to prevent cross-system SHA-256 hash key collisions."
                    )
                has_tier3_source = True

        if (
            has_tier3_source
            and ir.get("contract_version") != "5.0"
            and not hub.get("system_scoped_bk_justification")
        ):
            hub["system_scoped_bk_justification"] = (
                f"Auto-qualified Tier-3 local-scoped business key on {hub.get('name')} "
                "to prevent cross-system SHA-256 hash key collisions."
            )

    # 2. Split Nullable Optional FKs (`U_nn < 0.95`) out of N-ary Links
    new_links: list[dict[str, Any]] = []
    existing_link_names = {str(lnk.get("name", "")) for lnk in ir.get("links", [])}

    for lnk in ir.get("links", []):
        refs = list(lnk.get("hub_references", []))
        if len(refs) < 3:
            new_links.append(lnk)
            continue

        b_table = str(lnk.get("source_bronze_table", ""))
        t_profile = col_metrics.get(b_table, {})

        mandatory_refs: list[dict[str, Any]] = []
        optional_refs: list[dict[str, Any]] = []

        for ref in refs:
            fk_cols = list(ref.get("source_fk_columns", []))
            if not fk_cols:
                mandatory_refs.append(ref)
                continue
            fk_col = str(fk_cols[0])
            c_info = t_profile.get(fk_col, {})
            non_null_ratio = float(c_info.get("non_null_ratio", 1.0))
            if non_null_ratio < 0.95:
                optional_refs.append(ref)
            else:
                mandatory_refs.append(ref)

        if len(mandatory_refs) >= 2 and optional_refs:
            base_lnk = copy.deepcopy(lnk)
            base_lnk["hub_references"] = mandatory_refs
            new_links.append(base_lnk)

            base_slug = str(lnk.get("name", "")).removeprefix("lnk_")
            for opt_ref in optional_refs:
                fk_col = str(opt_ref["source_fk_columns"][0])
                raw_role = str(opt_ref.get("role_alias", "opt")).lower()
                if raw_role.startswith("co_invest") or fk_col.lower().startswith("co_invest"):
                    role_slug = "co_invest"
                else:
                    role_slug = raw_role

                opt_lnk_name = f"lnk_{base_slug}_{role_slug}"
                if opt_lnk_name not in existing_link_names:
                    opt_lnk = copy.deepcopy(lnk)
                    opt_lnk["name"] = opt_lnk_name
                    is_co_invest = role_slug == "co_invest" and (
                        "urn:ontology:relationship:co_invest_allocation" in relationship_uris
                        or base_slug == "lp_commitment"
                    )
                    if is_co_invest:
                        opt_lnk["ontology_concept_uri"] = (
                            "urn:ontology:relationship:co_invest_allocation"
                        )
                        opt_lnk["hub_references"] = [*mandatory_refs, opt_ref]
                    else:
                        opt_lnk["hub_references"] = [mandatory_refs[0], opt_ref]
                    opt_lnk["where_filter_condition"] = f"{fk_col} IS NOT NULL"
                    new_links.append(opt_lnk)
                    existing_link_names.add(opt_lnk_name)

                for entry in ledger:
                    e_table = str(entry.get("bronze_table") or entry.get("source_table") or "")
                    e_target = str(
                        entry.get("target_dv_entity") or entry.get("target_entity") or ""
                    )
                    if (
                        e_table == b_table
                        and entry.get("column_name") == fk_col
                        and e_target == lnk.get("name")
                    ):
                        if "target_dv_entity" in entry:
                            entry["target_dv_entity"] = opt_lnk_name
                        if "target_entity" in entry or "target_dv_entity" not in entry:
                            entry["target_entity"] = opt_lnk_name
        else:
            new_links.append(lnk)

    ir["links"] = new_links
    return ir


def codemod_extract_hub_mutable_attributes(
    contract: dict[str, Any],
    mutable_columns_by_table: dict[str, set[str]],
) -> dict[str, Any]:
    """Codemod 1: Extract mutable attributes erroneously placed inside Hub `key_columns`
    into a companion `STANDARD_SATELLITE`.
    """
    ir = copy.deepcopy(contract)
    satellites: list[dict[str, Any]] = list(ir.get("satellites", []))
    ledger: list[dict[str, Any]] = ir.get("column_triage_ledger", [])

    for hub in ir.get("hubs", []):
        h_name = str(hub.get("name", ""))
        for src in hub.get("business_key_sources", []):
            b_table = str(src.get("bronze_table", ""))
            mut_set = mutable_columns_by_table.get(b_table, set())
            if not mut_set:
                continue
            orig_keys = list(src.get("key_columns", []))
            immutable_keys = [c for c in orig_keys if c not in mut_set]
            evicted_cols = [c for c in orig_keys if c in mut_set]
            if not evicted_cols or not immutable_keys:
                continue

            src["key_columns"] = immutable_keys
            target_sat = None
            for sat in satellites:
                if (
                    sat.get("parent_entity") == h_name
                    and sat.get("source_bronze_table") == b_table
                    and sat.get("satellite_type") == "STANDARD_SATELLITE"
                    and sat.get("security_classification") not in {"PII_HIGH", "PCI"}
                ):
                    target_sat = sat
                    break

            if target_sat is not None:
                target_sat["payload_columns"] = sorted(
                    set(list(target_sat.get("payload_columns", [])) + evicted_cols)
                )
                sat_name = str(target_sat["name"])
            else:
                slug = h_name.removeprefix("hub_")
                sat_name = f"sat_{slug}_core"
                satellites.append(
                    {
                        "name": sat_name,
                        "parent_entity": h_name,
                        "satellite_type": "STANDARD_SATELLITE",
                        "source_bronze_table": b_table,
                        "security_classification": "PUBLIC",
                        "volatility_tier": "CORE",
                        "sub_sequence_column": None,
                        "driving_key_column": None,
                        "payload_columns": sorted(set(evicted_cols)),
                        "tracked_columns": None,
                        "soft_deprecated_columns": [],
                    }
                )

            for entry in ledger:
                if (
                    entry.get("source_table") == b_table
                    and entry.get("column_name") in evicted_cols
                ):
                    entry["dv_target_role"] = "SATELLITE_PAYLOAD"
                    entry["target_entity"] = sat_name

    ir["satellites"] = satellites
    return ir


codemod_1_extract_hub_mutable_attributes = codemod_extract_hub_mutable_attributes
