"""FastAPI router for deterministic pipeline validation and runs.

Routes include `/api/v1/pipelines` and `/api/v1/runs/validate`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from starlette.concurrency import run_in_threadpool

from opendv_contracts import PipelineRunHandle, RbacRole
from opendv_contracts.dtos.pipeline import (
    InferredExecutionPlanDTO,
    PipelineInputDTO,
    PipelineRunRequest,
    PipelineValidateRequest,
)
from opendv_platform_api.deps import get_opendv_service, get_rbac_role
from opendv_service import OpenDVService
from opendv_service.use_cases.pipeline import (
    SingleRunExecutionResultDTO,
)
from opendv_service.use_cases.pipeline import (
    validate_pipeline as validate_pipeline_use_case,
)

router = APIRouter(tags=["pipelines"])


@router.post("/runs/validate", response_model=InferredExecutionPlanDTO)
async def validate_single_run(
    req: PipelineInputDTO | dict[str, Any],
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> InferredExecutionPlanDTO:
    """Pre-flight validate single-run PipelineInputDTO and derive InferredExecutionPlanDTO."""
    plan = await run_in_threadpool(
        validate_pipeline_use_case,
        req,
        state=service.state,
        base_dir=service.workspace_root,
        is_server=True,
    )
    if isinstance(plan, InferredExecutionPlanDTO):
        return plan
    return InferredExecutionPlanDTO.model_validate(plan)


@router.post("/pipelines/validate")
async def validate_pipeline(
    req: PipelineValidateRequest | PipelineInputDTO,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> dict[str, Any] | InferredExecutionPlanDTO:
    """Deterministically validate an agentic pipeline input spec or single-run blueprint."""
    req_dict = (
        req.model_dump(exclude_unset=True)
        if hasattr(req, "model_dump")
        else (dict(req) if isinstance(req, dict) else {})
    )
    is_single_run = not any(
        k in req_dict for k in ("input_spec", "input_spec_content", "input_spec_path")
    ) and any(
        k in req_dict
        for k in (
            "blueprint",
            "blueprint_path",
            "blueprint_content",
            "sources",
            "unstructured_documents",
        )
    )
    if is_single_run:
        return await run_in_threadpool(
            validate_pipeline_use_case,
            req,
            state=service.state,
            base_dir=service.workspace_root,
            is_server=True,
        )
    val_req = (
        req
        if isinstance(req, PipelineValidateRequest)
        else PipelineValidateRequest.model_validate(req_dict)
    )
    return await run_in_threadpool(service.validate_pipeline, val_req)


@router.post(
    "/pipelines/runs",
    response_model=PipelineRunHandle | SingleRunExecutionResultDTO,
    status_code=202,
)
async def run_pipeline(
    req: PipelineInputDTO | PipelineRunRequest,
    response: Response,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    role: Annotated[RbacRole, Depends(get_rbac_role)],
    sync: Annotated[bool, Query()] = False,
) -> PipelineRunHandle | SingleRunExecutionResultDTO | dict[str, Any]:
    """Execute or submit an agentic pipeline run returning HTTP 202 handle."""
    if role == RbacRole.PUBLIC:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Public role cannot trigger pipeline runs.",
        )

    srv_mode = bool(
        getattr(service, "is_server", False)
        or getattr(getattr(service, "settings", None), "is_server", False)
    )
    if srv_mode:
        bad_paths = [
            f
            for f in ("blueprint_path",)
            if getattr(req, f, None)
        ]
        if bad_paths:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Field '{bad_paths[0]}' rejected: server API calls must provide "
                    "inline content or CAS references, not raw server filesystem paths."
                ),
            )

    if sync:
        from opendv_service.use_cases.pipeline import execute_pipeline_run_sync

        response.status_code = status.HTTP_200_OK
        return await run_in_threadpool(
            execute_pipeline_run_sync, service.state, req, role=role
        )
    handle = await service.execute_run_async(req, role=role)
    if hasattr(handle, "status_url"):
        response.headers["Location"] = handle.status_url
    return handle


__all__ = ["router"]
