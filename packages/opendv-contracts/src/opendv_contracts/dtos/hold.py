from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel, RunStatus


class HoldAction(StrEnum):
    """Decisions available to human stewards resolving a Captain-Hold."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ANSWER = "ANSWER"


class HoldStatus(StrEnum):
    """State of a Captain-Hold escalation."""

    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"


class HoldOptionCandidate(BaseContractModel):
    """Proposed candidate option available for steward selection."""

    candidate_id: str = Field(..., description="Unique option identifier")
    label: str = Field(..., description="Human-readable candidate description")
    score: float | None = Field(default=None, description="Confidence score")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Associated candidate data")


class HoldRequest(BaseContractModel):
    """Request DTO to query or create a hold."""

    run_id: str = Field(..., description="Run identifier")
    stage_id: str | None = Field(default=None, description="Pipeline stage")
    reason: str | None = Field(default=None, description="Reason for hold query/trigger")


class HoldResponse(BaseContractModel):
    """Descriptor for an active or historical Captain-Hold."""

    hold_id: str = Field(..., description="Hold identifier")
    run_id: str = Field(..., description="Associated run identifier")
    stage_id: str = Field(..., description="Stage where hold was triggered")
    event_id: str = Field(..., description="Triggering event identifier")
    status: HoldStatus = Field(default=HoldStatus.PENDING, description="Current hold status")
    reason: str = Field(..., description="Reason hold was raised")
    candidates: list[HoldOptionCandidate] = Field(
        default_factory=list, description="Available resolution choices"
    )
    created_at_utc: str = Field(..., description="ISO 8601 UTC hold creation timestamp")


class HoldDecisionRequest(BaseContractModel):
    """Steward submission resolving an active hold."""

    run_id: str = Field(..., description="Run identifier")
    hold_id: str = Field(..., description="Hold identifier")
    event_id: str | None = Field(default=None, description="Triggering event ID")
    action: HoldAction = Field(default=HoldAction.APPROVE, description="Steward action verdict")
    selected_candidate_id: str | None = Field(
        default=None, description="Selected candidate option if applicable"
    )
    resolution_notes: str | None = Field(default=None, description="Steward justification notes")
    steward_id: str = Field(default="steward-cli", description="Identity of the acting steward")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Supplementary resolution data"
    )


class HoldDecisionResponse(BaseContractModel):
    """Result returned after applying a steward hold decision."""

    decision_id: str = Field(..., description="Unique decision record identifier")
    hold_id: str = Field(..., description="Resolved hold identifier")
    action: HoldAction = Field(..., description="Action applied")
    status: str = Field(..., description="'ACCEPTED' or 'REJECTED'")
    resumed_run_id: str = Field(..., description="Run identifier resumed")
    next_stage_id: str = Field(..., description="Stage where pipeline resumed")
    next_event_id: str = Field(..., description="Subsequent event emitted")
    updated_run_status: RunStatus = Field(..., description="Updated lifecycle status of the run")
    resolved_at_utc: str = Field(..., description="ISO 8601 UTC resolution timestamp")
