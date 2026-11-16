from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from opendv_agent_harness.models import (
    AgentPersona,
    HarnessBudget,
    HarnessConfig,
    ModelTier,
    SandboxPolicy,
)
from opendv_core.config import CoreModelingConfig, get_core_modeling_config
from opendv_sources import load_structured_document

# 1. Pipeline Setup & Input Models
# ---------------------------------------------------------------------------


def _load_yaml_or_json(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    loaded = load_structured_document(file_path, workspace_root=Path.cwd(), allow_temp=True)
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
        "pause"
    )
    ontology_policy: str = "off"
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


class PipelineSettings(BaseModel):
    """Declarative runtime settings and thresholds for data-driven modeling."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    max_sandbox_rows: int = Field(default=10_000)
    levenshtein_rename_threshold: float = Field(default=0.85)
    jaccard_similarity_threshold: float = Field(default=0.90)
    business_key_uniqueness_threshold: float = Field(default=1.0)
    sparse_fk_null_threshold: float = Field(default=0.95)
    companion_satellite_volatility_tier: str = Field(default="ADDITIVE_DRIFT_COMPANION")
    system_table_prefixes: tuple[str, ...] = Field(
        default=("sqlite_stat", "sqlite_sequence", "information_schema")
    )

    @classmethod
    def from_core_config(cls, core: CoreModelingConfig | None = None) -> PipelineSettings:
        cfg = core or get_core_modeling_config()
        return cls(
            max_sandbox_rows=cfg.max_sandbox_rows,
            levenshtein_rename_threshold=cfg.levenshtein_rename_threshold,
            jaccard_similarity_threshold=cfg.jaccard_similarity_threshold,
            business_key_uniqueness_threshold=cfg.business_key_uniqueness_threshold,
            sparse_fk_null_threshold=cfg.sparse_fk_null_threshold,
            companion_satellite_volatility_tier=cfg.companion_satellite_volatility_tier,
            system_table_prefixes=cfg.system_table_prefixes,
        )


__all__ = [
    "HarnessAuthConfig",
    "HarnessSetupConfig",
    "PipelineSettings",
]
