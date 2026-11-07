"""Deterministic Neuro-Symbolic Mock Adapter for Open-DV Agent Harness Tests.

Provides an offline, zero-network `VaultAgentHarness` test double (`DeterministicHarnessAdapter`)
for unit and integration test suites.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
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
from opendv_agent_harness.routing import (
    DEFAULT_DETERMINISTIC_MODEL_MAP,
    resolve_provider_model,
)
from opendv_testkit.synthesis import (
    synthesize_minimal_schema_instance,
    validate_candidate_with_schema,
)


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
            return validate_candidate_with_schema(
                spec.output_schema,
                spec_meta["deterministic_structured_output"],
            )

        ctx_override = spec.context_refs.get(
            "deterministic_structured_output"
        ) or spec.context_refs.get("deterministic_structured_output_json")
        if isinstance(ctx_override, str) and ctx_override.strip():
            return validate_candidate_with_schema(spec.output_schema, ctx_override)

        factory = self.structured_output_factory or self._config.extra_provider_options.get(
            "structured_output_factory"
        )
        if callable(factory):
            produced = factory(spec)
            if produced is not None:
                return validate_candidate_with_schema(spec.output_schema, produced)

        cfg_override = self._config.extra_provider_options.get("deterministic_structured_output")
        if cfg_override is not None:
            return validate_candidate_with_schema(spec.output_schema, cfg_override)

        return synthesize_minimal_schema_instance(spec.output_schema, prompt=spec.prompt)

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
                f"{spec.stage_id.value} as persona '{spec.persona.value}'."
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
                    f"Deterministic {spec.persona.value} checkpoint at {spec.stage_id.value}."
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


__all__ = [
    "DEFAULT_DETERMINISTIC_MODEL_MAP",
    "DeterministicHarnessAdapter",
]
