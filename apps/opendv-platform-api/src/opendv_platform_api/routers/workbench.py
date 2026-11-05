"""Workbench view projection endpoints (`/api/v1/runs/{run_id}/workbench/{view_id}`)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from opendv_contracts import RbacRole
from opendv_platform_api.deps import get_opendv_service, get_rbac_role
from opendv_service import OpenDVService

router = APIRouter(prefix="/runs/{run_id}/workbench", tags=["workbench"])


@router.get("/{view_id}")
async def get_workbench_view_projection(
    run_id: str,
    view_id: str,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    role: Annotated[RbacRole, Depends(get_rbac_role)],
) -> dict[str, Any]:
    """Return RBAC-redacted 3-Layer workbench view state (`WB-00` through `WB-11`)."""
    return service.get_workbench_view_projection(run_id, view_id, role=role)
