"""Google Antigravity Python SDK (`google.antigravity`) Adapter for Open-DV Agent Harness.

Conforms to docs/03-verification-and-agentic-architecture.md
and docs/06-explainability-and-observability-spec.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import urllib.request
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
from opendv_agent_harness.routing import DEFAULT_AGY_MODEL_MAP
from opendv_agent_harness.tools.bridge import to_antigravity_tool_callable


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


class AntigravityHarnessAdapter(BaseVaultAgentHarness):
    """Production Adapter 1: Google Antigravity Python SDK (`google.antigravity`).

    Supports `LocalAgentConfig` (Gemini API / Vertex AI) with 9-tier safety policies,
    `SubagentConfig` delegation, concurrent `asyncio.TaskGroup` thought/tool/token streaming,
    constrained `structured_output()` validation, and Gemini REST API execution when
    `google.antigravity` is absent but `GEMINI_API_KEY` or `GOOGLE_API_KEY` is configured.
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
        """Return the canonical provider identifier 'antigravity'."""
        return "antigravity"

    def resolve_model(
        self,
        tier: ModelTier,
        persona: AgentPersona = AgentPersona.VAULT_FIRST_MATE,
    ) -> str:
        """Resolve logical ModelTier ('argon' or 'flash') to a Google Antigravity model ID."""
        del persona
        tier_enum = ModelTier(tier)
        if tier_enum in self._config.model_overrides:
            return str(self._config.model_overrides[tier_enum])
        return DEFAULT_AGY_MODEL_MAP.get(
            tier_enum,
            DEFAULT_AGY_MODEL_MAP[ModelTier.HIGH_REASONING],
        )

    def _resolve_api_key(self) -> str | None:
        """Resolve `GEMINI_API_KEY` or `GOOGLE_API_KEY` from config or process environment."""
        for candidate in (
            self._config.env.get("GEMINI_API_KEY"),
            self._config.env.get("GOOGLE_API_KEY"),
            self._config.extra_provider_options.get("api_key"),
            os.environ.get("GEMINI_API_KEY"),
            os.environ.get("GOOGLE_API_KEY"),
        ):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None

    def _load_sdk(self) -> Any | None:
        """Lazily load `google.antigravity`, return `None` for REST fallback, or fail closed."""
        if self._sdk_override is not None:
            return self._sdk_override
        try:
            import google.antigravity as agy_sdk  # type: ignore[import-not-found]

            return agy_sdk
        except ImportError:
            if self._resolve_api_key() is not None:
                return None
            if _allow_mock_sdk_shim(self._config):
                from opendv_agent_harness.adapters.deterministic_adapter import (
                    build_deterministic_agy_sdk_shim,
                )

                return build_deterministic_agy_sdk_shim()
            raise ProviderConfigurationError(
                "Google Antigravity SDK ('google.antigravity') is not installed and neither "
                "GEMINI_API_KEY nor GOOGLE_API_KEY is present in the environment. "
                "Configure GEMINI_API_KEY/GOOGLE_API_KEY or install google.antigravity."
            ) from None

    def _build_safety_policies(self, agy_policy: Any, spec: AgentTaskSpec[Any]) -> list[Any]:
        """Construct the 9-tier workspace and tool safety policy rules for Antigravity."""
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

    async def _stream_gemini_rest_task(
        self,
        spec: AgentTaskSpec[OutputT],
        ctx: ToolExecutionContext,
        model_id: str,
        api_key: str,
    ) -> AsyncIterator[HarnessStreamEvent]:
        """Execute task against the Gemini `generateContent` REST API when SDK is unavailable."""

        def make_event(kind: HarnessEventKind, **kwargs: Any) -> HarnessStreamEvent:
            return self._make_event(spec, ctx, kind, **kwargs)

        yield make_event(HarnessEventKind.SESSION_START)

        generation_config: dict[str, Any] = {}
        if spec.output_schema is not None:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = spec.output_schema.model_json_schema()

        request_payload: dict[str, Any] = {
            "systemInstruction": {
                "parts": [{"text": spec.system_instructions}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": spec.prompt}],
                }
            ],
        }
        if generation_config:
            request_payload["generationConfig"] = generation_config

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_id}:generateContent?key={api_key}"
        )
        req = urllib.request.Request(
            url=url,
            data=json.dumps(request_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )

        def _do_http_post() -> dict[str, Any]:
            with urllib.request.urlopen(req, timeout=self._config.budget.timeout_seconds) as resp:
                raw_bytes = resp.read()
                return json.loads(raw_bytes.decode("utf-8"))  # type: ignore[no-any-return]

        resp_json = await asyncio.to_thread(_do_http_post)

        text_chunks: list[str] = []
        for candidate in resp_json.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            content_obj = candidate.get("content") or {}
            for part in content_obj.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("thought"):
                    yield make_event(
                        HarnessEventKind.THOUGHT_DELTA,
                        delta_text=str(part.get("text", "")),
                    )
                elif "text" in part:
                    txt = str(part["text"])
                    text_chunks.append(txt)
                    yield make_event(HarnessEventKind.TEXT_DELTA, delta_text=txt)

        for obs_ev in ctx.pop_unstreamed_events():
            yield make_event(HarnessEventKind.OBSERVABILITY_EVENT, obs_event=obs_ev)
            if obs_ev.get("status") == "ASK-USER":
                yield make_event(HarnessEventKind.STEWARD_HOLD, obs_event=obs_ev)

        full_text = "".join(text_chunks).strip()
        structured_data: Any = None
        if spec.output_schema is not None and full_text:
            cleaned = _strip_markdown_json_fences(full_text)
            structured_data = spec.output_schema.model_validate_json(cleaned)

        usage_meta = resp_json.get("usageMetadata") or {}
        in_tokens = int(usage_meta.get("promptTokenCount", usage_meta.get("input_tokens", 0)))
        cached_tokens = int(usage_meta.get("cachedContentTokenCount", 0))
        out_tokens = int(usage_meta.get("candidatesTokenCount", usage_meta.get("output_tokens", 0)))
        thought_tokens = int(usage_meta.get("thoughtsTokenCount", 0))
        total_tokens = int(
            usage_meta.get(
                "totalTokenCount",
                usage_meta.get("total_tokens", in_tokens + out_tokens + thought_tokens),
            )
        )

        turn_meta: dict[str, Any] = {
            "session_id": spec.resume_session_id or spec.task_id,
            "structured_output": structured_data,
            "usage": {
                "input_tokens": in_tokens,
                "cached_input_tokens": cached_tokens,
                "output_tokens": out_tokens,
                "reasoning_tokens": thought_tokens,
                "total_tokens": total_tokens,
                "model_calls": 1,
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
        """Stream events from `google.antigravity.Agent` or Gemini REST API fallback."""
        agy = self._load_sdk()
        if agy is None:
            api_key = self._resolve_api_key()
            if api_key is None:
                raise RuntimeError(
                    "Missing GEMINI_API_KEY / GOOGLE_API_KEY for Gemini REST execution."
                )
            async for ev in self._stream_gemini_rest_task(spec, ctx, model_id, api_key):
                yield ev
            return

        agy_types = agy.types
        agy_hooks = agy.hooks.hooks
        agy_policy = agy.hooks.policy
        bridged_tools = [to_antigravity_tool_callable(td, ctx) for td in spec.tools]

        def make_event(kind: HarnessEventKind, **kwargs: Any) -> HarnessStreamEvent:
            return self._make_event(spec, ctx, kind, **kwargs)

        event_queue: asyncio.Queue[HarnessStreamEvent | None] = asyncio.Queue()

        @agy_hooks.post_tool_call  # type: ignore[untyped-decorator]
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

        @agy_hooks.on_tool_error  # type: ignore[untyped-decorator]
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
                    enabled_tools=[
                        *([] if sub.read_only else ["create_file", "edit_file"]),
                        *sub.allowed_tool_names,
                    ],
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
                    call_id = getattr(call, "id", None) or getattr(call, "call_id", None)
                    await event_queue.put(
                        make_event(
                            HarnessEventKind.TOOL_CALL_START,
                            tool_name=str(call.name),
                            tool_call_id=str(call_id) if call_id is not None else None,
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
            turn_meta: dict[str, Any] = {
                "session_id": getattr(agent, "conversation_id", spec.task_id),
                "structured_output": structured_data,
            }
            resp_usage = getattr(response, "usage", None)
            if isinstance(resp_usage, dict):
                turn_meta["usage"] = resp_usage
            yield make_event(
                HarnessEventKind.TURN_COMPLETE,
                tool_result=turn_meta,
            )


GoogleAntigravityAdapter = AntigravityHarnessAdapter

__all__ = [
    "DEFAULT_AGY_MODEL_MAP",
    "AntigravityHarnessAdapter",
    "GoogleAntigravityAdapter",
]
