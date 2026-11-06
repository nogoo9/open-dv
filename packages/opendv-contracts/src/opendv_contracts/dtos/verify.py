from __future__ import annotations

from typing import Any

from pydantic import Field

from opendv_contracts.dtos.common import (
    BaseContractModel,
    GateScorecardSummary,
    GateVerdict,
    RenderedOutput,
)


class VerifyRequest(BaseContractModel):
    """Request DTO for 6-Gate symbolic verification (Decision D1)."""

    contract_path: str | None = Field(default=None, description="Path to contract YAML")
    contract_content: str | None = Field(
        default=None, description="Inline contract YAML content (Decision D1)"
    )
    graph_a_path: str | None = Field(default=None, description="Path to Graph A ontology YAML")
    graph_a_content: str | None = Field(default=None, description="Inline Graph A YAML content")
    graph_b_path: str | None = Field(default=None, description="Path to Graph B profile JSON")
    graph_b_content: str | dict[str, Any] | None = Field(
        default=None, description="Inline Graph B JSON content"
    )
    prior_contract_path: str | None = Field(
        default=None, description="Path to prior baseline contract for evolution checks"
    )
    prior_contract_content: str | None = Field(
        default=None, description="Inline prior contract content"
    )
    run_id: str | None = Field(default=None, description="Run identifier")
    project_id: str | None = Field(default=None, description="Project identifier")
    rule_filter: list[str] = Field(
        default_factory=list, description="Optional rule subsets to evaluate"
    )
    compiled_sql_models: dict[str, str] | None = Field(
        default=None,
        description="Optional compiled physical SQL models for Gate 6 AST verification",
    )
    compiled_sql: str | None = Field(
        default=None,
        description="Optional raw SQL containing physical models for Gate 6 AST verification",
    )
    apply_codemods: bool = Field(
        default=True,
        description="Whether to apply automated codemods before verification",
    )


class RuleEvaluationDTO(BaseContractModel):
    """Individual rule evaluation result within a gate."""

    rule_id: str = Field(..., description="Rule ID (e.g., DV-COL-001, ALG-A1)")
    gate_id: str = Field(..., description="Gate ID (G0..G5)")
    verdict: GateVerdict = Field(..., description="Rule outcome")
    message: str = Field(..., description="Descriptive violation or compliance message")
    entity_ref: str | None = Field(default=None, description="Target entity/attribute identifier")


# Alias for backward / naming compatibility
GateRuleEvaluation = RuleEvaluationDTO


class VerifyResponse(BaseContractModel):
    """Response DTO containing 6-Gate scorecard, applied codemods,
    and diagnostics (Decisions D6, D7).
    """

    run_id: str = Field(..., description="Run identifier")
    status: str = Field(
        ..., description="Pipeline execution status ('PASS', 'AUTO-FIX', 'REJECT-WITH-TRACE', etc.)"
    )
    verdict: GateVerdict = Field(..., description="Overall synthesized gate verdict")
    overall_passed: bool = Field(
        ..., description="True if verification succeeded or was auto-repaired"
    )
    contract_path: str | None = Field(default=None, description="Contract path if file-based")
    applied_codemods: list[str] = Field(
        default_factory=list, description="List of applied algorithmic codemod IDs"
    )
    diagnostics: list[str] = Field(
        default_factory=list, description="Human/log diagnostic statements"
    )
    evaluations: list[RuleEvaluationDTO] = Field(
        default_factory=list, description="Detailed rule evaluation breakdown"
    )
    gate_scorecard: GateScorecardSummary = Field(
        default_factory=GateScorecardSummary, description="6-Gate status scorecard"
    )
    toon_text: str | None = Field(default=None, description="Rendered TOON trace notation")
    rendered: RenderedOutput | None = Field(default=None, description="Pre-rendered text output")
