"""Tests for GraphAOntology CAS lineage fields and backward compatibility (ONT-01)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    GraphAOntology,
)


def _sample_cbc() -> CanonicalBusinessConcept:
    return CanonicalBusinessConcept(
        concept_uri="urn:ontology:entity:customer",
        concept_name="Customer",
        elm_archetype="CORE_PARTY",
        definition="An individual or corporate customer receiving services.",
        target_hub="hub_customer",
    )


def test_graph_a_lineage_defaults_backwards_compatible() -> None:
    """Existing Graph A files without parent_sha256 or decision_refs pass validation."""
    data = {
        "ontology_version": "1.0.0",
        "domain_namespace": "crm",
        "enterprise_owner": "Data Governance Committee",
        "canonical_business_concepts": [_sample_cbc().model_dump()],
    }
    ont = GraphAOntology.model_validate(data)
    assert ont.ontology_version == "1.0.0"
    assert ont.parent_sha256 is None
    assert ont.decision_refs == []


def test_graph_a_lineage_valid_sha256_and_decision_refs() -> None:
    """Evolved Graph A carrying parent SHA-256 and steward decision IDs validates cleanly."""
    valid_sha = "a" * 64
    data = {
        "ontology_version": "1.1.0",
        "parent_sha256": valid_sha,
        "decision_refs": ["DEC-2026-001", "DEC-2026-002"],
        "domain_namespace": "crm",
        "enterprise_owner": "Data Governance Committee",
        "canonical_business_concepts": [_sample_cbc().model_dump()],
    }
    ont = GraphAOntology.model_validate(data)
    assert ont.ontology_version == "1.1.0"
    assert ont.parent_sha256 == valid_sha
    assert ont.decision_refs == ["DEC-2026-001", "DEC-2026-002"]


def test_graph_a_lineage_invalid_sha256_rejected() -> None:
    """Invalid parent_sha256 formats (short, non-hex, uppercase) fail regex validation."""
    base_data = {
        "ontology_version": "1.1.0",
        "domain_namespace": "crm",
        "enterprise_owner": "Data Governance Committee",
        "canonical_business_concepts": [_sample_cbc().model_dump()],
    }

    # Too short
    with pytest.raises(ValidationError):
        GraphAOntology.model_validate({**base_data, "parent_sha256": "abc123"})

    # Non-hex characters
    with pytest.raises(ValidationError):
        GraphAOntology.model_validate({**base_data, "parent_sha256": "g" * 64})

    # Uppercase hex rejected by pattern ^[a-f0-9]{64}$
    with pytest.raises(ValidationError):
        GraphAOntology.model_validate({**base_data, "parent_sha256": ("A" * 64)})


def test_graph_a_strict_forbid_extra() -> None:
    """StrictGraphAModel forbids undeclared properties."""
    data = {
        "ontology_version": "1.0.0",
        "domain_namespace": "crm",
        "enterprise_owner": "Data Governance Committee",
        "canonical_business_concepts": [_sample_cbc().model_dump()],
        "extra_field": "disallowed",
    }
    with pytest.raises(ValidationError):
        GraphAOntology.model_validate(data)
