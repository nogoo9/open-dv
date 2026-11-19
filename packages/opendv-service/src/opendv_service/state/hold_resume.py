"""Background Stages 03-07 pipeline resumption after Captain-Hold approval (`Audit #7`)."""

from __future__ import annotations

import contextlib
import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import yaml

from opendv_agent_harness import (
    HarnessSetupConfig,
    PipelineInputSpec,
    PipelineStepReport,
    build_observability_event_dict,
)
from opendv_agent_harness.pipeline.spec import AgenticPipelineReport
from opendv_contracts.errors import OpenDVError
from opendv_service.models import (
    ArtifactMetadataResponse,
    GateScorecardSummary,
    GateVerdictStatus,
    RunStatus,
)
from opendv_service.seams import get_default_pipeline_runner
from opendv_service.state.persistence import save_platform_state

if TYPE_CHECKING:
    from opendv_core.models.graph_a import GraphAOntology
    from opendv_service.state.base import BaseStateContainer


def _resume_context_path(state_dir: Path, run_id: str) -> Path:
    return state_dir / "resumable_runs" / f"{run_id}.json"


def has_resumable_run_context(state_dir: Path, run_id: str) -> bool:
    """Return True if paused run context exists on disk for ``run_id``."""
    return _resume_context_path(state_dir, run_id).is_file()


def _compute_scorecard_from_report(report: AgenticPipelineReport) -> GateScorecardSummary:
    if report.wave_reports:
        last_w = report.wave_reports[-1]
        gs = last_w.gate_scorecard
        codemods = set(last_w.applied_codemods or ())

        def _norm(raw: Any, fallback: GateVerdictStatus = "PASS") -> GateVerdictStatus:
            val = str(raw or fallback)
            if val in {"PASS", "AUTO-FIX", "AUTO_REPAIRED", "ASK-USER", "REJECT-WITH-TRACE"}:
                return val  # type: ignore[return-value]
            return fallback

        g0 = _norm(gs.get("G0_JSON_SCHEMA") or gs.get("G0"))
        g1 = _norm(gs.get("G1_ONTOLOGY_AND_GRAIN") or gs.get("G1"), "AUTO_REPAIRED")
        g2 = _norm(gs.get("G2_KEY_AND_FK_DISCIPLINE") or gs.get("G2"))
        g3 = _norm(gs.get("G3_NETWORKX_BIPARTITE_DAG") or gs.get("G3"))
        g4_default: GateVerdictStatus = (
            "AUTO-FIX" if "ALG_A1_3AXIS_SATELLITE_SPLIT" in codemods else "PASS"
        )
        g4 = _norm(gs.get("G4_YAGNI_AND_COLUMN_LEDGER") or gs.get("G4"), g4_default)
        g5: GateVerdictStatus = (
            "PASS" if (last_w.sandbox_summary or {}).get("passed", True) else "REJECT-WITH-TRACE"
        )
        return GateScorecardSummary(
            gate_0_profile_drift=g0,
            gate_1_contract_grain=g1,
            gate_2_sqlglot_ast=g2,
            gate_3_networkx_dag=g3,
            gate_4_yagni_cost_guard=g4,
            gate_5_duckdb_sandbox=g5,
        )
    return GateScorecardSummary(
        gate_0_profile_drift="PASS",
        gate_1_contract_grain="AUTO_REPAIRED",
        gate_2_sqlglot_ast="PASS",
        gate_3_networkx_dag="PASS",
        gate_4_yagni_cost_guard="PASS",
        gate_5_duckdb_sandbox="PASS",
    )


def _register_terminal_artifacts(
    state: BaseStateContainer,
    output_dir: Path,
    report: AgenticPipelineReport,
    report_dict: dict[str, Any],
) -> None:
    fc_path = output_dir / "final_contract.yaml"
    if fc_path.is_file():
        c_txt = fc_path.read_text(encoding="utf-8")
        c_dict = yaml.safe_load(c_txt)
        report_dict["contract"] = c_dict or c_txt
        report_dict["contract_content"] = c_txt
    bp_path = output_dir / "vault_blueprint.yaml"
    if bp_path.is_file():
        bp_txt = bp_path.read_text(encoding="utf-8")
        report_dict["blueprint"] = yaml.safe_load(bp_txt)
        report_dict["blueprint_content"] = bp_txt
    for ref in [*(report.artifacts or []), *(report.final_artifacts or {}).values()]:
        if ref.sha256:
            sha_u = ref.sha256.upper()
            with state._state_lock:
                already_present = sha_u in state.artifacts
            if not already_present:
                p = Path(ref.file_path or ref.path)
                content: Any = None
                if p.is_file():
                    raw_txt = p.read_text(encoding="utf-8", errors="ignore")
                    with contextlib.suppress(yaml.YAMLError, ValueError):
                        content = yaml.safe_load(raw_txt)
                meta = ArtifactMetadataResponse(
                    sha256=sha_u,
                    byte_length=int(ref.byte_length or 0),
                    media_type=ref.media_type or "application/x-yaml",
                    artifact_name=ref.name,
                    content=content,
                )
                with state._state_lock:
                    state.artifacts[sha_u] = meta
                    state.artifacts[sha_u.lower()] = meta


def _emit_stage_event(
    state: BaseStateContainer,
    run_id: str,
    *,
    stage_id: str,
    persona: str,
    status: str,
    headline: str,
) -> None:
    with state._state_lock:
        events = state.run_events.setdefault(run_id, [])
        evt_id = f"evt-resume-{len(events) + 1:03d}"
        evt = build_observability_event_dict(
            event_id=evt_id,
            run_id=run_id,
            trace_id=uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=uuid.uuid4().hex[:16],
            timestamp_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            stage_id=stage_id,
            agent_persona=persona,
            status=status,
            headline=headline,
            business_impact=headline,
            data_classification="INTERNAL",
            rule_ids_evaluated=["HOLD_RESUME"],
            summary_metrics={"resumed": True},
        )
        events.append(evt)
    ledger = state.run_ledgers.get(run_id)
    if ledger is not None:
        with contextlib.suppress(ValueError, TypeError, OSError):
            ledger.emit(evt)


def _execute_resume_worker(
    state: BaseStateContainer,
    *,
    run_id: str,
    updated_ontology: GraphAOntology | None,
) -> None:
    ctx_path = _resume_context_path(state.state_dir, run_id)
    try:
        ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
        spec_dict = dict(ctx.get("input_spec") or {})
        spec_dict["run_id"] = run_id
        if updated_ontology is not None:
            spec_dict["ontology"] = updated_ontology.model_dump(mode="json", exclude_none=True)
            spec_dict.pop("graph_a_ontology_path", None)
            spec_dict.pop("ontology_path", None)
        setup_dict = dict(ctx.get("harness_setup") or {})
        setup_dict["steward_hold_policy"] = "auto_approve_top_candidate"
        extra_opts = dict(setup_dict.get("extra_provider_options") or {})
        extra_opts["state_dir"] = str(state.state_dir)
        setup_dict["extra_provider_options"] = extra_opts

        spec_obj = PipelineInputSpec.model_validate(spec_dict)
        setup_obj = HarnessSetupConfig.model_validate(setup_dict)
        output_dir = Path(str(ctx["output_dir"])).resolve()
        base_dir = Path(str(ctx["base_dir"])).resolve()
        pipeline_runner = get_default_pipeline_runner()

        def _on_stage_complete(_wave_idx: int, step_rep: PipelineStepReport) -> None:
            if step_rep.stage_index >= 3:
                _emit_stage_event(
                    state,
                    run_id,
                    stage_id=step_rep.stage_id,
                    persona=step_rep.persona,
                    status=step_rep.status,
                    headline=step_rep.summary or f"Completed {step_rep.stage_id}.",
                )

        report = pipeline_runner(
            spec_obj,
            setup_obj,
            output_dir=output_dir,
            base_dir=base_dir,
            cas_store=state.cas_store,
            on_stage_complete=_on_stage_complete,
            is_server=bool(ctx.get("is_server", False)),
            allowed_hosts=ctx.get("allowed_hosts"),
        )

        with state._state_lock:
            has_compiler_evt = any(
                ev.get("stage_id") == "STAGE_07_DETERMINISTIC_COMPILER"
                for ev in state.run_events.get(run_id, [])
            )
        if not has_compiler_evt:
            _emit_stage_event(
                state,
                run_id,
                stage_id="STAGE_07_DETERMINISTIC_COMPILER",
                persona="deterministic-compiler",
                status="PASS" if report.status == "COMPLETED" else "REJECT-WITH-TRACE",
                headline="Deterministic compilation and contract emission complete.",
            )

        scorecard = _compute_scorecard_from_report(report)
        report_dict = report.model_dump(mode="json")
        report_dict["gate_scorecard"] = scorecard.model_dump(mode="json")
        _register_terminal_artifacts(state, output_dir, report, report_dict)

        final_contract_ref = (report.final_artifacts or {}).get("final_contract")
        final_ir_ref = (report.final_artifacts or {}).get("final_canonical_ir")
        with state._state_lock:
            run_dto = state.runs[run_id]
            proj = state.projects.get(run_dto.project_id)
            if proj is not None:
                state.projects[run_dto.project_id] = proj.model_copy(
                    update={
                        "latest_run_id": run_id,
                        "contract_path": (
                            final_contract_ref.file_path
                            if final_contract_ref and final_contract_ref.file_path
                            else proj.contract_path
                        ),
                        "canonical_ir_path": (
                            final_ir_ref.file_path
                            if final_ir_ref and final_ir_ref.file_path
                            else proj.canonical_ir_path
                        ),
                    }
                )
            last_stage = (
                report.step_reports[-1].stage_id
                if report.step_reports
                else "STAGE_07_DETERMINISTIC_COMPILER"
            )
            updated_run = run_dto.model_copy(
                update={
                    "status": RunStatus(report.status),
                    "completed_at_utc": report.completed_at
                    or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "current_stage_id": last_stage,
                    "active_hold_ids": [],
                    "event_count": len(state.run_events.get(run_id, [])),
                    "gate_scorecard": scorecard,
                }
            )
            state.runs[run_id] = updated_run
            state.pipeline_reports[run_id] = report_dict
            state.run_gate_reports[run_id] = state._build_default_gate_report(
                run_id, run_dto=updated_run
            )
            state._sync_pipeline_report(run_id)
            save_platform_state(state)
    except (
        OpenDVError,
        duckdb.Error,
        yaml.YAMLError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
    ) as exc:
        with state._state_lock:
            if run_id in state.runs:
                state.runs[run_id] = state.runs[run_id].model_copy(
                    update={
                        "status": RunStatus.FAILED,
                        "completed_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                )
            rep = state.pipeline_reports.get(run_id)
            if isinstance(rep, dict):
                rep["status"] = "FAILED"
                rep["error"] = str(exc)
        _emit_stage_event(
            state,
            run_id,
            stage_id="STAGE_03_SHIP_VAULT_PASS1",
            persona="ship-vault",
            status="REJECT-WITH-TRACE",
            headline=f"Background hold resumption failed: {exc}",
        )
        save_platform_state(state)


def maybe_spawn_hold_resume(
    state: BaseStateContainer,
    *,
    run_id: str,
    updated_ontology: GraphAOntology | None = None,
) -> bool:
    """Spawn background Stages 03-07 resumption thread when resumable context exists."""
    if not has_resumable_run_context(state.state_dir, run_id):
        return False
    worker = threading.Thread(
        target=_execute_resume_worker,
        kwargs={
            "state": state,
            "run_id": run_id,
            "updated_ontology": updated_ontology,
        },
        name=f"opendv-hold-resume-{run_id}",
        daemon=True,
    )
    worker.start()
    return True


__all__ = ["has_resumable_run_context", "maybe_spawn_hold_resume"]
