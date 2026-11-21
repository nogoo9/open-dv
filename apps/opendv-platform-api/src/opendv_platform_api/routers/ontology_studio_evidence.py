"""Ontology Studio source ingestion, connector profiling, parse, and evidence preview endpoints."""

from __future__ import annotations

import contextlib
import urllib.parse
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from opendv_platform_api import studio_deps as _sd
from opendv_platform_api import studio_router_adapters as _ad
from opendv_platform_api.deps import get_opendv_service
from opendv_platform_api.job_supervisor import run_parse_job
from opendv_platform_api.routers.ontology_studio_sessions import get_manifest
from opendv_platform_api.schemas import ontology_studio as _s
from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_platform_api.security.studio_auth import StudioPrincipal
from opendv_platform_api.studio_deps import EdAutoDep, EditorDep, MgrDep, ViewerDep
from opendv_service import OpenDVService
from opendv_service.studio import wizard_workflows as _wiz
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.session_source_workflows import persist_uploaded_source_sync

router = APIRouter(prefix="/ontology/studio", tags=["ontology-studio"])


@router.get("/sessions/{session_id}/sources", response_model=list[_s.SourceDocumentDTO])
def list_sources(
    session_id: str, session_mgr: Annotated[StudioSessionManager, Depends(_sd.get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(_sd.require_viewer)],
) -> list[_s.SourceDocumentDTO]:
    """List all ingested source documents registered in session manifest."""
    return _ad.source_manifest_to_docs(get_manifest(session_id, session_mgr, principal).files)


@router.post(
    "/sessions/{session_id}/sources", response_model=_s.SourceUploadResponse, status_code=201
)
async def upload_source(
    session_id: str, file: UploadFile, session_mgr: MgrDep, principal: EdAutoDep,
    request: Request, do_not_send_to_agent: Annotated[bool, Form()] = False,
    description: Annotated[str | None, Form()] = None, ocr_mode: Annotated[str, Form()] = "auto",
) -> _s.SourceUploadResponse:
    """Stream document to CAS blob store and register in inputs/manifest.json."""
    _sd.get_session_or_404(session_mgr, session_id)
    if not do_not_send_to_agent and "do_not_send_to_agent" in request.query_params:
        do_not_send_to_agent = request.query_params.get("do_not_send_to_agent", "").lower() in (
            "true", "1", "yes",
        )
    if description is None and "description" in request.query_params:
        description = request.query_params.get("description")
    clean_ocr = (ocr_mode or "auto").strip().lower()
    if clean_ocr not in {"auto", "force", "off"}:
        raise StudioAPIError("SYNTAX_INVALID_ARGUMENT", f"Invalid ocr_mode '{ocr_mode}'.")
    tmp_path, size_bytes = await _sd.stream_upload_to_tempfile(
        file, session_mgr.studio_root / ".scratch",
        max_bytes=_sd.get_effective_max_source_upload_bytes(),
        prefix="source_upload", suffix=".bin",
    )
    try:
        if size_bytes == 0:
            raise StudioAPIError("CORRUPT_SOURCE_BLOB", "Uploaded source file is empty.")
        filename, mime_type = _ad.resolve_upload_filename_mime(file)
        with _sd.translate_studio_errors():
            res = await run_in_threadpool(
                persist_uploaded_source_sync, session_mgr=session_mgr, session_id=session_id,
                tmp_blob_path=tmp_path, size_bytes=size_bytes, filename=filename,
                mime_type=mime_type, do_not_send_to_agent=do_not_send_to_agent,
                description=description, principal_id=principal.principal,
                is_automated=principal.is_automated, ocr_mode=clean_ocr,
            )
            return _s.SourceUploadResponse(**res)
    finally:
        with contextlib.suppress(OSError):
            if Path(tmp_path).exists():
                Path(tmp_path).unlink()


@router.post(
    "/sessions/{session_id}/sources/text", response_model=_s.SourceUploadResponse, status_code=201
)
def upload_text_source(
    session_id: str, payload: _s.TextSourceUploadRequest, session_mgr: MgrDep, principal: EdAutoDep
) -> _s.SourceUploadResponse:
    """Ingest free-form text or analyst notes directly into session CAS storage."""
    return _ad.upload_text_source_adapter(session_mgr, session_id, payload, principal)


@router.post("/sessions/{session_id}/sources/connectors", status_code=201)
def attach_connector_source_endpoint(
    session_id: str, payload: _s.ConnectorSourceRequest, session_mgr: MgrDep, principal: EdAutoDep,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> dict[str, Any]:
    """Attach and profile a tabular connector source (SQLite/DuckDB/CSV/TSV/Parquet/XLSX/JSONL)."""
    _sd.get_session_or_404(session_mgr, session_id)
    with _sd.translate_studio_errors():
        pcas = getattr(service.state, "cas_store", None) or getattr(service.state, "cas", None)
        body = {k: v for k, v in payload.model_dump(mode="json").items() if k != "allowed_hosts"}
        return _wiz.attach_connector_workflow(
            session_mgr=session_mgr, session_id=session_id, payload=body,
            principal_id=principal.principal, platform_cas=pcas,
            allowed_hosts=_sd.resolve_server_allowed_hosts(service),
        )


@router.get("/sessions/{session_id}/sources/{source_id}", response_model=_s.SourceDocumentDTO)
def get_source_detail(
    session_id: str, source_id: str, session_mgr: MgrDep, principal: ViewerDep
) -> _s.SourceDocumentDTO:
    """Retrieve metadata for an ingested source document."""
    src = next(
        (s for s in list_sources(session_id, session_mgr, principal) if s.source_id == source_id),
        None,
    )
    if not src or src.tombstoned:
        raise StudioAPIError("SOURCE_NOT_FOUND", f"Source '{source_id}' not found.")
    return src


@router.delete("/sessions/{session_id}/sources/{source_id}", status_code=204)
def delete_source_endpoint(
    session_id: str, source_id: str, session_mgr: MgrDep, principal: EditorDep
) -> None:
    """Tombstone and purge unreferenced source document (human editor/admin required)."""
    _sd.get_session_or_404(session_mgr, session_id)
    get_source_detail(session_id, source_id, session_mgr, principal)
    _ad.delete_session_source_or_raise(session_mgr, session_id, source_id)


@router.get("/sessions/{session_id}/sources/{source_id}/content")
def get_source_content(
    session_id: str, source_id: str, session_mgr: MgrDep, principal: ViewerDep
) -> Response:
    """Preview or download raw source content bytes from CAS enforcing taint perimeter."""
    src = get_source_detail(session_id, source_id, session_mgr, principal)
    raw_bytes = _ad.read_source_content_bytes(session_mgr, session_id, src, principal)
    safe_name = urllib.parse.quote(src.file_name or src.filename or f"{source_id}.bin")
    return Response(
        content=raw_bytes, media_type=src.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}",
            "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
        },
    )


@router.post("/sessions/{session_id}/parse", response_model=_s.JobHandleDTO, status_code=202)
def parse_sources(
    request: Request, session_id: str, payload: _s.ParseRequest,
    session_mgr: MgrDep, principal: EdAutoDep,
) -> _s.JobHandleDTO:
    """Trigger background document layout extraction and OCR parsing job."""
    _sd.get_session_or_404(session_mgr, session_id)
    pstate = getattr(request.app.state, "platform_state", None)
    _sd.ensure_no_active_session_job(session_mgr, session_id, platform_state=pstate)
    with _sd.translate_studio_errors():
        raw_ocr = _wiz.validate_parse_preflight_workflow(
            sdir=session_mgr.get_session_dir(session_id), session_id=session_id,
            source_ids=payload.source_ids, ocr_mode=payload.ocr_mode,
        )
    return _s.JobHandleDTO(
        **_sd.get_job_supervisor().submit_job(
            session_mgr=session_mgr, session_id=session_id, job_type="parse",
            runner_fn=run_parse_job, principal=principal.principal, role=principal.role,
            active_run_check=lambda: _sd.has_active_pipeline_run(pstate, session_id),
            source_ids=payload.source_ids, ocr_mode=raw_ocr,
        )
    )


@router.get("/sessions/{session_id}/evidence/tables")
def get_evidence_tables(session_id: str, session_mgr: MgrDep, _p: ViewerDep) -> dict[str, Any]:
    """Return profiled source tables, <= 50 sample rows, PK/FK hints, and FIBO gaps."""
    _sd.get_session_or_404(session_mgr, session_id)
    return _wiz.get_evidence_tables_workflow(session_mgr, session_id)


@router.get("/sessions/{session_id}/evidence/chunks")
def get_evidence_chunks(session_id: str, session_mgr: MgrDep, _p: ViewerDep) -> dict[str, Any]:
    """Return parsed document chunks, char spans, confidence, and table_data."""
    _sd.get_session_or_404(session_mgr, session_id)
    return _wiz.get_evidence_chunks_workflow(session_mgr, session_id)


__all__ = ["router"]
