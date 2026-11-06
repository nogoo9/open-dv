"""Unit and integration tests for async run execution and SSE event streaming (Decision D2)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from opendv_agent_harness.pipeline import (
    AgenticPipelineReport,
    DeterministicValidationReport,
)
from opendv_contracts import RunStatus
from opendv_service import (
    CreateProjectRequest,
    CreateRunRequest,
    NotFoundError,
    OpenDVService,
    PipelineRunRequest,
    RunContext,
    StateStore,
    format_sse_frame,
    subscribe_run_sse,
)


@pytest.fixture
def service_env(tmp_path: Path) -> tuple[OpenDVService, StateStore]:
    """Provide an initialized OpenDVService with fresh StateStore."""
    store = StateStore(state_dir=tmp_path / "state")
    service = OpenDVService(state_store=store)
    return service, store


def test_format_sse_frame_w3c_spec() -> None:
    """Verify format_sse_frame satisfies W3C Server-Sent Events framing specification."""
    # Frame with event type, id, and dictionary payload
    frame = format_sse_frame("stage_start", "evt-001", {"stage_id": "STAGE_01"})
    assert frame == 'event: stage_start\nid: evt-001\ndata: {"stage_id": "STAGE_01"}\n\n'

    # Frame with string data and no event_id
    frame_no_id = format_sse_frame("run_complete", None, '{"status": "COMPLETED"}')
    assert frame_no_id == 'event: run_complete\ndata: {"status": "COMPLETED"}\n\n'


def test_submit_pipeline_run_returns_handle(service_env: tuple[OpenDVService, StateStore]) -> None:
    """Verify submit_pipeline_run returns PipelineRunHandle with 202-ready metadata."""
    service, _ = service_env

    def _dummy_runner(spec_obj: Any, *args: Any, **kwargs: Any) -> AgenticPipelineReport:
        now = "2026-11-06T00:00:00Z"
        val = DeterministicValidationReport(
            stage="INPUT_VALIDATION", passed=True, checked_items_count=1, diagnostics=[]
        )
        return AgenticPipelineReport(
            run_id=getattr(spec_obj, "run_id", "run-dummy"),
            pipeline_id=spec_obj.pipeline_id,
            project_id=spec_obj.project_id,
            domain_namespace=spec_obj.domain_namespace,
            status="COMPLETED",
            overall_passed=True,
            started_at=now,
            completed_at=now,
            total_duration_ms=10.0,
            harness_setup={},
            input_validation=val,
            output_validation=val,
            wave_reports=[],
            step_reports=[],
            intermediate_artifacts=[],
            final_artifacts={},
            observability_events=[],
        )

    req = PipelineRunRequest(
        input_spec_path="examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml",
        harness_config_path="examples/pipeline_samples/global_commerce_vault/harness_config.yaml",
        provider_override="agy",
    )
    handle = service.run_engine.submit_pipeline_run(
        service.state,
        req,
        pipeline_runner=_dummy_runner,
    )
    assert handle.run_id.startswith("run-")
    assert handle.status == RunStatus.RUNNING
    assert handle.event_stream_url == f"/api/v1/runs/{handle.run_id}/events"
    assert handle.status_url == f"/api/v1/runs/{handle.run_id}"
    assert handle.created_at_utc is not None
    assert handle.created_at == handle.created_at_utc


@pytest.mark.asyncio
async def test_submit_run_async_returns_handle(
    service_env: tuple[OpenDVService, StateStore],
) -> None:
    """Verify submit_run returns PipelineRunHandle for project runs."""
    service, _ = service_env
    service.create_project(
        CreateProjectRequest(
            project_id="proj-apex-horizon-pe",
            display_name="Apex PE",
            domain_namespace="pe",
            contract_path="examples/private_equity/dv_pe_model_contract.yaml",
        )
    )
    req = CreateRunRequest(
        project_id="proj-apex-horizon-pe",
        harness_provider="agy",
    )
    handle = await service.create_run_async(req)
    assert handle.run_id.startswith("run-")
    assert handle.status == RunStatus.RUNNING
    assert handle.project_id == "proj-apex-horizon-pe"
    assert handle.harness_provider == "agy"


@pytest.mark.asyncio
async def test_sse_returns_404_for_unknown_run(
    service_env: tuple[OpenDVService, StateStore],
) -> None:
    """Verify subscribe_run_sse eagerly raises NotFoundError for non-existent run ID."""
    _service, store = service_env
    with pytest.raises(NotFoundError, match="Run 'run-unknown-9999' not found"):
        gen = subscribe_run_sse(None, store, "run-unknown-9999")
        await anext(gen)


@pytest.mark.asyncio
async def test_sse_event_stream_live_events_and_completion(
    service_env: tuple[OpenDVService, StateStore],
) -> None:
    """Verify live event streaming and terminal run_complete frame emission."""
    service, store = service_env
    engine = service.run_engine
    context = RunContext(run_id="run-test-live-01")
    engine._contexts["run-test-live-01"] = context

    # Run subscriber in background async task
    collected_frames: list[str] = []

    async def _subscriber() -> None:
        async for frame in subscribe_run_sse(context, store, "run-test-live-01", ping_interval=1.0):
            collected_frames.append(frame)

    sub_task = asyncio.create_task(_subscriber())
    await asyncio.sleep(0.02)  # Give subscriber time to attach

    # Broadcast events
    engine.broadcast_event(
        context,
        {
            "event_id": "evt-001",
            "event_type": "stage_start",
            "stage_id": "STAGE_01_INGESTION",
            "payload": {"step": 1},
        },
    )
    engine.broadcast_event(
        context,
        {
            "event_id": "evt-002",
            "event_type": "stage_complete",
            "stage_id": "STAGE_01_INGESTION",
            "payload": {"status": "PASS"},
        },
    )
    await asyncio.sleep(0.02)

    # Mark done
    with context.lock:
        context.is_done = True
        context.final_status = RunStatus.COMPLETED
        term = {
            "event_id": "evt-complete",
            "event_type": "run_complete",
            "payload": {"status": "COMPLETED"},
        }
        for q, loop in list(context.subscribers):
            loop.call_soon_threadsafe(q.put_nowait, term)

    await asyncio.wait_for(sub_task, timeout=2.0)

    assert len(collected_frames) == 3
    assert "evt-001" in collected_frames[0]
    assert "evt-002" in collected_frames[1]
    assert "event: run_complete" in collected_frames[2]
    assert '{"status": "COMPLETED"}' in collected_frames[2]


@pytest.mark.asyncio
async def test_sse_reconnection_with_last_event_id(
    service_env: tuple[OpenDVService, StateStore],
) -> None:
    """Verify Last-Event-ID reconnection replays strictly after matched event."""
    service, store = service_env
    service.create_project(
        CreateProjectRequest(
            project_id="default-project",
            display_name="Default",
            domain_namespace="test",
            contract_path="examples/private_equity/dv_pe_model_contract.yaml",
        )
    )
    dto = store.create_run(CreateRunRequest(project_id="default-project", harness_provider="agy"))
    run_id = dto.run_id

    # Pre-populate state events
    store.run_events[run_id] = [
        {"event_id": "evt-01", "event_type": "message", "payload": "first"},
        {"event_id": "evt-02", "event_type": "message", "payload": "second"},
        {"event_id": "evt-03", "event_type": "message", "payload": "third"},
    ]

    # Replay starting after evt-02
    frames = [
        f
        async for f in subscribe_run_sse(
            None,
            store,
            run_id,
            last_event_id="evt-02",
        )
    ]

    # Should replay evt-03 and terminal frame, omitting evt-01 and evt-02
    assert len(frames) == 2
    assert "id: evt-03" in frames[0]
    assert "evt-01" not in frames[0]
    assert "evt-02" not in frames[0]
    assert "event: run_complete" in frames[1]


@pytest.mark.asyncio
async def test_sse_heartbeat_ping_comments(service_env: tuple[OpenDVService, StateStore]) -> None:
    """Verify ': ping\\n\\n' comment frames are emitted during idle periods."""
    service, store = service_env
    run_id = "run-test-ping-03"
    context = RunContext(run_id=run_id)
    service.run_engine._contexts[run_id] = context

    frames: list[str] = []

    async def _consume() -> None:
        async for frame in subscribe_run_sse(context, store, run_id, ping_interval=0.05):
            frames.append(frame)
            if len(frames) >= 2:
                break

    await asyncio.wait_for(_consume(), timeout=1.0)
    assert frames[0] == ": ping\n\n"
    assert frames[1] == ": ping\n\n"


def test_run_abort_cooperative_cancellation(
    service_env: tuple[OpenDVService, StateStore],
) -> None:
    """Verify abort_run triggers context.abort_event and transitions run to ABORTED."""
    service, store = service_env
    engine = service.run_engine

    def _blocking_runner(*args: Any, **kwargs: Any) -> None:
        on_start = kwargs.get("on_stage_start")
        if on_start:
            on_start(0, "STAGE_00_ORCHESTRATION", "modeler", "reasoning")
        for _ in range(50):
            if kwargs.get("on_stream_event"):
                dummy_stream = type("StreamEvent", (), {"delta_text": "tok"})()
                kwargs["on_stream_event"](dummy_stream)
            time.sleep(0.05)

    req = PipelineRunRequest(
        input_spec_path="examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml",
        harness_config_path="examples/pipeline_samples/global_commerce_vault/harness_config.yaml",
        provider_override="agy",
    )
    handle = engine.submit_pipeline_run(store, req, pipeline_runner=_blocking_runner)
    run_id = handle.run_id

    # Verify context exists and abort_event is clear
    context = engine.get_context(run_id)
    assert context is not None
    assert not context.abort_event.is_set()

    # Call abort
    aborted_dto = service.abort_run(run_id)
    assert aborted_dto.status == RunStatus.ABORTED
    assert context.abort_event.is_set()

    # Context should be marked done as ABORTED
    for _ in range(30):
        if context.is_done:
            break
        time.sleep(0.05)
    assert context.is_done
    assert context.final_status == RunStatus.ABORTED


def test_concurrent_runs_isolation(service_env: tuple[OpenDVService, StateStore]) -> None:
    """Verify concurrent runs maintain isolated event contexts without crosstalk."""
    service, _ = service_env
    engine = service.run_engine

    ctx_a = RunContext(run_id="run-concurrent-A")
    ctx_b = RunContext(run_id="run-concurrent-B")
    engine._contexts["run-concurrent-A"] = ctx_a
    engine._contexts["run-concurrent-B"] = ctx_b

    engine.broadcast_event(ctx_a, {"event_id": "evt-A-1", "data": "A"})
    engine.broadcast_event(ctx_b, {"event_id": "evt-B-1", "data": "B"})

    with ctx_a.lock:
        a_events = [e["event_id"] for e in ctx_a.event_buffer]
    with ctx_b.lock:
        b_events = [e["event_id"] for e in ctx_b.event_buffer]

    assert a_events == ["evt-A-1"]
    assert b_events == ["evt-B-1"]
    assert "evt-B-1" not in a_events
    assert "evt-A-1" not in b_events
