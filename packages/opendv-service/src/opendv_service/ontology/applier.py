"""Deterministic ontology delta applier with lineage tracking."""

from __future__ import annotations

import re

from opendv_contracts.dtos.ontology import (
    AddConceptOp,
    AddGovernanceConstraintOp,
    AddKeyAuthorityPatternOp,
    AddReferenceEnumerationOp,
    AddRelationshipOp,
    AddSourceHintOp,
    OntologyDelta,
    RefineDefinitionOp,
)
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    GovernanceConstraints,
    GraphAOntology,
    KeyAuthorityTiers,
    KeyAuthorityTierSpec,
    ReferenceEnumeration,
    YagniCostGuardPolicy,
)
from opendv_service.ontology.hashing import compute_ontology_sha256
from opendv_service.ontology.validator import validate_ontology_delta


def _increment_semver_minor(version_str: str) -> str:
    """Increment minor version in semver string (e.g. '1.0.0' -> '1.1.0')."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version_str.strip())
    if match:
        major, minor, _patch = match.groups()
        return f"{major}.{int(minor) + 1}.0"
    return f"{version_str}.1"


def apply_ontology_delta(
    base: GraphAOntology,
    delta: OntologyDelta,
    accepted_op_ids: list[str] | None = None,
    decision_id: str | None = None,
    target_version: str | None = None,
) -> GraphAOntology:
    """Deterministically apply accepted delta operations to a base Graph A ontology.

    - Computes parent_sha256 = base_sha256.
    - Validates operations against ONT-EVO evolution locks.
    - Applies additive changes in a canonical order.
    - Lexicographically sorts concepts, relationships, and enumerations for deterministic hashing.
    - Appends decision_id to decision_refs.
    - Increments or assigns target ontology version.
    """
    # 1. Determine accepted operations
    if accepted_op_ids is not None:
        accepted_set = set(accepted_op_ids)
        ops_to_apply = [op for op in delta.operations if op.op_id in accepted_set]
    else:
        ops_to_apply = list(delta.operations)

    # 2. Validate operations against base
    sub_delta = OntologyDelta(
        delta_id=delta.delta_id,
        parent_sha256=delta.parent_sha256,
        domain_namespace=delta.domain_namespace,
        base_ontology_version=delta.base_ontology_version,
        target_ontology_version=delta.target_ontology_version,
        operations=ops_to_apply,
        metadata=delta.metadata,
    )
    val_result = validate_ontology_delta(base, sub_delta)
    if not val_result.valid:
        raise ValueError(f"Cannot apply invalid ontology delta: {'; '.join(val_result.violations)}")

    # 3. Compute base lineage hash
    base_sha256 = compute_ontology_sha256(base)

    # 4. Deep clone existing elements
    concepts: dict[str, CanonicalBusinessConcept] = {
        c.concept_uri: c.model_copy(deep=True) for c in base.canonical_business_concepts
    }
    relationships: dict[str, CanonicalRelationship] = {
        r.relationship_uri: r.model_copy(deep=True) for r in base.canonical_relationships
    }
    enumerations: dict[str, ReferenceEnumeration] = {
        e.enum_uri: e.model_copy(deep=True) for e in base.reference_enumerations
    }

    # 5. Apply operations in deterministic sequence
    for op in ops_to_apply:
        if isinstance(op, AddConceptOp):
            if op.concept_uri in concepts:
                existing = concepts[op.concept_uri]
                if not existing.target_hub and op.target_hub:
                    existing.target_hub = op.target_hub
            else:
                gov = (
                    GovernanceConstraints.model_validate(op.governance_constraints)
                    if op.governance_constraints
                    else None
                )
                kat = (
                    KeyAuthorityTiers.model_validate(op.key_authority_tiers)
                    if op.key_authority_tiers
                    else None
                )
                concepts[op.concept_uri] = CanonicalBusinessConcept(
                    concept_uri=op.concept_uri,
                    concept_name=op.concept_name
                    or op.concept_uri.split(":")[-1].replace("_", " ").title(),
                    elm_archetype=op.elm_archetype or "CORE_BUSINESS_CONCEPT",
                    definition=op.definition
                    or op.description
                    or f"Canonical concept for {op.concept_name}",
                    target_hub=op.target_hub,
                    governance_constraints=gov,
                    key_authority_tiers=kat,
                )

        elif isinstance(op, AddRelationshipOp):
            endpoints = list(op.endpoints)
            if not endpoints and op.source_concept_uri and op.target_concept_uri:
                endpoints = [op.source_concept_uri, op.target_concept_uri]

            yagni = (
                YagniCostGuardPolicy.model_validate(op.yagni_cost_guard_policy)
                if op.yagni_cost_guard_policy
                else None
            )
            relationships[op.relationship_uri] = CanonicalRelationship(
                relationship_uri=op.relationship_uri,
                relationship_name=op.relationship_name
                or op.relationship_uri.split(":")[-1].replace("_", " ").title(),
                target_link=op.target_link,
                endpoints=endpoints,
                grain_semantics=op.grain_semantics
                or op.description
                or f"Unit-of-work link {op.target_link}",
                yagni_cost_guard_policy=yagni,
            )

        elif isinstance(op, AddReferenceEnumerationOp):
            enum_uri = (
                op.enum_uri
                or f"urn:ontology:enum:{op.name or op.target_reference_table.replace('ref_', '')}"
            )
            table_name = op.target_reference_table or (
                f"ref_{op.name}" if not op.name.startswith("ref_") else op.name
            )
            codes = op.allowed_codes or op.values
            enumerations[enum_uri] = ReferenceEnumeration(
                enum_uri=enum_uri,
                target_reference_table=table_name,
                allowed_codes=codes,
            )

        elif isinstance(op, AddKeyAuthorityPatternOp):
            if op.concept_uri in concepts:
                c = concepts[op.concept_uri]
                if c.key_authority_tiers is None:
                    c.key_authority_tiers = KeyAuthorityTiers()
                tier_spec = KeyAuthorityTierSpec(
                    authority_name=op.authority_name,
                    regex_pattern=op.pattern_regex,
                    bkcc_policy=f"POLICY_{op.authority_name}",
                    require_src_qualifier=False,
                )
                tier_str = (op.tier or "TIER_1_GLOBAL_STANDARD").lower()
                if "tier_1" in tier_str or "global" in tier_str:
                    c.key_authority_tiers.tier_1_global_standard = tier_spec
                elif "tier_2" in tier_str or "enterprise" in tier_str or "sor" in tier_str:
                    c.key_authority_tiers.tier_2_enterprise_sor = tier_spec
                else:
                    c.key_authority_tiers.tier_3_local_scoped = tier_spec

        elif isinstance(op, AddGovernanceConstraintOp):
            if op.concept_uri in concepts:
                c = concepts[op.concept_uri]
                if c.governance_constraints is None:
                    c.governance_constraints = GovernanceConstraints()
                if op.pii_confidential_attributes:
                    existing_pii = c.governance_constraints.pii_confidential_attributes or []
                    c.governance_constraints.pii_confidential_attributes = sorted(
                        list(set(existing_pii + op.pii_confidential_attributes))
                    )
                if op.mnpi_confidential_attributes:
                    existing_mnpi = c.governance_constraints.mnpi_confidential_attributes or []
                    c.governance_constraints.mnpi_confidential_attributes = sorted(
                        list(set(existing_mnpi + op.mnpi_confidential_attributes))
                    )
                if op.required_satellite_split:
                    c.governance_constraints.required_satellite_split = op.required_satellite_split

        elif isinstance(op, RefineDefinitionOp):
            target_uri = op.target_uri or op.concept_uri
            if target_uri in concepts:
                c = concepts[target_uri]
                addition = op.refined_description
                if addition and addition not in c.definition:
                    c.definition = f"{c.definition} {addition}".strip()

        elif isinstance(op, AddSourceHintOp):
            # Source hints record authoring suggestions without mutating canonical ontology schema
            pass

    # 6. Lexicographically sort concepts, relationships, enumerations for deterministic hashing
    sorted_concepts = sorted(concepts.values(), key=lambda c: c.concept_uri)
    sorted_relationships = sorted(relationships.values(), key=lambda r: r.relationship_uri)
    sorted_enumerations = sorted(enumerations.values(), key=lambda e: e.enum_uri)

    # 7. Lineage & version tracking
    new_version = (
        target_version
        or delta.target_ontology_version
        or _increment_semver_minor(base.ontology_version)
    )
    new_decision_refs = list(base.decision_refs)
    if decision_id and decision_id not in new_decision_refs:
        new_decision_refs.append(decision_id)

    return GraphAOntology(
        ontology_version=new_version,
        parent_sha256=base_sha256,
        decision_refs=new_decision_refs,
        domain_namespace=base.domain_namespace,
        enterprise_owner=base.enterprise_owner,
        standards_references=list(base.standards_references),
        canonical_business_concepts=sorted_concepts,
        canonical_relationships=sorted_relationships,
        reference_enumerations=sorted_enumerations,
    )
