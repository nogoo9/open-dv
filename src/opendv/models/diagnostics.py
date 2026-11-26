"""Shared Layer-1 leaf models for diagnostics, citations, questions, and prover reports."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base Pydantic v2 model forbidding unexpected extra fields."""

    model_config = ConfigDict(extra="forbid")


KeyTierEnum = Literal[
    "TIER_1_GLOBAL_STANDARD",
    "TIER_2_ENTERPRISE_SOR",
    "TIER_3_LOCAL_SCOPED",
]

GateId = Literal["ONT", "V1", "V2", "V3", "V4", "V5", "V6", "V6b", "V7", "V8"]
DiagnosticSeverity = Literal["BLOCKER", "ERROR", "WARN", "ADVISORY"]
RealDataCheckStatus = Literal[
    "PASS_FULL",
    "PASS_SAMPLE_BOUNDED",
    "FAIL",
    "SKIPPED_PARTIAL_SAMPLE",
    "DISABLED_BY_USER",
]


class RealDataRuleResult(StrictModel):
    """Result of a single Gate V6 real-data invariant check."""

    status: RealDataCheckStatus
    detail: str
    table_row_counts: dict[str, int] = Field(default_factory=dict)
    consequence: str | None = None


class DocumentCitationRef(StrictModel):
    """Exact page and character-span citation into an unstructured source."""

    doc_id: str
    page_number: int = Field(ge=1)
    verbatim_quote: str
    occurrence: int = Field(default=1, ge=1)
    char_start: int | None = None
    char_end: int | None = None
    content_sha256: str | None = None


class ExternalMcpEvidenceRef(StrictModel):
    """Tamper-evident receipt reference for an external MCP tool invocation."""

    receipt_id: str
    server_id: str
    tool_name: str
    content_sha256: str
    masked_excerpt: str


class ClarificationQuestion(StrictModel):
    """Steward hold clarification question emitted during discovery or synthesis."""

    question_id: str
    concept_name: str
    title: str
    prompt: str
    sample_rows: list[str]
    impact_if_locked: str
    recommended_answer: str | None = None


class HorizonOptionSpec(StrictModel):
    """Selectable option for a Horizon Critic architectural choice."""

    option_id: str
    label: str
    description: str
    implications: str
    operator_hint: str | None = None


class HorizonQuestion(StrictModel):
    """Bucket-B Horizon architectural steering question (`BRK-01..08`)."""

    question_id: str
    brk_code: str
    title: str
    prompt: str
    options: list[HorizonOptionSpec]
    impact_summary: str


class VerifierDiagnostic(StrictModel):
    """Structured diagnostic emitted by ontology or DV verification gates."""

    gate: GateId
    rule_id: str
    severity: DiagnosticSeverity
    entity_name: str
    concept_uri: str | None = None
    waivable: bool = False
    suggested_waiver_id: str | None = None
    message: str
    counterexample_rows: list[dict[str, str | int | float | None]] = Field(default_factory=list)


class EmitterDefectReport(StrictModel):
    """Defect report attached to EMITTER_VERIFICATION_FAILED errors in Step 7."""

    target_id: str
    stage: str
    rule_id: str
    message: str
    sql_or_artifact_snippet: str


class AntiPatternJustification(StrictModel):
    """Audited Steward or V7-accepted justification for a waivable V6b rule."""

    waiver_id: str
    rule_id: str
    entity_name: str
    rationale: str
    actor: Literal["human", "agent"]
    reviewed_by_v7: bool = False


class IRProverReport(StrictModel):
    """Runtime verification result returned by IRRuntimeProver (Gate V6)."""

    dyn_results: dict[str, bool]
    ast_violations: list[VerifierDiagnostic]
    empirical_smells: list[VerifierDiagnostic]
    counterexamples: list[dict[str, str | int | float | None]]
    schema_proof_results: dict[str, bool] = Field(default_factory=dict)
    real_data_proof_results: dict[str, RealDataRuleResult] = Field(default_factory=dict)


__all__ = [
    "AntiPatternJustification",
    "ClarificationQuestion",
    "DiagnosticSeverity",
    "DocumentCitationRef",
    "EmitterDefectReport",
    "ExternalMcpEvidenceRef",
    "GateId",
    "HorizonOptionSpec",
    "HorizonQuestion",
    "IRProverReport",
    "KeyTierEnum",
    "RealDataCheckStatus",
    "RealDataRuleResult",
    "StrictModel",
    "VerifierDiagnostic",
]
