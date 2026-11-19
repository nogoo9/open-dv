"""Pipeline execution use case: Pre-flight validation, async execution, sync execution (WS4.1).

Refactored to support single-run PipelineInputDTO (ADR-019),
DualBlueprintIngressNormalizer (ADR-017),
and data-driven InferredExecutionPlanDTO derivation via mode_detector (ADR-018).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ConfigDict, Field, ValidationError

from opendv_agent_harness.pipeline.mode_detector import infer_execution_plan
from opendv_agent_harness.pipeline.runner import run_agentic_vault_pipeline
from opendv_agent_harness.pipeline.spec import PipelineInputSpec
from opendv_contracts import (
    EventType,
    PipelineEvent,
    PipelineRunHandle,
    RbacRole,
    RunStatus,
)
from opendv_contracts.dtos.blueprint import VaultBlueprintDTO
from opendv_contracts.dtos.common import BaseContractModel
from opendv_contracts.dtos.pipeline import (
    InferredExecutionPlanDTO,
    PipelineInputDTO,
    PipelineRunRequest,
    PipelineValidateRequest,
)
from opendv_service.errors import NotFoundError
from opendv_service.rbac import normalize_rbac_role, redact_event_for_role
from opendv_service.runs.options import resolve_effective_provider
from opendv_service.seams import PipelineRunner, get_default_pipeline_runner
from opendv_service.state import StateStore
from opendv_service.use_cases.pipeline_normalizer import (
    DualBlueprintIngressNormalizer,
    normalize_blueprint_ingress,
)


class SingleRunExecutionPlanDTO(InferredExecutionPlanDTO):
    """Data-driven execution plan DTO with backward-compatible dict access."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    passed: bool = Field(default=True)
    stage: str = Field(default="INPUT_VALIDATION")
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        return self.model_dump()[item]

    def __contains__(self, item: str) -> bool:
        return hasattr(self, item) or item in self.model_dump()

    def get(self, item: str, default: Any = None) -> Any:
        if hasattr(self, item):
            val = getattr(self, item)
            return default if val is None else val
        return self.model_dump().get(item, default)


def validate_pipeline(
    input_data_or_state: Any,
    request_or_state: Any = None,
    *,
    state: StateStore | None = None,
    base_dir: Path | None = None,
    is_server: bool = False,
) -> SingleRunExecutionPlanDTO:
    """Validate single-run inputs and derive InferredExecutionPlanDTO without invoking agents."""
    actual_state: StateStore | None
    actual_input: Any

    if isinstance(input_data_or_state, StateStore):
        actual_state = input_data_or_state
        actual_input = request_or_state
    elif isinstance(request_or_state, StateStore):
        actual_state = request_or_state
        actual_input = input_data_or_state
    else:
        actual_state = state
        actual_input = input_data_or_state

    if actual_input is None:
        raise ValueError("Pipeline inputs must be provided to validate_pipeline.")

    # Pre-flight deterministic validation
    val_rep: dict[str, Any] | None = None
    if isinstance(actual_input, PipelineValidateRequest):
        eval_state = actual_state or StateStore()
        val_rep = eval_state.validate_agentic_pipeline(actual_input)
        if isinstance(val_rep, dict) and not val_rep.get("passed", True):
            # Still infer plan structure but record failure
            pass
    else:
        # PipelineInputDTO or dict input
        eval_state = actual_state or StateStore()
        req_dict = (
            actual_input.model_dump(exclude_unset=True)
            if hasattr(actual_input, "model_dump")
            else (dict(actual_input) if isinstance(actual_input, dict) else {})
        )
        harness_prov = getattr(actual_input, "harness_provider", None) or req_dict.get(
            "harness_provider"
        )
        prov_override = (
            getattr(actual_input, "provider_override", None)
            or req_dict.get("provider_override")
            or harness_prov
        )
        bp_val = req_dict.get("blueprint")
        has_ont = bool(
            req_dict.get("graph_a_source")
            or req_dict.get("ontology_source")
            or bp_val
            or req_dict.get("blueprint_path")
            or req_dict.get("blueprint_content")
            or req_dict.get("ontology")
            or req_dict.get("ontology_path")
            or req_dict.get("ontology_content")
        )
        if has_ont:
            with contextlib.suppress(Exception):
                val_rep = eval_state.validate_agentic_pipeline(
                    PipelineValidateRequest(
                        input_spec=req_dict,
                        provider_override=prov_override,
                        unstructured_only=req_dict.get("unstructured_only", False),
                    )
                )

    # Normalize blueprint ingress
    eff_is_server = (
        is_server
        or (actual_state is not None and getattr(actual_state, "is_server", False))
        or (
            actual_state is not None
            and getattr(getattr(actual_state, "settings", None), "is_server", False)
        )
    )
    bp, contract, sources, documents = normalize_blueprint_ingress(
        actual_input, base_dir=base_dir, is_server=eff_is_server
    )

    # Infer execution plan via mode_detector (ADR-018 / 7-row matrix)
    try:
        inferred = infer_execution_plan(
            blueprint=bp,
            contract=contract,
            sources=sources,
            documents=documents,
        )
    except Exception:  # noqa: BLE001 - Fallback to metadata-only sources if URI resolution fails
        clean_sources: list[dict[str, Any]] = []
        for s in sources:
            s_dict = (
                s
                if isinstance(s, dict)
                else (s.model_dump() if hasattr(s, "model_dump") else {})
            )
            clean_s = {
                k: v for k, v in s_dict.items() if k not in ("uri", "data_path", "spec")
            }
            clean_s.setdefault("source_system", s_dict.get("source_system") or "source")
            clean_s.setdefault(
                "table_name", s_dict.get("table_name") or s_dict.get("feed_id") or "table"
            )
            clean_sources.append(clean_s)
        inferred = infer_execution_plan(
            blueprint=bp,
            contract=contract,
            sources=clean_sources,
            documents=documents,
        )

    passed_flag = True
    diagnostics: list[dict[str, Any]] = []
    if val_rep is not None:
        passed_flag = bool(val_rep.get("passed", True))
        diagnostics = list(val_rep.get("diagnostics") or [])
    elif not bp and not contract and not sources and not documents:
        passed_flag = False
        diagnostics = [
            {
                "code": "EMPTY_PIPELINE_INPUT",
                "severity": "ERROR",
                "message": (
                    "Pipeline input must provide at least one source, document, or blueprint."
                ),
            }
        ]

    plan_dto = SingleRunExecutionPlanDTO(
        plan_id=inferred.plan_id,
        primary_action=inferred.primary_action,
        table_actions=inferred.table_actions,
        doc_actions=inferred.doc_actions,
        inferred_at_utc=inferred.inferred_at_utc,
        explanation=inferred.explanation,
        passed=passed_flag,
        stage="INPUT_VALIDATION",
        diagnostics=diagnostics,
    )

    # For legacy PipelineValidateRequest callers (such as Platform API /pipelines/validate),
    # return dict response to satisfy response_model=dict[str, Any]
    if isinstance(actual_input, PipelineValidateRequest):
        plan_dict = plan_dto.model_dump(mode="json")
        plan_dict["passed"] = passed_flag
        plan_dict["stage"] = "INPUT_VALIDATION"
        plan_dict["diagnostics"] = diagnostics
        if val_rep:
            for k, v in val_rep.items():
                if k not in plan_dict:
                    plan_dict[k] = v
        return plan_dict  # type: ignore[return-value]

    return plan_dto


class SingleRunExecutionResultDTO(BaseContractModel):
    """Result of a single-run pipeline execution."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    run_id: str = Field(..., description="Unique run identifier")
    status: str = Field(..., description="Run status: COMPLETED, PAUSED_HOLD, FAILED")
    overall_passed: bool = Field(default=False)
    blueprint: VaultBlueprintDTO | dict[str, Any] | None = Field(default=None)
    evolution_diff: dict[str, Any] = Field(default_factory=dict)
    inference_plan: InferredExecutionPlanDTO | dict[str, Any] | None = Field(default=None)
    four_zone_sql: dict[str, Any] = Field(default_factory=dict)
    report: dict[str, Any] = Field(default_factory=dict)
    observability_events: list[dict[str, Any]] = Field(default_factory=list)

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        if item in self.report:
            return self.report[item]
        raise KeyError(item)

    def __contains__(self, item: str) -> bool:
        return (
            hasattr(self, item)
            or item in self.report
            or (self.model_extra and item in self.model_extra)
        )

    def get(self, item: str, default: Any = None) -> Any:
        if hasattr(self, item):
            val = getattr(self, item)
            return default if val is None else val
        return self.report.get(item, default)


def _to_vault_blueprint_dto(raw_bp: Any) -> VaultBlueprintDTO | None:
    if raw_bp is None or isinstance(raw_bp, VaultBlueprintDTO):
        return raw_bp
    if hasattr(raw_bp, "model_dump"):
        d = raw_bp.model_dump(mode="json")
    elif isinstance(raw_bp, dict):
        d = dict(raw_bp)
    else:
        return None
    clean_d = {k: v for k, v in d.items() if k != "blueprint_version"}
    clean_d["blueprint_version"] = "1.0.0"
    clean_d.pop("schema_version", None)
    try:
        return VaultBlueprintDTO.model_validate(clean_d)
    except (ValueError, TypeError, KeyError):
        return None


def _resolve_cas_sources_list(
    sources_list: Sequence[Any] | None,
    cas_store: Any | None,
) -> list[dict[str, Any]]:
    """Resolve cas:// URIs in source feed definitions to staged filesystem or sqlite paths."""
    if not sources_list:
        return []
    if cas_store is None or not hasattr(cas_store, "base_dir"):
        return [
            dict(s)
            if isinstance(s, dict)
            else (s.model_dump(mode="json") if hasattr(s, "model_dump") else dict(s))
            for s in sources_list
        ]
    cas_base = Path(cas_store.base_dir).resolve()
    resolved: list[dict[str, Any]] = []
    for s in sources_list:
        s_dict = (
            dict(s)
            if isinstance(s, dict)
            else (s.model_dump(mode="json") if hasattr(s, "model_dump") else dict(s))
        )
        s_uri = str(s_dict.get("uri") or "").strip()
        if s_uri.startswith("cas://"):
            raw_target = s_uri[len("cas://") :].split("?")[0].split("#")[0].strip()
            c_hash = raw_target.split("/")[0].strip().lower()
            cands = [cas_base / c_hash.upper(), cas_base / c_hash.lower()]
            cand_p = next((c for c in cands if c.is_file()), None)
            if cand_p is not None:
                is_sqlite = False
                with contextlib.suppress(OSError), open(cand_p, "rb") as bf:
                    if bf.read(16).startswith(b"SQLite format 3\x00"):
                        is_sqlite = True
                s_sys = s_dict.get("source_system") or "cas_store"
                s_opts = dict(s_dict.get("options") or {})
                s_opts["source_system"] = s_sys
                s_dict["options"] = s_opts
                s_dict["source_system"] = s_sys
                if is_sqlite:
                    s_dict["uri"] = f"sqlite:///{cand_p.as_posix()}"
                    s_dict["adapter"] = "sqlite"
                else:
                    s_dict["uri"] = cand_p.as_posix()
                    s_dict.setdefault("adapter", "file")
        resolved.append(s_dict)
    return resolved


def execute_pipeline_run_sync(
    state_or_input: Any,
    request_or_state: Any = None,
    *,
    role: RbacRole = RbacRole.INTERNAL_MODELER,
    pipeline_runner: PipelineRunner | None = None,
    on_stage_start: Callable[[int, str, str, str], None] | None = None,
    on_stage_complete: Callable[[int, Any], None] | None = None,
    on_stream_event: Callable[[Any], None] | None = None,
) -> SingleRunExecutionResultDTO:
    """Execute an end-to-end agentic pipeline run synchronously in-process."""
    state: StateStore | None
    req: Any

    if isinstance(state_or_input, StateStore):
        state = state_or_input
        req = request_or_state
    elif isinstance(request_or_state, StateStore):
        state = request_or_state
        req = state_or_input
    else:
        state = None
        req = state_or_input

    runner = pipeline_runner or get_default_pipeline_runner()

    if state is not None:
        if isinstance(req, PipelineInputDTO):
            bp_val = (
                req.blueprint.model_dump(mode="json")
                if hasattr(req.blueprint, "model_dump")
                else req.blueprint
            )
            spec_dump = req.model_dump(mode="json")
            if req.ontology_source and not spec_dump.get("graph_a_source"):
                spec_dump["graph_a_source"] = (
                    req.ontology_source.model_dump(mode="json")
                    if hasattr(req.ontology_source, "model_dump")
                    else req.ontology_source
                )
            resolved_srcs = _resolve_cas_sources_list(
                spec_dump.get("sources") or req.sources,
                getattr(state, "cas_store", None),
            )
            spec_dump["sources"] = resolved_srcs
            prov_override = resolve_effective_provider(req, state)
            run_req = PipelineRunRequest(
                input_spec=spec_dump,
                blueprint=bp_val,
                graph_a_source=spec_dump.get("graph_a_source"),
                sources=resolved_srcs,
                unstructured_documents=req.unstructured_documents,
                target_dialect=req.target_dialect,
                provider_override=prov_override,
            )
        elif isinstance(req, dict) and "blueprint" in req and "input_spec" not in req:
            resolved_srcs = _resolve_cas_sources_list(
                req.get("sources", []),
                getattr(state, "cas_store", None),
            )
            prov_override = resolve_effective_provider(req, state)
            run_req = PipelineRunRequest(
                input_spec=dict(req),
                blueprint=req.get("blueprint"),
                sources=resolved_srcs,
                unstructured_documents=req.get("unstructured_documents", []),
                target_dialect=req.get("target_dialect", "duckdb"),
                provider_override=prov_override,
            )
        elif isinstance(req, PipelineRunRequest):
            run_req = req
        else:
            run_req = PipelineRunRequest.model_validate(req)

        eff_role = normalize_rbac_role(role, default=RbacRole.INTERNAL_MODELER)
        rep_dict = state.execute_agentic_pipeline_run(
            run_req,
            role=eff_role,
            pipeline_runner=runner,
            on_stage_start=on_stage_start,
            on_stage_complete=on_stage_complete,
            on_stream_event=on_stream_event,
        )
        raw_rep = state.pipeline_reports.get(rep_dict.get("run_id", "")) or rep_dict
        raw_bp = raw_rep.get("blueprint")
        bp_dto = _to_vault_blueprint_dto(raw_bp)

        return SingleRunExecutionResultDTO(
            run_id=rep_dict.get("run_id", "run-sync"),
            status=rep_dict.get("status", "COMPLETED"),
            overall_passed=bool(rep_dict.get("overall_passed", True)),
            blueprint=bp_dto,
            evolution_diff=raw_rep.get("evolution_diff", {}),
            inference_plan=raw_rep.get("inference_plan", {}),
            four_zone_sql=raw_rep.get("four_zone_sql", {}),
            report=rep_dict,
            observability_events=rep_dict.get("observability_events", []),
        )

    # Standalone execution without StateStore
    spec_dict = req.model_dump(mode="json") if hasattr(req, "model_dump") else dict(req)
    spec_obj = PipelineInputSpec.model_validate(spec_dict)
    standalone_runner = pipeline_runner or run_agentic_vault_pipeline
    report = standalone_runner(
        spec_obj,
        on_stage_start=on_stage_start,
        on_stage_complete=on_stage_complete,
        on_stream_event=on_stream_event,
    )
    bp_dto = _to_vault_blueprint_dto(report.blueprint)

    return SingleRunExecutionResultDTO(
        run_id=report.run_id,
        status=report.status,
        overall_passed=report.overall_passed,
        blueprint=bp_dto,
        evolution_diff=report.evolution_diff,
        inference_plan=report.inference_plan,
        four_zone_sql=report.four_zone_sql,
        report=report.model_dump(mode="json"),
        observability_events=report.observability_events,
    )


async def execute_run_async(
    state_or_input: Any,
    request_or_state: Any = None,
    *,
    role: RbacRole = RbacRole.PUBLIC,
    pipeline_runner: PipelineRunner | None = None,
) -> PipelineRunHandle:
    """Initiate an asynchronous pipeline run returning immediately with HTTP 202 handle."""
    from opendv_service.runs import RunExecutionEngine

    state: StateStore
    req: Any
    if isinstance(state_or_input, StateStore):
        state = state_or_input
        req = request_or_state
    elif isinstance(request_or_state, StateStore):
        state = request_or_state
        req = state_or_input
    else:
        state = StateStore()
        req = state_or_input

    if isinstance(req, PipelineInputDTO):
        bp_val = (
            req.blueprint.model_dump(mode="json")
            if hasattr(req.blueprint, "model_dump")
            else req.blueprint
        )
        gov_pol = getattr(req, "governance_policy", None)
        hold_pol = getattr(req, "steward_hold_policy", None)
        if not hold_pol and gov_pol:
            hold_pol = (
                gov_pol.get("steward_hold_policy")
                if isinstance(gov_pol, dict)
                else getattr(gov_pol, "steward_hold_policy", None)
            )
        resolved_srcs = _resolve_cas_sources_list(
            req.sources,
            getattr(state, "cas_store", None),
        )
        spec_dict = req.model_dump(mode="json")
        spec_dict["sources"] = resolved_srcs
        run_req = PipelineRunRequest(
            input_spec=spec_dict,
            blueprint=bp_val,
            sources=resolved_srcs,
            unstructured_documents=req.unstructured_documents,
            target_dialect=req.target_dialect,
            provider_override=resolve_effective_provider(req, state),
            steward_hold_policy=str(hold_pol) if hold_pol else None,
        )
    elif isinstance(req, PipelineRunRequest):
        run_req = req
        if run_req.input_spec is None and (run_req.blueprint or run_req.sources):
            run_req.input_spec = run_req.model_dump(mode="json", exclude_none=True)
    else:
        try:
            input_dto = PipelineInputDTO.model_validate(req)
            return await execute_run_async(
                state, input_dto, role=role, pipeline_runner=pipeline_runner
            )
        except (ValidationError, ValueError, TypeError):
            run_req = PipelineRunRequest.model_validate(req)
            if run_req.input_spec is None and (run_req.blueprint or run_req.sources):
                run_req.input_spec = run_req.model_dump(mode="json", exclude_none=True)

    engine = RunExecutionEngine()
    return engine.submit_pipeline_run(state, run_req, role=role, pipeline_runner=pipeline_runner)


async def stream_events(
    state: StateStore,
    run_id: str,
    *,
    last_event_id: str | None = None,
    role: RbacRole = RbacRole.PUBLIC,
) -> AsyncIterator[PipelineEvent]:
    """Stream normalized PipelineEvent instances for a run over time.

    Enforces role-based event redaction.
    """
    if run_id not in state.runs and run_id not in state.run_events:
        raise NotFoundError(f"Run '{run_id}' not found.")

    eff_role = normalize_rbac_role(role, default=RbacRole.PUBLIC)
    sent_indices = 0
    seen_ids: set[str] = set()

    # If last_event_id is specified, skip events up to it
    events = state.run_events.get(run_id, [])
    if last_event_id:
        for idx, ev in enumerate(events):
            if ev.get("event_id") == last_event_id:
                sent_indices = idx + 1
                break

    while True:
        current_events = state.run_events.get(run_id, [])
        while sent_indices < len(current_events):
            raw_ev = current_events[sent_indices]
            sent_indices += 1
            ev_id = raw_ev.get("event_id", f"evt-{sent_indices}")
            if ev_id in seen_ids:
                continue
            seen_ids.add(ev_id)

            redacted = redact_event_for_role(raw_ev, eff_role)
            event_type_str = redacted.get("event_type", "LOG_MESSAGE")
            try:
                event_type = EventType(event_type_str)
            except ValueError:
                event_type = EventType.LOG_MESSAGE

            yield PipelineEvent(
                event_id=ev_id,
                run_id=run_id,
                timestamp_utc=redacted.get(
                    "timestamp_utc", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                ),
                stage_id=redacted.get("stage_id", "STAGE_00_ORCHESTRATION"),
                event_type=event_type,
                payload=redacted,
            )

        run_dto = state.runs.get(run_id)
        if run_dto is not None and run_dto.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.ABORTED,
        }:
            break

        await asyncio.sleep(0.05)


__all__ = [
    "DualBlueprintIngressNormalizer",
    "SingleRunExecutionPlanDTO",
    "SingleRunExecutionResultDTO",
    "execute_pipeline_run_sync",
    "execute_run_async",
    "normalize_blueprint_ingress",
    "stream_events",
    "validate_pipeline",
]
