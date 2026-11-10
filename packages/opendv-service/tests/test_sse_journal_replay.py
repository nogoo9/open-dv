"""Unit and integration tests for W3C SSE event streaming and journal cursor replay.

Tests Task 4D.3 (#210):
- W3C SSE event format verification (id:, event:, data:, double newline).
- Multi-line data formatting per W3C EventSource standard.
- Monotonic Last-Event-ID cursor replay resuming strictly after cursor.
- Lock inversion prevention & flush ordering under Total Lock Hierarchy:
    - Attempting flush under L3 while holding L5 raises LockOrderViolationError.
    - Buffering under L5 followed by flush after L5 release succeeds.
- Async live streaming and termination on terminal events.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from opendv_service.studio.locks import (
    L5_SESSION,
    LockOrderViolationError,
    studio_lock,
)
from opendv_service.studio.sse_journal import (
    SSEEvent,
    SSEJournalWriter,
    replay_sse_events,
    stream_sse_events,
)


@pytest.fixture
def session_env(tmp_path: Path) -> dict[str, Any]:
    session_dir = tmp_path / "test-session"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "jobs").mkdir(parents=True, exist_ok=True)
    return {"dir": session_dir, "jobs_dir": session_dir / "jobs"}


def test_sse_event_formatting_standard() -> None:
    evt = SSEEvent(
        id=1,
        event="step_progress",
        data={"step": "bm25_matching", "progress": 0.5},
        timestamp="2026-11-10T00:00:00Z",
    )

    wire = evt.to_sse_str()
    assert wire.startswith("id: 1\nevent: step_progress\ndata: {")
    assert wire.endswith("\n\n")

    # Verify bytes
    wire_bytes = evt.to_sse_bytes()
    assert isinstance(wire_bytes, bytes)
    assert wire_bytes == wire.encode("utf-8")


def test_sse_event_formatting_multiline_data() -> None:
    evt = SSEEvent(
        id=2,
        event="log_message",
        data="First line of log\nSecond line of log\nThird line",
    )

    wire = evt.to_sse_str()
    assert "data: First line of log\ndata: Second line of log\ndata: Third line" in wire
    assert wire.endswith("\n\n")


def test_sse_event_serialization_roundtrip() -> None:
    original = SSEEvent(
        id=42,
        event="custom_event",
        data={"key": "value", "list": [1, 2, 3]},
        timestamp="2026-11-10T12:00:00Z",
    )

    as_dict = original.to_dict()
    restored = SSEEvent.from_dict(as_dict)
    assert restored == original


def test_last_event_id_replay_cursor(session_env: dict[str, Any]) -> None:
    sdir: Path = session_env["dir"]
    writer = SSEJournalWriter(session_dir=sdir, job_id="job-replay-1")

    # Buffer and flush 5 events
    for i in range(1, 6):
        writer.buffer_event(event=f"step_{i}", data={"idx": i})

    flushed = writer.flush()
    assert len(flushed) == 5

    journal_file = writer.journal_file
    assert journal_file.exists()

    # 1. Replay from beginning (last_event_id = None)
    all_events = list(replay_sse_events(journal_file, last_event_id=None))
    assert len(all_events) == 5
    assert [e.id for e in all_events] == [1, 2, 3, 4, 5]

    # 2. Replay cursor at id 3 (should return 4 and 5)
    replayed_after_3 = list(replay_sse_events(journal_file, last_event_id=3))
    assert len(replayed_after_3) == 2
    assert [e.id for e in replayed_after_3] == [4, 5]

    # 3. String cursor "3"
    replayed_str_3 = list(replay_sse_events(journal_file, last_event_id="3"))
    assert len(replayed_str_3) == 2
    assert [e.id for e in replayed_str_3] == [4, 5]

    # 4. Replay at last id (should return empty)
    replayed_tail = list(replay_sse_events(journal_file, last_event_id=5))
    assert len(replayed_tail) == 0

    # 5. Invalid cursor gracefully ignored (treated as replay from beginning)
    replayed_invalid = list(replay_sse_events(journal_file, last_event_id="bogus"))
    assert len(replayed_invalid) == 5


def test_lock_inversion_prevention_raises_on_l5_flush(session_env: dict[str, Any]) -> None:
    """Verifies docs/19 §6.3 Total Lock Hierarchy lock inversion prevention rule.

    Calling flush() under L3 while holding L5 session lock MUST raise LockOrderViolationError.
    """
    sdir: Path = session_env["dir"]
    session_lock_file = sdir / ".session.lock"
    writer = SSEJournalWriter(session_dir=sdir, job_id="job-lock-inv")

    # Under L5 session lock:
    with studio_lock(session_lock_file, level=L5_SESSION, session_id=sdir.name):
        # 1. Buffering events in memory under L5 is permitted (no lock acquisition)
        evt = writer.buffer_event("mutation_progress", {"percent": 25})
        assert evt.id == 1

        # 2. Flushing under L3 while holding L5 is STRICTLY FORBIDDEN (lock inversion)
        with pytest.raises(LockOrderViolationError) as exc_info:
            writer.flush()

        assert "Lock inversion violation" in str(exc_info.value)
        assert "Release L5 session lock before flushing" in str(exc_info.value)

    # 3. After releasing L5, flushing succeeds under L3
    flushed = writer.flush()
    assert len(flushed) == 1
    assert flushed[0].id == 1


def test_monotonic_ids_across_multiple_flushes_and_restarts(
    session_env: dict[str, Any],
) -> None:
    sdir: Path = session_env["dir"]

    # First session writer
    writer1 = SSEJournalWriter(session_dir=sdir, job_id="job-mono")
    writer1.buffer_event("evt1", {"n": 1})
    writer1.buffer_event("evt2", {"n": 2})
    writer1.flush()

    writer1.buffer_event("evt3", {"n": 3})
    writer1.flush()

    # Re-initialize writer (e.g. process restart / supervisor reconnect)
    writer2 = SSEJournalWriter(session_dir=sdir, job_id="job-mono")
    writer2.buffer_event("evt4", {"n": 4})
    writer2.flush()

    all_events = list(replay_sse_events(writer2.journal_file))
    ids = [e.id for e in all_events]
    assert ids == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_async_flush_and_live_streaming(session_env: dict[str, Any]) -> None:
    sdir: Path = session_env["dir"]
    writer = SSEJournalWriter(session_dir=sdir, job_id="job-async-stream")

    # Buffer initial events
    writer.buffer_event("step_1", {"msg": "started"})
    writer.buffer_event("step_2", {"msg": "processing"})
    await writer.flush_async()

    collected_events: list[str] = []

    async def consumer() -> None:
        async for chunk in stream_sse_events(
            journal_file=writer.journal_file,
            last_event_id=1,  # Resume from 2
            writer=writer,
            poll_interval=0.01,
        ):
            collected_events.append(chunk)

    consumer_task = asyncio.create_task(consumer())

    # Wait for consumer to process historical replay
    await asyncio.sleep(0.05)

    # Publish live event and terminal event
    writer.buffer_event("step_3", {"msg": "almost done"})
    writer.buffer_event("completed", {"msg": "finished successfully"})
    await writer.flush_async()

    # Wait for consumer to finish on terminal event
    await asyncio.wait_for(consumer_task, timeout=2.0)

    # Verify collected stream (ignoring periodic keepalive heartbeat comments)
    real_events = [c for c in collected_events if not c.startswith(":")]
    assert len(real_events) == 3  # id 2 (replayed), id 3 (live), id 4 (terminal)
    assert "id: 2" in real_events[0]
    assert "id: 3" in real_events[1]
    assert "id: 4" in real_events[2]
    assert "event: completed" in real_events[2]
