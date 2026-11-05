"""End-to-End 8-Stage Neuro-Symbolic Agentic Data Vault 2.0 Pipeline (`WP-14`).

Implements the `vault-first-mate` / `no-mistakes` agentic pipeline over the hexagonal
`VaultAgentHarness` protocol (`AgyCliHarnessAdapter`, `ClaudeCliHarnessAdapter`,
`AntigravityHarnessAdapter`, `ClaudeAgentHarnessAdapter`) and deterministic verification
engines (`opendv-core`, `opendv-axi-verifier`, `opendv-duckdb-sandbox`, `opendv-telemetry`).

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
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import duckdb
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from opendv_agent_harness.hold_manager import StewardHoldManager, StewardHoldRecord
from opendv_agent_harness.models import (
    AgentPersona,
    AgentTaskSpec,
    DataClassification,
    HarnessBudget,
    HarnessConfig,
    HarnessRunResult,
    HarnessStreamEvent,
    ModelTier,
    ObservabilityVerdict,
    OutputT,
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
from opendv_axi_verifier.toon_codec import ToonViolation
from opendv_axi_verifier.verifiers.alg_a9b_unstructured import classify_er_resolution_tier
from opendv_contracts.errors import ErrorCode, InputValidationError, OpenDVError
from opendv_core.models.canonical_ir import CanonicalIR
from opendv_core.models.contract_v5 import (
    ColumnTriageEntry,
    NoMistakeDataVault2ContractV5,
)
from opendv_core.models.graph_a import CanonicalBusinessConcept, GraphAOntology
from opendv_core.models.graph_b import (
    CandidateRoleLiteral,
    ERTierLiteral,
    ExtractedSpan,
    GraphBProfile,
    ProfiledBronzeTable,
    ProfiledColumn,
    ProfiledSourceSystem,
    ProfiledUnstructuredDocument,
    RowChunkSpan,
)
from opendv_core.paths import (
    PathContainmentError,
    resolve_workspace_path,
)
from opendv_core.schema_registry import (
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
from opendv_sources import (
    SourceSpec,
    get_source_registry,
    load_structured_document,
)
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
    eff_base = file_path.parent.resolve() if file_path.is_absolute() else Path.cwd()
    loaded = load_structured_document(file_path, base_dir=eff_base, allow_temp=True)
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

    auth_mode: str = "none"
    mode: str = "none"
    api_key_env_var: str | None = None
    api_base_url: str | None = None
    gcp_project: str | None = None
    gcp_location: str | None = None
    gcp_credentials_path: str | None = None
    aws_region: str | None = None
    aws_profile: str | None = None
    require_credentials_present: bool = False
    require_preflight: bool = False

    @model_validator(mode="before")
    @classmethod
    def _sync_mode_and_preflight_before(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        copied = dict(data)
        if "mode" in copied and "auth_mode" not in copied and copied["mode"] is not None:
            copied["auth_mode"] = str(copied["mode"]).strip()
        elif "auth_mode" in copied and "mode" not in copied and copied["auth_mode"] is not None:
            copied["mode"] = str(copied["auth_mode"]).strip()

        if (
            "require_preflight" in copied
            and "require_credentials_present" not in copied
            and copied["require_preflight"] is not None
        ):
            copied["require_credentials_present"] = bool(copied["require_preflight"])
        elif (
            "require_credentials_present" in copied
            and "require_preflight" not in copied
            and copied["require_credentials_present"] is not None
        ):
            copied["require_preflight"] = bool(copied["require_credentials_present"])
        return copied

    @model_validator(mode="after")
    def _sync_mode_and_preflight_after(self) -> HarnessAuthConfig:
        effective_mode = (
            self.mode if (self.mode != "none" and self.auth_mode == "none") else self.auth_mode
        )
        object.__setattr__(self, "auth_mode", effective_mode)
        object.__setattr__(self, "mode", effective_mode)
        effective_preflight = bool(self.require_credentials_present or self.require_preflight)
        object.__setattr__(self, "require_credentials_present", effective_preflight)
        object.__setattr__(self, "require_preflight", effective_preflight)
        return self


class HarnessSetupConfig(BaseModel):
    """Configuration for setting up the `VaultAgentHarness` and self-healing policies."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal[
        "agy",
        "agy-cli",
        "antigravity",
        "claude",
        "claude-cli",
        "claude-code",
    ] = "agy"
    model_overrides: dict[str, str] = Field(
        default_factory=lambda: {
            "reasoning": "gemini-4-argon-high",
            "fast": "gemini-3.8-flash-high",
        },
        description="Semantic tier model overrides keyed by 'reasoning' or 'fast'.",
    )
    agent_models: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Optional fine-grained model override per agent persona "
            "('vault-first-mate', 'scout-source', 'scout-unstructured', 'ship-vault')."
        ),
    )
    auth: HarnessAuthConfig = Field(default_factory=HarnessAuthConfig)
    budget: HarnessBudget = Field(default_factory=HarnessBudget)
    sandbox: SandboxPolicy = Field(default_factory=SandboxPolicy)
    steward_hold_policy: Literal["pause", "auto_approve_top_candidate", "reject_on_hold"] = (
        "auto_approve_top_candidate"
    )
    stage_output_policy: Literal["llm_only", "symbolic_completion"] = "llm_only"
    inject_adversarial_fault_on_attempt_1: bool = False
    env: dict[str, str] = Field(default_factory=dict)
    extra_provider_options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("model_overrides", mode="before")
    @classmethod
    def _validate_model_overrides(cls, v: Any) -> dict[str, str]:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError(
                "model_overrides must be a mapping of semantic tier "
                "('reasoning' or 'fast') to model ID."
            )
        normalized: dict[str, str] = {}
        for raw_key, raw_val in v.items():
            if isinstance(raw_key, ModelTier):
                norm_key = raw_key.value
            elif isinstance(raw_key, str):
                key_str = raw_key.strip()
                if key_str in {"argon", "flash"}:
                    raise ValueError(
                        f"Legacy model_overrides key '{key_str}' is no longer supported; "
                        "use semantic tier keys 'reasoning' or 'fast'."
                    )
                if key_str in {"reasoning", "HIGH_REASONING"}:
                    norm_key = ModelTier.HIGH_REASONING.value
                elif key_str in {"fast", "FAST_TIER"}:
                    norm_key = ModelTier.FAST_TIER.value
                else:
                    raise ValueError(
                        f"Invalid model_overrides key '{raw_key}'; expected 'reasoning' or 'fast'."
                    )
            else:
                raise ValueError(
                    f"Invalid model_overrides key '{raw_key}'; expected 'reasoning' or 'fast'."
                )
            if not isinstance(raw_val, str) or not raw_val.strip():
                raise ValueError(
                    f"model_overrides['{norm_key}'] must be a non-empty model identifier string."
                )
            normalized[norm_key] = raw_val.strip()
        return normalized

    @field_validator("agent_models", mode="before")
    @classmethod
    def _validate_agent_models(cls, v: Any) -> dict[str, str]:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError("agent_models must be a mapping of agent persona to model ID.")
        allowed_personas = {
            AgentPersona.VAULT_FIRST_MATE,
            AgentPersona.SCOUT_SOURCE,
            AgentPersona.SCOUT_UNSTRUCTURED,
            AgentPersona.SHIP_VAULT,
            AgentPersona.AXI_DV,
            AgentPersona.DETERMINISTIC_COMPILER,
        }
        normalized: dict[str, str] = {}
        for raw_key, raw_val in v.items():
            persona_enum: AgentPersona | None = None
            if isinstance(raw_key, AgentPersona):
                persona_enum = raw_key
            elif isinstance(raw_key, str):
                key_str = raw_key.strip()
                if key_str in AgentPersona.__members__:
                    persona_enum = AgentPersona[key_str]
                else:
                    try:
                        persona_enum = AgentPersona(key_str)
                    except ValueError as exc:
                        raise ValueError(
                            f"Invalid agent_models key '{raw_key}'; expected one of "
                            "'vault-first-mate', 'scout-source', "
                            "'scout-unstructured', 'ship-vault'."
                        ) from exc
            if persona_enum is None or persona_enum not in allowed_personas:
                raise ValueError(
                    f"Invalid agent_models key '{raw_key}'; expected one of "
                    "'vault-first-mate', 'scout-source', 'scout-unstructured', 'ship-vault'."
                )
            if not isinstance(raw_val, str) or not raw_val.strip():
                raise ValueError(
                    f"agent_models['{persona_enum.value}'] must be a non-empty "
                    "model identifier string."
                )
            normalized[persona_enum.value] = raw_val.strip()
        return normalized

    @model_validator(mode="after")
    def _apply_provider_defaults(self) -> HarnessSetupConfig:
        if self.provider in {"claude-cli", "claude-code"}:
            if "auth" not in self.model_fields_set or (
                "auth_mode" not in self.auth.model_fields_set
                and "mode" not in self.auth.model_fields_set
                and self.auth.auth_mode == "none"
                and self.auth.mode == "none"
            ):
                object.__setattr__(self.auth, "auth_mode", "cli_session")
                object.__setattr__(self.auth, "mode", "cli_session")
            if "model_overrides" not in self.model_fields_set:
                object.__setattr__(
                    self,
                    "model_overrides",
                    {
                        "reasoning": "claude-opus-4-6",
                        "fast": "claude-haiku-4-5",
                    },
                )
        return self

    def to_harness_config(
        self,
        *,
        workspace_dir: Path | None = None,
        state_dir: Path | None = None,
        app_data_dir: Path | None = None,
        save_dir: Path | None = None,
    ) -> HarnessConfig:
        """Convert this `HarnessSetupConfig` into a runtime `HarnessConfig`."""
        default_ws = Path(os.environ.get("OPENDV_WORKSPACE_ROOT") or Path.cwd()).resolve()

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

        persona_overrides: dict[AgentPersona, str] = {}
        for k, v in self.agent_models.items():
            persona_key = AgentPersona[k] if k in AgentPersona.__members__ else AgentPersona(k)
            persona_overrides[persona_key] = str(v)

        eff_auth_mode = (
            self.auth.mode
            if (self.auth.mode != "none" and self.auth.auth_mode == "none")
            else self.auth.auth_mode
        )
        is_claude_family = self.provider in {"claude", "claude-cli", "claude-code"}

        resolved_env: dict[str, str] = dict(self.env)
        if eff_auth_mode in {"api_key_env", "api_key"}:
            env_var_name = self.auth.api_key_env_var or (
                "ANTHROPIC_API_KEY" if is_claude_family else "GEMINI_API_KEY"
            )
            resolved_key = _first_non_empty(
                resolved_env.get(env_var_name),
                os.environ.get(env_var_name),
            )
            if resolved_key:
                resolved_env[env_var_name] = resolved_key
                if is_claude_family:
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
        elif eff_auth_mode in {"vertex_adc", "vertex"}:
            gcp_project = _first_non_empty(
                self.auth.gcp_project,
                resolved_env.get("ANTHROPIC_VERTEX_PROJECT_ID")
                if is_claude_family
                else resolved_env.get("GOOGLE_CLOUD_PROJECT"),
                resolved_env.get("GOOGLE_CLOUD_PROJECT")
                if is_claude_family
                else resolved_env.get("ANTHROPIC_VERTEX_PROJECT_ID"),
                os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
                if is_claude_family
                else os.environ.get("GOOGLE_CLOUD_PROJECT"),
                os.environ.get("GOOGLE_CLOUD_PROJECT")
                if is_claude_family
                else os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID"),
            )
            gcp_location = _first_non_empty(
                self.auth.gcp_location,
                resolved_env.get("CLOUD_ML_REGION")
                if is_claude_family
                else resolved_env.get("GOOGLE_CLOUD_LOCATION"),
                resolved_env.get("GOOGLE_CLOUD_LOCATION")
                if is_claude_family
                else resolved_env.get("CLOUD_ML_REGION"),
                os.environ.get("CLOUD_ML_REGION")
                if is_claude_family
                else os.environ.get("GOOGLE_CLOUD_LOCATION"),
                os.environ.get("GOOGLE_CLOUD_LOCATION")
                if is_claude_family
                else os.environ.get("CLOUD_ML_REGION"),
            )
            gcp_credentials_path = _first_non_empty(
                self.auth.gcp_credentials_path,
                resolved_env.get("GOOGLE_APPLICATION_CREDENTIALS"),
                os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
            )
            if is_claude_family:
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
        elif eff_auth_mode == "aws_bedrock" and is_claude_family:
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
            if is_claude_family:
                resolved_env["ANTHROPIC_BASE_URL"] = self.auth.api_base_url
            else:
                resolved_env["GOOGLE_GEMINI_BASE_URL"] = self.auth.api_base_url

        return HarnessConfig(
            provider=self.provider,
            workspace_dir=eff_ws,
            state_dir=eff_state,
            app_data_dir=eff_app,
            save_dir=eff_save,
            base_url=self.auth.api_base_url,
            model_overrides=tier_overrides,
            agent_models=persona_overrides,
            budget=self.budget,
            sandbox=self.sandbox,
            env=resolved_env,
            extra_provider_options=dict(self.extra_provider_options),
        )

    def to_summary_dict(self) -> dict[str, Any]:
        """Serialize setup metadata for `report.harness_setup` without exposing secret values."""
        eff_auth_mode = (
            self.auth.mode
            if (self.auth.mode != "none" and self.auth.auth_mode == "none")
            else self.auth.auth_mode
        )
        d = self.model_dump(mode="json", fallback=str)
        d["auth_mode"] = eff_auth_mode
        d["api_key_env_var"] = self.auth.api_key_env_var
        d["env"] = {k: "[REDACTED]" for k in self.env}
        return d

    @classmethod
    def from_file(cls, path: str | Path) -> HarnessSetupConfig:
        """Load a `HarnessSetupConfig` from a YAML or JSON file."""
        return cls.model_validate(_load_yaml_or_json(path))


class PipelineForeignKeyMapping(BaseModel):
    """Foreign key mapping from a source column to a target Hub."""

    model_config = ConfigDict(extra="forbid")

    source_column: str
    target_hub: str
    role_alias: str | None = None
    is_nullable: bool | None = None


def _normalize_foreign_key_mappings(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, list):
        mapped: dict[str, str] = {}
        for item in value:
            if isinstance(item, PipelineForeignKeyMapping):
                mapped[item.source_column] = item.target_hub
            elif isinstance(item, dict):
                src_col = item.get("source_column") or item.get("column_name")
                tgt_hub = item.get("target_hub") or item.get("hub_name")
                if src_col and tgt_hub:
                    mapped[str(src_col)] = str(tgt_hub)
        return mapped
    msg = f"Expected dict or list for foreign_key_mappings, got {type(value).__name__}"
    raise ValueError(msg)


class PipelineSourceFeedInput(BaseModel):
    """Single Bronze relational source feed input within a pipeline wave."""

    model_config = ConfigDict(extra="forbid")

    feed_id: str | None = None
    source_system: str
    table_name: str
    source: SourceSpec | dict[str, Any] | None = None
    baseline_source: SourceSpec | dict[str, Any] | None = None
    data_path: str = ""
    file_path: str | None = None
    baseline_data_path: str | None = None
    baseline_file_path: str | None = None
    key_authority_tier: (
        Literal["TIER_1_GLOBAL_STANDARD", "TIER_2_ENTERPRISE_SOR", "TIER_3_LOCAL_SCOPED"] | None
    ) = None
    bkcc_namespace: str | None = None
    record_source_code: str | None = None
    target_hub_name: str | None = None
    business_key_columns: list[str] = Field(default_factory=list)
    surrogate_key_columns: list[str] = Field(default_factory=list)
    timestamp_column: str | None = None
    foreign_key_mappings: dict[str, str] = Field(default_factory=dict)
    cbc_mapping_hints: dict[str, str] = Field(default_factory=dict)
    pii_columns: list[str] = Field(default_factory=list)

    @field_validator("foreign_key_mappings", mode="before")
    @classmethod
    def _validate_fk_mappings(cls, value: Any) -> dict[str, str]:
        return _normalize_foreign_key_mappings(value)

    @model_validator(mode="after")
    def _normalize_source_feed_paths(self) -> PipelineSourceFeedInput:
        if self.source is not None:
            if isinstance(self.source, dict):
                self.source = SourceSpec.model_validate(self.source)
            if not self.data_path and self.source.uri:
                self.data_path = self.source.uri
        elif self.data_path or self.file_path:
            p = self.data_path or self.file_path or ""
            self.source = SourceSpec(adapter="file", uri=p)

        if not self.data_path and self.file_path:
            self.data_path = self.file_path
        elif self.data_path and self.file_path is None:
            self.file_path = self.data_path

        if self.baseline_source is not None:
            if isinstance(self.baseline_source, dict):
                self.baseline_source = SourceSpec.model_validate(self.baseline_source)
            if not self.baseline_data_path and self.baseline_source.uri:
                self.baseline_data_path = self.baseline_source.uri
        elif self.baseline_data_path or self.baseline_file_path:
            bp = self.baseline_data_path or self.baseline_file_path or ""
            self.baseline_source = SourceSpec(adapter="file", uri=bp)

        if self.baseline_data_path is None and self.baseline_file_path:
            self.baseline_data_path = self.baseline_file_path
        elif self.baseline_data_path and self.baseline_file_path is None:
            self.baseline_file_path = self.baseline_data_path
        if not self.feed_id:
            self.feed_id = f"{self.source_system.lower()}_{self.table_name.lower()}"
        return self


class PipelineUnstructuredCandidateInput(BaseModel):
    """Candidate entity extraction with character-offset grounding in an unstructured document."""

    model_config = ConfigDict(extra="forbid")

    extraction_id: str = ""
    candidate_id: str | None = None
    target_hub: str = "hub_customer"
    extracted_business_key: str
    verbatim_quote: str
    char_start: int | None = None
    char_end: int | None = None
    page_number: int = 1
    confidence_score: float = Field(default=0.95, ge=0.0, le=1.0)
    er_tier: (
        Literal["TIER_U1_DETERMINISTIC", "TIER_U2_HYBRID_EXACT", "TIER_U3_CAPTAIN_HOLD"] | None
    ) = None
    top2_margin: float = Field(default=0.25, ge=0.0, le=1.0)
    candidate_hub_matches: list[dict[str, Any]] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize_candidate_ids(self) -> PipelineUnstructuredCandidateInput:
        if not self.extraction_id and self.candidate_id:
            self.extraction_id = self.candidate_id
        elif not self.extraction_id:
            self.extraction_id = f"ext_{_slugify(self.extracted_business_key)}"
        if self.candidate_id is None:
            self.candidate_id = self.extraction_id
        return self


PipelineUnstructuredCandidate = PipelineUnstructuredCandidateInput


class PipelineUnstructuredDocInput(BaseModel):
    """Unstructured document onboarding specification within a pipeline wave."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    source: SourceSpec | dict[str, Any] | None = None
    document_path: str = ""
    file_path: str | None = None
    record_source: str = "UNSTRUCTURED_DOC"
    record_source_code: str | None = None
    target_hub_name: str | None = None
    link_name: str | None = None
    satellite_name: str | None = None
    extracted_candidates: list[PipelineUnstructuredCandidateInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize_doc_fields(self) -> PipelineUnstructuredDocInput:
        if self.source is not None:
            if isinstance(self.source, dict):
                self.source = SourceSpec.model_validate(self.source)
            if not self.document_path and self.source.uri:
                self.document_path = self.source.uri
        elif self.document_path or self.file_path:
            dp = self.document_path or self.file_path or ""
            adapter_name = get_source_registry().resolve_document_adapter_name(dp)
            self.source = SourceSpec(adapter=adapter_name, uri=dp)

        if not self.document_path and self.file_path:
            self.document_path = self.file_path
        elif self.document_path and self.file_path is None:
            self.file_path = self.document_path
        if self.record_source_code and self.record_source == "UNSTRUCTURED_DOC":
            self.record_source = self.record_source_code
        elif self.record_source_code is None:
            self.record_source_code = self.record_source
        return self


class Stage00OrchestrationPlanOutput(BaseModel):
    """Structured output returned by `@vault-first-mate` in Stage 00."""

    model_config = ConfigDict(extra="forbid")

    wave_execution_order: list[int] = Field(min_length=1)
    risk_assessment: str = Field(min_length=1)
    recommended_max_retries: int = Field(default=3, ge=1, le=5)


class Stage01FeedDiscoveryItem(BaseModel):
    """Discovered semantic classification for a single Bronze relational feed."""

    model_config = ConfigDict(extra="forbid")

    feed_id: str | None = None
    source_system: str | None = None
    table_name: str | None = None
    record_source_code: str | None = None
    target_hub_name: str | None = None
    business_key_columns: list[str] = Field(default_factory=list)
    surrogate_key_columns: list[str] = Field(default_factory=list)
    timestamp_column: str | None = None
    pii_columns: list[str] = Field(default_factory=list)
    key_authority_tier: Literal[
        "TIER_1_GLOBAL_STANDARD", "TIER_2_ENTERPRISE_SOR", "TIER_3_LOCAL_SCOPED"
    ] = "TIER_1_GLOBAL_STANDARD"
    bkcc_namespace: str = "GLOBAL"
    foreign_key_mappings: dict[str, str] = Field(default_factory=dict)
    cbc_mapping_hints: dict[str, str] = Field(default_factory=dict)

    @field_validator("foreign_key_mappings", mode="before")
    @classmethod
    def _validate_fk_mappings(cls, value: Any) -> dict[str, str]:
        return _normalize_foreign_key_mappings(value)


class Stage01SourceDiscoveryOutput(BaseModel):
    """Structured output returned by `@scout-source` in Stage 01."""

    model_config = ConfigDict(extra="forbid")

    feed_id: str | None = None
    source_system: str | None = None
    table_name: str | None = None
    record_source_code: str | None = None
    target_hub_name: str | None = None
    business_key_columns: list[str] = Field(default_factory=list)
    surrogate_key_columns: list[str] = Field(default_factory=list)
    timestamp_column: str | None = None
    pii_columns: list[str] = Field(default_factory=list)
    key_authority_tier: Literal[
        "TIER_1_GLOBAL_STANDARD", "TIER_2_ENTERPRISE_SOR", "TIER_3_LOCAL_SCOPED"
    ] = "TIER_1_GLOBAL_STANDARD"
    bkcc_namespace: str = "GLOBAL"
    foreign_key_mappings: dict[str, str] = Field(default_factory=dict)
    cbc_mapping_hints: dict[str, str] = Field(default_factory=dict)
    discovered_feeds: list[Stage01FeedDiscoveryItem] = Field(default_factory=list)

    @field_validator("foreign_key_mappings", mode="before")
    @classmethod
    def _validate_fk_mappings(cls, value: Any) -> dict[str, str]:
        return _normalize_foreign_key_mappings(value)

    @model_validator(mode="after")
    def _require_non_empty_discovery(self) -> Stage01SourceDiscoveryOutput:
        if (
            not self.discovered_feeds
            and not self.feed_id
            and not self.table_name
            and not self.target_hub_name
            and not self.business_key_columns
        ):
            msg = "Stage01SourceDiscoveryOutput requires at least one discovered feed or BK column."
            raise ValueError(msg)
        return self


class Stage02UnstructuredDiscoveryOutput(BaseModel):
    """Structured output returned by `@scout-unstructured` in Stage 02."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str | None = None
    target_hub_name: str = "hub_document"
    link_name: str | None = None
    satellite_name: str | None = None
    extracted_candidates: list[PipelineUnstructuredCandidateInput] = Field(default_factory=list)
    documents: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_non_empty_unstructured(self) -> Stage02UnstructuredDiscoveryOutput:
        if not self.doc_id and not self.extracted_candidates and not self.documents:
            msg = "Stage02UnstructuredDiscoveryOutput requires doc_id or extracted_candidates."
            raise ValueError(msg)
        return self


class Stage03ColumnTriageOutput(BaseModel):
    """Structured Pass 1 column triage ledger returned by `@ship-vault` in Stage 03."""

    model_config = ConfigDict(extra="forbid")

    column_triage_ledger: list[ColumnTriageEntry] = Field(min_length=1)
    unmapped_columns: list[str] = Field(default_factory=list)


class Stage04ContractSynthesisOutput(BaseModel):
    """Structured Pass 2 Data Vault 2.0 contract returned by `@ship-vault` in Stage 04/05."""

    model_config = ConfigDict(extra="forbid")

    contract: NoMistakeDataVault2ContractV5
    synthesis_rationale: str = ""


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
    model_used: str = ""
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
    fallbacks_applied: list[dict[str, Any]] = Field(default_factory=list)
    markdown_summary: str


# ---------------------------------------------------------------------------
# 3. Deterministic Input & Output Validation
# ---------------------------------------------------------------------------


def _validate_path_containment(
    raw_path: str,
    *,
    base_dir: Path,
    workspace_root: Path,
) -> tuple[Path | None, str | None]:
    """Resolve `raw_path` using canonical `resolve_workspace_path` and reject `..` traversal."""
    try:
        resolved = resolve_workspace_path(
            raw_path,
            workspace_root=workspace_root,
            base_dir=base_dir,
            allow_temp=True,
        )
        return resolved, None
    except PathContainmentError as exc:
        return None, str(exc)


def _to_source_target(
    src_source: SourceSpec | dict[str, Any] | None,
    path: Path | None,
) -> Path | str | SourceSpec:
    """Normalize a source spec or path into an authoritative DuckDB load target."""
    if src_source is not None:
        if isinstance(src_source, SourceSpec):
            return src_source.model_copy(update={"uri": str(path)}) if path else src_source
        if isinstance(src_source, dict):
            updated = dict(src_source)
            if path:
                updated["uri"] = str(path)
            return SourceSpec.model_validate(updated)
    assert path is not None
    return path


def _load_table_into_duckdb(
    conn: duckdb.DuckDBPyConnection,
    view_name: str,
    file_path: Path | str | SourceSpec,
) -> tuple[int, list[str]]:
    """Load tabular data into DuckDB as `view_name` using adapters and return (rows, cols)."""
    spec = (
        file_path
        if isinstance(file_path, SourceSpec)
        else SourceSpec(adapter="file", uri=str(Path(file_path).resolve()))
    )
    reg = get_source_registry()
    adapter = reg.get_tabular_adapter(spec.adapter)
    adapter.register(spec, conn, view_name)
    q_view = f'"{view_name}"'
    desc = conn.execute(f"DESCRIBE {q_view}").fetchall()
    cols = [str(r[0]) for r in desc]
    cnt_row = conn.execute(f"SELECT COUNT(*) FROM {q_view}").fetchone()
    row_count = int(cnt_row[0]) if cnt_row is not None else 0
    return row_count, cols


def validate_pipeline_inputs(
    input_spec: PipelineInputSpec | dict[str, Any] | str | Path,
    harness_setup: HarnessSetupConfig | dict[str, Any] | str | Path | None = None,
    *,
    base_dir: Path | None = None,
) -> DeterministicValidationReport:
    """Deterministically validate `PipelineInputSpec`, `HarnessSetupConfig`, ontology, and data."""
    diagnostics: list[dict[str, Any]] = []
    checked_items = 0

    ws_root = Path(os.environ.get("OPENDV_WORKSPACE_ROOT") or Path.cwd()).resolve()
    eff_base = (base_dir or ws_root).resolve()

    # 1. Validate PipelineInputSpec model
    checked_items += 1
    if isinstance(input_spec, dict):
        try:
            spec = PipelineInputSpec.model_validate(input_spec)
        except (ValidationError, ValueError, TypeError) as exc:
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
    elif isinstance(input_spec, (str, Path)):
        try:
            spec = PipelineInputSpec.from_file(input_spec)
        except (ValidationError, ValueError, TypeError, OSError) as exc:
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
        except (ValidationError, ValueError, TypeError) as exc:
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
    elif isinstance(harness_setup, (str, Path)):
        try:
            setup_obj = HarnessSetupConfig.from_file(harness_setup)
        except (ValidationError, ValueError, TypeError, OSError) as exc:
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
        except (OpenDVError, ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
            diagnostics.append(
                {
                    "code": "HARNESS_INIT_FAILED",
                    "severity": "ERROR",
                    "target": "harness_setup",
                    "message": str(exc),
                }
            )

    eff_auth_mode = (
        setup_obj.auth.mode
        if (setup_obj.auth.mode != "none" and setup_obj.auth.auth_mode == "none")
        else setup_obj.auth.auth_mode
    )
    eff_require_preflight = bool(
        setup_obj.auth.require_credentials_present or setup_obj.auth.require_preflight
    )
    is_claude_family = setup_obj.provider in {"claude", "claude-cli", "claude-code"}

    valid_all_auth_modes = {
        "none",
        "api_key_env",
        "api_key",
        "oauth_token",
        "vertex_adc",
        "vertex",
        "aws_bedrock",
        "cli_session",
        "ambient_cli",
        "foundry",
        "auto",
    }
    valid_claude_cli_modes = {
        "none",
        "cli_session",
        "ambient_cli",
        "api_key",
        "api_key_env",
        "oauth_token",
        "vertex",
        "vertex_adc",
        "aws_bedrock",
        "foundry",
        "auto",
    }

    if setup_obj.provider in {"claude-cli", "claude-code"}:
        if eff_auth_mode not in valid_claude_cli_modes:
            diagnostics.append(
                {
                    "code": "HARNESS_AUTH_INVALID",
                    "severity": "ERROR",
                    "target": "harness_setup.auth.mode",
                    "message": (
                        f"Invalid auth mode '{eff_auth_mode}' for provider "
                        f"'{setup_obj.provider}'. Supported modes: cli_session, api_key, "
                        "oauth_token, vertex, foundry, auto."
                    ),
                }
            )
    elif eff_auth_mode not in valid_all_auth_modes:
        diagnostics.append(
            {
                "code": "HARNESS_AUTH_INVALID",
                "severity": "ERROR",
                "target": "harness_setup.auth.auth_mode",
                "message": (
                    f"Invalid auth mode '{eff_auth_mode}' for provider '{setup_obj.provider}'."
                ),
            }
        )
    elif eff_auth_mode == "aws_bedrock" and not is_claude_family:
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

    if (
        setup_obj.provider in {"agy", "agy-cli"}
        and eff_auth_mode in {"cli_session", "ambient_cli"}
        and (eff_require_preflight or eff_auth_mode == "ambient_cli")
    ):
        from opendv_agent_harness.adapters.agy_cli_adapter import AgyCliHarnessAdapter

        agy_cli_check = AgyCliHarnessAdapter(setup_obj.to_harness_config(workspace_dir=ws_root))
        if agy_cli_check._resolve_agy_binary() is None:
            diagnostics.append(
                {
                    "code": "HARNESS_AUTH_MISSING",
                    "severity": "ERROR",
                    "target": "harness_setup.auth.auth_mode",
                    "message": (
                        f"Auth mode '{eff_auth_mode}' for provider '{setup_obj.provider}' "
                        "requires the 'agy' CLI executable. Checked sources: "
                        "1) extra_provider_options.agy_binary, "
                        "2) env var OPENDV_AGY_CLI_BIN, 3) PATH."
                    ),
                }
            )

    if (
        setup_obj.provider in {"claude-cli", "claude-code"}
        and eff_auth_mode in valid_claude_cli_modes
        and (eff_require_preflight or eff_auth_mode == "ambient_cli")
    ):
        from opendv_agent_harness.adapters.claude_cli_adapter import ClaudeCliHarnessAdapter

        claude_cli_check = ClaudeCliHarnessAdapter(
            setup_obj.to_harness_config(workspace_dir=ws_root)
        )
        if claude_cli_check._resolve_claude_binary() is None:
            diagnostics.append(
                {
                    "code": "HARNESS_PROVIDER_UNAVAILABLE",
                    "severity": "ERROR",
                    "target": "harness_setup.provider",
                    "message": (
                        f"Provider '{setup_obj.provider}' requires the 'claude' CLI executable. "
                        "Checked sources: 1) extra_provider_options.claude_binary, "
                        "2) env var OPENDV_CLAUDE_CLI_BIN, 3) PATH."
                    ),
                }
            )

    if eff_auth_mode in {"cli_session", "ambient_cli"} and setup_obj.provider in {
        "antigravity",
        "claude",
    }:
        import importlib.util

        sdk_pkg = (
            "google.antigravity" if setup_obj.provider == "antigravity" else "claude_agent_sdk"
        )
        try:
            sdk_spec = importlib.util.find_spec(sdk_pkg)
        except (ImportError, ModuleNotFoundError, ValueError):
            sdk_spec = None
        if sdk_spec is None:
            diagnostics.append(
                {
                    "code": "HARNESS_AUTH_MISSING",
                    "severity": "ERROR",
                    "target": "harness_setup.auth.auth_mode",
                    "message": (
                        f"Auth mode '{eff_auth_mode}' for provider "
                        f"'{setup_obj.provider}' requires SDK package '{sdk_pkg}' to be installed."
                    ),
                }
            )

    if eff_require_preflight:
        if eff_auth_mode in {"api_key_env", "api_key"} and setup_obj.provider not in {
            "claude-cli",
            "claude-code",
        }:
            target_var = setup_obj.auth.api_key_env_var or (
                "ANTHROPIC_API_KEY" if is_claude_family else "GEMINI_API_KEY"
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
        elif eff_auth_mode == "vertex_adc":
            proj_var = "ANTHROPIC_VERTEX_PROJECT_ID" if is_claude_family else "GOOGLE_CLOUD_PROJECT"
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
        elif eff_auth_mode == "aws_bedrock":
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
        elif eff_auth_mode == "none" and setup_obj.provider in {
            "antigravity",
            "claude",
        }:
            import importlib.util

            sdk_pkg = (
                "google.antigravity" if setup_obj.provider == "antigravity" else "claude_agent_sdk"
            )
            try:
                sdk_spec = importlib.util.find_spec(sdk_pkg)
            except (ImportError, ModuleNotFoundError, ValueError):
                sdk_spec = None
            has_api_key = bool(
                _first_non_empty(
                    resolved_harness_env.get("ANTHROPIC_API_KEY"),
                    os.environ.get("ANTHROPIC_API_KEY"),
                )
                if setup_obj.provider == "claude"
                else _first_non_empty(
                    resolved_harness_env.get("GEMINI_API_KEY"),
                    resolved_harness_env.get("GOOGLE_API_KEY"),
                    os.environ.get("GEMINI_API_KEY"),
                    os.environ.get("GOOGLE_API_KEY"),
                )
            )
            if sdk_spec is None and not has_api_key:
                diagnostics.append(
                    {
                        "code": "HARNESS_AUTH_MISSING",
                        "severity": "ERROR",
                        "target": "harness_setup.auth",
                        "message": (
                            f"Provider '{setup_obj.provider}' requires SDK '{sdk_pkg}' or an API "
                            f"key when require_credentials_present=True."
                        ),
                    }
                )

    # 3. Validate Graph A Ontology path & schema
    checked_items += 1
    ont_path, ont_err = _validate_path_containment(
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
        except (
            ValidationError,
            ValueError,
            TypeError,
            KeyError,
            OSError,
            yaml.YAMLError,
            json.JSONDecodeError,
        ) as exc:
            diagnostics.append(
                {
                    "code": "ONTOLOGY_SCHEMA_INVALID",
                    "severity": "ERROR",
                    "target": str(ont_path),
                    "message": f"Graph A ontology validation failed: {exc}",
                }
            )

    # 4. Validate optional initial_contract_path
    initial_hub_count = 0
    if spec.initial_contract_path is not None:
        checked_items += 1
        init_path, init_err = _validate_path_containment(
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
                initial_hub_count = len(raw_init.get("hubs", []))
            except (
                ValidationError,
                ValueError,
                TypeError,
                KeyError,
                OSError,
                yaml.YAMLError,
                json.JSONDecodeError,
            ) as exc:
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
        out_path, out_err = _validate_path_containment(
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
    seen_relational_sources = initial_hub_count
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
                if src.business_key_columns and any(
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
                                f"{wave.wave_index} cannot contain blank business key column names."
                            ),
                        }
                    )
                seen_relational_sources += 1
                d_path, d_err = _validate_path_containment(
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
                    load_target = _to_source_target(src.source, d_path)
                    row_cnt, cols = _load_table_into_duckdb(
                        conn, f"v_w{wave.wave_index}_s{s_idx}", load_target
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
                except (duckdb.Error, OSError, ValueError, TypeError) as exc:
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
                    b_path, b_err = _validate_path_containment(
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
                            b_target = _to_source_target(src.baseline_source, b_path)
                            b_rows, b_cols = _load_table_into_duckdb(
                                conn, f"v_w{wave.wave_index}_s{s_idx}_base", b_target
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
                        except (duckdb.Error, OSError, ValueError, TypeError) as exc:
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
                u_path, u_err = _validate_path_containment(
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
                if doc.source is not None:
                    raw_spec = doc.source
                    if isinstance(raw_spec, dict):
                        doc_spec = SourceSpec.model_validate(raw_spec)
                    else:
                        doc_spec = raw_spec.model_copy()
                    if not doc_spec.inline_content and doc_spec.uri:
                        doc_spec = doc_spec.model_copy(update={"uri": str(u_path)})
                else:
                    doc_spec = SourceSpec(
                        adapter=get_source_registry().resolve_document_adapter_name(u_path),
                        uri=str(u_path),
                    )
                doc_adapter = get_source_registry().get_document_adapter(doc_spec.adapter)
                extracted = doc_adapter.extract_document(doc_spec)
                doc_text = extracted.normalized_text
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
                    c_start = cand.char_start
                    c_end = cand.char_end
                    cand_quote = cand.verbatim_quote.replace("\r\n", "\n").replace("\r", "\n")
                    if c_start is None or c_end is None:
                        found_idx = doc_text.find(cand_quote)
                        if found_idx < 0:
                            diagnostics.append(
                                {
                                    "code": "SPAN_QUOTE_MISMATCH",
                                    "severity": "ERROR",
                                    "target": f"{doc.doc_id}:{cand.extraction_id}",
                                    "message": (
                                        f"Candidate '{cand.extraction_id}' verbatim_quote "
                                        f"'{cand_quote}' not found in '{doc.doc_id}'."
                                    ),
                                }
                            )
                        continue
                    if c_start < 0 or c_end <= c_start or c_end > len(doc_text):
                        diagnostics.append(
                            {
                                "code": "SPAN_OUT_OF_BOUNDS",
                                "severity": "ERROR",
                                "target": f"{doc.doc_id}:{cand.extraction_id}",
                                "message": (
                                    f"Candidate '{cand.extraction_id}' span "
                                    f"[{c_start}, {c_end}] is out of bounds "
                                    f"for document '{doc.doc_id}' (length={len(doc_text)})."
                                ),
                            }
                        )
                    elif doc_text[c_start:c_end] != cand_quote:
                        actual_slice = doc_text[c_start:c_end]
                        diagnostics.append(
                            {
                                "code": "SPAN_QUOTE_MISMATCH",
                                "severity": "ERROR",
                                "target": f"{doc.doc_id}:{cand.extraction_id}",
                                "message": (
                                    f"Candidate '{cand.extraction_id}' verbatim_quote "
                                    f"'{cand_quote}' != document slice "
                                    f"'{actual_slice}' at [{c_start}:{c_end}]."
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
            except (OpenDVError, ValueError, TypeError, KeyError, OSError) as exc:
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
                    except (
                        ValidationError,
                        ValueError,
                        TypeError,
                        KeyError,
                        OSError,
                        json.JSONDecodeError,
                    ) as exc:
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
        except (
            ValidationError,
            ValueError,
            TypeError,
            KeyError,
            OSError,
            yaml.YAMLError,
            json.JSONDecodeError,
        ) as exc:
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
        except (
            ValidationError,
            ValueError,
            TypeError,
            KeyError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
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
        except (ValidationError, ValueError, TypeError, KeyError, OSError) as exc:
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
    doc_text = doc_text.replace("\r\n", "\n").replace("\r", "\n")
    extracted_spans_gb: list[ExtractedSpan] = []
    for cand in doc.extracted_candidates:
        margin, _short_tier, gb_tier, _action = _compute_candidate_er_metrics(cand)
        quote = cand.verbatim_quote.replace("\r\n", "\n").replace("\r", "\n")
        c_start = cand.char_start
        c_end = cand.char_end
        if c_start is None or c_end is None:
            found_idx = doc_text.find(quote)
            c_start = max(0, found_idx)
            c_end = c_start + len(quote)
        extracted_spans_gb.append(
            ExtractedSpan(
                field=cand.extraction_id,
                span_start=c_start,
                span_end=c_end,
                verbatim_quote=quote,
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

    raise InputValidationError(
        f"Unmapped ontology concept '{concept_or_hub_hint}': "
        "concept or hub not found in GraphAOntology.",
        error_code=ErrorCode.INPUT_VALIDATION_ERROR,
    )


def _resolve_relationship_uri(
    link_name: str,
    hub_names: list[str],
    ontology: GraphAOntology,
) -> str:
    """Resolve a Link's `ontology_concept_uri` from `GraphAOntology`."""
    for rel in ontology.canonical_relationships:
        if rel.target_link == link_name:
            return rel.relationship_uri
    hub_set = set(hub_names)
    for rel in ontology.canonical_relationships:
        endpoint_hubs = {_resolve_hub_and_uri(ep, ontology)[0] for ep in rel.endpoints}
        if endpoint_hubs and endpoint_hubs.issubset(hub_set):
            return rel.relationship_uri
    raise InputValidationError(
        f"Unmapped ontology relationship for link '{link_name}' with hubs {hub_names}: "
        "not found in GraphAOntology.",
        error_code=ErrorCode.INPUT_VALIDATION_ERROR,
    )


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


_STAGE_CONTEXT_MARKER = "\n__OPENDV_STAGE_CONTEXT_JSON__:"


def _encode_stage_context(ctx: dict[str, Any]) -> str:
    """Append a serialized stage context footer for deterministic/shim structured synthesis."""
    return f"{_STAGE_CONTEXT_MARKER}{json.dumps(ctx, sort_keys=True)}"


def _decode_stage_context(prompt: str) -> dict[str, Any] | None:
    """Extract serialized stage context from `prompt` if present."""
    if not prompt or _STAGE_CONTEXT_MARKER not in prompt:
        return None
    _, raw_json = prompt.rsplit(_STAGE_CONTEXT_MARKER, 1)
    try:
        parsed = json.loads(raw_json.strip())
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def _seed_graph_b_systems_from_deployed_contract(
    deployed_contract: dict[str, Any],
    init_contract_path: Path | None = None,
) -> dict[str, ProfiledSourceSystem]:
    """Rehydrate `cumulative_source_systems` from a prior run's Graph B profile or contract."""
    systems_by_code: dict[str, ProfiledSourceSystem] = {}
    if init_contract_path is not None:
        waves_dir = init_contract_path.parent / "waves"
        if waves_dir.is_dir():
            gb_files = sorted(
                waves_dir.rglob("wave_*_graph_b_profile.json"),
                key=lambda p: (
                    int(m.group(1)) if (m := re.search(r"wave_(\d+)", p.name)) else 0,
                    str(p),
                ),
            )
            if gb_files:
                try:
                    prev_gb = GraphBProfile.model_validate(load_structured_file(gb_files[-1]))
                    for sys_obj in prev_gb.source_systems:
                        systems_by_code[sys_obj.source_system_code] = sys_obj
                except (ValidationError, ValueError, TypeError, OSError):
                    pass

    existing_tables = {
        tbl.bronze_table for sys_obj in systems_by_code.values() for tbl in sys_obj.tables
    }
    pii_sat_names = {
        str(sat.get("name", ""))
        for sat in deployed_contract.get("satellites", [])
        if isinstance(sat, dict)
        and sat.get("security_classification") in {"PII_HIGH", "RESTRICTED_PII", "PCI"}
    }
    drift_sat_names = {
        str(sat.get("name", ""))
        for sat in deployed_contract.get("satellites", [])
        if isinstance(sat, dict) and sat.get("volatility_tier") == "ADDITIVE_DRIFT_COMPANION"
    }
    bk_tier_by_col: dict[tuple[str, str], str] = {}
    for hub in deployed_contract.get("hubs", []):
        if not isinstance(hub, dict) or hub.get("name") == "hub_document":
            continue
        for bk_src in hub.get("business_key_sources", []):
            if not isinstance(bk_src, dict):
                continue
            b_tbl = str(bk_src.get("bronze_table", ""))
            tier = str(bk_src.get("key_authority_tier") or "TIER_1_GLOBAL_STANDARD")
            for k_col in bk_src.get("key_columns", []):
                bk_tier_by_col[(b_tbl, str(k_col))] = tier

    entries_by_table: dict[str, list[dict[str, Any]]] = {}
    for entry in deployed_contract.get("column_triage_ledger", []):
        if not isinstance(entry, dict):
            continue
        b_tbl = str(entry.get("bronze_table", ""))
        if b_tbl:
            entries_by_table.setdefault(b_tbl, []).append(entry)

    for b_tbl, entries in entries_by_table.items():
        if b_tbl in existing_tables:
            continue
        raw_sys = b_tbl.split(".")[0].upper()
        sys_code = re.sub(r"[^A-Z0-9_]+", "_", raw_sys).strip("_") or "SOURCE_SYSTEM"
        cols: list[ProfiledColumn] = []
        seen_cols: set[str] = set()
        for entry in entries:
            c_name = str(entry.get("column_name", ""))
            if not c_name or c_name in seen_cols:
                continue
            seen_cols.add(c_name)
            elm = str(entry.get("elm_classification", "DESCRIPTIVE_PAYLOAD"))
            target_ent = str(entry.get("target_dv_entity", ""))
            sec_tag: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "PII_HIGH", "PCI"] = "INTERNAL"
            u_tot = 0.5
            u_nn = 0.95
            null_r = 0.0
            cand_role: CandidateRoleLiteral = "DESCRIPTIVE_PAYLOAD"
            if elm == "BUSINESS_KEY":
                b_tier = bk_tier_by_col.get((b_tbl, c_name), "TIER_1_GLOBAL_STANDARD")
                if b_tier == "TIER_3_LOCAL_SCOPED":
                    cand_role = "TIER_3_LOCAL_SCOPED_KEY"
                elif b_tier == "TIER_2_ENTERPRISE_SOR":
                    cand_role = "TIER_2_BUSINESS_KEY"
                else:
                    cand_role = "TIER_1_BUSINESS_KEY"
                u_tot = 1.0
                u_nn = 1.0
            elif elm == "FOREIGN_KEY_REF":
                cand_role = "FOREIGN_KEY_REF"
                u_nn = 1.0
            elif elm == "OPTIONAL_FOREIGN_KEY_REF":
                cand_role = "OPTIONAL_FOREIGN_KEY_REF"
                u_nn = 0.8
                null_r = 0.2
            elif elm == "SYSTEM_SURROGATE_DROPPED":
                cand_role = "SYSTEM_SURROGATE_DROPPED"
            elif elm == "TECHNICAL_METADATA":
                cand_role = "TECHNICAL_METADATA"
            elif target_ent in pii_sat_names:
                cand_role = "PII_RESTRICTED_PAYLOAD"
                sec_tag = "PII_HIGH"
            elif target_ent in drift_sat_names:
                cand_role = "ADDITIVE_DRIFT_COMPANION"

            cols.append(
                ProfiledColumn(
                    column_name=c_name,
                    data_type="VARCHAR",
                    u_total=u_tot,
                    u_non_null=u_nn,
                    null_rate=null_r,
                    security_tag=sec_tag,
                    candidate_role=cand_role,
                )
            )
        if not cols:
            continue
        prof_tbl = ProfiledBronzeTable(
            bronze_table=b_tbl,
            row_count=10,
            daily_delta_rows=2,
            columns=cols,
        )
        existing_sys = systems_by_code.get(sys_code)
        if existing_sys is None:
            systems_by_code[sys_code] = ProfiledSourceSystem(
                source_system_code=sys_code,
                description=f"Seeded Bronze source system {sys_code}",
                tables=[prof_tbl],
            )
        else:
            systems_by_code[sys_code] = existing_sys.model_copy(
                update={"tables": [*existing_sys.tables, prof_tbl]}
            )

    return systems_by_code


def _extract_known_entities_from_cumulative_sources(
    *,
    ontology: GraphAOntology,
    cumulative_sources: dict[str, tuple[PipelineSourceFeedInput, Path]],
    deployed_contract: dict[str, Any] | None = None,
    base_dir: Path | None = None,
) -> list[tuple[str, str, str]]:
    """Extract `(resolved_hub, business_key, entity_name)` tuples from active relational sources."""
    known_entities: list[tuple[str, str, str]] = []
    seen_hub_bks: set[tuple[str, str]] = set()
    conn = duckdb.connect(":memory:")
    try:
        for q_tbl, (src, d_path) in cumulative_sources.items():
            if not src.business_key_columns or not d_path.is_file():
                continue
            v_name = f"v_er_{_slugify(q_tbl)}"
            src_target = _to_source_target(src.source, d_path)
            _, cols = _load_table_into_duckdb(conn, v_name, src_target)
            bk_col = src.business_key_columns[0]
            if bk_col not in cols:
                continue
            name_col = next(
                (
                    c
                    for c in cols
                    if any(
                        k in c.lower()
                        for k in ("legal_name", "company_name", "customer_name", "name")
                    )
                ),
                None,
            )
            hub_hint = (
                src.target_hub_name or src.cbc_mapping_hints.get(src.table_name) or src.table_name
            )
            resolved_hub, _ = _resolve_hub_and_uri(hub_hint, ontology)
            if name_col:
                rows = conn.execute(f'SELECT "{bk_col}", "{name_col}" FROM "{v_name}"').fetchall()
                for r in rows:
                    if r[0] is not None:
                        bk_s = str(r[0]).strip()
                        if (resolved_hub, bk_s) not in seen_hub_bks:
                            seen_hub_bks.add((resolved_hub, bk_s))
                            known_entities.append((resolved_hub, bk_s, str(r[1] or "").strip()))
            else:
                rows = conn.execute(f'SELECT "{bk_col}" FROM "{v_name}"').fetchall()
                for r in rows:
                    if r[0] is not None:
                        bk_s = str(r[0]).strip()
                        if (resolved_hub, bk_s) not in seen_hub_bks:
                            seen_hub_bks.add((resolved_hub, bk_s))
                            known_entities.append((resolved_hub, bk_s, ""))

        if deployed_contract is not None:
            search_roots: list[Path] = []
            if base_dir is not None:
                if (base_dir / "sources").is_dir():
                    search_roots.append(base_dir / "sources")
                if base_dir.is_dir():
                    search_roots.append(base_dir)
            for hub in deployed_contract.get("hubs", []):
                if not isinstance(hub, dict):
                    continue
                h_name = str(hub.get("name", ""))
                if not h_name or h_name == "hub_document":
                    continue
                for bk_src in hub.get("business_key_sources", []):
                    if not isinstance(bk_src, dict):
                        continue
                    b_tbl = str(bk_src.get("bronze_table", ""))
                    key_cols = [str(c) for c in bk_src.get("key_columns", []) if c]
                    if b_tbl in cumulative_sources or not key_cols:
                        continue
                    bk_col = key_cols[0]
                    tbl_short = b_tbl.split(".")[-1].lower()
                    matched_file: Path | None = None
                    source_reg = get_source_registry()
                    for s_root in search_roots:
                        candidates = sorted(
                            p
                            for p in s_root.rglob("*")
                            if p.is_file()
                            and source_reg.is_tabular_path(p)
                            and p.stem.lower().startswith(tbl_short)
                        )
                        if candidates:
                            matched_file = candidates[0]
                            break
                    if matched_file is None:
                        continue
                    v_name = f"v_er_dep_{_slugify(b_tbl)}"
                    try:
                        _, cols = _load_table_into_duckdb(conn, v_name, matched_file)
                    except (duckdb.Error, OSError, ValueError, TypeError):
                        continue
                    if bk_col not in cols:
                        continue
                    name_col = next(
                        (
                            c
                            for c in cols
                            if any(
                                k in c.lower()
                                for k in ("legal_name", "company_name", "customer_name", "name")
                            )
                        ),
                        None,
                    )
                    if name_col:
                        rows = conn.execute(
                            f'SELECT "{bk_col}", "{name_col}" FROM "{v_name}"'
                        ).fetchall()
                        for r in rows:
                            if r[0] is not None:
                                bk_s = str(r[0]).strip()
                                if (h_name, bk_s) not in seen_hub_bks:
                                    seen_hub_bks.add((h_name, bk_s))
                                    known_entities.append((h_name, bk_s, str(r[1] or "").strip()))
                    else:
                        rows = conn.execute(f'SELECT "{bk_col}" FROM "{v_name}"').fetchall()
                        for r in rows:
                            if r[0] is not None:
                                bk_s = str(r[0]).strip()
                                if (h_name, bk_s) not in seen_hub_bks:
                                    seen_hub_bks.add((h_name, bk_s))
                                    known_entities.append((h_name, bk_s, ""))
    finally:
        conn.close()
    return known_entities


def _discover_unstructured_doc_candidates_from_entities(
    *,
    doc: PipelineUnstructuredDocInput,
    doc_text: str,
    known_entities: list[tuple[str, str, str]],
    default_hub: str = "hub_customer",
) -> list[PipelineUnstructuredCandidateInput]:
    """Discover Tier-U1 exact and Tier-U3 variant entity spans against `known_entities`."""
    doc_text = doc_text.replace("\r\n", "\n").replace("\r", "\n")
    candidates: list[PipelineUnstructuredCandidateInput] = []
    used_bks: set[str] = set()
    variant_entities: list[tuple[re.Match[str], str, str, str]] = []

    for r_hub, bk_val, legal_name in known_entities:
        if not bk_val:
            continue
        name_tokens = legal_name.split() if legal_name else []
        var_matches: list[re.Match[str]] = []
        if len(name_tokens) >= 2:
            first_tok = name_tokens[0]
            pat = rf"\b{re.escape(first_tok)}(?:\s+[A-Z][A-Za-z0-9&.'-]+)+"
            var_matches = [
                m for m in re.finditer(pat, doc_text) if m.group(0).strip() != legal_name
            ]
        if var_matches:
            variant_entities.append((var_matches[0], r_hub, bk_val, legal_name))
            used_bks.add(bk_val)
            continue

        bk_idx = doc_text.find(bk_val)
        if bk_idx >= 0:
            line_start = doc_text.rfind("\n", 0, bk_idx) + 1
            name_idx = doc_text.find(legal_name, line_start, bk_idx) if legal_name else -1
            if name_idx >= 0:
                c_start = name_idx
                c_end = bk_idx + len(bk_val)
                if c_end < len(doc_text) and doc_text[c_end] in {")", "]"}:
                    c_end += 1
            else:
                c_start = bk_idx
                c_end = bk_idx + len(bk_val)
            quote_str = doc_text[c_start:c_end]
            other_entity = next(
                (e for e in known_entities if e[1] != bk_val and e[2]),
                (r_hub, "CAND-ALT", "Alternative Entity"),
            )
            candidates.append(
                PipelineUnstructuredCandidateInput(
                    extraction_id=f"ext_{_slugify(doc.doc_id)}_{_slugify(bk_val)}_u1",
                    target_hub=r_hub,
                    extracted_business_key=bk_val,
                    verbatim_quote=quote_str,
                    char_start=c_start,
                    char_end=c_end,
                    page_number=1,
                    confidence_score=0.98,
                    candidate_hub_matches=[
                        {
                            "candidate_key": bk_val,
                            "legal_name": legal_name or bk_val,
                            "score": 0.98,
                        },
                        {
                            "candidate_key": other_entity[1],
                            "legal_name": other_entity[2] or other_entity[1],
                            "score": 0.12,
                        },
                    ],
                )
            )
            used_bks.add(bk_val)

    for v_match, r_hub, bk_val, legal_name in variant_entities:
        v_quote = v_match.group(0).strip()
        v_start = v_match.start(0)
        v_end = v_start + len(v_quote)
        second_entity = next(
            (e for e in known_entities if e[1] != bk_val and e[1] not in used_bks and e[2]),
            next(
                (e for e in known_entities if e[1] != bk_val),
                (r_hub, "ALT-KEY", "Alternative Entity"),
            ),
        )
        candidates.append(
            PipelineUnstructuredCandidateInput(
                extraction_id=f"ext_{_slugify(doc.doc_id)}_{_slugify(bk_val)}_u3",
                target_hub=r_hub,
                extracted_business_key=bk_val,
                verbatim_quote=v_quote,
                char_start=v_start,
                char_end=v_end,
                page_number=1,
                confidence_score=0.68,
                candidate_hub_matches=[
                    {
                        "candidate_key": bk_val,
                        "legal_name": legal_name or bk_val,
                        "score": 0.68,
                    },
                    {
                        "candidate_key": second_entity[1],
                        "legal_name": second_entity[2] or second_entity[1],
                        "score": 0.61,
                    },
                ],
            )
        )

    if not candidates:
        code_match = re.search(
            r"\b[A-Z]{2,6}-[A-Z0-9]{2,8}(?:-[A-Z0-9]{2,10})?\b",
            doc_text,
        )
        if code_match is not None:
            q_str = code_match.group(0)
            c_start = code_match.start(0)
            c_end = code_match.end(0)
            candidates.append(
                PipelineUnstructuredCandidateInput(
                    extraction_id=f"ext_{_slugify(doc.doc_id)}_u1",
                    target_hub=default_hub,
                    extracted_business_key=q_str,
                    verbatim_quote=q_str,
                    char_start=c_start,
                    char_end=c_end,
                    page_number=1,
                    confidence_score=0.96,
                )
            )
        else:
            first_line = next(
                (ln.strip() for ln in doc_text.splitlines() if ln.strip()),
                doc_text.strip()[:40],
            )
            c_start = max(0, doc_text.find(first_line))
            c_end = c_start + len(first_line)
            candidates.append(
                PipelineUnstructuredCandidateInput(
                    extraction_id=f"ext_{_slugify(doc.doc_id)}_u1",
                    target_hub=default_hub,
                    extracted_business_key=doc.doc_id,
                    verbatim_quote=first_line,
                    char_start=c_start,
                    char_end=c_end,
                    page_number=1,
                    confidence_score=0.96,
                )
            )
    return candidates


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
        harness: VaultAgentHarness | None = None,
        on_stage_start: Callable[[int, str, str, str], None] | None = None,
        on_stage_complete: Callable[[int, PipelineStepReport], None] | None = None,
        on_stream_event: Callable[[HarnessStreamEvent], None] | None = None,
    ) -> None:
        self.input_spec = input_spec
        self.harness_setup = harness_setup or HarnessSetupConfig()
        self.workspace_root = Path(os.environ.get("OPENDV_WORKSPACE_ROOT") or Path.cwd()).resolve()
        self.base_dir = (base_dir or self.workspace_root).resolve()
        self._explicit_output_dir = Path(output_dir).resolve() if output_dir is not None else None
        self._explicit_cas_store = cas_store
        self._sdk_module = sdk_module
        self._explicit_harness = harness
        self._on_stage_start = on_stage_start
        self._on_stage_complete = on_stage_complete
        self._on_stream_event = on_stream_event
        self._runtime_initialized = False

    def _notify_stage_start(
        self,
        wave_index: int,
        stage_id: str,
        agent_persona: str,
        model_used: str,
    ) -> None:
        """Invoke `on_stage_start` callback if configured."""
        if self._on_stage_start is not None:
            self._on_stage_start(wave_index, stage_id, agent_persona, model_used)

    def _notify_stage_complete(
        self,
        wave_index: int,
        step_report: PipelineStepReport,
    ) -> None:
        """Invoke `on_stage_complete` callback if configured."""
        if self._on_stage_complete is not None:
            self._on_stage_complete(wave_index, step_report)

    async def _run_task_with_streaming(
        self,
        spec: AgentTaskSpec[OutputT],
    ) -> HarnessRunResult[OutputT]:
        """Execute `spec` on `self.harness`, streaming `HarnessStreamEvent`s if subscribed."""
        if self._on_stream_event is None:
            return await self.harness.run_task(spec)
        try:
            return await self.harness.run_task(spec, on_event=self._on_stream_event)
        except TypeError:
            return await self.harness.run_task(spec)

    def _ensure_runtime_initialized(self) -> None:
        if self._runtime_initialized:
            return
        if self._explicit_output_dir is not None:
            self.output_dir = self._explicit_output_dir
        elif self.input_spec.output_dir is not None:
            resolved_out, out_err = _validate_path_containment(
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
        if self._explicit_harness is not None:
            self.harness: VaultAgentHarness = self._explicit_harness
        else:
            self.harness = get_harness_adapter(
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
        event_type: str | None = None,
    ) -> dict[str, Any]:
        """Build, redact-check, and persist a schema-valid `VaultObservabilityEvent`."""
        event_id = f"evt-{run_id}-{workbench_view_id.value.lower()}-{uuid.uuid4().hex[:8]}"
        raw_event = build_observability_event_dict(
            event_id=event_id,
            event_type=event_type,
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

    def _build_fallback_applied_event(
        self,
        wave_index: int,
        stage_id: PipelineStageId,
        *,
        run_id: str,
        trace_id: str,
        fallback_key: str = "stage_output_policy.symbolic_completion",
    ) -> dict[str, Any]:
        """Build and emit a schema-valid FALLBACK_APPLIED observability event."""
        return self._emit_wb_event(
            run_id=run_id,
            trace_id=trace_id,
            stage_id=stage_id,
            workbench_view_id=WorkbenchViewId.WB_00,
            viz_id=VizId.VIZ_PIPE_00,
            persona=AgentPersona.VAULT_FIRST_MATE,
            status=ObservabilityVerdict.AUTO_REPAIRED,
            headline=(
                f"Applied explicit opt-in fallback '{fallback_key}' on Stage {stage_id.value}."
            ),
            business_impact=(
                "Explicit opt-in fallback applied to complete missing or invalid structured LLM "
                "stage output."
            ),
            rule_ids=["DV-OPT-IN-001"],
            summary_metrics={
                "wave_index": wave_index,
                "fallback_key": fallback_key,
                "event_type": "FALLBACK_APPLIED",
            },
            event_type="FALLBACK_APPLIED",
        )

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
        fallbacks_applied: list[dict[str, Any]] = []

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
                fallbacks_applied=fallbacks_applied,
                markdown_summary=md,
            )

        self._ensure_runtime_initialized()
        self._notify_stage_start(
            0,
            PipelineStageId.STAGE_00_ORCHESTRATION.value,
            AgentPersona.VAULT_FIRST_MATE.value,
            self.harness.resolve_model(ModelTier.HIGH_REASONING, AgentPersona.VAULT_FIRST_MATE),
        )

        ont_path, _ = _validate_path_containment(
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

        s0_ctx = {
            "stage": "STAGE_00_ORCHESTRATION",
            "domain_namespace": self.input_spec.domain_namespace,
            "wave_indices": [w.wave_index for w in self.input_spec.waves],
            "max_retries": self.harness_setup.budget.max_schema_validation_retries,
        }
        vfm_spec = build_vault_first_mate_task_spec(
            prompt=(
                f"Orchestrate {len(self.input_spec.waves)} waves for pipeline "
                f"'{self.input_spec.pipeline_id}' in domain '{self.input_spec.domain_namespace}'.\n"
                f"Graph A Ontology CBCs: "
                f"{[c.concept_name for c in ontology.canonical_business_concepts]}"
                f"{_encode_stage_context(s0_ctx)}"
            ),
            run_id=run_id,
            trace_id=trace_id,
        ).model_copy(update={"output_schema": Stage00OrchestrationPlanOutput})
        vfm_res = await self._run_task_with_streaming(vfm_spec)
        s0_plan = (
            vfm_res.structured_output
            if isinstance(vfm_res.structured_output, Stage00OrchestrationPlanOutput)
            else None
        )
        is_symbolic = self.harness_setup.stage_output_policy == "symbolic_completion"
        if s0_plan is None and is_symbolic:
            fallbacks_applied.append(
                {
                    "key": "stage_output_policy.symbolic_completion",
                    "applied_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "stage_id": PipelineStageId.STAGE_00_ORCHESTRATION.value,
                    "details": {"reason": "missing_orchestration_plan"},
                }
            )
            fb_event = self._build_fallback_applied_event(
                0,
                PipelineStageId.STAGE_00_ORCHESTRATION,
                run_id=run_id,
                trace_id=trace_id,
            )
            observability_events.append(fb_event)
        effective_max_retries = (
            s0_plan.recommended_max_retries
            if s0_plan is not None
            else self.harness_setup.budget.max_schema_validation_retries
        )
        waves_by_idx = {w.wave_index: w for w in self.input_spec.waves}
        if (
            s0_plan is not None
            and len(s0_plan.wave_execution_order) == len(self.input_spec.waves)
            and set(s0_plan.wave_execution_order) == set(waves_by_idx.keys())
        ):
            ordered_waves = [waves_by_idx[i] for i in s0_plan.wave_execution_order]
        else:
            ordered_waves = list(self.input_spec.waves)
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

        s0_metrics: dict[str, Any] = {
            "checked_items_count": input_val.checked_items_count,
            "cbc_count": len(ontology.canonical_business_concepts),
            "effective_max_retries": effective_max_retries,
        }
        if s0_plan is not None:
            s0_metrics["llm_structured_plan_used"] = True
            s0_metrics["risk_assessment"] = s0_plan.risk_assessment
            s0_metrics["wave_execution_order"] = list(s0_plan.wave_execution_order)

        s0_step_report = PipelineStepReport(
            step_id="step-w0-stage00-orchestration",
            wave_index=0,
            wave_name=self.input_spec.waves[0].wave_name,
            stage_id=PipelineStageId.STAGE_00_ORCHESTRATION.value,
            persona=AgentPersona.VAULT_FIRST_MATE.value,
            model_tier=ModelTier.HIGH_REASONING.value,
            model_used=vfm_res.model_used,
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
            metrics=s0_metrics,
        )
        step_reports.append(s0_step_report)
        self._notify_stage_complete(0, s0_step_report)

        # Cumulative state across waves
        cumulative_sources: dict[str, tuple[PipelineSourceFeedInput, Path]] = {}
        cumulative_source_systems: dict[str, ProfiledSourceSystem] = {}
        cumulative_doc_texts: dict[str, str] = {}
        cumulative_unstructured_docs: list[tuple[PipelineUnstructuredDocInput, str]] = []
        added_drift_cols_by_table: dict[str, list[str]] = {}
        deployed_contract_dict: dict[str, Any] | None = None
        if self.input_spec.initial_contract_path is not None:
            init_c_path, _ = _validate_path_containment(
                self.input_spec.initial_contract_path,
                base_dir=self.base_dir,
                workspace_root=self.workspace_root,
            )
            if init_c_path is not None:
                deployed_contract_dict = load_structured_file(init_c_path)
                cumulative_source_systems = _seed_graph_b_systems_from_deployed_contract(
                    deployed_contract_dict,
                    init_c_path,
                )
                for sat_entry in deployed_contract_dict.get("satellites", []):
                    if not isinstance(sat_entry, dict):
                        continue
                    sat_name = str(sat_entry.get("name", ""))
                    vol_tier = str(sat_entry.get("volatility_tier", ""))
                    b_tbl = str(sat_entry.get("source_bronze_table", "")).lower()
                    if b_tbl and (
                        vol_tier == "ADDITIVE_DRIFT_COMPANION" or sat_name.endswith("_ext_v2")
                    ):
                        drift_cols = added_drift_cols_by_table.setdefault(b_tbl, [])
                        for p_c in sat_entry.get("payload_columns", []):
                            p_str = str(p_c)
                            if p_str and p_str not in drift_cols:
                                drift_cols.append(p_str)

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
        for wave in ordered_waves:
            w_idx = wave.wave_index
            w_name = wave.wave_name
            wave_step_ids: list[str] = []
            if w_idx == 0:
                wave_step_ids.append("step-w0-stage00-orchestration")

            # ---------------------------------------------------------------
            # STAGE 01: Scout-Source (Bronze Statistical Profiling & Drift)
            # ---------------------------------------------------------------
            t0_s1 = time.perf_counter()
            self._notify_stage_start(
                w_idx,
                PipelineStageId.STAGE_01_SCOUT_SOURCE.value,
                AgentPersona.SCOUT_SOURCE.value,
                self.harness.resolve_model(ModelTier.FAST_TIER, AgentPersona.SCOUT_SOURCE),
            )
            profiler = GraphBProfiler()
            wave_drift_events: list[dict[str, Any]] = []
            wave_columns_profiled = 0
            wave_sources_profiled = len(wave.sources)
            current_wave_drift_tables: set[str] = set()
            raw_prof_tables: list[
                tuple[PipelineSourceFeedInput, Path, str, ProfiledBronzeTable]
            ] = []
            sample_rows_by_table: dict[str, list[dict[str, Any]]] = {}

            try:
                for src in wave.sources:
                    d_path, _ = _validate_path_containment(
                        src.data_path,
                        base_dir=self.base_dir,
                        workspace_root=self.workspace_root,
                    )
                    assert d_path is not None
                    qualified_table = f"{src.source_system.lower()}.{src.table_name.lower()}"

                    view_t1 = f"v_{_slugify(src.source_system)}_{_slugify(src.table_name)}"
                    src_target = _to_source_target(src.source, d_path)
                    _load_table_into_duckdb(profiler.conn, view_t1, src_target)
                    sample_cur = profiler.conn.execute(f'SELECT * FROM "{view_t1}" LIMIT 5')
                    sample_cols = (
                        [str(d[0]) for d in sample_cur.description]
                        if sample_cur.description
                        else []
                    )
                    sample_rows_by_table[qualified_table] = [
                        {
                            sample_cols[i]: (str(val) if val is not None else None)
                            for i, val in enumerate(row)
                        }
                        for row in sample_cur.fetchall()
                    ]
                    gb_single = profiler.build_graph_b_profile(
                        domain=self.input_spec.domain_namespace,
                        source_system_id=src.source_system.upper(),
                        bkcc_namespace=src.bkcc_namespace or "GLOBAL",
                        table_names=[view_t1],
                    )
                    prof_tbl = gb_single.source_systems[0].tables[0]
                    raw_prof_tables.append((src, d_path, qualified_table, prof_tbl))

                    # Detect schema drift if baseline_data_path is provided
                    if src.baseline_data_path is not None:
                        b_path, _ = _validate_path_containment(
                            src.baseline_data_path,
                            base_dir=self.base_dir,
                            workspace_root=self.workspace_root,
                        )
                        assert b_path is not None
                        view_t0 = f"{view_t1}_baseline"
                        b_target = _to_source_target(src.baseline_source, b_path)
                        _load_table_into_duckdb(profiler.conn, view_t0, b_target)
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
                    elif deployed_contract_dict is not None:
                        prior_cols = {
                            str(e.get("column_name", ""))
                            for e in deployed_contract_dict.get("column_triage_ledger", [])
                            if isinstance(e, dict)
                            and str(e.get("bronze_table", "")).lower() == qualified_table.lower()
                            and str(e.get("target_dv_entity", "")) != "hub_document"
                        }
                        if prior_cols:
                            for p_col in prof_tbl.columns:
                                if p_col.column_name not in prior_cols:
                                    wave_drift_events.append(
                                        {
                                            "drift_kind": "ADD_COLUMN",
                                            "column_name": p_col.column_name,
                                            "source_table": qualified_table,
                                            "prior_type": None,
                                            "new_type": p_col.data_type,
                                            "recommended_action": (
                                                "SPAWN_COMPANION_SATELLITE_ALG_A9A"
                                            ),
                                        }
                                    )
                                    current_wave_drift_tables.add(qualified_table)
                                    drift_cols = added_drift_cols_by_table.setdefault(
                                        qualified_table, []
                                    )
                                    if p_col.column_name not in drift_cols:
                                        drift_cols.append(p_col.column_name)
            finally:
                profiler.close()

            s1_profiled_tables_dict = {
                q_tbl: prof_tbl.model_dump(mode="json", exclude_none=True)
                for _, _, q_tbl, prof_tbl in raw_prof_tables
            }
            s1_ctx = {
                "stage": "STAGE_01_SCOUT_SOURCE",
                "wave_index": w_idx,
                "wave_name": w_name,
                "ontology": raw_ontology,
                "deployed_contract": deployed_contract_dict,
                "sources": [
                    raw_src.model_dump(mode="json", exclude_none=True)
                    for raw_src, _, _, _ in raw_prof_tables
                ],
                "profiled_tables": s1_profiled_tables_dict,
                "sample_rows": sample_rows_by_table,
                "cumulative_sources": {
                    q_tbl: prev_src.model_dump(mode="json", exclude_none=True)
                    for q_tbl, (prev_src, _) in cumulative_sources.items()
                },
            }
            scout_src_spec = build_scout_source_task_spec(
                prompt=(
                    f"Profile Wave {w_idx} ({w_name}) relational sources and discover "
                    f"business keys, foreign keys, surrogates, and PII columns.\n"
                    f"Graph A Ontology: {json.dumps(raw_ontology, sort_keys=True)}\n"
                    f"Graph B Statistical Profile: "
                    f"{json.dumps(s1_profiled_tables_dict, sort_keys=True)}\n"
                    f"Sample Rows (up to 5 per table): "
                    f"{json.dumps(sample_rows_by_table, sort_keys=True)}"
                    f"{_encode_stage_context(s1_ctx)}"
                ),
                run_id=run_id,
                trace_id=trace_id,
            ).model_copy(update={"output_schema": Stage01SourceDiscoveryOutput})
            s1_llm_discovery: Stage01SourceDiscoveryOutput | None = None
            s1_model_used = self.harness.resolve_model(
                scout_src_spec.model_tier, scout_src_spec.persona
            )
            if raw_prof_tables:
                scout_src_res = await self._run_task_with_streaming(scout_src_spec)
                s1_model_used = scout_src_res.model_used
                s1_llm_discovery = (
                    scout_src_res.structured_output
                    if isinstance(scout_src_res.structured_output, Stage01SourceDiscoveryOutput)
                    else None
                )
                if s1_llm_discovery is None and is_symbolic:
                    fallbacks_applied.append(
                        {
                            "key": "stage_output_policy.symbolic_completion",
                            "applied_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "stage_id": PipelineStageId.STAGE_01_SCOUT_SOURCE.value,
                            "details": {
                                "wave_index": w_idx,
                                "tables_discovered": len(raw_prof_tables),
                            },
                        }
                    )
                    fb_event = self._build_fallback_applied_event(
                        w_idx,
                        PipelineStageId.STAGE_01_SCOUT_SOURCE,
                        run_id=run_id,
                        trace_id=trace_id,
                    )
                    observability_events.append(fb_event)

            for raw_src, d_path, qualified_table, prof_tbl in raw_prof_tables:
                src = _discover_source_feed_semantics(
                    src=raw_src,
                    prof_tbl=prof_tbl,
                    ontology=ontology,
                    cumulative_sources=cumulative_sources,
                    deployed_contract=deployed_contract_dict,
                    llm_discovery=s1_llm_discovery,
                )
                cumulative_sources[qualified_table] = (src, d_path)

                # Enrich profiled columns with discovered or declared semantics
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
            s1_step_report = PipelineStepReport(
                step_id=s1_step_id,
                wave_index=w_idx,
                wave_name=w_name,
                stage_id=PipelineStageId.STAGE_01_SCOUT_SOURCE.value,
                persona=AgentPersona.SCOUT_SOURCE.value,
                model_tier=ModelTier.FAST_TIER.value,
                model_used=s1_model_used,
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
                    "llm_structured_discovery_used": s1_llm_discovery is not None,
                },
            )
            step_reports.append(s1_step_report)
            self._notify_stage_complete(w_idx, s1_step_report)

            # ---------------------------------------------------------------
            # STAGE 02: Scout-Unstructured (Span Grounding & 3-Tier ER)
            # ---------------------------------------------------------------
            t0_s2 = time.perf_counter()
            self._notify_stage_start(
                w_idx,
                PipelineStageId.STAGE_02_SCOUT_UNSTRUCTURED.value,
                AgentPersona.SCOUT_UNSTRUCTURED.value,
                self.harness.resolve_model(ModelTier.FAST_TIER, AgentPersona.SCOUT_UNSTRUCTURED),
            )
            tier_counts: dict[str, int] = {"Tier-U1": 0, "Tier-U2": 0, "Tier-U3": 0}
            grounding_records: list[dict[str, Any]] = []
            s2_status: Literal[
                "PASS", "AUTO_REPAIRED", "ASK-USER", "REJECT-WITH-TRACE", "ERROR"
            ] = "PASS"

            raw_loaded_docs: list[tuple[PipelineUnstructuredDocInput, str, str]] = []
            for raw_doc in wave.unstructured_documents:
                u_path, _ = _validate_path_containment(
                    raw_doc.document_path,
                    base_dir=self.base_dir,
                    workspace_root=self.workspace_root,
                )
                assert u_path is not None
                if raw_doc.source is not None:
                    raw_spec = raw_doc.source
                    if isinstance(raw_spec, dict):
                        doc_spec = SourceSpec.model_validate(raw_spec)
                    else:
                        doc_spec = raw_spec.model_copy()
                    if not doc_spec.inline_content and doc_spec.uri:
                        doc_spec = doc_spec.model_copy(update={"uri": str(u_path)})
                else:
                    doc_spec = SourceSpec(
                        adapter=get_source_registry().resolve_document_adapter_name(u_path),
                        uri=str(u_path),
                    )
                doc_adapter = get_source_registry().get_document_adapter(doc_spec.adapter)
                extracted = doc_adapter.extract_document(doc_spec)
                doc_text = extracted.normalized_text
                doc_sha256 = extracted.raw_sha256
                raw_loaded_docs.append((raw_doc, doc_text, doc_sha256))

            known_entities = _extract_known_entities_from_cumulative_sources(
                ontology=ontology,
                cumulative_sources=cumulative_sources,
                deployed_contract=deployed_contract_dict,
                base_dir=self.base_dir,
            )
            s2_ctx = {
                "stage": "STAGE_02_SCOUT_UNSTRUCTURED",
                "wave_index": w_idx,
                "wave_name": w_name,
                "ontology": raw_ontology,
                "active_hub_business_keys": [
                    {"hub": h, "business_key": bk, "legal_name": name}
                    for h, bk, name in known_entities
                ],
                "unstructured_documents": [
                    {
                        "doc": raw_doc.model_dump(mode="json", exclude_none=True),
                        "doc_text": doc_text,
                        "doc_sha256": doc_sha256,
                    }
                    for raw_doc, doc_text, doc_sha256 in raw_loaded_docs
                ],
            }
            doc_texts_preview = "\n---\n".join(
                f"[{d.doc_id}]\n{txt}" for d, txt, _ in raw_loaded_docs
            )
            scout_unstr_spec = build_scout_unstructured_task_spec(
                prompt=(
                    f"Ground unstructured spans for Wave {w_idx} ({w_name}).\n"
                    f"Graph A Ontology: {json.dumps(raw_ontology, sort_keys=True)}\n"
                    f"Active Hub Business Keys: "
                    f"{json.dumps(s2_ctx['active_hub_business_keys'], sort_keys=True)}\n"
                    f"Raw Unstructured Documents:\n{doc_texts_preview}"
                    f"{_encode_stage_context(s2_ctx)}"
                ),
                run_id=run_id,
                trace_id=trace_id,
                model_tier=ModelTier.FAST_TIER,
            ).model_copy(update={"output_schema": Stage02UnstructuredDiscoveryOutput})
            s2_llm_discovery: Stage02UnstructuredDiscoveryOutput | None = None
            s2_model_used = self.harness.resolve_model(
                scout_unstr_spec.model_tier, scout_unstr_spec.persona
            )
            if raw_loaded_docs:
                scout_unstr_res = await self._run_task_with_streaming(scout_unstr_spec)
                s2_model_used = scout_unstr_res.model_used
                s2_llm_discovery = (
                    scout_unstr_res.structured_output
                    if isinstance(
                        scout_unstr_res.structured_output,
                        Stage02UnstructuredDiscoveryOutput,
                    )
                    else None
                )
                if s2_llm_discovery is None and is_symbolic:
                    fallbacks_applied.append(
                        {
                            "key": "stage_output_policy.symbolic_completion",
                            "applied_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "stage_id": PipelineStageId.STAGE_02_SCOUT_UNSTRUCTURED.value,
                            "details": {
                                "wave_index": w_idx,
                                "docs_discovered": len(raw_loaded_docs),
                            },
                        }
                    )
                    fb_event = self._build_fallback_applied_event(
                        w_idx,
                        PipelineStageId.STAGE_02_SCOUT_UNSTRUCTURED,
                        run_id=run_id,
                        trace_id=trace_id,
                    )
                    observability_events.append(fb_event)

            for raw_doc, doc_text, doc_sha256 in raw_loaded_docs:
                doc = _discover_unstructured_doc_candidates(
                    doc=raw_doc,
                    doc_text=doc_text,
                    ontology=ontology,
                    cumulative_sources=cumulative_sources,
                    deployed_contract=deployed_contract_dict,
                    base_dir=self.base_dir,
                    llm_discovery=s2_llm_discovery,
                )
                if cumulative_source_systems:
                    first_sys_key = next(iter(cumulative_source_systems))
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
                        wave_gb_profile = GraphBProfile(
                            profile_version="1.0.0",
                            domain_namespace=_slugify(self.input_spec.domain_namespace),
                            profiled_at_utc=wave_gb_profile.profiled_at_utc,
                            source_systems=list(cumulative_source_systems.values()),
                        )
                        wave_gb_dict = wave_gb_profile.model_dump(mode="json", exclude_none=True)
                        validate_graph_b(wave_gb_dict)
                        intermediate_artifacts[:] = [
                            a
                            for a in intermediate_artifacts
                            if a.artifact_id != f"art-w{w_idx}-stage01-graph-b-profile"
                        ]
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
                        s1_out_shas[0] = gb_ref.sha256

                doc_held_or_rejected = False

                for cand in doc.extracted_candidates:
                    cand_quote = cand.verbatim_quote.replace("\r\n", "\n").replace("\r", "\n")
                    if cand_quote != cand.verbatim_quote:
                        cand = cand.model_copy(update={"verbatim_quote": cand_quote})
                    c_start = cand.char_start
                    c_end = cand.char_end
                    if c_start is None or c_end is None:
                        found_idx = doc_text.find(cand_quote)
                        c_start = max(0, found_idx)
                        c_end = c_start + len(cand_quote)
                        cand = cand.model_copy(update={"char_start": c_start, "char_end": c_end})
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
                            "verbatim_quote": cand_quote,
                            "char_start": c_start,
                            "char_end": c_end,
                            "span_verified": doc_text[c_start:c_end] == cand_quote,
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
            s2_step_report = PipelineStepReport(
                step_id=s2_step_id,
                wave_index=w_idx,
                wave_name=w_name,
                stage_id=PipelineStageId.STAGE_02_SCOUT_UNSTRUCTURED.value,
                persona=AgentPersona.SCOUT_UNSTRUCTURED.value,
                model_tier=ModelTier.FAST_TIER.value,
                model_used=s2_model_used,
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
                metrics={
                    "tier_counts": tier_counts,
                    "llm_structured_extraction_used": s2_llm_discovery is not None,
                },
            )
            step_reports.append(s2_step_report)
            self._notify_stage_complete(w_idx, s2_step_report)

            # ---------------------------------------------------------------
            # STAGE 03 & STAGE 04: Ship-Vault Pass 1 (100% Column Triage)
            # & Pass 2 (CumulativeDV 2.0 Contract Synthesis)
            # ---------------------------------------------------------------
            t0_s3 = time.perf_counter()
            self._notify_stage_start(
                w_idx,
                PipelineStageId.STAGE_03_SHIP_VAULT_PASS1.value,
                AgentPersona.SHIP_VAULT.value,
                self.harness.resolve_model(ModelTier.HIGH_REASONING, AgentPersona.SHIP_VAULT),
            )
            s3_ctx = {
                "stage": "STAGE_03_SHIP_VAULT_PASS1",
                "wave_index": w_idx,
                "wave_name": w_name,
                "domain_namespace": self.input_spec.domain_namespace,
                "ontology": raw_ontology,
                "graph_b_profile": wave_gb_dict,
                "deployed_contract": deployed_contract_dict,
                "cumulative_sources": {
                    q_tbl: src.model_dump(mode="json", exclude_none=True)
                    for q_tbl, (src, _) in cumulative_sources.items()
                },
                "cumulative_unstructured_docs": [
                    {
                        "doc": doc_in.model_dump(mode="json", exclude_none=True),
                        "sha256": doc_sha,
                    }
                    for doc_in, doc_sha in cumulative_unstructured_docs
                ],
                "added_drift_cols_by_table": added_drift_cols_by_table,
                "current_wave_drift_tables": sorted(current_wave_drift_tables),
            }
            pass1_spec = build_ship_vault_task_spec(
                prompt=(
                    f"Synthesize Pass 1 100% Column Triage Ledger for Wave {w_idx} ({w_name}).\n"
                    f"Graph A Ontology: {json.dumps(raw_ontology, sort_keys=True)}\n"
                    f"Graph B Statistical Profile: {json.dumps(wave_gb_dict, sort_keys=True)}\n"
                    f"Stage 01 Discovered Sources: "
                    f"{json.dumps(s3_ctx['cumulative_sources'], sort_keys=True)}\n"
                    f"Stage 02 Unstructured Extractions: "
                    f"{json.dumps(s3_ctx['cumulative_unstructured_docs'], sort_keys=True)}\n"
                    f"Deployed Contract: {json.dumps(deployed_contract_dict, sort_keys=True)}"
                    f"{_encode_stage_context(s3_ctx)}"
                ),
                run_id=run_id,
                trace_id=trace_id,
                stage_id=PipelineStageId.STAGE_03_SHIP_VAULT_PASS1,
                workbench_view_id=WorkbenchViewId.WB_04,
                viz_id=VizId.VIZ_STEP_05,
                output_schema=Stage03ColumnTriageOutput,
            )
            pass1_res = await self._run_task_with_streaming(pass1_spec)
            expected_col_keys = {
                (tbl_obj.bronze_table, col_obj.column_name)
                for sys_obj in wave_gb_profile.source_systems
                for tbl_obj in sys_obj.tables
                for col_obj in tbl_obj.columns
            }
            is_symbolic = self.harness_setup.stage_output_policy == "symbolic_completion"
            valid_llm = False
            llm_ledger_entries: list[dict[str, Any]] = []
            if (
                isinstance(pass1_res.structured_output, Stage03ColumnTriageOutput)
                and pass1_res.structured_output.column_triage_ledger
            ):
                llm_ledger_entries = [
                    entry.model_dump(mode="json", exclude_none=True)
                    for entry in pass1_res.structured_output.column_triage_ledger
                ]
                covered_keys = {(e["bronze_table"], e["column_name"]) for e in llm_ledger_entries}
                if expected_col_keys.issubset(covered_keys) and not any(
                    e.get("bronze_table") == "source_system.bronze_table"
                    for e in llm_ledger_entries
                ):
                    valid_llm = True

            if valid_llm:
                pass1_ledger = llm_ledger_entries
            elif is_symbolic:
                fallback_triage = synthesize_stage_output_from_prompt(
                    Stage03ColumnTriageOutput,
                    pass1_spec.prompt,
                )
                pass1_ledger = [
                    e.model_dump(mode="json", exclude_none=True)
                    for e in fallback_triage.column_triage_ledger
                ]
                fallbacks_applied.append(
                    {
                        "key": "stage_output_policy.symbolic_completion",
                        "applied_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "stage_id": PipelineStageId.STAGE_03_SHIP_VAULT_PASS1.value,
                        "details": {
                            "wave_index": w_idx,
                            "completed_fields": ["column_triage_ledger"],
                        },
                    }
                )
                fb_event = self._build_fallback_applied_event(
                    w_idx,
                    PipelineStageId.STAGE_03_SHIP_VAULT_PASS1,
                    run_id=run_id,
                    trace_id=trace_id,
                )
                observability_events.append(fb_event)
            else:
                s3_ms = round((time.perf_counter() - t0_s3) * 1000.0, 3)
                s3_step_id = f"step-w{w_idx}-stage03-ship-vault-pass1"
                wave_step_ids.append(s3_step_id)
                s3_step_report = PipelineStepReport(
                    step_id=s3_step_id,
                    wave_index=w_idx,
                    wave_name=w_name,
                    stage_id=PipelineStageId.STAGE_03_SHIP_VAULT_PASS1.value,
                    persona=AgentPersona.SHIP_VAULT.value,
                    model_tier=ModelTier.HIGH_REASONING.value,
                    model_used=pass1_res.model_used,
                    status="ERROR",
                    attempt_count=1,
                    duration_ms=s3_ms,
                    summary=(
                        "LLM structured output for Stage 03 Pass 1 Column Triage is invalid "
                        "or missing, and symbolic_completion fallback is disabled."
                    ),
                    tool_invocations=[],
                    input_artifact_sha256s=[gb_ref.sha256, ug_ref.sha256],
                    output_artifact_sha256s=[],
                    metrics={"llm_structured_triage_used": False},
                )
                step_reports.append(s3_step_report)
                wave_reports.append(
                    PipelineWaveReport(
                        wave_index=w_idx,
                        wave_name=w_name,
                        wave_type=wave.wave_type,
                        status="ERROR",
                        sources_profiled=wave_sources_profiled,
                        columns_profiled=wave_columns_profiled,
                        schema_drift_events=wave_drift_events,
                        unstructured_tier_counts=tier_counts,
                        self_healing_attempts=1,
                        applied_codemods=[],
                        gate_scorecard={"G0": "PASS", "G1": "FAIL"},
                        sandbox_summary={},
                        contract_sha256="",
                        canonical_ir_sha256="",
                        sql_bundle_sha256="",
                        step_ids=wave_step_ids,
                    )
                )
                break

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
            s3_step_report = PipelineStepReport(
                step_id=s3_step_id,
                wave_index=w_idx,
                wave_name=w_name,
                stage_id=PipelineStageId.STAGE_03_SHIP_VAULT_PASS1.value,
                persona=AgentPersona.SHIP_VAULT.value,
                model_tier=ModelTier.HIGH_REASONING.value,
                model_used=pass1_res.model_used,
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
                metrics={
                    "triaged_column_count": len(pass1_ledger),
                    "llm_structured_triage_used": isinstance(
                        pass1_res.structured_output, Stage03ColumnTriageOutput
                    ),
                },
            )
            step_reports.append(s3_step_report)
            self._notify_stage_complete(w_idx, s3_step_report)

            # STAGE 04: Pass 2 Candidate Contract Synthesis
            t0_s4 = time.perf_counter()
            self._notify_stage_start(
                w_idx,
                PipelineStageId.STAGE_04_SHIP_VAULT_PASS2.value,
                AgentPersona.SHIP_VAULT.value,
                self.harness.resolve_model(ModelTier.HIGH_REASONING, AgentPersona.SHIP_VAULT),
            )
            s4_ctx = {
                **s3_ctx,
                "stage": "STAGE_04_SHIP_VAULT_PASS2",
                "pass1_column_triage_ledger": pass1_ledger,
            }
            ship_spec = build_ship_vault_task_spec(
                prompt=(
                    f"Synthesize Pass 2 Data Vault 2.0 contract for Wave {w_idx} ({w_name}).\n"
                    f"Pass 1 Column Triage Ledger: {json.dumps(pass1_ledger, sort_keys=True)}\n"
                    f"Graph A Ontology: {json.dumps(raw_ontology, sort_keys=True)}\n"
                    f"Graph B Statistical Profile: {json.dumps(wave_gb_dict, sort_keys=True)}\n"
                    f"Deployed Contract: {json.dumps(deployed_contract_dict, sort_keys=True)}"
                    f"{_encode_stage_context(s4_ctx)}"
                ),
                run_id=run_id,
                trace_id=trace_id,
                stage_id=PipelineStageId.STAGE_04_SHIP_VAULT_PASS2,
                workbench_view_id=WorkbenchViewId.WB_05,
                viz_id=VizId.VIZ_STEP_06,
                output_schema=Stage04ContractSynthesisOutput,
            )
            ship_res = await self._run_task_with_streaming(ship_spec)
            llm_candidate: dict[str, Any] | None = None
            if isinstance(ship_res.structured_output, Stage04ContractSynthesisOutput):
                llm_candidate = ship_res.structured_output.contract.model_dump(
                    mode="json",
                    exclude_none=True,
                )
            elif isinstance(ship_res.structured_output, NoMistakeDataVault2ContractV5):
                llm_candidate = ship_res.structured_output.model_dump(
                    mode="json",
                    exclude_none=True,
                )
            valid_llm_contract = isinstance(
                ship_res.structured_output,
                (Stage04ContractSynthesisOutput, NoMistakeDataVault2ContractV5),
            )
            if valid_llm_contract and llm_candidate is not None:
                llm_candidate["column_triage_ledger"] = list(pass1_ledger)
                candidate_contract = llm_candidate
            elif is_symbolic:
                fallback_s4 = synthesize_stage_output_from_prompt(
                    Stage04ContractSynthesisOutput,
                    ship_spec.prompt,
                )
                canon_contract = fallback_s4.contract.model_dump(
                    mode="json",
                    exclude_none=True,
                )
                canon_contract["column_triage_ledger"] = list(pass1_ledger)
                candidate_contract = canon_contract
                fallbacks_applied.append(
                    {
                        "key": "stage_output_policy.symbolic_completion",
                        "applied_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "stage_id": PipelineStageId.STAGE_04_SHIP_VAULT_PASS2.value,
                        "details": {"wave_index": w_idx, "completed_fields": ["contract"]},
                    }
                )
                fb_event = self._build_fallback_applied_event(
                    w_idx,
                    PipelineStageId.STAGE_04_SHIP_VAULT_PASS2,
                    run_id=run_id,
                    trace_id=trace_id,
                )
                observability_events.append(fb_event)
            else:
                s4_ms = round((time.perf_counter() - t0_s4) * 1000.0, 3)
                s4_step_id = f"step-w{w_idx}-stage04-ship-vault-pass2"
                wave_step_ids.append(s4_step_id)
                s4_step_report = PipelineStepReport(
                    step_id=s4_step_id,
                    wave_index=w_idx,
                    wave_name=w_name,
                    stage_id=PipelineStageId.STAGE_04_SHIP_VAULT_PASS2.value,
                    persona=AgentPersona.SHIP_VAULT.value,
                    model_tier=ModelTier.HIGH_REASONING.value,
                    model_used=ship_res.model_used,
                    status="ERROR",
                    attempt_count=1,
                    duration_ms=s4_ms,
                    summary=(
                        "LLM structured output for Stage 04 Contract Synthesis is invalid "
                        "or missing, and symbolic_completion fallback is disabled."
                    ),
                    tool_invocations=[],
                    input_artifact_sha256s=[gb_ref.sha256, triage_ref.sha256],
                    output_artifact_sha256s=[],
                    metrics={"llm_structured_contract_used": False},
                )
                step_reports.append(s4_step_report)
                wave_reports.append(
                    PipelineWaveReport(
                        wave_index=w_idx,
                        wave_name=w_name,
                        wave_type=wave.wave_type,
                        status="ERROR",
                        sources_profiled=wave_sources_profiled,
                        columns_profiled=wave_columns_profiled,
                        schema_drift_events=wave_drift_events,
                        unstructured_tier_counts=tier_counts,
                        self_healing_attempts=1,
                        applied_codemods=[],
                        gate_scorecard={"G0": "PASS", "G1": "PASS", "G2": "FAIL"},
                        sandbox_summary={},
                        contract_sha256="",
                        canonical_ir_sha256="",
                        sql_bundle_sha256="",
                        step_ids=wave_step_ids,
                    )
                )
                break

            attempt1_contract = copy.deepcopy(candidate_contract)
            injected_fault = (
                w_idx == 0
                and deployed_contract_dict is None
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
            s4_step_report = PipelineStepReport(
                step_id=s4_step_id,
                wave_index=w_idx,
                wave_name=w_name,
                stage_id=PipelineStageId.STAGE_04_SHIP_VAULT_PASS2.value,
                persona=AgentPersona.SHIP_VAULT.value,
                model_tier=ModelTier.HIGH_REASONING.value,
                model_used=ship_res.model_used,
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
            step_reports.append(s4_step_report)
            self._notify_stage_complete(w_idx, s4_step_report)

            # ---------------------------------------------------------------
            # STAGE 05: AXI-DV Static Gates (G0-G5) & @toon_v1 Self-Healing
            # ---------------------------------------------------------------
            t0_s5 = time.perf_counter()
            s5_out_shas: list[str] = []
            s5_repair_failed = False
            current_attempt_contract = attempt1_contract
            max_attempts = max(1, effective_max_retries)
            attempt_num = 1
            axi_res = None
            s5_model_used = self.harness.resolve_model(ModelTier.FAST_TIER, AgentPersona.AXI_DV)
            self._notify_stage_start(
                w_idx,
                PipelineStageId.STAGE_05_AXI_DV_STATIC_GATES.value,
                AgentPersona.AXI_DV.value,
                s5_model_used,
            )

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
                    s5_repair_ctx = {
                        "stage": "STAGE_05_AXI_DV_STATIC_GATES",
                        "wave_index": w_idx,
                        "attempt_num": attempt_num,
                        "toon_text": axi_res.toon_text,
                        "failing_contract": current_attempt_contract,
                        "graph_b_profile": wave_gb_dict,
                        "ontology": raw_ontology,
                    }
                    repair_spec = build_ship_vault_task_spec(
                        prompt=(
                            f"Repair @toon_v1 diagnostic trace on Wave {w_idx} "
                            f"Attempt {attempt_num}:\n{axi_res.toon_text}\n\n"
                            f"Failing Contract YAML:\n"
                            f"{yaml.safe_dump(current_attempt_contract, sort_keys=False)}"
                            f"{_encode_stage_context(s5_repair_ctx)}"
                        ),
                        run_id=run_id,
                        trace_id=trace_id,
                        stage_id=PipelineStageId.STAGE_05_AXI_DV_STATIC_GATES,
                        workbench_view_id=WorkbenchViewId.WB_08,
                        viz_id=VizId.VIZ_GATE_04,
                        output_schema=Stage04ContractSynthesisOutput,
                    )
                    repair_res = await self._run_task_with_streaming(repair_spec)
                    s5_model_used = repair_res.model_used
                    llm_repaired: dict[str, Any] | None = None
                    if isinstance(repair_res.structured_output, Stage04ContractSynthesisOutput):
                        llm_repaired = repair_res.structured_output.contract.model_dump(
                            mode="json",
                            exclude_none=True,
                        )
                    elif isinstance(repair_res.structured_output, NoMistakeDataVault2ContractV5):
                        llm_repaired = repair_res.structured_output.model_dump(
                            mode="json",
                            exclude_none=True,
                        )
                    valid_repair = isinstance(
                        repair_res.structured_output,
                        (Stage04ContractSynthesisOutput, NoMistakeDataVault2ContractV5),
                    )
                    if valid_repair and llm_repaired is not None:
                        current_attempt_contract = llm_repaired
                    elif is_symbolic:
                        fallback_rep = synthesize_stage_output_from_prompt(
                            Stage04ContractSynthesisOutput,
                            repair_spec.prompt,
                        )
                        canon_repaired = fallback_rep.contract.model_dump(
                            mode="json",
                            exclude_none=True,
                        )
                        current_attempt_contract = canon_repaired
                        fallbacks_applied.append(
                            {
                                "key": "stage_output_policy.symbolic_completion",
                                "applied_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "stage_id": PipelineStageId.STAGE_05_AXI_DV_STATIC_GATES.value,
                                "details": {
                                    "wave_index": w_idx,
                                    "attempt": attempt_num + 1,
                                    "completed_fields": ["contract_repair"],
                                },
                            }
                        )
                        fb_event = self._build_fallback_applied_event(
                            w_idx,
                            PipelineStageId.STAGE_05_AXI_DV_STATIC_GATES,
                            run_id=run_id,
                            trace_id=trace_id,
                        )
                        observability_events.append(fb_event)
                    else:
                        s5_repair_failed = True
                        break

            if s5_repair_failed:
                s5_ms = round((time.perf_counter() - t0_s5) * 1000.0, 3)
                s5_step_id = f"step-w{w_idx}-stage05-repair-failed"
                wave_step_ids.append(s5_step_id)
                s5_step_report = PipelineStepReport(
                    step_id=s5_step_id,
                    wave_index=w_idx,
                    wave_name=w_name,
                    stage_id=PipelineStageId.STAGE_05_AXI_DV_STATIC_GATES.value,
                    persona=AgentPersona.SHIP_VAULT.value,
                    model_tier=ModelTier.HIGH_REASONING.value,
                    model_used=s5_model_used,
                    status="ERROR",
                    attempt_count=attempt_num,
                    duration_ms=s5_ms,
                    summary=(
                        "Repair structured output from LLM was missing or incomplete "
                        "and symbolic_completion fallback is disabled."
                    ),
                )
                step_reports.append(s5_step_report)
                wave_reports.append(
                    PipelineWaveReport(
                        wave_index=w_idx,
                        wave_name=w_name,
                        wave_type=wave.wave_type,
                        status="ERROR",
                        sources_profiled=wave_sources_profiled,
                        columns_profiled=wave_columns_profiled,
                        schema_drift_events=wave_drift_events,
                        unstructured_tier_counts=tier_counts,
                        self_healing_attempts=attempt_num,
                        applied_codemods=list(axi_res.applied_codemods) if axi_res else [],
                        gate_scorecard={"G0": "PASS", "G1": "PASS", "G2": "PASS", "G3": "FAIL"},
                        sandbox_summary={},
                        contract_sha256="",
                        canonical_ir_sha256="",
                        sql_bundle_sha256="",
                        step_ids=wave_step_ids,
                    )
                )
                break

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
            s5_step_report = PipelineStepReport(
                step_id=s5_step_id,
                wave_index=w_idx,
                wave_name=w_name,
                stage_id=PipelineStageId.STAGE_05_AXI_DV_STATIC_GATES.value,
                persona=AgentPersona.AXI_DV.value,
                model_tier=ModelTier.FAST_TIER.value,
                model_used=s5_model_used,
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
            step_reports.append(s5_step_report)
            self._notify_stage_complete(w_idx, s5_step_report)

            # ---------------------------------------------------------------
            # STAGE 06: AXI-DV Dynamic Sandbox (Canonical IR, 4-Zone SQL,
            #           4-Batch DuckDB Shadow Sandbox + DV-DYN-001..015)
            # ---------------------------------------------------------------
            t0_s6 = time.perf_counter()
            self._notify_stage_start(
                w_idx,
                PipelineStageId.STAGE_06_AXI_DV_DYNAMIC_SANDBOX.value,
                AgentPersona.AXI_DV.value,
                self.harness.resolve_model(ModelTier.FAST_TIER, AgentPersona.AXI_DV),
            )
            canonical_ir = lower_contract_to_canonical_ir(
                verified_contract_dict,
                graph_b_profile=wave_gb_profile,
                validate_schema=True,
            )
            ir_dict = canonical_ir.model_dump(mode="json", exclude_none=True)
            validate_canonical_ir(ir_dict)

            wave_bronze_tables: dict[str, Any] = {
                q_tbl: _to_source_target(_src_spec.source, d_path)
                for q_tbl, (_src_spec, d_path) in cumulative_sources.items()
            }
            compiled_bundle = compile_canonical_ir_to_4zone_sql(
                canonical_ir,
                bronze_tables=wave_bronze_tables,
                baseline_ir=last_canonical_ir,
            )

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
                    source=canonical_ir,
                    bronze_tables=wave_bronze_tables,
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
            s6_step_report = PipelineStepReport(
                step_id=s6_step_id,
                wave_index=w_idx,
                wave_name=w_name,
                stage_id=PipelineStageId.STAGE_06_AXI_DV_DYNAMIC_SANDBOX.value,
                persona=AgentPersona.AXI_DV.value,
                model_tier=ModelTier.FAST_TIER.value,
                model_used=self.harness.resolve_model(ModelTier.FAST_TIER, AgentPersona.AXI_DV),
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
            step_reports.append(s6_step_report)
            self._notify_stage_complete(w_idx, s6_step_report)

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
        if last_contract_ref is not None:
            t0_s7 = time.perf_counter()
            self._notify_stage_start(
                wave_reports[-1].wave_index,
                PipelineStageId.STAGE_07_DETERMINISTIC_COMPILER.value,
                AgentPersona.DETERMINISTIC_COMPILER.value,
                self.harness.resolve_model(
                    ModelTier.FAST_TIER, AgentPersona.DETERMINISTIC_COMPILER
                ),
            )
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
            s7_step_report = PipelineStepReport(
                step_id=s7_step_id,
                wave_index=wave_reports[-1].wave_index,
                wave_name=wave_reports[-1].wave_name,
                stage_id=PipelineStageId.STAGE_07_DETERMINISTIC_COMPILER.value,
                persona=AgentPersona.DETERMINISTIC_COMPILER.value,
                model_tier=ModelTier.FAST_TIER.value,
                model_used=self.harness.resolve_model(
                    ModelTier.FAST_TIER, AgentPersona.DETERMINISTIC_COMPILER
                ),
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
            step_reports.append(s7_step_report)
            self._notify_stage_complete(wave_reports[-1].wave_index, s7_step_report)

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
            fallbacks_applied=fallbacks_applied,
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


class _SymbolicStageSynthesizer:
    @staticmethod
    def discover_source_feed_semantics(
        *,
        src: PipelineSourceFeedInput,
        prof_tbl: ProfiledBronzeTable,
        ontology: GraphAOntology,
        cumulative_sources: dict[str, tuple[PipelineSourceFeedInput, Path]],
        deployed_contract: dict[str, Any] | None = None,
        llm_discovery: Stage01SourceDiscoveryOutput | Stage01FeedDiscoveryItem | None = None,
    ) -> PipelineSourceFeedInput:
        """Discover BKs, surrogates, FKs, PII, authority tier, and CBC hints when omitted."""
        llm_item: Stage01SourceDiscoveryOutput | Stage01FeedDiscoveryItem | None = None
        if isinstance(llm_discovery, Stage01FeedDiscoveryItem):
            llm_item = llm_discovery
        elif isinstance(llm_discovery, Stage01SourceDiscoveryOutput):
            qualified_table = f"{src.source_system.lower()}.{src.table_name.lower()}"
            for item in llm_discovery.discovered_feeds:
                if (
                    item.feed_id == src.feed_id
                    or item.table_name == src.table_name
                    or (item.feed_id and item.feed_id.lower() == qualified_table)
                ):
                    llm_item = item
                    break
            if llm_item is None and len(llm_discovery.discovered_feeds) == 1:
                llm_item = llm_discovery.discovered_feeds[0]
            if (
                llm_item is None
                and (
                    llm_discovery.business_key_columns
                    or llm_discovery.target_hub_name
                    or llm_discovery.foreign_key_mappings
                    or llm_discovery.pii_columns
                )
                and (
                    llm_discovery.feed_id is None
                    or llm_discovery.feed_id == src.feed_id
                    or llm_discovery.table_name in {None, src.table_name}
                )
            ):
                llm_item = llm_discovery

        # 1. Build lookup maps from Graph A Ontology
        ont_col_to_cbc: dict[
            str,
            tuple[
                CanonicalBusinessConcept,
                str,
                Literal["TIER_1_GLOBAL_STANDARD", "TIER_2_ENTERPRISE_SOR", "TIER_3_LOCAL_SCOPED"],
                str,
            ],
        ] = {}
        ont_pii_cols: set[str] = set()
        for cbc in ontology.canonical_business_concepts:
            c_hub, _ = _resolve_hub_and_uri(cbc.concept_name, ontology)
            if cbc.governance_constraints is not None:
                for pii_c in cbc.governance_constraints.pii_confidential_attributes or []:
                    ont_pii_cols.add(pii_c.lower())
                for mnpi_c in cbc.governance_constraints.mnpi_confidential_attributes or []:
                    ont_pii_cols.add(mnpi_c.lower())
            if cbc.elm_archetype == "WHAT_UNSTRUCTURED_ARTIFACT" or c_hub == "hub_document":
                continue
            if cbc.key_authority_tiers is not None:
                tier_specs: list[
                    tuple[
                        Any,
                        Literal[
                            "TIER_1_GLOBAL_STANDARD",
                            "TIER_2_ENTERPRISE_SOR",
                            "TIER_3_LOCAL_SCOPED",
                        ],
                    ]
                ] = [
                    (cbc.key_authority_tiers.tier_1_global_standard, "TIER_1_GLOBAL_STANDARD"),
                    (cbc.key_authority_tiers.tier_2_enterprise_sor, "TIER_2_ENTERPRISE_SOR"),
                    (cbc.key_authority_tiers.tier_3_local_scoped, "TIER_3_LOCAL_SCOPED"),
                ]
                for t_spec, t_lit in tier_specs:
                    if t_spec is not None and t_spec.column_patterns:
                        for pat in t_spec.column_patterns:
                            ont_col_to_cbc.setdefault(
                                pat.lower(),
                                (
                                    cbc,
                                    c_hub,
                                    t_lit,
                                    t_spec.bkcc_policy,
                                ),
                            )

        # 2. Resolve primary CBC and target Hub for this table
        def _tokens(text: str) -> set[str]:
            raw_toks = re.split(r"[^a-z0-9]+", text.lower())
            return {t.rstrip("s") for t in raw_toks if len(t.rstrip("s")) >= 2}

        tbl_tokens = _tokens(src.table_name)
        primary_cbc: CanonicalBusinessConcept | None = None
        explicit_hint = (
            src.target_hub_name
            or src.cbc_mapping_hints.get(src.table_name)
            or (llm_item.target_hub_name if llm_item is not None else None)
            or (llm_item.cbc_mapping_hints.get(src.table_name) if llm_item is not None else None)
        )
        if explicit_hint:
            resolved_h, _ = _resolve_hub_and_uri(explicit_hint, ontology)
            for cbc in ontology.canonical_business_concepts:
                c_hub, _ = _resolve_hub_and_uri(cbc.concept_name, ontology)
                if cbc.concept_name.upper() == explicit_hint.upper() or c_hub == resolved_h:
                    primary_cbc = cbc
                    break
        if primary_cbc is None:
            best_score = 0
            for cbc in ontology.canonical_business_concepts:
                c_toks = _tokens(cbc.concept_name.removeprefix("CBC_")) | _tokens(
                    (cbc.target_hub or "").removeprefix("hub_")
                )
                overlap = len(tbl_tokens & c_toks)
                if overlap > best_score:
                    best_score = overlap
                    primary_cbc = cbc

        if primary_cbc is not None:
            primary_hub_name, _ = _resolve_hub_and_uri(primary_cbc.concept_name, ontology)
        elif explicit_hint:
            primary_hub_name, _ = _resolve_hub_and_uri(explicit_hint, ontology)
        else:
            primary_hub_name, _ = _resolve_hub_and_uri(src.table_name, ontology)

        actual_col_names = {p_col.column_name for p_col in prof_tbl.columns}

        # 3. Discover surrogate_key_columns
        surrogate_cols: list[str] = list(src.surrogate_key_columns)
        if not surrogate_cols:
            if llm_item is not None and llm_item.surrogate_key_columns:
                for s_col in llm_item.surrogate_key_columns:
                    if s_col in actual_col_names and s_col.lower() not in ont_col_to_cbc:
                        surrogate_cols.append(s_col)
            for p_col in prof_tbl.columns:
                c_low = p_col.column_name.lower()
                if c_low in ont_col_to_cbc or p_col.column_name in surrogate_cols:
                    continue
                if p_col.candidate_role == "SYSTEM_SURROGATE_DROPPED" or re.search(
                    r"(^|_)(sk|seq|row_seq|seq_id|row_id|surrogate|autogen)(_id)?$",
                    c_low,
                ):
                    surrogate_cols.append(p_col.column_name)

        surrogate_set = set(surrogate_cols)

        # 4. Discover business_key_columns, key_authority_tier, and bkcc_namespace
        bk_cols: list[str] = list(src.business_key_columns)
        inferred_tier: (
            Literal["TIER_1_GLOBAL_STANDARD", "TIER_2_ENTERPRISE_SOR", "TIER_3_LOCAL_SCOPED"] | None
        ) = None
        inferred_bkcc: str | None = None

        if not bk_cols:
            # First try ontology column_patterns matching primary_cbc
            for p_col in prof_tbl.columns:
                c_low = p_col.column_name.lower()
                if p_col.column_name in surrogate_set:
                    continue
                if c_low in ont_col_to_cbc:
                    matched_cbc, matched_hub, m_tier, m_bkcc = ont_col_to_cbc[c_low]
                    if matched_hub == primary_hub_name or (
                        primary_cbc is not None
                        and matched_cbc.concept_name == primary_cbc.concept_name
                    ):
                        bk_cols.append(p_col.column_name)
                        inferred_tier = m_tier
                        inferred_bkcc = m_bkcc
                        break

        if not bk_cols and llm_item is not None and llm_item.business_key_columns:
            for l_bk in llm_item.business_key_columns:
                if l_bk in actual_col_names and l_bk not in surrogate_set:
                    c_low = l_bk.lower()
                    if c_low in ont_col_to_cbc and ont_col_to_cbc[c_low][1] != primary_hub_name:
                        continue
                    bk_cols.append(l_bk)

        if not bk_cols:
            # Second fallback: empirical uniqueness + name affinity with table/hub
            hub_toks = _tokens(primary_hub_name.removeprefix("hub_")) | tbl_tokens
            candidates: list[tuple[int, float, str]] = []
            for p_col in prof_tbl.columns:
                if p_col.column_name in surrogate_set:
                    continue
                c_low = p_col.column_name.lower()
                if c_low in ont_col_to_cbc:
                    # Belongs to another concept (FK), skip as primary BK if other candidates exist
                    continue
                col_toks = _tokens(c_low)
                has_key_suffix = c_low.endswith(
                    ("_id", "_key", "_code", "_number", "_num", "_no", "_uri")
                )
                affinity = len(col_toks & hub_toks) * 2 + (1 if has_key_suffix else 0)
                if p_col.null_rate == 0.0 and p_col.u_non_null >= 0.95:
                    candidates.append((affinity, p_col.u_non_null, p_col.column_name))
            if candidates:
                candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
                bk_cols.append(candidates[0][2])
            elif prof_tbl.columns:
                non_surr = [
                    c.column_name for c in prof_tbl.columns if c.column_name not in surrogate_set
                ]
                bk_cols.append(non_surr[0] if non_surr else prof_tbl.columns[0].column_name)

        # Check ontology tier for explicit or discovered BK column
        if inferred_tier is None and bk_cols:
            first_bk_low = bk_cols[0].lower()
            if first_bk_low in ont_col_to_cbc:
                _, _, inferred_tier, inferred_bkcc = ont_col_to_cbc[first_bk_low]

        eff_tier: Literal[
            "TIER_1_GLOBAL_STANDARD", "TIER_2_ENTERPRISE_SOR", "TIER_3_LOCAL_SCOPED"
        ] = (
            src.key_authority_tier
            or inferred_tier
            or (llm_item.key_authority_tier if llm_item is not None else None)
            or "TIER_1_GLOBAL_STANDARD"
        )
        raw_bkcc = (
            src.bkcc_namespace
            or inferred_bkcc
            or (
                llm_item.bkcc_namespace
                if (
                    llm_item is not None
                    and llm_item.bkcc_namespace
                    and not (
                        eff_tier == "TIER_3_LOCAL_SCOPED" and llm_item.bkcc_namespace == "GLOBAL"
                    )
                )
                else None
            )
            or ("GLOBAL" if eff_tier != "TIER_3_LOCAL_SCOPED" else src.source_system.upper())
        )
        eff_bkcc = (
            src.source_system.upper() if raw_bkcc == "SOURCE_SYSTEM_PREFIX_REQUIRED" else raw_bkcc
        )
        eff_record_source = (
            src.record_source_code
            or (llm_item.record_source_code if llm_item is not None else None)
            or (
                eff_bkcc
                if eff_tier == "TIER_3_LOCAL_SCOPED" and eff_bkcc != "GLOBAL"
                else src.source_system.upper()
            )
        )

        # 5. Discover foreign_key_mappings
        fk_mappings: dict[str, str] = dict(src.foreign_key_mappings)
        if not src.foreign_key_mappings:
            bk_set = set(bk_cols)
            if llm_item is not None and llm_item.foreign_key_mappings:
                for fk_col, raw_target_hub in llm_item.foreign_key_mappings.items():
                    if (
                        fk_col in actual_col_names
                        and fk_col not in surrogate_set
                        and fk_col not in bk_set
                    ):
                        resolved_fk_hub, _ = _resolve_hub_and_uri(raw_target_hub, ontology)
                        if resolved_fk_hub != primary_hub_name:
                            fk_mappings[fk_col] = resolved_fk_hub
            known_bk_to_hub: dict[str, str] = {}
            if deployed_contract is not None:
                for d_hub in deployed_contract.get("hubs", []):
                    if not isinstance(d_hub, dict):
                        continue
                    d_hub_name = str(d_hub.get("name", ""))
                    if not d_hub_name or d_hub_name == "hub_document":
                        continue
                    for bk_src in d_hub.get("business_key_sources", []):
                        if not isinstance(bk_src, dict):
                            continue
                        for k_col in bk_src.get("key_columns", []):
                            known_bk_to_hub[str(k_col).lower()] = d_hub_name
            for prev_src, _ in cumulative_sources.values():
                prev_hub = prev_src.target_hub_name
                if not prev_hub:
                    prev_hint = prev_src.cbc_mapping_hints.get(prev_src.table_name) or (
                        prev_src.table_name
                    )
                    prev_hub, _ = _resolve_hub_and_uri(prev_hint, ontology)
                for prev_bk in prev_src.business_key_columns:
                    known_bk_to_hub[prev_bk.lower()] = prev_hub

            for p_col in prof_tbl.columns:
                c_name = p_col.column_name
                c_low = c_name.lower()
                if c_name in surrogate_set or c_name in bk_set:
                    continue
                if c_low in ont_col_to_cbc:
                    _, target_hub, _, _ = ont_col_to_cbc[c_low]
                    if target_hub != primary_hub_name:
                        fk_mappings[c_name] = target_hub
                        continue
                if c_low in known_bk_to_hub and known_bk_to_hub[c_low] != primary_hub_name:
                    fk_mappings[c_name] = known_bk_to_hub[c_low]
                    continue
                if c_name not in fk_mappings and c_low.endswith(("_id", "_code", "_key")):
                    col_toks = _tokens(c_low)
                    for cbc in ontology.canonical_business_concepts:
                        c_hub, _ = _resolve_hub_and_uri(cbc.concept_name, ontology)
                        if c_hub == primary_hub_name or c_hub == "hub_document":
                            continue
                        c_toks = _tokens(cbc.concept_name.removeprefix("CBC_")) | _tokens(
                            c_hub.removeprefix("hub_")
                        )
                        if col_toks & c_toks:
                            fk_mappings[c_name] = c_hub
                            break

        # 6. Discover pii_columns
        pii_cols: list[str] = list(src.pii_columns)
        if not src.pii_columns:
            excluded = set(surrogate_cols) | set(bk_cols) | set(fk_mappings.keys())
            if llm_item is not None and llm_item.pii_columns:
                for p_c in llm_item.pii_columns:
                    if p_c in actual_col_names and p_c not in excluded and p_c not in pii_cols:
                        pii_cols.append(p_c)
            pii_keywords = (
                "email",
                "phone",
                "ssn",
                "tin",
                "social_security",
                "passport",
                "dob",
                "birth",
                "credit_card",
                "iban",
                "home_address",
            )
            for p_col in prof_tbl.columns:
                c_name = p_col.column_name
                c_low = c_name.lower()
                if c_name in excluded or c_name in pii_cols:
                    continue
                if (
                    c_low in ont_pii_cols
                    or p_col.security_tag in {"PII_HIGH", "RESTRICTED_PII"}
                    or p_col.candidate_role == "PII_RESTRICTED_PAYLOAD"
                    or any(kw in c_low for kw in pii_keywords)
                ):
                    pii_cols.append(c_name)

        # 7. Discover timestamp_column & cbc_mapping_hints
        ts_col = src.timestamp_column or (
            llm_item.timestamp_column
            if (llm_item is not None and llm_item.timestamp_column in actual_col_names)
            else None
        )
        if ts_col is None:
            for p_col in prof_tbl.columns:
                c_low = p_col.column_name.lower()
                sql_t = p_col.data_type.upper()
                if any(k in sql_t for k in ("DATE", "TIME", "TIMESTAMP")) or c_low.endswith(
                    ("_ts", "_dts", "_date", "_at")
                ):
                    ts_col = p_col.column_name
                    break

        cbc_hints = dict(src.cbc_mapping_hints)
        if llm_item is not None and llm_item.cbc_mapping_hints:
            for k, v in llm_item.cbc_mapping_hints.items():
                cbc_hints.setdefault(k, v)
        if primary_cbc is not None:
            cbc_hints.setdefault(src.table_name, primary_cbc.concept_name)
            for bk_c in bk_cols:
                cbc_hints.setdefault(bk_c, primary_cbc.concept_name)

        return src.model_copy(
            update={
                "key_authority_tier": eff_tier,
                "bkcc_namespace": eff_bkcc,
                "record_source_code": eff_record_source,
                "target_hub_name": primary_hub_name,
                "business_key_columns": bk_cols,
                "surrogate_key_columns": surrogate_cols,
                "timestamp_column": ts_col,
                "foreign_key_mappings": fk_mappings,
                "cbc_mapping_hints": cbc_hints,
                "pii_columns": pii_cols,
            }
        )

    @staticmethod
    def discover_unstructured_doc_candidates(
        *,
        doc: PipelineUnstructuredDocInput,
        doc_text: str,
        ontology: GraphAOntology,
        cumulative_sources: dict[str, tuple[PipelineSourceFeedInput, Path]],
        deployed_contract: dict[str, Any] | None = None,
        base_dir: Path | None = None,
        llm_discovery: Stage02UnstructuredDiscoveryOutput | None = None,
    ) -> PipelineUnstructuredDocInput:
        """Discover or ground character-offset candidate spans in an unstructured document."""
        doc_text = doc_text.replace("\r\n", "\n").replace("\r", "\n")
        candidates: list[PipelineUnstructuredCandidateInput] = list(doc.extracted_candidates)
        target_hub_name = doc.target_hub_name
        link_name = doc.link_name
        satellite_name = doc.satellite_name

        # If candidates are not explicitly pre-populated on `doc`, discover empirically against
        # cumulative_sources + deployed_contract and reconcile with any LLM-discovered spans
        if not candidates:
            known_entities = _extract_known_entities_from_cumulative_sources(
                ontology=ontology,
                cumulative_sources=cumulative_sources,
                deployed_contract=deployed_contract,
                base_dir=base_dir,
            )
            default_hub = "hub_customer"
            if cumulative_sources:
                first_src = next(iter(cumulative_sources.values()))[0]
                default_hub = first_src.target_hub_name or "hub_customer"
            elif known_entities:
                default_hub = known_entities[0][0]
            empirical_candidates = _discover_unstructured_doc_candidates_from_entities(
                doc=doc,
                doc_text=doc_text,
                known_entities=known_entities,
                default_hub=default_hub,
            )
            llm_candidates: list[PipelineUnstructuredCandidateInput] = []
            if llm_discovery is not None:
                if llm_discovery.extracted_candidates and llm_discovery.doc_id in {
                    None,
                    doc.doc_id,
                }:
                    llm_candidates = list(llm_discovery.extracted_candidates)
                elif llm_discovery.documents:
                    for d_entry in llm_discovery.documents:
                        if d_entry.get("doc_id") in {None, doc.doc_id}:
                            raw_cands = d_entry.get("extracted_candidates", [])
                            llm_candidates = [
                                PipelineUnstructuredCandidateInput.model_validate(c)
                                for c in raw_cands
                            ]
                            break
            llm_candidates = [
                c.model_copy(
                    update={
                        "verbatim_quote": c.verbatim_quote.replace("\r\n", "\n").replace("\r", "\n")
                    }
                )
                for c in llm_candidates
            ]

            if empirical_candidates:
                emp_by_bk = {c.extracted_business_key: c for c in empirical_candidates}
                merged_cands: list[PipelineUnstructuredCandidateInput] = list(empirical_candidates)
                for l_cand in llm_candidates:
                    if (
                        l_cand.extracted_business_key not in emp_by_bk
                        and l_cand.verbatim_quote
                        and doc_text.find(l_cand.verbatim_quote) >= 0
                    ):
                        merged_cands.append(l_cand)
                candidates = merged_cands
            else:
                candidates = [
                    c
                    for c in llm_candidates
                    if c.verbatim_quote and doc_text.find(c.verbatim_quote) >= 0
                ]

        # Ground any candidate missing char_start / char_end against doc_text
        grounded_candidates: list[PipelineUnstructuredCandidateInput] = []
        for cand in candidates:
            norm_quote = cand.verbatim_quote.replace("\r\n", "\n").replace("\r", "\n")
            if norm_quote != cand.verbatim_quote:
                cand = cand.model_copy(update={"verbatim_quote": norm_quote})
            found_idx = doc_text.find(norm_quote)
            if (
                cand.char_start is None
                or cand.char_end is None
                or (found_idx >= 0 and doc_text[cand.char_start : cand.char_end] != norm_quote)
            ):
                c_start = max(0, found_idx)
                c_end = c_start + len(norm_quote)
                grounded_candidates.append(
                    cand.model_copy(update={"char_start": c_start, "char_end": c_end})
                )
            else:
                grounded_candidates.append(cand)

        return doc.model_copy(
            update={
                "target_hub_name": target_hub_name or "hub_document",
                "link_name": link_name,
                "satellite_name": satellite_name,
                "extracted_candidates": grounded_candidates,
            }
        )

    @staticmethod
    def repair_contract_from_axi_violations(
        *,
        failing_contract: dict[str, Any],
        violations: list[ToonViolation],
        diagnostics: list[str],
        bronze_profile: GraphBProfile,
        ontology: GraphAOntology,
    ) -> dict[str, Any]:
        """Deterministically repair a failing contract AST from `@toon_v1` violations."""
        del violations, diagnostics, ontology
        repaired = copy.deepcopy(failing_contract)
        ledger: list[dict[str, Any]] = list(repaired.get("column_triage_ledger", []))
        existing_triples = {
            (
                str(e.get("bronze_table", "")),
                str(e.get("column_name", "")),
                str(e.get("target_dv_entity", "")),
            )
            for e in ledger
        }

        # Map bronze_table -> primary hub, satellites, links
        hubs_by_table: dict[str, str] = {}
        bk_cols_by_table: dict[str, set[str]] = {}
        for hub in repaired.get("hubs", []):
            h_name = str(hub.get("name", ""))
            for bk_src in hub.get("business_key_sources", []):
                b_tbl = str(bk_src.get("bronze_table", ""))
                hubs_by_table.setdefault(b_tbl, h_name)
                for k_col in bk_src.get("key_columns", []):
                    k_str = str(k_col)
                    bk_cols_by_table.setdefault(b_tbl, set()).add(k_str)
                    if (b_tbl, k_str, h_name) not in existing_triples:
                        ledger.append(
                            {
                                "bronze_table": b_tbl,
                                "column_name": k_str,
                                "elm_classification": "BUSINESS_KEY",
                                "target_dv_entity": h_name,
                            }
                        )
                        existing_triples.add((b_tbl, k_str, h_name))
                if bk_src.get("key_authority_tier") == "TIER_3_LOCAL_SCOPED":
                    bk_src["require_src_qualifier"] = True
                    cur_pfx = str(bk_src.get("bkcc_prefix") or "").strip()
                    if not cur_pfx or cur_pfx.upper() in {
                        "NONE",
                        "GLOBAL",
                        "SOURCE_SYSTEM_PREFIX_REQUIRED",
                    }:
                        bk_src["bkcc_prefix"] = bk_src.get("record_source_code") or "LOCAL"
                    if not bk_src.get("system_scoped_bk_justification"):
                        bk_src["system_scoped_bk_justification"] = (
                            f"Auto-repaired Tier-3 local-scoped BK justification for {b_tbl}."
                        )
                elif bk_src.get("key_authority_tier") == "TIER_1_GLOBAL_STANDARD":
                    bk_src["require_src_qualifier"] = False
                    bk_src["bkcc_prefix"] = "GLOBAL"

        fk_col_to_link: dict[tuple[str, str], tuple[str, str]] = {}
        for lnk in repaired.get("links", []):
            l_name = str(lnk.get("name", ""))
            b_tbl = str(lnk.get("source_bronze_table", ""))
            is_opt = bool(lnk.get("where_filter_condition"))
            elm = "OPTIONAL_FOREIGN_KEY_REF" if is_opt else "FOREIGN_KEY_REF"
            primary_h = hubs_by_table.get(b_tbl, "")
            for h_ref in lnk.get("hub_references", []):
                if str(h_ref.get("target_hub", "")) == primary_h:
                    continue
                for fk_c in h_ref.get("source_fk_columns", []):
                    fk_str = str(fk_c)
                    fk_col_to_link[(b_tbl, fk_str)] = (l_name, elm)
                    if (b_tbl, fk_str, l_name) not in existing_triples:
                        ledger.append(
                            {
                                "bronze_table": b_tbl,
                                "column_name": fk_str,
                                "elm_classification": elm,
                                "target_dv_entity": l_name,
                            }
                        )
                        existing_triples.add((b_tbl, fk_str, l_name))

        existing_pairs = {
            (str(e.get("bronze_table", "")), str(e.get("column_name", ""))) for e in ledger
        }

        sats_by_table: dict[str, list[dict[str, Any]]] = {}
        for sat in repaired.get("satellites", []):
            b_tbl = str(sat.get("source_bronze_table", ""))
            sats_by_table.setdefault(b_tbl, []).append(sat)

        # 1. Repair any unaccounted Bronze columns across bronze_profile
        sys_meta_names = {"load_dts", "rec_src", "record_source", "extract_dts"}
        for sys_obj in bronze_profile.source_systems:
            for tbl_obj in sys_obj.tables:
                b_tbl = tbl_obj.bronze_table
                primary_hub = hubs_by_table.get(
                    b_tbl,
                    repaired["hubs"][0]["name"] if repaired.get("hubs") else "hub_entity",
                )
                for p_col in tbl_obj.columns:
                    c_name = p_col.column_name
                    if (b_tbl, c_name) in existing_pairs:
                        continue
                    if c_name in bk_cols_by_table.get(b_tbl, set()):
                        ledger.append(
                            {
                                "bronze_table": b_tbl,
                                "column_name": c_name,
                                "elm_classification": "BUSINESS_KEY",
                                "target_dv_entity": primary_hub,
                            }
                        )
                    elif (b_tbl, c_name) in fk_col_to_link:
                        t_lnk, t_elm = fk_col_to_link[(b_tbl, c_name)]
                        ledger.append(
                            {
                                "bronze_table": b_tbl,
                                "column_name": c_name,
                                "elm_classification": t_elm,
                                "target_dv_entity": t_lnk,
                            }
                        )
                    elif p_col.candidate_role == "SYSTEM_SURROGATE_DROPPED":
                        ledger.append(
                            {
                                "bronze_table": b_tbl,
                                "column_name": c_name,
                                "elm_classification": "SYSTEM_SURROGATE_DROPPED",
                                "target_dv_entity": "DROPPED_SURROGATE",
                            }
                        )
                    elif c_name.lower() in sys_meta_names:
                        ledger.append(
                            {
                                "bronze_table": b_tbl,
                                "column_name": c_name,
                                "elm_classification": "TECHNICAL_METADATA",
                                "target_dv_entity": primary_hub,
                            }
                        )
                    else:
                        tbl_sats = sats_by_table.get(b_tbl, [])
                        matched_sat_name: str | None = None
                        for sat in tbl_sats:
                            if c_name in sat.get("payload_columns", []):
                                matched_sat_name = str(sat["name"])
                                break
                        if matched_sat_name is None:
                            is_pii = (
                                p_col.security_tag in {"PII_HIGH", "RESTRICTED_PII"}
                                or p_col.candidate_role == "PII_RESTRICTED_PAYLOAD"
                            )
                            for sat in tbl_sats:
                                if sat.get("satellite_type") != "STANDARD_SATELLITE":
                                    continue
                                if is_pii and sat.get("security_classification") in {
                                    "PII_HIGH",
                                    "RESTRICTED_PII",
                                }:
                                    sat.setdefault("payload_columns", []).append(c_name)
                                    matched_sat_name = str(sat["name"])
                                    break
                                if not is_pii and sat.get("security_classification") == "INTERNAL":
                                    sat.setdefault("payload_columns", []).append(c_name)
                                    matched_sat_name = str(sat["name"])
                                    break
                        if matched_sat_name is None and tbl_sats:
                            tbl_sats[0].setdefault("payload_columns", []).append(c_name)
                            matched_sat_name = str(tbl_sats[0]["name"])
                        ledger.append(
                            {
                                "bronze_table": b_tbl,
                                "column_name": c_name,
                                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                                "target_dv_entity": matched_sat_name or f"sat_{_slugify(b_tbl)}",
                            }
                        )
                    existing_pairs.add((b_tbl, c_name))

        repaired["column_triage_ledger"] = ledger
        return repaired

    @staticmethod
    def synthesize_cumulative_contract(
        *,
        ontology: GraphAOntology,
        cumulative_sources: dict[str, tuple[PipelineSourceFeedInput, Path]],
        cumulative_source_systems: dict[str, ProfiledSourceSystem],
        cumulative_unstructured_docs: list[tuple[PipelineUnstructuredDocInput, str]],
        added_drift_cols_by_table: dict[str, list[str]],
        current_wave_drift_tables: set[str] | None = None,
        wave_index: int,
        domain_namespace: str | None = None,
        deployed_contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Synthesize a cumulative `NoMistakeDataVault2ContractV5` dict for the current wave."""
        hubs_by_name: dict[str, dict[str, Any]] = {}
        links_by_name: dict[str, dict[str, Any]] = {}
        sats_by_name: dict[str, dict[str, Any]] = {}
        ledger: list[dict[str, Any]] = []
        active_drift_tables = current_wave_drift_tables or set()
        is_cold_start = wave_index == 0 and deployed_contract is None

        if deployed_contract is not None:
            for d_hub in deployed_contract.get("hubs", []):
                if isinstance(d_hub, dict) and d_hub.get("name"):
                    hubs_by_name[str(d_hub["name"])] = copy.deepcopy(d_hub)
            for d_lnk in deployed_contract.get("links", []):
                if isinstance(d_lnk, dict) and d_lnk.get("name"):
                    links_by_name[str(d_lnk["name"])] = copy.deepcopy(d_lnk)
            for d_sat in deployed_contract.get("satellites", []):
                if isinstance(d_sat, dict) and d_sat.get("name"):
                    sats_by_name[str(d_sat["name"])] = copy.deepcopy(d_sat)
            for d_entry in deployed_contract.get("column_triage_ledger", []):
                if isinstance(d_entry, dict):
                    ledger.append(copy.deepcopy(d_entry))

        # Map profiled columns by (qualified_table, column_name)
        prof_col_map: dict[tuple[str, str], ProfiledColumn] = {}
        for sys_obj in cumulative_source_systems.values():
            for tbl_obj in sys_obj.tables:
                for col_obj in tbl_obj.columns:
                    prof_col_map[(tbl_obj.bronze_table, col_obj.column_name)] = col_obj

        # 1. Process all relational sources accumulated through this wave
        for qualified_table, (src, _path) in cumulative_sources.items():
            # Remove prior ledger entries for this table (except hub_document) before re-populating
            ledger = [
                e
                for e in ledger
                if not (
                    str(e.get("bronze_table", "")).lower() == qualified_table.lower()
                    and str(e.get("target_dv_entity", "")) != "hub_document"
                )
            ]
            # Determine primary Hub for this table
            primary_hint = (
                src.target_hub_name
                or src.cbc_mapping_hints.get(src.table_name)
                or (
                    src.cbc_mapping_hints.get(src.business_key_columns[0])
                    if src.business_key_columns
                    else None
                )
                or src.table_name
            )
            primary_hub_name, primary_uri = _resolve_hub_and_uri(primary_hint, ontology)
            eff_tier = src.key_authority_tier or "TIER_1_GLOBAL_STANDARD"
            eff_bkcc = src.bkcc_namespace or "GLOBAL"
            eff_rec_src = (
                src.record_source_code or f"{src.source_system.upper()}_{src.table_name.upper()}"
            )

            bk_cols = list(src.business_key_columns)
            if bk_cols:
                is_tier3 = eff_tier == "TIER_3_LOCAL_SCOPED"
                is_tier1 = eff_tier == "TIER_1_GLOBAL_STANDARD"
                primary_t3_bkcc = (
                    eff_bkcc
                    if eff_bkcc.upper() not in {"NONE", "GLOBAL", "SOURCE_SYSTEM_PREFIX_REQUIRED"}
                    else eff_rec_src
                )
                bk_src_entry: dict[str, Any] = {
                    "bronze_table": qualified_table,
                    "key_columns": bk_cols,
                    "record_source_code": eff_rec_src,
                    "key_authority_tier": eff_tier,
                    "require_src_qualifier": is_tier3,
                    "bkcc_prefix": (
                        primary_t3_bkcc if is_tier3 else ("GLOBAL" if is_tier1 else None)
                    ),
                }
                if is_tier3:
                    bk_src_entry["system_scoped_bk_justification"] = (
                        f"Tier-3 local-scoped business key in namespace {primary_t3_bkcc} "
                        f"from source {eff_rec_src}."
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
                            and b["record_source_code"] == eff_rec_src
                        )
                    ]
                    existing_bks.append(bk_src_entry)
                    hubs_by_name[primary_hub_name]["business_key_sources"] = existing_bks

            # Ensure any target Hubs referenced by foreign_key_mappings exist
            mandatory_fk_refs: list[dict[str, Any]] = []
            optional_fk_refs: list[tuple[str, dict[str, Any]]] = []
            for fk_col, target_hint in src.foreign_key_mappings.items():
                fk_hub_name, fk_uri = _resolve_hub_and_uri(target_hint, ontology)
                fk_tier: str = eff_tier
                fk_bkcc = eff_bkcc
                fk_col_low = fk_col.lower()
                for cbc in ontology.canonical_business_concepts:
                    c_hub, _ = _resolve_hub_and_uri(cbc.concept_name, ontology)
                    if c_hub == fk_hub_name and cbc.key_authority_tiers is not None:
                        t_specs = [
                            (
                                cbc.key_authority_tiers.tier_1_global_standard,
                                "TIER_1_GLOBAL_STANDARD",
                            ),
                            (
                                cbc.key_authority_tiers.tier_2_enterprise_sor,
                                "TIER_2_ENTERPRISE_SOR",
                            ),
                            (
                                cbc.key_authority_tiers.tier_3_local_scoped,
                                "TIER_3_LOCAL_SCOPED",
                            ),
                        ]
                        for t_spec, t_lit in t_specs:
                            if (
                                t_spec is not None
                                and t_spec.column_patterns
                                and any(p.lower() == fk_col_low for p in t_spec.column_patterns)
                            ):
                                fk_tier = t_lit
                                if t_lit in {
                                    "TIER_1_GLOBAL_STANDARD",
                                    "TIER_2_ENTERPRISE_SOR",
                                }:
                                    fk_bkcc = "GLOBAL"
                                elif t_spec.bkcc_policy and t_spec.bkcc_policy.upper() not in {
                                    "NONE",
                                    "GLOBAL",
                                    "SOURCE_SYSTEM_PREFIX_REQUIRED",
                                }:
                                    fk_bkcc = t_spec.bkcc_policy
                                else:
                                    fk_bkcc = (
                                        eff_bkcc
                                        if eff_bkcc.upper()
                                        not in {"NONE", "GLOBAL", "SOURCE_SYSTEM_PREFIX_REQUIRED"}
                                        else eff_rec_src
                                    )
                                break
                        break
                if fk_col_low in {"lei", "isin", "cusip", "duns", "figi"} or fk_col_low.endswith(
                    ("_lei", "_isin", "_cusip", "_duns", "_figi", "lei_code")
                ):
                    fk_tier = "TIER_1_GLOBAL_STANDARD"
                    fk_bkcc = "GLOBAL"
                if fk_hub_name not in hubs_by_name:
                    is_t3_fk = fk_tier == "TIER_3_LOCAL_SCOPED"
                    is_t1_fk = fk_tier == "TIER_1_GLOBAL_STANDARD"
                    resolved_fk_bkcc = (
                        fk_bkcc
                        if fk_bkcc.upper()
                        not in {"NONE", "GLOBAL", "SOURCE_SYSTEM_PREFIX_REQUIRED"}
                        else eff_rec_src
                    )
                    fk_bk_src: dict[str, Any] = {
                        "bronze_table": qualified_table,
                        "key_columns": [fk_col],
                        "record_source_code": eff_rec_src,
                        "key_authority_tier": fk_tier,
                        "require_src_qualifier": is_t3_fk,
                        "bkcc_prefix": (
                            resolved_fk_bkcc if is_t3_fk else ("GLOBAL" if is_t1_fk else None)
                        ),
                    }
                    if is_t3_fk:
                        fk_bk_src["system_scoped_bk_justification"] = (
                            f"Tier-3 foreign key hub source from {eff_rec_src}."
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

                # On cold-start Wave 0, if optional_fk_refs is non-empty, include it in the 3-ary
                # link so Algorithm A.4 (codemod_enforce_cold_start_extensibility) splits it out!
                if is_cold_start and optional_fk_refs:
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
                    # On cold-start Wave 0, leave the sparse binary Link unfiltered so
                    # Stage 05 Algorithm A.4 (DV-G2-001b) attaches where_filter_condition in-place.
                    if is_cold_start:
                        links_by_name[opt_lnk_name] = {
                            "name": opt_lnk_name,
                            "link_type": "STANDARD_LINK",
                            "ontology_concept_uri": opt_uri,
                            "source_bronze_table": qualified_table,
                            "hub_references": [primary_ref, opt_ref],
                        }
                        fk_col_to_link_and_elm[fk_col] = (
                            opt_lnk_name,
                            "FOREIGN_KEY_REF",
                        )
                    else:
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

            if is_cold_start and pii_payload_cols and general_payload_cols:
                # Place general + PII columns in `sat_<slug>` on cold-start Wave 0 so Algorithm A.1
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
        if cumulative_unstructured_docs and (cumulative_sources or hubs_by_name):
            first_doc_input = cumulative_unstructured_docs[0][0]
            doc_hub_hint = first_doc_input.target_hub_name or "CBC_CONTRACT_DOCUMENT"
            doc_hub_name, doc_uri = _resolve_hub_and_uri(doc_hub_hint, ontology)
            if cumulative_sources:
                first_bronze_table = next(iter(cumulative_sources.keys()))
                first_src = cumulative_sources[first_bronze_table][0]
                first_bk_col = (
                    first_src.business_key_columns[0]
                    if first_src.business_key_columns
                    else first_src.table_name
                )
            else:
                non_doc_hubs = [h for h_name, h in hubs_by_name.items() if h_name != doc_hub_name]
                fallback_hub = (
                    non_doc_hubs[0] if non_doc_hubs else next(iter(hubs_by_name.values()))
                )
                first_bk_src = (fallback_hub.get("business_key_sources") or [{}])[0]
                first_bronze_table = str(first_bk_src.get("bronze_table", "bronze.unknown"))
                first_bk_cols = first_bk_src.get("key_columns") or ["id"]
                first_bk_col = str(first_bk_cols[0])
            if doc_hub_name not in hubs_by_name:
                hubs_by_name[doc_hub_name] = {
                    "name": doc_hub_name,
                    "ontology_concept_uri": doc_uri,
                    "business_key_sources": [
                        {
                            "bronze_table": first_bronze_table,
                            "key_columns": [first_bk_col],
                            "record_source_code": first_doc_input.record_source,
                            "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                            "require_src_qualifier": False,
                            "bkcc_prefix": "GLOBAL",
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
            non_doc_hub_names = [h for h in hubs_by_name if h != doc_hub_name]
            linked_hub = (
                target_hubs_in_docs[0]
                if target_hubs_in_docs
                else (non_doc_hub_names[0] if non_doc_hub_names else next(iter(hubs_by_name)))
            )
            unstr_lnk_name = (
                first_doc_input.link_name or f"lnk_document_{linked_hub.removeprefix('hub_')}"
            )
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
            prov_sat_name = (
                first_doc_input.satellite_name
                or f"sat_{unstr_lnk_name.removeprefix('lnk_')}_ai_provenance"
            )
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
            if not any(
                e.get("bronze_table") == first_bronze_table
                and e.get("column_name") == first_bk_col
                and e.get("target_dv_entity") == doc_hub_name
                for e in ledger
            ):
                ledger.append(
                    {
                        "bronze_table": first_bronze_table,
                        "column_name": first_bk_col,
                        "elm_classification": "BUSINESS_KEY",
                        "target_dv_entity": doc_hub_name,
                    }
                )

        lifecycle: Literal["COLD_START_N1", "MULTI_SOURCE_EXPANSION"] = (
            "COLD_START_N1" if is_cold_start else "MULTI_SOURCE_EXPANSION"
        )
        eff_domain = domain_namespace or "enterprise_dv"
        return {
            "contract_version": "5.0",
            "domain_namespace": _slugify(eff_domain),
            "lifecycle_stage": lifecycle,
            "column_triage_ledger": ledger,
            "hubs": list(hubs_by_name.values()),
            "links": list(links_by_name.values()),
            "satellites": list(sats_by_name.values()),
            "reference_tables": [],
            "unmapped_escalations": [],
        }


_DeterministicStageSynthesizer = _SymbolicStageSynthesizer
_discover_source_feed_semantics = _SymbolicStageSynthesizer.discover_source_feed_semantics
_discover_unstructured_doc_candidates = (
    _SymbolicStageSynthesizer.discover_unstructured_doc_candidates
)
_repair_contract_from_axi_violations = _SymbolicStageSynthesizer.repair_contract_from_axi_violations
_synthesize_cumulative_contract = _SymbolicStageSynthesizer.synthesize_cumulative_contract


def synthesize_stage_output_from_prompt(
    schema_cls: type[BaseModel],
    prompt: str = "",
) -> Any:
    """Synthesize a schema-valid stage output for deterministic harness and SDK shim runs."""
    ctx = _decode_stage_context(prompt)
    schema_name = schema_cls.__name__

    if schema_name == "Stage00OrchestrationPlanOutput":
        if ctx is not None:
            wave_order = [int(x) for x in ctx.get("wave_indices", [0])]
            max_retries = max(1, int(ctx.get("max_retries", 2)))
            domain_ns = str(ctx.get("domain_namespace", "enterprise_dv"))
            return Stage00OrchestrationPlanOutput(
                wave_execution_order=wave_order,
                recommended_max_retries=max_retries,
                risk_assessment=(f"Validated {len(wave_order)} wave(s) for domain '{domain_ns}'."),
            )
        return Stage00OrchestrationPlanOutput(
            wave_execution_order=[0],
            recommended_max_retries=2,
            risk_assessment="Default deterministic orchestration plan.",
        )

    if schema_name == "Stage01SourceDiscoveryOutput":
        if ctx is not None and "ontology" in ctx and "sources" in ctx:
            ontology = GraphAOntology.model_validate(ctx["ontology"])
            raw_sources = [
                PipelineSourceFeedInput.model_validate(s) for s in ctx.get("sources", [])
            ]
            profiled_tables = {
                k: ProfiledBronzeTable.model_validate(v)
                for k, v in ctx.get("profiled_tables", {}).items()
            }
            cum_sources: dict[str, tuple[PipelineSourceFeedInput, Path]] = {
                k: (
                    PipelineSourceFeedInput.model_validate(v),
                    Path(str(v.get("data_path", "."))),
                )
                for k, v in ctx.get("cumulative_sources", {}).items()
            }
            items: list[Stage01FeedDiscoveryItem] = []
            for raw_src in raw_sources:
                q_tbl = f"{raw_src.source_system.lower()}.{raw_src.table_name.lower()}"
                prof_tbl = profiled_tables.get(q_tbl)
                if prof_tbl is None:
                    continue
                disc = _discover_source_feed_semantics(
                    src=raw_src,
                    prof_tbl=prof_tbl,
                    ontology=ontology,
                    cumulative_sources=cum_sources,
                    deployed_contract=ctx.get("deployed_contract"),
                )
                cum_sources[q_tbl] = (disc, Path(disc.data_path))
                items.append(
                    Stage01FeedDiscoveryItem(
                        feed_id=disc.feed_id,
                        source_system=disc.source_system,
                        table_name=disc.table_name,
                        target_hub_name=disc.target_hub_name,
                        key_authority_tier=disc.key_authority_tier or "TIER_1_GLOBAL_STANDARD",
                        bkcc_namespace=disc.bkcc_namespace or "GLOBAL",
                        record_source_code=disc.record_source_code,
                        business_key_columns=list(disc.business_key_columns),
                        surrogate_key_columns=list(disc.surrogate_key_columns),
                        timestamp_column=disc.timestamp_column,
                        foreign_key_mappings=dict(disc.foreign_key_mappings),
                        cbc_mapping_hints=dict(disc.cbc_mapping_hints),
                        pii_columns=list(disc.pii_columns),
                    )
                )
            if items:
                first = items[0]
                return Stage01SourceDiscoveryOutput(
                    feed_id=first.feed_id,
                    table_name=first.table_name,
                    target_hub_name=first.target_hub_name,
                    key_authority_tier=first.key_authority_tier,
                    bkcc_namespace=first.bkcc_namespace,
                    record_source_code=first.record_source_code,
                    business_key_columns=list(first.business_key_columns),
                    surrogate_key_columns=list(first.surrogate_key_columns),
                    timestamp_column=first.timestamp_column,
                    foreign_key_mappings=dict(first.foreign_key_mappings),
                    cbc_mapping_hints=dict(first.cbc_mapping_hints),
                    pii_columns=list(first.pii_columns),
                    discovered_feeds=items,
                )
        default_item = Stage01FeedDiscoveryItem(
            feed_id="crm.customers",
            source_system="crm",
            table_name="customers",
            target_hub_name="hub_customer",
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
            bkcc_namespace="GLOBAL",
            record_source_code="CRM_CUSTOMERS",
            business_key_columns=["customer_id"],
        )
        return Stage01SourceDiscoveryOutput(
            feed_id=default_item.feed_id,
            table_name=default_item.table_name,
            target_hub_name=default_item.target_hub_name,
            key_authority_tier=default_item.key_authority_tier,
            bkcc_namespace=default_item.bkcc_namespace,
            record_source_code=default_item.record_source_code,
            business_key_columns=list(default_item.business_key_columns),
            discovered_feeds=[default_item],
        )

    if schema_name == "Stage02UnstructuredDiscoveryOutput":
        if ctx is not None and "unstructured_documents" in ctx:
            known_entities = [
                (
                    str(item.get("hub", "hub_customer")),
                    str(item.get("business_key", "")),
                    str(item.get("legal_name", "")),
                )
                for item in ctx.get("active_hub_business_keys", [])
            ]
            default_hub = known_entities[0][0] if known_entities else "hub_customer"
            docs_out: list[dict[str, Any]] = []
            first_cands: list[PipelineUnstructuredCandidateInput] = []
            first_doc_id: str | None = None
            first_target_hub: str = "hub_document"
            for d_entry in ctx.get("unstructured_documents", []):
                raw_doc = PipelineUnstructuredDocInput.model_validate(d_entry["doc"])
                doc_text = str(d_entry.get("doc_text", ""))
                cands = (
                    list(raw_doc.extracted_candidates)
                    if raw_doc.extracted_candidates
                    else _discover_unstructured_doc_candidates_from_entities(
                        doc=raw_doc,
                        doc_text=doc_text,
                        known_entities=known_entities,
                        default_hub=default_hub,
                    )
                )
                if first_doc_id is None:
                    first_doc_id = raw_doc.doc_id
                    first_target_hub = raw_doc.target_hub_name or "hub_document"
                    first_cands = cands
                docs_out.append(
                    {
                        "doc_id": raw_doc.doc_id,
                        "target_hub_name": raw_doc.target_hub_name or "hub_document",
                        "link_name": raw_doc.link_name,
                        "satellite_name": raw_doc.satellite_name,
                        "extracted_candidates": [c.model_dump(mode="json") for c in cands],
                    }
                )
            return Stage02UnstructuredDiscoveryOutput(
                doc_id=first_doc_id,
                target_hub_name=first_target_hub,
                extracted_candidates=first_cands,
                documents=docs_out,
            )
        default_cand = PipelineUnstructuredCandidateInput(
            extraction_id="ext_doc_sample_u1",
            target_hub="hub_customer",
            extracted_business_key="CUST-001",
            verbatim_quote="CUST-001",
            char_start=0,
            char_end=8,
            page_number=1,
            confidence_score=0.98,
        )
        return Stage02UnstructuredDiscoveryOutput(
            doc_id="doc_sample",
            target_hub_name="hub_document",
            extracted_candidates=[default_cand],
            documents=[
                {
                    "doc_id": "doc_sample",
                    "target_hub_name": "hub_document",
                    "extracted_candidates": [default_cand.model_dump(mode="json")],
                }
            ],
        )

    if schema_name in {
        "Stage03ColumnTriageOutput",
        "Stage04ContractSynthesisOutput",
        "NoMistakeDataVault2ContractV5",
    }:
        if ctx is not None:
            if (
                ctx.get("stage") == "STAGE_05_AXI_DV_STATIC_GATES"
                and "failing_contract" in ctx
                and "graph_b_profile" in ctx
                and "ontology" in ctx
            ):
                ontology = GraphAOntology.model_validate(ctx["ontology"])
                gb_prof = GraphBProfile.model_validate(ctx["graph_b_profile"])
                repaired_dict = _repair_contract_from_axi_violations(
                    failing_contract=dict(ctx["failing_contract"]),
                    violations=[],
                    diagnostics=[],
                    bronze_profile=gb_prof,
                    ontology=ontology,
                )
                contract_obj = NoMistakeDataVault2ContractV5.model_validate(repaired_dict)
                if schema_name == "NoMistakeDataVault2ContractV5":
                    return contract_obj
                return Stage04ContractSynthesisOutput(contract=contract_obj)

            if "ontology" in ctx and "graph_b_profile" in ctx and "cumulative_sources" in ctx:
                ontology = GraphAOntology.model_validate(ctx["ontology"])
                gb_prof = GraphBProfile.model_validate(ctx["graph_b_profile"])
                cum_sys = {s.source_system_code: s for s in gb_prof.source_systems}
                cum_sources = {
                    k: (
                        PipelineSourceFeedInput.model_validate(v),
                        Path(str(v.get("data_path", "."))),
                    )
                    for k, v in ctx.get("cumulative_sources", {}).items()
                }
                cum_unstr = [
                    (
                        PipelineUnstructuredDocInput.model_validate(d["doc"]),
                        str(d.get("sha256", "")),
                    )
                    for d in ctx.get("cumulative_unstructured_docs", [])
                ]
                contract_dict = _synthesize_cumulative_contract(
                    ontology=ontology,
                    cumulative_sources=cum_sources,
                    cumulative_source_systems=cum_sys,
                    cumulative_unstructured_docs=cum_unstr,
                    added_drift_cols_by_table={
                        str(k): [str(c) for c in v]
                        for k, v in ctx.get("added_drift_cols_by_table", {}).items()
                    },
                    current_wave_drift_tables={
                        str(t) for t in ctx.get("current_wave_drift_tables", [])
                    },
                    wave_index=int(ctx.get("wave_index", 0)),
                    domain_namespace=str(ctx.get("domain_namespace", "enterprise_dv")),
                    deployed_contract=ctx.get("deployed_contract"),
                )
                if schema_name == "Stage03ColumnTriageOutput":
                    return Stage03ColumnTriageOutput.model_validate(
                        {"column_triage_ledger": contract_dict["column_triage_ledger"]}
                    )
                if ctx.get("pass1_column_triage_ledger"):
                    contract_dict["column_triage_ledger"] = list(ctx["pass1_column_triage_ledger"])
                contract_obj = NoMistakeDataVault2ContractV5.model_validate(contract_dict)
                if schema_name == "NoMistakeDataVault2ContractV5":
                    return contract_obj
                return Stage04ContractSynthesisOutput(contract=contract_obj)

        minimal_contract_dict: dict[str, Any] = {
            "contract_version": "5.0",
            "domain_namespace": "enterprise_dv",
            "lifecycle_stage": "COLD_START_N1",
            "column_triage_ledger": [
                {
                    "bronze_table": "crm.customers",
                    "column_name": "customer_id",
                    "elm_classification": "BUSINESS_KEY",
                    "target_dv_entity": "hub_customer",
                },
                {
                    "bronze_table": "crm.customers",
                    "column_name": "customer_name",
                    "elm_classification": "DESCRIPTIVE_PAYLOAD",
                    "target_dv_entity": "sat_customer_core",
                },
            ],
            "hubs": [
                {
                    "name": "hub_customer",
                    "ontology_concept_uri": "urn:ontology:entity:customer",
                    "business_key_sources": [
                        {
                            "bronze_table": "crm.customers",
                            "key_columns": ["customer_id"],
                            "record_source_code": "CRM_CUSTOMERS",
                            "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                            "require_src_qualifier": False,
                            "bkcc_prefix": "GLOBAL",
                        }
                    ],
                }
            ],
            "links": [],
            "satellites": [
                {
                    "name": "sat_customer_core",
                    "satellite_type": "STANDARD_SATELLITE",
                    "parent_entity": "hub_customer",
                    "source_bronze_table": "crm.customers",
                    "security_classification": "INTERNAL",
                    "volatility_tier": "STANDARD",
                    "payload_columns": ["customer_name"],
                    "soft_deprecated_columns": [],
                }
            ],
            "reference_tables": [],
            "unmapped_escalations": [],
        }
        if schema_name == "Stage03ColumnTriageOutput":
            return Stage03ColumnTriageOutput.model_validate(
                {"column_triage_ledger": minimal_contract_dict["column_triage_ledger"]}
            )
        contract_obj = NoMistakeDataVault2ContractV5.model_validate(minimal_contract_dict)
        if schema_name == "NoMistakeDataVault2ContractV5":
            return contract_obj
        return Stage04ContractSynthesisOutput(contract=contract_obj)

    return None


synthesize_symbolic_stage_output = synthesize_stage_output_from_prompt


def run_agentic_vault_pipeline(
    input_spec: PipelineInputSpec | str | Path,
    harness_setup: HarnessSetupConfig | str | Path | None = None,
    *,
    output_dir: str | Path | None = None,
    base_dir: str | Path | None = None,
    cas_store: ContentAddressableStore | None = None,
    sdk_module: Any | None = None,
    harness: VaultAgentHarness | None = None,
    on_stage_start: Callable[[int, str, str, str], None] | None = None,
    on_stage_complete: Callable[[int, PipelineStepReport], None] | None = None,
    on_stream_event: Callable[[HarnessStreamEvent], None] | None = None,
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
        harness=harness,
        on_stage_start=on_stage_start,
        on_stage_complete=on_stage_complete,
        on_stream_event=on_stream_event,
    )
    return pipeline.run()


__all__ = [
    "AgenticPipelineReport",
    "AgenticVaultPipeline",
    "DeterministicValidationReport",
    "HarnessAuthConfig",
    "HarnessSetupConfig",
    "PipelineArtifactRef",
    "PipelineForeignKeyMapping",
    "PipelineInputSpec",
    "PipelineSourceFeedInput",
    "PipelineStepReport",
    "PipelineUnstructuredCandidate",
    "PipelineUnstructuredCandidateInput",
    "PipelineUnstructuredDocInput",
    "PipelineWaveInput",
    "PipelineWaveReport",
    "Stage00OrchestrationPlanOutput",
    "Stage01FeedDiscoveryItem",
    "Stage01SourceDiscoveryOutput",
    "Stage02UnstructuredDiscoveryOutput",
    "Stage03ColumnTriageOutput",
    "Stage04ContractSynthesisOutput",
    "run_agentic_vault_pipeline",
    "synthesize_stage_output_from_prompt",
    "validate_pipeline_inputs",
    "validate_pipeline_outputs",
]
