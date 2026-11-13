"""Hexagonal VaultAgentHarness protocol and template base class.

Conforms to docs/03-verification-and-agentic-architecture.md
and docs/06-explainability-and-observability-spec.md.
"""

from __future__ import annotations

import abc
import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

import pydantic

from opendv_agent_harness.models import (
    AgentPersona,
    AgentTaskSpec,
    HarnessConfig,
    HarnessEventKind,
    HarnessRunResult,
    HarnessStopReason,
    HarnessStreamEvent,
    HarnessUsageMetrics,
    ModelTier,
    OutputT,
    ToolCallBudgetExceededError,
    ToolExecutionContext,
)


@runtime_checkable
class VaultAgentHarness(Protocol):
    """Vendor-neutral hexagonal port for LLM agent SDK runtimes."""

    @property
    def provider_name(self) -> str: ...

    @property
    def config(self) -> HarnessConfig: ...

    def resolve_model(
        self,
        tier: ModelTier,
        persona: AgentPersona | None = None,
    ) -> str: ...

    def stream_task(
        self,
        spec: AgentTaskSpec[OutputT],
    ) -> AsyncIterator[HarnessStreamEvent]: ...

    async def run_task(
        self,
        spec: AgentTaskSpec[OutputT],
        *,
        on_event: Callable[[HarnessStreamEvent], None] | None = None,
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
        """Return the canonical provider name (e.g. 'antigravity' or 'claude')."""
        raise NotImplementedError

    @property
    def config(self) -> HarnessConfig:
        """Return the active harness configuration."""
        return self._config

    @abc.abstractmethod
    def resolve_model(
        self,
        tier: ModelTier,
        persona: AgentPersona | None = None,
    ) -> str:
        """Resolve a logical ModelTier and optional AgentPersona to a concrete vendor model ID."""
        raise NotImplementedError

    @abc.abstractmethod
    def _stream_provider_task(
        self,
        spec: AgentTaskSpec[OutputT],
        ctx: ToolExecutionContext,
        model_id: str,
    ) -> AsyncIterator[HarnessStreamEvent]:
        """Yield HarnessStreamEvent records from the underlying provider SDK."""
        raise NotImplementedError

    def _new_span_id(self) -> str:
        """Generate a W3C Trace Context 16-hex-character span ID."""
        return uuid.uuid4().hex[:16]

    def _make_event(
        self,
        spec: AgentTaskSpec[Any],
        ctx: ToolExecutionContext,
        kind: HarnessEventKind,
        **kwargs: Any,
    ) -> HarnessStreamEvent:
        """Construct a HarnessStreamEvent bound to the current task and W3C span context."""
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
            **kwargs,
        )

    def build_tool_context(self, spec: AgentTaskSpec[Any]) -> ToolExecutionContext:
        """Create a fresh ToolExecutionContext with a child W3C span ID."""
        return ToolExecutionContext(
            run_id=spec.run_id,
            trace_id=spec.trace_id,
            span_id=self._new_span_id(),
            parent_span_id=spec.parent_span_id,
            persona=spec.persona,
            stage_id=spec.stage_id,
            config=self._config,
        )

    async def stream_task(
        self,
        spec: AgentTaskSpec[OutputT],
    ) -> AsyncIterator[HarnessStreamEvent]:
        """Stream real-time HarnessStreamEvent items for the given AgentTaskSpec."""
        ctx = self.build_tool_context(spec)
        model_id = spec.model_override or self.resolve_model(spec.model_tier, spec.persona)
        async for event in self._stream_provider_task(spec, ctx, model_id):
            yield event

    async def run_task(
        self,
        spec: AgentTaskSpec[OutputT],
        *,
        on_event: Callable[[HarnessStreamEvent], None] | None = None,
    ) -> HarnessRunResult[OutputT]:
        """Execute a task to completion, aggregating text, tool counts, holds, and output."""
        start_ns = time.perf_counter_ns()
        ctx = self.build_tool_context(spec)
        model_id = spec.model_override or self.resolve_model(spec.model_tier, spec.persona)
        text_chunks: list[str] = []
        tool_call_count = 0
        session_id = spec.resume_session_id or f"sess-{uuid.uuid4().hex[:12]}"
        stop_reason = HarnessStopReason.COMPLETED
        structured_payload: OutputT | dict[str, Any] | None = None
        subagent_models: dict[str, str] = {}
        usage = HarnessUsageMetrics()

        try:
            async with asyncio.timeout(self._config.budget.timeout_seconds):
                async for event in self._stream_provider_task(spec, ctx, model_id):
                    if event.kind == HarnessEventKind.TOOL_CALL_START:
                        if tool_call_count >= self._config.budget.max_tool_calls:
                            stop_reason = HarnessStopReason.MAX_TOOL_CALLS_EXCEEDED
                            await self.cancel_active_turn(spec.task_id)
                            break
                        tool_call_count += 1
                    if on_event is not None:
                        on_event(event)
                    if event.kind == HarnessEventKind.TEXT_DELTA and event.delta_text:
                        text_chunks.append(event.delta_text)
                    elif event.kind == HarnessEventKind.STEWARD_HOLD or (
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
                            if spec.output_schema is not None:
                                if isinstance(raw_struct, spec.output_schema):
                                    structured_payload = raw_struct
                                elif isinstance(raw_struct, dict):
                                    structured_payload = spec.output_schema.model_validate(
                                        raw_struct
                                    )
                                elif isinstance(raw_struct, str):
                                    structured_payload = spec.output_schema.model_validate_json(
                                        raw_struct
                                    )
                                else:
                                    structured_payload = raw_struct
                            else:
                                structured_payload = raw_struct
                        if "subagent_models" in meta and isinstance(meta["subagent_models"], dict):
                            subagent_models.update(
                                {str(k): str(v) for k, v in meta["subagent_models"].items()}
                            )
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
        except ToolCallBudgetExceededError:
            stop_reason = HarnessStopReason.MAX_TOOL_CALLS_EXCEEDED
            await self.cancel_active_turn(spec.task_id)
        except asyncio.CancelledError:
            stop_reason = HarnessStopReason.CANCELLED
        except TimeoutError:
            stop_reason = HarnessStopReason.MAX_TURNS_EXCEEDED
        except pydantic.ValidationError:
            stop_reason = HarnessStopReason.VALIDATION_ERROR

        if ctx.tool_budget_exceeded:
            stop_reason = HarnessStopReason.MAX_TOOL_CALLS_EXCEEDED
        elif stop_reason == HarnessStopReason.COMPLETED and any(
            isinstance(ev, dict) and ev.get("status") == "ASK-USER" for ev in ctx.emitted_events
        ):
            stop_reason = HarnessStopReason.STEWARD_HOLD

        tool_call_count = max(tool_call_count, ctx.tool_calls_invoked)

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
            subagent_models=subagent_models,
        )

    async def cancel_active_turn(self, task_id: str) -> None:
        """Cancel an in-flight turn for `task_id` if registered."""
        cancel_fn = self._active_cancels.get(task_id)
        if cancel_fn is not None:
            await cancel_fn()


__all__ = [
    "BaseVaultAgentHarness",
    "VaultAgentHarness",
]
