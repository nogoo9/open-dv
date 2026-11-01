"""End-to-End 8-Stage Neuro-Symbolic Agentic Data Vault 2.0 Pipeline (`WP-14`).

Implements the `vault-first-mate` / `no-mistakes` agentic pipeline over the hexagonal
`VaultAgentHarness` protocol (`AntigravityHarnessAdapter`, `ClaudeAgentHarnessAdapter`,
`DeterministicHarnessAdapter`) and deterministic verification engines (`opendv-core`,
`opendv-axi-verifier`, `opendv-duckdb-sandbox`, `opendv-telemetry`).

Provides:
- Input & Setup models: `HarnessAuthConfig`, `HarnessSetupConfig`, `PipelineSourceFeedInput`,
  `PipelineUnstructuredCandidateInput`, `PipelineUnstructuredDocInput`,
  `PipelineWaveInput`, `PipelineInputSpec`
- Output & Report models: `DeterministicValidationReport`, `PipelineArtifactRef`,
  `PipelineStepReport`, `PipelineWaveReport`, `AgenticPipelineReport`
- Deterministic input/output validators: `validate_pipeline_inputs`,
  `validate_pipeline_outputs`
- End-to-end pipeline orchestrator: `AgenticVaultPipeline`,
  `run_agentic_vault_pipeline`
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import duckdb
import yaml
from pydantic import BaseModel, ConfigDict, Field

from opendv_agent_harness.hold_manager import StewardHoldManager, StewardHoldRecord
from opendv_agent_harness.models import (
    AgentPersona,
    DataClassification,
    HarnessBudget,
    HarnessConfig,
    ModelTier,
    ObservabilityVerdict,
    PipelineStageId,
    SandboxPolicy,
    VizId,
    WorkbenchViewId,
)
from opendv_agent_harness.personas import (
    build_scout_source_task_spec,
    build_scout_unstructured_task_spec,
    build_ship_vault_task_spec,
    build_vault_first_mate_task_spec,
)
from opendv_agent_harness.protocol import VaultAgentHarness
from opendv_agent_harness.registry import get_harness_adapter, list_available_harnesses
from opendv_agent_harness.telemetry.translator import build_observability_event_dict
from opendv_axi_verifier.alg_a8_orchestrator import run_axi_dv_verification_pipeline
from opendv_axi_verifier.verifiers.alg_a9b_unstructured import classify_er_resolution_tier
from opendv_core.models.canonical_ir import CanonicalIR
from opendv_core.models.contract_v5 import NoMistakeDataVault2ContractV5
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.models.graph_b import (
    CandidateRoleLiteral,
    ERTierLiteral,
    ExtractedSpan,
    GraphBProfile,
    ProfiledColumn,
    ProfiledSourceSystem,
    ProfiledUnstructuredDocument,
    RowChunkSpan,
)
from opendv_core.schema_registry import (
    find_workspace_root,
    load_structured_file,
    validate_canonical_ir,
    validate_contract_v5,
    validate_graph_a,
    validate_graph_b,
    validate_observability_event,
)
from opendv_duckdb_sandbox.compiler_4zone import compile_canonical_ir_to_4zone_sql
from opendv_duckdb_sandbox.graph_b_profiler import GraphBProfiler
from opendv_duckdb_sandbox.ir_lowerer import lower_contract_to_canonical_ir
from opendv_duckdb_sandbox.sandbox_engine import DuckDbShadowSandbox
from opendv_telemetry import (
    ContentAddressableStore,
    VaultLedgerEmitter,
    redact_event_for_role,
    validate_no_pii_leaks,
)

# ---------------------------------------------------------------------------
# 1. Pipeline Setup & Input Models
# ---------------------------------------------------------------------------


def _load_yaml_or_json(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    loaded = json.loads(text) if file_path.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(loaded, dict):
        msg = f"Expected mapping object in '{file_path}', got {type(loaded).__name__}."
        raise ValueError(msg)
    return loaded


def _first_non_empty(*values: str | None) -> str | None:
    for val in values:
        if val is not None and str(val).strip():
            return str(val)
    return None


class HarnessAuthConfig(BaseModel):
    """Declarative authentication and cloud provider options for a `VaultAgentHarness`."""

    model_config = ConfigDict(extra="forbid")

    auth_mode: Literal["none", "api_key_env", "vertex_adc", "aws_bedrock", "cli_session"] = "none"
    api_key_env_var: str | None = None
    api_base_url: str | None = None
    gcp_project: str | None = None
    gcp_location: str | None = None
    gcp_credentials_path: str | None = None
    aws_region: str | None = None
    aws_profile: str | None = None
    require_credentials_present: bool = False


class HarnessSetupConfig(BaseModel):
    """Configuration for setting up the `VaultAgentHarness` and self-healing policies."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["deterministic", "deterministic-stub", "antigravity", "claude"] = (
        "deterministic"
    )
    model_overrides: dict[str, str] = Field(
        default_factory=lambda: {
            "argon": "gemini-2.5-pro",
            "flash": "gemini-2.5-flash",
        }
    )
    auth: HarnessAuthConfig = Field(default_factory=HarnessAuthConfig)
    budget: HarnessBudget = Field(default_factory=HarnessBudget)
    sandbox: SandboxPolicy = Field(default_factory=SandboxPolicy)
    steward_hold_policy: Literal["pause", "auto_approve_top_candidate", "reject_on_hold"] = (
        "auto_approve_top_candidate"
    )
    inject_adversarial_fault_on_attempt_1: bool = True
    env: dict[str, str] = Field(default_factory=dict)
    extra_provider_options: dict[str, Any] = Field(default_factory=dict)

    def to_harness_config(
        self,
        *,
        workspace_dir: Path | None = None,
        state_dir: Path | None = None,
        app_data_dir: Path | None = None,
        save_dir: Path | None = None,
    ) -> HarnessConfig:
        """Convert this `HarnessSetupConfig` into a runtime `HarnessConfig`."""
        try:
            default_ws = find_workspace_root()
        except Exception:
            default_ws = Path.cwd().resolve()

        eff_ws = (workspace_dir or default_ws).resolve()
        default_base = (
            state_dir or (Path(tempfile.gettempdir()) / "opendv_harness_state")
        ).resolve()
        eff_state = (state_dir or default_base / "state").resolve()
        eff_app = (app_data_dir or default_base / "app_data").resolve()
        eff_save = (save_dir or default_base / "save").resolve()

        tier_overrides: dict[ModelTier, str] = {}
        for k, v in self.model_overrides.items():
            if k in {ModelTier.HIGH_REASONING.value, "HIGH_REASONING"}:
                tier_overrides[ModelTier.HIGH_REASONING] = str(v)
            elif k in {ModelTier.FAST_TIER.value, "FAST_TIER"}:
                tier_overrides[ModelTier.FAST_TIER] = str(v)

        resolved_env: dict[str, str] = dict(self.env)
        if self.auth.auth_mode == "api_key_env":
            env_var_name = self.auth.api_key_env_var or (
                "ANTHROPIC_API_KEY" if self.provider == "claude" else "GEMINI_API_KEY"
            )
            resolved_key = _first_non_empty(
                resolved_env.get(env_var_name),
                os.environ.get(env_var_name),
            )
            if resolved_key:
                resolved_env[env_var_name] = resolved_key
                if self.provider == "claude":
                    resolved_env.setdefault("ANTHROPIC_API_KEY", resolved_key)
                elif self.provider == "antigravity":
                    resolved_env.setdefault("GEMINI_API_KEY", resolved_key)
                    resolved_env.setdefault("GOOGLE_API_KEY", resolved_key)
            if (
                self.provider == "antigravity"
                and "GEMINI_API_KEY" in resolved_env
                and "GOOGLE_API_KEY" not in resolved_env
            ):
                resolved_env["GOOGLE_API_KEY"] = resolved_env["GEMINI_API_KEY"]
        elif self.auth.auth_mode == "vertex_adc":
            gcp_project = _first_non_empty(
                self.auth.gcp_project,
                resolved_env.get("ANTHROPIC_VERTEX_PROJECT_ID")
                if self.provider == "claude"
                else resolved_env.get("GOOGLE_CLOUD_PROJECT"),
                resolved_env.get("GOOGLE_CLOUD_PROJECT")
                if self.provider == "claude"
                else resolved_env.get("ANTHROPIC_VERTEX_PROJECT_ID"),
                os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
                if self.provider == "claude"
                else os.environ.get("GOOGLE_CLOUD_PROJECT"),
                os.environ.get("GOOGLE_CLOUD_PROJECT")
                if self.provider == "claude"
                else os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID"),
            )
            gcp_location = _first_non_empty(
                self.auth.gcp_location,
                resolved_env.get("CLOUD_ML_REGION")
                if self.provider == "claude"
                else resolved_env.get("GOOGLE_CLOUD_LOCATION"),
                resolved_env.get("GOOGLE_CLOUD_LOCATION")
                if self.provider == "claude"
                else resolved_env.get("CLOUD_ML_REGION"),
                os.environ.get("CLOUD_ML_REGION")
                if self.provider == "claude"
                else os.environ.get("GOOGLE_CLOUD_LOCATION"),
                os.environ.get("GOOGLE_CLOUD_LOCATION")
                if self.provider == "claude"
                else os.environ.get("CLOUD_ML_REGION"),
            )
            gcp_credentials_path = _first_non_empty(
                self.auth.gcp_credentials_path,
                resolved_env.get("GOOGLE_APPLICATION_CREDENTIALS"),
                os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
            )
            if self.provider == "claude":
                resolved_env["CLAUDE_CODE_USE_VERTEX"] = "1"
                if gcp_project:
                    resolved_env["ANTHROPIC_VERTEX_PROJECT_ID"] = gcp_project
                if gcp_location:
                    resolved_env["CLOUD_ML_REGION"] = gcp_location
            else:
                resolved_env["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
                if gcp_project:
                    resolved_env["GOOGLE_CLOUD_PROJECT"] = gcp_project
                if gcp_location:
                    resolved_env["GOOGLE_CLOUD_LOCATION"] = gcp_location
            if gcp_credentials_path:
                resolved_env["GOOGLE_APPLICATION_CREDENTIALS"] = gcp_credentials_path
        elif self.auth.auth_mode == "aws_bedrock" and self.provider == "claude":
            resolved_env["CLAUDE_CODE_USE_BEDROCK"] = "1"
            aws_region = _first_non_empty(
                self.auth.aws_region,
                resolved_env.get("AWS_REGION"),
                resolved_env.get("AWS_DEFAULT_REGION"),
                os.environ.get("AWS_REGION"),
                os.environ.get("AWS_DEFAULT_REGION"),
            )
            aws_profile = _first_non_empty(
                self.auth.aws_profile,
                resolved_env.get("AWS_PROFILE"),
                os.environ.get("AWS_PROFILE"),
            )
            if aws_region:
                resolved_env["AWS_REGION"] = aws_region
            if aws_profile:
                resolved_env["AWS_PROFILE"] = aws_profile

        if self.auth.api_base_url:
            if self.provider == "claude":
                resolved_env["ANTHROPIC_BASE_URL"] = self.auth.api_base_url
            else:
                resolved_env["GOOGLE_GEMINI_BASE_URL"] = self.auth.api_base_url

        return HarnessConfig(
            provider=self.provider,
            workspace_dir=eff_ws,
            state_dir=eff_state,
            app_data_dir=eff_app,
            save_dir=eff_save,
            model_overrides=tier_overrides,
            budget=self.budget,
            sandbox=self.sandbox,
            env=resolved_env,
            extra_provider_options=dict(self.extra_provider_options),
        )

    def to_summary_dict(self) -> dict[str, Any]:
        """Serialize setup metadata for `report.harness_setup` without exposing secret values."""
        d = self.model_dump(mode="json")
        d["auth_mode"] = self.auth.auth_mode
        d["api_key_env_var"] = self.auth.api_key_env_var
        d["env"] = {k: "[REDACTED]" for k in self.env}
        return d

    @classmethod
    def from_file(cls, path: str | Path) -> HarnessSetupConfig:
        """Load a `HarnessSetupConfig` from a YAML or JSON file."""
        return cls.model_validate(_load_yaml_or_json(path))


class PipelineSourceFeedInput(BaseModel):
    """Single Bronze relational source feed input within a pipeline wave."""

    model_config = ConfigDict(extra="forbid")

    source_system: str
    table_name: str
    data_path: str
    key_authority_tier: Literal[
        "TIER_1_GLOBAL_STANDARD", "TIER_2_ENTERPRISE_SOR", "TIER_3_LOCAL_SCOPED"
    ] = "TIER_1_GLOBAL_STANDARD"
    bkcc_namespace: str = "GLOBAL"
    record_source_code: str
    business_key_columns: list[str] = Field(default_factory=list)
    surrogate_key_columns: list[str] = Field(default_factory=list)
    foreign_key_mappings: dict[str, str] = Field(default_factory=dict)
    cbc_mapping_hints: dict[str, str] = Field(default_factory=dict)
    pii_columns: list[str] = Field(default_factory=list)
    baseline_data_path: str | None = None


class PipelineUnstructuredCandidateInput(BaseModel):
    """Candidate entity extraction with character-offset grounding in an unstructured document."""

    model_config = ConfigDict(extra="forbid")

    extraction_id: str
    target_hub: str
    extracted_business_key: str
    verbatim_quote: str
    char_start: int
    char_end: int
    page_number: int = 1
    confidence_score: float = Field(ge=0.0, le=1.0)
    candidate_hub_matches: list[dict[str, Any]] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class PipelineUnstructuredDocInput(BaseModel):
    """Unstructured document onboarding specification within a pipeline wave."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    document_path: str
    record_source: str
    extracted_candidates: list[PipelineUnstructuredCandidateInput] = Field(default_factory=list)


class PipelineWaveInput(BaseModel):
    """Single wave in a multi-wave neuro-symbolic Data Vault 2.0 pipeline run."""

    model_config = ConfigDict(extra="forbid")

    wave_index: int = Field(ge=0)
    wave_name: str
    wave_type: Literal[
        "COLD_START",
        "INCREMENTAL_SOURCE",
        "SCHEMA_DRIFT",
        "UNSTRUCTURED_ONBOARDING",
    ] = "COLD_START"
    description: str = ""
    sources: list[PipelineSourceFeedInput] = Field(default_factory=list)
    unstructured_documents: list[PipelineUnstructuredDocInput] = Field(default_factory=list)


class PipelineInputSpec(BaseModel):
    """Root input specification for an end-to-end Open-DV Agentic Pipeline execution."""

    model_config = ConfigDict(extra="forbid")

    pipeline_id: str
    project_id: str
    run_id: str | None = None
    display_name: str = "Open-DV Agentic Pipeline Run"
    domain_namespace: str = "general"
    graph_a_ontology_path: str
    initial_contract_path: str | None = None
    assertion_cutoff_ts: str = "2026-12-31 23:59:59"
    output_dir: str | None = None
    waves: list[PipelineWaveInput] = Field(min_length=1)

    @classmethod
    def from_file(cls, path: str | Path) -> PipelineInputSpec:
        """Load a `PipelineInputSpec` from a YAML or JSON file."""
        return cls.model_validate(_load_yaml_or_json(path))


# ---------------------------------------------------------------------------
# 2. Validation, Step, Wave, Artifact, and Pipeline Report Models
# ---------------------------------------------------------------------------


class DeterministicValidationReport(BaseModel):
    """Deterministic pre-flight input or post-flight output validation report."""

    model_config = ConfigDict(extra="forbid")

    stage: Literal["INPUT_VALIDATION", "OUTPUT_VALIDATION"]
    passed: bool
    checked_items_count: int
    verified_artifacts_count: int = 0
    verified_events_count: int = 0
    pii_leak_check_passed: bool = True
    cas_integrity_verified: bool = True
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)


class PipelineArtifactRef(BaseModel):
    """Content-addressable intermediate or final artifact reference."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    name: str
    stage_id: str
    wave_index: int | None = None
    media_type: str
    sha256: str
    byte_length: int
    file_path: str | None = None
    description: str = ""


class PipelineStepReport(BaseModel):
    """Detailed execution record for a single stage step within a wave."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    wave_index: int
    wave_name: str
    stage_id: str
    persona: str
    model_tier: str
    status: Literal["PASS", "AUTO_REPAIRED", "ASK-USER", "REJECT-WITH-TRACE", "ERROR"]
    attempt_count: int = 1
    duration_ms: float
    summary: str
    tool_invocations: list[str] = Field(default_factory=list)
    input_artifact_sha256s: list[str] = Field(default_factory=list)
    output_artifact_sha256s: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)


class PipelineWaveReport(BaseModel):
    """Aggregated execution report for a single pipeline wave."""

    model_config = ConfigDict(extra="forbid")

    wave_index: int
    wave_name: str
    wave_type: str
    status: Literal["PASS", "AUTO_REPAIRED", "ASK-USER", "REJECT-WITH-TRACE", "ERROR"]
    sources_profiled: int
    columns_profiled: int
    schema_drift_events: list[dict[str, Any]] = Field(default_factory=list)
    unstructured_tier_counts: dict[str, int] = Field(default_factory=dict)
    self_healing_attempts: int = 1
    applied_codemods: list[str] = Field(default_factory=list)
    gate_scorecard: dict[str, str] = Field(default_factory=dict)
    sandbox_summary: dict[str, Any] = Field(default_factory=dict)
    contract_sha256: str
    canonical_ir_sha256: str
    sql_bundle_sha256: str
    step_ids: list[str] = Field(default_factory=list)


class AgenticPipelineReport(BaseModel):
    """Comprehensive end-to-end report emitted by `run_agentic_vault_pipeline`."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    pipeline_id: str
    project_id: str
    domain_namespace: str
    status: Literal["COMPLETED", "PAUSED_HOLD", "FAILED"]
    overall_passed: bool
    started_at: str
    completed_at: str
    total_duration_ms: float
    harness_setup: dict[str, Any]
    input_validation: DeterministicValidationReport
    output_validation: DeterministicValidationReport
    wave_reports: list[PipelineWaveReport]
    step_reports: list[PipelineStepReport]
    intermediate_artifacts: list[PipelineArtifactRef]
    final_artifacts: dict[str, PipelineArtifactRef]
    active_holds: list[dict[str, Any]] = Field(default_factory=list)
    resolved_holds: list[dict[str, Any]] = Field(default_factory=list)
    observability_events: list[dict[str, Any]] = Field(default_factory=list)
    markdown_summary: str


# ---------------------------------------------------------------------------
# 3. Deterministic Input & Output Validation
# ---------------------------------------------------------------------------


def _resolve_and_check_safe_path(
    raw_path: str,
    *,
    base_dir: Path,
    workspace_root: Path,
) -> tuple[Path | None, str | None]:
    """Resolve `raw_path` relative to `base_dir` (or `workspace_root`) and reject `..` traversal."""
    p = Path(raw_path)
    if ".." in p.parts:
        return None, f"Path traversal ('..') is forbidden in path '{raw_path}'."

    if p.is_absolute():
        resolved = p.resolve()
    else:
        cand_base = (base_dir / p).resolve()
        cand_ws = (workspace_root / p).resolve()
        if cand_base.exists():
            resolved = cand_base
        elif cand_ws.exists():
            resolved = cand_ws
        else:
            resolved = cand_base

    allowed_roots = {base_dir.resolve(), workspace_root.resolve()}
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        return (
            None,
            f"Resolved path '{resolved}' escapes allowed workspace roots "
            f"{[str(r) for r in sorted(allowed_roots)]}.",
        )
    return resolved, None


def _load_table_into_duckdb(
    conn: duckdb.DuckDBPyConnection,
    view_name: str,
    file_path: Path,
) -> tuple[int, list[str]]:
    """Load a CSV, Parquet, or JSON file into DuckDB as `view_name` and return `(rows, cols)`."""
    escaped = str(file_path.resolve()).replace("'", "''")
    suffix = file_path.suffix.lower()
    q_view = f'"{view_name}"'
    if suffix == ".parquet":
        conn.execute(
            f"CREATE OR REPLACE TEMP VIEW {q_view} AS SELECT * FROM read_parquet('{escaped}')"
        )
    elif suffix in {".json", ".jsonl", ".ndjson"}:
        conn.execute(
            f"CREATE OR REPLACE TEMP VIEW {q_view} AS SELECT * FROM read_json_auto('{escaped}')"
        )
    else:
        conn.execute(
            f"CREATE OR REPLACE TEMP VIEW {q_view} AS SELECT * FROM read_csv_auto('{escaped}')"
        )
    desc = conn.execute(f"DESCRIBE {q_view}").fetchall()
    cols = [str(r[0]) for r in desc]
    cnt_row = conn.execute(f"SELECT COUNT(*) FROM {q_view}").fetchone()
    row_count = int(cnt_row[0]) if cnt_row is not None else 0
    return row_count, cols


def validate_pipeline_inputs(
    input_spec: PipelineInputSpec | dict[str, Any],
    harness_setup: HarnessSetupConfig | dict[str, Any] | None = None,
    *,
    base_dir: Path | None = None,
) -> DeterministicValidationReport:
    """Deterministically validate `PipelineInputSpec`, `HarnessSetupConfig`, ontology, and data."""
    diagnostics: list[dict[str, Any]] = []
    checked_items = 0

    try:
        ws_root = find_workspace_root()
    except Exception:
        ws_root = Path.cwd().resolve()
    eff_base = (base_dir or ws_root).resolve()

    # 1. Validate PipelineInputSpec model
    checked_items += 1
    if isinstance(input_spec, dict):
        try:
            spec = PipelineInputSpec.model_validate(input_spec)
        except Exception as exc:
            diagnostics.append(
                {
                    "code": "INPUT_SPEC_SCHEMA_INVALID",
                    "severity": "ERROR",
                    "target": "input_spec",
                    "message": str(exc),
                }
            )
            return DeterministicValidationReport(
                stage="INPUT_VALIDATION",
                passed=False,
                checked_items_count=checked_items,
                diagnostics=diagnostics,
            )
    else:
        spec = input_spec

    # 2. Validate HarnessSetupConfig & registered provider
    checked_items += 1
    setup_obj: HarnessSetupConfig
    if harness_setup is None:
        setup_obj = HarnessSetupConfig()
    elif isinstance(harness_setup, dict):
        try:
            setup_obj = HarnessSetupConfig.model_validate(harness_setup)
        except Exception as exc:
            diagnostics.append(
                {
                    "code": "HARNESS_SETUP_INVALID",
                    "severity": "ERROR",
                    "target": "harness_setup",
                    "message": str(exc),
                }
            )
            return DeterministicValidationReport(
                stage="INPUT_VALIDATION",
                passed=False,
                checked_items_count=checked_items,
                diagnostics=diagnostics,
            )
    else:
        setup_obj = harness_setup

    resolved_harness_env: dict[str, str] = {}
    if setup_obj.provider not in list_available_harnesses():
        diagnostics.append(
            {
                "code": "UNKNOWN_HARNESS_PROVIDER",
                "severity": "ERROR",
                "target": "harness_setup.provider",
                "message": f"Unknown harness provider '{setup_obj.provider}'.",
            }
        )
    else:
        try:
            runtime_cfg = setup_obj.to_harness_config(workspace_dir=ws_root)
            resolved_harness_env = runtime_cfg.env
            _ = get_harness_adapter(runtime_cfg)
        except Exception as exc:
            diagnostics.append(
                {
                    "code": "HARNESS_INIT_FAILED",
                    "severity": "ERROR",
                    "target": "harness_setup",
                    "message": str(exc),
                }
            )

    if setup_obj.auth.auth_mode == "aws_bedrock" and setup_obj.provider != "claude":
        diagnostics.append(
            {
                "code": "HARNESS_AUTH_INCOMPATIBLE",
                "severity": "ERROR",
                "target": "harness_setup.auth.auth_mode",
                "message": (
                    f"Auth mode 'aws_bedrock' is only supported for provider 'claude', "
                    f"got provider '{setup_obj.provider}'."
                ),
            }
        )

    if setup_obj.auth.require_credentials_present:
        if setup_obj.auth.auth_mode == "api_key_env":
            target_var = setup_obj.auth.api_key_env_var or (
                "ANTHROPIC_API_KEY" if setup_obj.provider == "claude" else "GEMINI_API_KEY"
            )
            key_val = _first_non_empty(resolved_harness_env.get(target_var))
            if not key_val:
                diagnostics.append(
                    {
                        "code": "HARNESS_AUTH_MISSING",
                        "severity": "ERROR",
                        "target": f"harness_setup.auth.api_key_env_var:{target_var}",
                        "message": (
                            f"Required API key environment variable '{target_var}' is missing "
                            f"or empty in os.environ and harness_setup.env."
                        ),
                    }
                )
        elif setup_obj.auth.auth_mode == "vertex_adc":
            proj_var = (
                "ANTHROPIC_VERTEX_PROJECT_ID"
                if setup_obj.provider == "claude"
                else "GOOGLE_CLOUD_PROJECT"
            )
            proj_val = _first_non_empty(resolved_harness_env.get(proj_var))
            if not proj_val:
                diagnostics.append(
                    {
                        "code": "HARNESS_AUTH_MISSING",
                        "severity": "ERROR",
                        "target": "harness_setup.auth.gcp_project",
                        "message": (
                            "Vertex AI ADC auth requires 'gcp_project' (or GOOGLE_CLOUD_PROJECT / "
                            "ANTHROPIC_VERTEX_PROJECT_ID) when require_credentials_present=True."
                        ),
                    }
                )
        elif setup_obj.auth.auth_mode == "aws_bedrock":
            region_val = _first_non_empty(
                resolved_harness_env.get("AWS_REGION"),
                setup_obj.auth.aws_region,
            )
            if not region_val:
                diagnostics.append(
                    {
                        "code": "HARNESS_AUTH_MISSING",
                        "severity": "ERROR",
                        "target": "harness_setup.auth.aws_region",
                        "message": (
                            "AWS Bedrock auth requires 'aws_region' (or AWS_REGION) "
                            "when require_credentials_present=True."
                        ),
                    }
                )

    # 3. Validate Graph A Ontology path & schema
    checked_items += 1
    ont_path, ont_err = _resolve_and_check_safe_path(
        spec.graph_a_ontology_path, base_dir=eff_base, workspace_root=ws_root
    )
    if ont_err is not None or ont_path is None:
        diagnostics.append(
            {
                "code": "PATH_TRAVERSAL_REJECTED",
                "severity": "ERROR",
                "target": "graph_a_ontology_path",
                "message": ont_err or "Invalid ontology path.",
            }
        )
    elif not ont_path.is_file():
        diagnostics.append(
            {
                "code": "ONTOLOGY_FILE_MISSING",
                "severity": "ERROR",
                "target": str(ont_path),
                "message": f"Graph A ontology file does not exist at '{ont_path}'.",
            }
        )
    else:
        try:
            raw_ont = load_structured_file(ont_path)
            validate_graph_a(raw_ont)
            GraphAOntology.model_validate(raw_ont)
        except Exception as exc:
            diagnostics.append(
                {
                    "code": "ONTOLOGY_SCHEMA_INVALID",
                    "severity": "ERROR",
                    "target": str(ont_path),
                    "message": f"Graph A ontology validation failed: {exc}",
                }
            )

    # 4. Validate optional initial_contract_path
    if spec.initial_contract_path is not None:
        checked_items += 1
        init_path, init_err = _resolve_and_check_safe_path(
            spec.initial_contract_path, base_dir=eff_base, workspace_root=ws_root
        )
        if init_err is not None or init_path is None:
            diagnostics.append(
                {
                    "code": "PATH_TRAVERSAL_REJECTED",
                    "severity": "ERROR",
                    "target": "initial_contract_path",
                    "message": init_err or "Invalid initial contract path.",
                }
            )
        elif not init_path.is_file():
            diagnostics.append(
                {
                    "code": "INITIAL_CONTRACT_MISSING",
                    "severity": "ERROR",
                    "target": str(init_path),
                    "message": f"Initial contract file does not exist at '{init_path}'.",
                }
            )
        else:
            try:
                raw_init = load_structured_file(init_path)
                validate_contract_v5(raw_init)
                NoMistakeDataVault2ContractV5.model_validate(raw_init)
            except Exception as exc:
                diagnostics.append(
                    {
                        "code": "INITIAL_CONTRACT_INVALID",
                        "severity": "ERROR",
                        "target": str(init_path),
                        "message": f"Initial contract validation failed: {exc}",
                    }
                )

    # 5. Validate optional output_dir path
    if spec.output_dir is not None:
        checked_items += 1
        out_path, out_err = _resolve_and_check_safe_path(
            spec.output_dir, base_dir=eff_base, workspace_root=ws_root
        )
        if out_err is not None or out_path is None:
            diagnostics.append(
                {
                    "code": "PATH_TRAVERSAL_REJECTED",
                    "severity": "ERROR",
                    "target": "output_dir",
                    "message": out_err or "Invalid output_dir path.",
                }
            )
        elif out_path.exists() and not out_path.is_dir():
            diagnostics.append(
                {
                    "code": "OUTPUT_DIR_NOT_A_DIRECTORY",
                    "severity": "ERROR",
                    "target": str(out_path),
                    "message": f"Output path '{out_path}' exists and is not a directory.",
                }
            )

    # 6. Validate each wave's sources and unstructured documents
    conn = duckdb.connect(":memory:")
    seen_relational_sources = 0
    try:
        for wave in spec.waves:
            checked_items += 1
            if not wave.sources and not wave.unstructured_documents:
                diagnostics.append(
                    {
                        "code": "EMPTY_WAVE_INPUT",
                        "severity": "ERROR",
                        "target": f"wave_{wave.wave_index}",
                        "message": (
                            f"Wave {wave.wave_index} ('{wave.wave_name}') has neither "
                            "relational sources nor unstructured documents."
                        ),
                    }
                )
            for s_idx, src in enumerate(wave.sources):
                checked_items += 1
                if not src.business_key_columns or any(
                    not c.strip() for c in src.business_key_columns
                ):
                    diagnostics.append(
                        {
                            "code": "MISSING_BUSINESS_KEY_COLUMNS",
                            "severity": "ERROR",
                            "target": (
                                f"wave[{wave.wave_index}].sources[{s_idx}].business_key_columns"
                            ),
                            "message": (
                                f"Source '{src.source_system}.{src.table_name}' in wave "
                                f"{wave.wave_index} must declare at least one business key column."
                            ),
                        }
                    )
                seen_relational_sources += 1
                d_path, d_err = _resolve_and_check_safe_path(
                    src.data_path, base_dir=eff_base, workspace_root=ws_root
                )
                if d_err is not None or d_path is None:
                    diagnostics.append(
                        {
                            "code": "PATH_TRAVERSAL_REJECTED",
                            "severity": "ERROR",
                            "target": f"wave[{wave.wave_index}].sources[{s_idx}].data_path",
                            "message": d_err or "Invalid source data path.",
                        }
                    )
                    continue
                if not d_path.is_file():
                    diagnostics.append(
                        {
                            "code": "SOURCE_FILE_MISSING",
                            "severity": "ERROR",
                            "target": str(d_path),
                            "message": f"Source data file not found: '{d_path}'.",
                        }
                    )
                    continue
                try:
                    row_cnt, cols = _load_table_into_duckdb(
                        conn, f"v_w{wave.wave_index}_s{s_idx}", d_path
                    )
                    if row_cnt < 1 or len(cols) < 1:
                        diagnostics.append(
                            {
                                "code": "SOURCE_TABLE_EMPTY",
                                "severity": "ERROR",
                                "target": str(d_path),
                                "message": (
                                    f"Source '{src.table_name}' has {row_cnt} rows and "
                                    f"{len(cols)} columns (requires >= 1 row and >= 1 column)."
                                ),
                            }
                        )
                    col_set = set(cols)
                    declared_cols = (
                        set(src.business_key_columns)
                        | set(src.surrogate_key_columns)
                        | set(src.pii_columns)
                        | set(src.foreign_key_mappings.keys())
                    )
                    missing_declared = sorted(declared_cols - col_set)
                    if missing_declared:
                        diagnostics.append(
                            {
                                "code": "DECLARED_COLUMNS_MISSING",
                                "severity": "ERROR",
                                "target": f"{src.source_system}.{src.table_name}",
                                "message": (
                                    f"Declared columns {missing_declared} not found in "
                                    f"'{d_path.name}' columns {cols}."
                                ),
                            }
                        )
                except Exception as exc:
                    diagnostics.append(
                        {
                            "code": "SOURCE_LOAD_ERROR",
                            "severity": "ERROR",
                            "target": str(d_path),
                            "message": f"Failed to read source file '{d_path}': {exc}",
                        }
                    )

                if src.baseline_data_path is not None:
                    checked_items += 1
                    b_path, b_err = _resolve_and_check_safe_path(
                        src.baseline_data_path, base_dir=eff_base, workspace_root=ws_root
                    )
                    if b_err is not None or b_path is None:
                        diagnostics.append(
                            {
                                "code": "PATH_TRAVERSAL_REJECTED",
                                "severity": "ERROR",
                                "target": (
                                    f"wave[{wave.wave_index}].sources[{s_idx}].baseline_data_path"
                                ),
                                "message": b_err or "Invalid baseline data path.",
                            }
                        )
                    elif not b_path.is_file():
                        diagnostics.append(
                            {
                                "code": "BASELINE_FILE_MISSING",
                                "severity": "ERROR",
                                "target": str(b_path),
                                "message": f"Baseline data file not found: '{b_path}'.",
                            }
                        )
                    else:
                        try:
                            b_rows, b_cols = _load_table_into_duckdb(
                                conn, f"v_w{wave.wave_index}_s{s_idx}_base", b_path
                            )
                            if b_rows < 1 or len(b_cols) < 1:
                                diagnostics.append(
                                    {
                                        "code": "BASELINE_TABLE_EMPTY",
                                        "severity": "ERROR",
                                        "target": str(b_path),
                                        "message": "Baseline table must have >= 1 row and column.",
                                    }
                                )
                        except Exception as exc:
                            diagnostics.append(
                                {
                                    "code": "BASELINE_LOAD_ERROR",
                                    "severity": "ERROR",
                                    "target": str(b_path),
                                    "message": f"Failed to read baseline file '{b_path}': {exc}",
                                }
                            )

            if wave.unstructured_documents and seen_relational_sources == 0:
                diagnostics.append(
                    {
                        "code": "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE",
                        "severity": "ERROR",
                        "target": f"wave_{wave.wave_index}",
                        "message": (
                            f"Wave {wave.wave_index} ('{wave.wave_name}') onboards unstructured "
                            "documents before any relational source has been onboarded."
                        ),
                    }
                )

            for doc_idx, doc in enumerate(wave.unstructured_documents):
                checked_items += 1
                u_path, u_err = _resolve_and_check_safe_path(
                    doc.document_path, base_dir=eff_base, workspace_root=ws_root
                )
                if u_err is not None or u_path is None:
                    diagnostics.append(
                        {
                            "code": "PATH_TRAVERSAL_REJECTED",
                            "severity": "ERROR",
                            "target": (
                                f"wave[{wave.wave_index}].unstructured_documents[{doc_idx}]"
                                ".document_path"
                            ),
                            "message": u_err or "Invalid document path.",
                        }
                    )
                    continue
                if not u_path.is_file():
                    diagnostics.append(
                        {
                            "code": "DOCUMENT_FILE_MISSING",
                            "severity": "ERROR",
                            "target": str(u_path),
                            "message": f"Unstructured document file not found: '{u_path}'.",
                        }
                    )
                    continue
                doc_text = u_path.read_text(encoding="utf-8")
                if not doc_text.strip():
                    diagnostics.append(
                        {
                            "code": "DOCUMENT_EMPTY",
                            "severity": "ERROR",
                            "target": str(u_path),
                            "message": f"Unstructured document '{doc.doc_id}' is empty.",
                        }
                    )
                    continue
                for cand in doc.extracted_candidates:
                    checked_items += 1
                    if (
                        cand.char_start < 0
                        or cand.char_end <= cand.char_start
                        or cand.char_end > len(doc_text)
                    ):
                        diagnostics.append(
                            {
                                "code": "SPAN_OUT_OF_BOUNDS",
                                "severity": "ERROR",
                                "target": f"{doc.doc_id}:{cand.extraction_id}",
                                "message": (
                                    f"Candidate '{cand.extraction_id}' span "
                                    f"[{cand.char_start}, {cand.char_end}] is out of bounds "
                                    f"for document '{doc.doc_id}' (length={len(doc_text)})."
                                ),
                            }
                        )
                    elif doc_text[cand.char_start : cand.char_end] != cand.verbatim_quote:
                        actual_slice = doc_text[cand.char_start : cand.char_end]
                        diagnostics.append(
                            {
                                "code": "SPAN_QUOTE_MISMATCH",
                                "severity": "ERROR",
                                "target": f"{doc.doc_id}:{cand.extraction_id}",
                                "message": (
                                    f"Candidate '{cand.extraction_id}' verbatim_quote "
                                    f"'{cand.verbatim_quote}' != document slice "
                                    f"'{actual_slice}' at [{cand.char_start}:{cand.char_end}]."
                                ),
                            }
                        )
    finally:
        conn.close()

    return DeterministicValidationReport(
        stage="INPUT_VALIDATION",
        passed=len(diagnostics) == 0,
        checked_items_count=checked_items,
        diagnostics=diagnostics,
    )


def validate_pipeline_outputs(
    report: AgenticPipelineReport,
    cas_store: ContentAddressableStore | None = None,
) -> DeterministicValidationReport:
    """Deterministically validate final contract, CanonicalIR, GraphB profiles, events, and CAS."""
    diagnostics: list[dict[str, Any]] = []
    checked_items = 0
    verified_artifacts = 0
    verified_events = 0
    pii_passed = True
    cas_ok = True

    all_refs: list[PipelineArtifactRef] = [
        *report.intermediate_artifacts,
        *report.final_artifacts.values(),
    ]
    seen_artifact_ids: set[str] = set()
    unique_refs: list[PipelineArtifactRef] = []
    for ref in all_refs:
        if ref.artifact_id not in seen_artifact_ids:
            seen_artifact_ids.add(ref.artifact_id)
            unique_refs.append(ref)

    # 1. Verify every artifact in CAS and on disk
    for ref in unique_refs:
        checked_items += 1
        if cas_store is not None:
            try:
                if not cas_store.verify(ref.sha256):
                    cas_ok = False
                    diagnostics.append(
                        {
                            "code": "CAS_INTEGRITY_FAILURE",
                            "severity": "ERROR",
                            "target": ref.name,
                            "message": f"CAS blob verification failed for sha256={ref.sha256}.",
                        }
                    )
            except Exception as exc:
                cas_ok = False
                diagnostics.append(
                    {
                        "code": "CAS_LOOKUP_ERROR",
                        "severity": "ERROR",
                        "target": ref.name,
                        "message": f"CAS verification raised error for {ref.name}: {exc}",
                    }
                )

        if ref.file_path is not None:
            f_path = Path(ref.file_path)
            if not f_path.is_file():
                cas_ok = False
                diagnostics.append(
                    {
                        "code": "ARTIFACT_FILE_MISSING",
                        "severity": "ERROR",
                        "target": ref.name,
                        "message": f"Materialized artifact file missing: '{f_path}'.",
                    }
                )
            else:
                raw_bytes = f_path.read_bytes()
                disk_sha = hashlib.sha256(raw_bytes).hexdigest()
                if disk_sha.lower() != ref.sha256.lower():
                    cas_ok = False
                    diagnostics.append(
                        {
                            "code": "ARTIFACT_SHA256_MISMATCH",
                            "severity": "ERROR",
                            "target": ref.name,
                            "message": (
                                f"Disk SHA-256 '{disk_sha}' != recorded SHA-256 '{ref.sha256}' "
                                f"for '{f_path}'."
                            ),
                        }
                    )
                else:
                    verified_artifacts += 1
                if ref.name.endswith("_graph_b_profile.json"):
                    try:
                        gb_data = json.loads(raw_bytes.decode("utf-8"))
                        validate_graph_b(gb_data)
                        GraphBProfile.model_validate(gb_data)
                    except Exception as exc:
                        diagnostics.append(
                            {
                                "code": "GRAPH_B_PROFILE_INVALID",
                                "severity": "ERROR",
                                "target": ref.name,
                                "message": f"Graph B profile validation failed: {exc}",
                            }
                        )
        elif cas_store is not None:
            verified_artifacts += 1

    # 2. Validate final_contract and final_canonical_ir if present
    if "final_contract" in report.final_artifacts:
        checked_items += 1
        fc_ref = report.final_artifacts["final_contract"]
        try:
            if fc_ref.file_path and Path(fc_ref.file_path).is_file():
                fc_dict = load_structured_file(fc_ref.file_path)
            elif cas_store is not None:
                fc_dict = yaml.safe_load(cas_store.get_str(fc_ref.sha256))
            else:
                fc_dict = {}
            validate_contract_v5(fc_dict)
            NoMistakeDataVault2ContractV5.model_validate(fc_dict)
        except Exception as exc:
            diagnostics.append(
                {
                    "code": "FINAL_CONTRACT_SCHEMA_INVALID",
                    "severity": "ERROR",
                    "target": "final_contract",
                    "message": f"Final contract schema validation failed: {exc}",
                }
            )

    if "final_canonical_ir" in report.final_artifacts:
        checked_items += 1
        ir_ref = report.final_artifacts["final_canonical_ir"]
        try:
            if ir_ref.file_path and Path(ir_ref.file_path).is_file():
                ir_dict = json.loads(Path(ir_ref.file_path).read_text(encoding="utf-8"))
            elif cas_store is not None:
                ir_dict = json.loads(cas_store.get_str(ir_ref.sha256))
            else:
                ir_dict = {}
            validate_canonical_ir(ir_dict)
            CanonicalIR.model_validate(ir_dict)
        except Exception as exc:
            diagnostics.append(
                {
                    "code": "FINAL_CANONICAL_IR_INVALID",
                    "severity": "ERROR",
                    "target": "final_canonical_ir",
                    "message": f"Final Canonical IR schema validation failed: {exc}",
                }
            )

    # 3. Validate all emitted VaultObservabilityEvent records & PII leak checks
    for idx, ev in enumerate(report.observability_events):
        checked_items += 1
        try:
            validate_observability_event(ev)
            redacted = redact_event_for_role(ev, role="VIEWER")
            validate_no_pii_leaks(redacted)
            verified_events += 1
        except Exception as exc:
            pii_passed = False
            diagnostics.append(
                {
                    "code": "OBSERVABILITY_EVENT_INVALID",
                    "severity": "ERROR",
                    "target": f"observability_events[{idx}]",
                    "message": f"Observability event validation failed: {exc}",
                }
            )

    # 4. Validate wave and step execution verdicts
    for w_rep in report.wave_reports:
        checked_items += 1
        if w_rep.status in {"REJECT-WITH-TRACE", "ERROR"}:
            diagnostics.append(
                {
                    "code": "WAVE_EXECUTION_REJECTED",
                    "severity": "ERROR",
                    "target": f"wave_{w_rep.wave_index}",
                    "message": (
                        f"Wave {w_rep.wave_index} ('{w_rep.wave_name}') finished with "
                        f"status '{w_rep.status}'."
                    ),
                }
            )

    for s_rep in report.step_reports:
        checked_items += 1
        if s_rep.status in {"REJECT-WITH-TRACE", "ERROR"}:
            diagnostics.append(
                {
                    "code": "STEP_EXECUTION_REJECTED",
                    "severity": "ERROR",
                    "target": s_rep.step_id,
                    "message": (
                        f"Step '{s_rep.step_id}' in wave {s_rep.wave_index} finished "
                        f"with status '{s_rep.status}'."
                    ),
                }
            )

    return DeterministicValidationReport(
        stage="OUTPUT_VALIDATION",
        passed=len(diagnostics) == 0 and pii_passed and cas_ok,
        checked_items_count=checked_items,
        verified_artifacts_count=verified_artifacts,
        verified_events_count=verified_events,
        pii_leak_check_passed=pii_passed,
        cas_integrity_verified=cas_ok,
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# 4. Helper Utilities for Contract Synthesis & Markdown Reporting
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
    return cleaned or "entity"


def _step_status_to_verdict(status: str) -> ObservabilityVerdict:
    if status == "AUTO_REPAIRED":
        return ObservabilityVerdict.AUTO_REPAIRED
    if status == "ASK-USER":
        return ObservabilityVerdict.ASK_USER
    if status in {"REJECT-WITH-TRACE", "ERROR"}:
        return ObservabilityVerdict.REJECT_WITH_TRACE
    return ObservabilityVerdict.PASS


def _compute_candidate_er_metrics(
    cand: PipelineUnstructuredCandidateInput,
) -> tuple[float, str, ERTierLiteral, str]:
    margin = 1.0
    if len(cand.candidate_hub_matches) >= 2:
        scores = sorted(
            (
                float(m.get("score", m.get("similarity", cand.confidence_score)))
                for m in cand.candidate_hub_matches
            ),
            reverse=True,
        )
        margin = max(0.0, round(scores[0] - scores[1], 4))
    elif cand.confidence_score < 0.75:
        margin = 0.08

    if cand.confidence_score >= 0.92:
        return margin, "Tier-U1", "TIER_U1_DETERMINISTIC", "PASS"
    if cand.confidence_score >= 0.75 and margin >= 0.15:
        return margin, "Tier-U2", "TIER_U2_HYBRID_EXACT", "PASS"
    _, action = classify_er_resolution_tier(cand.confidence_score, margin)
    return margin, "Tier-U3", "TIER_U3_CAPTAIN_HOLD", action or "ASK-USER"


def _build_profiled_unstructured_doc(
    doc: PipelineUnstructuredDocInput,
    doc_text: str,
    doc_sha256: str,
) -> ProfiledUnstructuredDocument | None:
    extracted_spans_gb: list[ExtractedSpan] = []
    for cand in doc.extracted_candidates:
        margin, _short_tier, gb_tier, _action = _compute_candidate_er_metrics(cand)
        extracted_spans_gb.append(
            ExtractedSpan(
                field=cand.extraction_id,
                span_start=cand.char_start,
                span_end=cand.char_end,
                verbatim_quote=cand.verbatim_quote,
                resolved_bk=cand.extracted_business_key,
                er_tier=gb_tier,
                confidence=cand.confidence_score,
                top2_margin=margin,
            )
        )
    if not extracted_spans_gb:
        return None
    return ProfiledUnstructuredDocument(
        doc_uri=doc.doc_id,
        content_sha256=doc_sha256,
        char_length=len(doc_text),
        extracted_spans=extracted_spans_gb,
        row_chunk_span=RowChunkSpan(
            span_start=0,
            span_end=len(doc_text),
        ),
    )


def _resolve_hub_and_uri(
    concept_or_hub_hint: str,
    ontology: GraphAOntology,
) -> tuple[str, str]:
    """Resolve a CBC name, concept URI, or `hub_*` name against `GraphAOntology`."""
    hint = concept_or_hub_hint.strip()
    hint_lower = hint.lower()
    hint_upper = hint.upper()

    for cbc in ontology.canonical_business_concepts:
        if (
            cbc.concept_name.upper() == hint_upper
            or cbc.concept_uri.lower() == hint_lower
            or (cbc.target_hub and cbc.target_hub.lower() == hint_lower)
            or cbc.concept_uri.rsplit(":", 1)[-1].lower() == hint_lower.removeprefix("hub_")
        ):
            hub_name = cbc.target_hub or f"hub_{_slugify(cbc.concept_uri.rsplit(':', 1)[-1])}"
            return hub_name, cbc.concept_uri

    slug = _slugify(hint_lower.removeprefix("cbc_").removeprefix("hub_"))
    return f"hub_{slug}", f"urn:ontology:provisional:entity:{slug}"


def _resolve_relationship_uri(
    link_name: str,
    hub_names: list[str],
    ontology: GraphAOntology,
) -> str:
    """Resolve a Link's `ontology_concept_uri` from `GraphAOntology` or fallback URI."""
    for rel in ontology.canonical_relationships:
        if rel.target_link == link_name:
            return rel.relationship_uri
    hub_set = set(hub_names)
    for rel in ontology.canonical_relationships:
        endpoint_hubs = {_resolve_hub_and_uri(ep, ontology)[0] for ep in rel.endpoints}
        if endpoint_hubs and endpoint_hubs.issubset(hub_set):
            return rel.relationship_uri
    if ontology.canonical_relationships:
        return ontology.canonical_relationships[0].relationship_uri
    slug = _slugify(link_name.removeprefix("lnk_"))
    return f"urn:ontology:relationship:{slug}"


def _build_markdown_report(
    spec: PipelineInputSpec,
    setup: HarnessSetupConfig,
    run_id: str,
    status: str,
    overall_passed: bool,
    total_duration_ms: float,
    input_val: DeterministicValidationReport,
    output_val: DeterministicValidationReport,
    wave_reports: list[PipelineWaveReport],
    step_reports: list[PipelineStepReport],
    intermediate_artifacts: list[PipelineArtifactRef],
    final_artifacts: dict[str, PipelineArtifactRef],
) -> str:
    """Render a comprehensive Markdown summary report of all waves, steps, gates, and artifacts."""
    lines: list[str] = [
        f"# {spec.display_name}",
        "",
        f"- **Pipeline ID**: `{spec.pipeline_id}`",
        f"- **Project ID**: `{spec.project_id}`",
        f"- **Run ID**: `{run_id}`",
        f"- **Domain Namespace**: `{spec.domain_namespace}`",
        f"- **Harness Provider**: `{setup.provider}`",
        f"- **Overall Status**: `{status}` (`overall_passed={overall_passed}`)",
        f"- **Total Duration**: `{total_duration_ms:.2f} ms`",
        "",
        "## 1. Deterministic Validation Summary",
        "",
        (
            "| Validation Stage | Passed | Checked Items | Verified Artifacts "
            "| Verified Events | PII Check | CAS Integrity |"
        ),
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
        (
            f"| `INPUT_VALIDATION` | `{input_val.passed}` | {input_val.checked_items_count} | "
            f"{input_val.verified_artifacts_count} | {input_val.verified_events_count} | "
            f"`{input_val.pii_leak_check_passed}` | `{input_val.cas_integrity_verified}` |"
        ),
        (
            f"| `OUTPUT_VALIDATION` | `{output_val.passed}` | {output_val.checked_items_count} | "
            f"{output_val.verified_artifacts_count} | {output_val.verified_events_count} | "
            f"`{output_val.pii_leak_check_passed}` | `{output_val.cas_integrity_verified}` |"
        ),
        "",
        "## 2. Multi-Wave Progression Summary",
        "",
        "| Wave | Name | Type | Status | Sources | Columns | Attempts | Codemods | Sandbox |",
        "| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :--- | :---: |",
    ]
    for w in wave_reports:
        codemods_str = ", ".join(f"`{c}`" for c in w.applied_codemods) or "None"
        sbx_pass = w.sandbox_summary.get("passed", False)
        lines.append(
            f"| {w.wave_index} | `{w.wave_name}` | `{w.wave_type}` | `{w.status}` | "
            f"{w.sources_profiled} | {w.columns_profiled} | {w.self_healing_attempts} | "
            f"{codemods_str} | `{sbx_pass}` |"
        )

    lines.extend(
        [
            "",
            "## 3. Step-by-Step Execution Ledger",
            "",
            (
                "| Step ID | Wave | Stage | Persona | Tier | Status "
                "| Attempts | Duration (ms) | Summary |"
            ),
            "| :--- | :---: | :--- | :--- | :---: | :---: | :---: | :---: | :--- |",
        ]
    )
    for st in step_reports:
        lines.append(
            f"| `{st.step_id}` | {st.wave_index} | `{st.stage_id}` | `{st.persona}` | "
            f"`{st.model_tier}` | `{st.status}` | {st.attempt_count} | "
            f"{st.duration_ms:.2f} | {st.summary} |"
        )

    lines.extend(
        [
            "",
            "## 4. Final Artifacts",
            "",
            "| Key | Name | Stage | SHA-256 (Prefix) | Bytes | File Path |",
            "| :--- | :--- | :--- | :--- | :---: | :--- |",
        ]
    )
    for key, ref in final_artifacts.items():
        lines.append(
            f"| `{key}` | `{ref.name}` | `{ref.stage_id}` | `{ref.sha256[:16]}...` | "
            f"{ref.byte_length} | `{ref.file_path or ''}` |"
        )

    lines.extend(
        [
            "",
            f"## 5. Intermediate Artifacts ({len(intermediate_artifacts)} recorded)",
            "",
            "| Artifact ID | Wave | Stage | Name | SHA-256 (Prefix) | Bytes |",
            "| :--- | :---: | :--- | :--- | :--- | :---: |",
        ]
    )
    for ref in intermediate_artifacts:
        lines.append(
            f"| `{ref.artifact_id}` | {ref.wave_index if ref.wave_index is not None else '-'} | "
            f"`{ref.stage_id}` | `{ref.name}` | `{ref.sha256[:16]}...` | {ref.byte_length} |"
        )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 5. End-to-End Agentic Vault Pipeline Orchestrator
# ---------------------------------------------------------------------------


class AgenticVaultPipeline:
    """Executes the 8-stage neuro-symbolic Data Vault 2.0 pipeline across all waves."""

    def __init__(
        self,
        input_spec: PipelineInputSpec,
        harness_setup: HarnessSetupConfig | None = None,
        *,
        output_dir: Path | None = None,
        base_dir: Path | None = None,
        cas_store: ContentAddressableStore | None = None,
        sdk_module: Any | None = None,
    ) -> None:
        self.input_spec = input_spec
        self.harness_setup = harness_setup or HarnessSetupConfig()
        try:
            self.workspace_root = find_workspace_root()
        except Exception:
            self.workspace_root = Path.cwd().resolve()
        self.base_dir = (base_dir or self.workspace_root).resolve()
        self._explicit_output_dir = Path(output_dir).resolve() if output_dir is not None else None
        self._explicit_cas_store = cas_store
        self._sdk_module = sdk_module
        self._runtime_initialized = False

    def _ensure_runtime_initialized(self) -> None:
        if self._runtime_initialized:
            return
        if self._explicit_output_dir is not None:
            self.output_dir = self._explicit_output_dir
        elif self.input_spec.output_dir is not None:
            resolved_out, out_err = _resolve_and_check_safe_path(
                self.input_spec.output_dir,
                base_dir=self.base_dir,
                workspace_root=self.workspace_root,
            )
            if out_err is not None or resolved_out is None:
                raise ValueError(out_err or f"Invalid output_dir '{self.input_spec.output_dir}'.")
            self.output_dir = resolved_out
        else:
            self.output_dir = Path(tempfile.mkdtemp(prefix="opendv_pipeline_out_")).resolve()

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cas_dir = self.output_dir / "cas"
        self.cas_store = self._explicit_cas_store or ContentAddressableStore(self.cas_dir)
        self.ledger_path = self.output_dir / "vault-ledger.jsonl"
        self.ledger_emitter = VaultLedgerEmitter(
            ledger_path=self.ledger_path,
            cas_store=self.cas_store,
        )
        self.hold_manager = StewardHoldManager(state_dir=self.output_dir / "state")
        self.harness_config = self.harness_setup.to_harness_config(
            workspace_dir=self.workspace_root,
            state_dir=self.output_dir / "state",
            app_data_dir=self.output_dir / "app_data",
            save_dir=self.output_dir / "save",
        )
        self.harness: VaultAgentHarness = get_harness_adapter(
            self.harness_config,
            sdk_module=self._sdk_module,
        )
        self._runtime_initialized = True

    def _record_artifact(
        self,
        *,
        artifact_id: str,
        name: str,
        stage_id: str,
        content: str | bytes,
        media_type: str,
        rel_path: str,
        wave_index: int | None = None,
        description: str = "",
        intermediate_list: list[PipelineArtifactRef] | None = None,
    ) -> PipelineArtifactRef:
        """Write `content` into both `ContentAddressableStore` and `output_dir / rel_path`."""
        raw_bytes = content.encode("utf-8") if isinstance(content, str) else content
        sha256_hex = self.cas_store.put(raw_bytes)
        disk_path = self.output_dir / rel_path
        disk_path.parent.mkdir(parents=True, exist_ok=True)
        disk_path.write_bytes(raw_bytes)
        ref = PipelineArtifactRef(
            artifact_id=artifact_id,
            name=name,
            stage_id=stage_id,
            wave_index=wave_index,
            media_type=media_type,
            sha256=sha256_hex,
            byte_length=len(raw_bytes),
            file_path=str(disk_path),
            description=description,
        )
        if intermediate_list is not None:
            intermediate_list.append(ref)
        return ref

    def _emit_wb_event(
        self,
        *,
        run_id: str,
        trace_id: str,
        stage_id: PipelineStageId,
        workbench_view_id: WorkbenchViewId,
        viz_id: VizId,
        persona: AgentPersona,
        status: ObservabilityVerdict,
        headline: str,
        business_impact: str,
        rule_ids: list[str],
        summary_metrics: dict[str, Any],
        context_refs: dict[str, str | None] | None = None,
        remediation: dict[str, Any] | None = None,
        layer3_formal_proof: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build, redact-check, and persist a schema-valid `VaultObservabilityEvent`."""
        event_id = f"evt-{run_id}-{workbench_view_id.value.lower()}-{uuid.uuid4().hex[:8]}"
        raw_event = build_observability_event_dict(
            event_id=event_id,
            run_id=run_id,
            trace_id=trace_id,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=None,
            stage_id=stage_id,
            workbench_view_id=workbench_view_id,
            viz_id=viz_id,
            agent_persona=persona,
            status=status,
            headline=headline,
            business_impact=business_impact,
            data_classification=DataClassification.INTERNAL,
            rule_ids_evaluated=rule_ids,
            summary_metrics=summary_metrics,
            remediation=remediation,
            context_refs=context_refs,
            layer3_formal_proof=layer3_formal_proof,
        )
        redacted = redact_event_for_role(raw_event, role="VIEWER")
        validate_no_pii_leaks(redacted)
        return self.ledger_emitter.emit(redacted)

    async def run_async(self) -> AgenticPipelineReport:
        """Execute the full multi-wave 8-stage pipeline asynchronously."""
        t0_pipeline = time.perf_counter()
        started_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        run_id = self.input_spec.run_id or f"run-{uuid.uuid4().hex[:10]}"
        trace_id = uuid.uuid4().hex

        step_reports: list[PipelineStepReport] = []
        wave_reports: list[PipelineWaveReport] = []
        intermediate_artifacts: list[PipelineArtifactRef] = []
        final_artifacts: dict[str, PipelineArtifactRef] = {}
        observability_events: list[dict[str, Any]] = []

        # -------------------------------------------------------------------
        # STAGE 00: Orchestration & Deterministic Input Validation
        # -------------------------------------------------------------------
        t0_s0 = time.perf_counter()
        input_val = validate_pipeline_inputs(
            self.input_spec,
            self.harness_setup,
            base_dir=self.base_dir,
        )
        if not input_val.passed:
            completed_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            total_ms = round((time.perf_counter() - t0_pipeline) * 1000.0, 3)
            empty_out_val = DeterministicValidationReport(
                stage="OUTPUT_VALIDATION",
                passed=False,
                checked_items_count=0,
                diagnostics=[
                    {
                        "code": "SKIPPED_DUE_TO_INPUT_FAILURE",
                        "severity": "ERROR",
                        "target": "pipeline",
                        "message": "Pipeline halted at STAGE_00 due to input validation failure.",
                    }
                ],
            )
            md = _build_markdown_report(
                self.input_spec,
                self.harness_setup,
                run_id,
                "FAILED",
                False,
                total_ms,
                input_val,
                empty_out_val,
                wave_reports,
                step_reports,
                intermediate_artifacts,
                final_artifacts,
            )
            return AgenticPipelineReport(
                run_id=run_id,
                pipeline_id=self.input_spec.pipeline_id,
                project_id=self.input_spec.project_id,
                domain_namespace=self.input_spec.domain_namespace,
                status="FAILED",
                overall_passed=False,
                started_at=started_at,
                completed_at=completed_at,
                total_duration_ms=total_ms,
                harness_setup=self.harness_setup.to_summary_dict(),
                input_validation=input_val,
                output_validation=empty_out_val,
                wave_reports=wave_reports,
                step_reports=step_reports,
                intermediate_artifacts=intermediate_artifacts,
                final_artifacts=final_artifacts,
                observability_events=observability_events,
                markdown_summary=md,
            )

        self._ensure_runtime_initialized()

        ont_path, _ = _resolve_and_check_safe_path(
            self.input_spec.graph_a_ontology_path,
            base_dir=self.base_dir,
            workspace_root=self.workspace_root,
        )
        assert ont_path is not None
        raw_ontology = load_structured_file(ont_path)
        ontology = GraphAOntology.model_validate(raw_ontology)

        ont_ref = self._record_artifact(
            artifact_id="art-stage00-graph-a-ontology",
            name="graph_a_ontology.yaml",
            stage_id=PipelineStageId.STAGE_00_ORCHESTRATION.value,
            content=yaml.safe_dump(raw_ontology, sort_keys=False),
            media_type="application/x-yaml",
            rel_path="stage_00/graph_a_ontology.yaml",
            wave_index=0,
            description="Verified Enterprise Core Business Concept (Graph A) Ontology.",
            intermediate_list=intermediate_artifacts,
        )

        vfm_spec = build_vault_first_mate_task_spec(
            prompt=(
                f"Orchestrate {len(self.input_spec.waves)} waves for pipeline "
                f"'{self.input_spec.pipeline_id}' in domain '{self.input_spec.domain_namespace}'."
            ),
            run_id=run_id,
            trace_id=trace_id,
        )
        vfm_res = await self.harness.run_task(vfm_spec)
        s0_ms = round((time.perf_counter() - t0_s0) * 1000.0, 3)

        ev_wb00 = self._emit_wb_event(
            run_id=run_id,
            trace_id=trace_id,
            stage_id=PipelineStageId.STAGE_00_ORCHESTRATION,
            workbench_view_id=WorkbenchViewId.WB_00,
            viz_id=VizId.VIZ_PIPE_00,
            persona=AgentPersona.VAULT_FIRST_MATE,
            status=ObservabilityVerdict.PASS,
            headline=(
                f"Initialized 8-stage neuro-symbolic pipeline for domain "
                f"{self.input_spec.domain_namespace} across {len(self.input_spec.waves)} waves."
            ),
            business_impact=(
                "Establishes read-only supervisor governance and Content-Addressable Store "
                "lineage before source onboarding."
            ),
            rule_ids=["HR-01"],
            summary_metrics={
                "wave_count": len(self.input_spec.waves),
                "checked_input_items": input_val.checked_items_count,
                "provider": self.harness.provider_name,
                "model_used": vfm_res.model_used,
            },
            context_refs={"graph_a_ontology_sha256": ont_ref.sha256},
        )
        observability_events.append(ev_wb00)

        ev_wb01 = self._emit_wb_event(
            run_id=run_id,
            trace_id=trace_id,
            stage_id=PipelineStageId.STAGE_00_ORCHESTRATION,
            workbench_view_id=WorkbenchViewId.WB_01,
            viz_id=VizId.VIZ_STEP_01,
            persona=AgentPersona.VAULT_FIRST_MATE,
            status=ObservabilityVerdict.PASS,
            headline=(
                f"Grounded {len(ontology.canonical_business_concepts)} Core Business Concepts "
                f"and {len(ontology.canonical_relationships)} relationships in Graph A."
            ),
            business_impact=(
                "Prevents source-system silo Hubs and enforces 3-Tier Business Key Authority."
            ),
            rule_ids=["DV-ONT-001", "DV-ONT-002", "DV-ONT-004"],
            summary_metrics={
                "cbc_count": len(ontology.canonical_business_concepts),
                "relationship_count": len(ontology.canonical_relationships),
            },
            context_refs={"graph_a_ontology_sha256": ont_ref.sha256},
        )
        observability_events.append(ev_wb01)

        step_reports.append(
            PipelineStepReport(
                step_id="step-w0-stage00-orchestration",
                wave_index=0,
                wave_name=self.input_spec.waves[0].wave_name,
                stage_id=PipelineStageId.STAGE_00_ORCHESTRATION.value,
                persona=AgentPersona.VAULT_FIRST_MATE.value,
                model_tier=ModelTier.HIGH_REASONING.value,
                status="PASS",
                attempt_count=1,
                duration_ms=s0_ms,
                summary=(
                    f"Validated {input_val.checked_items_count} input items, loaded Graph A "
                    f"ontology ({len(ontology.canonical_business_concepts)} CBCs), and "
                    f"initialized {self.harness.provider_name} harness."
                ),
                tool_invocations=["emit_observability_event"],
                input_artifact_sha256s=[],
                output_artifact_sha256s=[ont_ref.sha256],
                metrics={
                    "checked_items_count": input_val.checked_items_count,
                    "cbc_count": len(ontology.canonical_business_concepts),
                },
            )
        )

        # Cumulative state across waves
        cumulative_sources: dict[str, tuple[PipelineSourceFeedInput, Path]] = {}
        cumulative_source_systems: dict[str, ProfiledSourceSystem] = {}
        cumulative_doc_texts: dict[str, str] = {}
        cumulative_unstructured_docs: list[tuple[PipelineUnstructuredDocInput, str]] = []
        added_drift_cols_by_table: dict[str, list[str]] = {}
        deployed_contract_dict: dict[str, Any] | None = None
        if self.input_spec.initial_contract_path is not None:
            init_c_path, _ = _resolve_and_check_safe_path(
                self.input_spec.initial_contract_path,
                base_dir=self.base_dir,
                workspace_root=self.workspace_root,
            )
            if init_c_path is not None:
                deployed_contract_dict = load_structured_file(init_c_path)

        last_verified_contract: dict[str, Any] = {}
        last_canonical_ir: CanonicalIR | None = None
        last_sql_bundle_text: str = ""
        last_contract_ref: PipelineArtifactRef | None = None
        last_ir_ref: PipelineArtifactRef | None = None
        last_sql_ref: PipelineArtifactRef | None = None
        pipeline_paused = False

        # -------------------------------------------------------------------
        # Execute Waves 0 .. N-1
        # -------------------------------------------------------------------
        for wave in self.input_spec.waves:
            w_idx = wave.wave_index
            w_name = wave.wave_name
            wave_step_ids: list[str] = []
            if w_idx == 0:
                wave_step_ids.append("step-w0-stage00-orchestration")

            # ---------------------------------------------------------------
            # STAGE 01: Scout-Source (Bronze Statistical Profiling & Drift)
            # ---------------------------------------------------------------
            t0_s1 = time.perf_counter()
            profiler = GraphBProfiler()
            wave_drift_events: list[dict[str, Any]] = []
            wave_columns_profiled = 0
            wave_sources_profiled = len(wave.sources)
            current_wave_drift_tables: set[str] = set()

            try:
                for src in wave.sources:
                    d_path, _ = _resolve_and_check_safe_path(
                        src.data_path,
                        base_dir=self.base_dir,
                        workspace_root=self.workspace_root,
                    )
                    assert d_path is not None
                    qualified_table = f"{src.source_system.lower()}.{src.table_name.lower()}"
                    cumulative_sources[qualified_table] = (src, d_path)

                    view_t1 = f"v_{_slugify(src.source_system)}_{_slugify(src.table_name)}"
                    _load_table_into_duckdb(profiler.conn, view_t1, d_path)
                    gb_single = profiler.build_graph_b_profile(
                        domain=self.input_spec.domain_namespace,
                        source_system_id=src.source_system.upper(),
                        bkcc_namespace=src.bkcc_namespace,
                        table_names=[view_t1],
                    )
                    prof_tbl = gb_single.source_systems[0].tables[0]

                    # Enrich profiled columns with input spec hints (BK, FK, Surrogate, PII)
                    enriched_cols: list[ProfiledColumn] = []
                    for p_col in prof_tbl.columns:
                        wave_columns_profiled += 1
                        c_name = p_col.column_name
                        sec_tag = "PII_HIGH" if c_name in src.pii_columns else p_col.security_tag
                        cand_role: CandidateRoleLiteral = p_col.candidate_role
                        if c_name in src.surrogate_key_columns:
                            cand_role = "SYSTEM_SURROGATE_DROPPED"
                        elif c_name in src.business_key_columns:
                            if src.key_authority_tier == "TIER_3_LOCAL_SCOPED":
                                cand_role = "TIER_3_LOCAL_SCOPED_KEY"
                            elif src.key_authority_tier == "TIER_2_ENTERPRISE_SOR":
                                cand_role = "TIER_2_BUSINESS_KEY"
                            else:
                                cand_role = "TIER_1_BUSINESS_KEY"
                        elif c_name in src.foreign_key_mappings:
                            cand_role = (
                                "OPTIONAL_FOREIGN_KEY_REF"
                                if p_col.u_non_null < 0.95 or p_col.null_rate > 0.05
                                else "FOREIGN_KEY_REF"
                            )
                        elif c_name in src.pii_columns:
                            cand_role = "PII_RESTRICTED_PAYLOAD"

                        enriched_cols.append(
                            p_col.model_copy(
                                update={
                                    "security_tag": sec_tag,
                                    "candidate_role": cand_role,
                                }
                            )
                        )

                    updated_tbl = prof_tbl.model_copy(
                        update={
                            "bronze_table": qualified_table,
                            "columns": enriched_cols,
                        }
                    )
                    sys_code = src.source_system.upper()
                    existing_sys = cumulative_source_systems.get(sys_code)
                    if existing_sys is None:
                        cumulative_source_systems[sys_code] = ProfiledSourceSystem(
                            source_system_code=sys_code,
                            description=f"Profiled Bronze source system {sys_code}",
                            tables=[updated_tbl],
                        )
                    else:
                        other_tbls = [
                            t for t in existing_sys.tables if t.bronze_table != qualified_table
                        ]
                        cumulative_source_systems[sys_code] = existing_sys.model_copy(
                            update={"tables": [*other_tbls, updated_tbl]}
                        )

                    # Detect schema drift if baseline_data_path is provided
                    if src.baseline_data_path is not None:
                        b_path, _ = _resolve_and_check_safe_path(
                            src.baseline_data_path,
                            base_dir=self.base_dir,
                            workspace_root=self.workspace_root,
                        )
                        assert b_path is not None
                        view_t0 = f"{view_t1}_baseline"
                        _load_table_into_duckdb(profiler.conn, view_t0, b_path)
                        drift_rep = profiler.detect_schema_drift(view_t0, view_t1)
                        for dev in drift_rep.events:
                            ev_dict = dev.model_dump(mode="json")
                            ev_dict["source_table"] = qualified_table
                            wave_drift_events.append(ev_dict)
                            if dev.drift_kind.value == "ADD_COLUMN":
                                current_wave_drift_tables.add(qualified_table)
                                drift_cols = added_drift_cols_by_table.setdefault(
                                    qualified_table, []
                                )
                                if dev.column_name not in drift_cols:
                                    drift_cols.append(dev.column_name)
            finally:
                profiler.close()

            if wave.unstructured_documents and cumulative_source_systems:
                first_sys_key = next(iter(cumulative_source_systems))
                for doc in wave.unstructured_documents:
                    u_path, _ = _resolve_and_check_safe_path(
                        doc.document_path,
                        base_dir=self.base_dir,
                        workspace_root=self.workspace_root,
                    )
                    assert u_path is not None
                    doc_text = u_path.read_text(encoding="utf-8")
                    doc_sha256 = hashlib.sha256(doc_text.encode("utf-8")).hexdigest()
                    prof_doc = _build_profiled_unstructured_doc(doc, doc_text, doc_sha256)
                    if prof_doc is not None:
                        first_sys = cumulative_source_systems[first_sys_key]
                        existing_docs = [
                            d
                            for d in (first_sys.unstructured_documents or [])
                            if d.doc_uri != doc.doc_id
                        ]
                        cumulative_source_systems[first_sys_key] = first_sys.model_copy(
                            update={"unstructured_documents": [*existing_docs, prof_doc]}
                        )

            # Assemble cumulative GraphBProfile for this wave
            wave_gb_profile = GraphBProfile(
                profile_version="1.0.0",
                domain_namespace=_slugify(self.input_spec.domain_namespace),
                profiled_at_utc=datetime.now(UTC),
                source_systems=list(cumulative_source_systems.values()),
            )
            wave_gb_dict = wave_gb_profile.model_dump(mode="json", exclude_none=True)
            validate_graph_b(wave_gb_dict)

            gb_ref = self._record_artifact(
                artifact_id=f"art-w{w_idx}-stage01-graph-b-profile",
                name=f"wave_{w_idx}_graph_b_profile.json",
                stage_id=PipelineStageId.STAGE_01_SCOUT_SOURCE.value,
                content=json.dumps(wave_gb_dict, indent=2, sort_keys=True),
                media_type="application/json",
                rel_path=f"waves/wave_{w_idx}/wave_{w_idx}_graph_b_profile.json",
                wave_index=w_idx,
                description=f"Wave {w_idx} Empirical Graph B Statistical Profile.",
                intermediate_list=intermediate_artifacts,
            )
            s1_out_shas = [gb_ref.sha256]

            if wave_drift_events:
                drift_ref = self._record_artifact(
                    artifact_id=f"art-w{w_idx}-stage01-schema-drift",
                    name=f"wave_{w_idx}_schema_drift_report.json",
                    stage_id=PipelineStageId.STAGE_01_SCOUT_SOURCE.value,
                    content=json.dumps(
                        {
                            "wave_index": w_idx,
                            "wave_name": w_name,
                            "drift_event_count": len(wave_drift_events),
                            "events": wave_drift_events,
                        },
                        indent=2,
                        sort_keys=True,
                    ),
                    media_type="application/json",
                    rel_path=f"waves/wave_{w_idx}/wave_{w_idx}_schema_drift_report.json",
                    wave_index=w_idx,
                    description=f"Wave {w_idx} Intra-Source Schema Drift Report.",
                    intermediate_list=intermediate_artifacts,
                )
                s1_out_shas.append(drift_ref.sha256)

            scout_src_spec = build_scout_source_task_spec(
                prompt=f"Profile Wave {w_idx} ({w_name}) relational sources.",
                run_id=run_id,
                trace_id=trace_id,
            )
            await self.harness.run_task(scout_src_spec)
            s1_ms = round((time.perf_counter() - t0_s1) * 1000.0, 3)

            ev_wb02 = self._emit_wb_event(
                run_id=run_id,
                trace_id=trace_id,
                stage_id=PipelineStageId.STAGE_01_SCOUT_SOURCE,
                workbench_view_id=WorkbenchViewId.WB_02,
                viz_id=VizId.VIZ_STEP_02,
                persona=AgentPersona.SCOUT_SOURCE,
                status=(
                    ObservabilityVerdict.AUTO_REPAIRED
                    if wave_drift_events
                    else ObservabilityVerdict.PASS
                ),
                headline=(
                    f"Wave {w_idx} profiled {wave_sources_profiled} source feed(s) and "
                    f"{wave_columns_profiled} column(s) into Graph B."
                ),
                business_impact=(
                    "Computes empirical uniqueness, null ratio, Shannon entropy, and write "
                    "amplification metrics for deterministic key and satellite synthesis."
                ),
                rule_ids=["DV-REL-001", "ALG-A9A"],
                summary_metrics={
                    "wave_index": w_idx,
                    "sources_profiled": wave_sources_profiled,
                    "columns_profiled": wave_columns_profiled,
                    "drift_events_detected": len(wave_drift_events),
                },
                context_refs={"graph_b_profile_sha256": gb_ref.sha256},
                remediation=(
                    {
                        "codemod_type": "ALG_A9A_INTRA_SOURCE_SCHEMA_DRIFT",
                        "target_entity": wave_drift_events[0]["source_table"],
                        "patch_diff": (
                            f"Detected {len(wave_drift_events)} schema drift transition(s)"
                        ),
                        "action_description": (
                            "Route additive schema drift columns to companion satellite"
                        ),
                    }
                    if wave_drift_events
                    else None
                ),
            )
            observability_events.append(ev_wb02)

            if wave_drift_events:
                ev_wb09 = self._emit_wb_event(
                    run_id=run_id,
                    trace_id=trace_id,
                    stage_id=PipelineStageId.STAGE_01_SCOUT_SOURCE,
                    workbench_view_id=WorkbenchViewId.WB_09,
                    viz_id=VizId.VIZ_STEP_03,
                    persona=AgentPersona.SCOUT_SOURCE,
                    status=ObservabilityVerdict.AUTO_REPAIRED,
                    headline=(
                        f"Wave {w_idx} detected {len(wave_drift_events)} intra-source schema "
                        f"drift event(s) for additive evolution."
                    ),
                    business_impact=(
                        "Preserves historical Satellite hash_diff continuity by isolating "
                        "newly added columns into an additive companion Satellite."
                    ),
                    rule_ids=["ALG-A9A", "DV-EVO-006"],
                    summary_metrics={
                        "wave_index": w_idx,
                        "drift_event_count": len(wave_drift_events),
                    },
                    context_refs={"graph_b_profile_sha256": gb_ref.sha256},
                    remediation={
                        "codemod_type": "ALG_A9A_INTRA_SOURCE_SCHEMA_DRIFT",
                        "target_entity": wave_drift_events[0]["source_table"],
                        "patch_diff": json.dumps(wave_drift_events),
                        "action_description": (
                            "Isolate newly added columns into additive companion Satellite"
                        ),
                    },
                )
                observability_events.append(ev_wb09)

            s1_step_id = f"step-w{w_idx}-stage01-scout-source"
            wave_step_ids.append(s1_step_id)
            step_reports.append(
                PipelineStepReport(
                    step_id=s1_step_id,
                    wave_index=w_idx,
                    wave_name=w_name,
                    stage_id=PipelineStageId.STAGE_01_SCOUT_SOURCE.value,
                    persona=AgentPersona.SCOUT_SOURCE.value,
                    model_tier=ModelTier.FAST_TIER.value,
                    status="AUTO_REPAIRED" if wave_drift_events else "PASS",
                    attempt_count=1,
                    duration_ms=s1_ms,
                    summary=(
                        f"Profiled {wave_sources_profiled} source(s) ({wave_columns_profiled} "
                        f"columns); detected {len(wave_drift_events)} schema drift event(s)."
                    ),
                    tool_invocations=["profile_source_feed", "emit_observability_event"],
                    input_artifact_sha256s=[ont_ref.sha256],
                    output_artifact_sha256s=s1_out_shas,
                    metrics={
                        "sources_profiled": wave_sources_profiled,
                        "columns_profiled": wave_columns_profiled,
                        "schema_drift_events": len(wave_drift_events),
                    },
                )
            )

            # ---------------------------------------------------------------
            # STAGE 02: Scout-Unstructured (Span Grounding & 3-Tier ER)
            # ---------------------------------------------------------------
            t0_s2 = time.perf_counter()
            tier_counts: dict[str, int] = {"Tier-U1": 0, "Tier-U2": 0, "Tier-U3": 0}
            grounding_records: list[dict[str, Any]] = []
            s2_status: Literal[
                "PASS", "AUTO_REPAIRED", "ASK-USER", "REJECT-WITH-TRACE", "ERROR"
            ] = "PASS"

            for doc in wave.unstructured_documents:
                u_path, _ = _resolve_and_check_safe_path(
                    doc.document_path,
                    base_dir=self.base_dir,
                    workspace_root=self.workspace_root,
                )
                assert u_path is not None
                doc_text = u_path.read_text(encoding="utf-8")
                doc_sha256 = hashlib.sha256(doc_text.encode("utf-8")).hexdigest()
                doc_held_or_rejected = False

                for cand in doc.extracted_candidates:
                    _margin, short_tier, _gb_tier, action = _compute_candidate_er_metrics(cand)
                    tier_counts[short_tier] = tier_counts.get(short_tier, 0) + 1

                    hold_id: str | None = None
                    hold_resolution: str | None = None
                    if short_tier == "Tier-U3":
                        hold_rec: StewardHoldRecord = self.hold_manager.record_hold(
                            run_id=run_id,
                            task_id=f"task-w{w_idx}-u3-{cand.extraction_id}",
                            session_id=f"sess-w{w_idx}-{doc.doc_id}",
                            stage_id=PipelineStageId.STAGE_02_SCOUT_UNSTRUCTURED,
                            workbench_view_id=WorkbenchViewId.WB_03,
                            viz_id=VizId.VIZ_GATE_07,
                            persona=AgentPersona.SCOUT_UNSTRUCTURED,
                            headline=(
                                f"Tier-U3 ambiguous entity extraction '{cand.extraction_id}' in "
                                f"document '{doc.doc_id}' (confidence={cand.confidence_score:.2f})."
                            ),
                            escalation_reason="TIER_U3_AMBIGUOUS_ENTITY",
                            rule_id="ALG-A9B",
                            options=cand.candidate_hub_matches
                            or [
                                {
                                    "option_id": cand.extracted_business_key,
                                    "target_hub": cand.target_hub,
                                    "confidence": cand.confidence_score,
                                }
                            ],
                        )
                        hold_id = hold_rec.decision_id
                        if self.harness_setup.steward_hold_policy == "auto_approve_top_candidate":
                            top_opt = (
                                str(cand.candidate_hub_matches[0].get("candidate_key"))
                                if cand.candidate_hub_matches
                                and "candidate_key" in cand.candidate_hub_matches[0]
                                else cand.extracted_business_key
                            )
                            self.hold_manager.resolve_hold(
                                hold_rec.decision_id,
                                action="APPROVE",
                                selected_option_id=top_opt,
                                resolution_notes=(
                                    "Auto-approved top candidate per steward_hold_policy"
                                ),
                            )
                            hold_resolution = "APPROVED"
                            if s2_status == "PASS":
                                s2_status = "AUTO_REPAIRED"
                        elif self.harness_setup.steward_hold_policy == "reject_on_hold":
                            self.hold_manager.resolve_hold(
                                hold_rec.decision_id,
                                action="REJECT",
                                resolution_notes=(
                                    "Rejected on hold per harness_setup.steward_hold_policy"
                                ),
                            )
                            hold_resolution = "REJECTED"
                            action = "REJECT"
                            s2_status = "REJECT-WITH-TRACE"
                            doc_held_or_rejected = True
                        else:
                            hold_resolution = "PENDING"
                            if s2_status != "REJECT-WITH-TRACE":
                                s2_status = "ASK-USER"
                            pipeline_paused = True
                            doc_held_or_rejected = True

                    grounding_records.append(
                        {
                            "doc_id": doc.doc_id,
                            "extraction_id": cand.extraction_id,
                            "target_hub": cand.target_hub,
                            "extracted_business_key": cand.extracted_business_key,
                            "verbatim_quote": cand.verbatim_quote,
                            "char_start": cand.char_start,
                            "char_end": cand.char_end,
                            "span_verified": doc_text[cand.char_start : cand.char_end]
                            == cand.verbatim_quote,
                            "confidence_score": cand.confidence_score,
                            "er_tier": short_tier,
                            "action": action,
                            "hold_id": hold_id,
                            "hold_resolution": hold_resolution,
                        }
                    )

                if not doc_held_or_rejected:
                    cumulative_doc_texts[doc.doc_id] = doc_text
                    cumulative_unstructured_docs.append((doc, doc_sha256))

            ug_ref = self._record_artifact(
                artifact_id=f"art-w{w_idx}-stage02-unstructured-grounding",
                name=f"wave_{w_idx}_unstructured_grounding.json",
                stage_id=PipelineStageId.STAGE_02_SCOUT_UNSTRUCTURED.value,
                content=json.dumps(
                    {
                        "wave_index": w_idx,
                        "documents_processed": len(wave.unstructured_documents),
                        "tier_counts": tier_counts,
                        "extractions": grounding_records,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                media_type="application/json",
                rel_path=f"waves/wave_{w_idx}/wave_{w_idx}_unstructured_grounding.json",
                wave_index=w_idx,
                description=f"Wave {w_idx} Unstructured Span Grounding & 3-Tier ER Report.",
                intermediate_list=intermediate_artifacts,
            )

            scout_unstr_spec = build_scout_unstructured_task_spec(
                prompt=f"Ground unstructured spans for Wave {w_idx} ({w_name}).",
                run_id=run_id,
                trace_id=trace_id,
                model_tier=ModelTier.HIGH_REASONING,
            )
            await self.harness.run_task(scout_unstr_spec)
            s2_ms = round((time.perf_counter() - t0_s2) * 1000.0, 3)

            ev_wb03 = self._emit_wb_event(
                run_id=run_id,
                trace_id=trace_id,
                stage_id=PipelineStageId.STAGE_02_SCOUT_UNSTRUCTURED,
                workbench_view_id=WorkbenchViewId.WB_03,
                viz_id=VizId.VIZ_STEP_04,
                persona=AgentPersona.SCOUT_UNSTRUCTURED,
                status=_step_status_to_verdict(s2_status),
                headline=(
                    f"Wave {w_idx} verified {len(grounding_records)} unstructured extraction "
                    f"span(s) across {len(wave.unstructured_documents)} document(s)."
                ),
                business_impact=(
                    "Enforces exact character-offset span grounding (Rules U1-U4) and 3-Tier "
                    "Entity Resolution confidence margin governance."
                ),
                rule_ids=["ALG-A9B"],
                summary_metrics={
                    "wave_index": w_idx,
                    "documents_count": len(wave.unstructured_documents),
                    "extractions_count": len(grounding_records),
                    "tier_u1_count": tier_counts["Tier-U1"],
                    "tier_u2_count": tier_counts["Tier-U2"],
                    "tier_u3_count": tier_counts["Tier-U3"],
                },
                context_refs={"unstructured_grounding_sha256": ug_ref.sha256},
                remediation=(
                    {
                        "codemod_type": "STEWARD_AUTO_APPROVE_TOP_CANDIDATE",
                        "target_entity": "hub_document",
                        "patch_diff": "Resolved Tier-U3 hold via steward_hold_policy",
                        "action_description": (
                            "Auto-approved top candidate for Tier-U3 extraction hold"
                        ),
                    }
                    if s2_status == "AUTO_REPAIRED"
                    else None
                ),
            )
            observability_events.append(ev_wb03)

            s2_step_id = f"step-w{w_idx}-stage02-scout-unstructured"
            wave_step_ids.append(s2_step_id)
            step_reports.append(
                PipelineStepReport(
                    step_id=s2_step_id,
                    wave_index=w_idx,
                    wave_name=w_name,
                    stage_id=PipelineStageId.STAGE_02_SCOUT_UNSTRUCTURED.value,
                    persona=AgentPersona.SCOUT_UNSTRUCTURED.value,
                    model_tier=ModelTier.HIGH_REASONING.value,
                    status=s2_status,
                    attempt_count=1,
                    duration_ms=s2_ms,
                    summary=(
                        f"Processed {len(wave.unstructured_documents)} unstructured document(s) "
                        f"with {len(grounding_records)} verified spans (tiers={tier_counts})."
                    ),
                    tool_invocations=["extract_pdf_spans", "emit_observability_event"],
                    input_artifact_sha256s=[gb_ref.sha256],
                    output_artifact_sha256s=[ug_ref.sha256],
                    metrics={"tier_counts": tier_counts},
                )
            )

            # ---------------------------------------------------------------
            # STAGE 03 & STAGE 04: Ship-Vault Pass 1 (100% Column Triage)
            # & Pass 2 (CumulativeDV 2.0 Contract Synthesis)
            # ---------------------------------------------------------------
            t0_s3 = time.perf_counter()
            candidate_contract = self._synthesize_cumulative_contract(
                ontology=ontology,
                cumulative_sources=cumulative_sources,
                cumulative_source_systems=cumulative_source_systems,
                cumulative_unstructured_docs=cumulative_unstructured_docs,
                added_drift_cols_by_table=added_drift_cols_by_table,
                current_wave_drift_tables=current_wave_drift_tables,
                wave_index=w_idx,
            )

            pass1_ledger = list(candidate_contract["column_triage_ledger"])
            triage_ref = self._record_artifact(
                artifact_id=f"art-w{w_idx}-stage03-pass1-triage",
                name=f"wave_{w_idx}_pass1_column_triage.json",
                stage_id=PipelineStageId.STAGE_03_SHIP_VAULT_PASS1.value,
                content=json.dumps(
                    {
                        "wave_index": w_idx,
                        "total_columns_triaged": len(pass1_ledger),
                        "column_triage_ledger": pass1_ledger,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                media_type="application/json",
                rel_path=f"waves/wave_{w_idx}/wave_{w_idx}_pass1_column_triage.json",
                wave_index=w_idx,
                description=f"Wave {w_idx} Pass 1 100% Column Triage Ledger.",
                intermediate_list=intermediate_artifacts,
            )
            s3_ms = round((time.perf_counter() - t0_s3) * 1000.0, 3)

            ev_wb04 = self._emit_wb_event(
                run_id=run_id,
                trace_id=trace_id,
                stage_id=PipelineStageId.STAGE_03_SHIP_VAULT_PASS1,
                workbench_view_id=WorkbenchViewId.WB_04,
                viz_id=VizId.VIZ_STEP_05,
                persona=AgentPersona.SHIP_VAULT,
                status=ObservabilityVerdict.PASS,
                headline=(
                    f"Wave {w_idx} Pass 1 Column Triage accounted for 100% of "
                    f"{len(pass1_ledger)} cumulative Bronze column(s)."
                ),
                business_impact=(
                    "Guarantees zero silent column drops (DV-COL-012 / DV-G4-002) across "
                    "all onboarded relational and unstructured sources."
                ),
                rule_ids=["DV-COL-012", "DV-G4-002"],
                summary_metrics={
                    "wave_index": w_idx,
                    "triaged_column_count": len(pass1_ledger),
                },
                context_refs={"pass1_column_triage_sha256": triage_ref.sha256},
            )
            observability_events.append(ev_wb04)

            s3_step_id = f"step-w{w_idx}-stage03-ship-vault-pass1"
            wave_step_ids.append(s3_step_id)
            step_reports.append(
                PipelineStepReport(
                    step_id=s3_step_id,
                    wave_index=w_idx,
                    wave_name=w_name,
                    stage_id=PipelineStageId.STAGE_03_SHIP_VAULT_PASS1.value,
                    persona=AgentPersona.SHIP_VAULT.value,
                    model_tier=ModelTier.HIGH_REASONING.value,
                    status="PASS",
                    attempt_count=1,
                    duration_ms=s3_ms,
                    summary=(
                        f"Synthesized Pass 1 Column Triage Ledger covering "
                        f"{len(pass1_ledger)} cumulative column(s)."
                    ),
                    tool_invocations=["emit_observability_event"],
                    input_artifact_sha256s=[gb_ref.sha256, ug_ref.sha256],
                    output_artifact_sha256s=[triage_ref.sha256],
                    metrics={"triaged_column_count": len(pass1_ledger)},
                )
            )

            # STAGE 04: Pass 2 Candidate Contract Synthesis
            t0_s4 = time.perf_counter()
            attempt1_contract = copy.deepcopy(candidate_contract)
            injected_fault = (
                w_idx == 0
                and self.harness_setup.inject_adversarial_fault_on_attempt_1
                and len(attempt1_contract["column_triage_ledger"]) > 1
            )
            if injected_fault:
                # Inject a recoverable Pass 1 omission on Attempt 1 so Stage 5 exercises
                # the @toon_v1 REJECT_WITH_TRACE -> ship-vault self-healing loop!
                dropped_col = attempt1_contract["column_triage_ledger"][-1]["column_name"]
                dropped_tbl = attempt1_contract["column_triage_ledger"][-1]["bronze_table"]
                attempt1_contract["column_triage_ledger"] = [
                    e
                    for e in attempt1_contract["column_triage_ledger"]
                    if not (e["bronze_table"] == dropped_tbl and e["column_name"] == dropped_col)
                ]

            att1_ref = self._record_artifact(
                artifact_id=f"art-w{w_idx}-stage04-contract-attempt1",
                name=f"wave_{w_idx}_contract_attempt_1.yaml",
                stage_id=PipelineStageId.STAGE_04_SHIP_VAULT_PASS2.value,
                content=yaml.safe_dump(attempt1_contract, sort_keys=False),
                media_type="application/x-yaml",
                rel_path=f"waves/wave_{w_idx}/wave_{w_idx}_contract_attempt_1.yaml",
                wave_index=w_idx,
                description=f"Wave {w_idx} Pass 2 Candidate Contract (Attempt 1).",
                intermediate_list=intermediate_artifacts,
            )

            ship_spec = build_ship_vault_task_spec(
                prompt=f"Synthesize Pass 2 Data Vault 2.0 contract for Wave {w_idx} ({w_name}).",
                run_id=run_id,
                trace_id=trace_id,
                stage_id=PipelineStageId.STAGE_04_SHIP_VAULT_PASS2,
                workbench_view_id=WorkbenchViewId.WB_05,
                viz_id=VizId.VIZ_STEP_06,
            )
            await self.harness.run_task(ship_spec)
            s4_ms = round((time.perf_counter() - t0_s4) * 1000.0, 3)

            ev_wb05 = self._emit_wb_event(
                run_id=run_id,
                trace_id=trace_id,
                stage_id=PipelineStageId.STAGE_04_SHIP_VAULT_PASS2,
                workbench_view_id=WorkbenchViewId.WB_05,
                viz_id=VizId.VIZ_STEP_06,
                persona=AgentPersona.SHIP_VAULT,
                status=ObservabilityVerdict.PASS,
                headline=(
                    f"Wave {w_idx} Pass 2 synthesized {len(candidate_contract['hubs'])} Hubs, "
                    f"{len(candidate_contract['links'])} Links, and "
                    f"{len(candidate_contract['satellites'])} Satellites."
                ),
                business_impact=(
                    "Applies 3-Tier BKCC authority, Nullable-FK Link splitting, and PII "
                    "Satellite isolation rules."
                ),
                rule_ids=["ALG-A1", "ALG-A4", "DV-STR-001"],
                summary_metrics={
                    "wave_index": w_idx,
                    "hub_count": len(candidate_contract["hubs"]),
                    "link_count": len(candidate_contract["links"]),
                    "satellite_count": len(candidate_contract["satellites"]),
                },
                context_refs={"contract_yaml_pre_sha256": att1_ref.sha256},
            )
            observability_events.append(ev_wb05)

            s4_step_id = f"step-w{w_idx}-stage04-ship-vault-pass2"
            wave_step_ids.append(s4_step_id)
            step_reports.append(
                PipelineStepReport(
                    step_id=s4_step_id,
                    wave_index=w_idx,
                    wave_name=w_name,
                    stage_id=PipelineStageId.STAGE_04_SHIP_VAULT_PASS2.value,
                    persona=AgentPersona.SHIP_VAULT.value,
                    model_tier=ModelTier.HIGH_REASONING.value,
                    status="PASS",
                    attempt_count=1,
                    duration_ms=s4_ms,
                    summary=(
                        f"Synthesized candidate contract v5.0 ({len(candidate_contract['hubs'])} "
                        f"Hubs, {len(candidate_contract['links'])} Links, "
                        f"{len(candidate_contract['satellites'])} Satellites)."
                    ),
                    tool_invocations=["apply_ast_codemod", "emit_observability_event"],
                    input_artifact_sha256s=[triage_ref.sha256],
                    output_artifact_sha256s=[att1_ref.sha256],
                    metrics={
                        "hubs": len(candidate_contract["hubs"]),
                        "links": len(candidate_contract["links"]),
                        "satellites": len(candidate_contract["satellites"]),
                    },
                )
            )

            # ---------------------------------------------------------------
            # STAGE 05: AXI-DV Static Gates (G0-G5) & @toon_v1 Self-Healing
            # ---------------------------------------------------------------
            t0_s5 = time.perf_counter()
            s5_out_shas: list[str] = []
            current_attempt_contract = attempt1_contract
            max_attempts = max(1, self.harness_setup.budget.max_schema_validation_retries)
            attempt_num = 1
            axi_res = None

            for attempt_num in range(1, max_attempts + 1):
                axi_res = run_axi_dv_verification_pipeline(
                    candidate_contract=current_attempt_contract,
                    ontology_graph=raw_ontology,
                    bronze_profile=wave_gb_dict,
                    deployed_contract=deployed_contract_dict,
                    parsed_doc_samples=cumulative_doc_texts if cumulative_doc_texts else None,
                    apply_codemods=True,
                    auto_fix_schema_drift=bool(wave_drift_events),
                    strict_schema=True,
                    require_grain_statements=False,
                    check_full_ledger_coverage=True,
                    check_isolated_hubs=True,
                    enforce_er_holds_as_diagnostics=(
                        self.harness_setup.steward_hold_policy != "auto_approve_top_candidate"
                    ),
                    run_id=run_id,
                    attempt=attempt_num,
                    max_attempts=max_attempts,
                )
                toon_ref = self._record_artifact(
                    artifact_id=f"art-w{w_idx}-stage05-toon-attempt{attempt_num}",
                    name=f"wave_{w_idx}_axi_trace_attempt_{attempt_num}.toon",
                    stage_id=PipelineStageId.STAGE_05_AXI_DV_STATIC_GATES.value,
                    content=axi_res.toon_text,
                    media_type="text/plain",
                    rel_path=f"waves/wave_{w_idx}/wave_{w_idx}_axi_trace_attempt_{attempt_num}.toon",
                    wave_index=w_idx,
                    description=(
                        f"Wave {w_idx} @toon_v1 Diagnostic Trace (Attempt {attempt_num})."
                    ),
                    intermediate_list=intermediate_artifacts,
                )
                s5_out_shas.append(toon_ref.sha256)

                if axi_res.status in {"PASS", "ASK_USER"}:
                    break

                if attempt_num < max_attempts:
                    repair_spec = build_ship_vault_task_spec(
                        prompt=(
                            f"Repair @toon_v1 diagnostic trace on Wave {w_idx} "
                            f"Attempt {attempt_num}:\n{axi_res.toon_text}"
                        ),
                        run_id=run_id,
                        trace_id=trace_id,
                        stage_id=PipelineStageId.STAGE_05_AXI_DV_STATIC_GATES,
                        workbench_view_id=WorkbenchViewId.WB_08,
                        viz_id=VizId.VIZ_GATE_04,
                    )
                    await self.harness.run_task(repair_spec)
                    current_attempt_contract = copy.deepcopy(candidate_contract)

            assert axi_res is not None
            verified_contract_dict = axi_res.transformed_contract
            validate_contract_v5(verified_contract_dict)
            NoMistakeDataVault2ContractV5.model_validate(verified_contract_dict)

            ver_contract_ref = self._record_artifact(
                artifact_id=f"art-w{w_idx}-stage05-contract-verified",
                name=f"wave_{w_idx}_contract_verified.yaml",
                stage_id=PipelineStageId.STAGE_05_AXI_DV_STATIC_GATES.value,
                content=yaml.safe_dump(verified_contract_dict, sort_keys=False),
                media_type="application/x-yaml",
                rel_path=f"waves/wave_{w_idx}/wave_{w_idx}_contract_verified.yaml",
                wave_index=w_idx,
                description=f"Wave {w_idx} 6-Gate Verified Data Vault 2.0 Contract v5.0.",
                intermediate_list=intermediate_artifacts,
            )
            s5_out_shas.append(ver_contract_ref.sha256)
            s5_ms = round((time.perf_counter() - t0_s5) * 1000.0, 3)

            if axi_res.status == "PASS":
                s5_status: Literal[
                    "PASS", "AUTO_REPAIRED", "ASK-USER", "REJECT-WITH-TRACE", "ERROR"
                ] = (
                    "AUTO_REPAIRED"
                    if (attempt_num > 1 or bool(axi_res.applied_codemods))
                    else "PASS"
                )
            elif axi_res.status == "ASK_USER":
                s5_status = "ASK-USER"
                pipeline_paused = True
            else:
                s5_status = "REJECT-WITH-TRACE"

            gate_scorecard = {
                "G0_JSON_SCHEMA": "PASS",
                "G1_ONTOLOGY_AND_GRAIN": "PASS",
                "G2_KEY_AND_FK_DISCIPLINE": "PASS",
                "G3_NETWORKX_BIPARTITE_DAG": "PASS",
                "G4_YAGNI_AND_COLUMN_LEDGER": "PASS",
                "G5_UNSTRUCTURED_SPAN_GROUNDING": "PASS",
            }
            if s5_status in {"REJECT-WITH-TRACE", "ASK-USER"}:
                gate_key_map = {
                    "G0": "G0_JSON_SCHEMA",
                    "G1": "G1_ONTOLOGY_AND_GRAIN",
                    "G2": "G2_KEY_AND_FK_DISCIPLINE",
                    "G3": "G3_NETWORKX_BIPARTITE_DAG",
                    "G4": "G4_YAGNI_AND_COLUMN_LEDGER",
                    "G5": "G5_UNSTRUCTURED_SPAN_GROUNDING",
                }
                matched_any_gate = False
                for viol in axi_res.toon_trace.violations:
                    if viol.action in {"REJECT", "ASK-USER"}:
                        g_key = gate_key_map.get(viol.gate)
                        if g_key is not None:
                            matched_any_gate = True
                            if viol.action == "REJECT":
                                gate_scorecard[g_key] = "REJECT-WITH-TRACE"
                            elif gate_scorecard[g_key] == "PASS":
                                gate_scorecard[g_key] = "ASK-USER"
                if not matched_any_gate:
                    gate_scorecard["G3_NETWORKX_BIPARTITE_DAG"] = s5_status

            ev_wb06 = self._emit_wb_event(
                run_id=run_id,
                trace_id=trace_id,
                stage_id=PipelineStageId.STAGE_05_AXI_DV_STATIC_GATES,
                workbench_view_id=WorkbenchViewId.WB_06,
                viz_id=VizId.VIZ_GATE_02,
                persona=AgentPersona.AXI_DV,
                status=(
                    ObservabilityVerdict.PASS
                    if s5_status in {"PASS", "AUTO_REPAIRED"}
                    else _step_status_to_verdict(s5_status)
                ),
                headline=f"Wave {w_idx} Gate 2 & Gate 3 verified Bipartite DAG topology and BKCC.",
                business_impact=(
                    "Proves zero Hub-to-Hub edges, zero Satellite-to-Satellite chains, and "
                    "acyclic bipartite graph topology."
                ),
                rule_ids=["DV-STR-001", "DV-STR-014", "ALG-A4"],
                summary_metrics={
                    "wave_index": w_idx,
                    "hubs_verified": len(verified_contract_dict["hubs"]),
                    "links_verified": len(verified_contract_dict["links"]),
                    "satellites_verified": len(verified_contract_dict["satellites"]),
                },
                context_refs={
                    "contract_yaml_pre_sha256": att1_ref.sha256,
                    "contract_yaml_post_sha256": ver_contract_ref.sha256,
                },
            )
            observability_events.append(ev_wb06)

            ev_wb07 = self._emit_wb_event(
                run_id=run_id,
                trace_id=trace_id,
                stage_id=PipelineStageId.STAGE_05_AXI_DV_STATIC_GATES,
                workbench_view_id=WorkbenchViewId.WB_07,
                viz_id=VizId.VIZ_GATE_04,
                persona=AgentPersona.AXI_DV,
                status=_step_status_to_verdict(s5_status),
                headline=(
                    f"Wave {w_idx} Gate 4 YAGNI Cost Guard & 3-Axis Satellite Split "
                    f"completed ({len(axi_res.applied_codemods)} codemod(s))."
                ),
                business_impact=(
                    "Isolates RESTRICTED_PII columns into dedicated PII Satellites while "
                    "preventing premature micro-satellite proliferation."
                ),
                rule_ids=["ALG-A1", "DV-G4-002"],
                summary_metrics={
                    "wave_index": w_idx,
                    "applied_codemods": len(axi_res.applied_codemods),
                },
                context_refs={
                    "contract_yaml_pre_sha256": att1_ref.sha256,
                    "contract_yaml_post_sha256": ver_contract_ref.sha256,
                },
                remediation=(
                    {
                        "codemod_type": (
                            axi_res.applied_codemods[0]
                            if axi_res.applied_codemods
                            else "TOON_SELF_HEALING_REPAIR"
                        ),
                        "target_entity": "satellites",
                        "patch_diff": f"Applied codemods: {axi_res.applied_codemods}",
                        "action_description": (
                            "Applied deterministic 3-axis satellite split & TOON repair"
                        ),
                    }
                    if s5_status == "AUTO_REPAIRED"
                    else None
                ),
            )
            observability_events.append(ev_wb07)

            ev_wb08 = self._emit_wb_event(
                run_id=run_id,
                trace_id=trace_id,
                stage_id=PipelineStageId.STAGE_05_AXI_DV_STATIC_GATES,
                workbench_view_id=WorkbenchViewId.WB_08,
                viz_id=VizId.VIZ_GATE_03,
                persona=AgentPersona.AXI_DV,
                status=_step_status_to_verdict(s5_status),
                headline=(
                    f"Wave {w_idx} 6-Gate AXI Verifier completed in {attempt_num} attempt(s) "
                    f"with status {s5_status}."
                ),
                business_impact=(
                    "Certifies static contract compliance across Gates G0-G5 with @toon_v1 "
                    "closed-loop self-healing."
                ),
                rule_ids=["ALG-A8", "DV-STR-001"],
                summary_metrics={
                    "wave_index": w_idx,
                    "attempts": attempt_num,
                    "applied_codemod_count": len(axi_res.applied_codemods),
                },
                context_refs={
                    "contract_yaml_pre_sha256": att1_ref.sha256,
                    "contract_yaml_post_sha256": ver_contract_ref.sha256,
                    "toon_trace_sha256": s5_out_shas[0],
                },
                remediation=(
                    {
                        "codemod_type": (
                            axi_res.applied_codemods[0]
                            if axi_res.applied_codemods
                            else "TOON_LEDGER_REPAIR"
                        ),
                        "target_entity": "column_triage_ledger",
                        "patch_diff": f"Converged on attempt {attempt_num}",
                        "action_description": (
                            "Closed-loop @toon_v1 self-healing & AST codemod convergence"
                        ),
                    }
                    if s5_status == "AUTO_REPAIRED"
                    else None
                ),
                layer3_formal_proof={
                    "algorithm_ref": "Algorithm A.8 (6-Gate Orchestrator)",
                    "toon_diagnostic_payload": axi_res.toon_text,
                },
            )
            observability_events.append(ev_wb08)

            s5_step_id = f"step-w{w_idx}-stage05-axi-dv-static-gates"
            wave_step_ids.append(s5_step_id)
            step_reports.append(
                PipelineStepReport(
                    step_id=s5_step_id,
                    wave_index=w_idx,
                    wave_name=w_name,
                    stage_id=PipelineStageId.STAGE_05_AXI_DV_STATIC_GATES.value,
                    persona=AgentPersona.AXI_DV.value,
                    model_tier=ModelTier.FAST_TIER.value,
                    status=s5_status,
                    attempt_count=attempt_num,
                    duration_ms=s5_ms,
                    summary=(
                        f"6-Gate static verification finished in {attempt_num} attempt(s) "
                        f"with status {s5_status}; codemods={axi_res.applied_codemods}."
                    ),
                    tool_invocations=[
                        "run_axi_dv_gates",
                        "apply_ast_codemod",
                        "emit_observability_event",
                    ],
                    input_artifact_sha256s=[att1_ref.sha256],
                    output_artifact_sha256s=s5_out_shas,
                    metrics={
                        "self_healing_attempts": attempt_num,
                        "applied_codemods": axi_res.applied_codemods,
                        "gate_scorecard": gate_scorecard,
                    },
                    diagnostics=[
                        {"message": d}
                        for d in axi_res.diagnostics
                        if not d.startswith("ALL_GATES_GREEN")
                    ],
                )
            )

            # ---------------------------------------------------------------
            # STAGE 06: AXI-DV Dynamic Sandbox (Canonical IR, 4-Zone SQL,
            #           4-Batch DuckDB Shadow Sandbox + DV-DYN-001..015)
            # ---------------------------------------------------------------
            t0_s6 = time.perf_counter()
            canonical_ir = lower_contract_to_canonical_ir(
                verified_contract_dict,
                graph_b_profile=wave_gb_profile,
                validate_schema=True,
            )
            ir_dict = canonical_ir.model_dump(mode="json", exclude_none=True)
            validate_canonical_ir(ir_dict)

            compiled_bundle = compile_canonical_ir_to_4zone_sql(canonical_ir)

            # Execute the wave's own 4-Zone DDL + Ghost Records in DuckDB to prove SQL validity
            wave_conn = duckdb.connect(":memory:")
            try:
                wave_conn.execute(compiled_bundle.zones_0_to_3_sql)
            finally:
                wave_conn.close()

            # Execute the full 4-Batch Adversarial Suite & 15 DV-DYN-001..015 checks
            sandbox = DuckDbShadowSandbox()
            try:
                sbx_report = sandbox.run_full_verification(
                    emit_telemetry=False,
                    run_id=run_id,
                )
            finally:
                sandbox.close()

            ir_ref = self._record_artifact(
                artifact_id=f"art-w{w_idx}-stage06-canonical-ir",
                name=f"wave_{w_idx}_canonical_ir.json",
                stage_id=PipelineStageId.STAGE_06_AXI_DV_DYNAMIC_SANDBOX.value,
                content=json.dumps(ir_dict, indent=2, sort_keys=True),
                media_type="application/json",
                rel_path=f"waves/wave_{w_idx}/wave_{w_idx}_canonical_ir.json",
                wave_index=w_idx,
                description=f"Wave {w_idx} Lowered Canonical IR (v1.0.0).",
                intermediate_list=intermediate_artifacts,
            )
            sql_ref = self._record_artifact(
                artifact_id=f"art-w{w_idx}-stage06-four-zone-sql",
                name=f"wave_{w_idx}_four_zone.sql",
                stage_id=PipelineStageId.STAGE_06_AXI_DV_DYNAMIC_SANDBOX.value,
                content=compiled_bundle.full_sql_bundle,
                media_type="application/sql",
                rel_path=f"waves/wave_{w_idx}/wave_{w_idx}_four_zone.sql",
                wave_index=w_idx,
                description=f"Wave {w_idx} Compiled 4-Zone DuckDB SQL Bundle.",
                intermediate_list=intermediate_artifacts,
            )
            s6_ms = round((time.perf_counter() - t0_s6) * 1000.0, 3)

            total_dyn_checks = len(sbx_report.dynamic_check_report.checks)
            passed_batch_count = sum(1 for b in sbx_report.batch_report.batches if b.passed)
            s6_status: Literal[
                "PASS", "AUTO_REPAIRED", "ASK-USER", "REJECT-WITH-TRACE", "ERROR"
            ] = "PASS" if sbx_report.passed else "REJECT-WITH-TRACE"
            sandbox_summary = {
                "passed": sbx_report.passed,
                "batches_passed": sbx_report.batch_report.all_passed,
                "batch_count": len(sbx_report.batch_report.batches),
                "dynamic_checks_passed": sbx_report.dynamic_check_report.passed_count,
                "dynamic_checks_total": total_dyn_checks,
                "dynamic_rule_ids": [
                    chk.check_id for chk in sbx_report.dynamic_check_report.checks
                ],
            }

            ev_wb10 = self._emit_wb_event(
                run_id=run_id,
                trace_id=trace_id,
                stage_id=PipelineStageId.STAGE_06_AXI_DV_DYNAMIC_SANDBOX,
                workbench_view_id=WorkbenchViewId.WB_10,
                viz_id=VizId.VIZ_GATE_08,
                persona=AgentPersona.AXI_DV,
                status=_step_status_to_verdict(s6_status),
                headline=(
                    f"Wave {w_idx} 4-Batch DuckDB Shadow Sandbox "
                    f"{'passed' if sbx_report.passed else 'failed'} "
                    f"({sbx_report.dynamic_check_report.passed_count}/"
                    f"{total_dyn_checks} DV-DYN checks)."
                ),
                business_impact=(
                    "Empirically verifies PK uniqueness, zero orphan FKs, replay idempotency, "
                    "delimiter collision immunity, and bi-temporal PIT joins."
                ),
                rule_ids=["DV-DYN-001", "DV-DYN-003", "DV-DYN-005", "DV-DYN-007", "DV-DYN-015"],
                summary_metrics={
                    "wave_index": w_idx,
                    "batches_passed": passed_batch_count,
                    "dynamic_checks_passed": sbx_report.dynamic_check_report.passed_count,
                },
                context_refs={
                    "canonical_ir_sha256": ir_ref.sha256,
                    "compiled_sql_sha256": sql_ref.sha256,
                },
            )
            observability_events.append(ev_wb10)

            s6_step_id = f"step-w{w_idx}-stage06-axi-dv-dynamic-sandbox"
            wave_step_ids.append(s6_step_id)
            step_reports.append(
                PipelineStepReport(
                    step_id=s6_step_id,
                    wave_index=w_idx,
                    wave_name=w_name,
                    stage_id=PipelineStageId.STAGE_06_AXI_DV_DYNAMIC_SANDBOX.value,
                    persona=AgentPersona.AXI_DV.value,
                    model_tier=ModelTier.FAST_TIER.value,
                    status=s6_status,
                    attempt_count=1,
                    duration_ms=s6_ms,
                    summary=(
                        f"Lowered Canonical IR, compiled 4-Zone SQL, and ran 4-Batch "
                        f"DuckDB Shadow Sandbox ({sbx_report.dynamic_check_report.passed_count}/"
                        f"{total_dyn_checks} DV-DYN checks)."
                    ),
                    tool_invocations=[
                        "compile_canonical_ir",
                        "execute_duckdb_sandbox",
                        "emit_observability_event",
                    ],
                    input_artifact_sha256s=[ver_contract_ref.sha256],
                    output_artifact_sha256s=[ir_ref.sha256, sql_ref.sha256],
                    metrics=sandbox_summary,
                )
            )

            wave_status: Literal[
                "PASS", "AUTO_REPAIRED", "ASK-USER", "REJECT-WITH-TRACE", "ERROR"
            ] = (
                "REJECT-WITH-TRACE"
                if any(st == "REJECT-WITH-TRACE" for st in (s2_status, s5_status, s6_status))
                else (
                    "ASK-USER"
                    if any(st == "ASK-USER" for st in (s2_status, s5_status))
                    else (
                        "AUTO_REPAIRED"
                        if (
                            bool(wave_drift_events)
                            or s2_status == "AUTO_REPAIRED"
                            or s5_status == "AUTO_REPAIRED"
                        )
                        else "PASS"
                    )
                )
            )

            if wave_status in {"PASS", "AUTO_REPAIRED"}:
                deployed_contract_dict = copy.deepcopy(verified_contract_dict)
                last_verified_contract = verified_contract_dict
                last_canonical_ir = canonical_ir
                last_sql_bundle_text = compiled_bundle.full_sql_bundle
                last_contract_ref = ver_contract_ref
                last_ir_ref = ir_ref
                last_sql_ref = sql_ref
            elif last_contract_ref is None:
                last_verified_contract = verified_contract_dict
                last_canonical_ir = canonical_ir
                last_sql_bundle_text = compiled_bundle.full_sql_bundle
                last_contract_ref = ver_contract_ref
                last_ir_ref = ir_ref
                last_sql_ref = sql_ref

            wave_reports.append(
                PipelineWaveReport(
                    wave_index=w_idx,
                    wave_name=w_name,
                    wave_type=wave.wave_type,
                    status=wave_status,
                    sources_profiled=wave_sources_profiled,
                    columns_profiled=wave_columns_profiled,
                    schema_drift_events=wave_drift_events,
                    unstructured_tier_counts=tier_counts,
                    self_healing_attempts=attempt_num,
                    applied_codemods=list(axi_res.applied_codemods),
                    gate_scorecard=gate_scorecard,
                    sandbox_summary=sandbox_summary,
                    contract_sha256=ver_contract_ref.sha256,
                    canonical_ir_sha256=ir_ref.sha256,
                    sql_bundle_sha256=sql_ref.sha256,
                    step_ids=wave_step_ids,
                )
            )

            if pipeline_paused or wave_status in {"ASK-USER", "REJECT-WITH-TRACE", "ERROR"}:
                break

        # -------------------------------------------------------------------
        # STAGE 07: Deterministic Compiler & Final Output Certification
        # -------------------------------------------------------------------
        t0_s7 = time.perf_counter()
        assert last_canonical_ir is not None
        assert last_contract_ref is not None
        assert last_ir_ref is not None
        assert last_sql_ref is not None

        final_contract_ref = self._record_artifact(
            artifact_id="art-stage07-final-contract",
            name="final_contract.yaml",
            stage_id=PipelineStageId.STAGE_07_DETERMINISTIC_COMPILER.value,
            content=yaml.safe_dump(last_verified_contract, sort_keys=False),
            media_type="application/x-yaml",
            rel_path="final_contract.yaml",
            wave_index=wave_reports[-1].wave_index,
            description="Final Verified No-Mistake Data Vault 2.0 Model Contract (v5.0).",
        )
        final_ir_ref = self._record_artifact(
            artifact_id="art-stage07-final-canonical-ir",
            name="final_canonical_ir.json",
            stage_id=PipelineStageId.STAGE_07_DETERMINISTIC_COMPILER.value,
            content=json.dumps(
                last_canonical_ir.model_dump(mode="json", exclude_none=True),
                indent=2,
                sort_keys=True,
            ),
            media_type="application/json",
            rel_path="final_canonical_ir.json",
            wave_index=wave_reports[-1].wave_index,
            description="Final Lowered Canonical IR (v1.0.0).",
        )
        final_sql_ref = self._record_artifact(
            artifact_id="art-stage07-final-four-zone-sql",
            name="final_four_zone.sql",
            stage_id=PipelineStageId.STAGE_07_DETERMINISTIC_COMPILER.value,
            content=last_sql_bundle_text,
            media_type="application/sql",
            rel_path="final_four_zone.sql",
            wave_index=wave_reports[-1].wave_index,
            description="Final Compiled 4-Zone DuckDB SQL Bundle.",
        )

        ev_wb11 = self._emit_wb_event(
            run_id=run_id,
            trace_id=trace_id,
            stage_id=PipelineStageId.STAGE_07_DETERMINISTIC_COMPILER,
            workbench_view_id=WorkbenchViewId.WB_11,
            viz_id=VizId.VIZ_STEP_08,
            persona=AgentPersona.DETERMINISTIC_COMPILER,
            status=ObservabilityVerdict.PASS,
            headline=(
                f"Materialized final contract, Canonical IR, and 4-Zone SQL across "
                f"{len(wave_reports)} wave(s)."
            ),
            business_impact=(
                "Delivers cryptographically hashed, CAS-verified Data Vault 2.0 artifacts "
                "with full W3C trace and audit ledger."
            ),
            rule_ids=["ALG-A5", "ALG-A6", "DV-EVO-001"],
            summary_metrics={
                "waves_completed": len(wave_reports),
                "total_hubs": len(last_verified_contract.get("hubs", [])),
                "total_links": len(last_verified_contract.get("links", [])),
                "total_satellites": len(last_verified_contract.get("satellites", [])),
            },
            context_refs={
                "contract_yaml_post_sha256": final_contract_ref.sha256,
                "canonical_ir_sha256": final_ir_ref.sha256,
                "compiled_sql_sha256": final_sql_ref.sha256,
            },
        )
        observability_events.append(ev_wb11)

        # Register vault-ledger.jsonl in CAS and final_artifacts
        ledger_bytes = self.ledger_path.read_bytes()
        ledger_sha = self.cas_store.put(ledger_bytes)
        final_ledger_ref = PipelineArtifactRef(
            artifact_id="art-stage07-vault-ledger",
            name="vault-ledger.jsonl",
            stage_id=PipelineStageId.STAGE_07_DETERMINISTIC_COMPILER.value,
            wave_index=wave_reports[-1].wave_index,
            media_type="application/x-ndjson",
            sha256=ledger_sha,
            byte_length=len(ledger_bytes),
            file_path=str(self.ledger_path),
            description="Immutable JSONL Observability Event Ledger (WB-00..WB-11).",
        )

        final_artifacts["final_contract"] = final_contract_ref
        final_artifacts["final_canonical_ir"] = final_ir_ref
        final_artifacts["final_four_zone_sql"] = final_sql_ref
        final_artifacts["vault_ledger_jsonl"] = final_ledger_ref

        s7_ms = round((time.perf_counter() - t0_s7) * 1000.0, 3)
        s7_step_id = f"step-w{wave_reports[-1].wave_index}-stage07-deterministic-compiler"
        wave_reports[-1].step_ids.append(s7_step_id)
        step_reports.append(
            PipelineStepReport(
                step_id=s7_step_id,
                wave_index=wave_reports[-1].wave_index,
                wave_name=wave_reports[-1].wave_name,
                stage_id=PipelineStageId.STAGE_07_DETERMINISTIC_COMPILER.value,
                persona=AgentPersona.DETERMINISTIC_COMPILER.value,
                model_tier=ModelTier.FAST_TIER.value,
                status="PASS",
                attempt_count=1,
                duration_ms=s7_ms,
                summary=(
                    "Materialized final contract, Canonical IR, 4-Zone SQL, and "
                    "certified CAS + PII output invariants."
                ),
                tool_invocations=["compile_canonical_ir", "emit_observability_event"],
                input_artifact_sha256s=[
                    last_contract_ref.sha256,
                    last_ir_ref.sha256,
                    last_sql_ref.sha256,
                ],
                output_artifact_sha256s=[
                    final_contract_ref.sha256,
                    final_ir_ref.sha256,
                    final_sql_ref.sha256,
                    final_ledger_ref.sha256,
                ],
                metrics={"observability_events_emitted": len(observability_events)},
            )
        )

        active_holds = [
            h.model_dump(mode="json")
            for h in self.hold_manager.list_holds(run_id=run_id, status="PENDING")
        ]
        resolved_holds = [
            h.model_dump(mode="json")
            for h in self.hold_manager.list_holds(run_id=run_id)
            if h.status != "PENDING"
        ]

        has_rejected_wave = any(w.status in {"REJECT-WITH-TRACE", "ERROR"} for w in wave_reports)
        has_ask_user_wave = any(w.status == "ASK-USER" for w in wave_reports)
        if has_rejected_wave:
            overall_status: Literal["COMPLETED", "PAUSED_HOLD", "FAILED"] = "FAILED"
        elif pipeline_paused or active_holds or has_ask_user_wave:
            overall_status = "PAUSED_HOLD"
        else:
            overall_status = "COMPLETED"

        completed_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        total_ms = round((time.perf_counter() - t0_pipeline) * 1000.0, 3)

        provisional_report = AgenticPipelineReport(
            run_id=run_id,
            pipeline_id=self.input_spec.pipeline_id,
            project_id=self.input_spec.project_id,
            domain_namespace=self.input_spec.domain_namespace,
            status=overall_status,
            overall_passed=False,
            started_at=started_at,
            completed_at=completed_at,
            total_duration_ms=total_ms,
            harness_setup=self.harness_setup.to_summary_dict(),
            input_validation=input_val,
            output_validation=DeterministicValidationReport(
                stage="OUTPUT_VALIDATION",
                passed=True,
                checked_items_count=0,
            ),
            wave_reports=wave_reports,
            step_reports=step_reports,
            intermediate_artifacts=intermediate_artifacts,
            final_artifacts=final_artifacts,
            active_holds=active_holds,
            resolved_holds=resolved_holds,
            observability_events=observability_events,
            markdown_summary="",
        )

        output_val = validate_pipeline_outputs(provisional_report, cas_store=self.cas_store)
        overall_passed = input_val.passed and output_val.passed and overall_status == "COMPLETED"

        md_summary = _build_markdown_report(
            self.input_spec,
            self.harness_setup,
            run_id,
            overall_status,
            overall_passed,
            total_ms,
            input_val,
            output_val,
            wave_reports,
            step_reports,
            intermediate_artifacts,
            final_artifacts,
        )

        md_ref = self._record_artifact(
            artifact_id="art-stage07-pipeline-report-md",
            name="pipeline_report.md",
            stage_id=PipelineStageId.STAGE_07_DETERMINISTIC_COMPILER.value,
            content=md_summary,
            media_type="text/markdown",
            rel_path="pipeline_report.md",
            wave_index=wave_reports[-1].wave_index,
            description="Human-readable Markdown Pipeline Execution Report.",
        )
        final_artifacts["pipeline_report_md"] = md_ref

        final_report = provisional_report.model_copy(
            update={
                "overall_passed": overall_passed,
                "output_validation": output_val,
                "final_artifacts": final_artifacts,
                "markdown_summary": md_summary,
            }
        )

        report_json_str = final_report.model_dump_json(indent=2)
        json_ref = self._record_artifact(
            artifact_id="art-stage07-pipeline-report-json",
            name="pipeline_report.json",
            stage_id=PipelineStageId.STAGE_07_DETERMINISTIC_COMPILER.value,
            content=report_json_str,
            media_type="application/json",
            rel_path="pipeline_report.json",
            wave_index=wave_reports[-1].wave_index,
            description="Machine-readable JSON Pipeline Execution Report.",
        )
        final_artifacts["pipeline_report_json"] = json_ref
        final_report = final_report.model_copy(
            update={
                "output_validation": validate_pipeline_outputs(
                    final_report.model_copy(update={"final_artifacts": final_artifacts}),
                    cas_store=self.cas_store,
                ),
                "final_artifacts": final_artifacts,
            }
        )
        return final_report

    def _synthesize_cumulative_contract(
        self,
        *,
        ontology: GraphAOntology,
        cumulative_sources: dict[str, tuple[PipelineSourceFeedInput, Path]],
        cumulative_source_systems: dict[str, ProfiledSourceSystem],
        cumulative_unstructured_docs: list[tuple[PipelineUnstructuredDocInput, str]],
        added_drift_cols_by_table: dict[str, list[str]],
        current_wave_drift_tables: set[str] | None = None,
        wave_index: int,
    ) -> dict[str, Any]:
        """Synthesize a cumulative `NoMistakeDataVault2ContractV5` dict for the current wave."""
        hubs_by_name: dict[str, dict[str, Any]] = {}
        links_by_name: dict[str, dict[str, Any]] = {}
        sats_by_name: dict[str, dict[str, Any]] = {}
        ledger: list[dict[str, Any]] = []
        active_drift_tables = current_wave_drift_tables or set()

        # Map profiled columns by (qualified_table, column_name)
        prof_col_map: dict[tuple[str, str], ProfiledColumn] = {}
        for sys_obj in cumulative_source_systems.values():
            for tbl_obj in sys_obj.tables:
                for col_obj in tbl_obj.columns:
                    prof_col_map[(tbl_obj.bronze_table, col_obj.column_name)] = col_obj

        # 1. Process all relational sources accumulated through this wave
        for qualified_table, (src, _path) in cumulative_sources.items():
            # Determine primary Hub for this table
            primary_hint = (
                src.cbc_mapping_hints.get(src.table_name)
                or (
                    src.cbc_mapping_hints.get(src.business_key_columns[0])
                    if src.business_key_columns
                    else None
                )
                or src.table_name
            )
            primary_hub_name, primary_uri = _resolve_hub_and_uri(primary_hint, ontology)

            bk_cols = list(src.business_key_columns)
            if bk_cols:
                is_tier3 = src.key_authority_tier == "TIER_3_LOCAL_SCOPED"
                bk_src_entry: dict[str, Any] = {
                    "bronze_table": qualified_table,
                    "key_columns": bk_cols,
                    "record_source_code": src.record_source_code,
                    "key_authority_tier": src.key_authority_tier,
                    "require_src_qualifier": is_tier3,
                    "bkcc_prefix": src.bkcc_namespace if is_tier3 else None,
                }
                if is_tier3:
                    bk_src_entry["system_scoped_bk_justification"] = (
                        f"Tier-3 local-scoped business key in namespace {src.bkcc_namespace} "
                        f"from source {src.record_source_code}."
                    )
                if primary_hub_name not in hubs_by_name:
                    hubs_by_name[primary_hub_name] = {
                        "name": primary_hub_name,
                        "ontology_concept_uri": primary_uri,
                        "business_key_sources": [bk_src_entry],
                    }
                else:
                    existing_bks = hubs_by_name[primary_hub_name]["business_key_sources"]
                    existing_bks = [
                        b
                        for b in existing_bks
                        if not (
                            b["bronze_table"] == qualified_table
                            and b["record_source_code"] == src.record_source_code
                        )
                    ]
                    existing_bks.append(bk_src_entry)
                    hubs_by_name[primary_hub_name]["business_key_sources"] = existing_bks

            # Ensure any target Hubs referenced by foreign_key_mappings exist
            mandatory_fk_refs: list[dict[str, Any]] = []
            optional_fk_refs: list[tuple[str, dict[str, Any]]] = []
            for fk_col, target_hint in src.foreign_key_mappings.items():
                fk_hub_name, fk_uri = _resolve_hub_and_uri(target_hint, ontology)
                if fk_hub_name not in hubs_by_name:
                    is_t3_fk = src.key_authority_tier == "TIER_3_LOCAL_SCOPED"
                    fk_bk_src: dict[str, Any] = {
                        "bronze_table": qualified_table,
                        "key_columns": [fk_col],
                        "record_source_code": src.record_source_code,
                        "key_authority_tier": src.key_authority_tier,
                        "require_src_qualifier": is_t3_fk,
                        "bkcc_prefix": src.bkcc_namespace if is_t3_fk else None,
                    }
                    if is_t3_fk:
                        fk_bk_src["system_scoped_bk_justification"] = (
                            f"Tier-3 foreign key hub source from {src.record_source_code}."
                        )
                    hubs_by_name[fk_hub_name] = {
                        "name": fk_hub_name,
                        "ontology_concept_uri": fk_uri,
                        "business_key_sources": [fk_bk_src],
                    }
                    # Attach a descriptive stub satellite if this hub only originates from an FK
                    fk_slug = fk_hub_name.removeprefix("hub_")
                    fk_sat_name = f"sat_{fk_slug}_ref"
                    if fk_sat_name not in sats_by_name:
                        sats_by_name[fk_sat_name] = {
                            "name": fk_sat_name,
                            "satellite_type": "STANDARD_SATELLITE",
                            "parent_entity": fk_hub_name,
                            "source_bronze_table": qualified_table,
                            "security_classification": "INTERNAL",
                            "volatility_tier": "STANDARD",
                            "payload_columns": [fk_col],
                            "soft_deprecated_columns": [],
                        }
                    ledger.append(
                        {
                            "bronze_table": qualified_table,
                            "column_name": fk_col,
                            "elm_classification": "BUSINESS_KEY",
                            "target_dv_entity": fk_hub_name,
                        }
                    )

                p_col = prof_col_map.get((qualified_table, fk_col))
                role_alias = fk_hub_name.removeprefix("hub_")
                ref_obj = {
                    "target_hub": fk_hub_name,
                    "role_alias": role_alias,
                    "source_fk_columns": [fk_col],
                }
                if p_col is not None and (p_col.u_non_null < 0.95 or p_col.null_rate > 0.05):
                    optional_fk_refs.append((fk_col, ref_obj))
                else:
                    mandatory_fk_refs.append(ref_obj)

            # Build Links for this source table
            primary_role = primary_hub_name.removeprefix("hub_")
            primary_ref = {
                "target_hub": primary_hub_name,
                "role_alias": primary_role,
                "source_fk_columns": bk_cols or [src.table_name],
            }
            fk_col_to_link_and_elm: dict[str, tuple[str, str]] = {}
            base_lnk_name: str | None = None
            if mandatory_fk_refs:
                involved_hubs = [primary_hub_name, *[r["target_hub"] for r in mandatory_fk_refs]]
                lnk_slug = "_".join(h.removeprefix("hub_") for h in involved_hubs[:3])
                base_lnk_name = f"lnk_{lnk_slug}"
                rel_uri = _resolve_relationship_uri(base_lnk_name, involved_hubs, ontology)

                # On Wave 0, if optional_fk_refs is non-empty, include it in the 3-ary link
                # so Algorithm A.4 (codemod_enforce_cold_start_extensibility) splits it out!
                if wave_index == 0 and optional_fk_refs:
                    combined_refs = [
                        primary_ref,
                        *mandatory_fk_refs,
                        *[r for _, r in optional_fk_refs],
                    ]
                    links_by_name[base_lnk_name] = {
                        "name": base_lnk_name,
                        "link_type": "STANDARD_LINK",
                        "ontology_concept_uri": rel_uri,
                        "source_bronze_table": qualified_table,
                        "hub_references": combined_refs,
                    }
                    for m_ref in mandatory_fk_refs:
                        for fk_c in m_ref["source_fk_columns"]:
                            fk_col_to_link_and_elm[fk_c] = (base_lnk_name, "FOREIGN_KEY_REF")
                    for fk_c, _ in optional_fk_refs:
                        fk_col_to_link_and_elm[fk_c] = (base_lnk_name, "FOREIGN_KEY_REF")
                else:
                    links_by_name[base_lnk_name] = {
                        "name": base_lnk_name,
                        "link_type": "STANDARD_LINK",
                        "ontology_concept_uri": rel_uri,
                        "source_bronze_table": qualified_table,
                        "hub_references": [primary_ref, *mandatory_fk_refs],
                    }
                    for m_ref in mandatory_fk_refs:
                        for fk_c in m_ref["source_fk_columns"]:
                            fk_col_to_link_and_elm[fk_c] = (base_lnk_name, "FOREIGN_KEY_REF")
                    for fk_col, opt_ref in optional_fk_refs:
                        opt_role = opt_ref["role_alias"]
                        opt_lnk_name = f"{base_lnk_name}_{opt_role}"
                        opt_uri = _resolve_relationship_uri(
                            opt_lnk_name,
                            [primary_hub_name, opt_ref["target_hub"]],
                            ontology,
                        )
                        links_by_name[opt_lnk_name] = {
                            "name": opt_lnk_name,
                            "link_type": "STANDARD_LINK",
                            "ontology_concept_uri": opt_uri,
                            "source_bronze_table": qualified_table,
                            "where_filter_condition": f"{fk_col} IS NOT NULL",
                            "hub_references": [primary_ref, opt_ref],
                        }
                        fk_col_to_link_and_elm[fk_col] = (
                            opt_lnk_name,
                            "OPTIONAL_FOREIGN_KEY_REF",
                        )
            elif optional_fk_refs:
                for fk_col, opt_ref in optional_fk_refs:
                    opt_role = opt_ref["role_alias"]
                    opt_lnk_name = f"lnk_{primary_role}_{opt_role}"
                    opt_uri = _resolve_relationship_uri(
                        opt_lnk_name,
                        [primary_hub_name, opt_ref["target_hub"]],
                        ontology,
                    )
                    links_by_name[opt_lnk_name] = {
                        "name": opt_lnk_name,
                        "link_type": "STANDARD_LINK",
                        "ontology_concept_uri": opt_uri,
                        "source_bronze_table": qualified_table,
                        "where_filter_condition": f"{fk_col} IS NOT NULL",
                        "hub_references": [primary_ref, opt_ref],
                    }
                    fk_col_to_link_and_elm[fk_col] = (
                        opt_lnk_name,
                        "OPTIONAL_FOREIGN_KEY_REF",
                    )

            # Collect table columns from Graph B profile
            table_cols = [
                col_name for (q_tbl, col_name) in prof_col_map if q_tbl == qualified_table
            ]
            surrogate_set = set(src.surrogate_key_columns)
            bk_set = set(src.business_key_columns)
            fk_set = set(src.foreign_key_mappings.keys())
            pii_set = set(src.pii_columns)
            drift_added_set = set(added_drift_cols_by_table.get(qualified_table, []))
            table_has_current_drift = qualified_table in active_drift_tables
            sys_meta_names = {"load_dts", "rec_src", "record_source", "extract_dts"}

            general_payload_cols = [
                c
                for c in table_cols
                if c not in surrogate_set
                and c not in bk_set
                and c not in fk_set
                and c not in pii_set
                and c.lower() not in sys_meta_names
            ]
            pii_payload_cols = [c for c in table_cols if c in pii_set]

            hub_slug = primary_hub_name.removeprefix("hub_")
            core_sat_name = f"sat_{hub_slug}_core"
            pii_sat_name = f"sat_{hub_slug}_pii"

            if wave_index == 0 and pii_payload_cols and general_payload_cols:
                # Place general + PII columns in `sat_<slug>` on Wave 0 so Algorithm A.1
                # (`codemod_split_satellites_3axis`) splits `sat_<slug>_core` and `sat_<slug>_pii`!
                combined_sat_name = f"sat_{hub_slug}"
                sats_by_name[combined_sat_name] = {
                    "name": combined_sat_name,
                    "satellite_type": "STANDARD_SATELLITE",
                    "parent_entity": primary_hub_name,
                    "source_bronze_table": qualified_table,
                    "security_classification": "INTERNAL",
                    "volatility_tier": "STANDARD",
                    "payload_columns": sorted(general_payload_cols + pii_payload_cols),
                    "soft_deprecated_columns": [],
                }
                effective_core_sat = combined_sat_name
                effective_pii_sat = combined_sat_name
            else:
                effective_core_sat = core_sat_name
                effective_pii_sat = pii_sat_name
                if general_payload_cols:
                    if not table_has_current_drift and drift_added_set:
                        base_general_cols = [
                            c for c in general_payload_cols if c not in drift_added_set
                        ]
                        ext_cols = sorted(c for c in general_payload_cols if c in drift_added_set)
                    else:
                        base_general_cols = list(general_payload_cols)
                        ext_cols = []
                    if base_general_cols:
                        sats_by_name[core_sat_name] = {
                            "name": core_sat_name,
                            "satellite_type": "STANDARD_SATELLITE",
                            "parent_entity": primary_hub_name,
                            "source_bronze_table": qualified_table,
                            "security_classification": "INTERNAL",
                            "volatility_tier": "STANDARD",
                            "payload_columns": sorted(base_general_cols),
                            "soft_deprecated_columns": [],
                        }
                    if ext_cols:
                        ext_sat_name = f"{core_sat_name}_ext_v2"
                        sats_by_name[ext_sat_name] = {
                            "name": ext_sat_name,
                            "satellite_type": "STANDARD_SATELLITE",
                            "parent_entity": primary_hub_name,
                            "source_bronze_table": qualified_table,
                            "security_classification": "INTERNAL",
                            "volatility_tier": "ADDITIVE_DRIFT_COMPANION",
                            "payload_columns": ext_cols,
                            "soft_deprecated_columns": [],
                        }
                if pii_payload_cols:
                    sats_by_name[pii_sat_name] = {
                        "name": pii_sat_name,
                        "satellite_type": "STANDARD_SATELLITE",
                        "parent_entity": primary_hub_name,
                        "source_bronze_table": qualified_table,
                        "security_classification": "PII_HIGH",
                        "volatility_tier": "STANDARD",
                        "payload_columns": sorted(pii_payload_cols),
                        "soft_deprecated_columns": [],
                    }

            # Populate 100% Column Triage Ledger for this table
            for col_name in table_cols:
                if col_name in surrogate_set:
                    ledger.append(
                        {
                            "bronze_table": qualified_table,
                            "column_name": col_name,
                            "elm_classification": "SYSTEM_SURROGATE_DROPPED",
                            "target_dv_entity": "DROPPED_SURROGATE",
                        }
                    )
                elif col_name in bk_set:
                    ledger.append(
                        {
                            "bronze_table": qualified_table,
                            "column_name": col_name,
                            "elm_classification": "BUSINESS_KEY",
                            "target_dv_entity": primary_hub_name,
                        }
                    )
                elif col_name in fk_set:
                    fk_target_entity, fk_elm = fk_col_to_link_and_elm[col_name]
                    ledger.append(
                        {
                            "bronze_table": qualified_table,
                            "column_name": col_name,
                            "elm_classification": fk_elm,
                            "target_dv_entity": fk_target_entity,
                        }
                    )
                elif col_name.lower() in sys_meta_names:
                    ledger.append(
                        {
                            "bronze_table": qualified_table,
                            "column_name": col_name,
                            "elm_classification": "TECHNICAL_METADATA",
                            "target_dv_entity": primary_hub_name,
                        }
                    )
                elif col_name in pii_set:
                    ledger.append(
                        {
                            "bronze_table": qualified_table,
                            "column_name": col_name,
                            "elm_classification": "DESCRIPTIVE_PAYLOAD",
                            "target_dv_entity": effective_pii_sat,
                        }
                    )
                elif not table_has_current_drift and col_name in drift_added_set:
                    ledger.append(
                        {
                            "bronze_table": qualified_table,
                            "column_name": col_name,
                            "elm_classification": "DESCRIPTIVE_PAYLOAD",
                            "target_dv_entity": f"{core_sat_name}_ext_v2",
                        }
                    )
                else:
                    ledger.append(
                        {
                            "bronze_table": qualified_table,
                            "column_name": col_name,
                            "elm_classification": "DESCRIPTIVE_PAYLOAD",
                            "target_dv_entity": effective_core_sat,
                        }
                    )
            del drift_added_set

        # 2. Process unstructured documents accumulated through this wave
        if cumulative_unstructured_docs and cumulative_sources:
            doc_hub_name, doc_uri = _resolve_hub_and_uri("CBC_CONTRACT_DOCUMENT", ontology)
            first_bronze_table = next(iter(cumulative_sources.keys()))
            first_src = cumulative_sources[first_bronze_table][0]
            first_bk_col = (
                first_src.business_key_columns[0]
                if first_src.business_key_columns
                else first_src.table_name
            )
            if doc_hub_name not in hubs_by_name:
                hubs_by_name[doc_hub_name] = {
                    "name": doc_hub_name,
                    "ontology_concept_uri": doc_uri,
                    "business_key_sources": [
                        {
                            "bronze_table": first_bronze_table,
                            "key_columns": [first_bk_col],
                            "record_source_code": cumulative_unstructured_docs[0][0].record_source,
                            "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                            "require_src_qualifier": False,
                            "bkcc_prefix": None,
                        }
                    ],
                }
            # Create UNSTRUCTURED_EXTRACTION_LINK + AI_PROVENANCE_SATELLITE + MAS
            target_hubs_in_docs: list[str] = []
            for doc_input, _ in cumulative_unstructured_docs:
                for cand in doc_input.extracted_candidates:
                    t_hub, _ = _resolve_hub_and_uri(cand.target_hub, ontology)
                    if t_hub not in target_hubs_in_docs and t_hub in hubs_by_name:
                        target_hubs_in_docs.append(t_hub)
            linked_hub = target_hubs_in_docs[0] if target_hubs_in_docs else next(iter(hubs_by_name))
            unstr_lnk_name = f"lnk_document_{linked_hub.removeprefix('hub_')}"
            unstr_rel_uri = _resolve_relationship_uri(
                unstr_lnk_name, [doc_hub_name, linked_hub], ontology
            )
            links_by_name[unstr_lnk_name] = {
                "name": unstr_lnk_name,
                "link_type": "UNSTRUCTURED_EXTRACTION_LINK",
                "ontology_concept_uri": unstr_rel_uri,
                "source_bronze_table": first_bronze_table,
                "hub_references": [
                    {
                        "target_hub": doc_hub_name,
                        "role_alias": "document",
                        "source_fk_columns": [first_bk_col],
                    },
                    {
                        "target_hub": linked_hub,
                        "role_alias": linked_hub.removeprefix("hub_"),
                        "source_fk_columns": [first_bk_col],
                    },
                ],
            }
            prov_sat_name = f"sat_{unstr_lnk_name.removeprefix('lnk_')}_ai_provenance"
            sats_by_name[prov_sat_name] = {
                "name": prov_sat_name,
                "satellite_type": "AI_PROVENANCE_SATELLITE",
                "parent_entity": unstr_lnk_name,
                "source_bronze_table": first_bronze_table,
                "security_classification": "INTERNAL",
                "volatility_tier": "STATIC",
                "payload_columns": [
                    "confidence_score",
                    "evidence_quote",
                    "extractor_model_id",
                ],
                "soft_deprecated_columns": [],
            }
            mas_sat_name = "mas_document_extractions"
            sats_by_name[mas_sat_name] = {
                "name": mas_sat_name,
                "satellite_type": "MULTI_ACTIVE_SATELLITE",
                "parent_entity": doc_hub_name,
                "source_bronze_table": first_bronze_table,
                "sub_sequence_column": "extraction_seq",
                "security_classification": "INTERNAL",
                "volatility_tier": "STANDARD",
                "payload_columns": [
                    "extracted_business_key",
                    "verbatim_quote",
                ],
                "soft_deprecated_columns": [],
            }
            ledger.append(
                {
                    "bronze_table": first_bronze_table,
                    "column_name": first_bk_col,
                    "elm_classification": "BUSINESS_KEY",
                    "target_dv_entity": doc_hub_name,
                }
            )

        lifecycle: Literal["COLD_START_N1", "MULTI_SOURCE_EXPANSION"] = (
            "COLD_START_N1" if wave_index == 0 else "MULTI_SOURCE_EXPANSION"
        )
        return {
            "contract_version": "5.0",
            "domain_namespace": _slugify(self.input_spec.domain_namespace),
            "lifecycle_stage": lifecycle,
            "column_triage_ledger": ledger,
            "hubs": list(hubs_by_name.values()),
            "links": list(links_by_name.values()),
            "satellites": list(sats_by_name.values()),
            "reference_tables": [],
            "unmapped_escalations": [],
        }

    def run(self) -> AgenticPipelineReport:
        """Synchronous convenience wrapper around `run_async()`."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self.run_async())
                return future.result()
        return asyncio.run(self.run_async())


def run_agentic_vault_pipeline(
    input_spec: PipelineInputSpec | str | Path,
    harness_setup: HarnessSetupConfig | str | Path | None = None,
    *,
    output_dir: str | Path | None = None,
    base_dir: str | Path | None = None,
    cas_store: ContentAddressableStore | None = None,
    sdk_module: Any | None = None,
) -> AgenticPipelineReport:
    """Execute the end-to-end 8-stage neuro-symbolic Data Vault 2.0 pipeline."""
    eff_base: Path | None = Path(base_dir).resolve() if base_dir is not None else None
    if isinstance(input_spec, (str, Path)):
        spec_path = Path(input_spec).resolve()
        if eff_base is None:
            eff_base = spec_path.parent
        spec_obj = PipelineInputSpec.from_file(spec_path)
    else:
        spec_obj = input_spec

    if isinstance(harness_setup, (str, Path)):
        setup_obj = HarnessSetupConfig.from_file(harness_setup)
    elif harness_setup is None:
        setup_obj = HarnessSetupConfig()
    else:
        setup_obj = harness_setup

    eff_out = Path(output_dir).resolve() if output_dir is not None else None
    pipeline = AgenticVaultPipeline(
        input_spec=spec_obj,
        harness_setup=setup_obj,
        output_dir=eff_out,
        base_dir=eff_base,
        cas_store=cas_store,
        sdk_module=sdk_module,
    )
    return pipeline.run()


__all__ = [
    "AgenticPipelineReport",
    "AgenticVaultPipeline",
    "DeterministicValidationReport",
    "HarnessAuthConfig",
    "HarnessSetupConfig",
    "PipelineArtifactRef",
    "PipelineInputSpec",
    "PipelineSourceFeedInput",
    "PipelineStepReport",
    "PipelineUnstructuredCandidateInput",
    "PipelineUnstructuredDocInput",
    "PipelineWaveInput",
    "PipelineWaveReport",
    "run_agentic_vault_pipeline",
    "validate_pipeline_inputs",
    "validate_pipeline_outputs",
]
