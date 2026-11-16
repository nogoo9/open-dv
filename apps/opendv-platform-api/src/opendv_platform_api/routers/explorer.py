"""3-Level Explorer API router (`/api/v1/runs/{run_id}/explorer`).

Conforms to GAP-05, T6, Decision D3, and User Directive R9.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from opendv_contracts import ExplorerBundleDTO, RbacRole
from opendv_platform_api.deps import get_opendv_service, get_rbac_role
from opendv_service import OpenDVService

router = APIRouter(tags=["explorer"])


@router.get(
    "/runs/{run_id}/explorer",
    response_model=ExplorerBundleDTO,
    summary="Get 3-Level Explorer bundle for a run",
)
async def get_explorer_bundle(
    run_id: str,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    role: Annotated[RbacRole, Depends(get_rbac_role)],
) -> ExplorerBundleDTO:
    """Retrieve 3-level explorer bundle (Phases, Stages, Artifacts) for an execution run."""
    return service.get_explorer_bundle(run_id, role=role)
