"""Google Antigravity Python SDK (`google.antigravity`) Adapter for Open-DV Agent Harness.

Conforms to docs/03-verification-and-agentic-architecture.md
and docs/06-explainability-and-observability-spec.md.
"""

from __future__ import annotations

import asyncio
import contextlib
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


class AntigravityHarnessAdapter(BaseVaultAgentHarness):
    """Production Adapter 1: Google Antigravity Python SDK (`google.antigravity`).

    Supports `LocalAgentConfig` (Gemini API / Vertex AI) with 9-tier safety policies,
    `SubagentConfig` delegation, concurrent `asyncio.TaskGroup` thought/tool/token streaming,
    and constrained `structured_output()` validation.
    """

    def __init__(self, config: HarnessConfig, *, sdk_module: Any | None = None) -> None:
        super().__init__(config)
        self._sdk_override = sdk_module

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

    def _load_sdk(self) -> Any:
        """Lazily load `google.antigravity` or return the injected/fallback SDK module."""
        if self._sdk_override is not None:
            return self._sdk_override
        try:
            import google.antigravity as agy_sdk  # type: ignore[import-not-found]

            return agy_sdk
        except ModuleNotFoundError:
            from opendv_agent_harness.adapters.deterministic_adapter import (
                build_deterministic_agy_sdk_shim,
            )

            return build_deterministic_agy_sdk_shim()

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

    async def _stream_provider_task(
        self,
        spec: AgentTaskSpec[OutputT],
        ctx: ToolExecutionContext,
        model_id: str,
    ) -> AsyncIterator[HarnessStreamEvent]:
        """Stream events from `google.antigravity.Agent` via concurrent TaskGroup pumps."""
        agy = self._load_sdk()
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


__all__ = [
    "DEFAULT_AGY_MODEL_MAP",
    "AntigravityHarnessAdapter",
]
