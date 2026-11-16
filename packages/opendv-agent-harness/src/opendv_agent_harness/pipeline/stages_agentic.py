"""Deterministic Offline Fallback & LLM Agent Harness Stage Synthesis.

Conforms to ADR-020 and docs/25-data-driven-single-run-pipeline-design.md.
Provides deterministic stage outputs for offline test runs and SDK shims,
interfacing with LLM agent personas when active.
"""

from __future__ import annotations

import copy
import inspect
import json
import uuid
from typing import Any

import yaml
from pydantic import BaseModel

from opendv_agent_harness.models import (
    AgentPersona,
    AgentTaskSpec,
    ModelTier,
    PipelineStageId,
)
from opendv_agent_harness.pipeline.spec import (
    PipelineStepReport,
    Stage00OrchestrationPlanOutput,
    Stage01FeedDiscoveryItem,
    Stage01SourceDiscoveryOutput,
    Stage02UnstructuredDiscoveryOutput,
    Stage03ColumnTriageOutput,
    Stage04ContractSynthesisOutput,
)
from opendv_core.config import get_core_modeling_config
from opendv_core.models.graph_a import GraphAOntology

_STAGE_CONTEXT_MARKER = "\n__OPENDV_STAGE_CONTEXT_JSON__:"


def _encode_stage_context(ctx: dict[str, Any]) -> str:
    return f"{_STAGE_CONTEXT_MARKER}{json.dumps(ctx, sort_keys=True)}"


def _decode_stage_context(prompt: str) -> dict[str, Any] | None:
    if not prompt or _STAGE_CONTEXT_MARKER not in prompt:
        return None
    _, raw_json = prompt.rsplit(_STAGE_CONTEXT_MARKER, 1)
    try:
        parsed = json.loads(raw_json.strip())
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return None


class _SymbolicStageSynthesizer:
    """Offline deterministic synthesizer for pipeline stages 00 through 04."""

    @staticmethod
    def synthesize_cumulative_contract(*args: Any, **kwargs: Any) -> Any:
        from opendv_agent_harness.pipeline.contract_synthesizer import (
            _synthesize_cumulative_contract,
        )

        return _synthesize_cumulative_contract(*args, **kwargs)


_DeterministicStageSynthesizer = _SymbolicStageSynthesizer


def synthesize_stage_output_from_prompt(
    schema_cls: type[BaseModel],
    prompt: str = "",
) -> Any:
    """Synthesize a schema-valid stage output for deterministic runs."""
    ctx = _decode_stage_context(prompt)
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
        entities = []
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

        contract = {}
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

    # Generic fallback constructor
    return schema_cls.model_validate({})


def resolve_stage_model(
    stage_id: str,
    persona: str,
    setup: Any,
    harness: Any | None = None,
) -> str:
    """Resolve the LLM or symbolic model string for a stage and persona."""
    if setup is not None and getattr(setup, "agent_models", None) and persona in setup.agent_models:
        return setup.agent_models[persona]
    if harness is not None and hasattr(harness, "resolve_model"):
        try:
            tier = (
                ModelTier.HIGH_REASONING
                if persona in {"vault-first-mate", "ship-vault"}
                else ModelTier.FAST_TIER
            )
            return harness.resolve_model(tier, AgentPersona(persona))
        except (ValueError, KeyError, AttributeError, TypeError):
            pass
    if setup is not None and getattr(setup, "model_overrides", None):
        tier_key = "reasoning" if persona in {"vault-first-mate", "ship-vault"} else "fast"
        if tier_key in setup.model_overrides:
            return setup.model_overrides[tier_key]
    cfg = get_core_modeling_config()
    prov = getattr(setup, "provider", "antigravity") if setup is not None else "antigravity"
    if prov in {"claude", "claude-cli", "claude-code"}:
        return (
            cfg.default_claude_reasoning_model
            if persona in {"vault-first-mate", "ship-vault"}
            else cfg.default_claude_fast_model
        )
    if persona in {"axi-dv", "deterministic-compiler"}:
        return "symbolic"
    return (
        cfg.default_antigravity_reasoning_model
        if persona in {"vault-first-mate", "ship-vault"}
        else cfg.default_antigravity_fast_model
    )


async def execute_stage_task(
    wave_idx: int,
    stage_id: str,
    persona: str,
    harness: Any | None = None,
    on_stream_event: Any | None = None,
    run_id: str = "run_default",
    context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Any]:
    """Execute LLM agent task on harness if active and return (metrics, structured_output)."""
    metrics: dict[str, Any] = {}
    structured_out = None
    if harness is None or not hasattr(harness, "run_task"):
        return metrics, structured_out

    try:
        schema_map = {
            "STAGE_00_ORCHESTRATION": Stage00OrchestrationPlanOutput,
            "STAGE_01_SCOUT_SOURCE": Stage01SourceDiscoveryOutput,
            "STAGE_02_SCOUT_UNSTRUCTURED": Stage02UnstructuredDiscoveryOutput,
            "STAGE_03_SHIP_VAULT_PASS1": Stage03ColumnTriageOutput,
            "STAGE_04_SHIP_VAULT_PASS2": Stage04ContractSynthesisOutput,
        }
        out_schema = schema_map.get(stage_id, BaseModel)
        prompt_text = f"Execute {stage_id} for persona {persona} in wave {wave_idx}."
        if context:
            prompt_text = f"{prompt_text}{_encode_stage_context(context)}"
        t_spec = AgentTaskSpec(
            task_id=f"task-w{wave_idx}-{stage_id.lower()}",
            run_id=run_id,
            trace_id=uuid.uuid4().hex,
            persona=AgentPersona(persona),
            stage_id=PipelineStageId(stage_id),
            prompt=prompt_text,
            system_instructions=(
                f"Execute {stage_id} adhering to Data Vault 2.0 standards and enterprise ontology."
            ),
            output_schema=out_schema,
        )
        task_fn = harness.run_task
        sig = inspect.signature(task_fn)
        call_kwargs = (
            {"on_event": on_stream_event}
            if "on_event" in sig.parameters
            or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            else {}
        )
        res = (
            await task_fn(t_spec, **call_kwargs)
            if inspect.iscoroutinefunction(task_fn)
            else task_fn(t_spec, **call_kwargs)
        )
        if res and getattr(res, "structured_output", None) is not None:
            structured_out = res.structured_output
            flag_map = {
                "STAGE_00_ORCHESTRATION": "llm_structured_plan_used",
                "STAGE_01_SCOUT_SOURCE": "llm_structured_discovery_used",
                "STAGE_02_SCOUT_UNSTRUCTURED": "llm_structured_extraction_used",
                "STAGE_03_SHIP_VAULT_PASS1": "llm_structured_triage_used",
                "STAGE_04_SHIP_VAULT_PASS2": "llm_structured_contract_used",
            }
            if stage_id in flag_map:
                metrics[flag_map[stage_id]] = True
    except (TypeError, ValueError, KeyError, RuntimeError, AttributeError):
        pass

    return metrics, structured_out


def _step_tier(persona: str) -> str:
    if persona in {"vault-first-mate", "ship-vault"}:
        return "reasoning"
    if persona in {"scout-source", "scout-unstructured"}:
        return "fast"
    if persona in {"axi-dv", "deterministic-compiler", "shadow-sandbox"}:
        return "symbolic"
    p = persona.lower()
    if any(
        k in p
        for k in ("axi", "synthesizer", "architect", "reasoning", "ontology", "ship", "vault")
    ):
        return "reasoning"
    return "fast"


async def execute_stages_03_and_04(
    runner: Any,
    w_idx: int,
    w_type: str,
    s1_out: Any,
    wave_docs: list[Any],
    curr_contract: dict[str, Any] | None,
    inference_plan: Any,
    drift_findings: list[dict[str, Any]],
    wave_steps: list[Any],
    step_reports: list[Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute STAGE_03 (Pass 1 synthesis/evolution) and STAGE_04 (Pass 2 refinement)."""
    s03_id = "STAGE_03_SHIP_VAULT_PASS1"
    s03_persona = "ship-vault"
    s03_model = resolve_stage_model(s03_id, s03_persona, runner.harness_setup, runner.harness)
    runner._notify_stage_start(w_idx, s03_id, s03_persona, s03_model)
    runner._emit_stream(f"Starting {s03_id}...")

    from opendv_agent_harness.pipeline.contract_evolver import (
        evolve_contract_single_run,
        synthesize_unstructured_first_contract,
    )
    from opendv_agent_harness.pipeline.contract_synthesizer import (
        _synthesize_cumulative_contract,
        synthesize_ontology_seed_contract,
    )

    if curr_contract is None:
        if inference_plan.primary_action == "UNSTRUCTURED_FIRST":
            contract_dict = synthesize_unstructured_first_contract(
                runner.ontology, wave_docs, domain_namespace=runner.input_spec.domain_namespace
            )
        elif not s1_out.discovered_feeds and not wave_docs:
            contract_dict = synthesize_ontology_seed_contract(
                runner.ontology, domain_namespace=runner.input_spec.domain_namespace
            )
        else:
            contract_dict = _synthesize_cumulative_contract(
                ontology=runner.ontology,
                discovered_feeds=s1_out.discovered_feeds,
                deployed_contract=None,
                domain_namespace=runner.input_spec.domain_namespace,
                is_cold_start=True,
            )
    else:
        if not s1_out.discovered_feeds and not wave_docs:
            contract_dict = copy.deepcopy(curr_contract)
        else:
            epoch_tag = "v2"
            contract_dict, wave_drift = evolve_contract_single_run(
                deployed_contract=curr_contract,
                ontology=runner.ontology,
                discovered_feeds=s1_out.discovered_feeds,
                schema_epoch_tag=epoch_tag,
                cumulative_unstructured_docs=wave_docs,
                hold_manager=getattr(runner, "hold_manager", None),
                steward_hold_policy=getattr(runner.harness_setup, "steward_hold_policy", "pause"),
                run_id=runner.run_id,
            )
            drift_findings.extend(wave_drift)

    is_symbolic = (
        getattr(runner.harness_setup, "stage_output_policy", "llm_only") == "symbolic_completion"
    )

    s3_ctx = {
        "stage": s03_id,
        "primary_action": getattr(inference_plan, "primary_action", "COLD_START_N1"),
        "domain_namespace": runner.input_spec.domain_namespace,
        "column_triage_ledger": contract_dict.get("column_triage_ledger", []),
    }
    s03_metrics, s03_struct = await execute_stage_task(
        w_idx,
        s03_id,
        s03_persona,
        runner.harness,
        runner.on_stream_event,
        runner.run_id,
        context=s3_ctx,
    )
    if getattr(inference_plan, "primary_action", "") == "VERIFIED_NO_OP":
        pass
    elif s03_struct is not None and getattr(s03_struct, "column_triage_ledger", None):
        for entry in s03_struct.column_triage_ledger:
            e_dict = entry.model_dump(mode="json") if hasattr(entry, "model_dump") else entry
            b_tbl = e_dict.get("bronze_table")
            c_name = e_dict.get("column_name")
            ledger = contract_dict.setdefault("column_triage_ledger", [])
            existing = next(
                (
                    e
                    for e in ledger
                    if e.get("bronze_table") == b_tbl and e.get("column_name") == c_name
                ),
                None,
            )
            if existing:
                existing.update(e_dict)
            else:
                ledger.append(e_dict)
    elif (
        s03_struct is None or not getattr(s03_struct, "column_triage_ledger", None)
    ) and is_symbolic:
        runner.record_fallback(
            wave_index=w_idx,
            stage_id=PipelineStageId.STAGE_03_SHIP_VAULT_PASS1,
            details={"wave_index": w_idx, "completed_fields": ["column_triage_ledger"]},
        )
    else:
        step_03 = PipelineStepReport(
            stage_index=3,
            stage_id=s03_id,
            stage_name=s03_id,
            persona=s03_persona,
            model_tier=_step_tier(s03_persona),
            model_used=s03_model,
            status="ERROR",
            verdict="ERROR",
            duration_ms=25.0,
            summary=(
                "Stage 03 Pass 1 Column Triage LLM output is missing or invalid "
                "under llm_only policy."
            ),
            metrics=s03_metrics,
            wave_index=w_idx,
        )
        wave_steps.append(step_03)
        step_reports.append(step_03)
        runner._notify_stage_complete(w_idx, step_03)
        return None, None

    w_dir = runner.output_dir / "waves" / f"wave_{w_idx}"
    w_dir.mkdir(parents=True, exist_ok=True)
    attempt1_contract = copy.deepcopy(contract_dict)
    (w_dir / f"wave_{w_idx}_contract_attempt_1.yaml").write_text(
        yaml.safe_dump(attempt1_contract, sort_keys=False), encoding="utf-8"
    )

    step_03 = PipelineStepReport(
        stage_index=3,
        stage_id=s03_id,
        stage_name=s03_id,
        persona=s03_persona,
        model_tier=_step_tier(s03_persona),
        model_used=s03_model,
        status="PASS",
        verdict="PASS",
        duration_ms=25.0,
        summary="Synthesized candidate contract pass 1.",
        metrics=s03_metrics,
        wave_index=w_idx,
    )
    wave_steps.append(step_03)
    step_reports.append(step_03)
    runner._notify_stage_complete(w_idx, step_03)

    # STAGE 04: Contract Synthesis Pass 2
    s04_id = "STAGE_04_SHIP_VAULT_PASS2"
    s04_persona = "ship-vault"
    s04_model = resolve_stage_model(s04_id, s04_persona, runner.harness_setup, runner.harness)
    runner._notify_stage_start(w_idx, s04_id, s04_persona, s04_model)

    s4_ctx = {
        "stage": s04_id,
        "domain_namespace": runner.input_spec.domain_namespace,
        "pass1_column_triage_ledger": contract_dict.get("column_triage_ledger", []),
        "contract": contract_dict,
        "deployed_contract": curr_contract,
        "discovered_feeds": [
            f.model_dump(mode="json") if hasattr(f, "model_dump") else f
            for f in getattr(s1_out, "discovered_feeds", [])
        ],
        "ontology": (
            runner.ontology.model_dump(mode="json")
            if hasattr(runner.ontology, "model_dump")
            else runner.ontology
        ),
    }
    s04_metrics, s04_struct = await execute_stage_task(
        w_idx,
        s04_id,
        s04_persona,
        runner.harness,
        runner.on_stream_event,
        runner.run_id,
        context=s4_ctx,
    )
    cand_contract = None
    if s04_struct is not None:
        cand_contract = getattr(s04_struct, "contract", None) or getattr(
            s04_struct, "evolved_contract", None
        )
    if getattr(inference_plan, "primary_action", "") == "VERIFIED_NO_OP":
        pass
    elif cand_contract and isinstance(cand_contract, dict):
        cand_dict = (
            cand_contract.model_dump(mode="json")
            if hasattr(cand_contract, "model_dump")
            else cand_contract
        )
        orig_ledger = contract_dict.get("column_triage_ledger", [])
        cand_ledger = cand_dict.setdefault("column_triage_ledger", [])
        existing_cols = {
            (entry.get("bronze_table"), entry.get("column_name"))
            for entry in cand_ledger
            if isinstance(entry, dict)
        }
        for entry in orig_ledger:
            if isinstance(entry, dict):
                key = (entry.get("bronze_table"), entry.get("column_name"))
                if key not in existing_cols:
                    cand_ledger.append(entry)
                    existing_cols.add(key)

        if curr_contract is not None:
            from opendv_axi_verifier.verifiers.alg_a7_evolution_lock import (
                verify_additive_evolution_invariants,
            )

            evo_violations = verify_additive_evolution_invariants(curr_contract, cand_dict)
            if not evo_violations:
                contract_dict = cand_dict
        else:
            contract_dict = cand_dict
    elif is_symbolic:
        runner.record_fallback(
            wave_index=w_idx,
            stage_id=PipelineStageId.STAGE_04_SHIP_VAULT_PASS2,
            details={"wave_index": w_idx, "completed_fields": ["contract"]},
        )
    else:
        step_04 = PipelineStepReport(
            stage_index=4,
            stage_id=s04_id,
            stage_name=s04_id,
            persona=s04_persona,
            model_tier=_step_tier(s04_persona),
            model_used=s04_model,
            status="ERROR",
            verdict="ERROR",
            duration_ms=10.0,
            summary=(
                "Stage 04 Contract Synthesis LLM output is missing or invalid "
                "under llm_only policy."
            ),
            metrics=s04_metrics,
            wave_index=w_idx,
        )
        wave_steps.append(step_04)
        step_reports.append(step_04)
        runner._notify_stage_complete(w_idx, step_04)
        return None, None

    step_04 = PipelineStepReport(
        stage_index=4,
        stage_id=s04_id,
        stage_name=s04_id,
        persona=s04_persona,
        model_tier=_step_tier(s04_persona),
        model_used=s04_model,
        status="PASS",
        verdict="PASS",
        duration_ms=10.0,
        summary="Refined contract pass 2.",
        metrics=s04_metrics,
        wave_index=w_idx,
    )
    wave_steps.append(step_04)
    step_reports.append(step_04)
    runner._notify_stage_complete(w_idx, step_04)

    return contract_dict, attempt1_contract


__all__ = [
    "Stage00OrchestrationPlanOutput",
    "Stage01FeedDiscoveryItem",
    "Stage01SourceDiscoveryOutput",
    "Stage02UnstructuredDiscoveryOutput",
    "Stage03ColumnTriageOutput",
    "Stage04ContractSynthesisOutput",
    "_SymbolicStageSynthesizer",
    "_decode_stage_context",
    "_encode_stage_context",
    "_step_tier",
    "execute_stage_task",
    "execute_stages_03_and_04",
    "resolve_stage_model",
    "synthesize_stage_output_from_prompt",
]
