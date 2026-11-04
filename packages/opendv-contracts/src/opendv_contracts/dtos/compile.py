from __future__ import annotations

from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel, RenderedOutput


class CompileRequest(BaseContractModel):
    """Request DTO to compile DV 2.0 contract into Canonical IR & 4-Zone SQL (Decision D1)."""

    contract_path: str | None = Field(default=None, description="Path to contract YAML")
    contract_content: str | None = Field(
        default=None, description="Inline contract YAML content (Decision D1)"
    )
    canonical_ir_path: str | None = Field(
        default=None, description="Optional pre-existing Canonical IR JSON path"
    )
    canonical_ir_content: str | None = Field(
        default=None, description="Inline Canonical IR content"
    )
    run_id: str | None = Field(default=None, description="Associated run identifier")
    project_id: str | None = Field(default=None, description="Associated project identifier")
    include_batch4_restatement: bool = Field(
        default=False, description="Include batch 4 late arriving restatement seed"
    )


class CompileResponse(BaseContractModel):
    """Response DTO containing compiled 4-Zone SQL, zones,
    and topological ordering (Decisions D6, D7).
    """

    run_id: str = Field(..., description="Execution run identifier")
    status: str = Field(default="PASS", description="'PASS' or 'ERROR'")
    contract_path: str | None = Field(default=None, description="Contract path if file-based")
    canonical_ir_path: str | None = Field(
        default=None, description="Canonical IR path if file-based"
    )
    canonical_ir_sha256: str = Field(
        ..., pattern=r"^[0-9A-Fa-f]{64}$", description="SHA-256 of Canonical IR"
    )
    compiled_sql_sha256: str | None = Field(
        default=None, pattern=r"^[0-9A-Fa-f]{64}$", description="SHA-256 of 4-Zone SQL"
    )
    hubs_compiled: int = Field(default=0, ge=0, description="Count of compiled hubs")
    links_compiled: int = Field(default=0, ge=0, description="Count of compiled links")
    satellites_compiled: int = Field(default=0, ge=0, description="Count of compiled satellites")
    topological_load_order: list[str] = Field(
        default_factory=list, description="Topological dependency load order"
    )
    sql_zones: list[str] = Field(
        default_factory=list, description="Descriptive list of SQL compilation zones"
    )
    rendered_sql: dict[str, str] | None = Field(
        default=None, description="Zone-partitioned DDL/DML statements"
    )
    rendered: RenderedOutput | None = Field(default=None, description="Pre-rendered text summary")
