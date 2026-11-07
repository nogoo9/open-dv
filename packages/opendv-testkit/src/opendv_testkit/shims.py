"""Deterministic vendor SDK shims (Google Antigravity & Anthropic Claude Agent SDK).

Provides in-memory emulators of external vendor SDKs for offline unit and integration testing.
"""

from __future__ import annotations

import types
import uuid
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from opendv_testkit.synthesis import (
    resolve_schema_class,
    synthesize_minimal_schema_instance,
)


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
                return synthesize_minimal_schema_instance(schema, prompt=self._prompt)
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

            # Resolve structured output directly from output_format schema if present
            structured_out = None
            output_format = getattr(self._options, "output_format", None)
            if isinstance(output_format, dict):
                schema_dict = output_format.get("schema")
                if isinstance(schema_dict, dict):
                    title = schema_dict.get("title")
                    if title:
                        schema_cls = resolve_schema_class(title)
                        if schema_cls is not None:
                            structured_out = synthesize_minimal_schema_instance(
                                schema_cls,
                                prompt=self._prompt,
                            )

            yield ResultMessage(
                session_id=sess_id,
                structured_output=structured_out,
            )

    return types.SimpleNamespace(
        _is_deterministic_shim=True,
        HookMatcher=_HookMatcher,
        AgentDefinition=_AgentDefinition,
        ClaudeAgentOptions=_ClaudeAgentOptions,
        ClaudeSDKClient=_ClaudeSDKClient,
    )
