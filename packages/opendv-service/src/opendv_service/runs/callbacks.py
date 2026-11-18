"""Pipeline lifecycle callbacks for async run execution and live event broadcasting."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from opendv_agent_harness import build_observability_event_dict
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
        evt = build_observability_event_dict(
            event_id=evt_id,
            event_type=EventType.STAGE_STARTED.value,
            run_id=context.run_id,
            trace_id=uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
            timestamp_utc=now_iso,
            stage_id=stage_id,
            agent_persona=persona,
            status="PASS",
            headline=f"Stage {stage_id} started by {persona}.",
            business_impact=f"Executing wave {wave_idx} with model {model}.",
            summary_metrics={"wave_index": wave_idx, "model": model},
        )

        if context.run_id in state.runs:
            curr = state.runs[context.run_id]
            state.runs[context.run_id] = curr.model_copy(
                update={
                    "current_stage_id": stage_id,
                    "event_count": curr.event_count + 1,
                }
            )

        state.run_events.setdefault(context.run_id, []).append(evt)
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
        p_name = getattr(step_report, "persona", "vault-first-mate")

        evt = build_observability_event_dict(
            event_id=evt_id,
            event_type=ev_type,
            run_id=context.run_id,
            trace_id=uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
            timestamp_utc=now_iso,
            stage_id=st_id,
            agent_persona=p_name,
            status="PASS" if passed else "REJECT-WITH-TRACE",
            headline=f"Stage {st_id} completed: {status_raw}.",
            business_impact=str(getattr(step_report, "summary", ""))[:200]
            or f"Wave {wave_idx} stage complete.",
            summary_metrics={
                "wave_index": wave_idx,
                "duration_ms": int(getattr(step_report, "duration_ms", 0)),
            },
        )

        if context.run_id in state.runs:
            curr = state.runs[context.run_id]
            state.runs[context.run_id] = curr.model_copy(
                update={"event_count": curr.event_count + 1}
            )

        state.run_events.setdefault(context.run_id, []).append(evt)
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
        summary_txt = (thought_text or delta_text or "").strip()
        evt = build_observability_event_dict(
            event_id=f"evt-{context.run_id}-token-{uuid.uuid4().hex[:6]}",
            event_type=ev_type,
            run_id=context.run_id,
            trace_id=uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
            timestamp_utc=now_iso,
            stage_id=stage_id,
            agent_persona="vault-first-mate",
            status="PASS",
            headline="Stream delta checkpoint.",
            business_impact=summary_txt[:100] or "LLM stream activity.",
            summary_metrics={"delta_length": len(summary_txt)},
        )
        state.run_events.setdefault(context.run_id, []).append(evt)
        engine.broadcast_event(context, evt)

    return PipelineCallbacks(
        on_stage_start=_on_stage_start,
        on_stage_complete=_on_stage_complete,
        on_stream_event=_on_stream_event,
    )


__all__ = ["PipelineCallbacks", "build_pipeline_callbacks"]
