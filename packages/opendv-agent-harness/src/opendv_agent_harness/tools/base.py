"""Base Pydantic input/output models for the 8 canonical Open-DV domain tools.

Conforms to docs/03-verification-and-agentic-architecture.md
and docs/06-explainability-and-observability-spec.md.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from opendv_agent_harness.models import (
    AgentPersona,
    AgentToolDefinition,
    DataClassification,
    ObservabilityVerdict,
    PipelineStageId,
    ToolExecutionContext,
    VizId,
    WorkbenchViewId,
)


class DomainToolInput(BaseModel):
    """Strict base class for domain tool input schemas (`additionalProperties: false`)."""

    model_config = ConfigDict(extra="forbid")


class DomainToolOutput(BaseModel):
    """Strict base class for domain tool output schemas (`additionalProperties: false`)."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 1. profile_source_feed
# ---------------------------------------------------------------------------


class ProfileSourceFeedInput(DomainToolInput):
    """Input model for `profile_source_feed` (`STAGE_01_SCOUT_SOURCE`)."""

    source_system: str = Field(
        min_length=1,
        description="Source system identifier (e.g., 'INVESTRAN_PE_ERP').",
    )
    table_or_path: str = Field(
        default="raw_lp_commitments",
        min_length=1,
        description="Relational table name or Parquet/JSON path to profile.",
    )
    sample_rows: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Optional in-memory sample rows for statistical profiling.",
    )
    baseline_columns: list[str] = Field(
        default_factory=list,
        description=(
            "Optional baseline column names for S_k(t) -> S_k(t+1) schema drift comparison."
        ),
    )


class ProfileSourceFeedOutput(DomainToolOutput):
    """Output model for `profile_source_feed`."""

    source_system: str
    table_or_path: str
    row_count: int = Field(ge=0)
    column_metrics: dict[str, dict[str, float]] = Field(default_factory=dict)
    added_columns: list[str] = Field(default_factory=list)
    dropped_columns: list[str] = Field(default_factory=list)
    drift_status: ObservabilityVerdict = ObservabilityVerdict.PASS
    graph_b_sha256: str = Field(pattern=r"^[0-9A-F]{64}$")


# ---------------------------------------------------------------------------
# 2. extract_pdf_spans
# ---------------------------------------------------------------------------


class ExtractedPdfSpanItem(DomainToolOutput):
    """Single verified character-offset span extracted from an unstructured PDF document."""

    field_name: str = Field(min_length=1)
    extracted_value: str
    verbatim_quote: str
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    span_verified: bool
    er_tier: Literal["Tier-U1", "Tier-U2", "Tier-U3"] = "Tier-U1"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ExtractPdfSpansInput(DomainToolInput):
    """Input model for `extract_pdf_spans` (`STAGE_02_SCOUT_UNSTRUCTURED`)."""

    document_uri: str = Field(
        min_length=1,
        description="URI or filename of the unstructured PDF document.",
    )
    document_text: str = Field(
        default="",
        description="Raw extracted text of the document for span verification.",
    )
    candidate_spans: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Candidate spans to verify via Algorithm A.9b.",
    )
    er_margin: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Entity resolution confidence margin between top-2 hub candidates.",
    )


class ExtractPdfSpansOutput(DomainToolOutput):
    """Output model for `extract_pdf_spans`."""

    document_uri: str
    document_hk_sha256: str = Field(pattern=r"^[0-9A-F]{64}$")
    verified_spans: list[ExtractedPdfSpanItem] = Field(default_factory=list)
    all_spans_verified: bool = True
    er_tier: Literal["Tier-U1", "Tier-U2", "Tier-U3"] = "Tier-U1"
    status: ObservabilityVerdict = ObservabilityVerdict.PASS


# ---------------------------------------------------------------------------
# 3. compile_canonical_ir
# ---------------------------------------------------------------------------


class CompileCanonicalIrInput(DomainToolInput):
    """Input model for `compile_canonical_ir` (`STAGE_07_DETERMINISTIC_COMPILER`)."""

    contract_id: str = Field(
        min_length=1,
        description="Contract identifier to lower into Canonical IR and 4-Zone SQL.",
    )
    target_dialect: str = Field(
        default="duckdb",
        min_length=1,
        description="Target SQL dialect (e.g., 'duckdb', 'snowflake').",
    )
    contract_payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional parsed contract v5 dictionary or IR payload.",
    )


class CompileCanonicalIrOutput(DomainToolOutput):
    """Output model for `compile_canonical_ir`."""

    contract_id: str
    target_dialect: str
    canonical_ir_sha256: str = Field(pattern=r"^[0-9A-F]{64}$")
    compiled_sql_sha256: str = Field(pattern=r"^[0-9A-F]{64}$")
    hub_count: int = Field(default=0, ge=0)
    link_count: int = Field(default=0, ge=0)
    satellite_count: int = Field(default=0, ge=0)
    zones_compiled: list[str] = Field(
        default_factory=lambda: ["ZONE_0", "ZONE_1", "ZONE_2", "ZONE_3", "ZONE_4"]
    )
    status: ObservabilityVerdict = ObservabilityVerdict.PASS


# ---------------------------------------------------------------------------
# 4. run_axi_dv_gates
# ---------------------------------------------------------------------------


class RunAxiDvGatesInput(DomainToolInput):
    """Input model for `run_axi_dv_gates` (`STAGE_05_AXI_DV_STATIC_GATES`)."""

    contract_id: str = Field(
        min_length=1,
        description="Identifier of the contract under verification.",
    )
    gates_to_run: list[str] = Field(
        default_factory=lambda: ["G1", "G2", "G3", "G4", "G5"],
        min_length=1,
        description="Static gates to evaluate.",
    )
    contract_payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional contract dictionary to inspect.",
    )


class RunAxiDvGatesOutput(DomainToolOutput):
    """Output model for `run_axi_dv_gates`."""

    contract_id: str
    overall_verdict: ObservabilityVerdict = ObservabilityVerdict.PASS
    gate_verdicts: dict[str, ObservabilityVerdict] = Field(default_factory=dict)
    rule_ids_evaluated: list[str] = Field(default_factory=list)
    toon_diagnostic_trace: str | None = None


# ---------------------------------------------------------------------------
# 5. apply_ast_codemod
# ---------------------------------------------------------------------------


class ApplyAstCodemodInput(DomainToolInput):
    """Input model for `apply_ast_codemod` (`STAGE_04` / `STAGE_05`)."""

    codemod_id: Literal["ALG-A1", "ALG-A2", "ALG-A3", "ALG-A4", "ALG-A5", "ALG-A9A"] = Field(
        description="Deterministic codemod algorithm identifier.",
    )
    target_entity: str = Field(
        min_length=1,
        description="Target Hub, Link, or Satellite entity name.",
    )
    contract_yaml: str = Field(
        default="",
        description="Input contract YAML or SQL AST snippet before codemod.",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Algorithm-specific parameters (e.g., write_amp_mb_per_day, u_nn).",
    )


class ApplyAstCodemodOutput(DomainToolOutput):
    """Output model for `apply_ast_codemod`."""

    codemod_id: str
    target_entity: str
    applied: bool = True
    verdict: ObservabilityVerdict = ObservabilityVerdict.AUTO_REPAIRED
    contract_yaml_pre_sha256: str = Field(pattern=r"^[0-9A-F]{64}$")
    contract_yaml_post_sha256: str = Field(pattern=r"^[0-9A-F]{64}$")
    patch_diff: str
    action_description: str


# ---------------------------------------------------------------------------
# 6. execute_duckdb_sandbox
# ---------------------------------------------------------------------------


class ExecuteDuckdbSandboxInput(DomainToolInput):
    """Input model for `execute_duckdb_sandbox` (`STAGE_06_AXI_DV_DYNAMIC_SANDBOX`)."""

    sql_bundle: str = Field(
        default="",
        description="Optional DDL/DML SQL statements to execute in ephemeral :memory: DuckDB.",
    )
    batches_to_run: list[int] = Field(
        default_factory=lambda: [1, 2, 3, 4],
        min_length=1,
        description="Adversarial batches (1..4) to execute.",
    )
    assertion_cutoff_ts: str = Field(
        default="2026-11-15 23:59:59",
        min_length=1,
        description="Bi-temporal assertion cutoff timestamp for PIT queries.",
    )


class ExecuteDuckdbSandboxOutput(DomainToolOutput):
    """Output model for `execute_duckdb_sandbox`."""

    all_batches_passed: bool = True
    batch_verdicts: dict[str, Literal["PASS", "FAIL"]] = Field(default_factory=dict)
    dynamic_rules_evaluated: list[str] = Field(default_factory=list)
    replay_delta_rows: int = Field(default=0, ge=0)
    orphan_count: int = Field(default=0, ge=0)
    assertion_cutoff_ts: str
    status: ObservabilityVerdict = ObservabilityVerdict.PASS


# ---------------------------------------------------------------------------
# 7. emit_observability_event
# ---------------------------------------------------------------------------


class EmitObservabilityEventInput(DomainToolInput):
    """Input model for `emit_observability_event` (`STAGE_00`..`STAGE_07`)."""

    headline: str = Field(
        min_length=1,
        max_length=140,
        description="Layer 1 executive story banner headline (<=140 chars, zero raw 64-hex).",
    )
    business_impact: str = Field(
        default="Deterministic Data Vault 2.0 telemetry checkpoint recorded.",
        min_length=1,
        max_length=200,
        description="Layer 1 business impact summary (<=200 chars).",
    )
    status: ObservabilityVerdict = Field(
        default=ObservabilityVerdict.PASS,
        description="Gate or stage verdict pill.",
    )
    stage_id: PipelineStageId | None = Field(
        default=None,
        description="Optional override for pipeline stage ID.",
    )
    workbench_view_id: WorkbenchViewId | None = Field(
        default=None,
        description="Optional override for workbench view ID.",
    )
    viz_id: VizId | None = Field(
        default=None,
        description="Optional override for visualization ID.",
    )
    data_classification: DataClassification = Field(
        default=DataClassification.INTERNAL,
        description="5-tier RBAC data classification.",
    )
    rule_ids_evaluated: list[str] = Field(
        default_factory=lambda: ["DV-STR-001"],
        description="Normative rule IDs evaluated.",
    )
    summary_metrics: dict[str, int | float | str | bool] = Field(
        default_factory=dict,
        description="Layer 2 summary key-value metrics.",
    )
    semantic_hash_pills: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Layer 2 semantic hash pills.",
    )
    remediation: dict[str, Any] | None = Field(
        default=None,
        description="Mandatory remediation object when status is AUTO_REPAIRED.",
    )
    layer3_formal_proof: dict[str, Any] | None = Field(
        default=None,
        description="Optional Layer 3 formal proof drawer payload.",
    )


class EmitObservabilityEventOutput(DomainToolOutput):
    """Output model for `emit_observability_event`."""

    event_id: str
    status: ObservabilityVerdict
    stage_id: PipelineStageId
    workbench_view_id: WorkbenchViewId
    viz_id: VizId
    recorded: bool = True


# ---------------------------------------------------------------------------
# 8. request_steward_decision
# ---------------------------------------------------------------------------


class RequestStewardDecisionInput(DomainToolInput):
    """Input model for `request_steward_decision` (`ASK-USER` / Captain-Hold)."""

    escalation_reason: Literal[
        "TIER_U3_AMBIGUOUS_ENTITY",
        "DV_EVO_003_GRAIN_CONFLICT",
        "UNMAPPED_ONTOLOGY_CONCEPT",
        "MAX_RETRIES_EXHAUSTED",
    ] = Field(
        default="TIER_U3_AMBIGUOUS_ENTITY",
        description="Reason for pausing the pipeline at an ASK-USER Captain-Hold gate.",
    )
    headline: str = Field(
        min_length=1,
        max_length=140,
        description="Layer 1 headline explaining the hold (<=140 chars).",
    )
    business_impact: str = Field(
        default="Pipeline paused at Captain-Hold gate to prevent unverified schema or key binding.",
        min_length=1,
        max_length=200,
        description="Layer 1 business impact of the hold (<=200 chars).",
    )
    candidate_options: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Candidate resolution options presented to the human steward.",
    )
    rule_id: str = Field(
        default="ALG-A9B",
        min_length=1,
        description="Normative rule ID triggering the escalation (e.g., 'ALG-A9B', 'DV-EVO-003').",
    )
    data_classification: DataClassification = Field(
        default=DataClassification.CONFIDENTIAL,
        description="5-tier RBAC data classification of the hold payload.",
    )


class RequestStewardDecisionOutput(DomainToolOutput):
    """Output model for `request_steward_decision`."""

    hold_id: str
    event_id: str
    status: ObservabilityVerdict = ObservabilityVerdict.ASK_USER
    escalation_reason: str
    requires_steward_approval: bool = True


__all__ = [
    "AgentPersona",
    "AgentToolDefinition",
    "ApplyAstCodemodInput",
    "ApplyAstCodemodOutput",
    "CompileCanonicalIrInput",
    "CompileCanonicalIrOutput",
    "DomainToolInput",
    "DomainToolOutput",
    "EmitObservabilityEventInput",
    "EmitObservabilityEventOutput",
    "ExecuteDuckdbSandboxInput",
    "ExecuteDuckdbSandboxOutput",
    "ExtractPdfSpansInput",
    "ExtractPdfSpansOutput",
    "ExtractedPdfSpanItem",
    "ProfileSourceFeedInput",
    "ProfileSourceFeedOutput",
    "RequestStewardDecisionInput",
    "RequestStewardDecisionOutput",
    "RunAxiDvGatesInput",
    "RunAxiDvGatesOutput",
    "ToolExecutionContext",
]


def __getattr__(name: str) -> Any:
    if name in {
        "CANONICAL_TOOL_NAMES",
        "build_default_domain_tool_map",
        "build_default_domain_tools",
        "get_tools_for_persona",
    }:
        from opendv_agent_harness.tools import catalog

        return getattr(catalog, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
