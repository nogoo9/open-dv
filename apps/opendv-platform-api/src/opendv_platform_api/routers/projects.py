"""Project registration and lookup endpoints (`/api/v1/projects`)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from opendv_platform_api.deps import (
    CreateProjectRequest,
    PlatformStateContainer,
    ProjectResponse,
    get_platform_state,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def register_project(
    req: CreateProjectRequest,
    state: Annotated[PlatformStateContainer, Depends(get_platform_state)],
) -> ProjectResponse:
    """Register a Data Vault 2.0 project contract and Graph A / Graph B paths."""
    return state.create_project(req)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    state: Annotated[PlatformStateContainer, Depends(get_platform_state)],
) -> list[ProjectResponse]:
    """List all registered Data Vault 2.0 projects."""
    return list(state.projects.values())


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    state: Annotated[PlatformStateContainer, Depends(get_platform_state)],
) -> ProjectResponse:
    """Return metadata for a specific registered project."""
    proj = state.projects.get(project_id)
    if proj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )
    return proj
