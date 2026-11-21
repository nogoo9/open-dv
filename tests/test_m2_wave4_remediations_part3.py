"""Regression tests for M2 Iteration 3 remediations (Security, Ledger, SSE, SQL Guard, DTOs)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from opendv_agent_harness import (
    AgenticPipelineReport,
    PipelineStepReport,
    PipelineWaveReport,
)
from opendv_contracts.dtos.sources import SourceInspectResponseDTO
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.genesis import create_genesis_ontology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_duckdb_sandbox.sql_guard import validate_safe_sql
from opendv_platform_api.main import create_app
from opendv_service import OpenDVService, OpenDVSettings, PlatformStateContainer
from opendv_service.models import RunStatus
from opendv_service.runs.broadcaster import subscribe_run_sse
from opendv_service.runs.engine import RunContext
from opendv_service.seams import set_pipeline_runner_override
from opendv_service.studio.delta_normalizer import StudioWorkflowError
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.wizard_workflows import validate_construct_request_workflow
from opendv_service.use_cases.pipeline import execute_pipeline_run_sync


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


def test_connector_cas_hash_only_namespace_dict_and_corrupt_xlsx(
    tmp_path: Path,
    studio_api_env: tuple[TestClient, Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bugs 1.1, 1.2, 1.3: cas_hash without uri, NamespaceSpecDTO dict, corrupt .xlsx -> 422."""
    client, ws_root, _studio_root, _state_dir = studio_api_env
    sid = "sess-conn-iter3"
    assert client.post("/api/v1/ontology/studio/sessions", json={"session_id": sid}).status_code == 201

    # 1. Corrupt .xlsx -> 422 CORRUPT_SOURCE_BLOB (never 500)
    bad_xlsx = ws_root / "corrupt.xlsx"
    bad_xlsx.write_bytes(b"PK\x03\x04CORRUPT_ZIP_PAYLOAD")
    resp_xlsx = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/sources/connectors",
        json={"adapter": "file", "uri": str(bad_xlsx)},
    )
    assert resp_xlsx.status_code == 422, resp_xlsx.text
    assert resp_xlsx.json().get("error_code") == "CORRUPT_SOURCE_BLOB"

    # 2. cas_hash without uri -> 201 Created
    sqlite_p = tmp_path / "cas_only.sqlite"
    conn = sqlite3.connect(str(sqlite_p))
    conn.execute("CREATE TABLE positions (pos_id TEXT PRIMARY KEY, qty REAL)")
    conn.execute("INSERT INTO positions VALUES ('P1', 100.0)")
    conn.commit()
    conn.close()
    cas_sha = client.post("/api/v1/cas/blobs", content=sqlite_p.read_bytes()).json()["sha256"]

    resp_cas = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/sources/connectors",
        json={"adapter": "sqlite", "cas_hash": cas_sha, "source_name": "positions.sqlite"},
    )
    assert resp_cas.status_code == 201, resp_cas.text
    assert resp_cas.json()["table_count"] == 1

    # 3. Missing both uri and cas_hash -> 400
    resp_empty = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/sources/connectors",
        json={"adapter": "sqlite"},
    )
    assert resp_empty.status_code in (400, 422), resp_empty.text

    # 4. NamespaceSpecDTO dict accepted over REST
    captured: dict[str, Any] = {}

    def _fake_inspect(req: Any, **kwargs: Any) -> SourceInspectResponseDTO:
        captured["namespace"] = req.namespace.model_dump() if req.namespace else None
        captured["allowed_hosts"] = kwargs.get("allowed_hosts")
        return SourceInspectResponseDTO(
            adapter_id="uc",
            namespaces=[{"catalog": "prod", "schema_name": "trading"}],
            tables=[
                {
                    "table_name": "fills",
                    "columns": [{"name": "fill_id", "data_type": "STRING", "is_primary_key": True}],
                    "primary_key": ["fill_id"],
                }
            ],
            capabilities={},
            fingerprint="f" * 64,
            inspected_at_utc="2026-11-19T00:00:00Z",
        )

    monkeypatch.setattr("opendv_service.studio.connector_ingestion.inspect_source", _fake_inspect)
    monkeypatch.setenv("OPENDV_ALLOWED_HOSTS", "adb-prod.azuredatabricks.net")
    resp_ns = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/sources/connectors",
        json={
            "adapter": "uc",
            "uri": "databricks://prod.trading",
            "namespace": {"catalog": "prod", "schema_name": "trading"},
        },
    )
    assert resp_ns.status_code == 201, resp_ns.text
    assert captured["namespace"] == {"catalog": "prod", "schema_name": "trading"}
    assert captured["allowed_hosts"] == ["adb-prod.azuredatabricks.net"]


def test_step6_async_and_sync_ledger_preserved_linked_sse_and_latest_job_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bugs 5.1, 2.1, & Should-Fix #3: Async/sync ledger preserved, live linked SSE, latest job wins."""
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

    mgr = StudioSessionManager(studio_root=studio_root, workspace_root=ws_root)
    sid = "sess-step6-sse-01"
    mgr.create_session(sid, domain_namespace="custody")
    jobs_dir = mgr.get_session_dir(sid) / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    # Seed an OLD failed job and a NEWER completed job
    (jobs_dir / "job-01-old.json").write_text(
        json.dumps({"job_id": "job-01-old", "status": "FAILED", "created_at": "2026-11-19T00:00:00Z"}),
        encoding="utf-8",
    )
    (jobs_dir / "job-02-new.json").write_text(
        json.dumps({"job_id": "job-02-new", "status": "COMPLETED", "created_at": "2026-11-19T00:05:00Z"}),
        encoding="utf-8",
    )

    stage0_entered = threading.Event()
    release_stage1 = threading.Event()

    def _blocking_runner(
        spec_obj: Any,
        *args: Any,
        on_stage_start: Any = None,
        on_stage_complete: Any = None,
        **kwargs: Any,
    ) -> AgenticPipelineReport:
        if on_stage_start:
            on_stage_start(0, "STAGE_00_ORCHESTRATION", "vault-first-mate", "demo-model")
        stage0_entered.set()
        release_stage1.wait(timeout=5.0)
        rep1 = PipelineStepReport(
            stage_id="STAGE_01_SCOUT_SOURCE",
            stage_index=1,
            persona="scout-source",
            status="PASS",
            summary="Scouted sources",
        )
        if on_stage_start:
            on_stage_start(0, "STAGE_01_SCOUT_SOURCE", "scout-source", "demo-model")
        if on_stage_complete:
            on_stage_complete(0, rep1)
        return AgenticPipelineReport(
            run_id=spec_obj.run_id or "run-async-s6",
            status="COMPLETED",
            overall_passed=True,
            wave_reports=[PipelineWaveReport(sandbox_summary={"passed": True})],
            step_reports=[rep1],
        )

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    srv_thread = threading.Thread(target=server.run, daemon=True)
    srv_thread.start()
    while not server.started:
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"

    set_pipeline_runner_override(_blocking_runner)
    try:
        with httpx.Client(base_url=base_url, timeout=10.0) as hclient:
            run_resp = hclient.post(
                "/api/v1/pipelines/runs",
                json={
                    "session_id": sid,
                    "blueprint": _genesis_blueprint_dict("custody"),
                    "sources": [{"feed_id": "f1", "source_system": "crm", "uri": "sqlite:///crm.db"}],
                },
            )
            assert run_resp.status_code == 202, run_resp.text
            run_id = run_resp.json()["run_id"]
            assert stage0_entered.wait(timeout=5.0)

            collected_lines: list[str] = []
            with hclient.stream(
                "GET", f"/api/v1/ontology/studio/sessions/{sid}/logs/stream?once=false"
            ) as sse_resp:
                assert sse_resp.status_code == 200
                released = False
                for line in sse_resp.iter_lines():
                    collected_lines.append(line)
                    if not released and "STAGE_00_ORCHESTRATION" in line:
                        released = True
                        release_stage1.set()

            joined_sse = "\n".join(collected_lines)
            assert "STAGE_01_SCOUT_SOURCE" in joined_sse, f"Live SSE dropped stage 01: {joined_sse}"
            assert '"status": "COMPLETED"' in joined_sse, f"Expected latest COMPLETED, got: {joined_sse}"

            # Ledger must exist on disk after COMPLETED & /logs must return STAGE_STARTED == 1 per stage
            ledger_file = state_dir / "ledgers" / f"{run_id}.jsonl"
            assert ledger_file.is_file() and ledger_file.stat().st_size > 0
            logs_json = hclient.get(f"/api/v1/ontology/studio/sessions/{sid}/logs").json()
            entries = logs_json["entries"]
            assert entries
            s0_starts = [e for e in entries if e.get("stage_id") == "STAGE_00_ORCHESTRATION" and e.get("event") == "STAGE_STARTED"]
            assert len(s0_starts) == 1

            # Sync=true run also writes its own ledger file ledgers/{report.run_id}.jsonl
            def _sync_runner(spec_obj: Any, *args: Any, on_stage_complete: Any = None, **kwargs: Any) -> AgenticPipelineReport:
                rep = PipelineStepReport(stage_id="STAGE_07_DETERMINISTIC_COMPILER", stage_index=7, persona="deterministic-compiler", status="PASS")
                if on_stage_complete:
                    on_stage_complete(0, rep)
                return AgenticPipelineReport(run_id="run-sync-custom-id", status="COMPLETED", overall_passed=True, step_reports=[rep])

            set_pipeline_runner_override(_sync_runner)
            release_stage1.set()
            sync_resp = hclient.post(
                "/api/v1/pipelines/runs?sync=true",
                json={
                    "session_id": sid,
                    "blueprint": _genesis_blueprint_dict("custody"),
                    "sources": [{"feed_id": "f1", "source_system": "crm", "uri": "sqlite:///crm.db"}],
                },
            )
            assert sync_resp.status_code == 200, sync_resp.text
            assert (state_dir / "ledgers" / "run-sync-custom-id.jsonl").is_file()
    finally:
        release_stage1.set()
        set_pipeline_runner_override(None)
        server.should_exit = True
        srv_thread.join(timeout=3.0)


def test_paused_hold_context_none_sse_and_broadcaster_dedup_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Broadcaster emits event:hold when context=None (rehydrated & sync) and dedups queue.get()."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "state"
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")

    def _hold_runner(spec_obj: Any, *args: Any, **kwargs: Any) -> AgenticPipelineReport:
        return AgenticPipelineReport(
            run_id=spec_obj.run_id or "run-sync-hold",
            status="PAUSED_HOLD",
            overall_passed=False,
            active_holds=[{"hold_id": "hold-sync-1", "decision_id": "hold-sync-1", "headline": "Needs Steward"}],
        )

    state = PlatformStateContainer(workspace_root=ws_root, state_dir=state_dir)
    set_pipeline_runner_override(_hold_runner)
    try:
        res = execute_pipeline_run_sync(
            state,
            {
                "blueprint": _genesis_blueprint_dict("custody"),
                "sources": [{"feed_id": "f1", "source_system": "crm", "uri": "sqlite:///crm.db"}],
            },
        )
    finally:
        set_pipeline_runner_override(None)

    assert res.status == "PAUSED_HOLD"

    # 1. Sync PAUSED_HOLD with context=None
    app_sync = create_app(platform_state=state, workspace_root=ws_root, storage_root=state_dir)
    with TestClient(app_sync, client=("127.0.0.1", 50000), base_url="http://localhost:8000").stream(
        "GET", f"/api/v1/runs/{res.run_id}/events"
    ) as resp_sync:
        txt_sync = "".join(resp_sync.iter_text())
    assert "event: hold" in txt_sync and "PAUSED_HOLD" in txt_sync
    assert "event: run_complete" not in txt_sync

    # 2. Rehydrated PAUSED_HOLD in fresh PlatformStateContainer (context=None)
    rehydrated = PlatformStateContainer(workspace_root=ws_root, state_dir=state_dir)
    assert rehydrated.runs[res.run_id].status == RunStatus.PAUSED_HOLD
    app_rehyd = create_app(platform_state=rehydrated, workspace_root=ws_root, storage_root=state_dir)
    with TestClient(app_rehyd, client=("127.0.0.1", 50000), base_url="http://localhost:8000").stream(
        "GET", f"/api/v1/runs/{res.run_id}/events"
    ) as resp_rehyd:
        txt_rehyd = "".join(resp_rehyd.iter_text())
    assert "event: hold" in txt_rehyd and "PAUSED_HOLD" in txt_rehyd
    assert "event: run_complete" not in txt_rehyd

    # 3. Broadcaster queue.get() deduplicates event_id already replayed from state.run_events
    race_run = "run-race-dedup"
    dup_evt = {
        "event_id": "evt-race-stage0",
        "run_id": race_run,
        "stage_id": "STAGE_00_ORCHESTRATION",
        "event_type": "stage_started",
        "agent_persona": "vault-first-mate",
        "status": "PASS",
    }
    state.runs[race_run] = state.runs[res.run_id].model_copy(
        update={"run_id": race_run, "status": RunStatus.RUNNING}
    )
    state.run_events[race_run] = [dup_evt]
    ctx = RunContext(run_id=race_run, is_done=False)

    async def _run_race() -> list[str]:
        frames: list[str] = []

        async def _push_dup_then_done() -> None:
            while not ctx.subscribers:
                await asyncio.sleep(0.005)
            for q, loop in list(ctx.subscribers):
                loop.call_soon_threadsafe(q.put_nowait, dup_evt)
                loop.call_soon_threadsafe(
                    q.put_nowait, {"event_id": "evt-done", "event_type": "run_complete", "payload": {"status": "COMPLETED"}}
                )

        pusher = asyncio.create_task(_push_dup_then_done())
        async for frame in subscribe_run_sse(ctx, state, race_run, ping_interval=1.0):
            frames.append(frame)
        await pusher
        return frames

    race_frames = asyncio.run(_run_race())
    joined_race = "".join(race_frames)
    assert joined_race.count("id: evt-race-stage0\n") == 1


def test_sql_guard_rejects_compressed_double_quoted_files_and_construct_converts_opendv_error(
    tmp_path: Path,
) -> None:
    """Medium sql_guard compression suffixes + Nice-to-have construct OpenDVError translation."""
    for bad_sql in (
        'SELECT * FROM "secret.csv.gz"',
        'SELECT * FROM "secret.parquet.zst"',
        'SELECT * FROM "secret.json.gz"',
        'SELECT * FROM "secret.csv.zstd"',
        'SELECT * FROM "secret.tsv.bz2"',
        'SELECT * FROM "secret.jsonl.xz"',
        'SELECT * FROM "secret.parquet.lz4"',
        'WITH x AS (SELECT * FROM ("secret.csv.gz")) SELECT * FROM x',
    ):
        with pytest.raises(ValueError):
            validate_safe_sql(bad_sql)

    validate_safe_sql('SELECT "hk_fund", "created_at" FROM "raw_vault"."hub_fund"')

    mgr = StudioSessionManager(studio_root=tmp_path / "studio", workspace_root=tmp_path)
    meta = mgr.create_session("sess-construct-err", domain_namespace="custody")

    class _BadRunner:
        def _resolve_harness(self) -> Any:
            raise OpenDVError("No agent harness", error_code=ErrorCode.AGENT_NOT_CONFIGURED)

    with pytest.raises(StudioWorkflowError) as exc_info:
        validate_construct_request_workflow(
            meta=meta,
            sdir=mgr.get_session_dir("sess-construct-err"),
            base_version_req=None,
            base_sha256_req=None,
            auto_commit=False,
            mode_req="agent",
            if_match=None,
            is_agent_identity=False,
            agent_runner=_BadRunner(),
        )
    assert exc_info.value.http_status == 422
    assert exc_info.value.error_code == ErrorCode.AGENT_NOT_CONFIGURED
