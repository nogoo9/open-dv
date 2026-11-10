"""Comprehensive unit tests for Open-DV Global Total Lock Hierarchy & Deadlock Prevention.

Tests Task 4D.2 (#209) and ADR-009 / docs/19 §6.3:
- 5-level total global lock order (L1 -> L2 -> L3 -> L4 -> L5)
- Monotonic acquisition order validation and LockOrderViolationError on inversion
- Lexicographical tie-break ordering for multi-session locks (min(s1, s2) -> max(s1, s2))
- Conflicting lock acquisition timeout raising LockTimeoutError (409 / exit 6)
- Reverse release order LIFO enforcement and foreign session early release exception
- Lock diagnostic metadata payload (PID, timestamp, level, hostname, session_id)
- Stale lock detection and crash recovery probe
- High-concurrency stress testing across threads, processes, and async tasks
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from opendv_service.studio.locks import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    L1_LOCAL_TOKEN,
    L2_JOB,
    L3_JOURNAL,
    L4_WORKSPACE,
    L5_SESSION,
    LockLevel,
    LockOrderViolationError,
    LockReleaseOrderViolationError,
    LockTimeoutError,
    StudioLock,
    get_held_locks,
    infer_lock_level,
    is_lock_stale,
    is_pid_alive,
    multi_studio_lock,
    read_lock_metadata,
)

# =============================================================================
# 1. Lock Level Hierarchy & Enumeration Tests
# =============================================================================


def test_lock_levels_enumeration() -> None:
    """Verify 5-level total order enumeration and constant definitions."""
    assert LockLevel.L1 == 1
    assert LockLevel.L2 == 2
    assert LockLevel.L3 == 3
    assert LockLevel.L4 == 4
    assert LockLevel.L5 == 5

    assert L1_LOCAL_TOKEN == LockLevel.L1
    assert L2_JOB == LockLevel.L2
    assert L3_JOURNAL == LockLevel.L3
    assert L4_WORKSPACE == LockLevel.L4
    assert L5_SESSION == LockLevel.L5

    # Strictly monotonic order L1 < L2 < L3 < L4 < L5
    assert L1_LOCAL_TOKEN < L2_JOB < L3_JOURNAL < L4_WORKSPACE < L5_SESSION
    assert DEFAULT_LOCK_TIMEOUT_SECONDS == 30.0


def test_infer_lock_level() -> None:
    """Verify automatic lock level inference from normative file names."""
    assert infer_lock_level(".local_token.lock") == LockLevel.L1
    assert infer_lock_level("/path/to/.local_token.lock") == LockLevel.L1

    assert infer_lock_level(".job_parse_001.lock") == LockLevel.L2
    assert infer_lock_level("/sessions/s1/jobs/.job_abc-123.lock") == LockLevel.L2

    assert infer_lock_level(".journal_parse_001.lock") == LockLevel.L3
    assert infer_lock_level("/sessions/s1/jobs/.journal_abc-123.lock") == LockLevel.L3

    assert infer_lock_level("graph_a.lock") == LockLevel.L4
    assert infer_lock_level("/workspace/root/graph_a.lock") == LockLevel.L4

    assert infer_lock_level(".session.lock") == LockLevel.L5
    assert infer_lock_level("/studio/session_xyz/.session.lock") == LockLevel.L5

    with pytest.raises(ValueError, match="Cannot infer LockLevel"):
        infer_lock_level("random_file.txt")


# =============================================================================
# 2. Single Lock Acquisition, Release, and Metadata Tests
# =============================================================================


def test_single_lock_acquisition_and_release_sync_all_5_levels(tmp_path: Path) -> None:
    """Test acquiring and releasing single locks across all 5 levels with sync context."""
    lock_configs = [
        (tmp_path / ".local_token.lock", LockLevel.L1, None, None),
        (tmp_path / "jobs" / ".job_job1.lock", LockLevel.L2, "sess1", "job1"),
        (tmp_path / "jobs" / ".journal_job1.lock", LockLevel.L3, "sess1", "job1"),
        (tmp_path / "graph_a.lock", LockLevel.L4, None, None),
        (tmp_path / ".session.lock", LockLevel.L5, "sess1", None),
    ]

    for path, level, session_id, resource_id in lock_configs:
        lock = StudioLock(
            path=path,
            level=level,
            session_id=session_id,
            resource_id=resource_id,
        )

        assert not lock.is_locked
        assert len(get_held_locks()) == 0

        with lock as active_lock:
            assert active_lock is lock
            assert lock.is_locked
            assert len(get_held_locks()) == 1
            assert get_held_locks()[0] is lock

            # Diagnostic metadata verification while held
            meta = read_lock_metadata(path)
            assert meta is not None
            assert meta.pid == os.getpid()
            assert meta.lock_level == int(level)
            assert meta.level_name == level.name
            assert meta.hostname == socket.gethostname()
            if session_id:
                assert meta.session_id == session_id
            if resource_id:
                assert meta.resource_id == resource_id

            # Verify timestamp format
            parsed_dt = datetime.fromisoformat(meta.acquired_at)
            assert parsed_dt.tzinfo is not None

        # Outside context manager, lock is released and tracking is cleared
        assert not lock.is_locked
        assert len(get_held_locks()) == 0


@pytest.mark.asyncio
async def test_single_lock_acquisition_and_release_async_all_5_levels(
    tmp_path: Path,
) -> None:
    """Test acquiring and releasing single locks across all 5 levels with async context."""
    lock_configs = [
        (tmp_path / ".local_token.lock", LockLevel.L1, None, None),
        (tmp_path / "jobs" / ".job_job1.lock", LockLevel.L2, "sess1", "job1"),
        (tmp_path / "jobs" / ".journal_job1.lock", LockLevel.L3, "sess1", "job1"),
        (tmp_path / "graph_a.lock", LockLevel.L4, None, None),
        (tmp_path / ".session.lock", LockLevel.L5, "sess1", None),
    ]

    for path, level, session_id, resource_id in lock_configs:
        lock = StudioLock(
            path=path,
            level=level,
            session_id=session_id,
            resource_id=resource_id,
        )

        assert not lock.is_locked
        async with lock as active_lock:
            assert active_lock is lock
            assert lock.is_locked
            assert len(get_held_locks()) == 1

            meta = read_lock_metadata(path)
            assert meta is not None
            assert meta.pid == os.getpid()
            assert meta.lock_level == int(level)

        assert not lock.is_locked
        assert len(get_held_locks()) == 0


def test_factory_methods_all_5_levels(tmp_path: Path) -> None:
    """Verify StudioLock factory methods construct correct paths, levels, and IDs."""
    root = tmp_path / "studio"
    session_dir = root / "session_alpha"
    ws = tmp_path / "workspace"

    l1 = StudioLock.local_token(root)
    assert l1.level == LockLevel.L1
    assert l1.path == (root / ".local_token.lock").resolve()

    l2 = StudioLock.job(session_dir, "ingest_001")
    assert l2.level == LockLevel.L2
    assert l2.path == (session_dir / "jobs" / ".job_ingest_001.lock").resolve()
    assert l2.session_id == "session_alpha"
    assert l2.resource_id == "ingest_001"

    l3 = StudioLock.journal(session_dir, "ingest_001")
    assert l3.level == LockLevel.L3
    assert l3.path == (session_dir / "jobs" / ".journal_ingest_001.lock").resolve()
    assert l3.session_id == "session_alpha"
    assert l3.resource_id == "ingest_001"

    l4 = StudioLock.workspace(ws)
    assert l4.level == LockLevel.L4
    assert l4.path == (ws / "graph_a.lock").resolve()

    l5 = StudioLock.session(session_dir)
    assert l5.level == LockLevel.L5
    assert l5.path == (session_dir / ".session.lock").resolve()
    assert l5.session_id == "session_alpha"


# =============================================================================
# 3. Total Order Enforcement & LockOrderViolationError Inversion Tests
# =============================================================================


def test_strict_l1_to_l5_total_order_monotonic_success(tmp_path: Path) -> None:
    """Verify monotonic acquisition of L1 -> L2 -> L3 -> L4 -> L5 succeeds."""
    l1 = StudioLock.local_token(tmp_path)
    l2 = StudioLock.job(tmp_path / "s1", "j1")
    l3 = StudioLock.journal(tmp_path / "s1", "j1")
    l4 = StudioLock.workspace(tmp_path)
    l5 = StudioLock.session(tmp_path / "s1")

    with l1, l2, l3, l4, l5:
        assert len(get_held_locks()) == 5
        assert [lk.level for lk in get_held_locks()] == [
            LockLevel.L1,
            LockLevel.L2,
            LockLevel.L3,
            LockLevel.L4,
            LockLevel.L5,
        ]

    assert len(get_held_locks()) == 0


def test_lock_order_inversion_l4_before_l2_raises_violation(tmp_path: Path) -> None:
    """Attempting to acquire L2 while holding L4 must raise LockOrderViolationError."""
    l4 = StudioLock.workspace(tmp_path)
    l2 = StudioLock.job(tmp_path / "s1", "j1")

    with l4:
        with pytest.raises(LockOrderViolationError) as exc_info, l2:
            pass  # Should not be reached

        err = exc_info.value
        assert "Lock order violation" in str(err)
        assert err.http_status == 409
        assert err.exit_code == 6
        assert err.code == "LOCK_ORDER_VIOLATION"
        assert err.attempted_key == l2.order_key

    # L4 was cleanly released
    assert len(get_held_locks()) == 0


def test_lock_order_inversion_l5_before_l1_raises_violation(tmp_path: Path) -> None:
    """Attempting to acquire L1 while holding L5 must raise LockOrderViolationError."""
    l5 = StudioLock.session(tmp_path / "s1")
    l1 = StudioLock.local_token(tmp_path)

    with l5:
        with pytest.raises(LockOrderViolationError) as exc_info, l1:
            pass

        err = exc_info.value
        assert err.http_status == 409
        assert err.exit_code == 6
        assert err.code == "LOCK_ORDER_VIOLATION"


def test_lock_reentrancy_same_lock_rejected(tmp_path: Path) -> None:
    """Attempting to acquire the same lock while already held is rejected as order violation."""
    l5 = StudioLock.session(tmp_path / "s1")

    with l5:
        with pytest.raises(LockOrderViolationError) as exc_info, l5:
            pass

        err = exc_info.value
        assert "Lock order violation" in str(err)
        assert err.code == "LOCK_ORDER_VIOLATION"


def test_inversion_detected_before_file_lock_acquired(tmp_path: Path) -> None:
    """Order violation check runs BEFORE opening or touching the candidate file lock."""
    candidate_path = tmp_path / "never_created" / ".local_token.lock"
    l5 = StudioLock.session(tmp_path / "s1")
    l1 = StudioLock(candidate_path, level=LockLevel.L1)

    with l5:
        with pytest.raises(LockOrderViolationError):
            l1.acquire()

        # The candidate file must NOT even exist because violation was raised before I/O
        assert not candidate_path.exists()


# =============================================================================
# 4. Multi-Session Lexicographical Tie-Break Ordering Tests
# =============================================================================


def test_multi_session_lexicographical_tie_break_success(tmp_path: Path) -> None:
    """Same level across sessions: min(s1, s2) -> max(s1, s2) acquisition succeeds."""
    l5_alpha = StudioLock.session(tmp_path / "alpha", session_id="alpha")
    l5_beta = StudioLock.session(tmp_path / "beta", session_id="beta")

    # 'alpha' < 'beta', so alpha -> beta is valid monotonic order
    with l5_alpha, l5_beta:
        assert len(get_held_locks()) == 2
        assert [lk.session_id for lk in get_held_locks()] == ["alpha", "beta"]

    assert len(get_held_locks()) == 0


def test_multi_session_lexicographical_inversion_raises_violation(tmp_path: Path) -> None:
    """Acquiring max(s1, s2) then min(s1, s2) is an inversion and raises LockOrderViolationError."""
    l5_alpha = StudioLock.session(tmp_path / "alpha", session_id="alpha")
    l5_beta = StudioLock.session(tmp_path / "beta", session_id="beta")

    # 'beta' acquired first: attempting 'alpha' second must fail
    with l5_beta:
        with pytest.raises(LockOrderViolationError) as exc_info, l5_alpha:
            pass

        err = exc_info.value
        assert "Lock order violation" in str(err)
        assert err.code == "LOCK_ORDER_VIOLATION"


def test_multi_session_case_folding_ordering(tmp_path: Path) -> None:
    """Session IDs are sorted case-insensitively by lowercase string."""
    # "BETA" and "alpha": "alpha" < "beta"
    p_beta = tmp_path / "BETA" / ".session.lock"
    p_alpha = tmp_path / "alpha" / ".session.lock"
    l5_upper = StudioLock(p_beta, level=LockLevel.L5, session_id="BETA")
    l5_lower = StudioLock(p_alpha, level=LockLevel.L5, session_id="alpha")

    # Holding "BETA" and trying to acquire "alpha" fails because 'alpha' < 'beta'
    with l5_upper, pytest.raises(LockOrderViolationError):
        l5_lower.acquire()


def test_multi_studio_lock_auto_sorts_sessions(tmp_path: Path) -> None:
    """MultiStudioLock automatically sorts multi-session locks to prevent deadlocks."""
    l5_gamma = StudioLock.session(tmp_path / "gamma", session_id="gamma")
    l5_alpha = StudioLock.session(tmp_path / "alpha", session_id="alpha")
    l5_beta = StudioLock.session(tmp_path / "beta", session_id="beta")

    # Passed in unsorted order [gamma, alpha, beta]
    with multi_studio_lock([l5_gamma, l5_alpha, l5_beta]) as acquired:
        assert len(acquired) == 3
        # Must be acquired strictly in order: alpha -> beta -> gamma
        assert [lk.session_id for lk in acquired] == ["alpha", "beta", "gamma"]
        assert [lk.session_id for lk in get_held_locks()] == ["alpha", "beta", "gamma"]

    assert len(get_held_locks()) == 0


def test_multi_studio_lock_auto_sorts_levels(tmp_path: Path) -> None:
    """MultiStudioLock automatically sorts locks across different levels L1..L5."""
    l5 = StudioLock.session(tmp_path / "s1")
    l1 = StudioLock.local_token(tmp_path)
    l4 = StudioLock.workspace(tmp_path)

    # Passed out-of-order [L5, L1, L4]
    with multi_studio_lock([l5, l1, l4]) as acquired:
        assert [lk.level for lk in acquired] == [LockLevel.L1, LockLevel.L4, LockLevel.L5]

    assert len(get_held_locks()) == 0


@pytest.mark.asyncio
async def test_multi_studio_lock_async(tmp_path: Path) -> None:
    """MultiStudioLock works with async context managers."""
    l4 = StudioLock.workspace(tmp_path)
    l5 = StudioLock.session(tmp_path / "s1")

    async with multi_studio_lock([l5, l4]) as acquired:
        assert [lk.level for lk in acquired] == [LockLevel.L4, LockLevel.L5]

    assert len(get_held_locks()) == 0


# =============================================================================
# 5. Lock Contention & Timeout Enforcement Tests
# =============================================================================


def test_timeout_conflicting_lock_sync(tmp_path: Path) -> None:
    """Conflicting lock acquisition times out and raises LockTimeoutError (409 / exit 6)."""
    lock_path = tmp_path / "graph_a.lock"
    holder = StudioLock(lock_path, level=LockLevel.L4)
    challenger = StudioLock(lock_path, level=LockLevel.L4, timeout=0.1)

    # Use a thread to hold the lock in a separate execution context
    holder_acquired = threading.Event()
    stop_holder = threading.Event()

    def run_holder() -> None:
        with holder:
            holder_acquired.set()
            stop_holder.wait(timeout=2.0)

    t = threading.Thread(target=run_holder)
    t.start()
    try:
        assert holder_acquired.wait(timeout=2.0)

        # Challenger should time out within ~0.1s
        t_start = time.monotonic()
        with pytest.raises(LockTimeoutError) as exc_info:
            challenger.acquire()
        elapsed = time.monotonic() - t_start

        err = exc_info.value
        assert "LOCK_TIMEOUT" in str(err)
        assert err.http_status == 409
        assert err.exit_code == 6
        assert err.code == "LOCK_TIMEOUT"
        assert err.path == lock_path.resolve()
        assert elapsed >= 0.08

    finally:
        stop_holder.set()
        t.join(timeout=2.0)


@pytest.mark.asyncio
async def test_timeout_conflicting_lock_async(tmp_path: Path) -> None:
    """Async acquire times out cleanly when lock is contended."""
    lock_path = tmp_path / "graph_a.lock"
    holder = StudioLock(lock_path, level=LockLevel.L4)
    challenger = StudioLock(lock_path, level=LockLevel.L4, timeout=0.1)

    holder_acquired = threading.Event()
    stop_holder = threading.Event()

    def run_holder() -> None:
        with holder:
            holder_acquired.set()
            stop_holder.wait(timeout=2.0)

    t = threading.Thread(target=run_holder)
    t.start()
    try:
        assert holder_acquired.wait(timeout=2.0)

        t_start = time.monotonic()
        with pytest.raises(LockTimeoutError) as exc_info:
            await challenger.acquire_async()
        elapsed = time.monotonic() - t_start

        assert exc_info.value.code == "LOCK_TIMEOUT"
        assert exc_info.value.http_status == 409
        assert exc_info.value.exit_code == 6
        assert elapsed >= 0.08
    finally:
        stop_holder.set()
        t.join(timeout=2.0)


# =============================================================================
# 6. Reverse Release Order & Foreign Session Early Release Exception
# =============================================================================


def test_reverse_release_order_lifo_success(tmp_path: Path) -> None:
    """Standard reverse order release L5 then L4 succeeds cleanly."""
    l4 = StudioLock.workspace(tmp_path)
    l5 = StudioLock.session(tmp_path / "s1")

    l4.acquire()
    l5.acquire()
    assert len(get_held_locks()) == 2

    # LIFO: release L5 first, then L4
    l5.release()
    assert len(get_held_locks()) == 1
    assert get_held_locks()[0] is l4

    l4.release()
    assert len(get_held_locks()) == 0


def test_reverse_release_order_violation_raises(tmp_path: Path) -> None:
    """Releasing L4 while L5 is still held violates reverse release order."""
    l4 = StudioLock.workspace(tmp_path)
    l5 = StudioLock.session(tmp_path / "s1")

    l4.acquire()
    l5.acquire()

    try:
        with pytest.raises(LockReleaseOrderViolationError) as exc_info:
            l4.release()

        err = exc_info.value
        assert "Lock release order violation" in str(err)
        assert err.code == "LOCK_RELEASE_ORDER_VIOLATION"
        assert err.http_status == 409
        assert err.exit_code == 6
    finally:
        # Clean up in correct order
        l5.release()
        l4.release()


def test_foreign_session_early_release_exception_intent_less_than_curr(
    tmp_path: Path,
) -> None:
    """Promotion recovery exception: s_intent < s_curr permits early release of s_intent."""
    # When s_intent < s_curr:
    # 1. L4
    # 2. L5(s_intent, is_foreign=True)
    # 3. L5(s_curr, is_foreign=False)
    l4 = StudioLock.workspace(tmp_path)
    l5_intent = StudioLock.session(
        tmp_path / "session_01", session_id="session_01", is_foreign=True
    )
    l5_curr = StudioLock.session(
        tmp_path / "session_02", session_id="session_02", is_foreign=False
    )

    l4.acquire()
    l5_intent.acquire()
    l5_curr.acquire()

    assert len(get_held_locks()) == 3
    # Stack: [L4, L5(s_intent), L5(s_curr)]
    # Under standard LIFO, l5_intent is NOT top of stack.
    # BUT because is_foreign=True and level=L5, early release is explicitly permitted!
    l5_intent.release()

    # l5_curr and l4 remain held
    assert len(get_held_locks()) == 2
    assert get_held_locks()[0] is l4
    assert get_held_locks()[1] is l5_curr

    # Complete the operation holding local session L5
    l5_curr.release()
    l4.release()
    assert len(get_held_locks()) == 0


def test_foreign_session_early_release_exception_curr_less_than_intent(
    tmp_path: Path,
) -> None:
    """Promotion recovery exception: s_curr < s_intent permits early release of s_intent."""
    # When s_curr < s_intent:
    # 1. L4
    # 2. L5(s_curr, is_foreign=False)
    # 3. L5(s_intent, is_foreign=True)
    l4 = StudioLock.workspace(tmp_path)
    l5_curr = StudioLock.session(
        tmp_path / "session_01", session_id="session_01", is_foreign=False
    )
    l5_intent = StudioLock.session(
        tmp_path / "session_02", session_id="session_02", is_foreign=True
    )

    l4.acquire()
    l5_curr.acquire()
    l5_intent.acquire()

    # Release foreign session lock early
    l5_intent.release()
    assert len(get_held_locks()) == 2

    # Local session lock retained
    l5_curr.release()
    l4.release()
    assert len(get_held_locks()) == 0


def test_foreign_early_release_rejected_for_non_foreign(tmp_path: Path) -> None:
    """Non-foreign session locks cannot be released out of order."""
    l5_s1 = StudioLock.session(tmp_path / "s1", session_id="s1", is_foreign=False)
    l5_s2 = StudioLock.session(tmp_path / "s2", session_id="s2", is_foreign=False)

    l5_s1.acquire()
    l5_s2.acquire()

    try:
        with pytest.raises(LockReleaseOrderViolationError):
            l5_s1.release()  # Not foreign -> rejected
    finally:
        l5_s2.release()
        l5_s1.release()


def test_foreign_early_release_rejected_for_non_l5_levels(tmp_path: Path) -> None:
    """Foreign early release exception applies strictly to L5 session locks, not L4 or below."""
    l4 = StudioLock(tmp_path / "graph_a.lock", level=LockLevel.L4, is_foreign=True)
    l5 = StudioLock.session(tmp_path / "s1")

    l4.acquire()
    l5.acquire()

    try:
        with pytest.raises(LockReleaseOrderViolationError):
            # Attempting early release of L4 is rejected even if marked foreign
            l4.release()
    finally:
        l5.release()
        l4.release()


# =============================================================================
# 7. Stale Lock Detection & Crash Recovery Probe Tests
# =============================================================================


def test_stale_lock_detection_nonexistent_and_empty(tmp_path: Path) -> None:
    """Non-existent and empty files are not stale locks."""
    p_none = tmp_path / "none.lock"
    assert not is_lock_stale(p_none)

    p_empty = tmp_path / "empty.lock"
    p_empty.touch()
    assert not is_lock_stale(p_empty)


def test_stale_lock_detection_active_lock_is_not_stale(tmp_path: Path) -> None:
    """An actively held lock is NOT stale."""
    lock_path = tmp_path / ".session.lock"
    lock = StudioLock(lock_path, level=LockLevel.L5)

    with lock:
        assert not is_lock_stale(lock_path)

    # After clean release, file is empty -> not stale
    assert not is_lock_stale(lock_path)


def test_stale_lock_detection_crashed_holder_is_stale(tmp_path: Path) -> None:
    """A lock file on disk with metadata from a crashed/dead PID is stale."""
    lock_path = tmp_path / ".session.lock"
    fake_meta = {
        "pid": 999999,
        "acquired_at": datetime.now(UTC).isoformat(),
        "hostname": socket.gethostname(),
        "lock_level": 5,
        "level_name": "L5",
        "session_id": "crashed_session",
    }
    lock_path.write_text(json.dumps(fake_meta), encoding="utf-8")

    # Since no process holds the OS file lock on lock_path, it's stale!
    assert is_lock_stale(lock_path)


def test_stale_lock_reclaim_on_new_acquire(tmp_path: Path) -> None:
    """A new process/lock cleanly reclaims a stale lock file and overwrites metadata."""
    lock_path = tmp_path / ".session.lock"
    fake_meta = {
        "pid": 999999,
        "acquired_at": "2020-01-01T00:00:00Z",
        "hostname": "old-host",
        "lock_level": 5,
        "level_name": "L5",
        "session_id": "old_session",
    }
    lock_path.write_text(json.dumps(fake_meta), encoding="utf-8")
    assert is_lock_stale(lock_path)

    # Reclaim lock
    lock = StudioLock(lock_path, level=LockLevel.L5, session_id="new_session")
    with lock:
        assert not is_lock_stale(lock_path)
        meta = read_lock_metadata(lock_path)
        assert meta is not None
        assert meta.pid == os.getpid()
        assert meta.session_id == "new_session"

    assert not is_lock_stale(lock_path)


def test_is_pid_alive() -> None:
    """Verify is_pid_alive accurately checks process existence."""
    assert is_pid_alive(os.getpid())
    assert not is_pid_alive(-1)
    assert not is_pid_alive(0)


# =============================================================================
# 8. High-Concurrency Stress Tests (Multi-Threaded, Multi-Process, Async)
# =============================================================================


def test_multi_threaded_concurrency_stress(tmp_path: Path) -> None:
    """Multi-threaded stress test: 10 threads contend for lock; counter is race-free."""
    lock_path = tmp_path / "shared.lock"
    counter = 0
    threads = []
    num_threads = 10
    increments_per_thread = 20

    def worker() -> None:
        nonlocal counter
        lk = StudioLock(lock_path, level=LockLevel.L4, timeout=15.0)
        for _ in range(increments_per_thread):
            with lk:
                current = counter
                time.sleep(0.001)  # Context switch window
                counter = current + 1

    for _ in range(num_threads):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=30.0)

    assert counter == num_threads * increments_per_thread


def test_multi_process_concurrency_stress(tmp_path: Path) -> None:
    """Multi-process stress test: separate processes contend for OS file lock."""
    lock_path = tmp_path / "mp.lock"
    data_path = tmp_path / "data.txt"
    data_path.write_text("0", encoding="utf-8")

    num_processes = 4
    increments = 15

    child_script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from opendv_service.studio.locks import StudioLock, LockLevel\n"
        "lock_path = Path(sys.argv[1])\n"
        "data_path = Path(sys.argv[2])\n"
        "increments = int(sys.argv[3])\n"
        "lk = StudioLock(lock_path, level=LockLevel.L4, timeout=30.0)\n"
        "for _ in range(increments):\n"
        "    with lk:\n"
        "        val = int(data_path.read_text(encoding='utf-8').strip())\n"
        "        data_path.write_text(str(val + 1), encoding='utf-8')\n"
    )

    env = dict(os.environ)
    src_dir = str(Path(__file__).resolve().parent.parent / "src")
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing_pp}" if existing_pp else src_dir

    procs = []
    for _ in range(num_processes):
        p = subprocess.Popen(
            [sys.executable, "-c", child_script, str(lock_path), str(data_path), str(increments)],
            env=env,
        )
        procs.append(p)

    for p in procs:
        ret = p.wait(timeout=30.0)
        assert ret == 0

    final_val = int(data_path.read_text(encoding="utf-8").strip())
    assert final_val == num_processes * increments


@pytest.mark.asyncio
async def test_async_concurrency_stress(tmp_path: Path) -> None:
    """Async task stress test: 10 coroutines contend for lock cleanly."""
    lock_path = tmp_path / "async_shared.lock"
    counter = 0

    async def worker() -> None:
        nonlocal counter
        lk = StudioLock(lock_path, level=LockLevel.L4, timeout=15.0)
        for _ in range(15):
            async with lk:
                val = counter
                await asyncio.sleep(0.001)
                counter = val + 1

    tasks = [asyncio.create_task(worker()) for _ in range(8)]
    await asyncio.gather(*tasks)

    assert counter == 8 * 15
