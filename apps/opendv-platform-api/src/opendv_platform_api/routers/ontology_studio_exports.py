"""Ontology Studio starter-packs, export, promotion, bundle, and clone endpoints."""

from __future__ import annotations

import contextlib
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Header, Query, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from opendv_contracts.dtos.starter_packs import (
    ApplyStarterPackRequest,
    ApplyStarterPackResponse,
    StarterPackListResponse,
)
from opendv_platform_api.schemas.ontology_studio import (
    BundleExportRequest,
    CloneSessionRequest,
    PromoteRequest,
    PromoteResponse,
    StudioSessionDTO,
)
from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_platform_api.security.studio_auth import (
    SERVER_MODE_REMOTE,
    StudioPrincipal,
    get_server_mode,
)
from opendv_platform_api.studio_deps import (
    _resolve_allowed_workspace_roots,
    get_effective_max_bundle_import_bytes,
    get_session_manager,
    get_session_or_404,
    parse_strong_etag_or_400,
    raise_from_opendv_error,
    require_admin,
    require_editor,
    require_viewer,
    stream_upload_to_tempfile,
)
from opendv_service.studio.delta_normalizer import StudioWorkflowError
from opendv_service.studio.session_export_workflows import (
    clone_session_workflow,
    export_bundle_workflow,
    export_session_ontology_workflow,
    import_bundle_sync,
    promote_session_workflow,
)
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.session_source_workflows import apply_starter_pack_workflow
from opendv_sources.asset_management import AssetManagementStarterPackRegistry

router = APIRouter(prefix="/ontology/studio", tags=["ontology-studio"])


@router.get("/starter-packs", response_model=StarterPackListResponse)
def list_starter_packs_endpoint() -> StarterPackListResponse:
    """List available Asset Management vertical starter packs."""
    return AssetManagementStarterPackRegistry.list_response()


@router.post("/sessions/{session_id}/starter-packs/apply", response_model=ApplyStarterPackResponse)
def apply_starter_pack_endpoint(session_id: str, payload: ApplyStarterPackRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor)], if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> ApplyStarterPackResponse:
    """Apply one or more Asset Management starter packs to the active session."""
    meta = get_session_or_404(session_mgr, session_id)
    clean_if_match = parse_strong_etag_or_400(if_match) if if_match is not None else None
    clean_body = parse_strong_etag_or_400(payload.base_sha256, header_name="base_sha256") if payload.base_sha256 else None
    try:
        next_ver, new_sha, c_count, r_count = apply_starter_pack_workflow(session_mgr=session_mgr, meta=meta, sdir=session_mgr.get_session_dir(session_id), pack_ids=payload.pack_ids, base_version=payload.base_version, domain_namespace=payload.domain_namespace, expected_sha=clean_if_match or clean_body, principal_id=principal.principal)
        return ApplyStarterPackResponse(applied_pack_ids=payload.pack_ids, ontology_version=next_ver, ontology_sha256=new_sha, concept_count=c_count, relationship_count=r_count)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


@router.get("/sessions/{session_id}/export")
def export_ontology_endpoint(session_id: str, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], _principal: Annotated[StudioPrincipal, Depends(require_viewer)], export_format: str = Query(default="yaml"), version: str | None = Query(default=None)) -> Response:
    """Multi-format export of active or historical ontology snapshot."""
    meta = get_session_or_404(session_mgr, session_id)
    try:
        content, media_type, clean_ver, safe_ext = export_session_ontology_workflow(meta=meta, sdir=session_mgr.get_session_dir(session_id), export_format=export_format, version=version)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="ontology-v{clean_ver}.{safe_ext}"'})


@router.post("/sessions/{session_id}/promote", response_model=PromoteResponse)
def promote_session(request: Request, session_id: str, payload: PromoteRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_admin)], if_match: Annotated[str | None, Header(alias="If-Match")] = None, x_workspace_if_match: Annotated[str | None, Header(alias="X-OpenDV-Workspace-If-Match")] = None) -> PromoteResponse:
    """Two-phase atomic promotion of head ontology into workspace under L4 + L5 locks."""
    meta = get_session_or_404(session_mgr, session_id)
    try:
        res = promote_session_workflow(session_mgr=session_mgr, meta=meta, allowed_roots=_resolve_allowed_workspace_roots(session_mgr), if_match=if_match, head_sha256_body=payload.head_sha256, workspace_if_match=x_workspace_if_match, workspace_sha256_body=payload.workspace_sha256, rationale=payload.rationale, workspace_arg=payload.workspace, acknowledge_historical=payload.acknowledge_historical_contract_warning, historical_rationale=payload.historical_contract_rationale, principal_id=principal.principal, principal_role=principal.role, is_remote=(get_server_mode(request) == SERVER_MODE_REMOTE))
        return PromoteResponse(**res)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


@router.post("/sessions/{session_id}/bundle")
def export_bundle_endpoint(session_id: str, payload: BundleExportRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor)]) -> Response:
    """Export full session into portable archive .odvstudio.zip enforcing taint gates."""
    get_session_or_404(session_mgr, session_id)
    try:
        zip_bytes = export_bundle_workflow(session_mgr=session_mgr, sdir=session_mgr.get_session_dir(session_id), acknowledge_confidential=payload.acknowledge_confidential_source_export, export_rationale=payload.export_rationale, principal_id=principal.principal, is_admin=principal.is_admin)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc
    return Response(content=zip_bytes, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{session_id}.odvstudio.zip"'})


@router.post("/sessions/bundle/import", response_model=StudioSessionDTO, status_code=201)
async def import_bundle_endpoint(file: UploadFile, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor)], request: Request, acknowledge_unverified_import_attribution: bool = Query(default=False), acknowledgement_rationale: str | None = Query(default=None), new_session_id: str | None = Query(default=None), force: bool = Query(default=False), form_acknowledge: Annotated[bool | None, Form(alias="acknowledge_unverified_import_attribution")] = None, form_rationale: Annotated[str | None, Form(alias="acknowledgement_rationale")] = None, form_new_session_id: Annotated[str | None, Form(alias="new_session_id")] = None, form_force: Annotated[bool | None, Form(alias="force")] = None) -> StudioSessionDTO:
    """Streaming import of .odvstudio.zip archive."""
    del request
    eff_ack = bool(acknowledge_unverified_import_attribution or form_acknowledge)
    eff_rationale = acknowledgement_rationale if acknowledgement_rationale is not None else form_rationale
    eff_new_sid = new_session_id or form_new_session_id
    eff_force = bool(force or form_force)
    if (eff_ack or eff_force) and not principal.is_admin:
        raise StudioAPIError("FORBIDDEN_ADMIN_REQUIRED", "Importing bundles with attribution acknowledgement or force overwrite requires administrator role.")
    tmp_in, size_bytes = await stream_upload_to_tempfile(file, session_mgr.studio_root / ".scratch", max_bytes=get_effective_max_bundle_import_bytes(), prefix="bundle_import", suffix=".zip")
    try:
        if size_bytes == 0:
            raise StudioAPIError("ARCHIVE_SECURITY_VIOLATION", "Uploaded bundle archive is empty.")
        res = await run_in_threadpool(import_bundle_sync, tmp_in=tmp_in, session_mgr=session_mgr, principal_id=principal.principal, principal_role=principal.role, new_session_id=eff_new_sid, acknowledge_unverified_import_attribution=eff_ack, acknowledgement_rationale=eff_rationale, force=eff_force)
        return StudioSessionDTO(**res)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc
    finally:
        with contextlib.suppress(OSError):
            if tmp_in.exists():
                tmp_in.unlink()


@router.post("/sessions/{session_id}/bundle/clone", response_model=StudioSessionDTO, status_code=201)
def clone_session_endpoint(session_id: str, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_editor)], payload: CloneSessionRequest | None = None) -> StudioSessionDTO:
    """Clone session into an isolated session with re-anchored local audit chain under L5 lock."""
    get_session_or_404(session_mgr, session_id)
    try:
        res = clone_session_workflow(session_mgr=session_mgr, session_id=session_id, new_session_id=payload.new_session_id if payload else None, principal_id=principal.principal, principal_role=principal.role, is_admin=principal.is_admin, is_automated=principal.is_automated)
        return StudioSessionDTO(**res)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


__all__ = ["router"]
