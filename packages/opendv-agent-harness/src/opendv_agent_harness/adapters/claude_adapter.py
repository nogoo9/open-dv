"""Anthropic Claude Agent SDK (`claude_agent_sdk`) Adapter for Open-DV Agent Harness.

Conforms to docs/03-verification-and-agentic-architecture.md
and docs/06-explainability-and-observability-spec.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
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
from opendv_agent_harness.routing import DEFAULT_CLAUDE_MODEL_MAP
from opendv_agent_harness.tools.bridge import build_claude_sdk_mcp_bundle


class ProviderConfigurationError(RuntimeError, ImportError):
    """Raised when neither the vendor SDK nor REST API credentials are configured."""


def _strip_markdown_json_fences(raw_text: str) -> str:
    """Strip optional ```json ... ``` markdown fences around JSON text."""
    stripped = raw_text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def _allow_mock_sdk_shim(config: HarnessConfig) -> bool:
    """Return True only when mock SDK fallback is explicitly enabled via config or env."""
    if config.extra_provider_options.get("allow_mock_sdk_fallback") is True:
        return True
    env_flag = (
        config.env.get("OPENDV_ALLOW_MOCK_SDK_SHIM")
        or os.environ.get("OPENDV_ALLOW_MOCK_SDK_SHIM")
        or ""
    )
    return env_flag.strip().lower() in {"1", "true", "yes"}


class ClaudeAgentHarnessAdapter(BaseVaultAgentHarness):
    """Production Adapter 2: Anthropic Claude Agent SDK for Python (`claude-agent-sdk`).

    Uses `ClaudeSDKClient`, `ClaudeAgentOptions`, `@tool` + `create_sdk_mcp_server`,
    `AgentDefinition` subagents, `HookMatcher` guardrails, `output_format` JSON Schema,
    and Anthropic Messages REST API execution when `claude_agent_sdk` is absent but
    `ANTHROPIC_API_KEY` is configured.
    """

    def __init__(self, config: HarnessConfig, *, sdk_module: Any | None = None) -> None:
        super().__init__(config)
        self._sdk_override = sdk_module

    @property
    def _sdk_module(self) -> Any | None:
        return self._sdk_override

    @_sdk_module.setter
    def _sdk_module(self, value: Any | None) -> None:
        self._sdk_override = value

    @property
    def provider_name(self) -> str:
        """Return the canonical provider identifier 'claude'."""
        return "claude"

    def resolve_model(
        self,
        tier: ModelTier,
        persona: AgentPersona = AgentPersona.VAULT_FIRST_MATE,
    ) -> str:
        """Resolve logical ModelTier ('argon' or 'flash') to a Claude model ID."""
        del persona
        tier_enum = ModelTier(tier)
        if tier_enum in self._config.model_overrides:
            return str(self._config.model_overrides[tier_enum])
        return DEFAULT_CLAUDE_MODEL_MAP.get(
            tier_enum,
            DEFAULT_CLAUDE_MODEL_MAP[ModelTier.HIGH_REASONING],
        )

    def _resolve_api_key(self) -> str | None:
        """Resolve `ANTHROPIC_API_KEY` from config or process environment."""
        for candidate in (
            self._config.env.get("ANTHROPIC_API_KEY"),
            self._config.extra_provider_options.get("api_key"),
            os.environ.get("ANTHROPIC_API_KEY"),
        ):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None

    def _load_sdk(self) -> Any | None:
        """Lazily load `claude_agent_sdk`, return `None` for REST fallback, or fail closed."""
        if self._sdk_override is not None:
            return self._sdk_override
        try:
            import claude_agent_sdk  # type: ignore[import-not-found]

            return claude_agent_sdk
        except ImportError:
            if self._resolve_api_key() is not None:
                return None
            if _allow_mock_sdk_shim(self._config):
                from opendv_agent_harness.adapters.deterministic_adapter import (
                    build_deterministic_claude_sdk_shim,
                )

                return build_deterministic_claude_sdk_shim()
            raise ProviderConfigurationError(
                "Claude Agent SDK ('claude_agent_sdk') is not installed and "
                "ANTHROPIC_API_KEY is not present in the environment. "
                "Configure ANTHROPIC_API_KEY or install claude_agent_sdk."
            ) from None

    @staticmethod
    def strip_mcp_prefix(tool_name: str) -> str:
        """Strip the in-process MCP server prefix `mcp__opendv__` from tool names."""
        if tool_name.startswith("mcp__opendv__"):
            return tool_name.removeprefix("mcp__opendv__")
        return tool_name

    def _resolve_base_url(self) -> str:
        """Resolve Anthropic REST base URL from config, extra options, or environment."""
        for candidate in (
            self._config.base_url,
            self._config.env.get("ANTHROPIC_BASE_URL"),
            self._config.extra_provider_options.get("base_url"),
            os.environ.get("ANTHROPIC_BASE_URL"),
        ):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip().rstrip("/")
        return "https://api.anthropic.com"

    async def _stream_anthropic_rest_task(
        self,
        spec: AgentTaskSpec[OutputT],
        ctx: ToolExecutionContext,
        model_id: str,
        api_key: str,
    ) -> AsyncIterator[HarnessStreamEvent]:
        """Execute task against the Anthropic Messages REST API (`https://api.anthropic.com/v1/messages`)."""

        def make_event(kind: HarnessEventKind, **kwargs: Any) -> HarnessStreamEvent:
            return self._make_event(spec, ctx, kind, **kwargs)

        yield make_event(HarnessEventKind.SESSION_START)
        session_id = spec.resume_session_id or spec.task_id

        system_prompt = spec.system_instructions
        if spec.output_schema is not None:
            schema_json = json.dumps(spec.output_schema.model_json_schema())
            system_prompt = (
                f"{system_prompt}\n\n"
                f"Respond ONLY with valid JSON conforming to this JSON Schema:\n{schema_json}"
            )

        tools_by_name = {t.name: t for t in spec.tools}
        tools_payload: list[dict[str, Any]] = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.json_schema(),
            }
            for t in spec.tools
        ]

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": spec.prompt},
        ]

        base_url = self._resolve_base_url()
        url = f"{base_url}/messages" if base_url.endswith("/v1") else f"{base_url}/v1/messages"

        text_chunks: list[str] = []
        in_tok = 0
        cached_tok = 0
        out_tok = 0
        total_tok = 0
        model_calls = 0
        last_msg_id: str | None = None
        max_turns = max(1, int(self._config.budget.max_turns))

        for _turn in range(max_turns):
            request_payload: dict[str, Any] = {
                "model": model_id,
                "max_tokens": min(self._config.budget.max_output_tokens, 8192),
                "system": system_prompt,
                "messages": messages,
            }
            if tools_payload:
                request_payload["tools"] = tools_payload

            req = urllib.request.Request(
                url=url,
                data=json.dumps(request_payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                method="POST",
            )

            def _do_http_post(request_obj: urllib.request.Request = req) -> dict[str, Any]:
                with urllib.request.urlopen(
                    request_obj,
                    timeout=self._config.budget.timeout_seconds,
                ) as resp:
                    raw_bytes = resp.read()
                    return json.loads(raw_bytes.decode("utf-8"))  # type: ignore[no-any-return]

            resp_json = await asyncio.to_thread(_do_http_post)
            model_calls += 1
            if resp_json.get("id"):
                last_msg_id = str(resp_json["id"])

            raw_usage = resp_json.get("usage") or {}
            turn_in = int(raw_usage.get("input_tokens", 0))
            turn_cached = int(
                raw_usage.get("cache_read_input_tokens", raw_usage.get("cached_input_tokens", 0))
            )
            turn_out = int(raw_usage.get("output_tokens", 0))
            turn_total = int(raw_usage.get("total_tokens", turn_in + turn_cached + turn_out))
            in_tok += turn_in
            cached_tok += turn_cached
            out_tok += turn_out
            total_tok += turn_total

            content_blocks: list[dict[str, Any]] = []
            tool_use_blocks: list[dict[str, Any]] = []

            for block in resp_json.get("content") or []:
                if not isinstance(block, dict):
                    continue
                content_blocks.append(block)
                b_type = block.get("type")
                if b_type == "thinking":
                    yield make_event(
                        HarnessEventKind.THOUGHT_DELTA,
                        delta_text=str(block.get("thinking", "")),
                    )
                elif b_type == "tool_use":
                    tool_use_blocks.append(block)
                elif b_type == "text":
                    txt = str(block.get("text", ""))
                    text_chunks.append(txt)
                    yield make_event(HarnessEventKind.TEXT_DELTA, delta_text=txt)

            if not tool_use_blocks:
                break

            messages.append({"role": "assistant", "content": content_blocks})
            tool_result_blocks: list[dict[str, Any]] = []

            for block in tool_use_blocks:
                raw_name = str(block.get("name", ""))
                fn_name = self.strip_mcp_prefix(raw_name)
                call_id = str(block.get("id") or f"tu-rest-{uuid.uuid4().hex[:8]}")
                raw_input = block.get("input")
                fn_args = dict(raw_input) if isinstance(raw_input, dict) else {}

                yield make_event(
                    HarnessEventKind.TOOL_CALL_START,
                    tool_name=fn_name,
                    tool_call_id=call_id,
                    tool_args=fn_args,
                )
                tool_def = tools_by_name.get(fn_name)
                if tool_def is None:
                    err_msg = f"Unknown tool '{fn_name}'"
                    yield make_event(
                        HarnessEventKind.TOOL_CALL_ERROR,
                        tool_name=fn_name,
                        tool_call_id=call_id,
                        error_message=err_msg,
                    )
                    tool_result_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": err_msg,
                            "is_error": True,
                        }
                    )
                else:
                    try:
                        res_model = await tool_def.invoke(fn_args, ctx)
                        res_dict = (
                            res_model.model_dump(mode="json")
                            if hasattr(res_model, "model_dump")
                            else (
                                res_model if isinstance(res_model, dict) else {"result": res_model}
                            )
                        )
                        yield make_event(
                            HarnessEventKind.TOOL_CALL_RESULT,
                            tool_name=fn_name,
                            tool_call_id=call_id,
                            tool_result=res_dict,
                        )
                        tool_result_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": call_id,
                                "content": json.dumps(res_dict),
                            }
                        )
                    except Exception as exc:
                        err_msg = f"{type(exc).__name__}: {exc}"
                        yield make_event(
                            HarnessEventKind.TOOL_CALL_ERROR,
                            tool_name=fn_name,
                            tool_call_id=call_id,
                            error_message=err_msg,
                        )
                        tool_result_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": call_id,
                                "content": err_msg,
                                "is_error": True,
                            }
                        )

                for obs_ev in ctx.pop_unstreamed_events():
                    yield make_event(HarnessEventKind.OBSERVABILITY_EVENT, obs_event=obs_ev)
                    if obs_ev.get("status") == "ASK-USER":
                        yield make_event(HarnessEventKind.STEWARD_HOLD, obs_event=obs_ev)

            messages.append({"role": "user", "content": tool_result_blocks})

        for obs_ev in ctx.pop_unstreamed_events():
            yield make_event(HarnessEventKind.OBSERVABILITY_EVENT, obs_event=obs_ev)
            if obs_ev.get("status") == "ASK-USER":
                yield make_event(HarnessEventKind.STEWARD_HOLD, obs_event=obs_ev)

        full_text = "".join(text_chunks).strip()
        structured_data: Any = None
        if spec.output_schema is not None and full_text:
            cleaned = _strip_markdown_json_fences(full_text)
            structured_data = spec.output_schema.model_validate_json(cleaned)

        turn_meta: dict[str, Any] = {
            "session_id": str(last_msg_id or session_id),
            "structured_output": structured_data,
            "usage": {
                "input_tokens": in_tok,
                "cached_input_tokens": cached_tok,
                "output_tokens": out_tok,
                "reasoning_tokens": 0,
                "total_tokens": total_tok,
                "model_calls": model_calls,
                "cost_usd": 0.0,
            },
        }
        yield make_event(HarnessEventKind.TURN_COMPLETE, tool_result=turn_meta)

    async def _stream_provider_task(
        self,
        spec: AgentTaskSpec[OutputT],
        ctx: ToolExecutionContext,
        model_id: str,
    ) -> AsyncIterator[HarnessStreamEvent]:
        """Stream events from `claude_agent_sdk.ClaudeSDKClient` or Anthropic REST API fallback."""
        cas = self._load_sdk()
        if cas is None:
            api_key = self._resolve_api_key()
            if api_key is None:
                raise RuntimeError("Missing ANTHROPIC_API_KEY for Anthropic REST execution.")
            async for ev in self._stream_anthropic_rest_task(spec, ctx, model_id, api_key):
                yield ev
            return

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
                    *([] if sub.read_only else ["Write", "Edit"]),
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
                        raw_struct = getattr(message, "structured_output", None)
                        if (
                            raw_struct is None
                            and spec.output_schema is not None
                            and getattr(cas, "_is_deterministic_shim", False)
                        ):
                            from opendv_agent_harness.adapters.deterministic_adapter import (
                                _synthesize_minimal_schema_instance,
                            )

                            raw_struct = _synthesize_minimal_schema_instance(
                                spec.output_schema,
                                prompt=spec.prompt,
                            )
                        turn_meta: dict[str, Any] = {
                            "session_id": str(getattr(message, "session_id", session_id)),
                            "structured_output": raw_struct,
                        }
                        msg_usage = getattr(message, "usage", None)
                        if isinstance(msg_usage, dict):
                            turn_meta["usage"] = msg_usage
                        yield make_event(
                            HarnessEventKind.TURN_COMPLETE,
                            tool_result=turn_meta,
                        )
                for obs_ev in ctx.pop_unstreamed_events():
                    yield make_event(HarnessEventKind.OBSERVABILITY_EVENT, obs_event=obs_ev)
                    if obs_ev.get("status") == "ASK-USER":
                        yield make_event(HarnessEventKind.STEWARD_HOLD, obs_event=obs_ev)
            finally:
                self._active_cancels.pop(spec.task_id, None)


ClaudeAgentSdkAdapter = ClaudeAgentHarnessAdapter

__all__ = [
    "DEFAULT_CLAUDE_MODEL_MAP",
    "ClaudeAgentHarnessAdapter",
    "ClaudeAgentSdkAdapter",
]
