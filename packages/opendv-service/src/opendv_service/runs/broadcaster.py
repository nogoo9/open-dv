"""Real-time SSE event broadcaster, W3C frame formatter, and replay engine."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from opendv_contracts import RbacRole, RunStatus
from opendv_service.errors import NotFoundError
from opendv_service.rbac import normalize_rbac_role, redact_event_for_role

if TYPE_CHECKING:
    from opendv_service.runs.engine import RunContext
    from opendv_service.state import StateStore


def format_sse_frame(
    event_type: str,
    event_id: str | None,
    data: dict[str, Any] | str,
) -> str:
    """Format a single event frame conforming strictly to W3C Server-Sent Events spec."""
    lines: list[str] = []
    if event_type:
        lines.append(f"event: {event_type}")
    if event_id:
        lines.append(f"id: {event_id}")
    payload_json = json.dumps(data, sort_keys=True) if isinstance(data, (dict, list)) else str(data)
    lines.append(f"data: {payload_json}")
    return "\n".join(lines) + "\n\n"


def build_hold_sse_payload(
    state: Any,
    run_id: str,
    raw_evt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble rich `event: hold` payload from hold manager, report active_holds, and raw event."""
    payload: dict[str, Any] = {"status": "PAUSED_HOLD", "run_id": run_id}
    if isinstance(raw_evt, dict):
        if isinstance(raw_evt.get("payload"), dict):
            payload.update({k: v for k, v in raw_evt["payload"].items() if v is not None})
        for k in (
            "hold_id",
            "stage_id",
            "headline",
            "reason",
            "escalation_reason",
            "rule_id",
            "options",
        ):
            if raw_evt.get(k) is not None:
                payload.setdefault(k, raw_evt[k])

    first_hold: Any = None
    hold_mgrs = getattr(state, "hold_managers", None) or {}
    mgr = hold_mgrs.get(run_id)
    if mgr is not None and hasattr(mgr, "list_pending_holds"):
        pending = mgr.list_pending_holds(run_id=run_id)
        if not pending:
            pending = mgr.list_pending_holds()
        if pending:
            first_hold = pending[0]
    if first_hold is None:
        rep = (getattr(state, "pipeline_reports", None) or {}).get(run_id) or {}
        active = rep.get("active_holds") or []
        if active:
            first_hold = active[0]

    if first_hold is not None:
        h_dict = (
            first_hold.model_dump(mode="json")
            if hasattr(first_hold, "model_dump")
            else (dict(first_hold) if isinstance(first_hold, dict) else {})
        )
        hold_id = h_dict.get("hold_id")
        stage_id = h_dict.get("stage_id")
        rule_id = h_dict.get("rule_id")
        esc_reason = h_dict.get("escalation_reason")
        summary = h_dict.get("summary") or h_dict.get("architect_question") or h_dict.get("reason")
        headline = h_dict.get("headline") or summary or esc_reason or rule_id
        options = h_dict.get("options")
        if hold_id:
            payload["hold_id"] = hold_id
        if stage_id:
            payload["stage_id"] = stage_id
        if rule_id:
            payload["rule_id"] = rule_id
        if esc_reason:
            payload["escalation_reason"] = esc_reason
        if summary or esc_reason:
            payload["reason"] = str(summary or esc_reason)
        if headline:
            payload["headline"] = str(headline)
        if isinstance(options, list) and options:
            payload["options"] = options

    payload.setdefault("hold_id", f"hold-{run_id}")
    payload.setdefault("stage_id", "STAGE_04_SHIP_VAULT_PASS2")
    payload.setdefault("reason", str(payload.get("escalation_reason") or "Steward Hold Required"))
    payload.setdefault("headline", str(payload["reason"]))
    if not isinstance(payload.get("options"), list):
        payload["options"] = []
    payload["status"] = "PAUSED_HOLD"
    payload["run_id"] = run_id
    return payload


async def subscribe_run_sse(
    context: RunContext | None,
    state: StateStore,
    run_id: str,
    *,
    role: RbacRole = RbacRole.PUBLIC,
    last_event_id: str | None = None,
    ping_interval: float = 15.0,
) -> AsyncIterator[str]:
    """Stream live or buffered run events over Server-Sent Events (SSE).

    Features:
    1. Eager 404 validation before stream initiates.
    2. Last-Event-ID reconnection buffer replay.
    3. Live event streaming as stages execute.
    4. Periodic ': ping\n\n' heartbeats on connection idle.
    5. Role-based event field redaction.
    """
    if run_id not in state.runs and (context is None or context.run_id != run_id):
        raise NotFoundError(f"Run '{run_id}' not found.")

    eff_role = normalize_rbac_role(role, default=RbacRole.PUBLIC)
    queue: asyncio.Queue[dict[str, Any] | str] | None = None
    loop: asyncio.AbstractEventLoop | None = None

    persisted_events = list(state.run_events.get(run_id, []))
    run_dto = state.runs.get(run_id)
    current_status = run_dto.status if run_dto else (context.final_status if context else None)
    status_str_now = (
        current_status.value if hasattr(current_status, "value") else str(current_status or "")
    )
    is_hold_now = status_str_now == "PAUSED_HOLD"
    is_running_now = status_str_now in {"QUEUED", "RUNNING"}

    if context is not None:
        with context.lock:
            seen_buf_ids = {ev.get("event_id") for ev in persisted_events if isinstance(ev, dict)}
            if persisted_events:
                buffer = list(persisted_events)
                for ev in context.event_buffer:
                    if not isinstance(ev, dict):
                        continue
                    eid = ev.get("event_id")
                    etype = ev.get("event_type")
                    if etype == "hold" and not is_hold_now:
                        continue
                    if etype == "run_complete" and (is_running_now or not context.is_done):
                        continue
                    if eid not in seen_buf_ids:
                        buffer.append(ev)
                        seen_buf_ids.add(eid)
            else:
                buffer = list(context.event_buffer)
            if not context.is_done:
                is_running_now = True
            context_active = not context.is_done
            if context_active:
                queue = asyncio.Queue()
                loop = asyncio.get_running_loop()
                context.subscribers.add((queue, loop))
    else:
        buffer = persisted_events
        context_active = False

    explicit_hold_runs = getattr(state, "explicit_hold_runs", None) or set()
    has_explicit_hold = (run_id in explicit_hold_runs) or any(
        isinstance(ev, dict) and ev.get("event_type") == "hold" for ev in buffer
    )
    if is_running_now:
        is_done = False
        final_status = current_status
    elif is_hold_now and has_explicit_hold:
        is_done = True
        final_status = RunStatus.PAUSED_HOLD
    elif context is not None:
        is_done = context.is_done
        final_status = current_status or context.final_status
    else:
        is_done = True
        final_status = current_status or RunStatus.COMPLETED

    # 1. Determine events to replay based on Last-Event-ID
    if last_event_id:
        match_idx = -1
        if last_event_id == f"evt-{run_id}-hold" or last_event_id.endswith("-hold"):
            match_idx = len(buffer) - 1
            for idx, ev in enumerate(buffer):
                if str(ev.get("event_id", "")).startswith("evt-hold-resolved"):
                    match_idx = idx - 1
                    break
        else:
            for idx, ev in enumerate(buffer):
                if ev.get("event_id") == last_event_id:
                    match_idx = idx
                    break
        replayed = buffer[match_idx + 1 :] if match_idx >= 0 else buffer
    else:
        replayed = buffer

    # 2. Yield replayed frames
    had_terminal_in_replay = False
    emitted_ids: set[str] = {
        str(ev.get("event_id", ""))
        for ev in buffer[: max(0, len(buffer) - len(replayed))]
        if isinstance(ev, dict) and ev.get("event_id")
    }
    for raw_evt in replayed:
        ev_type = (
            str(raw_evt.get("event_type", "message")) if isinstance(raw_evt, dict) else "message"
        )
        ev_id = str(raw_evt.get("event_id", "")) if isinstance(raw_evt, dict) else ""
        if ev_id:
            emitted_ids.add(ev_id)
        if ev_type == "run_complete":
            stat = "COMPLETED"
            if isinstance(raw_evt, dict):
                if "payload" in raw_evt and isinstance(raw_evt["payload"], dict):
                    stat = raw_evt["payload"].get("status", "COMPLETED")
                elif "status" in raw_evt:
                    stat = raw_evt.get("status", "COMPLETED")
            had_terminal_in_replay = True
            yield format_sse_frame("run_complete", None, {"status": stat})
        elif ev_type == "hold":
            if is_hold_now:
                had_terminal_in_replay = True
            yield format_sse_frame(
                "hold",
                ev_id or f"evt-{run_id}-hold",
                build_hold_sse_payload(state, run_id, raw_evt),
            )
        else:
            redacted = redact_event_for_role(raw_evt, eff_role)
            yield format_sse_frame(ev_type, ev_id, redacted)

    # Emit terminal/hold frame if execution already ended or paused
    if is_done:
        if not had_terminal_in_replay:
            term_str = (
                final_status.value
                if hasattr(final_status, "value")
                else str(final_status or "COMPLETED")
            )
            if term_str == "PAUSED_HOLD" and has_explicit_hold:
                yield format_sse_frame(
                    "hold",
                    f"evt-{run_id}-hold",
                    build_hold_sse_payload(state, run_id),
                )
            else:
                if term_str not in {"ABORTED", "FAILED", "COMPLETED"}:
                    term_str = "COMPLETED"
                yield format_sse_frame("run_complete", None, {"status": term_str})
        return

    # 3. Live stream from subscriber queue or persisted run_events polling (during hold-resume)
    try:
        if context_active and queue is not None and loop is not None:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=ping_interval)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue

                if isinstance(item, str):
                    yield item
                    if "run_complete" in item or "event: hold" in item:
                        break
                elif isinstance(item, dict):
                    ev_type = str(item.get("event_type", "message"))
                    ev_id = str(item.get("event_id", ""))
                    if ev_id and ev_id in emitted_ids:
                        continue
                    if ev_id:
                        emitted_ids.add(ev_id)
                    if ev_type == "run_complete":
                        stat = "COMPLETED"
                        if "payload" in item and isinstance(item["payload"], dict):
                            stat = item["payload"].get("status", "COMPLETED")
                        elif "status" in item:
                            stat = item.get("status", "COMPLETED")
                        yield format_sse_frame("run_complete", None, {"status": stat})
                        break
                    if ev_type == "hold":
                        yield format_sse_frame(
                            "hold",
                            ev_id or f"evt-{run_id}-hold",
                            build_hold_sse_payload(state, run_id, item),
                        )
                        break
                    redacted = redact_event_for_role(item, eff_role)
                    yield format_sse_frame(ev_type, ev_id, redacted)
            return

        last_ping_at = asyncio.get_running_loop().time()
        while True:
            for raw_evt in list(state.run_events.get(run_id, [])):
                if not isinstance(raw_evt, dict):
                    continue
                ev_id = str(raw_evt.get("event_id", ""))
                if ev_id and ev_id in emitted_ids:
                    continue
                if ev_id:
                    emitted_ids.add(ev_id)
                ev_type = str(raw_evt.get("event_type", "message"))
                if ev_type == "hold":
                    yield format_sse_frame(
                        "hold",
                        ev_id or f"evt-{run_id}-hold",
                        build_hold_sse_payload(state, run_id, raw_evt),
                    )
                    return
                if ev_type == "run_complete":
                    stat = (
                        raw_evt.get("payload", {}).get("status", "COMPLETED")
                        if isinstance(raw_evt.get("payload"), dict)
                        else raw_evt.get("status", "COMPLETED")
                    )
                    yield format_sse_frame("run_complete", None, {"status": stat})
                    return
                redacted = redact_event_for_role(raw_evt, eff_role)
                yield format_sse_frame(ev_type, ev_id, redacted)

            latest_run = state.runs.get(run_id)
            if latest_run is not None:
                latest_stat = (
                    latest_run.status.value
                    if hasattr(latest_run.status, "value")
                    else str(latest_run.status)
                )
                if latest_stat == "PAUSED_HOLD":
                    yield format_sse_frame(
                        "hold",
                        f"evt-{run_id}-hold",
                        build_hold_sse_payload(state, run_id),
                    )
                    return
                if latest_stat in {"COMPLETED", "FAILED", "ABORTED"}:
                    yield format_sse_frame("run_complete", None, {"status": latest_stat})
                    return

            now_t = asyncio.get_running_loop().time()
            if (now_t - last_ping_at) >= ping_interval:
                last_ping_at = now_t
                yield ": ping\n\n"
            await asyncio.sleep(0.05)
    finally:
        if context is not None and queue is not None and loop is not None:
            with context.lock:
                context.subscribers.discard((queue, loop))


__all__ = ["format_sse_frame", "subscribe_run_sse"]
