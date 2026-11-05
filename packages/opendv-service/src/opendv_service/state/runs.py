"""Run lifecycle, gate report generation, and run creation for opendv-service."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from opendv_agent_harness import StewardHoldManager
from opendv_service.models import (
    CreateRunRequest,
    GateScorecardSummary,
    PipelineRunRequest,
    ProjectRunDTO,
    RunStatus,
)
from opendv_telemetry import VaultLedgerEmitter

if TYPE_CHECKING:
    from opendv_service.state.base import BaseStateContainer
    from opendv_service.state.pipeline import PipelineStateMixin
    from opendv_service.state.pipeline_execution import PipelineExecutionMixin


class RunStateMixin:
    """Project run management, initialization, and scorecard evaluation mixin."""

    def _init_run_artifacts(
        self: BaseStateContainer,
        run_id: str,
        project_id: str,
        harness_provider: Literal[
            "agy",
            "agy-cli",
            "antigravity",
            "claude",
            "claude-cli",
            "claude-code",
        ],
        started_at_utc: str,
    ) -> ProjectRunDTO:
        ledger_path = self.ledger_dir / f"{run_id}.jsonl"
        ledger_path.unlink(missing_ok=True)
        ledger = VaultLedgerEmitter(
            ledger_path=ledger_path,
            cas_store=self.cas,
        )

        shutil.rmtree(self.state_dir / run_id / "holds", ignore_errors=True)
        hold_mgr = StewardHoldManager(state_dir=self.state_dir / run_id)

        self.run_ledgers[run_id] = ledger
        self.hold_managers[run_id] = hold_mgr
        self.run_events[run_id] = []
        self.run_workbench_events[run_id] = []
        self.run_events_by_view[run_id] = {f"WB-{i:02d}": [] for i in range(12)}

        run_dto = ProjectRunDTO(
            run_id=run_id,
            project_id=project_id,
            harness_provider=harness_provider,
            status=RunStatus.PENDING,
            started_at_utc=started_at_utc,
            completed_at_utc=None,
            current_stage_id="STAGE_00_ORCHESTRATION",
            current_view_id="WB-00",
            event_count=0,
            gate_scorecard=GateScorecardSummary(
                gate_0_profile_drift="PENDING",
                gate_1_contract_grain="PENDING",
                gate_2_sqlglot_ast="PENDING",
                gate_3_networkx_dag="PENDING",
                gate_4_yagni_cost_guard="PENDING",
                gate_5_duckdb_sandbox="PENDING",
            ),
            active_hold_ids=[],
            events_sse_url=f"/api/v1/runs/{run_id}/events",
            gates_url=f"/api/v1/runs/{run_id}/gates",
            sandbox_url=f"/api/v1/runs/{run_id}/sandbox",
            decisions_url=f"/api/v1/runs/{run_id}/decisions",
        )
        self.runs[run_id] = run_dto
        if project_id in self.projects:
            self.projects[project_id] = self.projects[project_id].model_copy(
                update={"latest_run_id": run_id}
            )
        self.run_gate_reports[run_id] = self._build_default_gate_report(run_id)
        self.run_sandbox_history[run_id] = []
        self.run_decisions[run_id] = []
        return run_dto

    def _build_default_gate_report(
        self: BaseStateContainer,
        run_id: str,
    ) -> dict[str, Any]:
        """Construct the 6-Gate scorecard and theorem verification report for a run."""
        run_dto = self.runs[run_id]
        scorecard = run_dto.gate_scorecard.model_dump(mode="json")
        overall_passed = run_dto.status != RunStatus.FAILED and all(
            status in {"PASS", "AUTO-FIX", "AUTO_REPAIRED", "ASK-USER"}
            for status in scorecard.values()
        )
        if not overall_passed:
            verdict = "REJECT-WITH-TRACE"
        elif "ASK-USER" in scorecard.values():
            verdict = "ASK-USER"
        else:
            verdict = "PASS"
        return {
            "run_id": run_id,
            "project_id": run_dto.project_id,
            "evaluated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "overall_passed": overall_passed,
            "verdict": verdict,
            "gate_scorecard": scorecard,
            "gates": {
                "G0_PROFILE_DRIFT": {
                    "gate_id": "G0",
                    "status": run_dto.gate_scorecard.gate_0_profile_drift,
                    "sample_rules": ["ALG-A9A", "EVO-01", "EVO-02", "EVO-03", "EVO-04"],
                },
                "G1_CONTRACT_AND_GRAIN": {
                    "gate_id": "G1",
                    "status": run_dto.gate_scorecard.gate_1_contract_grain,
                    "sample_rules": ["DV-COL-001", "DV-COL-012", "KEY-01", "KEY-04"],
                },
                "G2_SQLGLOT_AST": {
                    "gate_id": "G2",
                    "status": run_dto.gate_scorecard.gate_2_sqlglot_ast,
                    "sample_rules": ["DV-AST-001", "DV-AST-002", "DV-AST-004", "ALG-A5"],
                },
                "G3_NETWORKX_DAG": {
                    "gate_id": "G3",
                    "status": run_dto.gate_scorecard.gate_3_networkx_dag,
                    "sample_rules": ["DV-STR-001", "DV-STR-014", "INV-01", "INV-03"],
                },
                "G4_YAGNI_COST_GUARD": {
                    "gate_id": "G4",
                    "status": run_dto.gate_scorecard.gate_4_yagni_cost_guard,
                },
                "G5_DUCKDB_SANDBOX": {
                    "gate_id": "G5",
                    "status": run_dto.gate_scorecard.gate_5_duckdb_sandbox,
                },
            },
        }

    def create_run(
        self: BaseStateContainer | PipelineExecutionMixin,
        req: CreateRunRequest,
    ) -> ProjectRunDTO:
        """Start a new verification/sandbox run and populate its observability ledger."""
        provider_override = (
            req.harness_provider
            if (req.harness_config_path is None or req.harness_provider != "antigravity")
            else None
        )
        if req.pipeline_input_path is not None:
            rep_dict = self.execute_agentic_pipeline_run(
                PipelineRunRequest(
                    input_spec_path=req.pipeline_input_path,
                    harness_config_path=req.harness_config_path,
                    provider_override=provider_override,
                )
            )
            return self.runs[str(rep_dict["run_id"])]

        if req.project_id is None or req.project_id not in self.projects:
            raise KeyError(f"Unknown project_id '{req.project_id}'")

        if req.contract_path is not None:
            self._resolve_repo_path(req.contract_path)
            self.projects[req.project_id] = self.projects[req.project_id].model_copy(
                update={"contract_path": req.contract_path}
            )

        proj = self.projects[req.project_id]
        bronze_sources = self.project_bronze_sources.get(req.project_id, [])
        if bronze_sources and proj.graph_a_ontology_path is not None:
            source_feeds: list[dict[str, Any]] = []
            unstructured_documents: list[dict[str, Any]] = []
            for idx, src_rel in enumerate(bronze_sources):
                resolved_src = self._resolve_repo_path(src_rel)
                ext = resolved_src.suffix.lower()
                if ext in {".txt", ".md", ".pdf"}:
                    unstructured_documents.append(
                        {
                            "doc_id": f"doc_{idx + 1:02d}_{resolved_src.stem}",
                            "document_path": str(resolved_src),
                        }
                    )
                else:
                    source_feeds.append(
                        {
                            "feed_id": f"feed_{idx + 1:02d}_{resolved_src.stem}",
                            "source_system": resolved_src.parent.name or "BRONZE_SOURCE",
                            "table_name": resolved_src.stem,
                            "file_path": str(resolved_src),
                        }
                    )
            dynamic_input_spec: dict[str, Any] = {
                "pipeline_id": f"pipe-{req.project_id}",
                "project_id": req.project_id,
                "display_name": proj.display_name,
                "domain_namespace": proj.domain_namespace,
                "graph_a_ontology_path": str(self._resolve_repo_path(proj.graph_a_ontology_path)),
                "waves": [
                    {
                        "wave_index": 0,
                        "wave_name": "Dynamic Project Discovery Wave",
                        "wave_type": "COLD_START",
                        "sources": source_feeds,
                        "unstructured_documents": unstructured_documents,
                    }
                ],
            }
            rep_dict = self.execute_agentic_pipeline_run(
                PipelineRunRequest(
                    input_spec=dynamic_input_spec,
                    harness_config_path=req.harness_config_path,
                    provider_override=provider_override,
                    steward_hold_policy="auto_approve_top_candidate",
                )
            )
            return self.runs[str(rep_dict["run_id"])]

        run_index = len(self.runs) + 1
        run_id = f"run-{req.project_id}-{run_index:04d}"
        now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self._init_run_artifacts(
            run_id=run_id,
            project_id=req.project_id,
            harness_provider=req.harness_provider,
            started_at_utc=now_iso,
        )

    def abort_run(
        self: BaseStateContainer | PipelineStateMixin,
        run_id: str,
    ) -> ProjectRunDTO:
        """Transition a run to ABORTED status."""
        if run_id not in self.runs:
            raise KeyError(f"Unknown run_id '{run_id}'")
        run_dto = self.runs[run_id]
        updated = run_dto.model_copy(
            update={
                "status": RunStatus.ABORTED,
                "completed_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        self.runs[run_id] = updated
        self._sync_pipeline_report(run_id)
        return updated
