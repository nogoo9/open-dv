"""Integration tests for `opendv ontology` CLI command group.

Tests standalone in-process execution of subcommands:
- `suggest` (exit code 2 without recommender; exit code 0 when available)
- `validate-delta` (exit code 0 for valid; exit code 1 for invalid delta)
- `diff` (exit code 0 structural diff)
- `accept` (exit code 0 and emits resulting Graph A vN+1)
- `reject` (exit code 0 records steward rejection)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from opendv_cli import app_main
from opendv_contracts import (
    AddConceptOp,
    OntologyDelta,
    OntologyProposal,
)
from opendv_service.ontology.registry import clear_recommenders, register_recommender


@pytest.fixture(autouse=True)
def _clean_recommender_registry():
    clear_recommenders()
    yield
    clear_recommenders()


@pytest.fixture
def base_ontology_file(tmp_path: Path) -> Path:
    p = tmp_path / "base_ontology.yaml"
    data = {
        "ontology_version": "1.0.0",
        "domain_namespace": "logistics",
        "enterprise_owner": "Apex Logistics",
        "standards_references": ["ISO 8601"],
        "canonical_business_concepts": [
            {
                "concept_uri": "urn:ontology:entity:depot",
                "concept_name": "Depot",
                "elm_archetype": "CORE_BUSINESS_CONCEPT",
                "definition": "A distribution center depot.",
                "target_hub": "hub_depot",
            },
            {
                "concept_uri": "urn:ontology:entity:vehicle",
                "concept_name": "Vehicle",
                "elm_archetype": "CORE_BUSINESS_CONCEPT",
                "definition": "A transport fleet vehicle.",
                "target_hub": "hub_vehicle",
            },
        ],
        "canonical_relationships": [],
        "reference_enumerations": [],
    }
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


@pytest.fixture
def valid_delta_file(tmp_path: Path) -> Path:
    p = tmp_path / "valid_delta.json"
    data = {
        "delta_id": "delta-valid-01",
        "operations": [
            {
                "op_id": "op-warehouse-01",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:entity:warehouse",
                "concept_name": "Warehouse",
                "elm_archetype": "CORE_BUSINESS_CONCEPT",
                "definition": "A storage facility.",
                "target_hub": "hub_warehouse",
            }
        ],
    }
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return p


@pytest.fixture
def invalid_delta_file(tmp_path: Path) -> Path:
    p = tmp_path / "invalid_delta.json"
    data = {
        "delta_id": "delta-invalid-01",
        "operations": [
            {
                "op_id": "op-unvetted-01",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:provisional:entity:unvetted",
                "concept_name": "Unvetted",
                "elm_archetype": "CORE_BUSINESS_CONCEPT",
                "definition": "Provisional concept.",
                "target_hub": "hub_unvetted",
            }
        ],
    }
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return p


def test_ontology_suggest_without_recommender_fails(
    base_ontology_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    clear_recommenders()
    rc = app_main(
        [
            "ontology",
            "suggest",
            "--base",
            str(base_ontology_file),
            "--json",
        ]
    )
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out.get("error_code") == "ONTOLOGY_RECOMMENDER_UNAVAILABLE"


def test_ontology_suggest_with_registered_recommender(
    base_ontology_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def mock_rec(req):
        return OntologyProposal(
            proposal_id="prop-test-cli",
            delta=OntologyDelta(
                delta_id="delta-cli-01",
                operations=[
                    AddConceptOp(
                        op_id="op-driver-01",
                        concept_uri="urn:ontology:entity:driver",
                        concept_name="Driver",
                        elm_archetype="CORE_BUSINESS_CONCEPT",
                        definition="A commercial vehicle operator.",
                        target_hub="hub_driver",
                    )
                ],
            ),
            rationale="Discovered Driver concept.",
        )

    register_recommender(mock_rec, "mock-cli-rec")
    out_file = tmp_path / "suggest_output.json"

    rc = app_main(
        [
            "ontology",
            "suggest",
            "--base",
            str(base_ontology_file),
            "--recommender-id",
            "mock-cli-rec",
            "--output",
            str(out_file),
            "--json",
        ]
    )
    assert rc == 0
    assert out_file.exists()
    out = json.loads(capsys.readouterr().out)
    assert out.get("proposal_id") == "prop-test-cli"


def test_ontology_validate_delta_valid(
    base_ontology_file: Path,
    valid_delta_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = app_main(
        [
            "ontology",
            "validate-delta",
            "--base",
            str(base_ontology_file),
            "--delta",
            str(valid_delta_file),
            "--json",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out.get("valid") is True


def test_ontology_validate_delta_invalid(
    base_ontology_file: Path,
    invalid_delta_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = app_main(
        [
            "ontology",
            "validate-delta",
            "--base",
            str(base_ontology_file),
            "--delta",
            str(invalid_delta_file),
            "--json",
        ]
    )
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out.get("valid") is False
    assert any("ONT-EVO-002" in v for v in out.get("violations", []))


def test_ontology_diff(
    base_ontology_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target_p = tmp_path / "target_ontology.yaml"
    target_data = yaml.safe_load(base_ontology_file.read_text(encoding="utf-8"))
    target_data["canonical_business_concepts"].append(
        {
            "concept_uri": "urn:ontology:entity:trailer",
            "concept_name": "Trailer",
            "elm_archetype": "CORE_BUSINESS_CONCEPT",
            "definition": "A trailer.",
            "target_hub": "hub_trailer",
        }
    )
    target_p.write_text(yaml.safe_dump(target_data), encoding="utf-8")

    rc = app_main(
        [
            "ontology",
            "diff",
            "--base",
            str(base_ontology_file),
            "--target",
            str(target_p),
            "--json",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert "urn:ontology:entity:trailer" in out.get("added_concepts", [])


def test_ontology_accept(
    base_ontology_file: Path,
    valid_delta_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out_yaml = tmp_path / "result_graph_a.yaml"
    rc = app_main(
        [
            "ontology",
            "accept",
            "--base",
            str(base_ontology_file),
            "--delta",
            str(valid_delta_file),
            "--output",
            str(out_yaml),
            "--notes",
            "Accepted warehouse concept",
            "--json",
        ]
    )
    assert rc == 0
    assert out_yaml.exists()
    out = json.loads(capsys.readouterr().out)
    assert out.get("decision", {}).get("action") == "ACCEPT"
    assert out.get("resulting_ontology_version") == "1.1.0"


def test_ontology_reject(
    base_ontology_file: Path,
    valid_delta_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = app_main(
        [
            "ontology",
            "reject",
            "--base",
            str(base_ontology_file),
            "--proposal-id",
            "prop-1234",
            "--notes",
            "Rejected proposal",
            "--json",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out.get("decision", {}).get("action") == "REJECT"
