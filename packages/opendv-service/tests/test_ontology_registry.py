"""Unit tests for OntologyRecommenderRegistry."""

from __future__ import annotations

import pytest

from opendv_contracts import (
    OntologyDelta,
    OntologyProposal,
    OntologyRecommendationRequest,
    OntologyRecommenderUnavailableError,
    RecommenderCapabilities,
)
from opendv_service.ontology.registry import (
    OntologyRecommenderRegistry,
    get_ontology_recommender_registry,
)


class MockRecommender:
    def __init__(self, recommender_id: str = "mock-recommender", version: str = "1.0.0") -> None:
        self.recommender_id = recommender_id
        self.recommender_version = version
        self.capabilities = RecommenderCapabilities()

    async def recommend(self, request: OntologyRecommendationRequest) -> OntologyProposal:
        return OntologyProposal(
            proposal_id="prop-test",
            delta=OntologyDelta(delta_id="delta-test", operations=[]),
        )


def test_registry_registration_and_lookup() -> None:
    reg = OntologyRecommenderRegistry()
    assert not reg.has_recommender()

    rec = MockRecommender("alpha-recommender")
    reg.register_recommender(rec)

    assert reg.has_recommender()
    assert reg.has_recommender("alpha-recommender")
    assert not reg.has_recommender("beta-recommender")

    retrieved = reg.get_recommender("alpha-recommender")
    assert retrieved.recommender_id == "alpha-recommender"

    # Default lookup when 1 recommender registered
    default_rec = reg.get_recommender()
    assert default_rec.recommender_id == "alpha-recommender"


def test_registry_lookup_missing_raises_unavailable() -> None:
    reg = OntologyRecommenderRegistry()
    with pytest.raises(OntologyRecommenderUnavailableError) as exc_info:
        reg.get_recommender()
    assert "No OntologyRecommender module is registered" in str(exc_info.value)
    assert exc_info.value.exit_code == 2
    assert exc_info.value.http_status == 422


def test_registry_lookup_specific_missing_raises_unavailable() -> None:
    reg = OntologyRecommenderRegistry()
    rec = MockRecommender("existing-rec")
    reg.register_recommender(rec)

    with pytest.raises(OntologyRecommenderUnavailableError) as exc_info:
        reg.get_recommender("nonexistent-rec")
    assert "is not installed" in str(exc_info.value)
    assert exc_info.value.exit_code == 2
    assert exc_info.value.http_status == 422


def test_registry_rejects_invalid_recommender() -> None:
    reg = OntologyRecommenderRegistry()
    with pytest.raises(TypeError):
        reg.register_recommender("not-a-recommender")


def test_registry_reset() -> None:
    reg = OntologyRecommenderRegistry()
    reg.register_recommender(MockRecommender("rec-1"))
    assert reg.has_recommender()

    reg.reset()
    assert not reg.has_recommender()
    assert len(reg.list_recommenders()) == 0


def test_global_registry_singleton() -> None:
    reg1 = get_ontology_recommender_registry()
    reg2 = get_ontology_recommender_registry()
    assert reg1 is reg2
