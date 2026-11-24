"""Unified W3C SSE stream adapter for Step 4 & Step 6 runs with Last-Event-ID replay."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from fastapi import Request
from fastapi.responses import StreamingResponse

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.service.app_service import OpenDVService

from ._tasks import RunTaskManager

_TERMINAL_OR_HOLD_STATUSES: frozenset[str] = frozenset(
    {"COMPLETED", "FAILED", "HOLD_CHECKPOINTED", "UNCERTIFIED_DRAFT"}
)


class SseStreamAdapter:
    """Tails JSONL RunStreamEvent logs and emits gapless W3C Server-Sent Events."""

    def __init__(
        self,
        *,
        poll_interval: float = 0.05,
        keepalive_interval: float = 15.0,
    ) -> None:
        self.poll_interval = poll_interval
        self.keepalive_interval = keepalive_interval

    @staticmethod
    def parse_cursor(last_event_id: str | None, since_seq: int = 0) -> int:
        if since_seq < 0:
            raise OpenDVError(
                f"since_seq must be >= 0, got {since_seq}.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if last_event_id is None:
            return since_seq
        cleaned = last_event_id.strip()
        if not cleaned or not cleaned.isdigit():
            raise OpenDVError(
                f"Invalid Last-Event-ID header '{last_event_id}'; expected non-negative integer.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        return max(since_seq, int(cleaned))

    def stream_response(
        self,
        request: Request,
        service: OpenDVService,
        task_manager: RunTaskManager,
        project_id: str,
        run_id: str,
        *,
        last_event_id: str | None = None,
        since_seq: int = 0,
        expected_step: str | None = None,
    ) -> StreamingResponse:
        """Validate run existence & Last-Event-ID synchronously, then return SSE stream."""
        service.get_project(project_id)
        initial_state = service.get_run(project_id, run_id)
        if expected_step is not None and initial_state.step != expected_step:
            raise OpenDVError(
                f"Run '{run_id}' does not belong to {expected_step}.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        cursor = self.parse_cursor(last_event_id, since_seq)

        async def _event_generator() -> AsyncIterator[str]:
            nonlocal cursor
            last_emit_ts = time.monotonic()

            while True:
                page = service.get_run_logs(project_id, run_id, since_seq=cursor)
                if page.events:
                    for ev in page.events:
                        yield (
                            f"id: {ev.seq}\n"
                            f"event: {ev.event_type}\n"
                            f"data: {ev.model_dump_json()}\n\n"
                        )
                        cursor = ev.seq
                    last_emit_ts = time.monotonic()

                state = service.get_run(project_id, run_id)
                if state.status in _TERMINAL_OR_HOLD_STATUSES and not task_manager.is_running(
                    project_id, run_id
                ):
                    final_page = service.get_run_logs(project_id, run_id, since_seq=cursor)
                    for ev in final_page.events:
                        yield (
                            f"id: {ev.seq}\n"
                            f"event: {ev.event_type}\n"
                            f"data: {ev.model_dump_json()}\n\n"
                        )
                        cursor = ev.seq
                    return

                if await request.is_disconnected():
                    return

                now = time.monotonic()
                if now - last_emit_ts >= self.keepalive_interval:
                    yield ": keepalive\n\n"
                    last_emit_ts = now

                await asyncio.sleep(self.poll_interval)

        return StreamingResponse(
            _event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )


__all__ = ["SseStreamAdapter"]
