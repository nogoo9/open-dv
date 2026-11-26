"""Step 4 coordinator discovery run wiring JobMcpBroker, ExternalMcpGateway, and harnesses."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pydantic import Field, TypeAdapter, ValidationError

if TYPE_CHECKING:
    from opendv.service.mcp_broker import JobMcpBroker

from opendv.agents import (
    AgentHarnessPort,
    AgentTurnRequest,
    load_agent_pack_with_config,
)
from opendv.models.diagnostics import ClarificationQuestion, StrictModel
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import RunCheckpointRecord, RunStreamEvent
from opendv.models.ontology import GraphAOntology
from opendv.models.ontology_deltas import AGENT_ADDITIVE_OPS, OntologyDelta
from opendv.models.policy import STEP4_DEFAULT_BUDGET
from opendv.models.project import DiscoveryMode
from opendv.models.wire_dtos_discovery import (
    ClientAdditiveOp,
    HoldAnswersReq,
    OntologyConstructReq,
    RunControlReq,
    RunStateResp,
)
from opendv.service._mcp_tools import coord_propose_ontology_delta
from opendv.service.bm25_matcher import Bm25ConceptMatcher
from opendv.service.readiness import HarnessReadinessService
from opendv.service.secrets import KeyringSecretStore
from opendv.service.settings_store import SettingsStore
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._discovery_run_ctx import (
    build_source_and_doc_context,
    build_workspace_seed_concepts,
    compute_profiles_fp,
    format_step4_prompt,
    normalize_step4_raw_op,
)
from opendv.service.workflows._run_adapters import (
    ServiceRunCheckpointStore,
    ServiceRunEventSink,
    acquire_project_job_lease,
    append_audit_log,
    coalesce_prior_state_budget,
    coalesce_snapshot_budget,
    compute_job_lease_ttl,
    enqueue_control_signal,
    load_latest_run_state,
    mark_run_failed_from_exception,
    record_run_state,
    release_project_job_lease,
    resolve_harness,
)

_DELTA_ADAPTER: TypeAdapter[OntologyDelta] = TypeAdapter(OntologyDelta)


class Step4CoordinatorOutput(StrictModel):
    """Compact structured output returned by Step 4 ontology coordinator turns."""

    proposed_ops: list[dict[str, Any]] = Field(default_factory=list)
    clarification_questions: list[ClarificationQuestion] = Field(default_factory=list)
    staged_candidate_ids: list[str] = Field(default_factory=list)
    summary: str = ""


class DiscoveryRunHelper:
    """Async Step 4 ontology construction, Steward Hold resume, and run control."""

    def __init__(
        self,
        *,
        state_manager: ProjectStateManager,
        settings_store: SettingsStore,
        secret_store: KeyringSecretStore,
        matcher: Bm25ConceptMatcher,
        workspace_root: Path,
        injected_harness: AgentHarnessPort | None = None,
        readiness_service: HarnessReadinessService | None = None,
    ) -> None:
        self._state = state_manager
        self._settings = settings_store
        self._secrets = secret_store
        self._matcher = matcher
        self._workspace_root = workspace_root
        self._injected_harness = injected_harness
        self._readiness_service = readiness_service

    async def start_construction(
        self,
        project_id: str,
        req: OntologyConstructReq,
        *,
        run_id: str | None = None,
    ) -> RunStateResp:
        """Execute an asynchronous Step 4 ontology discovery coordinator turn."""
        eff_run_id = run_id or f"run4-{uuid.uuid4().hex[:10]}"
        return await self._execute_turn(
            project_id=project_id,
            run_id=eff_run_id,
            discovery_mode=req.discovery_mode,
            hint=req.hint,
            checkpoint=None,
            answers=None,
            attempt=1,
        )

    async def answer_hold(
        self,
        project_id: str,
        run_id: str,
        req: HoldAnswersReq,
    ) -> RunStateResp:
        """Resume a Step 4 HOLD_CHECKPOINTED run with Steward clarification answers."""
        prior = load_latest_run_state(self._state, project_id, run_id)
        if prior.status != "HOLD_CHECKPOINTED":
            raise OpenDVError(
                f"Run '{run_id}' is in status '{prior.status}', not HOLD_CHECKPOINTED.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                run_id=run_id,
            )
        ckpt_store = ServiceRunCheckpointStore(self._state, project_id)
        ckpt = ckpt_store.load_checkpoint(run_id)
        if ckpt is None:
            raise OpenDVError(
                f"No Hold checkpoint record found for run '{run_id}'.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
                run_id=run_id,
            )
        next_ckpt = ckpt.model_copy(
            update={
                "resume_payload": {
                    **ckpt.resume_payload,
                    "steward_answers": [f"{k}={v}" for k, v in sorted(req.answers.items())],
                }
            }
        )
        raw_mode = ckpt.resume_payload.get("discovery_mode")
        mode: DiscoveryMode | None = (
            cast(DiscoveryMode, raw_mode)
            if raw_mode in ("incremental_new", "incremental_reverify", "full_rebuild")
            else None
        )
        return await self._execute_turn(
            project_id=project_id,
            run_id=run_id,
            discovery_mode=mode,
            hint=None,
            checkpoint=next_ckpt,
            answers=req.answers,
            attempt=prior.attempt + 1,
            prior_state=prior,
        )

    def control_run(
        self,
        project_id: str,
        run_id: str,
        req: RunControlReq,
    ) -> RunStateResp:
        """Enqueue a mid-run Step 4 control signal and return updated run state."""
        if req.action in ("accept-best", "horizon-review"):
            raise OpenDVError(
                f"Control action '{req.action}' is only valid for Step 6 runs.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                run_id=run_id,
            )
        current = load_latest_run_state(self._state, project_id, run_id)
        if current.step != "step4":
            raise OpenDVError(
                f"Step 4 run '{run_id}' not found in project '{project_id}'.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
                run_id=run_id,
            )
        enqueue_control_signal(self._state, project_id, run_id, req)
        current = load_latest_run_state(self._state, project_id, run_id)
        if req.action == "pause" and current.status == "RUNNING":
            updated = current.model_copy(
                update={"status": "HOLD_REQUESTED", "phase": "hold_requested_by_steward"}
            )
            record_run_state(self._state, project_id, updated)
            return load_latest_run_state(self._state, project_id, run_id)
        return current

    async def _execute_turn(
        self,
        *,
        project_id: str,
        run_id: str,
        discovery_mode: DiscoveryMode | None,
        hint: str | None,
        checkpoint: RunCheckpointRecord | None,
        answers: dict[str, str] | None,
        attempt: int,
        prior_state: RunStateResp | None = None,
    ) -> RunStateResp:
        proj = self._state.get_project(project_id)
        snap = proj.settings_snapshot
        eff_mode: DiscoveryMode = discovery_mode or snap.discovery_mode
        budget = (
            coalesce_prior_state_budget(STEP4_DEFAULT_BUDGET, prior_state)
            if checkpoint is not None and prior_state is not None
            else coalesce_snapshot_budget(STEP4_DEFAULT_BUDGET, snap)
        )
        ws_settings = self._settings.load_settings()
        harness = resolve_harness(
            snap,
            self._secrets,
            injected_harness=self._injected_harness,
            readiness_service=self._readiness_service,
        )
        acquire_project_job_lease(
            self._state,
            project_id,
            run_id,
            ttl_seconds=compute_job_lease_ttl(budget.max_turns, budget.timeout_seconds),
        )

        running_state = RunStateResp(
            run_id=run_id,
            project_id=project_id,
            step="step4",
            status="RUNNING",
            phase="discovery_scouting",
            attempt=attempt,
            budget_turns_used=0,
            budget_tokens_used=0,
            effective_max_turns=budget.max_turns,
            effective_max_tokens=budget.max_tokens,
            effective_timeout_seconds=budget.timeout_seconds,
        )
        broker: JobMcpBroker | None = None
        lease_released = False
        try:
            pre_sig_count = len(self._state.poll_control_signals(project_id, run_id))
            record_run_state(self._state, project_id, running_state)

            ontology = GraphAOntology(
                ontology_version="0.0.0",
                domain_namespace=proj.domain_namespace,
            )
            if proj.active_ontology_rev_id is not None:
                rev = self._state.get_revision(project_id, proj.active_ontology_rev_id)
                ontology = GraphAOntology.model_validate(rev["payload"])

            profiles, parsed_pages, doc_summaries = build_source_and_doc_context(
                self._state, project_id, self._workspace_root
            )
            fp = compute_profiles_fp(profiles)
            sink = ServiceRunEventSink(self._state, project_id)
            ckpt_store = ServiceRunCheckpointStore(self._state, project_id)
            from opendv.service.external_mcp import ExternalMcpGateway
            from opendv.service.mcp_broker import JobMcpBroker

            ext_gateway = ExternalMcpGateway(
                state_store=self._state,
                secret_store=self._secrets,
                settings_store=self._settings,
            )
            broker = JobMcpBroker(
                project_id=project_id,
                run_id=run_id,
                state_store=self._state,
                bm25_matcher=self._matcher,
                external_gateway=ext_gateway,
                budget=budget,
                event_sink=sink,
                workspace_seeds=build_workspace_seed_concepts(
                    self._state, project_id, self._workspace_root
                ),
                parsed_pages=parsed_pages,
                ontology_rev_id=proj.active_ontology_rev_id,
                sources_fingerprint=fp,
                discovery_mode=eff_mode,
            )
            prior_events = self._state.tail_run_stream_events(project_id, run_id, since_seq=0)
            broker.hold_coord.seq = max((ev.seq for ev in prior_events.events), default=0)
            await broker.start()
            dispatcher = broker.build_scout_dispatcher(
                harness,
                event_sink=sink,
                model_overrides=snap.model_overrides,
            )
            pack_detail, pack_cfg = load_agent_pack_with_config(
                "ontology-first-mate",
                workspace_root=self._workspace_root,
                trusted_workspaces=ws_settings.trusted_workspaces,
            )
            prompt = format_step4_prompt(
                project_id=project_id,
                ontology=ontology,
                profiles=profiles,
                doc_summaries=doc_summaries,
                discovery_mode=eff_mode,
                hint=hint,
                answers=answers,
            )
            turn_req = AgentTurnRequest(
                run_id=run_id,
                agent_id="ontology-first-mate",
                step="step4",
                prompt=prompt,
                system_instructions=pack_detail.markdown_body,
                output_schema=Step4CoordinatorOutput,
                model_tier=pack_cfg.model_tier,
                role_kind="coord",
                role_token=broker.coord_token,
                broker_url=broker.coord_url,
                allowed_tools=list(pack_cfg.allowed_tools),
                budget=budget,
                checkpoint=checkpoint,
                attempt=attempt,
                metadata={
                    "discovery_mode": eff_mode,
                    "model_overrides": dict(snap.model_overrides),
                },
            )
            pre_ckpt_count = len(
                self._state.list_events_or_receipts(
                    project_id, record_kind="run_checkpoint", run_id=run_id
                )
            )
            turn_res = await harness.run_turn(
                turn_req, event_sink=sink, scout_dispatcher=dispatcher
            )

            parsed_out: Step4CoordinatorOutput | None = None
            if isinstance(turn_res.structured_output, Step4CoordinatorOutput):
                parsed_out = turn_res.structured_output
            elif isinstance(turn_res.structured_output, dict):
                try:
                    parsed_out = Step4CoordinatorOutput.model_validate(turn_res.structured_output)
                except ValidationError:
                    parsed_out = None
            elif turn_res.text_output.strip().startswith("{"):
                try:
                    raw_txt = json.loads(turn_res.text_output)
                    if isinstance(raw_txt, dict):
                        parsed_out = Step4CoordinatorOutput.model_validate(raw_txt)
                except (json.JSONDecodeError, ValidationError):
                    parsed_out = None

            new_signals = self._state.poll_control_signals(
                project_id, run_id, after_seq=pre_sig_count
            )
            paused_by_control = any(sig.action == "pause" for sig in new_signals)

            if (
                parsed_out is not None
                and parsed_out.proposed_ops
                and not broker.hold_coord.hold_requested
                and not paused_by_control
            ):
                wrapped_ops: list[ClientAdditiveOp] = []
                for raw_op in parsed_out.proposed_ops:
                    norm_op = normalize_step4_raw_op(raw_op)
                    op_type = str(norm_op.get("op_type", ""))
                    if op_type not in AGENT_ADDITIVE_OPS:
                        raise OpenDVError(
                            f"Step 4 coordinator proposed non-additive op '{op_type}'.",
                            error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
                            run_id=run_id,
                        )
                    try:
                        delta = _DELTA_ADAPTER.validate_python(norm_op)
                    except ValidationError as exc:
                        raise OpenDVError(
                            f"Invalid Step 4 coordinator delta payload for '{op_type}': {exc}",
                            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                            run_id=run_id,
                        ) from exc
                    wrapped_ops.append(ClientAdditiveOp(op=delta))
                coord_propose_ontology_delta(broker.context, wrapped_ops)

            all_ckpts = self._state.list_events_or_receipts(
                project_id, record_kind="run_checkpoint", run_id=run_id
            )
            new_ckpt = (
                ckpt_store.load_checkpoint(run_id)
                if len(all_ckpts) > pre_ckpt_count
                else None
            )
            pending_questions = list(new_ckpt.pending_questions) if new_ckpt is not None else []
            if paused_by_control and not broker.hold_coord.hold_requested:
                broker.hold_coord.hold_requested = True
                await broker.hold_coord.wait_for_drain()
            if (
                not pending_questions
                and parsed_out is not None
                and parsed_out.clarification_questions
            ):
                pending_questions = list(parsed_out.clarification_questions)
            if new_ckpt is None and (pending_questions or broker.hold_coord.hold_requested):
                hold_seq = broker.hold_coord.emit(
                    "HOLD_CHECKPOINTED",
                    {
                        "pending_questions": [
                            q.model_dump(mode="json") for q in pending_questions
                        ],
                        "reason": "steward_pause" if paused_by_control else "steward_hold",
                    },
                )
                ckpt_store.save_checkpoint(
                    RunCheckpointRecord(
                        run_id=run_id,
                        step="step4",
                        phase="steward_hold",
                        status="HOLD_CHECKPOINTED",
                        last_seq=hold_seq,
                        ontology_rev_id=proj.active_ontology_rev_id,
                        sources_fingerprint=fp,
                        pending_questions=pending_questions,
                        budget_turns_used=turn_res.turns_used,
                        budget_tokens_used=turn_res.total_tokens,
                        token_count_source=turn_res.token_count_source,
                        resume_payload={"discovery_mode": eff_mode},
                    )
                )

            if pending_questions or broker.hold_coord.hold_requested:
                cur_seq = sink.current_seq(run_id)
                final_state = running_state.model_copy(
                    update={
                        "status": "HOLD_CHECKPOINTED",
                        "phase": "steward_hold",
                        "budget_turns_used": turn_res.turns_used,
                        "budget_tokens_used": turn_res.total_tokens,
                        "pending_questions": pending_questions,
                        "last_seq": cur_seq,
                    }
                )
            else:
                next_seq = max(broker.hold_coord.sync_seq(), sink.current_seq(run_id)) + 1
                broker.hold_coord.seq = next_seq
                sink.emit(
                    RunStreamEvent(
                        seq=next_seq,
                        run_id=run_id,
                        ts=datetime.now(tz=UTC).isoformat(),
                        event_type="RUN_COMPLETED",
                        step="step4",
                        payload={
                            "status": "COMPLETED",
                            "phase": "proposals_ready_for_steward",
                        },
                    )
                )
                final_state = running_state.model_copy(
                    update={
                        "status": "COMPLETED",
                        "phase": "proposals_ready_for_steward",
                        "budget_turns_used": turn_res.turns_used,
                        "budget_tokens_used": turn_res.total_tokens,
                        "last_seq": sink.current_seq(run_id),
                    }
                )
            if broker is not None:
                await broker.stop()
                broker = None
            release_project_job_lease(self._state, project_id, run_id)
            lease_released = True
            record_run_state(self._state, project_id, final_state)
            append_audit_log(
                self._state,
                project_id,
                event_type="STEP4_DISCOVERY_TURN",
                actor="agent",
                step="step4",
                run_id=run_id,
                summary=(
                    f"Step 4 turn finished with status {final_state.status} "
                    "(no autonomous Graph A commit)."
                ),
            )
            return load_latest_run_state(self._state, project_id, run_id)
        except Exception as exc:
            mark_run_failed_from_exception(self._state, project_id, running_state, exc)
            raise
        finally:
            if broker is not None:
                await broker.stop()
            if not lease_released:
                release_project_job_lease(self._state, project_id, run_id)
