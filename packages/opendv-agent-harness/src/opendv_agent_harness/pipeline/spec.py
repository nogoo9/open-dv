"""Pipeline specifications and execution reports."""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opendv_core.models.contract_v5 import NoMistakeDataVault2ContractV5
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.models.vault_blueprint import VaultBlueprint
from opendv_sources.models.specs import SourceSpec


class PipelineForeignKeyMapping(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    fk_columns: list[str] = Field(default_factory=list)
    fk_column: str | None = None
    target_hub: str
    target_table: str | None = None
    target_columns: list[str] = Field(default_factory=list)
    target_column: str | None = None
    relationship_name: str | None = None


def _normalize_foreign_key_mappings(raw_fks: Sequence[Any]) -> list[PipelineForeignKeyMapping]:
    normalized: list[PipelineForeignKeyMapping] = []
    for item in raw_fks:
        if isinstance(item, PipelineForeignKeyMapping):
            normalized.append(item)
        elif isinstance(item, dict):
            f_col = [str(item["fk_column"])] if item.get("fk_column") else []
            t_col = [str(item["target_column"])] if item.get("target_column") else []
            fk_c = list(item.get("fk_columns") or f_col)
            tgt_c = list(item.get("target_columns") or t_col)
            normalized.append(
                PipelineForeignKeyMapping(
                    fk_columns=fk_c,
                    fk_column=item.get("fk_column"),
                    target_hub=str(item.get("target_hub", "")),
                    target_table=item.get("target_table"),
                    target_columns=tgt_c,
                    target_column=item.get("target_column"),
                    relationship_name=item.get("relationship_name"),
                )
            )
    return normalized


class PipelineSourceFeedInput(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    feed_id: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    table_name: str | None = None
    record_source_code: str | None = None
    target_hub_name: str | None = None
    key_authority_tier: str | None = None
    bkcc_namespace: str | None = None
    timestamp_column: str | None = None
    file_path: str | None = None
    source_path: str | None = None
    data_path: str | None = None
    uri: str | None = None
    source_system: str | None = None
    format: str | None = None
    adapter: str | None = None
    target_hub: str | None = None
    business_keys: list[str] = Field(default_factory=list)
    business_key_columns: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    column_descriptors: list[dict[str, Any]] = Field(default_factory=list)
    column_types: dict[str, str] = Field(default_factory=dict)
    surrogate_key_columns: list[str] = Field(default_factory=list)
    pii_columns: list[str] = Field(default_factory=list)
    foreign_keys: list[PipelineForeignKeyMapping] = Field(default_factory=list)
    foreign_key_mappings: dict[str, str] = Field(default_factory=dict)
    cbc_mapping_hints: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    sampling_policy: dict[str, Any] = Field(default_factory=dict)
    target_dialect: str | None = None
    credentials_ref: str | None = None
    table_selection: Any = None
    namespace: Any = None
    baseline_data_path: str | None = None
    baseline_file_path: str | None = None
    source: SourceSpec | None = None
    baseline_source: SourceSpec | None = None

    @model_validator(mode="after")
    def _reconcile_fields(self) -> PipelineSourceFeedInput:
        self.source_id = self.source_id or self.feed_id
        self.feed_id = self.feed_id or self.source_id
        self.target_hub = self.target_hub or self.target_hub_name
        self.target_hub_name = self.target_hub_name or self.target_hub
        if self.business_key_columns and not self.business_keys:
            self.business_keys = list(self.business_key_columns)
        elif self.business_keys and not self.business_key_columns:
            self.business_key_columns = list(self.business_keys)
        if self.foreign_key_mappings and not self.foreign_keys:
            self.foreign_keys = [
                PipelineForeignKeyMapping(fk_columns=[k], fk_column=k, target_hub=v)
                for k, v in self.foreign_key_mappings.items()
            ]
        elif self.foreign_keys and not self.foreign_key_mappings:
            for fk in self.foreign_keys:
                for col in fk.fk_columns or ([fk.fk_column] if fk.fk_column else []):
                    self.foreign_key_mappings[col] = fk.target_hub
        self.uri = self.uri or self.data_path or self.file_path or self.source_path
        if not self.adapter and self.uri:
            with contextlib.suppress(Exception):
                from opendv_sources.registry.scheme_registry import get_scheme_registry

                self.adapter = get_scheme_registry().resolve_adapter(self.uri)
        self.adapter = self.adapter or "file"
        self.data_path = self.data_path or self.uri
        self.file_path = self.file_path or self.uri
        self.source_name = (
            self.source_name or self.table_name or (Path(self.uri).stem if self.uri else "source")
        )
        self.table_name = self.table_name or self.source_name
        self.source_system = self.source_system or self.table_name or "default_source"
        if self.source is None and self.uri:
            ns, ts = None, None
            with contextlib.suppress(ImportError, ValueError, TypeError):
                from opendv_sources.models.specs import NamespaceRef, TableSelectionSpec

                if self.namespace:
                    ns = (
                        self.namespace
                        if isinstance(self.namespace, NamespaceRef)
                        else NamespaceRef.model_validate(self.namespace)
                    )
                if self.table_selection:
                    ts = (
                        self.table_selection
                        if isinstance(self.table_selection, TableSelectionSpec)
                        else TableSelectionSpec.model_validate(self.table_selection)
                    )
            self.source = SourceSpec(
                uri=self.uri,
                adapter=self.adapter,
                format=self.format,
                table_selection=ts,
                namespace=ns,
                options=dict(self.options),
                credentials_ref=self.credentials_ref,
            )
        return self


class PipelineUnstructuredCandidateInput(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    candidate_id: str | None = None
    extraction_id: str | None = None
    entity_name: str | None = None
    target_hub: str | None = None
    concept_uri: str | None = None
    evidence_text: str | None = None
    confidence_score: float = 1.0
    extracted_business_key: str | None = None
    verbatim_quote: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    page_number: int | None = None
    er_tier: str | None = None
    top2_margin: float | None = None
    candidate_hub_matches: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reconcile_cand(self) -> PipelineUnstructuredCandidateInput:
        self.candidate_id = self.candidate_id or self.extraction_id
        self.extraction_id = self.extraction_id or self.candidate_id
        self.evidence_text = self.evidence_text or self.verbatim_quote
        self.verbatim_quote = self.verbatim_quote or self.evidence_text
        self.entity_name = self.entity_name or self.extracted_business_key
        self.extracted_business_key = self.extracted_business_key or self.entity_name
        return self


class PipelineUnstructuredDocInput(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    doc_id: str | None = None
    document_path: str | None = None
    file_path: str | None = None
    source: Any | None = None
    record_source: str | None = None
    target_hub_name: str | None = None
    link_name: str | None = None
    satellite_name: str | None = None
    content: str | None = None
    mime_type: str = "text/plain"
    target_hub: str = "hub_document"
    candidates: list[PipelineUnstructuredCandidateInput] = Field(default_factory=list)
    extracted_candidates: list[PipelineUnstructuredCandidateInput] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _reconcile_doc(self) -> PipelineUnstructuredDocInput:
        self.document_path = self.document_path or self.file_path
        self.file_path = self.file_path or self.document_path
        if self.extracted_candidates and not self.candidates:
            self.candidates = list(self.extracted_candidates)
        elif self.candidates and not self.extracted_candidates:
            self.extracted_candidates = list(self.candidates)
        return self



class PipelineWaveInput(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    wave_index: int = 0
    wave_name: str | None = None
    wave_type: str = "COLD_START"
    sources: list[PipelineSourceFeedInput] = Field(default_factory=list)
    unstructured_documents: list[PipelineUnstructuredDocInput] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)


class PipelineArtifactRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    artifact_id: str
    name: str
    sha256: str
    path: str
    file_path: str | None = None
    artifact_type: str = "JSON"
    media_type: str | None = None
    stage_id: str | None = None
    byte_length: int | None = None

    @model_validator(mode="after")
    def _sync_paths(self) -> PipelineArtifactRef:
        self.file_path = self.file_path or self.path
        self.path = self.path or self.file_path
        return self


class PipelineStepReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    step_id: str | None = None
    wave_index: int = 0
    wave_name: str = ""
    stage_id: str | None = None
    stage_index: int = 0
    stage_name: str = ""
    persona: str = ""
    model_tier: str = ""
    model_used: str = "symbolic"
    status: str = "PASS"
    verdict: str = "PASS"
    attempt_count: int = 1
    duration_ms: float = 0.0
    summary: str = ""
    artifacts: list[PipelineArtifactRef] = Field(default_factory=list)
    tool_invocations: list[str] = Field(default_factory=list)
    input_artifact_sha256s: list[str] = Field(default_factory=list)
    output_artifact_sha256s: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reconcile_step(self) -> PipelineStepReport:
        self.stage_id = self.stage_id or self.stage_name
        self.stage_name = self.stage_name or self.stage_id
        self.step_id = self.step_id or f"step-w{self.wave_index}-{self.stage_id or self.stage_name}"
        self.summary = self.summary or f"Stage {self.stage_id or self.stage_name} executed."
        return self


class PipelineWaveReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    wave_index: int = 0
    wave_name: str = ""
    wave_type: str = "COLD_START"
    status: str = "COMPLETED"
    verdict: str = "PASS"
    duration_ms: float = 0.0
    sources_profiled: int = 0
    columns_profiled: int = 0
    self_healing_attempts: int = 1
    applied_codemods: list[str] = Field(default_factory=list)
    gate_scorecard: dict[str, str] = Field(
        default_factory=lambda: {
            f"G{i}_{n}": "PASS"
            for i, n in enumerate([
                "JSON_SCHEMA", "ONTOLOGY_AND_GRAIN", "KEY_AND_FK_DISCIPLINE",
                "NETWORKX_BIPARTITE_DAG", "YAGNI_AND_COLUMN_LEDGER", "UNSTRUCTURED_SPAN_GROUNDING",
            ])
        }
    )
    unstructured_tier_counts: dict[str, int] = Field(
        default_factory=lambda: {"Tier-U1": 0, "Tier-U2": 0, "Tier-U3": 0}
    )
    sandbox_summary: dict[str, Any] = Field(default_factory=dict)
    step_reports: list[PipelineStepReport] = Field(default_factory=list)
    artifacts: list[PipelineArtifactRef] = Field(default_factory=list)
    schema_drift_events: list[dict[str, Any]] = Field(default_factory=list)


class DeterministicValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Literal["INPUT_VALIDATION", "OUTPUT_VALIDATION"] = "INPUT_VALIDATION"
    passed: bool = True
    checked_items_count: int = 0
    verified_artifacts_count: int = 0
    verified_events_count: int = 0
    pii_leak_check_passed: bool = True
    cas_integrity_verified: bool = True
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)


class AgenticPipelineReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str
    pipeline_id: str = "default_pipeline"
    project_id: str = "default_project"
    domain_namespace: str = "default"
    status: Literal["COMPLETED", "PAUSED_HOLD", "FAILED"] = "COMPLETED"
    overall_passed: bool = True
    started_at: str = ""
    completed_at: str = ""
    total_duration_ms: float = 0.0
    harness_setup: dict[str, Any] = Field(default_factory=dict)
    input_validation: DeterministicValidationReport | None = None
    output_validation: DeterministicValidationReport | None = None
    wave_reports: list[PipelineWaveReport] = Field(default_factory=list)
    step_reports: list[PipelineStepReport] = Field(default_factory=list)
    intermediate_artifacts: list[PipelineArtifactRef] = Field(default_factory=list)
    final_artifacts: dict[str, PipelineArtifactRef] = Field(default_factory=dict)
    active_holds: list[dict[str, Any]] = Field(default_factory=list)
    resolved_holds: list[dict[str, Any]] = Field(default_factory=list)
    observability_events: list[dict[str, Any]] = Field(default_factory=list)
    fallbacks_applied: list[dict[str, Any]] = Field(default_factory=list)
    markdown_summary: str = ""
    graph_a_sha256: str | None = None
    ontology_version: str | None = None
    parent_sha256: str | None = None
    decision_refs: list[str] = Field(default_factory=list)
    has_initial_contract: bool = False
    initial_contract_path: str | None = None
    wave_id: str | None = None
    lifecycle_stage: str = "COLD_START_N1"
    recommender_errors: list[str] | None = None

    verdict: str = "PASS"
    blueprint: VaultBlueprint | None = None
    artifacts: list[PipelineArtifactRef] = Field(default_factory=list)
    evolution_diff: dict[str, Any] = Field(default_factory=dict)
    inference_plan: dict[str, Any] = Field(default_factory=dict)
    four_zone_sql: dict[str, Any] = Field(default_factory=dict)
    summary_markdown: str = ""


class PipelineInputSpec(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    pipeline_id: str = "default_pipeline"
    project_id: str = "default_project"
    run_id: str | None = None
    display_name: str = "Open-DV Agentic Pipeline Run"
    graph_a_ontology_path: str | None = None
    graph_a_source: Any = None
    assertion_cutoff_ts: str = "2026-12-31 23:59:59"
    output_dir: str | None = None
    unstructured_only: bool = False

    domain_namespace: str = "default"
    blueprint: VaultBlueprint | None = None
    blueprint_path: str | None = None
    blueprint_content: str | None = None
    ontology: GraphAOntology | None = None
    ontology_path: str | None = None
    ontology_content: str | None = None
    initial_contract: NoMistakeDataVault2ContractV5 | None = None
    initial_contract_path: str | None = None
    initial_contract_content: str | None = None
    waves: list[PipelineWaveInput] = Field(default_factory=list)
    sources: list[PipelineSourceFeedInput] = Field(default_factory=list)
    unstructured_documents: list[PipelineUnstructuredDocInput] = Field(default_factory=list)
    target_dialect: str = "duckdb"
    ontology_policy: str = "off"
    dialect: str | None = None

    @classmethod
    def from_file(cls, path: str | Path) -> PipelineInputSpec:
        from opendv_core.schema_registry import load_structured_file

        return cls.model_validate(load_structured_file(Path(path)))

    @model_validator(mode="after")
    def _validate_graph_a_present(self) -> PipelineInputSpec:
        has_ont = bool(
            self.graph_a_source
            or (self.graph_a_ontology_path and str(self.graph_a_ontology_path).strip())
            or self.blueprint or self.blueprint_path or self.blueprint_content
            or self.ontology or self.ontology_path or self.ontology_content
        )
        if not has_ont:
            raise ValueError("Either 'graph_a_source' or 'graph_a_ontology_path' must be provided.")
        return self

    @model_validator(mode="after")
    def _reconcile_single_run_and_waves(self) -> PipelineInputSpec:
        if not self.waves and (self.sources or self.unstructured_documents):
            self.waves = [
                PipelineWaveInput(
                    sources=self.sources,
                    unstructured_documents=self.unstructured_documents,
                )
            ]
        elif self.waves and not self.sources and not self.unstructured_documents:
            self.sources = [s for w in self.waves for s in getattr(w, "sources", [])]
            self.unstructured_documents = [
                u for w in self.waves for u in getattr(w, "unstructured_documents", [])
            ]
        return self


class Stage00OrchestrationPlanOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plan_summary: str = ""
    inferred_action: str = "COLD_START_N1"
    active_stages: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6, 7])
    per_table_plan: dict[str, str] = Field(default_factory=dict)


class Stage01FeedDiscoveryItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_system: str
    table_name: str
    column_count: int = 0
    row_count: int = 0
    primary_keys: list[str] = Field(default_factory=list)
    foreign_keys: list[dict[str, Any]] = Field(default_factory=list)
    column_descriptors: list[dict[str, Any]] = Field(default_factory=list)
    record_source_code: str = ""
    target_hub_name: str = ""
    business_key_columns: list[str] = Field(default_factory=list)
    surrogate_key_columns: list[str] = Field(default_factory=list)
    timestamp_column: str | None = None
    pii_columns: list[str] = Field(default_factory=list)
    key_authority_tier: str = "TIER_1_GLOBAL_STANDARD"
    bkcc_namespace: str = "GLOBAL"
    foreign_key_mappings: dict[str, str] = Field(default_factory=dict)
    cbc_mapping_hints: dict[str, str] = Field(default_factory=dict)


class Stage01SourceDiscoveryOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    discovered_feeds: list[Stage01FeedDiscoveryItem] = Field(default_factory=list)
    profile_summaries: dict[str, Any] = Field(default_factory=dict)


class Stage02UnstructuredDiscoveryOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    doc_id: str = ""
    target_hub_name: str = ""
    link_name: str = ""
    satellite_name: str = ""
    extracted_candidates: list[Any] = Field(default_factory=list)
    discovered_entities: list[str] = Field(default_factory=list)
    spans_extracted: int = 0
    profiled_docs: list[dict[str, Any]] = Field(default_factory=list)


class Stage03ColumnTriageOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    triage_ledger: list[dict[str, Any]] = Field(default_factory=list)
    column_triage_ledger: list[dict[str, Any]] = Field(default_factory=list)
    ontology_deltas_proposed: list[dict[str, Any]] = Field(default_factory=list)


class Stage04ContractSynthesisOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    contract: dict[str, Any] = Field(default_factory=dict)
    evolved_contract: dict[str, Any] | None = None
    drift_findings: list[dict[str, Any]] = Field(default_factory=list)


# Aliases for single-run DTOs
PipelineExecutionSpec = PipelineInputSpec
SourceFeedSpec = PipelineSourceFeedInput
DocSpec = PipelineUnstructuredDocInput

__all__ = [
    "AgenticPipelineReport",
    "DeterministicValidationReport",
    "DocSpec",
    "PipelineArtifactRef",
    "PipelineExecutionSpec",
    "PipelineForeignKeyMapping",
    "PipelineInputSpec",
    "PipelineSourceFeedInput",
    "PipelineStepReport",
    "PipelineUnstructuredCandidateInput",
    "PipelineUnstructuredDocInput",
    "PipelineWaveInput",
    "PipelineWaveReport",
    "SourceFeedSpec",
    "Stage00OrchestrationPlanOutput",
    "Stage01FeedDiscoveryItem",
    "Stage01SourceDiscoveryOutput",
    "Stage02UnstructuredDiscoveryOutput",
    "Stage03ColumnTriageOutput",
    "Stage04ContractSynthesisOutput",
    "_normalize_foreign_key_mappings",
]
