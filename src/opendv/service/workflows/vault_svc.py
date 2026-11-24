"""Step 6 CEGAR synthesis, Horizon review, Steward holds/waivers, and Guided UX facade."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from pathlib import Path

from opendv.agents import (
    AgentHarnessPort,
    CegarRunRequest,
    CegarRunResult,
    run_step6_cegar,
    run_v8_horizon_review,
)
from opendv.models.diagnostics import AntiPatternJustification
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.policy import CEGAR_POLICY_SPECS, CegarPolicy
from opendv.models.project import RevisionMetadata
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_discovery import HoldAnswersReq, RunControlReq, RunStateResp
from opendv.models.wire_dtos_vault import (
    AttemptSnapshotSpec,
    GuidedRunView,
    HorizonActionReq,
    VaultBuildReq,
    VaultVerifyResp,
    WaiverSubmitReq,
)
from opendv.service.guided_translator import GuidedUxTranslator
from opendv.service.secrets import KeyringSecretStore
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._run_adapters import (
    ServiceControlSignalReader,
    ServiceRunCheckpointStore,
    ServiceRunEventSink,
    acquire_project_job_lease,
    enqueue_control_signal,
    list_run_attempts,
    load_active_certified_ir,
    load_latest_run_state,
    mark_run_failed_from_exception,
    record_run_state,
    release_project_job_lease,
    resolve_harness,
)
from opendv.service.workflows._vault_helpers import (
    build_vault_guided_view,
    load_cegar_result,
    load_verifier_inputs,
    persist_cegar_result,
    prepare_resume_cegar_request,
    save_horizon_assumption_revision,
)
from opendv.storage.prover.duckdb_prover import DuckDbProverAdapter
from opendv.verifier import VerifierReport, run_v1_to_v6_gates


def _require_step6_run(
    state_store: ProjectStateManager,
    project_id: str,
    run_id: str,
) -> RunStateResp:
    state = load_latest_run_state(state_store, project_id, run_id)
    if state.step != "step6":
        raise OpenDVError(
            f"Step 6 run '{run_id}' not found in project '{project_id}'.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    return state


class VaultService:
    """Step 6 Vault CEGAR synthesis, Horizon review, waivers, verification, and Guided UX."""

    def __init__(
        self,
        *,
        state_manager: ProjectStateManager,
        secret_store: KeyringSecretStore,
        workspace_root: Path,
        injected_harness: AgentHarnessPort | None = None,
    ) -> None:
        self._state = state_manager
        self._secrets = secret_store
        self._workspace_root = workspace_root
        self._injected_harness = injected_harness
        self._translator = GuidedUxTranslator()

    async def build_vault(
        self,
        project_id: str,
        req: VaultBuildReq,
        *,
        run_id: str | None = None,
    ) -> RunStateResp:
        """Start and execute a Step 6 CEGAR synthesis run wired with DuckDbProverAdapter."""
        proj = self._state.get_project(project_id)
        (
            ontology,
            profiles,
            clusters,
            locks,
            prior_ir,
            assumptions,
            ctx,
        ) = load_verifier_inputs(self._state, self._workspace_root, project_id)
        ctx = ctx.model_copy(update={"policy": req.policy})
        harness = resolve_harness(
            proj.settings_snapshot,
            self._secrets,
            injected_harness=self._injected_harness,
        )
        eff_run_id = run_id or f"run6-{uuid.uuid4().hex[:10]}"
        acquire_project_job_lease(self._state, project_id, eff_run_id)

        budget = CEGAR_POLICY_SPECS[req.policy].budget
        running = RunStateResp(
            run_id=eff_run_id,
            project_id=project_id,
            step="step6",
            status="RUNNING",
            policy=req.policy,
            phase="cegar_synthesis",
            attempt=0,
            budget_turns_used=0,
            budget_tokens_used=0,
            effective_max_turns=budget.max_turns,
            effective_max_tokens=budget.max_tokens,
            effective_timeout_seconds=budget.timeout_seconds,
        )

        try:
            record_run_state(self._state, project_id, running)
            cegar_req = CegarRunRequest(
                run_id=eff_run_id,
                project_id=project_id,
                ontology=ontology,
                profiles=profiles,
                clusters=clusters,
                policy=req.policy,
                budget=budget,
                evolution_locks=locks,
                prior_certified_ir=prior_ir,
                verifier_ctx=ctx,
                prover=DuckDbProverAdapter(),
                harness=harness,
                event_sink=ServiceRunEventSink(self._state, project_id),
                control_reader=ServiceControlSignalReader(self._state, project_id),
                checkpoint_store=ServiceRunCheckpointStore(self._state, project_id),
                horizon_assumptions=assumptions,
            )
            result = await run_step6_cegar(cegar_req)
            return persist_cegar_result(
                self._state,
                project_id=project_id,
                run_id=eff_run_id,
                policy=req.policy,
                ontology_rev_id=proj.active_ontology_rev_id,
                prior_wave_rev_id=proj.active_wave_id,
                result=result,
            )
        except Exception as exc:
            mark_run_failed_from_exception(self._state, project_id, running, exc)
            raise
        finally:
            release_project_job_lease(self._state, project_id, eff_run_id)

    def get_vault_run(self, project_id: str, run_id: str) -> RunStateResp:
        """Return latest RunStateResp for a Step 6 Vault run."""
        return _require_step6_run(self._state, project_id, run_id)

    def list_vault_attempts(self, project_id: str, run_id: str) -> list[AttemptSnapshotSpec]:
        """List recorded CEGAR AttemptSnapshotSpec records for run_id."""
        _require_step6_run(self._state, project_id, run_id)
        return list_run_attempts(self._state, project_id, run_id)

    async def send_vault_control(
        self,
        project_id: str,
        run_id: str,
        req: RunControlReq,
    ) -> RunStateResp:
        """Send a Step 6 control signal or run in-place V8 Horizon review on CERTIFIED_FAST."""
        state = _require_step6_run(self._state, project_id, run_id)
        sig = enqueue_control_signal(self._state, project_id, run_id, req)
        state = load_latest_run_state(self._state, project_id, run_id)
        if req.action == "horizon-review" and state.status == "COMPLETED":
            prior_res = load_cegar_result(self._state, project_id, run_id)
            if prior_res is not None and prior_res.status == "CERTIFIED_FAST":
                return await self._run_horizon_upgrade(project_id, run_id, prior_res)
        if req.action == "pause" and state.status == "RUNNING":
            updated = state.model_copy(
                update={"status": "HOLD_REQUESTED", "phase": f"hold_requested:{sig.signal_id}"}
            )
            record_run_state(self._state, project_id, updated)
            return load_latest_run_state(self._state, project_id, run_id)
        return state

    async def _run_horizon_upgrade(
        self,
        project_id: str,
        run_id: str,
        prior_res: CegarRunResult,
        *,
        horizon_req: HorizonActionReq | None = None,
    ) -> RunStateResp:
        proj = self._state.get_project(project_id)
        harness = resolve_harness(
            proj.settings_snapshot, self._secrets, injected_harness=self._injected_harness
        )
        ont_rev_id, prior_wave_rev_id, cegar_req = prepare_resume_cegar_request(
            self._state,
            self._workspace_root,
            project_id=project_id,
            run_id=run_id,
            policy=prior_res.policy,
            harness=harness,
            prior_res=prior_res,
        )
        running_state = load_latest_run_state(self._state, project_id, run_id).model_copy(
            update={"status": "RUNNING", "phase": "horizon_review"}
        )
        acquire_project_job_lease(self._state, project_id, run_id)
        try:
            record_run_state(self._state, project_id, running_state)
            if horizon_req is not None:
                saved = save_horizon_assumption_revision(self._state, project_id, horizon_req)
                cegar_req = cegar_req.model_copy(
                    update={"horizon_assumptions": [*cegar_req.horizon_assumptions, saved]}
                )
            upgraded = await run_v8_horizon_review(cegar_req, prior_res)
            return persist_cegar_result(
                self._state,
                project_id=project_id,
                run_id=run_id,
                policy=prior_res.policy,
                ontology_rev_id=ont_rev_id,
                prior_wave_rev_id=prior_wave_rev_id,
                result=upgraded,
            )
        except Exception as exc:
            mark_run_failed_from_exception(self._state, project_id, running_state, exc)
            raise
        finally:
            release_project_job_lease(self._state, project_id, run_id)

    async def submit_vault_decisions(
        self,
        project_id: str,
        run_id: str,
        req: HoldAnswersReq,
        *,
        horizon_req: HorizonActionReq | None = None,
    ) -> RunStateResp:
        """Resume a HOLD_CHECKPOINTED Step 6 CEGAR run with Steward answers."""
        prior_state = _require_step6_run(self._state, project_id, run_id)
        if prior_state.status not in ("HOLD_CHECKPOINTED", "UNCERTIFIED_DRAFT"):
            raise OpenDVError(
                f"Run '{run_id}' cannot accept Hold decisions in status '{prior_state.status}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                run_id=run_id,
            )
        ckpt_store = ServiceRunCheckpointStore(self._state, project_id)
        ckpt = ckpt_store.load_checkpoint(run_id)
        prior_res = load_cegar_result(self._state, project_id, run_id)
        proj = self._state.get_project(project_id)
        eff_policy: CegarPolicy = prior_state.policy or "balanced"
        harness = resolve_harness(
            proj.settings_snapshot, self._secrets, injected_harness=self._injected_harness
        )
        ont_rev_id, prior_wave_rev_id, cegar_req = prepare_resume_cegar_request(
            self._state,
            self._workspace_root,
            project_id=project_id,
            run_id=run_id,
            policy=eff_policy,
            harness=harness,
            prior_res=prior_res,
            ckpt_store=ckpt_store,
            checkpoint=ckpt,
            hold_answers=req,
        )
        running_state = prior_state.model_copy(update={"status": "RUNNING", "phase": "resuming"})
        acquire_project_job_lease(self._state, project_id, run_id)
        try:
            record_run_state(self._state, project_id, running_state)
            if horizon_req is not None:
                saved = save_horizon_assumption_revision(self._state, project_id, horizon_req)
                cegar_req = cegar_req.model_copy(
                    update={"horizon_assumptions": [*cegar_req.horizon_assumptions, saved]}
                )
            resumed = await run_step6_cegar(cegar_req)
            return persist_cegar_result(
                self._state,
                project_id=project_id,
                run_id=run_id,
                policy=eff_policy,
                ontology_rev_id=ont_rev_id,
                prior_wave_rev_id=prior_wave_rev_id,
                result=resumed,
            )
        except Exception as exc:
            mark_run_failed_from_exception(self._state, project_id, running_state, exc)
            raise
        finally:
            release_project_job_lease(self._state, project_id, run_id)

    async def submit_horizon_steering(
        self,
        project_id: str,
        run_id: str,
        req: HorizonActionReq,
    ) -> RunStateResp:
        """Apply Steward Accept or Steer response to a Horizon Critic review or Hold."""
        prior_state = _require_step6_run(self._state, project_id, run_id)
        prior_res = load_cegar_result(self._state, project_id, run_id)
        if prior_state.status == "COMPLETED":
            if prior_res is None:
                raise OpenDVError(
                    f"Run '{run_id}' has no CEGAR result for Horizon upgrade.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                    run_id=run_id,
                )
            if prior_res.status not in ("CERTIFIED_FAST", "CERTIFIED"):
                raise OpenDVError(
                    f"Run '{run_id}' cannot run Horizon upgrade from '{prior_res.status}'.",
                    error_code=ErrorCode.VERIFICATION_GATE_FAILED,
                    run_id=run_id,
                )
            return await self._run_horizon_upgrade(
                project_id, run_id, prior_res, horizon_req=req
            )
        if prior_state.status not in ("HOLD_CHECKPOINTED", "UNCERTIFIED_DRAFT"):
            raise OpenDVError(
                f"Run '{run_id}' cannot accept Hold decisions in status '{prior_state.status}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                run_id=run_id,
            )
        answers = {"hq-v8-low-score": req.action, **req.answers}
        return await self.submit_vault_decisions(
            project_id,
            run_id,
            HoldAnswersReq(answers=answers),
            horizon_req=req,
        )

    def get_vault_ir(self, project_id: str) -> CertifiedVaultIR:
        """Return the active CertifiedVaultIR for project_id."""
        return load_active_certified_ir(self._state, project_id)

    def verify_vault(self, project_id: str) -> VaultVerifyResp:
        """Execute deterministic V1-V6 + V6b verification with DuckDbProverAdapter."""
        ir = self.get_vault_ir(project_id)
        _ont, _profs, _cls, _locks, _prior, _assump, ctx = load_verifier_inputs(
            self._state, self._workspace_root, project_id
        )
        report: VerifierReport = run_v1_to_v6_gates(
            ir, ctx, prover=DuckDbProverAdapter(), include_v6b=True
        )
        return VaultVerifyResp(passed=report.passed, diagnostics=report.all_diagnostics)

    def _record_single_waiver(
        self,
        project_id: str,
        active_wave_id: str | None,
        w: WaiverSubmitReq,
    ) -> AntiPatternJustification:
        digest = hashlib.sha256(
            f"{project_id}:{w.rule_id}:{w.entity_name}:{w.rationale}".encode()
        ).hexdigest()[:8]
        waiver_id = f"WAIV-{digest}"
        just = AntiPatternJustification(
            waiver_id=waiver_id,
            rule_id=w.rule_id,
            entity_name=w.entity_name,
            rationale=w.rationale,
            actor="human",
            reviewed_by_v7=True,
        )
        self._state.save_revision(
            project_id,
            revision_id=f"vwaiv-{waiver_id}-{uuid.uuid4().hex[:6]}",
            rev_kind="vault_waiver",
            rev_key=f"{waiver_id}-{uuid.uuid4().hex[:6]}",
            parent_rev_id=active_wave_id,
            payload=just,
            metadata=RevisionMetadata(
                actor="human",
                reason=w.rationale,
                base_revision_id=active_wave_id,
                is_waiver_signed=True,
                waiver_rationale=w.rationale,
            ),
        )
        return just

    def submit_vault_waiver(
        self,
        project_id: str,
        req: WaiverSubmitReq,
    ) -> AntiPatternJustification:
        """Server-stamp a single WAIV-<sha8> waiver onto active CertifiedVaultIR."""
        ir = self.get_vault_ir(project_id)
        proj = self._state.get_project(project_id)
        just = self._record_single_waiver(project_id, proj.active_wave_id, req)
        existing = [
            j
            for j in ir.justifications
            if not (j.rule_id == req.rule_id and j.entity_name == req.entity_name)
        ]
        existing.append(just)
        updated_ir = ir.model_copy(update={"justifications": existing})
        next_rev = f"wave-{ir.wave_number}-waived-{uuid.uuid4().hex[:6]}"
        self._state.save_revision(
            project_id,
            revision_id=next_rev,
            rev_kind="wave_ir",
            rev_key=next_rev,
            parent_rev_id=proj.active_wave_id,
            payload=updated_ir,
            metadata=RevisionMetadata(
                actor="human",
                reason="Applied Steward V6b waiver",
                base_revision_id=proj.active_wave_id,
                is_waiver_signed=True,
            ),
        )
        self._state.update_project_pointers(project_id, active_wave_id=next_rev)
        return just

    def apply_vault_waivers(
        self,
        project_id: str,
        waivers: Sequence[WaiverSubmitReq],
    ) -> VaultVerifyResp:
        """Server-stamp WAIV-<sha8> waivers onto active CertifiedVaultIR and re-verify."""
        ir = self.get_vault_ir(project_id)
        proj = self._state.get_project(project_id)
        existing = list(ir.justifications)
        for w in waivers:
            just = self._record_single_waiver(project_id, proj.active_wave_id, w)
            existing = [
                j
                for j in existing
                if not (j.rule_id == w.rule_id and j.entity_name == w.entity_name)
            ]
            existing.append(just)

        updated_ir = ir.model_copy(update={"justifications": existing})
        next_rev = f"wave-{ir.wave_number}-waived-{uuid.uuid4().hex[:6]}"
        self._state.save_revision(
            project_id,
            revision_id=next_rev,
            rev_kind="wave_ir",
            rev_key=next_rev,
            parent_rev_id=proj.active_wave_id,
            payload=updated_ir,
            metadata=RevisionMetadata(
                actor="human",
                reason="Applied Steward V6b waiver(s)",
                base_revision_id=proj.active_wave_id,
                is_waiver_signed=True,
            ),
        )
        self._state.update_project_pointers(project_id, active_wave_id=next_rev)
        return self.verify_vault(project_id)

    def get_guided_view(self, project_id: str, run_id: str | None = None) -> GuidedRunView:
        """Project the latest Step 4/6 state into plain-English GuidedRunView."""
        return build_vault_guided_view(self._state, self._translator, project_id, run_id=run_id)
