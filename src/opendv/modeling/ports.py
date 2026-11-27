"""Layer-2c decoupled engine ports and turn DTOs for opendv.modeling.

Imports strictly opendv.models and opendv.verifier (zero agents/sources/storage/service/api/cli).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from opendv.models.diagnostics import StrictModel
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import ControlSignalRecord, RunCheckpointRecord, RunStep, RunStreamEvent
from opendv.models.policy import STEP4_DEFAULT_BUDGET, CegarPolicy, HarnessBudget, ModelTier
from opendv.models.project import HarnessProvider
from opendv.models.wire_dtos_discovery import ScoutDigest
from opendv.models.wire_dtos_vault import AttemptSnapshotSpec

RoleKind = Literal["coord", "scout", "none"]

MODEL_TIER_DEFAULTS: dict[HarnessProvider, dict[ModelTier, str]] = {
    "agy": {
        "FRONTIER_CRITIC": "gemini-4-argon-high",
        "STANDARD_WORKER": "gemini-3.8-flash-high",
    },
    "gemini": {
        "FRONTIER_CRITIC": "gemini-2.5-pro",
        "STANDARD_WORKER": "gemini-2.5-flash",
    },
    "claude-cli": {
        "FRONTIER_CRITIC": "claude-opus-4-6",
        "STANDARD_WORKER": "claude-sonnet-4-6",
    },
    "claude": {
        "FRONTIER_CRITIC": "claude-opus-4-6",
        "STANDARD_WORKER": "claude-sonnet-4-6",
    },
}

MODEL_ENV_OVERRIDES: dict[HarnessProvider, dict[ModelTier, str]] = {
    "agy": {
        "FRONTIER_CRITIC": "OPENDV_AGY_HIGH_MODEL",
        "STANDARD_WORKER": "OPENDV_AGY_FAST_MODEL",
    },
    "gemini": {
        "FRONTIER_CRITIC": "OPENDV_GEMINI_HIGH_MODEL",
        "STANDARD_WORKER": "OPENDV_GEMINI_FAST_MODEL",
    },
    "claude-cli": {
        "FRONTIER_CRITIC": "OPENDV_CLAUDE_HIGH_MODEL",
        "STANDARD_WORKER": "OPENDV_CLAUDE_FAST_MODEL",
    },
    "claude": {
        "FRONTIER_CRITIC": "OPENDV_CLAUDE_HIGH_MODEL",
        "STANDARD_WORKER": "OPENDV_CLAUDE_FAST_MODEL",
    },
}


class ResolvedModelingModel(StrictModel):
    """Resolved model selection for a provider, tier, and optional override."""

    provider: HarnessProvider = Field(description="Target harness provider.")
    tier: ModelTier = Field(description="Resolved logical model tier.")
    model_id: str = Field(description="Native model identifier.")
    source: Literal["agent_override", "tier_override", "env_override", "default"] = Field(
        description="Origin of the resolved model selection."
    )


def enforce_v7_tier_lock(policy: CegarPolicy, v7_critic_tier: ModelTier) -> ModelTier:
    """Reject downgrading V7 below FRONTIER_CRITIC under strict policy."""
    if policy == "strict" and v7_critic_tier != "FRONTIER_CRITIC":
        raise OpenDVError(
            "V7 critic tier is locked to FRONTIER_CRITIC under strict CEGAR policy.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return v7_critic_tier


def resolve_model_for_agent(
    provider: HarnessProvider,
    tier: ModelTier,
    *,
    agent_id: str | None = None,
    model_overrides: Mapping[str, str] | None = None,
    cegar_policy: CegarPolicy = "balanced",
    env: Mapping[str, str] | None = None,
) -> ResolvedModelingModel:
    """Resolve native model ID from (provider, ModelTier) with explicit override precedence."""
    if provider not in MODEL_TIER_DEFAULTS:
        raise OpenDVError(
            f"Unsupported harness provider {provider!r}.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if tier not in ("FRONTIER_CRITIC", "STANDARD_WORKER"):
        raise OpenDVError(
            f"Unsupported model tier {tier!r}.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    effective_tier: ModelTier = tier
    is_strict_v7 = agent_id == "dv-semantic-critic" and cegar_policy == "strict"
    if agent_id == "dv-horizon-critic" or is_strict_v7:
        effective_tier = "FRONTIER_CRITIC"

    overrides = model_overrides or {}
    if agent_id and overrides.get(agent_id, "").strip():
        return ResolvedModelingModel(
            provider=provider,
            tier=effective_tier,
            model_id=overrides[agent_id].strip(),
            source="agent_override",
        )
    if overrides.get(effective_tier, "").strip():
        return ResolvedModelingModel(
            provider=provider,
            tier=effective_tier,
            model_id=overrides[effective_tier].strip(),
            source="tier_override",
        )

    active_env = os.environ if env is None else env
    env_var_name = MODEL_ENV_OVERRIDES[provider][effective_tier]
    env_val = active_env.get(env_var_name, "").strip()
    if env_val:
        return ResolvedModelingModel(
            provider=provider,
            tier=effective_tier,
            model_id=env_val,
            source="env_override",
        )

    return ResolvedModelingModel(
        provider=provider,
        tier=effective_tier,
        model_id=MODEL_TIER_DEFAULTS[provider][effective_tier],
        source="default",
    )


class ModelingTurnRequest(BaseModel):
    """Stateless turn request passed across ModelingHarnessPort."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True, from_attributes=True)

    run_id: str = Field(min_length=1, description="Unique modeling run identifier.")
    agent_id: str = Field(min_length=1, description="Logical agent pack identifier.")
    step: RunStep = Field(description="Execution stage identifier.")
    prompt: str = Field(description="Rendered user prompt payload for this turn.")
    system_instructions: str = Field(default="", description="System role instructions.")
    output_schema: type[BaseModel] | None = Field(
        default=None,
        description="Optional Pydantic schema expected for structured output.",
    )
    model_tier: ModelTier = Field(default="STANDARD_WORKER", description="Requested tier.")
    model_override: str | None = Field(default=None, description="Explicit model ID override.")
    cegar_policy: CegarPolicy = Field(default="balanced", description="Active CEGAR policy.")
    role_kind: RoleKind = Field(default="none", description="Subagent broker role classification.")
    role_token: str | None = Field(default=None, description="Optional scoped broker token.")
    broker_url: str | None = Field(default=None, description="Optional coordinator broker URL.")
    allowed_tools: list[str] = Field(default_factory=list, description="Tool allowlist.")
    budget: HarnessBudget = Field(
        default_factory=lambda: STEP4_DEFAULT_BUDGET,
        description="Per-turn budget limits.",
    )
    checkpoint: RunCheckpointRecord | None = Field(
        default=None,
        description="Optional persisted resume checkpoint.",
    )
    attempt: int = Field(default=1, ge=0, description="Zero- or one-based attempt counter.")
    cluster_id: str | None = Field(default=None, description="Optional cluster shard ID.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Auxiliary metadata.")


class ModelingTurnResult(BaseModel):
    """Normalized turn response returned by a ModelingHarnessPort implementation."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True, from_attributes=True)

    agent_id: str = Field(description="Responding agent identifier.")
    provider: HarnessProvider = Field(description="Harness provider backing the turn.")
    model_id: str = Field(description="Executed native model identifier.")
    text_output: str = Field(default="", description="Raw text output when unstructured.")
    structured_output: Any = Field(default=None, description="Parsed structured output object.")
    turns_used: int = Field(default=1, ge=0, description="LLM turns consumed.")
    input_tokens: int = Field(default=0, ge=0, description="Input token count.")
    output_tokens: int = Field(default=0, ge=0, description="Output token count.")
    total_tokens: int = Field(default=0, ge=0, description="Combined token count.")
    token_count_source: Literal["reported", "estimated"] = Field(
        default="estimated",
        description="Whether tokens came from provider telemetry or local estimation.",
    )
    thought_deltas: list[str] = Field(default_factory=list, description="Optional thinking log.")
    text_deltas: list[str] = Field(default_factory=list, description="Optional text deltas.")
    tool_events: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Optional tool trace metadata.",
    )


@dataclass(frozen=True)
class LoadedModelingPack:
    """Minimally typed pack metadata & instructions supplied by ModelingPackLoader."""

    agent_id: str
    instructions_markdown: str
    allowed_tools: list[str]
    model_tier: ModelTier = "STANDARD_WORKER"


@runtime_checkable
class ModelingPackLoader(Protocol):
    """Port loading role instructions and the DV2.0 modeling guide without importing agents."""

    def load_pack(
        self,
        agent_id: str,
        *,
        workspace_root: Any = None,
        trusted_workspaces: Sequence[Any] = (),
    ) -> LoadedModelingPack:
        """Load SKILL.md metadata and instructions for agent_id."""
        ...

    def load_modeling_guide(self) -> str:
        """Load concise Data Vault 2.0 modeling guide markdown."""
        ...


@runtime_checkable
class ModelingEventSink(Protocol):
    """Sink receiving RunStreamEvent records emitted during modeling."""

    def emit(self, event: RunStreamEvent) -> None:
        """Persist or publish a monotonic RunStreamEvent."""
        ...


@runtime_checkable
class ModelingControlReader(Protocol):
    """Reader polling mid-run Steward control signals."""

    def poll_signals(self, run_id: str, *, after_seq: int = 0) -> list[ControlSignalRecord]:
        """Return unconsumed control signals for run_id."""
        ...


@runtime_checkable
class ModelingCheckpointStore(Protocol):
    """Persistence port for Hold/Uncertified checkpoints and CEGAR attempt snapshots."""

    def save_checkpoint(self, record: RunCheckpointRecord) -> None:
        """Persist a Hold or UncertifiedDraft checkpoint."""
        ...

    def load_checkpoint(self, run_id: str) -> RunCheckpointRecord | None:
        """Load the latest RunCheckpointRecord for run_id, if any."""
        ...

    def save_attempt_snapshot(self, run_id: str, snapshot: AttemptSnapshotSpec) -> None:
        """Persist a per-attempt AttemptSnapshotSpec."""
        ...

    def release_active_lease(self, run_id: str) -> None:
        """Release the active job lease when checkpointing a Hold."""
        ...


@runtime_checkable
class ModelingScoutDispatcher(Protocol):
    """Optional read-only scout dispatcher for coordinator turns."""

    async def dispatch_scout(
        self,
        scout_kind: Literal["table_cluster", "page_range"],
        target_ids: Sequence[str],
        *,
        run_id: str,
    ) -> list[ScoutDigest]:
        """Execute read-only scouts for target_ids and return ScoutDigest records."""
        ...


@runtime_checkable
class ModelingHarnessPort(Protocol):
    """Stateless turn-execution port for opendv.modeling."""

    @property
    def provider_name(self) -> HarnessProvider:
        """Return the canonical HarnessProvider identifier."""
        ...

    async def run_turn(
        self,
        request: Any,
        *,
        event_sink: ModelingEventSink | None = None,
        scout_dispatcher: ModelingScoutDispatcher | None = None,
    ) -> Any:
        """Execute a single stateless turn and return normalized output."""
        ...


__all__ = [
    "MODEL_ENV_OVERRIDES",
    "MODEL_TIER_DEFAULTS",
    "LoadedModelingPack",
    "ModelingCheckpointStore",
    "ModelingControlReader",
    "ModelingEventSink",
    "ModelingHarnessPort",
    "ModelingPackLoader",
    "ModelingScoutDispatcher",
    "ModelingTurnRequest",
    "ModelingTurnResult",
    "ResolvedModelingModel",
    "RoleKind",
    "enforce_v7_tier_lock",
    "resolve_model_for_agent",
]
