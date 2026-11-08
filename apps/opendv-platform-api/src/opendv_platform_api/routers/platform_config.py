"""Platform & Agent Harness configuration endpoints (`/api/v1/platform/config`)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from pydantic import ValidationError

from opendv_contracts import (
    InputValidationError,
    PlatformConfigResponse,
    PlatformConfigUpdateRequest,
    PlatformConfigValidateRequest,
    PlatformConfigValidationResponse,
    RbacRole,
)
from opendv_platform_api.deps import get_opendv_service
from opendv_service import OpenDVService, RolePermissionError, normalize_rbac_role

router = APIRouter(tags=["platform-config"])


@router.get("/platform/config", response_model=PlatformConfigResponse)
async def get_platform_config(
    request: Request,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    x_opendv_role: Annotated[str | None, Header(alias="X-OpenDV-Role")] = None,
) -> PlatformConfigResponse:
    """Return active Platform & Agent Harness configuration and provider readiness."""
    raw_role = x_opendv_role or request.headers.get("x-opendv-role")
    effective_role = (
        normalize_rbac_role(raw_role, default=RbacRole.PUBLIC)
        if raw_role is not None and raw_role.strip()
        else RbacRole.INTERNAL_MODELER
    )
    resp = service.get_platform_config()
    return resp.redact_for_role(effective_role.value)


@router.put("/platform/config", response_model=PlatformConfigResponse)
async def update_platform_config(
    req: PlatformConfigUpdateRequest,
    request: Request,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    x_opendv_role: Annotated[str | None, Header(alias="X-OpenDV-Role")] = None,
) -> PlatformConfigResponse:
    """Update Platform & Agent Harness configuration with RBAC enforcement."""
    raw_role = x_opendv_role or request.headers.get("x-opendv-role")
    effective_role = (
        normalize_rbac_role(raw_role, default=RbacRole.PUBLIC)
        if raw_role is not None and raw_role.strip()
        else RbacRole.INTERNAL_MODELER
    )
    if effective_role in {RbacRole.PUBLIC, RbacRole.AUDITOR_FINANCE}:
        raise RolePermissionError(
            f"Insufficient RBAC clearance: role '{effective_role.value}' is not authorized "
            "to modify platform configuration."
        )

    try:
        resp = service.update_platform_config(req)
        return resp.redact_for_role(effective_role.value)
    except (ValidationError, ValueError) as exc:
        raise InputValidationError(str(exc)) from exc


@router.post("/platform/config/validate", response_model=PlatformConfigValidationResponse)
async def validate_platform_config(
    req: PlatformConfigValidateRequest,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> PlatformConfigValidationResponse:
    """Deterministically validate candidate Platform & Agent Harness configuration."""
    return service.validate_platform_config(req)


__all__ = ["router"]
