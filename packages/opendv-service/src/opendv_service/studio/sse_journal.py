"""W3C Server-Sent Events (SSE) streaming engine and replay journal for Open-DV Ontology Studio.

Implements Task 4D.3 / Issue #210, docs/19 §6.3, and docs/20 §4.1-4.2:
- W3C SSE formatted streaming with gapless monotonic `Last-Event-ID` cursor replay
  (subscribing BEFORE disk replay and deduplicating by `evt.id > last_emitted_id`).
- Strict lock hierarchy compliance & lock inversion prevention (docs/19 §6.3):
    Jobs holding lease L2 buffer SSE telemetry in memory while operating under L5 session lock,
    and flush buffered events to `jobs/<job_id>.events.jsonl` under L3 journal lock ONLY
    after releasing L5 session lock. Attempting to flush under L3 while holding L5 raises
    LockOrderViolationError immediately.
- Thread-safe and async-safe buffer/ID synchronization with `loop.call_soon_threadsafe`
  listener delivery into bounded `asyncio.Queue(maxsize=1024)` instances (with automatic
  journal catch-up if a slow consumer overflows).
- Journal segment gzip rotation when active `.events.jsonl` exceeds 50 MB.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import os
import secrets
import shutil
import threading
import time
import zlib
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
from opendv_service.studio.workspace_security import (
    replace_with_retry,
    set_owner_only_permissions,
)

_DEFAULT_POLL_INTERVAL: Final[float] = 0.200
_DEFAULT_KEEPALIVE_INTERVAL: Final[float] = 15.0
MAX_LISTENER_QUEUE_SIZE: Final[int] = 1024
MAX_JOURNAL_SEGMENT_BYTES: Final[int] = 50 * 1024 * 1024  # 50 MB

TERMINAL_SSE_EVENTS: Final[frozenset[str]] = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "job_completed",
        "job_failed",
        "job_cancelled",
        "JOB_COMPLETED",
        "JOB_FAILED",
        "JOB_CANCELLED",
        "JOB_ABANDONED_SERVER_CRASH",
    }
)


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


@dataclass
class _ListenerRegistration:
    """Internal registration pairing a bounded asyncio.Queue with its owning event loop."""

    queue: asyncio.Queue[SSEEvent]
    loop: asyncio.AbstractEventLoop | None
    overflowed: bool = False


def _list_rotated_segments(parent: Path, prefix: str) -> list[tuple[int, Path]]:
    """Return sorted `(segment_idx, gz_path)` tuples for rotated `.N.gz` journal segments."""
    rotated: list[tuple[int, Path]] = []
    if not parent.is_dir():
        return rotated
    with contextlib.suppress(OSError):
        for candidate in parent.iterdir():
            cname = candidate.name
            if cname.startswith(prefix) and cname.endswith(".gz"):
                mid = cname[len(prefix) : -3]
                if mid.isdigit():
                    rotated.append((int(mid), candidate))
    rotated.sort(key=lambda item: item[0])
    return rotated


def _iter_gz_segment_lines(gz_path: Path) -> Iterator[str]:
    """Yield lines from a `.gz` segment safely, tolerating corrupt/truncated gzip streams."""
    with (
        contextlib.suppress(OSError, EOFError, zlib.error),
        gzip.open(gz_path, "rt", encoding="utf-8", errors="replace") as gz_fp,
    ):
        yield from gz_fp


def _iter_journal_lines(journal_file: Path) -> Iterator[str]:
    """Yield raw JSONL lines from rotated gzipped segments followed by active journal_file.

    Re-checks rotated `.gz` segments after snapshotting `journal_file` so concurrent segment
    rotation never drops events between directory listing and active-file read.
    """
    parent = journal_file.parent
    prefix = f"{journal_file.name}."
    seen_segment_indices: set[int] = set()

    for idx, gz_path in _list_rotated_segments(parent, prefix):
        seen_segment_indices.add(idx)
        yield from _iter_gz_segment_lines(gz_path)

    active_lines: list[str] = []
    if journal_file.is_file():
        with contextlib.suppress(OSError):
            active_lines = journal_file.read_text(encoding="utf-8", errors="replace").splitlines()

    for idx, gz_path in _list_rotated_segments(parent, prefix):
        if idx not in seen_segment_indices:
            seen_segment_indices.add(idx)
            yield from _iter_gz_segment_lines(gz_path)

    yield from active_lines


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
        *,
        max_segment_bytes: int = MAX_JOURNAL_SEGMENT_BYTES,
    ) -> None:
        self.session_dir = Path(session_dir)
        self.job_id = job_id
        self.session_id = session_id or self.session_dir.name
        self.jobs_dir = self.session_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.journal_file = self.jobs_dir / f"{job_id}.events.jsonl"
        self.lock_file = self.jobs_dir / f".journal_{job_id}.lock"
        self.max_segment_bytes = max_segment_bytes

        self._mu = threading.RLock()
        self._buffer: list[SSEEvent] = []
        self._listeners: list[_ListenerRegistration] = []
        self._next_id: int = 1
        self._init_cursor_from_file()

    def _init_cursor_from_file(self) -> None:
        """Initialize next monotonic event ID by scanning existing journal segments."""
        max_id = 0
        for raw_line in _iter_journal_lines(self.journal_file):
            line = raw_line.strip()
            if line:
                with contextlib.suppress(json.JSONDecodeError, KeyError, ValueError):
                    rec = json.loads(line)
                    eid = int(rec.get("id", 0))
                    if eid > max_id:
                        max_id = eid

        with self._mu:
            self._next_id = max_id + 1

    def buffer_event(self, event: str, data: Any) -> SSEEvent:
        """Buffer a telemetry event in memory (thread-safe; acquires zero file locks)."""
        now_iso = datetime.now(UTC).isoformat()
        with self._mu:
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

        with self._mu:
            if not self._buffer:
                return []

        with studio_lock(
            self.lock_file,
            level=L3_JOURNAL,
            session_id=self.session_id,
            timeout=timeout_sec,
        ):
            with self._mu:
                if not self._buffer:
                    return []
                events_to_write = self._buffer
                self._buffer = []

            try:
                self._sync_append_events(events_to_write)
            except BaseException:
                with self._mu:
                    self._buffer = events_to_write + self._buffer
                raise

            self._dispatch_to_listeners(events_to_write)
            return events_to_write

    async def flush_async(
        self, timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS
    ) -> list[SSEEvent]:
        """Asynchronously flush in-memory buffer to disk under L3 journal lock."""
        self._assert_no_l5_held()

        with self._mu:
            if not self._buffer:
                return []

        async with studio_lock(
            self.lock_file,
            level=L3_JOURNAL,
            session_id=self.session_id,
            timeout=timeout_sec,
        ):
            with self._mu:
                if not self._buffer:
                    return []
                events_to_write = self._buffer
                self._buffer = []

            try:
                await asyncio.to_thread(self._sync_append_events, events_to_write)
            except BaseException:
                with self._mu:
                    self._buffer = events_to_write + self._buffer
                raise

            self._dispatch_to_listeners(events_to_write)
            return events_to_write

    def _rotate_if_needed(self) -> None:
        """Rotate active journal file to numbered .gz segment when exceeding max_segment_bytes."""
        if not self.journal_file.is_file():
            return
        try:
            current_size = self.journal_file.stat().st_size
        except OSError:
            return
        if current_size < self.max_segment_bytes:
            return

        prefix = f"{self.journal_file.name}."
        next_idx = 1
        for candidate in self.jobs_dir.iterdir():
            cname = candidate.name
            if cname.startswith(prefix) and cname.endswith(".gz"):
                mid = cname[len(prefix) : -3]
                if mid.isdigit():
                    next_idx = max(next_idx, int(mid) + 1)

        gz_dest = self.jobs_dir / f"{self.journal_file.name}.{next_idx}.gz"
        gz_tmp = self.jobs_dir / f".{gz_dest.name}.{secrets.token_hex(4)}.tmp"
        empty_tmp = self.jobs_dir / f".{self.journal_file.name}.empty.{secrets.token_hex(4)}.tmp"
        flags = (
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            raw_fd = os.open(str(gz_tmp), flags, 0o600)
            with os.fdopen(raw_fd, "wb") as raw_fp:
                with (
                    self.journal_file.open("rb") as src_fp,
                    gzip.GzipFile(filename="", mode="wb", fileobj=raw_fp) as dst_gz,
                ):
                    shutil.copyfileobj(src_fp, dst_gz)
                raw_fp.flush()
                os.fsync(raw_fp.fileno())
            set_owner_only_permissions(gz_tmp)
            replace_with_retry(gz_tmp, gz_dest)
            set_owner_only_permissions(gz_dest)

            empty_fd = os.open(str(empty_tmp), flags, 0o600)
            try:
                os.fsync(empty_fd)
            finally:
                os.close(empty_fd)
            set_owner_only_permissions(empty_tmp)
            replace_with_retry(empty_tmp, self.journal_file)
            set_owner_only_permissions(self.journal_file)
        finally:
            for tmp_candidate in (gz_tmp, empty_tmp):
                with contextlib.suppress(OSError):
                    if tmp_candidate.exists():
                        tmp_candidate.unlink()

    def _sync_append_events(self, events: list[SSEEvent]) -> None:
        """Synchronously append events to file under held L3 lock."""
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed()
        with self.journal_file.open("a", encoding="utf-8") as fp:
            for evt in events:
                fp.write(json.dumps(evt.to_dict()) + "\n")
            fp.flush()
            os.fsync(fp.fileno())
        set_owner_only_permissions(self.journal_file)

    def _dispatch_to_listeners(self, events: list[SSEEvent]) -> None:
        """Thread-safely dispatch newly flushed events to subscribed asyncio queues."""
        with self._mu:
            regs = list(self._listeners)

        if not regs:
            return

        try:
            caller_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            caller_loop = None

        for reg in regs:

            def _deliver(
                target_reg: _ListenerRegistration = reg,
                evts: list[SSEEvent] = events,
            ) -> None:
                for evt in evts:
                    try:
                        target_reg.queue.put_nowait(evt)
                    except asyncio.QueueFull:
                        target_reg.overflowed = True

            if reg.loop is None or reg.loop is caller_loop:
                _deliver()
            elif not reg.loop.is_closed():
                with contextlib.suppress(RuntimeError):
                    reg.loop.call_soon_threadsafe(_deliver)

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
        """Subscribe a bounded live listener queue (`maxsize=1024`) for newly flushed events."""
        try:
            running_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        q: asyncio.Queue[SSEEvent] = asyncio.Queue(maxsize=MAX_LISTENER_QUEUE_SIZE)
        with self._mu:
            self._listeners.append(_ListenerRegistration(queue=q, loop=running_loop))
        return q

    def unsubscribe(self, q: asyncio.Queue[SSEEvent]) -> None:
        """Unsubscribe a live listener queue."""
        with self._mu:
            self._listeners = [reg for reg in self._listeners if reg.queue is not q]

    def consume_overflow_flag(self, q: asyncio.Queue[SSEEvent]) -> bool:
        """Atomically check and clear overflow flag for a subscribed queue."""
        with self._mu:
            for reg in self._listeners:
                if reg.queue is q:
                    was_overflowed = reg.overflowed
                    reg.overflowed = False
                    return was_overflowed
        return False


def replay_sse_events(
    journal_file: Path | str,
    last_event_id: int | str | None = None,
) -> Iterator[SSEEvent]:
    """Replay events from a journal file (and rotated .gz segments) strictly after last_event_id."""
    jpath = Path(journal_file)

    cursor_id = -1
    if last_event_id is not None:
        try:
            cursor_id = int(last_event_id)
        except (ValueError, TypeError):
            cursor_id = -1

    high_water_id = cursor_id
    for raw_line in _iter_journal_lines(jpath):
        line = raw_line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            evt = SSEEvent.from_dict(rec)
            if evt.id > high_water_id:
                high_water_id = evt.id
                yield evt
        except (json.JSONDecodeError, KeyError, ValueError):
            continue


async def stream_sse_events(
    journal_file: Path | str,
    last_event_id: int | str | None = None,
    writer: SSEJournalWriter | None = None,
    is_active: Callable[[], bool] | None = None,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
    keepalive_interval: float = _DEFAULT_KEEPALIVE_INTERVAL,
) -> AsyncIterator[str]:
    """Asynchronously stream SSE events with gapless subscribe-before-replay semantics."""
    jpath = Path(journal_file)
    last_emitted_id = -1
    if last_event_id is not None:
        with contextlib.suppress(ValueError, TypeError):
            last_emitted_id = int(last_event_id)

    # 1. Subscribe BEFORE replaying from disk so zero events flushed concurrently are dropped!
    q: asyncio.Queue[SSEEvent] | None = writer.subscribe() if writer is not None else None
    last_wire_send = time.monotonic()

    try:
        # 2. Replay historical events from journal file(s)
        for evt in replay_sse_events(jpath, last_event_id=last_emitted_id):
            yield evt.to_sse_str()
            last_emitted_id = evt.id
            last_wire_send = time.monotonic()
            if evt.event in TERMINAL_SSE_EVENTS:
                return

        # 3a. Live streaming via writer queue (deduplicating ids <= last_emitted_id)
        if writer is not None and q is not None:
            while True:
                if writer.consume_overflow_flag(q):
                    for evt in replay_sse_events(jpath, last_event_id=last_emitted_id):
                        yield evt.to_sse_str()
                        last_emitted_id = evt.id
                        last_wire_send = time.monotonic()
                        if evt.event in TERMINAL_SSE_EVENTS:
                            return

                try:
                    evt = await asyncio.wait_for(q.get(), timeout=poll_interval)
                    if evt.id > last_emitted_id:
                        yield evt.to_sse_str()
                        last_emitted_id = evt.id
                        last_wire_send = time.monotonic()
                        if evt.event in TERMINAL_SSE_EVENTS:
                            return
                except TimeoutError:
                    if writer.consume_overflow_flag(q):
                        for catchup_evt in replay_sse_events(jpath, last_event_id=last_emitted_id):
                            yield catchup_evt.to_sse_str()
                            last_emitted_id = catchup_evt.id
                            last_wire_send = time.monotonic()
                            if catchup_evt.event in TERMINAL_SSE_EVENTS:
                                return

                    if is_active is not None and not is_active():
                        # Drain any final queued or flushed journal events before closing
                        while not q.empty():
                            tail_evt = q.get_nowait()
                            if tail_evt.id > last_emitted_id:
                                yield tail_evt.to_sse_str()
                                last_emitted_id = tail_evt.id
                                if tail_evt.event in TERMINAL_SSE_EVENTS:
                                    return
                        for tail_evt in replay_sse_events(jpath, last_event_id=last_emitted_id):
                            yield tail_evt.to_sse_str()
                            last_emitted_id = tail_evt.id
                            if tail_evt.event in TERMINAL_SSE_EVENTS:
                                return
                        break

                    if time.monotonic() - last_wire_send >= keepalive_interval:
                        yield ": keepalive\n\n"
                        last_wire_send = time.monotonic()
        elif is_active is not None:
            # 3b. Cross-process file-tailing mode when writer is None
            while True:
                active_now = is_active()
                for evt in replay_sse_events(jpath, last_event_id=last_emitted_id):
                    yield evt.to_sse_str()
                    last_emitted_id = evt.id
                    last_wire_send = time.monotonic()
                    if evt.event in TERMINAL_SSE_EVENTS:
                        return
                if not active_now:
                    break
                await asyncio.sleep(poll_interval)
                if time.monotonic() - last_wire_send >= keepalive_interval:
                    yield ": keepalive\n\n"
                    last_wire_send = time.monotonic()
    finally:
        if writer is not None and q is not None:
            writer.unsubscribe(q)
