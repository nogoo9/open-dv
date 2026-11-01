"""Deterministic Neuro-Symbolic Adapter for the Open-DV Agent Harness.

Provides an offline, zero-network, reproducible `VaultAgentHarness` implementation
(`provider="deterministic"` / `provider="deterministic-stub"`) as well as deterministic
SDK shims for local/CI verification of `antigravity` and `claude` adapters when external
vendor SDK packages are not installed.

Conforms to docs/03-verification-and-agentic-architecture.md,
docs/06-explainability-and-observability-spec.md,
and docs/09-agnostic-agent-harness-and-sdk-spec.md.
"""

from __future__ import annotations

import asyncio
import types
import uuid
from collections.abc import AsyncIterator
from typing import Any

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
from opendv_agent_harness.routing import DEFAULT_DETERMINISTIC_MODEL_MAP


class DeterministicHarnessAdapter(BaseVaultAgentHarness):
    """Deterministic Adapter for reproducible neuro-symbolic pipeline execution and CI."""

    def __init__(self, config: HarnessConfig, *, sdk_module: Any | None = None) -> None:
        super().__init__(config)
        self._sdk_override = sdk_module

    @property
    def provider_name(self) -> str:
        """Return the configured deterministic provider identifier."""
        if self._config.provider in {"deterministic", "deterministic-stub"}:
            return str(self._config.provider)
        return "deterministic"

    def resolve_model(
        self,
        tier: ModelTier,
        persona: AgentPersona = AgentPersona.VAULT_FIRST_MATE,
    ) -> str:
        """Resolve logical ModelTier ('argon' or 'flash') to a deterministic model ID."""
        del persona
        tier_enum = ModelTier(tier)
        if tier_enum in self._config.model_overrides:
            return str(self._config.model_overrides[tier_enum])
        return DEFAULT_DETERMINISTIC_MODEL_MAP.get(
            tier_enum,
            DEFAULT_DETERMINISTIC_MODEL_MAP[ModelTier.HIGH_REASONING],
        )

    async def _stream_provider_task(
        self,
        spec: AgentTaskSpec[OutputT],
        ctx: ToolExecutionContext,
        model_id: str,
    ) -> AsyncIterator[HarnessStreamEvent]:
        """Yield deterministic HarnessStreamEvent items for `spec`."""
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

            structured_out: Any = None
            if spec.output_schema is not None:
                try:
                    structured_out = spec.output_schema.model_validate({})
                except Exception:
                    structured_out = None

            session_id = spec.resume_session_id or f"det-sess-{spec.task_id}"
            yield self._make_event(
                spec,
                ctx,
                HarnessEventKind.TURN_COMPLETE,
                tool_result={
                    "session_id": session_id,
                    "structured_output": structured_out,
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
            if schema is not None:
                try:
                    return schema.model_validate({})
                except Exception:
                    return None
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
