"""`vault-first-mate` Persona Builder: Read-Only Supervisor & Global Ontology Steward.

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
    VizId,
    WorkbenchViewId,
)
from opendv_agent_harness.personas.scout_source import build_scout_source_subagent_spec
from opendv_agent_harness.personas.scout_unstructured import (
    build_scout_unstructured_subagent_spec,
)
from opendv_agent_harness.personas.ship_vault import build_ship_vault_subagent_spec
from opendv_agent_harness.routing import get_persona_capability
from opendv_agent_harness.tools.catalog import get_tools_for_persona

VAULT_FIRST_MATE_SYSTEM_INSTRUCTIONS: str = (
    "You are `vault-first-mate`, the Read-Only Supervisor and Enterprise Core Business Concept "
    "(Graph A) Ontology Steward for the Open-DV neuro-symbolic pipeline "
    "(`STAGE_00_ORCHESTRATION`, `WB-00` / `WB-01`).\n"
    "Responsibilities:\n"
    "1. Enforce Hard Rule 1: Read-Only First Mate (`read_only_workspace=True`, "
    "`allow_shell_execution=False`). Never directly edit contracts or author SQL.\n"
    "2. Ground Enterprise Core Business Concepts (`Graph A`) and orchestrate the 3 specialized "
    "subagents: `scout-source` (`flash`), `scout-unstructured` (`flash`), and "
    "`ship-vault` (`argon`).\n"
    "3. Inspect 6-Gate verification verdicts (`run_axi_dv_gates`), 4-Batch DuckDB Shadow Sandbox "
    "results (`execute_duckdb_sandbox`), and Canonical IR artifacts (`compile_canonical_ir`).\n"
    "4. Escalate `ASK-USER` Captain-Hold decisions (`request_steward_decision`) and record "
    "auditable telemetry checkpoints (`emit_observability_event`)."
)


def build_vault_first_mate_subagents() -> tuple[SubagentSpec, ...]:
    """Return the 3 canonical subagents delegated by `vault-first-mate`."""
    return (
        build_scout_source_subagent_spec(),
        build_scout_unstructured_subagent_spec(),
        build_ship_vault_subagent_spec(),
    )


def build_vault_first_mate_subagent_spec() -> SubagentSpec:
    """Build a `SubagentSpec` descriptor for `vault-first-mate` (`argon` tier)."""
    profile = get_persona_capability(AgentPersona.VAULT_FIRST_MATE)
    tool_names = tuple(t.name for t in get_tools_for_persona(AgentPersona.VAULT_FIRST_MATE))
    return SubagentSpec(
        name=AgentPersona.VAULT_FIRST_MATE.value,
        persona=AgentPersona.VAULT_FIRST_MATE,
        description=(
            "Read-Only Supervisor & Enterprise Ontology Steward "
            "(STAGE_00_ORCHESTRATION, WB-00/WB-01)."
        ),
        system_instructions=VAULT_FIRST_MATE_SYSTEM_INSTRUCTIONS,
        model_tier=profile.default_model_tier,
        allowed_tool_names=tool_names,
        allowed_subagents=profile.allowed_subagents,
        read_only=profile.sandbox_policy.read_only_workspace,
    )


def build_vault_first_mate_task_spec(
    prompt: str = "Orchestrate the 8-stage neuro-symbolic Data Vault 2.0 verification pipeline.",
    *,
    run_id: str = "run-vault-first-mate",
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    task_id: str | None = None,
    stage_id: PipelineStageId = PipelineStageId.STAGE_00_ORCHESTRATION,
    workbench_view_id: WorkbenchViewId = WorkbenchViewId.WB_00,
    viz_id: VizId = VizId.VIZ_PIPE_00,
    model_tier: ModelTier = ModelTier.HIGH_REASONING,
    tools: Sequence[AgentToolDefinition[Any, Any]] | None = None,
    subagents: Sequence[SubagentSpec] | None = None,
    output_schema: type[OutputT] | None = None,
    attachments: Sequence[Path] = (),
    resume_session_id: str | None = None,
    context_refs: dict[str, str | None] | None = None,
    system_instructions: str = VAULT_FIRST_MATE_SYSTEM_INSTRUCTIONS,
) -> AgentTaskSpec[Any]:
    """Build an `AgentTaskSpec` for `vault-first-mate` (`STAGE_00_ORCHESTRATION`, `argon` tier)."""
    eff_trace = trace_id or uuid.uuid4().hex
    eff_tools = (
        tuple(tools)
        if tools is not None
        else tuple(get_tools_for_persona(AgentPersona.VAULT_FIRST_MATE))
    )
    eff_subagents = (
        tuple(subagents) if subagents is not None else build_vault_first_mate_subagents()
    )
    kwargs: dict[str, Any] = {
        "run_id": run_id,
        "trace_id": eff_trace,
        "parent_span_id": parent_span_id,
        "persona": AgentPersona.VAULT_FIRST_MATE,
        "stage_id": stage_id,
        "workbench_view_id": workbench_view_id,
        "viz_id": viz_id,
        "prompt": prompt,
        "system_instructions": system_instructions,
        "model_tier": model_tier,
        "tools": eff_tools,
        "subagents": eff_subagents,
        "output_schema": output_schema,
        "attachments": tuple(attachments),
        "resume_session_id": resume_session_id,
        "context_refs": dict(context_refs or {}),
    }
    if task_id is not None:
        kwargs["task_id"] = task_id
    return AgentTaskSpec(**kwargs)


create_vault_first_mate_task_spec = build_vault_first_mate_task_spec
build_vault_first_mate_spec = build_vault_first_mate_task_spec

__all__ = [
    "VAULT_FIRST_MATE_SYSTEM_INSTRUCTIONS",
    "build_vault_first_mate_spec",
    "build_vault_first_mate_subagent_spec",
    "build_vault_first_mate_subagents",
    "build_vault_first_mate_task_spec",
    "create_vault_first_mate_task_spec",
]
