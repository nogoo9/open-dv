"""Job Supervisor managing asynchronous Studio tasks and W3C SSE streaming (Task 5B.2 / Issue #216).

Conforms to docs/19 §6.3, docs/20 §3.1, docs/21 Task 5B.2:
- Manages background threads executing parse, clarify, and construct jobs.
- Holds exclusive L2 job execution leases (.job_<job_id>.lock).
- Enforces strict lock hierarchy order: buffers telemetry during L5 mutations,
  and flushes to SSE journal under L3 ONLY after releasing L5 session lock.
- Supports W3C Server-Sent Events with monotonic Last-Event-ID cursor replay
  and 15s keepalive comments (: keepalive\n\n).
- Cooperative job cancellation releasing L2 leases.
- Crash recovery detecting abandoned jobs via non-blocking L2 lease checks.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import threading
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from opendv_contracts.dtos.ingestion import EvidenceChunk, TableStructure
from opendv_contracts.dtos.ontology import AddConceptOp, AddExternalMappingOp
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_service.construction.clarification_harness import ClarificationHarness
from opendv_service.construction.deterministic_pipeline import (
    DeterministicConstructionPipeline,
)
from opendv_service.construction.models import ConstructOptions
from opendv_service.studio.atomic_commit import AtomicCommitService
from opendv_service.studio.locks import (
    L2_JOB,
    L5_SESSION,
    LockTimeoutError,
    studio_lock,
)
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.sse_journal import (
    SSEJournalWriter,
    stream_sse_events,
)
from opendv_service.studio.workspace_security import set_owner_only_permissions
from opendv_sources.adapters.multiformat_adapter import (
    DelimitedTabularAdapter,
    MarkdownDocumentAdapter,
    MarkupDocumentAdapter,
    PyArrowTabularAdapter,
    StructuredDataDocumentAdapter,
)
from opendv_sources.ingestion import PlainTextDocumentAdapter
from opendv_sources.ingestion.service import IngestionService

logger = logging.getLogger(__name__)


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
    cancel_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    writer: SSEJournalWriter | None = None
    error: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    thread: threading.Thread | None = None

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
            status_code=400,
        )


class JobSupervisor:
    """Central supervisor coordinating background tasks across studio sessions."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobContext] = {}
        self._lock = threading.Lock()

    def _get_jobs_dir(
        self, session_mgr: StudioSessionManager, session_id: str, create: bool = False
    ) -> Path:
        sdir = session_mgr.get_session_dir(session_id)
        if not sdir.is_dir() or not (sdir / "session.json").is_file():
            raise StudioAPIError(
                "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
            )
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
        if not str(job_file).startswith(str(jdir)):
            raise StudioAPIError(
                "SYNTAX_INVALID_ARGUMENT",
                f"Path traversal detected in job_id: '{job_id}'",
                status_code=400,
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
        os.replace(tmp_file, job_file)
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
            "error": ctx.error,
            "result": ctx.result,
        }
        self._write_job_data_atomic(session_mgr, session_id, ctx.job_id, payload)

    def submit_job(
        self,
        session_mgr: StudioSessionManager,
        session_id: str,
        job_type: str,
        runner_fn: Any,
        principal: str = "human:local_admin",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create, register, and launch a new asynchronous studio job."""
        session_dir = session_mgr.get_session_dir(session_id)
        if not session_dir.is_dir() or not (session_dir / "session.json").is_file():
            raise StudioAPIError(
                "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
            )

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
            writer=writer,
        )

        with self._lock:
            self._jobs[job_id] = ctx

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
        """Run job under L2 job lease with cooperative cancellation and crash handling."""
        session_dir = session_mgr.get_session_dir(ctx.session_id)
        lock_file = session_dir / "jobs" / f".job_{ctx.job_id}.lock"

        try:
            with studio_lock(lock_file, level=L2_JOB, session_id=ctx.session_id, timeout=10.0):
                ctx.status = "running"
                ctx.stage = "started"
                ctx.pct_complete = 5
                self._persist_job_status(session_mgr, ctx.session_id, ctx)

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

                # Execute domain runner logic
                result = runner_fn(session_mgr, ctx, **kwargs)

                # If auto-committed, delta is committed and cannot be marked cancelled!
                auto_committed = isinstance(result, dict) and result.get("auto_committed") is True

                with ctx.lock:
                    if auto_committed:
                        ctx.status = "completed"
                        ctx.stage = "completed"
                        ctx.pct_complete = 100
                        ctx.finished_at = datetime.now(UTC).isoformat()
                        ctx.result = result
                        if ctx.writer:
                            ctx.writer.buffer_event(
                                "job_completed",
                                {
                                    "job_id": ctx.job_id,
                                    "status": "completed",
                                    "result": ctx.result,
                                },
                            )
                            ctx.writer.flush()
                    elif ctx.cancel_event.is_set():
                        ctx.status = "cancelled"
                        ctx.stage = "cancelled"
                        ctx.finished_at = datetime.now(UTC).isoformat()
                        if ctx.writer:
                            ctx.writer.buffer_event(
                                "job_cancelled",
                                {"job_id": ctx.job_id, "status": "cancelled"},
                            )
                            ctx.writer.flush()
                    else:
                        ctx.status = "completed"
                        ctx.stage = "completed"
                        ctx.pct_complete = 100
                        ctx.finished_at = datetime.now(UTC).isoformat()
                        ctx.result = result or {}
                        if ctx.writer:
                            ctx.writer.buffer_event(
                                "job_completed",
                                {
                                    "job_id": ctx.job_id,
                                    "status": "completed",
                                    "result": ctx.result,
                                },
                            )
                            ctx.writer.flush()

        except Exception as exc:
            logger.exception("Job %s execution failed", ctx.job_id)
            ctx.status = "failed"
            ctx.stage = "failed"
            ctx.finished_at = datetime.now(UTC).isoformat()
            ctx.error = {
                "code": getattr(exc, "code", "EXECUTION_FAILURE"),
                "detail": str(exc),
            }
            if ctx.writer:
                with contextlib.suppress(Exception):
                    ctx.writer.buffer_event(
                        "job_failed",
                        {
                            "job_id": ctx.job_id,
                            "status": "failed",
                            "error": ctx.error,
                        },
                    )
                    ctx.writer.flush()

        finally:
            self._persist_job_status(session_mgr, ctx.session_id, ctx)

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

        # Check on-disk job file
        session_dir = session_mgr.get_session_dir(session_id)
        job_file = self._get_job_file(session_mgr, session_id, job_id, create=False)
        if not job_file.is_file():
            raise StudioAPIError("JOB_NOT_FOUND", f"Job '{job_id}' not found.", status_code=404)

        try:
            data = json.loads(job_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD", f"Job record '{job_id}' is corrupt.", status_code=500
            ) from exc

        # Crash recovery check: if status was pending/running, test if L2 lock can be acquired
        status = str(data.get("status", "")).upper()
        if status in ("PENDING", "RUNNING"):
            lock_file = session_dir / "jobs" / f".job_{job_id}.lock"
            try:
                # Non-blocking acquisition probe
                with studio_lock(lock_file, level=L2_JOB, session_id=session_id, timeout=0.01):
                    # Acquired lock successfully -> worker thread died / abandoned
                    data["status"] = "failed"
                    data["stage"] = "abandoned"
                    data["finished_at"] = datetime.now(UTC).isoformat()
                    data["error"] = {
                        "code": "JOB_ABANDONED_SERVER_CRASH",
                        "detail": "Background job abandoned due to process restart or crash.",
                    }
                    self._write_job_data_atomic(session_mgr, session_id, job_id, data)
            except LockTimeoutError:
                # Lock is actively held by a running worker; leave as running
                pass

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
        """Cancel an in-flight background job."""
        _validate_job_id(job_id)
        session_dir = session_mgr.get_session_dir(session_id)
        if not session_dir.is_dir() or not (session_dir / "session.json").is_file():
            raise StudioAPIError(
                "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
            )

        with self._lock:
            ctx = self._jobs.get(job_id)

        if ctx and ctx.session_id == session_id:
            with ctx.lock:
                if ctx.status.lower() in ("completed", "failed", "cancelled"):
                    raise StudioAPIError(
                        "JOB_ALREADY_TERMINAL",
                        f"Job '{job_id}' is already terminal ({ctx.status}).",
                        status_code=409,
                    )
                if ctx.stage == "committing":
                    raise StudioAPIError(
                        "JOB_CANNOT_BE_CANCELLED",
                        f"Job '{job_id}' is actively committing delta and cannot be cancelled.",
                        status_code=409,
                    )
                ctx.cancel_event.set()
                with contextlib.suppress(Exception):
                    cancel_marker = session_dir / "jobs" / f".{job_id}.cancel"
                    cancel_marker.write_text("cancelled", encoding="utf-8")
                    set_owner_only_permissions(cancel_marker)
                ctx.status = "cancelled"
                ctx.stage = "cancelled"
                ctx.finished_at = datetime.now(UTC).isoformat()
                if ctx.writer:
                    ctx.writer.buffer_event(
                        "job_cancelled",
                        {
                            "job_id": job_id,
                            "status": "cancelled",
                            "reason": reason or "Cancelled by user",
                        },
                    )
                    with contextlib.suppress(Exception):
                        ctx.writer.flush()
                self._persist_job_status(session_mgr, session_id, ctx)

            return self.get_job(session_mgr, session_id, job_id)

        # On-disk cancellation check (cross-process or post-restart)
        data = self.get_job(session_mgr, session_id, job_id)
        if data.get("status", "").lower() in ("completed", "failed", "cancelled"):
            raise StudioAPIError(
                "JOB_ALREADY_TERMINAL",
                f"Job '{job_id}' is already terminal ({data.get('status')}).",
                status_code=409,
            )
        if data.get("stage") == "committing":
            raise StudioAPIError(
                "JOB_CANNOT_BE_CANCELLED",
                f"Job '{job_id}' is actively committing delta and cannot be cancelled.",
                status_code=409,
            )

        cancel_marker = session_dir / "jobs" / f".{job_id}.cancel"
        with contextlib.suppress(Exception):
            cancel_marker.write_text("cancelled", encoding="utf-8")
            set_owner_only_permissions(cancel_marker)

        data["status"] = "cancelled"
        data["stage"] = "cancelled"
        data["finished_at"] = datetime.now(UTC).isoformat()
        self._write_job_data_atomic(session_mgr, session_id, job_id, data)
        return data

    async def stream_job_events(
        self,
        session_mgr: StudioSessionManager,
        session_id: str,
        job_id: str,
        last_event_id: int | str | None = None,
    ) -> AsyncIterator[str]:
        """Stream SSE wire protocol events for the given job with Last-Event-ID replay."""
        _validate_job_id(job_id)
        session_dir = session_mgr.get_session_dir(session_id)
        if not session_dir.is_dir() or not (session_dir / "session.json").is_file():
            raise StudioAPIError(
                "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
            )
        journal_file = session_dir / "jobs" / f"{job_id}.events.jsonl"
        job_file = self._get_job_file(session_mgr, session_id, job_id, create=False)

        if not job_file.is_file() and not journal_file.is_file():
            raise StudioAPIError("JOB_NOT_FOUND", f"Job '{job_id}' not found.", status_code=404)

        with self._lock:
            ctx = self._jobs.get(job_id)

        writer = ctx.writer if ctx else None

        def _is_active() -> bool:
            if ctx:
                return str(ctx.status).upper() in ("PENDING", "RUNNING")
            if job_file.is_file():
                with contextlib.suppress(Exception):
                    rec = json.loads(job_file.read_text(encoding="utf-8"))
                    return str(rec.get("status", "")).upper() in ("PENDING", "RUNNING")
            return False

        async for chunk in stream_sse_events(
            journal_file=journal_file,
            last_event_id=last_event_id,
            writer=writer,
            is_active=_is_active,
            poll_interval=1.0,
        ):
            yield chunk


# ---------------------------------------------------------------------------
# Domain Job Runner Functions
# ---------------------------------------------------------------------------


def run_parse_job(
    session_mgr: StudioSessionManager,
    ctx: JobContext,
    source_ids: list[str] | None = None,
    ocr_mode: str = "auto",
) -> dict[str, Any]:
    """Execute document parsing and layout extraction on session sources."""
    session_dir = session_mgr.get_session_dir(ctx.session_id)
    manifest_file = session_dir / "inputs" / "manifest.json"
    if not manifest_file.is_file():
        return {"parsed_count": 0, "chunks_count": 0}

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    files = manifest.get("files", [])

    if source_ids:
        files = [f for f in files if f.get("source_id") in source_ids]

    cas = session_mgr.get_cas(ctx.session_id)
    ingestion_service = IngestionService(
        adapters=[
            MarkdownDocumentAdapter(),
            DelimitedTabularAdapter(),
            PyArrowTabularAdapter(),
            StructuredDataDocumentAdapter(),
            MarkupDocumentAdapter(),
            PlainTextDocumentAdapter(),
        ]
    )
    chunks_file = session_dir / "evidence" / "chunks.jsonl"
    chunks_file.parent.mkdir(parents=True, exist_ok=True)
    tables_file = session_dir / "evidence" / "tables.jsonl"

    parsed_chunks: list[EvidenceChunk] = []
    parsed_tables: list[TableStructure] = []
    total_files = len(files)

    for idx, f in enumerate(files, 1):
        if ctx.is_cancelled(session_mgr):
            break

        ctx.stage = f"parsing_file_{idx}_of_{total_files}"
        ctx.pct_complete = int(10 + (idx / max(1, total_files)) * 80)
        get_job_supervisor()._persist_job_status(session_mgr, ctx.session_id, ctx)

        source_id = f.get("source_id", "")
        source_sha = f.get("sha256", "")
        filename = f.get("filename", "unknown.txt")

        try:
            raw_bytes = cas.get(source_sha)
            res = ingestion_service.ingest_bytes(
                data=raw_bytes,
                filename=filename,
            )
            for c in res.chunks:
                parsed_chunks.append(c)
                if ctx.writer:
                    ctx.writer.buffer_event(
                        "chunk_parsed",
                        {
                            "job_id": ctx.job_id,
                            "source_id": source_id,
                            "chunk_id": c.chunk_id,
                            "page_number": c.page_number or 1,
                            "byte_length": len(c.text.encode("utf-8")),
                        },
                    )
            if hasattr(res, "tables") and res.tables:
                for t in res.tables:
                    parsed_tables.append(t)
        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning("Failed to ingest %s: %s", filename, exc)

    # Persist evidence chunks to chunks.jsonl atomically under L5 session lock with deduplication
    if parsed_chunks:
        lock_file = session_dir / ".session.lock"
        with studio_lock(lock_file, level=L5_SESSION, session_id=ctx.session_id):
            existing_chunks: list[EvidenceChunk] = []
            if chunks_file.is_file():
                for line in chunks_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        with contextlib.suppress(Exception):
                            existing_chunks.append(EvidenceChunk.model_validate_json(line))

            seen_chunk_keys = {
                (c.source_sha256 or c.source_id or "", c.chunk_id) for c in parsed_chunks
            }
            combined_chunks = [
                c
                for c in existing_chunks
                if (c.source_sha256 or c.source_id or "", c.chunk_id) not in seen_chunk_keys
            ] + parsed_chunks

            tmp_chunks = chunks_file.parent / f".chunks.{uuid.uuid4().hex[:8]}.tmp"
            fd = os.open(str(tmp_chunks), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with open(fd, "w", encoding="utf-8") as fp:
                for c in combined_chunks:
                    fp.write(json.dumps(c.model_dump(mode="json", exclude_none=True)) + "\n")
                fp.flush()
                os.fsync(fp.fileno())
            set_owner_only_permissions(tmp_chunks)
            os.replace(tmp_chunks, chunks_file)
            set_owner_only_permissions(chunks_file)

    # Persist evidence tables to tables.jsonl atomically under L5 lock with deduplication (MAJ-04)
    if parsed_tables:
        lock_file = session_dir / ".session.lock"
        with studio_lock(lock_file, level=L5_SESSION, session_id=ctx.session_id):
            existing_tables: list[TableStructure] = []
            if tables_file.is_file():
                for line in tables_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        with contextlib.suppress(Exception):
                            existing_tables.append(TableStructure.model_validate_json(line))

            seen_table_ids = {t.table_id for t in parsed_tables}
            combined_tables = [
                t for t in existing_tables if t.table_id not in seen_table_ids
            ] + parsed_tables

            tmp_tables = tables_file.parent / f".tables.{uuid.uuid4().hex[:8]}.tmp"
            fd = os.open(str(tmp_tables), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with open(fd, "w", encoding="utf-8") as fp:
                for t in combined_tables:
                    fp.write(json.dumps(t.model_dump(mode="json", exclude_none=True)) + "\n")
                fp.flush()
                os.fsync(fp.fileno())
            set_owner_only_permissions(tmp_tables)
            os.replace(tmp_tables, tables_file)
            set_owner_only_permissions(tables_file)

    if ctx.writer:
        ctx.writer.flush()

    return {
        "parsed_sources_count": len(files),
        "chunks_count": len(parsed_chunks),
        "tables_count": len(parsed_tables),
    }


def run_clarify_job(
    session_mgr: StudioSessionManager,
    ctx: JobContext,
    mode: str = "auto",
    max_questions: int = 5,
) -> dict[str, Any]:
    """Execute polysemy scan and clarification question generation."""
    session_dir = session_mgr.get_session_dir(ctx.session_id)
    chunks_file = session_dir / "evidence" / "chunks.jsonl"
    chunks: list[EvidenceChunk] = []
    if chunks_file.is_file():
        for line in chunks_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                with contextlib.suppress(Exception):
                    chunks.append(EvidenceChunk.model_validate_json(line))

    if not chunks or ctx.is_cancelled(session_mgr):
        return {"questions_count": 0}

    harness = ClarificationHarness(session_dir=session_dir)
    questions = harness.generate_questions(
        candidate_terms=None,
        evidence_chunks=chunks,
        max_questions=max_questions,
    )

    if ctx.writer:
        for q in questions:
            ctx.writer.buffer_event(
                "clarification_generated",
                {
                    "job_id": ctx.job_id,
                    "question_id": q.question_id,
                    "focus_concept": q.focus_concept,
                },
            )
        ctx.writer.flush()

    return {"questions_count": len(questions)}


def run_construct_job(
    session_mgr: StudioSessionManager,
    ctx: JobContext,
    base_version: str,
    base_sha256: str,
    auto_commit: bool = False,
    fibo_modules: list[str] | None = None,
    mode: str = "auto",
    if_match: str | None = None,
) -> dict[str, Any]:
    """Execute deterministic construction pipeline producing proposal or version commit."""
    session_dir = session_mgr.get_session_dir(ctx.session_id)

    # 1. Load base ontology
    session_meta = session_mgr.get_session(ctx.session_id)
    active_version = (base_version or session_meta.head_version).lstrip("v")

    vdir = session_dir / "versions" / f"v{active_version}"
    ont_file = vdir / "ontology.json"
    if not ont_file.is_file():
        ont_file = vdir / "ontology.yaml"
    if ont_file.is_file():
        if ont_file.suffix == ".json":
            raw_json = ont_file.read_text(encoding="utf-8")
            base_ontology = GraphAOntology.model_validate_json(raw_json)
        else:
            import yaml

            raw_yaml = ont_file.read_text(encoding="utf-8")
            base_ontology = GraphAOntology.model_validate(yaml.safe_load(raw_yaml))
    else:
        raise StudioAPIError(
            "CORRUPT_SESSION_STATE",
            f"Base ontology artifact missing for version 'v{active_version}' "
            f"in session '{ctx.session_id}'.",
            status_code=500,
        )

    # OCC verification on loaded base ontology
    canonical_base_sha = compute_ontology_sha256(base_ontology)
    clean_base_sha = base_sha256.strip('"') if base_sha256 else None
    if clean_base_sha and clean_base_sha not in (canonical_base_sha, session_meta.head_sha256):
        raise StudioAPIError(
            "CONCURRENCY_CONFLICT",
            f"base_sha256 '{base_sha256}' does not match loaded base ontology "
            f"digest '{canonical_base_sha}'.",
            status_code=412,
        )

    # 2. Load evidence chunks & tables
    chunks_file = session_dir / "evidence" / "chunks.jsonl"
    chunks: list[EvidenceChunk] = []
    if chunks_file.is_file():
        for line in chunks_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                with contextlib.suppress(Exception):
                    chunks.append(EvidenceChunk.model_validate_json(line))

    tables_file = session_dir / "evidence" / "tables.jsonl"
    tables: list[TableStructure] = []
    if tables_file.is_file():
        for line in tables_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                with contextlib.suppress(Exception):
                    tables.append(TableStructure.model_validate_json(line))

    # 3. Load clarification answers
    answers_file = session_dir / "clarifications" / "answers.json"
    answers_dict: dict[str, Any] = {}
    if answers_file.is_file():
        with contextlib.suppress(Exception):
            answers_dict = json.loads(answers_file.read_text(encoding="utf-8"))

    pipeline = DeterministicConstructionPipeline()
    opts = ConstructOptions(
        auto_commit=False,  # auto_commit handled below under L5
        if_match=None,
    )
    proposal = pipeline.construct_proposal(
        base_ontology=base_ontology,
        evidence_chunks=chunks,
        tables=tables,
        clarification_answers=answers_dict,
        options=opts,
    )

    proposal_ops = (
        proposal.delta.operations
        if hasattr(proposal, "delta") and hasattr(proposal.delta, "operations")
        else getattr(proposal, "operations", [])
    )

    if ctx.writer:
        for op in proposal_ops:
            if hasattr(op, "external_mappings") and op.external_mappings:
                for m in op.external_mappings:
                    ctx.writer.buffer_event(
                        "fibo_grounding",
                        {
                            "job_id": ctx.job_id,
                            "concept_uri": getattr(op, "concept_uri", ""),
                            "matched_fibo_uri": m.target_uri,
                            "score": m.confidence,
                        },
                    )
        ctx.writer.flush()

    if ctx.is_cancelled(session_mgr):
        return {
            "proposal_id": getattr(proposal, "proposal_id", None),
            "status": "cancelled",
            "message": "Job cancelled before commit.",
        }

    if auto_commit:
        principal_id = getattr(ctx, "principal", None) or "human:local_admin"
        if principal_id.startswith("agent:"):
            raise StudioAPIError(
                "AGENT_PROPOSAL_ACCEPT_FORBIDDEN",
                "Automated agent identities are forbidden from auto-commit construction.",
                status_code=403,
            )

        # ONT-EVO-002: ungrounded or low-confidence concepts cannot be auto-committed
        # without human waiver (MAJ-01)
        added_concepts: dict[str, list[Any]] = {}
        for op in proposal_ops:
            op_type = getattr(op, "op_type", "")
            if op_type == "add_concept" or isinstance(op, AddConceptOp):
                c_uri = getattr(op, "concept_uri", "")
                mappings = list(getattr(op, "external_mappings", None) or [])
                added_concepts[c_uri] = mappings
            elif op_type == "add_external_mapping" or isinstance(op, AddExternalMappingOp):
                c_uri = getattr(op, "concept_uri", "")
                if c_uri in added_concepts:
                    added_concepts[c_uri].append(op)

        for c_uri, mappings in added_concepts.items():
            if not mappings:
                raise StudioAPIError(
                    "LOW_CONFIDENCE_GROUNDING",
                    f"Concept '{c_uri}' is ungrounded (missing external ontology mapping) "
                    "and cannot be auto-committed without human review and waiver.",
                    status_code=422,
                )
            for m in mappings:
                conf = getattr(m, "confidence", 0.0)
                if conf < 0.7:
                    raise StudioAPIError(
                        "LOW_CONFIDENCE_GROUNDING",
                        f"Concept '{c_uri}' has low-confidence grounding ({conf}) "
                        "and cannot be auto-committed without human review and waiver.",
                        status_code=422,
                    )

        # Also check all ops for low-confidence external mappings
        for op in proposal_ops:
            if hasattr(op, "external_mappings") and op.external_mappings:
                for m in op.external_mappings:
                    if getattr(m, "confidence", 1.0) < 0.7:
                        conf_val = getattr(m, "confidence", 0.0)
                        raise StudioAPIError(
                            "LOW_CONFIDENCE_GROUNDING",
                            f"Concept has low-confidence grounding ({conf_val}) "
                            "and cannot be auto-committed without human review and waiver.",
                            status_code=422,
                        )

        clean_if_match = (
            if_match.strip('"') if if_match else (base_sha256.strip('"') if base_sha256 else "")
        )
        if not clean_if_match:
            raise StudioAPIError(
                "MISSING_AUTO_COMMIT_PRECONDITION",
                "Auto-commit construction strictly requires an If-Match header or base_sha256 "
                "anchoring the expected base ontology SHA-256.",
                status_code=428,
            )
        if clean_if_match not in (canonical_base_sha, session_meta.head_sha256):
            raise StudioAPIError(
                "CONCURRENCY_CONFLICT",
                f"Auto-commit precondition '{clean_if_match}' does not match loaded "
                f"base ontology digest '{canonical_base_sha}'.",
                status_code=412,
            )
        if clean_if_match != session_meta.head_sha256:
            raise StudioAPIError(
                "CONCURRENCY_CONFLICT",
                f"Auto-commit precondition '{clean_if_match}' does not match active session "
                f"head digest '{session_meta.head_sha256}'.",
                status_code=412,
            )

        with ctx.lock:
            if ctx.is_cancelled(session_mgr):
                return {
                    "proposal_id": getattr(proposal, "proposal_id", None),
                    "status": "cancelled",
                    "message": "Job cancelled before commit.",
                }
            ctx.stage = "committing"

        # Commit under L5, buffer SSE, and flush to L3 ONLY after releasing L5!
        delta = (
            proposal.delta
            if hasattr(proposal, "delta")
            else (proposal.to_delta() if hasattr(proposal, "to_delta") else proposal)
        )
        atomic_svc = AtomicCommitService(session_manager=session_mgr)

        author = getattr(ctx, "principal", None) or "human:local_admin"
        commit_res = atomic_svc.commit_delta(
            session_id=ctx.session_id,
            delta=delta,
            proposal_id=getattr(proposal, "proposal_id", None),
            author=author,
            if_match=clean_if_match,
        )

        # Flush SSE journal under L3 after releasing L5 session lock
        if ctx.writer:
            ctx.writer.buffer_event(
                "version_committed",
                {
                    "job_id": ctx.job_id,
                    "target_version": commit_res.version,
                    "target_sha256": commit_res.target_sha256,
                },
            )
            ctx.writer.flush()

        return {
            "proposal_id": getattr(proposal, "proposal_id", None),
            "auto_committed": True,
            "target_version": commit_res.version,
            "target_sha256": commit_res.target_sha256,
        }

    # Save proposal to disk atomically
    proposals_dir = session_dir / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)
    prop_file = proposals_dir / f"{proposal.proposal_id}.json"
    tmp_prop = proposals_dir / f".{proposal.proposal_id}.{secrets.token_hex(4)}.tmp"
    fd = os.open(str(tmp_prop), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(fd, "w", encoding="utf-8") as fp:
        fp.write(json.dumps(proposal.model_dump(mode="json", exclude_none=True), indent=2))
        fp.flush()
        os.fsync(fp.fileno())
    set_owner_only_permissions(tmp_prop)
    os.replace(tmp_prop, prop_file)
    set_owner_only_permissions(prop_file)

    return {
        "proposal_id": proposal.proposal_id,
        "operations_count": len(proposal_ops),
    }


# Singleton job supervisor instance
_GLOBAL_SUPERVISOR: JobSupervisor | None = None


def get_job_supervisor() -> JobSupervisor:
    """Return shared JobSupervisor singleton instance."""
    global _GLOBAL_SUPERVISOR
    if _GLOBAL_SUPERVISOR is None:
        _GLOBAL_SUPERVISOR = JobSupervisor()
    return _GLOBAL_SUPERVISOR


__all__ = [
    "JobContext",
    "JobSupervisor",
    "get_job_supervisor",
    "run_clarify_job",
    "run_construct_job",
    "run_parse_job",
]
