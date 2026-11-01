"""Verification run management and SSE observability streaming (`/api/v1/runs`)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from opendv_platform_api.deps import (
    CreateRunRequest,
    PlatformStateContainer,
    ProjectRunDTO,
    RbacRole,
    format_sse_event_stream,
    get_platform_state,
    get_rbac_role,
)

router = APIRouter(prefix="/runs", tags=["runs"])


@router.post("", response_model=ProjectRunDTO, status_code=status.HTTP_201_CREATED)
async def start_run(
    req: CreateRunRequest,
    state: Annotated[PlatformStateContainer, Depends(get_platform_state)],
) -> ProjectRunDTO:
    """Start a verification, compilation, and sandbox execution run."""
    if req.project_id not in state.projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{req.project_id}' not found.",
        )
    return state.create_run(req)


@router.get("", response_model=list[ProjectRunDTO])
async def list_runs(
    state: Annotated[PlatformStateContainer, Depends(get_platform_state)],
    project_id: Annotated[str | None, Query()] = None,
) -> list[ProjectRunDTO]:
    """List all runs, optionally filtered by project_id."""
    runs = list(state.runs.values())
    if project_id is not None:
        runs = [r for r in runs if r.project_id == project_id]
    return runs


@router.get("/{run_id}", response_model=ProjectRunDTO)
async def get_run(
    run_id: str,
    state: Annotated[PlatformStateContainer, Depends(get_platform_state)],
) -> ProjectRunDTO:
    """Return status and gate scorecard for a specific run."""
    run_dto = state.runs.get(run_id)
    if run_dto is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )
    return run_dto


@router.post("/{run_id}/abort", response_model=ProjectRunDTO)
async def abort_run(
    run_id: str,
    state: Annotated[PlatformStateContainer, Depends(get_platform_state)],
) -> ProjectRunDTO:
    """Abort an active run."""
    if run_id not in state.runs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )
    return state.abort_run(run_id)


@router.get("/{run_id}/events")
async def stream_run_events(
    run_id: str,
    state: Annotated[PlatformStateContainer, Depends(get_platform_state)],
    role: Annotated[RbacRole, Depends(get_rbac_role)],
    last_event_id_header: Annotated[
        str | None, Header(alias="Last-Event-ID")
    ] = None,
    last_event_id_query: Annotated[
        str | None, Query(alias="last_event_id")
    ] = None,
) -> StreamingResponse:
    """Server-Sent Events (`text/event-stream`) stream of RBAC-redacted VaultObservabilityEvents."""
    if run_id not in state.runs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )

    effective_last_event_id = last_event_id_header or last_event_id_query
    events = state.run_events.get(run_id, [])

    async def _event_generator() -> AsyncGenerator[str, None]:
        async for frame in format_sse_event_stream(
            events,
            role=role,
            last_event_id=effective_last_event_id,
        ):
            yield frame

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-OpenDV-Effective-Role": role.value,
        },
    )
