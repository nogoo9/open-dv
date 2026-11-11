"""Empirical Adversarial Stress & Verification Test Suite for Milestone M6 (Phase 6 Track 6A).

Challenger 6.1 Empirical Stress Suite validating:
1. Canonical Invariant SHA-256 Hashes:
   - Bit-for-bit invariance of Private Equity Graph A
     (1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81)
   - Certified Wealth Management Graph A ground truth hash stability
2. Scoring Engine Edge Stress & Degraded Model Sensitivity:
   - Degraded models with missing concepts drop recall below 0.80 or fail CQ coverage.
   - Models with hallucinated concepts drop precision below 0.85.
   - Edge case handling: empty operation proposals and provisional URI handling.
3. Competency Question (CQ) Robustness:
   - Structural validation of all 20 CQs across PE and WM.
   - CQ sensitivity to dropped concepts, relationships, attributes, enums, and mappings.
   - Substring collision vulnerability analysis in relationship evaluation.
4. Dual-Mode Runtime SLA & Concordance:
   - 50-iteration statistical latency benchmarking verifying execution strictly < 2.5s.
   - Offline execution without cloud LLM API keys.
   - Jaccard concept concordance >= 80% between Deterministic and Agent modes.
5. Privacy Governance & Destructive Operation Guardrails:
   - Strict rejection of all 4 destructive delta operations with exit code 7.
   - Detection of leaked [REDACTED_ODV_*] and pseudo_* tokens.
   - TermScout HMAC-SHA256 pseudonymization under K_pseudo.
"""

from __future__ import annotations

import copy
import time

import pytest
from pydantic import ValidationError

from opendv_agent_harness.personas import OntologyArchitect, TermScout
from opendv_agent_harness.privacy import (
    AgentDestructiveOpForbiddenError,
    AgentUnresolvedPseudonymError,
    Pseudonymizer,
    RedactedPlaceholderLeakError,
    scan_redacted_placeholder_leak,
    scan_unresolved_pseudonyms,
)
from opendv_contracts.dtos.ingestion import EvidenceChunk, TableStructure
from opendv_contracts.dtos.ontology import (
    AddConceptOp,
    OntologyDelta,
    OntologyProposal,
    RemoveAttributeOp,
    RemoveConceptOp,
    RemoveExternalMappingOp,
    RemoveRelationshipOp,
)
from opendv_core import compute_ontology_sha256, validate_graph_a
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    GraphAOntology,
)
from opendv_service.construction.deterministic_pipeline import (
    DeterministicConstructionPipeline,
)
from tests.golden.ontology_studio.test_golden_evaluation import (
    GOLDEN_CQS_PATH,
    MIN_PRECISION_THRESHOLD,
    MIN_RECALL_THRESHOLD,
    PE_GROUND_TRUTH_PATH,
    WM_GROUND_TRUTH_PATH,
    GoldenEvaluator,
    _slugify,
)
from tests.golden.ontology_studio.test_golden_modes import (
    pe_evidence_tables as get_pe_evidence_tables,
)
from tests.golden.ontology_studio.test_golden_modes import (
    wm_evidence_tables as get_wm_evidence_tables,
)

EXPECTED_PE_CANONICAL_SHA256 = (
    "1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81"
)
EXPECTED_WM_CANONICAL_SHA256 = (
    "ca2fe09c9baa0a3d16200ae214b7cde99ab1788b224a370852f2a4db837f3c0c"
)


@pytest.fixture(scope="module")
def evaluator() -> GoldenEvaluator:
    return GoldenEvaluator(cqs_catalog_path=GOLDEN_CQS_PATH)


@pytest.fixture(scope="module")
def pe_gt() -> GraphAOntology:
    assert PE_GROUND_TRUTH_PATH.exists()
    return validate_graph_a(PE_GROUND_TRUTH_PATH)


@pytest.fixture(scope="module")
def wm_gt() -> GraphAOntology:
    assert WM_GROUND_TRUTH_PATH.exists()
    return validate_graph_a(WM_GROUND_TRUTH_PATH)


@pytest.fixture(scope="module")
def pe_tables() -> list[TableStructure]:
    return get_pe_evidence_tables.__wrapped__()


@pytest.fixture(scope="module")
def wm_tables() -> list[TableStructure]:
    return get_wm_evidence_tables.__wrapped__()


# =============================================================================
# 1. Canonical Invariant SHA-256 Stability Tests
# =============================================================================


class TestCanonicalInvarianceEmpirical:
    """Empirical verification of canonical Graph A SHA-256 invariants."""

    def test_pe_graph_a_sha256_bit_for_bit(self, pe_gt: GraphAOntology) -> None:
        """Verify PE Graph A evaluates bit-for-bit to EXPECTED_PE_CANONICAL_SHA256."""
        computed_sha = compute_ontology_sha256(pe_gt)
        assert computed_sha == EXPECTED_PE_CANONICAL_SHA256, (
            f"PE Graph A SHA-256 drift: {computed_sha} != {EXPECTED_PE_CANONICAL_SHA256}"
        )

    def test_wm_graph_a_sha256_stability(self, wm_gt: GraphAOntology) -> None:
        """Verify WM Graph A evaluates stably to EXPECTED_WM_CANONICAL_SHA256."""
        computed_sha = compute_ontology_sha256(wm_gt)
        assert computed_sha == EXPECTED_WM_CANONICAL_SHA256, (
            f"WM Graph A SHA-256 drift: {computed_sha} != {EXPECTED_WM_CANONICAL_SHA256}"
        )


# =============================================================================
# 2. Scoring Engine Edge Stress & Degraded Model Sensitivity
# =============================================================================


class TestScoringEngineDegradedModelSensitivity:
    """Stress-test precision/recall calculations and edge boundary conditions."""

    def test_single_concept_degraded_model_fails_ci_gate(
        self, evaluator: GoldenEvaluator, pe_gt: GraphAOntology, wm_gt: GraphAOntology
    ) -> None:
        """A model proposing only 1 concept must fail recall and CI gates safely."""
        # Single concept for PE
        pe_single = GraphAOntology(
            ontology_version="1.0.0",
            domain_namespace="pe_asset_management",
            canonical_business_concepts=[pe_gt.canonical_business_concepts[0]],
            canonical_relationships=[],
        )
        pe_report = evaluator.evaluate(pe_single, pe_gt, domain_filter="private_equity")
        assert pe_report.concept_precision == 1.0000
        assert pe_report.concept_recall == 0.2000  # 1 of 5
        assert pe_report.relationship_recall == 0.0000
        assert pe_report.cq_coverage <= 0.2000
        assert not pe_report.passes_ci_gate

        # Single concept for WM
        wm_single = GraphAOntology(
            ontology_version="1.0.0",
            domain_namespace="wealth_mgmt",
            canonical_business_concepts=[wm_gt.canonical_business_concepts[0]],
            canonical_relationships=[],
        )
        wm_report = evaluator.evaluate(wm_single, wm_gt, domain_filter="wealth_mgmt")
        assert wm_report.concept_precision == 1.0000
        assert wm_report.concept_recall == round(1 / 6, 4)
        assert wm_report.relationship_recall == 0.0000
        assert not wm_report.passes_ci_gate

    def test_missing_concept_drops_recall_below_threshold(
        self, evaluator: GoldenEvaluator, pe_gt: GraphAOntology
    ) -> None:
        """Dropping 2 concepts from PE (3 of 5) drops recall to 0.6000 (< 0.8000)."""
        retained = pe_gt.canonical_business_concepts[:3]
        degraded = GraphAOntology(
            ontology_version="1.0.0",
            domain_namespace="pe_asset_management",
            canonical_business_concepts=retained,
            canonical_relationships=list(pe_gt.canonical_relationships or []),
            reference_enumerations=list(pe_gt.reference_enumerations or []),
        )
        report = evaluator.evaluate(degraded, pe_gt, domain_filter="private_equity")
        assert report.concept_recall == 0.6000
        assert report.concept_recall < MIN_RECALL_THRESHOLD
        assert not report.passes_ci_gate

    def test_hallucinated_concepts_drop_precision_below_threshold(
        self, evaluator: GoldenEvaluator, pe_gt: GraphAOntology
    ) -> None:
        """Adding 1 hallucinated concept drops precision to 0.8333 (< 0.8500)."""
        extra = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:hallucinated_macro_hedge",
            concept_name="HallucinatedMacroHedge",
            target_hub="hub_hallucinated_macro_hedge",
        )
        degraded = GraphAOntology(
            ontology_version="1.0.0",
            domain_namespace="pe_asset_management",
            canonical_business_concepts=[*pe_gt.canonical_business_concepts, extra],
            canonical_relationships=list(pe_gt.canonical_relationships or []),
            reference_enumerations=list(pe_gt.reference_enumerations or []),
        )
        report = evaluator.evaluate(degraded, pe_gt, domain_filter="private_equity")
        assert report.concept_precision == round(5 / 6, 4)  # 0.8333
        assert report.concept_precision < MIN_PRECISION_THRESHOLD
        assert not report.passes_ci_gate

    def test_hallucinated_relationships_drop_precision_below_threshold(
        self, evaluator: GoldenEvaluator, wm_gt: GraphAOntology
    ) -> None:
        """Adding 1 hallucinated relationship to WM drops precision to 0.8333 (< 0.8500)."""
        extra_rel = CanonicalRelationship(
            relationship_uri="urn:ontology:relationship:bogus_unrealized_exposure",
            relationship_name="BogusUnrealizedExposure",
            target_link="lnk_bogus_unrealized_exposure",
            endpoints=["hub_party", "hub_portfolio"],
        )
        degraded = GraphAOntology(
            ontology_version="1.0.0",
            domain_namespace="wealth_mgmt",
            canonical_business_concepts=list(wm_gt.canonical_business_concepts or []),
            canonical_relationships=[*wm_gt.canonical_relationships, extra_rel],
            reference_enumerations=list(wm_gt.reference_enumerations or []),
        )
        report = evaluator.evaluate(degraded, wm_gt, domain_filter="wealth_mgmt")
        assert report.relationship_precision == round(5 / 6, 4)  # 0.8333
        assert report.relationship_precision < MIN_PRECISION_THRESHOLD
        assert not report.passes_ci_gate

    def test_empty_proposal_raises_validation_error_in_normalizer(
        self, evaluator: GoldenEvaluator, wm_gt: GraphAOntology
    ) -> None:
        """Evaluating an empty proposal (0 ops) triggers Pydantic minimum concept check."""
        empty_proposal = OntologyProposal(
            proposal_id="prop-empty-adversarial",
            wave_id="wave_3",
            recommender_id="rec-adversary",
            recommender_version="1.0.0",
            base_ontology_sha256=EXPECTED_WM_CANONICAL_SHA256,
            delta=OntologyDelta(
                delta_id="d-empty",
                base_version="0.0.0",
                author="adversary",
                operations=[],
            ),
        )
        with pytest.raises(ValidationError) as exc_info:
            evaluator.evaluate(empty_proposal, wm_gt, domain_filter="wealth_mgmt")
        assert "canonical_business_concepts" in str(exc_info.value)

    def test_provisional_uri_in_agent_proposal_triggers_canonical_uri_pattern_mismatch(
        self, evaluator: GoldenEvaluator, pe_gt: GraphAOntology
    ) -> None:
        """Provisional URIs in agent proposals trigger Pydantic pattern mismatch in normalizer."""
        agent_proposal = OntologyProposal(
            proposal_id="prop-agent-provisional",
            wave_id="wave_3",
            recommender_id="rec-agent",
            recommender_version="1.0.0",
            base_ontology_sha256=EXPECTED_PE_CANONICAL_SHA256,
            delta=OntologyDelta(
                delta_id="d-prov",
                base_version="0.0.0",
                author="agent:ontology-architect",
                operations=[
                    AddConceptOp(
                        op_id="op-01",
                        concept_uri="urn:ontology:provisional:entity:fund",
                        concept_name="Fund",
                        target_hub="hub_fund",
                    )
                ],
            ),
        )
        with pytest.raises(ValidationError) as exc_info:
            evaluator.evaluate(agent_proposal, pe_gt, domain_filter="private_equity")
        assert "string_pattern_mismatch" in str(exc_info.value)


# =============================================================================
# 3. Competency Question Robustness & Substring Collision Mining
# =============================================================================


class TestCompetencyQuestionsRobustnessEmpirical:
    """Stress-test competency question evaluation under targeted element removals."""

    def test_all_20_cqs_have_valid_structure(self, evaluator: GoldenEvaluator) -> None:
        """Verify exactly 20 CQs with non-empty required concepts and metadata."""
        assert len(evaluator.cqs) == 20
        pe_count = sum(1 for c in evaluator.cqs if c.get("domain") == "private_equity")
        wm_count = sum(1 for c in evaluator.cqs if c.get("domain") == "wealth_mgmt")
        assert pe_count == 10
        assert wm_count == 10

        for cq in evaluator.cqs:
            assert cq["cq_id"].startswith("CQ-")
            assert len(cq["question"]) >= 10
            assert len(cq["required_concepts"]) >= 1

    def test_wm_relationship_deletions_fail_corresponding_cqs(
        self, evaluator: GoldenEvaluator, wm_gt: GraphAOntology
    ) -> None:
        """Verify that dropping each canonical relationship in WM fails its governing CQ."""
        relationship_cqs = [
            ("PartyGuarantor", "CQ-WM-04"),
            ("CustomerAccount", "CQ-WM-01"),
            ("AccountPortfolio", "CQ-WM-02"),
            ("PortfolioHolding", "CQ-WM-03"),
            ("CustodianSettlement", "CQ-WM-05"),
        ]
        for rel_name, cq_id in relationship_cqs:
            ont = copy.deepcopy(wm_gt)
            ont.canonical_relationships = [
                r for r in ont.canonical_relationships if r.relationship_name != rel_name
            ]
            cq = next(c for c in evaluator.cqs if c["cq_id"] == cq_id)
            res = evaluator._evaluate_single_cq(cq, ont)
            assert not res.is_satisfied, f"Expected {cq_id} to fail when {rel_name} is removed"
            assert rel_name in res.missing_relationships

    def test_wm_attribute_and_mapping_deletions_fail_corresponding_cqs(
        self, evaluator: GoldenEvaluator, wm_gt: GraphAOntology
    ) -> None:
        """Verify dropping total_exposure, credit_limit, or FIBO mapping fails respective CQs."""
        # 1. Drop total_exposure
        ont_no_exp = copy.deepcopy(wm_gt)
        for c in ont_no_exp.canonical_business_concepts:
            if c.concept_name == "Party":
                c.attributes = [a for a in c.attributes if a.attribute_name != "total_exposure"]
        cq_08 = next(c for c in evaluator.cqs if c["cq_id"] == "CQ-WM-08")
        assert not evaluator._evaluate_single_cq(cq_08, ont_no_exp).is_satisfied

        # 2. Drop credit_limit
        ont_no_cred = copy.deepcopy(wm_gt)
        for c in ont_no_cred.canonical_business_concepts:
            if c.concept_name == "Customer":
                c.attributes = [a for a in c.attributes if a.attribute_name != "credit_limit"]
        cq_09 = next(c for c in evaluator.cqs if c["cq_id"] == "CQ-WM-09")
        assert not evaluator._evaluate_single_cq(cq_09, ont_no_cred).is_satisfied

        # 3. Drop LegalEntity FIBO mapping
        ont_no_map = copy.deepcopy(wm_gt)
        for c in ont_no_map.canonical_business_concepts:
            if c.concept_name == "Party":
                c.external_mappings = []
        cq_10 = next(c for c in evaluator.cqs if c["cq_id"] == "CQ-WM-10")
        assert not evaluator._evaluate_single_cq(cq_10, ont_no_map).is_satisfied

    def test_substring_overlap_behavior_in_pe_relationship_cqs(
        self, evaluator: GoldenEvaluator, pe_gt: GraphAOntology
    ) -> None:
        """Empirical Finding: Substring matching causes LPCommitment to satisfy CQ-PE-02."""
        ont = copy.deepcopy(pe_gt)
        ont.canonical_relationships = [
            r
            for r in ont.canonical_relationships
            if r.relationship_name != "LPCommitmentCoInvestTarget"
        ]
        cq_02 = next(c for c in evaluator.cqs if c["cq_id"] == "CQ-PE-02")
        res = evaluator._evaluate_single_cq(cq_02, ont)
        assert res.is_satisfied, (
            "Empirical verification: substring matching causes LPCommitment to satisfy CQ-PE-02"
        )


# =============================================================================
# 4. Dual-Mode Runtime SLA & Concordance Stress
# =============================================================================


class TestDualModeRuntimeSLAAndConcordanceEmpirical:
    """Stress-test runtime latency percentiles and dual-mode concordance under load."""

    def test_deterministic_pipeline_latency_percentiles(
        self, pe_tables: list[TableStructure]
    ) -> None:
        """Run 50 iterations and assert strict max latency < 2.500s (measured < 25ms)."""
        pipeline = DeterministicConstructionPipeline()
        base_ont = GraphAOntology(
            ontology_version="1.0.0",
            domain_namespace="pe_asset_management",
            canonical_business_concepts=[
                CanonicalBusinessConcept(
                    concept_uri="urn:ontology:entity:initial_seed",
                    concept_name="Initial Seed",
                    target_hub="hub_initial_seed",
                )
            ],
        )

        latencies: list[float] = []
        for _ in range(50):
            t0 = time.perf_counter()
            proposal = pipeline.construct_proposal(base_ontology=base_ont, tables=pe_tables)
            latencies.append(time.perf_counter() - t0)
            assert isinstance(proposal, OntologyProposal)

        p95_ms = sorted(latencies)[int(0.95 * len(latencies))] * 1000
        assert max(latencies) < 2.5000, f"Max latency {max(latencies):.4f}s exceeded 2.5s SLA"
        assert p95_ms < 50.0, f"p95 latency {p95_ms:.2f}ms exceeded 50ms guardrail"

    def test_deterministic_and_agent_mode_concordance_exact(
        self, pe_tables: list[TableStructure], wm_tables: list[TableStructure]
    ) -> None:
        """Assert concept Jaccard concordance between modes is >= 80% (measured 100%)."""
        pipeline = DeterministicConstructionPipeline()
        architect = OntologyArchitect()
        base_ont = GraphAOntology(
            ontology_version="1.0.0",
            domain_namespace="pe_asset_management",
            canonical_business_concepts=[
                CanonicalBusinessConcept(
                    concept_uri="urn:ontology:entity:initial_seed",
                    concept_name="Initial Seed",
                    target_hub="hub_initial_seed",
                )
            ],
        )

        # PE concordance
        det_pe = pipeline.construct_proposal(base_ontology=base_ont, tables=pe_tables)
        agent_pe = architect.propose_delta(base_ontology=base_ont, evidence=pe_tables)
        pe_det_c = {
            _slugify(op.concept_name or op.concept_uri)
            for op in det_pe.delta.operations
            if isinstance(op, AddConceptOp)
        }
        pe_agent_c = {
            _slugify(op.concept_name or op.concept_uri)
            for op in agent_pe.delta.operations
            if isinstance(op, AddConceptOp)
        }
        pe_concordance = len(pe_det_c & pe_agent_c) / len(pe_det_c | pe_agent_c)
        assert pe_concordance >= 0.8000
        assert pe_concordance == 1.0000

        # WM concordance
        det_wm = pipeline.construct_proposal(base_ontology=base_ont, tables=wm_tables)
        agent_wm = architect.propose_delta(base_ontology=base_ont, evidence=wm_tables)
        wm_det_c = {
            _slugify(op.concept_name or op.concept_uri)
            for op in det_wm.delta.operations
            if isinstance(op, AddConceptOp)
        }
        wm_agent_c = {
            _slugify(op.concept_name or op.concept_uri)
            for op in agent_wm.delta.operations
            if isinstance(op, AddConceptOp)
        }
        wm_concordance = len(wm_det_c & wm_agent_c) / len(wm_det_c | wm_agent_c)
        assert wm_concordance >= 0.8000
        assert wm_concordance == 1.0000


# =============================================================================
# 5. Privacy Leak Detection & Destructive Operation Rejection
# =============================================================================


class TestPrivacyAndDestructiveGuardrailsEmpirical:
    """Stress-test adversarial privacy contamination and destructive operation rejection."""

    def test_all_four_destructive_operations_rejected_with_exit_code_7(self) -> None:
        """Verify RemoveConcept/Rel/Attr/Mapping operations are rejected with exit code 7."""
        architect = OntologyArchitect()
        base_ont = GraphAOntology(
            ontology_version="1.0.0",
            domain_namespace="pe_asset_management",
            canonical_business_concepts=[
                CanonicalBusinessConcept(
                    concept_uri="urn:ontology:entity:fund",
                    concept_name="Fund",
                    target_hub="hub_fund",
                )
            ],
        )

        destructive_ops = [
            RemoveConceptOp(
                op_id="d1",
                concept_uri="urn:ontology:entity:fund",
                reason="adversarial test",
            ),
            RemoveRelationshipOp(
                op_id="d2",
                relationship_uri="urn:ontology:relationship:r1",
                reason="adversarial test",
            ),
            RemoveAttributeOp(
                op_id="d3",
                concept_uri="urn:ontology:entity:fund",
                attribute_name="a1",
                reason="adversarial test",
            ),
            RemoveExternalMappingOp(
                op_id="d4",
                concept_uri="urn:ontology:entity:fund",
                target_uri="urn:m1",
            ),
        ]

        for op in destructive_ops:
            with pytest.raises(AgentDestructiveOpForbiddenError) as exc_info:
                architect.propose_delta(base_ontology=base_ont, operations=[op])
            assert exc_info.value.exit_code == 7
            assert exc_info.value.error_code == "AGENT_DESTRUCTIVE_OP_FORBIDDEN"

    def test_leak_scanners_raise_on_contaminated_tokens(self) -> None:
        """Verify scan_redacted_placeholder_leak and scan_unresolved_pseudonyms catch leaks."""
        with pytest.raises(RedactedPlaceholderLeakError) as exc_redacted:
            scan_redacted_placeholder_leak("Leak of [REDACTED_ODV_COUNTERPARTY_01] token.")
        assert exc_redacted.value.exit_code == 4

        with pytest.raises(AgentUnresolvedPseudonymError) as exc_pseudo:
            scan_unresolved_pseudonyms("Leak of pseudo_1234567890ab token.")
        assert exc_pseudo.value.exit_code == 4

    def test_term_scout_pseudonymizes_overlapping_sensitive_entities(self) -> None:
        """Verify TermScout replaces sensitive entities without leaking plaintext names."""
        k_pseudo = b"adversarial-hmac-key-test-32-bytes"
        pseudo = Pseudonymizer(k_pseudo=k_pseudo)
        scout = TermScout(pseudonymizer=pseudo)

        txt = (
            "Confidential Side Letter with Sovereign Secret Fund and "
            "Sovereign Secret for LP Investment."
        )
        chunk = EvidenceChunk(
            chunk_id="chk-adv-01",
            document_id="doc-adv-01",
            text=txt,
            taint="confidential",
            metadata={"sensitive_entities": ["Sovereign Secret Fund", "Sovereign Secret"]},
            start_char=0,
            end_char=len(txt),
        )

        candidates = scout.extract_candidates(evidence_chunks=[chunk])
        assert len(candidates) >= 1
        for cand in candidates:
            for cit in cand.get("evidence_citations", []):
                snippet = cit.snippet
                if snippet is not None:
                    assert "Sovereign Secret Fund" not in snippet
                    assert "Sovereign Secret" not in snippet
                    assert "pseudo_" in snippet
