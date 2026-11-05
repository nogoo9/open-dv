from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from opendv_contracts.dtos.common import BaseContractModel, RenderedOutput


class SandboxQueryType(StrEnum):
    """Supported query types in the DuckDB 4-Zone shadow sandbox."""

    ADVERSARIAL_BATCHES_1_TO_4 = "ADVERSARIAL_BATCHES_1_TO_4"
    QUERY_D10_PIT = "QUERY_D10_PIT"
    QUERY_D10B_VALUATION = "QUERY_D10B_VALUATION"
    VERIFY_DYN_001_015 = "VERIFY_DYN_001_015"
    CUSTOM_SQL = "CUSTOM_SQL"


SANDBOX_QUERY_TYPE_ALIASES: dict[str, SandboxQueryType] = {
    "QUERY_D10_LP_COMMITMENT_PIT": SandboxQueryType.QUERY_D10_PIT,
    "QUERY_D10B_PORTCO_VALUATION_PIT": SandboxQueryType.QUERY_D10B_VALUATION,
    "RUN_4_BATCHES": SandboxQueryType.ADVERSARIAL_BATCHES_1_TO_4,
    "ADVERSARIAL_BATCHES_1_TO_4": SandboxQueryType.ADVERSARIAL_BATCHES_1_TO_4,
    "QUERY_D10_PIT": SandboxQueryType.QUERY_D10_PIT,
    "QUERY_D10B_VALUATION": SandboxQueryType.QUERY_D10B_VALUATION,
    "VERIFY_DYN_001_015": SandboxQueryType.VERIFY_DYN_001_015,
    "CUSTOM_SQL": SandboxQueryType.CUSTOM_SQL,
}


class SandboxColumnMetadata(BaseContractModel):
    """Metadata for a column in a sandbox query result."""

    name: str = Field(..., description="Column name")
    data_type: str = Field(..., description="DuckDB SQL data type")
    nullable: bool = Field(default=True, description="Whether column can contain NULL")


class SandboxQueryRequest(BaseContractModel):
    """Request DTO to execute queries against the 4-Zone DuckDB analytical sandbox (Decision D1)."""

    run_id: str | None = Field(default=None, description="Associated run identifier")
    contract_path: str | None = Field(default=None, description="Contract YAML path")
    contract_content: str | None = Field(
        default=None, description="Inline contract YAML (Decision D1)"
    )
    canonical_ir_path: str | None = Field(default=None, description="Canonical IR path")
    canonical_ir_content: str | None = Field(default=None, description="Inline Canonical IR")
    query_type: SandboxQueryType = Field(
        default=SandboxQueryType.ADVERSARIAL_BATCHES_1_TO_4,
        description="Target query/check sequence",
    )
    query_id: str | None = Field(default=None, description="Legacy query identifier alias")
    assertion_cutoff_ts: str = Field(
        default="2026-11-15 23:59:59", description="Bi-temporal Point-in-Time assertion timestamp"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary query parameter bindings"
    )
    sql: str | None = Field(default=None, description="Custom read-only SQL query")
    max_rows: int = Field(default=1000, ge=1, le=50000, description="Max rows to return")
    timeout_seconds: float = Field(default=30.0, ge=0.5, le=300.0, description="Execution timeout")

    @model_validator(mode="before")
    @classmethod
    def _normalize_query_type(cls, data: Any) -> Any:
        if isinstance(data, dict):
            qid = data.get("query_id")
            if qid:
                if qid in SANDBOX_QUERY_TYPE_ALIASES:
                    data["query_type"] = SANDBOX_QUERY_TYPE_ALIASES[qid]
                else:
                    raise ValueError(f"Unsupported sandbox query_id: {qid!r}")
            elif "query_type" in data and isinstance(data["query_type"], str):
                qt = data["query_type"]
                if qt in SANDBOX_QUERY_TYPE_ALIASES:
                    data["query_type"] = SANDBOX_QUERY_TYPE_ALIASES[qt]
        return data


class SandboxBatchResult(BaseContractModel):
    """Execution result for an individual sandbox batch."""

    batch_number: int = Field(ge=1, le=4, description="Batch sequence number")
    batch_name: str = Field(..., description="Batch descriptor name")
    passed: bool = Field(..., description="Batch passage status")
    row_count: int = Field(default=0, ge=0, description="Total ingested rows")
    delta_rows: int = Field(default=0, ge=0, description="Delta mutated rows")
    error_message: str | None = Field(default=None, description="Batch execution error if failed")


class SandboxQueryResponse(BaseContractModel):
    """Response DTO containing sandbox query results, batch verdicts,
    and dynamic check statuses (Decisions D6, D7).
    """

    run_id: str = Field(..., description="Run identifier")
    query_type: str = Field(..., description="Executed query type")
    assertion_cutoff_ts: str = Field(..., description="Cutoff timestamp applied")
    all_batches_passed: bool = Field(..., description="True if all batches passed assertions")
    batch_verdicts: dict[str, str] = Field(
        default_factory=dict, description="Mapping of batch name to PASS/FAIL"
    )
    batches: list[SandboxBatchResult] = Field(
        default_factory=list, description="Granular batch execution results"
    )
    dynamic_rules_passed: int = Field(default=0, ge=0, description="Dynamic checks passing count")
    dynamic_rules_total: int = Field(default=15, ge=0, description="Total dynamic checks evaluated")
    replay_delta_rows: int = Field(
        default=0, ge=0, description="Idempotent replay delta rows count"
    )
    row_count: int = Field(default=0, ge=0, description="Returned result rows count")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="Primary result rows")
    query_d10_rows: list[dict[str, Any]] = Field(
        default_factory=list, description="Point-in-Time Query D.10 rows"
    )
    query_d10b_rows: list[dict[str, Any]] = Field(
        default_factory=list, description="Valuation Query D.10b rows"
    )
    columns: list[SandboxColumnMetadata] = Field(
        default_factory=list, description="Result column metadata"
    )
    execution_stats: dict[str, Any] = Field(
        default_factory=dict,
        description="Execution statistics (duration_ms, truncated, row_count)",
    )
    rendered: RenderedOutput | None = Field(default=None, description="Pre-rendered text output")


__all__ = [
    "SANDBOX_QUERY_TYPE_ALIASES",
    "SandboxBatchResult",
    "SandboxColumnMetadata",
    "SandboxQueryRequest",
    "SandboxQueryResponse",
    "SandboxQueryType",
]
