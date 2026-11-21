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
from opendv_service.state.pii_redaction import redact_pii_text, sanitize_layer1_summary
from opendv_service.studio.agent_runner_helpers import serialize_and_mask_telemetry

if TYPE_CHECKING:
    from opendv_service.runs.engine import RunContext, RunExecutionEngine
    from opendv_service.state import StateStore

_LEDGER_PERSONAS = frozenset(
    {
        "vault-first-mate",
        "scout-source",
        "scout-unstructured",
        "ship-vault",
        "axi-dv",
        "deterministic-compiler",
    }
)


def _sanitize_telemetry_field(value: Any) -> str | None:
    masked = serialize_and_mask_telemetry(value)
    if not masked:
        return None
    return redact_pii_text(masked, mask_hex64=False)


def _attach_prompt_and_response(
    evt: dict[str, Any],
    *,
    prompt_val: Any = None,
    response_val: Any = None,
    token_usage: Any = None,
) -> None:
    l2 = evt.setdefault("layer2_visual_payload", {})
    if not isinstance(l2, dict):
        return
    clean_prompt = _sanitize_telemetry_field(prompt_val)
    if clean_prompt:
        l2["masked_prompt"] = clean_prompt
    clean_resp = _sanitize_telemetry_field(response_val)
    if clean_resp:
        l2["structured_or_raw_response"] = clean_resp
    if isinstance(token_usage, dict) and token_usage:
        l2["token_usage"] = dict(token_usage)


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
    """Construct lifecycle callbacks for abort checks, ledger flush, and SSE broadcast."""
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
            summary_metrics=_with_session(
                {"wave_index": wave_idx, "model": model, "model_id": model}
            ),
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

        safe_summary = sanitize_layer1_summary(str(getattr(step_report, "summary", "")), 200)
        step_metrics = getattr(step_report, "metrics", None)
        s_metrics = step_metrics if isinstance(step_metrics, dict) else {}
        summary_metrics: dict[str, Any] = {
            "wave_index": wave_idx,
            "duration_ms": int(getattr(step_report, "duration_ms", 0)),
        }
        model_used = getattr(step_report, "model_used", None) or s_metrics.get("model_id")
        if model_used:
            summary_metrics["model_id"] = str(model_used)
        if s_metrics.get("provider"):
            summary_metrics["provider"] = str(s_metrics["provider"])

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
            business_impact=safe_summary or f"Wave {wave_idx} stage complete.",
            summary_metrics=_with_session(summary_metrics),
        )
        _attach_prompt_and_response(
            evt,
            prompt_val=s_metrics.get("masked_prompt")
            or s_metrics.get("prompt")
            or getattr(step_report, "masked_prompt", None),
            response_val=s_metrics.get("structured_or_raw_response")
            or s_metrics.get("response")
            or s_metrics.get("structured_output")
            or s_metrics.get("text_output"),
            token_usage=s_metrics.get("token_usage") or s_metrics.get("usage"),
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
        tool_result = getattr(harness_event, "tool_result", None)
        obs = getattr(harness_event, "observability_event", None)
        has_tool_res = isinstance(tool_result, dict) and bool(tool_result)
        if not delta_text and not thought_text and not tool_name and not obs and not has_tool_res:
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
        summary_txt = str(
            thought_text or delta_text or tool_name or "LLM stream checkpoint"
        ).strip()
        metrics: dict[str, Any] = {"delta_length": len(summary_txt)}
        if tool_name:
            metrics["tool_name"] = str(tool_name)
        provider_val = getattr(harness_event, "provider", None)
        if provider_val:
            metrics["provider"] = str(provider_val)

        obs_dict = obs if isinstance(obs, dict) else {}
        obs_l2 = (
            obs_dict.get("layer2_visual_payload")
            if isinstance(obs_dict.get("layer2_visual_payload"), dict)
            else {}
        )
        obs_m = (
            obs_l2.get("summary_metrics") if isinstance(obs_l2.get("summary_metrics"), dict) else {}
        )
        tr_dict = tool_result if isinstance(tool_result, dict) else {}
        usage = (
            obs_dict.get("usage")
            if isinstance(obs_dict.get("usage"), dict)
            else tr_dict.get("usage")
        )
        if isinstance(usage, dict):
            metrics["usage"] = dict(usage)

        raw_persona = getattr(harness_event, "persona", None)
        persona_str = raw_persona.value if hasattr(raw_persona, "value") else str(raw_persona or "")
        safe_persona = persona_str if persona_str in _LEDGER_PERSONAS else "vault-first-mate"

        safe_impact = sanitize_layer1_summary(summary_txt, 100)
        evt = build_observability_event_dict(
            event_id=f"evt-{context.run_id}-token-{uuid.uuid4().hex[:6]}",
            event_type=ev_type,
            run_id=context.run_id,
            trace_id=uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
            timestamp_utc=now_iso,
            stage_id=stage_id,
            agent_persona=safe_persona,
            status="PASS",
            headline="Stream delta checkpoint.",
            business_impact=safe_impact or "LLM stream activity.",
            summary_metrics=_with_session(metrics),
        )
        prompt_val = (
            getattr(harness_event, "masked_prompt", None)
            or obs_dict.get("masked_prompt")
            or obs_dict.get("prompt")
            or obs_l2.get("masked_prompt")
            or obs_m.get("masked_prompt")
            or tr_dict.get("masked_prompt")
            or tr_dict.get("prompt")
        )
        response_val = (
            obs_dict.get("structured_or_raw_response")
            or obs_dict.get("response")
            or obs_l2.get("structured_or_raw_response")
            or obs_m.get("structured_or_raw_response")
            or tr_dict.get("structured_or_raw_response")
            or tr_dict.get("response")
            or tr_dict.get("structured_output")
            or tr_dict.get("text_output")
            or thought_text
            or delta_text
        )
        _attach_prompt_and_response(
            evt,
            prompt_val=prompt_val,
            response_val=response_val,
            token_usage=usage if isinstance(usage, dict) else None,
        )
        emit_incremental_ledger_event(state, context.run_id, evt)
        engine.broadcast_event(context, evt)

    return PipelineCallbacks(
        on_stage_start=_on_stage_start,
        on_stage_complete=_on_stage_complete,
        on_stream_event=_on_stream_event,
    )


__all__ = ["PipelineCallbacks", "build_pipeline_callbacks"]
