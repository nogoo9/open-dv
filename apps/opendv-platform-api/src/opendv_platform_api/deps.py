"""FastAPI dependency wiring and service injection for Open-DV Platform API."""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, Request

from opendv_service import (
    OpenDVService,
    OpenDVSettings,
    PlatformStateContainer,
    normalize_rbac_role,
)
from opendv_service.rbac import RbacRole


def get_opendv_service(request: Request) -> OpenDVService:
    """Retrieve or initialize the shared OpenDVService instance."""
    service = getattr(request.app.state, "service", None)
    if service is None:
        platform_state = getattr(request.app.state, "platform_state", None)
        settings: OpenDVSettings | None = getattr(request.app.state, "settings", None)
        ws_root = (
            getattr(platform_state, "workspace_root", None)
            if platform_state
            else (settings.workspace_root if settings else None)
        )
        service = OpenDVService(
            state_store=platform_state,
            workspace_root=ws_root,
            settings=settings,
        )
        request.app.state.service = service
    return service


def get_platform_state(request: Request) -> PlatformStateContainer:
    """Backward-compatible accessor for PlatformStateContainer."""
    platform_state = getattr(request.app.state, "platform_state", None)
    if platform_state is not None:
        return platform_state
    service = get_opendv_service(request)
    return service.state


def get_rbac_role(
    request: Request,
    x_opendv_role: Annotated[str | None, Header(alias="X-OpenDV-Role")] = None,
) -> RbacRole:
    """Extract and normalize 5-Tier RBAC role context from request headers."""
    header_val = x_opendv_role or request.headers.get("x-opendv-role")
    return normalize_rbac_role(header_val)


__all__ = [
    "get_opendv_service",
    "get_platform_state",
    "get_rbac_role",
]
