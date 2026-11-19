"""Regression tests for M2 Iteration 4 Challenger MEDIUM remediations."""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from starlette.testclient import TestClient

from opendv_agent_harness import (
    AgenticPipelineReport,
    PipelineStepReport,
    build_observability_event_dict,
)
from opendv_contracts import EventType
from opendv_core.genesis import create_genesis_ontology
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_platform_api.main import create_app
from opendv_service import (
    OpenDVService,
    OpenDVSettings,
    PipelineRunRequest,
    PlatformStateContainer,
)
from opendv_service.models import CaptainHoldDecisionRequest, RunStatus
from opendv_service.seams import set_pipeline_runner_override
from opendv_service.studio.session_logs_service import query_session_logs
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.sse_journal import SSEJournalWriter
from opendv_service.studio.wizard_workflows import stream_logs_workflow
from opendv_telemetry.ledger_emitter import VaultLedgerEmitter
from opendv_testkit import deterministic_pipeline_seam

REPO_ROOT = Path(__file__).resolve().parent.parent
GC_DIR = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"


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


def _seed_paused_hold_run(
    ws: Path, state_dir: Path, project_id: str
) -> tuple[OpenDVService, str]:
    ws.mkdir(parents=True, exist_ok=True)
    settings = OpenDVSettings.load(workspace_root=ws, state_dir=state_dir, is_server=True)
    service = OpenDVService(settings=settings, workspace_root=ws, storage_root=state_dir)

    gc_local = ws / "gc"
    shutil.copytree(GC_DIR, gc_local, dirs_exist_ok=True)
    src_ont = gc_local / "ontology" / "commerce_graph_a_ontology.yaml"
    ont_path = ws / "commerce_graph_a_ontology.yaml"
    shutil.copy2(src_ont, ont_path)
    GraphAOntology.model_validate(yaml.safe_load(ont_path.read_text(encoding="utf-8")))

    spec = yaml.safe_load((gc_local / "step_0_cold_start.yaml").read_text(encoding="utf-8"))
    spec["project_id"] = project_id
    spec["graph_a_ontology_path"] = str(ont_path.resolve())
    for src in spec.get("sources", []):
        src["data_path"] = str((gc_local / src["data_path"]).resolve())

    doc_path = ws / "sla_addendum.txt"
    quote = "Enterprise SLA Penalty applies if settlement exceeds T+2 business days."
    doc_path.write_text(quote, encoding="utf-8")
    spec["unstructured_documents"] = [
        {
            "doc_id": "doc-sla-reject",
            "file_path": str(doc_path),
            "content": quote,
            "candidates": [
                {
                    "candidate_id": "cand-sla-reject",
                    "entity_name": "SLA_PENALTY",
                    "concept_uri": "urn:ontology:entity:sla_penalty",
                    "verbatim_quote": quote,
                    "char_start": 0,
                    "char_end": len(quote),
                    "confidence_score": 0.72,
                    "er_tier": "Tier-U3",
                }
            ],
        }
    ]
    with deterministic_pipeline_seam():
        paused = service.execute_pipeline_run_sync(
            PipelineRunRequest(input_spec=spec, steward_hold_policy="pause")
        )
    assert paused["status"] == "PAUSED_HOLD", paused
    return service, str(paused["run_id"])


def test_resolve_captain_hold_reject_persists_failed_status_and_sse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix 1: resolve_captain_hold(..., REJECT) & non-resumable APPROVE persist across rehydration."""
    ws = tmp_path / "ws"
    state_dir = tmp_path / "state"
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")

    service, run_id = _seed_paused_hold_run(ws, state_dir, "proj-hold-reject")
    assert run_id in service.state.explicit_hold_runs

    resp = service.state.resolve_captain_hold(
        run_id,
        CaptainHoldDecisionRequest(
            action="REJECT",
            steward_id="steward_1",
            resolution_notes="Rejected SLA concept",
        ),
    )
    assert resp.updated_run_status == RunStatus.FAILED
    assert run_id not in service.state.explicit_hold_runs

    rehydrated = PlatformStateContainer(workspace_root=ws, state_dir=state_dir)
    assert rehydrated.runs[run_id].status == RunStatus.FAILED
    assert run_id not in rehydrated.explicit_hold_runs

    app = create_app(platform_state=rehydrated, workspace_root=ws, storage_root=state_dir)
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")
    with client.stream("GET", f"/api/v1/runs/{run_id}/events") as sse_resp:
        sse_text = "".join(sse_resp.iter_text())
    assert "event: run_complete" in sse_text
    assert '"FAILED"' in sse_text
    assert "event: hold" not in sse_text

    # Non-resumable APPROVE (no resumable_runs context) also persists COMPLETED + cleared hold set
    ws2 = tmp_path / "ws2"
    state_dir2 = tmp_path / "state2"
    service2, run_id2 = _seed_paused_hold_run(ws2, state_dir2, "proj-hold-nonres")
    resumable_file = state_dir2 / "resumable_runs" / f"{run_id2}.json"
    if resumable_file.is_file():
        resumable_file.unlink()
    resp2 = service2.state.resolve_captain_hold(
        run_id2,
        CaptainHoldDecisionRequest(
            action="APPROVE",
            steward_id="steward_1",
            resolution_notes="Approved without resumable context",
        ),
    )
    assert resp2.updated_run_status == RunStatus.COMPLETED
    rehydrated2 = PlatformStateContainer(workspace_root=ws2, state_dir=state_dir2)
    assert rehydrated2.runs[run_id2].status == RunStatus.COMPLETED
    assert run_id2 not in rehydrated2.explicit_hold_runs


def test_connector_cas_hash_only_without_source_name_infers_adapter_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix 2: POST .../sources/connectors with {adapter, cas_hash} and no uri/source_name -> 201."""
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

    sid = "sess-cas-no-name"
    assert client.post("/api/v1/ontology/studio/sessions", json={"session_id": sid}).status_code == 201

    samples = [
        ("csv", b"acct_id,balance\nA1,100.5\nA2,250.0\n"),
        ("tsv", b"acct_id\tbalance\nA1\t100.5\nA2\t250.0\n"),
        ("jsonl", b'{"acct_id":"A1","balance":100.5}\n{"acct_id":"A2","balance":250.0}\n'),
    ]
    for adapter, raw in samples:
        cas_sha = client.post("/api/v1/cas/blobs", content=raw).json()["sha256"]
        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/sources/connectors",
            json={"adapter": adapter, "cas_hash": cas_sha},
        )
        assert resp.status_code == 201, f"adapter={adapter}: {resp.text}"
        assert resp.json()["table_count"] == 1


def test_session_logs_found_with_custom_studio_root_and_unset_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix 3: Custom OPENDV_STUDIO_ROOT + unset OPENDV_STATE_DIR finds ws/.opendv/state/ledgers."""
    ws_root = tmp_path / "workspace"
    ws_root.mkdir(parents=True, exist_ok=True)
    studio_root = tmp_path / "standalone_studio"
    studio_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("OPENDV_STATE_DIR", raising=False)
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")

    settings = OpenDVSettings.load(workspace_root=ws_root, is_server=True)
    app = create_app(settings=settings, workspace_root=ws_root)
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")

    sid = "sess-custom-studio-ledger"
    assert client.post("/api/v1/ontology/studio/sessions", json={"session_id": sid}).status_code == 201

    def _sync_runner(
        spec_obj: Any, *args: Any, on_stage_complete: Any = None, **kwargs: Any
    ) -> AgenticPipelineReport:
        rep = PipelineStepReport(
            stage_id="STAGE_00_ORCHESTRATION",
            stage_index=0,
            persona="vault-first-mate",
            status="PASS",
        )
        if on_stage_complete:
            on_stage_complete(0, rep)
        return AgenticPipelineReport(
            run_id="run-custom-studio-01",
            status="COMPLETED",
            overall_passed=True,
            step_reports=[rep],
        )

    set_pipeline_runner_override(_sync_runner)
    try:
        run_resp = client.post(
            "/api/v1/pipelines/runs?sync=true",
            json={
                "session_id": sid,
                "blueprint": _genesis_blueprint_dict("custody"),
                "sources": [{"feed_id": "f1", "source_system": "crm", "uri": "sqlite:///crm.db"}],
            },
        )
        assert run_resp.status_code == 200, run_resp.text
    finally:
        set_pipeline_runner_override(None)

    assert (ws_root / ".opendv" / "state" / "ledgers" / "run-custom-studio-01.jsonl").is_file()
    logs_resp = client.get(f"/api/v1/ontology/studio/sessions/{sid}/logs")
    assert logs_resp.status_code == 200, logs_resp.text
    payload = logs_resp.json()
    assert payload["total"] > 0
    assert any(e.get("origin") == "pipeline_ledger" for e in payload["entries"])

    export_resp = client.get(f"/api/v1/ontology/studio/sessions/{sid}/logs/export")
    assert export_resp.status_code == 200
    assert "run-custom-studio-01" in export_resp.text


def test_stream_end_same_second_z_vs_microseconds_prefers_newer_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix 4: Same-second FAILED run ('...Z') + COMPLETED job ('....500000+00:00') -> COMPLETED."""
    ws_root = tmp_path / "workspace"
    ws_root.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "state"
    studio_root = tmp_path / "studio_root"
    studio_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")

    settings = OpenDVSettings.load(workspace_root=ws_root, state_dir=state_dir, is_server=True)
    service = OpenDVService(settings=settings, workspace_root=ws_root, storage_root=state_dir)
    app = create_app(
        settings=settings,
        platform_state=service.state,
        workspace_root=ws_root,
        storage_root=state_dir,
    )
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")

    sid = "sess-same-second-ts"
    mgr = StudioSessionManager(studio_root=studio_root, workspace_root=ws_root)
    mgr.create_session(sid, domain_namespace="custody")

    # Earlier FAILED pipeline run using "...Z" timestamp format
    from opendv_service.models import GateScorecardSummary, ProjectRunDTO

    run_id = "run-same-sec-fail"
    service.state.runs[run_id] = ProjectRunDTO(
        run_id=run_id,
        project_id="default-project",
        status=RunStatus.FAILED,
        started_at_utc="2026-11-19T07:29:35Z",
        completed_at_utc="2026-11-19T07:29:35Z",
        current_stage_id="STAGE_01_SCOUT_SOURCE",
        event_count=0,
        gate_scorecard=GateScorecardSummary(),
        active_hold_ids=[],
        events_sse_url=f"/api/v1/runs/{run_id}/events",
        gates_url=f"/api/v1/runs/{run_id}/gates",
        sandbox_url=f"/api/v1/runs/{run_id}/sandbox",
        decisions_url=f"/api/v1/runs/{run_id}/decisions",
    )
    service.state.run_session_ids[run_id] = sid

    # Newer COMPLETED Studio job 0.5s later in the SAME wall-clock second (".500000+00:00")
    jobs_dir = mgr.get_session_dir(sid) / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    (jobs_dir / "job-same-sec-ok.json").write_text(
        json.dumps(
            {
                "job_id": "job-same-sec-ok",
                "status": "completed",
                "created_at": "2026-11-19T07:29:35.500000+00:00",
                "finished_at": "2026-11-19T07:29:35.800000+00:00",
            }
        ),
        encoding="utf-8",
    )

    with client.stream("GET", f"/api/v1/ontology/studio/sessions/{sid}/logs/stream?once=true") as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "event: stream_end" in body
    assert '"status": "COMPLETED"' in body
    assert '"status": "FAILED"' not in body


def test_merged_session_logs_same_second_z_vs_microseconds_and_min_seq_polling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Iter 5: Same-second ledger ('...Z') + journal ('....700000+00:00') sort & min_seq poll."""
    ws, sroot = tmp_path / "ws", tmp_path / "studio"
    ws.mkdir()
    sroot.mkdir()
    monkeypatch.delenv("OPENDV_STATE_DIR", raising=False)
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(sroot))
    sid = "sess-same-sec-logs"
    mgr = StudioSessionManager(studio_root=sroot, workspace_root=ws)
    mgr.create_session(sid, domain_namespace="custody")
    sdir = mgr.get_session_dir(sid)

    emitter = VaultLedgerEmitter(ledger_path=ws / ".opendv" / "state" / "ledgers" / "run-ss.jsonl")
    emitter.emit(
        build_observability_event_dict(
            event_id="evt-ss-1",
            event_type=EventType.STAGE_STARTED.value,
            run_id="run-ss",
            trace_id=uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
            timestamp_utc="2026-11-19T07:29:35Z",
            stage_id="STAGE_00_ORCHESTRATION",
            agent_persona="vault-first-mate",
            status="PASS",
            headline="Ledger started",
            business_impact="Started.",
            summary_metrics={"session_id": sid},
        )
    )
    assert [(e["seq"], e["origin"]) for e in query_session_logs(mgr, sid, min_seq=0)] == [(1, "pipeline_ledger")]

    SSEJournalWriter(session_dir=sdir, job_id="job-ss").flush()
    jrec = {
        "id": 1,
        "event": "agent_turn",
        "timestamp": "2026-11-19T07:29:35.700000+00:00",
        "data": {"wizard_step": 4, "job_id": "job-ss", "stage_id": "STAGE_00_ORCHESTRATION", "persona": "term-scout", "category": "agent", "headline": "Journal later"},
    }
    (sdir / "jobs" / "job-ss.events.jsonl").write_text(json.dumps(jrec) + "\n", encoding="utf-8")

    assert [(e["seq"], e["origin"], e["headline"]) for e in query_session_logs(mgr, sid, min_seq=0)] == [
        (1, "pipeline_ledger", "Ledger started"),
        (2, "studio_journal", "Journal later"),
    ]
    assert [(e["seq"], e["origin"], e["headline"]) for e in query_session_logs(mgr, sid, min_seq=1)] == [
        (2, "studio_journal", "Journal later"),
    ]

    async def _collect() -> str:
        return "".join([f async for f in stream_logs_workflow(mgr, sid, last_event_id="1", once=True)])

    joined = asyncio.run(_collect())
    assert "id: 2\nevent: log_entry" in joined and "Journal later" in joined and "Ledger started" not in joined


def test_connector_duck_magic_false_positive_corrupt_duckdb_and_cas_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Iter 5: CSV w/ 'DUCK' in first 64B -> 201; corrupt .duckdb -> 422; cas:// URI -> 201."""
    ws, st, sr = tmp_path / "ws", tmp_path / "st", tmp_path / "sr"
    ws.mkdir()
    sr.mkdir()
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(sr))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
    cfg = OpenDVSettings.load(workspace_root=ws, state_dir=st, is_server=True)
    client = TestClient(create_app(settings=cfg, workspace_root=ws, storage_root=st), client=("127.0.0.1", 50000), base_url="http://localhost:8000")
    sid = "sess-duck-cas"
    assert client.post("/api/v1/ontology/studio/sessions", json={"session_id": sid}).status_code == 201
    url = f"/api/v1/ontology/studio/sessions/{sid}/sources/connectors"

    for raw in (b"company,ticker\nDUCKWORTH,DUCK\n", b"c1,c2,c\nDUCK,1,2\n"):
        sha = client.post("/api/v1/cas/blobs", content=raw).json()["sha256"]
        r = client.post(url, json={"adapter": "csv", "cas_hash": sha})
        assert r.status_code == 201 and r.json()["table_count"] == 1

    uri_sha = client.post("/api/v1/cas/blobs", content=b"acct,amt\nA1,42.5\n").json()["sha256"]
    r_uri = client.post(url, json={"adapter": "csv", "uri": f"cas://{uri_sha}"})
    assert r_uri.status_code == 201 and r_uri.json()["table_count"] == 1

    bad = b"\x00" * 8 + b"DUCK" + b"NOT_VALID_DUCKDB"
    bad_sha = client.post("/api/v1/cas/blobs", content=bad).json()["sha256"]
    bad_ddb = ws / "bad.duckdb"
    bad_ddb.write_bytes(bad)
    for payload in ({"adapter": "duckdb", "cas_hash": bad_sha}, {"adapter": "duckdb", "uri": str(bad_ddb)}):
        rb = client.post(url, json=payload)
        assert rb.status_code == 422 and rb.json().get("error_code") == "CORRUPT_SOURCE_BLOB"
