"""Captain-Hold / Steward hold resolution and event recording for opendv-service."""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from opendv_agent_harness import build_observability_event_dict
from opendv_contracts.dtos.ontology import (
    AcceptOntologyDeltaOption,
    CustomOntologyDeltaOption,
    RejectOntologyDeltaOption,
)
from opendv_contracts.errors import ErrorCode
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.schema_registry import load_structured_file
from opendv_service.errors import NotFoundError
from opendv_service.models import (
    ArtifactMetadataResponse,
    CaptainHoldDecisionRequest,
    CaptainHoldDecisionResponse,
    RunStatus,
)
from opendv_service.ontology.resolution import resolve_ontology_hold
from opendv_sources.config.graph_a_loader import load_graph_a_source

if TYPE_CHECKING:
    from opendv_service.state.base import BaseStateContainer
    from opendv_service.state.pipeline import PipelineStateMixin
    from opendv_service.state.runs import RunStateMixin


class HoldStateMixin:
    """Steward / Captain-Hold resolution mixin."""

    def resolve_captain_hold(
        self: BaseStateContainer | RunStateMixin | PipelineStateMixin,
        run_id: str,
        req: CaptainHoldDecisionRequest | None = None,
        *,
        hold_id: str | None = None,
        decision: str | None = None,
        notes: str | None = None,
        hold_option: Any | None = None,
    ) -> CaptainHoldDecisionResponse:
        """Apply a Steward decision to a hold, appending a resolution event."""
        if req is None:
            action_val = (decision or "APPROVE").strip().upper()
            action_lit = action_val if action_val in {"APPROVE", "REJECT", "ANSWER"} else "APPROVE"
            req = CaptainHoldDecisionRequest(
                hold_id=hold_id,
                action=action_lit,
                resolution_notes=notes,
                hold_option=hold_option,
            )
        elif hold_option is not None and req.hold_option is None:
            req = req.model_copy(update={"hold_option": hold_option})

        if run_id not in self.runs:
            raise NotFoundError(f"Run '{run_id}' not found.", error_code=ErrorCode.RUN_NOT_FOUND)

        if run_id not in self.hold_managers:
            raise NotFoundError(
                f"Hold not found on run '{run_id}'.",
                error_code=ErrorCode.HOLD_NOT_FOUND,
            )

        hold_mgr = self.hold_managers[run_id]
        resolved_hold_id = req.hold_id
        resolved_event_id = req.event_id
        if not resolved_hold_id or not resolved_event_id:
            pending_holds = hold_mgr.list_pending_holds(run_id=run_id)
            if len(pending_holds) == 1:
                resolved_hold_id = resolved_hold_id or pending_holds[0].hold_id
                resolved_event_id = resolved_event_id or pending_holds[0].event_id
            elif not resolved_hold_id and not resolved_event_id:
                raise NotFoundError(
                    "Hold ID is required when no single active hold is pending.",
                    error_code=ErrorCode.HOLD_NOT_FOUND,
                )
            else:
                candidate_lookup = resolved_hold_id or resolved_event_id or ""
                try:
                    existing = hold_mgr.get_hold(candidate_lookup)
                    resolved_hold_id = resolved_hold_id or existing.hold_id
                    resolved_event_id = resolved_event_id or existing.event_id
                except KeyError as exc:
                    raise NotFoundError(
                        f"Hold '{candidate_lookup}' not found on run '{run_id}'.",
                        error_code=ErrorCode.HOLD_NOT_FOUND,
                    ) from exc

        target_hold_id = resolved_hold_id or resolved_event_id
        if not target_hold_id:
            raise NotFoundError(
                "Hold ID is required when no single active hold is pending.",
                error_code=ErrorCode.HOLD_NOT_FOUND,
            )

        try:
            existing_record = hold_mgr.get_hold(target_hold_id)
            if existing_record.status != "PENDING":
                raise ValueError(
                    f"Captain-Hold '{target_hold_id}' is already resolved "
                    f"({existing_record.status})."
                )
        except KeyError as exc:
            raise NotFoundError(
                f"Hold '{target_hold_id}' not found on run '{run_id}'.",
                error_code=ErrorCode.HOLD_NOT_FOUND,
            ) from exc

        notes = req.resolution_notes or f"Resolved by {req.steward_id} via {req.action}"
        resolved_record = hold_mgr.resolve_hold(
            target_hold_id,
            action=req.action,
            selected_candidate_id=req.selected_candidate_id,
            resolution_notes=notes,
        )

        for pending in hold_mgr.list_pending_holds(run_id=run_id):
            hold_mgr.resolve_hold(
                pending.decision_id,
                action=req.action,
                selected_candidate_id=req.selected_candidate_id,
                resolution_notes=notes,
            )

        resolved_at = resolved_record.resolved_at_utc or datetime.now(UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        emitted_event_id = f"evt-hold-resolved-{len(self.run_events[run_id]) + 1:03d}"
        verdict = "PASS" if req.action in {"APPROVE", "ANSWER"} else "REJECT-WITH-TRACE"
        resolution_event = build_observability_event_dict(
            event_id=emitted_event_id,
            run_id=run_id,
            trace_id=uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=uuid.uuid4().hex[:16],
            timestamp_utc=resolved_at,
            stage_id="STAGE_02_SCOUT_UNSTRUCTURED",
            workbench_view_id="WB-03",
            viz_id="VIZ-STEP-04",
            agent_persona="vault-first-mate",
            status=verdict,
            headline=(
                f"Steward '{req.steward_id}' resolved Captain-Hold '{target_hold_id}' "
                f"with action {req.action}."
            ),
            business_impact=notes[:200],
            data_classification="CONFIDENTIAL",
            rule_ids_evaluated=["ALG-A9B", "U3"],
            summary_metrics={
                "hold_id": target_hold_id,
                "action": req.action,
                "steward_id": req.steward_id,
                "active_captain_holds": 0,
            },
        )

        self.run_ledgers[run_id].emit(resolution_event)
        self.run_events[run_id].append(resolution_event)
        self.run_workbench_events[run_id].append(resolution_event)
        self.run_events_by_view.setdefault(run_id, {}).setdefault("WB-03", []).append(
            resolution_event
        )

        run_dto = self.runs[run_id]
        remaining_ids = [rec.hold_id for rec in hold_mgr.list_pending_holds(run_id=run_id)]
        new_status = (
            RunStatus.COMPLETED if req.action in {"APPROVE", "ANSWER"} else RunStatus.FAILED
        )

        # ONT-04: If typed hold option is provided, evolve Graph A and resume wave
        if req.hold_option is not None:
            if isinstance(req.hold_option, (AcceptOntologyDeltaOption, CustomOntologyDeltaOption)):
                proj = self.projects.get(run_dto.project_id)
                base_ont: GraphAOntology | None = None
                if proj and proj.graph_a_ontology_path:
                    with contextlib.suppress(Exception):
                        loaded = load_graph_a_source(
                            proj.graph_a_ontology_path,
                            base_dir=self.repo_root,
                            workspace_root=self.repo_root,
                        )
                        base_ont = loaded.ontology
                if base_ont is None:
                    ont_sha = (proj.graph_a_sha256 if proj else None) or getattr(
                        run_dto, "graph_a_sha256", None
                    )
                    if ont_sha and ont_sha in self.artifacts:
                        art = self.artifacts[ont_sha]
                        if isinstance(art.content, dict):
                            base_ont = GraphAOntology.model_validate(art.content)
                if base_ont is not None:
                    deployed_c = None
                    if proj and proj.contract_path:
                        with contextlib.suppress(Exception):
                            deployed_c = load_structured_file(
                                proj.contract_path, workspace_root=self.repo_root
                            )
                    ont_res = resolve_ontology_hold(
                        base_ontology=base_ont,
                        option=req.hold_option,
                        run_id=run_id,
                        hold_id=target_hold_id,
                        steward_id=req.steward_id,
                        cas_store=self.cas_store,
                        deployed_contract=deployed_c,
                        ledger=self.run_ledgers.get(run_id),
                    )
                    if ont_res.new_ontology and ont_res.new_graph_a_sha256:
                        meta = ArtifactMetadataResponse(
                            sha256=ont_res.new_graph_a_sha256.upper(),
                            byte_length=len(ont_res.new_ontology.model_dump_json()),
                            media_type="application/x-yaml",
                            artifact_name="graph_a_ontology.yaml",
                            content=ont_res.new_ontology.model_dump(mode="json", exclude_none=True),
                        )
                        self.artifacts[ont_res.new_graph_a_sha256.upper()] = meta
                        self.artifacts[ont_res.new_graph_a_sha256.lower()] = meta
                        if proj:
                            self.projects[run_dto.project_id] = proj.model_copy(
                                update={"graph_a_sha256": ont_res.new_graph_a_sha256}
                            )
                        new_status = RunStatus.RUNNING
            elif isinstance(req.hold_option, RejectOntologyDeltaOption):
                new_status = RunStatus.FAILED

        updated_scorecard = (
            run_dto.gate_scorecard.model_copy(
                update={
                    "gate_1_contract_grain": (
                        "AUTO_REPAIRED"
                        if (
                            req.hold_option is not None
                            or run_dto.gate_scorecard.gate_1_contract_grain
                            in {"ASK-USER", "PENDING"}
                        )
                        else run_dto.gate_scorecard.gate_1_contract_grain
                    )
                }
            )
            if req.action in {"APPROVE", "ANSWER"}
            else run_dto.gate_scorecard.model_copy(
                update={"gate_1_contract_grain": "REJECT-WITH-TRACE"}
            )
        )
        is_resumed_running = new_status == RunStatus.RUNNING
        updated_run = run_dto.model_copy(
            update={
                "status": new_status,
                "completed_at_utc": None if is_resumed_running else resolved_at,
                "current_stage_id": (
                    "STAGE_03_SHIP_VAULT_PASS1" if is_resumed_running else run_dto.current_stage_id
                ),
                "active_hold_ids": remaining_ids,
                "event_count": len(self.run_events[run_id]),
                "gate_scorecard": updated_scorecard,
            }
        )
        self.runs[run_id] = updated_run
        self.run_gate_reports[run_id] = self._build_default_gate_report(run_id)
        self._sync_pipeline_report(run_id)

        decision_resp = CaptainHoldDecisionResponse(
            decision_id=resolved_record.decision_id,
            hold_id=target_hold_id,
            action=req.action,
            status="ACCEPTED" if req.action in {"APPROVE", "ANSWER"} else "REJECTED",
            resumed_run_id=run_id,
            next_stage_id="STAGE_03_SHIP_VAULT_PASS1",
            next_event_id=emitted_event_id,
            updated_run_status=new_status,
            resolved_at_utc=resolved_at,
        )
        self.run_decisions[run_id].append(decision_resp)
        return decision_resp
