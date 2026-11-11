"""Platform & Agent Harness Configuration Contract DTOs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from opendv_contracts.dtos.common import BaseContractModel


class ProviderReadinessDTO(BaseContractModel):
    """Readiness and credential status for a registered agent harness provider."""

    provider: str = Field(..., description="Registered provider identifier (e.g., agy, claude-cli)")
    execution_mode: Literal["cli", "sdk"] = Field(
        ..., description="Whether this provider executes via CLI subprocess or Python SDK"
    )
    available: bool = Field(
        ..., description="True when the binary or SDK is installed and ready for execution"
    )
    binary_name: str | None = Field(
        default=None, description="CLI executable name if execution_mode is 'cli'"
    )
    binary_found: bool = Field(
        default=False, description="True when the CLI binary is present on PATH"
    )
    sdk_installed: bool = Field(
        default=False, description="True when the backing Python SDK module is importable"
    )
    auth_mode: str = Field(default="none", description="Configured or default authentication mode")
    api_key_env_var: str = Field(
        default="", description="Environment variable name checked for API credentials"
    )
    credentials_present: bool = Field(
        default=False,
        description="True when credentials or CLI session are detected (never exposes raw secrets)",
    )
    default_reasoning_model: str = Field(
        ..., description="Default HIGH_REASONING tier model identifier for this provider"
    )
    default_fast_model: str = Field(
        ..., description="Default FAST_TIER model identifier for this provider"
    )
    status_message: str = Field(..., description="Human-readable readiness summary")


class HarnessAuthConfigDTO(BaseContractModel):
    """Declarative authentication and cloud provider routing configuration."""

    auth_mode: str = Field(
        default="none",
        description="Authentication mode (none, cli_session, api_key, vertex_adc, aws_bedrock)",
    )
    mode: str = Field(default="none", description="Alias for auth_mode")
    api_key_env_var: str | None = Field(
        default=None,
        description="Environment variable name holding the API key (never the raw secret)",
    )
    api_base_url: str | None = Field(default=None, description="Custom API base URL override")
    gcp_project: str | None = Field(default=None, description="Google Cloud / Vertex AI project ID")
    gcp_location: str | None = Field(default=None, description="Google Cloud / Vertex AI region")
    gcp_credentials_path: str | None = Field(
        default=None, description="Optional path to GCP ADC credentials JSON"
    )
    aws_region: str | None = Field(default=None, description="AWS Bedrock region")
    aws_profile: str | None = Field(default=None, description="AWS CLI profile name")
    require_credentials_present: bool = Field(
        default=False, description="Require pre-flight credential verification"
    )
    require_preflight: bool = Field(
        default=False, description="Alias for require_credentials_present"
    )

    @model_validator(mode="after")
    def _sync_auth_aliases(self) -> HarnessAuthConfigDTO:
        if self.auth_mode != "none" and self.mode == "none":
            object.__setattr__(self, "mode", self.auth_mode)
        elif self.mode != "none" and self.auth_mode == "none":
            object.__setattr__(self, "auth_mode", self.mode)
        if self.require_credentials_present and not self.require_preflight:
            object.__setattr__(self, "require_preflight", True)
        elif self.require_preflight and not self.require_credentials_present:
            object.__setattr__(self, "require_credentials_present", True)
        return self


class HarnessBudgetConfigDTO(BaseContractModel):
    """Token, turn, timeout, and cost ceiling configuration for agent tasks."""

    max_turns: int = Field(default=30, ge=1, le=200, description="Maximum agent loop turns")
    max_input_tokens: int = Field(
        default=200_000, ge=1_000, le=2_000_000, description="Maximum input context tokens"
    )
    max_output_tokens: int = Field(
        default=32_000, ge=512, le=200_000, description="Maximum generated output tokens"
    )
    timeout_seconds: int | float = Field(
        default=300.0, gt=0.0, le=3600.0, description="Task execution timeout in seconds"
    )
    max_cost_usd: float = Field(
        default=5.0, gt=0.0, le=500.0, description="Maximum cost budget per task in USD"
    )


class HarnessSandboxPolicyDTO(BaseContractModel):
    """Filesystem, shell, and network isolation policy for agent personas."""

    read_only_workspace: bool = Field(
        default=False, description="Enforce read-only workspace access"
    )
    allow_shell_execution: bool = Field(
        default=False, description="Permit arbitrary shell command execution"
    )
    allow_network_access: bool = Field(
        default=False, description="Permit outbound network access from tools"
    )
    allowed_builtin_tools: list[str] = Field(
        default_factory=list, description="Explicitly allowed host built-in tools"
    )
    denied_builtin_tools: list[str] = Field(
        default_factory=lambda: ["run_command"],
        description="Explicitly denied host built-in tools",
    )


class HarnessConfigDTO(BaseContractModel):
    """Complete Agent Harness & Governance setup configuration."""

    provider: Literal[
        "agy",
        "agy-cli",
        "antigravity",
        "claude",
        "claude-cli",
        "claude-code",
    ] = Field(default="agy", description="Default agent harness provider")
    model_overrides: dict[str, str] = Field(
        default_factory=lambda: {
            "reasoning": "gemini-4-argon-high",
            "fast": "gemini-3.8-flash-high",
        },
        description="Semantic model tier overrides keyed by 'reasoning' and 'fast'",
    )
    agent_models: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Optional per-persona model overrides "
            "(vault-first-mate, scout-source, scout-unstructured, ship-vault)"
        ),
    )
    auth: HarnessAuthConfigDTO = Field(
        default_factory=HarnessAuthConfigDTO,
        description="Authentication and cloud routing settings",
    )
    budget: HarnessBudgetConfigDTO = Field(
        default_factory=HarnessBudgetConfigDTO,
        description="Turn, token, timeout, and cost budget limits",
    )
    sandbox: HarnessSandboxPolicyDTO = Field(
        default_factory=HarnessSandboxPolicyDTO,
        description="Sandbox isolation policy",
    )
    steward_hold_policy: Literal["pause", "auto_approve_top_candidate", "reject_on_hold"] = Field(
        default="auto_approve_top_candidate",
        description="Default Steward Hold policy for Tier-U3 / ontology escalations",
    )
    ontology_policy: Literal["off", "recommend", "auto_accept"] = Field(
        default="off",
        description="Default ontology evolution policy (off, recommend, auto_accept)",
    )
    stage_output_policy: Literal["llm_only", "symbolic_completion"] = Field(
        default="llm_only",
        description="Stage output synthesis policy",
    )
    allow_cli_schema_omission_retry: bool = Field(
        default=False,
        description=(
            "Permit CLI harness fallback retry without --json-schema "
            "when provider CLI rejects schema flag"
        ),
    )


class PlatformConfigResponse(BaseContractModel):
    """Full Platform & Agent Harness configuration and readiness response."""

    workspace_root: str = Field(..., description="Resolved platform workspace root path")
    state_dir: str = Field(..., description="Resolved platform state directory path")
    cas_dir: str = Field(..., description="Content-Addressable Store directory path")
    ledgers_dir: str = Field(..., description="JSONL observability ledgers directory path")
    server_host: str = Field(default="", description="Platform API bind host")
    server_port: int = Field(default=8420, description="Platform API bind port")
    max_inline_bytes: int = Field(
        default=10 * 1024 * 1024, description="Maximum allowed inline upload payload size in bytes"
    )
    cors_origins: list[str] = Field(
        default_factory=list, description="Configured allowed CORS origins"
    )
    config_file_path: str | None = Field(
        default=None, description="Active opendv.config.yaml path if loaded"
    )
    persisted_config_path: str = Field(
        ..., description="Path where dynamic platform/harness config updates are persisted"
    )
    web_ui_bundled: bool = Field(
        default=False,
        description="True when the compiled static Web Explorer UI bundle is present",
    )
    harness: HarnessConfigDTO = Field(
        ..., description="Active platform-default Agent Harness & Governance configuration"
    )
    resolved_persona_models: dict[str, str] = Field(
        default_factory=dict,
        description="Effective resolved model IDs for each canonical agent persona",
    )
    providers: list[ProviderReadinessDTO] = Field(
        default_factory=list,
        description="Readiness and credential diagnostics for all registered providers",
    )

    def redact_for_role(self, role: str = "INTERNAL_MODELER") -> PlatformConfigResponse:
        """Return a copy with sensitive credential paths or host paths redacted for RBAC role."""
        auth = self.harness.auth
        if auth.gcp_credentials_path:
            auth = auth.model_copy(update={"gcp_credentials_path": "[REDACTED]"})
        redacted_harness = self.harness.model_copy(update={"auth": auth})
        if str(role).upper() == "PUBLIC":
            return self.model_copy(
                update={
                    "workspace_root": "[REDACTED]",
                    "state_dir": ".opendv/state",
                    "cas_dir": ".opendv/state/cas",
                    "ledgers_dir": ".opendv/state/ledgers",
                    "config_file_path": ("opendv.config.yaml" if self.config_file_path else None),
                    "persisted_config_path": ".opendv/state/platform_config.json",
                    "harness": redacted_harness,
                }
            )
        return self.model_copy(update={"harness": redacted_harness})


class ConfigValidationDiagnosticDTO(BaseContractModel):
    """Structured diagnostic item emitted during platform/harness configuration validation."""

    code: str | None = Field(default=None, description="Diagnostic code")
    severity: Literal["ERROR", "WARNING", "INFO"] = Field(
        default="ERROR", description="Diagnostic severity"
    )
    target: str | None = Field(default=None, description="Target configuration field path")
    message: str = Field(..., description="Human-readable diagnostic message")


class PlatformConfigUpdateRequest(BaseContractModel):
    """Request DTO to update Platform & Agent Harness configuration."""

    harness: dict[str, Any] | None = Field(
        default=None,
        description="Full or partial HarnessSetupConfig dictionary to merge and validate",
    )
    max_inline_bytes: int | None = Field(
        default=None,
        ge=1024,
        le=100 * 1024 * 1024,
        description="Optional override for max inline upload bytes",
    )
    cors_origins: list[str] | None = Field(
        default=None, description="Optional updated list of allowed CORS origins"
    )
    persist: bool = Field(
        default=True,
        description="Whether to persist updated configuration to state_dir/platform_config.json",
    )


class PlatformConfigValidateRequest(BaseContractModel):
    """Request DTO to deterministically validate candidate Harness & Platform settings."""

    harness: dict[str, Any] = Field(
        default_factory=dict,
        description="Candidate HarnessSetupConfig dictionary to validate",
    )


class PlatformConfigValidationResponse(BaseContractModel):
    """Deterministic validation result for candidate Platform & Harness settings."""

    passed: bool = Field(
        ..., description="True when candidate configuration is schema- and policy-valid"
    )
    diagnostics: list[ConfigValidationDiagnosticDTO] = Field(
        default_factory=list,
        description="List of validation diagnostics (code, severity, target, message)",
    )
    harness: HarnessConfigDTO | None = Field(
        default=None, description="Normalized HarnessConfigDTO when validation succeeds"
    )
    resolved_persona_models: dict[str, str] = Field(
        default_factory=dict,
        description="Resolved model IDs for each agent persona under candidate settings",
    )
    providers: list[ProviderReadinessDTO] = Field(
        default_factory=list,
        description="Readiness status for registered harness providers",
    )


__all__ = [
    "ConfigValidationDiagnosticDTO",
    "HarnessAuthConfigDTO",
    "HarnessBudgetConfigDTO",
    "HarnessConfigDTO",
    "HarnessSandboxPolicyDTO",
    "PlatformConfigResponse",
    "PlatformConfigUpdateRequest",
    "PlatformConfigValidateRequest",
    "PlatformConfigValidationResponse",
    "ProviderReadinessDTO",
]
