"""Job Supervisor managing asynchronous Studio tasks and W3C SSE streaming (Task 5B.2 / Issue #216).

Conforms to docs/19 §6.3, docs/20 §3.1, docs/21 Task 5B.2:
- Manages background threads executing parse, clarify, and construct jobs via `StudioJobRunners`.
- Holds exclusive L2 job execution leases (.job_<job_id>.lock).
- Enforces strict lock hierarchy order: buffers telemetry during L5 mutations,
  and flushes to SSE journal under L3 ONLY after releasing L5 session lock.
- Supports W3C Server-Sent Events with monotonic Last-Event-ID cursor replay
  and 15s keepalive comments (: keepalive\n\n).
- Cooperative job cancellation with locked terminal-state transitions (`MAJOR-04`).
- Startup and lazy crash recovery detecting abandoned jobs via non-blocking L2 lease checks.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import threading
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_service.studio.atomic_commit import SourceNotFoundError
from opendv_service.studio.job_runners import (
    run_clarify_session,
    run_construct_session,
    run_parse_session_sources,
)
from opendv_service.studio.locks import (
    L2_JOB,
    LockTimeoutError,
    studio_lock,
)
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.sse_journal import (
    SSEJournalWriter,
    stream_sse_events,
)
from opendv_service.studio.workspace_security import (
    replace_with_retry,
    set_owner_only_permissions,
)
from opendv_sources.exceptions import (
    OCRUnavailableError,
    OfficeConversionUnavailableError,
    SourceError,
    UnsupportedFormatError,
)

logger = logging.getLogger(__name__)

MAX_IN_MEMORY_JOBS: Final[int] = 256
_TERMINAL_JOB_STATUSES: Final[frozenset[str]] = frozenset({"completed", "failed", "cancelled"})
_VALID_ERROR_CODES: Final[frozenset[str]] = frozenset(e.value for e in ErrorCode)


class JobCancelledError(Exception):
    """Raised when cooperative cancellation wins before entering an irreversible phase."""


@dataclass
class JobContext:
    """In-memory tracking record for a running or recent background job."""

    job_id: str
    session_id: str
    job_type: str
    status: str
    stage: str
    pct_complete: int
    created_at: str
    finished_at: str | None = None
    principal: str = "human:local_admin"
    role: str = "admin"
    cancel_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    writer: SSEJournalWriter | None = None
    error: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    thread: threading.Thread | None = None
    terminal_event_emitted: bool = False
    commit_started: bool = False

    def is_cancelled(self, session_mgr: StudioSessionManager | None = None) -> bool:
        """Check if job has been cancelled either in-memory or via on-disk marker (MAJ-03)."""
        if self.cancel_event.is_set():
            return True
        if session_mgr is not None:
            with contextlib.suppress(Exception):
                sdir = session_mgr.get_session_dir(self.session_id)
                cancel_marker = sdir / "jobs" / f".{self.job_id}.cancel"
                if cancel_marker.is_file():
                    self.cancel_event.set()
                    return True
        return False


_JOB_ID_REGEX: Final[re.Pattern[str]] = re.compile(r"^job-[a-zA-Z0-9_-]{4,64}$")


def _validate_job_id(job_id: str) -> None:
    if not isinstance(job_id, str) or not _JOB_ID_REGEX.match(job_id):
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid job identifier '{job_id}'.",
        )


def _translate_runner_error(exc: Exception) -> StudioAPIError:
    """Translate service/source exceptions into canonical StudioAPIError instances."""
    if isinstance(exc, StudioAPIError):
        return exc
    if isinstance(exc, OCRUnavailableError):
        return StudioAPIError("OCR_UNAVAILABLE", str(exc))
    if isinstance(exc, OfficeConversionUnavailableError):
        return StudioAPIError("OFFICE_CONVERSION_UNAVAILABLE", str(exc))
    if isinstance(exc, UnsupportedFormatError):
        return StudioAPIError("UNSUPPORTED_FORMAT", str(exc))
    if isinstance(exc, SourceNotFoundError):
        return StudioAPIError("SOURCE_NOT_FOUND", str(exc))
    if isinstance(exc, OpenDVError):
        raw_code = getattr(exc, "error_code", None)
        code_str = (
            raw_code.value
            if hasattr(raw_code, "value")
            else (str(raw_code) if raw_code else "EXECUTION_FAILURE")
        )
        if code_str not in _VALID_ERROR_CODES:
            code_str = "EXECUTION_FAILURE"
        return StudioAPIError(
            code_str,
            str(getattr(exc, "message", None) or str(exc)),
            status_code=getattr(exc, "http_status", None),
        )
    if isinstance(exc, SourceError):
        raw_src_code = getattr(exc, "error_code", None)
        code_str = (
            raw_src_code.value
            if hasattr(raw_src_code, "value")
            else (str(raw_src_code) if raw_src_code else "SOURCE_LOAD_ERROR")
        )
        if code_str not in _VALID_ERROR_CODES:
            code_str = "SOURCE_LOAD_ERROR"
        return StudioAPIError(code_str, str(exc))
    return StudioAPIError("EXECUTION_FAILURE", str(exc))


class JobSupervisor:
    """Central supervisor coordinating background tasks across studio sessions."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobContext] = {}
        self._lock = threading.Lock()

    def _evict_terminal_jobs_locked(self) -> None:
        """Evict oldest terminal JobContext entries when cache exceeds MAX_IN_MEMORY_JOBS."""
        if len(self._jobs) <= MAX_IN_MEMORY_JOBS:
            return
        terminal_keys = [
            jid for jid, jctx in self._jobs.items() if jctx.status.lower() in _TERMINAL_JOB_STATUSES
        ]
        overflow = len(self._jobs) - MAX_IN_MEMORY_JOBS
        for jid in terminal_keys[: max(0, overflow)]:
            self._jobs.pop(jid, None)

    def purge_session_jobs(self, session_id: str) -> None:
        """Remove all cached job records belonging to a deleted session."""
        with self._lock:
            to_remove = [jid for jid, jctx in self._jobs.items() if jctx.session_id == session_id]
            for jid in to_remove:
                self._jobs.pop(jid, None)

    def resolve_job_session_id(self, session_mgr: StudioSessionManager, job_id: str) -> str:
        """Resolve `session_id` for `job_id`, checking in-memory `self._jobs` before disk."""
        _validate_job_id(job_id)
        with self._lock:
            ctx = self._jobs.get(job_id)
            in_mem_sid = ctx.session_id if ctx is not None else None

        if in_mem_sid is not None:
            sdir = session_mgr.get_session_dir(in_mem_sid)
            if sdir.is_dir() and (sdir / "session.json").is_file():
                return in_mem_sid

        for m in session_mgr.list_sessions():
            sdir = session_mgr.get_session_dir(m.session_id)
            jdir = sdir / "jobs"
            if (jdir / f"{job_id}.json").is_file() or (jdir / f"{job_id}.events.jsonl").is_file():
                return m.session_id

        raise StudioAPIError("JOB_NOT_FOUND", f"Job '{job_id}' not found.")

    def count_active_jobs(self, session_mgr: StudioSessionManager, session_id: str) -> int:
        """Count active (pending/running) jobs for `session_id` across memory and disk."""
        active_ids: set[str] = set()
        seen_in_memory: set[str] = set()
        with self._lock:
            for jid, jctx in self._jobs.items():
                if jctx.session_id == session_id:
                    seen_in_memory.add(jid)
                    if jctx.status.lower() in ("pending", "running"):
                        active_ids.add(jid)

        sdir = session_mgr.get_session_dir(session_id)
        jdir = sdir / "jobs"
        if jdir.is_dir():
            for jfile in jdir.glob("job-*.json"):
                jid = jfile.stem
                if jid in seen_in_memory or jid in active_ids:
                    continue
                with contextlib.suppress(OSError, json.JSONDecodeError):
                    data = json.loads(jfile.read_text(encoding="utf-8"))
                    if str(data.get("status", "")).lower() in ("pending", "running"):
                        lock_file = jdir / f".job_{jid}.lock"
                        try:
                            with studio_lock(
                                lock_file, level=L2_JOB, session_id=session_id, timeout=0.01
                            ):
                                pass
                        except LockTimeoutError:
                            active_ids.add(jid)

        return len(active_ids)

    def recover_abandoned_jobs(self, studio_root: Path) -> int:
        """Transition unclosed jobs from prior crashed server runs to failed (`MAJOR-04`)."""
        if not studio_root.is_dir():
            return 0
        recovered = 0
        for sdir in sorted(studio_root.iterdir()):
            if not sdir.is_dir() or not (sdir / "session.json").is_file():
                continue
            jdir = sdir / "jobs"
            if not jdir.is_dir():
                continue
            session_id = sdir.name
            for jfile in sorted(jdir.glob("job-*.json")):
                job_id = jfile.stem
                with self._lock:
                    if job_id in self._jobs:
                        continue
                try:
                    data = json.loads(jfile.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if str(data.get("status", "")).upper() not in ("PENDING", "RUNNING"):
                    continue

                lock_file = jdir / f".job_{job_id}.lock"
                abandoned = False
                try:
                    with studio_lock(lock_file, level=L2_JOB, session_id=session_id, timeout=0.01):
                        abandoned = True
                        now_iso = datetime.now(UTC).isoformat()
                        err_obj = {
                            "code": "JOB_ABANDONED_SERVER_CRASH",
                            "error_code": "JOB_ABANDONED_SERVER_CRASH",
                            "detail": "Background job abandoned due to process restart or crash.",
                        }
                        data["status"] = "failed"
                        data["stage"] = "abandoned"
                        data["finished_at"] = now_iso
                        data["error"] = err_obj
                        tmp_file = jdir / f".{job_id}.json.{uuid.uuid4().hex[:8]}.tmp"
                        tmp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
                        set_owner_only_permissions(tmp_file)
                        replace_with_retry(tmp_file, jfile)
                        set_owner_only_permissions(jfile)

                        with contextlib.suppress(Exception):
                            writer = SSEJournalWriter(
                                session_dir=sdir, job_id=job_id, session_id=session_id
                            )
                            writer.buffer_event(
                                "job_failed",
                                {
                                    "job_id": job_id,
                                    "status": "failed",
                                    "error": err_obj,
                                },
                            )
                            writer.flush()
                        recovered += 1
                except LockTimeoutError:
                    continue

                if abandoned:
                    with contextlib.suppress(OSError):
                        lock_file.unlink(missing_ok=True)

        return recovered

    def _get_jobs_dir(
        self, session_mgr: StudioSessionManager, session_id: str, create: bool = False
    ) -> Path:
        sdir = session_mgr.get_session_dir(session_id)
        if not sdir.is_dir() or not (sdir / "session.json").is_file():
            raise StudioAPIError("SESSION_NOT_FOUND", f"Session '{session_id}' not found.")
        jdir = sdir / "jobs"
        if create:
            jdir.mkdir(parents=True, exist_ok=True)
        return jdir

    def _get_job_file(
        self,
        session_mgr: StudioSessionManager,
        session_id: str,
        job_id: str,
        create: bool = False,
    ) -> Path:
        _validate_job_id(job_id)
        jdir = self._get_jobs_dir(session_mgr, session_id, create=create).resolve()
        job_file = (jdir / f"{job_id}.json").resolve()
        if not job_file.is_relative_to(jdir):
            raise StudioAPIError(
                "SYNTAX_INVALID_ARGUMENT",
                f"Path traversal detected in job_id: '{job_id}'",
            )
        return job_file

    def _write_job_data_atomic(
        self,
        session_mgr: StudioSessionManager,
        session_id: str,
        job_id: str,
        data: dict[str, Any],
    ) -> None:
        jdir = self._get_jobs_dir(session_mgr, session_id, create=True)
        job_file = self._get_job_file(session_mgr, session_id, job_id, create=True)
        tmp_file = jdir / f".{job_id}.json.{uuid.uuid4().hex[:8]}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as fp:
            json.dump(data, fp, indent=2)
            fp.flush()
            os.fsync(fp.fileno())
        set_owner_only_permissions(tmp_file)
        replace_with_retry(tmp_file, job_file)
        set_owner_only_permissions(job_file)

    def _persist_job_status(
        self,
        session_mgr: StudioSessionManager,
        session_id: str,
        ctx: JobContext,
    ) -> None:
        """Write job status metadata to <session_dir>/jobs/<job_id>.json atomically."""
        payload = {
            "job_id": ctx.job_id,
            "session_id": ctx.session_id,
            "job_type": ctx.job_type,
            "status": ctx.status,
            "stage": ctx.stage,
            "pct_complete": ctx.pct_complete,
            "created_at": ctx.created_at,
            "finished_at": ctx.finished_at,
            "heartbeat_at": datetime.now(UTC).isoformat(),
            "principal": ctx.principal,
            "role": ctx.role,
            "commit_started": ctx.commit_started,
            "error": ctx.error,
            "result": ctx.result,
        }
        with contextlib.suppress(StudioAPIError, OSError):
            self._write_job_data_atomic(session_mgr, session_id, ctx.job_id, payload)

    def submit_job(
        self,
        session_mgr: StudioSessionManager,
        session_id: str,
        job_type: str,
        runner_fn: Any,
        principal: str = "human:local_admin",
        role: str = "admin",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create, register, and launch a new asynchronous studio job."""
        session_dir = session_mgr.get_session_dir(session_id)
        if not session_dir.is_dir() or not (session_dir / "session.json").is_file():
            raise StudioAPIError("SESSION_NOT_FOUND", f"Session '{session_id}' not found.")

        job_id = f"job-{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(UTC).isoformat()
        writer = SSEJournalWriter(session_dir=session_dir, job_id=job_id, session_id=session_id)

        ctx = JobContext(
            job_id=job_id,
            session_id=session_id,
            job_type=job_type,
            status="PENDING",
            stage="queued",
            pct_complete=0,
            created_at=now_iso,
            principal=principal,
            role=role,
            writer=writer,
        )

        with self._lock:
            self._jobs[job_id] = ctx
            self._evict_terminal_jobs_locked()

        self._persist_job_status(session_mgr, session_id, ctx)

        def _worker_wrapper() -> None:
            self._execute_job(session_mgr, ctx, runner_fn, **kwargs)

        t = threading.Thread(target=_worker_wrapper, daemon=True, name=f"job-{job_id}")
        ctx.thread = t
        t.start()

        return {
            "job_id": job_id,
            "session_id": session_id,
            "job_type": job_type,
            "status": "pending",
            "created_at": now_iso,
        }

    def _execute_job(
        self,
        session_mgr: StudioSessionManager,
        ctx: JobContext,
        runner_fn: Any,
        **kwargs: Any,
    ) -> None:
        """Run job under L2 job lease with locked terminal state transitions (`MAJOR-04`)."""
        session_dir = session_mgr.get_session_dir(ctx.session_id)
        lock_file = session_dir / "jobs" / f".job_{ctx.job_id}.lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            with studio_lock(lock_file, level=L2_JOB, session_id=ctx.session_id, timeout=10.0):
                already_terminal = False
                emit_cancel_now = False
                with ctx.lock:
                    if (
                        ctx.terminal_event_emitted
                        or ctx.status.lower() in _TERMINAL_JOB_STATUSES
                        or ctx.is_cancelled(session_mgr)
                    ):
                        already_terminal = True
                        if not ctx.terminal_event_emitted:
                            if ctx.status.lower() not in _TERMINAL_JOB_STATUSES:
                                ctx.status = "cancelled"
                                ctx.stage = "cancelled"
                                ctx.finished_at = ctx.finished_at or datetime.now(UTC).isoformat()
                                ctx.terminal_event_emitted = True
                                emit_cancel_now = True
                                self._persist_job_status(session_mgr, ctx.session_id, ctx)
                            elif ctx.status.lower() == "cancelled":
                                ctx.stage = "cancelled"
                                ctx.finished_at = ctx.finished_at or datetime.now(UTC).isoformat()
                                ctx.terminal_event_emitted = True
                                emit_cancel_now = True
                                self._persist_job_status(session_mgr, ctx.session_id, ctx)
                    else:
                        ctx.status = "running"
                        ctx.stage = "started"
                        ctx.pct_complete = 5
                        self._persist_job_status(session_mgr, ctx.session_id, ctx)

                if already_terminal:
                    if emit_cancel_now and ctx.writer:
                        with contextlib.suppress(Exception):
                            ctx.writer.buffer_event(
                                "job_cancelled",
                                {"job_id": ctx.job_id, "status": "cancelled"},
                            )
                            ctx.writer.flush()
                    return

                if ctx.writer:
                    ctx.writer.buffer_event(
                        "job_started",
                        {
                            "job_id": ctx.job_id,
                            "session_id": ctx.session_id,
                            "job_type": ctx.job_type,
                            "timestamp": datetime.now(UTC).isoformat(),
                        },
                    )
                    ctx.writer.flush()

                result = runner_fn(session_mgr, ctx, **kwargs)
                auto_committed = isinstance(result, dict) and result.get("auto_committed") is True
                runner_reported_cancel = (
                    isinstance(result, dict) and result.get("status") == "cancelled"
                )

                event_to_emit: tuple[str, dict[str, Any]] | None = None
                with ctx.lock:
                    if ctx.terminal_event_emitted or ctx.status.lower() in _TERMINAL_JOB_STATUSES:
                        # Terminal state already finalized under ctx.lock (e.g. via cancel_job);
                        # never overwrite ctx.status or clobber terminal state on disk.
                        pass
                    elif auto_committed:
                        ctx.status = "completed"
                        ctx.stage = "completed"
                        ctx.pct_complete = 100
                        ctx.finished_at = datetime.now(UTC).isoformat()
                        ctx.result = result
                        ctx.terminal_event_emitted = True
                        self._persist_job_status(session_mgr, ctx.session_id, ctx)
                        event_to_emit = (
                            "job_completed",
                            {
                                "job_id": ctx.job_id,
                                "status": "completed",
                                "result": ctx.result,
                            },
                        )
                    elif ctx.cancel_event.is_set() or runner_reported_cancel:
                        ctx.status = "cancelled"
                        ctx.stage = "cancelled"
                        ctx.finished_at = ctx.finished_at or datetime.now(UTC).isoformat()
                        ctx.terminal_event_emitted = True
                        self._persist_job_status(session_mgr, ctx.session_id, ctx)
                        event_to_emit = (
                            "job_cancelled",
                            {"job_id": ctx.job_id, "status": "cancelled"},
                        )
                    else:
                        ctx.status = "completed"
                        ctx.stage = "completed"
                        ctx.pct_complete = 100
                        ctx.finished_at = datetime.now(UTC).isoformat()
                        ctx.result = result or {}
                        ctx.terminal_event_emitted = True
                        self._persist_job_status(session_mgr, ctx.session_id, ctx)
                        event_to_emit = (
                            "job_completed",
                            {
                                "job_id": ctx.job_id,
                                "status": "completed",
                                "result": ctx.result,
                            },
                        )

                if event_to_emit is not None and ctx.writer:
                    ctx.writer.buffer_event(event_to_emit[0], event_to_emit[1])
                    ctx.writer.flush()

        except JobCancelledError:
            cancel_event: tuple[str, dict[str, Any]] | None = None
            with ctx.lock:
                if (
                    not ctx.terminal_event_emitted
                    and ctx.status.lower() not in _TERMINAL_JOB_STATUSES
                ):
                    ctx.status = "cancelled"
                    ctx.stage = "cancelled"
                    ctx.finished_at = ctx.finished_at or datetime.now(UTC).isoformat()
                    ctx.terminal_event_emitted = True
                    self._persist_job_status(session_mgr, ctx.session_id, ctx)
                    cancel_event = (
                        "job_cancelled",
                        {"job_id": ctx.job_id, "status": "cancelled"},
                    )
                elif not ctx.terminal_event_emitted and ctx.status.lower() == "cancelled":
                    ctx.stage = "cancelled"
                    ctx.finished_at = ctx.finished_at or datetime.now(UTC).isoformat()
                    ctx.terminal_event_emitted = True
                    self._persist_job_status(session_mgr, ctx.session_id, ctx)
                    cancel_event = (
                        "job_cancelled",
                        {"job_id": ctx.job_id, "status": "cancelled"},
                    )
            if cancel_event is not None and ctx.writer:
                with contextlib.suppress(Exception):
                    ctx.writer.buffer_event(cancel_event[0], cancel_event[1])
                    ctx.writer.flush()

        except Exception as exc:
            mapped = _translate_runner_error(exc)
            fail_event: tuple[str, dict[str, Any]] | None = None
            with ctx.lock:
                if ctx.terminal_event_emitted or ctx.status.lower() in _TERMINAL_JOB_STATUSES:
                    pass
                elif ctx.cancel_event.is_set():
                    ctx.status = "cancelled"
                    ctx.stage = "cancelled"
                    ctx.finished_at = ctx.finished_at or datetime.now(UTC).isoformat()
                    ctx.terminal_event_emitted = True
                    self._persist_job_status(session_mgr, ctx.session_id, ctx)
                    fail_event = (
                        "job_cancelled",
                        {"job_id": ctx.job_id, "status": "cancelled"},
                    )
                else:
                    logger.exception("Job %s execution failed", ctx.job_id)
                    ctx.status = "failed"
                    ctx.stage = "failed"
                    ctx.finished_at = datetime.now(UTC).isoformat()
                    ctx.error = {
                        "code": mapped.code,
                        "error_code": mapped.code,
                        "detail": mapped.detail,
                    }
                    ctx.terminal_event_emitted = True
                    self._persist_job_status(session_mgr, ctx.session_id, ctx)
                    fail_event = (
                        "job_failed",
                        {
                            "job_id": ctx.job_id,
                            "status": "failed",
                            "error": ctx.error,
                        },
                    )

            if fail_event is not None and ctx.writer:
                with contextlib.suppress(Exception):
                    ctx.writer.buffer_event(fail_event[0], fail_event[1])
                    ctx.writer.flush()

        finally:
            with self._lock:
                self._evict_terminal_jobs_locked()

    def get_job(
        self,
        session_mgr: StudioSessionManager,
        session_id: str,
        job_id: str,
    ) -> dict[str, Any]:
        """Fetch job status, performing crash detection if process is missing."""
        with self._lock:
            ctx = self._jobs.get(job_id)

        if ctx and ctx.session_id == session_id:
            with ctx.lock:
                return {
                    "job_id": ctx.job_id,
                    "session_id": ctx.session_id,
                    "job_type": ctx.job_type,
                    "status": ctx.status.lower(),
                    "stage": ctx.stage,
                    "pct_complete": ctx.pct_complete,
                    "created_at": ctx.created_at,
                    "finished_at": ctx.finished_at,
                    "error": ctx.error,
                    "result": ctx.result,
                }

        session_dir = session_mgr.get_session_dir(session_id)
        job_file = self._get_job_file(session_mgr, session_id, job_id, create=False)
        if not job_file.is_file():
            raise StudioAPIError("JOB_NOT_FOUND", f"Job '{job_id}' not found.")

        try:
            data = json.loads(job_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD", f"Job record '{job_id}' is corrupt."
            ) from exc

        status = str(data.get("status", "")).upper()
        if status in ("PENDING", "RUNNING"):
            lock_file = session_dir / "jobs" / f".job_{job_id}.lock"
            abandoned = False
            try:
                with studio_lock(lock_file, level=L2_JOB, session_id=session_id, timeout=0.01):
                    abandoned = True
                    err_obj = {
                        "code": "JOB_ABANDONED_SERVER_CRASH",
                        "error_code": "JOB_ABANDONED_SERVER_CRASH",
                        "detail": "Background job abandoned due to process restart or crash.",
                    }
                    data["status"] = "failed"
                    data["stage"] = "abandoned"
                    data["finished_at"] = datetime.now(UTC).isoformat()
                    data["error"] = err_obj
                    self._write_job_data_atomic(session_mgr, session_id, job_id, data)
                    with contextlib.suppress(Exception):
                        writer = SSEJournalWriter(
                            session_dir=session_dir, job_id=job_id, session_id=session_id
                        )
                        writer.buffer_event(
                            "job_failed",
                            {"job_id": job_id, "status": "failed", "error": err_obj},
                        )
                        writer.flush()
            except LockTimeoutError:
                pass

            if abandoned:
                with contextlib.suppress(OSError):
                    lock_file.unlink(missing_ok=True)

        if "status" in data and isinstance(data["status"], str):
            data["status"] = data["status"].lower()

        return data

    def cancel_job(
        self,
        session_mgr: StudioSessionManager,
        session_id: str,
        job_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an in-flight background job cleanly under `ctx.lock`."""
        _validate_job_id(job_id)
        session_dir = session_mgr.get_session_dir(session_id)
        if not session_dir.is_dir() or not (session_dir / "session.json").is_file():
            raise StudioAPIError("SESSION_NOT_FOUND", f"Session '{session_id}' not found.")

        with self._lock:
            ctx = self._jobs.get(job_id)

        if ctx and ctx.session_id == session_id:
            with ctx.lock:
                if ctx.status.lower() in _TERMINAL_JOB_STATUSES:
                    raise StudioAPIError(
                        "JOB_ALREADY_TERMINAL",
                        f"Job '{job_id}' is already terminal ({ctx.status}).",
                    )
                if ctx.stage == "committing" or ctx.commit_started:
                    raise StudioAPIError(
                        "JOB_COMMIT_IN_PROGRESS",
                        f"Job '{job_id}' is actively committing delta and cannot be cancelled.",
                    )
                ctx.cancel_event.set()
                with contextlib.suppress(Exception):
                    cancel_marker = session_dir / "jobs" / f".{job_id}.cancel"
                    cancel_marker.parent.mkdir(parents=True, exist_ok=True)
                    cancel_marker.write_text("cancelled", encoding="utf-8")
                    set_owner_only_permissions(cancel_marker)
                ctx.status = "cancelled"
                ctx.stage = "cancelled"
                ctx.finished_at = datetime.now(UTC).isoformat()
                emit_now = not ctx.terminal_event_emitted
                ctx.terminal_event_emitted = True
                self._persist_job_status(session_mgr, session_id, ctx)

            if emit_now and ctx.writer:
                with contextlib.suppress(Exception):
                    ctx.writer.buffer_event(
                        "job_cancelled",
                        {
                            "job_id": job_id,
                            "status": "cancelled",
                            "reason": reason or "Cancelled by user",
                        },
                    )
                    ctx.writer.flush()

            return self.get_job(session_mgr, session_id, job_id)

        # On-disk cancellation check (cross-process or post-eviction) serialized via L2_JOB
        job_file = self._get_job_file(session_mgr, session_id, job_id, create=False)
        if not job_file.is_file():
            raise StudioAPIError("JOB_NOT_FOUND", f"Job '{job_id}' not found.")

        try:
            pre_data = json.loads(job_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD", f"Job record '{job_id}' is corrupt."
            ) from exc

        if str(pre_data.get("status", "")).lower() in _TERMINAL_JOB_STATUSES:
            raise StudioAPIError(
                "JOB_ALREADY_TERMINAL",
                f"Job '{job_id}' is already terminal ({pre_data.get('status')}).",
            )
        if pre_data.get("stage") == "committing" or pre_data.get("commit_started") is True:
            raise StudioAPIError(
                "JOB_COMMIT_IN_PROGRESS",
                f"Job '{job_id}' is actively committing delta and cannot be cancelled.",
            )

        lock_file = session_dir / "jobs" / f".job_{job_id}.lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with studio_lock(lock_file, level=L2_JOB, session_id=session_id, timeout=0.01):
                try:
                    data = json.loads(job_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise StudioAPIError(
                        "CORRUPT_SESSION_RECORD", f"Job record '{job_id}' is corrupt."
                    ) from exc

                if str(data.get("status", "")).lower() in _TERMINAL_JOB_STATUSES:
                    raise StudioAPIError(
                        "JOB_ALREADY_TERMINAL",
                        f"Job '{job_id}' is already terminal ({data.get('status')}).",
                    )
                if data.get("stage") == "committing" or data.get("commit_started") is True:
                    raise StudioAPIError(
                        "JOB_COMMIT_IN_PROGRESS",
                        f"Job '{job_id}' is actively committing delta and cannot be cancelled.",
                    )

                cancel_marker = session_dir / "jobs" / f".{job_id}.cancel"
                with contextlib.suppress(Exception):
                    cancel_marker.parent.mkdir(parents=True, exist_ok=True)
                    cancel_marker.write_text("cancelled", encoding="utf-8")
                    set_owner_only_permissions(cancel_marker)

                data["status"] = "cancelled"
                data["stage"] = "cancelled"
                data["finished_at"] = datetime.now(UTC).isoformat()
                self._write_job_data_atomic(session_mgr, session_id, job_id, data)

                with contextlib.suppress(Exception):
                    writer = SSEJournalWriter(
                        session_dir=session_dir, job_id=job_id, session_id=session_id
                    )
                    writer.buffer_event(
                        "job_cancelled",
                        {
                            "job_id": job_id,
                            "status": "cancelled",
                            "reason": reason or "Cancelled by user",
                        },
                    )
                    writer.flush()
        except LockTimeoutError as exc:
            raise StudioAPIError(
                "JOB_COMMIT_IN_PROGRESS",
                f"Job '{job_id}' is held by an active worker and cannot be cancelled on disk.",
            ) from exc

        with contextlib.suppress(OSError):
            lock_file.unlink(missing_ok=True)
        return data

    def stream_job_events(
        self,
        session_mgr: StudioSessionManager,
        session_id: str,
        job_id: str,
        last_event_id: int | str | None = None,
    ) -> AsyncIterator[str]:
        """Validate session/job synchronously, then return an async SSE wire generator."""
        _validate_job_id(job_id)
        session_dir = session_mgr.get_session_dir(session_id)
        if not session_dir.is_dir() or not (session_dir / "session.json").is_file():
            raise StudioAPIError("SESSION_NOT_FOUND", f"Session '{session_id}' not found.")
        job_file = self._get_job_file(session_mgr, session_id, job_id, create=False)
        journal_file = (session_dir / "jobs" / f"{job_id}.events.jsonl").resolve()
        if not journal_file.is_relative_to((session_dir / "jobs").resolve()):
            raise StudioAPIError(
                "SYNTAX_INVALID_ARGUMENT",
                f"Path traversal detected in job_id: '{job_id}'",
            )

        with self._lock:
            ctx = self._jobs.get(job_id)

        has_in_mem = ctx is not None and ctx.session_id == session_id
        if not has_in_mem and not job_file.is_file() and not journal_file.is_file():
            raise StudioAPIError("JOB_NOT_FOUND", f"Job '{job_id}' not found.")

        eff_ctx = ctx if has_in_mem else None
        writer = eff_ctx.writer if eff_ctx else None

        def _is_active() -> bool:
            if eff_ctx:
                with eff_ctx.lock:
                    return str(eff_ctx.status).upper() in ("PENDING", "RUNNING")
            if job_file.is_file():
                with contextlib.suppress(Exception):
                    rec = json.loads(job_file.read_text(encoding="utf-8"))
                    return str(rec.get("status", "")).upper() in ("PENDING", "RUNNING")
            return False

        async def _event_generator() -> AsyncIterator[str]:
            async for chunk in stream_sse_events(
                journal_file=journal_file,
                last_event_id=last_event_id,
                writer=writer,
                is_active=_is_active,
                poll_interval=0.25,
                keepalive_interval=15.0,
            ):
                yield chunk

        return _event_generator()


# ---------------------------------------------------------------------------
# Domain Job Runner Functions (Delegating to opendv_service.studio.job_runners)
# ---------------------------------------------------------------------------


def run_parse_job(
    session_mgr: StudioSessionManager,
    ctx: JobContext,
    source_ids: list[str] | None = None,
    ocr_mode: str = "auto",
    actor_role: str | None = None,
) -> dict[str, Any]:
    """Execute document parsing and layout extraction via `StudioJobRunners`."""

    def _on_progress(stage: str, pct: int) -> None:
        with ctx.lock:
            if ctx.terminal_event_emitted or ctx.status.lower() in _TERMINAL_JOB_STATUSES:
                return
            ctx.stage = stage
            ctx.pct_complete = pct
            get_job_supervisor()._persist_job_status(session_mgr, ctx.session_id, ctx)

    try:
        return run_parse_session_sources(
            session_manager=session_mgr,
            session_id=ctx.session_id,
            job_id=ctx.job_id,
            source_ids=source_ids,
            ocr_mode=ocr_mode,
            actor_role=actor_role or ctx.role,
            writer=ctx.writer,
            is_cancelled=lambda: ctx.is_cancelled(session_mgr),
            on_progress=_on_progress,
        )
    except Exception as exc:
        raise _translate_runner_error(exc) from exc


def run_clarify_job(
    session_mgr: StudioSessionManager,
    ctx: JobContext,
    mode: str = "auto",
    max_questions: int = 5,
    focus_concepts: Sequence[str | dict[str, Any]] | None = None,
    candidate_terms: Sequence[str | dict[str, Any]] | None = None,
    actor_role: str | None = None,
) -> dict[str, Any]:
    """Execute polysemy scan and clarification question generation via `StudioJobRunners`."""
    terms = candidate_terms if candidate_terms is not None else focus_concepts
    try:
        return run_clarify_session(
            session_manager=session_mgr,
            session_id=ctx.session_id,
            job_id=ctx.job_id,
            mode=mode,
            max_questions=max_questions,
            candidate_terms=terms,
            actor_role=actor_role or ctx.role,
            writer=ctx.writer,
            is_cancelled=lambda: ctx.is_cancelled(session_mgr),
        )
    except Exception as exc:
        raise _translate_runner_error(exc) from exc


def run_construct_job(
    session_mgr: StudioSessionManager,
    ctx: JobContext,
    base_version: str | None = None,
    base_sha256: str | None = None,
    auto_commit: bool = False,
    fibo_modules: list[str] | None = None,
    mode: str = "deterministic",
    if_match: str | None = None,
    actor_role: str | None = None,
    agent_runner: Any = None,
) -> dict[str, Any]:
    """Execute ontology construction pipeline via `StudioJobRunners`."""
    eff_mode = "deterministic" if (mode or "").strip().lower() == "challenger" else mode

    def _on_stage(stage: str) -> None:
        with ctx.lock:
            if stage == "committing":
                if not ctx.commit_started and (
                    ctx.terminal_event_emitted
                    or ctx.status.lower() in _TERMINAL_JOB_STATUSES
                    or ctx.is_cancelled(session_mgr)
                ):
                    raise JobCancelledError("Job cancelled prior to commit phase")
                ctx.commit_started = True
                if not ctx.terminal_event_emitted and ctx.status.lower() not in (
                    _TERMINAL_JOB_STATUSES
                ):
                    ctx.stage = "committing"
                    ctx.pct_complete = max(ctx.pct_complete, 90)
                    get_job_supervisor()._persist_job_status(session_mgr, ctx.session_id, ctx)
            else:
                if ctx.terminal_event_emitted or ctx.status.lower() in _TERMINAL_JOB_STATUSES:
                    return
                ctx.stage = stage
                get_job_supervisor()._persist_job_status(session_mgr, ctx.session_id, ctx)

    try:
        return run_construct_session(
            session_manager=session_mgr,
            session_id=ctx.session_id,
            base_version=base_version,
            base_sha256=base_sha256,
            job_id=ctx.job_id,
            auto_commit=auto_commit,
            fibo_modules=fibo_modules,
            mode=eff_mode,
            if_match=if_match,
            actor=ctx.principal,
            actor_role=actor_role or ctx.role,
            require_admin_for_auto_commit=False,
            writer=ctx.writer,
            is_cancelled=lambda: ctx.is_cancelled(session_mgr),
            on_stage=_on_stage,
            agent_runner=agent_runner,
        )
    except JobCancelledError:
        raise
    except Exception as exc:
        raise _translate_runner_error(exc) from exc


_GLOBAL_SUPERVISOR: JobSupervisor | None = None


def get_job_supervisor() -> JobSupervisor:
    """Return shared JobSupervisor singleton instance."""
    global _GLOBAL_SUPERVISOR
    if _GLOBAL_SUPERVISOR is None:
        _GLOBAL_SUPERVISOR = JobSupervisor()
    return _GLOBAL_SUPERVISOR


__all__ = [
    "MAX_IN_MEMORY_JOBS",
    "JobCancelledError",
    "JobContext",
    "JobSupervisor",
    "get_job_supervisor",
    "run_clarify_job",
    "run_construct_job",
    "run_parse_job",
]
