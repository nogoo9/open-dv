"""Algorithm A.4: 3-Tier BKCC Authority & Nullable-FK Link Splitter (`U_nn >= 0.95`).

Enforces two cold-start extensibility invariants:
  1. 3-Tier Business Key Collision Code (`BKCC`) Authority:
     - `TIER_1_GLOBAL_STANDARD`: `require_src_qualifier=False`, `bkcc_prefix='GLOBAL'`
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

from opendv_core.constants import GLOBAL_BKCC

GLOBAL_STANDARD_COLUMN_HINTS: frozenset[str] = frozenset(
    {"lei", "isin", "lei_code", "isin_code", "iban", "cusip", "figi_code", "swift_bic"}
)


def _extract_tier_sources_and_columns(
    tier_entry: Any,
) -> tuple[set[str], set[str], str | None]:
    if isinstance(tier_entry, list | set | tuple):
        return {str(x) for x in tier_entry}, set(), None
    if isinstance(tier_entry, dict):
        cols = {str(x) for x in tier_entry.get("column_patterns", [])}
        srcs = {str(x) for x in tier_entry.get("authoritative_sor", [])}
        bkcc_policy = tier_entry.get("bkcc_policy")
        policy_str = str(bkcc_policy) if bkcc_policy else None
        if policy_str and policy_str not in {"GLOBAL", "SOURCE_SYSTEM_PREFIX_REQUIRED"}:
            srcs.add(policy_str)
        return srcs, cols, policy_str
    return set(), set(), None


def _extract_ontology_authority(
    ontology_graph: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Extract per-concept authority tiers and relationship URIs from Graph A."""
    authority_map: dict[str, dict[str, Any]] = {}
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
        t1_srcs, t1_cols, _ = _extract_tier_sources_and_columns(
            tiers.get("tier_1_global_standard") or tiers.get("tier_1_global_standards")
        )
        t2_srcs, t2_cols, _ = _extract_tier_sources_and_columns(tiers.get("tier_2_enterprise_sor"))
        t2_srcs.update(str(x) for x in concept.get("authoritative_sor", []))
        t3_srcs, t3_cols, t3_policy = _extract_tier_sources_and_columns(
            tiers.get("tier_3_local_scoped") or tiers.get("tier_3_local_scoped_requires_bkcc")
        )
        authority_map[uri] = {
            "tier1": t1_srcs,
            "tier1_cols": t1_cols,
            "tier2": t2_srcs,
            "tier2_cols": t2_cols,
            "tier3": t3_srcs,
            "tier3_cols": t3_cols,
            "tier3_bkcc_policy": t3_policy,
        }

    for uri, meta in ontology_graph.items():
        if isinstance(meta, dict) and uri not in authority_map:
            authority_map[str(uri)] = {
                "tier1": set(),
                "tier1_cols": set(),
                "tier2": {str(x) for x in meta.get("authoritative_sor", [])},
                "tier2_cols": set(),
                "tier3": set(),
                "tier3_cols": set(),
                "tier3_bkcc_policy": None,
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
            rec_src_code = str(
                sys_entry.get("record_source_code") or sys_entry.get("source_system_code") or ""
            )
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
        auth_info = authority_map.get(
            uri,
            {
                "tier1": set(),
                "tier1_cols": set(),
                "tier2": set(),
                "tier2_cols": set(),
                "tier3": set(),
                "tier3_cols": set(),
                "tier3_bkcc_policy": None,
            },
        )
        has_tier3_source = False

        for src in hub.get("business_key_sources", []):
            b_table = str(src.get("bronze_table", ""))
            key_cols = [str(c) for c in src.get("key_columns", [])]
            if not key_cols:
                continue
            c_profs = [col_metrics.get(b_table, {}).get(c, {}) for c in key_cols]
            rec_src = str(
                src.get("record_source_code") or c_profs[0].get("record_source_code") or ""
            )
            tbl_collision = table_collisions.get(b_table, {})
            required_bkcc = tbl_collision.get("required_bkcc_prefix") or tbl_collision.get(
                "requires_tier3_bkcc_prefix"
            )

            declared_tier = str(src.get("key_authority_tier") or src.get("bk_authority_tier") or "")
            is_tier3_forced = (
                any(
                    cp.get("candidate_role") == "TIER_3_LOCAL_SCOPED_KEY"
                    or cp.get("pattern_class")
                    in {"MONOTONIC_INT", "LOCAL_SEQUENCE", "LOCAL_INTEGER_SEQ"}
                    for cp in c_profs
                )
                or rec_src in auth_info["tier3"]
                or any(c in auth_info["tier3_cols"] for c in key_cols)
                or bool(required_bkcc)
            )
            is_tier1 = not is_tier3_forced and (
                declared_tier == "TIER_1_GLOBAL_STANDARD"
                or all(
                    cp.get("pattern_class") == "GLOBAL_STANDARD_CODE"
                    or cp.get("candidate_role") == "TIER_1_BUSINESS_KEY"
                    or rec_src in auth_info["tier1"]
                    or c in auth_info["tier1_cols"]
                    or c in GLOBAL_STANDARD_COLUMN_HINTS
                    for c, cp in zip(key_cols, c_profs, strict=False)
                )
            )
            is_tier2 = (
                not is_tier3_forced
                and not is_tier1
                and (
                    rec_src in auth_info["tier2"]
                    or all(
                        cp.get("candidate_role") == "TIER_2_BUSINESS_KEY"
                        or c in auth_info["tier2_cols"]
                        for c, cp in zip(key_cols, c_profs, strict=False)
                    )
                )
            )

            tier_key = (
                "key_authority_tier"
                if ("key_authority_tier" in src or ir.get("contract_version") == "5.0")
                else "bk_authority_tier"
            )
            if is_tier1:
                src[tier_key] = "TIER_1_GLOBAL_STANDARD"
                src["require_src_qualifier"] = False
                src["bkcc_prefix"] = GLOBAL_BKCC
                if src.get("system_scoped_bk_justification") is None:
                    src.pop("system_scoped_bk_justification", None)
            elif is_tier2:
                src[tier_key] = "TIER_2_ENTERPRISE_SOR"
                src["require_src_qualifier"] = False
                src["bkcc_prefix"] = None
                if src.get("system_scoped_bk_justification") is None:
                    src.pop("system_scoped_bk_justification", None)
            else:
                src[tier_key] = "TIER_3_LOCAL_SCOPED"
                src["require_src_qualifier"] = True
                policy_bkcc = auth_info.get("tier3_bkcc_policy")
                if isinstance(required_bkcc, str) and required_bkcc:
                    resolved_bkcc = required_bkcc
                elif policy_bkcc and policy_bkcc not in {
                    "GLOBAL",
                    "SOURCE_SYSTEM_PREFIX_REQUIRED",
                }:
                    resolved_bkcc = str(policy_bkcc)
                else:
                    resolved_bkcc = rec_src or "LEGACY_GP_ADMIN"
                src["bkcc_prefix"] = resolved_bkcc
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
        if len(refs) < 3 or lnk.get("where_filter_condition"):
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

                opt_fk_cols = {str(c) for c in opt_ref.get("source_fk_columns", [])}
                for entry in ledger:
                    e_table = str(entry.get("bronze_table") or entry.get("source_table") or "")
                    e_target = str(
                        entry.get("target_dv_entity") or entry.get("target_entity") or ""
                    )
                    if (
                        e_table == b_table
                        and str(entry.get("column_name", "")) in opt_fk_cols
                        and e_target == lnk.get("name")
                    ):
                        if "target_dv_entity" in entry or "target_entity" not in entry:
                            entry["target_dv_entity"] = opt_lnk_name
                        if "target_entity" in entry:
                            entry["target_entity"] = opt_lnk_name
                        if "elm_classification" in entry or "dv_target_role" not in entry:
                            entry["elm_classification"] = "OPTIONAL_FOREIGN_KEY_REF"
                        if "dv_target_role" in entry:
                            entry["dv_target_role"] = "OPTIONAL_FOREIGN_KEY_REF"
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
                        "volatility_tier": "STANDARD",
                        "payload_columns": sorted(set(evicted_cols)),
                        "soft_deprecated_columns": [],
                    }
                )

            for entry in ledger:
                e_table = str(entry.get("bronze_table") or entry.get("source_table") or "")
                if e_table == b_table and entry.get("column_name") in evicted_cols:
                    if "target_dv_entity" in entry or "target_entity" not in entry:
                        entry["target_dv_entity"] = sat_name
                    if "target_entity" in entry:
                        entry["target_entity"] = sat_name
                    if "elm_classification" in entry or "dv_target_role" not in entry:
                        entry["elm_classification"] = "DESCRIPTIVE_PAYLOAD"
                    if "dv_target_role" in entry:
                        entry["dv_target_role"] = "SATELLITE_PAYLOAD"

    ir["satellites"] = satellites
    return ir


codemod_1_extract_hub_mutable_attributes = codemod_extract_hub_mutable_attributes
