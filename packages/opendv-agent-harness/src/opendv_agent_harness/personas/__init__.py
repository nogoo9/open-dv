"""Persona Specification Builders for the 4 Canonical Open-DV Agent Personas.

Provides task and subagent builders for:
1. `vault-first-mate` (`reasoning` supervisor with 3 subagents)
2. `scout-source` (`fast` leaf scout)
3. `scout-unstructured` (`fast` leaf scout)
4. `ship-vault` (`reasoning` isolated worktree worker)
"""

from __future__ import annotations

import hashlib
from typing import Any

from opendv_agent_harness.models import AgentPersona, AgentTaskSpec, SubagentSpec
from opendv_agent_harness.personas.domain_clarifier import (
    DOMAIN_CLARIFIER_SYSTEM_INSTRUCTIONS,
    DomainClarifier,
    build_domain_clarifier_subagent_spec,
    build_domain_clarifier_task_spec,
)
from opendv_agent_harness.personas.ontology_architect import (
    ONTOLOGY_ARCHITECT_SYSTEM_INSTRUCTIONS,
    OntologyArchitect,
    build_ontology_architect_subagent_spec,
    build_ontology_architect_task_spec,
)
from opendv_agent_harness.personas.scout_source import (
    SCOUT_SOURCE_SYSTEM_INSTRUCTIONS,
    build_scout_source_subagent_spec,
    build_scout_source_task_spec,
    create_scout_source_task_spec,
)
from opendv_agent_harness.personas.scout_unstructured import (
    SCOUT_UNSTRUCTURED_SYSTEM_INSTRUCTIONS,
    build_scout_unstructured_subagent_spec,
    build_scout_unstructured_task_spec,
    create_scout_unstructured_task_spec,
)
from opendv_agent_harness.personas.ship_vault import (
    SHIP_VAULT_SYSTEM_INSTRUCTIONS,
    build_ship_vault_subagent_spec,
    build_ship_vault_task_spec,
    create_ship_vault_task_spec,
)
from opendv_agent_harness.personas.term_scout import (
    TERM_SCOUT_SYSTEM_INSTRUCTIONS,
    TermScout,
    build_term_scout_subagent_spec,
    build_term_scout_task_spec,
)
from opendv_agent_harness.personas.vault_first_mate import (
    VAULT_FIRST_MATE_SYSTEM_INSTRUCTIONS,
    build_vault_first_mate_subagent_spec,
    build_vault_first_mate_subagents,
    build_vault_first_mate_task_spec,
    create_vault_first_mate_task_spec,
)


def build_input_context_refs(input_data: Any) -> dict[str, str]:
    """Derive canonical context references from input data (SUG-01)."""
    if input_data is None:
        return {}
    if isinstance(input_data, (int, float, bool)):
        return {"input_data": str(input_data)}
    if hasattr(input_data, "chunk_id"):
        return {"input_data": f"urn:opendv:evidence:chunk:{input_data.chunk_id}"}
    if hasattr(input_data, "table_id"):
        return {"input_data": f"urn:opendv:evidence:table:{input_data.table_id}"}
    if isinstance(input_data, (list, tuple)):
        return {"input_data": f"items:{len(input_data)}"}
    if isinstance(input_data, str):
        digest = hashlib.sha256(input_data.encode("utf-8")).hexdigest()[:16]
        return {"input_data": f"sha256:{digest}"}
    return {"input_data": f"ref:{type(input_data).__name__}"}


def build_persona_subagent_spec(persona: AgentPersona | str) -> SubagentSpec:
    """Dispatch `persona` to its canonical `SubagentSpec` builder."""
    persona_enum = AgentPersona(persona)
    if persona_enum == AgentPersona.VAULT_FIRST_MATE:
        return build_vault_first_mate_subagent_spec()
    if persona_enum == AgentPersona.SCOUT_SOURCE:
        return build_scout_source_subagent_spec()
    if persona_enum == AgentPersona.SCOUT_UNSTRUCTURED:
        return build_scout_unstructured_subagent_spec()
    if persona_enum == AgentPersona.SHIP_VAULT:
        return build_ship_vault_subagent_spec()
    if persona_enum == AgentPersona.TERM_SCOUT:
        return build_term_scout_subagent_spec()
    if persona_enum == AgentPersona.DOMAIN_CLARIFIER:
        return build_domain_clarifier_subagent_spec()
    if persona_enum == AgentPersona.ONTOLOGY_ARCHITECT:
        return build_ontology_architect_subagent_spec()
    raise ValueError(f"Unsupported agent persona '{persona_enum}' for SubagentSpec builder.")


def build_persona_task_spec(
    persona: AgentPersona | str,
    prompt: str | None = None,
    **kwargs: Any,
) -> AgentTaskSpec[Any]:
    """Dispatch `persona` to its canonical `AgentTaskSpec` builder."""
    persona_enum = AgentPersona(persona)
    if prompt is not None:
        kwargs["prompt"] = prompt
    if persona_enum == AgentPersona.VAULT_FIRST_MATE:
        return build_vault_first_mate_task_spec(**kwargs)
    if persona_enum == AgentPersona.SCOUT_SOURCE:
        return build_scout_source_task_spec(**kwargs)
    if persona_enum == AgentPersona.SCOUT_UNSTRUCTURED:
        return build_scout_unstructured_task_spec(**kwargs)
    if persona_enum == AgentPersona.SHIP_VAULT:
        return build_ship_vault_task_spec(**kwargs)
    if persona_enum == AgentPersona.TERM_SCOUT:
        return build_term_scout_task_spec(**kwargs)
    if persona_enum == AgentPersona.DOMAIN_CLARIFIER:
        return build_domain_clarifier_task_spec(**kwargs)
    if persona_enum == AgentPersona.ONTOLOGY_ARCHITECT:
        return build_ontology_architect_task_spec(**kwargs)
    raise ValueError(f"Unsupported agent persona '{persona_enum}' for AgentTaskSpec builder.")


__all__ = [
    "DOMAIN_CLARIFIER_SYSTEM_INSTRUCTIONS",
    "ONTOLOGY_ARCHITECT_SYSTEM_INSTRUCTIONS",
    "SCOUT_SOURCE_SYSTEM_INSTRUCTIONS",
    "SCOUT_UNSTRUCTURED_SYSTEM_INSTRUCTIONS",
    "SHIP_VAULT_SYSTEM_INSTRUCTIONS",
    "TERM_SCOUT_SYSTEM_INSTRUCTIONS",
    "VAULT_FIRST_MATE_SYSTEM_INSTRUCTIONS",
    "DomainClarifier",
    "OntologyArchitect",
    "TermScout",
    "build_domain_clarifier_subagent_spec",
    "build_domain_clarifier_task_spec",
    "build_input_context_refs",
    "build_ontology_architect_subagent_spec",
    "build_ontology_architect_task_spec",
    "build_persona_subagent_spec",
    "build_persona_task_spec",
    "build_scout_source_subagent_spec",
    "build_scout_source_task_spec",
    "build_scout_unstructured_subagent_spec",
    "build_scout_unstructured_task_spec",
    "build_ship_vault_subagent_spec",
    "build_ship_vault_task_spec",
    "build_term_scout_subagent_spec",
    "build_term_scout_task_spec",
    "build_vault_first_mate_subagent_spec",
    "build_vault_first_mate_subagents",
    "build_vault_first_mate_task_spec",
    "create_scout_source_task_spec",
    "create_scout_unstructured_task_spec",
    "create_ship_vault_task_spec",
    "create_vault_first_mate_task_spec",
]
