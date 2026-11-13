"""Global total lock hierarchy and deadlock prevention for Open-DV Ontology Studio.

Implements Task 4D.2 / Issue #209 and ADR-009 / ADR-016 / docs/19 §6.3:
- 5-level total global lock order:
    L1: .local_token.lock -> L2: .job_<jid>.lock -> L3: .journal_<jid>.lock ->
    L4: graph_a.lock -> L5: .session.lock
- Strict monotonic acquisition order validation:
    Locks must be acquired strictly by level L1 -> L5. Multi-session locks at
    the same level sort lexicographically by lowercase session_id (min(s1, s2) -> max(s1, s2)),
    resource_id, and normalized file path os.path.normcase(str(path)).
    Attempting out-of-order acquisition raises typed LockOrderViolationError before file locking.
- 30-second default timeout (configurable) raising LockTimeoutError("LOCK_TIMEOUT") (409 / exit 6).
- Reverse release order enforcement with explicit permitted exception for early release
    of foreign session lock (L5(s_intent)) during promotion intent recovery.
- Cross-platform file locking handling Windows WinError 32 sharing violations and Unix flock
    with exponential backoff and jitter (10ms to 250ms).
- Diagnostic lock metadata tracking PID, timestamp, hostname, lock level, and session ID,
    with deterministic unlock-and-close rollback on metadata write failures.
- Stale lock detection and crash recovery probe.
- Clean sync and async context managers: studio_lock, multi_studio_lock, reentrant_session_lock.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import os
import random
import socket
import sys
import threading
import time
from collections.abc import Iterable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO, Final

from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_service.studio.workspace_security import set_owner_only_permissions

if sys.platform == "win32":
    import ctypes
    import msvcrt

    _WIN32_LOCK_OFFSET: Final[int] = 1_000_000
    _WIN32_LOCK_LENGTH: Final[int] = 1
else:
    import fcntl


class LockLevel(IntEnum):
    """Authoritative 5-level total lock hierarchy per docs/19 §6.3."""

    L1 = 1  # .local_token.lock
    L2 = 2  # .job_<jid>.lock
    L3 = 3  # .journal_<jid>.lock
    L4 = 4  # graph_a.lock
    L5 = 5  # .session.lock


L1_LOCAL_TOKEN: Final[LockLevel] = LockLevel.L1
L2_JOB: Final[LockLevel] = LockLevel.L2
L3_JOURNAL: Final[LockLevel] = LockLevel.L3
L4_WORKSPACE: Final[LockLevel] = LockLevel.L4
L5_SESSION: Final[LockLevel] = LockLevel.L5

DEFAULT_LOCK_TIMEOUT_SECONDS: Final[float] = 30.0
MIN_BACKOFF_SECONDS: Final[float] = 0.010  # 10ms
MAX_BACKOFF_SECONDS: Final[float] = 0.250  # 250ms
STALE_EMPTY_LOCK_AGE_SECONDS: Final[float] = 5.0

LockOrderKey = tuple[int, str, str, str]


class StudioLockError(OpenDVError):
    """Base exception for Ontology Studio file locking errors."""

    code: str = "LOCK_ERROR"
    error_code: ErrorCode = ErrorCode.INTERNAL_ERROR
    http_status: int = 500
    exit_code: int = 1

    def __init__(self, message: str = "Studio lock error", *args: Any) -> None:
        Exception.__init__(self, message, *args)
        self.message = message
        self.status_code = self.http_status
        self.details = {}


class LockTimeoutError(StudioLockError):
    """Raised when a lock acquisition times out within the window (HTTP 409, exit code 6)."""

    code: str = "LOCK_TIMEOUT"
    error_code: ErrorCode = ErrorCode.LOCK_TIMEOUT
    http_status: int = 409
    exit_code: int = 6

    def __init__(
        self,
        message: str = "LOCK_TIMEOUT",
        path: Path | None = None,
        timeout: float | None = None,
        *args: Any,
    ) -> None:
        super().__init__(message, *args)
        self.path = path
        self.timeout = timeout


class LockOrderViolationError(StudioLockError):
    """Raised when an out-of-order lock acquisition is attempted before acquiring file lock."""

    code: str = "LOCK_ORDER_VIOLATION"
    error_code: ErrorCode = ErrorCode.LOCK_TIMEOUT
    http_status: int = 409
    exit_code: int = 6

    def __init__(
        self,
        message: str,
        attempted_key: tuple[Any, ...] | None = None,
        held_keys: list[tuple[Any, ...]] | None = None,
        *args: Any,
    ) -> None:
        super().__init__(message, *args)
        self.attempted_key = attempted_key
        self.held_keys = held_keys


class LockReleaseOrderViolationError(LockOrderViolationError):
    """Raised when a lock is released out of reverse acquisition order."""

    code: str = "LOCK_RELEASE_ORDER_VIOLATION"
    http_status: int = 409
    exit_code: int = 6


@dataclass(frozen=True)
class LockMetadata:
    """Diagnostic metadata payload stored inside active lock files."""

    pid: int
    acquired_at: str
    hostname: str
    lock_level: int
    level_name: str
    session_id: str | None = None
    resource_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert metadata to dictionary representation."""
        return {
            "pid": self.pid,
            "acquired_at": self.acquired_at,
            "hostname": self.hostname,
            "lock_level": self.lock_level,
            "level_name": self.level_name,
            "session_id": self.session_id,
            "resource_id": self.resource_id,
        }

    def to_json(self) -> str:
        """Serialize metadata to formatted JSON."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LockMetadata:
        """Construct LockMetadata from dictionary payload."""
        level_val = int(data["lock_level"])
        try:
            level_name = LockLevel(level_val).name
        except ValueError:
            level_name = str(data.get("level_name", f"L{level_val}"))

        return cls(
            pid=int(data["pid"]),
            acquired_at=str(data["acquired_at"]),
            hostname=str(data["hostname"]),
            lock_level=level_val,
            level_name=str(data.get("level_name", level_name)),
            session_id=data.get("session_id"),
            resource_id=data.get("resource_id"),
        )


def infer_lock_level(path: Path | str) -> LockLevel:
    """Infer LockLevel from lock filename according to docs/19 §6.3 conventions."""
    p = Path(path)
    name = p.name
    if name == ".local_token.lock":
        return LockLevel.L1
    if name.startswith(".job_") and name.endswith(".lock"):
        return LockLevel.L2
    if name.startswith(".journal_") and name.endswith(".lock"):
        return LockLevel.L3
    if name == "graph_a.lock" or name.endswith(".graph_a.lock"):
        return LockLevel.L4
    if name == ".session.lock" or (name.startswith(".lifecycle_") and name.endswith(".lock")):
        return LockLevel.L5
    raise ValueError(
        f"Cannot infer LockLevel from filename '{name}'. Explicit level parameter required."
    )


# ContextVar tracking (owner_thread_id, owner_task_id, lock) tuples.
# Because ContextVar snapshots are inherited by asyncio.create_task() and asyncio.to_thread(),
# ownership is filtered by (threading.get_ident(), id(asyncio.current_task())) and lock.is_locked.
_HeldLockEntry = tuple[int, int | None, "StudioLock"]
_held_locks_var: ContextVar[tuple[_HeldLockEntry, ...]] = ContextVar(
    "studio_held_locks", default=()
)


def _current_execution_owner() -> tuple[int, int | None]:
    """Return `(thread_id, task_id)` identifying the active thread and asyncio Task."""
    cur_tid = threading.get_ident()
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return (cur_tid, id(task) if task is not None else None)


def get_owned_held_locks() -> tuple[StudioLock, ...]:
    """Return immutable tuple of active locks owned by the current thread and asyncio task."""
    cur_tid, cur_task_id = _current_execution_owner()
    return tuple(
        lock
        for owner_tid, owner_task_id, lock in _held_locks_var.get()
        if lock.is_locked and owner_tid == cur_tid and owner_task_id == cur_task_id
    )


def get_held_locks() -> tuple[StudioLock, ...]:
    """Return immutable tuple of locks currently held in the active task/thread."""
    return get_owned_held_locks()


def _is_contention_error(err: BaseException) -> bool:
    """Check if exception represents a lock contention / sharing violation."""
    if isinstance(err, BlockingIOError):
        return True
    if isinstance(err, PermissionError):
        return True
    if isinstance(err, OSError):
        winerror = getattr(err, "winerror", None)
        if winerror in (32, 33):  # ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
            return True
        if err.errno in (
            errno.EACCES,
            errno.EAGAIN,
            errno.EWOULDBLOCK,
            errno.EDEADLK,
        ):
            return True
    return False


def _validate_acquisition_order(candidate: StudioLock) -> None:
    """Validate that candidate lock respects total global acquisition order."""
    held = get_owned_held_locks()
    if not held:
        return

    cand_key = candidate.order_key
    for h in held:
        if cand_key <= h.order_key:
            raise LockOrderViolationError(
                f"Lock order violation: Attempting to acquire lock '{candidate.path.name}' "
                f"(level={candidate.level.name}, session_id={candidate.session_id}, "
                f"order_key={cand_key}) while holding lock '{h.path.name}' "
                f"(level={h.level.name}, session_id={h.session_id}, order_key={h.order_key}). "
                f"Total order L1->L5 and session tie-break require monotonic acquisition.",
                attempted_key=cand_key,
                held_keys=[x.order_key for x in held],
            )


def _validate_release_order(
    lock: StudioLock,
    allow_foreign_early_release: bool = False,
) -> None:
    """Validate reverse release order L5 -> L1."""
    held = get_owned_held_locks()
    if not held:
        return

    if lock is held[-1]:
        return

    if (
        (lock.is_foreign or allow_foreign_early_release)
        and lock.level == LockLevel.L5
        and lock in held
    ):
        return

    raise LockReleaseOrderViolationError(
        f"Lock release order violation: Attempting to release lock '{lock.path.name}' "
        f"(level={lock.level.name}, session_id={lock.session_id}) out of reverse order. "
        f"Most recently acquired lock is '{held[-1].path.name}' "
        f"(level={held[-1].level.name}, session_id={held[-1].session_id}).",
        attempted_key=lock.order_key,
        held_keys=[x.order_key for x in held],
    )


def is_pid_alive(pid: int) -> bool:
    """Check if process with given PID exists and has not exited on the current host."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000, STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong(0)
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return int(exit_code.value) == 259
            return True
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except OSError as err:
        return err.errno == errno.EPERM
    return True


def read_lock_metadata(path: Path | str) -> LockMetadata | None:
    """Safely read and parse diagnostic metadata payload from lock file."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        raw = p.read_bytes()
        if not raw.strip():
            return None
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            return None
        return LockMetadata.from_dict(data)
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None


def _probe_kernel_lock_free(p: Path) -> bool:
    """Non-blocking probe testing whether the OS kernel lock on `p` is uncontended."""
    try:
        with open(p, "a+b") as f:
            if sys.platform == "win32":
                f.seek(_WIN32_LOCK_OFFSET)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, _WIN32_LOCK_LENGTH)
                f.seek(_WIN32_LOCK_OFFSET)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, _WIN32_LOCK_LENGTH)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (PermissionError, OSError) as err:
        if _is_contention_error(err):
            return False
        raise
    return True


def is_lock_stale(path: Path | str) -> bool:
    """Detect if a lock file on disk is abandoned and not actively held.

    1. If diagnostic metadata is present on the same hostname and `is_pid_alive(meta.pid)`
       is False, returns True immediately without probing/contending on the kernel lock.
    2. If diagnostic metadata is present (live PID or remote hostname), probes the OS
       kernel lock non-blockingly.
    3. If the lock file is 0 bytes (e.g. crash between open and metadata flush) and older
       than `STALE_EMPTY_LOCK_AGE_SECONDS` (5s), probes the OS kernel lock and returns True
       if uncontended.
    """
    p = Path(path)
    if not p.is_file():
        return False

    meta = read_lock_metadata(p)
    if meta is not None:
        if meta.hostname == socket.gethostname() and not is_pid_alive(meta.pid):
            return True
        return _probe_kernel_lock_free(p)

    try:
        st = p.stat()
    except OSError:
        return False

    if st.st_size == 0 and (time.time() - st.st_mtime) > STALE_EMPTY_LOCK_AGE_SECONDS:
        return _probe_kernel_lock_free(p)

    return False


class StudioLock:
    """High-reliability cross-platform file lock adhering to 5-level total global order."""

    def __init__(
        self,
        path: Path | str,
        level: LockLevel | int | None = None,
        session_id: str | None = None,
        resource_id: str | None = None,
        timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
        is_foreign: bool = False,
        remove_on_release: bool = False,
    ) -> None:
        self.path = Path(path).resolve()
        self.timeout = float(timeout)
        self.is_foreign = bool(is_foreign)
        self.remove_on_release = bool(remove_on_release)

        if level is not None:
            self.level = LockLevel(level)
        else:
            self.level = infer_lock_level(self.path)

        # Session ID resolution
        if session_id is not None:
            self.session_id: str | None = session_id
        elif self.level == LockLevel.L5 and self.path.name == ".session.lock":
            self.session_id = self.path.parent.name
        elif (
            self.level == LockLevel.L5
            and self.path.name.startswith(".lifecycle_")
            and self.path.name.endswith(".lock")
        ):
            self.session_id = self.path.name[len(".lifecycle_") : -len(".lock")] or None
        elif self.level in (LockLevel.L2, LockLevel.L3) and self.path.parent.name == "jobs":
            self.session_id = self.path.parent.parent.name
        else:
            self.session_id = None

        # Resource ID resolution (e.g. job_id or deterministic L5 ordering)
        if resource_id is not None:
            self.resource_id: str | None = resource_id
        elif self.level == LockLevel.L2 and self.path.name.startswith(".job_"):
            self.resource_id = self.path.name[len(".job_") : -len(".lock")]
        elif self.level == LockLevel.L3 and self.path.name.startswith(".journal_"):
            self.resource_id = self.path.name[len(".journal_") : -len(".lock")]
        elif (
            self.level == LockLevel.L5
            and self.path.name.startswith(".lifecycle_")
            and self.path.name.endswith(".lock")
        ):
            self.resource_id = "0_lifecycle"
        elif self.level == LockLevel.L5 and self.path.name == ".session.lock":
            self.resource_id = "1_session"
        else:
            self.resource_id = None

        self._file: BinaryIO | None = None
        self._is_locked: bool = False
        self._metadata: LockMetadata | None = None

    @property
    def is_locked(self) -> bool:
        """Return True if this lock instance actively holds the file lock."""
        return self._is_locked

    @property
    def metadata(self) -> LockMetadata | None:
        """Return diagnostic metadata recorded when the lock was acquired."""
        return self._metadata

    @property
    def order_key(self) -> LockOrderKey:
        """Total ordering key for deadlock-free monotonic acquisition.

        Key components:
        1. level: LockLevel integer (1..5)
        2. session_id: Lowercase session_id (min(s1, s2) -> max(s1, s2))
        3. resource_id: Lowercase resource_id (e.g. job_id)
        4. path: Normalized case-folded filesystem path tie-breaker
        """
        session_str = (self.session_id or "").strip().lower()
        resource_str = (self.resource_id or "").strip().lower()
        norm_path = os.path.normcase(str(self.path))
        return (int(self.level), session_str, resource_str, norm_path)

    @staticmethod
    def _unlock_and_close_raw_handle(f: BinaryIO) -> None:
        """Release kernel lock and close file handle when aborting partial acquisition."""
        if not f.closed:
            try:
                if sys.platform == "win32":
                    f.seek(_WIN32_LOCK_OFFSET)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, _WIN32_LOCK_LENGTH)
                else:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            finally:
                with contextlib.suppress(OSError):
                    f.close()

    def _acquire_lowlevel_lock(self) -> BinaryIO:
        """Open lock file and acquire low-level OS kernel file lock."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        path_existed = self.path.exists()

        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
        fd = os.open(str(self.path), flags, 0o600)
        f = os.fdopen(fd, "r+b")
        locked = False
        try:
            if sys.platform == "win32":
                f.seek(_WIN32_LOCK_OFFSET)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, _WIN32_LOCK_LENGTH)
                locked = True
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                f_st = os.fstat(f.fileno())
                try:
                    p_st = os.stat(self.path)
                except FileNotFoundError as exc:
                    raise BlockingIOError(
                        errno.EAGAIN, "Lock file unlinked or replaced during acquire"
                    ) from exc
                if (f_st.st_dev, f_st.st_ino) != (p_st.st_dev, p_st.st_ino):
                    raise BlockingIOError(
                        errno.EAGAIN, "Lock file unlinked or replaced during acquire"
                    )

            if not path_existed:
                with contextlib.suppress(Exception):
                    set_owner_only_permissions(self.path)

            return f
        except BaseException:
            if locked:
                self._unlock_and_close_raw_handle(f)
            else:
                with contextlib.suppress(OSError):
                    f.close()
            raise

    def _write_metadata(self, f: BinaryIO) -> LockMetadata:
        """Write JSON diagnostic metadata at file offset 0."""
        meta = LockMetadata(
            pid=os.getpid(),
            acquired_at=datetime.now(UTC).isoformat(),
            hostname=socket.gethostname(),
            lock_level=int(self.level),
            level_name=self.level.name,
            session_id=self.session_id,
            resource_id=self.resource_id,
        )
        raw = meta.to_json().encode("utf-8")
        f.seek(0)
        f.write(raw)
        f.truncate(len(raw))
        f.flush()
        return meta

    def acquire(self, timeout: float | None = None) -> StudioLock:
        """Acquire lock synchronously, enforcing hierarchy order and retry backoff."""
        if self._is_locked:
            raise LockOrderViolationError(
                f"Lock order violation: Reentrant acquisition of lock '{self.path.name}' "
                f"(level={self.level.name}, session_id={self.session_id}) is not permitted. "
                f"Monotonic total lock order forbids acquiring already-held locks.",
                attempted_key=self.order_key,
                held_keys=[x.order_key for x in get_held_locks()],
            )

        _validate_acquisition_order(self)

        effective_timeout = self.timeout if timeout is None else float(timeout)
        start_time = time.monotonic()
        attempt = 0

        while True:
            try:
                f = self._acquire_lowlevel_lock()
                try:
                    meta = self._write_metadata(f)
                except BaseException:
                    self._unlock_and_close_raw_handle(f)
                    raise

                self._file = f
                self._metadata = meta
                self._is_locked = True

                cur_tid, cur_task_id = _current_execution_owner()
                active_entries = tuple(
                    entry for entry in _held_locks_var.get() if entry[2].is_locked
                )
                _held_locks_var.set((*active_entries, (cur_tid, cur_task_id, self)))
                return self

            except (PermissionError, OSError) as err:
                if not _is_contention_error(err):
                    raise

                elapsed = time.monotonic() - start_time
                remaining = effective_timeout - elapsed
                if remaining <= 0:
                    raise LockTimeoutError(
                        f"LOCK_TIMEOUT: Failed to acquire lock on {self.path} "
                        f"(level={self.level.name}, session_id={self.session_id}) "
                        f"after {effective_timeout:.2f}s.",
                        path=self.path,
                        timeout=effective_timeout,
                    ) from err

                backoff = min(MAX_BACKOFF_SECONDS, MIN_BACKOFF_SECONDS * (1.5**attempt))
                jittered = random.uniform(MIN_BACKOFF_SECONDS, max(MIN_BACKOFF_SECONDS, backoff))
                sleep_time = min(remaining, jittered)
                time.sleep(sleep_time)
                attempt += 1

    async def acquire_async(self, timeout: float | None = None) -> StudioLock:
        """Acquire lock asynchronously, yielding event loop during backoff polling."""
        if self._is_locked:
            raise LockOrderViolationError(
                f"Lock order violation: Reentrant acquisition of lock '{self.path.name}' "
                f"(level={self.level.name}, session_id={self.session_id}) is not permitted. "
                f"Monotonic total lock order forbids acquiring already-held locks.",
                attempted_key=self.order_key,
                held_keys=[x.order_key for x in get_held_locks()],
            )

        _validate_acquisition_order(self)

        effective_timeout = self.timeout if timeout is None else float(timeout)
        start_time = time.monotonic()
        attempt = 0

        while True:
            try:
                f = self._acquire_lowlevel_lock()
                try:
                    meta = self._write_metadata(f)
                except BaseException:
                    self._unlock_and_close_raw_handle(f)
                    raise

                self._file = f
                self._metadata = meta
                self._is_locked = True

                cur_tid, cur_task_id = _current_execution_owner()
                active_entries = tuple(
                    entry for entry in _held_locks_var.get() if entry[2].is_locked
                )
                _held_locks_var.set((*active_entries, (cur_tid, cur_task_id, self)))
                return self

            except (PermissionError, OSError) as err:
                if not _is_contention_error(err):
                    raise

                elapsed = time.monotonic() - start_time
                remaining = effective_timeout - elapsed
                if remaining <= 0:
                    raise LockTimeoutError(
                        f"LOCK_TIMEOUT: Failed to acquire lock on {self.path} "
                        f"(level={self.level.name}, session_id={self.session_id}) "
                        f"after {effective_timeout:.2f}s.",
                        path=self.path,
                        timeout=effective_timeout,
                    ) from err

                backoff = min(MAX_BACKOFF_SECONDS, MIN_BACKOFF_SECONDS * (1.5**attempt))
                jittered = random.uniform(MIN_BACKOFF_SECONDS, max(MIN_BACKOFF_SECONDS, backoff))
                sleep_time = min(remaining, jittered)
                await asyncio.sleep(sleep_time)
                attempt += 1

    def release(self, allow_foreign_early_release: bool = False) -> None:
        """Release lock, validating reverse release order and clearing payload."""
        if not self._is_locked:
            return

        _validate_release_order(self, allow_foreign_early_release=allow_foreign_early_release)

        self._close_file()
        self._is_locked = False
        self._metadata = None

        _held_locks_var.set(
            tuple(
                entry
                for entry in _held_locks_var.get()
                if entry[2] is not self and entry[2].is_locked
            )
        )

        if self.remove_on_release:
            with contextlib.suppress(OSError):
                self.path.unlink(missing_ok=True)

    async def release_async(self, allow_foreign_early_release: bool = False) -> None:
        """Asynchronously release lock."""
        self.release(allow_foreign_early_release=allow_foreign_early_release)

    def _close_file(self) -> None:
        """Cleanly truncate diagnostic metadata, release kernel lock, and close file handle."""
        if self._file is not None and not self._file.closed:
            try:
                self._file.seek(0)
                self._file.truncate(0)
                self._file.flush()
            except OSError:
                pass

            try:
                if sys.platform == "win32":
                    self._file.seek(_WIN32_LOCK_OFFSET)
                    msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, _WIN32_LOCK_LENGTH)
                else:
                    fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            finally:
                self._file.close()
                self._file = None

    def __enter__(self) -> StudioLock:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.release()

    async def __aenter__(self) -> StudioLock:
        return await self.acquire_async()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.release_async()

    def __repr__(self) -> str:
        return (
            f"StudioLock(path='{self.path.name}', level={self.level.name}, "
            f"session_id={self.session_id!r}, locked={self._is_locked})"
        )

    # -------------------------------------------------------------------------
    # Factory Methods for the 5 Hierarchy Levels
    # -------------------------------------------------------------------------

    @classmethod
    def local_token(
        cls,
        studio_root: Path | str,
        timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> StudioLock:
        """Construct L1 (.local_token.lock) lock under studio root."""
        path = Path(studio_root) / ".local_token.lock"
        return cls(path, level=LockLevel.L1, timeout=timeout)

    @classmethod
    def job(
        cls,
        session_dir: Path | str,
        job_id: str,
        timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> StudioLock:
        """Construct L2 (.job_<jid>.lock) job lease lock under session jobs directory."""
        path = Path(session_dir) / "jobs" / f".job_{job_id}.lock"
        return cls(
            path,
            level=LockLevel.L2,
            session_id=Path(session_dir).name,
            resource_id=job_id,
            timeout=timeout,
        )

    @classmethod
    def journal(
        cls,
        session_dir: Path | str,
        job_id: str,
        timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> StudioLock:
        """Construct L3 (.journal_<jid>.lock) journal lock under session jobs directory."""
        path = Path(session_dir) / "jobs" / f".journal_{job_id}.lock"
        return cls(
            path,
            level=LockLevel.L3,
            session_id=Path(session_dir).name,
            resource_id=job_id,
            timeout=timeout,
        )

    @classmethod
    def workspace(
        cls,
        workspace_dir: Path | str,
        resource_id: str | None = None,
        timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> StudioLock:
        """Construct L4 (graph_a.lock) workspace contract lock."""
        p = Path(workspace_dir)
        path = (
            p
            if p.name == "graph_a.lock" or p.name.endswith(".graph_a.lock")
            else p / "graph_a.lock"
        )
        return cls(path, level=LockLevel.L4, resource_id=resource_id, timeout=timeout)

    @classmethod
    def session(
        cls,
        session_dir: Path | str,
        session_id: str | None = None,
        timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
        is_foreign: bool = False,
    ) -> StudioLock:
        """Construct L5 (.session.lock) session state lock."""
        p = Path(session_dir)
        path = p / ".session.lock" if p.name != ".session.lock" else p
        sid = session_id or (p.parent.name if p.name == ".session.lock" else p.name)
        return cls(
            path,
            level=LockLevel.L5,
            session_id=sid,
            timeout=timeout,
            is_foreign=is_foreign,
        )


class MultiStudioLock:
    """Context manager acquiring multiple locks in canonical deadlock-free order."""

    def __init__(
        self,
        locks: Iterable[StudioLock],
        timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self.locks: list[StudioLock] = sorted(locks, key=lambda lk: lk.order_key)
        self.timeout = float(timeout)
        self._acquired: list[StudioLock] = []

    def acquire(self) -> list[StudioLock]:
        """Acquire all locks in canonical sorted order, rolling back on failure."""
        self._acquired.clear()
        try:
            for lock in self.locks:
                lock.acquire(timeout=self.timeout)
                self._acquired.append(lock)
            return list(self._acquired)
        except BaseException:
            for lock in reversed(self._acquired):
                with contextlib.suppress(StudioLockError, OSError):
                    lock.release()
            self._acquired.clear()
            raise

    async def acquire_async(self) -> list[StudioLock]:
        """Asynchronously acquire all locks in canonical sorted order."""
        self._acquired.clear()
        try:
            for lock in self.locks:
                await lock.acquire_async(timeout=self.timeout)
                self._acquired.append(lock)
            return list(self._acquired)
        except BaseException:
            for lock in reversed(self._acquired):
                with contextlib.suppress(StudioLockError, OSError):
                    await lock.release_async()
            self._acquired.clear()
            raise

    def release(self) -> None:
        """Release all acquired locks in reverse acquisition order."""
        errors: list[Exception] = []
        for lock in reversed(self._acquired):
            if lock.is_locked:
                try:
                    lock.release()
                except (StudioLockError, OSError) as err:
                    errors.append(err)
        self._acquired.clear()
        if errors:
            raise errors[0]

    async def release_async(self) -> None:
        """Asynchronously release all acquired locks in reverse order."""
        errors: list[Exception] = []
        for lock in reversed(self._acquired):
            if lock.is_locked:
                try:
                    await lock.release_async()
                except (StudioLockError, OSError) as err:
                    errors.append(err)
        self._acquired.clear()
        if errors:
            raise errors[0]

    def __enter__(self) -> list[StudioLock]:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.release()

    async def __aenter__(self) -> list[StudioLock]:
        return await self.acquire_async()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.release_async()


def studio_lock(
    target: Path | str | StudioLock,
    level: LockLevel | int | None = None,
    session_id: str | None = None,
    timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    is_foreign: bool = False,
    remove_on_release: bool = False,
) -> StudioLock:
    """Create or wrap a StudioLock for sync/async context management."""
    if isinstance(target, StudioLock):
        return target
    return StudioLock(
        path=target,
        level=level,
        session_id=session_id,
        timeout=timeout,
        is_foreign=is_foreign,
        remove_on_release=remove_on_release,
    )


def multi_studio_lock(
    locks: Iterable[StudioLock],
    timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> MultiStudioLock:
    """Create a MultiStudioLock context manager for deadlock-free multi-lock management."""
    return MultiStudioLock(locks=locks, timeout=timeout)


@contextlib.contextmanager
def reentrant_session_lock(
    lock_path: Path | str,
    session_id: str | None = None,
    timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> Iterator[StudioLock]:
    """Acquire L5 `.lifecycle_<sid>.lock` and `.session.lock` in order, or reuse if held.

    Never creates parent directories if the session directory does not exist.
    """
    p = Path(lock_path)
    resolved = (p if p.name == ".session.lock" else p / ".session.lock").resolve()

    target_norm = os.path.normcase(str(resolved))
    held_locks = get_held_locks()
    for held in held_locks:
        if held.level == LockLevel.L5 and os.path.normcase(str(held.path)) == target_norm:
            yield held
            return

    if not resolved.parent.parent.exists():
        raise FileNotFoundError(f"Session directory '{resolved.parent.name}' does not exist")

    sid = session_id or resolved.parent.name
    lifecycle_path = (resolved.parent.parent / f".lifecycle_{sid}.lock").resolve()
    lifecycle_norm = os.path.normcase(str(lifecycle_path))
    lifecycle_already_held = any(
        held.level == LockLevel.L5 and os.path.normcase(str(held.path)) == lifecycle_norm
        for held in held_locks
    )

    if not lifecycle_already_held:
        with studio_lock(
            lifecycle_path,
            level=L5_SESSION,
            session_id=sid,
            timeout=timeout,
            remove_on_release=False,
        ):
            if not resolved.parent.exists():
                raise FileNotFoundError(
                    f"Session directory '{resolved.parent.name}' does not exist"
                )
            with studio_lock(
                resolved,
                level=L5_SESSION,
                session_id=sid,
                timeout=timeout,
            ) as lk:
                yield lk
    else:
        if not resolved.parent.exists():
            raise FileNotFoundError(f"Session directory '{resolved.parent.name}' does not exist")
        with studio_lock(
            resolved,
            level=L5_SESSION,
            session_id=sid,
            timeout=timeout,
        ) as lk:
            yield lk
