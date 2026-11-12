"""Pure state ontology delta application engine and 24-op SemVer calculus.

Provides:
- `apply(base, delta)`: Pure state transition engine: NEVER mutates `base` in-place,
  supports all 24 closed delta operations, and enforces 24-op SemVer calculus.
- `compute_next_semver(base_version, delta, base_ontology)`: SemVer bumping logic
  including greenfield genesis (0.0.0 -> 1.0.0 on AddConceptOp), additive MINOR,
  overwrite/structural deletions MAJOR, and metadata PATCH.
- `apply_ontology_delta(...)`: Backward-compatible entrypoint.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from typing import Any

from opendv_contracts.dtos.ontology import (
    AddAttributeOp,
    AddConceptOp,
    AddExternalMappingOp,
    AddGovernanceConstraintOp,
    AddKeyAuthorityPatternOp,
    AddReferenceEnumCodeOp,
    AddReferenceEnumerationOp,
    AddRelationshipOp,
    AddSourceHintOp,
    AddSynonymOp,
    AnnotateConceptMetadataOp,
    DeprecateConceptOp,
    DeprecateRelationshipOp,
    OntologyDelta,
    RefineDefinitionOp,
    RemoveAttributeOp,
    RemoveConceptOp,
    RemoveExternalMappingOp,
    RemoveRelationshipOp,
    RepinExternalMappingOp,
    RescoreExternalMappingOp,
    ReviseDescriptionOp,
    SetConceptElmArchetypeOp,
    SetParentConceptOp,
    StewardWaiverRecord,
    UpdateConceptUrnOp,
    is_destructive_delta,
)
from opendv_contracts.errors import ErrorCode
from opendv_contracts.spi.deployment import DeploymentStateProvider
from opendv_contracts.spi.grounding import GroundingVerifier
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    ConceptAttribute,
    ExternalMapping,
    GovernanceConstraints,
    GraphAOntology,
    KeyAuthorityTiers,
    KeyAuthorityTierSpec,
    ReferenceEnumeration,
    YagniCostGuardPolicy,
)
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.ontology.validator import (
    ContractViolationError,
    GovernanceValidator,
    OntologyContractViolationError,
    OntologyGovernanceError,
    SemVerCalculusError,
    StructuralValidator,
    build_deployment_provider_from_contract,
    validate_ontology_delta,
)

_SEMVER_REGEX = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def parse_semver(ver_str: str) -> tuple[int, int, int]:
    """Parse major, minor, patch from SemVer string."""
    m = _SEMVER_REGEX.match(ver_str.strip())
    if not m:
        raise SemVerCalculusError(f"ONT_EVO_ENVELOPE_VIOLATION: Invalid SemVer string '{ver_str}'.")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def compute_next_semver(
    base_version: str,
    delta: OntologyDelta,
    base_ontology: GraphAOntology | None = None,
    *,
    allow_bootstrap: bool = False,
    allow_legacy_ip1_bootstrap: bool = False,
) -> str:
    """Calculate the target SemVer for applying `delta` on `base_version`.

    Rules:
    - Greenfield Genesis (`0.x.y -> 1.0.0`): Whenever base major == 0 and `base_ontology` has 0
      CanonicalBusinessConcepts, adding >= 1 CBC promotes directly to `1.0.0`.
    - Pre-CBC Drafts (`0.x.y` with 0 CBCs): Rejects MAJOR destructive ops; advances `0.(x+1).0`
      while 0 CBCs exist, validating any explicit target > base via `parse_semver`.
    - Post-Genesis (`>= 1.0.0`):
      - MAJOR: RemoveConceptOp, RemoveRelationshipOp, RemoveAttributeOp, UpdateConceptUrnOp,
        overwriting existing non-None values on SetConceptElmArchetypeOp or SetParentConceptOp,
        or AddAttributeOp(is_optional=False) on an existing concept.
      - MINOR: AddConceptOp, AddRelationshipOp, AddAttributeOp, initial unset Set* assignments,
        AddReferenceEnumerationOp, AddKeyAuthorityPatternOp, AddGovernanceConstraintOp,
        DeprecateConceptOp, DeprecateRelationshipOp.
      - PATCH: AddSynonymOp, AddExternalMappingOp, ReviseDescriptionOp, RescoreExternalMappingOp,
        AddReferenceEnumCodeOp, RepinExternalMappingOp, RefineDefinitionOp, AddSourceHintOp,
        AnnotateConceptMetadataOp, RemoveExternalMappingOp.
      - `1.0.0 -> 1.0.0` is NEVER permitted via caller metadata; it is ONLY allowed when explicit
        server kwarg `allow_bootstrap=True` is passed AND `base_ontology` has 0 CBCs.
    """
    clean_base = base_version.strip()
    b_maj, b_min, b_pat = parse_semver(clean_base)

    has_cbc_addition = any(isinstance(op, AddConceptOp) for op in delta.operations)
    has_explicit_target = (
        "target_ontology_version" in delta.model_fields_set
        or "target_version" in delta.model_fields_set
    ) and delta.target_ontology_version is not None

    existing_concept_uris: set[str] = (
        {c.concept_uri for c in base_ontology.canonical_business_concepts}
        if base_ontology is not None
        else set()
    )
    base_has_cbcs = len(existing_concept_uris) > 0

    if not delta.operations:
        if has_explicit_target and delta.target_ontology_version:
            t_maj, t_min, t_pat = parse_semver(delta.target_ontology_version)
            if (t_maj, t_min, t_pat) < (b_maj, b_min, b_pat):
                raise SemVerCalculusError(
                    f"ONT_EVO_ENVELOPE_VIOLATION: Target version '{delta.target_ontology_version}' "
                    f"cannot downgrade base '{clean_base}'."
                )
            return delta.target_ontology_version
        return clean_base

    # Determine highest required bump rank: 3=MAJOR, 2=MINOR, 1=PATCH
    max_rank = 1
    for op in delta.operations:
        if isinstance(
            op, (RemoveConceptOp, RemoveRelationshipOp, RemoveAttributeOp, UpdateConceptUrnOp)
        ):
            max_rank = max(max_rank, 3)
            continue

        if isinstance(op, (SetParentConceptOp, SetConceptElmArchetypeOp)):
            eff = op.effective_semver_class(base_ontology=base_ontology)
            max_rank = max(max_rank, 3) if eff == "MAJOR" else max(max_rank, 2)
            continue

        if isinstance(op, AddAttributeOp):
            if not op.is_optional and op.concept_uri in existing_concept_uris:
                max_rank = max(max_rank, 3)
            else:
                max_rank = max(max_rank, 2)
            continue

        if isinstance(
            op,
            (
                AddConceptOp,
                AddRelationshipOp,
                AddReferenceEnumerationOp,
                AddKeyAuthorityPatternOp,
                AddGovernanceConstraintOp,
                DeprecateConceptOp,
                DeprecateRelationshipOp,
            ),
        ):
            max_rank = max(max_rank, 2)
            continue

        max_rank = max(max_rank, 1)

    # Pre-1.0.0 Drafts (0.x.y)
    if b_maj == 0:
        if not base_has_cbcs:
            if max_rank == 3:
                raise SemVerCalculusError(
                    f"ONT_EVO_ENVELOPE_VIOLATION: Destructive MAJOR operations are prohibited "
                    f"on pre-CBC draft '{clean_base}'."
                )
            if has_cbc_addition:
                if has_explicit_target and delta.target_ontology_version:
                    t_maj, t_min, t_pat = parse_semver(delta.target_ontology_version)
                    if (t_maj, t_min, t_pat) < (1, 0, 0):
                        raise SemVerCalculusError(
                            f"ONT_EVO_ENVELOPE_VIOLATION: Initial CBC addition requires release "
                            f"version >= 1.0.0, got '{delta.target_ontology_version}'."
                        )
                    return delta.target_ontology_version
                return "1.0.0"

            # Still 0 CBCs in draft: advance 0.(b_min+1).0 and strictly validate explicit targets
            if has_explicit_target and delta.target_ontology_version:
                t_maj, t_min, t_pat = parse_semver(delta.target_ontology_version)
                if t_maj >= 1:
                    raise SemVerCalculusError(
                        f"ONT_EVO_ENVELOPE_VIOLATION: Cannot promote draft '{clean_base}' to "
                        f"'{delta.target_ontology_version}' without adding at least one concept."
                    )
                if (t_maj, t_min, t_pat) <= (b_maj, b_min, b_pat):
                    raise SemVerCalculusError(
                        f"ONT_EVO_ENVELOPE_VIOLATION: Pre-CBC draft target "
                        f"'{delta.target_ontology_version}' must be strictly greater than "
                        f"'{clean_base}'."
                    )
                if max_rank >= 2 and t_min <= b_min:
                    raise SemVerCalculusError(
                        f"ONT_EVO_ENVELOPE_VIOLATION: Pre-CBC MINOR draft target "
                        f"'{delta.target_ontology_version}' requires minor bump >= 0.{b_min + 1}.0."
                    )
                return delta.target_ontology_version
            return f"0.{b_min + 1}.0"

        # 0.x.y draft that already contains CBCs
        if has_explicit_target and delta.target_ontology_version:
            t_maj, t_min, t_pat = parse_semver(delta.target_ontology_version)
            if (t_maj, t_min, t_pat) <= (b_maj, b_min, b_pat):
                raise SemVerCalculusError(
                    f"ONT_EVO_ENVELOPE_VIOLATION: Target version '{delta.target_ontology_version}' "
                    f"must be strictly greater than '{clean_base}'."
                )
            return delta.target_ontology_version
        if max_rank >= 2:
            return f"0.{b_min + 1}.0"
        return f"0.{b_min}.{b_pat + 1}"

    # Post-Genesis releases (>= 1.0.0)
    if has_explicit_target and delta.target_ontology_version:
        t_maj, t_min, t_pat = parse_semver(delta.target_ontology_version)

        if (
            clean_base == "1.0.0"
            and delta.target_ontology_version == "1.0.0"
            and base_ontology is not None
            and max_rank < 3
            and (
                (allow_bootstrap and not base_has_cbcs)
                or (allow_legacy_ip1_bootstrap and not delta.parent_sha256)
            )
        ):
            return delta.target_ontology_version

        if max_rank == 3:  # MAJOR
            if t_maj <= b_maj:
                raise SemVerCalculusError(
                    f"ONT_EVO_ENVELOPE_VIOLATION: Target version '{delta.target_ontology_version}' "
                    f"is invalid for base '{clean_base}' with MAJOR breaking operations. "
                    f"Expected major bump >= {b_maj + 1}.0.0."
                )
        elif max_rank == 2:  # MINOR
            if (t_maj < b_maj) or (t_maj == b_maj and t_min <= b_min):
                raise SemVerCalculusError(
                    f"ONT_EVO_ENVELOPE_VIOLATION: Target version '{delta.target_ontology_version}' "
                    f"is invalid for base '{clean_base}' with MINOR operations. "
                    f"Expected minor or major bump >= {b_maj}.{b_min + 1}.0."
                )
        elif max_rank == 1 and (t_maj, t_min, t_pat) <= (b_maj, b_min, b_pat):
            raise SemVerCalculusError(
                f"ONT_EVO_ENVELOPE_VIOLATION: Target version '{delta.target_ontology_version}' "
                f"is invalid for base '{clean_base}'. Target must be strictly greater than base."
            )
        return delta.target_ontology_version

    # Auto-calculate next SemVer if unspecified
    if max_rank == 3:
        return f"{b_maj + 1}.0.0"
    if max_rank == 2:
        return f"{b_maj}.{b_min + 1}.0"
    return f"{b_maj}.{b_min}.{b_pat + 1}"


def _parse_concept_attributes(
    raw_attrs: list[Any] | None,
    concept_uri: str,
) -> list[ConceptAttribute]:
    """Normalize AddConceptOp.attributes items into validated ConceptAttribute instances."""
    if not raw_attrs:
        return []
    parsed: list[ConceptAttribute] = []
    seen_names: set[str] = set()
    for item in raw_attrs:
        if isinstance(item, ConceptAttribute):
            attr = item.model_copy(deep=True)
        elif isinstance(item, str):
            attr = ConceptAttribute(attribute_name=item, data_type="string", is_optional=True)
        elif isinstance(item, dict):
            attr = ConceptAttribute.model_validate(item)
        else:
            attr = ConceptAttribute.model_validate(item)

        if attr.attribute_name in seen_names:
            raise OntologyGovernanceError(
                f"DUPLICATE_ATTRIBUTE_NAME: Duplicate attribute '{attr.attribute_name}' "
                f"on concept '{concept_uri}'.",
                error_code=ErrorCode.DUPLICATE_ATTRIBUTE_NAME,
            )
        seen_names.add(attr.attribute_name)
        parsed.append(attr)
    return parsed


def _merge_key_authority_tiers_additive(
    existing: KeyAuthorityTiers,
    incoming: KeyAuthorityTiers,
    concept_uri: str,
) -> None:
    """Fill only None tier slots on `existing`; raise ContractViolationError on conflict."""
    for slot in ("tier_1_global_standard", "tier_2_enterprise_sor", "tier_3_local_scoped"):
        inc_val = getattr(incoming, slot)
        if inc_val is None:
            continue
        cur_val = getattr(existing, slot)
        if cur_val is None:
            setattr(existing, slot, inc_val)
        elif cur_val != inc_val:
            raise ContractViolationError(
                f"ONT-EVO-003: Non-additive conflict on '{concept_uri}': Key authority slot "
                f"'{slot}' is already bound to '{cur_val.authority_name}'; cannot overwrite "
                f"with '{inc_val.authority_name}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )


def apply(
    base: GraphAOntology,
    delta: OntologyDelta,
    *,
    validate_structural: bool = True,
    allow_bootstrap: bool = False,
    allow_legacy_ip1_bootstrap: bool = False,
    _legacy_v1_projection: bool = False,
) -> GraphAOntology:
    """Pure state transition engine: NEVER mutates `base` in-place.

    Returns a new, fully validated `GraphAOntology` instance applying all operations
    in `delta` with SemVer calculus and lineage anchoring.

    Args:
        base: Base GraphAOntology state.
        delta: OntologyDelta carrying 24-op closed catalog operations.
        validate_structural: Whether to run Phase 1 StructuralValidator on target.
        allow_bootstrap: Server-asserted cold-start 1.0.0 -> 1.0.0 flag (requires 0 CBCs in base).

    Returns:
        New validated GraphAOntology instance.
    """
    # 1. Compute lineage hash over base
    base_sha256 = compute_ontology_sha256(base)

    # 2. Deep-copy elements into mutable working structures
    concepts: dict[str, CanonicalBusinessConcept] = {
        c.concept_uri: c.model_copy(deep=True) for c in base.canonical_business_concepts
    }
    relationships: dict[str, CanonicalRelationship] = {
        r.relationship_uri: r.model_copy(deep=True) for r in base.canonical_relationships
    }
    enumerations: dict[str, ReferenceEnumeration] = {
        e.enum_uri: e.model_copy(deep=True) for e in base.reference_enumerations
    }
    decision_refs = list(base.decision_refs)
    standards_references = list(base.standards_references)

    # 3. Apply operations in exact deterministic sequence
    for op in delta.operations:
        # --- Major Deletions & Renames ---
        if isinstance(op, RemoveConceptOp):
            concepts.pop(op.concept_uri, None)
            # Remove relationships that reference the deleted concept as an endpoint
            relationships = {
                k: r for k, r in relationships.items() if op.concept_uri not in r.endpoints
            }
            # Reset parent concept pointer if any child referenced deleted concept
            for c in concepts.values():
                if c.parent_concept_uri == op.concept_uri:
                    c.parent_concept_uri = None

        elif isinstance(op, UpdateConceptUrnOp):
            if op.old_concept_uri in concepts:
                if op.new_concept_uri in concepts and op.new_concept_uri != op.old_concept_uri:
                    raise ContractViolationError(
                        f"ONT-EVO-003: Cannot rename '{op.old_concept_uri}' to existing concept "
                        f"URI '{op.new_concept_uri}'.",
                        error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
                    )
                c = concepts.pop(op.old_concept_uri)
                c.concept_uri = op.new_concept_uri
                concepts[op.new_concept_uri] = c
                for r in relationships.values():
                    r.endpoints = [
                        op.new_concept_uri if ep == op.old_concept_uri else ep for ep in r.endpoints
                    ]
                for other_c in concepts.values():
                    if other_c.parent_concept_uri == op.old_concept_uri:
                        other_c.parent_concept_uri = op.new_concept_uri

        elif isinstance(op, RemoveRelationshipOp):
            to_remove = [
                uri
                for uri, r in relationships.items()
                if (op.relationship_uri and uri == op.relationship_uri)
                or (op.relationship_name and r.relationship_name == op.relationship_name)
            ]
            for uri in to_remove:
                relationships.pop(uri, None)

        elif isinstance(op, RemoveAttributeOp):
            if op.concept_uri in concepts:
                c = concepts[op.concept_uri]
                if c.attributes:
                    c.attributes = [
                        a for a in c.attributes if a.attribute_name != op.attribute_name
                    ]
                    if not c.attributes:
                        c.attributes = None

        # --- Minor Additive & Structural Mutations ---
        elif isinstance(op, AddConceptOp):
            ext_maps: list[ExternalMapping] = []
            if op.external_mappings:
                for m in op.external_mappings:
                    if isinstance(m, dict):
                        ext_maps.append(ExternalMapping.model_validate(m))
                    elif isinstance(m, ExternalMapping):
                        ext_maps.append(m.model_copy(deep=True))

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
            op_attrs = (
                []
                if _legacy_v1_projection
                else _parse_concept_attributes(op.attributes, op.concept_uri)
            )

            if op.concept_uri in concepts:
                existing = concepts[op.concept_uri]
                if op.target_hub:
                    if existing.target_hub and existing.target_hub != op.target_hub:
                        raise ContractViolationError(
                            f"ONT-EVO-001: Concept '{op.concept_uri}' is already bound to hub "
                            f"'{existing.target_hub}'; cannot overwrite with '{op.target_hub}'.",
                            error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
                        )
                    existing.target_hub = op.target_hub

                if op.parent_concept_uri is not None:
                    if existing.parent_concept_uri is None:
                        existing.parent_concept_uri = op.parent_concept_uri
                    elif existing.parent_concept_uri != op.parent_concept_uri:
                        raise OntologyGovernanceError(
                            f"PARENT_ALREADY_EXISTS: Concept '{op.concept_uri}' already has "
                            f"parent '{existing.parent_concept_uri}'; use SetParentConceptOp "
                            "for destructive reparenting.",
                            error_code=ErrorCode.PARENT_ALREADY_EXISTS,
                        )

                if not _legacy_v1_projection and op.description and existing.description is None:
                    existing.description = op.description
                if op.definition and existing.definition.startswith("Canonical business concept:"):
                    existing.definition = op.definition

                if op_attrs:
                    if existing.attributes is None:
                        existing.attributes = []
                    existing_attr_map = {a.attribute_name: a for a in existing.attributes}
                    for new_a in op_attrs:
                        if new_a.attribute_name in existing_attr_map:
                            ex_a = existing_attr_map[new_a.attribute_name]
                            if (
                                ex_a.data_type != new_a.data_type
                                or ex_a.is_optional != new_a.is_optional
                            ):
                                raise OntologyGovernanceError(
                                    f"DUPLICATE_ATTRIBUTE_NAME: Cannot overwrite existing "
                                    f"attribute '{new_a.attribute_name}' on '{op.concept_uri}'.",
                                    error_code=ErrorCode.DUPLICATE_ATTRIBUTE_NAME,
                                )
                        else:
                            existing.attributes.append(new_a)

                if op.synonyms:
                    existing_syns = existing.synonyms or []
                    for syn in op.synonyms:
                        if not any(s.casefold() == syn.casefold() for s in existing_syns):
                            existing_syns.append(syn)
                    existing.synonyms = existing_syns
                if ext_maps:
                    existing_maps = existing.external_mappings or []
                    existing_map_keys = {(m.target_uri, m.predicate) for m in existing_maps}
                    for new_m in ext_maps:
                        if (new_m.target_uri, new_m.predicate) not in existing_map_keys:
                            existing_maps.append(new_m)
                    existing.external_mappings = existing_maps
                if gov is not None:
                    if existing.governance_constraints is None:
                        existing.governance_constraints = gov
                    else:
                        ex_gov = existing.governance_constraints
                        if (
                            gov.required_satellite_split
                            and ex_gov.required_satellite_split
                            and ex_gov.required_satellite_split != gov.required_satellite_split
                        ):
                            raise ContractViolationError(
                                f"ONT-EVO-003: Conflicting required_satellite_split on "
                                f"'{op.concept_uri}'.",
                                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
                            )
                        if gov.required_satellite_split and not ex_gov.required_satellite_split:
                            ex_gov.required_satellite_split = gov.required_satellite_split
                        if gov.pii_confidential_attributes:
                            ex_gov.pii_confidential_attributes = sorted(
                                set(
                                    (ex_gov.pii_confidential_attributes or [])
                                    + gov.pii_confidential_attributes
                                )
                            )
                        if gov.mnpi_confidential_attributes:
                            ex_gov.mnpi_confidential_attributes = sorted(
                                set(
                                    (ex_gov.mnpi_confidential_attributes or [])
                                    + gov.mnpi_confidential_attributes
                                )
                            )
                if kat is not None:
                    if existing.key_authority_tiers is None:
                        existing.key_authority_tiers = kat
                    else:
                        _merge_key_authority_tiers_additive(
                            existing.key_authority_tiers, kat, op.concept_uri
                        )
                if op.source_hints:
                    ex_hints = existing.source_hints or []
                    for h in op.source_hints:
                        if h not in ex_hints:
                            ex_hints.append(h)
                    existing.source_hints = ex_hints
                if op.metadata:
                    if existing.metadata is None:
                        existing.metadata = dict(op.metadata)
                    else:
                        for mk, mv in op.metadata.items():
                            existing.metadata.setdefault(mk, mv)
            else:
                c_name = op.concept_name or op.concept_uri.split(":")[-1].replace("_", " ").title()
                defn = op.definition or op.description or f"Canonical business concept: {c_name}."
                desc_val = None if _legacy_v1_projection else (op.description or None)
                concepts[op.concept_uri] = CanonicalBusinessConcept(
                    concept_uri=op.concept_uri,
                    concept_name=c_name,
                    elm_archetype=op.elm_archetype or "CORE_BUSINESS_CONCEPT",
                    definition=defn,
                    description=desc_val,
                    attributes=op_attrs if op_attrs else None,
                    target_hub=op.target_hub,
                    parent_concept_uri=op.parent_concept_uri,
                    synonyms=list(op.synonyms) if op.synonyms else None,
                    external_mappings=ext_maps if ext_maps else None,
                    governance_constraints=gov,
                    key_authority_tiers=kat,
                    metadata=dict(op.metadata) if op.metadata else None,
                    source_hints=list(op.source_hints) if op.source_hints else None,
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

            if op.relationship_uri in relationships:
                existing_r = relationships[op.relationship_uri]
                if (
                    op.relationship_name
                    and existing_r.relationship_name
                    and op.relationship_name != existing_r.relationship_name
                ):
                    raise OntologyGovernanceError(
                        f"DUPLICATE_RELATIONSHIP_URI: Relationship '{op.relationship_uri}' "
                        f"already exists with name '{existing_r.relationship_name}'; cannot "
                        f"overwrite with '{op.relationship_name}'.",
                        error_code=ErrorCode.DUPLICATE_RELATIONSHIP_URI,
                    )
                if (
                    op.target_link
                    and existing_r.target_link
                    and op.target_link != existing_r.target_link
                ):
                    raise ContractViolationError(
                        f"ONT-EVO-001: Relationship '{op.relationship_uri}' is already bound to "
                        f"link '{existing_r.target_link}'; cannot overwrite with "
                        f"'{op.target_link}'.",
                        error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
                    )
                if (
                    endpoints
                    and existing_r.endpoints
                    and list(endpoints) != list(existing_r.endpoints)
                ):
                    raise OntologyGovernanceError(
                        f"DUPLICATE_RELATIONSHIP_URI: Relationship '{op.relationship_uri}' "
                        f"already exists with endpoints {existing_r.endpoints}; cannot overwrite "
                        f"with {endpoints}.",
                        error_code=ErrorCode.DUPLICATE_RELATIONSHIP_URI,
                    )
                if (
                    not allow_legacy_ip1_bootstrap
                    and op.grain_semantics
                    and existing_r.grain_semantics
                    and not existing_r.grain_semantics.startswith("Unit-of-work link ")
                    and op.grain_semantics != existing_r.grain_semantics
                ):
                    raise OntologyGovernanceError(
                        f"DUPLICATE_RELATIONSHIP_URI: Cannot overwrite grain_semantics on "
                        f"existing relationship '{op.relationship_uri}'.",
                        error_code=ErrorCode.DUPLICATE_RELATIONSHIP_URI,
                    )
                if not existing_r.target_link and op.target_link:
                    existing_r.target_link = op.target_link
                if not _legacy_v1_projection and existing_r.description is None and op.description:
                    existing_r.description = op.description
                if (
                    not _legacy_v1_projection
                    and existing_r.cardinality is None
                    and op.cardinality is not None
                ):
                    existing_r.cardinality = op.cardinality
                if yagni and existing_r.yagni_cost_guard_policy is None:
                    existing_r.yagni_cost_guard_policy = yagni
                if op.metadata:
                    if existing_r.metadata is None:
                        existing_r.metadata = dict(op.metadata)
                    else:
                        for mk, mv in op.metadata.items():
                            existing_r.metadata.setdefault(mk, mv)
            else:
                r_name = (
                    op.relationship_name
                    or op.relationship_uri.split(":")[-1].replace("_", " ").title()
                )
                if op.relationship_name:
                    for other_r in relationships.values():
                        if other_r.relationship_name == op.relationship_name:
                            raise OntologyGovernanceError(
                                f"DUPLICATE_RELATIONSHIP_NAME: Relationship name "
                                f"'{op.relationship_name}' is already used by "
                                f"'{other_r.relationship_uri}'.",
                                error_code=ErrorCode.DUPLICATE_RELATIONSHIP_NAME,
                            )
                grain = (
                    op.grain_semantics or op.description or f"Unit-of-work link {op.target_link}."
                )
                rel_desc = None if _legacy_v1_projection else (op.description or None)
                rel_card = None if _legacy_v1_projection else op.cardinality
                relationships[op.relationship_uri] = CanonicalRelationship(
                    relationship_uri=op.relationship_uri,
                    relationship_name=r_name,
                    target_link=op.target_link,
                    endpoints=endpoints,
                    grain_semantics=grain,
                    description=rel_desc,
                    cardinality=rel_card,
                    yagni_cost_guard_policy=yagni,
                    metadata=dict(op.metadata) if op.metadata else None,
                )

        elif isinstance(op, AddAttributeOp):
            if op.concept_uri in concepts:
                c = concepts[op.concept_uri]
                if c.attributes is None:
                    c.attributes = []
                for a in c.attributes:
                    if a.attribute_name == op.attribute_name:
                        raise OntologyGovernanceError(
                            f"DUPLICATE_ATTRIBUTE_NAME: Attribute '{op.attribute_name}' already "
                            f"exists on concept '{op.concept_uri}'.",
                            error_code=ErrorCode.DUPLICATE_ATTRIBUTE_NAME,
                        )
                c.attributes.append(
                    ConceptAttribute(
                        attribute_name=op.attribute_name,
                        data_type=op.data_type or "string",
                        description=op.description,
                        is_optional=op.is_optional,
                        example_values=list(op.example_values) if op.example_values else None,
                        metadata=dict(op.metadata) if op.metadata else None,
                    )
                )

        elif isinstance(op, SetParentConceptOp):
            if op.concept_uri in concepts:
                concepts[op.concept_uri].parent_concept_uri = op.parent_concept_uri

        elif isinstance(op, SetConceptElmArchetypeOp):
            if op.concept_uri in concepts:
                concepts[op.concept_uri].elm_archetype = op.elm_archetype

        elif isinstance(op, AddReferenceEnumerationOp):
            enum_uri = (
                op.enum_uri
                or f"urn:ontology:enum:{op.name or op.target_reference_table.replace('ref_', '')}"
            )
            table_name = op.target_reference_table or (
                f"ref_{op.name}" if not op.name.startswith("ref_") else op.name
            )
            codes = list(op.allowed_codes or op.values or [])
            enum_name_val = None if _legacy_v1_projection else (op.enumeration_name or None)
            enum_desc_val = None if _legacy_v1_projection else (op.description or None)
            enum_code_descs = (
                None
                if _legacy_v1_projection
                else (dict(op.code_descriptions) if op.code_descriptions else None)
            )
            enum_meta = (
                None if _legacy_v1_projection else (dict(op.metadata) if op.metadata else None)
            )

            if enum_uri in enumerations:
                ex_enum = enumerations[enum_uri]
                if (
                    op.target_reference_table
                    and ex_enum.target_reference_table
                    and op.target_reference_table != ex_enum.target_reference_table
                ):
                    raise ContractViolationError(
                        f"ONT-EVO-001: Enumeration '{enum_uri}' is already bound to "
                        f"'{ex_enum.target_reference_table}'; cannot rebind to "
                        f"'{op.target_reference_table}'.",
                        error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
                    )
                merged_codes = sorted(list(set(ex_enum.allowed_codes + codes)))
                merged_code_descs = (
                    dict(ex_enum.code_descriptions) if ex_enum.code_descriptions else None
                )
                if enum_code_descs:
                    if merged_code_descs is None:
                        merged_code_descs = dict(enum_code_descs)
                    else:
                        for ck, cv in enum_code_descs.items():
                            merged_code_descs.setdefault(ck, cv)
                merged_meta = dict(ex_enum.metadata) if ex_enum.metadata else None
                if enum_meta:
                    if merged_meta is None:
                        merged_meta = dict(enum_meta)
                    else:
                        for mk, mv in enum_meta.items():
                            merged_meta.setdefault(mk, mv)
                enumerations[enum_uri] = ReferenceEnumeration(
                    enum_uri=enum_uri,
                    enumeration_name=ex_enum.enumeration_name or enum_name_val,
                    target_reference_table=ex_enum.target_reference_table or table_name,
                    allowed_codes=merged_codes,
                    description=ex_enum.description or enum_desc_val,
                    code_descriptions=merged_code_descs,
                    deprecated=ex_enum.deprecated,
                    metadata=merged_meta,
                )
            else:
                enumerations[enum_uri] = ReferenceEnumeration(
                    enum_uri=enum_uri,
                    enumeration_name=enum_name_val,
                    target_reference_table=table_name,
                    allowed_codes=codes,
                    description=enum_desc_val,
                    code_descriptions=enum_code_descs,
                    metadata=enum_meta,
                )

        elif isinstance(op, AddReferenceEnumCodeOp):
            target_enum = None
            op_enum_slug = (op.enumeration_name or "").removeprefix("ref_")
            for e in enumerations.values():
                e_slug = (e.target_reference_table or "").removeprefix("ref_")
                if (op.enum_uri and e.enum_uri == op.enum_uri) or (
                    op.enumeration_name
                    and (
                        e.enumeration_name == op.enumeration_name
                        or e.target_reference_table == f"ref_{op.enumeration_name}"
                        or e.target_reference_table == op.enumeration_name
                        or (op_enum_slug and e_slug == op_enum_slug)
                    )
                ):
                    target_enum = e
                    break
            if target_enum is None:
                raise OntologyGovernanceError(
                    f"REFERENCED_CONCEPT_NOT_FOUND: Target enumeration "
                    f"'{op.enum_uri or op.enumeration_name}' not found.",
                    error_code=ErrorCode.REFERENCED_CONCEPT_NOT_FOUND,
                )
            if op.code in target_enum.allowed_codes:
                raise OntologyGovernanceError(
                    f"DUPLICATE_ENUM_CODE: Code '{op.code}' already exists in enumeration "
                    f"'{target_enum.enum_uri}'.",
                    error_code=ErrorCode.DUPLICATE_ENUM_CODE,
                )
            target_enum.allowed_codes = sorted(list(set([*target_enum.allowed_codes, op.code])))
            if op.description:
                if target_enum.code_descriptions is None:
                    target_enum.code_descriptions = {}
                target_enum.code_descriptions[op.code] = op.description

        elif isinstance(op, AddKeyAuthorityPatternOp):
            if op.concept_uri in concepts:
                c = concepts[op.concept_uri]
                if c.key_authority_tiers is None:
                    c.key_authority_tiers = KeyAuthorityTiers()
                if op.tier_spec is not None:
                    tier_spec = KeyAuthorityTierSpec.model_validate(op.tier_spec)
                else:
                    tier_spec = KeyAuthorityTierSpec(
                        authority_name=op.authority_name,
                        regex_pattern=op.pattern_regex,
                        bkcc_policy=f"POLICY_{op.authority_name}",
                        require_src_qualifier=False,
                    )
                tier_str = (op.tier or "TIER_1_GLOBAL_STANDARD").lower()
                if "tier_1" in tier_str or "t1" in tier_str or "global" in tier_str:
                    slot_name = "tier_1_global_standard"
                elif (
                    "tier_2" in tier_str
                    or "t2" in tier_str
                    or "enterprise" in tier_str
                    or "sor" in tier_str
                ):
                    slot_name = "tier_2_enterprise_sor"
                else:
                    slot_name = "tier_3_local_scoped"

                existing_slot = getattr(c.key_authority_tiers, slot_name)
                if existing_slot is None:
                    setattr(c.key_authority_tiers, slot_name, tier_spec)
                elif existing_slot != tier_spec:
                    raise ContractViolationError(
                        f"ONT-EVO-003: Non-additive conflict: Concept '{op.concept_uri}' already "
                        f"has authority '{existing_slot.authority_name}' for {slot_name}; cannot "
                        f"overwrite with '{tier_spec.authority_name}'.",
                        error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
                    )

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
                    ex_split = c.governance_constraints.required_satellite_split
                    if ex_split and ex_split != op.required_satellite_split:
                        raise ContractViolationError(
                            f"ONT-EVO-003: Cannot overwrite required_satellite_split "
                            f"'{ex_split}' on '{op.concept_uri}' with "
                            f"'{op.required_satellite_split}'.",
                            error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
                        )
                    c.governance_constraints.required_satellite_split = op.required_satellite_split

        elif isinstance(op, DeprecateConceptOp):
            if op.concept_uri in concepts:
                c = concepts[op.concept_uri]
                c.deprecated = True
                if op.reason or op.superseded_by_concept_uri:
                    if c.metadata is None:
                        c.metadata = {}
                    if op.reason:
                        c.metadata["deprecation_reason"] = op.reason
                    if op.superseded_by_concept_uri:
                        c.metadata["superseded_by"] = op.superseded_by_concept_uri

        elif isinstance(op, DeprecateRelationshipOp):
            for r in relationships.values():
                if (op.relationship_uri and r.relationship_uri == op.relationship_uri) or (
                    op.relationship_name and r.relationship_name == op.relationship_name
                ):
                    r.deprecated = True
                    if op.reason:
                        if r.metadata is None:
                            r.metadata = {}
                        r.metadata["deprecation_reason"] = op.reason

        # --- Metadata & Refinements ---
        elif isinstance(op, AddSynonymOp):
            if op.concept_uri in concepts:
                c = concepts[op.concept_uri]
                if c.synonyms is None:
                    c.synonyms = []
                cleaned = op.synonym.strip()
                if cleaned:
                    if any(s.casefold() == cleaned.casefold() for s in c.synonyms):
                        raise OntologyGovernanceError(
                            f"DUPLICATE_SYNONYM: Synonym '{cleaned}' already exists on concept "
                            f"'{op.concept_uri}'.",
                            error_code=ErrorCode.DUPLICATE_SYNONYM,
                        )
                    c.synonyms.append(cleaned)

        elif isinstance(op, AddExternalMappingOp):
            if op.concept_uri in concepts:
                c = concepts[op.concept_uri]
                if c.external_mappings is None:
                    c.external_mappings = []
                target_u = op.target_uri or op.standard_uri
                scheme_val = op.scheme or op.standard_name
                pred = op.predicate or op.mapping_type or "skos:exactMatch"
                found = False
                for m in c.external_mappings:
                    if m.target_uri == target_u and m.predicate == pred:
                        if m.confidence is None and op.mapping_confidence is not None:
                            m.confidence = op.mapping_confidence
                        if m.pinned is None and op.pinned is not None:
                            m.pinned = op.pinned
                        found = True
                        break
                if not found:
                    c.external_mappings.append(
                        ExternalMapping(
                            scheme=scheme_val or "custom",
                            target_uri=target_u,
                            predicate=pred,
                            confidence=op.mapping_confidence,
                            pinned=op.pinned,
                        )
                    )

        elif isinstance(op, ReviseDescriptionOp):
            target_u = op.concept_uri or op.target_uri
            if target_u in concepts:
                c = concepts[target_u]
                c.description = op.description
                if not c.definition or c.definition.startswith("Canonical"):
                    c.definition = op.description

        elif isinstance(op, RescoreExternalMappingOp):
            if op.concept_uri in concepts:
                c = concepts[op.concept_uri]
                if c.external_mappings:
                    target_u = op.target_uri or op.standard_uri
                    for m in c.external_mappings:
                        if m.target_uri == target_u and not m.pinned:
                            m.confidence = op.new_confidence

        elif isinstance(op, RepinExternalMappingOp):
            if op.concept_uri in concepts:
                c = concepts[op.concept_uri]
                if c.external_mappings:
                    target_u = op.target_uri or op.standard_uri
                    for m in c.external_mappings:
                        if m.target_uri == target_u:
                            m.pinned = op.pinned
                            if op.new_reference_pack_id:
                                m.reference_pack_id = op.new_reference_pack_id
                            if op.new_reference_pack_sha256:
                                m.reference_pack_sha256 = op.new_reference_pack_sha256

        elif isinstance(op, RefineDefinitionOp):
            target_u = op.concept_uri or op.target_uri
            if target_u in concepts:
                c = concepts[target_u]
                def_text = op.refined_description or op.definition
                if def_text and def_text not in c.definition:
                    c.definition = f"{c.definition} {def_text}".strip()
                if not _legacy_v1_projection and op.additional_attributes:
                    extra_attrs = _parse_concept_attributes(op.additional_attributes, target_u)
                    if extra_attrs:
                        if c.attributes is None:
                            c.attributes = []
                        existing_names = {a.attribute_name for a in c.attributes}
                        for new_a in extra_attrs:
                            if new_a.attribute_name not in existing_names:
                                c.attributes.append(new_a)
                                existing_names.add(new_a.attribute_name)

        elif isinstance(op, AddSourceHintOp):
            if op.concept_uri in concepts:
                c = concepts[op.concept_uri]
                if c.source_hints is None:
                    c.source_hints = []
                hint_str = (op.hint or "").strip()
                if not hint_str:
                    src_id = op.source_system or op.source_name
                    col_id = op.column_name or op.column_hint
                    hint_str = ".".join(p for p in (src_id, op.table_name, col_id) if p)
                if hint_str and hint_str not in c.source_hints:
                    c.source_hints.append(hint_str)

        elif isinstance(op, AnnotateConceptMetadataOp):
            if op.concept_uri in concepts:
                c = concepts[op.concept_uri]
                if c.metadata is None:
                    c.metadata = {}
                c.metadata.update(op.metadata)

        elif isinstance(op, RemoveExternalMappingOp):
            if op.concept_uri in concepts:
                c = concepts[op.concept_uri]
                if c.external_mappings:
                    target_u = op.target_uri or op.standard_uri
                    c.external_mappings = [
                        m
                        for m in c.external_mappings
                        if not (
                            m.target_uri == target_u
                            and (op.mapping_type is None or m.predicate == op.mapping_type)
                        )
                    ]
                    if not c.external_mappings:
                        c.external_mappings = None

    # 4. Deterministically sort all collections for JCS canonical hashing
    sorted_concepts = sorted(concepts.values(), key=lambda c: c.concept_uri)
    sorted_relationships = sorted(relationships.values(), key=lambda r: r.relationship_uri)
    sorted_enumerations = sorted(enumerations.values(), key=lambda e: e.enum_uri)

    # 5. Validate domain_namespace invariant and calculate resulting SemVer version
    if (
        not allow_legacy_ip1_bootstrap
        and delta.domain_namespace
        and delta.domain_namespace != base.domain_namespace
    ):
        raise OntologyContractViolationError(
            f"Delta domain_namespace '{delta.domain_namespace}' does not match "
            f"base ontology domain_namespace '{base.domain_namespace}'.",
            error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
        )

    new_version = compute_next_semver(
        base.ontology_version,
        delta,
        base_ontology=base,
        allow_bootstrap=allow_bootstrap,
        allow_legacy_ip1_bootstrap=allow_legacy_ip1_bootstrap,
    )

    target_ontology = GraphAOntology(
        ontology_version=new_version,
        parent_sha256=base_sha256,
        decision_refs=decision_refs,
        domain_namespace=(
            (delta.domain_namespace or base.domain_namespace)
            if allow_legacy_ip1_bootstrap
            else base.domain_namespace
        ),
        enterprise_owner=base.enterprise_owner,
        standards_references=standards_references,
        canonical_business_concepts=sorted_concepts,
        canonical_relationships=sorted_relationships,
        reference_enumerations=sorted_enumerations,
        metadata=dict(base.metadata) if base.metadata else None,
    )

    # 6. Optional Phase 1 Structural Validation
    if validate_structural:
        StructuralValidator().validate(target_ontology, delta=delta)

    return target_ontology


class _HmacWaiverVerifier:
    """HMAC-SHA256 K_waiver verifier over StewardWaiverRecord.signing_payload()."""

    def __init__(self, secret: bytes | str) -> None:
        self._key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)

    def verify_waiver(
        self,
        waiver: StewardWaiverRecord,
        delta_sha256: str,
        base_sha256: str | None = None,
    ) -> bool:
        if not self._key:
            return False
        if (
            waiver.proposal_delta_sha256
            and delta_sha256
            and waiver.proposal_delta_sha256.lower() != delta_sha256.lower()
        ):
            return False
        if base_sha256 and waiver.base_sha256 and waiver.base_sha256.lower() != base_sha256.lower():
            return False
        if waiver.expires_at is not None and waiver.expires_at <= datetime.now(UTC):
            return False
        payload_bytes = waiver.signing_payload().encode("utf-8")
        expected = hmac.new(self._key, payload_bytes, hashlib.sha256).hexdigest().lower()
        raw_sig = (waiver.signature_or_token or "").strip().lower()
        if raw_sig.startswith("hmac-sha256:"):
            raw_sig = raw_sig.removeprefix("hmac-sha256:")
        return hmac.compare_digest(raw_sig, expected)


def apply_ontology_delta(
    base: GraphAOntology,
    delta: OntologyDelta,
    accepted_op_ids: list[str] | None = None,
    decision_id: str | None = None,
    target_version: str | None = None,
    deployed_contract: Any | None = None,
    *,
    actor_role: str = "steward",
    deployment_provider: DeploymentStateProvider | None = None,
    allow_bootstrap: bool = False,
    allow_legacy_ip1_bootstrap: bool = False,
    enforce_grounding: bool = False,
    waivers: Sequence[StewardWaiverRecord] | None = None,
    waiver_secret: bytes | str | None = None,
    grandfathered_concept_uris: Collection[str] | None = None,
    grounding_verifier: GroundingVerifier | None = None,
) -> GraphAOntology:
    """Deterministically apply accepted delta operations to a base Graph A ontology.

    Enforces ONT-EVO-001 deployed contract immutability and admin authorization on
    destructive revisions (`P05::MAJ-09`).

    Note on `enforce_grounding`:
        `enforce_grounding` defaults to `False` strictly for backward compatibility with
        legacy Wave-1 IP-1 hold-resolution callers (`execute_ontology_decision` /
        `resolve_ontology_hold`). Ontology Studio commits enforce Path A FIBO grounding
        and cryptographic `K_waiver` verification via `GovernanceValidator` /
        `AtomicCommitService(enforce_governance=True)` (or by passing
        `enforce_grounding=True` here).
    """
    if accepted_op_ids is not None:
        accepted_set = set(accepted_op_ids)
        ops_to_apply = [op for op in delta.operations if op.op_id in accepted_set]
    else:
        ops_to_apply = list(delta.operations)

    sub_payload: dict[str, Any] = {
        "delta_id": delta.delta_id,
        "parent_sha256": delta.parent_sha256,
        "domain_namespace": delta.domain_namespace,
        "base_ontology_version": delta.base_ontology_version,
        "operations": [op.model_dump(mode="json") for op in ops_to_apply],
        "metadata": delta.metadata,
    }
    eff_target_ver = target_version or delta.target_ontology_version
    if eff_target_ver is not None:
        sub_payload["target_ontology_version"] = eff_target_ver
    if delta.author is not None:
        sub_payload["author"] = delta.author
    if delta.created_at is not None:
        sub_payload["created_at"] = delta.created_at
    if delta.rationale is not None:
        sub_payload["rationale"] = delta.rationale

    sub_delta = OntologyDelta.model_validate(
        sub_payload,
        context={"allow_bootstrap": True}
        if (allow_bootstrap or allow_legacy_ip1_bootstrap)
        else None,
    )

    # Enforce admin role for destructive MAJOR revisions (P05::MAJ-09)
    if is_destructive_delta(sub_delta, base) and actor_role != "admin":
        raise OntologyGovernanceError(
            "FORBIDDEN_ADMIN_REQUIRED: Destructive delta operations require 'admin' role.",
            error_code=ErrorCode.FORBIDDEN_ADMIN_REQUIRED,
            status_code=403,
            exit_code=7,
        )

    # Enforce GovernanceValidator deployed-contract guard before apply() (P05::MAJ-09)
    eff_provider = deployment_provider or build_deployment_provider_from_contract(deployed_contract)
    if eff_provider is not None:
        GovernanceValidator(deployment_provider=eff_provider).validate_deployed_contracts(
            base, sub_delta
        )

    val_result = validate_ontology_delta(base, sub_delta, deployed_contract=deployed_contract)
    if not val_result.valid:
        raise ValueError(f"Cannot apply invalid ontology delta: {'; '.join(val_result.violations)}")

    is_legacy_v1 = (delta.metadata or {}).get("benchmark") == "PAR-ONT-HOLD"
    target = apply(
        base,
        sub_delta,
        validate_structural=True,
        allow_bootstrap=allow_bootstrap,
        allow_legacy_ip1_bootstrap=allow_legacy_ip1_bootstrap,
        _legacy_v1_projection=is_legacy_v1,
    )

    if enforce_grounding:
        eff_waiver_verifier = (
            _HmacWaiverVerifier(waiver_secret)
            if waiver_secret is not None and len(waiver_secret) > 0
            else None
        )
        GovernanceValidator(
            deployment_provider=eff_provider,
            grounding_verifier=grounding_verifier,
            waiver_verifier=eff_waiver_verifier,
            strict_verifier_required=(grounding_verifier is None),
        ).validate_grounding(
            base,
            sub_delta,
            target,
            waivers=list(waivers) if waivers is not None else None,
            grandfathered_concept_uris=(
                frozenset(grandfathered_concept_uris)
                if grandfathered_concept_uris is not None
                else None
            ),
        )

    if decision_id and decision_id not in target.decision_refs:
        target.decision_refs.append(decision_id)

    return target


__all__ = [
    "OntologyContractViolationError",
    "apply",
    "apply_ontology_delta",
    "compute_next_semver",
    "parse_semver",
]
