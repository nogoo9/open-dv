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
    if context is not None:
        with context.lock:
            buffer = persisted_events if persisted_events else list(context.event_buffer)
            is_done = context.is_done
            final_status = context.final_status
            if not is_done:
                queue = asyncio.Queue()
                loop = asyncio.get_running_loop()
                context.subscribers.add((queue, loop))
    else:
        buffer = persisted_events
        is_done = True
        run_dto = state.runs.get(run_id)
        final_status = run_dto.status if run_dto else RunStatus.COMPLETED

    # 1. Determine events to replay based on Last-Event-ID
    if last_event_id:
        match_idx = -1
        for idx, ev in enumerate(buffer):
            if ev.get("event_id") == last_event_id:
                match_idx = idx
                break
        replayed = buffer[match_idx + 1 :] if match_idx >= 0 else buffer
    else:
        replayed = buffer

    # 2. Yield replayed frames
    had_terminal_in_replay = False
    for raw_evt in replayed:
        ev_type = (
            str(raw_evt.get("event_type", "message")) if isinstance(raw_evt, dict) else "message"
        )
        ev_id = str(raw_evt.get("event_id", "")) if isinstance(raw_evt, dict) else ""
        if ev_type == "run_complete":
            had_terminal_in_replay = True
            stat = "COMPLETED"
            if isinstance(raw_evt, dict):
                if "payload" in raw_evt and isinstance(raw_evt["payload"], dict):
                    stat = raw_evt["payload"].get("status", "COMPLETED")
                elif "status" in raw_evt:
                    stat = raw_evt.get("status", "COMPLETED")
            yield format_sse_frame("run_complete", None, {"status": stat})
        else:
            redacted = redact_event_for_role(raw_evt, eff_role)
            yield format_sse_frame(ev_type, ev_id, redacted)

    # If execution already ended, emit terminal frame if needed and terminate
    if is_done:
        if not had_terminal_in_replay:
            if final_status == RunStatus.ABORTED or str(final_status) == "ABORTED":
                status_str = "ABORTED"
            elif final_status == RunStatus.FAILED or str(final_status) == "FAILED":
                status_str = "FAILED"
            else:
                status_str = "COMPLETED"
            term_payload = {"status": status_str}
            yield format_sse_frame("run_complete", None, term_payload)
        return

    # 3. Live stream from subscriber queue
    assert queue is not None
    assert loop is not None
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=ping_interval)
            except TimeoutError:
                yield ": ping\n\n"
                continue

            if isinstance(item, str):
                yield item
                if "run_complete" in item:
                    break
            elif isinstance(item, dict):
                ev_type = str(item.get("event_type", "message"))
                ev_id = str(item.get("event_id", ""))
                if ev_type == "run_complete":
                    stat = "COMPLETED"
                    if "payload" in item and isinstance(item["payload"], dict):
                        stat = item["payload"].get("status", "COMPLETED")
                    elif "status" in item:
                        stat = item.get("status", "COMPLETED")
                    yield format_sse_frame("run_complete", None, {"status": stat})
                    break
                redacted = redact_event_for_role(item, eff_role)
                yield format_sse_frame(ev_type, ev_id, redacted)
    finally:
        if context is not None:
            with context.lock:
                context.subscribers.discard((queue, loop))


__all__ = ["format_sse_frame", "subscribe_run_sse"]
