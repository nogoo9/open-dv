"""Pipeline lifecycle callbacks for async run execution and live event broadcasting."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from opendv_contracts import EventType
from opendv_service.errors import PipelineAbortedError

if TYPE_CHECKING:
    from opendv_service.runs.engine import RunContext, RunExecutionEngine
    from opendv_service.state import StateStore


@dataclass(frozen=True)
class PipelineCallbacks:
    """Callback handlers wired into the pipeline runner."""

    on_stage_start: Callable[[int, str, str, str], None]
    on_stage_complete: Callable[[int, Any], None]
    on_stream_event: Callable[[Any], None]


def build_pipeline_callbacks(
    context: RunContext,
    state: StateStore,
    engine: RunExecutionEngine,
) -> PipelineCallbacks:
    """Construct lifecycle callbacks that monitor abort requests and broadcast live events."""

    def _on_stage_start(wave_idx: int, stage_id: str, persona: str, model: str) -> None:
        if context.abort_event.is_set():
            raise PipelineAbortedError(f"Run '{context.run_id}' aborted by user.")

        now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        evt_id = f"evt-{context.run_id}-{stage_id.lower()}-start-{uuid.uuid4().hex[:6]}"
        evt = {
            "event_id": evt_id,
            "run_id": context.run_id,
            "timestamp_utc": now_iso,
            "stage_id": stage_id,
            "event_type": EventType.STAGE_STARTED.value,
            "payload": {
                "wave_index": wave_idx,
                "stage_id": stage_id,
                "agent_persona": persona,
                "model": model,
                "status": "RUNNING",
            },
        }

        if context.run_id in state.runs:
            curr = state.runs[context.run_id]
            state.runs[context.run_id] = curr.model_copy(
                update={
                    "current_stage_id": stage_id,
                    "event_count": curr.event_count + 1,
                }
            )

        engine.broadcast_event(context, evt)

    def _on_stage_complete(wave_idx: int, step_report: Any) -> None:
        if context.abort_event.is_set():
            raise PipelineAbortedError(f"Run '{context.run_id}' aborted by user.")

        st_id = getattr(step_report, "stage_id", "STAGE_00_ORCHESTRATION")
        now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        evt_id = f"evt-{context.run_id}-{st_id.lower()}-complete-{uuid.uuid4().hex[:6]}"
        status_raw = str(getattr(step_report, "status", "PASS"))
        passed = status_raw in {"PASS", "COMPLETED", "AUTO-FIX", "AUTO_REPAIRED"}
        ev_type = EventType.STAGE_COMPLETED.value if passed else EventType.STAGE_FAILED.value

        evt = {
            "event_id": evt_id,
            "run_id": context.run_id,
            "timestamp_utc": now_iso,
            "stage_id": st_id,
            "event_type": ev_type,
            "payload": {
                "wave_index": wave_idx,
                "stage_id": st_id,
                "status": status_raw,
                "summary": str(getattr(step_report, "summary", "")),
                "duration_ms": int(getattr(step_report, "duration_ms", 0)),
            },
        }

        if context.run_id in state.runs:
            curr = state.runs[context.run_id]
            state.runs[context.run_id] = curr.model_copy(
                update={"event_count": curr.event_count + 1}
            )

        engine.broadcast_event(context, evt)

    def _on_stream_event(harness_event: Any) -> None:
        if context.abort_event.is_set():
            raise PipelineAbortedError(f"Run '{context.run_id}' aborted by user.")

        delta_text = getattr(harness_event, "delta_text", None) or getattr(
            harness_event, "token_delta", None
        )
        thought_text = getattr(harness_event, "thought_delta", None)
        if not delta_text and not thought_text:
            return

        now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        stage_id = "STAGE_00_ORCHESTRATION"
        if context.run_id in state.runs:
            stage_id = state.runs[context.run_id].current_stage_id

        ev_type = EventType.THOUGHT_DELTA.value if thought_text else EventType.TOKEN_DELTA.value
        payload = {"thought": thought_text} if thought_text else {"delta": delta_text}
        evt = {
            "event_id": f"evt-{context.run_id}-token-{uuid.uuid4().hex[:6]}",
            "run_id": context.run_id,
            "timestamp_utc": now_iso,
            "stage_id": stage_id,
            "event_type": ev_type,
            "payload": payload,
        }
        engine.broadcast_event(context, evt)

    return PipelineCallbacks(
        on_stage_start=_on_stage_start,
        on_stage_complete=_on_stage_complete,
        on_stream_event=_on_stream_event,
    )


__all__ = ["PipelineCallbacks", "build_pipeline_callbacks"]
