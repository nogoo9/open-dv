"""`scout-source` Persona Builder: Bronze Statistical Profiler & Intra-Source Drift Detective.

Conforms to docs/03-verification-and-agentic-architecture.md (§8.2)
and docs/09-agnostic-agent-harness-and-sdk-spec.md (§5).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opendv_agent_harness.models import (
    AgentPersona,
    AgentTaskSpec,
    AgentToolDefinition,
    ModelTier,
    OutputT,
    PipelineStageId,
    SubagentSpec,
)
from opendv_agent_harness.routing import get_persona_capability
from opendv_agent_harness.tools.catalog import get_tools_for_persona

SCOUT_SOURCE_SYSTEM_INSTRUCTIONS: str = (
    "You are `scout-source`, the deterministic Bronze Statistical Profiler and Intra-Source "
    "Schema Drift Detective in the Open-DV neuro-symbolic pipeline (`STAGE_01_SCOUT_SOURCE`, "
    "`WB-02` / `WB-09`).\n"
    "Responsibilities:\n"
    "1. Operate strictly in read-only mode "
    "(`read_only_workspace=True`, `allow_shell_execution=False`).\n"
    "2. Invoke `profile_source_feed` to compute empirical Graph B information-theoretic metrics "
    "(`U(C)`, `U_nn(C)`, `H_norm(C)`) and detect `S_k(t) -> S_k(t+1)` intra-source schema drift "
    "per Algorithm A.9a.\n"
    "3. Emit verified telemetry checkpoints via `emit_observability_event` with zero raw 64-hex "
    "digests in Layer 1 headlines."
)


def build_scout_source_subagent_spec() -> SubagentSpec:
    """Build the canonical `SubagentSpec` for the `scout-source` leaf scout (`fast` tier)."""
    profile = get_persona_capability(AgentPersona.SCOUT_SOURCE)
    tool_names = tuple(t.name for t in get_tools_for_persona(AgentPersona.SCOUT_SOURCE))
    return SubagentSpec(
        name=AgentPersona.SCOUT_SOURCE.value,
        persona=AgentPersona.SCOUT_SOURCE,
        description=(
            "Bronze Statistical Profiler & Intra-Source Schema Drift Detective "
            "(STAGE_01_SCOUT_SOURCE, WB-02/WB-09)."
        ),
        system_instructions=SCOUT_SOURCE_SYSTEM_INSTRUCTIONS,
        model_tier=profile.default_model_tier,
        allowed_tool_names=tool_names,
        allowed_subagents=profile.allowed_subagents,
        read_only=profile.sandbox_policy.read_only_workspace,
    )


def build_scout_source_task_spec(
    prompt: str = "Profile Bronze relational feed and evaluate intra-source schema drift.",
    *,
    run_id: str = "run-scout-source",
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    task_id: str | None = None,
    stage_id: PipelineStageId = PipelineStageId.STAGE_01_SCOUT_SOURCE,
    model_tier: ModelTier = ModelTier.FAST_TIER,
    model_override: str | None = None,
    tools: Sequence[AgentToolDefinition[Any, Any]] | None = None,
    subagents: Sequence[SubagentSpec] = (),
    output_schema: type[OutputT] | None = None,
    attachments: Sequence[Path] = (),
    resume_session_id: str | None = None,
    context_refs: dict[str, str | None] | None = None,
    system_instructions: str = SCOUT_SOURCE_SYSTEM_INSTRUCTIONS,
) -> AgentTaskSpec[Any]:
    """Build an `AgentTaskSpec` for `scout-source` (`STAGE_01_SCOUT_SOURCE`, `fast` tier)."""
    eff_trace = trace_id or uuid.uuid4().hex
    eff_tools = (
        tuple(tools)
        if tools is not None
        else tuple(get_tools_for_persona(AgentPersona.SCOUT_SOURCE))
    )
    kwargs: dict[str, Any] = {
        "run_id": run_id,
        "trace_id": eff_trace,
        "parent_span_id": parent_span_id,
        "persona": AgentPersona.SCOUT_SOURCE,
        "stage_id": stage_id,
        "prompt": prompt,
        "system_instructions": system_instructions,
        "model_tier": model_tier,
        "model_override": model_override,
        "tools": eff_tools,
        "subagents": tuple(subagents),
        "output_schema": output_schema,
        "attachments": tuple(attachments),
        "resume_session_id": resume_session_id,
        "context_refs": dict(context_refs or {}),
    }
    if task_id is not None:
        kwargs["task_id"] = task_id
    return AgentTaskSpec(**kwargs)


create_scout_source_task_spec = build_scout_source_task_spec

__all__ = [
    "SCOUT_SOURCE_SYSTEM_INSTRUCTIONS",
    "build_scout_source_subagent_spec",
    "build_scout_source_task_spec",
    "create_scout_source_task_spec",
]
