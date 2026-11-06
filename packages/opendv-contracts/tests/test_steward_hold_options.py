"""Tests for StewardHoldOption DTOs, discriminated unions, and JSON Schema validation (ONT-04)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import TypeAdapter, ValidationError

from opendv_contracts.dtos.ontology import (
    AcceptOntologyDeltaOption,
    AddConceptOp,
    CustomOntologyDeltaOption,
    GeneralHoldOption,
    OntologyDelta,
    RejectOntologyDeltaOption,
    StewardHoldOption,
)


@pytest.fixture
def steward_hold_schema() -> dict:
    repo_root = Path(__file__).resolve().parents[3]
    schema_path = repo_root / "schemas" / "v1" / "steward-hold-option.schema.json"
    assert schema_path.is_file(), f"Schema not found at {schema_path}"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _sample_delta() -> OntologyDelta:
    return OntologyDelta(
        delta_id="delta-hold-test-01",
        parent_sha256="a" * 64,
        domain_namespace="logistics_and_commerce",
        operations=[
            AddConceptOp(
                op_id="op-sample-01",
                concept_uri="urn:ontology:entity:supplier_delivery",
                concept_name="CBC_SUPPLIER_DELIVERY",
                elm_archetype="WHAT_RESOURCE_EVENT",
                definition="Consignment delivery from supplier.",
                target_hub="hub_supplier_delivery",
            )
        ],
    )


def test_accept_ontology_delta_option_roundtrip(steward_hold_schema: dict) -> None:
    delta = _sample_delta()
    opt = AcceptOntologyDeltaOption(
        delta=delta,
        delta_sha256="b" * 64,
        steward_note="Steward approved supplier delivery addition.",
        accepted_op_ids=["op-sample-01"],
    )
    assert opt.kind == "accept_ontology_delta"
    data = opt.model_dump(mode="json")
    assert data["kind"] == "accept_ontology_delta"
    assert data["delta_sha256"] == "b" * 64
    assert data["accepted_op_ids"] == ["op-sample-01"]

    # Validate against JSON schema
    jsonschema.validate(instance=data, schema=steward_hold_schema)

    adapter = TypeAdapter(StewardHoldOption)
    deserialized = adapter.validate_python(data)
    assert isinstance(deserialized, AcceptOntologyDeltaOption)
    assert deserialized.delta.delta_id == delta.delta_id
    assert deserialized.steward_note == opt.steward_note


def test_reject_ontology_delta_option_roundtrip(steward_hold_schema: dict) -> None:
    opt = RejectOntologyDeltaOption(
        reason="Does not align with domain architecture guidelines.",
        steward_note="Please restructure as an attribute instead of a new hub.",
        rejected_op_ids=["op-sample-01"],
    )
    assert opt.kind == "reject_ontology_delta"
    data = opt.model_dump(mode="json")
    assert data["kind"] == "reject_ontology_delta"
    assert data["reason"] == "Does not align with domain architecture guidelines."
    assert data["rejected_op_ids"] == ["op-sample-01"]

    jsonschema.validate(instance=data, schema=steward_hold_schema)

    adapter = TypeAdapter(StewardHoldOption)
    deserialized = adapter.validate_python(data)
    assert isinstance(deserialized, RejectOntologyDeltaOption)
    assert deserialized.reason == opt.reason


def test_custom_ontology_delta_option_roundtrip(steward_hold_schema: dict) -> None:
    delta = _sample_delta()
    opt = CustomOntologyDeltaOption(
        custom_delta=delta,
        steward_note="Adjusted elm_archetype to resource event.",
    )
    assert opt.kind == "custom_ontology_delta"
    data = opt.model_dump(mode="json")
    assert data["kind"] == "custom_ontology_delta"

    jsonschema.validate(instance=data, schema=steward_hold_schema)

    adapter = TypeAdapter(StewardHoldOption)
    deserialized = adapter.validate_python(data)
    assert isinstance(deserialized, CustomOntologyDeltaOption)
    assert deserialized.custom_delta.delta_id == delta.delta_id


def test_general_hold_option_roundtrip(steward_hold_schema: dict) -> None:
    opt = GeneralHoldOption(
        action="APPROVE",
        params={"candidate_key": "CUST-999"},
        steward_note="Approved manual entity match.",
    )
    assert opt.kind == "general_hold"
    data = opt.model_dump(mode="json")
    assert data["kind"] == "general_hold"
    assert data["action"] == "APPROVE"
    assert data["params"] == {"candidate_key": "CUST-999"}

    jsonschema.validate(instance=data, schema=steward_hold_schema)

    adapter = TypeAdapter(StewardHoldOption)
    deserialized = adapter.validate_python(data)
    assert isinstance(deserialized, GeneralHoldOption)
    assert deserialized.action == "APPROVE"
    assert deserialized.params == {"candidate_key": "CUST-999"}


def test_discriminated_union_invalid_kind() -> None:
    adapter = TypeAdapter(StewardHoldOption)
    with pytest.raises(ValidationError) as exc:
        adapter.validate_python({"kind": "unsupported_hold_kind", "action": "SKIP"})
    assert "kind" in str(exc.value)
