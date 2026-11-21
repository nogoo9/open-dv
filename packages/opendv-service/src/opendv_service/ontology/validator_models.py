"""Shared governance exceptions, validation result DTOs, and concept mutation predicates."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from opendv_contracts.dtos.ontology import (
    CONCEPT_MUTATING_OP_TYPES,
    RELATIONSHIP_ENDPOINT_MUTATING_OP_TYPES,
    AddAttributeOp,
    AddConceptOp,
    AddExternalMappingOp,
    AddGovernanceConstraintOp,
    AddKeyAuthorityPatternOp,
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
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.genesis import GENESIS_DRAFT_SHA256
from opendv_core.models.graph_a import CanonicalBusinessConcept, GraphAOntology

DEFAULT_GENESIS_SHA256 = GENESIS_DRAFT_SHA256
_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class OntologyGovernanceError(OpenDVError, ValueError):
    """Base exception for all ontology governance and evolution guard errors."""

    error_code: ErrorCode = ErrorCode.ONT_EVO_CONTRACT_VIOLATION
    status_code: int = 422
    exit_code: int = 4

    def __init__(
        self,
        message: str,
        error_code: ErrorCode | str | None = None,
        status_code: int | None = None,
        exit_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        resolved_code: ErrorCode | None = None
        raw_cls_code = self.__class__.error_code
        candidate = error_code if error_code is not None else raw_cls_code
        if isinstance(candidate, ErrorCode):
            resolved_code = candidate
        elif isinstance(candidate, str):
            try:
                resolved_code = ErrorCode(candidate)
            except ValueError:
                resolved_code = ErrorCode.ONT_EVO_CONTRACT_VIOLATION
        super().__init__(message, error_code=resolved_code, details=details)
        self.message = message
        if error_code is not None:
            self.error_code = resolved_code if resolved_code is not None else error_code  # type: ignore[assignment]
        self.status_code = (
            status_code
            if status_code is not None
            else (self.http_status if resolved_code is not None else self.__class__.status_code)
        )
        self.http_status = self.status_code
        if exit_code is not None:
            self.exit_code = exit_code


class ContractViolationError(OntologyGovernanceError):
    """Raised when an operation violates deployed contract immutability (ONT-EVO-001)."""

    error_code = "ONT_EVO_CONTRACT_VIOLATION"
    status_code = 422
    exit_code = 4


OntologyContractViolationError = ContractViolationError


class UngroundedConceptError(OntologyGovernanceError):
    """Raised when evolved concept lacks reference/empirical grounding or waiver (ONT-EVO-002)."""

    error_code = "ONT_EVO_UNGROUNDED_CONCEPT"
    status_code = 422
    exit_code = 4


class ConcurrentModificationError(OntologyGovernanceError):
    """Raised when an optimistic concurrency control precondition fails (ONT-EVO-003)."""

    error_code = "ONT_EVO_CONCURRENT_MODIFICATION"
    status_code = 412
    exit_code = 5


class HierarchicalCycleError(OntologyGovernanceError):
    """Raised when taxonomy DAG hierarchy contains a directed cycle."""

    error_code = "HIERARCHICAL_CYCLE_DETECTED"
    status_code = 422
    exit_code = 4


class ProvisionalTokenError(OntologyGovernanceError):
    """Raised when a provisional URI pattern leaks into committed ontology."""

    error_code = "ONT_EVO_GROUNDING_VIOLATION"
    status_code = 422
    exit_code = 4


class InvalidGroundingWaiverError(OntologyGovernanceError):
    """Raised when a steward waiver record is invalid or lacks required rationale."""

    error_code = "INVALID_GROUNDING_WAIVER"
    status_code = 422
    exit_code = 4


class SemVerCalculusError(OntologyGovernanceError):
    """Raised when delta target version violates SemVer calculus bump requirements."""

    error_code = "ONT_EVO_ENVELOPE_VIOLATION"
    status_code = 422
    exit_code = 4


@dataclass
class StructuralValidationResult:
    """Result report emitted by Phase 1 StructuralValidator."""

    valid: bool
    diagnostics: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    error_code: str | None = None


@dataclass
class GovernanceValidationResult:
    """Result report emitted by Phase 2 GovernanceValidator."""

    valid: bool
    diagnostics: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    error_code: str | None = None


def _is_concept_deployed(
    concept: CanonicalBusinessConcept | None,
    uri: str,
    deployed_concepts: set[str],
    deployed_entities: set[str],
    deployed_tables: set[str],
) -> bool:
    """Return True iff `uri` or its bound Hub/Satellite physical table is deployed."""
    if uri in deployed_concepts or uri in deployed_entities:
        return True
    if concept is not None:
        if concept.target_hub and concept.target_hub in deployed_tables:
            return True
        slug = (
            concept.target_hub.removeprefix("hub_")
            if concept.target_hub
            else uri.rsplit(":", 1)[-1]
        )
        if slug and (f"hub_{slug}" in deployed_tables or f"sat_{slug}" in deployed_tables):
            return True
    return False


def _resolve_base_relationships(
    base: GraphAOntology,
    relationship_uri: str | None,
    relationship_name: str | None,
) -> list[Any]:
    """Resolve matching relationships in `base` by EITHER URI or name (mirroring apply())."""
    rel_uri = (relationship_uri or "").strip()
    rel_name = (relationship_name or "").strip()
    matched: list[Any] = []
    for r in base.canonical_relationships:
        if (rel_uri and r.relationship_uri == rel_uri) or (
            rel_name and r.relationship_name == rel_name
        ):
            matched.append(r)
    return matched


def is_concept_mutated(
    base: GraphAOntology,
    delta: OntologyDelta,
    concept_uri: str,
) -> bool:
    """Return True iff `delta` mutates `concept_uri` under ONT-EVO-002 rules."""
    for op in delta.operations:
        op_type = op.op_type
        if op_type in RELATIONSHIP_ENDPOINT_MUTATING_OP_TYPES and isinstance(
            op, (RemoveRelationshipOp, DeprecateRelationshipOp)
        ):
            for rel in _resolve_base_relationships(base, op.relationship_uri, op.relationship_name):
                if concept_uri in rel.endpoints:
                    return True
            continue

        if op_type not in CONCEPT_MUTATING_OP_TYPES and op_type != "remove_concept":
            continue

        if isinstance(op, AddConceptOp):
            if op.concept_uri == concept_uri:
                return True
        elif isinstance(op, UpdateConceptUrnOp):
            if concept_uri in (op.old_concept_uri, op.new_concept_uri):
                return True
        elif isinstance(op, (RefineDefinitionOp, ReviseDescriptionOp)):
            target_u = op.concept_uri or op.target_uri
            if target_u == concept_uri:
                return True
        elif (
            isinstance(
                op,
                (
                    SetParentConceptOp,
                    SetConceptElmArchetypeOp,
                    DeprecateConceptOp,
                    RemoveConceptOp,
                    AddAttributeOp,
                    RemoveAttributeOp,
                    AddKeyAuthorityPatternOp,
                    AddGovernanceConstraintOp,
                    AddSynonymOp,
                    AddExternalMappingOp,
                    RescoreExternalMappingOp,
                    RepinExternalMappingOp,
                    RemoveExternalMappingOp,
                    AddSourceHintOp,
                    AnnotateConceptMetadataOp,
                ),
            )
            and op.concept_uri == concept_uri
        ):
            return True

    return False


PATH_B_GROUNDING_ORIGINS: frozenset[str] = frozenset(
    {"STRUCTURED_SCHEMA", "DOCUMENT_OBSERVED"}
)


def _is_verifiable_evidence_ref(ref: Any, origin: str) -> bool:
    """Return True if `ref` contains a concrete verifiable empirical anchor for Path B."""
    ev_type = str(getattr(ref, "evidence_type", "") or "").strip()
    if not ev_type:
        return False
    if origin == "STRUCTURED_SCHEMA":
        return bool(
            getattr(ref, "bronze_table", None)
            or getattr(ref, "column_name", None)
            or getattr(ref, "table_id", None)
            or getattr(ref, "reference_uri", None)
            or getattr(ref, "snippet", None)
        )
    return bool(
        getattr(ref, "chunk_id", None)
        or getattr(ref, "doc_sha256", None)
        or getattr(ref, "source_label", None)
        or getattr(ref, "reference_uri", None)
        or getattr(ref, "snippet", None)
        or getattr(ref, "char_start", None) is not None
    )


def _collect_concept_evidence_and_origin(
    concept_uri: str,
    concept: CanonicalBusinessConcept | None,
    delta: OntologyDelta,
) -> tuple[str | None, list[Any]]:
    """Collect effective grounding_origin and EvidenceRefs for `concept_uri`."""
    origin = getattr(concept, "grounding_origin", None) if concept is not None else None
    refs: list[Any] = []
    for op in delta.operations:
        op_target = (
            getattr(op, "new_concept_uri", None)
            or getattr(op, "concept_uri", None)
            or getattr(op, "target_uri", None)
        )
        if op_target != concept_uri:
            continue
        if origin is None:
            origin = getattr(op, "grounding_origin", None)
        op_refs = getattr(op, "evidence_refs", None) or []
        refs.extend(op_refs)
    return origin, refs


def is_path_b_empirically_grounded(
    concept_uri: str,
    concept: CanonicalBusinessConcept | None,
    delta: OntologyDelta,
) -> bool:
    """Evaluate Path B empirical grounding (ONT-EVO-004/005)."""
    origin, refs = _collect_concept_evidence_and_origin(concept_uri, concept, delta)
    if origin is not None and origin in PATH_B_GROUNDING_ORIGINS:
        return any(_is_verifiable_evidence_ref(ref, origin) for ref in refs)
    if origin is not None and origin != "FIBO_GROUNDED":
        return False
    for ref in refs:
        ev_type = str(getattr(ref, "evidence_type", "") or "").strip()
        if ev_type == "column_profile" and _is_verifiable_evidence_ref(ref, "STRUCTURED_SCHEMA"):
            return True
        if ev_type == "document_span" and _is_verifiable_evidence_ref(ref, "DOCUMENT_OBSERVED"):
            return True
    return False


__all__ = [
    "DEFAULT_GENESIS_SHA256",
    "PATH_B_GROUNDING_ORIGINS",
    "_HEX64_RE",
    "ConcurrentModificationError",
    "ContractViolationError",
    "GovernanceValidationResult",
    "HierarchicalCycleError",
    "InvalidGroundingWaiverError",
    "OntologyContractViolationError",
    "OntologyGovernanceError",
    "ProvisionalTokenError",
    "SemVerCalculusError",
    "StructuralValidationResult",
    "UngroundedConceptError",
    "_is_concept_deployed",
    "_resolve_base_relationships",
    "is_concept_mutated",
    "is_path_b_empirically_grounded",
]
