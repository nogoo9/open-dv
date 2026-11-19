"""Pipeline lifecycle callbacks for async run execution, incremental ledger flush, and live SSE."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from opendv_agent_harness import build_observability_event_dict
from opendv_contracts import EventType
from opendv_service.errors import PipelineAbortedError
from opendv_service.state.persistence import emit_incremental_ledger_event

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
    """Construct lifecycle callbacks that monitor abort requests, flush ledger, and broadcast SSE."""
    session_id = getattr(context, "session_id", None)

    def _with_session(metrics: dict[str, Any]) -> dict[str, Any]:
        if session_id:
            metrics["session_id"] = session_id
        return metrics

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
            summary_metrics=_with_session({"wave_index": wave_idx, "model": model}),
        )

        lock = getattr(state, "_state_lock", None)
        if lock is not None:
            with lock:
                if context.run_id in state.runs:
                    curr = state.runs[context.run_id]
                    state.runs[context.run_id] = curr.model_copy(
                        update={"current_stage_id": stage_id, "event_count": curr.event_count + 1}
                    )
        elif context.run_id in state.runs:
            curr = state.runs[context.run_id]
            state.runs[context.run_id] = curr.model_copy(
                update={"current_stage_id": stage_id, "event_count": curr.event_count + 1}
            )

        emit_incremental_ledger_event(state, context.run_id, evt)
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
            summary_metrics=_with_session(
                {
                    "wave_index": wave_idx,
                    "duration_ms": int(getattr(step_report, "duration_ms", 0)),
                }
            ),
        )

        lock = getattr(state, "_state_lock", None)
        if lock is not None:
            with lock:
                if context.run_id in state.runs:
                    curr = state.runs[context.run_id]
                    state.runs[context.run_id] = curr.model_copy(
                        update={"event_count": curr.event_count + 1}
                    )
        elif context.run_id in state.runs:
            curr = state.runs[context.run_id]
            state.runs[context.run_id] = curr.model_copy(
                update={"event_count": curr.event_count + 1}
            )

        emit_incremental_ledger_event(state, context.run_id, evt)
        engine.broadcast_event(context, evt)

    def _on_stream_event(harness_event: Any) -> None:
        if context.abort_event.is_set():
            raise PipelineAbortedError(f"Run '{context.run_id}' aborted by user.")

        delta_text = getattr(harness_event, "delta_text", None) or getattr(
            harness_event, "token_delta", None
        )
        thought_text = getattr(harness_event, "thought_delta", None)
        tool_name = getattr(harness_event, "tool_name", None)
        obs = getattr(harness_event, "observability_event", None)
        if not delta_text and not thought_text and not tool_name and not obs:
            return

        now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        stage_id = "STAGE_00_ORCHESTRATION"
        lock = getattr(state, "_state_lock", None)
        if lock is not None:
            with lock:
                if context.run_id in state.runs:
                    stage_id = state.runs[context.run_id].current_stage_id
        elif context.run_id in state.runs:
            stage_id = state.runs[context.run_id].current_stage_id

        ev_type = EventType.THOUGHT_DELTA.value if thought_text else EventType.TOKEN_DELTA.value
        summary_txt = str(thought_text or delta_text or tool_name or "LLM stream checkpoint").strip()
        metrics: dict[str, Any] = {"delta_length": len(summary_txt)}
        if tool_name:
            metrics["tool_name"] = str(tool_name)
        if isinstance(obs, dict) and isinstance(obs.get("usage"), dict):
            metrics["usage"] = dict(obs["usage"])

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
            summary_metrics=_with_session(metrics),
        )
        emit_incremental_ledger_event(state, context.run_id, evt)
        engine.broadcast_event(context, evt)

    return PipelineCallbacks(
        on_stage_start=_on_stage_start,
        on_stage_complete=_on_stage_complete,
        on_stream_event=_on_stream_event,
    )


__all__ = ["PipelineCallbacks", "build_pipeline_callbacks"]
