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
    AddConceptOp,
    AddExternalMappingOp,
    AddGovernanceConstraintOp,
    AddKeyAuthorityPatternOp,
    AddReferenceEnumerationOp,
    AddRelationshipOp,
    AddSynonymOp,
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
from opendv_contracts.errors import OpenDVError
from opendv_contracts.spi.deployment import DeploymentStateProvider
from opendv_contracts.spi.grounding import GroundingVerifier
from opendv_contracts.spi.waiver import WaiverVerifier
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256

# ---------------------------------------------------------------------------
# Typed Governance Exceptions (HTTP status & CLI exit code mapped)
# ---------------------------------------------------------------------------


class OntologyGovernanceError(OpenDVError, ValueError):
    """Base exception for all ontology governance and evolution guard errors."""

    error_code: str = "ONT_EVO_GOVERNANCE_ERROR"
    status_code: int = 422
    exit_code: int = 4

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        status_code: int | None = None,
        exit_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if error_code is not None:
            self.error_code = error_code
        self.status_code = status_code if status_code is not None else self.__class__.status_code
        self.http_status = self.status_code
        self.exit_code = exit_code if exit_code is not None else self.__class__.exit_code
        self.details = details or {}


class ContractViolationError(OntologyGovernanceError):
    """Raised when an operation violates deployed contract immutability (ONT-EVO-001)."""

    error_code = "ONT_EVO_CONTRACT_VIOLATION"
    status_code = 422
    exit_code = 4


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

    def validate_hub_bijection(self, ontology: GraphAOntology) -> None:
        """Validate 1:1 Hub bijection invariant across concepts.

        Raises:
            ContractViolationError: If two distinct concepts bind to the same target_hub.
        """
        hub_to_concept: dict[str, str] = {}
        for c in ontology.canonical_business_concepts:
            if c.target_hub:
                if c.target_hub in hub_to_concept:
                    prev = hub_to_concept[c.target_hub]
                    if prev != c.concept_uri:
                        raise ContractViolationError(
                            f"ONT-EVO-003: Anti-silo violation: Hub '{c.target_hub}' is already "
                            f"bound to concept '{prev}'; cannot bind distinct concept "
                            f"'{c.concept_uri}'."
                        )
                hub_to_concept[c.target_hub] = c.concept_uri

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
            prov_tokens.extend(
                self.scan_provisional_tokens(delta.model_dump(mode="json", exclude_none=True))
            )

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
    ) -> None:
        self.deployment_provider = deployment_provider
        self.grounding_verifier = grounding_verifier
        self.waiver_verifier = waiver_verifier

    def validate_occ(
        self,
        delta: OntologyDelta,
        session_head_sha256: str | None = None,
        if_match: str | None = None,
    ) -> None:
        """Enforce ONT-EVO-003: Pure Optimistic Concurrency Control preconditions."""
        delta_base_sha = delta.base_sha256 or delta.parent_sha256 or ""

        if (
            delta.base_version != "0.0.0"
            and session_head_sha256 is not None
            and session_head_sha256.lower() != delta_base_sha.lower()
        ):
            raise ConcurrentModificationError(
                f"ONT_EVO_CONCURRENT_MODIFICATION: session_head_sha256 "
                f"'{session_head_sha256}' does not match delta base_sha256 '{delta_base_sha}'."
            )

        if if_match is not None:
            cleaned_if_match = if_match.strip().strip('"').strip("'")
            if cleaned_if_match.lower() != delta_base_sha.lower():
                raise ConcurrentModificationError(
                    f"ONT_EVO_CONCURRENT_MODIFICATION: If-Match header '{cleaned_if_match}' "
                    f"does not match delta base_sha256 '{delta_base_sha}'."
                )
            if (
                session_head_sha256 is not None
                and cleaned_if_match.lower() != session_head_sha256.lower()
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
        base_rels = {r.relationship_uri: r for r in base.canonical_relationships}

        for op in delta.operations:
            if isinstance(op, RemoveConceptOp):
                if op.concept_uri in deployed_concepts or op.concept_uri in deployed_entities:
                    raise ContractViolationError(
                        f"ONT_EVO_CONTRACT_VIOLATION: Cannot remove deployed concept "
                        f"'{op.concept_uri}'."
                    )
                if op.concept_uri in base_concepts:
                    hub = base_concepts[op.concept_uri].target_hub
                    if hub and hub in deployed_tables:
                        raise ContractViolationError(
                            f"ONT_EVO_CONTRACT_VIOLATION: Cannot remove concept '{op.concept_uri}' "
                            f"bound to deployed hub table '{hub}'."
                        )

            elif isinstance(op, UpdateConceptUrnOp):
                if (
                    op.old_concept_uri in deployed_concepts
                    or op.old_concept_uri in deployed_entities
                ):
                    raise ContractViolationError(
                        f"ONT_EVO_CONTRACT_VIOLATION: Cannot rename deployed concept "
                        f"'{op.old_concept_uri}'."
                    )

            elif isinstance(op, RemoveRelationshipOp):
                rel_id = op.relationship_uri or op.relationship_name
                if rel_id in deployed_entities:
                    raise ContractViolationError(
                        f"ONT_EVO_CONTRACT_VIOLATION: Cannot remove deployed relationship "
                        f"'{rel_id}'."
                    )
                if op.relationship_uri in base_rels:
                    link = base_rels[op.relationship_uri].target_link
                    if link and link in deployed_tables:
                        raise ContractViolationError(
                            f"ONT_EVO_CONTRACT_VIOLATION: Cannot remove relationship "
                            f"'{op.relationship_uri}' bound to deployed link table '{link}'."
                        )

            elif isinstance(op, RemoveAttributeOp):
                if op.concept_uri in deployed_concepts or op.concept_uri in deployed_entities:
                    raise ContractViolationError(
                        f"ONT_EVO_CONTRACT_VIOLATION: Cannot remove attribute "
                        f"'{op.attribute_name}' from deployed concept '{op.concept_uri}'."
                    )

            elif isinstance(op, SetParentConceptOp):
                if (
                    op.concept_uri in deployed_concepts or op.concept_uri in deployed_entities
                ) and op.concept_uri in base_concepts:
                    existing_parent = base_concepts[op.concept_uri].parent_concept_uri
                    if existing_parent is not None and existing_parent != op.parent_concept_uri:
                        raise ContractViolationError(
                            f"ONT_EVO_CONTRACT_VIOLATION: Destructive reparenting of deployed "
                            f"concept '{op.concept_uri}' from '{existing_parent}' to "
                            f"'{op.parent_concept_uri}' is prohibited."
                        )

            elif isinstance(op, SetConceptElmArchetypeOp):
                if (
                    op.concept_uri in deployed_concepts or op.concept_uri in deployed_entities
                ) and op.concept_uri in base_concepts:
                    existing_arch = base_concepts[op.concept_uri].elm_archetype
                    if existing_arch is not None and existing_arch != op.elm_archetype:
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
        touched: set[str] = set()
        for op in delta.operations:
            if isinstance(op, AddConceptOp):
                touched.add(op.concept_uri)
            elif isinstance(op, UpdateConceptUrnOp):
                touched.add(op.new_concept_uri)
                touched.add(op.old_concept_uri)
            elif isinstance(op, SetParentConceptOp):
                touched.add(op.concept_uri)
            elif isinstance(op, (RefineDefinitionOp, ReviseDescriptionOp)):
                target_u = op.concept_uri or op.target_uri
                if target_u:
                    touched.add(target_u)
            elif isinstance(
                op,
                (
                    AddSynonymOp,
                    AddExternalMappingOp,
                    RescoreExternalMappingOp,
                    RepinExternalMappingOp,
                    RemoveExternalMappingOp,
                ),
            ):
                touched.add(op.concept_uri)

        target_concepts = {c.concept_uri: c for c in target.canonical_business_concepts}
        waiver_list = waivers or []
        grandfathered = set(grandfathered_concept_uris or ())

        for concept_uri in sorted(touched):
            if concept_uri not in target_concepts:
                continue

            # Grandfathered concepts void exemption if touched
            if concept_uri in grandfathered:
                # Exemption voided upon mutation
                pass

            c = target_concepts[concept_uri]

            # Path A: Reference grounding
            is_grounded = False
            candidates = c.external_mappings or []
            if self.grounding_verifier is not None:
                res = self.grounding_verifier.verify_grounding(concept_uri, candidates=candidates)
                if res.is_grounded and res.score >= 0.6500:
                    is_grounded = True
            else:
                for m in candidates:
                    pred = (m.predicate or "").lower()
                    if (
                        "broadmatch" not in pred
                        and m.confidence is not None
                        and m.confidence >= 0.6500
                    ):
                        is_grounded = True
                        break

            # Path B: Administrative Waiver
            if not is_grounded:
                for w in waiver_list:
                    if concept_uri in w.target_concept_uris:
                        if not w.rationale or len(w.rationale.strip()) < 10:
                            raise InvalidGroundingWaiverError(
                                "INVALID_GROUNDING_WAIVER: Waiver rationale must be at "
                                "least 10 characters."
                            )
                        if w.authorized_role not in ("admin", "steward"):
                            raise InvalidGroundingWaiverError(
                                "INVALID_GROUNDING_WAIVER: Waiver authorized_role must be "
                                "admin or steward."
                            )
                        if self.waiver_verifier is not None:
                            valid = self.waiver_verifier.verify_waiver(
                                w,
                                delta_sha256=delta.canonical_sha256(),
                                base_sha256=delta.base_sha256,
                            )
                            if not valid:
                                raise InvalidGroundingWaiverError(
                                    "INVALID_GROUNDING_WAIVER: Cryptographic waiver "
                                    "verification failed."
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
        waivers: list[StewardWaiverRecord] | None = None,
        grandfathered_concept_uris: set[str] | frozenset[str] | None = None,
    ) -> GovernanceValidationResult:
        """Run complete Phase 2 governance validation.

        Raises typed exceptions on violations or returns GovernanceValidationResult.
        """
        self.validate_occ(delta, session_head_sha256=session_head_sha256, if_match=if_match)
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

    # 1. Provisional URI scan
    validator = StructuralValidator()
    prov_uris = validator.scan_provisional_tokens(delta.model_dump(mode="json", exclude_none=True))
    for prov in sorted(set(prov_uris)):
        violations.append(
            f"ONT-EVO-002: Provisional URI '{prov}' is strictly forbidden in "
            "accepted ontology deltas."
        )

    # 2. Deployed contract checks
    deployed_hubs, deployed_links, deployed_refs = _extract_deployed_contract_mappings(
        deployed_contract
    )

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
    "ConcurrentModificationError",
    "ContractViolationError",
    "GovernanceValidationResult",
    "GovernanceValidator",
    "HierarchicalCycleError",
    "InvalidGroundingWaiverError",
    "OntologyGovernanceError",
    "ProvisionalTokenError",
    "SemVerCalculusError",
    "StructuralValidationResult",
    "StructuralValidator",
    "UngroundedConceptError",
    "validate_ontology_delta",
]
