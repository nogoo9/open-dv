"""Step 6 CEGAR input hydration, request assembly, resume preparation, and Guided UX."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from opendv.agents import AgentHarnessPort
from opendv.agents.pack_loader import load_modeling_guide
from opendv.modeling import (
    DVModelingRequest,
    DVModelingResult,
    ModelingCheckpointStore,
    ModelingControlReader,
    ModelingEventSink,
    ModelingHarnessPort,
    ModelingResumeInput,
    WaveEvolutionInput,
)
from opendv.models.diagnostics import (
    AntiPatternJustification,
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
    ProjectState,
    TrustedWorkspaceSpec,
)
from opendv.models.sources import DocumentMap, SourceProfile
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_discovery import HoldAnswersReq, RunStateResp
from opendv.models.wire_dtos_vault import VaultBuildReq
from opendv.service.secrets import KeyringSecretStore
from opendv.service.state_store import (
    ProjectStateManager,
    ServiceControlSignalReader,
    ServiceRunCheckpointStore,
    ServiceRunEventSink,
)
from opendv.service.workflows._run_adapters import (
    ServiceModelingPackLoader,
    coalesce_prior_state_budget,
    coalesce_snapshot_budget,
    list_revision_models,
    load_latest_evolution_lock,
    wrap_modeling_harness,
)
from opendv.service.workflows._source_docs import load_doc_pages_for_record
from opendv.service.workflows._vault_proving import load_cached_proving_samples
from opendv.service.workflows._vault_revisions import (
    build_vault_status,
    cegar_result_path,
    load_cegar_result,
    map_cegar_status_to_run_status,
    persist_cegar_result,
    resolve_evolution_lock_for_wave,
    resolve_pre_steer_fallback,
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
    *,
    secret_store: KeyringSecretStore | None = None,
    real_row_proving_override: bool | None = None,
    cdc_volatility_by_table: Mapping[str, Mapping[str, float]] | None = None,
    fk_turnover_by_table: Mapping[str, Mapping[str, float]] | None = None,
) -> tuple[
    GraphAOntology,
    list[SourceProfile],
    list[tuple[str, Sequence[str]]],
    EvolutionLockSpec | None,
    CertifiedVaultIR | None,
    list[HorizonAssumptionSpec],
    VerifierContext,
]:
    """Hydrate ontology, profiles, clusters, proving cache, locks, waivers, & VerifierContext."""
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
    vault_waivers = [
        item
        for _, item in list_revision_models(
            state_manager, project_id, "vault_waiver", AntiPatternJustification
        )
    ]
    ocr_receipts = [
        OcrPageReceipt.model_validate(r["payload"])
        for r in state_manager.list_events_or_receipts(project_id, record_kind="ocr_receipt")
    ]
    eff_real_row = (
        proj.settings_snapshot.real_row_proving
        if real_row_proving_override is None
        else real_row_proving_override
    )
    source_rows, sample_flags = load_cached_proving_samples(
        state_manager,
        secret_store,
        project_id,
        profiles,
        proving_sample_rows=proj.settings_snapshot.proving_sample_rows,
        real_row_proving=eff_real_row,
    )
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
        source_rows_by_table=source_rows or None,
        table_sample_flags=sample_flags or None,
        real_row_proving=eff_real_row,
        modeling_preferences=proj.settings_snapshot.modeling_preferences,
        persisted_waivers=vault_waivers,
        cdc_volatility_by_table={
            k: dict(v) for k, v in (cdc_volatility_by_table or {}).items()
        },
        fk_turnover_by_table={k: dict(v) for k, v in (fk_turnover_by_table or {}).items()},
    )
    return ontology, profiles, clusters, locks, prior_ir, assumptions, ctx


def resolve_step6_v7_tier(policy: CegarPolicy, snap: ProjectSettingsSnapshot) -> ModelTier:
    """Enforce FRONTIER_CRITIC under strict CEGAR policy regardless of snapshot setting."""
    return "FRONTIER_CRITIC" if policy == "strict" else snap.v7_critic_tier


def _assemble_modeling_request(
    state_manager: ProjectStateManager,
    workspace_root: Path,
    *,
    proj: ProjectState,
    run_id: str,
    policy: CegarPolicy,
    harness: AgentHarnessPort | ModelingHarnessPort,
    ontology: GraphAOntology,
    profiles: Sequence[SourceProfile],
    clusters: Sequence[tuple[str, Sequence[str]]],
    locks: EvolutionLockSpec | None,
    prior_ir: CertifiedVaultIR | None,
    assumptions: Sequence[HorizonAssumptionSpec],
    ctx: VerifierContext,
    guide_text: str,
    trusted_workspaces: Sequence[TrustedWorkspaceSpec],
    model_overrides: Mapping[str, str] | None,
    prior_wave_rev_id: str | None = None,
    wave_override: int | None = None,
    business_context_hints: str | None = None,
    ckpt_store: ServiceRunCheckpointStore | None = None,
    resume: ModelingResumeInput | None = None,
    explicit_budget: HarnessBudget | None = None,
    v7_critic_tier: ModelTier | None = None,
) -> DVModelingRequest:
    return DVModelingRequest(
        run_id=run_id,
        project_id=proj.project_id,
        ontology=ontology,
        profiles=profiles,
        clusters=clusters,
        policy=policy,
        budget=explicit_budget,
        harness=wrap_modeling_harness(harness),
        pack_loader=ServiceModelingPackLoader(
            workspace_root=workspace_root,
            trusted_workspaces=trusted_workspaces,
        ),
        prover=DuckDbProverAdapter(),
        verifier_ctx=ctx,
        event_sink=cast(ModelingEventSink, ServiceRunEventSink(state_manager, proj.project_id)),
        control_reader=cast(
            ModelingControlReader, ServiceControlSignalReader(state_manager, proj.project_id)
        ),
        checkpoint_store=cast(
            ModelingCheckpointStore,
            ckpt_store or ServiceRunCheckpointStore(state_manager, proj.project_id),
        ),
        modeling_preferences=proj.settings_snapshot.modeling_preferences,
        business_context_hints=business_context_hints,
        guide_markdown=guide_text,
        cdc_volatility_by_table=dict(ctx.cdc_volatility_by_table) or None,
        fk_turnover_by_table=dict(ctx.fk_turnover_by_table) or None,
        source_rows_by_table=ctx.source_rows_by_table,
        table_sample_flags=ctx.table_sample_flags,
        real_row_proving=ctx.real_row_proving,
        persisted_waivers=list(ctx.persisted_waivers),
        horizon_assumptions=list(assumptions),
        wave=WaveEvolutionInput(
            evolution_locks=locks,
            prior_certified_ir=prior_ir,
            prior_wave_id=prior_wave_rev_id,
            wave_number_override=wave_override,
        ),
        resume=resume or ModelingResumeInput(),
        v7_critic_tier=v7_critic_tier
        if v7_critic_tier is not None
        else resolve_step6_v7_tier(policy, proj.settings_snapshot),
        model_overrides=model_overrides,
    )


def build_initial_modeling_request(
    state_manager: ProjectStateManager,
    workspace_root: Path,
    *,
    proj: ProjectState,
    run_id: str,
    req: VaultBuildReq,
    harness: AgentHarnessPort | ModelingHarnessPort,
    secret_store: KeyringSecretStore | None = None,
    trusted_workspaces: Sequence[TrustedWorkspaceSpec] = (),
    model_overrides: Mapping[str, str] | None = None,
) -> DVModelingRequest:
    """Hydrate initial Step 6 DVModelingRequest from project state, proving cache, and req."""
    snap = proj.settings_snapshot
    effective_policy: CegarPolicy = req.policy or snap.cegar_policy
    eff_budget = coalesce_snapshot_budget(CEGAR_POLICY_SPECS[effective_policy].budget, snap)
    v7_tier = resolve_step6_v7_tier(effective_policy, snap)
    eff_overrides = (
        dict(model_overrides)
        if model_overrides is not None
        else (dict(snap.model_overrides) or None)
    )
    ontology, profiles, clusters, locks, prior_ir, assumptions, ctx = load_verifier_inputs(
        state_manager,
        workspace_root,
        proj.project_id,
        secret_store=secret_store,
        real_row_proving_override=req.real_row_proving,
        cdc_volatility_by_table=req.cdc_volatility_by_table,
        fk_turnover_by_table=req.fk_turnover_by_table,
    )
    guide_text, _ = load_modeling_guide(
        workspace_root=workspace_root,
        trusted_workspaces=trusted_workspaces,
    )
    eff_hints = (
        req.business_context_hints
        if req.business_context_hints is not None
        else snap.business_context_hints
    )
    return _assemble_modeling_request(
        state_manager,
        workspace_root,
        proj=proj,
        run_id=run_id,
        policy=effective_policy,
        harness=harness,
        ontology=ontology,
        profiles=profiles,
        clusters=clusters,
        locks=locks,
        prior_ir=prior_ir,
        assumptions=assumptions,
        ctx=ctx.model_copy(update={"policy": effective_policy}),
        guide_text=guide_text,
        trusted_workspaces=trusted_workspaces,
        model_overrides=eff_overrides,
        prior_wave_rev_id=proj.active_wave_id,
        business_context_hints=eff_hints,
        explicit_budget=eff_budget,
        v7_critic_tier=v7_tier,
    )


def prepare_resume_cegar_request(
    state_manager: ProjectStateManager,
    workspace_root: Path,
    *,
    project_id: str,
    run_id: str,
    policy: CegarPolicy,
    harness: AgentHarnessPort | ModelingHarnessPort,
    prior_res: DVModelingResult | None,
    ckpt_store: ServiceRunCheckpointStore | None = None,
    checkpoint: RunCheckpointRecord | None = None,
    hold_answers: HoldAnswersReq | None = None,
    secret_store: KeyringSecretStore | None = None,
    trusted_workspaces: Sequence[TrustedWorkspaceSpec] = (),
    model_overrides: Mapping[str, str] | None = None,
    revert_to_pre_steer: bool = False,
    prior_state: RunStateResp | None = None,
) -> tuple[str | None, str | None, DVModelingRequest]:
    """Hydrate DVModelingRequest for Hold resume, Revert, or in-place V8 Horizon upgrade."""
    proj = state_manager.get_project(project_id)
    ontology, profiles, clusters, locks, prior_ir, assumptions, ctx = load_verifier_inputs(
        state_manager,
        workspace_root,
        project_id,
        secret_store=secret_store,
    )
    guide_text, _ = load_modeling_guide(
        workspace_root=workspace_root,
        trusted_workspaces=trusted_workspaces,
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

    eff_overrides = (
        dict(model_overrides)
        if model_overrides is not None
        else (dict(snap.model_overrides) or None)
    )
    fb_draft, fb_cert = resolve_pre_steer_fallback(
        state_manager,
        project_id,
        run_id,
        prior_res,
        checkpoint,
        revert_requested=revert_to_pre_steer,
    )
    req = _assemble_modeling_request(
        state_manager,
        workspace_root,
        proj=proj,
        run_id=run_id,
        policy=policy,
        harness=harness,
        ontology=ontology,
        profiles=profiles,
        clusters=clusters,
        locks=locks,
        prior_ir=prior_ir,
        assumptions=assumptions,
        ctx=ctx.model_copy(
            update={
                "policy": policy,
                "prior_certified_ir": prior_ir,
                "evolution_locks": locks,
            }
        ),
        guide_text=guide_text,
        trusted_workspaces=trusted_workspaces,
        model_overrides=eff_overrides,
        prior_wave_rev_id=prior_wave_rev_id,
        wave_override=wave_override,
        business_context_hints=snap.business_context_hints,
        ckpt_store=ckpt_store,
        resume=ModelingResumeInput(
            checkpoint=checkpoint,
            hold_answers=hold_answers,
            revert_to_pre_steer=revert_to_pre_steer,
            resumed_draft_ir=prior_res.draft_ir if prior_res else None,
            resumed_certified_ir=prior_res.certified_ir if prior_res else None,
            pre_steer_fallback_draft_ir=fb_draft,
            pre_steer_fallback_certified_ir=fb_cert,
            last_sig_seq=sig_watermark,
        ),
        explicit_budget=budget,
        v7_critic_tier=v7_tier,
    )
    return proj.active_ontology_rev_id, prior_wave_rev_id, req


from opendv.service.workflows._vault_views import build_vault_guided_view  # noqa: E402

__all__ = [
    "build_initial_modeling_request",
    "build_vault_guided_view",
    "build_vault_status",
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
