"""Platform & Agent Harness configuration endpoints (`/api/v1/platform/config`)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import ValidationError

from opendv_contracts import (
    InputValidationError,
    PlatformConfigResponse,
    PlatformConfigUpdateRequest,
    PlatformConfigValidateRequest,
    PlatformConfigValidationResponse,
)
from opendv_contracts import (
    RbacRole as ContractsRbacRole,
)
from opendv_platform_api.deps import get_opendv_service, get_rbac_role
from opendv_platform_api.studio_deps import ViewerDep
from opendv_service import OpenDVService, RolePermissionError
from opendv_service.rbac import RbacRole

router = APIRouter(tags=["platform-config"])


def _attach_remote_unauthenticated(
    resp: PlatformConfigResponse, service: OpenDVService
) -> PlatformConfigResponse:
    flag = bool(getattr(getattr(service, "settings", None), "remote_unauthenticated_mode", False))
    return resp.model_copy(update={"remote_unauthenticated_mode": flag})


@router.get("/platform/config", response_model=PlatformConfigResponse)
async def get_platform_config(
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    role: Annotated[RbacRole, Depends(get_rbac_role)],
) -> PlatformConfigResponse:
    """Return active Platform & Agent Harness configuration and provider readiness."""
    resp = _attach_remote_unauthenticated(service.get_platform_config(), service)
    return resp.redact_for_role(role.value)


@router.put("/platform/config", response_model=PlatformConfigResponse)
async def update_platform_config(
    req: PlatformConfigUpdateRequest,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    role: Annotated[RbacRole, Depends(get_rbac_role)],
) -> PlatformConfigResponse:
    """Update Platform & Agent Harness configuration with RBAC enforcement."""
    if str(role) in {ContractsRbacRole.PUBLIC.value, ContractsRbacRole.AUDITOR_FINANCE.value}:
        err = RolePermissionError(
            f"Insufficient RBAC clearance: role '{role.value}' is not authorized "
            "to modify platform configuration."
        )
        err.http_status = 403
        raise err

    try:
        resp = _attach_remote_unauthenticated(service.update_platform_config(req), service)
        return resp.redact_for_role(role.value)
    except (ValidationError, ValueError) as exc:
        raise InputValidationError(str(exc)) from exc


@router.post("/platform/config/validate", response_model=PlatformConfigValidationResponse)
async def validate_platform_config(
    req: PlatformConfigValidateRequest,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    _principal: ViewerDep,
    _role: Annotated[RbacRole, Depends(get_rbac_role)],
) -> PlatformConfigValidationResponse:
    """Deterministically validate candidate Platform & Agent Harness configuration."""
    return service.validate_platform_config(req)


__all__ = ["router"]
