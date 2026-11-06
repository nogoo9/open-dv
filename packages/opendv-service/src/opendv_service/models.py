"""Domain DTO models for opendv-service and backward compatibility with platform-api."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opendv_contracts import (
    CreateRunRequest,
    GateScorecardSummary,
    LifecycleStage,
    PipelineRunRequest,
    PipelineValidateRequest,
    ProjectRunDTO,
    SandboxQueryRequest,
)


class RunStatus(StrEnum):
    """Execution lifecycle status of a platform verification or sandbox run (docs/08 §5.2)."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED_HOLD = "PAUSED_HOLD"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


type GateVerdictStatus = Literal[
    "PENDING",
    "PASS",
    "AUTO-FIX",
    "AUTO_REPAIRED",
    "REJECT-WITH-TRACE",
    "ASK-USER",
]


class CreateProjectRequest(BaseModel):
    """Request payload to register a Data Vault 2.0 project (docs/08 §5.2)."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    display_name: str = Field(default="Open-DV Project", min_length=1, max_length=256)
    name: str | None = Field(default=None)
    domain_namespace: str = Field(default="general", min_length=1, max_length=128)
    domain: str | None = Field(default=None)
    graph_a_ontology_path: str | None = Field(default=None)
    graph_a_source: dict[str, Any] | None = Field(default=None)
    bronze_sources: list[str] = Field(default_factory=list)
    contract_path: str | None = Field(default=None)
    canonical_ir_path: str | None = Field(default=None)
    graph_b_path: str | None = Field(default=None)

    @model_validator(mode="after")
    def _sync_aliases(self) -> CreateProjectRequest:
        if self.name and self.display_name == "Open-DV Project":
            self.display_name = self.name
        if self.domain and self.domain_namespace == "general":
            self.domain_namespace = self.domain
        return self


class ProjectResponse(BaseModel):
    """Registered Data Vault 2.0 project descriptor (docs/08 §5.2 & docs/10 §2.3)."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    display_name: str
    domain_namespace: str
    graph_a_sha256: str | None = None
    latest_run_id: str | None = None
    created_at_utc: str
    contract_path: str | None = None
    canonical_ir_path: str | None = None
    graph_a_ontology_path: str | None = None
    graph_b_path: str | None = None


class CaptainHoldDecisionRequest(BaseModel):
    """Human-in-the-loop Steward decision resolving a Tier U3/G3/G4 hold (docs/08 §5.2)."""

    model_config = ConfigDict(extra="forbid")

    event_id: str | None = Field(default=None)
    hold_id: str | None = Field(default=None)
    decision_type: Literal["RESOLVE_CAPTAIN_HOLD"] = "RESOLVE_CAPTAIN_HOLD"
    action: Literal["APPROVE", "REJECT", "ANSWER"] = "APPROVE"
    decision: Literal["APPROVE", "REJECT", "ANSWER"] | None = None
    selected_candidate_id: str | None = None
    resolution_notes: str | None = None
    steward_id: str = "steward-captain"
    steward_rationale: str | None = None

    @model_validator(mode="after")
    def _normalize_fields(self) -> CaptainHoldDecisionRequest:
        if self.decision is not None:
            self.action = self.decision
        if self.steward_rationale and not self.resolution_notes:
            self.resolution_notes = self.steward_rationale
        return self


class CaptainHoldDecisionResponse(BaseModel):
    """Result of applying a human Steward decision to an active hold."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    hold_id: str
    action: str
    status: Literal["ACCEPTED", "REJECTED"]
    resumed_run_id: str
    next_stage_id: str
    next_event_id: str
    updated_run_status: RunStatus
    resolved_at_utc: str


# Aliases for hold decisions
HoldDecisionRequest = CaptainHoldDecisionRequest
HoldDecisionResponse = CaptainHoldDecisionResponse


class SandboxExecutionResponse(BaseModel):
    """Structured response from the ephemeral DuckDB 4-Zone sandbox (docs/08 §5.2)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    query_type: str
    assertion_cutoff_ts: str
    all_batches_passed: bool
    batch_verdicts: dict[str, Literal["PASS", "FAIL"]]
    dynamic_rules_passed: int
    dynamic_rules_total: int = 15
    replay_delta_rows: int = 0
    row_count: int = Field(ge=0)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    query_d10_rows: list[dict[str, Any]] = Field(default_factory=list)
    query_d10b_rows: list[dict[str, Any]] = Field(default_factory=list)


class ArtifactUploadRequest(BaseModel):
    """Request payload for storing a JSON/text artifact in the Content-Addressable Store."""

    model_config = ConfigDict(extra="forbid")

    content: str | dict[str, Any] | list[Any]
    media_type: str = Field(default="application/json")
    artifact_name: str | None = Field(default=None)


class ArtifactMetadataResponse(BaseModel):
    """Response descriptor for a Content-Addressable Store artifact."""

    model_config = ConfigDict(extra="forbid")

    sha256: str = Field(..., pattern=r"^[0-9A-Fa-f]{64}$")
    byte_length: int = Field(ge=0)
    media_type: str = Field(default="application/json")
    artifact_name: str | None = None
    content: Any


__all__ = [
    "ArtifactMetadataResponse",
    "ArtifactUploadRequest",
    "CaptainHoldDecisionRequest",
    "CaptainHoldDecisionResponse",
    "CreateProjectRequest",
    "CreateRunRequest",
    "GateScorecardSummary",
    "GateVerdictStatus",
    "HoldDecisionRequest",
    "HoldDecisionResponse",
    "LifecycleStage",
    "PipelineRunRequest",
    "PipelineValidateRequest",
    "ProjectResponse",
    "ProjectRunDTO",
    "RunStatus",
    "SandboxExecutionResponse",
    "SandboxQueryRequest",
]
