"""Inline prompt construction and deterministic context decoding for agentic stages."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

from opendv_agent_harness.pipeline.spec import (
    Stage00OrchestrationPlanOutput,
    Stage01FeedDiscoveryItem,
    Stage01SourceDiscoveryOutput,
    Stage02UnstructuredDiscoveryOutput,
    Stage03ColumnTriageOutput,
    Stage04ContractSynthesisOutput,
)
from opendv_core.models.graph_a import GraphAOntology

_LEGACY_CONTEXT_MARKER = "\n__OPENDV_STAGE_CONTEXT_JSON__:"
_INLINE_JSON_BLOCK_RE = re.compile(
    r"###\s+Stage Context\s*\r?\n```json\s*\r?\n(.*?)\r?\n```",
    re.DOTALL,
)


def build_inline_stage_prompt(
    wave_idx: int,
    stage_id: str,
    persona: str,
    context: dict[str, Any] | None = None,
) -> str:
    """Format the stage prompt with readable inline structured context."""
    base = f"Execute {stage_id} for persona {persona} in wave {wave_idx}."
    if not context:
        return base
    rendered_json = json.dumps(context, indent=2, sort_keys=True, default=str)
    return (
        f"{base}\n\n"
        f"### Stage Context\n"
        f"```json\n{rendered_json}\n```"
    )


def decode_inline_stage_context(prompt: str) -> dict[str, Any] | None:
    """Extract structured context JSON from an inline stage prompt."""
    if not prompt:
        return None
    match = _INLINE_JSON_BLOCK_RE.search(prompt)
    raw_json = ""
    if match:
        raw_json = match.group(1).strip()
    elif _LEGACY_CONTEXT_MARKER in prompt:
        _, raw_json = prompt.rsplit(_LEGACY_CONTEXT_MARKER, 1)
        raw_json = raw_json.strip()
    if not raw_json:
        return None
    try:
        parsed = json.loads(raw_json)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return None


def synthesize_stage_output_from_prompt(
    schema_cls: type[BaseModel],
    prompt: str = "",
) -> Any:
    """Synthesize a schema-valid stage output for deterministic shim runs."""
    ctx = decode_inline_stage_context(prompt)
    s_name = schema_cls.__name__

    if s_name == "Stage00OrchestrationPlanOutput":
        if ctx is not None:
            from opendv_agent_harness.pipeline.mode_detector import infer_execution_plan
            from opendv_agent_harness.pipeline.spec import (
                PipelineSourceFeedInput,
                PipelineUnstructuredDocInput,
            )

            sources = [PipelineSourceFeedInput.model_validate(s) for s in ctx.get("sources", [])]
            docs = [
                PipelineUnstructuredDocInput.model_validate(d) for d in ctx.get("documents", [])
            ]
            contract = ctx.get("deployed_contract")
            plan = infer_execution_plan(contract=contract, sources=sources, documents=docs)
            return Stage00OrchestrationPlanOutput(
                plan_summary=plan.explanation,
                inferred_action=plan.primary_action,
                per_table_plan={t.table_name: t.action for t in plan.table_actions},
            )
        return Stage00OrchestrationPlanOutput(
            plan_summary="Default deterministic orchestration plan.",
            inferred_action="COLD_START_N1",
        )

    if s_name == "Stage01SourceDiscoveryOutput":
        items: list[Stage01FeedDiscoveryItem] = []
        if ctx is not None and "sources" in ctx:
            for s in ctx.get("sources", []):
                tname = s.get("table_name") or "table"
                ss = s.get("source_system") or "source"
                pks = list(s.get("business_keys") or [f"{tname}_id"])
                items.append(
                    Stage01FeedDiscoveryItem(
                        source_system=ss,
                        table_name=tname,
                        column_count=len(pks) + 2,
                        row_count=100,
                        primary_keys=pks,
                        foreign_keys=[],
                        column_descriptors=[
                            {"name": pk, "physical_type": "VARCHAR(64)", "is_primary_key": True}
                            for pk in pks
                        ],
                    )
                )
        return Stage01SourceDiscoveryOutput(discovered_feeds=items)

    if s_name == "Stage02UnstructuredDiscoveryOutput":
        entities: list[str] = []
        if ctx is not None and "ontology" in ctx:
            ont = GraphAOntology.model_validate(ctx["ontology"])
            entities = [cbc.concept_name for cbc in ont.canonical_business_concepts]
        return Stage02UnstructuredDiscoveryOutput(
            discovered_entities=entities,
            spans_extracted=len(entities),
        )

    if s_name == "Stage03ColumnTriageOutput":
        ledger = []
        if ctx is not None and "column_triage_ledger" in ctx:
            ledger = ctx["column_triage_ledger"]
        return Stage03ColumnTriageOutput(
            triage_ledger=ledger,
            column_triage_ledger=ledger,
            ontology_deltas_proposed=[],
        )

    if s_name == "Stage04ContractSynthesisOutput":
        from opendv_agent_harness.pipeline.contract_synthesizer import (
            _synthesize_cumulative_contract,
        )

        contract: dict[str, Any] = {}
        if ctx is not None and "contract" in ctx and ctx["contract"]:
            contract = ctx["contract"]
        elif ctx is not None and "ontology" in ctx and ctx["ontology"]:
            ont = GraphAOntology.model_validate(ctx["ontology"])
            contract = _synthesize_cumulative_contract(
                ontology=ont,
                discovered_feeds=[],
                deployed_contract=ctx.get("deployed_contract"),
            )
        return Stage04ContractSynthesisOutput(contract=contract)

    return schema_cls.model_validate({})


__all__ = [
    "build_inline_stage_prompt",
    "decode_inline_stage_context",
    "synthesize_stage_output_from_prompt",
]
