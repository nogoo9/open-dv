from __future__ import annotations

from typing import Any

from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel


class CreateProjectRequest(BaseContractModel):
    """Request DTO to create or initialize a project workspace."""

    project_id: str = Field(..., description="Unique project slug or identifier")
    name: str = Field(..., description="Display name for project")
    description: str | None = Field(default=None, description="Optional project description")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Supplementary project metadata"
    )


class ProjectResponse(BaseContractModel):
    """Response DTO representing project workspace details."""

    project_id: str = Field(..., description="Unique project identifier")
    name: str = Field(..., description="Display name")
    description: str | None = Field(default=None, description="Project description")
    created_at_utc: str = Field(..., description="ISO 8601 UTC creation timestamp")
    runs_count: int = Field(default=0, ge=0, description="Total runs executed within project")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Project metadata")
