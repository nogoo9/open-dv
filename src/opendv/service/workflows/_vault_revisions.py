"""Step 6 CEGAR revision persistence, status mapping, and evolution-lock resolution."""

from __future__ import annotations

import contextlib
import os
import re
import uuid
from pathlib import Path
from typing import Literal

from opendv.modeling import DVModelingResult
from opendv.models.diagnostics import (
    AntiPatternJustification,
    ClarificationQuestion,
    HorizonQuestion,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import RunCheckpointRecord
from opendv.models.json_hasher import sha256_jcs
from opendv.models.modeling_preferences import ModelingStatus, NextActionEnum
from opendv.models.policy import CEGAR_POLICY_SPECS, CegarPolicy
from opendv.models.project import (
    EvolutionLockSpec,
    HorizonAssumptionSpec,
    RevisionMetadata,
)
from opendv.models.vault_ir import DraftVaultIR
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_discovery import RunStateResp, RunStatus
from opendv.models.wire_dtos_vault import HorizonActionReq, VaultStatusResp
from opendv.service.os_platform import atomic_write_text
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._run_adapters import (
    append_audit_log,
    list_project_runs,
    list_revision_models,
    list_run_attempts,
    load_active_certified_ir,
    load_latest_run_state,
    record_run_state,
    validate_project_id_or_error,
)
from opendv.verifier import build_evolution_lock_from_certified


def map_cegar_status_to_run_status(status: ModelingStatus) -> RunStatus:
    """Map ModelingStatus string onto unified RunStatus."""
    if status in ("CERTIFIED", "CERTIFIED_FAST"):
        return "COMPLETED"
    if status == "HOLD_CHECKPOINTED":
        return "HOLD_CHECKPOINTED"
    if status == "UNCERTIFIED_DRAFT":
        return "UNCERTIFIED_DRAFT"
    return "FAILED"


_SAFE_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def cegar_result_path(
    state_manager: ProjectStateManager, project_id: str, run_id: str
) -> Path:
    """Return contained .cegar_result.json path inside project's runs directory."""
    clean_pid = validate_project_id_or_error(project_id)
    if not _SAFE_RUN_ID_RE.fullmatch(run_id) or ".." in run_id:
        raise OpenDVError(
            f"Invalid run_id '{run_id}' for CEGAR result path.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    runs_base = os.path.realpath(os.fspath(state_manager.runs_dir(clean_pid)))
    target_real = os.path.realpath(os.path.join(runs_base, f"{run_id}.cegar_result.json"))
    if not target_real.startswith(runs_base + os.sep):
        raise OpenDVError(
            f"CEGAR result path '{target_real}' escapes runs directory '{runs_base}'.",
            error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        )
    return Path(target_real)


def save_cegar_result(
    state_manager: ProjectStateManager,
    project_id: str,
    run_id: str,
    result: DVModelingResult,
) -> None:
    path = cegar_result_path(state_manager, project_id, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, result.model_dump_json())


def load_cegar_result(
    state_manager: ProjectStateManager,
    project_id: str,
    run_id: str,
) -> DVModelingResult | None:
    runs_base = os.path.realpath(
        os.fspath(state_manager.runs_dir(validate_project_id_or_error(project_id)))
    )
    path = cegar_result_path(state_manager, project_id, run_id)
    path_real = os.path.realpath(os.fspath(path))
    if not path_real.startswith(runs_base + os.sep) or not os.path.isfile(path_real):
        return None
    with open(path_real, encoding="utf-8") as fh:
        return DVModelingResult.model_validate_json(fh.read())


def resolve_evolution_lock_for_wave(
    state_manager: ProjectStateManager,
    project_id: str,
    wave_rev_id: str | None,
    prior_ir: CertifiedVaultIR | None,
) -> EvolutionLockSpec | None:
    """Resolve EvolutionLockSpec for wave_rev_id via ancestor walk, wave_number, or IR."""
    if wave_rev_id is None and prior_ir is None:
        return None
    lock_revs = state_manager.list_revisions(project_id, rev_kind="evolution_lock")
    cur_id = wave_rev_id
    visited: set[str] = set()
    while cur_id is not None and cur_id not in visited:
        visited.add(cur_id)
        matched = next(
            (r for r in reversed(lock_revs) if r.get("parent_rev_id") == cur_id),
            None,
        )
        if matched is not None:
            return EvolutionLockSpec.model_validate(matched["payload"])
        try:
            rev = state_manager.get_revision(project_id, cur_id)
        except OpenDVError:
            break
        raw_parent = rev.get("parent_rev_id")
        cur_id = str(raw_parent) if raw_parent is not None else None
    if prior_ir is not None:
        by_wave = next(
            (
                r
                for r in reversed(lock_revs)
                if isinstance(r.get("payload"), dict)
                and r["payload"].get("wave_number") == prior_ir.wave_number
            ),
            None,
        )
        if by_wave is not None:
            return EvolutionLockSpec.model_validate(by_wave["payload"])
        return build_evolution_lock_from_certified(prior_ir)
    return None


def save_horizon_assumption_revision(
    state_manager: ProjectStateManager,
    project_id: str,
    req: HorizonActionReq,
) -> HorizonAssumptionSpec:
    """Persist Steward Accept/Steer HorizonAssumptionSpec revision and return it."""
    decision: Literal["ACCEPT", "STEER"] = "ACCEPT" if req.action == "accept" else "STEER"
    assumption = HorizonAssumptionSpec(
        assumption_id=f"ha-{uuid.uuid4().hex[:8]}",
        brk_code="BRK-01",
        title=f"Steward Horizon {decision}",
        notes=req.notes or f"Steward selected {req.action}",
        steward_decision=decision,
        selected_option_ids=[req.action],
        added_fields=list(req.added_fields),
    )
    state_manager.save_revision(
        project_id,
        revision_id=f"hor-{assumption.assumption_id}",
        rev_kind="horizon_assumption",
        rev_key=assumption.assumption_id,
        parent_rev_id=None,
        payload=assumption,
        metadata=RevisionMetadata(actor="human", reason=assumption.notes),
    )
    return assumption


def persist_cegar_result(
    state_manager: ProjectStateManager,
    *,
    project_id: str,
    run_id: str,
    policy: CegarPolicy,
    ontology_rev_id: str | None,
    prior_wave_rev_id: str | None,
    result: DVModelingResult,
) -> RunStateResp:
    """Persist CEGAR wave IR, evolution lock, horizon assumptions, and RunStateResp."""
    save_cegar_result(state_manager, project_id, run_id, result)
    run_status = map_cegar_status_to_run_status(result.status)
    budget = CEGAR_POLICY_SPECS[policy].budget

    if result.certified_ir is not None and result.status in ("CERTIFIED", "CERTIFIED_FAST"):
        wave_rev_id = f"wave-{result.wave_number}-{uuid.uuid4().hex[:8]}"
        base_wave_key = f"wave-{result.wave_number}-{run_id}"
        wave_key = (
            f"{base_wave_key}-v8-{uuid.uuid4().hex[:6]}"
            if state_manager.get_revision_by_key(project_id, "wave_ir", base_wave_key) is not None
            else base_wave_key
        )
        state_manager.save_revision(
            project_id,
            revision_id=wave_rev_id,
            rev_kind="wave_ir",
            rev_key=wave_key,
            parent_rev_id=prior_wave_rev_id,
            payload=result.certified_ir,
            metadata=RevisionMetadata(
                actor="agent",
                reason=f"Step 6 CEGAR {result.status} ({policy}) wave {result.wave_number}",
                base_revision_id=ontology_rev_id,
            ),
        )
        proj = state_manager.get_project(project_id)
        should_advance_active = True
        if proj.active_wave_id is not None:
            active_rev = state_manager.get_revision(project_id, proj.active_wave_id)
            active_ir = CertifiedVaultIR.model_validate(active_rev["payload"])
            if active_ir.wave_number > result.wave_number:
                should_advance_active = False
        if should_advance_active:
            state_manager.update_project_pointers(project_id, active_wave_id=wave_rev_id)

        if result.evolution_lock is not None:
            base_lock_key = f"lock-wave-{result.wave_number}-{run_id}"
            lock_key = (
                f"{base_lock_key}-v8-{uuid.uuid4().hex[:6]}"
                if state_manager.get_revision_by_key(project_id, "evolution_lock", base_lock_key)
                is not None
                else base_lock_key
            )
            state_manager.save_revision(
                project_id,
                revision_id=f"lock-{result.wave_number}-{uuid.uuid4().hex[:8]}",
                rev_kind="evolution_lock",
                rev_key=lock_key,
                parent_rev_id=wave_rev_id,
                payload=result.evolution_lock,
                metadata=RevisionMetadata(
                    actor="agent",
                    reason=f"Sealed wave {result.wave_number} evolution locks",
                    base_revision_id=wave_rev_id,
                ),
            )

    existing_assumptions = {
        a.assumption_id
        for _, a in list_revision_models(
            state_manager, project_id, "horizon_assumption", HorizonAssumptionSpec
        )
        if a.assumption_id != "ha-hq-v8-low-score"
    }
    for assumption in result.horizon_assumptions:
        rev_key: str | None = None
        if assumption.assumption_id == "ha-hq-v8-low-score":
            candidate_key = f"ha-hq-v8-low-score-{run_id}-{assumption.steward_decision.lower()}"
            if (
                state_manager.get_revision_by_key(project_id, "horizon_assumption", candidate_key)
                is None
            ):
                rev_key = candidate_key
        elif assumption.assumption_id not in existing_assumptions:
            rev_key = f"{assumption.assumption_id}-{uuid.uuid4().hex[:6]}"
        if rev_key is not None:
            state_manager.save_revision(
                project_id,
                revision_id=f"hor-{rev_key}-{uuid.uuid4().hex[:6]}",
                rev_kind="horizon_assumption",
                rev_key=rev_key,
                parent_rev_id=prior_wave_rev_id,
                payload=assumption,
                metadata=RevisionMetadata(
                    actor="human",
                    reason=f"Horizon {assumption.brk_code}: {assumption.steward_decision}",
                ),
            )

    for prop in result.ontology_proposals:
        if (
            state_manager.get_revision_by_key(
                project_id, "ontology_proposal", prop.proposal_id
            )
            is None
        ):
            state_manager.save_revision(
                project_id,
                revision_id=f"prop-{prop.proposal_id}-{uuid.uuid4().hex[:6]}",
                rev_kind="ontology_proposal",
                rev_key=prop.proposal_id,
                parent_rev_id=ontology_rev_id,
                payload=prop,
                metadata=RevisionMetadata(
                    actor="agent",
                    reason=f"Step 6 CEGAR proposal {prop.proposal_id} (wave {result.wave_number})",
                    base_revision_id=ontology_rev_id,
                ),
            )

    pending = (
        list(result.checkpoint.pending_questions)
        if result.checkpoint is not None
        else list(result.pending_questions)
    )
    phase_map: dict[ModelingStatus, str] = {
        "CERTIFIED": "wave_certified",
        "CERTIFIED_FAST": "wave_certified_fast",
        "HOLD_CHECKPOINTED": "steward_hold",
        "UNCERTIFIED_DRAFT": "uncertified_draft",
        "FAILED": "failed",
    }
    state = RunStateResp(
        run_id=run_id,
        project_id=project_id,
        step="step6",
        status=run_status,
        policy=policy,
        phase=phase_map[result.status],
        attempt=result.attempts_used,
        budget_turns_used=result.turns_used,
        budget_tokens_used=result.tokens_used,
        effective_max_turns=budget.max_turns,
        effective_max_tokens=budget.max_tokens,
        effective_timeout_seconds=budget.timeout_seconds,
        pending_questions=pending,
        last_seq=result.last_seq,
    )
    record_run_state(state_manager, project_id, state)
    append_audit_log(
        state_manager,
        project_id,
        event_type="STEP6_CEGAR_OUTCOME",
        actor="agent",
        step="step6",
        run_id=run_id,
        summary=f"Step 6 CEGAR ended with {result.status} (wave={result.wave_number}).",
        details={
            "status": result.status,
            "wave_number": result.wave_number,
            "attempts_used": result.attempts_used,
            "energy_e": result.final_energy_e,
        },
    )
    return load_latest_run_state(state_manager, project_id, run_id)


def resolve_pre_steer_fallback(
    state_manager: ProjectStateManager,
    project_id: str,
    run_id: str,
    prior_res: DVModelingResult | None,
    checkpoint: RunCheckpointRecord | None,
    *,
    revert_requested: bool,
) -> tuple[DraftVaultIR | None, CertifiedVaultIR | None]:
    """Resolve pre-steer DraftVaultIR and CertifiedVaultIR from attempts or wave history."""
    fb_ref = checkpoint.pre_steer_fallback_ref if checkpoint is not None else None
    if fb_ref is None and prior_res is not None:
        raw_ref = prior_res.telemetry.get("pre_steer_fallback_ref")
        fb_ref = raw_ref if isinstance(raw_ref, str) else None
    target_sha = (
        fb_ref.removeprefix("sha256:")
        if isinstance(fb_ref, str) and fb_ref.startswith("sha256:")
        else None
    )

    fb_draft: DraftVaultIR | None = None
    fb_cert: CertifiedVaultIR | None = None
    if target_sha:
        attempts = list(prior_res.attempts) if prior_res is not None else []
        with contextlib.suppress(OpenDVError):
            attempts.extend(list_run_attempts(state_manager, project_id, run_id))
        for snap in attempts:
            if snap.ir_sha256 == target_sha:
                fb_draft = snap.draft_ir
                break
        if (
            prior_res is not None
            and prior_res.certified_ir is not None
            and sha256_jcs(prior_res.certified_ir.model_dump(mode="json")) == target_sha
        ):
            fb_cert = prior_res.certified_ir
        if fb_cert is None:
            for rev in reversed(state_manager.list_revisions(project_id, rev_kind="wave_ir")):
                payload = rev.get("payload")
                if isinstance(payload, dict) and sha256_jcs(payload) == target_sha:
                    fb_cert = CertifiedVaultIR.model_validate(payload)
                    break

    if revert_requested and fb_draft is None and fb_cert is None and prior_res is not None:
        fb_draft = prior_res.draft_ir
        fb_cert = prior_res.certified_ir
    return fb_draft, fb_cert


def build_vault_status(
    state_manager: ProjectStateManager,
    project_id: str,
    *,
    run_id: str | None = None,
    holds_only: bool = False,
) -> VaultStatusResp:
    """Summarize project vault certification, open holds, and persisted waivers."""
    clean_pid = validate_project_id_or_error(project_id)
    proj = state_manager.get_project(clean_pid)

    waived: list[AntiPatternJustification] = []
    if not holds_only:
        by_id: dict[str, AntiPatternJustification] = {}
        for _, item in list_revision_models(
            state_manager, clean_pid, "vault_waiver", AntiPatternJustification
        ):
            by_id[item.waiver_id] = item
        if proj.active_wave_id is not None:
            active_ir = load_active_certified_ir(state_manager, clean_pid)
            for just in active_ir.justifications:
                if just.waiver_id not in by_id and (just.reviewed_by_v7 or just.actor == "human"):
                    by_id[just.waiver_id] = just
        waived = list(by_id.values())

    run_state: RunStateResp | None = None
    if run_id is not None:
        loaded = load_latest_run_state(state_manager, clean_pid, run_id)
        if loaded.step != "step6":
            raise OpenDVError(
                f"Step 6 run '{run_id}' not found in project '{clean_pid}'.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        run_state = loaded
    else:
        step6_runs = list_project_runs(state_manager, clean_pid, step="step6")
        if step6_runs:
            run_state = step6_runs[0]

    if run_state is None:
        return VaultStatusResp(
            project_id=clean_pid,
            active_wave_id=proj.active_wave_id,
            run_id=None,
            status=None,
            next_action=None,
            open_holds=[],
            waived_rules=waived,
        )

    cegar_res = load_cegar_result(state_manager, clean_pid, run_state.run_id)
    status_val = cegar_res.status if cegar_res is not None else run_state.status
    next_act: NextActionEnum | None = cegar_res.next_action if cegar_res is not None else None
    if next_act is None:
        fallback_map: dict[str, NextActionEnum] = {
            "COMPLETED": "READY_FOR_DOWNSTREAM",
            "HOLD_CHECKPOINTED": "ASK_HUMAN_HOLD",
            "UNCERTIFIED_DRAFT": "REPAIR_DRAFT_IR",
            "FAILED": "FAILED",
        }
        next_act = fallback_map.get(run_state.status)

    open_holds: list[ClarificationQuestion | HorizonQuestion] = []
    if run_state.status == "HOLD_CHECKPOINTED":
        if cegar_res is not None and cegar_res.pending_questions:
            open_holds = list(cegar_res.pending_questions)
        else:
            open_holds = list(run_state.pending_questions)

    return VaultStatusResp(
        project_id=clean_pid,
        active_wave_id=proj.active_wave_id,
        run_id=run_state.run_id,
        status=status_val,
        next_action=next_act,
        open_holds=open_holds,
        waived_rules=waived,
    )
