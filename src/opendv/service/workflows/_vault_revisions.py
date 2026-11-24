"""Step 6 CEGAR revision persistence, status mapping, and evolution-lock resolution."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal

from opendv.agents import CegarRunResult, CegarStatus
from opendv.models.errors import OpenDVError
from opendv.models.policy import CEGAR_POLICY_SPECS, CegarPolicy
from opendv.models.project import (
    EvolutionLockSpec,
    HorizonAssumptionSpec,
    RevisionMetadata,
)
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_discovery import RunStateResp, RunStatus
from opendv.models.wire_dtos_vault import HorizonActionReq
from opendv.service.os_platform import atomic_write_text
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._run_adapters import (
    append_audit_log,
    list_revision_models,
    load_latest_run_state,
    record_run_state,
)
from opendv.verifier import build_evolution_lock_from_certified


def map_cegar_status_to_run_status(status: CegarStatus) -> RunStatus:
    """Map CEGAR status string onto unified RunStatus."""
    if status in ("CERTIFIED", "CERTIFIED_FAST"):
        return "COMPLETED"
    if status == "HOLD_CHECKPOINTED":
        return "HOLD_CHECKPOINTED"
    if status == "UNCERTIFIED_DRAFT":
        return "UNCERTIFIED_DRAFT"
    return "FAILED"


def cegar_result_path(
    state_manager: ProjectStateManager, project_id: str, run_id: str
) -> Path:
    return state_manager.runs_dir(project_id) / f"{run_id}.cegar_result.json"


def save_cegar_result(
    state_manager: ProjectStateManager,
    project_id: str,
    run_id: str,
    result: CegarRunResult,
) -> None:
    path = cegar_result_path(state_manager, project_id, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, result.model_dump_json())


def load_cegar_result(
    state_manager: ProjectStateManager,
    project_id: str,
    run_id: str,
) -> CegarRunResult | None:
    path = cegar_result_path(state_manager, project_id, run_id)
    if not path.is_file():
        return None
    return CegarRunResult.model_validate_json(path.read_text(encoding="utf-8"))


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
    result: CegarRunResult,
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

    pending = list(result.checkpoint.pending_questions) if result.checkpoint is not None else []
    phase_map: dict[CegarStatus, str] = {
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
