"""Unit tests for OntologyPolicy engine."""

from __future__ import annotations

import pytest

from opendv_contracts import (
    InputValidationError,
    OntologyDelta,
    OntologyProposal,
    OntologyRecommendationRequest,
    OntologyRecommenderUnavailableError,
    RecommenderCapabilities,
)
from opendv_service.ontology.policy import OntologyPolicy
from opendv_service.ontology.registry import OntologyRecommenderRegistry


class DummyRecommender:
    recommender_id = "test-rec"
    recommender_version = "1.0.0"
    capabilities = RecommenderCapabilities()

    async def recommend(self, req: OntologyRecommendationRequest) -> OntologyProposal:
        return OntologyProposal(
            proposal_id="p1",
            delta=OntologyDelta(delta_id="d1", operations=[]),
            confidence_score=0.95,
        )


def test_policy_defaults_and_normalization() -> None:
    pol = OntologyPolicy()
    assert pol.mode == "off"
    assert not pol.is_active

    assert OntologyPolicy.from_value("off").mode == "off"
    assert OntologyPolicy.from_value("disabled").mode == "off"
    assert OntologyPolicy.from_value(None).mode == "off"
    assert OntologyPolicy.from_value("").mode == "off"
    assert OntologyPolicy.from_value("suggest_only").mode == "suggest_only"
    assert OntologyPolicy.from_value("steward_review").mode == "steward_review"

    # auto_accept_above with threshold
    pol_auto = OntologyPolicy.from_value("auto_accept_above:0.85")
    assert pol_auto.mode == "auto_accept_above"
    assert pol_auto.auto_accept_threshold == 0.85
    assert pol_auto.is_active


def test_policy_invalid_mode_raises() -> None:
    with pytest.raises(InputValidationError):
        OntologyPolicy.from_value("unknown_mode")


def test_policy_invalid_threshold_raises() -> None:
    with pytest.raises(InputValidationError):
        OntologyPolicy.from_value("auto_accept_above:invalid_number")


def test_policy_hook_invocation_checks() -> None:
    pol_off = OntologyPolicy(mode="off")
    assert not pol_off.should_invoke_ip2()
    assert not pol_off.should_invoke_ip3()
    assert not pol_off.should_invoke_ip4()
    assert not pol_off.should_populate_hold_options()

    pol_suggest = OntologyPolicy(mode="suggest_only")
    assert pol_suggest.should_invoke_ip2()
    assert pol_suggest.should_invoke_ip3()
    assert pol_suggest.should_invoke_ip4()
    assert not pol_suggest.should_populate_hold_options()

    pol_review = OntologyPolicy(mode="steward_review")
    assert pol_review.should_invoke_ip2()
    assert pol_review.should_invoke_ip3()
    assert pol_review.should_populate_hold_options()


def test_policy_should_auto_accept() -> None:
    proposal_high = OntologyProposal(
        proposal_id="p1",
        delta=OntologyDelta(delta_id="d1", operations=[]),
        confidence_score=0.95,
    )
    proposal_low = OntologyProposal(
        proposal_id="p2",
        delta=OntologyDelta(delta_id="d2", operations=[]),
        confidence_score=0.80,
    )

    pol_review = OntologyPolicy(mode="steward_review")
    assert not pol_review.should_auto_accept(proposal_high)

    pol_auto = OntologyPolicy(mode="auto_accept_above", auto_accept_threshold=0.90)
    assert pol_auto.should_auto_accept(proposal_high)
    assert not pol_auto.should_auto_accept(proposal_low)


def test_policy_validate_availability() -> None:
    empty_registry = OntologyRecommenderRegistry()

    # Off mode does not validate or raise
    pol_off = OntologyPolicy(mode="off")
    pol_off.validate_availability(empty_registry)  # should not raise

    # Active mode without recommender raises ONTOLOGY_RECOMMENDER_UNAVAILABLE
    pol_active = OntologyPolicy(mode="suggest_only")
    with pytest.raises(OntologyRecommenderUnavailableError) as exc_info:
        pol_active.validate_availability(empty_registry)
    assert "no OntologyRecommender" in str(exc_info.value)
    assert exc_info.value.exit_code == 2
    assert exc_info.value.http_status == 422

    # Active mode with registered recommender passes
    empty_registry.register_recommender(DummyRecommender())
    pol_active.validate_availability(empty_registry)  # passes
