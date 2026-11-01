"""Anthropic Claude Agent SDK (`claude_agent_sdk`) Adapter for Open-DV Agent Harness.

Conforms to docs/03-verification-and-agentic-architecture.md
and docs/06-explainability-and-observability-spec.md.
"""

from __future__ import annotations

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


class ClaudeAgentHarnessAdapter(BaseVaultAgentHarness):
    """Production Adapter 2: Anthropic Claude Agent SDK for Python (`claude-agent-sdk`).

    Uses `ClaudeSDKClient`, `ClaudeAgentOptions`, `@tool` + `create_sdk_mcp_server`,
    `AgentDefinition` subagents, `HookMatcher` guardrails, and `output_format` JSON Schema.
    """

    def __init__(self, config: HarnessConfig, *, sdk_module: Any | None = None) -> None:
        super().__init__(config)
        self._sdk_override = sdk_module

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

    def _load_sdk(self) -> Any:
        """Lazily load `claude_agent_sdk` or return the injected/fallback SDK module."""
        if self._sdk_override is not None:
            return self._sdk_override
        try:
            import claude_agent_sdk  # type: ignore[import-not-found]

            return claude_agent_sdk
        except ModuleNotFoundError:
            from opendv_agent_harness.adapters.deterministic_adapter import (
                build_deterministic_claude_sdk_shim,
            )

            return build_deterministic_claude_sdk_shim()

    @staticmethod
    def strip_mcp_prefix(tool_name: str) -> str:
        """Strip the in-process MCP server prefix `mcp__opendv__` from tool names."""
        if tool_name.startswith("mcp__opendv__"):
            return tool_name.removeprefix("mcp__opendv__")
        return tool_name

    async def _stream_provider_task(
        self,
        spec: AgentTaskSpec[OutputT],
        ctx: ToolExecutionContext,
        model_id: str,
    ) -> AsyncIterator[HarnessStreamEvent]:
        """Stream events from `claude_agent_sdk.ClaudeSDKClient`."""
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
                        turn_meta: dict[str, Any] = {
                            "session_id": str(getattr(message, "session_id", session_id)),
                            "structured_output": getattr(message, "structured_output", None),
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


__all__ = [
    "DEFAULT_CLAUDE_MODEL_MAP",
    "ClaudeAgentHarnessAdapter",
]
