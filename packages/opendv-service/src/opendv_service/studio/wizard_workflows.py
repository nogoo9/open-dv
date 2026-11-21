"""Shared Phase 2 Studio wizard, connector, evidence, citation, FIBO Q&A, and logs workflows."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Any

from opendv_contracts.errors import OpenDVError
from opendv_service.studio.connector_ingestion_helpers import validate_connector_payload
from opendv_service.studio.delta_normalizer import (
    StudioWorkflowError,
    parse_strong_etag_or_error,
    raise_from_opendv_error,
)
from opendv_service.studio.proposal_helpers import ensure_session_version_exists
from opendv_service.studio.session_logs_service import _parse_iso_utc, normalize_iso_utc
from opendv_service.studio.session_manager import SessionMetadata, StudioSessionManager
from opendv_sources.fibo import get_default_fibo_loader
from opendv_sources.ingestion.layout_evaluator import TesseractOCRRouter


def validate_parse_preflight_workflow(
    *,
    sdir: Path,
    session_id: str,
    source_ids: list[str] | None,
    ocr_mode: str | None,
) -> str:
    """Validate parse request source_ids and OCR engine availability."""
    raw_ocr = (ocr_mode or "").strip() if isinstance(ocr_mode, str) else ""
    if raw_ocr not in {"auto", "force", "off"}:
        raise StudioWorkflowError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid ocr_mode '{ocr_mode}'. Must be 'auto', 'force', or 'off'.",
            status_code=400,
            exit_code=2,
        )
    if source_ids is not None:
        mfile = sdir / "inputs" / "manifest.json"
        active_ids: set[str] = set()
        if mfile.is_file():
            try:
                mdata = json.loads(mfile.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StudioWorkflowError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt manifest.json in session '{session_id}': {exc}",
                    status_code=500,
                    exit_code=1,
                ) from exc
            if isinstance(mdata, dict) and isinstance(mdata.get("files"), list):
                active_ids = {
                    str(f.get("source_id"))
                    for f in mdata["files"]
                    if isinstance(f, dict)
                    and f.get("source_id")
                    and not f.get("tombstoned", False)
                    and not f.get("deleted", False)
                }
        for req_sid in source_ids:
            if req_sid not in active_ids:
                raise StudioWorkflowError(
                    "SOURCE_NOT_FOUND",
                    f"Source '{req_sid}' not found or tombstoned in session '{session_id}'.",
                    status_code=404,
                    exit_code=3,
                )

    if raw_ocr == "force" and not TesseractOCRRouter(ocr_mode="force").is_available():
        raise StudioWorkflowError(
            "OCR_UNAVAILABLE",
            "Tesseract OCR engine is unavailable on this host for ocr_mode='force'.",
            status_code=501,
            exit_code=1,
        )
    return raw_ocr


def validate_construct_request_workflow(
    *,
    meta: SessionMetadata,
    sdir: Path,
    base_version_req: str | None,
    base_sha256_req: str | None,
    auto_commit: bool,
    mode_req: str | None,
    if_match: str | None,
    is_agent_identity: bool,
    agent_runner: Any,
) -> tuple[str, str, str | None, str | None]:
    """Validate construct preconditions and enforce fail-closed 422 AGENT_NOT_CONFIGURED."""
    ensure_session_version_exists(sdir, base_version_req)
    if mode_req is None:
        raw_mode = "deterministic" if auto_commit else "auto"
    else:
        raw_mode = mode_req.strip().lower()
        if raw_mode not in ("auto", "agent", "deterministic", "challenger"):
            raise StudioWorkflowError(
                "SYNTAX_INVALID_ARGUMENT",
                f"Invalid construction mode '{mode_req}'. "
                "Must be 'auto', 'agent', or 'deterministic'.",
                status_code=400,
                exit_code=2,
            )

    clean_if_match = parse_strong_etag_or_error(if_match) if if_match is not None else None
    clean_body_sha = (
        parse_strong_etag_or_error(base_sha256_req, header_name="base_sha256")
        if base_sha256_req
        else None
    )

    if auto_commit:
        if is_agent_identity:
            raise StudioWorkflowError(
                "AGENT_PROPOSAL_ACCEPT_FORBIDDEN",
                "Automated agent identities are forbidden from auto-commit construction.",
                status_code=403,
                exit_code=7,
            )
        if mode_req is not None and raw_mode != "deterministic":
            raise StudioWorkflowError(
                "SYNTAX_INVALID_ARGUMENT",
                "Auto-commit construction is only permitted in deterministic mode; "
                "non-deterministic modes require human proposal review.",
                status_code=400,
                exit_code=2,
            )
        if not (clean_if_match or clean_body_sha):
            raise StudioWorkflowError(
                "MISSING_AUTO_COMMIT_PRECONDITION",
                "Auto-commit construction requires an 'If-Match' header or 'base_sha256' "
                "anchoring session head SHA-256.",
                status_code=400,
                exit_code=2,
            )
        clean_mode = "deterministic"
    else:
        clean_mode = "deterministic" if raw_mode == "challenger" else raw_mode

    base_version = base_version_req or meta.head_version
    base_sha256 = clean_body_sha or (
        meta.head_sha256
        if base_version.lstrip("v") == meta.head_version.lstrip("v")
        else None
    )

    if clean_if_match and clean_body_sha and clean_if_match != clean_body_sha:
        raise StudioWorkflowError(
            "PRECONDITION_BASE_SHA_MISMATCH",
            f"If-Match header ('{clean_if_match}') does not match "
            f"body base_sha256 ('{clean_body_sha}').",
            status_code=412,
            exit_code=5,
        )
    if clean_if_match is not None and clean_if_match != meta.head_sha256:
        raise StudioWorkflowError(
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"If-Match ('{clean_if_match}') does not match "
            f"session head SHA-256 ('{meta.head_sha256}').",
            status_code=412,
            exit_code=5,
        )
    if auto_commit and clean_body_sha is not None and clean_body_sha != meta.head_sha256:
        raise StudioWorkflowError(
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"base_sha256 ('{clean_body_sha}') does not match "
            f"session head SHA-256 ('{meta.head_sha256}').",
            status_code=412,
            exit_code=5,
        )

    if clean_mode == "agent" or (
        clean_mode == "auto" and not auto_commit and agent_runner is not None
    ):
        if agent_runner is None:
            raise StudioWorkflowError(
                "AGENT_NOT_CONFIGURED",
                "Real LLM agent harness is not configured.",
                status_code=422,
                exit_code=10,
            )
        ensure_fn = getattr(agent_runner, "ensure_agent_configured", None) or getattr(
            agent_runner, "assert_configured", None
        )
        if callable(ensure_fn):
            try:
                ensure_fn()
            except OpenDVError as exc:
                raise raise_from_opendv_error(exc) from exc
        else:
            resolve_fn = getattr(agent_runner, "_resolve_harness", None)
            avail_fn = getattr(agent_runner, "_is_harness_executable_available", None)
            if callable(resolve_fn):
                try:
                    h = resolve_fn()
                except OpenDVError as exc:
                    raise raise_from_opendv_error(exc) from exc
                if h is None or (callable(avail_fn) and not avail_fn(h)):
                    raise StudioWorkflowError(
                        "AGENT_NOT_CONFIGURED",
                        "No usable LLM agent harness or provider credentials are configured.",
                        status_code=422,
                        exit_code=10,
                    )

    return clean_mode, base_version, base_sha256, clean_if_match


def attach_connector_workflow(
    *,
    session_mgr: StudioSessionManager,
    session_id: str,
    payload: dict[str, Any],
    principal_id: str = "human:local_user",
    platform_cas: Any | None = None,
    allowed_hosts: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Ingest and profile a tabular connector source into a Studio session."""
    clean_payload = {k: v for k, v in payload.items() if k != "allowed_hosts"}
    try:
        validate_connector_payload(clean_payload)
        from opendv_service.studio import connector_ingestion as ci

        fn = getattr(ci, "attach_connector_source", None) or getattr(
            ci, "ingest_connector_source", None
        )
        if callable(fn):
            res = fn(
                session_mgr=session_mgr,
                session_id=session_id,
                request_payload=clean_payload,
                principal=principal_id,
                platform_cas=platform_cas,
                allowed_hosts=allowed_hosts,
            )
            return res.model_dump(mode="json") if hasattr(res, "model_dump") else dict(res)
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except ImportError:
        pass
    raise StudioWorkflowError(
        "INTERNAL_ERROR",
        "Connector ingestion service unavailable.",
        status_code=500,
        exit_code=1,
    )


def get_evidence_tables_workflow(
    session_mgr: StudioSessionManager, session_id: str
) -> dict[str, Any]:
    try:
        from opendv_service.studio import connector_ingestion as ci

        fn = getattr(ci, "get_session_evidence_tables", None) or getattr(
            ci, "list_evidence_tables", None
        )
        if callable(fn):
            res = fn(session_mgr, session_id)
            return res if isinstance(res, dict) else {"session_id": session_id, "tables": res}
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except ImportError:
        pass
    sdir = session_mgr.get_session_dir(session_id)
    tpath = sdir / "evidence" / "tables.json"
    tables = json.loads(tpath.read_text(encoding="utf-8")) if tpath.is_file() else []
    return {"session_id": session_id, "tables": tables}


def get_evidence_chunks_workflow(
    session_mgr: StudioSessionManager, session_id: str
) -> dict[str, Any]:
    try:
        from opendv_service.studio import connector_ingestion as ci

        fn = getattr(ci, "get_session_evidence_chunks", None) or getattr(
            ci, "list_evidence_chunks", None
        )
        if callable(fn):
            res = fn(session_mgr, session_id)
            return (
                res
                if isinstance(res, dict)
                else {"session_id": session_id, "chunks": res, "observed_term_counts": {}}
            )
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except ImportError:
        pass
    sdir = session_mgr.get_session_dir(session_id)
    cpath = sdir / "evidence" / "chunks.json"
    chunks = json.loads(cpath.read_text(encoding="utf-8")) if cpath.is_file() else []
    return {"session_id": session_id, "chunks": chunks, "observed_term_counts": {}}


def resolve_citations_workflow(
    session_mgr: StudioSessionManager, session_id: str, citations: list[dict[str, Any]]
) -> dict[str, Any]:
    from opendv_service.studio.citation_resolver import resolve_session_citations

    try:
        resolved = resolve_session_citations(session_mgr, session_id, citations)
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    return (
        resolved
        if isinstance(resolved, dict)
        else {"session_id": session_id, "resolved": resolved}
    )


def lookup_fibo_workflow(query: str, limit: int = 25) -> dict[str, Any]:
    loader = get_default_fibo_loader()
    matches: list[dict[str, Any]] = []
    if query.startswith("http://") or query.startswith("https://"):
        item = loader.lookup_by_uri(query)
        raw_items = [item] if item is not None else []
    else:
        raw_items = loader.search_candidates(query, limit=limit)
    for item in raw_items:
        uri = str(item.get("uri") or "")
        parents = list(item.get("sub_class_of") or [])
        if not parents and uri:
            with contextlib.suppress(Exception):
                parents = list(loader.get_direct_superclasses(uri))
        matches.append(
            {
                "uri": uri,
                "label": item.get("label") or "",
                "definition": item.get("definition") or "",
                "module": item.get("module") or "",
                "parents": parents,
                "match_score": item.get("match_score"),
            }
        )
    return {"query": query, "matches": matches}


def ask_fibo_qa_workflow(
    *,
    session_mgr: StudioSessionManager,
    session_id: str,
    question: str,
    source_ids: list[str] | None = None,
    concept_uri: str | None = None,
    agent_runner: Any = None,
) -> dict[str, Any]:
    from opendv_service.studio.fibo_qa_service import FiboQARequest, answer_fibo_qa

    jdir = session_mgr.get_session_dir(session_id) / "jobs"
    if jdir.is_dir():
        for jfile in jdir.glob("job-*.json"):
            with contextlib.suppress(OSError, json.JSONDecodeError):
                jdata = json.loads(jfile.read_text(encoding="utf-8"))
                st = str(jdata.get("status", "")).lower()
                if st not in {"completed", "failed", "cancelled", "canceled", ""}:
                    raise StudioWorkflowError(
                        "SESSION_JOB_ACTIVE",
                        f"Session '{session_id}' already has an active running job.",
                        status_code=409,
                        exit_code=6,
                    )

    harness = None
    if agent_runner is not None:
        resolve_fn = getattr(agent_runner, "_resolve_harness", None)
        avail_fn = getattr(agent_runner, "_is_harness_executable_available", None)
        candidate = resolve_fn() if callable(resolve_fn) else agent_runner
        if candidate is None or (callable(avail_fn) and not avail_fn(candidate)):
            raise StudioWorkflowError(
                "AGENT_NOT_CONFIGURED",
                "No usable LLM agent harness or provider credentials are configured.",
                status_code=422,
                exit_code=10,
            )
        harness = candidate
    req = FiboQARequest(question=question, source_ids=source_ids, concept_uri=concept_uri)
    try:
        res = answer_fibo_qa(
            session_mgr=session_mgr, session_id=session_id, request=req, harness=harness
        )
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except Exception as exc:
        if "ConfigurationError" in type(exc).__name__ or "not found" in str(exc).lower():
            raise StudioWorkflowError(
                "AGENT_NOT_CONFIGURED", str(exc), status_code=422, exit_code=10
            ) from exc
        raise
    return res.model_dump(mode="json") if hasattr(res, "model_dump") else dict(res)


def query_logs_workflow(
    session_mgr: StudioSessionManager,
    session_id: str,
    *,
    stage_id: str | None = None,
    wizard_step: int | None = None,
    persona: str | None = None,
    source_id: str | None = None,
    job_id: str | None = None,
    category: str | None = None,
    limit: int = 500,
    offset: int = 0,
    min_seq: int = 0,
    extra_ledger_dirs: Sequence[Path | str] | None = None,
) -> dict[str, Any]:
    from opendv_service.studio.session_logs_service import (
        expand_stage_filter,
        query_session_logs,
    )

    allowed_stages = expand_stage_filter(stage_id)
    entries = query_session_logs(
        session_mgr,
        session_id,
        stage_id=stage_id,
        wizard_step=wizard_step,
        persona=persona,
        source_id=source_id,
        job_id=job_id,
        category=category,
        limit=limit,
        offset=offset,
        min_seq=min_seq,
        extra_ledger_dirs=extra_ledger_dirs,
    )
    return {
        "session_id": session_id,
        "entries": entries,
        "total": len(entries),
        "resolved_stage_ids": sorted(allowed_stages) if allowed_stages else [],
    }


def collect_disk_job_records(
    session_mgr: StudioSessionManager, session_id: str
) -> list[tuple[tuple[str, str, int, str], str]]:
    """Collect ``((created_at, finished_at, mtime_ns, name), UPPER_STATUS)`` for Studio jobs."""
    jobs_dir = session_mgr.get_session_dir(session_id) / "jobs"
    records: list[tuple[tuple[str, str, int, str], str]] = []
    if jobs_dir.is_dir():
        for jf in sorted(jobs_dir.glob("*.json")):
            with contextlib.suppress(OSError, json.JSONDecodeError):
                rec = json.loads(jf.read_text(encoding="utf-8"))
                if isinstance(rec, dict) and rec.get("status"):
                    key = (
                        normalize_iso_utc(rec.get("created_at")),
                        normalize_iso_utc(rec.get("finished_at")),
                        int(jf.stat().st_mtime_ns),
                        jf.name,
                    )
                    records.append((key, str(rec["status"]).upper()))
    return records


def resolve_latest_terminal_status(
    records: list[tuple[tuple[str, str, int, str], str]],
) -> str:
    """Resolve terminal stream_end status from the most recent job/run record."""
    if not records:
        return "COMPLETED"
    _, latest = max(
        records,
        key=lambda item: (
            _parse_iso_utc(item[0][0]),
            _parse_iso_utc(item[0][1]),
            item[0][2],
            item[0][3],
        ),
    )
    if latest in {"FAILED", "REJECT-WITH-TRACE"}:
        return "FAILED"
    if latest in {"CANCELLED", "CANCELED", "ABORTED"}:
        return "CANCELLED"
    return "COMPLETED"


def _disk_session_work_status(
    session_mgr: StudioSessionManager, session_id: str
) -> tuple[bool, str]:
    records = collect_disk_job_records(session_mgr, session_id)
    active = any(s in {"QUEUED", "RUNNING"} for _, s in records)
    return active, resolve_latest_terminal_status(records)


async def stream_logs_workflow(
    session_mgr: StudioSessionManager,
    session_id: str,
    *,
    last_event_id: str | None = None,
    once: bool = False,
    stage_id: str | None = None,
    wizard_step: int | None = None,
    persona: str | None = None,
    category: str | None = None,
    is_active_fn: Callable[[], bool] | None = None,
    terminal_status_fn: Callable[[], str] | None = None,
    extra_ledger_dirs: Sequence[Path | str] | None = None,
    poll_interval_s: float = 0.25,
    keepalive_interval_s: float = 15.0,
    max_stream_seconds: float = 3600.0,
) -> AsyncIterator[str]:
    cursor = int(last_event_id.strip()) if last_event_id and last_event_id.strip().isdigit() else 0
    seen_seq = cursor
    started_at = time.monotonic()
    last_wire_at = started_at
    batch_size = 500

    while True:
        while True:
            data = query_logs_workflow(
                session_mgr,
                session_id,
                stage_id=stage_id,
                wizard_step=wizard_step,
                persona=persona,
                category=category,
                limit=batch_size,
                min_seq=seen_seq,
                extra_ledger_dirs=extra_ledger_dirs,
            )
            batch = data["entries"]
            for entry in batch:
                seq = int(entry.get("seq") or 0)
                if seq > seen_seq:
                    seen_seq = seq
                    last_wire_at = time.monotonic()
                    yield f"id: {seq}\nevent: log_entry\ndata: {json.dumps(entry)}\n\n"
            if len(batch) < batch_size:
                break

        disk_active, disk_status = _disk_session_work_status(session_mgr, session_id)
        active = bool(is_active_fn()) if is_active_fn is not None else disk_active
        now = time.monotonic()
        if once or not active or (now - started_at) >= max_stream_seconds:
            if not once and not active:
                tail = query_logs_workflow(
                    session_mgr,
                    session_id,
                    stage_id=stage_id,
                    wizard_step=wizard_step,
                    persona=persona,
                    category=category,
                    limit=batch_size,
                    min_seq=seen_seq,
                    extra_ledger_dirs=extra_ledger_dirs,
                )
                for entry in tail["entries"]:
                    seq = int(entry.get("seq") or 0)
                    if seq > seen_seq:
                        seen_seq = seq
                        yield f"id: {seq}\nevent: log_entry\ndata: {json.dumps(entry)}\n\n"
            term_status = (
                str(terminal_status_fn()).upper()
                if terminal_status_fn is not None
                else disk_status
            )
            if not active and term_status in {"QUEUED", "RUNNING"}:
                term_status = "COMPLETED"
            yield (
                f"id: {seen_seq + 1}\n"
                f"event: stream_end\n"
                f"data: {json.dumps({'status': term_status, 'session_id': session_id})}\n\n"
            )
            break

        if (now - last_wire_at) >= keepalive_interval_s:
            last_wire_at = now
            yield ": keepalive\n\n"
        await asyncio.sleep(poll_interval_s)


def export_logs_workflow(
    session_mgr: StudioSessionManager,
    session_id: str,
    *,
    extra_ledger_dirs: Sequence[Path | str] | None = None,
) -> str:
    data = query_logs_workflow(
        session_mgr, session_id, limit=100_000, extra_ledger_dirs=extra_ledger_dirs
    )
    return "\n".join(json.dumps(e) for e in data["entries"]) + ("\n" if data["entries"] else "")
