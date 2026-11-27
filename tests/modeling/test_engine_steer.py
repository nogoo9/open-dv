"""Phase 4 tests for Mode-2 CEGAR engine, S6-06 STEER/REVERT, clarify_mode, and accept-best."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from opendv.modeling import (
    DVModelingRequest,
    ModelingPreferences,
    ModelingResumeInput,
    ModelingTurnResult,
    model_vault,
)
from opendv.modeling.horizon import (
    deduplicate_horizon_questions,
    format_horizon_key,
    horizon_question_dedup_key,
)
from opendv.modeling.models import compute_preferences_sha256
from opendv.models.diagnostics import (
    HorizonOptionSpec,
    HorizonQuestion,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import ControlSignalRecord, RunCheckpointRecord
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ConceptAttribute,
    ConceptTableBinding,
    GraphAOntology,
)
from opendv.models.ontology_deltas import AddConcept
from opendv.models.policy import HarnessBudget
from opendv.models.project import HarnessProvider
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
from opendv.models.vault_ir import (
    ColumnTriageEntry,
    DraftField,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
    LinkParticipantSpec,
)
from opendv.models.vault_ir import BusinessKeyPart
from opendv.models.wire_dtos_discovery import HoldAnswersReq
from opendv.models.wire_dtos_vault import (
    BucketAOperatorCall,
    HorizonCriticVerdict,
    SemanticCriticVerdict,
)
from opendv.sources.profiler import profile_source
from opendv.storage import DuckDbProverAdapter
from opendv.verifier.ports import VerifierContext
from tests.agents.test_cegar_v7_v8_horizon import (
    PUBLIC_AM,
    _build_12trap_draft,
    _build_12trap_ontology,
    _triage_all,
)
from tests.helpers import (
    DeterministicMockHarness,
    InMemoryControlSignalReader,
    InMemoryRunCheckpointStore,
    InMemoryRunEventSink,
    MockFaultSpec,
    MockTurnScript,
)


class _ModelingMockHarness:
    """Adapt DeterministicMockHarness to Layer-2c ModelingHarnessPort."""

    def __init__(self, inner: DeterministicMockHarness) -> None:
        self._inner = inner

    @property
    def provider_name(self) -> HarnessProvider:
        return self._inner.provider_name

    @property
    def requests(self) -> list[Any]:
        return self._inner.requests

    async def run_turn(
        self,
        request: Any,
        *,
        event_sink: Any = None,
        scout_dispatcher: Any = None,
    ) -> ModelingTurnResult:
        res = await self._inner.run_turn(
            request,
            event_sink=event_sink,
            scout_dispatcher=scout_dispatcher,
        )
        return ModelingTurnResult.model_validate(res.model_dump())


def _col(
    col_name: str,
    sql_type: str = "VARCHAR",
    *,
    is_pk: bool = False,
    pk_ord: int | None = None,
    fk_ref: str | None = None,
    distinct_count_est: int = 10,
    format_shape: Any = "MIXED",
    sample_values: list[str | None] | None = None,
) -> ColumnProfile:
    return ColumnProfile(
        col_name=col_name,
        sql_type=sql_type,
        nullable=False,
        is_pk=is_pk,
        pk_ord=pk_ord,
        fk_ref=fk_ref,
        null_ratio=0.0,
        distinct_count_est=distinct_count_est,
        format_shape=format_shape,
        sample_values=sample_values or [],
    )


def _build_ambiguous_fixture() -> tuple[list[SourceProfile], GraphAOntology, DraftVaultIR, str]:
    crm_acct_tid = "src_crm:public.crm_accounts"
    erp_acct_tid = "src_erp:public.erp_accounts"
    mgr_tid = "src_crm:public.crm_managers"
    rel_tid = "src_crm:public.crm_account_mgr"

    crm_prof = SourceProfile(
        source_id="src_crm",
        schema_fingerprint="fp_crm_ambig",
        table_count=3,
        tables=[
            TableProfile(
                table_id=crm_acct_tid,
                table_name="crm_accounts",
                row_count_est=100,
                columns=[
                    _col("account_id", is_pk=True, pk_ord=1, distinct_count_est=100),
                    _col(
                        "account_tier",
                        distinct_count_est=3,
                        format_shape="CODE_UPPER",
                        sample_values=["GOLD", "SILVER", "BRONZE"],
                    ),
                ],
                best_non_surrogate_bk_candidate_col="account_id",
                best_non_surrogate_bk_uniqueness_ratio=1.0,
            ),
            TableProfile(
                table_id=mgr_tid,
                table_name="crm_managers",
                row_count_est=20,
                columns=[
                    _col("manager_id", is_pk=True, pk_ord=1, distinct_count_est=20),
                    _col("manager_name", distinct_count_est=20),
                ],
                best_non_surrogate_bk_candidate_col="manager_id",
                best_non_surrogate_bk_uniqueness_ratio=1.0,
            ),
            TableProfile(
                table_id=rel_tid,
                table_name="crm_account_mgr",
                row_count_est=120,
                columns=[
                    _col(
                        "account_id",
                        is_pk=True,
                        pk_ord=1,
                        fk_ref="crm_accounts.account_id",
                        distinct_count_est=100,
                    ),
                    _col("manager_id", fk_ref="crm_managers.manager_id", distinct_count_est=20),
                    _col("status", distinct_count_est=2),
                ],
            ),
        ],
    )
    erp_prof = SourceProfile(
        source_id="src_erp",
        schema_fingerprint="fp_erp_ambig",
        table_count=1,
        tables=[
            TableProfile(
                table_id=erp_acct_tid,
                table_name="erp_accounts",
                row_count_est=100,
                columns=[
                    _col("account_id", is_pk=True, pk_ord=1, distinct_count_est=100),
                    _col(
                        "account_tier",
                        distinct_count_est=3,
                        format_shape="CODE_UPPER",
                        sample_values=["TIER1", "TIER2", "TIER3"],
                    ),
                ],
                best_non_surrogate_bk_candidate_col="account_id",
                best_non_surrogate_bk_uniqueness_ratio=1.0,
            )
        ],
    )
    profiles = [crm_prof, erp_prof]
    ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="asset_management",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am:Account",
                concept_name="Account",
                description="Client account",
                key_authority_tier="TIER_3_LOCAL_SCOPED",
                bkcc_default="CRM",
                table_bindings=[
                    ConceptTableBinding(source_id="src_crm", table_id=crm_acct_tid, col_name="account_id"),
                    ConceptTableBinding(source_id="src_erp", table_id=erp_acct_tid, col_name="account_id"),
                ],
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am:Manager",
                concept_name="Manager",
                description="Account manager",
                key_authority_tier="TIER_2_ENTERPRISE_SOR",
                bkcc_default="HR",
                table_bindings=[
                    ConceptTableBinding(source_id="src_crm", table_id=mgr_tid, col_name="manager_id"),
                ],
            ),
        ],
    )
    draft = DraftVaultIR(
        project_id="proj_ambig",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_3_LOCAL_SCOPED",
                survivorship_priority=["src_crm", "src_erp"],
                business_keys=[
                    BusinessKeyPart(
                        table_id=crm_acct_tid,
                        col_name="account_id",
                        source_id="src_crm",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="CRM",
                    ),
                    BusinessKeyPart(
                        table_id=erp_acct_tid,
                        col_name="account_id",
                        source_id="src_erp",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="ERP",
                    ),
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="src_crm", table_id=crm_acct_tid, col_mappings={"account_id": "account_id"}),
                    DraftSourceBinding(source_id="src_erp", table_id=erp_acct_tid, col_mappings={"account_id": "account_id"}),
                ],
            ),
            DraftHubSpec(
                hub_name="hub_manager",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=mgr_tid,
                        col_name="manager_id",
                        source_id="src_crm",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="HR",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="src_crm", table_id=mgr_tid, col_mappings={"manager_id": "manager_id"})
                ],
            ),
        ],
        links=[
            DraftLinkSpec(
                link_name="lnk_account_manager",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_account",
                        role_name="ACCOUNT",
                        fk_col_name="hk_account",
                        source_col_names=["account_id"],
                        target_hub_binding_table_id=crm_acct_tid,
                    ),
                    LinkParticipantSpec(
                        hub_name="hub_manager",
                        role_name="MANAGER",
                        fk_col_name="hk_manager",
                        source_col_names=["manager_id"],
                    ),
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_crm",
                        table_id=rel_tid,
                        col_mappings={"account_id": "account_id", "manager_id": "manager_id"},
                    )
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_account_crm",
                sat_kind="STANDARD",
                parent_name="hub_account",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_crm",
                        table_id=crm_acct_tid,
                        col_mappings={"account_tier": "account_tier"},
                    )
                ],
                attributes=[
                    DraftField(name="account_tier", logical_type="UTF8_STRING", nullable=False, is_pii=False)
                ],
            ),
            DraftSatelliteSpec(
                sat_name="sat_account_erp",
                sat_kind="STANDARD",
                parent_name="hub_account",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_erp",
                        table_id=erp_acct_tid,
                        col_mappings={"account_tier": "account_tier"},
                    )
                ],
                attributes=[
                    DraftField(name="account_tier", logical_type="UTF8_STRING", nullable=False, is_pii=False)
                ],
            ),
            DraftSatelliteSpec(
                sat_name="sat_manager_crm",
                sat_kind="STANDARD",
                parent_name="hub_manager",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_crm",
                        table_id=mgr_tid,
                        col_mappings={"manager_name": "manager_name"},
                    )
                ],
                attributes=[
                    DraftField(name="manager_name", logical_type="UTF8_STRING", nullable=False, is_pii=False)
                ],
            ),
        ],
        column_triage=_triage_all(profiles),
    )
    return profiles, ont, draft, rel_tid


@pytest.mark.asyncio
async def test_model_vault_12trap_end_to_end_remediation_and_certification() -> None:
    """Full Mode-2 model_vault on ds_cegar_12trap.duckdb repairs Attempt 1 -> CERTIFIED."""
    prof = profile_source("trap_db", "duckdb", str(PUBLIC_AM / "ds_cegar_12trap.duckdb"))
    profiles = [prof]
    ont = _build_12trap_ontology()
    prover = DuckDbProverAdapter()
    mgr_id = next(t.table_id for t in prof.tables if t.table_name == "trap_manager_eff")

    att1_draft = _build_12trap_draft(profiles, defective=True)
    att2_draft = _build_12trap_draft(profiles, defective=False)

    harness = _ModelingMockHarness(
        DeterministicMockHarness(
            scripts_by_agent={
                "ship-vault": [att1_draft, att2_draft],
                "dv-semantic-critic": [SemanticCriticVerdict(score=94, findings=[], accepted_waiver_ids=[])],
                "dv-horizon-critic": [HorizonCriticVerdict(score=92, classification="REASONABLY_STABLE")],
            }
        )
    )
    prefs = ModelingPreferences()
    req = DVModelingRequest(
        run_id="run-m2-12trap",
        project_id="proj_12trap",
        ontology=ont,
        profiles=profiles,
        policy="balanced",
        modeling_preferences=prefs,
        fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
        prover=prover,
        harness=harness,
        event_sink=InMemoryRunEventSink(),
        checkpoint_store=InMemoryRunCheckpointStore(),
    )
    res = await model_vault(req)

    assert res.status == "CERTIFIED"
    assert res.next_action == "READY_FOR_DOWNSTREAM"
    assert res.attempts_used == 2
    assert res.final_energy_e == 0
    assert res.deterministic_energy == 0
    assert res.v7_verdict is not None and res.v7_verdict.score == 94
    assert res.v8_verdict is not None and res.v8_verdict.score == 92
    assert res.fragility_summary is not None
    assert res.preferences_sha256 == compute_preferences_sha256(prefs)
    assert len(res.preferences_sha256) == 64
    assert len(res.modeling_guide_sha256) == 64
    assert res.verifier_report is not None and res.verifier_report.prover_report is not None
    assert len(res.verifier_report.prover_report.dyn_results) == 15
    assert all(res.verifier_report.prover_report.dyn_results.values())

    ship_reqs = [r for r in harness.requests if r.agent_id == "ship-vault"]
    assert len(ship_reqs) == 2
    att2_prompt = json.loads(ship_reqs[1].prompt)
    assert att2_prompt["remediation_hints"]


@pytest.mark.asyncio
async def test_clarify_mode_ask_on_ambiguity_vs_auto() -> None:
    """ask_on_ambiguity pauses once at S6-02 then certifies; auto skips hold and populates suggested_questions."""
    profiles, ont, draft, rel_tid = _build_ambiguous_fixture()
    all_clusters = [("cluster_all", [t.table_id for p in profiles for t in p.tables])]
    prover = DuckDbProverAdapter()
    cp_store = InMemoryRunCheckpointStore()

    hold_harness = _ModelingMockHarness(
        DeterministicMockHarness(
            scripts_by_agent={
                "ship-vault": [draft],
                "dv-semantic-critic": [SemanticCriticVerdict(score=95)],
                "dv-horizon-critic": [HorizonCriticVerdict(score=91, classification="REASONABLY_STABLE")],
            }
        )
    )
    ask_prefs = ModelingPreferences(clarify_mode="ask_on_ambiguity")
    hold_res = await model_vault(
        DVModelingRequest(
            run_id="run-clarify-ask",
            project_id="proj_ambig",
            ontology=ont,
            profiles=profiles,
            clusters=all_clusters,
            policy="balanced",
            modeling_preferences=ask_prefs,
            fk_turnover_by_table={rel_tid: {"manager_id": 0.4}},
            prover=prover,
            harness=hold_harness,
            checkpoint_store=cp_store,
        )
    )
    assert hold_res.status == "HOLD_CHECKPOINTED"
    assert hold_res.next_action == "ASK_HUMAN_HOLD"
    assert hold_res.checkpoint is not None
    assert hold_res.checkpoint.phase == "S6-02"
    assert hold_res.pending_questions
    assert len(hold_harness.requests) == 0

    answers = {
        q.question_id: (q.recommended_answer or "src_crm, src_erp")
        for q in hold_res.pending_questions
    }
    resumed_res = await model_vault(
        DVModelingRequest(
            run_id="run-clarify-ask",
            project_id="proj_ambig",
            ontology=ont,
            profiles=profiles,
            clusters=all_clusters,
            policy="balanced",
            modeling_preferences=ask_prefs,
            fk_turnover_by_table={rel_tid: {"manager_id": 0.4}},
            prover=prover,
            harness=hold_harness,
            checkpoint_store=cp_store,
            resume=ModelingResumeInput(
                checkpoint=hold_res.checkpoint,
                hold_answers=HoldAnswersReq(answers=answers),
            ),
        )
    )
    assert resumed_res.status == "CERTIFIED"
    assert resumed_res.next_action == "READY_FOR_DOWNSTREAM"
    ship_reqs = [r for r in hold_harness.requests if r.agent_id == "ship-vault"]
    assert len(ship_reqs) == 1
    assert json.loads(ship_reqs[0].prompt)["hold_answers"] == answers

    auto_harness = _ModelingMockHarness(
        DeterministicMockHarness(
            scripts_by_agent={
                "ship-vault": [draft],
                "dv-semantic-critic": [SemanticCriticVerdict(score=95)],
                "dv-horizon-critic": [HorizonCriticVerdict(score=91, classification="REASONABLY_STABLE")],
            }
        )
    )
    auto_res = await model_vault(
        DVModelingRequest(
            run_id="run-clarify-auto",
            project_id="proj_ambig",
            ontology=ont,
            profiles=profiles,
            clusters=all_clusters,
            policy="balanced",
            modeling_preferences=ModelingPreferences(clarify_mode="auto"),
            fk_turnover_by_table={rel_tid: {"manager_id": 0.4}},
            prover=prover,
            harness=auto_harness,
        )
    )
    assert auto_res.status == "CERTIFIED"
    assert auto_res.next_action == "READY_FOR_DOWNSTREAM"
    assert auto_res.suggested_questions
    assert {q.question_id for q in auto_res.suggested_questions} == {
        q.question_id for q in hold_res.pending_questions
    }


@pytest.mark.asyncio
async def test_s606_steer_and_revert_contracts() -> None:
    """Verify S6-06 pre_steer_fallback_ref, STEER resume, <=1 post-steer V8 call, and zero-LLM REVERT."""
    prof = profile_source("trap_db", "duckdb", str(PUBLIC_AM / "ds_cegar_12trap.duckdb"))
    profiles = [prof]
    ont = _build_12trap_ontology()
    prover = DuckDbProverAdapter()
    mgr_id = next(t.table_id for t in prof.tables if t.table_name == "trap_manager_eff")
    clean_draft = _build_12trap_draft(profiles, defective=False)
    defective_draft = _build_12trap_draft(profiles, defective=True)

    # 1. V8 auto-steer followed by post-steer V8 < 85 pauses at S6-06 with pre_steer_fallback_ref + revert
    h_auto_hold = _ModelingMockHarness(
        DeterministicMockHarness(
            scripts_by_agent={
                "ship-vault": [clean_draft],
                "dv-semantic-critic": [SemanticCriticVerdict(score=95)],
                "dv-horizon-critic": [
                    HorizonCriticVerdict(
                        score=74,
                        classification="NEEDS_STEER",
                        bucket_a_ops=[
                            BucketAOperatorCall(
                                operator="WIDEN_TYPE_TO_DECIMAL",
                                sat_name="sat_trade_nav",
                                source_column="nav_per_share",
                                params={"target_column": "nav_per_share", "precision": "28", "scale": "6"},
                            )
                        ],
                    ),
                    HorizonCriticVerdict(score=79, classification="REASONABLY_STABLE"),
                ],
            }
        )
    )
    auto_hold_res = await model_vault(
        DVModelingRequest(
            run_id="run-s606-autohold",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            prover=prover,
            harness=h_auto_hold,
        )
    )
    assert auto_hold_res.status == "HOLD_CHECKPOINTED"
    assert auto_hold_res.checkpoint is not None
    assert auto_hold_res.checkpoint.phase == "S6-06"
    assert auto_hold_res.checkpoint.pre_steer_fallback_ref is not None
    assert auto_hold_res.telemetry["pre_steer_fallback_ref"] == auto_hold_res.checkpoint.pre_steer_fallback_ref
    assert "revert" in [o.option_id for o in auto_hold_res.pending_questions[0].options]

    # 2. Fresh V8 < 85 pauses at S6-06; resuming with structured "steer" runs synthesis, resets best/digests,
    #    forwards horizon_assumptions + prior_draft_summary + prior_v8_verdict, and caps V8 <= 1 call
    h_low = _ModelingMockHarness(
        DeterministicMockHarness(
            scripts_by_agent={
                "ship-vault": [clean_draft],
                "dv-semantic-critic": [SemanticCriticVerdict(score=95)],
                "dv-horizon-critic": [
                    HorizonCriticVerdict(score=77, classification="REASONABLY_STABLE")
                ],
            }
        )
    )
    low_hold = await model_vault(
        DVModelingRequest(
            run_id="run-s606-steer",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            prover=prover,
            harness=h_low,
        )
    )
    assert low_hold.status == "HOLD_CHECKPOINTED"
    assert low_hold.checkpoint is not None
    assert low_hold.checkpoint.phase == "S6-06"
    assert low_hold.draft_ir is not None and low_hold.certified_ir is not None

    # 2a. Post-steer certification retires fallback
    h_steer_ok = _ModelingMockHarness(
        DeterministicMockHarness(
            scripts_by_agent={
                "ship-vault": [clean_draft],
                "dv-semantic-critic": [SemanticCriticVerdict(score=96)],
                "dv-horizon-critic": [
                    HorizonCriticVerdict(score=92, classification="REASONABLY_STABLE")
                ],
            }
        )
    )
    steer_ok = await model_vault(
        DVModelingRequest(
            run_id="run-s606-steer",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            prover=prover,
            harness=h_steer_ok,
            resume=ModelingResumeInput(
                checkpoint=low_hold.checkpoint,
                hold_answers=HoldAnswersReq(answers={"hq-v8-low-score": "steer"}),
                resumed_draft_ir=low_hold.draft_ir,
                resumed_certified_ir=low_hold.certified_ir,
            ),
        )
    )
    assert steer_ok.status == "CERTIFIED"
    assert steer_ok.v8_calls_used == 1
    assert steer_ok.telemetry["post_steer"] is True
    assert steer_ok.telemetry["reverted"] is False
    ship_steer_reqs = [r for r in h_steer_ok.requests if r.agent_id == "ship-vault"]
    assert len(ship_steer_reqs) == 1  # skip_synthesis=False and seen_digests reset
    steer_payload = json.loads(ship_steer_reqs[0].prompt)
    assert steer_payload["horizon_assumptions"][0]["steward_decision"] == "STEER"
    assert steer_payload["prior_draft_summary"]["hubs"]
    assert steer_payload["prior_v8_verdict"]["score"] == 77

    # 2b. Post-steer V8 returning bucket_a_ops is capped at <= 1 V8 call and holds with revert option
    h_steer_cap = _ModelingMockHarness(
        DeterministicMockHarness(
            scripts_by_agent={
                "ship-vault": [clean_draft],
                "dv-semantic-critic": [SemanticCriticVerdict(score=96)],
                "dv-horizon-critic": [
                    HorizonCriticVerdict(
                        score=72,
                        classification="NEEDS_STEER",
                        bucket_a_ops=[
                            BucketAOperatorCall(
                                operator="WIDEN_TYPE_TO_DECIMAL",
                                sat_name="sat_trade_nav",
                                source_column="nav_per_share",
                                params={"target_column": "nav_per_share", "precision": "28", "scale": "6"},
                            )
                        ],
                    )
                ],
            }
        )
    )
    steer_cap = await model_vault(
        DVModelingRequest(
            run_id="run-s606-steer-cap",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            prover=prover,
            harness=h_steer_cap,
            resume=ModelingResumeInput(
                checkpoint=low_hold.checkpoint,
                hold_answers=HoldAnswersReq(answers={"hq-v8-low-score": "steer"}),
                resumed_draft_ir=low_hold.draft_ir,
                resumed_certified_ir=low_hold.certified_ir,
            ),
        )
    )
    assert steer_cap.status == "HOLD_CHECKPOINTED"
    assert steer_cap.v8_calls_used == 1
    assert steer_cap.checkpoint is not None
    assert steer_cap.checkpoint.pre_steer_fallback_ref is not None
    assert "revert" in [o.option_id for o in steer_cap.pending_questions[0].options]

    # 3. Explicit REVERT restores pre-steer IR with ZERO @ship-vault calls via all 3 pathways
    # 3a: revert_to_pre_steer=True
    h_rev_flag = _ModelingMockHarness(DeterministicMockHarness())
    rev_flag = await model_vault(
        DVModelingRequest(
            run_id="run-rev-flag",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            prover=prover,
            harness=h_rev_flag,
            resume=ModelingResumeInput(
                checkpoint=steer_cap.checkpoint,
                revert_to_pre_steer=True,
                pre_steer_fallback_draft_ir=low_hold.draft_ir,
                pre_steer_fallback_certified_ir=low_hold.certified_ir,
            ),
        )
    )
    assert rev_flag.status == "CERTIFIED"
    assert rev_flag.telemetry["reverted"] is True
    assert rev_flag.certified_ir is not None
    assert [r for r in h_rev_flag.requests if r.agent_id == "ship-vault"] == []

    # 3b: hold_answers={"action": "revert"}
    h_rev_ans = _ModelingMockHarness(DeterministicMockHarness())
    rev_ans = await model_vault(
        DVModelingRequest(
            run_id="run-rev-ans",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            prover=prover,
            harness=h_rev_ans,
            resume=ModelingResumeInput(
                checkpoint=steer_cap.checkpoint,
                hold_answers=HoldAnswersReq(answers={"action": "revert"}),
                pre_steer_fallback_draft_ir=low_hold.draft_ir,
                pre_steer_fallback_certified_ir=low_hold.certified_ir,
            ),
        )
    )
    assert rev_ans.status == "CERTIFIED"
    assert rev_ans.telemetry["reverted"] is True
    assert [r for r in h_rev_ans.requests if r.agent_id == "ship-vault"] == []

    # 3c: ControlSignalRecord(action="revert")
    ctrl_rev = InMemoryControlSignalReader(
        [
            ControlSignalRecord(
                signal_id="sig-rev-1",
                run_id="run-rev-sig",
                action="revert",
                created_at="2026-11-26T00:00:00Z",
            )
        ]
    )
    h_rev_sig = _ModelingMockHarness(DeterministicMockHarness(scripts_by_agent={"ship-vault": [defective_draft]}))
    rev_sig = await model_vault(
        DVModelingRequest(
            run_id="run-rev-sig",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            prover=prover,
            harness=h_rev_sig,
            control_reader=ctrl_rev,
            resume=ModelingResumeInput(
                pre_steer_fallback_draft_ir=low_hold.draft_ir,
                pre_steer_fallback_certified_ir=low_hold.certified_ir,
            ),
        )
    )
    assert rev_sig.status == "CERTIFIED"
    assert rev_sig.telemetry["reverted"] is True
    assert [r for r in h_rev_sig.requests if r.agent_id == "ship-vault"] == []


@pytest.mark.asyncio
async def test_deduplicate_horizon_questions_and_recheck_filtering() -> None:
    """deduplicate_horizon_questions deduplicates by (brk_code, entity, column) and filters answered keys."""
    opt = HorizonOptionSpec(
        option_id="keep",
        label="Keep",
        description="Keep design",
        implications="No structural change.",
        operator_hint="lnk_account_manager.manager_id",
    )
    q1 = HorizonQuestion(
        question_id="hq-1",
        brk_code="BRK-02",
        title="Check lnk_account_manager.manager_id",
        prompt="Verify FK turnover on lnk_account_manager.manager_id.",
        options=[opt],
        impact_summary="Link arity.",
    )
    q2_dup = HorizonQuestion(
        question_id="hq-2",
        brk_code="brk-02",
        title="Duplicate lnk_account_manager.manager_id",
        prompt="Repeated check for lnk_account_manager.manager_id.",
        options=[opt],
        impact_summary="Link arity.",
    )
    q3_hub = HorizonQuestion(
        question_id="hq-3",
        brk_code="BRK-01",
        title="Check hub_account",
        prompt="Review BK on hub_account.",
        options=[
            HorizonOptionSpec(
                option_id="accept",
                label="Accept",
                description="Accept BK",
                implications="Locks BK.",
            )
        ],
        impact_summary="Hub BK.",
    )

    assert horizon_question_dedup_key(q1) == ("BRK-02", "lnk_account_manager", "manager_id")
    assert horizon_question_dedup_key(q3_hub) == ("BRK-01", "hub_account", "")

    deduped = deduplicate_horizon_questions([q1, q2_dup, q3_hub])
    assert [q.question_id for q in deduped] == ["hq-1", "hq-3"]

    answered = [format_horizon_key(horizon_question_dedup_key(q1))]
    filtered = deduplicate_horizon_questions([q1, q2_dup, q3_hub], answered_keys=answered)
    assert [q.question_id for q in filtered] == ["hq-3"]

    # Cross-check inside model_vault: answered bucket_b question is filtered on V8 re-check
    profiles, ont, draft, rel_tid = _build_ambiguous_fixture()
    all_clusters = [("cluster_all", [t.table_id for p in profiles for t in p.tables])]
    prover = DuckDbProverAdapter()
    cp_hold = await model_vault(
        DVModelingRequest(
            run_id="run-hq-dedup",
            project_id="proj_ambig",
            ontology=ont,
            profiles=profiles,
            clusters=all_clusters,
            policy="balanced",
            fk_turnover_by_table={rel_tid: {"manager_id": 0.4}},
            prover=prover,
            harness=_ModelingMockHarness(
                DeterministicMockHarness(
                    scripts_by_agent={
                        "ship-vault": [draft],
                        "dv-semantic-critic": [SemanticCriticVerdict(score=95)],
                        "dv-horizon-critic": [
                            HorizonCriticVerdict(
                                score=80,
                                classification="HUMAN_REVIEW_REQUIRED",
                                bucket_b_questions=[q1, q2_dup],
                            )
                        ],
                    }
                )
            ),
        )
    )
    assert cp_hold.status == "HOLD_CHECKPOINTED"
    assert [q.question_id for q in cp_hold.pending_questions] == ["hq-1"]

    resumed = await model_vault(
        DVModelingRequest(
            run_id="run-hq-dedup",
            project_id="proj_ambig",
            ontology=ont,
            profiles=profiles,
            clusters=all_clusters,
            policy="balanced",
            fk_turnover_by_table={rel_tid: {"manager_id": 0.4}},
            prover=prover,
            resume=ModelingResumeInput(
                checkpoint=cp_hold.checkpoint,
                hold_answers=HoldAnswersReq(answers={"hq-1": "keep"}),
                resumed_draft_ir=cp_hold.draft_ir,
                resumed_certified_ir=cp_hold.certified_ir,
            ),
            harness=_ModelingMockHarness(
                DeterministicMockHarness(
                    scripts_by_agent={
                        "dv-horizon-critic": [
                            HorizonCriticVerdict(
                                score=90,
                                classification="HUMAN_REVIEW_REQUIRED",
                                bucket_b_questions=[q2_dup],
                            )
                        ]
                    }
                )
            ),
        )
    )
    assert resumed.status == "CERTIFIED"


@pytest.mark.asyncio
async def test_accept_best_control_signal_strict_and_balanced() -> None:
    """accept-best fails closed under strict (best=None, det>0, V7<95) and never accepts det>0 under balanced."""
    prof = profile_source("trap_db", "duckdb", str(PUBLIC_AM / "ds_cegar_12trap.duckdb"))
    profiles = [prof]
    ont = _build_12trap_ontology()
    prover = DuckDbProverAdapter()
    mgr_id = next(t.table_id for t in prof.tables if t.table_name == "trap_manager_eff")
    clean_draft = _build_12trap_draft(profiles, defective=False)
    broken_draft = _build_12trap_draft(profiles, defective=True)

    def _sig_ctrl(run_id: str) -> InMemoryControlSignalReader:
        return InMemoryControlSignalReader(
            [
                ControlSignalRecord(
                    signal_id=f"sig-{run_id}",
                    run_id=run_id,
                    action="accept-best",
                    created_at="2026-11-26T00:00:00Z",
                )
            ]
        )

    # 1. strict with best is None (exhausted turn budget on resumed checkpoint) -> VERIFICATION_GATE_FAILED
    exhausted_cp = RunCheckpointRecord(
        run_id="run-strict-none",
        step="step6",
        phase="S6-02",
        status="HOLD_CHECKPOINTED",
        last_seq=1,
        ontology_rev_id="1.0.0",
        sources_fingerprint="fp",
        budget_turns_used=10,
        budget_tokens_used=0,
        token_count_source="estimated",
    )
    with pytest.raises(OpenDVError) as exc_none:
        await model_vault(
            DVModelingRequest(
                run_id="run-strict-none",
                project_id="proj_12trap",
                ontology=ont,
                profiles=profiles,
                policy="strict",
                budget=HarnessBudget(max_turns=2, max_tokens=50_000, timeout_seconds=120),
                prover=prover,
                control_reader=_sig_ctrl("run-strict-none"),
                resume=ModelingResumeInput(checkpoint=exhausted_cp),
                harness=_ModelingMockHarness(DeterministicMockHarness()),
            )
        )
    assert exc_none.value.error_code == ErrorCode.VERIFICATION_GATE_FAILED

    # 2. strict with deterministic_energy > 0 -> VERIFICATION_GATE_FAILED
    with pytest.raises(OpenDVError) as exc_det:
        await model_vault(
            DVModelingRequest(
                run_id="run-strict-det",
                project_id="proj_12trap",
                ontology=ont,
                profiles=profiles,
                policy="strict",
                prover=prover,
                control_reader=_sig_ctrl("run-strict-det"),
                harness=_ModelingMockHarness(
                    DeterministicMockHarness(scripts_by_agent={"ship-vault": [broken_draft]})
                ),
            )
        )
    assert exc_det.value.error_code == ErrorCode.VERIFICATION_GATE_FAILED

    # 3. strict with V7 < 95 -> VERIFICATION_GATE_FAILED
    with pytest.raises(OpenDVError) as exc_v7:
        await model_vault(
            DVModelingRequest(
                run_id="run-strict-v7",
                project_id="proj_12trap",
                ontology=ont,
                profiles=profiles,
                policy="strict",
                fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
                prover=prover,
                control_reader=_sig_ctrl("run-strict-v7"),
                harness=_ModelingMockHarness(
                    DeterministicMockHarness(
                        scripts_by_agent={
                            "ship-vault": [clean_draft],
                            "dv-semantic-critic": [SemanticCriticVerdict(score=91, findings=["<95"])],
                        }
                    )
                ),
            )
        )
    assert exc_v7.value.error_code == ErrorCode.VERIFICATION_GATE_FAILED

    # 4. balanced with deterministic_energy > 0 -> does NOT accept, returns UNCERTIFIED_DRAFT
    bal_broken = await model_vault(
        DVModelingRequest(
            run_id="run-bal-det",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            prover=prover,
            control_reader=_sig_ctrl("run-bal-det"),
            harness=_ModelingMockHarness(
                DeterministicMockHarness(
                    scripts_by_agent={"ship-vault": [broken_draft]},
                    fault=MockFaultSpec(duplicate_ir_cycle=True),
                )
            ),
        )
    )
    assert bal_broken.status == "UNCERTIFIED_DRAFT"
    assert bal_broken.next_action == "REPAIR_DRAFT_IR"
    assert bal_broken.deterministic_energy is not None and bal_broken.deterministic_energy > 0


@pytest.mark.asyncio
async def test_ontology_proposals_harvested_from_ship_vault_tool_events() -> None:
    """OntologyProposalSpec entries emitted in @ship-vault tool_events are harvested as PENDING_STEWARD."""
    profiles, ont, draft, rel_tid = _build_ambiguous_fixture()
    all_clusters = [("cluster_all", [t.table_id for p in profiles for t in p.tables])]
    prover = DuckDbProverAdapter()
    prop_payload = {
        "proposal_id": "prop-w1-account-region",
        "summary": "Add AccountRegion concept observed during modeling",
        "delta_ops": [
            AddConcept(
                concept_uri="urn:opendv:am:AccountRegion",
                concept_name="AccountRegion",
                description="Account geographic region",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                attributes=[
                    ConceptAttribute(
                        name="region_code",
                        logical_type="UTF8_STRING",
                        sql_type_hint="VARCHAR",
                        nullable=False,
                        is_business_key=True,
                        is_pii=False,
                    )
                ],
            ).model_dump(mode="json")
        ],
    }

    res = await model_vault(
        DVModelingRequest(
            run_id="run-props",
            project_id="proj_ambig",
            ontology=ont,
            profiles=profiles,
            clusters=all_clusters,
            policy="balanced",
            fk_turnover_by_table={rel_tid: {"manager_id": 0.4}},
            prover=prover,
            harness=_ModelingMockHarness(
                DeterministicMockHarness(
                    scripts_by_agent={
                        "ship-vault": [
                            MockTurnScript(
                                structured_output=draft,
                                tool_events=[{"ontology_proposals": [prop_payload]}],
                            )
                        ],
                        "dv-semantic-critic": [SemanticCriticVerdict(score=95)],
                        "dv-horizon-critic": [HorizonCriticVerdict(score=92, classification="REASONABLY_STABLE")],
                    }
                )
            ),
        )
    )
    assert res.status == "CERTIFIED"
    assert len(res.ontology_proposals) == 1
    assert res.ontology_proposals[0].proposal_id == "prop-w1-account-region"
    assert res.ontology_proposals[0].status == "PENDING_STEWARD"
    assert len(res.ontology_proposals[0].delta_ops) == 1
