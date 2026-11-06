"""Deterministic ontology delta validation and evolution lock (ONT-EVO-001..003)."""

from __future__ import annotations

from typing import Any

from opendv_contracts.dtos.ontology import (
    AddConceptOp,
    AddRelationshipOp,
    OntologyDelta,
    OntologyValidationResult,
)
from opendv_core.models.graph_a import GraphAOntology
from opendv_service.ontology.hashing import compute_ontology_sha256


def _extract_deployed_contract_mappings(
    deployed_contract: Any,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Extract (concept_uri -> hub), (rel_uri -> link), (enum_uri -> ref_table)."""
    deployed_hubs: dict[str, str] = {}
    deployed_links: dict[str, str] = {}
    deployed_refs: dict[str, str] = {}

    if deployed_contract is None:
        return deployed_hubs, deployed_links, deployed_refs

    # Support Pydantic model or dict
    doc = (
        deployed_contract.model_dump(mode="json")
        if hasattr(deployed_contract, "model_dump")
        else (deployed_contract if isinstance(deployed_contract, dict) else {})
    )

    for hub in doc.get("hubs", []):
        if isinstance(hub, dict) and "ontology_concept_uri" in hub and "name" in hub:
            deployed_hubs[hub["ontology_concept_uri"]] = hub["name"]

    for link in doc.get("links", []):
        if isinstance(link, dict) and "ontology_concept_uri" in link and "name" in link:
            deployed_links[link["ontology_concept_uri"]] = link["name"]

    for ref in doc.get("reference_tables", []):
        if isinstance(ref, dict) and "ontology_concept_uri" in ref and "name" in ref:
            deployed_refs[ref["ontology_concept_uri"]] = ref["name"]

    return deployed_hubs, deployed_links, deployed_refs


def _check_provisional_uri(val: Any) -> str | None:
    """Return the provisional URI string if val contains provisional prefix, else None."""
    if isinstance(val, str) and "urn:ontology:provisional:" in val:
        return val
    return None


def validate_ontology_delta(
    base: GraphAOntology,
    delta: OntologyDelta,
    deployed_contract: Any | None = None,
) -> OntologyValidationResult:
    """Deterministically validate an OntologyDelta against base Graph A and deployed contract.

    Enforces:
    - ONT-EVO-001: Breaking change check against deployed contract entities.
    - ONT-EVO-002: Strict rejection of provisional URIs (urn:ontology:provisional:*).
    - ONT-EVO-003: Non-additive conflict rejection (target_hub rebinding, anti-silo collisions,
                   dangling endpoints, lineage mismatch).
    """
    violations: list[str] = []
    diagnostics: list[str] = []

    # 1. ONT-EVO-002: Strict rejection of provisional URIs across all operations
    for op in delta.operations:
        op_dict = op.model_dump()
        for key, val in op_dict.items():
            if isinstance(val, str):
                prov = _check_provisional_uri(val)
                if prov:
                    violations.append(
                        f"ONT-EVO-002: Provisional URI '{prov}' in field '{key}' of operation "
                        f"'{op.op_id}' is strictly forbidden in accepted ontology deltas."
                    )
            elif isinstance(val, list):
                for item in val:
                    prov = _check_provisional_uri(item)
                    if prov:
                        violations.append(
                            f"ONT-EVO-002: Provisional URI '{prov}' in list '{key}' of operation "
                            f"'{op.op_id}' is strictly forbidden in accepted ontology deltas."
                        )

    # 2. ONT-EVO-001: Breaking change check against deployed contract
    deployed_hubs, deployed_links, _ = _extract_deployed_contract_mappings(deployed_contract)

    for op in delta.operations:
        if isinstance(op, AddConceptOp):
            if op.concept_uri in deployed_hubs:
                deployed_hub = deployed_hubs[op.concept_uri]
                if op.target_hub and op.target_hub != deployed_hub:
                    violations.append(
                        f"ONT-EVO-001: Breaking change detected for deployed contract: Concept "
                        f"'{op.concept_uri}' is deployed to hub '{deployed_hub}' and cannot be "
                        f"rebound to '{op.target_hub}'."
                    )
        elif isinstance(op, AddRelationshipOp) and op.relationship_uri in deployed_links:
            deployed_link = deployed_links[op.relationship_uri]
            if op.target_link and op.target_link != deployed_link:
                violations.append(
                    "ONT-EVO-001: Breaking change detected for deployed contract: "
                    f"Relationship '{op.relationship_uri}' is deployed to link '{deployed_link}' "
                    f"and cannot be rebound to '{op.target_link}'."
                )

    # 3. ONT-EVO-003: Non-additive conflict and anti-silo checks
    base_concepts = {c.concept_uri: c for c in base.canonical_business_concepts}
    hub_to_concept: dict[str, str] = {
        c.target_hub: c.concept_uri for c in base.canonical_business_concepts if c.target_hub
    }

    # Track concept additions in delta
    delta_concept_ops: dict[str, list[AddConceptOp]] = {}

    for op in delta.operations:
        if isinstance(op, AddConceptOp):
            delta_concept_ops.setdefault(op.concept_uri, []).append(op)

            # Check conflict with base
            if op.concept_uri in base_concepts:
                existing = base_concepts[op.concept_uri]
                if existing.target_hub and op.target_hub and existing.target_hub != op.target_hub:
                    violations.append(
                        f"ONT-EVO-003: Non-additive conflict: Concept '{op.concept_uri}' already "
                        f"exists in base ontology with target_hub '{existing.target_hub}', "
                        f"conflicting with delta target_hub '{op.target_hub}'."
                    )

            # Anti-silo check: One Hub per Canonical Business Concept
            if op.target_hub:
                if op.target_hub in hub_to_concept:
                    bound_concept = hub_to_concept[op.target_hub]
                    if bound_concept != op.concept_uri:
                        violations.append(
                            f"ONT-EVO-003: Anti-silo violation: Hub '{op.target_hub}' is already "
                            f"bound to concept '{bound_concept}'; cannot bind distinct concept "
                            f"'{op.concept_uri}'."
                        )
                else:
                    hub_to_concept[op.target_hub] = op.concept_uri

    # Check multiple AddConceptOps for same URI within delta
    for uri, ops in delta_concept_ops.items():
        if len(ops) > 1:
            hubs = {o.target_hub for o in ops if o.target_hub}
            if len(hubs) > 1:
                violations.append(
                    f"ONT-EVO-003: Non-additive conflict: Multiple AddConceptOp for '{uri}' "
                    f"specify conflicting target_hubs: {sorted(hubs)}."
                )

    # Dangling endpoint validation for relationships
    known_concept_uris = set(base_concepts.keys()) | set(delta_concept_ops.keys())

    for op in delta.operations:
        if isinstance(op, AddRelationshipOp):
            endpoints = list(op.endpoints)
            if op.source_concept_uri and op.source_concept_uri not in endpoints:
                endpoints.append(op.source_concept_uri)
            if op.target_concept_uri and op.target_concept_uri not in endpoints:
                endpoints.append(op.target_concept_uri)

            if not endpoints:
                violations.append(
                    f"ONT-EVO-003: Relationship '{op.relationship_uri}' has no "
                    "endpoint concepts specified."
                )
            for ep in endpoints:
                if ep not in known_concept_uris:
                    violations.append(
                        f"ONT-EVO-003: Dangling endpoint in relationship '{op.relationship_uri}': "
                        f"Concept URI '{ep}' does not exist in base ontology or delta."
                    )

    # Lineage check (parent_sha256 if supplied)
    if delta.parent_sha256 and delta.parent_sha256.strip():
        base_sha256 = compute_ontology_sha256(base)
        if delta.parent_sha256.lower() != base_sha256.lower():
            violations.append(
                f"ONT-EVO-003: Lineage mismatch: delta parent_sha256 '{delta.parent_sha256}' "
                f"does not match base ontology SHA-256 '{base_sha256}'."
            )

    # Determine primary error code if any
    error_code = None
    if violations:
        if any("ONT-EVO-001" in v for v in violations):
            error_code = "ONT-EVO-001"
        elif any("ONT-EVO-002" in v for v in violations):
            error_code = "ONT-EVO-002"
        elif any("ONT-EVO-003" in v for v in violations):
            error_code = "ONT-EVO-003"

    diagnostics.append(
        f"Validation complete: {len(delta.operations)} operations evaluated, "
        f"{len(violations)} violations detected."
    )

    return OntologyValidationResult(
        valid=len(violations) == 0,
        diagnostics=diagnostics,
        violations=violations,
        error_code=error_code,
    )
