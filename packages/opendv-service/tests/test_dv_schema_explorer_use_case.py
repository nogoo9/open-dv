"""Unit and direct router tests for DV Schema Explorer use cases and endpoints."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from opendv_core.schema_registry import get_examples_dir
from opendv_platform_api.deps import get_opendv_service
from opendv_platform_api.routers.dv_schema_explorer import router as dv_schema_router
from opendv_service import OpenDVService, OpenDVSettings, PipelineRunRequest
from opendv_service.use_cases.dv_schema_explorer import (
    execute_dv_schema_query,
    get_dv_schema_projection,
    get_dv_schema_table_rows,
)
from opendv_testkit import deterministic_pipeline_seam

GC_DIR = get_examples_dir() / "pipeline_samples" / "global_commerce_vault"


def _build_mounted_client(service: OpenDVService) -> TestClient:
    app = FastAPI()
    app.include_router(dv_schema_router, prefix="/api/v1")
    app.dependency_overrides[get_opendv_service] = lambda: service
    return TestClient(app)


def test_dv_schema_explorer_projection_compare_rows_and_sql_guard(tmp_path: Path) -> None:
    """Verify DV schema projection, wave diff, persisted DuckDB rows, and read-only SQL guard."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    settings = OpenDVSettings.load(
        workspace_root=ws,
        state_dir=tmp_path / "state",
        is_server=True,
    )
    service = OpenDVService(
        settings=settings,
        workspace_root=ws,
        storage_root=tmp_path / "state",
    )

    w0_spec: dict[str, Any] = yaml.safe_load(
        (GC_DIR / "step_0_cold_start.yaml").read_text(encoding="utf-8")
    )
    w0_spec["project_id"] = "proj-dv-schema-unit"
    w0_spec["graph_a_ontology_path"] = str(
        (GC_DIR / "ontology" / "commerce_graph_a_ontology.yaml").resolve()
    )
    for src in w0_spec.get("sources", []):
        src["data_path"] = str((GC_DIR / src["data_path"]).resolve())

    with deterministic_pipeline_seam():
        r1 = service.execute_pipeline_run_sync(PipelineRunRequest(input_spec=w0_spec))
    assert r1["status"] == "COMPLETED"
    run1_id = str(r1["run_id"])

    db_path = service.state.state_dir / "sandboxes" / f"{run1_id}.duckdb"
    assert db_path.is_file()

    w1_spec = copy.deepcopy(w0_spec)
    w1_spec["pipeline_id"] = "dv_schema_wave2"
    with deterministic_pipeline_seam():
        r2 = service.execute_pipeline_run_sync(PipelineRunRequest(input_spec=w1_spec))
    assert r2["status"] == "COMPLETED"
    run2_id = str(r2["run_id"])

    proj = get_dv_schema_projection(service.state, run2_id, compare_run_id=run1_id)
    assert proj.run_id == run2_id
    assert proj.compare_run_id == run1_id
    assert proj.hubs
    assert proj.satellites
    assert proj.column_triage_ledger
    assert proj.scorecard
    assert proj.wave_diff is not None
    assert proj.wave_diff.dropped_columns_count == 0
    assert proj.wave_diff.hash_diff_mutations_count == 0

    first_hub_name = proj.hubs[0].name
    rows_dto = get_dv_schema_table_rows(service.state, run1_id, first_hub_name, limit=10, offset=0)
    assert rows_dto.table_name == first_hub_name
    assert len(rows_dto.rows) >= 2

    q_res = execute_dv_schema_query(
        service.state,
        run1_id,
        sql=f"SELECT COUNT(*) AS cnt FROM raw_vault.{first_hub_name}",
        max_rows=50,
    )
    assert q_res.row_count == 1
    assert int(q_res.rows[0]["cnt"]) >= 2

    client = _build_mounted_client(service)
    resp_schema = client.get(f"/api/v1/runs/{run2_id}/dv-schema?compare={run1_id}")
    assert resp_schema.status_code == 200
    body = resp_schema.json()
    assert body["compare_run_id"] == run1_id
    assert body["wave_diff"]["dropped_columns_count"] == 0

    resp_rows = client.get(f"/api/v1/runs/{run1_id}/dv-schema/tables/{first_hub_name}/rows?limit=5")
    assert resp_rows.status_code == 200
    assert len(resp_rows.json()["rows"]) >= 2

    resp_safe = client.post(
        f"/api/v1/runs/{run1_id}/dv-schema/query",
        json={"sql": f"SELECT COUNT(*) AS cnt FROM {first_hub_name}", "max_rows": 25},
    )
    assert resp_safe.status_code == 200
    assert int(resp_safe.json()["rows"][0]["cnt"]) >= 2

    for bad_sql in (
        f"DROP TABLE raw_vault.{first_hub_name}",
        "SELECT * FROM 'secret.csv'",
        "SELECT * FROM read_text('/etc/passwd')",
    ):
        resp_bad = client.post(
            f"/api/v1/runs/{run1_id}/dv-schema/query",
            json={"sql": bad_sql, "max_rows": 10},
        )
        assert resp_bad.status_code in {400, 422}

    assert client.get("/api/v1/runs/nonexistent/dv-schema").status_code == 404
    missing_rows_url = f"/api/v1/runs/{run1_id}/dv-schema/tables/no_such_tbl/rows"
    assert client.get(missing_rows_url).status_code == 404
