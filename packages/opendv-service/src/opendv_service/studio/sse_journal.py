"""W3C Server-Sent Events (SSE) streaming engine and replay journal for Open-DV Ontology Studio.

Implements Task 4D.3 / Issue #210 and docs/19 §6.3:
- W3C SSE formatted streaming with monotonic `Last-Event-ID` cursor replay.
- Strict lock hierarchy compliance & lock inversion prevention (docs/19 §6.3):
    Jobs holding lease L2 buffer SSE telemetry in memory while operating under L5 session lock,
    and flush buffered events to `jobs/<job_id>.events.jsonl` under L3 journal lock ONLY
    after releasing L5 session lock. Attempting to flush under L3 while holding L5 raises
    LockOrderViolationError immediately.
- Replay reader starvation prevention: Replay readers stream directly from the append-only
    journal without contending on L5 session locks.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from opendv_service.studio.locks import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    L3_JOURNAL,
    LockLevel,
    LockOrderViolationError,
    get_held_locks,
    studio_lock,
)
from opendv_service.studio.workspace_security import set_owner_only_permissions

_DEFAULT_POLL_INTERVAL: Final[float] = 0.050


@dataclass(frozen=True)
class SSEEvent:
    """A single Server-Sent Event formatted per the W3C EventSource standard."""

    id: int
    event: str
    data: dict[str, Any] | str
    timestamp: str = ""

    def to_sse_str(self) -> str:
        """Format event as standard W3C SSE wire text."""
        data_str = json.dumps(self.data) if isinstance(self.data, dict) else str(self.data)
        lines: list[str] = [f"id: {self.id}", f"event: {self.event}"]
        for dline in data_str.splitlines():
            lines.append(f"data: {dline}")
        lines.append("")
        lines.append("")
        return "\n".join(lines)

    def to_sse_bytes(self) -> bytes:
        """Format event as UTF-8 wire bytes."""
        return self.to_sse_str().encode("utf-8")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for journal logging."""
        return {
            "id": self.id,
            "event": self.event,
            "data": self.data,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SSEEvent:
        """Instantiate SSEEvent from parsed journal JSON dictionary."""
        return cls(
            id=int(data["id"]),
            event=str(data.get("event", "message")),
            data=data.get("data", {}),
            timestamp=str(data.get("timestamp", "")),
        )


class SSEJournalWriter:
    """Thread-safe and async-safe journal writer with lock inversion protection.

    Buffers telemetry events in memory during L5 session mutations, and flushes
    under L3 journal lock only after L5 is released.
    """

    def __init__(
        self,
        session_dir: Path | str,
        job_id: str,
        session_id: str | None = None,
    ) -> None:
        self.session_dir = Path(session_dir)
        self.job_id = job_id
        self.session_id = session_id or self.session_dir.name
        self.jobs_dir = self.session_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.journal_file = self.jobs_dir / f"{job_id}.events.jsonl"
        self.lock_file = self.jobs_dir / f".journal_{job_id}.lock"

        self._buffer: list[SSEEvent] = []
        self._listeners: list[asyncio.Queue[SSEEvent]] = []
        self._next_id: int = 1
        self._init_cursor_from_file()

    def _init_cursor_from_file(self) -> None:
        """Initialize next monotonic event ID by scanning existing journal file."""
        if not self.journal_file.exists():
            return

        max_id = 0
        with contextlib.suppress(OSError):
            for line in self.journal_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    with contextlib.suppress(json.JSONDecodeError, KeyError, ValueError):
                        rec = json.loads(line)
                        eid = int(rec.get("id", 0))
                        if eid > max_id:
                            max_id = eid

        self._next_id = max_id + 1

    def buffer_event(self, event: str, data: Any) -> SSEEvent:
        """Buffer a telemetry event in memory.

        Safe to call while holding L5 session lock because it touches zero lock files.
        """
        now_iso = datetime.now(UTC).isoformat()
        evt = SSEEvent(
            id=self._next_id,
            event=event,
            data=data,
            timestamp=now_iso,
        )
        self._next_id += 1
        self._buffer.append(evt)
        return evt

    def flush(self, timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> list[SSEEvent]:
        """Flush in-memory buffer to disk under L3 journal lock.

        Enforces Lock Inversion Prevention (docs/19 §6.3):
        Verifies that no lock at level L5 (or higher) is held in the current context.
        Raises LockOrderViolationError if L5 is held.
        """
        self._assert_no_l5_held()

        if not self._buffer:
            return []

        events_to_write = list(self._buffer)

        with studio_lock(
            self.lock_file,
            level=L3_JOURNAL,
            session_id=self.session_id,
            timeout=timeout_sec,
        ):
            self.jobs_dir.mkdir(parents=True, exist_ok=True)
            with self.journal_file.open("a", encoding="utf-8") as fp:
                for evt in events_to_write:
                    fp.write(json.dumps(evt.to_dict()) + "\n")
                fp.flush()
                os.fsync(fp.fileno())

            set_owner_only_permissions(self.journal_file)

        # Remove flushed events from buffer
        del self._buffer[: len(events_to_write)]

        # Dispatch to live listeners
        for listener in list(self._listeners):
            for evt in events_to_write:
                with contextlib.suppress(asyncio.QueueFull):
                    listener.put_nowait(evt)

        return events_to_write

    async def flush_async(
        self, timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS
    ) -> list[SSEEvent]:
        """Asynchronously flush in-memory buffer to disk under L3 journal lock."""
        self._assert_no_l5_held()

        if not self._buffer:
            return []

        events_to_write = list(self._buffer)

        async with studio_lock(
            self.lock_file,
            level=L3_JOURNAL,
            session_id=self.session_id,
            timeout=timeout_sec,
        ):
            await asyncio.to_thread(self._sync_append_events, events_to_write)

        del self._buffer[: len(events_to_write)]

        for listener in list(self._listeners):
            for evt in events_to_write:
                with contextlib.suppress(asyncio.QueueFull):
                    listener.put_nowait(evt)

        return events_to_write

    def _sync_append_events(self, events: list[SSEEvent]) -> None:
        """Synchronously append events to file."""
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        with self.journal_file.open("a", encoding="utf-8") as fp:
            for evt in events:
                fp.write(json.dumps(evt.to_dict()) + "\n")
            fp.flush()
            os.fsync(fp.fileno())
        set_owner_only_permissions(self.journal_file)

    def _assert_no_l5_held(self) -> None:
        """Verify that L5 session lock is not currently held in this context."""
        held_locks = get_held_locks()
        for lmeta in held_locks:
            if lmeta.level >= LockLevel.L5:
                raise LockOrderViolationError(
                    f"Lock inversion violation (docs/19 §6.3): cannot acquire L3 journal lock "
                    f"'{self.lock_file.name}' while holding level {lmeta.level.name} lock "
                    f"'{lmeta.path.name}'. Release L5 session lock before flushing SSE journal."
                )

    def subscribe(self) -> asyncio.Queue[SSEEvent]:
        """Subscribe a live listener queue for newly flushed events."""
        q: asyncio.Queue[SSEEvent] = asyncio.Queue()
        self._listeners.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[SSEEvent]) -> None:
        """Unsubscribe a live listener queue."""
        if q in self._listeners:
            self._listeners.remove(q)


def replay_sse_events(
    journal_file: Path | str,
    last_event_id: int | str | None = None,
) -> Iterator[SSEEvent]:
    """Replay events from a journal file strictly after last_event_id.

    Args:
        journal_file: Path to <job_id>.events.jsonl.
        last_event_id: Optional cursor indicating the last event received by client.
            If provided, all events with id <= int(last_event_id) are skipped.

    Yields:
        SSEEvent instances in strictly monotonic order.
    """
    jpath = Path(journal_file)
    if not jpath.exists():
        return

    cursor_id = -1
    if last_event_id is not None:
        try:
            cursor_id = int(last_event_id)
        except (ValueError, TypeError):
            cursor_id = -1

    for line in jpath.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            evt = SSEEvent.from_dict(rec)
            if evt.id > cursor_id:
                yield evt
        except (json.JSONDecodeError, KeyError, ValueError):
            continue


async def stream_sse_events(
    journal_file: Path | str,
    last_event_id: int | str | None = None,
    writer: SSEJournalWriter | None = None,
    is_active: Callable[[], bool] | None = None,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
) -> AsyncIterator[str]:
    """Asynchronously stream SSE events, replaying from journal then listening live.

    Yields standard W3C SSE wire text chunks formatted as:
    ```
    id: <id>
    event: <event_name>
    data: <json_string>

    ```
    """
    jpath = Path(journal_file)
    last_emitted_id = -1
    if last_event_id is not None:
        with contextlib.suppress(ValueError, TypeError):
            last_emitted_id = int(last_event_id)

    # 1. Replay historical events from journal file
    for evt in replay_sse_events(jpath, last_event_id=last_emitted_id):
        yield evt.to_sse_str()
        last_emitted_id = evt.id

    # 2. If writer is provided, subscribe to live flushed events
    terminal_events = {
        "completed",
        "failed",
        "cancelled",
        "JOB_COMPLETED",
        "JOB_FAILED",
    }
    if writer is not None:
        q = writer.subscribe()
        try:
            while True:
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=poll_interval)
                    if evt.id > last_emitted_id:
                        yield evt.to_sse_str()
                        last_emitted_id = evt.id
                        if evt.event in terminal_events:
                            break
                except TimeoutError:
                    if is_active is not None and not is_active():
                        break
                    # Periodic heartbeat / keep-alive comment
                    yield ": keepalive\n\n"
        finally:
            writer.unsubscribe(q)
