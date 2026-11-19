"""Ontology Studio session lifecycle and auth bootstrap REST endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response

from opendv_platform_api.schemas.ontology_studio import (
    CreateSessionRequest,
    ImportOntologyRequest,
    InitStudioRequest,
    InitStudioResponse,
    LaunchNonceResponse,
    ManifestDTO,
    StudioSessionDetailDTO,
    StudioSessionDTO,
    StudioSessionStatusDTO,
    StudioSessionSummaryDTO,
    TokenExchangeRequest,
    TokenExchangeResponse,
    WizardSessionListResponse,
)
from opendv_platform_api.security.csrf import verify_csrf
from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_platform_api.security.studio_auth import (
    SERVER_MODE_REMOTE,
    StudioPrincipal,
    exchange_launch_nonce,
    get_active_session_role,
    get_local_token_path,
    get_server_mode,
    is_loopback_client,
    mint_launch_nonce,
)
from opendv_platform_api.studio_deps import (
    _resolve_allowed_workspace_roots,
    derive_session_status,
    get_job_supervisor,
    get_session_manager,
    get_session_or_404,
    get_studio_root,
    raise_from_opendv_error,
    require_admin,
    require_editor_or_automated,
    require_viewer,
)
from opendv_service.studio.delta_normalizer import StudioWorkflowError
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.session_workflows import (
    create_session_workflow,
    get_session_detail_workflow,
    get_session_manifest_workflow,
    import_ontology_session_workflow,
    list_sessions_workflow,
)

router = APIRouter(prefix="/ontology/studio", tags=["ontology-studio"])


@router.post("/init", response_model=InitStudioResponse)
def init_studio(
    request: Request,
    studio_root: Annotated[Path, Depends(get_studio_root)],
    payload: InitStudioRequest | None = None,
    force: bool = Query(default=False),
) -> InitStudioResponse:
    """Compatibility bootstrap shim returning loopback open-access identity."""
    _ = force
    if get_server_mode(request) == SERVER_MODE_REMOTE or not is_loopback_client(request):
        raise StudioAPIError("LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE", "Bootstrap /init is strictly restricted to local loopback callers.", instance=str(request.url.path))
    verify_csrf(request)
    request.state.studio_principal = StudioPrincipal(principal="human:local_user", role="admin", is_admin=True, auth_method="loopback")
    return InitStudioResponse(status="initialized", token_path=str(get_local_token_path(studio_root)), principal="human:local_user", role="admin", local_token=None, domain_namespace=payload.domain_namespace if payload else None)


@router.get("/sessions", response_model=list[StudioSessionSummaryDTO] | WizardSessionListResponse)
def list_sessions(
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    wizard_only: bool = Query(default=False),
) -> Any:
    """List studio sessions with pagination, status, and optional D25 wizard filtering."""
    return list_sessions_workflow(session_mgr, limit=limit, offset=offset, status=status, wizard_only=wizard_only)


@router.post("/sessions", response_model=StudioSessionDTO, status_code=201)
def create_session(request: Request, payload: CreateSessionRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], _principal: Annotated[StudioPrincipal, Depends(require_editor_or_automated)]) -> StudioSessionDTO:
    """Create a new studio session (genesis 0.0.0 or seeded from workspace)."""
    try:
        res = create_session_workflow(session_mgr=session_mgr, allowed_roots=_resolve_allowed_workspace_roots(session_mgr), session_id=payload.session_id, domain_namespace=payload.domain_namespace, enterprise_owner=payload.enterprise_owner, from_workspace=payload.from_workspace, is_remote=(get_server_mode(request) == SERVER_MODE_REMOTE))
        return StudioSessionDTO(**res)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


@router.post("/sessions/import-ontology", response_model=StudioSessionDTO, status_code=201)
def import_ontology_session(payload: ImportOntologyRequest, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], _principal: Annotated[StudioPrincipal, Depends(require_editor_or_automated)]) -> StudioSessionDTO:
    """Create a new Studio session from YAML, JSON, or Turtle ontology content."""
    try:
        res = import_ontology_session_workflow(session_mgr=session_mgr, session_id=payload.session_id, domain_namespace=payload.domain_namespace, enterprise_owner=payload.enterprise_owner, import_format=payload.format, content=payload.content, expected_sha256=payload.expected_sha256)
        return StudioSessionDTO(**res)
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


@router.get("/sessions/{session_id}", response_model=StudioSessionDetailDTO)
def get_session(session_id: str, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], _principal: Annotated[StudioPrincipal, Depends(require_viewer)]) -> StudioSessionDetailDTO:
    """Retrieve studio session details and counts."""
    meta = get_session_or_404(session_mgr, session_id)
    return StudioSessionDetailDTO(**get_session_detail_workflow(session_mgr, meta))


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], _principal: Annotated[StudioPrincipal, Depends(require_admin)], force: bool = Query(default=False)) -> None:
    """Tombstone and remove session directory (admin required)."""
    meta = get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)
    if ((sdir / "promotion.json").is_file() or bool(meta.promoted_versions)) and not force:
        raise StudioAPIError("CANNOT_DELETE_PROMOTED_SESSION", f"Session '{session_id}' has already been promoted. Pass force=true to delete.")
    get_job_supervisor().purge_session_jobs(session_id)
    session_mgr.delete_session(session_id)


@router.get("/sessions/{session_id}/status", response_model=StudioSessionStatusDTO)
def get_session_status(session_id: str, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], _principal: Annotated[StudioPrincipal, Depends(require_viewer)]) -> StudioSessionStatusDTO:
    """Lightweight status check returning version state and active jobs."""
    meta = get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)
    return StudioSessionStatusDTO(session_id=meta.session_id, status=derive_session_status(sdir, meta), active_version=meta.head_version, head_sha256=meta.head_sha256, active_jobs=get_job_supervisor().count_active_jobs(session_mgr, session_id), taint_classification=meta.taint_classification)


@router.get("/sessions/{session_id}/manifest", response_model=ManifestDTO)
def get_manifest(session_id: str, session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)], principal: Annotated[StudioPrincipal, Depends(require_viewer)]) -> ManifestDTO:
    """Inspect CAS manifest listing all registered sources."""
    get_session_or_404(session_mgr, session_id)
    return ManifestDTO(**get_session_manifest_workflow(session_mgr, session_id, is_automated=principal.is_automated))


@router.post("/auth/launch-nonce", response_model=LaunchNonceResponse)
def mint_launch_nonce_endpoint(request: Request, principal: Annotated[StudioPrincipal, Depends(require_viewer)]) -> LaunchNonceResponse:
    """Mint single-use browser launch nonce (256-bit, 60s TTL) preserving caller RBAC role."""
    if get_server_mode(request) == SERVER_MODE_REMOTE or not is_loopback_client(request):
        raise StudioAPIError("LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE", "Minting launch nonce is strictly restricted to loopback callers.", instance=str(request.url.path))
    if principal.is_automated:
        raise StudioAPIError("AGENT_OPERATION_FORBIDDEN", "Automated principals are strictly forbidden from minting browser launch nonces.", instance=str(request.url.path))
    nonce = mint_launch_nonce(principal=principal.principal, role=principal.role, ttl=60.0)
    return LaunchNonceResponse(launch_nonce=nonce, nonce=nonce, expires_in=60)


@router.post("/auth/token", response_model=TokenExchangeResponse)
def exchange_launch_nonce_endpoint(request: Request, response: Response, payload: TokenExchangeRequest) -> TokenExchangeResponse:
    """Exchange single-use browser launch nonce for HttpOnly session cookie."""
    if get_server_mode(request) == SERVER_MODE_REMOTE or not is_loopback_client(request):
        raise StudioAPIError("LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE", "Nonce exchange is strictly restricted to loopback callers.", instance=str(request.url.path))
    verify_csrf(request)
    raw_nonce = payload.launch_nonce or payload.nonce
    if not raw_nonce:
        raise StudioAPIError("SYNTAX_INVALID_ARGUMENT", "Missing required 'nonce' field.")
    session_token, principal_str = exchange_launch_nonce(str(raw_nonce))
    session_role = get_active_session_role(session_token)
    eff_role = session_role or "admin"
    request.state.studio_principal = StudioPrincipal(principal=principal_str, role=eff_role, is_admin=(eff_role == "admin"), auth_method="cookie")
    response.set_cookie(key="opendv_session", value=session_token, httponly=True, samesite="strict", secure=(request.url.scheme == "https"))
    return TokenExchangeResponse(status="authenticated", principal=principal_str, role=session_role)


__all__ = ["router"]
