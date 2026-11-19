"""Ontology Studio source ingestion, connector profiling, parse, and evidence preview endpoints."""

from __future__ import annotations

import contextlib
import urllib.parse
from pathlib import PureWindowsPath
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from opendv_contracts.errors import OpenDVError
from opendv_platform_api.deps import get_opendv_service
from opendv_platform_api.job_supervisor import run_parse_job
from opendv_platform_api.routers.ontology_studio_sessions import get_manifest
from opendv_platform_api.schemas.ontology_studio import (
    ConnectorSourceRequest,
    JobHandleDTO,
    ParseRequest,
    SourceDocumentDTO,
    SourceUploadResponse,
    TextSourceUploadRequest,
)
from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_platform_api.security.studio_auth import StudioPrincipal
from opendv_platform_api.studio_deps import (
    get_effective_max_source_upload_bytes,
    get_job_supervisor,
    get_session_manager,
    get_session_or_404,
    raise_from_opendv_error,
    require_editor,
    require_editor_or_automated,
    require_viewer,
    resolve_server_allowed_hosts,
    stream_upload_to_tempfile,
)
from opendv_service import OpenDVService
from opendv_service.studio.atomic_commit import delete_source
from opendv_service.studio.delta_normalizer import StudioWorkflowError
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.session_source_workflows import persist_uploaded_source_sync
from opendv_service.studio.wizard_workflows import (
    attach_connector_workflow,
    get_evidence_chunks_workflow,
    get_evidence_tables_workflow,
    validate_parse_preflight_workflow,
)

router = APIRouter(prefix="/ontology/studio", tags=["ontology-studio"])


@router.get("/sessions/{session_id}/sources", response_model=list[SourceDocumentDTO])
def list_sources(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_viewer)],
) -> list[SourceDocumentDTO]:
    """List all ingested source documents registered in session manifest."""
    manifest_dto = get_manifest(session_id, session_mgr, principal)
    return [
        SourceDocumentDTO(source_id=f.get("source_id", ""), source_sha256=f.get("sha256", ""), file_name=f.get("filename", ""), filename=f.get("filename", ""), size_bytes=f.get("size_bytes", 0), byte_count=f.get("size_bytes", 0), mime_type=f.get("mime_type", "application/octet-stream"), do_not_send_to_agent=bool(f.get("do_not_send_to_agent", False)), tombstoned=bool(f.get("tombstoned", False)))
        for f in manifest_dto.files if not f.get("tombstoned", False)
    ]


@router.post("/sessions/{session_id}/sources", response_model=SourceUploadResponse, status_code=201)
async def upload_source(session_id: str, file: UploadFile, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor_or_automated)], request: Request, do_not_send_to_agent: Annotated[bool, Form()] = False, description: Annotated[str | None, Form()] = None, ocr_mode: Annotated[str, Form()] = "auto") -> SourceUploadResponse:
    """Stream document to CAS blob store and register in inputs/manifest.json."""
    get_session_or_404(session_mgr, session_id)
    if not do_not_send_to_agent and "do_not_send_to_agent" in request.query_params:
        do_not_send_to_agent = request.query_params.get("do_not_send_to_agent", "").lower() in ("true", "1", "yes")
    if description is None and "description" in request.query_params:
        description = request.query_params.get("description")
    clean_ocr = (ocr_mode or "auto").strip().lower()
    if clean_ocr not in {"auto", "force", "off"}:
        raise StudioAPIError("SYNTAX_INVALID_ARGUMENT", f"Invalid ocr_mode '{ocr_mode}'.")
    tmp_blob_path, size_bytes = await stream_upload_to_tempfile(file, session_mgr.studio_root / ".scratch", max_bytes=get_effective_max_source_upload_bytes(), prefix="source_upload", suffix=".bin")
    try:
        if size_bytes == 0:
            raise StudioAPIError("CORRUPT_SOURCE_BLOB", "Uploaded source file is empty.")
        base_name = PureWindowsPath((file.filename or "").strip()).name
        filename = base_name if base_name and base_name not in {".", ".."} else "upload.bin"
        raw_ct = (file.content_type or "").split(";", 1)[0].strip().lower()
        mime_type = "text/markdown" if (not raw_ct or raw_ct == "application/octet-stream") and filename.lower().endswith((".md", ".markdown")) else (raw_ct or "application/octet-stream")
        res = await run_in_threadpool(persist_uploaded_source_sync, session_mgr=session_mgr, session_id=session_id, tmp_blob_path=tmp_blob_path, size_bytes=size_bytes, filename=filename, mime_type=mime_type, do_not_send_to_agent=do_not_send_to_agent, description=description, principal_id=principal.principal, is_automated=principal.is_automated, ocr_mode=clean_ocr)
        return SourceUploadResponse(**res)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc
    finally:
        with contextlib.suppress(OSError):
            if tmp_blob_path.exists():
                tmp_blob_path.unlink()


@router.post("/sessions/{session_id}/sources/text", response_model=SourceUploadResponse, status_code=201)
def upload_text_source(session_id: str, payload: TextSourceUploadRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor_or_automated)]) -> SourceUploadResponse:
    """Ingest free-form text or analyst notes directly into session CAS storage."""
    get_session_or_404(session_mgr, session_id)
    try:
        res = session_mgr.add_text_source(session_id, text=payload.text, source_name=payload.source_name, format=payload.format, do_not_send_to_agent=payload.do_not_send_to_agent, description=payload.description, principal=principal.principal)
        return SourceUploadResponse(source_id=res["source_id"], source_sha256=res["source_sha256"], file_name=res["file_name"], filename=res["filename"], size_bytes=res["size_bytes"], byte_count=res["byte_count"], mime_type=res["mime_type"], do_not_send_to_agent=res["do_not_send_to_agent"])
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except Exception as exc:
        raise StudioAPIError("EXECUTION_FAILURE", str(exc)) from exc


@router.post("/sessions/{session_id}/sources/connectors", status_code=201)
def attach_connector_source_endpoint(session_id: str, payload: ConnectorSourceRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor_or_automated)], service: Annotated[OpenDVService, Depends(get_opendv_service)]) -> dict[str, Any]:
    """Attach and profile a tabular connector source (SQLite/DuckDB/CSV/TSV/Parquet/XLSX/JSONL)."""
    get_session_or_404(session_mgr, session_id)
    try:
        platform_cas = getattr(service.state, "cas_store", None) or getattr(service.state, "cas", None)
        body = {k: v for k, v in payload.model_dump(mode="json").items() if k != "allowed_hosts"}
        return attach_connector_workflow(session_mgr=session_mgr, session_id=session_id, payload=body, principal_id=principal.principal, platform_cas=platform_cas, allowed_hosts=resolve_server_allowed_hosts(service))
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


@router.get("/sessions/{session_id}/sources/{source_id}", response_model=SourceDocumentDTO)
def get_source_detail(session_id: str, source_id: str, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_viewer)]) -> SourceDocumentDTO:
    """Retrieve metadata for an ingested source document."""
    src = next((s for s in list_sources(session_id, session_mgr, principal) if s.source_id == source_id), None)
    if not src or src.tombstoned:
        raise StudioAPIError("SOURCE_NOT_FOUND", f"Source '{source_id}' not found.")
    return src


@router.delete("/sessions/{session_id}/sources/{source_id}", status_code=204)
def delete_source_endpoint(session_id: str, source_id: str, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor)]) -> None:
    """Tombstone and purge unreferenced source document (human editor/admin required)."""
    get_session_or_404(session_mgr, session_id)
    get_source_detail(session_id, source_id, session_mgr, principal)
    try:
        delete_source(session_dir=session_mgr.get_session_dir(session_id), source_id=source_id)
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except Exception as exc:
        msg = str(exc)
        if "SOURCE_NOT_FOUND" in msg:
            raise StudioAPIError("SOURCE_NOT_FOUND", msg) from exc
        if "COMMITTED_VERSION" in msg or "SourceReferencedByCommittedVersionError" in type(exc).__name__:
            raise StudioAPIError("SOURCE_REFERENCED_BY_COMMITTED_VERSION", f"Source '{source_id}' is referenced by a committed version and cannot be deleted.") from exc
        if "PENDING_PROPOSAL" in msg or "SourceReferencedByPendingProposalError" in type(exc).__name__:
            raise StudioAPIError("SOURCE_REFERENCED_BY_PENDING_PROPOSAL", f"Source '{source_id}' is referenced by a pending proposal and cannot be deleted.") from exc
        raise StudioAPIError("EXECUTION_FAILURE", msg) from exc


@router.get("/sessions/{session_id}/sources/{source_id}/content")
def get_source_content(session_id: str, source_id: str, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_viewer)]) -> Response:
    """Preview or download raw source content bytes from CAS enforcing taint perimeter."""
    src = get_source_detail(session_id, source_id, session_mgr, principal)
    if principal.is_automated and src.do_not_send_to_agent:
        raise StudioAPIError("CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN", f"Automated principal '{principal.principal}' is strictly forbidden from accessing confidential source '{source_id}'.")
    try:
        raw_bytes = session_mgr.get_cas(session_id).get(src.source_sha256)
    except Exception as exc:
        raise StudioAPIError("SOURCE_NOT_FOUND", f"Raw blob for '{source_id}' missing in CAS.") from exc
    safe_name = urllib.parse.quote(src.file_name or src.filename or f"{source_id}.bin")
    return Response(content=raw_bytes, media_type=src.mime_type or "application/octet-stream", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


@router.post("/sessions/{session_id}/parse", response_model=JobHandleDTO, status_code=202)
def parse_sources(session_id: str, payload: ParseRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor_or_automated)]) -> JobHandleDTO:
    """Trigger background document layout extraction and OCR parsing job."""
    get_session_or_404(session_mgr, session_id)
    try:
        raw_ocr = validate_parse_preflight_workflow(sdir=session_mgr.get_session_dir(session_id), session_id=session_id, source_ids=payload.source_ids, ocr_mode=payload.ocr_mode)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc
    return JobHandleDTO(**get_job_supervisor().submit_job(session_mgr=session_mgr, session_id=session_id, job_type="parse", runner_fn=run_parse_job, principal=principal.principal, role=principal.role, source_ids=payload.source_ids, ocr_mode=raw_ocr))


@router.get("/sessions/{session_id}/evidence/tables")
def get_evidence_tables(session_id: str, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], _principal: Annotated[StudioPrincipal, Depends(require_viewer)]) -> dict[str, Any]:
    """Return profiled source tables, <= 50 sample rows, PK/FK hints, and FIBO gaps."""
    get_session_or_404(session_mgr, session_id)
    return get_evidence_tables_workflow(session_mgr, session_id)


@router.get("/sessions/{session_id}/evidence/chunks")
def get_evidence_chunks(session_id: str, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], _principal: Annotated[StudioPrincipal, Depends(require_viewer)]) -> dict[str, Any]:
    """Return parsed document chunks, char spans, confidence, and table_data."""
    get_session_or_404(session_mgr, session_id)
    return get_evidence_chunks_workflow(session_mgr, session_id)


__all__ = ["router"]
