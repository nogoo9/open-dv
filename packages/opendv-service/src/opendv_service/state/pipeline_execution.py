"""Agentic 8-stage pipeline run execution, artifact registration, and state updates."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from opendv_agent_harness import (
    AgenticPipelineReport,
    HarnessStreamEvent,
    PipelineStepReport,
    StewardHoldManager,
    build_observability_event_dict,
)
from opendv_core import resolve_workspace_path
from opendv_service.legacy_demo import (
    PE_DEMO_CONTEXT_REFS,
    WORKBENCH_VIEW_TITLES,
)
from opendv_service.models import (
    ArtifactMetadataResponse,
    GateScorecardSummary,
    GateVerdictStatus,
    PipelineRunRequest,
    ProjectResponse,
    ProjectRunDTO,
    RunStatus,
)
from opendv_service.rbac import (
    RbacRole,
    normalize_rbac_role,
    redact_event_for_role,
)
from opendv_service.seams import PipelineRunner, get_default_pipeline_runner
from opendv_telemetry import VaultLedgerEmitter

if TYPE_CHECKING:
    from opendv_service.state.base import BaseStateContainer
    from opendv_service.state.pipeline import PipelineStateMixin
    from opendv_service.state.runs import RunStateMixin


class PipelineExecutionMixin:
    """End-to-end agentic pipeline run execution mixin."""

    def execute_agentic_pipeline_run(
        self: BaseStateContainer | PipelineStateMixin | RunStateMixin,
        req: PipelineRunRequest,
        role: RbacRole | str = RbacRole.PUBLIC,
        *,
        pipeline_runner: PipelineRunner | None = None,
        on_stage_start: Callable[[int, str, str, str], None] | None = None,
        on_stage_complete: Callable[[int, PipelineStepReport], None] | None = None,
        on_stream_event: Callable[[HarnessStreamEvent], None] | None = None,
    ) -> dict[str, Any]:
        """Execute an end-to-end 8-stage agentic pipeline run and register state in platform."""
        eff_role = normalize_rbac_role(role, default=RbacRole.PUBLIC)
        spec_obj, setup_obj, base_dir = self._resolve_pipeline_inputs(req)

        output_dir_str = (
            req.output_dir.strip()
            if req.output_dir is not None and req.output_dir.strip()
            else None
        )
        if output_dir_str is not None:
            out_dir: Path | None = self._resolve_repo_path(output_dir_str)
        elif spec_obj.output_dir is not None:
            out_dir = None
        else:
            self._pipeline_run_seq += 1
            out_dir = self.state_dir / "pipeline_runs" / f"run_{self._pipeline_run_seq:04d}"

        if out_dir is not None:
            shutil.rmtree(out_dir / "state" / "holds", ignore_errors=True)
        elif spec_obj.output_dir is not None:
            spec_out_cand = resolve_workspace_path(spec_obj.output_dir, workspace_root=base_dir)
            shutil.rmtree(spec_out_cand / "state" / "holds", ignore_errors=True)

        runner: PipelineRunner = pipeline_runner or get_default_pipeline_runner()
        report: AgenticPipelineReport = runner(
            spec_obj,
            setup_obj,
            output_dir=out_dir,
            base_dir=base_dir,
            cas_store=self.cas_store,
            on_stage_start=on_stage_start,
            on_stage_complete=on_stage_complete,
            on_stream_event=on_stream_event,
        )

        # Store all intermediate and final artifacts in self.cas_store and self.artifacts
        for ref in [*report.intermediate_artifacts, *report.final_artifacts.values()]:
            raw_bytes: bytes | None = None
            if ref.file_path and Path(ref.file_path).is_file():
                raw_bytes = Path(ref.file_path).read_bytes()
                self.cas_store.put(raw_bytes)
            elif self.cas_store.has(ref.sha256):
                raw_bytes = self.cas_store.get_bytes(ref.sha256)

            if raw_bytes is not None:
                parsed_content: Any
                try:
                    text_val = raw_bytes.decode("utf-8")
                    if ref.media_type == "application/json":
                        parsed_content = json.loads(text_val)
                    elif ref.media_type in {"application/yaml", "application/x-yaml"}:
                        parsed_content = yaml.safe_load(text_val)
                    else:
                        parsed_content = text_val
                except Exception:
                    parsed_content = raw_bytes.decode("utf-8", errors="replace")

                meta = ArtifactMetadataResponse(
                    sha256=ref.sha256.upper(),
                    byte_length=ref.byte_length,
                    media_type=ref.media_type,
                    artifact_name=ref.name,
                    content=parsed_content,
                )
                self.artifacts[ref.sha256.upper()] = meta
                self.artifacts[ref.sha256.lower()] = meta

        ont_art_ref = next(
            (
                a
                for a in report.intermediate_artifacts
                if a.artifact_id == "art-stage00-graph-a-ontology"
            ),
            None,
        )
        ont_sha = (
            ont_art_ref.sha256.upper()
            if ont_art_ref is not None
            else PE_DEMO_CONTEXT_REFS["graph_a_sha256"]
        )
        last_gb_ref = next(
            (
                a
                for a in reversed(report.intermediate_artifacts)
                if a.artifact_id.endswith("-stage01-graph-b-profile") and a.file_path
            ),
            None,
        )
        raw_ont_path = Path(spec_obj.graph_a_ontology_path)
        if raw_ont_path.is_absolute():
            cand_ont = raw_ont_path.resolve()
        else:
            cand_base_ont = (base_dir / raw_ont_path).resolve()
            cand_repo_ont = (self.repo_root / raw_ont_path).resolve()
            cand_ont = cand_base_ont if cand_base_ont.exists() else cand_repo_ont
        if cand_ont.exists() and cand_ont.is_relative_to(self.repo_root):
            resolved_graph_a_path = str(cand_ont.relative_to(self.repo_root))
        elif ont_art_ref is not None and ont_art_ref.file_path:
            resolved_graph_a_path = ont_art_ref.file_path
        elif cand_ont.is_relative_to(self.repo_root):
            resolved_graph_a_path = str(cand_ont.relative_to(self.repo_root))
        else:
            resolved_graph_a_path = str(cand_ont)

        final_contract_ref = report.final_artifacts.get("final_contract")
        final_ir_ref = report.final_artifacts.get("final_canonical_ir")

        if spec_obj.project_id not in self.projects:
            self.projects[spec_obj.project_id] = ProjectResponse(
                project_id=spec_obj.project_id,
                display_name=spec_obj.display_name,
                domain_namespace=spec_obj.domain_namespace,
                graph_a_sha256=ont_sha,
                latest_run_id=report.run_id,
                created_at_utc=report.started_at,
                contract_path=final_contract_ref.file_path if final_contract_ref else None,
                canonical_ir_path=final_ir_ref.file_path if final_ir_ref else None,
                graph_a_ontology_path=resolved_graph_a_path,
                graph_b_path=last_gb_ref.file_path if last_gb_ref else None,
            )
        else:
            existing_proj = self.projects[spec_obj.project_id]
            proj_updates: dict[str, Any] = {
                "latest_run_id": report.run_id,
                "graph_a_sha256": ont_sha,
                "graph_a_ontology_path": resolved_graph_a_path,
                "contract_path": (
                    final_contract_ref.file_path
                    if final_contract_ref and final_contract_ref.file_path
                    else existing_proj.contract_path
                ),
                "canonical_ir_path": (
                    final_ir_ref.file_path
                    if final_ir_ref and final_ir_ref.file_path
                    else existing_proj.canonical_ir_path
                ),
                "graph_b_path": (
                    last_gb_ref.file_path
                    if last_gb_ref and last_gb_ref.file_path
                    else existing_proj.graph_b_path
                ),
            }
            self.projects[spec_obj.project_id] = existing_proj.model_copy(update=proj_updates)

        events_list = list(report.observability_events)
        self.run_events[report.run_id] = events_list
        by_view: dict[str, list[dict[str, Any]]] = {f"WB-{i:02d}": [] for i in range(12)}
        for evt in events_list:
            vw = str(evt.get("workbench_view_id") or "").strip().upper()[:5]
            if vw in by_view:
                by_view[vw].append(evt)

        workbench_events_list = list(events_list)
        trace_id = (
            str(events_list[0].get("trace_id"))
            if events_list
            else "4bf92f3577b34da6a3ce929d0e0e4736"
        )
        for idx in range(12):
            vw_code = f"WB-{idx:02d}"
            if not by_view[vw_code]:
                synth_evt = build_observability_event_dict(
                    event_id=f"evt-{report.run_id}-{vw_code.lower()}-synth",
                    run_id=report.run_id,
                    trace_id=trace_id,
                    span_id=f"{idx + 100:016x}",
                    parent_span_id=None,
                    timestamp_utc=report.completed_at,
                    stage_id="STAGE_00_ORCHESTRATION",
                    workbench_view_id=vw_code,
                    viz_id="VIZ-PIPE-00",
                    agent_persona="vault-first-mate",
                    status="PASS" if report.overall_passed else "REJECT-WITH-TRACE",
                    headline=f"{WORKBENCH_VIEW_TITLES[vw_code]} projection ready.",
                    business_impact=(
                        f"Deterministic projection for {vw_code} in "
                        f"pipeline '{report.pipeline_id}'."
                    ),
                    data_classification="INTERNAL",
                    rule_ids_evaluated=["HR-01"],
                    summary_metrics={"wave_count": len(report.wave_reports)},
                )
                by_view[vw_code].append(synth_evt)
                workbench_events_list.append(synth_evt)

        self.run_events_by_view[report.run_id] = by_view
        self.run_workbench_events[report.run_id] = workbench_events_list

        ledger_file = self.ledger_dir / f"{report.run_id}.jsonl"
        ledger_file.unlink(missing_ok=True)
        ledger = VaultLedgerEmitter(
            ledger_path=ledger_file,
            cas_store=self.cas,
        )
        for evt in events_list:
            ledger.emit(evt)
        self.run_ledgers[report.run_id] = ledger

        shutil.rmtree(self.state_dir / report.run_id / "holds", ignore_errors=True)
        hold_mgr = StewardHoldManager(state_dir=self.state_dir / report.run_id)
        active_hold_ids: list[str] = []
        for h in report.active_holds:
            hid = str(h.get("hold_id") or h.get("decision_id") or f"hold-{report.run_id}")
            active_hold_ids.append(hid)
            hold_mgr.record_hold(
                run_id=report.run_id,
                task_id=str(h.get("task_id") or f"task-{report.run_id}"),
                session_id=str(h.get("session_id") or f"sess-{report.run_id}"),
                stage_id=str(h.get("stage_id") or "STAGE_02_SCOUT_UNSTRUCTURED"),
                workbench_view_id=str(h.get("workbench_view_id") or "WB-03"),
                viz_id=str(h.get("viz_id") or "VIZ-GATE-07"),
                persona=str(h.get("persona") or "vault-first-mate"),
                decision_id=str(h.get("decision_id") or hid),
                hold_id=hid,
                event_id=str(
                    h.get("event_id") or (events_list[-1]["event_id"] if events_list else "evt-0")
                ),
                headline=str(h.get("headline") or "Steward hold pending resolution."),
                escalation_reason=str(h.get("escalation_reason") or "TIER_U3_AMBIGUOUS_ENTITY"),
                rule_id=str(h.get("rule_id") or "ALG-A9B"),
                options=list(h.get("options") or []),
            )
        for h in report.resolved_holds:
            hid = str(h.get("hold_id") or h.get("decision_id") or f"hold-{report.run_id}")
            rec = hold_mgr.record_hold(
                run_id=report.run_id,
                task_id=str(h.get("task_id") or f"task-{report.run_id}"),
                session_id=str(h.get("session_id") or f"sess-{report.run_id}"),
                stage_id=str(h.get("stage_id") or "STAGE_02_SCOUT_UNSTRUCTURED"),
                workbench_view_id=str(h.get("workbench_view_id") or "WB-03"),
                viz_id=str(h.get("viz_id") or "VIZ-GATE-07"),
                persona=str(h.get("persona") or "vault-first-mate"),
                decision_id=str(h.get("decision_id") or hid),
                hold_id=hid,
                event_id=str(
                    h.get("event_id") or (events_list[-1]["event_id"] if events_list else "evt-0")
                ),
                headline=str(h.get("headline") or "Steward hold resolved."),
                escalation_reason=str(h.get("escalation_reason") or "TIER_U3_AMBIGUOUS_ENTITY"),
                rule_id=str(h.get("rule_id") or "ALG-A9B"),
                options=list(h.get("options") or []),
            )
            hold_mgr.resolve_hold(
                rec.decision_id,
                action="REJECT" if str(h.get("status")) == "REJECTED" else "APPROVE",
                selected_option_id=h.get("selected_option_id"),
                resolution_notes=h.get("resolution_notes"),
            )
        self.hold_managers[report.run_id] = hold_mgr
        self.run_decisions[report.run_id] = []
        self.run_sandbox_history[report.run_id] = []

        all_codemods = {c for w in report.wave_reports for c in w.applied_codemods}
        if report.wave_reports:
            last_w = report.wave_reports[-1]
            w_sc = last_w.gate_scorecard
            g0_stat: GateVerdictStatus = (
                "AUTO-FIX"
                if "ALG_A9A_INTRA_SOURCE_SCHEMA_DRIFT" in all_codemods
                else (
                    "REJECT-WITH-TRACE"
                    if w_sc.get("G0_JSON_SCHEMA") == "REJECT-WITH-TRACE"
                    else "PASS"
                )
            )
            g1_raw = w_sc.get("G1_ONTOLOGY_AND_GRAIN", "PASS")
            g1_stat: GateVerdictStatus = (
                "REJECT-WITH-TRACE"
                if g1_raw == "REJECT-WITH-TRACE"
                else (
                    "ASK-USER"
                    if g1_raw == "ASK-USER" or report.status == "PAUSED_HOLD"
                    else (
                        "AUTO_REPAIRED"
                        if any(w.self_healing_attempts > 1 for w in report.wave_reports)
                        else "PASS"
                    )
                )
            )
            g2_stat: GateVerdictStatus = (
                "REJECT-WITH-TRACE"
                if w_sc.get("G2_KEY_AND_FK_DISCIPLINE") == "REJECT-WITH-TRACE"
                else "PASS"
            )
            g3_stat: GateVerdictStatus = (
                "REJECT-WITH-TRACE"
                if w_sc.get("G3_NETWORKX_BIPARTITE_DAG") == "REJECT-WITH-TRACE"
                else "PASS"
            )
            g4_raw = w_sc.get("G4_YAGNI_AND_COLUMN_LEDGER", "PASS")
            g4_stat: GateVerdictStatus = (
                "REJECT-WITH-TRACE"
                if g4_raw == "REJECT-WITH-TRACE"
                else ("AUTO-FIX" if "ALG_A1_3AXIS_SATELLITE_SPLIT" in all_codemods else "PASS")
            )
            g5_stat: GateVerdictStatus = (
                "PASS" if last_w.sandbox_summary.get("passed", False) else "REJECT-WITH-TRACE"
            )
            scorecard = GateScorecardSummary(
                gate_0_profile_drift=g0_stat,
                gate_1_contract_grain=g1_stat,
                gate_2_sqlglot_ast=g2_stat,
                gate_3_networkx_dag=g3_stat,
                gate_4_yagni_cost_guard=g4_stat,
                gate_5_duckdb_sandbox=g5_stat,
            )
        else:
            scorecard = GateScorecardSummary(
                gate_0_profile_drift="REJECT-WITH-TRACE",
                gate_1_contract_grain="REJECT-WITH-TRACE",
                gate_2_sqlglot_ast="REJECT-WITH-TRACE",
                gate_3_networkx_dag="REJECT-WITH-TRACE",
                gate_4_yagni_cost_guard="REJECT-WITH-TRACE",
                gate_5_duckdb_sandbox="REJECT-WITH-TRACE",
            )

        run_status = RunStatus(report.status)
        last_step = report.step_reports[-1] if report.step_reports else None
        last_evt = events_list[-1] if events_list else None
        run_dto = ProjectRunDTO(
            run_id=report.run_id,
            project_id=spec_obj.project_id,
            harness_provider=setup_obj.provider,
            status=run_status,
            started_at_utc=report.started_at,
            completed_at_utc=(None if run_status == RunStatus.PAUSED_HOLD else report.completed_at),
            current_stage_id=(last_step.stage_id if last_step else "STAGE_00_ORCHESTRATION"),
            current_view_id=(
                str(last_evt.get("workbench_view_id", "WB-00")) if last_evt else "WB-00"
            ),
            event_count=len(events_list),
            gate_scorecard=scorecard,
            active_hold_ids=active_hold_ids,
            events_sse_url=f"/api/v1/runs/{report.run_id}/events",
            gates_url=f"/api/v1/runs/{report.run_id}/gates",
            sandbox_url=f"/api/v1/runs/{report.run_id}/sandbox",
            decisions_url=f"/api/v1/runs/{report.run_id}/decisions",
        )
        self.runs[report.run_id] = run_dto

        gate_rep = self._build_default_gate_report(report.run_id)
        if report.wave_reports:
            gate_rep["wave_gate_scorecard"] = dict(report.wave_reports[-1].gate_scorecard)
        self.run_gate_reports[report.run_id] = gate_rep

        report_dict = report.model_dump(mode="json")
        self.pipeline_reports[report.run_id] = report_dict
        synced = self._sync_pipeline_report(report.run_id)
        redacted_rep = dict(synced)
        redacted_rep["observability_events"] = [
            redact_event_for_role(ev, eff_role) for ev in synced.get("observability_events", [])
        ]
        return redacted_rep
