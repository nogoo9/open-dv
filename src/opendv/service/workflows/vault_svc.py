"""Step 6 CEGAR synthesis, Horizon review, Steward holds/waivers, and Guided UX facade."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from pathlib import Path

from opendv.agents import AgentHarnessPort
from opendv.modeling import DVModelingResult, model_vault, run_v8_horizon_review
from opendv.models.diagnostics import AntiPatternJustification
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.policy import CEGAR_POLICY_SPECS, CegarPolicy
from opendv.models.project import RevisionMetadata, WorkspaceSettings
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_discovery import HoldAnswersReq, RunControlReq, RunStateResp
from opendv.models.wire_dtos_vault import (
    AttemptSnapshotSpec,
    GuidedRunView,
    HorizonActionReq,
    VaultBuildReq,
    VaultStatusResp,
    VaultVerifyResp,
    WaiverSubmitReq,
)
from opendv.service.guided_translator import GuidedUxTranslator
from opendv.service.readiness import HarnessReadinessService
from opendv.service.secrets import KeyringSecretStore
from opendv.service.settings_store import SettingsStore
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._run_adapters import (
    ServiceRunCheckpointStore,
    acquire_project_job_lease,
    coalesce_snapshot_budget,
    compute_job_lease_ttl,
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
    build_initial_modeling_request,
    build_vault_guided_view,
    build_vault_status,
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


def _carry_prior_proposals(
    prior_res: DVModelingResult | None,
    current: DVModelingResult,
) -> DVModelingResult:
    """Carry unseen prior ontology proposals across HOLD/Horizon resumes."""
    if prior_res is None or not prior_res.ontology_proposals:
        return current
    seen_ids = {p.proposal_id for p in current.ontology_proposals}
    carried = [p for p in prior_res.ontology_proposals if p.proposal_id not in seen_ids]
    if not carried:
        return current
    return current.model_copy(
        update={"ontology_proposals": [*carried, *current.ontology_proposals]}
    )


class VaultService:
    """Step 6 Vault CEGAR synthesis, Horizon review, waivers, verification, and Guided UX."""

    def __init__(
        self,
        *,
        state_manager: ProjectStateManager,
        secret_store: KeyringSecretStore,
        workspace_root: Path,
        injected_harness: AgentHarnessPort | None = None,
        settings_store: SettingsStore | None = None,
        readiness_service: HarnessReadinessService | None = None,
    ) -> None:
        self._state = state_manager
        self._secrets = secret_store
        self._workspace_root = workspace_root
        self._injected_harness = injected_harness
        self._settings = settings_store
        self._readiness_service = readiness_service
        self._translator = GuidedUxTranslator()

    def _load_ws_settings(self) -> WorkspaceSettings:
        return self._settings.load_settings() if self._settings is not None else WorkspaceSettings()

    async def build_vault(
        self,
        project_id: str,
        req: VaultBuildReq,
        *,
        run_id: str | None = None,
    ) -> RunStateResp:
        """Start and execute a Step 6 CEGAR synthesis run wired with DuckDbProverAdapter."""
        proj = self._state.get_project(project_id)
        ws = self._load_ws_settings()
        snap = proj.settings_snapshot
        effective_policy: CegarPolicy = req.policy or snap.cegar_policy
        raw_harness = resolve_harness(
            snap,
            self._secrets,
            injected_harness=self._injected_harness,
            readiness_service=self._readiness_service,
        )
        eff_run_id = run_id or f"run6-{uuid.uuid4().hex[:10]}"
        budget = coalesce_snapshot_budget(CEGAR_POLICY_SPECS[effective_policy].budget, snap)
        modeling_req = build_initial_modeling_request(
            self._state,
            self._workspace_root,
            proj=proj,
            run_id=eff_run_id,
            req=req,
            harness=raw_harness,
            secret_store=self._secrets,
            trusted_workspaces=ws.trusted_workspaces,
            model_overrides=dict(snap.model_overrides) or None,
        )
        acquire_project_job_lease(
            self._state,
            project_id,
            eff_run_id,
            ttl_seconds=compute_job_lease_ttl(budget.max_turns, budget.timeout_seconds),
        )

        running = RunStateResp(
            run_id=eff_run_id,
            project_id=project_id,
            step="step6",
            status="RUNNING",
            policy=effective_policy,
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
            result = await model_vault(modeling_req)
            return persist_cegar_result(
                self._state,
                project_id=project_id,
                run_id=eff_run_id,
                policy=effective_policy,
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

    def get_vault_status(
        self,
        project_id: str,
        *,
        run_id: str | None = None,
        holds_only: bool = False,
    ) -> VaultStatusResp:
        """Return project vault status, open holds, and persisted waivers."""
        return build_vault_status(
            self._state,
            project_id,
            run_id=run_id,
            holds_only=holds_only,
        )

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
        prior_res: DVModelingResult,
        *,
        horizon_req: HorizonActionReq | None = None,
    ) -> RunStateResp:
        proj = self._state.get_project(project_id)
        ws = self._load_ws_settings()
        prior_state = load_latest_run_state(self._state, project_id, run_id)
        harness = resolve_harness(
            proj.settings_snapshot,
            self._secrets,
            injected_harness=self._injected_harness,
            readiness_service=self._readiness_service,
        )
        ont_rev_id, prior_wave_rev_id, modeling_req = prepare_resume_cegar_request(
            self._state,
            self._workspace_root,
            project_id=project_id,
            run_id=run_id,
            policy=prior_res.policy,
            harness=harness,
            prior_res=prior_res,
            secret_store=self._secrets,
            trusted_workspaces=ws.trusted_workspaces,
            model_overrides=dict(proj.settings_snapshot.model_overrides) or None,
            prior_state=prior_state,
        )
        running_state = prior_state.model_copy(
            update={"status": "RUNNING", "phase": "horizon_review"}
        )
        acquire_project_job_lease(
            self._state,
            project_id,
            run_id,
            ttl_seconds=compute_job_lease_ttl(
                prior_state.effective_max_turns,
                prior_state.effective_timeout_seconds,
            ),
        )
        try:
            record_run_state(self._state, project_id, running_state)
            if horizon_req is not None:
                saved = save_horizon_assumption_revision(self._state, project_id, horizon_req)
                modeling_req = modeling_req.model_copy(
                    update={"horizon_assumptions": [*modeling_req.horizon_assumptions, saved]}
                )
            upgraded = _carry_prior_proposals(
                prior_res, await run_v8_horizon_review(modeling_req, prior_res)
            )
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
        revert: bool = False,
    ) -> RunStateResp:
        """Resume a HOLD_CHECKPOINTED Step 6 CEGAR run with Steward answers or revert."""
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
        ws = self._load_ws_settings()
        eff_policy: CegarPolicy = prior_state.policy or proj.settings_snapshot.cegar_policy
        harness = resolve_harness(
            proj.settings_snapshot,
            self._secrets,
            injected_harness=self._injected_harness,
            readiness_service=self._readiness_service,
        )
        ont_rev_id, prior_wave_rev_id, modeling_req = prepare_resume_cegar_request(
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
            secret_store=self._secrets,
            trusted_workspaces=ws.trusted_workspaces,
            model_overrides=dict(proj.settings_snapshot.model_overrides) or None,
            revert_to_pre_steer=revert,
            prior_state=prior_state,
        )
        running_state = prior_state.model_copy(update={"status": "RUNNING", "phase": "resuming"})
        acquire_project_job_lease(
            self._state,
            project_id,
            run_id,
            ttl_seconds=compute_job_lease_ttl(
                prior_state.effective_max_turns,
                prior_state.effective_timeout_seconds,
            ),
        )
        try:
            record_run_state(self._state, project_id, running_state)
            if horizon_req is not None and not revert:
                saved = save_horizon_assumption_revision(self._state, project_id, horizon_req)
                modeling_req = modeling_req.model_copy(
                    update={"horizon_assumptions": [*modeling_req.horizon_assumptions, saved]}
                )
            resumed = _carry_prior_proposals(prior_res, await model_vault(modeling_req))
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
        """Apply Steward Accept, Steer, or Revert response to a Horizon review or Hold."""
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
            if req.action == "revert":
                raise OpenDVError(
                    f"Run '{run_id}' cannot revert from COMPLETED without an active Hold.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
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
        if req.action == "revert":
            return await self.submit_vault_decisions(
                project_id,
                run_id,
                HoldAnswersReq(answers={"__action__": "revert", **req.answers}),
                revert=True,
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
            self._state,
            self._workspace_root,
            project_id,
            secret_store=self._secrets,
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
        """Server-stamp a single WAIV-<sha8> waiver (pre-certification or active wave IR)."""
        proj = self._state.get_project(project_id)
        just = self._record_single_waiver(project_id, proj.active_wave_id, req)
        if proj.active_wave_id is None:
            return just
        ir = self.get_vault_ir(project_id)
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
