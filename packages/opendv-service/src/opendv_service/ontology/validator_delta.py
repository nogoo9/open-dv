"""Deterministic OntologyDelta validation against base Graph A and deployed contract."""

from __future__ import annotations

from typing import Any

from opendv_contracts.dtos.ontology import (
    AddConceptOp,
    AddGovernanceConstraintOp,
    AddKeyAuthorityPatternOp,
    AddReferenceEnumerationOp,
    AddRelationshipOp,
    OntologyDelta,
    OntologyValidationResult,
    RefineDefinitionOp,
)
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.ontology.validator_deployment import (
    _extract_deployed_contract_mappings,
    build_deployment_provider_from_contract,
)
from opendv_service.ontology.validator_models import ContractViolationError
from opendv_service.ontology.validator_structural import StructuralValidator


def validate_ontology_delta(
    base: GraphAOntology,
    delta: OntologyDelta,
    deployed_contract: Any | None = None,
) -> OntologyValidationResult:
    """Deterministically validate an OntologyDelta against base Graph A and deployed contract."""
    from opendv_service.ontology.validator_governance import GovernanceValidator

    violations: list[str] = []
    diagnostics: list[str] = []

    validator = StructuralValidator()
    delta_dump = delta.model_dump(mode="json", exclude_none=True)
    if isinstance(delta_dump.get("metadata"), dict) and (
        "staged_candidates" in delta_dump["metadata"]
    ):
        meta_copy = dict(delta_dump["metadata"])
        meta_copy.pop("staged_candidates", None)
        delta_dump["metadata"] = meta_copy
    prov_uris = validator.scan_provisional_tokens(delta_dump)
    for prov in sorted(set(prov_uris)):
        violations.append(
            f"ONT-EVO-002: Provisional URI '{prov}' is strictly forbidden in "
            "accepted ontology deltas."
        )

    deployed_hubs, deployed_links, deployed_refs = _extract_deployed_contract_mappings(
        deployed_contract
    )
    if deployed_contract is not None:
        provider = build_deployment_provider_from_contract(deployed_contract)
        try:
            GovernanceValidator(deployment_provider=provider).validate_deployed_contracts(
                base, delta
            )
        except ContractViolationError as exc:
            violations.append(f"ONT-EVO-001: {exc.message}")

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
                    f"ONT-EVO-001: Breaking change detected for deployed contract: "
                    f"Relationship '{op.relationship_uri}' is deployed to link '{deployed_link}' "
                    f"and cannot be rebound to '{op.target_link}'."
                )
        elif isinstance(op, AddReferenceEnumerationOp):
            suffix = op.name or (op.target_reference_table or "").replace("ref_", "")
            enum_uri = op.enum_uri or f"urn:ontology:enum:{suffix}"
            if enum_uri in deployed_refs:
                deployed_ref = deployed_refs[enum_uri]
                computed_table = (
                    f"ref_{op.name}"
                    if getattr(op, "name", None) and not op.name.startswith("ref_")
                    else getattr(op, "name", "")
                )
                target_table = op.target_reference_table or computed_table
                if target_table and target_table != deployed_ref:
                    violations.append(
                        f"ONT-EVO-001: Breaking change detected for deployed contract: Reference "
                        f"enumeration '{enum_uri}' is deployed to reference table '{deployed_ref}' "
                        f"and cannot be rebound to '{target_table}'."
                    )

    base_concepts = {c.concept_uri: c for c in base.canonical_business_concepts}
    base_rels = {r.relationship_uri: r for r in base.canonical_relationships}
    base_enums = {e.enum_uri: e for e in base.reference_enumerations}
    hub_to_concept: dict[str, str] = {
        c.target_hub: c.concept_uri for c in base.canonical_business_concepts if c.target_hub
    }

    delta_concept_ops: dict[str, list[AddConceptOp]] = {}
    for op in delta.operations:
        if isinstance(op, AddConceptOp):
            delta_concept_ops.setdefault(op.concept_uri, []).append(op)
            if op.concept_uri in base_concepts:
                existing = base_concepts[op.concept_uri]
                if existing.target_hub and op.target_hub and existing.target_hub != op.target_hub:
                    violations.append(
                        f"ONT-EVO-003: Non-additive conflict: Concept '{op.concept_uri}' already "
                        f"exists in base ontology with target_hub '{existing.target_hub}', "
                        f"conflicting with delta target_hub '{op.target_hub}'."
                    )
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

    for uri, ops in delta_concept_ops.items():
        if len(ops) > 1:
            hubs = {o.target_hub for o in ops if o.target_hub}
            if len(hubs) > 1:
                violations.append(
                    f"ONT-EVO-003: Non-additive conflict: Multiple AddConceptOp for '{uri}' "
                    f"specify conflicting target_hubs: {sorted(hubs)}."
                )

    known_concept_uris = set(base_concepts.keys()) | set(delta_concept_ops.keys())

    for op in delta.operations:
        if isinstance(op, AddRelationshipOp):
            if op.relationship_uri in base_rels:
                existing_r = base_rels[op.relationship_uri]
                if op.target_link and op.target_link != existing_r.target_link:
                    violations.append(
                        f"ONT-EVO-003: Non-additive conflict: Relationship '{op.relationship_uri}' "
                        f"already exists with target_link '{existing_r.target_link}', "
                        f"cannot rebind to '{op.target_link}'."
                    )
                op_eps = list(op.endpoints)
                if op.source_concept_uri and op.source_concept_uri not in op_eps:
                    op_eps.append(op.source_concept_uri)
                if op.target_concept_uri and op.target_concept_uri not in op_eps:
                    op_eps.append(op.target_concept_uri)
                if op_eps and set(op_eps) != set(existing_r.endpoints):
                    violations.append(
                        f"ONT-EVO-003: Non-additive conflict: Cannot rewrite endpoints for "
                        f"existing relationship '{op.relationship_uri}' from "
                        f"{existing_r.endpoints} to {op_eps}."
                    )

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

        elif isinstance(op, AddReferenceEnumerationOp):
            suffix = op.name or (op.target_reference_table or "").replace("ref_", "")
            enum_uri = op.enum_uri or f"urn:ontology:enum:{suffix}"
            if enum_uri in base_enums:
                existing_e = base_enums[enum_uri]
                computed_table = (
                    f"ref_{op.name}"
                    if getattr(op, "name", None) and not op.name.startswith("ref_")
                    else getattr(op, "name", "")
                )
                table_name = op.target_reference_table or computed_table
                if table_name and table_name != existing_e.target_reference_table:
                    violations.append(
                        f"ONT-EVO-003: Non-additive conflict: Reference enumeration '{enum_uri}' "
                        f"already exists with table '{existing_e.target_reference_table}', "
                        f"cannot rebind to '{table_name}'."
                    )
                op_codes = set(op.allowed_codes or op.values or [])
                existing_codes = set(existing_e.allowed_codes)
                if op_codes and not existing_codes.issubset(op_codes):
                    dropped = existing_codes - op_codes
                    violations.append(
                        f"ONT-EVO-003: Non-additive conflict: Cannot shrink or drop existing "
                        f"allowed_codes {sorted(dropped)} for enumeration '{enum_uri}'."
                    )

        elif isinstance(op, AddKeyAuthorityPatternOp):
            if op.concept_uri not in known_concept_uris:
                violations.append(
                    f"ONT-EVO-003: Dangling concept target in AddKeyAuthorityPatternOp "
                    f"'{op.op_id}': Concept URI '{op.concept_uri}' does not exist in base "
                    "ontology or delta."
                )
            elif op.concept_uri in base_concepts:
                c = base_concepts[op.concept_uri]
                if c.key_authority_tiers is not None:
                    tier_str = (op.tier or "TIER_1_GLOBAL_STANDARD").lower()
                    if "tier_1" in tier_str or "global" in tier_str:
                        existing_spec = c.key_authority_tiers.tier_1_global_standard
                        tier_name = "tier_1_global_standard"
                    elif "tier_2" in tier_str or "enterprise" in tier_str or "sor" in tier_str:
                        existing_spec = c.key_authority_tiers.tier_2_enterprise_sor
                        tier_name = "tier_2_enterprise_sor"
                    else:
                        existing_spec = c.key_authority_tiers.tier_3_local_scoped
                        tier_name = "tier_3_local_scoped"
                    if (
                        existing_spec is not None
                        and existing_spec.authority_name != op.authority_name
                    ):
                        violations.append(
                            f"ONT-EVO-003: Non-additive conflict: Concept '{op.concept_uri}' "
                            f"already has authority '{existing_spec.authority_name}' for "
                            f"{tier_name}; cannot overwrite with '{op.authority_name}'."
                        )

        elif isinstance(op, AddGovernanceConstraintOp):
            if op.concept_uri not in known_concept_uris:
                violations.append(
                    f"ONT-EVO-003: Dangling concept target in AddGovernanceConstraintOp "
                    f"'{op.op_id}': Concept URI '{op.concept_uri}' does not exist in base "
                    "ontology or delta."
                )

        elif isinstance(op, RefineDefinitionOp):
            target_uri = op.target_uri or op.concept_uri
            if target_uri not in known_concept_uris:
                violations.append(
                    f"ONT-EVO-003: Dangling concept target in RefineDefinitionOp '{op.op_id}': "
                    f"Concept URI '{target_uri}' does not exist in base ontology or delta."
                )

    if delta.parent_sha256 and delta.parent_sha256.strip():
        base_sha256 = compute_ontology_sha256(base)
        if delta.parent_sha256.lower() != base_sha256.lower():
            violations.append(
                f"ONT-EVO-003: Lineage mismatch: delta parent_sha256 '{delta.parent_sha256}' "
                f"does not match base ontology SHA-256 '{base_sha256}'."
            )

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


__all__ = ["validate_ontology_delta"]
