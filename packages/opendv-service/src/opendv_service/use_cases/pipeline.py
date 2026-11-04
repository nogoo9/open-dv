"""Pipeline execution use case: Pre-flight validation, async execution, sync execution."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

from opendv_contracts import (
    EventType,
    PipelineEvent,
    PipelineRunHandle,
    RbacRole,
    RunStatus,
)
from opendv_service.errors import NotFoundError
from opendv_service.models import (
    GateScorecardSummary,
    PipelineRunRequest,
    PipelineValidateRequest,
    ProjectRunDTO,
)
from opendv_service.rbac import normalize_rbac_role, redact_event_for_role
from opendv_service.seams import PipelineRunner, get_default_pipeline_runner
from opendv_service.state import StateStore


def validate_pipeline(
    state: StateStore,
    request: PipelineValidateRequest,
) -> dict[str, Any]:
    """Perform deterministic pre-flight input and configuration validation."""
    return state.validate_agentic_pipeline(request)


def execute_pipeline_run_sync(
    state: StateStore,
    request: PipelineRunRequest,
    *,
    role: RbacRole = RbacRole.INTERNAL_MODELER,
    pipeline_runner: PipelineRunner | None = None,
    on_stage_start: Callable[[int, str, str, str], None] | None = None,
    on_stage_complete: Callable[[int, Any], None] | None = None,
    on_stream_event: Callable[[Any], None] | None = None,
) -> dict[str, Any]:
    """Execute an end-to-end agentic pipeline run synchronously in-process."""
    eff_role = normalize_rbac_role(role, default=RbacRole.INTERNAL_MODELER)
    runner = pipeline_runner or get_default_pipeline_runner()
    return state.execute_agentic_pipeline_run(
        request,
        role=eff_role,
        pipeline_runner=runner,
        on_stage_start=on_stage_start,
        on_stage_complete=on_stage_complete,
        on_stream_event=on_stream_event,
    )


async def execute_run_async(
    state: StateStore,
    request: PipelineRunRequest,
    *,
    pipeline_runner: PipelineRunner | None = None,
) -> PipelineRunHandle:
    """Initiate an asynchronous pipeline run returning immediately with HTTP 202 handle."""
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    now_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    run_dto = ProjectRunDTO(
        run_id=run_id,
        project_id="default-project",
        harness_provider=getattr(request, "provider_override", None) or "antigravity",  # type: ignore[arg-type]
        status=RunStatus.RUNNING,
        started_at_utc=now_utc,
        current_stage_id="STAGE_00_ORCHESTRATION",
        current_view_id="WB-00",
        event_count=0,
        gate_scorecard=GateScorecardSummary(),
        active_hold_ids=[],
        events_sse_url=f"/api/v1/runs/{run_id}/events",
        gates_url=f"/api/v1/runs/{run_id}/gates",
        sandbox_url=f"/api/v1/runs/{run_id}/sandbox",
        decisions_url=f"/api/v1/runs/{run_id}/decisions",
    )
    state.runs[run_id] = run_dto
    state.run_events[run_id] = []

    runner = pipeline_runner or get_default_pipeline_runner()

    def _run_in_background() -> None:
        try:
            state.execute_agentic_pipeline_run(
                request,
                role=RbacRole.PUBLIC,
                pipeline_runner=runner,
            )
        except Exception:
            if run_id in state.runs:
                state.runs[run_id] = state.runs[run_id].model_copy(
                    update={
                        "status": RunStatus.FAILED,
                        "completed_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                )

    # Launch execution asynchronously
    asyncio.get_event_loop().run_in_executor(None, _run_in_background)

    return PipelineRunHandle(
        run_id=run_id,
        status=RunStatus.RUNNING,
        created_at_utc=now_utc,
        event_stream_url=f"/api/v1/runs/{run_id}/events",
        status_url=f"/api/v1/runs/{run_id}",
    )


async def stream_events(
    state: StateStore,
    run_id: str,
    *,
    last_event_id: str | None = None,
    role: RbacRole = RbacRole.PUBLIC,
) -> AsyncIterator[PipelineEvent]:
    """Stream normalized PipelineEvent instances for a run over time.

    Enforces role-based event redaction.
    """
    if run_id not in state.runs and run_id not in state.run_events:
        raise NotFoundError(f"Run '{run_id}' not found.")

    eff_role = normalize_rbac_role(role, default=RbacRole.PUBLIC)
    sent_indices = 0
    seen_ids: set[str] = set()

    # If last_event_id is specified, skip events up to it
    events = state.run_events.get(run_id, [])
    if last_event_id:
        for idx, ev in enumerate(events):
            if ev.get("event_id") == last_event_id:
                sent_indices = idx + 1
                break

    while True:
        current_events = state.run_events.get(run_id, [])
        while sent_indices < len(current_events):
            raw_ev = current_events[sent_indices]
            sent_indices += 1
            ev_id = raw_ev.get("event_id", f"evt-{sent_indices}")
            if ev_id in seen_ids:
                continue
            seen_ids.add(ev_id)

            redacted = redact_event_for_role(raw_ev, eff_role)
            event_type_str = redacted.get("event_type", "LOG_MESSAGE")
            try:
                event_type = EventType(event_type_str)
            except ValueError:
                event_type = EventType.LOG_MESSAGE

            yield PipelineEvent(
                event_id=ev_id,
                run_id=run_id,
                timestamp_utc=redacted.get(
                    "timestamp_utc", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                ),
                stage_id=redacted.get("stage_id", "STAGE_00_ORCHESTRATION"),
                event_type=event_type,
                payload=redacted,
            )

        run_dto = state.runs.get(run_id)
        if run_dto is not None and run_dto.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.ABORTED,
        }:
            break

        await asyncio.sleep(0.05)


__all__ = [
    "execute_pipeline_run_sync",
    "execute_run_async",
    "stream_events",
    "validate_pipeline",
]
