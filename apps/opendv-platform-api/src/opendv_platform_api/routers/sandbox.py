"""Ephemeral 4-Zone DuckDB sandbox endpoints (`/api/v1/runs/{run_id}/sandbox`)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, model_validator

from opendv_platform_api.deps import get_opendv_service
from opendv_service import OpenDVService, SandboxExecutionResponse


class SandboxQueryRequest(BaseModel):
    """Request to run a parameterized audit query or 4-Batch sequence in the DuckDB sandbox."""

    model_config = ConfigDict(extra="forbid")

    query_type: Literal[
        "ADVERSARIAL_BATCHES_1_TO_4",
        "QUERY_D10_PIT",
        "QUERY_D10B_VALUATION",
        "QUERY_D10_LP_COMMITMENT_PIT",
        "QUERY_D10B_PORTCO_VALUATION_PIT",
        "RUN_4_BATCHES",
        "VERIFY_DYN_001_015",
    ] = "ADVERSARIAL_BATCHES_1_TO_4"
    query_id: str | None = None
    assertion_cutoff_ts: str = "2026-11-15 23:59:59"

    @model_validator(mode="after")
    def _sync_query_type(self) -> SandboxQueryRequest:
        if self.query_id is not None:
            mapping = {
                "QUERY_D10_LP_COMMITMENT_PIT": "QUERY_D10_PIT",
                "QUERY_D10B_PORTCO_VALUATION_PIT": "QUERY_D10B_VALUATION",
                "RUN_4_BATCHES": "ADVERSARIAL_BATCHES_1_TO_4",
                "VERIFY_DYN_001_015": "VERIFY_DYN_001_015",
                "ADVERSARIAL_BATCHES_1_TO_4": "ADVERSARIAL_BATCHES_1_TO_4",
                "QUERY_D10_PIT": "QUERY_D10_PIT",
                "QUERY_D10B_VALUATION": "QUERY_D10B_VALUATION",
            }
            if self.query_id in mapping:
                self.query_type = mapping[self.query_id]  # type: ignore[assignment]
            else:
                raise ValueError(f"Unsupported sandbox query_id: {self.query_id!r}")
        return self

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
