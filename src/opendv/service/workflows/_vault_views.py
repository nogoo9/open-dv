"""Guided UX view projection helper for Step 6 Vault workflows."""

from __future__ import annotations

from typing import Literal

from opendv.models.diagnostics import (
    AntiPatternJustification,
    ClarificationQuestion,
    VerifierDiagnostic,
)
from opendv.models.wire_dtos_discovery import RunStateResp
from opendv.models.wire_dtos_vault import GuidedRunView
from opendv.service.guided_translator import GuidedUxTranslator
from opendv.service.state_store import ProjectStateManager, ServiceRunCheckpointStore
from opendv.service.workflows._run_adapters import (
    list_project_runs,
    list_revision_models,
    load_active_certified_ir,
    load_latest_run_state,
)
from opendv.service.workflows._vault_revisions import load_cegar_result


def build_vault_guided_view(
    state_manager: ProjectStateManager,
    translator: GuidedUxTranslator,
    project_id: str,
    run_id: str | None = None,
) -> GuidedRunView:
    """Project latest Step 4/6 state into plain-English GuidedRunView."""
    proj = state_manager.get_project(project_id)
    all_runs = list_project_runs(state_manager, project_id)
    if run_id is not None:
        run_state = load_latest_run_state(state_manager, project_id, run_id)
    elif all_runs:
        run_state = all_runs[0]
    else:
        run_state = RunStateResp(
            run_id="idle",
            project_id=project_id,
            step="step6",
            status="QUEUED",
            policy=proj.settings_snapshot.cegar_policy,
            phase="awaiting_build",
            attempt=0,
            budget_turns_used=0,
            budget_tokens_used=0,
        )

    step4_held = next(
        (r for r in all_runs if r.step == "step4" and r.status == "HOLD_CHECKPOINTED"),
        None,
    )
    cegar_res = (
        load_cegar_result(state_manager, project_id, run_state.run_id)
        if run_state.run_id != "idle"
        else None
    )
    ckpt = (
        ServiceRunCheckpointStore(state_manager, project_id).load_checkpoint(run_state.run_id)
        if run_state.run_id != "idle"
        else None
    )
    cert_ir = cegar_res.certified_ir if cegar_res is not None else None
    if cert_ir is None and proj.active_wave_id is not None:
        cert_ir = load_active_certified_ir(state_manager, project_id)
    diags: list[VerifierDiagnostic] = []
    if cegar_res is not None and cegar_res.verifier_report is not None:
        diags = list(cegar_res.verifier_report.all_diagnostics)
    cert_status: Literal["UNCERTIFIED", "CERTIFIED_FAST", "CERTIFIED"] | None = None
    if cegar_res is not None and cegar_res.status in ("CERTIFIED", "CERTIFIED_FAST"):
        cert_status = cegar_res.status
    elif cert_ir is not None and run_state.status == "COMPLETED":
        cert_status = "CERTIFIED_FAST" if run_state.policy == "fast" else "CERTIFIED"

    persisted_waiver_ids = [
        w.waiver_id
        for _, w in list_revision_models(
            state_manager, project_id, "vault_waiver", AntiPatternJustification
        )
    ]
    ir_waiver_ids = [j.waiver_id for j in cert_ir.justifications] if cert_ir is not None else []
    merged_waiver_ids = list(dict.fromkeys([*ir_waiver_ids, *persisted_waiver_ids]))

    return translator.translate_run_view(
        run_state,
        certified_ir=cert_ir,
        draft_ir=cegar_res.draft_ir if cegar_res is not None else None,
        diagnostics=diags,
        v7_verdict=cegar_res.v7_verdict if cegar_res is not None else None,
        v8_verdict=cegar_res.v8_verdict if cegar_res is not None else None,
        checkpoint=ckpt,
        step4_run_id=step4_held.run_id if step4_held is not None else None,
        step4_pending_questions=(
            [q for q in step4_held.pending_questions if isinstance(q, ClarificationQuestion)]
            if step4_held is not None
            else ()
        ),
        ontology_rev_id=proj.active_ontology_rev_id,
        policy=run_state.policy or proj.settings_snapshot.cegar_policy,
        certification_status=cert_status,
        wave_number=cert_ir.wave_number if cert_ir is not None else 1,
        attempt=run_state.attempt,
        energy_e=cegar_res.final_energy_e if cegar_res is not None else None,
        waiver_ids=merged_waiver_ids,
    )


__all__ = ["build_vault_guided_view"]
