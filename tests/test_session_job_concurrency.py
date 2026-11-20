"""Phase 2 Track B TDD Tests: Single Active Job per Session (409), Thread-Safe State & Disk Rehydration.

Covers:
- FR-RUN-01 / D27: One active job per session -> 409 SESSION_JOB_ACTIVE (CLI exit 20),
  while distinct sessions run concurrently without blocking.
- Audit #8: PlatformStateContainer threading.Lock safety across concurrent mutations +
  .opendv/state/ atomic persistence & startup rehydration (state/persistence.py).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings, PlatformStateContainer
from opendv_service.models import (
    GateScorecardSummary,
    ProjectRunDTO,
    RunStatus,
)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from opendv_core.schema_registry import get_default_schema_registry

    get_default_schema_registry()
    studio_root = tmp_path / "opendv_studio"
    studio_root.mkdir(parents=True, exist_ok=True)
    ws_root = tmp_path / "workspace"
    ws_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
    return studio_root


def _client(tmp_path: Path) -> TestClient:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    settings = OpenDVSettings.load(workspace_root=ws, state_dir=tmp_path / "state", is_server=True)
    app = create_app(settings=settings, workspace_root=ws, storage_root=tmp_path / "state")
    return TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")


def test_error_code_session_job_active_defined() -> None:
    """ErrorCode.SESSION_JOB_ACTIVE maps to HTTP 409 and normative conflict exit code 6."""
    assert hasattr(ErrorCode, "SESSION_JOB_ACTIVE")
    err = OpenDVError("Active job running in session", error_code=ErrorCode.SESSION_JOB_ACTIVE)
    assert err.http_status == 409
    assert err.exit_code == 6


def test_concurrent_jobs_same_session_rejected_409_while_distinct_sessions_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Submitting a second job in the same session returns 409 SESSION_JOB_ACTIVE; distinct sessions run in parallel."""
    import opendv_platform_api.job_supervisor as js_mod
    from opendv_platform_api.routers import ontology_studio as studio_router

    entered = threading.Event()
    release = threading.Event()

    def _blocking_parse(*args: Any, **kwargs: Any) -> dict[str, Any]:
        entered.set()
        release.wait(timeout=5.0)
        return {"parsed_sources": 1, "chunks_count": 1, "tables_count": 0}

    try:
        from opendv_platform_api import studio_deps

        supervisor = studio_deps.get_job_supervisor()
    except ImportError:
        supervisor = studio_router.get_job_supervisor()

    monkeypatch.setattr(supervisor, "_jobs", {})
    monkeypatch.setattr(js_mod, "run_parse_session_sources", _blocking_parse)
    if hasattr(studio_router, "run_parse_session_sources"):
        monkeypatch.setattr(studio_router, "run_parse_session_sources", _blocking_parse)

    client = _client(tmp_path)
    for sid in ("conc-sess-a", "conc-sess-b"):
        assert client.post("/api/v1/ontology/studio/sessions", json={"session_id": sid, "domain_namespace": "custody_ops"}).status_code == 201
        assert client.post(f"/api/v1/ontology/studio/sessions/{sid}/sources/text", json={"text": "Settlement trade note."}).status_code == 201

    try:
        r1 = client.post("/api/v1/ontology/studio/sessions/conc-sess-a/parse", json={"ocr_mode": "off"})
        assert r1.status_code == 202, r1.text
        assert entered.wait(timeout=3.0), "Background parse job did not enter"

        # Second job on SAME session must fail with 409 SESSION_JOB_ACTIVE
        r_conflict = client.post(
            "/api/v1/ontology/studio/sessions/conc-sess-a/construct",
            json={"mode": "deterministic", "base_version": "0.0.0"},
        )
        assert r_conflict.status_code == 409, r_conflict.text
        assert r_conflict.json().get("error_code") == "SESSION_JOB_ACTIVE"

        # Job on DISTINCT session must succeed concurrently
        r_other = client.post("/api/v1/ontology/studio/sessions/conc-sess-b/parse", json={"ocr_mode": "off"})
        assert r_other.status_code == 202, r_other.text
    finally:
        release.set()


def test_platform_state_container_threading_lock_and_disk_persistence_rehydration(
    tmp_path: Path,
) -> None:
    """PlatformStateContainer protects shared dicts with a threading.Lock and persists/rehydrates under .opendv/state/."""
    from opendv_service.state import persistence as state_persistence

    assert hasattr(state_persistence, "save_platform_state")
    assert hasattr(state_persistence, "load_platform_state")

    storage = tmp_path / ".opendv" / "state"
    state = PlatformStateContainer(workspace_root=tmp_path, storage_root=storage)
    assert hasattr(state, "_state_lock") or hasattr(state, "_lock")

    errors: list[Exception] = []

    def _worker(idx: int) -> None:
        try:
            for step in range(15):
                rid = f"run-t{idx}-s{step}"
                dto = ProjectRunDTO(
                    run_id=rid,
                    project_id=f"proj-{idx}",
                    status=RunStatus.COMPLETED,
                    started_at_utc="2026-11-19T00:00:00Z",
                    current_stage_id="STAGE_07_DETERMINISTIC_COMPILER",
                    events_sse_url=f"/api/v1/runs/{rid}/events",
                    gates_url=f"/api/v1/runs/{rid}/gates",
                    sandbox_url=f"/api/v1/runs/{rid}/sandbox",
                    decisions_url=f"/api/v1/runs/{rid}/decisions",
                    gate_scorecard=GateScorecardSummary(),
                )
                state.register_run(dto)  # type: ignore[attr-defined]
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert errors == []
    assert len(state.runs) == 60

    # Rehydrate fresh container from disk
    rehydrated = PlatformStateContainer(workspace_root=tmp_path, storage_root=storage)
    assert len(rehydrated.runs) == 60
    assert "run-t0-s0" in rehydrated.runs


def test_pipeline_run_and_studio_job_cross_conflict_returns_409_session_job_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-RUN-01 / D27: Concurrent POST /pipelines/runs or Studio job on active session returns 409 SESSION_JOB_ACTIVE."""
    import opendv_platform_api.job_supervisor as js_mod
    from opendv_platform_api import studio_deps
    from opendv_platform_api.routers import ontology_studio as studio_router

    entered = threading.Event()
    release = threading.Event()

    def _blocking_parse(*args: Any, **kwargs: Any) -> dict[str, Any]:
        entered.set()
        release.wait(timeout=5.0)
        return {"parsed_sources": 1, "chunks_count": 1, "tables_count": 0}

    supervisor = studio_deps.get_job_supervisor()
    monkeypatch.setattr(supervisor, "_jobs", {})
    monkeypatch.setattr(js_mod, "run_parse_session_sources", _blocking_parse)
    if hasattr(studio_router, "run_parse_session_sources"):
        monkeypatch.setattr(studio_router, "run_parse_session_sources", _blocking_parse)

    client = _client(tmp_path)
    sid = "pipe-conc-01"
    assert (
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "custody_ops"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/sources/text",
            json={"text": "Settlement trade note."},
        ).status_code
        == 201
    )

    try:
        # 1. While Studio parse job is active on sid, POST /pipelines/runs for sid -> 409 SESSION_JOB_ACTIVE
        r1 = client.post(f"/api/v1/ontology/studio/sessions/{sid}/parse", json={"ocr_mode": "off"})
        assert r1.status_code == 202, r1.text
        assert entered.wait(timeout=3.0)

        r_pipe = client.post(
            "/api/v1/pipelines/runs",
            json={"session_id": sid, "target_dialect": "duckdb", "sources": []},
        )
        assert r_pipe.status_code == 409, r_pipe.text
        assert r_pipe.json().get("error_code") == "SESSION_JOB_ACTIVE", r_pipe.json()
    finally:
        release.set()
        from opendv_service.studio.session_manager import StudioSessionManager

        smgr = StudioSessionManager(studio_root=tmp_path / "opendv_studio")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if supervisor.count_active_jobs(smgr, sid) == 0:
                break
            time.sleep(0.05)

    # 2. While a pipeline run is RUNNING on sid, second POST /pipelines/runs AND Studio /fibo-qa -> 409 SESSION_JOB_ACTIVE
    app = client.app
    pstate = app.state.platform_state
    active_run_id = "run-active-guard-1"
    pstate.runs[active_run_id] = ProjectRunDTO(
        run_id=active_run_id,
        project_id="default-project",
        status=RunStatus.RUNNING,
        started_at_utc="2026-11-20T00:00:00Z",
        current_stage_id="STAGE_01_SCOUT_SOURCE",
        events_sse_url=f"/api/v1/runs/{active_run_id}/events",
        gates_url=f"/api/v1/runs/{active_run_id}/gates",
        sandbox_url=f"/api/v1/runs/{active_run_id}/sandbox",
        decisions_url=f"/api/v1/runs/{active_run_id}/decisions",
        gate_scorecard=GateScorecardSummary(),
        session_id=sid,
    )
    pstate.run_session_ids[active_run_id] = sid

    r_pipe2 = client.post(
        "/api/v1/pipelines/runs",
        json={"session_id": sid, "target_dialect": "duckdb", "sources": []},
    )
    assert r_pipe2.status_code == 409, r_pipe2.text
    assert r_pipe2.json().get("error_code") == "SESSION_JOB_ACTIVE", r_pipe2.json()

    r_studio2 = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/fibo-qa",
        json={"question": "What concepts exist?"},
    )
    assert r_studio2.status_code == 409, r_studio2.text
    assert r_studio2.json().get("error_code") == "SESSION_JOB_ACTIVE", r_studio2.json()


def test_post_runs_enforces_d27_session_job_active_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 2 TDD (D27): POST /api/v1/runs with PipelineInputDTO(session_id=...) returns 409 SESSION_JOB_ACTIVE."""
    import opendv_platform_api.job_supervisor as js_mod
    from opendv_platform_api import studio_deps
    from opendv_platform_api.routers import ontology_studio as studio_router

    entered = threading.Event()
    release = threading.Event()

    def _blocking_parse(*args: Any, **kwargs: Any) -> dict[str, Any]:
        entered.set()
        release.wait(timeout=5.0)
        return {"parsed_sources": 1, "chunks_count": 1, "tables_count": 0}

    supervisor = studio_deps.get_job_supervisor()
    monkeypatch.setattr(supervisor, "_jobs", {})
    monkeypatch.setattr(js_mod, "run_parse_session_sources", _blocking_parse)
    if hasattr(studio_router, "run_parse_session_sources"):
        monkeypatch.setattr(studio_router, "run_parse_session_sources", _blocking_parse)

    client = _client(tmp_path)
    sid = "runs-conc-01"
    assert (
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "custody_ops"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/sources/text",
            json={"text": "Settlement trade note."},
        ).status_code
        == 201
    )

    try:
        # 1. Active Studio parse job on sid blocks POST /api/v1/runs for sid with 409 SESSION_JOB_ACTIVE
        r1 = client.post(f"/api/v1/ontology/studio/sessions/{sid}/parse", json={"ocr_mode": "off"})
        assert r1.status_code == 202, r1.text
        assert entered.wait(timeout=3.0)

        r_runs = client.post(
            "/api/v1/runs",
            json={"session_id": sid, "target_dialect": "duckdb", "sources": []},
        )
        assert r_runs.status_code == 409, r_runs.text
        assert r_runs.json().get("error_code") == "SESSION_JOB_ACTIVE", r_runs.json()
    finally:
        release.set()
        from opendv_service.studio.session_manager import StudioSessionManager

        smgr = StudioSessionManager(studio_root=tmp_path / "opendv_studio")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if supervisor.count_active_jobs(smgr, sid) == 0:
                break
            time.sleep(0.05)

    # 2. Active pipeline run on sid blocks POST /api/v1/runs for sid with 409 SESSION_JOB_ACTIVE
    pstate = client.app.state.platform_state
    active_run_id = "run-active-runs-guard-1"
    pstate.runs[active_run_id] = ProjectRunDTO(
        run_id=active_run_id,
        project_id="default-project",
        status=RunStatus.RUNNING,
        started_at_utc="2026-11-20T00:00:00Z",
        current_stage_id="STAGE_01_SCOUT_SOURCE",
        events_sse_url=f"/api/v1/runs/{active_run_id}/events",
        gates_url=f"/api/v1/runs/{active_run_id}/gates",
        sandbox_url=f"/api/v1/runs/{active_run_id}/sandbox",
        decisions_url=f"/api/v1/runs/{active_run_id}/decisions",
        gate_scorecard=GateScorecardSummary(),
        session_id=sid,
    )
    pstate.run_session_ids[active_run_id] = sid

    r_runs2 = client.post(
        "/api/v1/runs",
        json={"session_id": sid, "target_dialect": "duckdb", "sources": []},
    )
    assert r_runs2.status_code == 409, r_runs2.text
    assert r_runs2.json().get("error_code") == "SESSION_JOB_ACTIVE", r_runs2.json()



def _seed_run(pstate: Any, run_id: str, sid: str, status: RunStatus) -> None:
    pstate.runs[run_id] = ProjectRunDTO(
        run_id=run_id,
        project_id="default-project",
        status=status,
        started_at_utc="2026-11-20T00:00:00Z",
        current_stage_id="STAGE_01_SCOUT_SOURCE",
        events_sse_url=f"/api/v1/runs/{run_id}/events",
        gates_url=f"/api/v1/runs/{run_id}/gates",
        sandbox_url=f"/api/v1/runs/{run_id}/sandbox",
        decisions_url=f"/api/v1/runs/{run_id}/decisions",
        gate_scorecard=GateScorecardSummary(),
        session_id=sid,
    )
    pstate.run_session_ids[run_id] = sid


def test_paused_hold_run_is_not_active_for_enforcement_or_session_logs(tmp_path: Path) -> None:
    """D27: a PAUSED_HOLD run is parked, so enforcement and the logs activity view both treat it as inactive."""
    from opendv_platform_api import studio_deps
    from opendv_service.studio.session_manager import StudioSessionManager

    client = _client(tmp_path)
    sid = "held-sess-01"
    pstate = client.app.state.platform_state
    smgr = StudioSessionManager(studio_root=tmp_path / "opendv_studio")
    _seed_run(pstate, "run-held-1", sid, RunStatus.PAUSED_HOLD)

    is_active, _ = studio_deps.session_logs_activity_hooks(smgr, sid, platform_state=pstate)
    assert studio_deps.has_active_pipeline_run(pstate, sid) is False
    assert is_active() is False

    _seed_run(pstate, "run-live-1", sid, RunStatus.RUNNING)
    assert studio_deps.has_active_pipeline_run(pstate, sid) is True
    assert is_active() is True


def test_resuming_held_run_while_session_has_active_run_returns_409(tmp_path: Path) -> None:
    """D27: resolving a Captain-Hold cannot resume a second active run in the same session."""
    client = _client(tmp_path)
    sid = "held-sess-02"
    pstate = client.app.state.platform_state
    _seed_run(pstate, "run-held-2", sid, RunStatus.PAUSED_HOLD)
    _seed_run(pstate, "run-live-2", sid, RunStatus.RUNNING)

    resp = client.post(
        "/api/v1/runs/run-held-2/decisions", json={"hold_id": "hold-x", "action": "APPROVE"}
    )
    assert resp.status_code == 409, resp.text
    assert resp.json().get("error_code") == "SESSION_JOB_ACTIVE", resp.json()
    assert pstate.runs["run-held-2"].status == RunStatus.PAUSED_HOLD


def test_sync_pipeline_run_reserves_session_against_concurrent_run_and_studio_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D27: while a sync run executes (before it registers), same-session runs and Studio jobs get 409."""
    import opendv_service.use_cases.pipeline as pipeline_uc
    from opendv_platform_api import studio_deps

    entered = threading.Event()
    release = threading.Event()

    def _blocking_sync_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        entered.set()
        release.wait(timeout=5.0)
        return {
            "run_id": "run-sync-race-1",
            "created_at_utc": "2026-11-20T00:00:00Z",
            "event_stream_url": "/api/v1/runs/run-sync-race-1/events",
            "status_url": "/api/v1/runs/run-sync-race-1",
        }

    monkeypatch.setattr(studio_deps.get_job_supervisor(), "_jobs", {})
    monkeypatch.setattr(pipeline_uc, "execute_pipeline_run_sync", _blocking_sync_run)

    client = _client(tmp_path)
    sid = "sync-race-01"
    assert client.post("/api/v1/ontology/studio/sessions", json={"session_id": sid, "domain_namespace": "custody_ops"}).status_code == 201
    assert client.post(f"/api/v1/ontology/studio/sessions/{sid}/sources/text", json={"text": "Settlement trade note."}).status_code == 201

    body = {"session_id": sid, "target_dialect": "duckdb", "sources": []}
    first = threading.Thread(
        target=lambda: client.post("/api/v1/pipelines/runs?sync=true", json=body)
    )
    first.start()
    try:
        assert entered.wait(timeout=5.0), "First sync run did not start"
        second = client.post("/api/v1/pipelines/runs?sync=true", json=body)
        assert second.status_code == 409, second.text
        assert second.json().get("error_code") == "SESSION_JOB_ACTIVE", second.json()

        studio = client.post(f"/api/v1/ontology/studio/sessions/{sid}/parse", json={"ocr_mode": "off"})
        assert studio.status_code == 409, studio.text
        assert studio.json().get("error_code") == "SESSION_JOB_ACTIVE", studio.json()
    finally:
        release.set()
        first.join(timeout=5.0)

    assert sid not in studio_deps.get_job_supervisor()._reserved_run_sessions
