"""Integration tests for run creation options (GAP-07: initial_contract and wave_id)."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

from opendv_cli.executor import LocalExecutor
from opendv_contracts import LifecycleStage
from opendv_platform_api import PlatformStateContainer, create_app
from opendv_testkit import seed_canonical_pe_demo


@pytest.fixture
def test_env(tmp_path: Path) -> tuple[TestClient, Path]:
    """Provide a TestClient connected to a test workspace and state container."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    state = PlatformStateContainer(
        workspace_root=workspace_root,
        storage_root=workspace_root / ".opendv_state",
    )
    seed_canonical_pe_demo(state)
    app = create_app(platform_state=state, workspace_root=workspace_root)
    client = TestClient(app)
    return client, workspace_root


def test_create_run_default_lifecycle_cold_start(test_env: tuple[TestClient, Path]) -> None:
    """Verify default run creation sets COLD_START_N1 and has_initial_contract=False."""
    client, _ = test_env
    res = client.post(
        "/api/v1/runs",
        json={"project_id": "default-project", "harness_provider": "antigravity"},
    )
    assert res.status_code == 202
    handle = res.json()
    assert handle["has_initial_contract"] is False
    assert handle["initial_contract_path"] is None
    assert handle["wave_id"] is None
    assert handle["lifecycle_stage"] == LifecycleStage.COLD_START_N1

    run_id = handle["run_id"]
    get_res = client.get(f"/api/v1/runs/{run_id}")
    assert get_res.status_code == 200
    dto = get_res.json()
    assert dto["has_initial_contract"] is False
    assert dto["initial_contract_path"] is None
    assert dto["wave_id"] is None
    assert dto["lifecycle_stage"] == LifecycleStage.COLD_START_N1


def test_create_run_with_inline_initial_contract(test_env: tuple[TestClient, Path]) -> None:
    """Verify inline initial_contract sets MULTI_SOURCE_EXPANSION and has_initial_contract=True."""
    client, _ = test_env
    inline_yaml = "domain: PrivateEquity\nversion: '1.0'\nentities:\n  - Fund\n"
    res = client.post(
        "/api/v1/runs",
        json={
            "project_id": "default-project",
            "initial_contract": inline_yaml,
            "wave_id": "W1",
            "harness_provider": "antigravity",
        },
    )
    assert res.status_code == 202
    handle = res.json()
    assert handle["has_initial_contract"] is True
    assert handle["initial_contract_path"] is None
    assert handle["wave_id"] == "W1"
    assert handle["lifecycle_stage"] == LifecycleStage.MULTI_SOURCE_EXPANSION

    run_id = handle["run_id"]
    get_res = client.get(f"/api/v1/runs/{run_id}")
    assert get_res.status_code == 200
    dto = get_res.json()
    assert dto["has_initial_contract"] is True
    assert dto["wave_id"] == "W1"
    assert dto["lifecycle_stage"] == LifecycleStage.MULTI_SOURCE_EXPANSION


def test_create_run_with_initial_contract_path_safe(test_env: tuple[TestClient, Path]) -> None:
    """Verify file-backed initial_contract_path safely loads and sets MULTI_SOURCE_EXPANSION."""
    client, workspace_root = test_env
    contracts_dir = workspace_root / "contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    deployed_file = contracts_dir / "deployed_contract.yaml"
    deployed_file.write_text(
        yaml.safe_dump({"domain": "Healthcare", "version": "2.0"}),
        encoding="utf-8",
    )

    res = client.post(
        "/api/v1/runs",
        json={
            "project_id": "default-project",
            "initial_contract_path": "contracts/deployed_contract.yaml",
            "wave_id": "W2",
        },
    )
    assert res.status_code == 202
    handle = res.json()
    assert handle["has_initial_contract"] is True
    assert handle["initial_contract_path"] == "contracts/deployed_contract.yaml"
    assert handle["wave_id"] == "W2"
    assert handle["lifecycle_stage"] == LifecycleStage.MULTI_SOURCE_EXPANSION

    run_id = handle["run_id"]
    get_res = client.get(f"/api/v1/runs/{run_id}")
    assert get_res.status_code == 200
    dto = get_res.json()
    assert dto["has_initial_contract"] is True
    assert dto["initial_contract_path"] == "contracts/deployed_contract.yaml"
    assert dto["wave_id"] == "W2"
    assert dto["lifecycle_stage"] == LifecycleStage.MULTI_SOURCE_EXPANSION


def test_create_run_with_path_traversal_rejected(test_env: tuple[TestClient, Path]) -> None:
    """Verify initial_contract_path with path traversal is rejected with HTTP 400."""
    client, _ = test_env
    res = client.post(
        "/api/v1/runs",
        json={
            "project_id": "proj-traversal",
            "initial_contract_path": "../../etc/passwd",
        },
    )
    assert res.status_code == 400
    body = res.json()
    assert body.get("status") == 400
    assert "Path Containment Violation" in body.get("title", "")


def test_pipeline_run_with_creation_options(test_env: tuple[TestClient, Path]) -> None:
    """Verify POST /api/v1/pipelines/runs propagates initial_contract and wave_id."""
    client, workspace_root = test_env
    input_spec = workspace_root / "pipeline_input.yaml"
    input_spec.write_text(
        yaml.safe_dump({"pipeline_id": "pipe-exp", "source_csv_path": "data.csv"}),
        encoding="utf-8",
    )

    res = client.post(
        "/api/v1/pipelines/runs",
        json={
            "input_spec_path": "pipeline_input.yaml",
            "initial_contract": "domain: SupplyChain\nversion: '1.0'\n",
            "wave_id": "W0",
        },
    )
    assert res.status_code == 202
    handle = res.json()
    assert handle["has_initial_contract"] is True
    assert handle["wave_id"] == "W0"
    assert handle["lifecycle_stage"] == LifecycleStage.MULTI_SOURCE_EXPANSION


def test_pipeline_run_with_path_traversal_rejected(test_env: tuple[TestClient, Path]) -> None:
    """Verify POST /api/v1/pipelines/runs with path traversal initial_contract_path is rejected."""
    client, _ = test_env
    res = client.post(
        "/api/v1/pipelines/runs",
        json={
            "input_spec_path": "valid_spec.yaml",
            "initial_contract_path": "../../../system_secrets.yaml",
        },
    )
    assert res.status_code == 400
    body = res.json()
    assert body.get("status") == 400


def test_local_executor_run_create_options(tmp_path: Path) -> None:
    """Verify CLI LocalExecutor run create forwards initial_contract and wave_id."""
    from opendv_service import CreateProjectRequest

    executor = LocalExecutor(workspace_root=tmp_path)
    executor.service.create_project(
        CreateProjectRequest(project_id="proj-cli", display_name="CLI Project")
    )
    args = argparse.Namespace(
        run_action="create",
        project_id="proj-cli",
        harness="antigravity",
        initial_contract="domain: CliTest\n",
        initial_contract_inline=None,
        contract=None,
        initial_contract_path=None,
        wave_id="W3",
    )
    code, payload = executor.execute("run", args)
    assert code == 0
    assert payload["has_initial_contract"] is True
    assert payload["wave_id"] == "W3"
    assert payload["lifecycle_stage"] == LifecycleStage.MULTI_SOURCE_EXPANSION


def test_create_run_with_missing_contract_file_rejected_400(
    test_env: tuple[TestClient, Path],
) -> None:
    """Verify missing initial_contract_path returns HTTP 400 Problem Details."""
    client, _ = test_env
    res = client.post(
        "/api/v1/runs",
        json={
            "project_id": "default-project",
            "initial_contract_path": "nonexistent/contract.yaml",
        },
    )
    assert res.status_code == 400
    body = res.json()
    assert body["status"] == 400
    assert "contract file does not exist" in body["detail"].lower()


def test_create_run_with_directory_contract_path_rejected_400(
    test_env: tuple[TestClient, Path],
) -> None:
    """Verify initial_contract_path pointing to a directory returns HTTP 400 Problem Details."""
    client, workspace_root = test_env
    (workspace_root / "subdir").mkdir(exist_ok=True)
    res = client.post(
        "/api/v1/runs",
        json={
            "project_id": "default-project",
            "initial_contract_path": "subdir",
        },
    )
    assert res.status_code == 400
    body = res.json()
    assert body["status"] == 400
    assert "not a regular file" in body["detail"].lower()


def test_create_run_with_malformed_yaml_rejected_400(test_env: tuple[TestClient, Path]) -> None:
    """Verify malformed initial_contract YAML returns HTTP 400 Problem Details."""
    client, _ = test_env
    res = client.post(
        "/api/v1/runs",
        json={
            "project_id": "default-project",
            "initial_contract": "key: [unclosed_bracket",
        },
    )
    assert res.status_code == 400
    body = res.json()
    assert body["status"] == 400
    assert "malformed initial_contract yaml" in body["detail"].lower()


def test_create_run_with_hostile_yaml_constructor_rejected_400(
    test_env: tuple[TestClient, Path],
) -> None:
    """Verify hostile PyYAML constructor tags in initial_contract return HTTP 400."""
    client, _ = test_env
    res = client.post(
        "/api/v1/runs",
        json={
            "project_id": "default-project",
            "initial_contract": '!!python/object/apply:os.system ["calc"]',
        },
    )
    assert res.status_code == 400
    body = res.json()
    assert body["status"] == 400
    assert "malformed initial_contract yaml" in body["detail"].lower()


def test_create_run_with_oversized_payload_rejected_422(test_env: tuple[TestClient, Path]) -> None:
    """Verify initial_contract exceeding 1 MB returns HTTP 422 Problem Details."""
    client, _ = test_env
    res = client.post(
        "/api/v1/runs",
        json={
            "project_id": "default-project",
            "initial_contract": "x" * 1_000_001,
        },
    )
    assert res.status_code == 422
    body = res.json()
    assert body["status"] == 422


def test_pipeline_run_with_missing_contract_file_rejected_400(
    test_env: tuple[TestClient, Path],
) -> None:
    """Verify pipeline run with missing initial_contract_path returns HTTP 400 Problem Details."""
    client, workspace_root = test_env
    input_spec = workspace_root / "pipeline_input.yaml"
    input_spec.write_text("pipeline_id: pipe-test\n", encoding="utf-8")
    res = client.post(
        "/api/v1/pipelines/runs",
        json={
            "input_spec_path": "pipeline_input.yaml",
            "initial_contract_path": "nonexistent/file.yaml",
        },
    )
    assert res.status_code == 400
    body = res.json()
    assert body["status"] == 400
    assert "contract file does not exist" in body["detail"].lower()


def test_pipeline_run_with_malformed_yaml_rejected_400(test_env: tuple[TestClient, Path]) -> None:
    """Verify pipeline run with malformed initial_contract returns HTTP 400 Problem Details."""
    client, workspace_root = test_env
    input_spec = workspace_root / "pipeline_input.yaml"
    input_spec.write_text("pipeline_id: pipe-test\n", encoding="utf-8")
    res = client.post(
        "/api/v1/pipelines/runs",
        json={
            "input_spec_path": "pipeline_input.yaml",
            "initial_contract": "invalid: [unclosed",
        },
    )
    assert res.status_code == 400
    body = res.json()
    assert body["status"] == 400
    assert "malformed initial_contract yaml" in body["detail"].lower()


def test_pipeline_run_with_oversized_payload_rejected_422(
    test_env: tuple[TestClient, Path],
) -> None:
    """Verify pipeline run with initial_contract exceeding 1 MB returns HTTP 422 Problem Details."""
    client, workspace_root = test_env
    input_spec = workspace_root / "pipeline_input.yaml"
    input_spec.write_text("pipeline_id: pipe-test\n", encoding="utf-8")
    res = client.post(
        "/api/v1/pipelines/runs",
        json={
            "input_spec_path": "pipeline_input.yaml",
            "initial_contract": "y" * 1_000_001,
        },
    )
    assert res.status_code == 422
    body = res.json()
    assert body["status"] == 422
