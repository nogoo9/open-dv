"""Integration tests for Ontology API routes and Explorer run artifacts.

Validates:
- GET /api/v1/runs/{run_id}/artifacts (returns 24 artifacts with CAS hash and RBAC redaction).
- POST /api/v1/ontology/suggestions (422 fail-fast when unavailable; 200 when registered).
- POST /api/v1/ontology/validate-delta (valid vs invalid delta under ONT-EVO evolution locks).
- POST /api/v1/ontology/diff (structural diff between two Graph A ontologies).
- POST /api/v1/ontology/decisions (ACCEPT and REJECT decisions with CAS lineage).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from opendv_contracts import (
    AddConceptOp,
    OntologyDelta,
    OntologyProposal,
)
from opendv_platform_api.main import create_app
from opendv_service import (
    CreateProjectRequest,
    CreateRunRequest,
    OpenDVService,
    PlatformStateContainer,
    RunStatus,
)
from opendv_service.ontology.registry import clear_recommenders, register_recommender


@pytest.fixture(autouse=True)
def _clean_recommender_registry():
    clear_recommenders()
    yield
    clear_recommenders()


@pytest.fixture
def sample_base_ontology() -> dict:
    return {
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
        "canonical_relationships": [
            {
                "relationship_uri": "urn:ontology:relationship:vehicle_assignment",
                "relationship_name": "VehicleAssignment",
                "target_link": "lnk_vehicle_assignment",
                "endpoints": [
                    "urn:ontology:entity:depot",
                    "urn:ontology:entity:vehicle",
                ],
                "grain_semantics": "Assignment of vehicle to depot.",
            }
        ],
        "reference_enumerations": [
            {
                "enum_uri": "urn:ontology:enum:vehicle_type",
                "target_reference_table": "ref_vehicle_type",
                "allowed_codes": ["VAN", "TRUCK", "TRAIN"],
            }
        ],
    }


@pytest.fixture
def test_setup(tmp_path: Path):
    state = PlatformStateContainer(
        workspace_root=tmp_path,
        storage_root=tmp_path / "state",
    )
    service = OpenDVService(state=state, workspace_root=tmp_path)
    proj = service.create_project(
        CreateProjectRequest(
            project_id="proj-ont-test",
            display_name="Ontology Test Project",
        )
    )
    run = service.create_run(
        CreateRunRequest(
            project_id=proj.project_id,
            harness_provider="antigravity",
        )
    )
    state.runs[run.run_id] = run.model_copy(
        update={
            "status": RunStatus.COMPLETED,
            "current_stage_id": "STAGE_07_DETERMINISTIC_COMPILER",
        }
    )
    app = create_app(service=service, workspace_root=tmp_path)
    client = TestClient(app)
    return client, run.run_id, tmp_path


def test_list_run_artifacts(test_setup):
    client, run_id, _ = test_setup
    resp = client.get(f"/api/v1/runs/{run_id}/artifacts", headers={"X-OpenDV-Role": "PUBLIC"})
    assert resp.status_code == 200
    artifacts = resp.json()
    assert isinstance(artifacts, list)
    assert len(artifacts) == 24
    for art in artifacts:
        assert "artifact_id" in art
        assert "sha256" in art
        assert len(art["sha256"]) == 64
        assert art["sha256"] == art["sha256"].lower()


def test_suggest_ontology_unavailable_recommender(test_setup):
    client, _, _ = test_setup
    # Invoke suggestion with an uninstalled / unavailable recommender
    req_body = {
        "mode": "BOOTSTRAP",
        "recommender_id": "nonexistent-recommender-12345",
    }
    resp = client.post("/api/v1/ontology/suggestions", json=req_body)
    assert resp.status_code == 422
    err_body = resp.json()
    assert err_body["type"] == "urn:opendv:ontology-recommender-unavailable"
    assert "Ontology Recommender Unavailable" in err_body["title"]


def test_suggest_ontology_with_registered_recommender(test_setup):
    client, _, _ = test_setup

    async def mock_rec(req):
        return OntologyProposal(
            proposal_id="prop-test-01",
            delta=OntologyDelta(
                delta_id="delta-mock-01",
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
            rationale="Discovered Driver concept in payroll records.",
        )

    register_recommender(mock_rec, "mock-plugin")

    req_body = {
        "mode": "DELTA",
        "recommender_id": "mock-plugin",
    }
    resp = client.post("/api/v1/ontology/suggestions", json=req_body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["proposal_id"] == "prop-test-01"
    assert len(data["delta"]["operations"]) == 1
    assert data["delta"]["operations"][0]["concept_name"] == "Driver"


def test_validate_ontology_delta(test_setup, sample_base_ontology):
    client, _, _ = test_setup

    # Valid additive delta
    valid_delta = {
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

    resp = client.post(
        "/api/v1/ontology/validate-delta",
        json={
            "base_ontology": sample_base_ontology,
            "delta": valid_delta,
        },
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["valid"] is True
    assert len(res["violations"]) == 0

    # Invalid delta with provisional URI (violates ONT-EVO-002)
    invalid_delta = {
        "delta_id": "delta-invalid-01",
        "operations": [
            {
                "op_id": "op-unvetted-01",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:provisional:entity:unvetted_entity",
                "concept_name": "Unvetted",
                "elm_archetype": "CORE_BUSINESS_CONCEPT",
                "definition": "A provisional entity.",
                "target_hub": "hub_unvetted",
            }
        ],
    }
    resp_inv = client.post(
        "/api/v1/ontology/validate-delta",
        json={
            "base_ontology": sample_base_ontology,
            "delta": invalid_delta,
        },
    )
    assert resp_inv.status_code == 200
    res_inv = resp_inv.json()
    assert res_inv["valid"] is False
    assert any("ONT-EVO-002" in v for v in res_inv["violations"])


def test_diff_ontology(test_setup, sample_base_ontology):
    client, _, _ = test_setup
    import copy

    target_ontology = copy.deepcopy(sample_base_ontology)
    target_ontology["canonical_business_concepts"].append(
        {
            "concept_uri": "urn:ontology:entity:trailer",
            "concept_name": "Trailer",
            "elm_archetype": "CORE_BUSINESS_CONCEPT",
            "definition": "An unpowered cargo container.",
            "target_hub": "hub_trailer",
        }
    )

    resp = client.post(
        "/api/v1/ontology/diff",
        json={
            "base_ontology": sample_base_ontology,
            "target_ontology": target_ontology,
        },
    )
    assert resp.status_code == 200
    diff = resp.json()
    assert "added_concepts" in diff
    assert "urn:ontology:entity:trailer" in diff["added_concepts"]


def test_ontology_decisions_reject_and_accept(test_setup, sample_base_ontology):
    client, _, _ = test_setup

    # 1. Reject decision
    reject_req = {
        "action": "REJECT",
        "proposal_id": "prop-to-reject-01",
        "base_ontology": sample_base_ontology,
        "decided_by": "lead-steward",
        "steward_notes": "Concept already captured in upstream CRM.",
    }
    resp_rej = client.post("/api/v1/ontology/decisions", json=reject_req)
    assert resp_rej.status_code == 200
    data_rej = resp_rej.json()
    assert data_rej["status"] == "COMPLETED"
    assert data_rej["decision"]["action"] == "REJECT"
    assert data_rej["decision"]["decided_by"] == "lead-steward"

    # 2. Accept valid delta
    valid_delta = {
        "delta_id": "delta-forklift-01",
        "operations": [
            {
                "op_id": "op-forklift-01",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:entity:forklift",
                "concept_name": "Forklift",
                "elm_archetype": "CORE_BUSINESS_CONCEPT",
                "definition": "Depot warehouse equipment.",
                "target_hub": "hub_forklift",
            }
        ],
    }
    accept_req = {
        "action": "ACCEPT",
        "proposal_id": "prop-to-accept-01",
        "base_ontology": sample_base_ontology,
        "delta": valid_delta,
        "decided_by": "lead-steward",
        "steward_notes": "Accepted additive concept.",
    }
    resp_acc = client.post("/api/v1/ontology/decisions", json=accept_req)
    assert resp_acc.status_code == 200
    data_acc = resp_acc.json()
    assert data_acc["status"] == "COMPLETED"
    assert data_acc["decision"]["action"] == "ACCEPT"
    assert data_acc["resulting_ontology_yaml"] is not None
    assert data_acc["resulting_ontology_sha256"] is not None
    assert len(data_acc["resulting_ontology_sha256"]) == 64
    assert data_acc["resulting_ontology_version"] == "1.1.0"
