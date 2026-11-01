"""Model-tier routing and persona capability matrix for the Open-DV Agent Harness.

Conforms to docs/03-verification-and-agentic-architecture.md
and docs/06-explainability-and-observability-spec.md.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from opendv_agent_harness.models import (
    AgentPersona,
    ModelTier,
    PipelineStageId,
    SandboxPolicy,
    WorkbenchViewId,
)

DEFAULT_AGY_MODEL_MAP: dict[ModelTier, str] = {
    ModelTier.HIGH_REASONING: os.environ.get("OPENDV_AGY_HIGH_MODEL", "gemini-4-argon-high"),
    ModelTier.FAST_TIER: os.environ.get("OPENDV_AGY_FAST_MODEL", "gemini-3.8-flash-high"),
}

DEFAULT_CLAUDE_MODEL_MAP: dict[ModelTier, str] = {
    ModelTier.HIGH_REASONING: os.environ.get("OPENDV_CLAUDE_HIGH_MODEL", "claude-opus-5-5-high"),
    ModelTier.FAST_TIER: os.environ.get("OPENDV_CLAUDE_FAST_MODEL", "claude-sonnet-5-5-medium"),
}

DEFAULT_DETERMINISTIC_MODEL_MAP: dict[ModelTier, str] = {
    ModelTier.HIGH_REASONING: os.environ.get(
        "OPENDV_DETERMINISTIC_HIGH_MODEL", "deterministic-argon-v1"
    ),
    ModelTier.FAST_TIER: os.environ.get(
        "OPENDV_DETERMINISTIC_FAST_MODEL", "deterministic-flash-v1"
    ),
}


class PersonaCapabilityProfile(BaseModel):
    """RBAC capability and model-tier profile for an AgentPersona."""

    model_config = ConfigDict(frozen=True)

    persona: AgentPersona
    default_model_tier: ModelTier
    sandbox_policy: SandboxPolicy
    authorized_tools: frozenset[str]
    allowed_subagents: tuple[str, ...] = ()
    primary_stages: tuple[PipelineStageId, ...] = ()
    primary_views: tuple[WorkbenchViewId, ...] = ()

    @property
    def model_tier(self) -> ModelTier:
        """Alias for `default_model_tier`."""
        return self.default_model_tier

    @property
    def sandbox(self) -> SandboxPolicy:
        """Alias for `sandbox_policy`."""
        return self.sandbox_policy

    def __getitem__(self, key: str) -> Any:
        if key in {"model_tier", "default_model_tier"}:
            return self.default_model_tier
        if key in {"sandbox", "sandbox_policy"}:
            return self.sandbox_policy
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        """Dictionary-compatible getter for capability profile fields."""
        try:
            return self[key]
        except KeyError:
            return default


PERSONA_CAPABILITY_MATRIX: dict[AgentPersona, PersonaCapabilityProfile] = {
    AgentPersona.VAULT_FIRST_MATE: PersonaCapabilityProfile(
        persona=AgentPersona.VAULT_FIRST_MATE,
        default_model_tier=ModelTier.HIGH_REASONING,
        sandbox_policy=SandboxPolicy(
            read_only_workspace=True,
            allow_shell_execution=False,
        ),
        authorized_tools=frozenset(
            {
                "profile_source_feed",
                "run_axi_dv_gates",
                "execute_duckdb_sandbox",
                "compile_canonical_ir",
                "emit_observability_event",
                "request_steward_decision",
            }
        ),
        allowed_subagents=(
            AgentPersona.SCOUT_SOURCE.value,
            AgentPersona.SCOUT_UNSTRUCTURED.value,
            AgentPersona.SHIP_VAULT.value,
        ),
        primary_stages=(PipelineStageId.STAGE_00_ORCHESTRATION,),
        primary_views=(WorkbenchViewId.WB_00, WorkbenchViewId.WB_01),
    ),
    AgentPersona.SCOUT_SOURCE: PersonaCapabilityProfile(
        persona=AgentPersona.SCOUT_SOURCE,
        default_model_tier=ModelTier.FAST_TIER,
        sandbox_policy=SandboxPolicy(
            read_only_workspace=True,
            allow_shell_execution=False,
        ),
        authorized_tools=frozenset(
            {
                "profile_source_feed",
                "emit_observability_event",
            }
        ),
        allowed_subagents=(),
        primary_stages=(PipelineStageId.STAGE_01_SCOUT_SOURCE,),
        primary_views=(WorkbenchViewId.WB_02, WorkbenchViewId.WB_09),
    ),
    AgentPersona.SCOUT_UNSTRUCTURED: PersonaCapabilityProfile(
        persona=AgentPersona.SCOUT_UNSTRUCTURED,
        default_model_tier=ModelTier.FAST_TIER,
        sandbox_policy=SandboxPolicy(
            read_only_workspace=True,
            allow_shell_execution=False,
        ),
        authorized_tools=frozenset(
            {
                "extract_pdf_spans",
                "emit_observability_event",
                "request_steward_decision",
            }
        ),
        allowed_subagents=(),
        primary_stages=(PipelineStageId.STAGE_02_SCOUT_UNSTRUCTURED,),
        primary_views=(WorkbenchViewId.WB_03,),
    ),
    AgentPersona.SHIP_VAULT: PersonaCapabilityProfile(
        persona=AgentPersona.SHIP_VAULT,
        default_model_tier=ModelTier.HIGH_REASONING,
        sandbox_policy=SandboxPolicy(
            read_only_workspace=False,
            allow_shell_execution=False,
            allowed_builtin_tools=(
                "view_file",
                "list_directory",
                "find_file",
                "search_directory",
                "create_file",
                "edit_file",
            ),
            denied_builtin_tools=("run_command",),
        ),
        authorized_tools=frozenset(
            {
                "apply_ast_codemod",
                "run_axi_dv_gates",
                "execute_duckdb_sandbox",
                "compile_canonical_ir",
                "emit_observability_event",
                "request_steward_decision",
            }
        ),
        allowed_subagents=(),
        primary_stages=(
            PipelineStageId.STAGE_03_SHIP_VAULT_PASS1,
            PipelineStageId.STAGE_04_SHIP_VAULT_PASS2,
            PipelineStageId.STAGE_05_AXI_DV_STATIC_GATES,
            PipelineStageId.STAGE_06_AXI_DV_DYNAMIC_SANDBOX,
            PipelineStageId.STAGE_07_DETERMINISTIC_COMPILER,
        ),
        primary_views=(
            WorkbenchViewId.WB_04,
            WorkbenchViewId.WB_05,
            WorkbenchViewId.WB_06,
            WorkbenchViewId.WB_07,
            WorkbenchViewId.WB_08,
            WorkbenchViewId.WB_10,
            WorkbenchViewId.WB_11,
        ),
    ),
}


def get_persona_capability(persona: AgentPersona | str) -> PersonaCapabilityProfile:
    """Return the PersonaCapabilityProfile for the requested AgentPersona."""
    persona_enum = AgentPersona(persona)
    if persona_enum not in PERSONA_CAPABILITY_MATRIX:
        raise KeyError(f"No capability profile registered for persona '{persona_enum}'.")
    return PERSONA_CAPABILITY_MATRIX[persona_enum]


def resolve_default_tier_for_persona(persona: AgentPersona | str) -> ModelTier:
    """Return the default ModelTier ('argon' or 'flash') for a persona."""
    persona_enum = AgentPersona(persona)
    if persona_enum in PERSONA_CAPABILITY_MATRIX:
        return PERSONA_CAPABILITY_MATRIX[persona_enum].default_model_tier
    return ModelTier.HIGH_REASONING


def resolve_provider_model(
    provider: Literal["antigravity", "claude", "deterministic", "deterministic-stub"] | str,
    tier: ModelTier | str,
    overrides: dict[ModelTier, str] | None = None,
) -> str:
    """Resolve a logical ModelTier to a provider-specific model identifier."""
    tier_enum = ModelTier(tier)
    if overrides and tier_enum in overrides:
        return str(overrides[tier_enum])
    if provider == "claude":
        return DEFAULT_CLAUDE_MODEL_MAP.get(
            tier_enum,
            DEFAULT_CLAUDE_MODEL_MAP[ModelTier.HIGH_REASONING],
        )
    if provider in {"deterministic", "deterministic-stub"}:
        return DEFAULT_DETERMINISTIC_MODEL_MAP.get(
            tier_enum,
            DEFAULT_DETERMINISTIC_MODEL_MAP[ModelTier.HIGH_REASONING],
        )
    return DEFAULT_AGY_MODEL_MAP.get(
        tier_enum,
        DEFAULT_AGY_MODEL_MAP[ModelTier.HIGH_REASONING],
    )


def is_tool_authorized_for_persona(persona: AgentPersona | str, tool_name: str) -> bool:
    """Return True if `tool_name` is authorized for `persona` in PERSONA_CAPABILITY_MATRIX."""
    persona_enum = AgentPersona(persona)
    profile = PERSONA_CAPABILITY_MATRIX.get(persona_enum)
    if profile is None:
        return False
    return tool_name in profile.authorized_tools


__all__ = [
    "DEFAULT_AGY_MODEL_MAP",
    "DEFAULT_CLAUDE_MODEL_MAP",
    "DEFAULT_DETERMINISTIC_MODEL_MAP",
    "PERSONA_CAPABILITY_MATRIX",
    "PersonaCapabilityProfile",
    "get_persona_capability",
    "is_tool_authorized_for_persona",
    "resolve_default_tier_for_persona",
    "resolve_provider_model",
]
