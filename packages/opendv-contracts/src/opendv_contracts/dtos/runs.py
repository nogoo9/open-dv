"""Run creation and status DTOs for Open-DV verification and pipeline runs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from opendv_contracts.dtos.common import (
    BaseContractModel,
    GateScorecardSummary,
    LifecycleStage,
    RunStatus,
)


class CreateRunRequest(BaseContractModel):
    """Request payload to start a verification, compilation,
    and sandbox execution run (docs/08 §5.2).
    """

    project_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$",
        description="Target project identifier",
    )
    harness_provider: Literal[
        "agy",
        "agy-cli",
        "antigravity",
        "claude",
        "claude-cli",
        "claude-code",
    ] = Field(
        default="antigravity",
        description="Execution harness provider",
    )
    model_tier_override: Literal["reasoning", "fast"] | str | None = Field(
        default=None,
        description="Optional model tier override",
    )
    contract_path: str | None = Field(
        default=None,
        description="Legacy contract path",
    )
    pipeline_input_path: str | None = Field(
        default=None,
        description="Path to pipeline input specification",
    )
    harness_config_path: str | None = Field(
        default=None,
        description="Path to harness configuration file",
    )
    max_repair_retries: int = Field(
        default=3,
        ge=0,
        le=5,
        description="Maximum autonomous repair retry iterations",
    )
    run_sandbox_batches: bool = Field(
        default=True,
        description="Whether to run 4-batch adversarial sandbox tests",
    )
    execute_duckdb_sandbox: bool = Field(
        default=True,
        description="Whether to execute DuckDB shadow sandbox queries",
    )
    assertion_cutoff_ts: str = Field(
        default="2026-11-01T23:59:59Z",
        description="Temporal cutoff timestamp for deterministic assertions",
    )
    initial_contract_path: str | None = Field(
        default=None,
        description="Path to initial deployed contract YAML/JSON (Decision D1 / GAP-07)",
    )
    initial_contract_content: str | None = Field(
        default=None,
        max_length=1_000_000,
        description="Inline starting contract YAML or JSON string (alias for initial_contract)",
    )
    initial_contract: str | None = Field(
        default=None,
        max_length=1_000_000,
        description="Inline starting contract YAML or JSON string (Decision D1 / GAP-07)",
    )
    wave_id: str | None = Field(
        default=None,
        description="Target wave identifier (e.g. W0, W1, W2) (GAP-07)",
    )

    @model_validator(mode="after")
    def _validate_target(self) -> CreateRunRequest:
        if self.initial_contract is None and self.initial_contract_content is not None:
            object.__setattr__(self, "initial_contract", self.initial_contract_content)
        elif self.initial_contract_content is None and self.initial_contract is not None:
            object.__setattr__(self, "initial_contract_content", self.initial_contract)
        if self.pipeline_input_path is not None and self.pipeline_input_path.strip() == "":
            object.__setattr__(self, "pipeline_input_path", None)
        if self.harness_config_path is not None and self.harness_config_path.strip() == "":
            object.__setattr__(self, "harness_config_path", None)
        if (
            not self.project_id
            and not self.pipeline_input_path
            and not self.initial_contract
            and not self.initial_contract_path
        ):
            raise ValueError(
                "Either 'project_id', 'pipeline_input_path', or "
                "'initial_contract'/'initial_contract_path' must be provided."
            )
        return self


class ProjectRunDTO(BaseContractModel):
    """Status, scorecard, and metadata descriptor for a platform execution run."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        use_enum_values=False,
    )

    run_id: str = Field(..., description="Unique run identifier")
    project_id: str = Field(..., description="Associated project identifier")
    harness_provider: str = Field(
        default="antigravity",
        description="Harness provider used for execution",
    )
    status: RunStatus = Field(
        default=RunStatus.PENDING,
        description="Current lifecycle status of the run",
    )
    started_at_utc: str = Field(..., description="ISO 8601 UTC start timestamp")
    completed_at_utc: str | None = Field(
        default=None,
        description="ISO 8601 UTC completion timestamp",
    )
    current_stage_id: str = Field(
        default="STAGE_00_ORCHESTRATION",
        description="Current or terminal pipeline stage identifier",
    )
    event_count: int = Field(
        default=0,
        ge=0,
        description="Total observability events emitted during the run",
    )
    gate_scorecard: GateScorecardSummary = Field(
        default_factory=GateScorecardSummary,
        description="6-Gate verification scorecard summary",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_scorecard(cls, data: Any) -> Any:
        if isinstance(data, dict):
            sc = data.get("gate_scorecard")
            if sc is not None and not isinstance(sc, (GateScorecardSummary, dict)):
                if hasattr(sc, "model_dump"):
                    data["gate_scorecard"] = sc.model_dump()
                elif hasattr(sc, "__dict__"):
                    data["gate_scorecard"] = dict(sc.__dict__)
        return data

    active_hold_ids: list[str] = Field(
        default_factory=list,
        description="IDs of currently pending human Steward holds",
    )
    fallbacks_applied: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Log of fallback strategies applied during execution",
    )
    events_sse_url: str = Field(
        ...,
        description="Server-Sent Events endpoint URL for live streaming",
    )
    gates_url: str = Field(
        ...,
        description="REST endpoint URL for gate scorecards",
    )
    sandbox_url: str = Field(
        ...,
        description="REST endpoint URL for sandbox query execution",
    )
    decisions_url: str = Field(
        ...,
        description="REST endpoint URL for Steward hold resolutions",
    )
    has_initial_contract: bool = Field(
        default=False,
        description="Whether an initial contract was provided for expansion (GAP-07)",
    )
    initial_contract_path: str | None = Field(
        default=None,
        description="Path to initial contract if file-backed (GAP-07)",
    )
    wave_id: str | None = Field(
        default=None,
        description="Target wave identifier if restricted (GAP-07)",
    )
    lifecycle_stage: str = Field(
        default=LifecycleStage.COLD_START_N1.value,
        description="Contract lifecycle stage (COLD_START_N1 or MULTI_SOURCE_EXPANSION)",
    )


__all__ = [
    "CreateRunRequest",
    "LifecycleStage",
    "ProjectRunDTO",
]
