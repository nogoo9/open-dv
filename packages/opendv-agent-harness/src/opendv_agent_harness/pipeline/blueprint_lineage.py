"""Blueprint lineage advancement and contract evolution diff utilities.

Conforms to ADR-018, DV-EVO-001..008, and single-run blueprint evolution contracts.
"""

from __future__ import annotations

from typing import Any

from opendv_core.models.contract_v5 import NoMistakeDataVault2ContractV5
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.models.vault_blueprint import (
    VaultBlueprint,
    compute_blueprint_sha256,
    create_vault_blueprint,
)


def compute_blueprint_evolution_diff(
    prior_contract: dict[str, Any] | None,
    new_contract: dict[str, Any] | None,
    ontology: GraphAOntology | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute deterministic structural diff between prior and newly evolved DV contracts."""
    if not new_contract:
        return {"added_hubs": [], "added_links": [], "added_satellites": [], "drift_events": []}

    existing_concepts: set[str] = set()
    existing_relationships: set[str] = set()
    ont_obj = ontology or (new_contract.get("ontology") if new_contract else None)
    if ont_obj is None and prior_contract and isinstance(prior_contract, dict):
        ont_obj = prior_contract.get("ontology")

    if ont_obj is not None:
        if isinstance(ont_obj, GraphAOntology):
            for c in ont_obj.canonical_business_concepts:
                if c.target_hub:
                    existing_concepts.add(c.target_hub)
                existing_concepts.add(c.concept_uri)
                if c.concept_name:
                    existing_concepts.add(c.concept_name.lower().replace(" ", "_"))
            rels = (
                getattr(ont_obj, "canonical_relationships", None)
                or getattr(ont_obj, "canonical_business_relationships", None)
                or []
            )
            for rel in rels:
                if rel.target_link:
                    existing_relationships.add(rel.target_link)
                existing_relationships.add(rel.relationship_uri)
                if rel.relationship_name:
                    existing_relationships.add(rel.relationship_name.lower().replace(" ", "_"))
        elif isinstance(ont_obj, dict):
            for c_raw in ont_obj.get("canonical_business_concepts", []):
                if isinstance(c_raw, dict):
                    if c_raw.get("target_hub"):
                        existing_concepts.add(c_raw["target_hub"])
                    if c_raw.get("concept_uri"):
                        existing_concepts.add(c_raw["concept_uri"])
                    if c_raw.get("concept_name"):
                        existing_concepts.add(str(c_raw["concept_name"]).lower().replace(" ", "_"))
            dict_rels = (
                ont_obj.get("canonical_relationships")
                or ont_obj.get("canonical_business_relationships")
                or []
            )
            for r_raw in dict_rels:
                if isinstance(r_raw, dict):
                    if r_raw.get("target_link"):
                        existing_relationships.add(r_raw["target_link"])
                    if r_raw.get("relationship_uri"):
                        existing_relationships.add(r_raw["relationship_uri"])
                    if r_raw.get("relationship_name"):
                        existing_relationships.add(
                            str(r_raw["relationship_name"]).lower().replace(" ", "_")
                        )

    if prior_contract and isinstance(prior_contract, dict):
        for h_prior in prior_contract.get("hubs", []):
            if isinstance(h_prior, dict) and h_prior.get("name"):
                h_name = str(h_prior["name"])
                existing_concepts.add(h_name)
                h_slug = h_name.replace("hub_", "")
                existing_concepts.add(h_slug)
                existing_concepts.add(f"urn:ontology:entity:{h_slug}")
        for l_prior in prior_contract.get("links", []):
            if isinstance(l_prior, dict) and l_prior.get("name"):
                l_name = str(l_prior["name"])
                existing_relationships.add(l_name)
                l_slug = l_name.replace("lnk_", "")
                existing_relationships.add(l_slug)
                existing_relationships.add(f"urn:ontology:relationship:{l_slug}")

    p_hubs = {
        h.get("name")
        for h in (prior_contract.get("hubs") if prior_contract else [])
        if isinstance(h, dict)
    }
    p_links = {
        lnk.get("name")
        for lnk in (prior_contract.get("links") if prior_contract else [])
        if isinstance(lnk, dict)
    }
    p_sats = {
        s.get("name")
        for s in (prior_contract.get("satellites") if prior_contract else [])
        if isinstance(s, dict)
    }

    n_hubs = [
        h.get("name")
        for h in new_contract.get("hubs", [])
        if isinstance(h, dict) and h.get("name") not in p_hubs
    ]
    n_links = [
        lnk.get("name")
        for lnk in new_contract.get("links", [])
        if isinstance(lnk, dict) and lnk.get("name") not in p_links
    ]
    n_sats = [
        s.get("name")
        for s in new_contract.get("satellites", [])
        if isinstance(s, dict) and s.get("name") not in p_sats
    ]

    companions = [
        s.get("name")
        for s in new_contract.get("satellites", [])
        if isinstance(s, dict) and s.get("volatility_tier") == "ADDITIVE_DRIFT_COMPANION"
    ]

    widened_columns: list[dict[str, Any]] = []
    for entry in new_contract.get("column_triage_ledger", []):
        if isinstance(entry, dict):
            disp = str(entry.get("disposition") or entry.get("triage_action") or "").upper()
            if "WIDEN" in disp or entry.get("is_widened"):
                widened_columns.append({
                    "table_name": entry.get("target_satellite") or entry.get("table_name", ""),
                    "column_name": entry.get("column_name") or entry.get("target_column", ""),
                    "prior_type": entry.get("prior_type") or entry.get("from_type", ""),
                    "new_type": entry.get("physical_type") or entry.get("to_type", ""),
                })
    if prior_contract:
        prior_sat_cols = {
            s.get("name"): {
                c.get("name"): c.get("data_type") or c.get("physical_type")
                for c in s.get("columns", [])
                if isinstance(c, dict) and c.get("name")
            }
            for s in prior_contract.get("satellites", [])
            if isinstance(s, dict) and s.get("name")
        }
        for s in new_contract.get("satellites", []):
            if not isinstance(s, dict):
                continue
            s_name = s.get("name")
            if s_name in prior_sat_cols:
                p_cols = prior_sat_cols[s_name]
                for c in s.get("columns", []):
                    if not isinstance(c, dict):
                        continue
                    c_name = c.get("name")
                    c_type = c.get("data_type") or c.get("physical_type")
                    if c_name in p_cols and p_cols[c_name] != c_type:
                        widened_columns.append({
                            "table_name": s_name,
                            "column_name": c_name,
                            "prior_type": p_cols[c_name],
                            "new_type": c_type,
                        })

    ontology_deltas: list[dict[str, Any]] = []
    if new_contract.get("ontology_deltas_applied"):
        ontology_deltas.extend(new_contract.get("ontology_deltas_applied", []))
    elif new_contract.get("ontology_deltas_proposed"):
        ontology_deltas.extend(new_contract.get("ontology_deltas_proposed", []))
    else:
        for h in n_hubs:
            concept_slug = h.replace("hub_", "")
            c_uri = f"urn:ontology:entity:{concept_slug}"
            slug_norm = concept_slug.rstrip("s")
            if (
                h in existing_concepts
                or c_uri in existing_concepts
                or concept_slug in existing_concepts
                or slug_norm in existing_concepts
                or f"hub_{slug_norm}" in existing_concepts
                or f"urn:ontology:entity:{slug_norm}" in existing_concepts
            ):
                continue
            ontology_deltas.append({
                "op_type": "add_concept",
                "op_id": f"op-hub-{concept_slug}",
                "concept_uri": c_uri,
                "concept_name": concept_slug.replace("_", " ").title(),
                "target_hub": h,
            })
        for lnk_name in n_links:
            rel_slug = lnk_name.replace("lnk_", "")
            r_uri = f"urn:ontology:relationship:{rel_slug}"
            if (
                lnk_name in existing_relationships
                or r_uri in existing_relationships
                or rel_slug in existing_relationships
            ):
                continue
            lnk_def = next(
                (
                    lnk
                    for lnk in new_contract.get("links", [])
                    if isinstance(lnk, dict) and lnk.get("name") == lnk_name
                ),
                None,
            )
            refs = (lnk_def.get("hub_references") or lnk_def.get("hubs") or []) if lnk_def else []
            endpoints: list[str] = []
            for r in refs:
                r_name = (
                    r.get("hub") or r.get("name") or r.get("target_hub") or r.get("table") or ""
                    if isinstance(r, dict)
                    else str(r)
                )
                if r_name:
                    endpoints.append(f"urn:ontology:entity:{r_name.replace('hub_', '')}")
            if len(endpoints) >= 2:
                ontology_deltas.append({
                    "op_type": "add_relationship",
                    "op_id": f"op-lnk-{rel_slug}",
                    "relationship_uri": r_uri,
                    "relationship_name": rel_slug.replace("_", " ").title(),
                    "target_link": lnk_name,
                    "endpoints": endpoints,
                })
        for s_name in n_sats:
            sat_slug = s_name.replace("sat_", "")
            s_def = next(
                (
                    s
                    for s in new_contract.get("satellites", [])
                    if isinstance(s, dict) and s.get("name") == s_name
                ),
                None,
            )
            if not s_def:
                continue
            parent = s_def.get("parent_entity") or s_def.get("parent_hub") or ""
            if not parent or str(parent).startswith("lnk_"):
                continue
            parent_concept = parent.replace("hub_", "")
            parent_hub_def = next(
                (
                    h
                    for h in new_contract.get("hubs", [])
                    if isinstance(h, dict)
                    and (
                        h.get("name") == parent
                        or h.get("hub_name") == parent
                        or h.get("name") == f"hub_{parent_concept}"
                    )
                ),
                None,
            )
            concept_uri = (
                parent_hub_def.get("ontology_concept_uri")
                if parent_hub_def and parent_hub_def.get("ontology_concept_uri")
                else f"urn:ontology:entity:{parent_concept}"
            )
            ontology_deltas.append({
                "op_type": "add_attribute",
                "op_id": f"op-sat-{sat_slug}",
                "concept_uri": concept_uri,
                "attribute_name": sat_slug,
                "data_type": "string",
                "description": f"Attribute evolved from satellite {s_name}",
                "is_optional": True,
            })

    return {
        "added_hubs": sorted(n_hubs),
        "added_links": sorted(n_links),
        "added_satellites": sorted(n_sats),
        "companion_satellites": sorted(companions),
        "widened_columns": widened_columns,
        "ontology_deltas_applied": ontology_deltas,
        "total_ledger_columns": len(new_contract.get("column_triage_ledger", [])),
    }


def advance_blueprint_lineage(
    prior_blueprint: VaultBlueprint | None,
    ontology: GraphAOntology,
    contract: NoMistakeDataVault2ContractV5 | dict[str, Any] | None,
    domain_namespace: str = "default",
    lineage_notes: str | None = None,
) -> VaultBlueprint:
    """Create updated VaultBlueprint vN+1 cryptographically chained to prior_blueprint."""
    prior_sha: str | None = None
    next_rev = 1
    if prior_blueprint is not None:
        prior_sha = compute_blueprint_sha256(prior_blueprint)
        next_rev = prior_blueprint.lineage.blueprint_revision + 1
        domain_namespace = prior_blueprint.domain_namespace or domain_namespace

    cnt_model = (
        NoMistakeDataVault2ContractV5.model_validate(contract)
        if isinstance(contract, dict)
        else contract
    )

    return create_vault_blueprint(
        domain_namespace=domain_namespace,
        ontology=ontology,
        contract=cnt_model,
        parent_blueprint_sha256=prior_sha,
        blueprint_revision=next_rev,
        lineage_notes=lineage_notes or f"Single-run evolution revision {next_rev}",
    )


__all__ = [
    "advance_blueprint_lineage",
    "compute_blueprint_evolution_diff",
]
