"""Ephemeral 4-Zone DuckDB sandbox endpoints (`/api/v1/runs/{run_id}/sandbox`)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from opendv_contracts import SandboxQueryRequest
from opendv_platform_api.deps import get_opendv_service
from opendv_service import OpenDVService, SandboxExecutionResponse

router = APIRouter(prefix="/runs/{run_id}/sandbox", tags=["sandbox"])


@router.get("")
async def get_run_sandbox_state(
    run_id: str,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> dict[str, Any]:
    """Return 4-Zone DuckDB sandbox state and execution history for a run."""
    return service.get_sandbox_state(run_id)


@router.post("", response_model=SandboxExecutionResponse)
async def execute_run_sandbox_query(
    run_id: str,
    req: SandboxQueryRequest,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> SandboxExecutionResponse:
    """Execute a parameterized audit query or 4-Batch sequence in the DuckDB sandbox."""
    return service.execute_run_sandbox_query(run_id, req)
