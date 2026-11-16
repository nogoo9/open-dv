"""Open-DV Data Vault 2.0 Agentic Modeling Pipeline (Decomposed Architecture).

Conforms to ADR-017, ADR-018, ADR-019, ADR-020, and ADR-021.
Decomposed into 14 single-responsibility modules under opendv_agent_harness/pipeline/,
each measuring strictly < 5,000 tokens (cl100k_base).
"""

from __future__ import annotations

from opendv_agent_harness.models import PipelineStageId
from opendv_agent_harness.pipeline.axi_repair_loop import (
    AxiDvPipelineResult,
    execute_axi_repair_loop,
)
from opendv_agent_harness.pipeline.config import (
    HarnessAuthConfig,
    HarnessSetupConfig,
    PipelineSettings,
)
from opendv_agent_harness.pipeline.contract_evolver import (
    evolve_contract_single_run,
    synthesize_unstructured_first_contract,
)
from opendv_agent_harness.pipeline.contract_synthesizer import (
    _synthesize_cumulative_contract,
    synthesize_ontology_seed_contract,
)
from opendv_agent_harness.pipeline.discovery_tabular import (
    _load_table_into_duckdb,
    _to_source_target,
    discover_tabular_feeds,
)
from opendv_agent_harness.pipeline.discovery_unstructured import (
    _build_profiled_unstructured_doc,
    _compute_candidate_er_metrics,
    _resolve_hub_and_uri,
    _resolve_relationship_uri,
    _slugify,
    discover_unstructured_docs,
)
from opendv_agent_harness.pipeline.mode_detector import (
    DocExecutionActionDTO,
    InferredExecutionPlanDTO,
    TableExecutionActionDTO,
    infer_execution_plan,
    infer_table_action,
)
from opendv_agent_harness.pipeline.ontology_evolution import (
    apply_ontology_deltas,
    propose_concept_delta,
    resolve_ontology_policy,
)
from opendv_agent_harness.pipeline.runner import (
    AgenticVaultPipeline,
    resume_agentic_vault_pipeline,
    run_agentic_vault_pipeline,
)
from opendv_agent_harness.pipeline.sandbox_compiler import (
    compile_and_execute_sandbox,
)
from opendv_agent_harness.pipeline.spec import (
    AgenticPipelineReport,
    DeterministicValidationReport,
    DocSpec,
    PipelineArtifactRef,
    PipelineExecutionSpec,
    PipelineForeignKeyMapping,
    PipelineInputSpec,
    PipelineSourceFeedInput,
    PipelineStepReport,
    PipelineUnstructuredCandidateInput,
    PipelineUnstructuredDocInput,
    PipelineWaveInput,
    PipelineWaveReport,
    SourceFeedSpec,
    Stage00OrchestrationPlanOutput,
    Stage01FeedDiscoveryItem,
    Stage01SourceDiscoveryOutput,
    Stage02UnstructuredDiscoveryOutput,
    Stage03ColumnTriageOutput,
    Stage04ContractSynthesisOutput,
    _normalize_foreign_key_mappings,
)
from opendv_agent_harness.pipeline.stages_agentic import (
    _decode_stage_context,
    _DeterministicStageSynthesizer,
    _encode_stage_context,
    _SymbolicStageSynthesizer,
    synthesize_stage_output_from_prompt,
)
from opendv_agent_harness.pipeline.validation_postflight import (
    advance_blueprint_lineage,
    compute_blueprint_evolution_diff,
    validate_pipeline_outputs,
)
from opendv_agent_harness.pipeline.validation_preflight import (
    _first_non_empty,
    _load_yaml_or_json,
    _validate_path_containment,
    validate_pipeline_inputs,
)
from opendv_core.models.vault_blueprint import VaultBlueprint, create_vault_blueprint

# Backward-compatibility alias
PipelineUnstructuredCandidate = PipelineUnstructuredCandidateInput

__all__ = [
    "AgenticPipelineReport",
    "AgenticVaultPipeline",
    "AxiDvPipelineResult",
    "DeterministicValidationReport",
    "DocExecutionActionDTO",
    "DocSpec",
    "HarnessAuthConfig",
    "HarnessSetupConfig",
    "InferredExecutionPlanDTO",
    "PipelineArtifactRef",
    "PipelineExecutionSpec",
    "PipelineForeignKeyMapping",
    "PipelineInputSpec",
    "PipelineSettings",
    "PipelineSourceFeedInput",
    "PipelineStageId",
    "PipelineStepReport",
    "PipelineUnstructuredCandidate",
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
    "TableExecutionActionDTO",
    "VaultBlueprint",
    "_DeterministicStageSynthesizer",
    "_SymbolicStageSynthesizer",
    "_build_profiled_unstructured_doc",
    "_compute_candidate_er_metrics",
    "_decode_stage_context",
    "_encode_stage_context",
    "_first_non_empty",
    "_load_table_into_duckdb",
    "_load_yaml_or_json",
    "_normalize_foreign_key_mappings",
    "_resolve_hub_and_uri",
    "_resolve_relationship_uri",
    "_slugify",
    "_synthesize_cumulative_contract",
    "_to_source_target",
    "_validate_path_containment",
    "advance_blueprint_lineage",
    "apply_ontology_deltas",
    "compile_and_execute_sandbox",
    "compute_blueprint_evolution_diff",
    "create_vault_blueprint",
    "discover_tabular_feeds",
    "discover_unstructured_docs",
    "evolve_contract_single_run",
    "execute_axi_repair_loop",
    "infer_execution_plan",
    "infer_table_action",
    "propose_concept_delta",
    "resolve_ontology_policy",
    "resume_agentic_vault_pipeline",
    "run_agentic_vault_pipeline",
    "synthesize_ontology_seed_contract",
    "synthesize_stage_output_from_prompt",
    "synthesize_unstructured_first_contract",
    "validate_pipeline_inputs",
    "validate_pipeline_outputs",
]
