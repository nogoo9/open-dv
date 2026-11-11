"""`term-scout` Persona Builder: Fast Information-Extraction & Candidate Harvester."""

from __future__ import annotations

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
from opendv_contracts.dtos.ingestion import EvidenceChunk, TableStructure
from opendv_contracts.dtos.ontology import EvidenceRef

TERM_SCOUT_SYSTEM_INSTRUCTIONS: str = (
    "You are `@term-scout`. Extract candidate concepts, attributes, and enums."
)

_COMMON_BUSINESS_TERMS: tuple[str, ...] = (
    "legal entity",
    "counterparty",
    "account",
    "position",
    "instrument",
    "security",
    "bond",
    "equity",
    "fund",
    "facility",
    "portfolio",
    "trade",
    "transaction",
    "customer",
    "party",
    "agreement",
    "contract",
    "currency",
)

_PRECOMPILED_PATTERNS: dict[str, re.Pattern[str]] = {
    term: re.compile(rf"\b{re.escape(term)}\b", flags=re.IGNORECASE)
    for term in _COMMON_BUSINESS_TERMS
}


def build_term_scout_subagent_spec() -> SubagentSpec:
    """Build the canonical `SubagentSpec` for the `@term-scout` leaf scout (`fast` tier)."""
    profile = get_persona_capability(AgentPersona.TERM_SCOUT)
    tool_names = tuple(t.name for t in get_tools_for_persona(AgentPersona.TERM_SCOUT))
    return SubagentSpec(
        name=AgentPersona.TERM_SCOUT.value,
        persona=AgentPersona.TERM_SCOUT,
        description="@term-scout fast harvester.",
        system_instructions=TERM_SCOUT_SYSTEM_INSTRUCTIONS,
        model_tier=profile.default_model_tier,
        allowed_tool_names=tool_names,
        allowed_subagents=profile.allowed_subagents,
        read_only=profile.sandbox_policy.read_only_workspace,
    )


def build_term_scout_task_spec(
    prompt: str = "Extract candidate business concepts and attributes from evidence.",
    *,
    run_id: str = "run-term-scout",
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    task_id: str | None = None,
    stage_id: PipelineStageId = PipelineStageId.STAGE_02_SCOUT_UNSTRUCTURED,
    model_tier: ModelTier | None = None,
    model_override: str | None = None,
    tools: Sequence[AgentToolDefinition[Any, Any]] | None = None,
    subagents: Sequence[SubagentSpec] = (),
    workspace_root: Path | None = None,
    input_data: Any = None,
    expected_output_type: type[OutputT] | None = None,
    system_instructions: str = TERM_SCOUT_SYSTEM_INSTRUCTIONS,
) -> AgentTaskSpec[Any]:
    """Build the canonical `AgentTaskSpec` for `@term-scout`."""
    profile = get_persona_capability(AgentPersona.TERM_SCOUT)
    eff_trace = trace_id or uuid.uuid4().hex
    eff_tools = (
        tuple(tools) if tools is not None else tuple(get_tools_for_persona(AgentPersona.TERM_SCOUT))
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
        "persona": AgentPersona.TERM_SCOUT,
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


def _is_explicitly_public(taint: Any) -> bool:
    """Return True if and only if taint classification is explicitly public.

    Fails closed: None (missing/unclassified), internal, confidential, or restricted
    all evaluate to False (MAJ-01, MIN-01).
    """
    if taint is None:
        return False
    val = taint.value if hasattr(taint, "value") else str(taint)
    return str(val).lower().strip() == "public"


class TermScout:
    """Execution engine for `@term-scout` candidate entity harvesting.

    Note: This persona provides a deterministic rule-based baseline implementation;
    in production, an LLM-backed runtime may be attached via the agent harness.
    """

    def __init__(self, pseudonymizer: Pseudonymizer | None = None) -> None:
        self.pseudonymizer = pseudonymizer

    def extract_candidates(
        self,
        evidence_chunks: Sequence[EvidenceChunk] | None = None,
        tables: Sequence[TableStructure] | None = None,
    ) -> list[dict[str, Any]]:
        """Harvest candidate entity terms, attributes, and source citations."""
        candidates: list[dict[str, Any]] = []
        seen_terms: set[str] = set()

        # Step 1: Extract from text chunks
        term_citations: dict[str, list[EvidenceRef]] = {}

        for chunk in evidence_chunks or []:
            # Fail closed: Only explicit 'public' taint is untainted (MIN-01)
            is_tainted = not _is_explicitly_public(chunk.taint)

            # Mask entire chunk text first before slicing snippets (MAJ-02)
            masked_full_text: str | None
            if is_tainted:
                if self.pseudonymizer:
                    raw_sensitive = (
                        chunk.metadata.get("sensitive_entities") if chunk.metadata else None
                    )
                    if isinstance(raw_sensitive, (list, tuple)):
                        sensitive = [str(s).strip() for s in raw_sensitive if str(s).strip()]
                    else:
                        sensitive = []

                    if sensitive:
                        masked_full_text = self.pseudonymizer.pseudonymize_text(
                            chunk.text, sensitive
                        )
                        # Post-masking safety verification (fail-closed, MAJ-01):
                        # Ensure no listed sensitive term remains anywhere in masked text
                        lower_masked = masked_full_text.lower()
                        for s_term in sensitive:
                            if s_term.lower() in lower_masked:
                                masked_full_text = None
                                break
                    else:
                        masked_full_text = None
                else:
                    masked_full_text = None
            else:
                masked_full_text = chunk.text

            # Search on original source text so character offsets are exact
            for term, pattern in _PRECOMPILED_PATTERNS.items():
                m = pattern.search(chunk.text)
                if m:
                    t_title = term.title()
                    chunk_base = chunk.start_char or 0
                    char_start = chunk_base + m.start()
                    char_end = chunk_base + m.end()

                    if masked_full_text is None:
                        snippet = None
                    else:
                        m_masked = pattern.search(masked_full_text)
                        if m_masked:
                            start_win = max(0, m_masked.start() - 25)
                            while start_win > 0 and not masked_full_text[start_win].isspace():
                                start_win -= 1
                            end_win = min(len(masked_full_text), m_masked.end() + 25)
                            while (
                                end_win < len(masked_full_text)
                                and not masked_full_text[end_win].isspace()
                            ):
                                end_win += 1
                            snippet = masked_full_text[start_win:end_win].strip()
                        else:
                            snippet = None

                    citation = EvidenceRef(
                        evidence_type="document_span",
                        reference_uri=f"urn:opendv:evidence:chunk:{chunk.chunk_id}",
                        snippet=snippet,
                        char_start=char_start,
                        char_end=char_end,
                        confidence=1.0,
                    )
                    if t_title not in term_citations:
                        term_citations[t_title] = []
                    term_citations[t_title].append(citation)

        for t_title, citations in term_citations.items():
            seen_terms.add(t_title.lower())
            candidates.append(
                {
                    "term": t_title,
                    "suggested_type": "entity",
                    "evidence_citations": citations,
                }
            )

        # Step 2: Extract from tables
        for tbl in tables or []:
            tbl_taint = getattr(tbl, "taint", None)
            if tbl_taint is None and hasattr(tbl, "metadata") and tbl.metadata:
                tbl_taint = tbl.metadata.get("taint")
            # Fail closed: Only explicit 'public' taint is untainted.
            # Missing taint (None), internal, confidential, or restricted are tainted (MAJ-01).
            tbl_tainted = not _is_explicitly_public(tbl_taint)

            headers = tbl.headers or []
            rows = tbl.rows or []
            for col_idx, header in enumerate(headers):
                clean = header.strip()
                if self.pseudonymizer:
                    clean = self.pseudonymizer.de_pseudonymize(clean)
                lower = clean.lower()
                entity_term: str | None = None
                if lower.endswith("_id"):
                    entity_term = lower.removesuffix("_id").replace("_", " ").title()
                elif lower.endswith("_pk"):
                    entity_term = lower.removesuffix("_pk").replace("_", " ").title()
                elif lower.endswith("_key"):
                    entity_term = lower.removesuffix("_key").replace("_", " ").title()

                if entity_term:
                    if entity_term.lower() not in seen_terms:
                        seen_terms.add(entity_term.lower())
                        tbl_id = getattr(tbl, "table_id", None) or "table"
                        candidates.append(
                            {
                                "term": entity_term,
                                "suggested_type": "entity",
                                "evidence_citations": [
                                    EvidenceRef(
                                        evidence_type="column_profile",
                                        reference_uri=f"urn:opendv:evidence:table:{tbl_id}#{header}",
                                        confidence=1.0,
                                    )
                                ],
                            }
                        )
                elif lower.endswith(("_code", "_status", "_type")):
                    if lower.endswith("_code"):
                        ref_term = lower.removesuffix("_code").replace("_", " ").title()
                    elif lower.endswith("_status"):
                        ref_term = lower.removesuffix("_status").replace("_", " ").title()
                    else:
                        ref_term = lower.removesuffix("_type").replace("_", " ").title()

                    # Extract codes from table rows with fail-closed confidential
                    # taint guard (MAJ-01)
                    if tbl_tainted:
                        if not self.pseudonymizer:
                            codes = []
                        else:
                            raw_sensitive = (
                                tbl.metadata.get("sensitive_entities")
                                if hasattr(tbl, "metadata") and tbl.metadata
                                else None
                            )
                            if isinstance(raw_sensitive, (list, tuple)) and raw_sensitive:
                                sensitive = [
                                    str(s).strip() for s in raw_sensitive if str(s).strip()
                                ]
                            else:
                                sensitive = []

                            if not sensitive:
                                codes = []  # Fail closed: tainted table with no sensitive list
                            else:
                                raw_codes_set: set[str] = set()
                                masking_failed = False
                                for row in rows:
                                    if col_idx < len(row) and row[col_idx] is not None:
                                        cell_val = str(row[col_idx]).strip()
                                        if not cell_val:
                                            continue
                                        masked_val = self.pseudonymizer.pseudonymize_text(
                                            cell_val, sensitive
                                        )
                                        lower_masked = masked_val.lower()
                                        # Verify no sensitive entity survived masking
                                        if any(s.lower() in lower_masked for s in sensitive):
                                            masking_failed = True
                                            break
                                        # If cell contained sensitive entity (masked to pseudo_*),
                                        # fail closed so confidential entity never leaks into enum
                                        if "pseudo_" in lower_masked:
                                            masking_failed = True
                                            break
                                        raw_codes_set.add(masked_val)

                                if masking_failed or not raw_codes_set:
                                    codes = []
                                else:
                                    codes = (
                                        sorted(raw_codes_set) if len(raw_codes_set) <= 100 else []
                                    )
                    else:
                        raw_codes = {
                            str(row[col_idx]).strip()
                            for row in rows
                            if col_idx < len(row)
                            and row[col_idx] is not None
                            and str(row[col_idx]).strip()
                        }
                        codes = sorted(raw_codes) if len(raw_codes) <= 100 else []

                    if ref_term.lower() not in seen_terms:
                        seen_terms.add(ref_term.lower())
                        tbl_id = getattr(tbl, "table_id", None) or "table"
                        candidates.append(
                            {
                                "term": ref_term,
                                "suggested_type": "enumeration",
                                "codes": codes,
                                "evidence_citations": [
                                    EvidenceRef(
                                        evidence_type="column_profile",
                                        reference_uri=f"urn:opendv:evidence:table:{tbl_id}#{header}",
                                        confidence=1.0,
                                    )
                                ],
                            }
                        )

        return candidates
