"""Deterministic ontology delta validation and evolution lock (ONT-EVO-001..003).

Provides:
- Phase 1: Pure in-memory StructuralValidator (acyclicity, provisional token leak, Hub bijection).
- Phase 2: GovernanceValidator (ONT-EVO-001 deployed contract immutability, ONT-EVO-002 positive
  reference grounding with administrative waivers, ONT-EVO-003 pure OCC precondition verification).
- Backward-compatible validate_ontology_delta.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from opendv_contracts.dtos.ontology import (
    ALLOWED_PATH_A_PREDICATES,
    CONCEPT_MUTATING_OP_TYPES,
    EXISTING_CONCEPT_MUTATING_OP_TYPES,
    PATH_A_GROUNDING_THRESHOLD,
    RELATIONSHIP_ENDPOINT_MUTATING_OP_TYPES,
    AddAttributeOp,
    AddConceptOp,
    AddExternalMappingOp,
    AddGovernanceConstraintOp,
    AddKeyAuthorityPatternOp,
    AddReferenceEnumerationOp,
    AddRelationshipOp,
    AddSourceHintOp,
    AddSynonymOp,
    AnnotateConceptMetadataOp,
    DeprecateConceptOp,
    DeprecateRelationshipOp,
    OntologyDelta,
    OntologyValidationResult,
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
from opendv_contracts.spi.deployment import DeploymentStateProvider
from opendv_contracts.spi.grounding import GroundingVerifier
from opendv_contracts.spi.waiver import WaiverVerifier
from opendv_core.genesis import GENESIS_DRAFT_SHA256
from opendv_core.models.graph_a import CanonicalBusinessConcept, GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.ontology.matching.verifier import (
    get_default_grounding_verifier,
    is_allowed_path_a_predicate,
)

DEFAULT_GENESIS_SHA256 = GENESIS_DRAFT_SHA256

# ---------------------------------------------------------------------------
# Typed Governance Exceptions (HTTP status & CLI exit code mapped)
# ---------------------------------------------------------------------------


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
    """Raised when an evolved concept lacks reference grounding and waiver (ONT-EVO-002)."""

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


# ---------------------------------------------------------------------------
# Validation Result DTOs
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Phase 1: Pure Structural Validator (In-Memory)
# ---------------------------------------------------------------------------


class StructuralValidator:
    """Phase 1: Pure in-memory primitive structural validator (zero filesystem/state access).

    Enforces:
    - Taxonomy DAG acyclicity via Tarjan's Strongly Connected Components (SCC) algorithm.
    - URI syntax & grammar validation, scanning for reserved provisional tokens.
    - 1:1 Hub bijection invariant (one Hub table per Canonical Business Concept).
    """

    PROVISIONAL_PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(r"(?i)urn:opendv:provisional:"),
        re.compile(r"(?i)urn:ontology:provisional:"),
    )

    def scan_provisional_tokens(self, val: Any) -> list[str]:
        """Recursively scan nested structures for forbidden provisional URI patterns."""
        found: list[str] = []
        if isinstance(val, str):
            for pat in self.PROVISIONAL_PATTERNS:
                if pat.search(val):
                    found.append(val)
                    break
        elif isinstance(val, Mapping):
            for k, v in val.items():
                found.extend(self.scan_provisional_tokens(k))
                found.extend(self.scan_provisional_tokens(v))
        elif isinstance(val, (list, tuple, set, frozenset)):
            for item in val:
                found.extend(self.scan_provisional_tokens(item))
        elif hasattr(val, "__dict__"):
            found.extend(self.scan_provisional_tokens(vars(val)))
        return found

    def validate_acyclicity(self, ontology: GraphAOntology) -> None:
        """Validate taxonomy DAG acyclicity via Tarjan's Strongly Connected Components (SCC).

        Builds directed adjacency where child concept points to its parent concept.
        Any strongly connected component of size > 1 or a self-referential loop indicates a cycle.

        Raises:
            HierarchicalCycleError: If any directed cycle or self-referential parent is detected.
        """
        adj: dict[str, list[str]] = {}
        for c in ontology.canonical_business_concepts:
            if c.concept_uri not in adj:
                adj[c.concept_uri] = []
            if c.parent_concept_uri:
                if c.parent_concept_uri == c.concept_uri:
                    raise HierarchicalCycleError(
                        f"HIERARCHICAL_CYCLE_DETECTED: Concept '{c.concept_uri}' "
                        "cannot be its own parent."
                    )
                adj[c.concept_uri].append(c.parent_concept_uri)

        # Tarjan's SCC algorithm
        index = 0
        indices: dict[str, int] = {}
        lowlink: dict[str, int] = {}
        stack: list[str] = []
        on_stack: set[str] = set()
        sccs: list[list[str]] = []

        def strongconnect(node: str) -> None:
            nonlocal index
            indices[node] = index
            lowlink[node] = index
            index += 1
            stack.append(node)
            on_stack.add(node)

            for neighbor in adj.get(node, []):
                if neighbor not in indices:
                    strongconnect(neighbor)
                    lowlink[node] = min(lowlink[node], lowlink[neighbor])
                elif neighbor in on_stack:
                    lowlink[node] = min(lowlink[node], indices[neighbor])

            if lowlink[node] == indices[node]:
                scc: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack.remove(w)
                    scc.append(w)
                    if w == node:
                        break
                sccs.append(scc)

        for node in list(adj.keys()):
            if node not in indices:
                strongconnect(node)

        for scc in sccs:
            if len(scc) > 1:
                cycle_str = " -> ".join([*scc, scc[0]])
                raise HierarchicalCycleError(
                    f"HIERARCHICAL_CYCLE_DETECTED: Directed cycle detected in taxonomy DAG: "
                    f"{cycle_str}"
                )
            if len(scc) == 1:
                node = scc[0]
                if node in adj.get(node, []):
                    raise HierarchicalCycleError(
                        f"HIERARCHICAL_CYCLE_DETECTED: Concept '{node}' cannot be its own parent."
                    )

    _LINK_PREFIXES: tuple[str, ...] = (
        "t_lnk_",
        "nh_lnk_",
        "x_lnk_",
        "lnk_",
        "sal_",
        "hal_",
    )

    def validate_hub_bijection(self, ontology: GraphAOntology) -> None:
        """Validate 1:1 Hub bijection invariant and physical table uniqueness/slug budgets.

        Raises:
            ContractViolationError: If two distinct concepts bind to the same target_hub, or if
                physical link/reference table names collide.
            OntologyGovernanceError: If any physical table base slug exceeds 55 characters.
        """
        hub_to_concept: dict[str, str] = {}
        seen_physical_tables: dict[str, str] = {}

        for c in ontology.canonical_business_concepts:
            if c.target_hub:
                hub_slug = c.target_hub.removeprefix("hub_")
                if not hub_slug or len(hub_slug) > 55 or len(c.target_hub) > 63:
                    raise OntologyGovernanceError(
                        f"INVALID_PHYSICAL_TABLE_NAME: Hub '{c.target_hub}' on concept "
                        f"'{c.concept_uri}' exceeds the 55-character base slug budget.",
                        error_code=ErrorCode.INVALID_PHYSICAL_TABLE_NAME,
                    )
                if c.target_hub in hub_to_concept:
                    prev = hub_to_concept[c.target_hub]
                    if prev != c.concept_uri:
                        raise ContractViolationError(
                            f"ONT-EVO-003: Anti-silo violation: Hub '{c.target_hub}' is already "
                            f"bound to concept '{prev}'; cannot bind distinct concept "
                            f"'{c.concept_uri}'.",
                            error_code=ErrorCode.HUB_BIJECTION_VIOLATION,
                        )
                hub_to_concept[c.target_hub] = c.concept_uri
                seen_physical_tables[c.target_hub] = c.concept_uri

        link_to_rel: dict[str, str] = {}
        for r in ontology.canonical_relationships:
            if r.target_link:
                link_slug = r.target_link
                for pfx in self._LINK_PREFIXES:
                    if link_slug.startswith(pfx):
                        link_slug = link_slug[len(pfx) :]
                        break
                if not link_slug or len(link_slug) > 55 or len(r.target_link) > 63:
                    raise OntologyGovernanceError(
                        f"INVALID_PHYSICAL_TABLE_NAME: Link '{r.target_link}' on relationship "
                        f"'{r.relationship_uri}' exceeds the 55-character base slug budget.",
                        error_code=ErrorCode.INVALID_PHYSICAL_TABLE_NAME,
                    )
                if (
                    r.target_link in link_to_rel
                    and link_to_rel[r.target_link] != r.relationship_uri
                ):
                    raise ContractViolationError(
                        f"PHYSICAL_TABLE_NAME_COLLISION: Link table '{r.target_link}' is already "
                        f"bound to relationship '{link_to_rel[r.target_link]}'; cannot bind "
                        f"'{r.relationship_uri}'.",
                        error_code=ErrorCode.PHYSICAL_TABLE_NAME_COLLISION,
                    )
                if r.target_link in seen_physical_tables:
                    raise ContractViolationError(
                        f"PHYSICAL_TABLE_NAME_COLLISION: Physical table '{r.target_link}' "
                        f"collides with '{seen_physical_tables[r.target_link]}'.",
                        error_code=ErrorCode.PHYSICAL_TABLE_NAME_COLLISION,
                    )
                link_to_rel[r.target_link] = r.relationship_uri
                seen_physical_tables[r.target_link] = r.relationship_uri

        ref_to_enum: dict[str, str] = {}
        for e in ontology.reference_enumerations:
            if e.target_reference_table:
                ref_slug = e.target_reference_table.removeprefix("ref_")
                if not ref_slug or len(ref_slug) > 55 or len(e.target_reference_table) > 63:
                    raise OntologyGovernanceError(
                        f"INVALID_PHYSICAL_TABLE_NAME: Reference table "
                        f"'{e.target_reference_table}' on enumeration '{e.enum_uri}' exceeds "
                        "the 55-character base slug budget.",
                        error_code=ErrorCode.INVALID_PHYSICAL_TABLE_NAME,
                    )
                if (
                    e.target_reference_table in ref_to_enum
                    and ref_to_enum[e.target_reference_table] != e.enum_uri
                ):
                    raise ContractViolationError(
                        f"PHYSICAL_TABLE_NAME_COLLISION: Reference table "
                        f"'{e.target_reference_table}' is already bound to enumeration "
                        f"'{ref_to_enum[e.target_reference_table]}'; cannot bind '{e.enum_uri}'.",
                        error_code=ErrorCode.PHYSICAL_TABLE_NAME_COLLISION,
                    )
                if e.target_reference_table in seen_physical_tables:
                    raise ContractViolationError(
                        f"PHYSICAL_TABLE_NAME_COLLISION: Physical table "
                        f"'{e.target_reference_table}' collides with "
                        f"'{seen_physical_tables[e.target_reference_table]}'.",
                        error_code=ErrorCode.PHYSICAL_TABLE_NAME_COLLISION,
                    )
                ref_to_enum[e.target_reference_table] = e.enum_uri
                seen_physical_tables[e.target_reference_table] = e.enum_uri

    def validate(
        self,
        ontology: GraphAOntology,
        delta: OntologyDelta | None = None,
    ) -> StructuralValidationResult:
        """Run Phase 1 structural validation.

        Raises typed exceptions on violations or returns StructuralValidationResult.
        """
        ont_dict = ontology.model_dump(mode="json", exclude_none=True)
        prov_tokens = self.scan_provisional_tokens(ont_dict)
        if delta is not None:
            delta_dump = delta.model_dump(mode="json", exclude_none=True)
            # Exclude segregated holding-area staged_candidates in delta.metadata from commit scan
            if isinstance(delta_dump.get("metadata"), dict) and (
                "staged_candidates" in delta_dump["metadata"]
            ):
                meta_copy = dict(delta_dump["metadata"])
                meta_copy.pop("staged_candidates", None)
                delta_dump["metadata"] = meta_copy
            prov_tokens.extend(self.scan_provisional_tokens(delta_dump))

        if prov_tokens:
            unique_prov = sorted(set(prov_tokens))
            raise ProvisionalTokenError(
                f"ONT_EVO_GROUNDING_VIOLATION: Reserved provisional URI detected: {unique_prov}"
            )

        self.validate_acyclicity(ontology)
        self.validate_hub_bijection(ontology)

        diagnostics = [
            f"Structural validation passed: {len(ontology.canonical_business_concepts)} concepts, "
            f"{len(ontology.canonical_relationships)} relationships."
        ]
        return StructuralValidationResult(valid=True, diagnostics=diagnostics)


# ---------------------------------------------------------------------------
# Concept Mutation & Deployment Predicates (ONT-EVO-001 / ONT-EVO-002)
# ---------------------------------------------------------------------------

_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")


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
    """Return True iff `delta` mutates `concept_uri` under ONT-EVO-002 rules.

    Covers all 18 `CONCEPT_MUTATING_OP_TYPES` unconditionally plus endpoint concepts in
    `base` for `RELATIONSHIP_ENDPOINT_MUTATING_OP_TYPES` (`remove_relationship`,
    `deprecate_relationship`). Note:
    - `AddRelationshipOp` does NOT void grandfathered exemption of untouched endpoints.
    - All 18 `CONCEPT_MUTATING_OP_TYPES` (including `RescoreExternalMappingOp` at any
      confidence) trigger `GovernanceValidator.validate_grounding()`.
    """
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


# ---------------------------------------------------------------------------
# Phase 2: Governance Validator (ONT-EVO Guards)
# ---------------------------------------------------------------------------


class GovernanceValidator:
    """Phase 2: Governance guard validator enforcing ONT-EVO-001..003.

    Enforces:
    - ONT-EVO-001 (Deployed Contract Immutability): Blocks destructive deletions and reparenting
      on deployed Data Vault physical tables using DeploymentStateProvider SPI.
    - ONT-EVO-002 (Positive Reference Grounding): Evaluates target ontology for every concept
      touched by modifying operations; requires verified BM25 score >= 0.6500 or waiver.
    - ONT-EVO-003 (Pure OCC Precondition): Asserts session head SHA-256 == delta base SHA-256 and
      validates If-Match header against base digest.
    """

    def __init__(
        self,
        deployment_provider: DeploymentStateProvider | None = None,
        grounding_verifier: GroundingVerifier | None = None,
        waiver_verifier: WaiverVerifier | None = None,
        *,
        strict_verifier_required: bool = True,
        allow_unverified_waivers: bool = False,
    ) -> None:
        self.deployment_provider = deployment_provider
        self.grounding_verifier = (
            grounding_verifier
            if grounding_verifier is not None
            else (get_default_grounding_verifier() if strict_verifier_required else None)
        )
        self.waiver_verifier = waiver_verifier
        self.strict_verifier_required = strict_verifier_required
        self.allow_unverified_waivers = allow_unverified_waivers

    @staticmethod
    def _parse_if_match_digest(if_match: str) -> str:
        """Validate RFC 9110 strong 64-hex ETag or raw 64-hex digest; reject weak/wildcard/lists."""
        raw = if_match.strip()
        if not raw or raw == "*" or "," in raw or raw.startswith(("W/", "w/")):
            raise OpenDVError(
                f"INVALID_PRECONDITION_HEADER: Malformed or weak If-Match header '{if_match}'.",
                error_code=ErrorCode.INVALID_PRECONDITION_HEADER,
            )
        if raw.startswith('"') or raw.endswith('"'):
            if not (raw.startswith('"') and raw.endswith('"') and len(raw) == 66):
                raise OpenDVError(
                    f"INVALID_PRECONDITION_HEADER: Malformed ETag quotes in If-Match '{if_match}'.",
                    error_code=ErrorCode.INVALID_PRECONDITION_HEADER,
                )
            candidate = raw[1:-1]
        else:
            candidate = raw

        if not _HEX64_RE.match(candidate):
            raise OpenDVError(
                f"INVALID_PRECONDITION_HEADER: If-Match '{if_match}' is not a 64-hex SHA-256.",
                error_code=ErrorCode.INVALID_PRECONDITION_HEADER,
            )
        return candidate.lower()

    def validate_occ(
        self,
        delta: OntologyDelta,
        session_head_sha256: str | None = None,
        if_match: str | None = None,
        actual_base_version: str | None = None,
    ) -> None:
        """Enforce ONT-EVO-003: Pure Optimistic Concurrency Control preconditions."""
        delta_base_sha = (delta.base_sha256 or delta.parent_sha256 or "").strip()

        cleaned_if_match: str | None = None
        if if_match is not None:
            cleaned_if_match = self._parse_if_match_digest(if_match)

        clean_delta_ver = (delta.base_version or "").strip().lstrip("v")
        clean_actual_ver = (
            actual_base_version.strip().lstrip("v") if actual_base_version is not None else None
        )

        if clean_actual_ver is not None and clean_delta_ver != clean_actual_ver:
            raise ConcurrentModificationError(
                f"ONT_EVO_CONCURRENT_MODIFICATION: delta base_version '{delta.base_version}' "
                f"does not match actual base version '{actual_base_version}'."
            )

        if session_head_sha256 is not None:
            clean_head_sha = session_head_sha256.strip().lower()
            if delta_base_sha:
                if clean_head_sha != delta_base_sha.lower():
                    raise ConcurrentModificationError(
                        f"ONT_EVO_CONCURRENT_MODIFICATION: session_head_sha256 "
                        f"'{session_head_sha256}' does not match delta base_sha256 "
                        f"'{delta_base_sha}'."
                    )
            else:
                allow_empty_genesis_sha = (clean_actual_ver == "0.0.0") or (
                    clean_actual_ver is None
                    and clean_delta_ver == "0.0.0"
                    and (
                        cleaned_if_match is not None
                        or clean_head_sha == DEFAULT_GENESIS_SHA256.lower()
                    )
                )
                if not allow_empty_genesis_sha:
                    raise ConcurrentModificationError(
                        f"ONT_EVO_CONCURRENT_MODIFICATION: empty delta base_sha256 is not "
                        f"permitted against session_head_sha256 '{session_head_sha256}'."
                    )

        if cleaned_if_match is not None:
            if (
                clean_delta_ver != "0.0.0"
                or (clean_actual_ver is not None and clean_actual_ver != "0.0.0")
                or bool(delta_base_sha)
            ) and cleaned_if_match != delta_base_sha.lower():
                raise ConcurrentModificationError(
                    f"ONT_EVO_CONCURRENT_MODIFICATION: If-Match header '{cleaned_if_match}' "
                    f"does not match delta base_sha256 '{delta_base_sha}'."
                )
            if (
                session_head_sha256 is not None
                and cleaned_if_match != session_head_sha256.strip().lower()
            ):
                raise ConcurrentModificationError(
                    f"ONT_EVO_CONCURRENT_MODIFICATION: If-Match header '{cleaned_if_match}' "
                    f"does not match session head SHA-256 '{session_head_sha256}'."
                )

    def validate_deployed_contracts(
        self,
        base: GraphAOntology,
        delta: OntologyDelta,
    ) -> None:
        """Enforce ONT-EVO-001: Deployed Contract Immutability."""
        if self.deployment_provider is None:
            return

        try:
            deployed_concepts = set(self.deployment_provider.get_deployed_concepts())
            deployed_tables = set(self.deployment_provider.get_deployed_tables())
            deployed_entities = set(self.deployment_provider.get_deployed_entities())
        except Exception as err:
            # Fails closed if deployment state provider raises
            raise ContractViolationError(
                f"ONT_EVO_CONTRACT_VIOLATION: DeploymentStateProvider error (fails closed): {err}"
            ) from err

        base_concepts = {c.concept_uri: c for c in base.canonical_business_concepts}

        for op in delta.operations:
            if isinstance(op, RemoveConceptOp):
                base_c = base_concepts.get(op.concept_uri)
                if _is_concept_deployed(
                    base_c,
                    op.concept_uri,
                    deployed_concepts,
                    deployed_entities,
                    deployed_tables,
                ):
                    hub = base_c.target_hub if base_c else None
                    if hub and hub in deployed_tables:
                        raise ContractViolationError(
                            f"ONT_EVO_CONTRACT_VIOLATION: Cannot remove concept '{op.concept_uri}' "
                            f"bound to deployed hub table '{hub}'."
                        )
                    raise ContractViolationError(
                        f"ONT_EVO_CONTRACT_VIOLATION: Cannot remove deployed concept "
                        f"'{op.concept_uri}'."
                    )

            elif isinstance(op, UpdateConceptUrnOp):
                base_c = base_concepts.get(op.old_concept_uri)
                if _is_concept_deployed(
                    base_c,
                    op.old_concept_uri,
                    deployed_concepts,
                    deployed_entities,
                    deployed_tables,
                ):
                    raise ContractViolationError(
                        f"ONT_EVO_CONTRACT_VIOLATION: Cannot rename deployed concept "
                        f"'{op.old_concept_uri}'."
                    )

            elif isinstance(op, RemoveRelationshipOp):
                rel_id = op.relationship_uri or op.relationship_name
                if (op.relationship_uri and op.relationship_uri in deployed_entities) or (
                    op.relationship_name and op.relationship_name in deployed_entities
                ):
                    raise ContractViolationError(
                        f"ONT_EVO_CONTRACT_VIOLATION: Cannot remove deployed relationship "
                        f"'{rel_id}'."
                    )
                for r in _resolve_base_relationships(
                    base, op.relationship_uri, op.relationship_name
                ):
                    if r.relationship_uri in deployed_entities or (
                        r.relationship_name and r.relationship_name in deployed_entities
                    ):
                        raise ContractViolationError(
                            f"ONT_EVO_CONTRACT_VIOLATION: Cannot remove deployed relationship "
                            f"'{r.relationship_uri}'."
                        )
                    if r.target_link and r.target_link in deployed_tables:
                        raise ContractViolationError(
                            f"ONT_EVO_CONTRACT_VIOLATION: Cannot remove relationship "
                            f"'{r.relationship_uri}' bound to deployed link table "
                            f"'{r.target_link}'."
                        )

            elif isinstance(op, RemoveAttributeOp):
                base_c = base_concepts.get(op.concept_uri)
                if _is_concept_deployed(
                    base_c,
                    op.concept_uri,
                    deployed_concepts,
                    deployed_entities,
                    deployed_tables,
                ):
                    raise ContractViolationError(
                        f"ONT_EVO_CONTRACT_VIOLATION: Cannot remove attribute "
                        f"'{op.attribute_name}' from deployed concept '{op.concept_uri}'."
                    )

            elif isinstance(op, AddAttributeOp):
                if not op.is_optional and op.concept_uri in base_concepts:
                    base_c = base_concepts[op.concept_uri]
                    if _is_concept_deployed(
                        base_c,
                        op.concept_uri,
                        deployed_concepts,
                        deployed_entities,
                        deployed_tables,
                    ):
                        raise ContractViolationError(
                            f"ONT_EVO_CONTRACT_VIOLATION: Cannot add mandatory attribute "
                            f"'{op.attribute_name}' (is_optional=False) to deployed concept "
                            f"'{op.concept_uri}'."
                        )

            elif isinstance(op, SetParentConceptOp):
                if op.concept_uri in base_concepts:
                    base_c = base_concepts[op.concept_uri]
                    existing_parent = base_c.parent_concept_uri
                    if (
                        existing_parent is not None
                        and existing_parent != op.parent_concept_uri
                        and _is_concept_deployed(
                            base_c,
                            op.concept_uri,
                            deployed_concepts,
                            deployed_entities,
                            deployed_tables,
                        )
                    ):
                        raise ContractViolationError(
                            f"ONT_EVO_CONTRACT_VIOLATION: Destructive reparenting of deployed "
                            f"concept '{op.concept_uri}' from '{existing_parent}' to "
                            f"'{op.parent_concept_uri}' is prohibited."
                        )

            elif isinstance(op, SetConceptElmArchetypeOp):
                if op.concept_uri in base_concepts:
                    base_c = base_concepts[op.concept_uri]
                    existing_arch = base_c.elm_archetype
                    if (
                        existing_arch is not None
                        and existing_arch != op.elm_archetype
                        and _is_concept_deployed(
                            base_c,
                            op.concept_uri,
                            deployed_concepts,
                            deployed_entities,
                            deployed_tables,
                        )
                    ):
                        raise ContractViolationError(
                            f"ONT_EVO_CONTRACT_VIOLATION: Destructive archetype change on "
                            f"deployed concept '{op.concept_uri}' from '{existing_arch}' to "
                            f"'{op.elm_archetype}' is prohibited."
                        )

    def validate_grounding(
        self,
        base: GraphAOntology,
        delta: OntologyDelta,
        target: GraphAOntology,
        waivers: list[StewardWaiverRecord] | None = None,
        grandfathered_concept_uris: set[str] | frozenset[str] | None = None,
    ) -> None:
        """Enforce ONT-EVO-002: Positive Reference Grounding on touched concepts."""
        rename_map: dict[str, str] = {}
        for op in delta.operations:
            if isinstance(op, UpdateConceptUrnOp):
                rename_map[op.old_concept_uri] = op.new_concept_uri

        all_candidate_uris = {
            *(c.concept_uri for c in base.canonical_business_concepts),
            *(c.concept_uri for c in target.canonical_business_concepts),
            *rename_map.keys(),
            *rename_map.values(),
        }

        touched: set[str] = set()
        for u in all_candidate_uris:
            if is_concept_mutated(base, delta, u):
                touched.add(u)
                # Propagate mutation across URN renames so target concept is verified
                curr = u
                visited_renames: set[str] = set()
                while curr in rename_map and curr not in visited_renames:
                    visited_renames.add(curr)
                    curr = rename_map[curr]
                    touched.add(curr)

        target_concepts = {c.concept_uri: c for c in target.canonical_business_concepts}
        waiver_list = waivers or []
        # Note: any concept in `grandfathered_concept_uris` that is NOT in `touched` retains its
        # grandfathered exemption; any concept in `touched` has its grandfathered status voided.
        _ = grandfathered_concept_uris

        verifier: GroundingVerifier | None = self.grounding_verifier
        if verifier is None and self.strict_verifier_required and touched:
            verifier = get_default_grounding_verifier()

        for concept_uri in sorted(touched):
            if concept_uri not in target_concepts:
                continue

            c = target_concepts[concept_uri]

            # Path A: Verified Reference Grounding (strictly scheme == "FIBO")
            is_grounded = False
            fibo_candidates = [
                m
                for m in (c.external_mappings or [])
                if (getattr(m, "scheme", "") or "").strip().upper() == "FIBO"
                and is_allowed_path_a_predicate(getattr(m, "predicate", None))
                and (
                    getattr(m, "confidence", None) is None
                    or float(m.confidence) >= PATH_A_GROUNDING_THRESHOLD
                )
            ]
            if verifier is not None:
                if fibo_candidates:
                    res = verifier.verify_grounding(
                        concept_uri,
                        candidates=fibo_candidates,
                        concept_name=c.concept_name,
                        synonyms=tuple(c.synonyms or ()),
                    )
                    if res.is_grounded and res.score >= PATH_A_GROUNDING_THRESHOLD:
                        is_grounded = True
            else:
                # Explicit unit-test fallback (`strict_verifier_required=False`):
                # still enforce FIBO scheme, Path A predicate allow-list & 0.6500 threshold.
                for m in fibo_candidates:
                    if (
                        bool(m.target_uri and m.target_uri.strip())
                        and m.confidence is not None
                        and m.confidence >= PATH_A_GROUNDING_THRESHOLD
                    ):
                        is_grounded = True
                        break

            # Path B: Administrative Waiver (strictly admin role + verified token)
            if not is_grounded:
                for w in waiver_list:
                    if concept_uri in w.target_concept_uris:
                        if not w.rationale or len(w.rationale.strip()) < 10:
                            raise InvalidGroundingWaiverError(
                                "INVALID_GROUNDING_WAIVER: Waiver rationale must be at "
                                "least 10 characters."
                            )
                        if w.authorized_role != "admin":
                            raise InvalidGroundingWaiverError(
                                "INVALID_GROUNDING_WAIVER: Waiver authorized_role must be 'admin'."
                            )
                        sig = getattr(w, "signature_or_token", None)
                        if self.waiver_verifier is not None:
                            if sig is not None and not str(sig).strip():
                                raise InvalidGroundingWaiverError(
                                    "INVALID_GROUNDING_WAIVER: Waiver signature_or_token "
                                    "must be non-empty."
                                )
                            valid = self.waiver_verifier.verify_waiver(
                                w,
                                delta_sha256=delta.canonical_sha256(),
                                base_sha256=delta.base_sha256 or compute_ontology_sha256(base),
                            )
                            if not valid:
                                raise InvalidGroundingWaiverError(
                                    "INVALID_GROUNDING_WAIVER: Cryptographic waiver "
                                    "verification failed."
                                )
                        elif not self.allow_unverified_waivers:
                            raise InvalidGroundingWaiverError(
                                "INVALID_GROUNDING_WAIVER: WaiverVerifier is required to "
                                "authorize administrative waivers in governed mode."
                            )
                        is_grounded = True
                        break

            if not is_grounded:
                raise UngroundedConceptError(
                    f"ONT_EVO_UNGROUNDED_CONCEPT: Touched concept '{concept_uri}' lacks verified "
                    "positive reference grounding (BM25 >= 0.6500) and lacks an authorized "
                    "administrative waiver."
                )

    def validate(
        self,
        base: GraphAOntology,
        delta: OntologyDelta,
        target: GraphAOntology,
        *,
        session_head_sha256: str | None = None,
        if_match: str | None = None,
        actual_base_version: str | None = None,
        waivers: list[StewardWaiverRecord] | None = None,
        grandfathered_concept_uris: set[str] | frozenset[str] | None = None,
    ) -> GovernanceValidationResult:
        """Run complete Phase 2 governance validation.

        Raises typed exceptions on violations or returns GovernanceValidationResult.
        """
        self.validate_occ(
            delta,
            session_head_sha256=session_head_sha256,
            if_match=if_match,
            actual_base_version=actual_base_version,
        )
        self.validate_deployed_contracts(base, delta)
        self.validate_grounding(
            base,
            delta,
            target,
            waivers=waivers,
            grandfathered_concept_uris=grandfathered_concept_uris,
        )
        return GovernanceValidationResult(valid=True, diagnostics=["Governance validation passed."])


# ---------------------------------------------------------------------------
# Backward Compatibility Helpers
# ---------------------------------------------------------------------------


class _ContractDeploymentProvider:
    """Adapter turning a v5 deployed contract object/dict into a DeploymentStateProvider."""

    def __init__(self, deployed_contract: Any) -> None:
        doc = (
            deployed_contract.model_dump(mode="json")
            if hasattr(deployed_contract, "model_dump")
            else (deployed_contract if isinstance(deployed_contract, dict) else {})
        )
        self._concepts: set[str] = set()
        self._entities: set[str] = set()
        self._tables: set[str] = set()

        for hub in doc.get("hubs", []) or []:
            if isinstance(hub, dict):
                name = hub.get("name") or hub.get("table_name")
                if isinstance(name, str) and name:
                    self._tables.add(name)
                uri = hub.get("ontology_concept_uri") or hub.get("concept_uri")
                if isinstance(uri, str) and uri:
                    self._concepts.add(uri)
                    self._entities.add(uri)

        for link in doc.get("links", []) or []:
            if isinstance(link, dict):
                name = link.get("name") or link.get("table_name")
                if isinstance(name, str) and name:
                    self._tables.add(name)
                uri = link.get("ontology_concept_uri") or link.get("relationship_uri")
                if isinstance(uri, str) and uri:
                    self._entities.add(uri)

        for sat in doc.get("satellites", []) or []:
            if isinstance(sat, dict):
                name = sat.get("name") or sat.get("table_name")
                if isinstance(name, str) and name:
                    self._tables.add(name)
                uri = sat.get("ontology_concept_uri") or sat.get("concept_uri")
                if isinstance(uri, str) and uri:
                    self._concepts.add(uri)
                    self._entities.add(uri)

        for ref in doc.get("reference_tables", []) or []:
            if isinstance(ref, dict):
                name = ref.get("name") or ref.get("table_name")
                if isinstance(name, str) and name:
                    self._tables.add(name)
                uri = ref.get("ontology_concept_uri") or ref.get("enum_uri")
                if isinstance(uri, str) and uri:
                    self._entities.add(uri)

    def get_deployed_concepts(self) -> set[str]:
        return set(self._concepts)

    def get_deployed_tables(self) -> set[str]:
        return set(self._tables)

    def get_deployed_entities(self) -> set[str]:
        return set(self._entities)


def build_deployment_provider_from_contract(
    deployed_contract: Any,
) -> DeploymentStateProvider | None:
    """Construct a DeploymentStateProvider from a legacy v5 deployed contract payload."""
    if deployed_contract is None:
        return None
    return _ContractDeploymentProvider(deployed_contract)  # type: ignore[return-value]


def _extract_deployed_contract_mappings(
    deployed_contract: Any,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Extract (concept_uri -> hub), (rel_uri -> link), (enum_uri -> ref_table)."""
    deployed_hubs: dict[str, str] = {}
    deployed_links: dict[str, str] = {}
    deployed_refs: dict[str, str] = {}

    if deployed_contract is None:
        return deployed_hubs, deployed_links, deployed_refs

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
        if isinstance(ref, dict) and "name" in ref:
            uri = ref.get("ontology_concept_uri") or ref.get("enum_uri")
            if uri:
                deployed_refs[uri] = ref["name"]

    return deployed_hubs, deployed_links, deployed_refs


def validate_ontology_delta(
    base: GraphAOntology,
    delta: OntologyDelta,
    deployed_contract: Any | None = None,
) -> OntologyValidationResult:
    """Deterministically validate an OntologyDelta against base Graph A and deployed contract."""
    violations: list[str] = []
    diagnostics: list[str] = []

    # 1. Provisional URI scan (excluding segregated holding-area metadata.staged_candidates)
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

    # 2. Deployed contract checks
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

    # 3. Base conflicts & anti-silo checks
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
                    existing_spec = None
                    tier_name = ""
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


__all__ = [
    "ALLOWED_PATH_A_PREDICATES",
    "DEFAULT_GENESIS_SHA256",
    "EXISTING_CONCEPT_MUTATING_OP_TYPES",
    "ConcurrentModificationError",
    "ContractViolationError",
    "GovernanceValidationResult",
    "GovernanceValidator",
    "HierarchicalCycleError",
    "InvalidGroundingWaiverError",
    "OntologyContractViolationError",
    "OntologyGovernanceError",
    "ProvisionalTokenError",
    "SemVerCalculusError",
    "StructuralValidationResult",
    "StructuralValidator",
    "UngroundedConceptError",
    "is_concept_mutated",
    "validate_ontology_delta",
]
