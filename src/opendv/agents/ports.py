"""Layer 2b ports, ModelTier resolution matrix, and turn DTOs for opendv.agents.

Imports strictly opendv.models and opendv.verifier (zero sources/storage/service/api/cli).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
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


class ResolvedModel(StrictModel):
    """Resolved model selection for a provider, tier, and optional override."""

    provider: HarnessProvider
    tier: ModelTier
    model_id: str
    source: Literal["agent_override", "tier_override", "env_override", "default"]


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
) -> ResolvedModel:
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
        return ResolvedModel(
            provider=provider,
            tier=effective_tier,
            model_id=overrides[agent_id].strip(),
            source="agent_override",
        )
    if overrides.get(effective_tier, "").strip():
        return ResolvedModel(
            provider=provider,
            tier=effective_tier,
            model_id=overrides[effective_tier].strip(),
            source="tier_override",
        )

    active_env = os.environ if env is None else env
    env_var_name = MODEL_ENV_OVERRIDES[provider][effective_tier]
    env_val = active_env.get(env_var_name, "").strip()
    if env_val:
        return ResolvedModel(
            provider=provider,
            tier=effective_tier,
            model_id=env_val,
            source="env_override",
        )

    return ResolvedModel(
        provider=provider,
        tier=effective_tier,
        model_id=MODEL_TIER_DEFAULTS[provider][effective_tier],
        source="default",
    )


def merge_model_overrides(
    runner_overrides: Mapping[str, str],
    request: AgentTurnRequest,
) -> dict[str, str]:
    """Merge runner-level, request.metadata['model_overrides'], and per-turn model_override."""
    merged = dict(runner_overrides)
    meta_ovr = request.metadata.get("model_overrides")
    if isinstance(meta_ovr, Mapping):
        for k, v in meta_ovr.items():
            if isinstance(k, str) and isinstance(v, str) and v.strip():
                merged[k] = v.strip()
    if request.model_override and request.model_override.strip():
        merged[request.agent_id] = request.model_override.strip()
    return merged


class AgentTurnRequest(BaseModel):
    """Stateless turn request passed across AgentHarnessPort."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    run_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    step: RunStep
    prompt: str
    system_instructions: str = ""
    output_schema: type[BaseModel] | None = None
    model_tier: ModelTier = "STANDARD_WORKER"
    model_override: str | None = None
    cegar_policy: CegarPolicy = "balanced"
    role_kind: RoleKind = "none"
    role_token: str | None = None
    broker_url: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    budget: HarnessBudget = Field(default_factory=lambda: STEP4_DEFAULT_BUDGET)
    checkpoint: RunCheckpointRecord | None = None
    attempt: int = Field(default=1, ge=0)
    cluster_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentTurnResult(BaseModel):
    """Normalized turn response returned by CLI, SDK, or test harness runners."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    agent_id: str
    provider: HarnessProvider
    model_id: str
    text_output: str = ""
    structured_output: Any = None
    turns_used: int = Field(default=1, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    token_count_source: Literal["reported", "estimated"] = "estimated"
    thought_deltas: list[str] = Field(default_factory=list)
    text_deltas: list[str] = Field(default_factory=list)
    tool_events: list[dict[str, Any]] = Field(default_factory=list)


@runtime_checkable
class RunEventSink(Protocol):
    """Sink receiving RunStreamEvent records emitted during Step 4 and Step 6."""

    def emit(self, event: RunStreamEvent) -> None:
        """Persist or publish a monotonic RunStreamEvent."""
        ...


@runtime_checkable
class ControlSignalReader(Protocol):
    """Reader polling mid-run Steward control signals (pause, hint, accept-best, etc.)."""

    def poll_signals(self, run_id: str, *, after_seq: int = 0) -> list[ControlSignalRecord]:
        """Return unconsumed control signals for run_id."""
        ...


@runtime_checkable
class RunCheckpointStore(Protocol):
    """Persistence port for Hold/Uncertified checkpoints and CEGAR attempt snapshots."""

    def save_checkpoint(self, record: RunCheckpointRecord) -> None:
        """Persist a stateless Hold or UncertifiedDraft checkpoint."""
        ...

    def load_checkpoint(self, run_id: str) -> RunCheckpointRecord | None:
        """Load the latest RunCheckpointRecord for run_id, if any."""
        ...

    def save_attempt_snapshot(self, run_id: str, snapshot: AttemptSnapshotSpec) -> None:
        """Persist a per-attempt CEGAR AttemptSnapshotSpec."""
        ...

    def release_active_lease(self, run_id: str) -> None:
        """Atomically release the active job lease when checkpointing a Hold."""
        ...


@runtime_checkable
class ScoutToolDispatcher(Protocol):
    """Isolated Step 4 scout dispatcher invoked by brokered coordinator turns."""

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
class AgentHarnessPort(Protocol):
    """Unified stateless turn-execution port shared by CLI, SDK, and test runners."""

    @property
    def provider_name(self) -> HarnessProvider:
        """Return the canonical HarnessProvider identifier."""
        ...

    async def run_turn(
        self,
        request: AgentTurnRequest,
        *,
        event_sink: RunEventSink | None = None,
        scout_dispatcher: ScoutToolDispatcher | None = None,
    ) -> AgentTurnResult:
        """Execute a single stateless turn and return normalized text/structured output."""
        ...


__all__ = [
    "MODEL_ENV_OVERRIDES",
    "MODEL_TIER_DEFAULTS",
    "AgentHarnessPort",
    "AgentTurnRequest",
    "AgentTurnResult",
    "ControlSignalReader",
    "ResolvedModel",
    "RoleKind",
    "RunCheckpointStore",
    "RunEventSink",
    "ScoutToolDispatcher",
    "enforce_v7_tier_lock",
    "merge_model_overrides",
    "resolve_model_for_agent",
]
