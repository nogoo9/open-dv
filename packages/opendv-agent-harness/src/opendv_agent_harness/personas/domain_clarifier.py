"""`domain-clarifier` Persona Builder: Polysemy Detective & Clarification Generator."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Mapping, Sequence
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
from opendv_agent_harness.privacy import Pseudonymizer, validate_task_spec_privacy
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
    validate_task_spec_privacy(
        prompt=prompt,
        system_instructions=system_instructions,
        input_data=input_data,
    )
    profile = get_persona_capability(AgentPersona.DOMAIN_CLARIFIER)
    eff_trace = trace_id or uuid.uuid4().hex
    if tools is not None:
        for t in tools:
            if (
                t.name not in profile.authorized_tools
                or AgentPersona.DOMAIN_CLARIFIER not in t.allowed_personas
            ):
                raise PermissionError(
                    f"Tool '{t.name}' is not authorized for persona "
                    f"'{AgentPersona.DOMAIN_CLARIFIER.value}'."
                )
        eff_tools = tuple(tools)
    else:
        eff_tools = tuple(get_tools_for_persona(AgentPersona.DOMAIN_CLARIFIER))
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


def _match_polysemic_key(
    term_clean: str,
    polysemic_map: Mapping[str, list[dict[str, str]]],
) -> str | None:
    """Match a single- or multi-word term against polysemic catalog keys (MAJ-10)."""
    normalized_lookup: dict[str, str] = {}
    for raw_key in polysemic_map:
        slug_k = _to_slug(raw_key)
        space_k = slug_k.replace("_", " ")
        normalized_lookup.setdefault(raw_key.strip().lower(), raw_key)
        if slug_k:
            normalized_lookup.setdefault(slug_k, raw_key)
            normalized_lookup.setdefault(space_k, raw_key)

    term_lower = term_clean.strip().lower()
    term_slug = _to_slug(term_clean)
    if term_lower in normalized_lookup:
        return normalized_lookup[term_lower]
    if term_slug in normalized_lookup:
        return normalized_lookup[term_slug]

    tokens = [tok for tok in term_slug.split("_") if tok]
    # Check longest contiguous multi-token n-grams first, then single-token word boundary
    for window_len in range(len(tokens), 0, -1):
        for start_idx in range(0, len(tokens) - window_len + 1):
            sub_slug = "_".join(tokens[start_idx : start_idx + window_len])
            if sub_slug in normalized_lookup:
                return normalized_lookup[sub_slug]
    return None


class DomainClarifier:
    """Semantic reasoning engine for polysemy detection and question generation.

    Note: This persona provides a deterministic rule-based baseline implementation;
    in production, an LLM-backed runtime may be attached via the agent harness.
    """

    def __init__(
        self,
        pseudonymizer: Pseudonymizer | None = None,
        polysemic_definitions: Mapping[str, list[dict[str, str]]] | None = None,
    ) -> None:
        self.pseudonymizer = pseudonymizer
        self._polysemic_map: Mapping[str, list[dict[str, str]]] = (
            polysemic_definitions
            if polysemic_definitions is not None
            else KNOWN_POLYSEMIC_DEFINITIONS
        )

    def generate_questions(
        self,
        candidate_terms: Sequence[str | dict[str, Any]],
        max_questions: int = 5,
    ) -> list[ClarificationQuestion]:
        """Alias for `generate_questions_for_terms`."""
        return self.generate_questions_for_terms(
            candidate_terms=candidate_terms,
            max_questions=max_questions,
        )

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

            term_slug = _to_slug(term_clean)
            dedup_key = term_slug or term_clean.lower()
            if dedup_key in seen_terms:
                continue
            seen_terms.add(dedup_key)

            evidence_citations: list[EvidenceRef] = []
            if isinstance(item, dict) and "evidence_citations" in item:
                evidence_citations = item["evidence_citations"]

            matched_key = _match_polysemic_key(term_clean, self._polysemic_map)
            if matched_key is not None:
                options_data = self._polysemic_map[matched_key]
                slug_for_id = term_slug or _to_slug(matched_key) or "concept"
                options: list[ClarificationOption] = []
                for i, opt in enumerate(options_data, start=1):
                    options.append(
                        ClarificationOption(
                            option_id=f"opt-{slug_for_id}-{i}",
                            label=opt["label"],
                            fibo_uri=opt.get("fibo_uri"),
                            rationale=opt["rationale"],
                        )
                    )
                hash_src = (
                    f"{matched_key}:{slug_for_id}:{':'.join(opt['label'] for opt in options_data)}"
                )
                hash_suffix = hashlib.sha256(hash_src.encode("utf-8")).hexdigest()[:8]
                qid = f"q-poly-{slug_for_id}-{hash_suffix}"
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
                        matched_key=matched_key,
                    )
                )

        return questions
