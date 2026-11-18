"""Verification run management, pipeline triggers, and SSE streaming (`/api/v1/runs`)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from opendv_contracts import PipelineRunHandle, RbacRole, RunArtifactDTO
from opendv_contracts.dtos.pipeline import PipelineInputDTO
from opendv_platform_api.deps import get_opendv_service, get_rbac_role
from opendv_service import (
    CreateRunRequest,
    OpenDVService,
    ProjectRunDTO,
    RunStatus,
    redact_event_for_role,
)
from opendv_service.use_cases.pipeline import (
    execute_pipeline_run_sync,
)
from opendv_service.use_cases.pipeline import (
    execute_run_async as execute_run_async_use_case,
)

router = APIRouter(tags=["runs"])
_SERVER_PATH_FIELDS = ("blueprint_path",)


@router.post("/runs", response_model=PipelineRunHandle | dict[str, Any], status_code=202)
async def start_run(
    req: PipelineInputDTO | CreateRunRequest,
    response: Response,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    role: Annotated[RbacRole, Depends(get_rbac_role)],
    sync: Annotated[bool, Query()] = False,
) -> PipelineRunHandle | dict[str, Any]:
    """Start an asynchronous verification run or execute a single-run pipeline."""
    # Reject empty request payloads (R2-M11)
    provided = (
        req.model_dump(exclude_unset=True)
        if hasattr(req, "model_dump")
        else (dict(req) if isinstance(req, dict) else {})
    )
    if not provided or not any(bool(v) for v in provided.values()):
        msg = "Request body cannot be empty: must specify pipeline input or run configuration."
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=msg)

    if role == RbacRole.PUBLIC:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Public role cannot trigger pipeline runs.",
        )

    is_server_mode = getattr(service, "is_server", False) or getattr(
        getattr(service, "settings", None), "is_server", False
    )
    if is_server_mode:
        for path_field in _SERVER_PATH_FIELDS:
            if getattr(req, path_field, None):
                msg = (
                    f"Field '{path_field}' rejected: server API calls must provide "
                    "inline content or CAS references, not raw server filesystem paths."
                )
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)

    is_proj = (
        isinstance(req, PipelineInputDTO)
        and getattr(req, "project_id", None)
        and not (req.sources or req.blueprint or req.blueprint_path or req.blueprint_content)
    )
    if is_proj:
        req = CreateRunRequest(
            project_id=getattr(req, "project_id", "default-project"),
            harness_provider=getattr(req, "harness_provider", "antigravity") or "agy",
        )
        is_single_run = False
    else:
        is_single_run = isinstance(req, PipelineInputDTO) or (
            isinstance(req, CreateRunRequest) and bool(req.blueprint_path or req.blueprint_content)
        )
    if is_single_run:
        single_input = req if isinstance(req, PipelineInputDTO) else PipelineInputDTO(
            blueprint_path=getattr(req, "blueprint_path", None),
            blueprint_content=getattr(req, "blueprint_content", None),
            harness_provider=getattr(req, "harness_provider", "antigravity") or "antigravity",
            target_dialect="duckdb",
        )
        if sync:
            res = await run_in_threadpool(
                execute_pipeline_run_sync, service.state, single_input, role=role
            )
            response.status_code = status.HTTP_200_OK
            res_dict = res if isinstance(res, dict) else res.model_dump(mode="json")
            if isinstance(res_dict, dict) and "observability_events" in res_dict:
                evs = res_dict.get("observability_events", [])
                res_dict["observability_events"] = [redact_event_for_role(ev, role) for ev in evs]
            return res_dict
        handle = await execute_run_async_use_case(
            service.state, single_input, pipeline_runner=service.pipeline_runner
        )
    else:
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
    run_id: str, service: Annotated[OpenDVService, Depends(get_opendv_service)]
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
    run_id: str, service: Annotated[OpenDVService, Depends(get_opendv_service)]
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
    """Server-Sent Events (`text/event-stream`) stream of RBAC-redacted events."""
    effective_last_event_id = last_event_id_header or last_event_id_query
    service.get_run(run_id)
    event_gen = service.stream_run_events(run_id, role=role, last_event_id=effective_last_event_id)
    headers = {
        "Cache-Control": "no-cache", "Connection": "keep-alive",
        "X-Accel-Buffering": "no", "X-OpenDV-Effective-Role": role.value,
    }
    return StreamingResponse(event_gen, media_type="text/event-stream", headers=headers)
