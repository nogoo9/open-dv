"""`ship-vault` Persona Builder: 3-Pass CoT Modeller & Closed-Loop TOON Repair Worker.

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
from opendv_agent_harness.routing import get_persona_capability
from opendv_agent_harness.tools.catalog import get_tools_for_persona

SHIP_VAULT_SYSTEM_INSTRUCTIONS: str = (
    "You are `ship-vault`, the 3-Pass Chain-of-Thought Data Vault 2.0 Modeller and Closed-Loop "
    "TOON Self-Healing Worker in the Open-DV neuro-symbolic pipeline (`STAGE_03_SHIP_VAULT_PASS1` "
    "through `STAGE_07_DETERMINISTIC_COMPILER`, `WB-04`..`WB-08`, `WB-10`, `WB-11`).\n"
    "Responsibilities:\n"
    "1. Operate in an isolated disposable worktree (`read_only_workspace=False`, "
    "`allow_shell_execution=False`). Never author raw SQL DDL/DML directly.\n"
    "2. Execute the 3-Pass CoT State Machine (100% `column_triage_ledger` accounting, 3-Tier BKCC "
    "authority, Nullable-FK Link Splitter `U_nn >= 0.95`, and YAGNI Write-Amplification Cost Guard "
    "`W_amp >= 50 MB/day`).\n"
    "3. Invoke `apply_ast_codemod` (`ALG-A1`..`ALG-A5`, `ALG-A9A`), `run_axi_dv_gates` (Gates 1-5 "
    "with `@toon_v1` diagnostic traces), `compile_canonical_ir`, and `execute_duckdb_sandbox` "
    "(4 Adversarial Shadow Sandbox Batches `DV-DYN-001`..`015`).\n"
    "4. Repair any `REJECT-WITH-TRACE` diagnostics within `max_retries = 3` or escalate genuine "
    "business grain conflicts (`DV-EVO-003`) via `request_steward_decision`."
)


def build_ship_vault_subagent_spec() -> SubagentSpec:
    """Build the canonical `SubagentSpec` for the `ship-vault` isolated worker (`argon` tier)."""
    profile = get_persona_capability(AgentPersona.SHIP_VAULT)
    tool_names = tuple(t.name for t in get_tools_for_persona(AgentPersona.SHIP_VAULT))
    return SubagentSpec(
        name=AgentPersona.SHIP_VAULT.value,
        persona=AgentPersona.SHIP_VAULT,
        description=(
            "3-Pass CoT Data Vault 2.0 Modeller & Closed-Loop TOON Repair Worker "
            "(STAGE_03..STAGE_07, WB-04..WB-11)."
        ),
        system_instructions=SHIP_VAULT_SYSTEM_INSTRUCTIONS,
        model_tier=profile.default_model_tier,
        allowed_tool_names=tool_names,
        allowed_subagents=profile.allowed_subagents,
        read_only=profile.sandbox_policy.read_only_workspace,
    )


def build_ship_vault_task_spec(
    prompt: str = "Synthesize, verify, repair, and compile the Data Vault 2.0 model contract.",
    *,
    run_id: str = "run-ship-vault",
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    task_id: str | None = None,
    stage_id: PipelineStageId = PipelineStageId.STAGE_03_SHIP_VAULT_PASS1,
    workbench_view_id: WorkbenchViewId = WorkbenchViewId.WB_04,
    viz_id: VizId = VizId.VIZ_STEP_05,
    model_tier: ModelTier = ModelTier.HIGH_REASONING,
    tools: Sequence[AgentToolDefinition[Any, Any]] | None = None,
    subagents: Sequence[SubagentSpec] = (),
    output_schema: type[OutputT] | None = None,
    attachments: Sequence[Path] = (),
    resume_session_id: str | None = None,
    context_refs: dict[str, str | None] | None = None,
    system_instructions: str = SHIP_VAULT_SYSTEM_INSTRUCTIONS,
) -> AgentTaskSpec[Any]:
    """Build an `AgentTaskSpec` for `ship-vault` (`STAGE_03..STAGE_07`, `argon` tier)."""
    eff_trace = trace_id or uuid.uuid4().hex
    eff_tools = (
        tuple(tools) if tools is not None else tuple(get_tools_for_persona(AgentPersona.SHIP_VAULT))
    )
    kwargs: dict[str, Any] = {
        "run_id": run_id,
        "trace_id": eff_trace,
        "parent_span_id": parent_span_id,
        "persona": AgentPersona.SHIP_VAULT,
        "stage_id": stage_id,
        "workbench_view_id": workbench_view_id,
        "viz_id": viz_id,
        "prompt": prompt,
        "system_instructions": system_instructions,
        "model_tier": model_tier,
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


create_ship_vault_task_spec = build_ship_vault_task_spec
build_ship_vault_spec = build_ship_vault_task_spec
build_ship_vault_task = build_ship_vault_task_spec

__all__ = [
    "SHIP_VAULT_SYSTEM_INSTRUCTIONS",
    "build_ship_vault_spec",
    "build_ship_vault_subagent_spec",
    "build_ship_vault_task",
    "build_ship_vault_task_spec",
    "create_ship_vault_task_spec",
]
