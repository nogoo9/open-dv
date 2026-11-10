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

import re
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
    UpdateConceptUrnOp,
)
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
    SemVerCalculusError,
    StructuralValidator,
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
) -> str:
    """Calculate the target SemVer for applying `delta` on `base_version`.

    Rules:
    - Greenfield Genesis (0.0.0 -> 1.0.0): If base is '0.0.0' and delta contains at least one
      AddConceptOp, version transitions directly to 1.0.0 (initial certified release).
    - Pre-CBC Drafts: While 0.0.0 without AddConceptOp, advances draft minor 0.1.0.
    - Post-Genesis (>= 1.0.0):
      - MAJOR: RemoveConceptOp, RemoveRelationshipOp, RemoveAttributeOp, UpdateConceptUrnOp,
        or overwriting existing non-None values on SetConceptElmArchetypeOp or SetParentConceptOp,
        or AddAttributeOp(is_optional=False) on an existing concept.
      - MINOR: AddConceptOp, AddRelationshipOp, AddAttributeOp, initial unset Set* assignments,
        AddReferenceEnumerationOp, AddKeyAuthorityPatternOp, AddGovernanceConstraintOp,
        DeprecateConceptOp, DeprecateRelationshipOp.
      - PATCH: AddSynonymOp, AddExternalMappingOp, ReviseDescriptionOp, RescoreExternalMappingOp,
        AddReferenceEnumCodeOp, RepinExternalMappingOp, RefineDefinitionOp, AddSourceHintOp,
        AnnotateConceptMetadataOp, RemoveExternalMappingOp.
      - If delta.target_ontology_version is specified, validates that it meets or exceeds the
        required bump, raising SemVerCalculusError on violation.
    """
    clean_base = base_version.strip()
    has_cbc_addition = any(isinstance(op, AddConceptOp) for op in delta.operations)
    has_explicit_target = (
        "target_ontology_version" in delta.model_fields_set
        or "target_version" in delta.model_fields_set
    ) and delta.target_ontology_version is not None

    # Greenfield Genesis draft transition
    if clean_base == "0.0.0":
        if has_cbc_addition:
            if has_explicit_target:
                t_maj, t_min, t_pat = parse_semver(delta.target_ontology_version)
                if (t_maj, t_min, t_pat) < (1, 0, 0):
                    raise SemVerCalculusError(
                        f"ONT_EVO_ENVELOPE_VIOLATION: Initial CBC addition requires release "
                        f"version >= 1.0.0, got '{delta.target_ontology_version}'."
                    )
                return delta.target_ontology_version
            return "1.0.0"

        # Pre-CBC draft increments
        if has_explicit_target:
            return delta.target_ontology_version
        return "0.1.0"

    # Post-Genesis releases (>= 1.0.0)
    b_maj, b_min, b_pat = parse_semver(clean_base)

    # Determine highest required bump rank: 3=MAJOR, 2=MINOR, 1=PATCH
    max_rank = 1
    existing_concept_uris: set[str] = (
        {c.concept_uri for c in base_ontology.canonical_business_concepts}
        if base_ontology is not None
        else set()
    )

    for op in delta.operations:
        # Check explicit structural deletions and identity mutations
        if isinstance(
            op, (RemoveConceptOp, RemoveRelationshipOp, RemoveAttributeOp, UpdateConceptUrnOp)
        ):
            max_rank = max(max_rank, 3)
            continue

        # Check SetParentConceptOp & SetConceptElmArchetypeOp dynamic elevation
        if isinstance(op, (SetParentConceptOp, SetConceptElmArchetypeOp)):
            eff = op.effective_semver_class(base_ontology=base_ontology)
            max_rank = max(max_rank, 3) if eff == "MAJOR" else max(max_rank, 2)
            continue

        # Check AddAttributeOp: non-optional attribute on existing concept is destructive (MAJOR)
        if isinstance(op, AddAttributeOp):
            if not op.is_optional and op.concept_uri in existing_concept_uris:
                max_rank = max(max_rank, 3)
            else:
                max_rank = max(max_rank, 2)
            continue

        # Additive structural & deprecations
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

        # Metadata / annotations are PATCH
        max_rank = max(max_rank, 1)

    # Validate against target_ontology_version if explicitly specified
    if has_explicit_target and delta.target_ontology_version:
        t_maj, t_min, t_pat = parse_semver(delta.target_ontology_version)

        meta = delta.metadata or {}
        mode_val = meta.get("mode")
        action_val = meta.get("action")
        is_bootstrap = (
            mode_val == "BOOTSTRAP"
            or action_val == "BOOTSTRAP"
            or (
                clean_base == "1.0.0"
                and delta.target_ontology_version == "1.0.0"
                and mode_val in ("BOOTSTRAP", None)
            )
        )
        if (
            is_bootstrap
            and clean_base == "1.0.0"
            and delta.target_ontology_version == "1.0.0"
            and max_rank < 3
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


def apply(
    base: GraphAOntology,
    delta: OntologyDelta,
    *,
    validate_structural: bool = True,
) -> GraphAOntology:
    """Pure state transition engine: NEVER mutates `base` in-place.

    Returns a new, fully validated `GraphAOntology` instance applying all operations
    in `delta` with SemVer calculus and lineage anchoring.

    Args:
        base: Base GraphAOntology state.
        delta: OntologyDelta carrying 24-op closed catalog operations.
        validate_structural: Whether to run Phase 1 StructuralValidator on target.

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

            if op.concept_uri in concepts:
                existing = concepts[op.concept_uri]
                if not existing.target_hub and op.target_hub:
                    existing.target_hub = op.target_hub
                if op.parent_concept_uri:
                    existing.parent_concept_uri = op.parent_concept_uri
                if op.synonyms:
                    existing_syns = existing.synonyms or []
                    for syn in op.synonyms:
                        if not any(s.casefold() == syn.casefold() for s in existing_syns):
                            existing_syns.append(syn)
                    existing.synonyms = existing_syns
                if ext_maps:
                    existing_maps = existing.external_mappings or []
                    existing_map_uris = {m.target_uri for m in existing_maps}
                    for new_m in ext_maps:
                        if new_m.target_uri not in existing_map_uris:
                            existing_maps.append(new_m)
                    existing.external_mappings = existing_maps
            else:
                c_name = op.concept_name or op.concept_uri.split(":")[-1].replace("_", " ").title()
                defn = op.definition or op.description or f"Canonical business concept: {c_name}."
                concepts[op.concept_uri] = CanonicalBusinessConcept(
                    concept_uri=op.concept_uri,
                    concept_name=c_name,
                    elm_archetype=op.elm_archetype or "CORE_BUSINESS_CONCEPT",
                    definition=defn,
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
                if op.relationship_name:
                    existing_r.relationship_name = op.relationship_name
                if op.target_link:
                    existing_r.target_link = op.target_link
                if endpoints:
                    existing_r.endpoints = endpoints
                if op.grain_semantics:
                    existing_r.grain_semantics = op.grain_semantics
                if yagni and existing_r.yagni_cost_guard_policy is None:
                    existing_r.yagni_cost_guard_policy = yagni
                if op.metadata:
                    if existing_r.metadata is None:
                        existing_r.metadata = {}
                    existing_r.metadata.update(op.metadata)
            else:
                r_name = (
                    op.relationship_name
                    or op.relationship_uri.split(":")[-1].replace("_", " ").title()
                )
                grain = (
                    op.grain_semantics or op.description or f"Unit-of-work link {op.target_link}."
                )
                relationships[op.relationship_uri] = CanonicalRelationship(
                    relationship_uri=op.relationship_uri,
                    relationship_name=r_name,
                    target_link=op.target_link,
                    endpoints=endpoints,
                    grain_semantics=grain,
                    yagni_cost_guard_policy=yagni,
                    metadata=dict(op.metadata) if op.metadata else None,
                )

        elif isinstance(op, AddAttributeOp):
            if op.concept_uri in concepts:
                c = concepts[op.concept_uri]
                if c.attributes is None:
                    c.attributes = []
                found = False
                for a in c.attributes:
                    if a.attribute_name == op.attribute_name:
                        if op.data_type:
                            a.data_type = op.data_type
                        if op.description:
                            a.description = op.description
                        if op.is_optional is not None:
                            a.is_optional = op.is_optional
                        if op.example_values:
                            a.example_values = list(op.example_values)
                        if op.metadata:
                            if a.metadata is None:
                                a.metadata = {}
                            a.metadata.update(op.metadata)
                        found = True
                        break
                if not found:
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

            if enum_uri in enumerations:
                existing_codes = enumerations[enum_uri].allowed_codes
                merged_codes = sorted(list(set(existing_codes + codes)))
                enumerations[enum_uri] = ReferenceEnumeration(
                    enum_uri=enum_uri,
                    target_reference_table=enumerations[enum_uri].target_reference_table,
                    allowed_codes=merged_codes,
                )
            else:
                enumerations[enum_uri] = ReferenceEnumeration(
                    enum_uri=enum_uri,
                    target_reference_table=table_name,
                    allowed_codes=codes,
                )

        elif isinstance(op, AddReferenceEnumCodeOp):
            target_enum = None
            for e in enumerations.values():
                if (op.enum_uri and e.enum_uri == op.enum_uri) or (
                    op.enumeration_name
                    and (
                        e.enumeration_name == op.enumeration_name
                        or e.target_reference_table == f"ref_{op.enumeration_name}"
                        or e.target_reference_table == op.enumeration_name
                    )
                ):
                    target_enum = e
                    break
            if target_enum is not None:
                if op.code not in target_enum.allowed_codes:
                    target_enum.allowed_codes = sorted(
                        list(set([*target_enum.allowed_codes, op.code]))
                    )
                if op.description:
                    if target_enum.code_descriptions is None:
                        target_enum.code_descriptions = {}
                    target_enum.code_descriptions[op.code] = op.description

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
                if cleaned and not any(s.casefold() == cleaned.casefold() for s in c.synonyms):
                    c.synonyms.append(cleaned)

        elif isinstance(op, AddExternalMappingOp):
            if op.concept_uri in concepts:
                c = concepts[op.concept_uri]
                if c.external_mappings is None:
                    c.external_mappings = []
                target_u = op.target_uri or op.standard_uri
                scheme_val = op.scheme or op.standard_name
                pred = op.predicate or op.mapping_type
                found = False
                for m in c.external_mappings:
                    if m.target_uri == target_u and m.predicate == pred:
                        if op.mapping_confidence is not None:
                            m.confidence = op.mapping_confidence
                        if op.pinned is not None:
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
                        if m.target_uri == target_u:
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

        elif isinstance(op, AddSourceHintOp):
            if op.concept_uri in concepts:
                c = concepts[op.concept_uri]
                if c.source_hints is None:
                    c.source_hints = []
                hint_str = op.hint or f"{op.source_name}.{op.table_name}.{op.column_name}".strip(
                    "."
                )
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

    # 5. Calculate resulting SemVer version
    new_version = compute_next_semver(base.ontology_version, delta, base_ontology=base)

    target_ontology = GraphAOntology(
        ontology_version=new_version,
        parent_sha256=base_sha256,
        decision_refs=decision_refs,
        domain_namespace=delta.domain_namespace or base.domain_namespace,
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


def apply_ontology_delta(
    base: GraphAOntology,
    delta: OntologyDelta,
    accepted_op_ids: list[str] | None = None,
    decision_id: str | None = None,
    target_version: str | None = None,
    deployed_contract: Any | None = None,
) -> GraphAOntology:
    """Deterministically apply accepted delta operations to a base Graph A ontology.

    Maintains backward compatibility with legacy API signatures while utilizing
    pure state transition applier.
    """
    if accepted_op_ids is not None:
        accepted_set = set(accepted_op_ids)
        ops_to_apply = [op for op in delta.operations if op.op_id in accepted_set]
    else:
        ops_to_apply = list(delta.operations)

    sub_delta = OntologyDelta(
        delta_id=delta.delta_id,
        parent_sha256=delta.parent_sha256,
        domain_namespace=delta.domain_namespace,
        base_ontology_version=delta.base_ontology_version,
        target_ontology_version=target_version or delta.target_ontology_version,
        operations=ops_to_apply,
        metadata=delta.metadata,
    )

    val_result = validate_ontology_delta(base, sub_delta, deployed_contract=deployed_contract)
    if not val_result.valid:
        raise ValueError(f"Cannot apply invalid ontology delta: {'; '.join(val_result.violations)}")

    target = apply(base, sub_delta, validate_structural=True)

    if decision_id and decision_id not in target.decision_refs:
        target.decision_refs.append(decision_id)

    return target


__all__ = [
    "apply",
    "apply_ontology_delta",
    "compute_next_semver",
    "parse_semver",
]
