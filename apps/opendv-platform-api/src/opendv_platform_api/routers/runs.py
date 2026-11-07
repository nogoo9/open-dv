"""Verification run management, pipeline triggers, and SSE streaming (`/api/v1/runs`)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.responses import StreamingResponse

from opendv_contracts import PipelineRunHandle, RbacRole, RunArtifactDTO
from opendv_platform_api.deps import get_opendv_service, get_rbac_role
from opendv_service import (
    CreateRunRequest,
    OpenDVService,
    PipelineRunRequest,
    PipelineValidateRequest,
    ProjectRunDTO,
    RunStatus,
)

router = APIRouter(tags=["runs"])


@router.post(
    "/runs",
    response_model=PipelineRunHandle,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_run(
    req: CreateRunRequest,
    response: Response,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> PipelineRunHandle:
    """Start an asynchronous verification, compilation, and sandbox execution run (Decision D2)."""
    handle = await service.create_run_async(req)
    response.headers["Location"] = handle.status_url
    return handle


@router.get("/runs", response_model=list[ProjectRunDTO])
async def list_runs(
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    project_id: Annotated[str | None, Query()] = None,
    status: Annotated[RunStatus | None, Query()] = None,
    stage_id: Annotated[str | None, Query()] = None,
) -> list[ProjectRunDTO]:
    """List all runs, optionally filtered by project_id, status, and stage_id."""
    return service.list_runs(project_id=project_id, status=status, stage_id=stage_id)


@router.get("/runs/{run_id}", response_model=ProjectRunDTO)
async def get_run(
    run_id: str,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> ProjectRunDTO:
    """Return status and gate scorecard for a specific run."""
    return service.get_run(run_id)


@router.get("/runs/{run_id}/artifacts", response_model=list[RunArtifactDTO])
async def list_run_artifacts(
    run_id: str,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    role: Annotated[RbacRole, Depends(get_rbac_role)],
) -> list[RunArtifactDTO]:
    """Retrieve all run-isolated artifacts with lowercase 64-hex CAS hash and redaction."""
    return service.list_run_artifacts(run_id, role=role)


@router.get("/runs/{run_id}/artifacts/{artifact_id}", response_model=RunArtifactDTO)
async def get_run_artifact(
    run_id: str,
    artifact_id: str,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    role: Annotated[RbacRole, Depends(get_rbac_role)],
) -> RunArtifactDTO:
    """Retrieve a run-isolated artifact with lowercase 64-hex CAS hash and RBAC redaction (T6)."""
    return service.get_run_artifact(run_id, artifact_id, role=role)


@router.get("/runs/{run_id}/report")
async def get_run_report(
    run_id: str,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    role: Annotated[RbacRole, Depends(get_rbac_role)],
) -> dict[str, Any]:
    """Return the `AgenticPipelineReport` for `run_id` (or synthesize a summary report)."""
    return service.get_pipeline_report(run_id, role=role)


@router.post("/runs/{run_id}/abort", response_model=ProjectRunDTO)
async def abort_run(
    run_id: str,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> ProjectRunDTO:
    """Abort an active run."""
    return service.abort_run(run_id)


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    role: Annotated[RbacRole, Depends(get_rbac_role)],
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    last_event_id_query: Annotated[str | None, Query(alias="last_event_id")] = None,
) -> StreamingResponse:
    """Server-Sent Events (`text/event-stream`) stream of RBAC-redacted VaultObservabilityEvents."""
    effective_last_event_id = last_event_id_header or last_event_id_query
    # Eagerly verify run exists so unknown run returns 404 ProblemDetail
    # before 200 stream starts (B2)
    service.get_run(run_id)
    event_gen = service.stream_run_events(
        run_id,
        role=role,
        last_event_id=effective_last_event_id,
    )
    return StreamingResponse(
        event_gen,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-OpenDV-Effective-Role": role.value,
        },
    )


@router.post("/pipelines/validate")
async def validate_pipeline(
    req: PipelineValidateRequest,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> dict[str, Any]:
    """Deterministically validate an agentic pipeline input spec and harness config."""
    return service.validate_pipeline(req)


@router.post(
    "/pipelines/runs",
    response_model=PipelineRunHandle,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_pipeline(
    req: PipelineRunRequest,
    response: Response,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> PipelineRunHandle:
    """Initiate an asynchronous end-to-end 8-stage agentic pipeline run (Decision D2)."""
    handle = await service.execute_run_async(req)
    response.headers["Location"] = handle.status_url
    return handle
