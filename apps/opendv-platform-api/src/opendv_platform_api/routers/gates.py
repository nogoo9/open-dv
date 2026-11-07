"""Gate verification scorecard and theorem endpoints (`/api/v1/runs/{run_id}/gates`)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends

from opendv_platform_api.deps import get_opendv_service
from opendv_service import OpenDVService

router = APIRouter(prefix="/runs/{run_id}/gates", tags=["gates"])


@router.get("")
async def get_run_gates(
    run_id: str,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> dict[str, Any]:
    """Return Gate G1/G2/G3/G4 scorecard and theorem proof report for a run."""
    return service.get_gate_report(run_id)


@router.post("")
async def evaluate_run_gates(
    run_id: str,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    payload: Annotated[dict[str, Any] | None, Body()] = None,
) -> dict[str, Any]:
    """Trigger or re-evaluate Gate G1/G2/G3/G4 verification for a run."""
    return service.evaluate_run_gates(run_id, payload)
