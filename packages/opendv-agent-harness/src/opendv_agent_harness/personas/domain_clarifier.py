"""`domain-clarifier` Persona Builder: Polysemy Detective & Clarification Generator."""

from __future__ import annotations

import hashlib
import re
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
from opendv_agent_harness.privacy import Pseudonymizer
from opendv_agent_harness.routing import get_persona_capability
from opendv_agent_harness.tools.catalog import get_tools_for_persona
from opendv_contracts.catalogs.polysemy import KNOWN_POLYSEMIC_DEFINITIONS
from opendv_contracts.dtos.clarification import (
    ClarificationOption,
    ClarificationQuestion,
)
from opendv_contracts.dtos.ontology import EvidenceRef

DOMAIN_CLARIFIER_SYSTEM_INSTRUCTIONS: str = (
    "You are `@domain-clarifier`. Formulate ClarificationQuestions from polysemic terms."
)


def build_domain_clarifier_subagent_spec() -> SubagentSpec:
    """Build canonical `SubagentSpec` for `@domain-clarifier` (`reasoning` tier)."""
    profile = get_persona_capability(AgentPersona.DOMAIN_CLARIFIER)
    tool_names = tuple(t.name for t in get_tools_for_persona(AgentPersona.DOMAIN_CLARIFIER))
    return SubagentSpec(
        name=AgentPersona.DOMAIN_CLARIFIER.value,
        persona=AgentPersona.DOMAIN_CLARIFIER,
        description="@domain-clarifier reasoning tier SPI.",
        system_instructions=DOMAIN_CLARIFIER_SYSTEM_INSTRUCTIONS,
        model_tier=profile.default_model_tier,
        allowed_tool_names=tool_names,
        allowed_subagents=profile.allowed_subagents,
        read_only=profile.sandbox_policy.read_only_workspace,
    )


def build_domain_clarifier_task_spec(
    prompt: str = "Analyze candidate terms for polysemy and generate clarification questions.",
    *,
    run_id: str = "run-domain-clarifier",
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    task_id: str | None = None,
    stage_id: PipelineStageId = PipelineStageId.STAGE_00_ORCHESTRATION,
    model_tier: ModelTier | None = None,
    model_override: str | None = None,
    tools: Sequence[AgentToolDefinition[Any, Any]] | None = None,
    subagents: Sequence[SubagentSpec] = (),
    workspace_root: Path | None = None,
    input_data: Any = None,
    expected_output_type: type[OutputT] | None = None,
    system_instructions: str = DOMAIN_CLARIFIER_SYSTEM_INSTRUCTIONS,
) -> AgentTaskSpec[Any]:
    """Build canonical `AgentTaskSpec` for `@domain-clarifier`."""
    profile = get_persona_capability(AgentPersona.DOMAIN_CLARIFIER)
    eff_trace = trace_id or uuid.uuid4().hex
    eff_tools = (
        tuple(tools)
        if tools is not None
        else tuple(get_tools_for_persona(AgentPersona.DOMAIN_CLARIFIER))
    )
    from opendv_agent_harness.personas import build_input_context_refs

    context_refs: dict[str, str | None] = {}
    if workspace_root is not None:
        context_refs["workspace_root"] = str(workspace_root)
    context_refs.update(build_input_context_refs(input_data))
    kwargs: dict[str, Any] = {
        "run_id": run_id,
        "trace_id": eff_trace,
        "parent_span_id": parent_span_id,
        "persona": AgentPersona.DOMAIN_CLARIFIER,
        "stage_id": stage_id,
        "prompt": prompt,
        "system_instructions": system_instructions,
        "model_tier": model_tier or profile.default_model_tier,
        "model_override": model_override,
        "tools": eff_tools,
        "subagents": tuple(subagents),
        "context_refs": context_refs,
    }
    if task_id is not None:
        kwargs["task_id"] = task_id
    if expected_output_type is not None:
        kwargs["output_schema"] = expected_output_type
    return AgentTaskSpec(**kwargs)


def _to_slug(name: str) -> str:
    """Normalize term to lowercase alphanumeric slug without spaces."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", s).lower()
    return re.sub(r"_+", "_", s).strip("_")


class DomainClarifier:
    """Semantic reasoning engine for polysemy detection and question generation.

    Note: This persona provides a deterministic rule-based baseline implementation;
    in production, an LLM-backed runtime may be attached via the agent harness.
    """

    def __init__(self, pseudonymizer: Pseudonymizer | None = None) -> None:
        self.pseudonymizer = pseudonymizer

    def generate_questions_for_terms(
        self,
        candidate_terms: Sequence[str | dict[str, Any]],
        max_questions: int = 5,
    ) -> list[ClarificationQuestion]:
        """Detect ambiguous concepts and formulate structured ClarificationQuestion objects."""
        questions: list[ClarificationQuestion] = []
        seen_terms: set[str] = set()

        for item in candidate_terms:
            if len(questions) >= max_questions:
                break

            term_name = item.get("term", "") if isinstance(item, dict) else str(item)

            if not term_name:
                continue

            term_clean = term_name.strip()
            if not term_clean:
                continue

            # De-pseudonymize if pseudonymizer is active (MIN-05)
            if self.pseudonymizer:
                term_clean = self.pseudonymizer.de_pseudonymize(term_clean)

            term_lower = term_clean.lower()
            if term_lower in seen_terms:
                continue
            seen_terms.add(term_lower)

            evidence_citations: list[EvidenceRef] = []
            if isinstance(item, dict) and "evidence_citations" in item:
                evidence_citations = item["evidence_citations"]

            if term_lower in KNOWN_POLYSEMIC_DEFINITIONS:
                options_data = KNOWN_POLYSEMIC_DEFINITIONS[term_lower]
                term_slug = _to_slug(term_clean)
                options: list[ClarificationOption] = []
                for i, opt in enumerate(options_data, start=1):
                    options.append(
                        ClarificationOption(
                            option_id=f"opt-{term_slug}-{i}",
                            label=opt["label"],
                            fibo_uri=opt.get("fibo_uri"),
                            rationale=opt["rationale"],
                        )
                    )
                hash_src = f"{term_lower}:{':'.join(opt['label'] for opt in options_data)}"
                hash_suffix = hashlib.sha256(hash_src.encode("utf-8")).hexdigest()[:8]
                qid = f"q-poly-{term_slug}-{hash_suffix}"
                questions.append(
                    ClarificationQuestion(
                        question_id=qid,
                        focus_concept=term_clean,
                        question_text=(
                            f"The term '{term_clean}' is polysemic in financial domains. "
                            f"How should '{term_clean}' be classified in this ontology?"
                        ),
                        evidence_citations=evidence_citations,
                        options=options,
                        allow_custom_text=True,
                    )
                )

        return questions
