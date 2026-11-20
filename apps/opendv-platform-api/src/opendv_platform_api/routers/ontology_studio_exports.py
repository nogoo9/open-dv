"""Ontology Studio starter-packs, export, promotion, bundle, and clone endpoints."""

from __future__ import annotations

import contextlib
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Header, Query, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from opendv_contracts.dtos import starter_packs as _sp
from opendv_platform_api import studio_deps as _sd
from opendv_platform_api.schemas import ontology_studio as _s
from opendv_platform_api.security import studio_auth as _sa
from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_platform_api.studio_deps import AdminDep, EditorDep, MgrDep, ViewerDep
from opendv_service.studio import session_export_workflows as _sew
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.session_source_workflows import apply_starter_pack_workflow
from opendv_sources.asset_management import AssetManagementStarterPackRegistry

router = APIRouter(prefix="/ontology/studio", tags=["ontology-studio"])


@router.get("/starter-packs", response_model=_sp.StarterPackListResponse)
def list_starter_packs_endpoint() -> _sp.StarterPackListResponse:
    """List available Asset Management vertical starter packs."""
    return AssetManagementStarterPackRegistry.list_response()


@router.post(
    "/sessions/{session_id}/starter-packs/apply", response_model=_sp.ApplyStarterPackResponse
)
def apply_starter_pack_endpoint(
    session_id: str, payload: _sp.ApplyStarterPackRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(_sd.get_session_manager)],
    principal: Annotated[_sa.StudioPrincipal, Depends(_sd.require_editor)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> _sp.ApplyStarterPackResponse:
    """Apply one or more Asset Management starter packs to the active session."""
    meta = _sd.get_session_or_404(session_mgr, session_id)
    clean_if = _sd.parse_strong_etag_or_400(if_match) if if_match is not None else None
    clean_b = (
        _sd.parse_strong_etag_or_400(payload.base_sha256, header_name="base_sha256")
        if payload.base_sha256
        else None
    )
    with _sd.translate_studio_errors():
        next_ver, new_sha, c_cnt, r_cnt = apply_starter_pack_workflow(
            session_mgr=session_mgr, meta=meta, sdir=session_mgr.get_session_dir(session_id),
            pack_ids=payload.pack_ids, base_version=payload.base_version,
            domain_namespace=payload.domain_namespace, expected_sha=clean_if or clean_b,
            principal_id=principal.principal,
        )
        return _sp.ApplyStarterPackResponse(
            applied_pack_ids=payload.pack_ids, ontology_version=next_ver,
            ontology_sha256=new_sha, concept_count=c_cnt, relationship_count=r_cnt,
        )


@router.get("/sessions/{session_id}/export")
def export_ontology_endpoint(
    session_id: str, session_mgr: MgrDep, _p: ViewerDep,
    export_format: str = Query(default="yaml"), version: str | None = Query(default=None),
) -> Response:
    """Multi-format export of active or historical ontology snapshot."""
    meta = _sd.get_session_or_404(session_mgr, session_id)
    with _sd.translate_studio_errors():
        content, media_type, clean_ver, safe_ext = _sew.export_session_ontology_workflow(
            meta=meta, sdir=session_mgr.get_session_dir(session_id),
            export_format=export_format, version=version,
        )
    disp = f'attachment; filename="ontology-v{clean_ver}.{safe_ext}"'
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": disp})


@router.post("/sessions/{session_id}/promote", response_model=_s.PromoteResponse)
def promote_session(
    request: Request, session_id: str, payload: _s.PromoteRequest,
    session_mgr: MgrDep, principal: AdminDep,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    x_workspace_if_match: Annotated[str | None, Header(alias="X-OpenDV-Workspace-If-Match")] = None,
) -> _s.PromoteResponse:
    """Two-phase atomic promotion of head ontology into workspace under L4 + L5 locks."""
    meta = _sd.get_session_or_404(session_mgr, session_id)
    with _sd.translate_studio_errors():
        res = _sew.promote_session_workflow(
            session_mgr=session_mgr, meta=meta,
            allowed_roots=_sd._resolve_allowed_workspace_roots(session_mgr), if_match=if_match,
            head_sha256_body=payload.head_sha256, workspace_if_match=x_workspace_if_match,
            workspace_sha256_body=payload.workspace_sha256, rationale=payload.rationale,
            workspace_arg=payload.workspace,
            acknowledge_historical=payload.acknowledge_historical_contract_warning,
            historical_rationale=payload.historical_contract_rationale,
            principal_id=principal.principal, principal_role=principal.role,
            is_remote=(_sa.get_server_mode(request) == _sa.SERVER_MODE_REMOTE),
        )
        return _s.PromoteResponse(**res)


@router.post("/sessions/{session_id}/bundle")
def export_bundle_endpoint(
    session_id: str, payload: _s.BundleExportRequest, session_mgr: MgrDep, principal: EditorDep
) -> Response:
    """Export full session into portable archive .odvstudio.zip enforcing taint gates."""
    _sd.get_session_or_404(session_mgr, session_id)
    with _sd.translate_studio_errors():
        zip_bytes = _sew.export_bundle_workflow(
            session_mgr=session_mgr, sdir=session_mgr.get_session_dir(session_id),
            acknowledge_confidential=payload.acknowledge_confidential_source_export,
            export_rationale=payload.export_rationale, principal_id=principal.principal,
            is_admin=principal.is_admin,
        )
    disp = f'attachment; filename="{session_id}.odvstudio.zip"'
    return Response(
        content=zip_bytes, media_type="application/zip", headers={"Content-Disposition": disp}
    )


@router.post("/sessions/bundle/import", response_model=_s.StudioSessionDTO, status_code=201)
async def import_bundle_endpoint(
    file: UploadFile, session_mgr: MgrDep, principal: EditorDep, request: Request,
    acknowledge_unverified_import_attribution: bool = Query(default=False),
    acknowledgement_rationale: str | None = Query(default=None),
    new_session_id: str | None = Query(default=None), force: bool = Query(default=False),
    form_acknowledge: Annotated[
        bool | None, Form(alias="acknowledge_unverified_import_attribution")
    ] = None,
    form_rationale: Annotated[str | None, Form(alias="acknowledgement_rationale")] = None,
    form_new_session_id: Annotated[str | None, Form(alias="new_session_id")] = None,
    form_force: Annotated[bool | None, Form(alias="force")] = None,
) -> _s.StudioSessionDTO:
    """Streaming import of .odvstudio.zip archive."""
    del request
    eff_ack = bool(acknowledge_unverified_import_attribution or form_acknowledge)
    eff_rat = acknowledgement_rationale if acknowledgement_rationale is not None else form_rationale
    eff_new_sid = new_session_id or form_new_session_id
    eff_force = bool(force or form_force)
    if (eff_ack or eff_force) and not principal.is_admin:
        raise StudioAPIError(
            "FORBIDDEN_ADMIN_REQUIRED",
            "Importing bundles with attribution acknowledgement or force overwrite "
            "requires administrator role.",
        )
    tmp_in, size_bytes = await _sd.stream_upload_to_tempfile(
        file, session_mgr.studio_root / ".scratch",
        max_bytes=_sd.get_effective_max_bundle_import_bytes(),
        prefix="bundle_import", suffix=".zip",
    )
    try:
        if size_bytes == 0:
            raise StudioAPIError("ARCHIVE_SECURITY_VIOLATION", "Uploaded bundle archive is empty.")
        with _sd.translate_studio_errors():
            res = await run_in_threadpool(
                _sew.import_bundle_sync, tmp_in=tmp_in, session_mgr=session_mgr,
                principal_id=principal.principal, principal_role=principal.role,
                new_session_id=eff_new_sid, acknowledge_unverified_import_attribution=eff_ack,
                acknowledgement_rationale=eff_rat, force=eff_force,
            )
            return _s.StudioSessionDTO(**res)
    finally:
        with contextlib.suppress(OSError):
            if tmp_in.exists():
                tmp_in.unlink()


@router.post(
    "/sessions/{session_id}/bundle/clone", response_model=_s.StudioSessionDTO, status_code=201
)
def clone_session_endpoint(
    session_id: str, session_mgr: MgrDep, principal: EditorDep,
    payload: _s.CloneSessionRequest | None = None,
) -> _s.StudioSessionDTO:
    """Clone session into an isolated session with re-anchored local audit chain under L5 lock."""
    _sd.get_session_or_404(session_mgr, session_id)
    with _sd.translate_studio_errors():
        res = _sew.clone_session_workflow(
            session_mgr=session_mgr, session_id=session_id,
            new_session_id=payload.new_session_id if payload else None,
            principal_id=principal.principal, principal_role=principal.role,
            is_admin=principal.is_admin, is_automated=principal.is_automated,
        )
        return _s.StudioSessionDTO(**res)


__all__ = ["router"]
