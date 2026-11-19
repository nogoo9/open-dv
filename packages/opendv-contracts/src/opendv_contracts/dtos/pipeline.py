"""Single-run pipeline inputs, validation, run handles, execution plans, and event DTOs.

Conforms to ADR-017, ADR-018, ADR-019, and docs/25-data-driven-single-run-pipeline-design.md.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from opendv_contracts.dtos.blueprint import VaultBlueprintDTO
from opendv_contracts.dtos.common import BaseContractModel, EventType, RunStatus
from opendv_contracts.dtos.sources import SourceFeedDTO


class TableExecutionActionDTO(BaseContractModel):
    """Inferred per-table action within a single-run execution plan."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_system: str = Field(..., description="Source system identifier")
    table_name: str = Field(..., description="Table name within source system")
    action: Literal[
        "GREENFIELD_SYNTHESIS",
        "INCREMENTAL_ONBOARDING",
        "INTRA_SOURCE_DRIFT",
        "NO_OP",
    ] = Field(..., description="Inferred action per 7-row matrix")
    matched_existing_entity: str | None = Field(
        default=None,
        description="Target Hub or Satellite if already deployed in contract",
    )
    drift_findings: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Alg A.9a schema drift findings if table is in existing contract",
    )


class DocExecutionActionDTO(BaseContractModel):
    """Inferred per-document action within a single-run execution plan."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    doc_id: str = Field(..., description="Unique document identifier")
    action: Literal[
        "GROUND_ONTOLOGY_CONCEPT",
        "EXTRACT_SPANS_AND_LINKS",
    ] = Field(default="EXTRACT_SPANS_AND_LINKS")
    target_hub: str = Field(default="hub_document")


class InferredExecutionPlanDTO(BaseContractModel):
    """Data-driven per-table & per-document execution plan (ADR-018 / doc 25 §1.2)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    plan_id: str = Field(..., description="Unique plan identifier")
    primary_action: Literal[
        "COLD_START_N1",
        "VERIFIED_NO_OP",
        "MULTI_SOURCE_EXPANSION",
        "SCHEMA_DRIFT",
        "UNSTRUCTURED_FIRST",
        "HYBRID_PER_SOURCE",
    ] = Field(..., description="Primary classification across the 7-row matrix")
    table_actions: list[TableExecutionActionDTO] = Field(
        default_factory=list,
        description="Per-table actions derived without user wave_type",
    )
    doc_actions: list[DocExecutionActionDTO] = Field(
        default_factory=list,
        description="Per-document extraction actions",
    )
    inferred_at_utc: str = Field(..., description="ISO 8601 UTC inference timestamp")
    explanation: str = Field(
        default="",
        description="Human-readable explanation of why this plan was inferred",
    )
    passed: bool = Field(
        default=True,
        description="Whether pre-flight validation passed",
    )
    diagnostics: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Diagnostic messages or issues identified during pre-flight validation",
    )


class PipelineInputDTO(BaseContractModel):
    """Normative single-run pipeline input specification (ADR-019)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    blueprint: VaultBlueprintDTO | dict[str, Any] | None = Field(
        default=None,
        description="VaultBlueprint envelope (containing ontology + optional contract)",
    )
    blueprint_path: str | None = Field(
        default=None,
        description="Path to vault_blueprint.yaml",
    )
    blueprint_content: str | None = Field(
        default=None,
        max_length=5_000_000,
        description="Inline YAML or JSON string for VaultBlueprint",
    )
    ontology_source: dict[str, Any] | None = Field(
        default=None,
        description="Fallback ontology mapping if blueprint is omitted",
    )
    initial_contract_path: str | None = Field(
        default=None,
        description="Fallback initial contract path if blueprint is omitted",
    )
    initial_contract_content: str | None = Field(
        default=None,
        max_length=5_000_000,
        description="Fallback initial contract content",
    )
    sources: list[SourceFeedDTO] = Field(
        default_factory=list,
        description="Universal multi-connector source feeds",
    )
    unstructured_documents: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Optional unstructured materials (PDF, Markdown, text, notes)",
    )
    target_dialect: str = Field(
        default="duckdb",
        description="Target SQL dialect ('duckdb', 'databricks', etc.)",
    )
    governance_policy: dict[str, Any] = Field(
        default_factory=dict,
        description="Governance settings (ontology delta policy, hold policy, model routing)",
    )
    harness_provider: str = Field(
        default="antigravity",
        description="Execution harness provider ('agy', 'claude', etc.)",
    )
    session_id: str | None = Field(
        default=None,
        description="Optional Ontology Studio session ID linked to this pipeline run",
    )


class PipelineValidateRequest(BaseContractModel):
    """Request DTO for static pipeline configuration validation (Decision D1 / Single-Run)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    blueprint: dict[str, Any] | None = Field(
        default=None,
        description="Inline VaultBlueprint payload (ADR-017)",
    )
    blueprint_content: str | None = Field(
        default=None,
        max_length=5_000_000,
        description="Inline YAML or JSON string for VaultBlueprint",
    )
    blueprint_path: str | None = Field(
        default=None,
        description="Path to VaultBlueprint file",
    )
    sources: list[SourceFeedDTO] | list[dict[str, Any]] = Field(
        default_factory=list,
        description="Universal source feeds",
    )
    unstructured_documents: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Unstructured documents",
    )
    target_dialect: str | None = Field(
        default=None,
        description="Target SQL dialect override",
    )
    input_spec: dict[str, Any] | None = Field(
        default=None,
        description="Legacy inline input specification",
    )
    input_spec_content: str | None = Field(
        default=None,
        max_length=1_000_000,
        description="Legacy inline YAML or JSON string for pipeline input specification",
    )
    input_spec_path: str | None = Field(
        default=None,
        description="Legacy path to input spec file",
    )
    graph_a_source: dict[str, Any] | None = Field(
        default=None,
        description="Legacy pluggable SourceSpec for Graph A ontology",
    )
    harness_config: dict[str, Any] | None = Field(
        default=None,
        description="Inline harness configuration",
    )
    harness_config_path: str | None = Field(
        default=None,
        description="Path to harness configuration file",
    )
    initial_contract_path: str | None = Field(
        default=None,
        description="Path to starting contract YAML",
    )
    initial_contract_content: str | None = Field(
        default=None,
        max_length=1_000_000,
        description="Inline starting contract YAML",
    )
    initial_contract: str | None = Field(
        default=None,
        max_length=1_000_000,
        description="Inline starting contract YAML alias",
    )
    provider_override: str | None = Field(
        default=None,
        description="Target harness provider override",
    )
    steward_hold_policy: str | None = Field(
        default=None,
        description="Steward hold policy",
    )
    unstructured_only: bool = Field(
        default=False,
        description="Enable unstructured-only mode",
    )

    @model_validator(mode="after")
    def _sync_contract_fields(self) -> PipelineValidateRequest:
        if self.initial_contract is None and self.initial_contract_content is not None:
            object.__setattr__(self, "initial_contract", self.initial_contract_content)
        elif self.initial_contract_content is None and self.initial_contract is not None:
            object.__setattr__(self, "initial_contract_content", self.initial_contract)
        return self


class PipelineRunRequest(PipelineValidateRequest):
    """Request DTO to initiate an asynchronous end-to-end pipeline run (Decision D2)."""

    project_id: str | None = Field(
        default=None,
        description="Optional associated project identifier",
    )
    run_id: str | None = Field(
        default=None,
        description="Optional predetermined run identifier",
    )
    output_dir: str | None = Field(
        default=None,
        description="Destination directory for run artifacts",
    )
    dry_run: bool = Field(
        default=False,
        description="Simulate execution without persisting artifacts",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Extended execution config",
    )
    session_id: str | None = Field(
        default=None,
        description="Optional Ontology Studio session ID linked to this pipeline run",
    )


class PipelineRunHandle(BaseContractModel):
    """Handle returned immediately upon submitting a pipeline run (HTTP 202 Accepted)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    run_id: str = Field(..., description="Unique generated run identifier")
    status: RunStatus = Field(default=RunStatus.RUNNING, description="Initial run status")
    created_at_utc: str = Field(..., description="ISO 8601 UTC creation timestamp")
    created_at: str | None = Field(default=None, description="Alias for created_at_utc")
    event_stream_url: str = Field(..., description="SSE endpoint URL for real-time events")
    events_sse_url: str | None = Field(default=None, description="Alias for event_stream_url")
    status_url: str = Field(..., description="REST endpoint URL to query current run status")
    project_id: str | None = Field(
        default=None, description="Optional associated project identifier"
    )
    harness_provider: str | None = Field(default=None, description="Harness provider for the run")
    has_initial_contract: bool = Field(
        default=False, description="Whether an initial contract was provided"
    )
    initial_contract_path: str | None = Field(
        default=None, description="Path to initial contract if file-backed"
    )
    lifecycle_stage: str = Field(
        default="COLD_START_N1",
        description="Lifecycle stage of the run",
    )

    @model_validator(mode="after")
    def _sync_created_at(self) -> PipelineRunHandle:
        if self.created_at is None:
            object.__setattr__(self, "created_at", self.created_at_utc)
        if self.events_sse_url is None:
            object.__setattr__(self, "events_sse_url", self.event_stream_url)
        return self


class PipelineEvent(BaseContractModel):
    """Normative event frame streamed across SSE or recorded in run ledger."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    event_id: str = Field(..., description="Unique event identifier")
    run_id: str = Field(..., description="Associated run identifier")
    timestamp_utc: str = Field(..., description="ISO 8601 UTC event timestamp")
    stage_id: str = Field(..., description="Pipeline stage identifier")
    event_type: EventType = Field(..., description="Event classification")
    payload: dict[str, Any] = Field(default_factory=dict, description="Event data payload")


__all__ = [
    "DocExecutionActionDTO",
    "InferredExecutionPlanDTO",
    "PipelineEvent",
    "PipelineInputDTO",
    "PipelineRunHandle",
    "PipelineRunRequest",
    "PipelineValidateRequest",
    "TableExecutionActionDTO",
]
