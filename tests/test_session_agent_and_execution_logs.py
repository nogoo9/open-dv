"""Phase 2 Track B TDD Tests: Session Agent & Execution Logs, SSE Streaming, Mid-Run Flush & Bundle Retention.

Covers:
- FR-LOG-01 / FR-LOG-02: session_logs_service merges Studio journals + linked run ledgers in monotonic order,
  filters by wizard_step/stage_id (both STAGE_00..07 and 00..06 pill aliases)/persona/source_id/job_id/category,
  W3C SSE /logs/stream w/ Last-Event-ID replay, and /logs/export .jsonl download.
- FR-LOG-05 / FR-LOG-06: Mid-run incremental flush in pipeline_execution/callbacks via loop.call_soon_threadsafe.
- Audit #14: Tail-only SSEJournalWriter cursor init without decompressing rotated .gz archives.
- FR-LOG-07 / D24: 50MB .gz rotated log preservation across bundle export & import.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from opendv_agent_harness import (
    AgenticPipelineReport,
    HarnessEventKind,
    HarnessStreamEvent,
    PipelineStepReport,
    build_observability_event_dict,
)
from opendv_contracts import EventType
from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings, PlatformStateContainer
from opendv_service.models import PipelineRunRequest
from opendv_service.studio.bundle import export_bundle, import_bundle
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.sse_journal import SSEJournalWriter
from opendv_telemetry.ledger_emitter import VaultLedgerEmitter


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


def _seed_studio_and_ledger_logs(
    studio_root: Path, state_dir: Path, session_id: str, run_id: str
) -> None:
    mgr = StudioSessionManager(studio_root=studio_root, workspace_root=studio_root.parent / "workspace")
    mgr.create_session(session_id=session_id, domain_namespace="custody_ops")
    sdir = mgr.get_session_dir(session_id)

    writer = SSEJournalWriter(session_dir=sdir, job_id="job-logs-01")
    writer.buffer_event(
        "agent_turn",
        {
            "wizard_step": 4,
            "job_id": "job-logs-01",
            "stage_id": "STAGE_00_ORCHESTRATION",
            "persona": "term-scout",
            "source_id": "src-abc",
            "category": "agent",
            "provider": "claude",
            "model_id": "claude-sonnet-4-5",
            "duration_ms": 120,
            "token_usage": {"input_tokens": 50, "output_tokens": 30},
            "masked_prompt": "Inspect <MASKED_PII> columns",
            "structured_or_raw_response": '{"concepts": []}',
            "tool_calls": [],
            "toon_diagnostics": [],
            "status": "PASS",
        },
    )
    writer.flush()

    # Linked pipeline run ledger with STAGE_03, STAGE_04, STAGE_05, STAGE_06, STAGE_07 events
    ledger_path = state_dir / "ledgers" / f"{run_id}.jsonl"
    emitter = VaultLedgerEmitter(ledger_path=ledger_path)
    for st_id, persona in [
        ("STAGE_03_SHIP_VAULT_PASS1", "ship-vault"),
        ("STAGE_04_SHIP_VAULT_PASS2", "ship-vault"),
        ("STAGE_05_AXI_DV_STATIC_GATES", "axi-dv"),
        ("STAGE_06_AXI_DV_DYNAMIC_SANDBOX", "axi-dv"),
        ("STAGE_07_DETERMINISTIC_COMPILER", "deterministic-compiler"),
    ]:
        emitter.emit(
            build_observability_event_dict(
                event_id=f"evt-{st_id.lower()}",
                event_type=EventType.STAGE_COMPLETED.value,
                run_id=run_id,
                trace_id=uuid.uuid4().hex,
                span_id=uuid.uuid4().hex[:16],
                timestamp_utc="2026-11-19T01:00:00Z",
                stage_id=st_id,
                agent_persona=persona,
                status="PASS",
                headline=f"{st_id} complete",
                business_impact="Stage finished.",
                summary_metrics={"session_id": session_id},
            )
        )


def test_session_logs_service_merges_and_filters_stage_aliases_and_export(
    tmp_path: Path, isolated_env: Path
) -> None:
    """session_logs_service & /logs merge journals+ledgers, support STAGE_00..07 and 00..06 pill aliases, and export .jsonl."""
    from opendv_service.studio.session_logs_service import query_session_logs

    state_dir = tmp_path / "state"
    _seed_studio_and_ledger_logs(isolated_env, state_dir, "logs-sess-01", "run-logs-01")
    assert callable(query_session_logs)

    client = _client(tmp_path)
    all_resp = client.get("/api/v1/ontology/studio/sessions/logs-sess-01/logs")
    assert all_resp.status_code == 200, all_resp.text
    entries = all_resp.json()["entries"]
    assert len(entries) >= 6
    seqs = [e["seq"] for e in entries]
    assert seqs == sorted(seqs)

    # Pill alias '03' resolves to STAGE_03_SHIP_VAULT_PASS1 + STAGE_04_SHIP_VAULT_PASS2
    pill03 = client.get("/api/v1/ontology/studio/sessions/logs-sess-01/logs", params={"stage_id": "03"})
    assert pill03.status_code == 200
    assert {e["stage_id"] for e in pill03.json()["entries"]} == {
        "STAGE_03_SHIP_VAULT_PASS1",
        "STAGE_04_SHIP_VAULT_PASS2",
    }

    # Pill alias '05' -> STAGE_07_DETERMINISTIC_COMPILER, '06' -> STAGE_06_AXI_DV_DYNAMIC_SANDBOX
    pill05 = client.get("/api/v1/ontology/studio/sessions/logs-sess-01/logs", params={"stage_id": "05"})
    assert {e["stage_id"] for e in pill05.json()["entries"]} == {"STAGE_07_DETERMINISTIC_COMPILER"}
    pill06 = client.get("/api/v1/ontology/studio/sessions/logs-sess-01/logs", params={"stage_id": "06"})
    assert {e["stage_id"] for e in pill06.json()["entries"]} == {"STAGE_06_AXI_DV_DYNAMIC_SANDBOX"}

    # Canonical STAGE_05_AXI_DV_STATIC_GATES & persona/source/step/job/category filters
    canon = client.get(
        "/api/v1/ontology/studio/sessions/logs-sess-01/logs",
        params={"stage_id": "STAGE_05_AXI_DV_STATIC_GATES", "persona": "axi-dv"},
    )
    assert len(canon.json()["entries"]) == 1

    # Export .jsonl
    exp = client.get("/api/v1/ontology/studio/sessions/logs-sess-01/logs/export")
    assert exp.status_code == 200
    lines = [json.loads(ln) for ln in exp.text.strip().splitlines() if ln.strip()]
    assert len(lines) >= 6


def test_session_logs_sse_stream_last_event_id_and_mid_run_incremental_flush(
    tmp_path: Path, isolated_env: Path
) -> None:
    """SSE /logs/stream supports Last-Event-ID replay AND pipeline_execution flushes ledger entries mid-run via call_soon_threadsafe."""
    state_dir = tmp_path / "state"
    _seed_studio_and_ledger_logs(isolated_env, state_dir, "logs-sse-01", "run-sse-01")

    client = _client(tmp_path)
    stream_resp = client.get(
        "/api/v1/ontology/studio/sessions/logs-sse-01/logs/stream",
        headers={"Last-Event-ID": "1"},
    )
    assert stream_resp.status_code == 200, stream_resp.text
    assert "text/event-stream" in stream_resp.headers.get("content-type", "")
    assert "id: 1\n" not in stream_resp.text
    assert "data:" in stream_resp.text

    # Verify VaultLedgerEmitter uses loop.call_soon_threadsafe when emitting from a worker thread
    loop = asyncio.new_event_loop()
    called_threadsafe = {"count": 0}
    orig_call = loop.call_soon_threadsafe

    def _spy_call(cb: Any, *args: Any) -> Any:
        called_threadsafe["count"] += 1
        return orig_call(cb, *args)

    try:
        emitter = VaultLedgerEmitter(ledger_path=tmp_path / "live_ledger.jsonl")
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        # Register subscriber bound to loop (supporting tuple or loop registration)
        emitter.subscribe_queue(q, loop=loop) if hasattr(emitter, "subscribe_queue") else emitter._subscribers.add(q)  # type: ignore[arg-type]
        loop.call_soon_threadsafe = _spy_call  # type: ignore[method-assign]
        emitter.emit(
            build_observability_event_dict(
                event_id="evt-mid-run-01",
                event_type=EventType.STAGE_STARTED.value,
                run_id="run-mid-1",
                trace_id=uuid.uuid4().hex,
                span_id=uuid.uuid4().hex[:16],
                timestamp_utc="2026-11-19T01:05:00Z",
                stage_id="STAGE_01_SCOUT_SOURCE",
                agent_persona="scout-source",
                status="PASS",
                headline="Mid-run stage started.",
                business_impact="Flushing mid-run.",
            )
        )
        assert (tmp_path / "live_ledger.jsonl").is_file()
        assert called_threadsafe["count"] >= 1
    finally:
        loop.close()


def test_sse_journal_writer_tail_only_cursor_init_skips_gz_decompression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SSEJournalWriter.__init__ uses tail-only cursor scan without decompressing rotated .gz segments (Audit #14)."""
    sdir = tmp_path / "sess-tail"
    jdir = sdir / "jobs"
    jdir.mkdir(parents=True, exist_ok=True)

    gz_path = jdir / "job-tail-1.events.jsonl.1.gz"
    with gzip.open(gz_path, "wt", encoding="utf-8") as gz_fp:
        gz_fp.write(json.dumps({"id": 1, "event": "old", "data": {}, "timestamp": ""}) + "\n")
        gz_fp.write(json.dumps({"id": 2, "event": "old", "data": {}, "timestamp": ""}) + "\n")

    active_path = jdir / "job-tail-1.events.jsonl"
    active_path.write_text(
        json.dumps({"id": 3, "event": "active", "data": {}, "timestamp": ""}) + "\n",
        encoding="utf-8",
    )

    def _forbid_gz(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("SSEJournalWriter.__init__ must NOT decompress rotated .gz segments")

    monkeypatch.setattr(gzip, "open", _forbid_gz)
    writer = SSEJournalWriter(session_dir=sdir, job_id="job-tail-1")
    evt = writer.buffer_event("next", {"ok": True})
    assert evt.id == 4


def test_bundle_export_import_preserves_rotated_gz_session_logs(tmp_path: Path) -> None:
    """Rotated .gz and active .jsonl session logs survive bundle export/import and remain queryable."""
    from opendv_service.studio.session_logs_service import query_session_logs

    src_root = tmp_path / "src_studio"
    dst_root = tmp_path / "dst_studio"
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    mgr = StudioSessionManager(studio_root=src_root, workspace_root=ws)
    mgr.create_session(session_id="bundle-logs-01", domain_namespace="custody_ops")
    sdir = mgr.get_session_dir("bundle-logs-01")
    jdir = sdir / "jobs"
    jdir.mkdir(parents=True, exist_ok=True)

    gz_file = jdir / "job-b1.events.jsonl.1.gz"
    with gzip.open(gz_file, "wt", encoding="utf-8") as gz_fp:
        gz_fp.write(
            json.dumps(
                {
                    "id": 1,
                    "event": "agent_turn",
                    "data": {"wizard_step": 4, "stage_id": "STAGE_00_ORCHESTRATION", "persona": "term-scout"},
                    "timestamp": "2026-11-19T00:00:00Z",
                }
            )
            + "\n"
        )
    active_file = jdir / "job-b1.events.jsonl"
    active_file.write_text(
        json.dumps(
            {
                "id": 2,
                "event": "agent_turn",
                "data": {"wizard_step": 4, "stage_id": "STAGE_01_SCOUT_SOURCE", "persona": "ontology-architect"},
                "timestamp": "2026-11-19T00:00:01Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    bundle_zip = tmp_path / "bundle.odvstudio.zip"
    export_bundle(sdir, bundle_zip, security_manager=mgr.security_manager, allow_cli_output_path=True)

    dst_mgr = StudioSessionManager(studio_root=dst_root, workspace_root=ws)
    restored_target = dst_mgr.get_session_dir("bundle-logs-restored")
    import_bundle(bundle_zip, restored_target, dst_mgr.security_manager, new_session_id="bundle-logs-restored")
    restored_sdir = dst_mgr.get_session_dir("bundle-logs-restored")
    assert (restored_sdir / "jobs" / "job-b1.events.jsonl.1.gz").is_file()
    assert (restored_sdir / "jobs" / "job-b1.events.jsonl").is_file()

    restored_entries = query_session_logs(dst_mgr, "bundle-logs-restored")
    assert len(restored_entries) >= 2


def test_studio_agent_runner_telemetry_captures_masked_prompt_and_response(
    tmp_path: Path, isolated_env: Path
) -> None:
    """StudioAgentRunner agent_telemetry persists PII-masked masked_prompt and structured_or_raw_response with zero MNPI leakage."""
    from opendv_agent_harness.models import (
        AgentTaskSpec,
        HarnessConfig,
        HarnessRunResult,
        HarnessStopReason,
        HarnessUsageMetrics,
        ModelTier,
    )
    from opendv_contracts.dtos.ontology import (
        AddConceptOp,
        EvidenceRef,
        OntologyDelta,
        OntologyProposal,
    )
    from opendv_core.genesis import create_genesis_ontology
    from opendv_core.ontology_hashing import compute_ontology_sha256
    from opendv_service.studio.agent_runner import StudioAgentRunner
    from opendv_service.studio.job_runners import run_construct_session, run_parse_session_sources

    mnpi_sentinel = "MNPI_CONFIDENTIAL_DEAL_XYZ_9001"
    raw_email = "alice.doe@private-fund.org"
    raw_ssn = "123-45-6789"
    raw_ein = "12-3456789"

    mgr = StudioSessionManager(studio_root=isolated_env, workspace_root=tmp_path / "workspace")
    mgr.create_session(session_id="tel-mask-01", domain_namespace="custody_ops")
    mgr.add_text_source(
        "tel-mask-01",
        text=f"Secret memo {mnpi_sentinel} must stay out of prompts and logs.",
        source_name="secret_mnpi.md",
        do_not_send_to_agent=True,
    )
    mgr.add_text_source(
        "tel-mask-01",
        text=f"Public owner policy for {raw_email} SSN {raw_ssn} EIN {raw_ein}.",
        source_name="public_policy.md",
        do_not_send_to_agent=False,
    )
    run_parse_session_sources(mgr, "tel-mask-01", ocr_mode="off")

    class _TelemetrySpyHarness:
        def __init__(self) -> None:
            self._cfg = HarnessConfig(
                provider="claude",
                workspace_dir=tmp_path,
                state_dir=tmp_path / "s",
                app_data_dir=tmp_path / "a",
                save_dir=tmp_path / "sv",
            )

        @property
        def provider_name(self) -> str:
            return "claude"

        @property
        def config(self) -> HarnessConfig:
            return self._cfg

        def resolve_model(self, tier: ModelTier, persona: Any = None) -> str:
            return "claude-sonnet-4-5"

        async def cancel_active_turn(self, task_id: str) -> None:
            return None

        async def stream_task(self, spec: AgentTaskSpec[Any]) -> Any:
            if False:
                yield  # pragma: no cover

        async def run_task(self, spec: AgentTaskSpec[Any], *, on_event: Any = None) -> HarnessRunResult[Any]:
            base = create_genesis_ontology("custody_ops")
            delta = OntologyDelta(
                delta_id="d-tel-01",
                parent_sha256=compute_ontology_sha256(base),
                base_version="0.0.0",
                target_version="1.0.0",
                domain_namespace="custody_ops",
                operations=[
                    AddConceptOp(
                        op_id="op-tel-1",
                        concept_uri="urn:ontology:entity:owner",
                        concept_name="Owner",
                        target_hub="hub_owner",
                        definition="Beneficial owner.",
                        rationale=f"Grounded contact {raw_email} EIN {raw_ein}.",
                        evidence_refs=[EvidenceRef(evidence_type="document_span", snippet="owner policy", confidence=0.9)],
                    )
                ],
            )
            prop = OntologyProposal(
                proposal_id="prop-tel-01",
                recommender_id="agent:tel",
                base_ontology_sha256=compute_ontology_sha256(base),
                delta=delta,
                confidence=0.91,
                rationale=f"Proposed owner entity; SSN {raw_ssn} redacted.",
            )
            return HarnessRunResult(
                task_id=spec.task_id,
                run_id=spec.run_id,
                session_id="tel-mask-01",
                trace_id=spec.trace_id,
                span_id=uuid.uuid4().hex[:16],
                provider="claude",
                model_used="claude-sonnet-4-5",
                stop_reason=HarnessStopReason.COMPLETED,
                text_output=prop.model_dump_json(),
                usage=HarnessUsageMetrics(input_tokens=100, output_tokens=50, total_tokens=150, cost_usd=0.01),
                structured_output=prop,
            )

    sdir = mgr.get_session_dir("tel-mask-01")
    writer = SSEJournalWriter(session_dir=sdir, job_id="job-tel-mask-01")
    runner = StudioAgentRunner(harness=_TelemetrySpyHarness())
    run_construct_session(
        mgr,
        "tel-mask-01",
        mode="agent",
        job_id="job-tel-mask-01",
        agent_runner=runner,
        writer=writer,
    )

    client = _client(tmp_path)
    logs_resp = client.get("/api/v1/ontology/studio/sessions/tel-mask-01/logs")
    assert logs_resp.status_code == 200, logs_resp.text
    tel_entries = [e for e in logs_resp.json()["entries"] if e["event"] == "agent_telemetry"]
    assert len(tel_entries) >= 1
    entry = tel_entries[-1]
    assert entry["masked_prompt"], "masked_prompt must be populated on agent_telemetry"
    assert entry["structured_or_raw_response"], "structured_or_raw_response must be populated on agent_telemetry"
    assert "<MASKED_PII>" in entry["masked_prompt"]
    assert "<MASKED_PII>" in entry["structured_or_raw_response"]
    for forbidden in (mnpi_sentinel, raw_email, raw_ssn, raw_ein):
        assert forbidden not in entry["masked_prompt"]
        assert forbidden not in entry["structured_or_raw_response"]
        assert forbidden not in json.dumps(entry)

    exp = client.get("/api/v1/ontology/studio/sessions/tel-mask-01/logs/export")
    assert exp.status_code == 200
    assert mnpi_sentinel not in exp.text
    assert raw_email not in exp.text
    assert raw_ssn not in exp.text
    assert raw_ein not in exp.text


def test_fibo_qa_persists_masked_prompt_and_response_to_session_logs(
    tmp_path: Path, isolated_env: Path
) -> None:
    """answer_fibo_qa / ask_fibo_qa_workflow writes a PII-masked agent_telemetry entry readable via /logs and /logs/export."""
    from opendv_agent_harness.models import (
        AgentTaskSpec,
        HarnessConfig,
        HarnessRunResult,
        HarnessStopReason,
        HarnessUsageMetrics,
        ModelTier,
    )
    from opendv_contracts.dtos.ontology import EvidenceRef
    from opendv_service.studio.fibo_qa_service import FiboQAResponse
    from opendv_service.studio.job_runners import run_parse_session_sources
    from opendv_service.studio.wizard_workflows import ask_fibo_qa_workflow

    mnpi_sentinel = "MNPI_QA_RESTRICTED_8841"
    raw_email = "steward@custody-secret.org"
    raw_ssn = "987-65-4321"
    raw_ein = "55-1234567"

    mgr = StudioSessionManager(studio_root=isolated_env, workspace_root=tmp_path / "workspace")
    mgr.create_session(session_id="qa-logs-01", domain_namespace="custody_ops")
    mgr.add_text_source(
        "qa-logs-01",
        text=f"Do not disclose {mnpi_sentinel} merger plan.",
        source_name="restricted_qa.md",
        do_not_send_to_agent=True,
    )
    mgr.add_text_source(
        "qa-logs-01",
        text="Settlement SLA requires T+1 confirmation.",
        source_name="sla.md",
        do_not_send_to_agent=False,
    )
    run_parse_session_sources(mgr, "qa-logs-01", ocr_mode="off")

    class _QaLogSpyHarness:
        def __init__(self) -> None:
            self._cfg = HarnessConfig(
                provider="claude",
                workspace_dir=tmp_path,
                state_dir=tmp_path / "s",
                app_data_dir=tmp_path / "a",
                save_dir=tmp_path / "sv",
            )

        @property
        def provider_name(self) -> str:
            return "claude"

        @property
        def config(self) -> HarnessConfig:
            return self._cfg

        def resolve_model(self, tier: ModelTier, persona: Any = None) -> str:
            return "claude-sonnet-4-5"

        async def cancel_active_turn(self, task_id: str) -> None:
            return None

        async def stream_task(self, spec: AgentTaskSpec[Any]) -> Any:
            if False:
                yield  # pragma: no cover

        async def run_task(self, spec: AgentTaskSpec[Any], *, on_event: Any = None) -> HarnessRunResult[Any]:
            qa_resp = FiboQAResponse(
                answer=f"Map Settlement SLA to FIBO Occurrence; contact EIN {raw_ein} or {raw_email}.",
                citations=[EvidenceRef(evidence_type="document_span", source_label="sla.md", snippet="T+1 confirmation", confidence=0.95)],
                suggested_actions=[],
            )
            return HarnessRunResult(
                task_id=spec.task_id,
                run_id=spec.run_id,
                session_id="qa-logs-01",
                trace_id=spec.trace_id,
                span_id=uuid.uuid4().hex[:16],
                provider="claude",
                model_used="claude-sonnet-4-5",
                stop_reason=HarnessStopReason.COMPLETED,
                text_output=qa_resp.model_dump_json(),
                usage=HarnessUsageMetrics(input_tokens=45, output_tokens=25, total_tokens=70, cost_usd=0.003),
                structured_output=qa_resp,
            )

    res = ask_fibo_qa_workflow(
        session_mgr=mgr,
        session_id="qa-logs-01",
        question=f"How should {raw_email} (SSN {raw_ssn}) classify Settlement SLA?",
        agent_runner=_QaLogSpyHarness(),
    )
    assert "Settlement SLA" in res["answer"]

    client = _client(tmp_path)
    logs_resp = client.get("/api/v1/ontology/studio/sessions/qa-logs-01/logs")
    assert logs_resp.status_code == 200, logs_resp.text
    qa_entries = [
        e
        for e in logs_resp.json()["entries"]
        if e["event"] == "agent_telemetry" and e.get("persona") == "domain-clarifier"
    ]
    assert len(qa_entries) >= 1
    entry = qa_entries[-1]
    assert entry["wizard_step"] == 4
    assert entry["model_id"] == "claude-sonnet-4-5"
    assert entry["status"] == "PASS"
    assert entry["token_usage"]["total_tokens"] == 70
    assert entry["masked_prompt"]
    assert entry["structured_or_raw_response"]
    assert "<MASKED_PII>" in entry["masked_prompt"]
    assert "<MASKED_PII>" in entry["structured_or_raw_response"]
    for forbidden in (mnpi_sentinel, raw_email, raw_ssn, raw_ein):
        assert forbidden not in entry["masked_prompt"]
        assert forbidden not in entry["structured_or_raw_response"]
        assert forbidden not in json.dumps(entry)

    exp = client.get("/api/v1/ontology/studio/sessions/qa-logs-01/logs/export")
    assert exp.status_code == 200
    assert "agent_telemetry" in exp.text
    for forbidden in (mnpi_sentinel, raw_email, raw_ssn, raw_ein):
        assert forbidden not in exp.text


def test_ledger_normalization_and_pipeline_callbacks_project_masked_prompt_and_response(
    tmp_path: Path, isolated_env: Path
) -> None:
    """_normalize_ledger_record and pipeline callbacks project PII-masked prompt/response instead of None."""
    from opendv_contracts import RunStatus
    from opendv_service.models import ProjectRunDTO
    from opendv_service.runs.callbacks import build_pipeline_callbacks
    from opendv_service.runs.engine import RunContext, RunExecutionEngine
    from opendv_service.studio.session_logs_service import (
        _normalize_ledger_record,
        query_session_logs,
    )

    # 1. Direct _normalize_ledger_record projection from summary_metrics and payload
    rec_metrics = {
        "timestamp_utc": "2026-11-19T02:00:00Z",
        "event_type": "STAGE_COMPLETED",
        "run_id": "run-norm-1",
        "stage_id": "STAGE_04_SHIP_VAULT_PASS2",
        "agent_persona": "ship-vault",
        "status": "PASS",
        "layer2_visual_payload": {
            "semantic_hash_pills": [],
            "summary_metrics": {
                "session_id": "norm-sess-1",
                "masked_prompt": "Prompt for bob@firm.io SSN 111-22-3333",
                "structured_or_raw_response": '{"contract": "ok", "ein": "44-5556667"}',
            },
        },
    }
    norm1 = _normalize_ledger_record(rec_metrics, 1)
    assert norm1["masked_prompt"] == "Prompt for <MASKED_PII> SSN <MASKED_PII>"
    assert norm1["structured_or_raw_response"] == '{"contract": "ok", "ein": "<MASKED_PII>"}'

    rec_payload = {
        "timestamp_utc": "2026-11-19T02:01:00Z",
        "event_type": "TOKEN_DELTA",
        "run_id": "run-norm-1",
        "stage_id": "STAGE_03_SHIP_VAULT_PASS1",
        "agent_persona": "ship-vault",
        "status": "PASS",
        "payload": {
            "masked_prompt": "Inspect carol@bank.com",
            "structured_or_raw_response": {"hub": "hub_position", "tax_id": "222-33-4444"},
        },
    }
    norm2 = _normalize_ledger_record(rec_payload, 2)
    assert norm2["masked_prompt"] == "Inspect <MASKED_PII>"
    assert norm2["structured_or_raw_response"] is not None
    assert "<MASKED_PII>" in norm2["structured_or_raw_response"]
    assert "222-33-4444" not in norm2["structured_or_raw_response"]

    # 2. End-to-end callbacks -> VaultLedgerEmitter -> query_session_logs
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "state"
    state = PlatformStateContainer(workspace_root=ws, state_dir=state_dir)
    mgr = StudioSessionManager(studio_root=isolated_env, workspace_root=ws)
    mgr.create_session(session_id="cb-logs-01", domain_namespace="custody_ops")

    run_id = "run-cb-01"
    state.runs[run_id] = ProjectRunDTO(
        run_id=run_id,
        project_id="default-project",
        harness_provider="claude",
        status=RunStatus.RUNNING,
        started_at_utc="2026-11-19T02:05:00Z",
        current_stage_id="STAGE_04_SHIP_VAULT_PASS2",
        event_count=0,
        events_sse_url=f"/api/v1/runs/{run_id}/events",
        gates_url=f"/api/v1/runs/{run_id}/gates",
        sandbox_url=f"/api/v1/runs/{run_id}/sandbox",
        decisions_url=f"/api/v1/runs/{run_id}/decisions",
        session_id="cb-logs-01",
    )
    engine = RunExecutionEngine()
    ctx = RunContext(run_id=run_id, session_id="cb-logs-01")
    cbs = build_pipeline_callbacks(ctx, state, engine)

    cbs.on_stage_complete(
        0,
        PipelineStepReport(
            stage_id="STAGE_04_SHIP_VAULT_PASS2",
            persona="ship-vault",
            model_used="claude-sonnet-4-5",
            status="PASS",
            duration_ms=55.0,
            summary="Contract synthesized.",
            metrics={
                "provider": "claude",
                "masked_prompt": "Stage prompt for dave@bank.com",
                "structured_or_raw_response": '{"sat": "sat_positions", "ssn": "123-45-6789"}',
            },
        ),
    )
    cbs.on_stream_event(
        HarnessStreamEvent(
            run_id=run_id,
            task_id="task-cb-1",
            trace_id=uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
            provider="claude",
            persona="ship-vault",
            stage_id="STAGE_04_SHIP_VAULT_PASS2",
            kind=HarnessEventKind.TURN_COMPLETE,
            tool_result={
                "masked_prompt": "Turn prompt for eve@bank.com",
                "structured_output": {"hub": "hub_position", "ein": "12-3456789"},
                "usage": {"total_tokens": 88},
            },
        )
    )

    entries = query_session_logs(
        mgr,
        "cb-logs-01",
        extra_ledger_dirs=[state_dir / "ledgers"],
    )
    ledger_entries = [e for e in entries if e["origin"] == "pipeline_ledger"]
    assert len(ledger_entries) >= 2
    for le in ledger_entries:
        assert le["masked_prompt"], f"Expected masked_prompt on {le}"
        assert le["structured_or_raw_response"], f"Expected structured_or_raw_response on {le}"
        assert "<MASKED_PII>" in le["masked_prompt"]
        assert "<MASKED_PII>" in le["structured_or_raw_response"]
        for raw_pii in ("dave@bank.com", "eve@bank.com", "123-45-6789", "12-3456789"):
            assert raw_pii not in le["masked_prompt"]
            assert raw_pii not in le["structured_or_raw_response"]

