"""Algorithm Mode Detector & 7-Row Data-Driven Inference Engine.

Conforms to ADR-018, ADR-019, and docs/25-data-driven-single-run-pipeline-design.md (§1.2).
Eliminates user wave_type everywhere, deriving per-table, per-doc, and overall run plans
strictly from (blueprint.contract, sources, documents).
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Sequence
from typing import Any

import yaml

from opendv_agent_harness.pipeline.spec import (
    PipelineSourceFeedInput,
    PipelineUnstructuredDocInput,
)
from opendv_axi_verifier.codemods.alg_a9a_schema_drift import (
    detect_schema_drift_from_contract,
)
from opendv_contracts.dtos.pipeline import (
    DocExecutionActionDTO,
    InferredExecutionPlanDTO,
    TableExecutionActionDTO,
)
from opendv_core.models.contract_v5 import NoMistakeDataVault2ContractV5
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.models.vault_blueprint import VaultBlueprint


def _get_deployed_tables_and_columns(
    contract: NoMistakeDataVault2ContractV5 | dict[str, Any] | None,
) -> tuple[set[str], dict[str, set[str]], dict[str, dict[str, str]]]:
    """Extract triaged table names, columns, and SQL types from existing contract."""
    if not contract:
        return set(), {}, {}

    if hasattr(contract, "model_dump"):
        c_dict = contract.model_dump(mode="json")
    elif isinstance(contract, dict):
        c_dict = contract
    else:
        c_dict = {}

    tables: set[str] = set()
    cols_per_table: dict[str, set[str]] = {}
    types_per_table: dict[str, dict[str, str]] = {}

    for e in c_dict.get("column_triage_ledger") or []:
        if not isinstance(e, dict):
            continue
        t = str(e.get("bronze_table") or e.get("source_table") or "")
        c = str(e.get("column_name", ""))
        if t and c:
            t_clean = t.lower()
            tables.add(t_clean)
            tables.add(t_clean.split(".")[-1])
            cols_per_table.setdefault(t_clean, set()).add(c)
            cols_per_table.setdefault(t_clean.split(".")[-1], set()).add(c)
            stype = e.get("source_sql_type")
            if stype:
                types_per_table.setdefault(t_clean, {})[c] = str(stype)
                types_per_table.setdefault(t_clean.split(".")[-1], {})[c] = str(stype)

    return tables, cols_per_table, types_per_table


def infer_table_action(
    source_system: str,
    table_name: str,
    incoming_columns: list[str] | set[str],
    incoming_types: dict[str, str] | None,
    contract: NoMistakeDataVault2ContractV5 | dict[str, Any] | None,
) -> TableExecutionActionDTO:
    """Classify a single incoming table against existing contract per 7-row matrix."""
    if contract is None:
        return TableExecutionActionDTO(
            source_system=source_system,
            table_name=table_name,
            action="GREENFIELD_SYNTHESIS",
            matched_existing_entity=None,
            drift_findings=[],
        )

    deployed_tables, _, _ = _get_deployed_tables_and_columns(contract)
    t_key = table_name.lower()
    t_base = t_key.split(".")[-1]
    qual_key = f"{source_system.lower()}.{t_base}"

    is_deployed = qual_key in deployed_tables or t_key in deployed_tables
    if not is_deployed and t_base in deployed_tables:
        conflicting = any(
            dt.endswith(f".{t_base}") and not dt.startswith(f"{source_system.lower()}.")
            for dt in deployed_tables
            if "." in dt
        )
        if not conflicting:
            is_deployed = True

    if not is_deployed:
        return TableExecutionActionDTO(
            source_system=source_system,
            table_name=table_name,
            action="INCREMENTAL_ONBOARDING",
            matched_existing_entity=None,
            drift_findings=[],
        )

    c_dict = (
        contract.model_dump(mode="json")
        if hasattr(contract, "model_dump")
        else (contract if isinstance(contract, dict) else {})
    )
    matched_sat: str | None = None
    for sat in c_dict.get("satellites") or []:
        if isinstance(sat, dict):
            src_b = str(sat.get("source_bronze_table") or "")
            if src_b.lower() in (t_key, t_base):
                matched_sat = sat.get("name")
                break
    if not matched_sat:
        for e in c_dict.get("column_triage_ledger") or []:
            if isinstance(e, dict):
                t = str(e.get("bronze_table") or e.get("source_table") or "").lower()
                tgt = str(e.get("target_dv_entity") or "")
                if t in (t_key, t_base) and tgt.startswith("sat_"):
                    matched_sat = tgt
                    break
    matched_entity = matched_sat or f"sat_{t_base}"

    # Deployed table -> Check for Intra-Source Drift (Alg A.9a)
    drift_findings = []
    if incoming_columns:
        findings = detect_schema_drift_from_contract(
            contract=contract,
            source_table=table_name,
            candidate_columns=list(incoming_columns),
            candidate_types=incoming_types or {},
            target_entity=matched_entity,
        )
        drift_findings = [f.to_dict() for f in findings]

    action: Any = "INTRA_SOURCE_DRIFT" if drift_findings else "NO_OP"
    return TableExecutionActionDTO(
        source_system=source_system,
        table_name=table_name,
        action=action,
        matched_existing_entity=matched_entity,
        drift_findings=drift_findings,
    )


def infer_execution_plan(
    blueprint: VaultBlueprint | dict[str, Any] | None = None,
    contract: NoMistakeDataVault2ContractV5 | dict[str, Any] | None = None,
    sources: Sequence[PipelineSourceFeedInput | dict[str, Any]] = (),
    documents: Sequence[PipelineUnstructuredDocInput | dict[str, Any]] = (),
) -> InferredExecutionPlanDTO:
    """Realize the normative 7-row data-driven inference matrix (zero wave_type)."""
    # 1. Resolve effective contract
    eff_contract: NoMistakeDataVault2ContractV5 | dict[str, Any] | None = contract
    if eff_contract is None and blueprint is not None:
        if isinstance(blueprint, VaultBlueprint):
            eff_contract = blueprint.contract
        elif isinstance(blueprint, dict):
            eff_contract = blueprint.get("contract")

    has_contract = eff_contract is not None
    n_sources = len(sources)
    n_docs = len(documents)

    plan_id = f"plan_{uuid.uuid4().hex[:12]}"
    now_utc = datetime.datetime.now(datetime.UTC).isoformat()

    table_actions: list[TableExecutionActionDTO] = []
    doc_actions: list[DocExecutionActionDTO] = []

    # 2. Derive per-table actions
    expanded_items: list[tuple[str, str, list[str], dict[str, str]]] = []
    for src in sources:
        did_expand = False
        try:
            from opendv_sources.models.specs import SourceSpec
            from opendv_sources.registry.source_registry import get_source_registry
            from opendv_sources.spi.catalog import CatalogSourceAdapter

            spec_obj = getattr(src, "source", None) or getattr(src, "spec", None)
            if spec_obj is None and isinstance(src, dict):
                spec_obj = src.get("source") or src.get("spec")
            if not isinstance(spec_obj, SourceSpec):
                uri_val = getattr(src, "uri", None) or getattr(src, "data_path", None) or (
                    src.get("uri") or src.get("data_path") if isinstance(src, dict) else None
                )
                adapter_val = getattr(src, "adapter", None) or (
                    src.get("adapter") if isinstance(src, dict) else None
                )
                if not adapter_val and uri_val:
                    u_str = str(uri_val).lower()
                    if u_str.endswith((".sqlite", ".sqlite3", ".db")) or u_str.startswith("sqlite://"):
                        adapter_val = "sqlite"
                    elif u_str.startswith(("databricks://", "uc://")):
                        adapter_val = "databricks"
                ts_val = getattr(src, "table_selection", None) or (
                    src.get("table_selection") if isinstance(src, dict) else None
                )
                ns_val = getattr(src, "namespace", None) or (
                    src.get("namespace") if isinstance(src, dict) else None
                )
                opts_val = getattr(src, "options", None) or (
                    src.get("options") if isinstance(src, dict) else None
                )
                if adapter_val and uri_val:
                    spec_obj = SourceSpec(
                        adapter=adapter_val,
                        uri=str(uri_val),
                        table_selection=ts_val,
                        namespace=ns_val,
                        options=dict(opts_val or {}),
                    )
            elif isinstance(spec_obj, SourceSpec):
                ts_val = getattr(src, "table_selection", None) or (
                    src.get("table_selection") if isinstance(src, dict) else None
                )
                ns_val = getattr(src, "namespace", None) or (
                    src.get("namespace") if isinstance(src, dict) else None
                )
                if spec_obj.table_selection is None and ts_val:
                    spec_obj = spec_obj.model_copy(update={"table_selection": ts_val})
                if spec_obj.namespace is None and ns_val:
                    spec_obj = spec_obj.model_copy(update={"namespace": ns_val})

            if isinstance(spec_obj, SourceSpec):
                ad_obj = get_source_registry().get_tabular_adapter(spec_obj.adapter)
                if isinstance(ad_obj, CatalogSourceAdapter):
                    r_tables = ad_obj.expand_feed(spec_obj)
                    if r_tables:
                        did_expand = True
                        for rt in r_tables:
                            expanded_items.append((
                                rt.source_system or "source",
                                rt.table_name or "table",
                                [c.name for c in rt.schema_descriptor.columns],
                                {c.name: c.physical_type for c in rt.schema_descriptor.columns},
                            ))
        except (
            KeyError,
            ValueError,
            OSError,
            RuntimeError,
            AttributeError,
            TypeError,
            ImportError,
        ):
            did_expand = False

        if not did_expand:
            incoming_types: dict[str, str] = {}
            if isinstance(src, PipelineSourceFeedInput):
                ss = src.source_system or "source"
                tn = src.table_name or "table"
                cols = list(
                    src.columns
                    or [cd.get("name") for cd in src.column_descriptors if cd.get("name")]
                    or src.business_keys
                )
                if src.column_types:
                    incoming_types = dict(src.column_types)
                elif src.column_descriptors:
                    incoming_types = {
                        cd["name"]: cd.get("physical_type", "VARCHAR")
                        for cd in src.column_descriptors
                        if cd.get("name")
                    }
            elif isinstance(src, dict):
                ss = str(src.get("source_system") or "source")
                tn = str(src.get("table_name") or src.get("source_name") or "table")
                raw_descs = src.get("column_descriptors") or []
                cols = list(
                    src.get("columns")
                    or [
                        cd.get("name")
                        for cd in raw_descs
                        if isinstance(cd, dict) and cd.get("name")
                    ]
                    or src.get("business_keys")
                    or []
                )
                if src.get("column_types"):
                    incoming_types = dict(src["column_types"])
                elif raw_descs:
                    incoming_types = {
                        cd["name"]: cd.get("physical_type", "VARCHAR")
                        for cd in raw_descs
                        if isinstance(cd, dict) and cd.get("name")
                    }
            else:
                ss, tn, cols = "source", "table", []
            expanded_items.append((ss, tn, cols, incoming_types))

    for ss, tn, cols, inc_types in expanded_items:
        t_action = infer_table_action(
            source_system=ss,
            table_name=tn,
            incoming_columns=cols,
            incoming_types=inc_types or None,
            contract=eff_contract,
        )
        table_actions.append(t_action)

    # 3. Derive per-doc actions
    for idx, doc in enumerate(documents):
        if isinstance(doc, PipelineUnstructuredDocInput):
            d_id = doc.doc_id or f"doc_{idx}"
            th = doc.target_hub or "hub_document"
        elif isinstance(doc, dict):
            d_id = str(doc.get("doc_id") or f"doc_{idx}")
            th = str(doc.get("target_hub") or "hub_document")
        else:
            d_id, th = f"doc_{idx}", "hub_document"

        action_type = (
            "GROUND_ONTOLOGY_CONCEPT"
            if not has_contract and n_sources == 0
            else "EXTRACT_SPANS_AND_LINKS"
        )
        doc_actions.append(
            DocExecutionActionDTO(
                doc_id=d_id,
                action=action_type,
                target_hub=th,
            )
        )

    # 4. Map to 7-row matrix
    if not has_contract:
        if n_sources == 0 and n_docs == 0:
            primary = "COLD_START_N1"
            expl = (
                "Row 1: Ontology-Only Seed Synthesis directly from concepts and key authority "
                "tiers (zero data)."
            )
        elif n_sources > 0 and n_docs == 0:
            primary = "COLD_START_N1"
            expl = (
                f"Row 2: Greenfield Structured-Only design across {n_sources} source tables."
            )
        elif n_sources == 0 and n_docs > 0:
            primary = "UNSTRUCTURED_FIRST"
            expl = (
                f"Row 3: Unstructured-First design synthesizing hub_document and extraction "
                f"links for {n_docs} docs."
            )
        else:
            primary = "COLD_START_N1"
            expl = (
                f"Row 4: Greenfield Multi-Source Hybrid design combining {n_sources} tables "
                f"and {n_docs} docs."
            )
    else:
        if n_sources == 0 and n_docs == 0:
            primary = "VERIFIED_NO_OP"
            expl = "Row 5: Deterministic Verification Pass on existing contract with 0 mutations."
        else:
            actions_set = {t.action for t in table_actions}
            if n_docs > 0 and (
                "INCREMENTAL_ONBOARDING" in actions_set or "INTRA_SOURCE_DRIFT" in actions_set
            ):
                primary = "HYBRID_PER_SOURCE"
                expl = (
                    "Row 7: Hybrid Per-Source Plan combining multi-connector onboarding, "
                    "drift triage, and docs."
                )
            elif "INCREMENTAL_ONBOARDING" in actions_set and "INTRA_SOURCE_DRIFT" in actions_set:
                primary = "HYBRID_PER_SOURCE"
                expl = (
                    "Row 7: Hybrid Per-Source Plan combining incremental onboarding and "
                    "intra-source schema drift."
                )
            elif "INTRA_SOURCE_DRIFT" in actions_set:
                primary = "SCHEMA_DRIFT"
                expl = (
                    "Row 6: Intra-Source Contract-Diff Schema Drift (Alg A.9a) "
                    "without raw baseline data."
                )
            elif "INCREMENTAL_ONBOARDING" in actions_set:
                primary = "MULTI_SOURCE_EXPANSION"
                expl = (
                    "Row 6 (Incremental): Multi-Source Expansion (Alg A.7) "
                    "preserving deployed vault entities."
                )
            elif n_docs > 0:
                primary = "HYBRID_PER_SOURCE"
                expl = (
                    "Row 7: Hybrid Per-Source Plan combining multi-connector onboarding, "
                    "drift triage, and docs."
                )
            else:
                primary = "VERIFIED_NO_OP"
                expl = "Existing contract verified with zero schema drift across inspected tables."

    return InferredExecutionPlanDTO(
        plan_id=plan_id,
        primary_action=primary,
        table_actions=table_actions,
        doc_actions=doc_actions,
        inferred_at_utc=now_utc,
        explanation=expl,
    )


def resolve_initial_blueprint_and_ontology(
    input_spec: Any,
    base_dir: Any = None,
    workspace_root: Any = None,
    cas_store: Any = None,
) -> tuple[VaultBlueprint | None, GraphAOntology, NoMistakeDataVault2ContractV5 | None, Any | None]:
    """Resolve initial blueprint, ontology, and contract inputs for pipeline execution."""
    from opendv_agent_harness.pipeline.config import _load_yaml_or_json
    from opendv_core.models.graph_a import GraphAOntology

    bp = getattr(input_spec, "blueprint", None)
    if isinstance(bp, str):
        bp = VaultBlueprint.model_validate(yaml.safe_load(bp))
    elif isinstance(bp, dict):
        bp = VaultBlueprint.model_validate(bp)
    elif not bp:
        bp_content = getattr(input_spec, "blueprint_content", None)
        bp_path = getattr(input_spec, "blueprint_path", None)
        if bp_content:
            raw_bp = yaml.safe_load(bp_content) if isinstance(bp_content, str) else bp_content
            if isinstance(raw_bp, dict):
                bp = VaultBlueprint.model_validate(raw_bp)
        elif bp_path:
            raw_bp = _load_yaml_or_json(bp_path)
            bp = VaultBlueprint.model_validate(raw_bp)

    if bp is not None:
        return bp, bp.ontology, bp.contract, None

    loaded_graph_a = None
    ont = getattr(input_spec, "ontology", None)
    if not ont and (
        getattr(input_spec, "ontology_path", None)
        or getattr(input_spec, "ontology_content", None)
        or getattr(input_spec, "graph_a_ontology_path", None)
        or getattr(input_spec, "graph_a_source", None)
    ):
        g_inp = (
            input_spec.graph_a_source
            if getattr(input_spec, "graph_a_source", None) is not None
            else (
                input_spec.graph_a_ontology_path
                or input_spec.ontology_path
                or input_spec.ontology_content
            )
        )
        from opendv_sources import load_graph_a_source

        loaded = load_graph_a_source(g_inp, base_dir=base_dir, workspace_root=workspace_root)
        loaded_graph_a = loaded
        if cas_store is not None and getattr(loaded, "raw_bytes", None):
            cas_store.put(loaded.raw_bytes)
        ont = loaded.ontology

    if ont is None:
        ont = GraphAOntology(ontology_version="0.1.0")

    cnt = getattr(input_spec, "initial_contract", None)
    if isinstance(cnt, str):
        cnt = NoMistakeDataVault2ContractV5.model_validate(yaml.safe_load(cnt))
    elif isinstance(cnt, dict):
        cnt = NoMistakeDataVault2ContractV5.model_validate(cnt)
    elif not cnt:
        cnt_content = getattr(input_spec, "initial_contract_content", None)
        cnt_path = getattr(input_spec, "initial_contract_path", None)
        if cnt_content:
            raw_cnt = yaml.safe_load(cnt_content) if isinstance(cnt_content, str) else cnt_content
            if isinstance(raw_cnt, dict):
                cnt = NoMistakeDataVault2ContractV5.model_validate(raw_cnt)
        elif cnt_path:
            raw_cnt = _load_yaml_or_json(cnt_path)
            cnt = NoMistakeDataVault2ContractV5.model_validate(raw_cnt)

    return None, ont, cnt, loaded_graph_a


async def execute_stage_00_orchestration(
    runner: Any,
    w_idx: int,
    in_bp: Any,
    curr_contract: dict[str, Any] | None,
    wave_sources: list[Any],
    wave_docs: list[Any],
    wave_steps: list[Any],
    step_reports: list[Any],
) -> InferredExecutionPlanDTO:
    """Execute STAGE_00 mode detection and inferred execution plan generation."""
    from opendv_agent_harness.pipeline.spec import PipelineStepReport
    from opendv_agent_harness.pipeline.stages_agentic import (
        _step_tier,
        execute_stage_task,
        resolve_stage_model,
    )

    s00_id = "STAGE_00_ORCHESTRATION"
    s00_persona = "vault-first-mate"
    s00_model = resolve_stage_model(s00_id, s00_persona, runner.harness_setup, runner.harness)
    runner._notify_stage_start(w_idx, s00_id, s00_persona, s00_model)
    runner._emit_stream(f"Starting {s00_id}...")

    inference_plan = infer_execution_plan(
        blueprint=in_bp,
        contract=curr_contract,
        sources=wave_sources,
        documents=wave_docs,
    )
    s00_ctx = {
        "blueprint": in_bp.model_dump(mode="json") if hasattr(in_bp, "model_dump") else in_bp,
        "contract": curr_contract,
        "sources": [
            s.model_dump(mode="json") if hasattr(s, "model_dump") else s
            for s in wave_sources
        ],
        "documents": [
            d.model_dump(mode="json") if hasattr(d, "model_dump") else d
            for d in wave_docs
        ],
        "inference_plan": (
            inference_plan.model_dump(mode="json")
            if hasattr(inference_plan, "model_dump")
            else inference_plan
        ),
    }
    s00_metrics, _ = await execute_stage_task(
        w_idx,
        s00_id,
        s00_persona,
        runner.harness,
        runner.on_stream_event,
        runner.run_id,
        context=s00_ctx,
        stage_output_policy=runner.harness_setup.stage_output_policy,
    )
    step_00 = PipelineStepReport(
        stage_index=0,
        stage_id=s00_id,
        stage_name=s00_id,
        persona=s00_persona,
        model_tier=_step_tier(s00_persona),
        model_used=s00_model,
        status="PASS",
        verdict="PASS",
        duration_ms=10.0,
        summary=f"Inferred execution plan: {inference_plan.primary_action}.",
        metrics=s00_metrics,
        wave_index=w_idx,
    )
    wave_steps.append(step_00)
    step_reports.append(step_00)
    runner._notify_stage_complete(w_idx, step_00)
    return inference_plan


__all__ = [
    "DocExecutionActionDTO",
    "InferredExecutionPlanDTO",
    "TableExecutionActionDTO",
    "execute_stage_00_orchestration",
    "infer_execution_plan",
    "infer_table_action",
    "resolve_initial_blueprint_and_ontology",
]
