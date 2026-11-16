"""3-Level Explorer API router (`/api/v1/runs/{run_id}/explorer`).

Conforms to GAP-05, T6, Decision D3, and User Directive R9.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

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
    wave_id: Annotated[
        str | None,
        Query(
            description=(
                "Optional wave identifier filter (e.g. 'W1' or '1'). When provided, "
                "filters artifacts to those matching the specified wave."
            ),
        ),
    ] = None,
) -> ExplorerBundleDTO:
    """Retrieve 3-level explorer bundle (Phases, Stages, Artifacts) for an execution run.

    Supports optional wave subset filtering (`?wave_id=W1` or `?wave_id=1`).
    """
    return service.get_explorer_bundle(run_id, wave_id=wave_id, role=role)
