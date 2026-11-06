"""Unit tests for OntologyRecommender SPI port and recommendation request DTOs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from opendv_contracts import (
    AddConceptOp,
    OntologyDelta,
    OntologyProposal,
    OntologyRecommendationRequest,
    OntologyRecommender,
    RecommenderCapabilities,
)


class DummyValidRecommender:
    """Class conforming to OntologyRecommender protocol."""

    recommender_id: str = "test-recommender"
    recommender_version: str = "1.0.0"
    capabilities: RecommenderCapabilities = RecommenderCapabilities()

    async def recommend(self, request: OntologyRecommendationRequest) -> OntologyProposal:
        return OntologyProposal(
            proposal_id="prop-001",
            delta=OntologyDelta(delta_id="delta-001", operations=[]),
        )


class DummyInvalidRecommender:
    """Class missing recommender_id and recommend method."""

    recommender_version: str = "1.0.0"


def test_recommender_protocol_runtime_checkable() -> None:
    """Verify runtime_checkable protocol validation."""
    valid_inst = DummyValidRecommender()
    assert isinstance(valid_inst, OntologyRecommender)

    invalid_inst = DummyInvalidRecommender()
    assert not isinstance(invalid_inst, OntologyRecommender)


def test_recommender_capabilities_defaults() -> None:
    """Verify RecommenderCapabilities default attributes."""
    caps = RecommenderCapabilities()
    assert "BOOTSTRAP" in caps.supported_modes
    assert "DELTA" in caps.supported_modes
    assert "HOLD_RESOLUTION" in caps.supported_modes
    assert "profiles" in caps.supported_input_kinds
    assert not caps.needs_llm
    assert caps.clearance_level == "INTERNAL"


def test_ontology_recommendation_request_frozen_immutability() -> None:
    """Verify OntologyRecommendationRequest is frozen read-only."""
    req = OntologyRecommendationRequest(
        request_id="req-123",
        mode="DELTA",
        domain_namespace="logistics",
        profiles=[{"table": "raw_orders", "row_count": 100}],
        injection_point="IP-2",
    )
    assert req.request_id == "req-123"
    assert req.mode == "DELTA"
    assert req.clearance_level == "INTERNAL"
    assert req.injection_point == "IP-2"

    with pytest.raises(ValidationError):
        req.mode = "BOOTSTRAP"  # Frozen forbids mutation


def test_ontology_recommendation_request_extra_forbidden() -> None:
    """Verify extra attributes are strictly forbidden."""
    with pytest.raises(ValidationError):
        OntologyRecommendationRequest(
            request_id="req-123",
            unrecognized_field="illegal",  # extra='forbid'
        )


def test_ontology_recommendation_request_serialization() -> None:
    """Verify model_dump and model_validate round-trip."""
    req = OntologyRecommendationRequest(
        request_id="req-456",
        mode="BOOTSTRAP",
        domain_namespace="finance",
        base_ontology=None,
        samples={"transactions": [{"id": "tx1", "amount": 100}]},
        injection_point="IP-1",
    )
    dumped = req.model_dump(mode="json")
    assert dumped["request_id"] == "req-456"
    assert dumped["mode"] == "BOOTSTRAP"
    assert dumped["injection_point"] == "IP-1"

    restored = OntologyRecommendationRequest.model_validate(dumped)
    assert restored == req
