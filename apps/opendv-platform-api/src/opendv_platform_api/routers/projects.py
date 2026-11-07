"""Project registration and lookup endpoints (`/api/v1/projects`)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from opendv_platform_api.deps import get_opendv_service
from opendv_service import CreateProjectRequest, OpenDVService, ProjectResponse

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def register_project(
    req: CreateProjectRequest,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> ProjectResponse:
    """Register a Data Vault 2.0 project contract and Graph A / Graph B paths."""
    return service.create_project(req)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> list[ProjectResponse]:
    """List all registered Data Vault 2.0 projects."""
    return service.list_projects()


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> ProjectResponse:
    """Return metadata for a specific registered project."""
    return service.get_project(project_id)
