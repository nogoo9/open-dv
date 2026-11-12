"""Ontology delta rebase conflict detection and SemVer recomputation engine (docs/19 §7.4).

Implements deterministic 24-operation rebase conflict analysis (`check_rebase_conflicts`)
comparing a proposed delta/operation list anchored at `base_ontology` against an advanced
`head_ontology` (and optional `intervening_deltas`), followed by SemVer target recomputation
and full structural (and optional governance) validation on the rebased state.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
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
    OntologyOperation,
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
)
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    GraphAOntology,
    ReferenceEnumeration,
)
from opendv_service.ontology.applier import apply, compute_next_semver
from opendv_service.ontology.hashing import compute_ontology_sha256
from opendv_service.ontology.validator import GovernanceValidator, OntologyGovernanceError


@dataclass(frozen=True)
class RebaseConflict:
    """Single rebase conflict detected between a proposal operation and head state."""

    proposal_op_id: str
    proposal_op_type: str
    conflicting_target: str
    reason: str
    error_code: str = ErrorCode.PROPOSAL_STALE_REBASE_REQUIRED.value
    intervening_op_id: str | None = None
    intervening_op_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "proposal_op_id": self.proposal_op_id,
            "proposal_op_type": self.proposal_op_type,
            "conflicting_target": self.conflicting_target,
            "reason": self.reason,
            "error_code": self.error_code,
        }
        if self.intervening_op_id is not None:
            payload["intervening_op_id"] = self.intervening_op_id
        if self.intervening_op_type is not None:
            payload["intervening_op_type"] = self.intervening_op_type
        return payload


@dataclass(frozen=True)
class RebaseCheckResult:
    """Outcome of checking whether a stale proposal can cleanly rebase onto HEAD."""

    can_rebase: bool
    conflicts: list[RebaseConflict] = field(default_factory=list)
    target_version: str | None = None
    rebased_delta: OntologyDelta | None = None
    rebased_ontology: GraphAOntology | None = None
    head_sha256: str = ""

    @property
    def is_clean(self) -> bool:
        return self.can_rebase

    @property
    def has_conflicts(self) -> bool:
        return not self.can_rebase or bool(self.conflicts)


def _cbc_dump(cbc: CanonicalBusinessConcept) -> dict[str, Any]:
    return cbc.model_dump(mode="json", exclude_none=True)


def _rel_dump(rel: CanonicalRelationship) -> dict[str, Any]:
    return rel.model_dump(mode="json", exclude_none=True)


def _enum_dump(enum: ReferenceEnumeration) -> dict[str, Any]:
    return enum.model_dump(mode="json", exclude_none=True)


def _enum_key(enum: ReferenceEnumeration) -> str:
    return (enum.enumeration_name or enum.target_reference_table or "").strip().casefold()


def _mapping_dict(cbc: CanonicalBusinessConcept | None) -> dict[str, dict[str, Any]]:
    if cbc is None:
        return {}
    return {
        m.target_uri: m.model_dump(mode="json", exclude_none=True)
        for m in (cbc.external_mappings or [])
    }


def _attr_dict(cbc: CanonicalBusinessConcept | None) -> dict[str, dict[str, Any]]:
    if cbc is None:
        return {}
    return {
        a.attribute_name.casefold(): a.model_dump(mode="json", exclude_none=True)
        for a in (cbc.attributes or [])
    }


def _normalize_tier_slot(tier_val: str | None) -> str:
    tier_str = (tier_val or "TIER_1_GLOBAL_STANDARD").lower()
    if "tier_1" in tier_str or "global" in tier_str:
        return "tier_1_global_standard"
    if "tier_2" in tier_str or "enterprise" in tier_str or "sor" in tier_str:
        return "tier_2_enterprise_sor"
    return "tier_3_local_scoped"


def _tier_dict(cbc: CanonicalBusinessConcept | None) -> dict[str, dict[str, Any]]:
    if cbc is None or cbc.key_authority_tiers is None:
        return {}
    tiers = cbc.key_authority_tiers
    out: dict[str, dict[str, Any]] = {}
    for slot in ("tier_1_global_standard", "tier_2_enterprise_sor", "tier_3_local_scoped"):
        spec = getattr(tiers, slot, None)
        if spec is not None:
            out[slot] = spec.model_dump(mode="json", exclude_none=True)
    return out


def _collect_physical_tables(ont: GraphAOntology) -> dict[str, str]:
    """Map physical table identifier -> owning entity/relationship/enum URI."""
    tables: dict[str, str] = {}
    for c in ont.canonical_business_concepts:
        if c.target_hub:
            tables[c.target_hub] = c.concept_uri
    for r in ont.canonical_relationships:
        if r.target_link:
            tables[r.target_link] = r.relationship_uri
    for e in ont.reference_enumerations:
        if e.target_reference_table:
            tables[e.target_reference_table] = e.enum_uri
    return tables


def _find_intervening_op(
    intervening_ops: Sequence[OntologyOperation],
    *,
    concept_uri: str | None = None,
    relationship_uri: str | None = None,
    relationship_name: str | None = None,
    enum_uri: str | None = None,
    enum_name: str | None = None,
    op_types: set[str] | None = None,
) -> tuple[str | None, str | None]:
    """Locate the first matching intervening operation for diagnostic attribution."""
    for iop in intervening_ops:
        iop_t = iop.op_type
        if op_types is not None and iop_t != "update_concept_urn" and iop_t not in op_types:
            continue
        if concept_uri is not None:
            if isinstance(iop, UpdateConceptUrnOp) and (
                iop.old_concept_uri == concept_uri or iop.new_concept_uri == concept_uri
            ):
                if op_types is None or "update_concept_urn" in op_types:
                    return iop.op_id, iop_t
            else:
                iop_curi = getattr(iop, "concept_uri", None) or (
                    iop.target_uri if isinstance(iop, RefineDefinitionOp) else None
                )
                if iop_curi == concept_uri:
                    return iop.op_id, iop_t
        if (
            relationship_uri is not None
            and getattr(iop, "relationship_uri", None) == relationship_uri
        ):
            return iop.op_id, iop_t
        if (
            relationship_name is not None
            and (getattr(iop, "relationship_name", None) or "").casefold()
            == relationship_name.casefold()
        ):
            return iop.op_id, iop_t
        if enum_uri is not None and getattr(iop, "enum_uri", None) == enum_uri:
            return iop.op_id, iop_t
        if enum_name is not None:
            iop_enum = getattr(iop, "enumeration_name", None) or getattr(iop, "name", None) or ""
            if iop_enum.strip().casefold() == enum_name.strip().casefold():
                return iop.op_id, iop_t
    return None, None


def check_rebase_conflicts(
    proposal_ops: Sequence[OntologyOperation] | OntologyDelta,
    base_ontology: GraphAOntology,
    head_ontology: GraphAOntology,
    intervening_deltas: Sequence[OntologyDelta] | None = None,
    *,
    governance_validator: GovernanceValidator | None = None,
    waivers: list[StewardWaiverRecord] | None = None,
    grandfathered_concept_uris: set[str] | frozenset[str] | None = None,
    raise_on_conflict: bool = False,
) -> RebaseCheckResult:
    """Evaluate 24-operation rebase conflict matrix between base and head ontologies.

    Args:
        proposal_ops: Proposed `OntologyDelta` or sequence of `OntologyOperation`s
            originally authored against `base_ontology`.
        base_ontology: Historical ontology snapshot the proposal was authored against.
        head_ontology: Current session HEAD ontology snapshot.
        intervening_deltas: Optional sequence of committed deltas between `base_ontology`
            and `head_ontology` for fine-grained operation-level attribution.
        governance_validator: Optional `GovernanceValidator` to run Phase 2 grounding and
            deployed-contract checks on the rebased delta.
        waivers: Optional admin waivers passed to `governance_validator`.
        grandfathered_concept_uris: Optional grandfathered concept URI set.
        raise_on_conflict: When True, raises `OpenDVError` on conflict or validation error.

    Returns:
        `RebaseCheckResult` detailing conflict status, conflicts, recomputed `target_version`,
        `rebased_delta`, and `rebased_ontology`.
    """
    source_delta: OntologyDelta | None = (
        proposal_ops if isinstance(proposal_ops, OntologyDelta) else None
    )
    ops: list[OntologyOperation] = (
        list(proposal_ops.operations)
        if isinstance(proposal_ops, OntologyDelta)
        else list(proposal_ops)
    )
    intervening_ops: list[OntologyOperation] = [
        iop for d in (intervening_deltas or []) for iop in d.operations
    ]

    head_sha = compute_ontology_sha256(head_ontology)

    base_concepts = {c.concept_uri: c for c in base_ontology.canonical_business_concepts}
    head_concepts = {c.concept_uri: c for c in head_ontology.canonical_business_concepts}
    head_concept_names = {
        c.concept_name.strip().casefold(): c.concept_uri
        for c in head_ontology.canonical_business_concepts
        if c.concept_name
    }

    base_rels_by_uri = {r.relationship_uri: r for r in base_ontology.canonical_relationships}
    head_rels_by_uri = {r.relationship_uri: r for r in head_ontology.canonical_relationships}
    base_rels_by_name = {
        r.relationship_name.casefold(): r
        for r in base_ontology.canonical_relationships
        if r.relationship_name
    }
    head_rels_by_name = {
        r.relationship_name.casefold(): r
        for r in head_ontology.canonical_relationships
        if r.relationship_name
    }

    base_enums_by_uri = {e.enum_uri: e for e in base_ontology.reference_enumerations}
    head_enums_by_uri = {e.enum_uri: e for e in head_ontology.reference_enumerations}
    base_enums_by_name = {
        _enum_key(e): e for e in base_ontology.reference_enumerations if _enum_key(e)
    }
    head_enums_by_name = {
        _enum_key(e): e for e in head_ontology.reference_enumerations if _enum_key(e)
    }

    head_physical_tables = _collect_physical_tables(head_ontology)

    # Track concepts/relationships/enums added earlier within the proposal itself
    proposal_added_concepts: set[str] = set()
    proposal_removed_rels: set[str] = set()
    for pop in ops:
        if isinstance(pop, RemoveRelationshipOp):
            if pop.relationship_uri:
                proposal_removed_rels.add(pop.relationship_uri)
            if pop.relationship_name:
                proposal_removed_rels.add(pop.relationship_name.casefold())

    # Precompute intervening operation sets for transient add/remove detection
    intervening_removed_or_renamed_concepts: set[str] = set()
    intervening_deprecated_concepts: set[str] = set()
    intervening_touched_concepts: set[str] = set()
    for iop in intervening_ops:
        if isinstance(iop, RemoveConceptOp):
            intervening_removed_or_renamed_concepts.add(iop.concept_uri)
            intervening_touched_concepts.add(iop.concept_uri)
        elif isinstance(iop, UpdateConceptUrnOp):
            intervening_removed_or_renamed_concepts.add(iop.old_concept_uri)
            intervening_touched_concepts.add(iop.old_concept_uri)
            intervening_touched_concepts.add(iop.new_concept_uri)
        elif isinstance(iop, DeprecateConceptOp):
            intervening_deprecated_concepts.add(iop.concept_uri)
            intervening_touched_concepts.add(iop.concept_uri)
        elif isinstance(iop, RefineDefinitionOp) and (iop.concept_uri or iop.target_uri):
            intervening_touched_concepts.add(iop.concept_uri or iop.target_uri)
        elif getattr(iop, "concept_uri", None):
            intervening_touched_concepts.add(iop.concept_uri)

    conflicts: list[RebaseConflict] = []

    def add_conflict(
        op: OntologyOperation,
        target: str,
        reason: str,
        *,
        error_code: ErrorCode = ErrorCode.PROPOSAL_STALE_REBASE_REQUIRED,
        iop_id: str | None = None,
        iop_type: str | None = None,
    ) -> None:
        conflicts.append(
            RebaseConflict(
                proposal_op_id=op.op_id,
                proposal_op_type=op.op_type,
                conflicting_target=target,
                reason=reason,
                error_code=error_code.value,
                intervening_op_id=iop_id,
                intervening_op_type=iop_type,
            )
        )

    def check_concept_available(op: OntologyOperation, uri: str) -> bool:
        """Verify target concept `uri` was not deleted, renamed away, or newly deprecated."""
        if uri in intervening_removed_or_renamed_concepts or (
            uri in base_concepts and uri not in head_concepts
        ):
            iop_id, iop_type = _find_intervening_op(
                intervening_ops,
                concept_uri=uri,
                op_types={"remove_concept", "update_concept_urn"},
            )
            add_conflict(
                op,
                uri,
                f"Target concept '{uri}' was removed or renamed in an intervening commit.",
                iop_id=iop_id,
                iop_type=iop_type,
            )
            return False

        was_deprecated = uri in base_concepts and bool(base_concepts[uri].deprecated)
        is_deprecated = (uri in head_concepts and bool(head_concepts[uri].deprecated)) or (
            uri in intervening_deprecated_concepts
        )
        if is_deprecated and not was_deprecated:
            iop_id, iop_type = _find_intervening_op(
                intervening_ops,
                concept_uri=uri,
                op_types={"deprecate_concept"},
            )
            add_conflict(
                op,
                uri,
                f"Target concept '{uri}' was deprecated in an intervening commit.",
                iop_id=iop_id,
                iop_type=iop_type,
            )
            return False

        if uri not in head_concepts and uri not in proposal_added_concepts:
            add_conflict(
                op,
                uri,
                f"Target concept '{uri}' does not exist in HEAD ontology.",
                error_code=ErrorCode.REFERENCED_CONCEPT_NOT_FOUND,
            )
            return False

        return True

    def concept_changed_between_base_and_head(uri: str) -> bool:
        if uri in intervening_touched_concepts:
            return True
        b_c = base_concepts.get(uri)
        h_c = head_concepts.get(uri)
        if b_c is None or h_c is None:
            return b_c is not h_c
        return _cbc_dump(b_c) != _cbc_dump(h_c)

    def concept_gained_new_incoming_references(uri: str) -> bool:
        base_rel_uris = set(base_rels_by_uri.keys())
        for r_uri, h_rel in head_rels_by_uri.items():
            if (
                r_uri not in base_rel_uris
                and r_uri not in proposal_removed_rels
                and h_rel.relationship_name.casefold() not in proposal_removed_rels
                and uri in (h_rel.endpoints or [])
            ):
                return True
        for c_uri, h_c in head_concepts.items():
            if h_c.parent_concept_uri == uri:
                b_c = base_concepts.get(c_uri)
                if b_c is None or b_c.parent_concept_uri != uri:
                    return True
        return False

    for op in ops:
        if isinstance(op, AddConceptOp):
            uri = op.concept_uri
            name_cf = op.concept_name.strip().casefold()
            hub = op.target_hub or f"hub_{uri.rsplit(':', 1)[-1]}"
            if uri in head_concepts or uri in intervening_touched_concepts:
                iop_id, iop_type = _find_intervening_op(intervening_ops, concept_uri=uri)
                add_conflict(
                    op,
                    uri,
                    f"Concept URI '{uri}' already exists or was modified in HEAD.",
                    iop_id=iop_id,
                    iop_type=iop_type,
                )
                continue
            if name_cf in head_concept_names:
                add_conflict(
                    op,
                    uri,
                    f"Concept name '{op.concept_name}' already exists in HEAD under "
                    f"'{head_concept_names[name_cf]}'.",
                )
                continue
            if hub in head_physical_tables:
                add_conflict(
                    op,
                    hub,
                    f"Physical table '{hub}' already exists in HEAD "
                    f"(owned by '{head_physical_tables[hub]}').",
                    error_code=ErrorCode.PHYSICAL_TABLE_NAME_COLLISION,
                )
                continue
            if op.parent_concept_uri and not check_concept_available(op, op.parent_concept_uri):
                continue
            proposal_added_concepts.add(uri)

        elif isinstance(op, RemoveConceptOp):
            uri = op.concept_uri
            if not check_concept_available(op, uri):
                continue
            if concept_changed_between_base_and_head(uri) or concept_gained_new_incoming_references(
                uri
            ):
                iop_id, iop_type = _find_intervening_op(intervening_ops, concept_uri=uri)
                add_conflict(
                    op,
                    uri,
                    f"Cannot remove concept '{uri}' because it was concurrently modified or "
                    "referenced in HEAD.",
                    iop_id=iop_id,
                    iop_type=iop_type,
                )

        elif isinstance(op, DeprecateConceptOp):
            uri = op.concept_uri
            if not check_concept_available(op, uri):
                continue
            if concept_changed_between_base_and_head(uri) or concept_gained_new_incoming_references(
                uri
            ):
                iop_id, iop_type = _find_intervening_op(intervening_ops, concept_uri=uri)
                add_conflict(
                    op,
                    uri,
                    f"Cannot deprecate concept '{uri}' because it was concurrently modified or "
                    "referenced in HEAD.",
                    iop_id=iop_id,
                    iop_type=iop_type,
                )

        elif isinstance(op, UpdateConceptUrnOp):
            old_uri = op.old_concept_uri
            new_uri = op.new_concept_uri
            if not check_concept_available(op, old_uri):
                continue
            if new_uri in head_concepts or new_uri in intervening_touched_concepts:
                iop_id, iop_type = _find_intervening_op(intervening_ops, concept_uri=new_uri)
                add_conflict(
                    op,
                    new_uri,
                    f"Target rename URI '{new_uri}' already exists or was touched in HEAD.",
                    iop_id=iop_id,
                    iop_type=iop_type,
                )
                continue
            if concept_changed_between_base_and_head(
                old_uri
            ) or concept_gained_new_incoming_references(old_uri):
                iop_id, iop_type = _find_intervening_op(intervening_ops, concept_uri=old_uri)
                add_conflict(
                    op,
                    old_uri,
                    f"Cannot rename concept '{old_uri}' because it was concurrently modified or "
                    "referenced in HEAD.",
                    iop_id=iop_id,
                    iop_type=iop_type,
                )
                continue
            proposal_added_concepts.add(new_uri)

        elif isinstance(op, SetParentConceptOp):
            uri = op.concept_uri
            if not check_concept_available(op, uri):
                continue
            if op.parent_concept_uri and not check_concept_available(op, op.parent_concept_uri):
                continue
            b_parent = base_concepts[uri].parent_concept_uri if uri in base_concepts else None
            h_parent = head_concepts[uri].parent_concept_uri if uri in head_concepts else None
            iop_id, iop_type = _find_intervening_op(
                intervening_ops, concept_uri=uri, op_types={"set_parent_concept"}
            )
            if b_parent != h_parent or iop_id is not None:
                add_conflict(
                    op,
                    uri,
                    f"Concept '{uri}' parent hierarchy was concurrently modified in HEAD.",
                    iop_id=iop_id,
                    iop_type=iop_type,
                )

        elif isinstance(op, SetConceptElmArchetypeOp):
            uri = op.concept_uri
            if not check_concept_available(op, uri):
                continue
            b_arch = base_concepts[uri].elm_archetype if uri in base_concepts else None
            h_arch = head_concepts[uri].elm_archetype if uri in head_concepts else None
            iop_id, iop_type = _find_intervening_op(
                intervening_ops, concept_uri=uri, op_types={"set_concept_elm_archetype"}
            )
            if b_arch != h_arch or iop_id is not None:
                add_conflict(
                    op,
                    uri,
                    f"Concept '{uri}' ELM archetype was concurrently modified in HEAD.",
                    iop_id=iop_id,
                    iop_type=iop_type,
                )

        elif isinstance(op, AddAttributeOp):
            uri = op.concept_uri
            if not check_concept_available(op, uri):
                continue
            attr_cf = op.attribute_name.casefold()
            b_attrs = _attr_dict(base_concepts.get(uri))
            h_attrs = _attr_dict(head_concepts.get(uri))
            intervening_attr_hit = next(
                (
                    (iop.op_id, iop.op_type)
                    for iop in intervening_ops
                    if (
                        getattr(iop, "concept_uri", None) == uri
                        or (isinstance(iop, RefineDefinitionOp) and iop.target_uri == uri)
                    )
                    and (
                        (
                            isinstance(iop, (AddAttributeOp, RemoveAttributeOp))
                            and iop.attribute_name.casefold() == attr_cf
                        )
                        or (
                            isinstance(iop, RefineDefinitionOp)
                            and any(
                                (
                                    a
                                    if isinstance(a, str)
                                    else getattr(a, "attribute_name", str(a))
                                ).casefold()
                                == attr_cf
                                for a in (iop.additional_attributes or [])
                            )
                        )
                    )
                ),
                (None, None),
            )
            if (
                attr_cf in h_attrs
                or b_attrs.get(attr_cf) != h_attrs.get(attr_cf)
                or intervening_attr_hit[0] is not None
            ):
                add_conflict(
                    op,
                    f"{uri}#{op.attribute_name}",
                    f"Attribute '{op.attribute_name}' on '{uri}' already exists or was "
                    "concurrently modified in HEAD.",
                    error_code=ErrorCode.DUPLICATE_ATTRIBUTE_NAME,
                    iop_id=intervening_attr_hit[0],
                    iop_type=intervening_attr_hit[1],
                )

        elif isinstance(op, RemoveAttributeOp):
            uri = op.concept_uri
            if not check_concept_available(op, uri):
                continue
            attr_cf = op.attribute_name.casefold()
            b_attrs = _attr_dict(base_concepts.get(uri))
            h_attrs = _attr_dict(head_concepts.get(uri))
            intervening_attr_hit = next(
                (
                    (iop.op_id, iop.op_type)
                    for iop in intervening_ops
                    if getattr(iop, "concept_uri", None) == uri
                    and isinstance(iop, (AddAttributeOp, RemoveAttributeOp))
                    and iop.attribute_name.casefold() == attr_cf
                ),
                (None, None),
            )
            if (
                (uri in head_concepts and attr_cf not in h_attrs)
                or b_attrs.get(attr_cf) != h_attrs.get(attr_cf)
                or intervening_attr_hit[0] is not None
            ):
                add_conflict(
                    op,
                    f"{uri}#{op.attribute_name}",
                    f"Attribute '{op.attribute_name}' on '{uri}' was concurrently removed or "
                    "modified in HEAD.",
                    iop_id=intervening_attr_hit[0],
                    iop_type=intervening_attr_hit[1],
                )

        elif isinstance(op, AddRelationshipOp):
            r_uri = op.relationship_uri
            r_name = (op.relationship_name or r_uri.rsplit(":", 1)[-1]).strip()
            r_link = op.target_link or f"lnk_{r_name}"
            endpoints = (
                list(op.endpoints)
                if op.endpoints
                else [ep for ep in (op.source_concept_uri, op.target_concept_uri) if ep]
            )
            if not all(check_concept_available(op, ep) for ep in endpoints):
                continue
            if r_uri in head_rels_by_uri:
                iop_id, iop_type = _find_intervening_op(intervening_ops, relationship_uri=r_uri)
                add_conflict(
                    op,
                    r_uri,
                    f"Relationship URI '{r_uri}' already exists in HEAD.",
                    error_code=ErrorCode.DUPLICATE_RELATIONSHIP_URI,
                    iop_id=iop_id,
                    iop_type=iop_type,
                )
                continue
            if r_name.casefold() in head_rels_by_name:
                iop_id, iop_type = _find_intervening_op(intervening_ops, relationship_name=r_name)
                add_conflict(
                    op,
                    r_name,
                    f"Relationship name '{r_name}' already exists in HEAD.",
                    error_code=ErrorCode.DUPLICATE_RELATIONSHIP_NAME,
                    iop_id=iop_id,
                    iop_type=iop_type,
                )
                continue
            if r_link in head_physical_tables:
                add_conflict(
                    op,
                    r_link,
                    f"Physical link table '{r_link}' collides with existing table in HEAD.",
                    error_code=ErrorCode.PHYSICAL_TABLE_NAME_COLLISION,
                )

        elif isinstance(op, (RemoveRelationshipOp, DeprecateRelationshipOp)):
            r_uri = op.relationship_uri
            r_name = op.relationship_name
            b_rel = (
                base_rels_by_uri.get(r_uri)
                if r_uri
                else (base_rels_by_name.get(r_name.casefold()) if r_name else None)
            )
            h_rel = (
                head_rels_by_uri.get(r_uri)
                if r_uri
                else (head_rels_by_name.get(r_name.casefold()) if r_name else None)
            )
            target_id = r_uri or r_name or "<relationship>"
            iop_id, iop_type = _find_intervening_op(
                intervening_ops, relationship_uri=r_uri, relationship_name=r_name
            )
            if (
                h_rel is None
                or (b_rel is not None and _rel_dump(b_rel) != _rel_dump(h_rel))
                or iop_id
            ):
                add_conflict(
                    op,
                    target_id,
                    f"Relationship '{target_id}' was concurrently removed, deprecated, or "
                    "modified in HEAD.",
                    iop_id=iop_id,
                    iop_type=iop_type,
                )

        elif isinstance(op, AddReferenceEnumerationOp):
            e_uri = op.enum_uri
            e_name = (op.name or op.enumeration_name or e_uri.rsplit(":", 1)[-1]).strip()
            e_tbl = op.target_reference_table or f"ref_{e_name}"
            if e_uri in head_enums_by_uri:
                iop_id, iop_type = _find_intervening_op(intervening_ops, enum_uri=e_uri)
                add_conflict(
                    op,
                    e_uri,
                    f"Reference enumeration URI '{e_uri}' already exists in HEAD.",
                    iop_id=iop_id,
                    iop_type=iop_type,
                )
                continue
            if e_name.casefold() in head_enums_by_name:
                iop_id, iop_type = _find_intervening_op(intervening_ops, enum_name=e_name)
                add_conflict(
                    op,
                    e_name,
                    f"Reference enumeration '{e_name}' already exists in HEAD.",
                    iop_id=iop_id,
                    iop_type=iop_type,
                )
                continue
            if e_tbl in head_physical_tables:
                add_conflict(
                    op,
                    e_tbl,
                    f"Physical reference table '{e_tbl}' collides with existing table in HEAD.",
                    error_code=ErrorCode.PHYSICAL_TABLE_NAME_COLLISION,
                )

        elif isinstance(op, AddReferenceEnumCodeOp):
            e_uri = op.enum_uri.strip()
            e_name = op.enumeration_name.strip()
            e_key = e_name.casefold()
            b_enum = (
                base_enums_by_uri.get(e_uri)
                if e_uri
                else (base_enums_by_name.get(e_key) if e_key else None)
            )
            h_enum = (
                head_enums_by_uri.get(e_uri)
                if e_uri
                else (head_enums_by_name.get(e_key) if e_key else None)
            )
            target_enum = e_uri or e_name or "<enum>"
            if b_enum is not None and h_enum is None:
                iop_id, iop_type = _find_intervening_op(
                    intervening_ops, enum_uri=e_uri or None, enum_name=e_name or None
                )
                add_conflict(
                    op,
                    target_enum,
                    f"Reference enumeration '{target_enum}' was removed in HEAD.",
                    iop_id=iop_id,
                    iop_type=iop_type,
                )
                continue
            if h_enum is not None and (
                bool(h_enum.deprecated) or op.code in (h_enum.allowed_codes or [])
            ):
                iop_id, iop_type = _find_intervening_op(
                    intervening_ops, enum_uri=e_uri or None, enum_name=e_name or None
                )
                add_conflict(
                    op,
                    f"{target_enum}:{op.code}",
                    f"Enumeration code '{op.code}' on '{target_enum}' already exists or enum was "
                    "deprecated in HEAD.",
                    error_code=ErrorCode.DUPLICATE_ENUM_CODE,
                    iop_id=iop_id,
                    iop_type=iop_type,
                )

        elif isinstance(op, AddSynonymOp):
            uri = op.concept_uri
            if not check_concept_available(op, uri):
                continue
            syn_cf = op.synonym.strip().casefold()
            h_c = head_concepts.get(uri)
            if h_c is not None and (
                syn_cf == h_c.concept_name.strip().casefold()
                or any(s.strip().casefold() == syn_cf for s in (h_c.synonyms or []))
            ):
                iop_id, iop_type = _find_intervening_op(
                    intervening_ops, concept_uri=uri, op_types={"add_synonym"}
                )
                add_conflict(
                    op,
                    f"{uri}:{op.synonym}",
                    f"Synonym '{op.synonym}' on '{uri}' already exists in HEAD.",
                    error_code=ErrorCode.DUPLICATE_SYNONYM,
                    iop_id=iop_id,
                    iop_type=iop_type,
                )

        elif isinstance(
            op,
            (
                AddExternalMappingOp,
                RescoreExternalMappingOp,
                RepinExternalMappingOp,
                RemoveExternalMappingOp,
            ),
        ):
            uri = op.concept_uri
            if not check_concept_available(op, uri):
                continue
            b_maps = _mapping_dict(base_concepts.get(uri))
            h_maps = _mapping_dict(head_concepts.get(uri))
            target_uris = [op.target_uri]
            intervening_map_hit = next(
                (
                    (iop.op_id, iop.op_type)
                    for iop in intervening_ops
                    if getattr(iop, "concept_uri", None) == uri
                    and isinstance(
                        iop,
                        (
                            AddExternalMappingOp,
                            RescoreExternalMappingOp,
                            RepinExternalMappingOp,
                            RemoveExternalMappingOp,
                        ),
                    )
                    and iop.target_uri in target_uris
                ),
                (None, None),
            )
            changed_in_head = any(b_maps.get(t) != h_maps.get(t) for t in target_uris)
            already_present_on_add = isinstance(op, AddExternalMappingOp) and (
                op.target_uri in h_maps
            )
            missing_on_mutate = isinstance(
                op,
                (RescoreExternalMappingOp, RepinExternalMappingOp, RemoveExternalMappingOp),
            ) and (uri in head_concepts and op.target_uri not in h_maps)
            if (
                changed_in_head
                or already_present_on_add
                or missing_on_mutate
                or intervening_map_hit[0] is not None
            ):
                add_conflict(
                    op,
                    f"{uri}->{target_uris[0]}",
                    f"External mapping on '{uri}' ({', '.join(target_uris)}) was concurrently "
                    "modified in HEAD.",
                    iop_id=intervening_map_hit[0],
                    iop_type=intervening_map_hit[1],
                )

        elif isinstance(op, (ReviseDescriptionOp, RefineDefinitionOp)):
            uri = op.concept_uri or op.target_uri
            if not check_concept_available(op, uri):
                continue
            b_c = base_concepts.get(uri)
            h_c = head_concepts.get(uri)
            iop_id, iop_type = _find_intervening_op(
                intervening_ops,
                concept_uri=uri,
                op_types={"revise_description", "refine_definition"},
            )
            desc_changed = (
                b_c is not None
                and h_c is not None
                and (b_c.description != h_c.description or b_c.definition != h_c.definition)
            )
            if desc_changed or iop_id is not None:
                add_conflict(
                    op,
                    uri,
                    f"Description/definition on '{uri}' was concurrently revised in HEAD.",
                    iop_id=iop_id,
                    iop_type=iop_type,
                )
                continue
            if isinstance(op, RefineDefinitionOp) and op.additional_attributes:
                h_attrs = _attr_dict(h_c)
                for add_attr in op.additional_attributes:
                    attr_name = (
                        add_attr
                        if isinstance(add_attr, str)
                        else getattr(add_attr, "attribute_name", str(add_attr))
                    )
                    if attr_name.casefold() in h_attrs:
                        add_conflict(
                            op,
                            f"{uri}#{attr_name}",
                            f"Additional attribute '{attr_name}' on '{uri}' "
                            "already exists in HEAD.",
                            error_code=ErrorCode.DUPLICATE_ATTRIBUTE_NAME,
                        )
                        break

        elif isinstance(op, AddKeyAuthorityPatternOp):
            uri = op.concept_uri
            if not check_concept_available(op, uri):
                continue
            slot = _normalize_tier_slot(op.tier)
            b_tiers = _tier_dict(base_concepts.get(uri))
            h_tiers = _tier_dict(head_concepts.get(uri))
            intervening_tier_hit = next(
                (
                    (iop.op_id, iop.op_type)
                    for iop in intervening_ops
                    if isinstance(iop, AddKeyAuthorityPatternOp)
                    and iop.concept_uri == uri
                    and _normalize_tier_slot(iop.tier) == slot
                ),
                (None, None),
            )
            if (
                slot in h_tiers
                or b_tiers.get(slot) != h_tiers.get(slot)
                or intervening_tier_hit[0] is not None
            ):
                add_conflict(
                    op,
                    f"{uri}:{op.tier}",
                    f"Key authority tier '{op.tier}' on '{uri}' already exists or was "
                    "concurrently modified in HEAD.",
                    iop_id=intervening_tier_hit[0],
                    iop_type=intervening_tier_hit[1],
                )

        elif isinstance(op, AddGovernanceConstraintOp):
            uri = op.concept_uri
            if not check_concept_available(op, uri):
                continue
            b_c = base_concepts.get(uri)
            h_c = head_concepts.get(uri)
            b_gov = (
                b_c.governance_constraints.model_dump(mode="json")
                if (b_c and b_c.governance_constraints is not None)
                else None
            )
            h_gov = (
                h_c.governance_constraints.model_dump(mode="json")
                if (h_c and h_c.governance_constraints is not None)
                else None
            )
            iop_id, iop_type = _find_intervening_op(
                intervening_ops, concept_uri=uri, op_types={"add_governance_constraint"}
            )
            if b_gov != h_gov or iop_id is not None:
                add_conflict(
                    op,
                    uri,
                    f"Governance constraints on '{uri}' were concurrently modified in HEAD.",
                    iop_id=iop_id,
                    iop_type=iop_type,
                )

        elif isinstance(op, AnnotateConceptMetadataOp):
            uri = op.concept_uri
            if not check_concept_available(op, uri):
                continue
            b_meta = dict(base_concepts[uri].metadata or {}) if uri in base_concepts else {}
            h_meta = dict(head_concepts[uri].metadata or {}) if uri in head_concepts else {}
            overlapping_keys = [
                k for k in op.metadata if b_meta.get(k) != h_meta.get(k) or k in h_meta
            ]
            intervening_meta_hit = next(
                (
                    (iop.op_id, iop.op_type)
                    for iop in intervening_ops
                    if isinstance(iop, AnnotateConceptMetadataOp)
                    and iop.concept_uri == uri
                    and bool(set(op.metadata.keys()) & set(iop.metadata.keys()))
                ),
                (None, None),
            )
            if overlapping_keys or intervening_meta_hit[0] is not None:
                add_conflict(
                    op,
                    f"{uri}:{','.join(sorted(op.metadata.keys()))}",
                    f"Metadata keys on '{uri}' collided with concurrent updates in HEAD.",
                    iop_id=intervening_meta_hit[0],
                    iop_type=intervening_meta_hit[1],
                )

        elif isinstance(op, AddSourceHintOp):
            uri = op.concept_uri
            if not check_concept_available(op, uri):
                continue
            hint_clean = (op.hint or "").strip()
            if not hint_clean:
                parts = [
                    p
                    for p in (
                        op.source_system or op.source_name,
                        op.table_name,
                        op.column_name or op.column_hint,
                    )
                    if p
                ]
                hint_clean = ".".join(parts)
            h_hints = set(head_concepts[uri].source_hints or []) if uri in head_concepts else set()
            if hint_clean and hint_clean in h_hints:
                iop_id, iop_type = _find_intervening_op(
                    intervening_ops, concept_uri=uri, op_types={"add_source_hint"}
                )
                add_conflict(
                    op,
                    f"{uri}:{hint_clean}",
                    f"Source hint '{hint_clean}' on '{uri}' already exists in HEAD.",
                    iop_id=iop_id,
                    iop_type=iop_type,
                )

    if conflicts:
        if raise_on_conflict:
            first = conflicts[0]
            raise OpenDVError(
                first.reason,
                error_code=ErrorCode.PROPOSAL_STALE_REBASE_REQUIRED,
                details={"conflicts": [c.to_dict() for c in conflicts]},
            )
        return RebaseCheckResult(
            can_rebase=False,
            conflicts=conflicts,
            target_version=None,
            rebased_delta=None,
            rebased_ontology=None,
            head_sha256=head_sha,
        )

    # Recompute SemVer target version against HEAD and validate structural + governance invariants
    try:
        head_ver = head_ontology.ontology_version
        if not ops:
            target_ver = head_ver
        else:
            probe_delta = OntologyDelta.model_construct(
                _fields_set={
                    "delta_id",
                    "base_ontology_version",
                    "parent_sha256",
                    "domain_namespace",
                    "operations",
                    "metadata",
                },
                delta_id=source_delta.delta_id if source_delta else "delta-rebase-probe",
                base_ontology_version=head_ver,
                target_ontology_version=None,
                parent_sha256=None if head_ver == "0.0.0" else head_sha,
                domain_namespace=(
                    source_delta.domain_namespace
                    if source_delta and source_delta.domain_namespace
                    else head_ontology.domain_namespace
                ),
                operations=ops,
                metadata=dict(source_delta.metadata) if source_delta else {},
            )
            target_ver = compute_next_semver(
                head_ver,
                probe_delta,
                base_ontology=head_ontology,
            )

        rebased_delta = OntologyDelta.model_validate(
            {
                "delta_id": source_delta.delta_id if source_delta else "delta-rebased",
                "base_ontology_version": head_ver,
                "target_ontology_version": target_ver,
                "parent_sha256": None if head_ver == "0.0.0" else head_sha,
                "domain_namespace": (
                    source_delta.domain_namespace
                    if source_delta and source_delta.domain_namespace
                    else head_ontology.domain_namespace
                ),
                "author": source_delta.author if source_delta else "steward:rebase",
                "rationale": (
                    source_delta.rationale
                    if source_delta and source_delta.rationale
                    else "Rebased ontology delta proposal."
                ),
                "operations": [op.model_dump(mode="json", exclude_none=True) for op in ops],
                "metadata": dict(source_delta.metadata) if source_delta else {},
            }
        )

        rebased_ont = apply(head_ontology, rebased_delta, validate_structural=True)

        if governance_validator is not None:
            governance_validator.validate_deployed_contracts(head_ontology, rebased_delta)
            governance_validator.validate_grounding(
                head_ontology,
                rebased_delta,
                rebased_ont,
                waivers=waivers,
                grandfathered_concept_uris=grandfathered_concept_uris,
            )
    except (OntologyGovernanceError, OpenDVError, ValueError) as exc:
        if isinstance(exc, OpenDVError) and hasattr(exc.error_code, "value"):
            err_code = exc.error_code.value
        elif "cycle" in str(exc).lower():
            err_code = ErrorCode.HIERARCHICAL_CYCLE_DETECTED.value
        else:
            err_code = str(
                getattr(exc, "error_code", ErrorCode.PROPOSAL_STALE_REBASE_REQUIRED.value)
            )
        first_op = ops[0] if ops else None
        conflict = RebaseConflict(
            proposal_op_id=first_op.op_id if first_op else "delta",
            proposal_op_type=first_op.op_type if first_op else "delta",
            conflicting_target=head_ontology.domain_namespace,
            reason=str(exc),
            error_code=err_code,
        )
        if raise_on_conflict:
            if isinstance(exc, OpenDVError):
                raise
            raise OpenDVError(
                str(exc),
                error_code=ErrorCode.PROPOSAL_STALE_REBASE_REQUIRED,
                details={"conflicts": [conflict.to_dict()]},
            ) from exc
        return RebaseCheckResult(
            can_rebase=False,
            conflicts=[conflict],
            target_version=None,
            rebased_delta=None,
            rebased_ontology=None,
            head_sha256=head_sha,
        )

    return RebaseCheckResult(
        can_rebase=True,
        conflicts=[],
        target_version=target_ver,
        rebased_delta=rebased_delta,
        rebased_ontology=rebased_ont,
        head_sha256=head_sha,
    )


__all__ = [
    "RebaseCheckResult",
    "RebaseConflict",
    "check_rebase_conflicts",
]
