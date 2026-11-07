"""`scout-unstructured` Persona Builder: Span-Grounded NER & 3-Tier Entity Resolution Scout.

Conforms to docs/03-verification-and-agentic-architecture.md (§8.2),
docs/04-unstructured-and-multimodal-dv2.md (§2-§4),
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

SCOUT_UNSTRUCTURED_SYSTEM_INSTRUCTIONS: str = (
    "You are `scout-unstructured`, the Span-Grounded Multimodal NER and 3-Tier Entity Resolution "
    "Scout in the Open-DV neuro-symbolic pipeline (`STAGE_02_SCOUT_UNSTRUCTURED`).\n"
    "Responsibilities:\n"
    "1. Operate strictly in read-only mode "
    "(`read_only_workspace=True`, `allow_shell_execution=False`).\n"
    "2. Invoke `extract_pdf_spans` to extract and verify exact character-offset spans "
    "`[char_start, char_end]` under Rules U1-U4 and Algorithm A.9b.\n"
    "3. Enforce the 3-Tier Entity Resolution protocol (`Tier-U1` exact identifier match, "
    "`Tier-U2` high-confidence RRF/Cross-Encoder binding, `Tier-U3` abstention queue when margin "
    "< 0.15) and invoke `request_steward_decision` (`ASK-USER`) on `Tier-U3` ambiguities.\n"
    "4. Emit telemetry records via `emit_observability_event`."
)


def build_scout_unstructured_subagent_spec() -> SubagentSpec:
    """Build the canonical `SubagentSpec` for the `scout-unstructured` leaf scout (`fast` tier)."""
    profile = get_persona_capability(AgentPersona.SCOUT_UNSTRUCTURED)
    tool_names = tuple(t.name for t in get_tools_for_persona(AgentPersona.SCOUT_UNSTRUCTURED))
    return SubagentSpec(
        name=AgentPersona.SCOUT_UNSTRUCTURED.value,
        persona=AgentPersona.SCOUT_UNSTRUCTURED,
        description=(
            "Span-Grounded Multimodal NER & 3-Tier Entity Resolution Scout "
            "(STAGE_02_SCOUT_UNSTRUCTURED)."
        ),
        system_instructions=SCOUT_UNSTRUCTURED_SYSTEM_INSTRUCTIONS,
        model_tier=profile.default_model_tier,
        allowed_tool_names=tool_names,
        allowed_subagents=profile.allowed_subagents,
        read_only=profile.sandbox_policy.read_only_workspace,
    )


def build_scout_unstructured_task_spec(
    prompt: str = (
        "Extract character-offset grounded PDF spans and evaluate 3-Tier Entity Resolution."
    ),
    *,
    run_id: str = "run-scout-unstructured",
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    task_id: str | None = None,
    stage_id: PipelineStageId = PipelineStageId.STAGE_02_SCOUT_UNSTRUCTURED,
    model_tier: ModelTier = ModelTier.FAST_TIER,
    model_override: str | None = None,
    tools: Sequence[AgentToolDefinition[Any, Any]] | None = None,
    subagents: Sequence[SubagentSpec] = (),
    output_schema: type[OutputT] | None = None,
    attachments: Sequence[Path] = (),
    resume_session_id: str | None = None,
    context_refs: dict[str, str | None] | None = None,
    system_instructions: str = SCOUT_UNSTRUCTURED_SYSTEM_INSTRUCTIONS,
) -> AgentTaskSpec[Any]:
    """Build an `AgentTaskSpec` for `scout-unstructured` (`STAGE_02`, `fast` tier)."""
    eff_trace = trace_id or uuid.uuid4().hex
    eff_tools = (
        tuple(tools)
        if tools is not None
        else tuple(get_tools_for_persona(AgentPersona.SCOUT_UNSTRUCTURED))
    )
    kwargs: dict[str, Any] = {
        "run_id": run_id,
        "trace_id": eff_trace,
        "parent_span_id": parent_span_id,
        "persona": AgentPersona.SCOUT_UNSTRUCTURED,
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


create_scout_unstructured_task_spec = build_scout_unstructured_task_spec

__all__ = [
    "SCOUT_UNSTRUCTURED_SYSTEM_INSTRUCTIONS",
    "build_scout_unstructured_subagent_spec",
    "build_scout_unstructured_task_spec",
    "create_scout_unstructured_task_spec",
]
