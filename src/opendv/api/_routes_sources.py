"""Group 3 API routes: Sources, uploads, reattach, and Evidence Navigator endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile

from opendv.models.project import ConceptMemorySpec
from opendv.models.sources import (
    ConnectorAdapterSpec,
    DocumentMap,
    PageTextView,
    SourceProfile,
    SourceSummary,
    TableIndexEntry,
    TablePreviewPage,
)
from opendv.models.wire_dtos_discovery import (
    ConceptMemoryUpdate,
    ScanApprovalReq,
    SourceAttachReq,
    SourceInspectReq,
)

from ._route_helpers import (
    dual_request_body_openapi,
    enforce_api_workspace_path,
    get_service,
    handle_source_reattach,
    read_upload_bounded,
)

router = APIRouter()

_PATCH_SOURCE_OPENAPI = dual_request_body_openapi(
    "SourceReattachReq",
    {"kind": {"type": "string"}},
)


@router.get("/sources/adapters", response_model=list[ConnectorAdapterSpec])
def list_source_adapters(request: Request) -> list[ConnectorAdapterSpec]:
    return get_service(request).list_source_adapters()


@router.post("/sources/inspect", response_model=SourceProfile)
def inspect_source(request: Request, body: SourceInspectReq) -> SourceProfile:
    svc = get_service(request)
    effective = body
    if body.kind != "databricks_uc":
        checked = enforce_api_workspace_path(request, svc, body.uri_or_path)
        effective = body.model_copy(update={"uri_or_path": checked})
    return svc.inspect_source(effective)


@router.get("/projects/{project_id}/sources", response_model=list[SourceSummary])
def list_sources(request: Request, project_id: str) -> list[SourceSummary]:
    return get_service(request).list_sources(project_id)


@router.post("/projects/{project_id}/sources", response_model=SourceSummary)
def attach_source(
    request: Request, project_id: str, body: SourceAttachReq
) -> SourceSummary:
    svc = get_service(request)
    effective = body
    if body.kind != "databricks_uc":
        checked = enforce_api_workspace_path(request, svc, body.uri_or_filename)
        effective = body.model_copy(update={"uri_or_filename": checked})
    return svc.attach_source(project_id, effective)


@router.post("/projects/{project_id}/sources/upload", response_model=SourceSummary)
async def upload_source(
    request: Request,
    project_id: str,
    file: Annotated[UploadFile, File()],
    kind: Annotated[str | None, Form()] = None,
) -> SourceSummary:
    content = await read_upload_bounded(file)
    return get_service(request).upload_source(
        project_id,
        filename=file.filename or "upload",
        content=content,
        kind=kind,
    )


@router.patch(
    "/projects/{project_id}/sources/{sid}",
    response_model=SourceSummary,
    openapi_extra=_PATCH_SOURCE_OPENAPI,
)
async def reattach_source(
    request: Request, project_id: str, sid: str
) -> SourceSummary:
    return await handle_source_reattach(request, get_service(request), project_id, sid)


@router.delete("/projects/{project_id}/sources/{sid}", status_code=204)
def detach_source(request: Request, project_id: str, sid: str) -> Response:
    get_service(request).detach_source(project_id, sid)
    return Response(status_code=204)


@router.get(
    "/projects/{project_id}/evidence/tables/index",
    response_model=list[TableIndexEntry],
)
def list_tables_index(request: Request, project_id: str) -> list[TableIndexEntry]:
    return get_service(request).list_tables_index(project_id)


@router.get(
    "/projects/{project_id}/evidence/tables/{tid}",
    response_model=TablePreviewPage,
)
def preview_table_by_id(
    request: Request,
    project_id: str,
    tid: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    source_id: str | None = Query(default=None),
) -> TablePreviewPage:
    return get_service(request).preview_table_by_id(
        project_id, tid, offset=offset, limit=limit, source_id=source_id
    )


@router.get(
    "/projects/{project_id}/evidence/documents/map",
    response_model=list[DocumentMap],
)
def list_document_maps(request: Request, project_id: str) -> list[DocumentMap]:
    return get_service(request).list_document_maps(project_id)


@router.get(
    "/projects/{project_id}/evidence/documents/{did}/pages",
    response_model=list[PageTextView],
)
def get_document_pages(
    request: Request,
    project_id: str,
    did: str,
    page_start: int = Query(default=1, ge=1),
    page_end: int | None = Query(default=None, ge=1),
) -> list[PageTextView]:
    return get_service(request).get_document_pages(
        project_id, did, page_start=page_start, page_end=page_end
    )


@router.post(
    "/projects/{project_id}/evidence/documents/{did}/approve-scan",
    response_model=DocumentMap,
)
def approve_scan_pages(
    request: Request, project_id: str, did: str, body: ScanApprovalReq
) -> DocumentMap:
    return get_service(request).approve_scan_pages(project_id, did, body)


@router.get(
    "/projects/{project_id}/evidence/concept-memory",
    response_model=list[ConceptMemorySpec],
)
def get_concept_memory(request: Request, project_id: str) -> list[ConceptMemorySpec]:
    return get_service(request).get_concept_memory(project_id)


@router.post(
    "/projects/{project_id}/evidence/concept-memory",
    response_model=ConceptMemorySpec,
)
def upsert_concept_memory(
    request: Request, project_id: str, body: ConceptMemoryUpdate
) -> ConceptMemorySpec:
    return get_service(request).upsert_concept_memory(project_id, body)


__all__ = ["router"]
