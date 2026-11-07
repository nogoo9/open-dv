"""Vendor-neutral domain models for the Open-DV Agnostic Agent Harness.

Conforms to docs/03-verification-and-agentic-architecture.md
and docs/06-explainability-and-observability-spec.md.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

import pydantic
from pydantic import BaseModel, ConfigDict, Field

InT = TypeVar("InT", bound=BaseModel)
OutT = TypeVar("OutT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class AgentPersona(StrEnum):
    """Canonical agent personas across the 8-stage neuro-symbolic DV 2.0 pipeline."""

    VAULT_FIRST_MATE = "vault-first-mate"
    SCOUT_SOURCE = "scout-source"
    SCOUT_UNSTRUCTURED = "scout-unstructured"
    SHIP_VAULT = "ship-vault"
    AXI_DV = "axi-dv"
    DETERMINISTIC_COMPILER = "deterministic-compiler"


class PipelineStageId(StrEnum):
    """Canonical 8-stage neuro-symbolic pipeline stage identifiers."""

    STAGE_00_ORCHESTRATION = "STAGE_00_ORCHESTRATION"
    STAGE_01_SCOUT_SOURCE = "STAGE_01_SCOUT_SOURCE"
    STAGE_02_SCOUT_UNSTRUCTURED = "STAGE_02_SCOUT_UNSTRUCTURED"
    STAGE_03_SHIP_VAULT_PASS1 = "STAGE_03_SHIP_VAULT_PASS1"
    STAGE_04_SHIP_VAULT_PASS2 = "STAGE_04_SHIP_VAULT_PASS2"
    STAGE_05_AXI_DV_STATIC_GATES = "STAGE_05_AXI_DV_STATIC_GATES"
    STAGE_06_AXI_DV_DYNAMIC_SANDBOX = "STAGE_06_AXI_DV_DYNAMIC_SANDBOX"
    STAGE_07_DETERMINISTIC_COMPILER = "STAGE_07_DETERMINISTIC_COMPILER"


class ObservabilityVerdict(StrEnum):
    """Normative 5-state gate and stage observability verdicts."""

    PASS = "PASS"
    AUTO_FIX = "AUTO-FIX"
    AUTO_REPAIRED = "AUTO_REPAIRED"
    REJECT_WITH_TRACE = "REJECT-WITH-TRACE"
    ASK_USER = "ASK-USER"


class DataClassification(StrEnum):
    """5-tier RBAC data sensitivity classification levels."""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    MNPI = "MNPI"
    RESTRICTED_PII = "RESTRICTED_PII"


class ModelTier(StrEnum):
    """Logical model routing tiers decoupled from vendor model IDs."""

    HIGH_REASONING = "reasoning"  # Deep architectural reasoning, 3-Pass CoT, TOON repair
    FAST_TIER = "fast"  # High-throughput profiling orchestration, PDF span extraction, triage


class SandboxPolicy(BaseModel):
    """Filesystem and tool execution sandbox policy."""

    model_config = ConfigDict(frozen=True)

    read_only_workspace: bool = True
    allowed_workspaces: tuple[Path, ...] = ()
    allow_shell_execution: bool = False
    blocked_shell_substrings: tuple[str, ...] = (
        "rm -rf",
        "git push",
        "git worktree",
        "treehouse",
        "no-mistakes daemon",
    )
    allowed_builtin_tools: tuple[str, ...] = (
        "view_file",
        "list_directory",
        "find_file",
        "search_directory",
    )
    denied_builtin_tools: tuple[str, ...] = ("run_command", "create_file", "edit_file")


class HarnessBudget(BaseModel):
    """Execution budget and token/cost guardrails for an agent task turn."""

    model_config = ConfigDict(frozen=True)

    max_turns: int = Field(default=12, ge=1, le=100)
    max_model_calls: int = Field(default=20, ge=1, le=200)
    max_tool_calls: int = Field(default=40, ge=1, le=500)
    max_input_tokens: int = Field(default=200_000, ge=1_000)
    max_output_tokens: int = Field(default=32_000, ge=512)
    max_total_tokens: int = Field(default=232_000, ge=1_512)
    max_cost_usd: float = Field(default=5.0, gt=0.0)
    timeout_seconds: float = Field(default=300.0, gt=1.0)
    max_schema_validation_retries: int = Field(default=3, ge=0, le=10)


class HarnessConfig(BaseModel):
    """Runtime configuration for a VaultAgentHarness adapter instance."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: Literal[
        "agy",
        "agy-cli",
        "antigravity",
        "claude",
        "claude-cli",
        "claude-code",
    ] = "antigravity"
    workspace_dir: Path
    state_dir: Path
    app_data_dir: Path
    save_dir: Path
    base_url: str | None = None
    model_overrides: dict[ModelTier, str] = Field(
        default_factory=dict,
        description="Semantic tier model overrides keyed by 'reasoning' or 'fast'.",
    )
    agent_models: dict[AgentPersona, str] = Field(
        default_factory=dict,
        description=(
            "Optional fine-grained per-agent model overrides keyed by AgentPersona "
            "('vault-first-mate', 'scout-source', 'scout-unstructured', 'ship-vault')."
        ),
    )
    budget: HarnessBudget = Field(default_factory=HarnessBudget)
    sandbox: SandboxPolicy = Field(default_factory=SandboxPolicy)
    env: dict[str, str] = Field(default_factory=dict)
    viewer_rbac_clears_mnpi: bool = False
    allow_cli_schema_omission_retry: bool = False
    extra_provider_options: dict[str, Any] = Field(default_factory=dict)

    @pydantic.field_validator("workspace_dir", "state_dir", "app_data_dir", "save_dir")
    @classmethod
    def _ensure_absolute_path(cls, v: Path) -> Path:
        if not v.is_absolute():
            raise ValueError(f"Path '{v}' must be an expanded absolute path.")
        return v

    @pydantic.field_validator("model_overrides", mode="before")
    @classmethod
    def _validate_model_overrides(cls, v: Any) -> dict[ModelTier, str]:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError(
                "model_overrides must be a mapping of ModelTier ('reasoning' or 'fast') "
                "to model ID."
            )
        normalized: dict[ModelTier, str] = {}
        for raw_key, raw_val in v.items():
            if isinstance(raw_key, ModelTier):
                tier_key = raw_key
            elif isinstance(raw_key, str):
                key_str = raw_key.strip()
                if key_str in {"argon", "flash"}:
                    raise ValueError(
                        f"Legacy model_overrides key '{key_str}' is no longer supported; "
                        "use semantic tier keys 'reasoning' or 'fast'."
                    )
                if key_str in ModelTier.__members__:
                    tier_key = ModelTier[key_str]
                else:
                    try:
                        tier_key = ModelTier(key_str)
                    except ValueError as exc:
                        raise ValueError(
                            f"Invalid model_overrides key '{raw_key}'; "
                            "expected 'reasoning' or 'fast'."
                        ) from exc
            else:
                raise ValueError(
                    f"Invalid model_overrides key '{raw_key}'; expected 'reasoning' or 'fast'."
                )
            if not isinstance(raw_val, str) or not raw_val.strip():
                raise ValueError(
                    f"model_overrides['{tier_key.value}'] must be a non-empty "
                    "model identifier string."
                )
            normalized[tier_key] = raw_val.strip()
        return normalized

    @pydantic.field_validator("agent_models", mode="before")
    @classmethod
    def _validate_agent_models(cls, v: Any) -> dict[AgentPersona, str]:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError("agent_models must be a mapping of AgentPersona to model ID.")
        normalized: dict[AgentPersona, str] = {}
        for raw_key, raw_val in v.items():
            if isinstance(raw_key, AgentPersona):
                persona_key = raw_key
            elif isinstance(raw_key, str):
                key_str = raw_key.strip()
                if key_str in AgentPersona.__members__:
                    persona_key = AgentPersona[key_str]
                else:
                    try:
                        persona_key = AgentPersona(key_str)
                    except ValueError as exc:
                        raise ValueError(
                            f"Invalid agent_models key '{raw_key}'; expected a valid AgentPersona "
                            "('vault-first-mate', 'scout-source', "
                            "'scout-unstructured', 'ship-vault')."
                        ) from exc
            else:
                raise ValueError(
                    f"Invalid agent_models key '{raw_key}'; expected a valid AgentPersona."
                )
            if not isinstance(raw_val, str) or not raw_val.strip():
                raise ValueError(
                    f"agent_models['{persona_key.value}'] must be a non-empty "
                    "model identifier string."
                )
            normalized[persona_key] = raw_val.strip()
        return normalized


class ToolExecutionContext:
    """Injected into pure-Python domain tools during execution across any SDK adapter."""

    def __init__(
        self,
        *,
        run_id: str,
        trace_id: str,
        span_id: str,
        parent_span_id: str | None = None,
        persona: AgentPersona,
        stage_id: PipelineStageId,
        config: HarnessConfig,
    ) -> None:
        self.run_id = run_id
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.persona = persona
        self.stage_id = stage_id
        self.config = config
        self._session_state: dict[str, Any] = {}
        self.emitted_events: list[dict[str, Any]] = []
        self._emitted_cursor: int = 0
        self.artifacts_written: dict[str, str] = {}

    def get_state(self, key: str, default: Any = None) -> Any:
        """Retrieve a session state value by key."""
        return self._session_state.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        """Store a session state value by key."""
        self._session_state[key] = value

    def emit_observability_event(self, event_dict: dict[str, Any]) -> None:
        """Buffer an observability event dictionary for streaming and ledger persistence."""
        self.emitted_events.append(event_dict)

    def pop_unstreamed_events(self) -> list[dict[str, Any]]:
        """Drain newly emitted observability events since the last cursor check."""
        pending = self.emitted_events[self._emitted_cursor :]
        self._emitted_cursor = len(self.emitted_events)
        return pending

    def write_cas_artifact(self, artifact_key: str, content: bytes | str) -> str:
        """Write content to the CAS store and return its 64-hex uppercase SHA-256."""
        raw_bytes = content.encode("utf-8") if isinstance(content, str) else content
        sha256_hex = hashlib.sha256(raw_bytes).hexdigest().upper()
        artifacts_dir = self.config.state_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        blob_path = artifacts_dir / sha256_hex
        if not blob_path.exists():
            blob_path.write_bytes(raw_bytes)
        self.artifacts_written[artifact_key] = sha256_hex
        return sha256_hex


class AgentToolDefinition(Generic[InT, OutT]):  # noqa: UP046
    """Vendor-neutral domain tool specification bridged into Antigravity and Claude SDKs."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_model: type[InT],
        output_model: type[OutT],
        handler: Callable[[InT, ToolExecutionContext], Awaitable[OutT]],
        allowed_personas: frozenset[AgentPersona] | set[AgentPersona],
        read_only: bool = True,
        requires_steward_approval: bool = False,
    ) -> None:
        if not name.isidentifier():
            raise ValueError(f"Tool name '{name}' must be a valid Python identifier.")
        self.name = name
        self.description = description
        self.input_model = input_model
        self.output_model = output_model
        self.handler = handler
        self.allowed_personas: frozenset[AgentPersona] = frozenset(allowed_personas)
        self.read_only = read_only
        self.requires_steward_approval = requires_steward_approval

    def json_schema(self) -> dict[str, Any]:
        """Return the Draft 2020-12 JSON Schema for the tool input model."""
        return self.input_model.model_json_schema()

    async def invoke(self, raw_args: dict[str, Any] | InT, ctx: ToolExecutionContext) -> OutT:
        """Enforce persona RBAC, validate input arguments, and invoke the async tool handler."""
        if ctx.persona not in self.allowed_personas:
            raise PermissionError(
                f"Persona '{ctx.persona}' is not authorized to invoke tool '{self.name}'."
            )
        validated_input = (
            raw_args
            if isinstance(raw_args, self.input_model)
            else self.input_model.model_validate(raw_args)
        )
        return await self.handler(validated_input, ctx)


class SubagentSpec(BaseModel):
    """Specification for a delegable subagent persona."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    persona: AgentPersona
    description: str
    system_instructions: str
    model_tier: ModelTier = ModelTier.FAST_TIER
    model_override: str | None = Field(default=None)
    allowed_tool_names: tuple[str, ...] = ()
    allowed_subagents: tuple[str, ...] = ()
    read_only: bool = True


class AgentTaskSpec(BaseModel, Generic[OutputT]):  # noqa: UP046
    """Vendor-neutral task specification dispatched to a VaultAgentHarness."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str = Field(default_factory=lambda: f"task-{uuid.uuid4().hex[:12]}")
    run_id: str
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    parent_span_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    persona: AgentPersona
    stage_id: PipelineStageId
    prompt: str
    system_instructions: str
    model_tier: ModelTier = ModelTier.HIGH_REASONING
    model_override: str | None = Field(default=None)
    tools: Sequence[AgentToolDefinition[Any, Any]] = ()
    subagents: Sequence[SubagentSpec] = ()
    output_schema: type[OutputT] | None = None
    attachments: Sequence[Path] = ()
    resume_session_id: str | None = None
    context_refs: dict[str, str | None] = Field(default_factory=dict)


class HarnessEventKind(StrEnum):
    """Event kinds emitted by VaultAgentHarness.stream_task()."""

    SESSION_START = "SESSION_START"
    THOUGHT_DELTA = "THOUGHT_DELTA"
    TEXT_DELTA = "TEXT_DELTA"
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_RESULT = "TOOL_CALL_RESULT"
    TOOL_CALL_ERROR = "TOOL_CALL_ERROR"
    SUBAGENT_SPAWN = "SUBAGENT_SPAWN"
    STEWARD_HOLD = "STEWARD_HOLD"
    OBSERVABILITY_EVENT = "OBSERVABILITY_EVENT"
    TURN_COMPLETE = "TURN_COMPLETE"
    CANCELLED = "CANCELLED"


class HarnessStreamEvent(BaseModel):
    """Real-time stream event emitted during agent task execution."""

    event_id: str = Field(default_factory=lambda: f"hse-{uuid.uuid4().hex[:12]}")
    timestamp_utc: str = Field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    run_id: str
    task_id: str
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    provider: str
    persona: AgentPersona
    stage_id: PipelineStageId
    kind: HarnessEventKind
    delta_text: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None
    observability_event: dict[str, Any] | None = None


class HarnessStopReason(StrEnum):
    """Terminal stop reason for a completed or halted HarnessRunResult."""

    COMPLETED = "COMPLETED"
    MAX_TURNS_EXCEEDED = "MAX_TURNS_EXCEEDED"
    MAX_MODEL_CALLS_EXCEEDED = "MAX_MODEL_CALLS_EXCEEDED"
    MAX_TOOL_CALLS_EXCEEDED = "MAX_TOOL_CALLS_EXCEEDED"
    TOKEN_BUDGET_EXCEEDED = "TOKEN_BUDGET_EXCEEDED"
    COST_BUDGET_EXCEEDED = "COST_BUDGET_EXCEEDED"
    STEWARD_HOLD = "STEWARD_HOLD"
    CANCELLED = "CANCELLED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class HarnessUsageMetrics(BaseModel):
    """Aggregated token, call, cost, and latency metrics for a task turn."""

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0


class HarnessRunResult(BaseModel, Generic[OutputT]):  # noqa: UP046
    """Final aggregated result returned by VaultAgentHarness.run_task()."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str
    run_id: str
    session_id: str
    trace_id: str
    span_id: str
    provider: str
    model_used: str
    stop_reason: HarnessStopReason
    text_output: str
    structured_output: OutputT | dict[str, Any] | None = None
    usage: HarnessUsageMetrics = Field(default_factory=HarnessUsageMetrics)
    emitted_events: list[dict[str, Any]] = Field(default_factory=list)
    artifacts_written: dict[str, str] = Field(default_factory=dict)
    subagent_models: dict[str, str] = Field(default_factory=dict)


__all__ = [
    "AgentPersona",
    "AgentTaskSpec",
    "AgentToolDefinition",
    "DataClassification",
    "HarnessBudget",
    "HarnessConfig",
    "HarnessEventKind",
    "HarnessRunResult",
    "HarnessStopReason",
    "HarnessStreamEvent",
    "HarnessUsageMetrics",
    "InT",
    "ModelTier",
    "ObservabilityVerdict",
    "OutT",
    "OutputT",
    "PipelineStageId",
    "SandboxPolicy",
    "SubagentSpec",
    "ToolExecutionContext",
]
