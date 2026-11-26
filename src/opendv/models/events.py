"""Audit log, MCP/OCR receipt, checkpoint, control signal, and SSE event models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from opendv.models.diagnostics import ClarificationQuestion, HorizonQuestion, StrictModel

RunStep = Literal["step4", "step6"]
RunEventType = Literal[
    "SESSION_START",
    "STAGE_UPDATE",
    "ATTEMPT_SNAPSHOT",
    "GATE_CHECK_STREAM",
    "THOUGHT_DELTA",
    "TEXT_DELTA",
    "TOOL_CALL",
    "TOOL_RESULT",
    "HOLD_REQUESTED",
    "HOLD_CHECKPOINTED",
    "RUN_COMPLETED",
    "RUN_FAILED",
]


class AuditLogRecord(StrictModel):
    """Tamper-evident project audit log record stored in events_and_receipts."""

    event_type: str
    actor: Literal["human", "agent", "system"]
    step: str
    summary: str
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class ExternalMcpReceipt(StrictModel):
    """Persisted invocation receipt from ExternalMcpGateway."""

    receipt_id: str
    server_id: str
    tool_name: str
    doc_id: str | None = None
    page_number: int | None = None
    args_sha256: str
    content_sha256: str
    masked_excerpt: str


class OcrPageReceipt(StrictModel):
    """Persisted OCR text and digest receipt for a scanned PDF page."""

    receipt_id: str
    doc_id: str
    page_number: int = Field(ge=1)
    ocr_text_masked: str
    content_sha256: str
    ocr_box_count: int = Field(ge=0)


class RunCheckpointRecord(StrictModel):
    """Stateless Hold / Draft checkpoint state persisted in events_and_receipts."""

    run_id: str
    step: RunStep
    phase: str
    status: Literal["HOLD_REQUESTED", "HOLD_CHECKPOINTED", "UNCERTIFIED_DRAFT"]
    last_seq: int = Field(ge=0)
    ontology_rev_id: str | None = None
    sources_fingerprint: str
    pending_questions: list[ClarificationQuestion | HorizonQuestion] = Field(default_factory=list)
    budget_turns_used: int = Field(ge=0)
    budget_tokens_used: int = Field(ge=0)
    token_count_source: Literal["reported", "estimated"]
    resume_payload: dict[str, str | int | bool | list[str]] = Field(default_factory=dict)
    pre_steer_fallback_ref: str | None = None
    prior_v8_verdict_json: str | None = None


class ControlSignalRecord(StrictModel):
    """Persisted run control signal (pause, hint, accept-best, raise-budget, horizon, revert)."""

    signal_id: str
    run_id: str
    action: Literal["pause", "hint", "accept-best", "raise-budget", "horizon-review", "revert"]
    hint: str | None = None
    extra_turns: int | None = None
    extra_tokens: int | None = None
    extra_timeout_seconds: int | None = None
    created_at: str


class RunStreamEvent(StrictModel):
    """Single JSONL / SSE frame written to runs/<run_id>.jsonl."""

    seq: int = Field(ge=1)
    run_id: str
    ts: str
    event_type: RunEventType
    step: RunStep
    payload: dict[str, Any]


class RunEventLogPage(StrictModel):
    """Paginated slice of historical RunStreamEvent records."""

    events: list[RunStreamEvent]
    next_since_seq: int | None = None


__all__ = [
    "AuditLogRecord",
    "ControlSignalRecord",
    "ExternalMcpReceipt",
    "OcrPageReceipt",
    "RunCheckpointRecord",
    "RunEventLogPage",
    "RunEventType",
    "RunStep",
    "RunStreamEvent",
]
