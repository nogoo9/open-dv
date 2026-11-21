"""Regression tests for M2 Wave 4 Review & Challenger remediations (Part 1: Tasks 1-4)."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import duckdb
import pytest
from starlette.testclient import TestClient

from opendv_agent_harness import AgenticPipelineReport, PipelineStepReport, PipelineWaveReport
from opendv_contracts.errors import OpenDVError
from opendv_core.genesis import create_genesis_ontology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_core.paths import PathContainmentError
from opendv_duckdb_sandbox.sql_guard import validate_safe_sql
from opendv_platform_api.main import create_app
from opendv_service import OpenDVService, OpenDVSettings
from opendv_service.models import GateScorecardSummary, ProjectRunDTO, RunStatus
from opendv_service.seams import set_pipeline_runner_override
from opendv_service.studio.connector_ingestion import attach_connector_source
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.sse_journal import SSEJournalWriter
from opendv_service.studio.wizard_workflows import stream_logs_workflow


def _genesis_blueprint_dict(domain_namespace: str = "custody") -> dict[str, Any]:
    ont = create_genesis_ontology(domain_namespace)
    return {
        "domain_namespace": domain_namespace,
        "ontology": ont.model_dump(mode="json", exclude_none=True),
        "lineage": {
            "domain_namespace": domain_namespace,
            "ontology_sha256": compute_ontology_sha256(ont),
            "blueprint_revision": 1,
        },
    }


@pytest.fixture
def studio_api_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, Path, Path, Path]:
    ws_root = tmp_path / "workspace"
    ws_root.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "state"
    studio_root = tmp_path / "studio_root"
    studio_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
    settings = OpenDVSettings.load(workspace_root=ws_root, state_dir=state_dir, is_server=True)
    app = create_app(settings=settings, workspace_root=ws_root, storage_root=state_dir)
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")
    return client, ws_root, studio_root, state_dir


def test_connector_containment_cas_hash_and_databricks_uc(
    tmp_path: Path,
    studio_api_env: tuple[TestClient, Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 1: Workspace containment, fail-closed inspect, CAS staging, and Databricks UC."""
    client, ws_root, _studio_root, _state_dir = studio_api_env
    sid = "sess-conn-sec-01"
    assert client.post("/api/v1/ontology/studio/sessions", json={"session_id": sid}).status_code == 201

    outside_csv = tmp_path / "outside_secret.csv"
    outside_csv.write_text("id,secret\n1,s3cr3t\n", encoding="utf-8")

    dot_opendv = ws_root / ".opendv" / "internal.csv"
    dot_opendv.parent.mkdir(parents=True, exist_ok=True)
    dot_opendv.write_text("k,v\na,1\n", encoding="utf-8")

    for bad_uri in ("../outside_secret.csv", str(outside_csv), str(dot_opendv), ".opendv/internal.csv"):
        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/sources/connectors",
            json={"adapter": "file", "uri": bad_uri},
        )
        assert resp.status_code in (400, 403), f"Expected failure for {bad_uri}: {resp.text}"

    link_path = ws_root / "escape_link.csv"
    try:
        os.symlink(outside_csv, link_path)
    except (OSError, NotImplementedError):
        pass
    else:
        resp_sym = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/sources/connectors",
            json={"adapter": "file", "uri": str(link_path)},
        )
        assert resp_sym.status_code in (400, 403), resp_sym.text

    bad_sqlite = ws_root / "corrupt.sqlite"
    bad_sqlite.write_bytes(b"NOT_A_VALID_SQLITE_HEADER_123456")
    resp_corrupt = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/sources/connectors",
        json={"adapter": "sqlite", "uri": str(bad_sqlite)},
    )
    assert resp_corrupt.status_code in (400, 422, 500)
    assert resp_corrupt.status_code != 201

    sqlite_local = tmp_path / "client_only.sqlite"
    sconn = sqlite3.connect(str(sqlite_local))
    sconn.execute("CREATE TABLE orders (order_id TEXT PRIMARY KEY, amount REAL)")
    sconn.execute("INSERT INTO orders VALUES ('ORD-1', 42.5)")
    sconn.commit()
    sconn.close()
    raw_db = sqlite_local.read_bytes()
    cas_resp = client.post("/api/v1/cas/blobs", content=raw_db)
    assert cas_resp.status_code == 201, cas_resp.text
    cas_sha = cas_resp.json()["sha256"]

    conn_cas = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/sources/connectors",
        json={
            "adapter": "sqlite",
            "uri": "/nonexistent/client/path/client_only.sqlite",
            "cas_hash": cas_sha,
        },
    )
    assert conn_cas.status_code == 201, conn_cas.text
    assert conn_cas.json()["table_count"] == 1

    class _UcCursor:
        def __init__(self) -> None:
            self._rows: list[tuple[Any, ...]] = []

        def execute(self, sql: str) -> None:
            low = sql.lower()
            if "distinct table_schema" in low:
                self._rows = [("core",)]
            elif "information_schema.tables" in low:
                self._rows = [("main_cat", "core", "portfolio_holdings", "MANAGED", "Holdings")]
            elif "information_schema.columns" in low:
                self._rows = [
                    ("holding_id", 1, "STRING", "NO", "Holding ID"),
                    ("market_val", 2, "DOUBLE", "YES", "Market value"),
                ]
            else:
                self._rows = []

        def fetchall(self) -> list[tuple[Any, ...]]:
            return list(self._rows)

        def close(self) -> None:
            return None

    class _UcConnection:
        def cursor(self) -> _UcCursor:
            return _UcCursor()

    monkeypatch.setenv("DATABRICKS_TOKEN", "test-databricks-token-1")
    mgr = StudioSessionManager(studio_root=_studio_root, workspace_root=ws_root)
    uc_res = attach_connector_source(
        session_mgr=mgr,
        session_id=sid,
        allowed_hosts=["adb-demo.azuredatabricks.net"],
        request_payload={
            "adapter": "databricks",
            "uri": "databricks://main_cat.core",
            "namespace": "main_cat.core",
            "table": "portfolio_holdings",
            "credentials_ref": "env:DATABRICKS_TOKEN",
            "options": {
                "host": "https://adb-demo.azuredatabricks.net",
                "http_path": "/sql/1.0/warehouses/demo",
                "connection": _UcConnection(),
            },
        },
    )
    assert uc_res["adapter"] == "databricks"
    assert uc_res["table_count"] == 1

    with pytest.raises((ValueError, OpenDVError, PathContainmentError)):
        attach_connector_source(
            session_mgr=mgr,
            session_id=sid,
            request_payload={
                "adapter": "uc",
                "uri": "databricks://main_cat.core",
                "credentials_ref": "env:DATABRICKS_TOKEN",
                "options": {"host": "https://evil.attacker.local", "http_path": "/sql/1"},
            },
        )


def test_live_logs_stream_polls_until_completion_and_journal_rotation_cursor(tmp_path: Path) -> None:
    """Task 2: Live /logs/stream continues past 2 polls with keepalive and rotated cursor continuity."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio", workspace_root=tmp_path)
    sid = "sess-logs-live"
    mgr.create_session(sid, domain_namespace="custody")
    sdir = mgr.get_session_dir(sid)

    writer = SSEJournalWriter(sdir, "job-rot-1", session_id=sid, max_segment_bytes=120)
    writer.buffer_event("log_entry", {"stage_id": "STAGE_00_ORCHESTRATION", "masked_prompt": "a" * 160})
    writer.flush()
    writer.buffer_event("log_entry", {"stage_id": "STAGE_01_SCOUT_SOURCE", "masked_prompt": "b" * 160})
    writer.flush()
    writer.buffer_event("log_entry", {"stage_id": "STAGE_01_SCOUT_SOURCE", "masked_prompt": "c" * 160})
    writer.flush()
    writer.journal_file.write_bytes(b"")
    restarted = SSEJournalWriter(sdir, "job-rot-1", session_id=sid, max_segment_bytes=120)
    next_evt = restarted.buffer_event("log_entry", {"stage_id": "STAGE_02_SCOUT_UNSTRUCTURED", "masked_prompt": "d"})
    restarted.flush()
    assert next_evt.id >= 3, f"Expected monotonic ID > 2 across .gz boundary, got {next_evt.id}"

    poll_state = {"count": 0}

    def _active_for_five_polls() -> bool:
        poll_state["count"] += 1
        if poll_state["count"] == 4:
            restarted.buffer_event("log_entry", {"stage_id": "STAGE_03_SHIP_VAULT_PASS1", "masked_prompt": "late"})
            restarted.flush()
        return poll_state["count"] < 5

    async def _collect() -> list[str]:
        frames: list[str] = []
        async for chunk in stream_logs_workflow(
            mgr,
            sid,
            once=False,
            is_active_fn=_active_for_five_polls,
            terminal_status_fn=lambda: "FAILED",
            poll_interval_s=0.01,
            keepalive_interval_s=0.015,
        ):
            frames.append(chunk)
        return frames

    frames = asyncio.run(_collect())
    joined = "".join(frames)
    assert ": keepalive\n\n" in joined
    assert "STAGE_03_SHIP_VAULT_PASS1" in joined
    assert "event: stream_end" in joined and '"status": "FAILED"' in joined


def test_422_agent_not_configured_on_auto_construct_and_pipeline_runs_and_sync_200(
    studio_api_env: tuple[TestClient, Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 3: Synchronous 422 AGENT_NOT_CONFIGURED on auto construct and /pipelines/runs + sync=true 200."""
    client, _ws_root, _studio_root, _state_dir = studio_api_env
    sid = "sess-gate-auto"
    assert client.post("/api/v1/ontology/studio/sessions", json={"session_id": sid}).status_code == 201

    monkeypatch.setenv("PATH", "")
    for env_k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENDV_AGENT_PROVIDER"):
        monkeypatch.delenv(env_k, raising=False)

    for payload in ({}, {"mode": "auto"}):
        resp = client.post(f"/api/v1/ontology/studio/sessions/{sid}/construct", json=payload)
        assert resp.status_code == 422, resp.text
        assert resp.json().get("error_code") == "AGENT_NOT_CONFIGURED"

    pipe_payload = {
        "blueprint": _genesis_blueprint_dict("custody"),
        "sources": [{"feed_id": "f1", "source_system": "crm", "uri": "sqlite:///demo.db"}],
        "target_dialect": "duckdb",
    }
    resp_async = client.post("/api/v1/pipelines/runs", json=pipe_payload)
    assert resp_async.status_code == 422, resp_async.text
    assert resp_async.json().get("error_code") == "AGENT_NOT_CONFIGURED"

    resp_sync_fail = client.post("/api/v1/pipelines/runs?sync=true", json=pipe_payload)
    assert resp_sync_fail.status_code == 422, resp_sync_fail.text

    def _fake_runner(spec_obj: Any, *args: Any, **kwargs: Any) -> AgenticPipelineReport:
        return AgenticPipelineReport(
            run_id=spec_obj.run_id or "run-sync-ok",
            status="COMPLETED",
            overall_passed=True,
            wave_reports=[PipelineWaveReport(sandbox_summary={"passed": True})],
            step_reports=[PipelineStepReport(stage_id="STAGE_07_DETERMINISTIC_COMPILER", stage_index=7, status="PASS")],
        )

    set_pipeline_runner_override(_fake_runner)
    try:
        resp_sync_ok = client.post("/api/v1/pipelines/runs?sync=true", json=pipe_payload)
        assert resp_sync_ok.status_code == 200, resp_sync_ok.text
        body = resp_sync_ok.json()
        assert body["status"] == "COMPLETED"
        assert "run_id" in body
        assert "report" in body
    finally:
        set_pipeline_runner_override(None)


def test_sql_guard_rejects_bypass_vectors_and_dv_schema_query_returns_400(
    studio_api_env: tuple[TestClient, Path, Path, Path],
) -> None:
    """Task 4: SQL guard blocks paren/quoted file paths and missing tables return HTTP 400."""
    client, _ws_root, _studio_root, state_dir = studio_api_env
    for bad_sql in (
        "SELECT * FROM ('secret.csv')",
        "WITH a AS (SELECT * FROM ('secret.csv')) SELECT * FROM a",
        'SELECT * FROM "secret.csv"',
        'SELECT * FROM "sub/secret.parquet"',
        'SELECT * FROM "/etc/passwd"',
    ):
        with pytest.raises(ValueError):
            validate_safe_sql(bad_sql)

    validate_safe_sql("SELECT customer_id FROM raw_vault.hub_customer WHERE country = 'US'")

    run_id = "run-dvq-400"
    sbx_dir = state_dir / "sandboxes"
    sbx_dir.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(sbx_dir / f"{run_id}.duckdb"))
    conn.execute("CREATE TABLE hub_customer (hk VARCHAR)")
    conn.close()

    svc: OpenDVService = client.app.state.service
    svc.state.runs[run_id] = ProjectRunDTO(
        run_id=run_id,
        project_id="default-project",
        harness_provider="agy-cli",
        status=RunStatus.COMPLETED,
        started_at_utc="2026-11-19T00:00:00Z",
        gate_scorecard=GateScorecardSummary(),
        events_sse_url=f"/api/v1/runs/{run_id}/events",
        gates_url=f"/api/v1/runs/{run_id}/gates",
        sandbox_url=f"/api/v1/runs/{run_id}/sandbox",
        decisions_url=f"/api/v1/runs/{run_id}/decisions",
    )
    resp = client.post(
        f"/api/v1/runs/{run_id}/dv-schema/query",
        json={"sql": "SELECT * FROM totally_missing_table_xyz"},
    )
    assert resp.status_code == 400, resp.text


def test_rest_connector_rejects_client_allowed_hosts_bypass(
    studio_api_env: tuple[TestClient, Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Iteration 3: REST JSON body cannot override OPENDV_ALLOWED_HOSTS via allowed_hosts."""
    client, _ws_root, _studio_root, _state_dir = studio_api_env
    sid = "sess-ssrf-rest-01"
    assert client.post("/api/v1/ontology/studio/sessions", json={"session_id": sid}).status_code == 201
    monkeypatch.setenv("OPENDV_ALLOWED_HOSTS", "adb-demo.azuredatabricks.net")
    monkeypatch.setenv("DATABRICKS_TOKEN", "test-databricks-token-1")

    for target_host in ("https://evil.example.com", "https://adb-demo.azuredatabricks.net"):
        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/sources/connectors",
            json={
                "adapter": "databricks",
                "uri": "databricks://main_cat.core",
                "credentials_ref": "env:DATABRICKS_TOKEN",
                "allowed_hosts": ["evil.example.com"],
                "options": {"host": target_host, "http_path": "/sql/1.0/warehouses/demo"},
            },
        )
        assert resp.status_code in (400, 422), f"Expected rejection for {target_host}: {resp.text}"

    # Direct workflow bypass check: even if payload contains allowed_hosts, workflow strips it
    from opendv_service.studio.wizard_workflows import attach_connector_workflow

    mgr = StudioSessionManager(studio_root=_studio_root, workspace_root=_ws_root)
    with pytest.raises((ValueError, OpenDVError)):
        attach_connector_workflow(
            session_mgr=mgr,
            session_id=sid,
            payload={
                "adapter": "uc",
                "uri": "databricks://main_cat.core",
                "credentials_ref": "env:DATABRICKS_TOKEN",
                "allowed_hosts": ["evil.example.com"],
                "options": {"host": "https://evil.example.com", "http_path": "/sql/1"},
            },
            allowed_hosts=["adb-demo.azuredatabricks.net"],
        )


def test_attach_connector_csv_with_utc_timestamp_succeeds_without_pytz(
    tmp_path: Path,
) -> None:
    """CSV/DuckDB sources with TIMESTAMPTZ columns must serialize sample rows without pytz."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    csv_file = ws_root / "ds06_unmapped_feed.csv"
    csv_file.write_text(
        "telemetry_event_id,facility_telemetry,recorded_at_utc\n"
        "evt-1,zone_a,2026-01-01T00:00:00Z\n",
        encoding="utf-8",
    )
    mgr = StudioSessionManager(studio_root=tmp_path / "studio", workspace_root=ws_root)
    mgr.create_session("s-tz-csv", domain_namespace="ops")
    res = attach_connector_source(
        session_mgr=mgr,
        session_id="s-tz-csv",
        request_payload={"adapter": "file", "uri": str(csv_file), "source_format": "csv"},
    )
    assert res["table_count"] == 1
    assert len(res["tables"]) == 1
    assert res["tables"][0].endswith("ds06_unmapped_feed")

