from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from opendv_contracts.dtos.common import BaseContractModel, EventType, RunStatus


class PipelineValidateRequest(BaseContractModel):
    """Request DTO for static pipeline configuration validation (Decision D1)."""

    input_spec: dict[str, Any] | None = Field(
        default=None, description="Inline input specification (Decision D1)"
    )
    input_spec_path: str | None = Field(default=None, description="Path to input spec file")
    harness_config: dict[str, Any] | None = Field(
        default=None, description="Inline harness configuration"
    )
    harness_config_path: str | None = Field(
        default=None, description="Path to harness configuration file"
    )
    initial_contract_path: str | None = Field(
        default=None, description="Path to starting contract YAML"
    )
    initial_contract_content: str | None = Field(
        default=None, description="Inline starting contract YAML"
    )
    provider_override: str | None = Field(
        default=None, description="Target harness provider override"
    )
    steward_hold_policy: str | None = Field(default=None, description="Steward hold policy")


class PipelineRunRequest(PipelineValidateRequest):
    """Request DTO to initiate an asynchronous end-to-end pipeline run (Decision D2)."""

    run_id: str | None = Field(default=None, description="Optional predetermined run identifier")
    output_dir: str | None = Field(
        default=None, description="Destination directory for run artifacts"
    )
    dry_run: bool = Field(
        default=False, description="Simulate execution without persisting artifacts"
    )
    config: dict[str, Any] = Field(default_factory=dict, description="Extended execution config")


class PipelineRunHandle(BaseContractModel):
    """Handle returned immediately upon submitting a pipeline run
    (HTTP 202 Accepted, Decision D2).
    """

    run_id: str = Field(..., description="Unique generated run identifier")
    status: RunStatus = Field(default=RunStatus.RUNNING, description="Initial run status")
    created_at_utc: str = Field(..., description="ISO 8601 UTC creation timestamp")
    created_at: str | None = Field(default=None, description="Alias for created_at_utc")
    event_stream_url: str = Field(..., description="SSE endpoint URL for real-time events")
    status_url: str = Field(..., description="REST endpoint URL to query current run status")
    project_id: str | None = Field(
        default=None, description="Optional associated project identifier"
    )
    harness_provider: str | None = Field(default=None, description="Harness provider for the run")

    @model_validator(mode="after")
    def _sync_created_at(self) -> PipelineRunHandle:
        if self.created_at is None:
            object.__setattr__(self, "created_at", self.created_at_utc)
        return self


class PipelineEvent(BaseContractModel):
    """Normative event frame streamed across SSE or recorded in run ledger.
    Note: Under Decision D3, legacy identifiers workbench_view_id and viz_id are omitted outright.
    """

    event_id: str = Field(..., description="Unique event identifier")
    run_id: str = Field(..., description="Associated run identifier")
    timestamp_utc: str = Field(..., description="ISO 8601 UTC event timestamp")
    stage_id: str = Field(..., description="Pipeline stage identifier")
    event_type: EventType = Field(..., description="Event classification")
    payload: dict[str, Any] = Field(default_factory=dict, description="Event data payload")
