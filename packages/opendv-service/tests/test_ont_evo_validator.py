"""Unit and regression tests for Pure State Applier, SemVer Calculus & ONT-EVO Governance Guards.

Covers:
- Phase 1: Pure in-memory StructuralValidator (Tarjan SCC acyclicity, provisional tokens, Hub).
- Phase 2: GovernanceValidator (ONT-EVO-001 deployed contract immutability, ONT-EVO-002 positive
  grounding with administrative waivers, ONT-EVO-003 pure OCC precondition verification).
- 24-op SemVer calculus (genesis, additive minor, overwrite/deletion major, metadata patch).
- Pure state transition applier (immutability of base, cascading deletion cleanup, 24 ops).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

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
)
from opendv_contracts.spi.grounding import GroundingVerificationResult
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    ConceptAttribute,
    ExternalMapping,
    GraphAOntology,
    ReferenceEnumeration,
)
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.ontology import (
    ConcurrentModificationError,
    ContractViolationError,
    HierarchicalCycleError,
    InvalidGroundingWaiverError,
    ProvisionalTokenError,
    SemVerCalculusError,
    UngroundedConceptError,
    apply,
    apply_ontology_delta,
    compute_next_semver,
)
from opendv_service.ontology.validator import (
    GovernanceValidator,
    StructuralValidator,
)

# ---------------------------------------------------------------------------
# Test SPI Doubles (Mock-Free Pure Classes)
# ---------------------------------------------------------------------------


class StubDeploymentProvider:
    """Mock-free DeploymentStateProvider SPI double."""

    def __init__(
        self,
        deployed_concepts: set[str] | None = None,
        deployed_tables: set[str] | None = None,
        deployed_entities: set[str] | None = None,
        raise_on_access: bool = False,
    ) -> None:
        self._concepts = deployed_concepts or set()
        self._tables = deployed_tables or set()
        self._entities = deployed_entities or set()
        self._raise_on_access = raise_on_access

    def get_deployed_concepts(self) -> list[str]:
        if self._raise_on_access:
            raise RuntimeError("Database connection unreachable")
        return sorted(self._concepts)

    def get_deployed_tables(self) -> list[str]:
        if self._raise_on_access:
            raise RuntimeError("Database connection unreachable")
        return sorted(self._tables)

    def get_deployed_entities(self) -> list[str]:
        if self._raise_on_access:
            raise RuntimeError("Database connection unreachable")
        return sorted(self._entities)


class StubGroundingVerifier:
    """Mock-free GroundingVerifier SPI double."""

    def __init__(self, scores: dict[str, float] | None = None, default_score: float = 0.85) -> None:
        self._scores = scores or {}
        self._default_score = default_score

    def verify_grounding(
        self, concept_uri: str, candidates: list[Any], **kwargs: Any
    ) -> GroundingVerificationResult:
        score = self._scores.get(concept_uri, self._default_score)
        is_grounded = score >= 0.6500
        return GroundingVerificationResult(
            is_grounded=is_grounded,
            score=score,
            matched_uri="https://spec.edmcouncil.org/fibo/matched",
            diagnostics=["strategy: bm25_cross_encoder"],
        )


class StubWaiverVerifier:
    """Mock-free WaiverVerifier SPI double."""

    def __init__(self, valid_waivers: set[Any] | None = None) -> None:
        self._valid_waivers = valid_waivers

    def verify_waiver(
        self, waiver: StewardWaiverRecord, delta_sha256: str, base_sha256: str | None = None
    ) -> bool:
        if self._valid_waivers is not None:
            return waiver.waiver_id in self._valid_waivers
        return True


# ---------------------------------------------------------------------------
# Fixture Helpers
# ---------------------------------------------------------------------------


def make_sample_ontology() -> GraphAOntology:
    """Construct a clean, valid GraphAOntology baseline (v1.0.0)."""
    concept_a = CanonicalBusinessConcept(
        concept_uri="urn:ontology:entity:party",
        concept_name="Party",
        target_hub="hub_party",
        elm_archetype="CORE_BUSINESS_CONCEPT",
        attributes=[
            ConceptAttribute(attribute_name="party_id", data_type="string", is_optional=False),
            ConceptAttribute(attribute_name="name", data_type="string", is_optional=True),
        ],
        external_mappings=[
            ExternalMapping(
                scheme="fibo",
                target_uri="https://spec.edmcouncil.org/fibo/ontology/FND/AgentsAndPeople/Agents/Agent",
                predicate="exactMatch",
                confidence=0.92,
            )
        ],
    )
    concept_b = CanonicalBusinessConcept(
        concept_uri="urn:ontology:entity:account",
        concept_name="Account",
        target_hub="hub_account",
        elm_archetype="CORE_BUSINESS_CONCEPT",
        attributes=[
            ConceptAttribute(attribute_name="account_id", data_type="string", is_optional=False),
            ConceptAttribute(attribute_name="balance", data_type="decimal", is_optional=True),
        ],
        external_mappings=[
            ExternalMapping(
                scheme="fibo",
                target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/DebtAndEquities/Debt/CreditAccount",
                predicate="closeMatch",
                confidence=0.88,
            )
        ],
    )
    rel_ab = CanonicalRelationship(
        relationship_uri="urn:ontology:relationship:party_holds_account",
        relationship_name="party_holds_account",
        target_link="lnk_party_account",
        endpoints=["urn:ontology:entity:party", "urn:ontology:entity:account"],
        grain_semantics="One party may hold multiple accounts.",
    )
    enum_cur = ReferenceEnumeration(
        enum_uri="urn:ontology:enum:currency",
        target_reference_table="ref_currency",
        allowed_codes=["USD", "EUR", "GBP"],
    )
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="finance",
        enterprise_owner="finance-data-office",
        canonical_business_concepts=[concept_a, concept_b],
        canonical_relationships=[rel_ab],
        reference_enumerations=[enum_cur],
    )


# ---------------------------------------------------------------------------
# Phase 1: StructuralValidator Tests
# ---------------------------------------------------------------------------


def test_structural_validator_provisional_tokens_in_ontology_raises() -> None:
    ont = make_sample_ontology()
    ont.metadata = {"leak": "urn:opendv:provisional:draft_tag"}

    validator = StructuralValidator()
    with pytest.raises(ProvisionalTokenError) as exc_info:
        validator.validate(ont)
    assert exc_info.value.status_code == 422
    assert exc_info.value.exit_code == 4
    assert exc_info.value.error_code == "ONT_EVO_GROUNDING_VIOLATION"


def test_structural_validator_provisional_tokens_in_delta_raises() -> None:
    ont = make_sample_ontology()
    delta = OntologyDelta(
        delta_id="delta-1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        operations=[
            AddConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:provisional:entity:trade",
                concept_name="Trade",
                target_hub="hub_trade",
            )
        ],
    )

    validator = StructuralValidator()
    with pytest.raises(ProvisionalTokenError) as exc_info:
        validator.validate(ont, delta=delta)
    assert exc_info.value.error_code == "ONT_EVO_GROUNDING_VIOLATION"


def test_structural_validator_acyclicity_valid_hierarchy() -> None:
    ont = make_sample_ontology()
    c3 = CanonicalBusinessConcept(
        concept_uri="urn:ontology:entity:individual",
        concept_name="Individual",
        parent_concept_uri="urn:ontology:entity:party",
    )
    ont.canonical_business_concepts.append(c3)

    validator = StructuralValidator()
    res = validator.validate(ont)
    assert res.valid is True


def test_structural_validator_acyclicity_direct_cycle_raises() -> None:
    ont = make_sample_ontology()
    ont.canonical_business_concepts[0].parent_concept_uri = "urn:ontology:entity:account"
    ont.canonical_business_concepts[1].parent_concept_uri = "urn:ontology:entity:party"

    validator = StructuralValidator()
    with pytest.raises(HierarchicalCycleError) as exc_info:
        validator.validate(ont)
    assert exc_info.value.status_code == 422
    assert exc_info.value.exit_code == 4
    assert exc_info.value.error_code == "HIERARCHICAL_CYCLE_DETECTED"


def test_structural_validator_acyclicity_self_parent_raises() -> None:
    ont = make_sample_ontology()
    ont.canonical_business_concepts[0].parent_concept_uri = "urn:ontology:entity:party"

    validator = StructuralValidator()
    with pytest.raises(HierarchicalCycleError) as exc_info:
        validator.validate(ont)
    assert "cannot be its own parent" in exc_info.value.message


def test_structural_validator_hub_bijection_duplicate_raises() -> None:
    ont = make_sample_ontology()
    c3 = CanonicalBusinessConcept(
        concept_uri="urn:ontology:entity:duplicate_party",
        concept_name="DuplicateParty",
        target_hub="hub_party",  # same as Party!
    )
    ont.canonical_business_concepts.append(c3)

    validator = StructuralValidator()
    with pytest.raises(ContractViolationError) as exc_info:
        validator.validate(ont)
    assert "Anti-silo violation: Hub 'hub_party' is already bound" in exc_info.value.message


# ---------------------------------------------------------------------------
# Phase 2: GovernanceValidator Tests (ONT-EVO-001, 002, 003)
# ---------------------------------------------------------------------------


def test_ont_evo_001_remove_deployed_concept_raises() -> None:
    ont = make_sample_ontology()
    provider = StubDeploymentProvider(deployed_concepts={"urn:ontology:entity:party"})
    gov = GovernanceValidator(deployment_provider=provider)

    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        target_ontology_version="2.0.0",
        operations=[RemoveConceptOp(op_id="op-1", concept_uri="urn:ontology:entity:party")],
    )

    with pytest.raises(ContractViolationError) as exc_info:
        gov.validate_deployed_contracts(ont, delta)
    assert exc_info.value.error_code == "ONT_EVO_CONTRACT_VIOLATION"
    assert exc_info.value.status_code == 422
    assert exc_info.value.exit_code == 4


def test_ont_evo_001_update_urn_deployed_concept_raises() -> None:
    ont = make_sample_ontology()
    provider = StubDeploymentProvider(deployed_concepts={"urn:ontology:entity:party"})
    gov = GovernanceValidator(deployment_provider=provider)

    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        target_ontology_version="2.0.0",
        operations=[
            UpdateConceptUrnOp(
                op_id="op-1",
                old_concept_uri="urn:ontology:entity:party",
                new_concept_uri="urn:ontology:entity:legal_entity",
            )
        ],
    )

    with pytest.raises(ContractViolationError) as exc_info:
        gov.validate_deployed_contracts(ont, delta)
    assert exc_info.value.error_code == "ONT_EVO_CONTRACT_VIOLATION"


def test_ont_evo_001_remove_deployed_relationship_raises() -> None:
    ont = make_sample_ontology()
    provider = StubDeploymentProvider(
        deployed_entities={"urn:ontology:relationship:party_holds_account"}
    )
    gov = GovernanceValidator(deployment_provider=provider)

    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        target_ontology_version="2.0.0",
        operations=[
            RemoveRelationshipOp(
                op_id="op-1",
                relationship_uri="urn:ontology:relationship:party_holds_account",
            )
        ],
    )

    with pytest.raises(ContractViolationError) as exc_info:
        gov.validate_deployed_contracts(ont, delta)
    assert exc_info.value.error_code == "ONT_EVO_CONTRACT_VIOLATION"


def test_ont_evo_001_remove_attribute_deployed_concept_raises() -> None:
    ont = make_sample_ontology()
    provider = StubDeploymentProvider(deployed_concepts={"urn:ontology:entity:party"})
    gov = GovernanceValidator(deployment_provider=provider)

    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        target_ontology_version="2.0.0",
        operations=[
            RemoveAttributeOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:party",
                attribute_name="name",
            )
        ],
    )

    with pytest.raises(ContractViolationError) as exc_info:
        gov.validate_deployed_contracts(ont, delta)
    assert exc_info.value.error_code == "ONT_EVO_CONTRACT_VIOLATION"


def test_ont_evo_001_destructive_reparenting_deployed_concept_raises() -> None:
    ont = make_sample_ontology()
    ont.canonical_business_concepts[0].parent_concept_uri = "urn:ontology:entity:actor"
    provider = StubDeploymentProvider(deployed_concepts={"urn:ontology:entity:party"})
    gov = GovernanceValidator(deployment_provider=provider)

    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        target_ontology_version="2.0.0",
        operations=[
            SetParentConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:party",
                parent_concept_uri="urn:ontology:entity:new_parent",
            )
        ],
    )

    with pytest.raises(ContractViolationError) as exc_info:
        gov.validate_deployed_contracts(ont, delta)
    assert exc_info.value.error_code == "ONT_EVO_CONTRACT_VIOLATION"
    assert "Destructive reparenting of deployed concept" in exc_info.value.message


def test_ont_evo_001_destructive_archetype_change_deployed_concept_raises() -> None:
    ont = make_sample_ontology()
    provider = StubDeploymentProvider(deployed_concepts={"urn:ontology:entity:party"})
    gov = GovernanceValidator(deployment_provider=provider)

    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        target_ontology_version="2.0.0",
        operations=[
            SetConceptElmArchetypeOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:party",
                elm_archetype="TRANSACTIONAL_EVENT",
            )
        ],
    )

    with pytest.raises(ContractViolationError) as exc_info:
        gov.validate_deployed_contracts(ont, delta)
    assert exc_info.value.error_code == "ONT_EVO_CONTRACT_VIOLATION"
    assert "Destructive archetype change on deployed concept" in exc_info.value.message


def test_ont_evo_001_fails_closed_when_deployment_provider_errors() -> None:
    ont = make_sample_ontology()
    provider = StubDeploymentProvider(raise_on_access=True)
    gov = GovernanceValidator(deployment_provider=provider)

    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        operations=[
            AddConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:instrument",
                concept_name="Instrument",
            )
        ],
    )

    with pytest.raises(ContractViolationError) as exc_info:
        gov.validate_deployed_contracts(ont, delta)
    assert "fails closed" in exc_info.value.message


def test_ont_evo_002_grounded_concept_passes() -> None:
    base = make_sample_ontology()
    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        operations=[
            AddConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:collateral",
                concept_name="Collateral",
            ),
            AddExternalMappingOp(
                op_id="op-2",
                concept_uri="urn:ontology:entity:collateral",
                scheme="fibo",
                target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/DebtAndEquities/Debt/Collateral",
                mapping_type="exactMatch",
                mapping_confidence=0.88,
            ),
        ],
    )
    target = apply(base, delta)

    verifier = StubGroundingVerifier(default_score=0.88)
    gov = GovernanceValidator(grounding_verifier=verifier)
    # Should not raise
    gov.validate_grounding(base, delta, target)


def test_ont_evo_002_ungrounded_concept_without_waiver_raises() -> None:
    base = make_sample_ontology()
    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        operations=[
            AddConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:collateral",
                concept_name="Collateral",
            )
        ],
    )
    target = apply(base, delta)

    verifier = StubGroundingVerifier(default_score=0.55)  # < 0.6500 threshold
    gov = GovernanceValidator(grounding_verifier=verifier)

    with pytest.raises(UngroundedConceptError) as exc_info:
        gov.validate_grounding(base, delta, target)
    assert exc_info.value.status_code == 422
    assert exc_info.value.exit_code == 4
    assert exc_info.value.error_code == "ONT_EVO_UNGROUNDED_CONCEPT"
    assert "lacks verified positive reference grounding" in exc_info.value.message


def test_ont_evo_002_valid_steward_waiver_passes() -> None:
    base = make_sample_ontology()
    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        operations=[
            AddConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:collateral",
                concept_name="Collateral",
            )
        ],
    )
    target = apply(base, delta)

    verifier = StubGroundingVerifier(default_score=0.40)
    gov = GovernanceValidator(grounding_verifier=verifier, waiver_verifier=StubWaiverVerifier())

    waiver = StewardWaiverRecord(
        proposal_id=uuid4(),
        proposal_delta_sha256="aa" * 32,
        base_sha256="bb" * 32,
        target_concept_uris=["urn:ontology:entity:collateral"],
        rationale="Proprietary synthetic derivative not modeled in FIBO standard.",
        authorized_by="steward-alice",
        authorized_role="steward",
        signature_or_token="token-valid",
    )

    gov.validate_grounding(base, delta, target, waivers=[waiver])


def test_ont_evo_002_waiver_short_rationale_raises() -> None:
    base = make_sample_ontology()
    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        operations=[
            AddConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:collateral",
                concept_name="Collateral",
            )
        ],
    )
    target = apply(base, delta)

    verifier = StubGroundingVerifier(default_score=0.40)
    gov = GovernanceValidator(grounding_verifier=verifier)

    waiver = StewardWaiverRecord.model_construct(
        waiver_id=uuid4(),
        target_concept_uris=["urn:ontology:entity:collateral"],
        rationale="Too short",  # < 10 characters
        authorized_by="steward-alice",
        authorized_role="steward",
    )

    with pytest.raises(InvalidGroundingWaiverError) as exc_info:
        gov.validate_grounding(base, delta, target, waivers=[waiver])
    assert exc_info.value.error_code == "INVALID_GROUNDING_WAIVER"
    assert "at least 10 characters" in exc_info.value.message


def test_ont_evo_002_waiver_unauthorized_role_raises() -> None:
    base = make_sample_ontology()
    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        operations=[
            AddConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:collateral",
                concept_name="Collateral",
            )
        ],
    )
    target = apply(base, delta)

    verifier = StubGroundingVerifier(default_score=0.40)
    gov = GovernanceValidator(grounding_verifier=verifier)

    waiver = StewardWaiverRecord.model_construct(
        waiver_id=uuid4(),
        target_concept_uris=["urn:ontology:entity:collateral"],
        rationale="Sufficient rationale for synthetic contract, but invalid role.",
        authorized_by="viewer-bob",
        authorized_role="viewer",  # invalid role
    )

    with pytest.raises(InvalidGroundingWaiverError) as exc_info:
        gov.validate_grounding(base, delta, target, waivers=[waiver])
    assert exc_info.value.error_code == "INVALID_GROUNDING_WAIVER"
    assert "authorized_role must be admin or steward" in exc_info.value.message


def test_ont_evo_002_waiver_verification_failure_raises() -> None:
    base = make_sample_ontology()
    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        operations=[
            AddConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:collateral",
                concept_name="Collateral",
            )
        ],
    )
    target = apply(base, delta)

    verifier = StubGroundingVerifier(default_score=0.40)
    w_verifier = StubWaiverVerifier(valid_waivers=set())  # Empty set: rejects all
    gov = GovernanceValidator(grounding_verifier=verifier, waiver_verifier=w_verifier)

    waiver = StewardWaiverRecord(
        proposal_id=uuid4(),
        proposal_delta_sha256="aa" * 32,
        base_sha256="bb" * 32,
        target_concept_uris=["urn:ontology:entity:collateral"],
        rationale="Proprietary synthetic derivative not modeled in FIBO standard.",
        authorized_by="steward-alice",
        authorized_role="steward",
        signature_or_token="token-valid",
    )

    with pytest.raises(InvalidGroundingWaiverError) as exc_info:
        gov.validate_grounding(base, delta, target, waivers=[waiver])
    assert "Cryptographic waiver verification failed" in exc_info.value.message


def test_ont_evo_002_touched_grandfathered_concept_voids_exemption() -> None:
    base = make_sample_ontology()
    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        target_ontology_version="1.0.1",
        operations=[
            AddSynonymOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:party",
                synonym="Counterparty",
            )
        ],
    )
    target = apply(base, delta)

    # Concept party is grandfathered, but when touched by AddSynonymOp,
    # its exemption is voided. If grounding score < 0.6500, it raises.
    verifier = StubGroundingVerifier(scores={"urn:ontology:entity:party": 0.50})
    gov = GovernanceValidator(grounding_verifier=verifier)

    with pytest.raises(UngroundedConceptError) as exc_info:
        gov.validate_grounding(
            base,
            delta,
            target,
            grandfathered_concept_uris={"urn:ontology:entity:party"},
        )
    assert exc_info.value.error_code == "ONT_EVO_UNGROUNDED_CONCEPT"


def test_ont_evo_003_occ_matching_passes() -> None:
    ont = make_sample_ontology()
    base_sha = compute_ontology_sha256(ont)

    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        base_sha256=base_sha,
        operations=[],
    )

    gov = GovernanceValidator()
    # Matching session head sha and if-match header
    gov.validate_occ(delta, session_head_sha256=base_sha, if_match=f'"{base_sha}"')


def test_ont_evo_003_occ_mismatched_session_head_raises() -> None:
    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        base_sha256="aa" * 32,
        operations=[],
    )

    gov = GovernanceValidator()
    with pytest.raises(ConcurrentModificationError) as exc_info:
        gov.validate_occ(delta, session_head_sha256="bb" * 32)
    assert exc_info.value.status_code == 412
    assert exc_info.value.exit_code == 5
    assert exc_info.value.error_code == "ONT_EVO_CONCURRENT_MODIFICATION"


def test_ont_evo_003_occ_mismatched_if_match_raises() -> None:
    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        base_sha256="aa" * 32,
        operations=[],
    )

    gov = GovernanceValidator()
    with pytest.raises(ConcurrentModificationError) as exc_info:
        gov.validate_occ(delta, if_match="cc" * 32)
    assert exc_info.value.status_code == 412
    assert exc_info.value.exit_code == 5


# ---------------------------------------------------------------------------
# SemVer Calculus Tests (compute_next_semver & bump validation)
# ---------------------------------------------------------------------------


def test_semver_greenfield_genesis_0_0_0_to_1_0_0() -> None:
    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="0.0.0",
        operations=[
            AddConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:party",
                concept_name="Party",
            )
        ],
    )
    assert compute_next_semver("0.0.0", delta) == "1.0.0"


def test_semver_greenfield_genesis_target_less_than_1_0_0_raises() -> None:
    delta = OntologyDelta.model_construct(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="0.0.0",
        target_ontology_version="0.9.0",
        operations=[
            AddConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:party",
                concept_name="Party",
            )
        ],
    )
    with pytest.raises(SemVerCalculusError) as exc_info:
        compute_next_semver("0.0.0", delta)
    assert exc_info.value.status_code == 422
    assert exc_info.value.exit_code == 4
    assert exc_info.value.error_code == "ONT_EVO_ENVELOPE_VIOLATION"


def test_semver_draft_increments_pre_cbc() -> None:
    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="0.0.0",
        target_ontology_version="0.1.0",
        operations=[
            AddReferenceEnumerationOp(
                op_id="op-1",
                name="currency",
                target_reference_table="ref_currency",
                allowed_codes=["USD"],
            )
        ],
    )
    assert compute_next_semver("0.0.0", delta) == "0.1.0"


def test_semver_additive_minor_bump() -> None:
    ont = make_sample_ontology()
    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        operations=[
            AddAttributeOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:party",
                attribute_name="tax_id",
                data_type="string",
                is_optional=True,
            )
        ],
    )
    assert compute_next_semver("1.0.0", delta, base_ontology=ont) == "1.1.0"


def test_semver_overwrite_major_bump() -> None:
    ont = make_sample_ontology()
    ont.canonical_business_concepts[0].parent_concept_uri = "urn:ontology:entity:legal_entity"
    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        target_ontology_version="2.0.0",
        operations=[
            SetParentConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:party",
                parent_concept_uri="urn:ontology:entity:actor",
            )
        ],
    )
    assert compute_next_semver("1.0.0", delta, base_ontology=ont) == "2.0.0"


def test_semver_initial_unset_minor_bump() -> None:
    ont = make_sample_ontology()
    ont.canonical_business_concepts[0].parent_concept_uri = None
    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        operations=[
            SetParentConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:party",
                parent_concept_uri="urn:ontology:entity:actor",
            )
        ],
    )
    # Initial assignment of unset parent concept is non-breaking MINOR
    assert compute_next_semver("1.0.0", delta, base_ontology=ont) == "1.1.0"


def test_semver_mandatory_attribute_on_existing_concept_major_bump() -> None:
    ont = make_sample_ontology()
    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        target_ontology_version="2.0.0",
        operations=[
            AddAttributeOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:party",
                attribute_name="jurisdiction",
                data_type="string",
                is_optional=False,  # Breaking change on existing concept!
            )
        ],
    )
    assert compute_next_semver("1.0.0", delta, base_ontology=ont) == "2.0.0"


def test_semver_structural_deletion_major_bump() -> None:
    ont = make_sample_ontology()
    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        target_ontology_version="2.0.0",
        operations=[
            RemoveAttributeOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:party",
                attribute_name="name",
            )
        ],
    )
    assert compute_next_semver("1.0.0", delta, base_ontology=ont) == "2.0.0"


def test_semver_metadata_patch_bump() -> None:
    ont = make_sample_ontology()
    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        target_ontology_version="1.0.1",
        operations=[
            AddSynonymOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:party",
                synonym="Counterparty",
            )
        ],
    )
    assert compute_next_semver("1.0.0", delta, base_ontology=ont) == "1.0.1"


def test_semver_target_version_validation() -> None:
    ont = make_sample_ontology()
    # A major operation requires major bump. Target 1.1.0 violates this.
    delta = OntologyDelta.model_construct(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        target_ontology_version="1.1.0",
        operations=[
            RemoveConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:account",
            )
        ],
    )
    with pytest.raises(SemVerCalculusError) as exc_info:
        compute_next_semver("1.0.0", delta, base_ontology=ont)
    assert "Expected major bump >= 2.0.0" in exc_info.value.message


# ---------------------------------------------------------------------------
# Pure State Applier Tests (Immutability & All 24 Ops)
# ---------------------------------------------------------------------------


def test_apply_pure_immutability_base_unchanged() -> None:
    base = make_sample_ontology()
    base_sha_before = compute_ontology_sha256(base)
    orig_concept_count = len(base.canonical_business_concepts)
    orig_rel_count = len(base.canonical_relationships)

    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        target_ontology_version="2.0.0",
        operations=[
            AddConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:instrument",
                concept_name="Instrument",
            ),
            RemoveConceptOp(
                op_id="op-2",
                concept_uri="urn:ontology:entity:account",
            ),
        ],
    )

    target = apply(base, delta)

    # Base ontology instance and serialization MUST remain unchanged
    assert len(base.canonical_business_concepts) == orig_concept_count
    assert len(base.canonical_relationships) == orig_rel_count
    assert compute_ontology_sha256(base) == base_sha_before

    # Target ontology reflects the applied state
    assert len(target.canonical_business_concepts) == 2
    concept_uris = {c.concept_uri for c in target.canonical_business_concepts}
    assert "urn:ontology:entity:instrument" in concept_uris
    assert "urn:ontology:entity:account" not in concept_uris
    # Relationship referencing deleted account should be automatically removed
    assert len(target.canonical_relationships) == 0


def test_apply_update_concept_urn_cascades_endpoints_and_parents() -> None:
    base = make_sample_ontology()
    base.canonical_business_concepts[1].parent_concept_uri = "urn:ontology:entity:party"

    delta = OntologyDelta(
        delta_id="d1",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        target_ontology_version="2.0.0",
        operations=[
            UpdateConceptUrnOp(
                op_id="op-1",
                old_concept_uri="urn:ontology:entity:party",
                new_concept_uri="urn:ontology:entity:legal_entity",
            )
        ],
    )

    target = apply(base, delta)
    c_uris = {c.concept_uri for c in target.canonical_business_concepts}
    assert "urn:ontology:entity:legal_entity" in c_uris
    assert "urn:ontology:entity:party" not in c_uris

    # Child's parent pointer updated
    acc = next(
        c
        for c in target.canonical_business_concepts
        if c.concept_uri == "urn:ontology:entity:account"
    )
    assert acc.parent_concept_uri == "urn:ontology:entity:legal_entity"

    # Relationship endpoints updated
    rel = target.canonical_relationships[0]
    assert "urn:ontology:entity:legal_entity" in rel.endpoints
    assert "urn:ontology:entity:party" not in rel.endpoints


def test_apply_all_24_delta_ops_exercised() -> None:
    """Ensure all 24 operations in the closed catalog can be applied purely and cleanly."""
    base = make_sample_ontology()

    ops = [
        # 1. AddConceptOp
        AddConceptOp(
            op_id="op-01",
            concept_uri="urn:ontology:entity:custody_asset",
            concept_name="Custody Asset",
            target_hub="hub_custody_asset",
        ),
        # 2. AddRelationshipOp
        AddRelationshipOp(
            op_id="op-02",
            relationship_uri="urn:ontology:relationship:account_holds_asset",
            relationship_name="account_holds_asset",
            target_link="lnk_account_asset",
            endpoints=["urn:ontology:entity:account", "urn:ontology:entity:custody_asset"],
        ),
        # 3. AddAttributeOp
        AddAttributeOp(
            op_id="op-03",
            concept_uri="urn:ontology:entity:custody_asset",
            attribute_name="cusip",
            data_type="string",
            is_optional=True,
        ),
        # 4. SetParentConceptOp
        SetParentConceptOp(
            op_id="op-04",
            concept_uri="urn:ontology:entity:custody_asset",
            parent_concept_uri="urn:ontology:entity:party",
        ),
        # 5. SetConceptElmArchetypeOp
        SetConceptElmArchetypeOp(
            op_id="op-05",
            concept_uri="urn:ontology:entity:custody_asset",
            elm_archetype="CORE_BUSINESS_CONCEPT",
        ),
        # 6. AddSynonymOp
        AddSynonymOp(
            op_id="op-06",
            concept_uri="urn:ontology:entity:custody_asset",
            synonym="Safe Asset",
        ),
        # 7. AddExternalMappingOp
        AddExternalMappingOp(
            op_id="op-07",
            concept_uri="urn:ontology:entity:custody_asset",
            scheme="fibo",
            target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Securities/Asset",
            mapping_type="exactMatch",
            mapping_confidence=0.91,
        ),
        # 8. ReviseDescriptionOp
        ReviseDescriptionOp(
            op_id="op-08",
            concept_uri="urn:ontology:entity:custody_asset",
            description="Custody asset held on behalf of an institutional client.",
        ),
        # 9. RescoreExternalMappingOp
        RescoreExternalMappingOp(
            op_id="op-09",
            concept_uri="urn:ontology:entity:custody_asset",
            target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Securities/Asset",
            new_confidence=0.95,
        ),
        # 10. AddReferenceEnumerationOp
        AddReferenceEnumerationOp(
            op_id="op-10",
            name="asset_class",
            target_reference_table="ref_asset_class",
            allowed_codes=["EQUITY", "FIXED_INCOME"],
        ),
        # 11. AddKeyAuthorityPatternOp
        AddKeyAuthorityPatternOp(
            op_id="op-11",
            concept_uri="urn:ontology:entity:custody_asset",
            tier="T1_GOLDEN",
            pattern_regex="^[A-Z0-9]{9}$",
        ),
        # 12. AddGovernanceConstraintOp
        AddGovernanceConstraintOp(
            op_id="op-12",
            concept_uri="urn:ontology:entity:custody_asset",
            constraint_type="security_split",
            pii_confidential_attributes=["tax_id"],
            required_satellite_split="sat_custody_asset_pii",
        ),
        # 13. DeprecateConceptOp
        DeprecateConceptOp(
            op_id="op-13",
            concept_uri="urn:ontology:entity:account",
            reason="Superseded by institutional account module.",
        ),
        # 14. DeprecateRelationshipOp
        DeprecateRelationshipOp(
            op_id="op-14",
            relationship_uri="urn:ontology:relationship:party_holds_account",
            reason="Replaced by direct booking links.",
        ),
        # 15. AddReferenceEnumCodeOp
        AddReferenceEnumCodeOp(
            op_id="op-15",
            enumeration_name="asset_class",
            code="COMMODITY",
        ),
        # 16. RepinExternalMappingOp
        RepinExternalMappingOp(
            op_id="op-16",
            concept_uri="urn:ontology:entity:custody_asset",
            target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Securities/Asset",
            pinned=True,
        ),
        # 17. RefineDefinitionOp
        RefineDefinitionOp(
            op_id="op-17",
            concept_uri="urn:ontology:entity:custody_asset",
            definition="Certified institutional custody asset.",
        ),
        # 18. AddSourceHintOp
        AddSourceHintOp(
            op_id="op-18",
            concept_uri="urn:ontology:entity:custody_asset",
            source_system="CORE_BANKING_ORACLE",
        ),
        # 19. AnnotateConceptMetadataOp
        AnnotateConceptMetadataOp(
            op_id="op-19",
            concept_uri="urn:ontology:entity:custody_asset",
            metadata={"classification": "CONFIDENTIAL"},
        ),
        # 20. RemoveExternalMappingOp
        RemoveExternalMappingOp(
            op_id="op-20",
            concept_uri="urn:ontology:entity:party",
            target_uri="https://spec.edmcouncil.org/fibo/ontology/FND/AgentsAndPeople/Agents/Agent",
        ),
        # 21. RemoveAttributeOp
        RemoveAttributeOp(
            op_id="op-21",
            concept_uri="urn:ontology:entity:party",
            attribute_name="name",
        ),
        # 22. RemoveRelationshipOp
        RemoveRelationshipOp(
            op_id="op-22",
            relationship_uri="urn:ontology:relationship:account_holds_asset",
        ),
        # 23. UpdateConceptUrnOp
        UpdateConceptUrnOp(
            op_id="op-23",
            old_concept_uri="urn:ontology:entity:custody_asset",
            new_concept_uri="urn:ontology:entity:client_asset",
        ),
        # 24. RemoveConceptOp
        RemoveConceptOp(
            op_id="op-24",
            concept_uri="urn:ontology:entity:client_asset",
        ),
    ]

    delta = OntologyDelta(
        delta_id="delta-all-24",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        target_ontology_version="2.0.0",
        operations=ops,
    )

    target = apply(base, delta)
    assert target is not None
    assert target.ontology_version == "2.0.0"  # Destructive ops elevated to MAJOR
    assert base.ontology_version == "1.0.0"


def test_apply_ontology_delta_backward_compat() -> None:
    base = make_sample_ontology()
    delta = OntologyDelta(
        delta_id="delta-compat",
        domain_namespace="finance",
        base_ontology_version="1.0.0",
        target_ontology_version="1.0.1",
        operations=[
            AddSynonymOp(
                op_id="op-syn-1",
                concept_uri="urn:ontology:entity:party",
                synonym="Holder",
            ),
            AddSynonymOp(
                op_id="op-syn-2",
                concept_uri="urn:ontology:entity:party",
                synonym="Subject",
            ),
        ],
    )

    # Filtering only op-syn-1
    target = apply_ontology_delta(
        base,
        delta,
        accepted_op_ids=["op-syn-1"],
        decision_id="DEC-4001",
    )

    c = next(
        c
        for c in target.canonical_business_concepts
        if c.concept_uri == "urn:ontology:entity:party"
    )
    assert "Holder" in (c.synonyms or [])
    assert "Subject" not in (c.synonyms or [])
    assert "DEC-4001" in target.decision_refs
