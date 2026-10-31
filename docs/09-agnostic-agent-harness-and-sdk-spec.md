# 09 — Harness-Agnostic Agent Runtime & Dual-SDK Adapter Specification (`packages/opendv-agent-harness`)

> **Module:** `docs/09-agnostic-agent-harness-and-sdk-spec.md`  
> **Parent Specification:** [`DV2_AGENT_REFERENCE_GUIDE.md`](DV2_AGENT_REFERENCE_GUIDE.md) (`v6.0.0`)  
> **Companion Platform Specs:** [`08-platform-mvp-monorepo-and-api-spec.md`](08-platform-mvp-monorepo-and-api-spec.md) · [`10-decoupled-ui-workbench-and-theme-spec.md`](10-decoupled-ui-workbench-and-theme-spec.md) · [`11-agent-worker-implementation-plan.md`](11-agent-worker-implementation-plan.md)  
> **Scope:** Vendor-neutral `VaultAgentHarness` protocol, `AgentTaskSpec`, `AgentToolDefinition`, `SubagentSpec`, `argon`/`flash` model-tier routing, automatic dual-SDK tool bridges, concrete `AntigravityHarnessAdapter` (`google.antigravity`) and `ClaudeAgentHarnessAdapter` (`claude_agent_sdk`) implementations, and real-time translation of SDK streams into `VaultObservabilityEvent` ledger records.

---

## 1. Hexagonal Agent Harness Architecture & Core Design Principles

`packages/opendv-agent-harness` isolates the `open-dv` neuro-symbolic modelling pipeline (`STAGE_00_ORCHESTRATION` through `STAGE_07_DETERMINISTIC_COMPILER`) from vendor-specific LLM agent SDKs using a strict **Ports and Adapters (Hexagonal) architecture**:

1. **Zero Vendor Leakage:** `packages/opendv-core`, `packages/opendv-axi-verifier`, `packages/opendv-duckdb-sandbox`, and `apps/opendv-platform-api` interact **exclusively** with `VaultAgentHarness.stream_task(spec)` and `VaultAgentHarness.run_task(spec)`.
2. **Single-Source-of-Truth Pure-Python Domain Tools:** All 8 deterministic domain tools (`profile_source_feed`, `extract_pdf_spans`, `compile_canonical_ir`, `run_axi_dv_gates`, `apply_ast_codemod`, `execute_duckdb_sandbox`, `emit_observability_event`, `request_steward_decision`) are authored once as `AgentToolDefinition[InT, OutT]` descriptors and bridged automatically into:
   - **Google Antigravity SDK (`google.antigravity`)**: Native typed async callables with synthesized `inspect.Signature`, `__annotations__`, Google-style docstrings, and `ToolContext` state synchronization.
   - **Anthropic Claude Agent SDK (`claude_agent_sdk`)**: In-process `@tool` handlers bundled via `create_sdk_mcp_server(name="opendv", ...)` under the `mcp__opendv__<tool_name>` namespace.
3. **Import Hygiene Guardrail (`google.antigravity` vs. stdlib `antigravity`):** In Python 3.12+, `import antigravity` resolves to the standard library's Easter egg module (`webbrowser.open("https://xkcd.com/353/")`). `AntigravityHarnessAdapter` imports exclusively from `google.antigravity` (`from google.antigravity import Agent, LocalAgentConfig, CapabilitiesConfig, types`) and `google.antigravity.hooks`, enforced by Ruff rule `TID251`.

### 1.1 Side-by-Side SDK Crosswalk Matrix

| `open-dv` Harness Concept | `opendv_agent_harness` Type | Adapter 1: Google Antigravity SDK (`google.antigravity`) | Adapter 2: Claude Agent SDK (`claude_agent_sdk`) |
| :--- | :--- | :--- | :--- |
| **Session Context Manager** | `VaultAgentHarness.stream_task(spec)` | `async with Agent(LocalAgentConfig(...)) as agent:` | `async with ClaudeSDKClient(ClaudeAgentOptions(...)) as client:` |
| **System Persona** | `AgentTaskSpec.system_instructions` | `types.TemplatedSystemInstructions(identity=...)` | `ClaudeAgentOptions(system_prompt=...)` |
| **Custom Domain Tools** | `AgentToolDefinition[InT, OutT]` | `LocalAgentConfig(tools=[to_antigravity_tool_callable(...)])` | `@tool` + `create_sdk_mcp_server("opendv", ...)` (`mcp__opendv__*`) |
| **Subagent Delegation** | `SubagentSpec` | `types.SubagentConfig(name, description, capabilities=SubagentCapabilities(...))` | `AgentDefinition(description, prompt, tools, model)` in `options.agents` |
| **Sandbox & Tool Policy** | `SandboxPolicy` | 9-tier `policy.deny_all()`, `policy.workspace_only(ws)`, `policy.allow(...)` | `allowed_tools`, `disallowed_tools`, `permission_mode="dontAsk"`, `HookMatcher` |
| **Constrained JSON Output** | `AgentTaskSpec.output_schema` | `LocalAgentConfig(response_schema=PydanticModel)` $\to$ `await response.structured_output()` | `ClaudeAgentOptions(output_format={"type": "json_schema", "schema": ...})` $\to$ `ResultMessage.structured_output` |
| **Reasoning Stream** | `HarnessEventKind.THOUGHT_DELTA` | `async for thought in response.thoughts:` | `ThinkingBlock.thinking` inside `AssistantMessage.content` |
| **Tool Call Stream** | `TOOL_CALL_START` / `TOOL_CALL_RESULT` | `async for call in response.tool_calls:` + `@hooks.post_tool_call` | `ToolUseBlock` (prefix stripped) / `ToolResultBlock` |
| **Turn Cancellation** | `cancel_active_turn(task_id)` | `await response.cancel()` (`AntigravityCancelledError`) | `await client.interrupt()` |
| **Session Resumption** | `AgentTaskSpec.resume_session_id` | `LocalAgentConfig(conversation_id=id, save_dir=dir)` | `ClaudeAgentOptions(resume=session_id)` |

---

## 2. Vendor-Neutral Protocol, Domain Models & Model-Tier Router (`models.py`, `protocol.py`, `routing.py`)

```python
from __future__ import annotations

import abc
import asyncio
import hashlib
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Generic, Literal, Protocol, TypeVar, runtime_checkable

import pydantic
from pydantic import BaseModel, ConfigDict, Field

InT = TypeVar("InT", bound=BaseModel)
OutT = TypeVar("OutT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class AgentPersona(StrEnum):
    VAULT_FIRST_MATE = "vault-first-mate"
    SCOUT_SOURCE = "scout-source"
    SCOUT_UNSTRUCTURED = "scout-unstructured"
    SHIP_VAULT = "ship-vault"
    AXI_DV = "axi-dv"
    DETERMINISTIC_COMPILER = "deterministic-compiler"


class PipelineStageId(StrEnum):
    STAGE_00_ORCHESTRATION = "STAGE_00_ORCHESTRATION"
    STAGE_01_SCOUT_SOURCE = "STAGE_01_SCOUT_SOURCE"
    STAGE_02_SCOUT_UNSTRUCTURED = "STAGE_02_SCOUT_UNSTRUCTURED"
    STAGE_03_SHIP_VAULT_PASS1 = "STAGE_03_SHIP_VAULT_PASS1"
    STAGE_04_SHIP_VAULT_PASS2 = "STAGE_04_SHIP_VAULT_PASS2"
    STAGE_05_AXI_DV_STATIC_GATES = "STAGE_05_AXI_DV_STATIC_GATES"
    STAGE_06_AXI_DV_DYNAMIC_SANDBOX = "STAGE_06_AXI_DV_DYNAMIC_SANDBOX"
    STAGE_07_DETERMINISTIC_COMPILER = "STAGE_07_DETERMINISTIC_COMPILER"


class WorkbenchViewId(StrEnum):
    WB_00 = "WB-00"
    WB_01 = "WB-01"
    WB_02 = "WB-02"
    WB_03 = "WB-03"
    WB_04 = "WB-04"
    WB_05 = "WB-05"
    WB_06 = "WB-06"
    WB_07 = "WB-07"
    WB_08 = "WB-08"
    WB_09 = "WB-09"
    WB_10 = "WB-10"
    WB_11 = "WB-11"


class VizId(StrEnum):
    VIZ_PIPE_00 = "VIZ-PIPE-00"
    VIZ_STEP_01 = "VIZ-STEP-01"
    VIZ_STEP_02 = "VIZ-STEP-02"
    VIZ_STEP_03 = "VIZ-STEP-03"
    VIZ_STEP_04 = "VIZ-STEP-04"
    VIZ_STEP_05 = "VIZ-STEP-05"
    VIZ_STEP_06 = "VIZ-STEP-06"
    VIZ_STEP_07 = "VIZ-STEP-07"
    VIZ_STEP_08 = "VIZ-STEP-08"
    VIZ_GATE_01 = "VIZ-GATE-01"
    VIZ_GATE_02 = "VIZ-GATE-02"
    VIZ_GATE_03 = "VIZ-GATE-03"
    VIZ_GATE_04 = "VIZ-GATE-04"
    VIZ_GATE_05 = "VIZ-GATE-05"
    VIZ_GATE_06 = "VIZ-GATE-06"
    VIZ_GATE_07 = "VIZ-GATE-07"
    VIZ_GATE_08 = "VIZ-GATE-08"


class ObservabilityVerdict(StrEnum):
    PASS = "PASS"
    AUTO_FIX = "AUTO-FIX"
    REJECT_WITH_TRACE = "REJECT-WITH-TRACE"
    ASK_USER = "ASK-USER"


class DataClassification(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED_PII = "RESTRICTED_PII"


class ModelTier(StrEnum):
    """Logical model routing tiers decoupled from vendor model IDs."""

    HIGH_REASONING = "argon"  # Deep architectural reasoning, 3-Pass CoT, TOON repair, orchestration
    FAST_TIER = "flash"       # High-throughput profiling orchestration, PDF span extraction, triage


class SandboxPolicy(BaseModel):
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
    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: Literal["antigravity", "claude"] = "antigravity"
    workspace_dir: Path
    state_dir: Path
    app_data_dir: Path
    save_dir: Path
    model_overrides: dict[ModelTier, str] = Field(default_factory=dict)
    budget: HarnessBudget = Field(default_factory=HarnessBudget)
    sandbox: SandboxPolicy = Field(default_factory=SandboxPolicy)
    env: dict[str, str] = Field(default_factory=dict)
    viewer_rbac_clears_mnpi: bool = False
    extra_provider_options: dict[str, Any] = Field(default_factory=dict)

    @pydantic.field_validator("workspace_dir", "state_dir", "app_data_dir", "save_dir")
    @classmethod
    def _ensure_absolute_path(cls, v: Path) -> Path:
        if not v.is_absolute():
            raise ValueError(f"Path '{v}' must be an expanded absolute path.")
        return v


class ToolExecutionContext:
    """Injected into pure-Python domain tools during execution across any SDK adapter."""

    def __init__(
        self,
        *,
        run_id: str,
        trace_id: str,
        span_id: str,
        parent_span_id: str | None,
        persona: AgentPersona,
        stage_id: PipelineStageId,
        workbench_view_id: WorkbenchViewId,
        viz_id: VizId,
        config: HarnessConfig,
    ) -> None:
        self.run_id = run_id
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.persona = persona
        self.stage_id = stage_id
        self.workbench_view_id = workbench_view_id
        self.viz_id = viz_id
        self.config = config
        self._session_state: dict[str, Any] = {}
        self.emitted_events: list[dict[str, Any]] = []
        self._emitted_cursor: int = 0
        self.artifacts_written: dict[str, str] = {}

    def get_state(self, key: str, default: Any = None) -> Any:
        return self._session_state.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        self._session_state[key] = value

    def emit_observability_event(self, event_dict: dict[str, Any]) -> None:
        self.emitted_events.append(event_dict)

    def pop_unstreamed_events(self) -> list[dict[str, Any]]:
        pending = self.emitted_events[self._emitted_cursor :]
        self._emitted_cursor = len(self.emitted_events)
        return pending

    def write_cas_artifact(self, artifact_key: str, content: bytes | str) -> str:
        raw_bytes = content.encode("utf-8") if isinstance(content, str) else content
        sha256_hex = hashlib.sha256(raw_bytes).hexdigest().upper()
        artifacts_dir = self.config.state_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        blob_path = artifacts_dir / sha256_hex
        if not blob_path.exists():
            blob_path.write_bytes(raw_bytes)
        self.artifacts_written[artifact_key] = sha256_hex
        return sha256_hex


class AgentToolDefinition(Generic[InT, OutT]):
    """Vendor-neutral domain tool specification bridged into Antigravity and Claude SDKs."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_model: type[InT],
        output_model: type[OutT],
        handler: Callable[[InT, ToolExecutionContext], Awaitable[OutT]],
        allowed_personas: frozenset[AgentPersona],
        read_only: bool = True,
        requires_steward_approval: bool = False,
        default_viz_id: VizId | None = None,
    ) -> None:
        if not name.isidentifier():
            raise ValueError(f"Tool name '{name}' must be a valid Python identifier.")
        self.name = name
        self.description = description
        self.input_model = input_model
        self.output_model = output_model
        self.handler = handler
        self.allowed_personas = allowed_personas
        self.read_only = read_only
        self.requires_steward_approval = requires_steward_approval
        self.default_viz_id = default_viz_id

    def json_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    async def invoke(self, raw_args: dict[str, Any], ctx: ToolExecutionContext) -> OutT:
        if ctx.persona not in self.allowed_personas:
            raise PermissionError(
                f"Persona '{ctx.persona}' is not authorized to invoke tool '{self.name}'."
            )
        validated_input = self.input_model.model_validate(raw_args)
        return await self.handler(validated_input, ctx)


class SubagentSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    persona: AgentPersona
    description: str
    system_instructions: str
    model_tier: ModelTier = ModelTier.FAST_TIER
    allowed_tool_names: tuple[str, ...] = ()
    allowed_subagents: tuple[str, ...] = ()
    read_only: bool = True


class AgentTaskSpec(BaseModel, Generic[OutputT]):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str = Field(default_factory=lambda: f"task-{uuid.uuid4().hex[:12]}")
    run_id: str
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    parent_span_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    persona: AgentPersona
    stage_id: PipelineStageId
    workbench_view_id: WorkbenchViewId
    viz_id: VizId
    prompt: str
    system_instructions: str
    model_tier: ModelTier = ModelTier.HIGH_REASONING
    tools: Sequence[AgentToolDefinition[Any, Any]] = ()
    subagents: Sequence[SubagentSpec] = ()
    output_schema: type[OutputT] | None = None
    attachments: Sequence[Path] = ()
    resume_session_id: str | None = None
    context_refs: dict[str, str | None] = Field(default_factory=dict)


class HarnessEventKind(StrEnum):
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
    event_id: str = Field(default_factory=lambda: f"hse-{uuid.uuid4().hex[:12]}")
    timestamp_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    run_id: str
    task_id: str
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    provider: str
    persona: AgentPersona
    stage_id: PipelineStageId
    workbench_view_id: WorkbenchViewId
    viz_id: VizId
    kind: HarnessEventKind
    delta_text: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None
    observability_event: dict[str, Any] | None = None


class HarnessStopReason(StrEnum):
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
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0


class HarnessRunResult(BaseModel, Generic[OutputT]):
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


@runtime_checkable
class VaultAgentHarness(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def config(self) -> HarnessConfig: ...

    def resolve_model(self, tier: ModelTier, persona: AgentPersona) -> str: ...

    def stream_task(
        self,
        spec: AgentTaskSpec[OutputT],
    ) -> AsyncIterator[HarnessStreamEvent]: ...

    async def run_task(
        self,
        spec: AgentTaskSpec[OutputT],
    ) -> HarnessRunResult[OutputT]: ...

    async def cancel_active_turn(self, task_id: str) -> None: ...


class BaseVaultAgentHarness(abc.ABC):
    """Template base class implementing W3C span management and run_task() over stream_task()."""

    def __init__(self, config: HarnessConfig) -> None:
        self._config = config
        self._active_cancels: dict[str, Callable[[], Awaitable[None]]] = {}

    @property
    @abc.abstractmethod
    def provider_name(self) -> str:
        raise NotImplementedError

    @property
    def config(self) -> HarnessConfig:
        return self._config

    @abc.abstractmethod
    def resolve_model(self, tier: ModelTier, persona: AgentPersona) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def _stream_provider_task(
        self,
        spec: AgentTaskSpec[OutputT],
        ctx: ToolExecutionContext,
        model_id: str,
    ) -> AsyncIterator[HarnessStreamEvent]:
        raise NotImplementedError

    def _new_span_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def _make_event(
        self,
        spec: AgentTaskSpec[Any],
        ctx: ToolExecutionContext,
        kind: HarnessEventKind,
        **kwargs: Any,
    ) -> HarnessStreamEvent:
        if "obs_event" in kwargs and "observability_event" not in kwargs:
            kwargs["observability_event"] = kwargs.pop("obs_event")
        return HarnessStreamEvent(
            kind=kind,
            task_id=spec.task_id,
            run_id=spec.run_id,
            trace_id=ctx.trace_id,
            span_id=ctx.span_id,
            parent_span_id=ctx.parent_span_id,
            provider=self.provider_name,
            persona=spec.persona,
            stage_id=spec.stage_id,
            workbench_view_id=spec.workbench_view_id,
            viz_id=spec.viz_id,
            **kwargs,
        )

    def build_tool_context(self, spec: AgentTaskSpec[Any]) -> ToolExecutionContext:
        return ToolExecutionContext(
            run_id=spec.run_id,
            trace_id=spec.trace_id,
            span_id=self._new_span_id(),
            parent_span_id=spec.parent_span_id,
            persona=spec.persona,
            stage_id=spec.stage_id,
            workbench_view_id=spec.workbench_view_id,
            viz_id=spec.viz_id,
            config=self._config,
        )

    async def stream_task(
        self,
        spec: AgentTaskSpec[OutputT],
    ) -> AsyncIterator[HarnessStreamEvent]:
        ctx = self.build_tool_context(spec)
        model_id = self.resolve_model(spec.model_tier, spec.persona)
        async for event in self._stream_provider_task(spec, ctx, model_id):
            yield event

    async def run_task(
        self,
        spec: AgentTaskSpec[OutputT],
    ) -> HarnessRunResult[OutputT]:
        start_ns = time.perf_counter_ns()
        ctx = self.build_tool_context(spec)
        model_id = self.resolve_model(spec.model_tier, spec.persona)
        text_chunks: list[str] = []
        tool_call_count = 0
        session_id = spec.resume_session_id or f"sess-{uuid.uuid4().hex[:12]}"
        stop_reason = HarnessStopReason.COMPLETED
        structured_payload: OutputT | dict[str, Any] | None = None
        usage = HarnessUsageMetrics()

        try:
            async with asyncio.timeout(self._config.budget.timeout_seconds):
                async for event in self._stream_provider_task(spec, ctx, model_id):
                    if event.kind == HarnessEventKind.TEXT_DELTA and event.delta_text:
                        text_chunks.append(event.delta_text)
                    elif event.kind == HarnessEventKind.TOOL_CALL_START:
                        tool_call_count += 1
                    elif event.kind == HarnessEventKind.STEWARD_HOLD:
                        stop_reason = HarnessStopReason.STEWARD_HOLD
                    elif (
                        event.kind == HarnessEventKind.OBSERVABILITY_EVENT
                        and isinstance(event.observability_event, dict)
                        and event.observability_event.get("status") == "ASK-USER"
                    ):
                        stop_reason = HarnessStopReason.STEWARD_HOLD
                    elif event.kind == HarnessEventKind.TURN_COMPLETE and event.tool_result:
                        meta = event.tool_result
                        session_id = str(meta.get("session_id", session_id))
                        raw_struct = meta.get("structured_output")
                        if raw_struct is not None:
                            if spec.output_schema is not None and isinstance(raw_struct, dict):
                                structured_payload = spec.output_schema.model_validate(raw_struct)
                            else:
                                structured_payload = raw_struct
                        if "usage" in meta and isinstance(meta["usage"], dict):
                            u = meta["usage"]
                            usage = HarnessUsageMetrics(
                                input_tokens=int(u.get("input_tokens", 0)),
                                cached_input_tokens=int(u.get("cached_input_tokens", 0)),
                                output_tokens=int(u.get("output_tokens", 0)),
                                reasoning_tokens=int(u.get("reasoning_tokens", 0)),
                                total_tokens=int(u.get("total_tokens", 0)),
                                model_calls=int(u.get("model_calls", 1)),
                                tool_calls=tool_call_count,
                                cost_usd=float(u.get("cost_usd", 0.0)),
                            )
        except asyncio.CancelledError:
            stop_reason = HarnessStopReason.CANCELLED
        except TimeoutError:
            stop_reason = HarnessStopReason.MAX_TURNS_EXCEEDED

        usage.duration_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000)
        usage.tool_calls = tool_call_count
        return HarnessRunResult(
            task_id=spec.task_id,
            run_id=spec.run_id,
            session_id=session_id,
            trace_id=ctx.trace_id,
            span_id=ctx.span_id,
            provider=self.provider_name,
            model_used=model_id,
            stop_reason=stop_reason,
            text_output="".join(text_chunks),
            structured_output=structured_payload,
            usage=usage,
            emitted_events=list(ctx.emitted_events),
            artifacts_written=dict(ctx.artifacts_written),
        )

    async def cancel_active_turn(self, task_id: str) -> None:
        cancel_fn = self._active_cancels.get(task_id)
        if cancel_fn is not None:
            await cancel_fn()
```

---

## 3. Pure-Python Domain Tool Catalog & Dual-SDK Tool Bridge (`tools/`)

### 3.1 The 8 Canonical `open-dv` Domain Tools

| Tool # | Tool Identifier | Authorized Personas | Read-Only? | Primary Pipeline Stage(s) | Workbench / `VizId` | Underlying Algorithm / Schema |
| :---: | :--- | :--- | :---: | :--- | :--- | :--- |
| **1** | `profile_source_feed` | `scout-source`, `vault-first-mate` | Yes | `STAGE_01_SCOUT_SOURCE` | `WB-02` (`VIZ-STEP-02`), `WB-09` (`VIZ-STEP-03`) | `docs/05` §1 Graph B + `Algorithm A.9a` |
| **2** | `extract_pdf_spans` | `scout-unstructured` | Yes | `STAGE_02_SCOUT_UNSTRUCTURED` | `WB-03` (`VIZ-STEP-04`, `VIZ-GATE-06`) | `docs/04` Rules `U1–U4`, `Tier-U1..U3`, `Alg A.9b` |
| **3** | `compile_canonical_ir` | `ship-vault`, `vault-first-mate` | Writes CAS | `STAGE_07_DETERMINISTIC_COMPILER` | `WB-11` (`VIZ-STEP-07`, `VIZ-STEP-08`) | `schemas/dv2-canonical-ir.schema.json` + `Alg A.5` + `D.10` |
| **4** | `run_axi_dv_gates` | `ship-vault`, `vault-first-mate` | Yes | `STAGE_05_AXI_DV_STATIC_GATES` | `WB-00` (`VIZ-GATE-01`), `WB-06` (`VIZ-GATE-03`), `WB-08` (`VIZ-GATE-07`) | `Algorithms A.6`, `A.7`, `A.8`, `@toon_v1` |
| **5** | `apply_ast_codemod` | `ship-vault` | Writes CAS | `STAGE_04_SHIP_VAULT_PASS2`, `STAGE_05` | `WB-05` (`VIZ-GATE-04`), `WB-07` (`VIZ-GATE-05`) | `Algorithms A.1`, `A.2`, `A.3`, `A.4`, `A.5`, `A.9a` |
| **6** | `execute_duckdb_sandbox` | `ship-vault`, `vault-first-mate` | Ephemeral DB | `STAGE_06_AXI_DV_DYNAMIC_SANDBOX` | `WB-10` (`VIZ-GATE-08`) | `docs/03` §2 & §7 (`DV-DYN-001..015` + Batches 1–4) |
| **7** | `emit_observability_event` | All 4 Agent Personas | Appends JSONL | `STAGE_00` .. `STAGE_07` | `WB-00` .. `WB-11` | `schemas/vault-observability-event.schema.json` |
| **8** | `request_steward_decision` | `vault-first-mate`, `scout-unstructured`, `ship-vault` | Holds Task | `STAGE_00`, `STAGE_02`, `STAGE_05` | `WB-03` (`Tier-U3`), `WB-08` (`ASK-USER`) | Captain-Hold (`ASK-USER` on `Tier-U3` or `DV-EVO-003`) |

### 3.2 Automatic Dual-SDK Tool Bridge (`tools/bridge.py`)

```python
from __future__ import annotations

import inspect
import json
from collections.abc import Sequence
from typing import Any, Callable


def build_google_docstring(description: str, model_fields: dict[str, Any]) -> str:
    """Generate a Google-style docstring from Pydantic model fields for AGY tool introspection."""
    lines = [description.strip(), "", "Args:"]
    for field_name, field_info in model_fields.items():
        desc = getattr(field_info, "description", None) or f"Parameter '{field_name}'."
        lines.append(f"    {field_name}: {desc}")
    return "\n".join(lines)


def to_antigravity_tool_callable(
    tool_def: Any,
    exec_ctx: Any,
) -> Callable[..., Any]:
    """
    Synthesize a strongly-typed Python async function with exact inspect.Signature,
    __annotations__, and Google-style docstring for google.antigravity.LocalAgentConfig(tools=[...]).
    """
    parameters: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}

    for field_name, field_info in tool_def.input_model.model_fields.items():
        annotation = field_info.annotation if field_info.annotation is not None else Any
        default = inspect.Parameter.empty if field_info.is_required() else field_info.default
        parameters.append(
            inspect.Parameter(
                name=field_name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
        )
        annotations[field_name] = annotation

    annotations["return"] = str

    async def _agy_tool_wrapper(**kwargs: Any) -> str:
        agy_ctx = kwargs.pop("ctx", None)
        if agy_ctx is not None and hasattr(agy_ctx, "get_state") and hasattr(agy_ctx, "set_state"):
            saved = agy_ctx.get_state("opendv_state", {})
            for k, v in saved.items():
                exec_ctx.set_state(k, v)

        output_model = await tool_def.invoke(kwargs, exec_ctx)

        if agy_ctx is not None and hasattr(agy_ctx, "set_state"):
            agy_ctx.set_state("opendv_state", exec_ctx._session_state)

        return str(output_model.model_dump_json())

    _agy_tool_wrapper.__name__ = tool_def.name
    _agy_tool_wrapper.__qualname__ = tool_def.name
    _agy_tool_wrapper.__doc__ = build_google_docstring(
        tool_def.description,
        tool_def.input_model.model_fields,
    )
    _agy_tool_wrapper.__annotations__ = annotations
    _agy_tool_wrapper.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        parameters=parameters,
        return_annotation=str,
    )
    return _agy_tool_wrapper


def build_claude_sdk_mcp_bundle(
    tools: Sequence[Any],
    exec_ctx: Any,
    server_key: str = "opendv",
) -> tuple[dict[str, Any], list[str], dict[str, dict[str, Any]]]:
    """
    Package AgentToolDefinitions into:
      1. `mcp_servers` mapping for ClaudeAgentOptions(mcp_servers=...)
      2. `qualified_tool_names` (`["mcp__opendv__<name>", ...]`) for ClaudeAgentOptions(allowed_tools=...)
      3. `raw_specs` dictionary for unit/contract testing without spawning a subprocess.
    """
    raw_specs: dict[str, dict[str, Any]] = {}
    qualified_names: list[str] = []
    sdk_tools: list[Any] = []
    handlers_by_name: dict[str, Callable[[dict[str, Any]], Any]] = {}

    try:
        from claude_agent_sdk import create_sdk_mcp_server, tool as claude_tool_decorator  # type: ignore[import-not-found]
        has_claude_sdk = True
    except ImportError:
        has_claude_sdk = False

    for t_def in tools:
        async def _bound_handler(args: dict[str, Any], td: Any = t_def) -> dict[str, Any]:
            try:
                result = await td.invoke(args, exec_ctx)
                return {
                    "content": [{"type": "text", "text": result.model_dump_json()}],
                    "is_error": False,
                }
            except Exception as exc:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({"error": type(exc).__name__, "detail": str(exc)}),
                        }
                    ],
                    "is_error": True,
                }

        qual_name = f"mcp__{server_key}__{t_def.name}"
        qualified_names.append(qual_name)
        handlers_by_name[qual_name] = _bound_handler
        raw_specs[qual_name] = {
            "name": t_def.name,
            "qualified_name": qual_name,
            "description": t_def.description,
            "input_schema": t_def.json_schema(),
        }

        if has_claude_sdk:
            decorated = claude_tool_decorator(
                t_def.name,
                t_def.description,
                t_def.json_schema(),
            )(_bound_handler)
            sdk_tools.append(decorated)

    if has_claude_sdk:
        mcp_server = create_sdk_mcp_server(name=server_key, version="1.0.0", tools=sdk_tools)
    else:
        mcp_server = {
            "type": "sdk_in_process_stub",
            "name": server_key,
            "tools": qualified_names,
            "handlers": handlers_by_name,
        }

    return {server_key: mcp_server}, qualified_names, raw_specs
```

---

## 4. Concrete SDK Adapters (`AntigravityHarnessAdapter` & `ClaudeAgentHarnessAdapter`)

### 4.1 Adapter 1: `AntigravityHarnessAdapter` (`adapters/antigravity_adapter.py`)

```python
from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from typing import Any


DEFAULT_AGY_MODEL_MAP: dict[ModelTier, str] = {
    ModelTier.HIGH_REASONING: os.environ.get("OPENDV_AGY_HIGH_MODEL", "gemini-4-argon-high"),
    ModelTier.FAST_TIER: os.environ.get("OPENDV_AGY_FAST_MODEL", "gemini-3.8-flash-high"),
}


class AntigravityHarnessAdapter(BaseVaultAgentHarness):
    """
    Production Adapter 1: Google Antigravity Python SDK (`google.antigravity`).
    Supports `LocalAgentConfig` (Gemini API / Vertex AI) with 9-tier safety policies
    and structured output.
    """

    def __init__(self, config: HarnessConfig, *, sdk_module: Any | None = None) -> None:
        super().__init__(config)
        self._sdk_override = sdk_module

    @property
    def provider_name(self) -> str:
        return "antigravity"

    def resolve_model(
        self,
        tier: ModelTier,
        persona: AgentPersona = AgentPersona.VAULT_FIRST_MATE,
    ) -> str:
        del persona
        tier_enum = ModelTier(tier)
        if tier_enum in self._config.model_overrides:
            return str(self._config.model_overrides[tier_enum])
        return DEFAULT_AGY_MODEL_MAP.get(
            tier_enum,
            DEFAULT_AGY_MODEL_MAP[ModelTier.HIGH_REASONING],
        )

    def _load_sdk(self) -> Any:
        if self._sdk_override is not None:
            return self._sdk_override
        import google.antigravity as agy_sdk  # type: ignore[import-not-found]
        return agy_sdk

    def _build_safety_policies(self, agy_policy: Any, spec: AgentTaskSpec[Any]) -> list[Any]:
        sbx = self._config.sandbox
        policies: list[Any] = [
            agy_policy.deny_all(),
            agy_policy.workspace_only([str(self._config.workspace_dir)]),
        ]
        for tool_name in sbx.allowed_builtin_tools:
            policies.append(agy_policy.allow(tool_name))

        if not sbx.read_only_workspace:
            policies.append(agy_policy.allow("create_file"))
            policies.append(agy_policy.allow("edit_file"))
        else:
            policies.append(agy_policy.deny("create_file"))
            policies.append(agy_policy.deny("edit_file"))

        if sbx.allow_shell_execution:
            blocked = sbx.blocked_shell_substrings
            policies.append(
                agy_policy.deny(
                    "run_command",
                    when=lambda args: any(b in str(args.get("CommandLine", "")) for b in blocked),
                    name="deny_dangerous_shell_commands",
                )
            )
            policies.append(agy_policy.allow("run_command"))
        else:
            policies.append(agy_policy.deny("run_command"))

        for tool_def in spec.tools:
            policies.append(agy_policy.allow(tool_def.name))

        return policies

    async def _stream_provider_task(
        self,
        spec: AgentTaskSpec[OutputT],
        ctx: ToolExecutionContext,
        model_id: str,
    ) -> AsyncIterator[HarnessStreamEvent]:
        agy = self._load_sdk()
        agy_types = agy.types
        agy_hooks = agy.hooks.hooks
        agy_policy = agy.hooks.policy
        bridged_tools = [to_antigravity_tool_callable(td, ctx) for td in spec.tools]

        def make_event(kind: HarnessEventKind, **kwargs: Any) -> HarnessStreamEvent:
            return self._make_event(spec, ctx, kind, **kwargs)

        event_queue: asyncio.Queue[HarnessStreamEvent | None] = asyncio.Queue()

        @agy_hooks.post_tool_call
        async def _on_post_tool_call(data: Any) -> None:
            parsed = data if isinstance(data, dict) else {"raw_output": str(data)}
            await event_queue.put(make_event(HarnessEventKind.TOOL_CALL_RESULT, tool_result=parsed))
            for obs_ev in ctx.pop_unstreamed_events():
                await event_queue.put(
                    make_event(HarnessEventKind.OBSERVABILITY_EVENT, obs_event=obs_ev)
                )
                if obs_ev.get("status") == "ASK-USER":
                    await event_queue.put(
                        make_event(HarnessEventKind.STEWARD_HOLD, obs_event=obs_ev)
                    )

        @agy_hooks.on_tool_error
        async def _on_tool_error(err: Exception) -> str | None:
            await event_queue.put(
                make_event(
                    HarnessEventKind.TOOL_CALL_ERROR,
                    tool_result={"error": type(err).__name__, "detail": str(err)},
                )
            )
            return f"[Tool Execution Error ({type(err).__name__}): {err}]"

        subagent_configs = [
            agy_types.SubagentConfig(
                name=sub.name,
                description=sub.description,
                capabilities=agy_types.SubagentCapabilities(
                    agent_behavior=agy_types.AgentBehavior.AUTONOMOUS,
                    enabled_tools=list(sub.allowed_tool_names),
                    allowed_subagents=list(sub.allowed_subagents),
                ),
            )
            for sub in spec.subagents
        ]

        budget_cfg_cls = getattr(agy_types, "BudgetConfig", None)
        budget_cfg = (
            budget_cfg_cls(
                max_turns=self._config.budget.max_turns,
                max_cost_usd=self._config.budget.max_cost_usd,
            )
            if budget_cfg_cls is not None
            else {
                "max_turns": self._config.budget.max_turns,
                "max_cost_usd": self._config.budget.max_cost_usd,
            }
        )

        agent_cfg = agy.LocalAgentConfig(
            model=model_id,
            system_instructions=agy_types.TemplatedSystemInstructions(
                identity=spec.system_instructions,
            ),
            tools=bridged_tools,
            subagents=subagent_configs,
            capabilities=agy_types.CapabilitiesConfig(
                agent_behavior=agy_types.AgentBehavior.AUTONOMOUS,
                enable_subagents=bool(subagent_configs),
                max_subagent_depth=3 if subagent_configs else 1,
                allowed_subagents=[s.name for s in spec.subagents],
            ),
            policies=self._build_safety_policies(agy_policy, spec),
            hooks=[_on_post_tool_call, _on_tool_error],
            workspaces=[str(self._config.workspace_dir)],
            app_data_dir=str(self._config.app_data_dir),
            save_dir=str(self._config.save_dir),
            env=dict(self._config.env),
            budget_config=budget_cfg,
            response_schema=spec.output_schema,
            conversation_id=spec.resume_session_id,
        )

        yield make_event(HarnessEventKind.SESSION_START)

        async with agy.Agent(agent_cfg) as agent:
            response = await agent.chat(spec.prompt)
            self._active_cancels[spec.task_id] = response.cancel

            async def _pump_thoughts() -> None:
                async for thought in response.thoughts:
                    await event_queue.put(
                        make_event(HarnessEventKind.THOUGHT_DELTA, delta_text=str(thought))
                    )

            async def _pump_tool_calls() -> None:
                async for call in response.tool_calls:
                    await event_queue.put(
                        make_event(
                            HarnessEventKind.TOOL_CALL_START,
                            tool_name=str(call.name),
                            tool_args=dict(call.args) if isinstance(call.args, dict) else {},
                        )
                    )

            async def _pump_tokens() -> None:
                async for token in response:
                    await event_queue.put(
                        make_event(HarnessEventKind.TEXT_DELTA, delta_text=str(token))
                    )

            async def _run_pumps() -> None:
                try:
                    async with asyncio.TaskGroup() as tg:
                        tg.create_task(_pump_thoughts())
                        tg.create_task(_pump_tool_calls())
                        tg.create_task(_pump_tokens())
                finally:
                    await event_queue.put(None)

            pump_task = asyncio.create_task(_run_pumps())
            try:
                while True:
                    item = await event_queue.get()
                    if item is None:
                        break
                    yield item
                await pump_task
            finally:
                self._active_cancels.pop(spec.task_id, None)
                if not pump_task.done():
                    pump_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await pump_task

            for obs_ev in ctx.pop_unstreamed_events():
                yield make_event(HarnessEventKind.OBSERVABILITY_EVENT, obs_event=obs_ev)
                if obs_ev.get("status") == "ASK-USER":
                    yield make_event(HarnessEventKind.STEWARD_HOLD, obs_event=obs_ev)

            structured_data = (
                await response.structured_output() if spec.output_schema is not None else None
            )
            yield make_event(
                HarnessEventKind.TURN_COMPLETE,
                tool_result={
                    "session_id": getattr(agent, "conversation_id", spec.task_id),
                    "structured_output": structured_data,
                },
            )
```

### 4.2 Adapter 2: `ClaudeAgentHarnessAdapter` (`adapters/claude_adapter.py`)

```python
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any


DEFAULT_CLAUDE_MODEL_MAP: dict[ModelTier, str] = {
    ModelTier.HIGH_REASONING: os.environ.get("OPENDV_CLAUDE_HIGH_MODEL", "claude-opus-5-5-high"),
    ModelTier.FAST_TIER: os.environ.get("OPENDV_CLAUDE_FAST_MODEL", "claude-sonnet-5-5-medium"),
}


class ClaudeAgentHarnessAdapter(BaseVaultAgentHarness):
    """
    Production Adapter 2: Anthropic Claude Agent SDK for Python (`claude-agent-sdk`).
    Uses `ClaudeSDKClient`, `ClaudeAgentOptions`, `@tool` + `create_sdk_mcp_server`,
    `AgentDefinition` subagents, `HookMatcher` guardrails, and `output_format` JSON Schema.
    """

    def __init__(self, config: HarnessConfig, *, sdk_module: Any | None = None) -> None:
        super().__init__(config)
        self._sdk_override = sdk_module

    @property
    def provider_name(self) -> str:
        return "claude"

    def resolve_model(
        self,
        tier: ModelTier,
        persona: AgentPersona = AgentPersona.VAULT_FIRST_MATE,
    ) -> str:
        del persona
        tier_enum = ModelTier(tier)
        if tier_enum in self._config.model_overrides:
            return str(self._config.model_overrides[tier_enum])
        return DEFAULT_CLAUDE_MODEL_MAP.get(
            tier_enum,
            DEFAULT_CLAUDE_MODEL_MAP[ModelTier.HIGH_REASONING],
        )

    def _load_sdk(self) -> Any:
        if self._sdk_override is not None:
            return self._sdk_override
        import claude_agent_sdk  # type: ignore[import-not-found]
        return claude_agent_sdk

    @staticmethod
    def strip_mcp_prefix(tool_name: str) -> str:
        if tool_name.startswith("mcp__opendv__"):
            return tool_name.removeprefix("mcp__opendv__")
        return tool_name

    async def _stream_provider_task(
        self,
        spec: AgentTaskSpec[OutputT],
        ctx: ToolExecutionContext,
        model_id: str,
    ) -> AsyncIterator[HarnessStreamEvent]:
        cas = self._load_sdk()
        sbx = self._config.sandbox
        mcp_servers, qualified_tool_names, _ = build_claude_sdk_mcp_bundle(spec.tools, ctx)

        def make_event(kind: HarnessEventKind, **kwargs: Any) -> HarnessStreamEvent:
            return self._make_event(spec, ctx, kind, **kwargs)

        allowed_tools: list[str] = ["Read", "Glob", "Grep", *qualified_tool_names]
        disallowed_tools: list[str] = []
        if sbx.read_only_workspace:
            disallowed_tools.extend(["Write", "Edit"])
        else:
            allowed_tools.extend(["Write", "Edit"])

        blocked_substrings = sbx.blocked_shell_substrings

        async def _guard_bash_hook(
            input_data: dict[str, Any],
            tool_use_id: str | None = None,
            context: Any = None,
        ) -> dict[str, Any]:
            del tool_use_id, context
            tool_input = input_data.get("tool_input") or {}
            cmd = str(tool_input.get("command", ""))
            if any(b in cmd for b in blocked_substrings):
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Blocked dangerous shell command substring.",
                    }
                }
            return {}

        hook_matcher_cls = getattr(cas, "HookMatcher", None)
        pre_tool_hooks: list[Any] = []
        if sbx.allow_shell_execution:
            allowed_tools.append("Bash")
            pre_tool_hooks.append(
                hook_matcher_cls(matcher="Bash", hooks=[_guard_bash_hook])
                if hook_matcher_cls is not None
                else {"matcher": "Bash", "hooks": [_guard_bash_hook]}
            )
        else:
            disallowed_tools.append("Bash")

        if spec.subagents:
            allowed_tools.append("Agent")

        agents_dict = {
            sub.name: cas.AgentDefinition(
                description=sub.description,
                prompt=sub.system_instructions,
                tools=[
                    "Read",
                    "Glob",
                    "Grep",
                    *([] if sub.read_only_workspace else ["Write", "Edit"]),
                    *[f"mcp__opendv__{t}" for t in sub.allowed_tool_names],
                ],
                model=self.resolve_model(sub.model_tier, sub.persona),
            )
            for sub in spec.subagents
        }

        output_format_cfg = (
            {"type": "json_schema", "schema": spec.output_schema.model_json_schema()}
            if spec.output_schema is not None
            else None
        )

        options = cas.ClaudeAgentOptions(
            system_prompt=spec.system_instructions,
            model=model_id,
            cwd=str(self._config.workspace_dir),
            env=dict(self._config.env),
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            permission_mode="dontAsk",
            hooks={"PreToolUse": pre_tool_hooks} if pre_tool_hooks else {},
            max_turns=self._config.budget.max_turns,
            max_budget_usd=self._config.budget.max_cost_usd,
            resume=spec.resume_session_id,
            mcp_servers=mcp_servers,
            agents=agents_dict,
            output_format=output_format_cfg,
        )

        yield make_event(HarnessEventKind.SESSION_START)
        session_id = spec.resume_session_id or spec.task_id

        async with cas.ClaudeSDKClient(options=options) as client:
            self._active_cancels[spec.task_id] = client.interrupt
            try:
                await client.query(spec.prompt)
                async for message in client.receive_response():
                    msg_type = type(message).__name__
                    for block in getattr(message, "content", []) or []:
                        b_type = type(block).__name__
                        if b_type == "ThinkingBlock":
                            yield make_event(
                                HarnessEventKind.THOUGHT_DELTA,
                                delta_text=str(getattr(block, "thinking", "")),
                            )
                        elif b_type == "TextBlock":
                            yield make_event(
                                HarnessEventKind.TEXT_DELTA,
                                delta_text=str(getattr(block, "text", "")),
                            )
                        elif b_type == "ToolUseBlock":
                            raw_name = str(getattr(block, "name", ""))
                            yield make_event(
                                HarnessEventKind.TOOL_CALL_START,
                                tool_name=self.strip_mcp_prefix(raw_name),
                                tool_call_id=getattr(block, "id", None),
                                tool_args=dict(getattr(block, "input", {}) or {}),
                            )
                        elif b_type == "ToolResultBlock":
                            is_err = bool(getattr(block, "is_error", False))
                            raw_content = getattr(block, "content", None)
                            result_payload: dict[str, Any] = (
                                raw_content
                                if isinstance(raw_content, dict)
                                else {"content": raw_content}
                            )
                            yield make_event(
                                HarnessEventKind.TOOL_CALL_ERROR
                                if is_err
                                else HarnessEventKind.TOOL_CALL_RESULT,
                                tool_call_id=getattr(block, "tool_use_id", None),
                                tool_result=result_payload,
                            )
                    for obs_ev in ctx.pop_unstreamed_events():
                        yield make_event(HarnessEventKind.OBSERVABILITY_EVENT, obs_event=obs_ev)
                        if obs_ev.get("status") == "ASK-USER":
                            yield make_event(HarnessEventKind.STEWARD_HOLD, obs_event=obs_ev)
                    if msg_type == "ResultMessage":
                        yield make_event(
                            HarnessEventKind.TURN_COMPLETE,
                            tool_result={
                                "session_id": str(getattr(message, "session_id", session_id)),
                                "structured_output": getattr(message, "structured_output", None),
                            },
                        )
                for obs_ev in ctx.pop_unstreamed_events():
                    yield make_event(HarnessEventKind.OBSERVABILITY_EVENT, obs_event=obs_ev)
                    if obs_ev.get("status") == "ASK-USER":
                        yield make_event(HarnessEventKind.STEWARD_HOLD, obs_event=obs_ev)
            finally:
                self._active_cancels.pop(spec.task_id, None)
```

---

## 5. Persona Capability Matrix & Model-Tier Routing Policy

| Persona (`AgentPersona`) | Default `ModelTier` | Filesystem Sandbox (`SandboxPolicy`) | Authorized Domain Tools | Allowed Subagents | Primary Stages & Workbench Views |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`vault-first-mate`** *(Read-Only Supervisor & Ontology Steward)* | `HIGH_REASONING` (`argon`) | `read_only_workspace=True`, `allow_shell_execution=False` | `profile_source_feed`, `run_axi_dv_gates`, `execute_duckdb_sandbox`, `compile_canonical_ir`, `emit_observability_event`, `request_steward_decision` | `scout-source`, `scout-unstructured`, `ship-vault` | `STAGE_00_ORCHESTRATION` (`WB-00`, `WB-01`) |
| **`scout-source`** *(Bronze Statistical Profiler & Drift Detective)* | `FAST_TIER` (`flash`) | `read_only_workspace=True`, `allow_shell_execution=False` | `profile_source_feed`, `emit_observability_event` | None (`leaf scout`) | `STAGE_01_SCOUT_SOURCE` (`WB-02`, `WB-09`) |
| **`scout-unstructured`** *(Span-Grounded NER & 3-Tier ER Scout)* | `FAST_TIER` (`flash`) | `read_only_workspace=True`, `allow_shell_execution=False` | `extract_pdf_spans`, `emit_observability_event`, `request_steward_decision` | None (`leaf scout`) | `STAGE_02_SCOUT_UNSTRUCTURED` (`WB-03`) |
| **`ship-vault`** *(3-Pass CoT Modeller & Closed-Loop TOON Repair Worker)* | `HIGH_REASONING` (`argon`) | `read_only_workspace=False` *(isolated worktree)*, `allow_shell_execution=False` | `apply_ast_codemod`, `run_axi_dv_gates`, `execute_duckdb_sandbox`, `compile_canonical_ir`, `emit_observability_event`, `request_steward_decision` | None (`isolated worker`) | `STAGE_03` .. `STAGE_07` (`WB-04`..`WB-08`, `WB-10`, `WB-11`) |
