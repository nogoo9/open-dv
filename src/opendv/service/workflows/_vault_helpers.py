"""Step 6 CEGAR input hydration, resume preparation, and Guided UX projection helpers."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from opendv.agents import AgentHarnessPort, CegarRunRequest, CegarRunResult
from opendv.models.diagnostics import (
    AntiPatternJustification,
    ClarificationQuestion,
    VerifierDiagnostic,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import OcrPageReceipt, RunCheckpointRecord
from opendv.models.ontology import GraphAOntology
from opendv.models.policy import CEGAR_POLICY_SPECS, CegarPolicy, HarnessBudget, ModelTier
from opendv.models.project import (
    EvolutionLockSpec,
    HorizonAssumptionSpec,
    OntologyWaiverSpec,
    ProjectSettingsSnapshot,
)
from opendv.models.sources import DocumentMap, SourceProfile
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_discovery import HoldAnswersReq, RunStateResp
from opendv.models.wire_dtos_vault import GuidedRunView
from opendv.service.guided_translator import GuidedUxTranslator
from opendv.service.state_store import (
    ProjectStateManager,
    ServiceControlSignalReader,
    ServiceRunCheckpointStore,
    ServiceRunEventSink,
)
from opendv.service.workflows._run_adapters import (
    coalesce_prior_state_budget,
    coalesce_snapshot_budget,
    list_project_runs,
    list_revision_models,
    load_active_certified_ir,
    load_latest_evolution_lock,
    load_latest_run_state,
)
from opendv.service.workflows._source_docs import load_doc_pages_for_record
from opendv.service.workflows._vault_revisions import (
    cegar_result_path,
    load_cegar_result,
    map_cegar_status_to_run_status,
    persist_cegar_result,
    resolve_evolution_lock_for_wave,
    save_cegar_result,
    save_horizon_assumption_revision,
)
from opendv.sources import cluster_tables
from opendv.storage.prover.duckdb_prover import DuckDbProverAdapter
from opendv.verifier import VerifierContext


def load_verifier_inputs(
    state_manager: ProjectStateManager,
    workspace_root: Path,
    project_id: str,
) -> tuple[
    GraphAOntology,
    list[SourceProfile],
    list[tuple[str, Sequence[str]]],
    EvolutionLockSpec | None,
    CertifiedVaultIR | None,
    list[HorizonAssumptionSpec],
    VerifierContext,
]:
    """Hydrate ontology, profiles, clusters, locks, prior IR, and VerifierContext."""
    proj = state_manager.get_project(project_id)
    if proj.active_ontology_rev_id is None:
        raise OpenDVError(
            f"Project '{project_id}' requires committed Graph A before Vault synthesis.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    ont_rev = state_manager.get_revision(project_id, proj.active_ontology_rev_id)
    ontology = GraphAOntology.model_validate(ont_rev["payload"])

    profiles: list[SourceProfile] = []
    doc_maps: list[DocumentMap] = []
    parsed_doc_pages: dict[str, dict[int, str]] = {}
    all_tables = []
    meta_absent = False
    for src in state_manager.list_sources(project_id):
        rec = state_manager.get_source_record(project_id, src.source_id)
        src_meta_absent = str(rec["status"]) == "METADATA_ONLY_BINARY_ABSENT"
        if src_meta_absent:
            meta_absent = True
        prof = SourceProfile.model_validate(rec["profile"])
        if prof.tables:
            profiles.append(prof)
            all_tables.extend(prof.tables)
        if rec["document_map"] is not None:
            dmap = DocumentMap.model_validate(rec["document_map"])
            doc_maps.append(dmap)
            if not src_meta_absent:
                pages = load_doc_pages_for_record(
                    state_manager, workspace_root, project_id, src.source_id, rec, dmap
                )
                if pages is not None:
                    page_dict = {idx + 1: text for idx, text in enumerate(pages)}
                    parsed_doc_pages[src.source_id] = page_dict
                    parsed_doc_pages[dmap.doc_id] = page_dict

    clusters: list[tuple[str, Sequence[str]]] = []
    if all_tables:
        clustered = cluster_tables(all_tables)
        by_cluster: dict[str, list[str]] = {}
        for tid, cid in clustered.table_to_cluster.items():
            by_cluster.setdefault(cid, []).append(tid)
        clusters = sorted(by_cluster.items())

    prior_ir: CertifiedVaultIR | None = None
    if proj.active_wave_id is not None:
        wave_rev = state_manager.get_revision(project_id, proj.active_wave_id)
        prior_ir = CertifiedVaultIR.model_validate(wave_rev["payload"])
        locks = resolve_evolution_lock_for_wave(
            state_manager, project_id, proj.active_wave_id, prior_ir
        )
    else:
        locks = load_latest_evolution_lock(state_manager, project_id)

    assumptions = [
        item
        for _, item in list_revision_models(
            state_manager, project_id, "horizon_assumption", HorizonAssumptionSpec
        )
        if item.assumption_id != "ha-hq-v8-low-score"
    ]
    ont_waivers = [
        item
        for _, item in list_revision_models(
            state_manager, project_id, "ontology_waiver", OntologyWaiverSpec
        )
    ]
    ocr_receipts = [
        OcrPageReceipt.model_validate(r["payload"])
        for r in state_manager.list_events_or_receipts(project_id, record_kind="ocr_receipt")
    ]
    ctx = VerifierContext(
        profiles=profiles,
        ontology=ontology,
        evolution_locks=locks,
        prior_certified_ir=prior_ir,
        document_maps=doc_maps,
        parsed_doc_pages=parsed_doc_pages,
        ocr_receipts=ocr_receipts,
        ontology_waivers=ont_waivers,
        metadata_only_binary_absent=meta_absent,
    )
    return ontology, profiles, clusters, locks, prior_ir, assumptions, ctx


def resolve_step6_v7_tier(policy: CegarPolicy, snap: ProjectSettingsSnapshot) -> ModelTier:
    """Enforce FRONTIER_CRITIC under strict CEGAR policy regardless of snapshot setting."""
    return "FRONTIER_CRITIC" if policy == "strict" else snap.v7_critic_tier


def prepare_resume_cegar_request(
    state_manager: ProjectStateManager,
    workspace_root: Path,
    *,
    project_id: str,
    run_id: str,
    policy: CegarPolicy,
    harness: AgentHarnessPort,
    prior_res: CegarRunResult | None,
    ckpt_store: ServiceRunCheckpointStore | None = None,
    checkpoint: RunCheckpointRecord | None = None,
    hold_answers: HoldAnswersReq | None = None,
    prior_state: RunStateResp | None = None,
) -> tuple[str | None, str | None, CegarRunRequest]:
    """Hydrate CEGAR request for Hold resume or in-place V8 Horizon upgrade."""
    proj = state_manager.get_project(project_id)
    ontology, profiles, clusters, locks, prior_ir, assumptions, ctx = load_verifier_inputs(
        state_manager, workspace_root, project_id
    )
    prior_wave_rev_id = proj.active_wave_id
    wave_override: int | None = None
    if prior_res is not None:
        if prior_res.horizon_assumptions:
            merged_assumptions = {a.assumption_id: a for a in assumptions}
            for a in prior_res.horizon_assumptions:
                merged_assumptions[a.assumption_id] = a
            assumptions = list(merged_assumptions.values())
        existing_wave = state_manager.get_revision_by_key(
            project_id, "wave_ir", f"wave-{prior_res.wave_number}-{run_id}"
        )
        if existing_wave is not None:
            raw_parent = existing_wave["parent_rev_id"]
            prior_wave_rev_id = str(raw_parent) if raw_parent is not None else None
            wave_override = prior_res.wave_number
            if prior_wave_rev_id is None:
                prior_ir = None
                locks = None
            else:
                parent_rev = state_manager.get_revision(project_id, prior_wave_rev_id)
                prior_ir = CertifiedVaultIR.model_validate(parent_rev["payload"])
                locks = resolve_evolution_lock_for_wave(
                    state_manager, project_id, prior_wave_rev_id, prior_ir
                )
    sig_watermark = (
        len(state_manager.poll_control_signals(project_id, run_id))
        if checkpoint is None
        else 0
    )
    snap = proj.settings_snapshot
    v7_tier = resolve_step6_v7_tier(policy, snap)
    base_budget = CEGAR_POLICY_SPECS[policy].budget
    budget: HarnessBudget | None
    if checkpoint is not None:
        if "budget_max_turns" in checkpoint.resume_payload:
            budget = None
        else:
            run_rows = state_manager.list_events_or_receipts(
                project_id, record_kind="run_state", run_id=run_id
            )
            init_state = (
                RunStateResp.model_validate(run_rows[0]["payload"])
                if run_rows
                else prior_state
            )
            budget = coalesce_prior_state_budget(base_budget, init_state)
    elif prior_state is not None:
        budget = coalesce_prior_state_budget(base_budget, prior_state)
    else:
        budget = coalesce_snapshot_budget(base_budget, snap)

    req = CegarRunRequest(
        run_id=run_id,
        project_id=project_id,
        ontology=ontology,
        profiles=profiles,
        clusters=clusters,
        policy=policy,
        budget=budget,
        v7_critic_tier=v7_tier,
        model_overrides=dict(snap.model_overrides),
        evolution_locks=locks,
        prior_certified_ir=prior_ir,
        verifier_ctx=ctx.model_copy(
            update={
                "policy": policy,
                "prior_certified_ir": prior_ir,
                "evolution_locks": locks,
            }
        ),
        prover=DuckDbProverAdapter(),
        harness=harness,
        event_sink=ServiceRunEventSink(state_manager, project_id),
        control_reader=ServiceControlSignalReader(state_manager, project_id),
        checkpoint_store=ckpt_store or ServiceRunCheckpointStore(state_manager, project_id),
        horizon_assumptions=assumptions,
        hold_answers=hold_answers,
        checkpoint=checkpoint,
        resumed_draft_ir=prior_res.draft_ir if prior_res else None,
        resumed_certified_ir=prior_res.certified_ir if prior_res else None,
        wave_number_override=wave_override,
        last_sig_seq=sig_watermark,
    )
    return proj.active_ontology_rev_id, prior_wave_rev_id, req


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


__all__ = [
    "build_vault_guided_view",
    "cegar_result_path",
    "load_cegar_result",
    "load_verifier_inputs",
    "map_cegar_status_to_run_status",
    "persist_cegar_result",
    "prepare_resume_cegar_request",
    "resolve_evolution_lock_for_wave",
    "resolve_step6_v7_tier",
    "save_cegar_result",
    "save_horizon_assumption_revision",
]
