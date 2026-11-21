"""Ontology Studio job status, SSE event streaming, and session logs endpoints."""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse

from opendv_platform_api.schemas.ontology_studio import JobStatusDTO
from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_platform_api.security.studio_auth import StudioPrincipal
from opendv_platform_api.studio_deps import (
    get_job_supervisor,
    get_session_manager,
    get_session_or_404,
    require_editor_or_automated,
    require_viewer,
    resolve_platform_ledger_dirs,
    session_logs_activity_hooks,
    validate_job_id_param,
)
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.wizard_workflows import (
    export_logs_workflow,
    query_logs_workflow,
    stream_logs_workflow,
)

router = APIRouter(prefix="/ontology/studio", tags=["ontology-studio"])
_SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


@router.get("/sessions/{session_id}/jobs/{job_id}", response_model=JobStatusDTO)
@router.get("/jobs/{job_id}", response_model=JobStatusDTO)
def get_job_status_endpoint(
    job_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
    session_id: str | None = None,
) -> JobStatusDTO:
    """Get background job status, stage, pct_complete, and error/result."""
    if session_id:
        get_session_or_404(session_mgr, session_id)
    validate_job_id_param(job_id)
    supervisor = get_job_supervisor()
    effective_sid = session_id or supervisor.resolve_job_session_id(session_mgr, job_id)
    return JobStatusDTO(**supervisor.get_job(session_mgr, effective_sid, job_id))


@router.get("/sessions/{session_id}/jobs/{job_id}/events")
@router.get("/jobs/{job_id}/events")
async def get_job_events_stream(
    job_id: str,
    request: Request,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
    session_id: str | None = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Stream W3C Server-Sent Events with monotonic Last-Event-ID replay and keepalive."""
    del request
    if session_id:
        get_session_or_404(session_mgr, session_id)
    validate_job_id_param(job_id)
    if last_event_id is not None and not re.match(r"^[0-9]+$", last_event_id.strip()):
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid Last-Event-ID cursor '{last_event_id}'; must be a non-negative integer.",
        )
    supervisor = get_job_supervisor()
    effective_sid = session_id or supervisor.resolve_job_session_id(session_mgr, job_id)
    stream_iter = supervisor.stream_job_events(
        session_mgr=session_mgr,
        session_id=effective_sid,
        job_id=job_id,
        last_event_id=last_event_id,
    )
    return StreamingResponse(stream_iter, media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/sessions/{session_id}/jobs/{job_id}/cancel", response_model=JobStatusDTO)
@router.post("/jobs/{job_id}/cancel", response_model=JobStatusDTO)
def cancel_job_endpoint(
    job_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_editor_or_automated)],
    session_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> JobStatusDTO:
    """Cooperatively abort a running background job, releasing L2 lease."""
    if session_id:
        get_session_or_404(session_mgr, session_id)
    validate_job_id_param(job_id)
    supervisor = get_job_supervisor()
    effective_sid = session_id or supervisor.resolve_job_session_id(session_mgr, job_id)
    reason = (payload or {}).get("reason")
    return JobStatusDTO(**supervisor.cancel_job(session_mgr, effective_sid, job_id, reason=reason))


@router.get("/sessions/{session_id}/logs")
def get_session_logs_endpoint(
    session_id: str,
    request: Request,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
    stage_id: str | None = Query(default=None),
    stage: str | None = Query(default=None),
    wizard_step: int | None = Query(default=None),
    step: int | None = Query(default=None),
    persona: str | None = Query(default=None),
    source_id: str | None = Query(default=None),
    job_id: str | None = Query(default=None),
    category: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Query merged Studio journals and linked run ledgers with stage/step/persona filters."""
    get_session_or_404(session_mgr, session_id)
    return query_logs_workflow(
        session_mgr,
        session_id,
        stage_id=stage_id or stage,
        wizard_step=wizard_step if wizard_step is not None else step,
        persona=persona,
        source_id=source_id,
        job_id=job_id,
        category=category,
        limit=limit,
        offset=offset,
        extra_ledger_dirs=resolve_platform_ledger_dirs(request),
    )


@router.get("/sessions/{session_id}/logs/stream")
async def stream_session_logs_endpoint(
    session_id: str,
    request: Request,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
    once: bool = Query(default=False),
    stage_id: str | None = Query(default=None),
    stage: str | None = Query(default=None),
    wizard_step: int | None = Query(default=None),
    persona: str | None = Query(default=None),
    category: str | None = Query(default=None),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Stream session agent & execution logs over W3C SSE with Last-Event-ID replay."""
    get_session_or_404(session_mgr, session_id)
    platform_state = getattr(request.app.state, "platform_state", None)
    if platform_state is None and getattr(request.app.state, "service", None) is not None:
        platform_state = getattr(request.app.state.service, "state", None)
    is_active_fn, terminal_status_fn = session_logs_activity_hooks(
        session_mgr, session_id, platform_state
    )
    effective_last_event_id = last_event_id or request.query_params.get("last_event_id")
    stream_iter = stream_logs_workflow(
        session_mgr,
        session_id,
        last_event_id=effective_last_event_id,
        once=once,
        stage_id=stage_id or stage,
        wizard_step=wizard_step,
        persona=persona,
        category=category,
        is_active_fn=is_active_fn,
        terminal_status_fn=terminal_status_fn,
        extra_ledger_dirs=resolve_platform_ledger_dirs(request),
    )
    return StreamingResponse(stream_iter, media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/sessions/{session_id}/logs/export")
def export_session_logs_endpoint(
    session_id: str,
    request: Request,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
) -> Response:
    """Export full session agent & execution log trail as JSONL."""
    get_session_or_404(session_mgr, session_id)
    jsonl_text = export_logs_workflow(
        session_mgr, session_id, extra_ledger_dirs=resolve_platform_ledger_dirs(request)
    )
    return Response(
        content=jsonl_text,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{session_id}-logs.jsonl"'},
    )


__all__ = ["router"]
