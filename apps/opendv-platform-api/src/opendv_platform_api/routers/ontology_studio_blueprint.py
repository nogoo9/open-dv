"""Ontology Studio VaultBlueprint export API Router (`/api/v1/ontology/studio`)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request
from pydantic import BaseModel, Field

from opendv_contracts.dtos.blueprint import BlueprintExportResponseDTO
from opendv_platform_api.routers.ontology_studio import (
    get_session_manager,
    get_studio_root,
    require_viewer,
)
from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_platform_api.security.studio_auth import (
    SERVER_MODE_REMOTE,
    StudioPrincipal,
    get_server_mode,
    is_loopback_client,
)
from opendv_service.studio.blueprint_export import export_vault_blueprint_response
from opendv_service.studio.session_manager import StudioSessionManager

router = APIRouter(prefix="/ontology/studio", tags=["ontology-studio"])


class ExportBlueprintRequest(BaseModel):
    """Optional payload specifying target ontology version tag."""

    version_tag: str | None = Field(
        default=None,
        description="Optional semantic version tag of ontology snapshot to export (e.g. 'v1.0.0')",
    )


def get_studio_viewer(
    request: Request,
    studio_root: Annotated[Path, Depends(get_studio_root)],
) -> StudioPrincipal:
    """Authenticate caller as studio viewer, with permissive fallback for local loopback."""
    try:
        return require_viewer(request, studio_root)
    except StudioAPIError:
        if get_server_mode(request) == SERVER_MODE_REMOTE:
            raise
        if is_loopback_client(request):
            principal = StudioPrincipal(
                principal="human:local_admin",
                role="admin",
                is_admin=True,
                auth_method="loopback",
            )
            request.state.studio_principal = principal
            return principal
        raise


@router.post(
    "/sessions/{session_id}/export-blueprint",
    response_model=BlueprintExportResponseDTO,
)
def export_session_blueprint_post(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(get_studio_viewer)],
    payload: Annotated[ExportBlueprintRequest | None, Body()] = None,
    version_tag: Annotated[str | None, Query()] = None,
) -> BlueprintExportResponseDTO:
    """Export the active or specified version of a Studio session as a VaultBlueprintDTO (POST)."""
    target_version = (
        payload.version_tag if payload and payload.version_tag else None
    ) or version_tag
    return export_vault_blueprint_response(
        session_id=session_id,
        version_tag=target_version,
        session_manager=session_mgr,
    )


@router.get(
    "/sessions/{session_id}/export-blueprint",
    response_model=BlueprintExportResponseDTO,
)
def export_session_blueprint_get(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(get_studio_viewer)],
    version_tag: Annotated[str | None, Query()] = None,
) -> BlueprintExportResponseDTO:
    """Export the active or specified version of a Studio session as a VaultBlueprintDTO (GET)."""
    return export_vault_blueprint_response(
        session_id=session_id,
        version_tag=version_tag,
        session_manager=session_mgr,
    )


__all__ = ["router"]
