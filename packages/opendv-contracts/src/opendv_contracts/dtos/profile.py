from __future__ import annotations

from typing import Any

from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel, RenderedOutput


class ProfileRequest(BaseContractModel):
    """Request DTO for Bronze data profiling and Graph B empirical discovery (Decision D1)."""

    source_path: str | None = Field(
        default=None, description="Local or workspace-relative path to source CSV/data"
    )
    file_content: str | None = Field(
        default=None, description="Inline raw CSV/data payload (Decision D1)"
    )
    client_path: str | None = Field(
        default=None, description="Originating client path for inline uploads (Decision D1)"
    )
    graph_b_path: str | None = Field(
        default=None, description="Path to existing Graph B profile to validate"
    )
    graph_b_content: dict[str, Any] | None = Field(
        default=None, description="Inline Graph B dictionary"
    )
    project_id: str | None = Field(default=None, description="Associated project identifier")
    source_system: str = Field(default="BRONZE_SOURCE", description="Source system business code")
    table_name: str | None = Field(default=None, description="Explicit target table name")
    options: dict[str, Any] = Field(default_factory=dict, description="Profiler tuning parameters")


class ProfileResponse(BaseContractModel):
    """Standardized response from Bronze data profiling (Decisions D6, D7)."""

    status: str = Field(..., description="'PASS' or 'ERROR'")
    profile_id: str = Field(..., description="Unique profile identifier")
    source_system: str = Field(..., description="Sanitized source system identifier")
    table_name: str = Field(..., description="Sanitized table identifier")
    table_count: int = Field(default=0, ge=0, description="Total tables profiled")
    column_count: int = Field(default=0, ge=0, description="Total columns profiled")
    source_path: str | None = Field(default=None, description="Source data location if file-based")
    graph_b_profile: dict[str, Any] | None = Field(
        default=None, description="Normalized Graph B schema descriptor"
    )
    rendered: RenderedOutput | None = Field(default=None, description="Pre-rendered text output")
