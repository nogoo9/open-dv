"""Model-tier routing and persona capability matrix for the Open-DV Agent Harness.

Conforms to docs/03-verification-and-agentic-architecture.md
and docs/06-explainability-and-observability-spec.md.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from opendv_agent_harness.models import (
    AgentPersona,
    ModelTier,
    PipelineStageId,
    SandboxPolicy,
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
    ),
    AgentPersona.TERM_SCOUT: PersonaCapabilityProfile(
        persona=AgentPersona.TERM_SCOUT,
        default_model_tier=ModelTier.FAST_TIER,
        sandbox_policy=SandboxPolicy(
            read_only_workspace=True,
            allow_shell_execution=False,
        ),
        authorized_tools=frozenset(
            {
                "extract_candidate_terms",
                "emit_observability_event",
            }
        ),
        allowed_subagents=(),
        primary_stages=(),
    ),
    AgentPersona.DOMAIN_CLARIFIER: PersonaCapabilityProfile(
        persona=AgentPersona.DOMAIN_CLARIFIER,
        default_model_tier=ModelTier.HIGH_REASONING,
        sandbox_policy=SandboxPolicy(
            read_only_workspace=True,
            allow_shell_execution=False,
        ),
        authorized_tools=frozenset(
            {
                "formulate_clarification_questions",
                "evaluate_polysemy",
                "emit_observability_event",
            }
        ),
        allowed_subagents=(),
        primary_stages=(),
    ),
    AgentPersona.ONTOLOGY_ARCHITECT: PersonaCapabilityProfile(
        persona=AgentPersona.ONTOLOGY_ARCHITECT,
        default_model_tier=ModelTier.HIGH_REASONING,
        sandbox_policy=SandboxPolicy(
            read_only_workspace=True,
            allow_shell_execution=False,
        ),
        authorized_tools=frozenset(
            {
                "propose_ontology_delta",
                "validate_ontology_delta",
                "emit_observability_event",
            }
        ),
        allowed_subagents=(
            AgentPersona.TERM_SCOUT.value,
            AgentPersona.DOMAIN_CLARIFIER.value,
        ),
        primary_stages=(),
    ),
}


def get_persona_capability(persona: AgentPersona | str) -> PersonaCapabilityProfile:
    """Return the PersonaCapabilityProfile for the requested AgentPersona."""
    persona_enum = AgentPersona(persona)
    if persona_enum not in PERSONA_CAPABILITY_MATRIX:
        raise KeyError(f"No capability profile registered for persona '{persona_enum}'.")
    return PERSONA_CAPABILITY_MATRIX[persona_enum]


def resolve_default_tier_for_persona(persona: AgentPersona | str) -> ModelTier:
    """Return the default ModelTier ('reasoning' or 'fast') for a persona."""
    persona_enum = AgentPersona(persona)
    if persona_enum in PERSONA_CAPABILITY_MATRIX:
        return PERSONA_CAPABILITY_MATRIX[persona_enum].default_model_tier
    return ModelTier.HIGH_REASONING


def _normalize_tier_key(tier: ModelTier | str) -> ModelTier:
    if isinstance(tier, ModelTier):
        return tier
    key_str = str(tier).strip()
    if key_str in ModelTier.__members__:
        return ModelTier[key_str]
    return ModelTier(key_str)


def _normalize_persona_key(persona: AgentPersona | str) -> AgentPersona:
    if isinstance(persona, AgentPersona):
        return persona
    key_str = str(persona).strip()
    if key_str in AgentPersona.__members__:
        return AgentPersona[key_str]
    return AgentPersona(key_str)


def resolve_provider_model(
    provider: Literal[
        "agy",
        "agy-cli",
        "antigravity",
        "claude",
        "claude-agent",
        "claude-cli",
        "claude-code",
        "deterministic",
        "deterministic-stub",
    ]
    | str,
    tier: ModelTier | str,
    overrides: Mapping[ModelTier, str] | Mapping[str, str] | None = None,
    persona: AgentPersona | str | None = None,
    agent_models: Mapping[AgentPersona, str] | Mapping[str, str] | None = None,
) -> str:
    """Resolve a logical ModelTier and optional AgentPersona to a provider-specific model ID.

    Precedence:
    1. Fine-grained per-agent override in `agent_models` when `persona` is provided.
    2. Semantic tier override in `overrides` ('reasoning' or 'fast').
    3. Provider default model map (`DEFAULT_AGY_MODEL_MAP`, `DEFAULT_CLAUDE_MODEL_MAP`,
       or `DEFAULT_DETERMINISTIC_MODEL_MAP`).
    """
    if persona is not None and agent_models:
        try:
            persona_enum = _normalize_persona_key(persona)
        except ValueError:
            persona_enum = None
        for candidate_key in (
            persona,
            persona_enum,
            persona_enum.value if persona_enum is not None else None,
            persona_enum.name if persona_enum is not None else None,
        ):
            if candidate_key is not None and candidate_key in agent_models:
                candidate_val = str(agent_models[candidate_key]).strip()  # type: ignore[index]
                if candidate_val:
                    return candidate_val

    tier_enum = _normalize_tier_key(tier)
    if overrides:
        for candidate_tier in (tier, tier_enum, tier_enum.value, tier_enum.name):
            if candidate_tier in overrides:
                candidate_val = str(overrides[candidate_tier]).strip()  # type: ignore[index]
                if candidate_val:
                    return candidate_val

    if provider in {"claude", "claude-agent", "claude-cli", "claude-code"}:
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


def is_cli_schema_omission_retry_allowed(config: Any) -> bool:
    """Determine whether a CLI harness is permitted to retry when LLM rejects schema flag."""
    from opendv_contracts.fallbacks import FallbackKey, is_fallback_enabled

    extra = getattr(config, "extra_provider_options", {}) or {}
    fb_dict = extra.get("fallbacks")
    return (
        getattr(config, "allow_cli_schema_omission_retry", False)
        or bool(extra.get("allow_cli_schema_omission_retry"))
        or is_fallback_enabled(
            FallbackKey.CLI_SCHEMA_OMISSION_RETRY,
            {"fallbacks": fb_dict} if isinstance(fb_dict, dict) else None,
        )
        or (
            os.environ.get("OPENDV_FALLBACK_CLI_SCHEMA_OMISSION_RETRY", "").lower()
            in ("1", "true", "yes")
        )
    )


__all__ = [
    "DEFAULT_AGY_MODEL_MAP",
    "DEFAULT_CLAUDE_MODEL_MAP",
    "DEFAULT_DETERMINISTIC_MODEL_MAP",
    "PERSONA_CAPABILITY_MATRIX",
    "PersonaCapabilityProfile",
    "get_persona_capability",
    "is_cli_schema_omission_retry_allowed",
    "is_tool_authorized_for_persona",
    "resolve_default_tier_for_persona",
    "resolve_provider_model",
]
