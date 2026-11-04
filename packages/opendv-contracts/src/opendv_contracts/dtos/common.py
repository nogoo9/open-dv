from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class BaseContractModel(BaseModel):
    """Base Pydantic model for all Open-DV contract DTOs."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        use_enum_values=True,
    )


class ContractVersion:
    CURRENT: str = "1.0.0"


class RenderedOutput(BaseContractModel):
    """Pre-rendered presentation text produced by opendv-service
    for consistent CLI output (Decisions D6, D7).
    """

    toon: str | None = Field(default=None, description="Diagnostic TOON notation trace")
    text: str | None = Field(default=None, description="Human-readable terminal summary table")
    summary: str | None = Field(default=None, description="Single-line concise verdict summary")


class RunStatus(StrEnum):
    """Lifecycle status of a pipeline run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED_HOLD = "PAUSED_HOLD"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class EventType(StrEnum):
    """Event classification emitted during pipeline execution."""

    SESSION_START = "SESSION_START"
    STAGE_STARTED = "STAGE_STARTED"
    STAGE_COMPLETED = "STAGE_COMPLETED"
    STAGE_FAILED = "STAGE_FAILED"
    STEP_STARTED = "STEP_STARTED"
    STEP_COMPLETED = "STEP_COMPLETED"
    GATE_EVALUATED = "GATE_EVALUATED"
    GATE_PASSED = "GATE_PASSED"
    GATE_FAILED = "GATE_FAILED"
    STEWARD_HOLD = "STEWARD_HOLD"
    HOLD_DECISION = "HOLD_DECISION"
    SANDBOX_BATCH_COMPLETED = "SANDBOX_BATCH_COMPLETED"
    ARTIFACT_COMMITTED = "ARTIFACT_COMMITTED"
    LOG_MESSAGE = "LOG_MESSAGE"
    TOKEN_DELTA = "TOKEN_DELTA"
    THOUGHT_DELTA = "THOUGHT_DELTA"
    FALLBACK_APPLIED = "FALLBACK_APPLIED"


class RbacRole(StrEnum):
    """5-tier RBAC classification clearance."""

    PUBLIC = "PUBLIC"
    INTERNAL_MODELER = "INTERNAL_MODELER"
    DATA_STEWARD = "DATA_STEWARD"
    AUDITOR_FINANCE = "AUDITOR_FINANCE"
    RESTRICTED_PII_CUSTODIAN = "RESTRICTED_PII_CUSTODIAN"


class GateVerdict(StrEnum):
    """Normative 5-state gate evaluation verdicts."""

    PENDING = "PENDING"
    PASS = "PASS"
    AUTO_FIX = "AUTO-FIX"
    AUTO_REPAIRED = "AUTO_REPAIRED"
    REJECT_WITH_TRACE = "REJECT-WITH-TRACE"
    ASK_USER = "ASK-USER"


class GateScorecardSummary(BaseContractModel):
    """6-Gate verification scorecard summary."""

    gate_0_profile_drift: GateVerdict = Field(
        default=GateVerdict.PASS, description="Gate 0 profile drift verdict"
    )
    gate_1_contract_grain: GateVerdict = Field(
        default=GateVerdict.AUTO_REPAIRED, description="Gate 1 contract grain verdict"
    )
    gate_2_sqlglot_ast: GateVerdict = Field(
        default=GateVerdict.PASS, description="Gate 2 SQLGlot AST verdict"
    )
    gate_3_networkx_dag: GateVerdict = Field(
        default=GateVerdict.PASS, description="Gate 3 NetworkX DAG verdict"
    )
    gate_4_yagni_cost_guard: GateVerdict = Field(
        default=GateVerdict.AUTO_FIX, description="Gate 4 YAGNI cost guard verdict"
    )
    gate_5_duckdb_sandbox: GateVerdict = Field(
        default=GateVerdict.PASS, description="Gate 5 DuckDB sandbox verdict"
    )
