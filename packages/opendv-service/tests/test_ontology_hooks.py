"""Unit tests for PipelineExtensionHooks (IP-1..6)."""

from __future__ import annotations

from typing import Any

import pytest

from opendv_contracts import (
    AddConceptOp,
    OntologyDelta,
    OntologyProposal,
    OntologyRecommendationRequest,
    OntologyRecommenderUnavailableError,
    RecommenderCapabilities,
)
from opendv_service.ontology.hooks import (
    BootstrapContext,
    GateViolationContext,
    UnresolvedMentionsContext,
    WaveProfilingContext,
    get_pipeline_extension_hooks,
)
from opendv_service.ontology.registry import OntologyRecommenderRegistry


class MockHookRecommender:
    """Mock recommender returning realistic delta proposals."""

    recommender_id = "test-recommender"
    recommender_version = "1.0.0"
    capabilities = RecommenderCapabilities()

    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.last_request: OntologyRecommendationRequest | None = None

    async def recommend(self, req: OntologyRecommendationRequest) -> OntologyProposal:
        self.last_request = req
        if self.should_fail:
            raise RuntimeError("Recommender provider connection timeout")

        # Propose adding concept matching focus or test
        ops = [
            AddConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:supplier_delivery",
                concept_name="Supplier Delivery",
                target_hub="hub_supplier_delivery",
                definition="A delivery event from an upstream supplier.",
            )
        ]
        return OntologyProposal(
            proposal_id=f"prop-{req.mode.lower()}",
            recommender_id=self.recommender_id,
            recommender_version=self.recommender_version,
            delta=OntologyDelta(
                delta_id="delta-001",
                base_ontology_version="1.0.0",
                operations=ops,
            ),
            confidence_score=0.92,
        )


@pytest.fixture
def mock_registry() -> tuple[OntologyRecommenderRegistry, MockHookRecommender]:
    reg = OntologyRecommenderRegistry()
    rec = MockHookRecommender()
    reg.register_recommender(rec)
    return reg, rec


@pytest.fixture
def base_ontology_dict() -> dict[str, Any]:
    return {
        "ontology_version": "1.0.0",
        "domain_namespace": "logistics",
        "enterprise_owner": "Apex",
        "canonical_business_concepts": [
            {
                "concept_uri": "urn:ontology:entity:supplier",
                "concept_name": "Supplier",
                "target_hub": "hub_supplier",
            }
        ],
    }


@pytest.mark.asyncio
async def test_ip1_bootstrap_suggestion(
    mock_registry: tuple[OntologyRecommenderRegistry, MockHookRecommender],
    base_ontology_dict: dict[str, Any],
) -> None:
    reg, rec = mock_registry
    hooks = get_pipeline_extension_hooks(policy="suggest_only", registry=reg)

    ctx = BootstrapContext(
        domain_namespace="logistics",
        base_ontology=base_ontology_dict,
        samples={"raw_feed": [{"ssn": "123-45-6789", "name": "Acme"}]},
    )
    proposal = await hooks.suggest_ontology_deltas(ctx)
    assert proposal.proposal_id == "prop-delta"
    assert rec.last_request is not None
    assert rec.last_request.injection_point == "IP-1"
    # Ensure samples were redacted
    assert rec.last_request.samples["raw_feed"][0]["ssn"] == "[REDACTED:RESTRICTED_PII]"


@pytest.mark.asyncio
async def test_ip2_advisory_after_profiling(
    mock_registry: tuple[OntologyRecommenderRegistry, MockHookRecommender],
    base_ontology_dict: dict[str, Any],
) -> None:
    reg, rec = mock_registry

    # 1. With policy 'off', hook is never invoked
    hooks_off = get_pipeline_extension_hooks(policy="off", registry=reg)
    ctx = WaveProfilingContext(
        run_id="run-1",
        wave_index=1,
        wave_id="w1",
        ontology=base_ontology_dict,
    )
    assert await hooks_off.on_after_profiling(ctx) is None
    assert rec.last_request is None

    # 2. With policy 'suggest_only', hook fires and generates proposal
    hooks_on = get_pipeline_extension_hooks(policy="suggest_only", registry=reg)
    proposal = await hooks_on.on_after_profiling(ctx)
    assert proposal is not None
    assert rec.last_request is not None
    assert rec.last_request.injection_point == "IP-2"


@pytest.mark.asyncio
async def test_ip2_advisory_failure_recorded_without_raising(
    base_ontology_dict: dict[str, Any],
) -> None:
    reg = OntologyRecommenderRegistry()
    failing_rec = MockHookRecommender(should_fail=True)
    reg.register_recommender(failing_rec)

    hooks = get_pipeline_extension_hooks(policy="suggest_only", registry=reg)
    ctx = WaveProfilingContext(
        run_id="run-1",
        wave_index=1,
        wave_id="w1",
        ontology=base_ontology_dict,
    )
    # Advisory failure should return None and log in recommender_errors
    proposal = await hooks.on_after_profiling(ctx)
    assert proposal is None
    assert len(hooks.recommender_errors) == 1
    assert "timeout" in hooks.recommender_errors[0]


@pytest.mark.asyncio
async def test_ip3_blocking_hold_violation(
    mock_registry: tuple[OntologyRecommenderRegistry, MockHookRecommender],
    base_ontology_dict: dict[str, Any],
) -> None:
    reg, rec = mock_registry
    hooks = get_pipeline_extension_hooks(policy="steward_review", registry=reg)

    ctx = GateViolationContext(
        run_id="run-1",
        wave_index=1,
        wave_id="w1",
        ontology=base_ontology_dict,
        g1_violations=[{"rule": "DV-G4-001", "target": "hub_supplier_delivery"}],
    )
    proposal = await hooks.on_ontology_violation(ctx)
    assert proposal is not None
    assert rec.last_request is not None
    assert rec.last_request.injection_point == "IP-3"
    assert rec.last_request.mode == "HOLD_RESOLUTION"
    assert "violations" in rec.last_request.focus


@pytest.mark.asyncio
async def test_ip3_fail_fast_when_no_recommender(
    base_ontology_dict: dict[str, Any],
) -> None:
    empty_reg = OntologyRecommenderRegistry()
    hooks = get_pipeline_extension_hooks(policy="steward_review", registry=empty_reg)

    ctx = GateViolationContext(
        run_id="run-1",
        wave_index=1,
        wave_id="w1",
        ontology=base_ontology_dict,
    )
    with pytest.raises(OntologyRecommenderUnavailableError):
        await hooks.on_ontology_violation(ctx)


@pytest.mark.asyncio
async def test_ip4_unresolved_mentions(
    mock_registry: tuple[OntologyRecommenderRegistry, MockHookRecommender],
    base_ontology_dict: dict[str, Any],
) -> None:
    reg, rec = mock_registry
    hooks = get_pipeline_extension_hooks(policy="suggest_only", registry=reg)

    ctx = UnresolvedMentionsContext(
        run_id="run-1",
        wave_index=1,
        wave_id="w1",
        ontology=base_ontology_dict,
        unresolved_mentions=[{"mention_text": "Supplier Delivery Slip", "char_span": [10, 32]}],
    )
    proposal = await hooks.on_unresolved_mentions(ctx)
    assert proposal is not None
    assert rec.last_request is not None
    assert rec.last_request.injection_point == "IP-4"
    assert "unresolved_mentions" in rec.last_request.focus


def test_ip5_business_context_injection() -> None:
    hooks = get_pipeline_extension_hooks()
    docs = [{"doc_id": "glossary-1", "title": "Logistics Terms"}]
    ctx_text = "FIBO Supply Chain Reference"

    norm_docs, norm_ctx = hooks.inject_business_context(docs, ctx_text)
    assert len(norm_docs) == 1
    assert norm_docs[0]["doc_id"] == "glossary-1"
    assert norm_ctx == "FIBO Supply Chain Reference"


def test_ip6_explorer_review_artifact_formatting() -> None:
    hooks = get_pipeline_extension_hooks()
    proposal = OntologyProposal(
        proposal_id="prop-123",
        recommender_id="rec-abc",
        delta=OntologyDelta(delta_id="delta-xyz", operations=[]),
        confidence_score=0.95,
    )
    art = hooks.format_review_artifacts(proposal)
    assert art["artifact_kind"] == "ontology_proposal"
    assert art["proposal_id"] == "prop-123"
    assert art["confidence_score"] == 0.95
    assert "delta" in art
