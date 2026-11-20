"""Human-in-the-loop Steward decision endpoints (`/api/v1/runs/{run_id}/decisions`)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from opendv_platform_api.deps import get_opendv_service, get_rbac_role
from opendv_platform_api.studio_deps import ViewerDep
from opendv_service import CaptainHoldDecisionRequest, CaptainHoldDecisionResponse, OpenDVService
from opendv_service.rbac import RbacRole

router = APIRouter(prefix="/runs/{run_id}/decisions", tags=["decisions"])


@router.get("")
async def list_run_decisions(
    run_id: str,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    _principal: ViewerDep,
) -> dict[str, Any]:
    """List active Captain-Holds and resolved Steward decisions for a run."""
    return service.list_run_decisions(run_id)


@router.post("", response_model=CaptainHoldDecisionResponse)
async def submit_captain_hold_decision(
    run_id: str,
    req: CaptainHoldDecisionRequest,
    request: Request,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    _principal: ViewerDep,
) -> CaptainHoldDecisionResponse:
    """Resolve an active Tier U3 / G3 / G4 Captain-Hold with a Steward decision."""
    if get_rbac_role(request) == RbacRole.PUBLIC:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Public role cannot resolve Steward Captain-Hold decisions.",
        )
    return service.resolve_captain_hold(run_id, req)
