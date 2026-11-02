"""Deterministic Neuro-Symbolic Mock Adapter for Open-DV Agent Harness Tests.

Provides an internal test-only, offline, zero-network `VaultAgentHarness` mock implementation
(`DeterministicHarnessAdapter`) as well as deterministic SDK shims for unit and integration
test suites when external vendor SDK packages are not installed.

Conforms to docs/03-verification-and-agentic-architecture.md,
docs/06-explainability-and-observability-spec.md,
and docs/09-agnostic-agent-harness-and-sdk-spec.md.
"""

from __future__ import annotations

import asyncio
import enum
import types
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel

from opendv_agent_harness.models import (
    AgentPersona,
    AgentTaskSpec,
    HarnessConfig,
    HarnessEventKind,
    HarnessStreamEvent,
    ModelTier,
    OutputT,
    ToolExecutionContext,
)
from opendv_agent_harness.protocol import BaseVaultAgentHarness
from opendv_agent_harness.routing import DEFAULT_DETERMINISTIC_MODEL_MAP, resolve_provider_model


def _validate_candidate_with_schema(schema_cls: type[BaseModel], candidate: Any) -> Any:
    """Validate `candidate` (model instance, dict, or JSON string) against `schema_cls`."""
    if isinstance(candidate, schema_cls):
        return candidate
    if isinstance(candidate, BaseModel):
        return schema_cls.model_validate(candidate.model_dump(mode="json"))
    if isinstance(candidate, dict):
        return schema_cls.model_validate(candidate)
    if isinstance(candidate, str):
        return schema_cls.model_validate_json(candidate)
    return candidate


def _default_for_annotation(annotation: Any, *, non_empty_collections: bool = False) -> Any:
    """Construct a minimal valid default value for a type annotation."""
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Literal and args:
        return args[0]
    if origin in (list, tuple, set, frozenset):
        if non_empty_collections and args:
            elem = _default_for_annotation(args[0], non_empty_collections=True)
            return [elem] if elem is not None else []
        return []
    if origin is dict:
        return {}
    if origin is not None and args:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) < len(args) and not non_empty_collections:
            return None
        if non_none:
            return _default_for_annotation(
                non_none[0],
                non_empty_collections=non_empty_collections,
            )

    if isinstance(annotation, type):
        if issubclass(annotation, enum.Enum):
            members = list(annotation)
            return members[0].value if members else "default"
        if issubclass(annotation, BaseModel):
            inst = _synthesize_minimal_schema_instance(annotation)
            return inst.model_dump(mode="json") if inst is not None else {}
        if issubclass(annotation, bool):
            return True
        if issubclass(annotation, int):
            return 1
        if issubclass(annotation, float):
            return 1.0
        if issubclass(annotation, str):
            return "deterministic"
    return "deterministic"


_PIPELINE_STAGE_SCHEMA_NAMES = frozenset(
    {
        "Stage00OrchestrationPlanOutput",
        "Stage01SourceDiscoveryOutput",
        "Stage02UnstructuredDiscoveryOutput",
        "Stage03ColumnTriageOutput",
        "Stage04ContractSynthesisOutput",
        "NoMistakeDataVault2ContractV5",
    }
)


def _synthesize_minimal_schema_instance(
    schema_cls: type[BaseModel],
    prompt: str = "",
) -> Any:
    """Synthesize a valid instance of `schema_cls` from `prompt` context or field defaults."""
    if schema_cls.__name__ in _PIPELINE_STAGE_SCHEMA_NAMES:
        try:
            from opendv_agent_harness.pipeline import synthesize_stage_output_from_prompt

            stage_out = synthesize_stage_output_from_prompt(schema_cls, prompt)
            if stage_out is not None:
                return stage_out
        except Exception:
            pass

    try:
        return schema_cls.model_validate({})
    except Exception:
        pass

    for non_empty in (False, True):
        payload: dict[str, Any] = {}
        for field_name, field_info in schema_cls.model_fields.items():
            if not field_info.is_required() and not non_empty:
                continue
            payload[field_name] = _default_for_annotation(
                field_info.annotation,
                non_empty_collections=non_empty,
            )
        try:
            return schema_cls.model_validate(payload)
        except Exception:
            continue

    return None


class DeterministicHarnessAdapter(BaseVaultAgentHarness):
    """Test-only deterministic mock adapter for offline unit and integration tests."""

    def __init__(
        self,
        config: HarnessConfig,
        *,
        sdk_module: Any | None = None,
        structured_output_factory: Callable[[AgentTaskSpec[Any]], Any] | None = None,
    ) -> None:
        super().__init__(config)
        self._sdk_override = sdk_module
        self.structured_output_factory: Callable[[AgentTaskSpec[Any]], Any] | None = (
            structured_output_factory
            or config.extra_provider_options.get("structured_output_factory")
        )

    @property
    def _sdk_module(self) -> Any | None:
        return self._sdk_override

    @_sdk_module.setter
    def _sdk_module(self, value: Any | None) -> None:
        self._sdk_override = value

    @property
    def provider_name(self) -> str:
        """Return the configured deterministic provider identifier."""
        if self._config.provider in {"deterministic", "deterministic-stub"}:
            return str(self._config.provider)
        return "deterministic"

    def resolve_model(
        self,
        tier: ModelTier,
        persona: AgentPersona | None = None,
    ) -> str:
        """Resolve logical ModelTier ('reasoning'/'fast') and AgentPersona to a deterministic ID."""
        return resolve_provider_model(
            self.provider_name,
            tier,
            overrides=self._config.model_overrides,
            persona=persona,
            agent_models=self._config.agent_models,
        )

    def _resolve_structured_output(self, spec: AgentTaskSpec[OutputT]) -> Any:
        """Resolve a validated `spec.output_schema` instance from metadata, factory, or defaults."""
        if spec.output_schema is None:
            return None

        spec_meta = getattr(spec, "metadata", None)
        if (
            isinstance(spec_meta, dict)
            and spec_meta.get("deterministic_structured_output") is not None
        ):
            return _validate_candidate_with_schema(
                spec.output_schema,
                spec_meta["deterministic_structured_output"],
            )

        ctx_override = spec.context_refs.get(
            "deterministic_structured_output"
        ) or spec.context_refs.get("deterministic_structured_output_json")
        if isinstance(ctx_override, str) and ctx_override.strip():
            return _validate_candidate_with_schema(spec.output_schema, ctx_override)

        factory = self.structured_output_factory or self._config.extra_provider_options.get(
            "structured_output_factory"
        )
        if callable(factory):
            produced = factory(spec)
            if produced is not None:
                return _validate_candidate_with_schema(spec.output_schema, produced)

        cfg_override = self._config.extra_provider_options.get("deterministic_structured_output")
        if cfg_override is not None:
            return _validate_candidate_with_schema(spec.output_schema, cfg_override)

        return _synthesize_minimal_schema_instance(spec.output_schema, prompt=spec.prompt)

    async def _stream_provider_task(
        self,
        spec: AgentTaskSpec[OutputT],
        ctx: ToolExecutionContext,
        model_id: str,
    ) -> AsyncIterator[HarnessStreamEvent]:
        """Yield deterministic HarnessStreamEvent items for `spec`."""
        model_id = (
            spec.model_override or model_id or self.resolve_model(spec.model_tier, spec.persona)
        )
        subagent_models: dict[str, str] = {
            sub.name: (sub.model_override or self.resolve_model(sub.model_tier, sub.persona))
            for sub in spec.subagents
        }
        cancelled = False

        async def _cancel() -> None:
            nonlocal cancelled
            cancelled = True

        self._active_cancels[spec.task_id] = _cancel
        try:
            yield self._make_event(spec, ctx, HarnessEventKind.SESSION_START)
            await asyncio.sleep(0)
            if cancelled:
                yield self._make_event(spec, ctx, HarnessEventKind.CANCELLED)
                return

            thought_text = (
                f"[{self.provider_name}:{model_id}] Executing deterministic "
                f"{spec.stage_id.value} as persona '{spec.persona.value}' "
                f"for view {spec.workbench_view_id.value} ({spec.viz_id.value})."
            )
            yield self._make_event(
                spec,
                ctx,
                HarnessEventKind.THOUGHT_DELTA,
                delta_text=thought_text,
            )

            # If emit_observability_event tool is registered, invoke it deterministically
            emit_tool = next(
                (t for t in spec.tools if t.name == "emit_observability_event"),
                None,
            )
            if emit_tool is not None:
                call_id = f"det-call-{uuid.uuid4().hex[:8]}"
                headline = (
                    f"Deterministic {spec.persona.value} checkpoint at "
                    f"{spec.stage_id.value} ({spec.workbench_view_id.value})."
                )
                tool_args: dict[str, Any] = {
                    "status": "PASS",
                    "headline": headline,
                    "business_impact": (
                        "Guarantees reproducible neuro-symbolic verification and "
                        "W3C trace continuity across all pipeline stages."
                    ),
                    "rule_ids_evaluated": ["HR-01"],
                    "summary_metrics": {
                        "persona": spec.persona.value,
                        "stage_id": spec.stage_id.value,
                        "model_id": model_id,
                    },
                }
                yield self._make_event(
                    spec,
                    ctx,
                    HarnessEventKind.TOOL_CALL_START,
                    tool_name=emit_tool.name,
                    tool_call_id=call_id,
                    tool_args=tool_args,
                )
                result_obj = await emit_tool.invoke(tool_args, ctx)
                result_dict = result_obj.model_dump(mode="json")
                yield self._make_event(
                    spec,
                    ctx,
                    HarnessEventKind.TOOL_CALL_RESULT,
                    tool_name=emit_tool.name,
                    tool_call_id=call_id,
                    tool_result=result_dict,
                )
                for obs_ev in ctx.pop_unstreamed_events():
                    yield self._make_event(
                        spec,
                        ctx,
                        HarnessEventKind.OBSERVABILITY_EVENT,
                        obs_event=obs_ev,
                    )
                    if obs_ev.get("status") == "ASK-USER":
                        yield self._make_event(
                            spec,
                            ctx,
                            HarnessEventKind.STEWARD_HOLD,
                            obs_event=obs_ev,
                        )

            summary_text = (
                f"Completed {spec.stage_id.value} ({spec.persona.value}) using {model_id}."
            )
            yield self._make_event(
                spec,
                ctx,
                HarnessEventKind.TEXT_DELTA,
                delta_text=summary_text,
            )

            structured_out = self._resolve_structured_output(spec)

            session_id = spec.resume_session_id or f"det-sess-{spec.task_id}"
            yield self._make_event(
                spec,
                ctx,
                HarnessEventKind.TURN_COMPLETE,
                tool_result={
                    "session_id": session_id,
                    "structured_output": structured_out,
                    "subagent_models": subagent_models,
                    "usage": {
                        "input_tokens": 128,
                        "cached_input_tokens": 32,
                        "output_tokens": 64,
                        "reasoning_tokens": 32,
                        "total_tokens": 224,
                        "model_calls": 1,
                        "cost_usd": 0.0,
                    },
                },
            )
        finally:
            self._active_cancels.pop(spec.task_id, None)


def build_deterministic_agy_sdk_shim() -> Any:
    """Build an in-memory `google.antigravity` SDK shim for offline execution."""

    class _TemplatedSystemInstructions:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class _SubagentCapabilities:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class _SubagentConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.__dict__.update(kwargs)

    class _CapabilitiesConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class _BudgetConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class _AgentBehavior:
        AUTONOMOUS = "AUTONOMOUS"

    class _PolicyModule:
        @staticmethod
        def deny_all() -> dict[str, Any]:
            return {"rule": "deny_all"}

        @staticmethod
        def workspace_only(paths: list[str]) -> dict[str, Any]:
            return {"rule": "workspace_only", "paths": paths}

        @staticmethod
        def allow(tool_name: str, **kwargs: Any) -> dict[str, Any]:
            return {"rule": "allow", "tool": tool_name, **kwargs}

        @staticmethod
        def deny(tool_name: str, **kwargs: Any) -> dict[str, Any]:
            return {"rule": "deny", "tool": tool_name, **kwargs}

    class _HooksDecorator:
        @staticmethod
        def post_tool_call(fn: Any) -> Any:
            fn._is_post_tool_call = True
            return fn

        @staticmethod
        def on_tool_error(fn: Any) -> Any:
            fn._is_on_tool_error = True
            return fn

    class _LocalAgentConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    class _FakeResponse:
        def __init__(self, cfg: _LocalAgentConfig, prompt: str) -> None:
            self._cfg = cfg
            self._prompt = prompt
            self.usage = {
                "input_tokens": 140,
                "cached_input_tokens": 20,
                "output_tokens": 70,
                "reasoning_tokens": 30,
                "total_tokens": 240,
                "model_calls": 1,
                "cost_usd": 0.0,
            }

        async def cancel(self) -> None:
            return None

        @property
        async def thoughts(self) -> AsyncIterator[str]:
            yield f"[antigravity:{getattr(self._cfg, 'model', 'agy')}] Analysing task prompt."

        @property
        async def tool_calls(self) -> AsyncIterator[Any]:
            tools = getattr(self._cfg, "tools", []) or []
            emit_fn = next(
                (t for t in tools if getattr(t, "__name__", "") == "emit_observability_event"),
                None,
            )
            if emit_fn is not None:
                args = {
                    "status": "PASS",
                    "headline": "Antigravity adapter deterministic checkpoint.",
                    "business_impact": "Verifies Antigravity SDK adapter pipeline integration.",
                    "rule_ids_evaluated": ["HR-01"],
                }
                yield types.SimpleNamespace(
                    id="agy-call-01",
                    name="emit_observability_event",
                    args=args,
                )
                res = await emit_fn(**args)
                for hook in getattr(self._cfg, "hooks", []) or []:
                    if getattr(hook, "_is_post_tool_call", False):
                        await hook(res)

        def __aiter__(self) -> AsyncIterator[str]:
            return self._tokens()

        async def _tokens(self) -> AsyncIterator[str]:
            yield "Antigravity deterministic turn complete."

        async def structured_output(self) -> Any:
            schema = getattr(self._cfg, "response_schema", None)
            if schema is not None and isinstance(schema, type) and issubclass(schema, BaseModel):
                return _synthesize_minimal_schema_instance(schema, prompt=self._prompt)
            return None

    class _Agent:
        def __init__(self, cfg: _LocalAgentConfig) -> None:
            self._cfg = cfg
            self.conversation_id = (
                getattr(cfg, "conversation_id", None) or f"agy-sess-{uuid.uuid4().hex[:8]}"
            )

        async def __aenter__(self) -> _Agent:
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

        async def chat(self, prompt: str) -> _FakeResponse:
            return _FakeResponse(self._cfg, prompt)

    agy_types = types.SimpleNamespace(
        TemplatedSystemInstructions=_TemplatedSystemInstructions,
        SubagentCapabilities=_SubagentCapabilities,
        SubagentConfig=_SubagentConfig,
        CapabilitiesConfig=_CapabilitiesConfig,
        BudgetConfig=_BudgetConfig,
        AgentBehavior=_AgentBehavior,
    )
    agy_hooks = types.SimpleNamespace(
        hooks=_HooksDecorator(),
        policy=_PolicyModule(),
    )
    return types.SimpleNamespace(
        types=agy_types,
        hooks=agy_hooks,
        LocalAgentConfig=_LocalAgentConfig,
        Agent=_Agent,
    )


def build_deterministic_claude_sdk_shim() -> Any:
    """Build an in-memory `claude_agent_sdk` shim for offline execution."""

    class ThinkingBlock:
        def __init__(self, thinking: str) -> None:
            self.thinking = thinking

    class TextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class AssistantMessage:
        def __init__(self, content: list[Any]) -> None:
            self.content = content

    class ResultMessage:
        def __init__(
            self,
            session_id: str,
            structured_output: Any = None,
            usage: dict[str, Any] | None = None,
        ) -> None:
            self.content: list[Any] = []
            self.session_id = session_id
            self.structured_output = structured_output
            self.usage = usage or {
                "input_tokens": 130,
                "cached_input_tokens": 25,
                "output_tokens": 65,
                "reasoning_tokens": 30,
                "total_tokens": 225,
                "model_calls": 1,
                "cost_usd": 0.0,
            }

    class _HookMatcher:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class _AgentDefinition:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.__dict__.update(kwargs)

    class _ClaudeAgentOptions:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    class _ClaudeSDKClient:
        def __init__(self, options: _ClaudeAgentOptions) -> None:
            self._options = options
            self._prompt = ""

        async def __aenter__(self) -> _ClaudeSDKClient:
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

        async def interrupt(self) -> None:
            return None

        async def query(self, prompt: str) -> None:
            self._prompt = prompt

        async def receive_response(self) -> AsyncIterator[Any]:
            model_name = getattr(self._options, "model", "claude")
            yield AssistantMessage(
                [
                    ThinkingBlock(f"[claude:{model_name}] Analysing task prompt."),
                    TextBlock("Claude deterministic turn complete."),
                ]
            )
            sess_id = (
                getattr(self._options, "resume", None) or f"claude-sess-{uuid.uuid4().hex[:8]}"
            )
            yield ResultMessage(session_id=sess_id)

    return types.SimpleNamespace(
        _is_deterministic_shim=True,
        HookMatcher=_HookMatcher,
        AgentDefinition=_AgentDefinition,
        ClaudeAgentOptions=_ClaudeAgentOptions,
        ClaudeSDKClient=_ClaudeSDKClient,
    )


__all__ = [
    "DEFAULT_DETERMINISTIC_MODEL_MAP",
    "DeterministicHarnessAdapter",
    "build_deterministic_agy_sdk_shim",
    "build_deterministic_claude_sdk_shim",
]
