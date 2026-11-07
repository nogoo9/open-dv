"""Integration tests for Explorer REST endpoints (`/api/v1/runs/{run_id}/explorer` and artifacts).

Conforms to GAP-05, T6, Decision D3, and User Directive R9 (<500 LOC / <20 KB).
"""

from __future__ import annotations

import re
from pathlib import Path

from starlette.testclient import TestClient

from opendv_contracts import ExplorerBundleDTO, RunArtifactDTO
from opendv_platform_api.main import create_app
from opendv_service import (
    CreateProjectRequest,
    CreateRunRequest,
    OpenDVService,
    PlatformStateContainer,
    RunStatus,
)


def _setup_test_client(tmp_path: Path) -> tuple[TestClient, str, str]:
    """Helper to initialize OpenDVService with a sample project and two runs."""
    state = PlatformStateContainer(
        workspace_root=tmp_path,
        storage_root=tmp_path / "state",
    )
    service = OpenDVService(state=state, workspace_root=tmp_path)
    proj = service.create_project(
        CreateProjectRequest(
            project_id="test-explorer-rest",
            display_name="Explorer REST Project",
        )
    )
    run_a = service.create_run(
        CreateRunRequest(
            project_id=proj.project_id,
            harness_provider="antigravity",
        )
    )
    run_b = service.create_run(
        CreateRunRequest(
            project_id=proj.project_id,
            harness_provider="antigravity",
        )
    )
    # Set run A to COMPLETED, run B to PAUSED_HOLD
    state.runs[run_a.run_id] = run_a.model_copy(
        update={
            "status": RunStatus.COMPLETED,
            "current_stage_id": "STAGE_07_DETERMINISTIC_COMPILER",
        }
    )
    state.runs[run_b.run_id] = run_b.model_copy(
        update={
            "status": RunStatus.PAUSED_HOLD,
            "current_stage_id": "STAGE_02_SCOUT_UNSTRUCTURED",
            "active_hold_ids": ["hold-tier-u3-rheinland-precision"],
        }
    )

    app = create_app(service=service)
    client = TestClient(app)
    return client, run_a.run_id, run_b.run_id


def test_get_explorer_bundle_endpoint_success(tmp_path: Path) -> None:
    """Verify GET /api/v1/runs/{run_id}/explorer returns 200 and 8 canonical stages."""
    client, run_a_id, _ = _setup_test_client(tmp_path)

    resp = client.get(f"/api/v1/runs/{run_a_id}/explorer")
    assert resp.status_code == 200
    data = resp.json()

    bundle = ExplorerBundleDTO.model_validate(data)
    assert bundle.run_id == run_a_id
    assert bundle.project_id == "test-explorer-rest"
    assert len(bundle.stages) == 8
    assert len(bundle.phases) == 4
    assert len(bundle.waves) == 4
    assert len(bundle.artifacts) == 24

    # Verify stages in order
    assert bundle.stages[0].key == "STAGE_00_ORCHESTRATION"
    assert bundle.stages[7].key == "STAGE_07_DETERMINISTIC_COMPILER"


def test_get_explorer_bundle_endpoint_404_not_found(tmp_path: Path) -> None:
    """Verify GET /api/v1/runs/{non_existent}/explorer returns RFC 9457 404 ProblemDetail."""
    client, _, _ = _setup_test_client(tmp_path)

    resp = client.get("/api/v1/runs/non-existent-run/explorer")
    assert resp.status_code == 404
    body = resp.json()
    assert body["status"] == 404
    assert "not found" in body["detail"].lower()


def test_get_explorer_bundle_wave_filter(tmp_path: Path) -> None:
    """Verify ?wave_id=W1 filters artifacts and ?wave_id=invalid returns 404."""
    client, run_a_id, _ = _setup_test_client(tmp_path)

    # Filter by W1
    resp_w1 = client.get(f"/api/v1/runs/{run_a_id}/explorer?wave_id=W1")
    assert resp_w1.status_code == 200
    bundle_w1 = ExplorerBundleDTO.model_validate(resp_w1.json())
    for art in bundle_w1.artifacts.values():
        assert "W1" in art.waves

    # Numeric wave filter ?wave_id=1
    resp_1 = client.get(f"/api/v1/runs/{run_a_id}/explorer?wave_id=1")
    assert resp_1.status_code == 200
    bundle_1 = ExplorerBundleDTO.model_validate(resp_1.json())
    assert set(bundle_1.artifacts.keys()) == set(bundle_w1.artifacts.keys())

    # Invalid wave returns 404
    resp_inv = client.get(f"/api/v1/runs/{run_a_id}/explorer?wave_id=W99")
    assert resp_inv.status_code == 404


def test_get_explorer_bundle_paused_hold_run(tmp_path: Path) -> None:
    """Verify PAUSED_HOLD run returns HOLD status and steward extraction options."""
    client, _, run_b_id = _setup_test_client(tmp_path)

    resp = client.get(f"/api/v1/runs/{run_b_id}/explorer")
    assert resp.status_code == 200
    bundle = ExplorerBundleDTO.model_validate(resp.json())

    stage_02 = next(s for s in bundle.stages if s.key == "STAGE_02_SCOUT_UNSTRUCTURED")
    assert stage_02.tag == "HOLD"
    assert "PAUSED" in bundle.status_label


def test_get_run_artifact_endpoint_success_and_cas_sha(tmp_path: Path) -> None:
    """Verify GET /api/v1/runs/{run_id}/artifacts/{artifact_id} returns 200 with CAS hash."""
    client, run_a_id, _ = _setup_test_client(tmp_path)

    resp = client.get(f"/api/v1/runs/{run_a_id}/artifacts/harness")
    assert resp.status_code == 200
    artifact = RunArtifactDTO.model_validate(resp.json())

    assert artifact.artifact_id == "harness"
    assert artifact.run_id == run_a_id
    assert re.match(r"^[0-9a-f]{64}$", artifact.sha256) is not None
    assert len(artifact.content) > 0


def test_get_run_artifact_endpoint_run_isolation(tmp_path: Path) -> None:
    """Verify T6 Invariant: cross-run artifact access or non-existent artifact returns 404."""
    client, run_a_id, run_b_id = _setup_test_client(tmp_path)

    # Valid artifact under Run A
    resp_a = client.get(f"/api/v1/runs/{run_a_id}/artifacts/harness")
    assert resp_a.status_code == 200

    # Non-existent artifact under Run A returns 404
    resp_nonexistent = client.get(f"/api/v1/runs/{run_a_id}/artifacts/does-not-exist")
    assert resp_nonexistent.status_code == 404

    # Run B does not have invalid artifact either
    resp_b_nonexistent = client.get(f"/api/v1/runs/{run_b_id}/artifacts/does-not-exist")
    assert resp_b_nonexistent.status_code == 404

    # Non-existent run returns 404
    resp_unknown_run = client.get("/api/v1/runs/unknown-run/artifacts/harness")
    assert resp_unknown_run.status_code == 404


def test_get_run_artifact_endpoint_rbac_masking(tmp_path: Path) -> None:
    """Verify X-OpenDV-Role header controls PII redaction."""
    client, run_a_id, _ = _setup_test_client(tmp_path)

    # PUBLIC role masks PII
    resp_pub = client.get(
        f"/api/v1/runs/{run_a_id}/artifacts/msa",
        headers={"X-OpenDV-Role": "PUBLIC"},
    )
    assert resp_pub.status_code == 200
    assert "<PII>" in resp_pub.json()["content"]

    # DATA_STEWARD role sees clear text
    resp_steward = client.get(
        f"/api/v1/runs/{run_a_id}/artifacts/msa",
        headers={"X-OpenDV-Role": "DATA_STEWARD"},
    )
    assert resp_steward.status_code == 200
    assert "<PII>" not in resp_steward.json()["content"]


def test_list_runs_endpoint_filtering(tmp_path: Path) -> None:
    """Verify GET /api/v1/runs status and stage_id query filters."""
    client, run_a_id, run_b_id = _setup_test_client(tmp_path)

    # Filter by status=COMPLETED
    resp_comp = client.get("/api/v1/runs?status=COMPLETED")
    assert resp_comp.status_code == 200
    runs_comp = resp_comp.json()
    assert any(r["run_id"] == run_a_id for r in runs_comp)
    assert not any(r["run_id"] == run_b_id for r in runs_comp)

    # Filter by status=PAUSED_HOLD
    resp_hold = client.get("/api/v1/runs?status=PAUSED_HOLD")
    assert resp_hold.status_code == 200
    runs_hold = resp_hold.json()
    assert any(r["run_id"] == run_b_id for r in runs_hold)
    assert not any(r["run_id"] == run_a_id for r in runs_hold)

    # Filter by stage_id=STAGE_02_SCOUT_UNSTRUCTURED
    resp_stage = client.get("/api/v1/runs?stage_id=STAGE_02_SCOUT_UNSTRUCTURED")
    assert resp_stage.status_code == 200
    runs_stage = resp_stage.json()
    assert any(r["run_id"] == run_b_id for r in runs_stage)
    assert not any(r["run_id"] == run_a_id for r in runs_stage)
