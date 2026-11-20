"""Agentic 8-stage pipeline run execution, artifact registration, and state updates."""

from __future__ import annotations

import contextlib
import os
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from opendv_agent_harness import (
    AgenticPipelineReport,
    HarnessStreamEvent,
    PipelineStepReport,
)
from opendv_core import resolve_workspace_path
from opendv_service.models import (
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
from opendv_service.state.persistence import (
    finalize_run_ledger,
    register_report_artifacts,
    save_platform_state,
    wrap_pipeline_callbacks_with_ledger_flush,
)

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
        target_run_id = getattr(req, "run_id", None) or getattr(spec_obj, "run_id", None)
        if target_run_id and not spec_obj.run_id:
            spec_obj = spec_obj.model_copy(update={"run_id": target_run_id})
        target_session_id = (
            getattr(req, "session_id", None)
            or getattr(spec_obj, "session_id", None)
            or (
                req.input_spec.get("session_id")
                if isinstance(getattr(req, "input_spec", None), dict)
                else None
            )
        )
        if target_session_id and not getattr(spec_obj, "session_id", None):
            spec_obj = spec_obj.model_copy(update={"session_id": target_session_id})
        if target_session_id and target_run_id and hasattr(self, "run_session_ids"):
            with self._state_lock:
                self.run_session_ids[target_run_id] = str(target_session_id)

        eff_is_server = bool(
            getattr(self, "is_server", False)
            or getattr(getattr(self, "settings", None), "is_server", False)
        )
        server_allowed_hosts = getattr(self, "allowed_hosts", None)
        if server_allowed_hosts is None and getattr(self, "settings", None) is not None:
            server_allowed_hosts = getattr(self.settings, "allowed_hosts", None)
        if server_allowed_hosts is None:
            env_hosts = os.environ.get("OPENDV_ALLOWED_HOSTS") or os.environ.get(
                "OPENDV_ALLOWED_DATABRICKS_HOSTS"
            )
            if env_hosts:
                server_allowed_hosts = [
                    h.strip().lower() for h in env_hosts.split(",") if h.strip()
                ]

        output_dir_str = (
            req.output_dir.strip()
            if req.output_dir is not None and req.output_dir.strip()
            else None
        )
        if eff_is_server:
            if spec_obj.output_dir is not None:
                spec_obj = spec_obj.model_copy(update={"output_dir": None})
            with self._state_lock:
                self._pipeline_run_seq += 1
                seq_num = self._pipeline_run_seq
            out_dir: Path | None = (
                self.state_dir / "pipeline_runs" / f"run_{seq_num:04d}"
            )
        elif output_dir_str is not None:
            out_dir = self._resolve_repo_path(output_dir_str)
        elif spec_obj.output_dir is not None:
            out_dir = None
        else:
            with self._state_lock:
                self._pipeline_run_seq += 1
                seq_num = self._pipeline_run_seq
            out_dir = self.state_dir / "pipeline_runs" / f"run_{seq_num:04d}"

        if out_dir is not None:
            shutil.rmtree(out_dir / "state" / "holds", ignore_errors=True)
        elif not eff_is_server and spec_obj.output_dir is not None:
            spec_out_cand = resolve_workspace_path(spec_obj.output_dir, workspace_root=base_dir)
            shutil.rmtree(spec_out_cand / "state" / "holds", ignore_errors=True)

        cb_run_id = (
            target_run_id
            or getattr(spec_obj, "run_id", None)
            or f"run-live-{uuid.uuid4().hex[:8]}"
        )
        w_start, w_complete, w_stream = wrap_pipeline_callbacks_with_ledger_flush(
            self,
            cb_run_id,
            session_id=target_session_id,
            on_stage_start=on_stage_start,
            on_stage_complete=on_stage_complete,
            on_stream_event=on_stream_event,
        )
        runner: PipelineRunner = pipeline_runner or get_default_pipeline_runner()
        report: AgenticPipelineReport = runner(
            spec_obj,
            setup_obj,
            output_dir=out_dir,
            base_dir=base_dir,
            cas_store=self.cas_store,
            on_stage_start=w_start,
            on_stage_complete=w_complete,
            on_stream_event=w_stream,
            is_server=eff_is_server,
            allowed_hosts=server_allowed_hosts,
        )

        # Store all intermediate and final artifacts in self.cas_store and self.artifacts
        register_report_artifacts(self, report)

        ont_art_ref = next(
            (
                a
                for a in report.intermediate_artifacts
                if a.artifact_id == "art-stage00-graph-a-ontology"
            ),
            None,
        )
        ont_sha = (
            report.graph_a_sha256.upper()
            if getattr(report, "graph_a_sha256", None)
            else (ont_art_ref.sha256.upper() if ont_art_ref is not None else None)
        )
        last_gb_ref = next(
            (
                a
                for a in reversed(report.intermediate_artifacts)
                if a.artifact_id.endswith("-stage01-graph-b-profile") and a.file_path
            ),
            None,
        )
        resolved_graph_b_path: str | None = (
            last_gb_ref.file_path if last_gb_ref and last_gb_ref.file_path else None
        )
        if resolved_graph_b_path is None and out_dir is not None:
            gb_files = sorted((out_dir / "waves").glob("*/wave_*_graph_b_profile.json"))
            if gb_files:
                cand_gb = gb_files[-1].resolve()
                if cand_gb.is_relative_to(self.repo_root):
                    resolved_graph_b_path = str(cand_gb.relative_to(self.repo_root))
                else:
                    resolved_graph_b_path = str(cand_gb)
        resolved_graph_a_path: str = ""
        if ont_art_ref is not None and ont_art_ref.file_path:
            resolved_graph_a_path = ont_art_ref.file_path
        elif getattr(spec_obj, "graph_a_ontology_path", None):
            raw_ont_path = Path(spec_obj.graph_a_ontology_path)
            if raw_ont_path.is_absolute():
                cand_ont = raw_ont_path.resolve()
            else:
                cand_base_ont = (base_dir / raw_ont_path).resolve()
                cand_repo_ont = (self.repo_root / raw_ont_path).resolve()
                cand_ont = cand_base_ont if cand_base_ont.exists() else cand_repo_ont
            if cand_ont.is_relative_to(self.repo_root):
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
                graph_b_path=resolved_graph_b_path,
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
                    resolved_graph_b_path
                    if resolved_graph_b_path
                    else existing_proj.graph_b_path
                ),
            }
            self.projects[spec_obj.project_id] = existing_proj.model_copy(update=proj_updates)

        events_list = finalize_run_ledger(
            self,
            report_run_id=report.run_id,
            target_run_id=target_run_id,
            cb_run_id=cb_run_id,
            target_session_id=target_session_id,
            report_events=list(report.observability_events),
        )

        from opendv_service.state.hold_resume import sync_report_holds_to_manager

        active_hold_ids = sync_report_holds_to_manager(
            self, report, events_list, target_run_id=target_run_id
        )

        all_codemods = {c for w in report.wave_reports for c in w.applied_codemods}
        last_w = report.wave_reports[-1] if report.wave_reports else None
        if last_w:
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
        else:
            g0_stat = g2_stat = g3_stat = g4_stat = g5_stat = "REJECT-WITH-TRACE"
            g1_stat = "ASK-USER" if report.status == "PAUSED_HOLD" else "REJECT-WITH-TRACE"

        scorecard = GateScorecardSummary(
            gate_0_profile_drift=g0_stat,
            gate_1_contract_grain=g1_stat,
            gate_2_sqlglot_ast=g2_stat,
            gate_3_networkx_dag=g3_stat,
            gate_4_yagni_cost_guard=g4_stat,
            gate_5_duckdb_sandbox=g5_stat,
        )

        run_status = RunStatus(report.status)
        last_step = report.step_reports[-1] if report.step_reports else None
        has_initial_contract = getattr(report, "has_initial_contract", False) or bool(
            getattr(spec_obj, "initial_contract_path", None)
            or getattr(spec_obj, "initial_contract", None)
            or getattr(spec_obj, "initial_contract_content", None)
            or (getattr(getattr(spec_obj, "blueprint", None), "contract", None) is not None)
        )
        init_c_path = getattr(report, "initial_contract_path", None) or getattr(
            spec_obj, "initial_contract_path", None
        )
        wave_id_val = getattr(report, "wave_id", None) or getattr(spec_obj, "wave_id", None)
        rep_lc = getattr(report, "lifecycle_stage", None)
        lifecycle_stage = (
            "MULTI_SOURCE_EXPANSION"
            if has_initial_contract and rep_lc in (None, "COLD_START_N1")
            else (rep_lc or ("MULTI_SOURCE_EXPANSION" if has_initial_contract else "COLD_START_N1"))
        )
        resolved_sid = (
            target_session_id
            or getattr(self, "run_session_ids", {}).get(report.run_id)
            or (getattr(self, "run_session_ids", {}).get(target_run_id) if target_run_id else None)
        )
        run_dto = ProjectRunDTO(
            run_id=report.run_id,
            project_id=spec_obj.project_id,
            harness_provider=setup_obj.provider,
            status=run_status,
            started_at_utc=report.started_at,
            completed_at_utc=(None if run_status == RunStatus.PAUSED_HOLD else report.completed_at),
            current_stage_id=(last_step.stage_id if last_step else "STAGE_00_ORCHESTRATION"),
            event_count=len(events_list),
            gate_scorecard=scorecard,
            active_hold_ids=active_hold_ids,
            fallbacks_applied=report.fallbacks_applied,
            events_sse_url=f"/api/v1/runs/{report.run_id}/events",
            gates_url=f"/api/v1/runs/{report.run_id}/gates",
            sandbox_url=f"/api/v1/runs/{report.run_id}/sandbox",
            decisions_url=f"/api/v1/runs/{report.run_id}/decisions",
            has_initial_contract=has_initial_contract,
            initial_contract_path=init_c_path,
            lifecycle_stage=lifecycle_stage,
            session_id=str(resolved_sid) if resolved_sid else None,
        )

        gate_rep = self._build_default_gate_report(report.run_id, run_dto=run_dto)
        gate_rep["has_initial_contract"] = has_initial_contract
        gate_rep["initial_contract_path"] = init_c_path
        gate_rep["wave_id"] = wave_id_val
        gate_rep["lifecycle_stage"] = lifecycle_stage
        if report.wave_reports:
            gate_rep["wave_gate_scorecard"] = dict(report.wave_reports[-1].gate_scorecard)
        self.run_gate_reports[report.run_id] = gate_rep
        if target_run_id and target_run_id != report.run_id:
            self.run_gate_reports[target_run_id] = dict(gate_rep)

        report_dict = report.model_dump(mode="json")
        scorecard_dict = scorecard.model_dump(mode="json")
        sandbox_summary = last_w.sandbox_summary if last_w else {}
        sandbox_passed = bool(sandbox_summary.get("passed", False)) or (g5_stat == "PASS")
        scorecard_dict.update({
            "gates_passed": all(
                v in ("PASS", "AUTO-FIX", "AUTO_REPAIRED")
                for v in (g0_stat, g1_stat, g2_stat, g3_stat, g4_stat, g5_stat)
            ),
            "shadow_passed": sandbox_passed,
            "sandbox_passed": sandbox_passed,
            "four_zone_passed": sandbox_passed,
            "sandbox_summary": sandbox_summary,
        })
        report_dict["gate_scorecard"] = scorecard_dict
        if final_contract_ref and final_contract_ref.sha256:
            c_sha = final_contract_ref.sha256
            art_meta = self.artifacts.get(c_sha.upper()) or self.artifacts.get(c_sha.lower())
            if art_meta is not None:
                c_content = art_meta.content
                if isinstance(c_content, str):
                    try:
                        c_dict = yaml.safe_load(c_content)
                    except (yaml.YAMLError, ValueError, TypeError):
                        c_dict = None
                    report_dict["contract"] = c_dict or c_content
                    report_dict["contract_content"] = c_content
                elif isinstance(c_content, dict):
                    report_dict["contract"] = c_content
                    with contextlib.suppress(yaml.YAMLError, TypeError, ValueError):
                        report_dict["contract_content"] = yaml.safe_dump(c_content, sort_keys=False)
        if (
            "contract" not in report_dict
            and final_contract_ref
            and final_contract_ref.file_path
        ):
            c_p = Path(final_contract_ref.file_path)
            if not c_p.is_absolute():
                c_p = (self.repo_root / c_p).resolve()
            if c_p.is_file():
                with contextlib.suppress(yaml.YAMLError, OSError, ValueError, TypeError):
                    c_txt = c_p.read_text(encoding="utf-8")
                    report_dict["contract"] = yaml.safe_load(c_txt)
                    report_dict["contract_content"] = c_txt
        if out_dir is not None:
            bp_file = out_dir / "vault_blueprint.yaml"
            if bp_file.is_file():
                with contextlib.suppress(yaml.YAMLError, OSError, ValueError, TypeError):
                    bp_txt = bp_file.read_text(encoding="utf-8")
                    report_dict["blueprint"] = yaml.safe_load(bp_txt)
                    report_dict["blueprint_content"] = bp_txt
        if "contract" in report_dict and "blueprint" not in report_dict:
            report_dict["blueprint"] = {
                "domain_namespace": spec_obj.domain_namespace or "default",
                "contract": report_dict["contract"],
            }
        with self._state_lock:
            self.pipeline_reports[report.run_id] = report_dict
            if target_run_id and target_run_id != report.run_id:
                self.pipeline_reports[target_run_id] = report_dict
            self.runs[report.run_id] = run_dto
            if target_run_id and target_run_id != report.run_id:
                self.runs[target_run_id] = run_dto.model_copy(update={"run_id": target_run_id})
            if hasattr(self, "explicit_hold_runs"):
                if run_status == RunStatus.PAUSED_HOLD:
                    self.explicit_hold_runs.add(report.run_id)
                    if target_run_id and target_run_id != report.run_id:
                        self.explicit_hold_runs.add(target_run_id)
                else:
                    self.explicit_hold_runs.discard(report.run_id)
                    if target_run_id:
                        self.explicit_hold_runs.discard(target_run_id)
            if run_status == RunStatus.PAUSED_HOLD:
                from opendv_service.runs.broadcaster import build_hold_sse_payload

                eff_hold_run = target_run_id or report.run_id
                hold_payload = build_hold_sse_payload(self, eff_hold_run)
                hold_evt_id = f"evt-{eff_hold_run}-hold"
                ev_list = self.run_events.setdefault(eff_hold_run, [])
                if not any(
                    isinstance(e, dict) and e.get("event_id") == hold_evt_id for e in ev_list
                ):
                    ev_list.append(
                        {
                            "event_id": hold_evt_id,
                            "run_id": eff_hold_run,
                            "timestamp_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "stage_id": str(
                                hold_payload.get("stage_id") or "STAGE_04_SHIP_VAULT_PASS2"
                            ),
                            "event_type": "hold",
                            "payload": hold_payload,
                        }
                    )
        save_platform_state(self)
        eff_run_id = target_run_id or report.run_id
        synced = self._sync_pipeline_report(eff_run_id)
        redacted_rep = dict(synced)
        redacted_rep["observability_events"] = [
            redact_event_for_role(ev, eff_role) for ev in synced.get("observability_events", [])
        ]
        return redacted_rep
