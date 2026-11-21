"""Integration tests for Single-Run Execution Plan inference, Blueprint Export, and REST routes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from opendv_contracts.dtos.blueprint import BlueprintExportResponseDTO, VaultBlueprintDTO
from opendv_contracts.dtos.pipeline import (
    InferredExecutionPlanDTO,
    PipelineInputDTO,
)
from opendv_contracts.dtos.sources import SourceFeedDTO
from opendv_core.genesis import create_genesis_ontology
from opendv_platform_api import PlatformStateContainer, create_app
from opendv_service.studio.session_manager import StudioSessionManager


@pytest.fixture
def api_client(tmp_path: Path, repo_root: Path) -> tuple[TestClient, PlatformStateContainer, Path]:
    """Create test client with clean isolated state and studio root."""
    state_dir = tmp_path / "state"
    studio_root = state_dir / "ontology_studio"
    studio_root.mkdir(parents=True, exist_ok=True)

    state = PlatformStateContainer(
        state_dir=state_dir,
        workspace_root=repo_root,
    )
    app = create_app(platform_state=state, workspace_root=repo_root)
    client = TestClient(app, client=("127.0.0.1", 50001), base_url="http://localhost:8000")
    return client, state, studio_root


def _create_sample_studio_session(
    studio_root: Path, session_id: str, domain_namespace: str = "wealth_mgmt"
) -> None:
    """Helper creating a valid Studio session with a genesis ontology artifact."""
    mgr = StudioSessionManager(studio_root=studio_root)
    mgr.create_session(session_id=session_id, domain_namespace=domain_namespace)
    sdir = mgr.get_session_dir(session_id)
    vdir = sdir / "versions" / "v1.0.0"
    vdir.mkdir(parents=True, exist_ok=True)
    ont = create_genesis_ontology(domain_namespace)
    (vdir / "ontology.json").write_text(json.dumps(ont.model_dump(mode="json")), encoding="utf-8")


def test_runs_validate_greenfield_cold_start(
    api_client: tuple[TestClient, PlatformStateContainer, Path],
) -> None:
    """Verify POST /api/v1/runs/validate with Greenfield PipelineInputDTO returns COLD_START_N1."""
    client, _, _ = api_client
    single_run_input = PipelineInputDTO(
        sources=[
            SourceFeedDTO(
                feed_id="feed-crm",
                source_system="crm_salesforce",
                uri="sqlite:///crm.db",
            )
        ]
    )

    resp = client.post("/api/v1/runs/validate", json=single_run_input.model_dump(mode="json"))
    assert resp.status_code == 200, resp.text

    plan = InferredExecutionPlanDTO.model_validate(resp.json())
    assert plan.primary_action == "COLD_START_N1"
    assert len(plan.table_actions) == 1
    assert plan.table_actions[0].action == "GREENFIELD_SYNTHESIS"


def test_runs_validate_existing_contract_multi_source(
    api_client: tuple[TestClient, PlatformStateContainer, Path],
    repo_root: Path,
) -> None:
    """Verify POST /api/v1/runs/validate with existing contract returns MULTI_SOURCE plan."""
    client, _, _ = api_client
    contract_path = repo_root / "examples/private_equity/dv_pe_model_contract.yaml"
    assert contract_path.is_file(), f"PE contract not found at {contract_path}"

    ont_path = repo_root / "examples/private_equity/pe_graph_a_ontology.yaml"
    assert ont_path.is_file(), f"PE ontology not found at {ont_path}"

    single_run_input = {
        "contract": contract_path.read_text(encoding="utf-8"),
        "ontology": ont_path.read_text(encoding="utf-8"),
        "sources": [
            {
                "feed_id": "feed-billing",
                "source_system": "billing_stripe",
                "uri": "sqlite:///billing.db",
            }
        ],
    }

    resp = client.post("/api/v1/runs/validate", json=single_run_input)
    assert resp.status_code == 200, resp.text

    plan = InferredExecutionPlanDTO.model_validate(resp.json())
    assert plan.primary_action == "MULTI_SOURCE_EXPANSION"
    assert len(plan.table_actions) >= 1


def test_pipelines_validate_single_run_compatibility(
    api_client: tuple[TestClient, PlatformStateContainer, Path],
) -> None:
    """Verify POST /api/v1/pipelines/validate accepts single-run PipelineInputDTO returning plan."""
    client, _, _ = api_client
    single_run_input = PipelineInputDTO(
        sources=[
            SourceFeedDTO(
                feed_id="feed-erp",
                source_system="erp_netsuite",
                uri="sqlite:///erp.db",
            )
        ]
    )

    resp = client.post("/api/v1/pipelines/validate", json=single_run_input.model_dump(mode="json"))
    assert resp.status_code == 200, resp.text

    plan = InferredExecutionPlanDTO.model_validate(resp.json())
    assert plan.primary_action == "COLD_START_N1"


def test_studio_export_blueprint_post_and_get(
    api_client: tuple[TestClient, PlatformStateContainer, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify POST and GET /api/v1/ontology/studio/sessions/{id}/export-blueprint."""
    client, _, studio_root = api_client
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))

    session_id = "studio-sess-export-01"
    _create_sample_studio_session(studio_root, session_id, domain_namespace="asset_mgmt")

    # 1. POST export-blueprint
    resp_post = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/export-blueprint",
        json={"version_tag": "v1.0.0"},
    )
    assert resp_post.status_code == 200, resp_post.text

    export_dto = BlueprintExportResponseDTO.model_validate(resp_post.json())
    assert export_dto.blueprint is not None
    assert isinstance(export_dto.blueprint, VaultBlueprintDTO)
    assert export_dto.blueprint.domain_namespace == "asset_mgmt"
    assert export_dto.blueprint.lineage.blueprint_revision == 1
    assert len(export_dto.blueprint.lineage.ontology_sha256) == 64

    # 2. GET export-blueprint
    resp_get = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}/export-blueprint?version_tag=v1.0.0"
    )
    assert resp_get.status_code == 200, resp_get.text
    export_get_dto = BlueprintExportResponseDTO.model_validate(resp_get.json())
    assert export_get_dto.blueprint.domain_namespace == "asset_mgmt"


def test_studio_export_blueprint_session_not_found(
    api_client: tuple[TestClient, PlatformStateContainer, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify export-blueprint for non-existent session returns 404."""
    client, _, studio_root = api_client
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))

    resp = client.post("/api/v1/ontology/studio/sessions/sess-missing-999/export-blueprint")
    assert resp.status_code == 404


def test_post_runs_accepting_single_run_payload(
    deterministic_pipeline_runner: None,
    api_client: tuple[TestClient, PlatformStateContainer, Path],
) -> None:
    """Verify POST /api/v1/runs and /api/v1/pipelines/runs accept single-run PipelineInputDTO."""
    client, _, _ = api_client
    single_run_input = {
        "sources": [
            {
                "feed_id": "feed-crm-single",
                "source_system": "crm_salesforce",
                "uri": "sqlite:///data.db",
            }
        ],
        "target_dialect": "duckdb",
    }

    # 1. POST /api/v1/runs
    resp_runs = client.post("/api/v1/runs", json=single_run_input)
    assert resp_runs.status_code == 202, resp_runs.text
    assert "location" in resp_runs.headers
    body_runs = resp_runs.json()
    assert "run_id" in body_runs
    assert "status_url" in body_runs
    assert "events_sse_url" in body_runs

    # 2. POST /api/v1/pipelines/runs
    resp_pipe = client.post("/api/v1/pipelines/runs", json=single_run_input)
    assert resp_pipe.status_code == 202, resp_pipe.text
    assert "location" in resp_pipe.headers
    body_pipe = resp_pipe.json()
    assert "run_id" in body_pipe
    assert "status_url" in body_pipe


def test_post_pipelines_runs_resolves_studio_cas_sources_and_unstructured_docs(
    tmp_path: Path,
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify POST /api/v1/pipelines/runs resolves Studio session cas:// sources & docs."""
    import sqlite3

    from opendv_agent_harness.pipeline.spec import (
        AgenticPipelineReport,
        DeterministicValidationReport,
        PipelineInputSpec,
    )
    from opendv_core.models.vault_blueprint import create_vault_blueprint
    from opendv_service import OpenDVService

    state_dir = tmp_path / "state"
    studio_root = state_dir / "ontology_studio"
    session_id = "ses-api-cas-01"
    mgr = StudioSessionManager(studio_root=studio_root)
    mgr.create_session(session_id=session_id, domain_namespace="wealth_mgmt")
    cas = mgr.get_cas(session_id)

    db_file = tmp_path / "positions.sqlite"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE pos (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO pos VALUES ('P1')")
    conn.commit()
    conn.close()
    sqlite_sha = cas.put(db_file.read_bytes())
    md_sha = cas.put(b"# SLA Policy\n\nSettlement cutoff is T+1.\n")
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))

    ont = create_genesis_ontology("wealth_mgmt", "EA")
    bp = create_vault_blueprint(domain_namespace="wealth_mgmt", ontology=ont)
    captured_specs: list[PipelineInputSpec] = []

    def mock_runner(
        spec: PipelineInputSpec, *args: object, **kwargs: object
    ) -> AgenticPipelineReport:
        captured_specs.append(spec)
        return AgenticPipelineReport(
            run_id=spec.run_id or "run-api-cas",
            pipeline_id="pipe-api",
            project_id="proj-api",
            domain_namespace="wealth_mgmt",
            status="COMPLETED",
            overall_passed=True,
            blueprint=bp,
            input_validation=DeterministicValidationReport(),
            output_validation=DeterministicValidationReport(),
        )

    state = PlatformStateContainer(state_dir=state_dir, workspace_root=repo_root)
    app = create_app(platform_state=state, workspace_root=repo_root)
    app.state.service = OpenDVService(
        state_store=state,
        workspace_root=repo_root,
        pipeline_runner=mock_runner,
        is_server=True,
    )
    client = TestClient(app, client=("127.0.0.1", 50001), base_url="http://localhost:8000")

    payload = {
        "session_id": session_id,
        "blueprint": bp.model_dump(mode="json"),
        "target_dialect": "duckdb",
        "sources": [
            {"feed_id": "src-1", "source_system": "positions", "uri": f"cas://{sqlite_sha}"}
        ],
        "unstructured_documents": [
            {
                "doc_id": "doc-1",
                "document_path": f"cas://{md_sha}",
                "mime_type": "text/markdown",
                "metadata": {"title": "sla.md", "source_sha256": md_sha},
            }
        ],
    }
    PipelineInputDTO.model_validate(payload)
    resp = client.post("/api/v1/pipelines/runs", json=payload)
    assert resp.status_code == 202, resp.text
    import time

    for _ in range(40):
        if captured_specs:
            break
        time.sleep(0.05)
    assert captured_specs
    spec = captured_specs[0]
    assert (spec.sources[0].uri or "").startswith("sqlite:///")
    assert Path(spec.unstructured_documents[0].document_path or "").is_file()
    assert "Settlement cutoff is T+1" in (spec.unstructured_documents[0].content or "")
