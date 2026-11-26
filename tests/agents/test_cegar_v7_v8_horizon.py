"""Phase V2-5 Milestone M1 tests: UAT-V25-AGT-06..10 (CEGAR, V7, V8, scale_200, W1->W2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from opendv.modeling import (
    DVModelingRequest,
    ModelingResumeInput,
    ModelingTurnRequest,
    ModelingTurnResult,
    WaveEvolutionInput,
    audit_v6b_justifications,
    model_vault,
    run_v8_horizon_critic,
    run_v8_horizon_review,
)
from opendv.modeling._sharding import run_ship_vault_synthesis
from opendv.modeling.ports import ModelingEventSink, ModelingScoutDispatcher
from opendv.models.diagnostics import (
    AntiPatternJustification,
    ClarificationQuestion,
    HorizonOptionSpec,
    HorizonQuestion,
)
from opendv.models.errors import CliExitCode, ErrorCode, OpenDVError, get_error_entry
from opendv.models.events import ControlSignalRecord
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ConceptAttribute,
    GraphAOntology,
)
from opendv.models.ontology_deltas import AddConcept
from opendv.models.policy import HarnessBudget
from opendv.models.project import EvolutionLockSpec, HarnessProvider
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir import (
    BusinessKeyPart,
    ColumnTriageEntry,
    DecimalType,
    DraftField,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
    DraftShardIR,
    DraftSourceBinding,
    DraftVaultIR,
    LinkParticipantSpec,
)
from opendv.models.wire_dtos_discovery import HoldAnswersReq
from opendv.models.wire_dtos_vault import (
    BucketAOperatorCall,
    HorizonCriticVerdict,
    SemanticCriticVerdict,
)
from opendv.sources.clustering import cluster_tables
from opendv.sources.profiler import profile_source
from opendv.storage import DuckDbProverAdapter, WaveMigrator
from opendv.verifier import (
    VerifierContext,
    build_evolution_lock_from_certified,
    merge_draft_shards,
    run_l1_codemods,
    run_v1_to_v6_gates,
)
from tests.fixtures.scale_200 import build_scale_200_table_profiles
from tests.helpers import (
    DeterministicMockHarness,
    InMemoryControlSignalReader,
    InMemoryRunCheckpointStore,
    InMemoryRunEventSink,
    MockFaultSpec,
    MockTurnScript,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_AM = REPO_ROOT / "data" / "public_am"


def _triage_all(
    profiles: list[SourceProfile],
    *,
    system_cols: set[str] | None = None,
) -> list[ColumnTriageEntry]:
    syscols = system_cols or set()
    entries: list[ColumnTriageEntry] = []
    for prof in profiles:
        for tbl in prof.tables:
            for col in tbl.columns:
                if col.col_name in syscols:
                    entries.append(
                        ColumnTriageEntry(
                            table_id=tbl.table_id,
                            col_name=col.col_name,
                            disposition="SYSTEM_METADATA",
                            target_ref=None,
                            rationale="Technical timestamp column excluded from vault payload.",
                        )
                    )
                else:
                    entries.append(
                        ColumnTriageEntry(
                            table_id=tbl.table_id,
                            col_name=col.col_name,
                            disposition="MODELED",
                            target_ref="modeled",
                            rationale="",
                        )
                    )
    return entries


def _build_12trap_ontology() -> GraphAOntology:
    specs = [
        ("Account", "TIER_3_LOCAL_SCOPED", "CRM"),
        ("Issuer", "TIER_2_ENTERPRISE_SOR", "SEC_CIK"),
        ("Broker", "TIER_1_GLOBAL_STANDARD", "GLOBAL"),
        ("CurrencyCode", "TIER_1_GLOBAL_STANDARD", "GLOBAL"),
        ("Ballot", "TIER_3_LOCAL_SCOPED", "PROXY"),
        ("PortfolioManager", "TIER_2_ENTERPRISE_SOR", "PM_DIR"),
        ("Position", "TIER_3_LOCAL_SCOPED", "IBOR"),
        ("Trade", "TIER_2_ENTERPRISE_SOR", "OMS"),
        ("PlannedClearingHouse", "TIER_1_GLOBAL_STANDARD", "GLOBAL"),
    ]
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="asset_management",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri=f"urn:opendv:am:{name}",
                concept_name=name,
                description=f"{name} concept",
                key_authority_tier=tier,  # type: ignore[arg-type]
                bkcc_default=bkcc,
            )
            for name, tier, bkcc in specs
        ],
    )


def _build_12trap_draft(
    profiles: list[SourceProfile],
    *,
    defective: bool,
) -> DraftVaultIR:
    tbl_map = {t.table_name: t.table_id for p in profiles for t in p.tables}
    crm_id = tbl_map["trap_crm_accounts"]
    ibor_id = tbl_map["trap_ibor_accounts"]
    ballot_id = tbl_map["trap_ballots_mas"]
    snap_id = tbl_map["trap_snapshots_churn"]
    mgr_id = tbl_map["trap_manager_eff"]
    trd_id = tbl_map["trap_early_trades"]

    hubs = [
        DraftHubSpec(
            hub_name="hub_account",
            key_tier="TIER_3_LOCAL_SCOPED",
            business_keys=[
                BusinessKeyPart(
                    table_id=crm_id,
                    col_name="account_id",
                    source_id="trap_db",
                    key_tier="TIER_3_LOCAL_SCOPED",
                    bkcc_value="CRM",
                ),
                BusinessKeyPart(
                    table_id=ibor_id,
                    col_name="account_id",
                    source_id="trap_db",
                    key_tier="TIER_3_LOCAL_SCOPED",
                    bkcc_value="IBOR",
                ),
                BusinessKeyPart(
                    table_id=mgr_id,
                    col_name="account_id",
                    source_id="trap_db",
                    key_tier="TIER_3_LOCAL_SCOPED",
                    bkcc_value="IBOR",
                ),
            ],
            source_bindings=[
                DraftSourceBinding(source_id="trap_db", table_id=crm_id, col_mappings={"account_id": "account_id"}),
                DraftSourceBinding(source_id="trap_db", table_id=ibor_id, col_mappings={"account_id": "account_id"}),
                DraftSourceBinding(source_id="trap_db", table_id=mgr_id, col_mappings={"account_id": "account_id"}),
            ],
        ),
        DraftHubSpec(
            hub_name="hub_issuer",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id=ibor_id,
                    col_name="issuer_cik",
                    source_id="trap_db",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    bkcc_value="SEC_CIK",
                )
            ],
            source_bindings=[
                DraftSourceBinding(source_id="trap_db", table_id=ibor_id, col_mappings={"issuer_cik": "issuer_cik"})
            ],
        ),
        DraftHubSpec(
            hub_name="hub_broker",
            key_tier="TIER_1_GLOBAL_STANDARD",
            business_keys=[
                BusinessKeyPart(
                    table_id=ibor_id,
                    col_name="executing_broker_lei",
                    source_id="trap_db",
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    bkcc_value="GLOBAL",
                )
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id="trap_db",
                    table_id=ibor_id,
                    col_mappings={"executing_broker_lei": "executing_broker_lei"},
                )
            ],
        ),
        DraftHubSpec(
            hub_name="hub_currency_code",
            key_tier="TIER_1_GLOBAL_STANDARD",
            business_keys=[
                BusinessKeyPart(
                    table_id=ballot_id,
                    col_name="iso_currency_code",
                    source_id="trap_db",
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    bkcc_value="GLOBAL",
                )
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id="trap_db",
                    table_id=ballot_id,
                    col_mappings={"iso_currency_code": "iso_currency_code"},
                )
            ],
        ),
        DraftHubSpec(
            hub_name="hub_ballot",
            key_tier="TIER_3_LOCAL_SCOPED",
            business_keys=[
                BusinessKeyPart(
                    table_id=ballot_id,
                    col_name="ballot_id",
                    source_id="trap_db",
                    key_tier="TIER_3_LOCAL_SCOPED",
                    bkcc_value="PROXY",
                )
            ],
            source_bindings=[
                DraftSourceBinding(source_id="trap_db", table_id=ballot_id, col_mappings={"ballot_id": "ballot_id"})
            ],
        ),
        DraftHubSpec(
            hub_name="hub_portfolio_manager",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id=mgr_id,
                    col_name="portfolio_manager_id",
                    source_id="trap_db",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    bkcc_value="PM_DIR",
                )
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id="trap_db",
                    table_id=mgr_id,
                    col_mappings={"portfolio_manager_id": "portfolio_manager_id"},
                )
            ],
        ),
        DraftHubSpec(
            hub_name="hub_position",
            key_tier="TIER_3_LOCAL_SCOPED",
            business_keys=[
                BusinessKeyPart(
                    table_id=snap_id,
                    col_name="position_id",
                    source_id="trap_db",
                    key_tier="TIER_3_LOCAL_SCOPED",
                    bkcc_value="IBOR",
                )
            ],
            source_bindings=[
                DraftSourceBinding(source_id="trap_db", table_id=snap_id, col_mappings={"position_id": "position_id"})
            ],
        ),
        DraftHubSpec(
            hub_name="hub_trade",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id=trd_id,
                    col_name="trade_id",
                    source_id="trap_db",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    bkcc_value="OMS",
                )
            ],
            source_bindings=[
                DraftSourceBinding(source_id="trap_db", table_id=trd_id, col_mappings={"trade_id": "trade_id"})
            ],
        ),
        DraftHubSpec(
            hub_name="hub_planned_clearing_house",
            key_tier="TIER_1_GLOBAL_STANDARD",
            business_keys=[],
            source_bindings=[],
            is_planned_concept=True,
        ),
    ]
    links = [
        DraftLinkSpec(
            link_name="lnk_account_broker",
            link_kind="STANDARD",
            participants=[
                LinkParticipantSpec(
                    hub_name="hub_account",
                    role_name="ACCOUNT",
                    fk_col_name="hk_account",
                    source_col_names=["account_id"],
                ),
                LinkParticipantSpec(
                    hub_name="hub_broker",
                    role_name="BROKER",
                    fk_col_name="hk_broker",
                    source_col_names=["executing_broker_lei"],
                ),
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id="trap_db",
                    table_id=ibor_id,
                    col_mappings={"account_id": "account_id", "executing_broker_lei": "executing_broker_lei"},
                )
            ],
        ),
        DraftLinkSpec(
            link_name="lnk_account_manager",
            link_kind="STANDARD",
            participants=[
                LinkParticipantSpec(
                    hub_name="hub_account",
                    role_name="ACCOUNT",
                    fk_col_name="hk_account",
                    source_col_names=["account_id"],
                ),
                LinkParticipantSpec(
                    hub_name="hub_portfolio_manager",
                    role_name="MANAGER",
                    fk_col_name="hk_portfolio_manager",
                    source_col_names=["portfolio_manager_id"],
                ),
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id="trap_db",
                    table_id=mgr_id,
                    col_mappings={"account_id": "account_id", "portfolio_manager_id": "portfolio_manager_id"},
                )
            ],
        ),
    ]
    ballot_sat = (
        DraftSatelliteSpec(
            sat_name="sat_ballot_proposals",
            sat_kind="STANDARD",
            parent_name="hub_ballot",
            source_bindings=[
                DraftSourceBinding(
                    source_id="trap_db",
                    table_id=ballot_id,
                    col_mappings={"vote_cast_code": "vote_cast_code"},
                )
            ],
            attributes=[
                DraftField(name="vote_cast_code", logical_type="UTF8_STRING", nullable=True, is_pii=False),
            ],
        )
        if defective
        else DraftSatelliteSpec(
            sat_name="mas_ballot_proposals",
            sat_kind="MAS",
            parent_name="hub_ballot",
            child_dependent_keys=["proposal_seq"],
            source_bindings=[
                DraftSourceBinding(
                    source_id="trap_db",
                    table_id=ballot_id,
                    col_mappings={"proposal_seq": "proposal_seq", "shares_voted": "shares_voted"},
                )
            ],
            attributes=[
                DraftField(name="proposal_seq", logical_type="INT32", nullable=True, is_pii=False),
                DraftField(name="shares_voted", logical_type="INT64", nullable=True, is_pii=False),
            ],
        )
    )
    snap_attrs = [DraftField(name="quantity", logical_type=DecimalType(precision=18, scale=4), nullable=True, is_pii=False)]
    snap_map = {"quantity": "quantity"}
    if defective:
        snap_attrs.append(DraftField(name="etl_snapshot_ts", logical_type="TIMESTAMP_UTC_US", nullable=True, is_pii=False))
        snap_map["etl_snapshot_ts"] = "etl_snapshot_ts"

    sats = [
        DraftSatelliteSpec(
            sat_name="sat_account_crm",
            sat_kind="STANDARD",
            parent_name="hub_account",
            source_bindings=[
                DraftSourceBinding(source_id="trap_db", table_id=crm_id, col_mappings={"account_tier": "account_tier"})
            ],
            attributes=[
                DraftField(name="beneficial_owner_tax_id", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                DraftField(name="stewardship_officer_email", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                DraftField(name="client_phone", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                DraftField(name="account_tier", logical_type="UTF8_STRING", nullable=True, is_pii=False, classification_tag="STATIC"),
                DraftField(name="daily_nav_usd", logical_type=DecimalType(precision=18, scale=2), nullable=True, is_pii=False, classification_tag="HIGH_FREQ"),
            ],
        ),
        DraftSatelliteSpec(
            sat_name="sat_issuer_ibor",
            sat_kind="STANDARD",
            parent_name="hub_issuer",
            source_bindings=[
                DraftSourceBinding(source_id="trap_db", table_id=ibor_id, col_mappings={"issuer_legal_name": "issuer_legal_name"})
            ],
            attributes=[
                DraftField(name="issuer_legal_name", logical_type="UTF8_STRING", nullable=True, is_pii=False),
            ],
        ),
        ballot_sat,
        DraftSatelliteSpec(
            sat_name="sat_position_snap",
            sat_kind="STANDARD",
            parent_name="hub_position",
            source_bindings=[
                DraftSourceBinding(source_id="trap_db", table_id=snap_id, col_mappings=snap_map)
            ],
            attributes=snap_attrs,
        ),
        DraftSatelliteSpec(
            sat_name="sat_trade_nav",
            sat_kind="STANDARD",
            parent_name="hub_trade",
            is_bitemporal=True,
            applied_dts_col="applied_dts",
            source_bindings=[
                DraftSourceBinding(source_id="trap_db", table_id=trd_id, col_mappings={"nav_per_share": "nav_per_share"})
            ],
            attributes=[
                DraftField(name="nav_per_share", logical_type=DecimalType(precision=18, scale=4), nullable=True, is_pii=False),
            ],
        ),
    ]
    return DraftVaultIR(
        project_id="proj_12trap",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=hubs,
        links=links,
        satellites=sats,
        column_triage=_triage_all(profiles, system_cols=set() if defective else {"etl_snapshot_ts"}),
    )


@pytest.mark.asyncio
async def test_uat_v25_agt_06_cegar_12trap_duckdb_repair_and_cycle_guard() -> None:
    """UAT-V25-AGT-06: ds_cegar_12trap.duckdb + DuckDbProverAdapter repair + 2-cycle guard."""
    prof = profile_source("trap_db", "duckdb", str(PUBLIC_AM / "ds_cegar_12trap.duckdb"))
    profiles = [prof]
    ont = _build_12trap_ontology()
    prover = DuckDbProverAdapter()
    mgr_id = next(t.table_id for t in prof.tables if t.table_name == "trap_manager_eff")

    att1_draft = _build_12trap_draft(profiles, defective=True)
    att2_draft = _build_12trap_draft(profiles, defective=False)

    sink = InMemoryRunEventSink()
    cp_store = InMemoryRunCheckpointStore()
    harness = DeterministicMockHarness(
        scripts_by_agent={
            "ship-vault": [att1_draft, att2_draft],
            "dv-semantic-critic": [SemanticCriticVerdict(score=94, findings=[], accepted_waiver_ids=[])],
            "dv-horizon-critic": [HorizonCriticVerdict(score=92, classification="REASONABLY_STABLE")],
        }
    )

    res = await model_vault(
        DVModelingRequest(
            run_id="run-12trap",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            verifier_ctx=VerifierContext(
                profiles=profiles,
                fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            ),
            prover=prover,
            harness=harness,
            event_sink=sink,
            checkpoint_store=cp_store,
        )
    )
    assert res.status == "CERTIFIED"
    assert res.attempts_used == 2
    assert res.certified_ir is not None
    assert res.evolution_lock is not None
    assert res.verifier_report is not None
    assert res.verifier_report.prover_report is not None
    assert len(res.verifier_report.prover_report.dyn_results) == 15
    assert all(res.verifier_report.prover_report.dyn_results.values())
    assert res.attempts[1].energy_e is not None and res.attempts[0].energy_e is not None
    assert res.attempts[1].energy_e < res.attempts[0].energy_e

    # Verify S6-01..S6-07 stages emitted in sequence
    stages = [
        ev.payload["stage_id"]
        for ev in sink.events
        if ev.event_type == "STAGE_UPDATE"
    ]
    for required_stage in ("S6-01", "S6-02", "S6-03", "S6-04", "S6-06", "S6-07"):
        assert required_stage in stages

    # Sub-case: 2nd duplicate SHA-256 IR triggers cycle guard immediately (halts at Attempt 2 < k_max=4)
    cycle_harness = DeterministicMockHarness(
        scripts_by_agent={"ship-vault": [att1_draft]},
        fault=MockFaultSpec(duplicate_ir_cycle=True),
    )
    cycle_res = await model_vault(
        DVModelingRequest(
            run_id="run-12trap-cycle",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            verifier_ctx=VerifierContext(profiles=profiles),
            prover=prover,
            harness=cycle_harness,
        )
    )
    assert cycle_res.cycle_halted is True
    assert cycle_res.attempts_used == 2
    assert cycle_res.status == "UNCERTIFIED_DRAFT"


@pytest.mark.asyncio
async def test_uat_v25_agt_07_cegar_all_4_policies_waivers_and_gates() -> None:
    """UAT-V25-AGT-07: symbolic_only, fast (+horizon-review), balanced, strict, UNCERTIFIED_DRAFT."""
    prof = profile_source("trap_db", "duckdb", str(PUBLIC_AM / "ds_cegar_12trap.duckdb"))
    profiles = [prof]
    ont = _build_12trap_ontology()
    prover = DuckDbProverAdapter()
    mgr_id = next(t.table_id for t in prof.tables if t.table_name == "trap_manager_eff")

    clean_draft = _build_12trap_draft(profiles, defective=False)
    broken_draft = _build_12trap_draft(profiles, defective=True)

    # 1. symbolic_only: 1 pass, no V7
    sym_res = await model_vault(
        DVModelingRequest(
            run_id="run-sym",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="symbolic_only",
            verifier_ctx=VerifierContext(
                profiles=profiles,
                fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            ),
            prover=prover,
            harness=DeterministicMockHarness(scripts_by_agent={"ship-vault": [clean_draft]}),
        )
    )
    assert sym_res.status == "CERTIFIED"
    assert sym_res.attempts_used == 1
    assert sym_res.v7_verdict is None

    # 2. fast: CERTIFIED_FAST -> in-place horizon-review upgrade to CERTIFIED without wave bump
    fast_harness = DeterministicMockHarness(
        scripts_by_agent={
            "ship-vault": [clean_draft],
            "dv-horizon-critic": [HorizonCriticVerdict(score=90, classification="REASONABLY_STABLE")],
        }
    )
    fast_req = DVModelingRequest(
        run_id="run-fast",
        project_id="proj_12trap",
        ontology=ont,
        profiles=profiles,
        policy="fast",
        verifier_ctx=VerifierContext(
            profiles=profiles,
            fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
        ),
        prover=prover,
        harness=fast_harness,
    )
    fast_res = await model_vault(fast_req)
    assert fast_res.status == "CERTIFIED_FAST"
    assert fast_res.v7_verdict is None
    assert fast_res.wave_number == 1

    upgraded = await run_v8_horizon_review(fast_req, fast_res)
    assert upgraded.status == "CERTIFIED"
    assert upgraded.wave_number == 1
    assert upgraded.policy == "fast"
    assert upgraded.v8_verdict is not None

    # 3. strict: accept-best rejected when v7 < 95 -> VERIFICATION_GATE_FAILED; passes with V7 >= 95 + waiver
    ctrl = InMemoryControlSignalReader(
        [
            ControlSignalRecord(
                signal_id="sig-acc-1",
                run_id="run-strict-fail",
                action="accept-best",
                created_at="2026-11-23T00:00:00Z",
            )
        ]
    )
    with pytest.raises(OpenDVError) as exc_strict:
        await model_vault(
            DVModelingRequest(
                run_id="run-strict-fail",
                project_id="proj_12trap",
                ontology=ont,
                profiles=profiles,
                policy="strict",
                verifier_ctx=VerifierContext(
                    profiles=profiles,
                    fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
                ),
                prover=prover,
                control_reader=ctrl,
                harness=DeterministicMockHarness(
                    scripts_by_agent={
                        "ship-vault": [clean_draft],
                        "dv-semantic-critic": [SemanticCriticVerdict(score=91, findings=["Below 95"], accepted_waiver_ids=[])],
                    }
                ),
            )
        )
    assert exc_strict.value.error_code == ErrorCode.VERIFICATION_GATE_FAILED

    waived_draft = clean_draft.model_copy(
        update={
            "justifications": [
                AntiPatternJustification(
                    waiver_id="WAIV-c981d86d",
                    rule_id="DV-AP-004",
                    entity_name="hub_broker",
                    rationale="Sparse optional broker LEI modeled with IS_NOT_NULL filter.",
                    actor="agent",
                    reviewed_by_v7=False,
                )
            ]
        }
    )
    strict_ok = await model_vault(
        DVModelingRequest(
            run_id="run-strict-ok",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="strict",
            verifier_ctx=VerifierContext(
                profiles=profiles,
                fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            ),
            prover=prover,
            harness=DeterministicMockHarness(
                scripts_by_agent={
                    "ship-vault": [waived_draft],
                    "dv-semantic-critic": [
                        SemanticCriticVerdict(
                            score=96,
                            findings=[],
                            accepted_waiver_ids=["WAIV-c981d86d"],
                        )
                    ],
                    "dv-horizon-critic": [
                        HorizonCriticVerdict(score=95, classification="REASONABLY_STABLE")
                    ],
                }
            ),
        )
    )
    assert strict_ok.status == "CERTIFIED"
    assert strict_ok.certified_ir is not None
    assert strict_ok.certified_ir.justifications[0].reviewed_by_v7 is True

    # 4. Broken V1-V6 ALWAYS yields UNCERTIFIED_DRAFT (CliExitCode 5)
    broken_res = await model_vault(
        DVModelingRequest(
            run_id="run-broken",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="symbolic_only",
            verifier_ctx=VerifierContext(profiles=profiles),
            prover=prover,
            harness=DeterministicMockHarness(scripts_by_agent={"ship-vault": [broken_draft]}),
        )
    )
    assert broken_res.status == "UNCERTIFIED_DRAFT"
    assert get_error_entry(ErrorCode.UNCERTIFIED_DRAFT).exit_code == CliExitCode.HOLD_OR_UNCERTIFIED


@pytest.mark.asyncio
async def test_uat_v25_agt_08_v8_horizon_bucket_a_reroute_bucket_b_and_fast_path() -> None:
    """UAT-V25-AGT-08: prime_allocations_w2.csv Bucket A (<=1) + DuckDbProverAdapter, Bucket B, >=85."""
    alloc_prof = profile_source("src_alloc", "csv", str(PUBLIC_AM / "prime_allocations_w2.csv"))
    profiles = [alloc_prof]
    tbl_id = alloc_prof.tables[0].table_id
    prover = DuckDbProverAdapter()
    ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="asset_management",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am:Block",
                concept_name="Block",
                description="Block trade",
                key_authority_tier="TIER_2_ENTERPRISE_SOR",
                bkcc_default="OMS",
            )
        ],
    )
    w1_mas_draft = DraftVaultIR(
        project_id="proj_alloc",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_block",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=tbl_id,
                        col_name="block_id",
                        source_id="src_alloc",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="OMS",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_alloc",
                        table_id=tbl_id,
                        col_mappings={"block_id": "block_id"},
                    )
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="mas_allocations",
                sat_kind="MAS",
                parent_name="hub_block",
                child_dependent_keys=["allocation_seq"],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_alloc",
                        table_id=tbl_id,
                        col_mappings={
                            "allocation_seq": "allocation_seq",
                            "shares_allocated": "shares_allocated",
                            "avg_price_dec": "avg_price_dec",
                        },
                    )
                ],
                attributes=[
                    DraftField(
                        name="allocation_seq",
                        logical_type="INT32",
                        nullable=False,
                        is_pii=False,
                    ),
                    DraftField(
                        name="shares_allocated",
                        logical_type="INT32",
                        nullable=False,
                        is_pii=False,
                    ),
                    DraftField(
                        name="avg_price_dec",
                        logical_type=DecimalType(precision=18, scale=4),
                        nullable=False,
                        is_pii=False,
                    ),
                ],
            )
        ],
        column_triage=_triage_all(profiles),
    )
    _, w1_mas_cert, _ = run_l1_codemods(w1_mas_draft, profiles=profiles)
    w1_mas_report = run_v1_to_v6_gates(
        w1_mas_cert,
        VerifierContext(profiles=profiles, ontology=ont),
        prover=prover,
        include_v6b=True,
    )
    assert w1_mas_report.v1_v6_fail_count == 0
    w1_mas_locks = build_evolution_lock_from_certified(w1_mas_cert)

    # Case 1a: PROMOTE_HUB_BK_TO_IMMUTABLE on locked hub_block -> canonical Hub + sal_block under DuckDbProverAdapter
    h_promote = DeterministicMockHarness(
        scripts_by_agent={
            "ship-vault": [w1_mas_draft.model_copy(update={"wave_number": 2})],
            "dv-semantic-critic": [SemanticCriticVerdict(score=95, findings=[], accepted_waiver_ids=[])],
            "dv-horizon-critic": [
                HorizonCriticVerdict(
                    score=76,
                    classification="NEEDS_STEER",
                    bucket_a_ops=[
                        BucketAOperatorCall(
                            operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                            target_hub="hub_block",
                            table_id=tbl_id,
                            source_column="block_id",
                            params={"new_bk_col": "fund_account", "source_id": "src_alloc"},
                        )
                    ],
                ),
                HorizonCriticVerdict(
                    score=93,
                    classification="REASONABLY_STABLE",
                    bucket_c_watchlist=["Monitor fund_account distribution"],
                ),
            ],
        }
    )
    res_prom = await model_vault(
        DVModelingRequest(
            run_id="run-v8-prom",
            project_id="proj_alloc",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            wave=WaveEvolutionInput(
                evolution_locks=w1_mas_locks,
                prior_certified_ir=w1_mas_cert,
            ),
            prover=prover,
            harness=h_promote,
        )
    )
    assert res_prom.status == "CERTIFIED"
    assert res_prom.horizon_auto_steers_used == 1
    assert res_prom.v8_calls_used == 2
    assert res_prom.verifier_report is not None
    assert res_prom.verifier_report.v1_v6_fail_count == 0
    assert res_prom.verifier_report.prover_report is not None
    assert all(res_prom.verifier_report.prover_report.dyn_results.values())
    assert res_prom.certified_ir is not None
    assert "hub_block_canonical" in {h.hub_name for h in res_prom.certified_ir.hubs}
    assert "sal_block" in {lnk.link_name for lnk in res_prom.certified_ir.links}

    # Case 1b: Bucket A ops targeting locked sat_allocations -> reroutes to mas_allocations & _ext_vN
    w1_std_draft = w1_mas_draft.model_copy(
        update={
            "satellites": [
                DraftSatelliteSpec(
                    sat_name="sat_allocations",
                    sat_kind="STANDARD",
                    parent_name="hub_block",
                    source_bindings=[
                        DraftSourceBinding(
                            source_id="src_alloc",
                            table_id=tbl_id,
                            col_mappings={"shares_allocated": "shares_allocated", "avg_price_dec": "avg_price_dec"},
                        )
                    ],
                    attributes=[
                        DraftField(name="shares_allocated", logical_type="INT32", nullable=False, is_pii=False),
                        DraftField(name="avg_price_dec", logical_type=DecimalType(precision=18, scale=4), nullable=False, is_pii=False),
                    ],
                )
            ]
        }
    )
    _, w1_std_cert, _ = run_l1_codemods(w1_std_draft, profiles=profiles)
    w1_std_locks = build_evolution_lock_from_certified(w1_std_cert)
    bucket_a_ops = [
        BucketAOperatorCall(
            operator="INJECT_MAS_CDK",
            sat_name="sat_allocations",
            source_column="allocation_seq",
            params={"cdk_col": "allocation_seq"},
        ),
        BucketAOperatorCall(
            operator="WIDEN_TYPE_TO_DECIMAL",
            sat_name="sat_allocations",
            source_column="shares_allocated",
            params={"target_column": "shares_allocated", "precision": "28", "scale": "4"},
        ),
        BucketAOperatorCall(
            operator="SPLIT_HIGH_FREQ_OR_PII_SAT",
            sat_name="sat_allocations",
            target_hub="hub_block",
            params={
                "new_sat_name": "sat_allocations_fast",
                "split_reason": "VELOCITY",
                "columns_csv": "avg_price_dec",
            },
        ),
    ]
    h_bucket_a = DeterministicMockHarness(
        scripts_by_agent={
            "ship-vault": [w1_std_draft.model_copy(update={"wave_number": 2})],
            "dv-semantic-critic": [SemanticCriticVerdict(score=95, findings=[], accepted_waiver_ids=[])],
            "dv-horizon-critic": [
                HorizonCriticVerdict(
                    score=72,
                    classification="NEEDS_STEER",
                    absorbed_natively=["sat_*_ext_v2"],
                    bucket_a_ops=bucket_a_ops,
                ),
                HorizonCriticVerdict(
                    score=94,
                    classification="REASONABLY_STABLE",
                    bucket_c_watchlist=["Monitor fund_account distribution"],
                ),
            ],
        }
    )
    class _SchemaOnlyProver:
        def prove(
            self,
            certified_ir: Any,
            profiles: Any,
            *,
            policy: Any = "balanced",
            source_rows_by_table: Any = None,
            table_sample_flags: Any = None,
            real_row_proving: bool = True,
        ) -> Any:
            from opendv.models.diagnostics import IRProverReport

            _ = (certified_ir, profiles, policy, source_rows_by_table, table_sample_flags, real_row_proving)
            return IRProverReport(
                dyn_results={},
                ast_violations=[],
                empirical_smells=[],
                counterexamples=[],
            )

    res_a = await model_vault(
        DVModelingRequest(
            run_id="run-v8-a",
            project_id="proj_alloc",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            wave=WaveEvolutionInput(
                evolution_locks=w1_std_locks,
                prior_certified_ir=w1_std_cert,
            ),
            prover=_SchemaOnlyProver(),
            harness=h_bucket_a,
        )
    )
    assert res_a.status == "CERTIFIED"
    assert res_a.horizon_auto_steers_used == 1
    assert res_a.v8_calls_used == 2
    assert res_a.certified_ir is not None
    sat_names = {s.sat_name for s in res_a.certified_ir.satellites}
    assert "mas_allocations" in sat_names
    assert "sat_allocations_ext_v2" in sat_names

    # Case 2: Bucket B pauses immediately (0 retries) -> resume records HorizonAssumptionSpec (with DuckDbProverAdapter)
    q_b = HorizonQuestion(
        question_id="hq-brk02",
        brk_code="BRK-02",
        title="Custodian split legs",
        prompt="Can Wave 2 blocks split across multiple fund accounts?",
        options=[
            HorizonOptionSpec(
                option_id="allow_split",
                label="Allow multi-custodian splits",
                description="Preserve MAS allocation leg key",
                implications="Requires CDK",
            )
        ],
        impact_summary="Protects Link arity.",
    )
    cp_store = InMemoryRunCheckpointStore()
    h_bucket_b = DeterministicMockHarness(
        scripts_by_agent={
            "ship-vault": [w1_mas_draft.model_copy(update={"wave_number": 2})],
            "dv-semantic-critic": [SemanticCriticVerdict(score=94, findings=[], accepted_waiver_ids=[])],
            "dv-horizon-critic": [
                HorizonCriticVerdict(
                    score=84,
                    classification="HUMAN_REVIEW_REQUIRED",
                    bucket_b_questions=[q_b],
                )
            ],
        }
    )
    res_b = await model_vault(
        DVModelingRequest(
            run_id="run-v8-b",
            project_id="proj_alloc",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            wave=WaveEvolutionInput(
                evolution_locks=w1_mas_locks,
                prior_certified_ir=w1_mas_cert,
            ),
            prover=prover,
            harness=h_bucket_b,
            checkpoint_store=cp_store,
        )
    )
    assert res_b.status == "HOLD_CHECKPOINTED"
    assert res_b.horizon_auto_steers_used == 0
    assert res_b.v8_calls_used == 1
    assert res_b.checkpoint is not None

    # Resume Bucket B with Steward choice -> persists HorizonAssumptionSpec & fast-paths >= 85
    h_resume = DeterministicMockHarness(
        scripts_by_agent={
            "ship-vault": [w1_mas_draft.model_copy(update={"wave_number": 2})],
            "dv-semantic-critic": [SemanticCriticVerdict(score=95, findings=[], accepted_waiver_ids=[])],
            "dv-horizon-critic": [
                HorizonCriticVerdict(score=91, classification="REASONABLY_STABLE")
            ],
        }
    )
    res_b_done = await model_vault(
        DVModelingRequest(
            run_id="run-v8-b",
            project_id="proj_alloc",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            wave=WaveEvolutionInput(
                evolution_locks=w1_mas_locks,
                prior_certified_ir=w1_mas_cert,
            ),
            prover=prover,
            harness=h_resume,
            resume=ModelingResumeInput(
                checkpoint=res_b.checkpoint,
                hold_answers=HoldAnswersReq(answers={"hq-brk02": "allow_split"}),
            ),
        )
    )
    assert res_b_done.status == "CERTIFIED"
    assert len(res_b_done.horizon_assumptions) == 1
    assert res_b_done.horizon_assumptions[0].brk_code == "BRK-02"


@pytest.mark.asyncio
async def test_uat_v25_agt_09_scale_200_cluster_sharded_cegar() -> None:
    """UAT-V25-AGT-09: 220 tables (11 clusters, 1,760 cols) sharded merge + 7-tuple critic prompts."""
    tables = build_scale_200_table_profiles()
    assert len(tables) == 220
    cluster_res = cluster_tables(tables)
    assert len(cluster_res.clusters) == 11
    prof = SourceProfile(
        source_id="scale_200",
        schema_fingerprint="scale220",
        table_count=220,
        tables=tables,
    )
    clusters = [(f"cluster_{idx:02d}", list(c_tbls)) for idx, c_tbls in enumerate(cluster_res.clusters)]
    from tests.fixtures.scale_200 import SCALE_DOMAINS

    ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="enterprise_scale",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri=f"urn:opendv:scale:{domain.capitalize()}",
                concept_name=domain.capitalize(),
                description="Scale domain",
                key_authority_tier="TIER_3_LOCAL_SCOPED",
            )
            for domain in SCALE_DOMAINS
        ],
    )

    def _shard_script(req: ModelingTurnRequest) -> DraftShardIR:
        cid = req.cluster_id or "cluster_00"
        base_cid = cid.split("__part")[0]
        c_idx = int(base_cid.split("_")[-1])
        c_tids = set(clusters[c_idx][1])
        c_tables = [t for t in tables if t.table_id in c_tids or t.table_name in c_tids]
        prompt_obj = json.loads(req.prompt)
        shard_tids = {t["table_id"] for t in prompt_obj.get("tables", [])}
        shard_tables = [t for t in c_tables if t.table_id in shard_tids] or c_tables
        domain = shard_tables[0].table_name.split("_")[1]
        anchor = next(t for t in c_tables if t.table_name.endswith("_tbl_000"))
        has_anchor = any(t.table_id == anchor.table_id for t in shard_tables)
        triage = (
            []
            if req.attempt == 1
            else [
                ColumnTriageEntry(
                    table_id=t.table_id,
                    col_name=c.col_name,
                    disposition="MODELED",
                    target_ref=f"hub_{domain}",
                )
                for t in shard_tables
                for c in t.columns
            ]
        )
        return DraftShardIR(
            cluster_id=cid,
            hubs=(
                [
                    DraftHubSpec(
                        hub_name=f"hub_{domain}",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        business_keys=[
                            BusinessKeyPart(
                                table_id=anchor.table_id,
                                col_name=f"{domain}_id",
                                source_id="scale_200",
                                key_tier="TIER_3_LOCAL_SCOPED",
                                bkcc_value=domain.upper(),
                            )
                        ],
                        source_bindings=[
                            DraftSourceBinding(
                                source_id="scale_200",
                                table_id=anchor.table_id,
                                col_mappings={f"{domain}_id": f"{domain}_id"},
                            )
                        ],
                    )
                ]
                if has_anchor
                else []
            ),
            satellites=(
                [
                    DraftSatelliteSpec(
                        sat_name=f"sat_{domain}_core",
                        sat_kind="STANDARD",
                        parent_name=f"hub_{domain}",
                        source_bindings=[
                            DraftSourceBinding(
                                source_id="scale_200",
                                table_id=anchor.table_id,
                                col_mappings={"amount_dec": "amount_dec"},
                            )
                        ],
                        attributes=[
                            DraftField(
                                name="amount_dec",
                                logical_type=DecimalType(precision=18, scale=4),
                                nullable=True,
                                is_pii=False,
                            )
                        ],
                    )
                ]
                if has_anchor
                else []
            ),
            column_triage=triage,
        )

    def selector(req: ModelingTurnRequest) -> Any:
        if req.agent_id == "ship-vault":
            return _shard_script(req)
        if req.agent_id == "dv-semantic-critic":
            return SemanticCriticVerdict(score=95, findings=[], accepted_waiver_ids=[])
        return HorizonCriticVerdict(score=93, classification="REASONABLY_STABLE")

    harness = DeterministicMockHarness(selector=selector)
    res = await model_vault(
        DVModelingRequest(
            run_id="run-scale-200",
            project_id="proj_scale200",
            ontology=ont,
            profiles=[prof],
            clusters=clusters,
            policy="balanced",
            prover=DuckDbProverAdapter(),
            harness=harness,
        )
    )
    assert res.status == "CERTIFIED"
    assert res.attempts_used == 2
    assert res.certified_ir is not None
    assert len(res.certified_ir.hubs) == 11

    # Att 1 injected 1,760 UNRESOLVED triage rows failing DV-COL-001; Att 2 reaches 0 unaccounted
    assert any(d.rule_id == "DV-COL-001" for d in res.attempts[0].diagnostics)
    unresolved_att2 = [e for e in res.certified_ir.column_triage if e.disposition == "UNRESOLVED"]
    assert len(unresolved_att2) == 0

    # Assert <=150-col sharded turns (22 shards x 2 attempts = 44) + compact 7-tuple payload passed to V7/V8
    ship_reqs = [r for r in harness.requests if r.agent_id == "ship-vault"]
    assert len(ship_reqs) == 44
    assert ship_reqs[0].metadata["sharded"] is True
    assert len(ship_reqs[0].metadata["preallocated_hubs"]) == 11

    v7_req = next(r for r in harness.requests if r.agent_id == "dv-semantic-critic")
    v7_payload = json.loads(v7_req.prompt)
    g7 = v7_payload["graph_b_columns_7tuple"]
    if isinstance(g7, list):
        assert len(g7) == 1760
        assert len(g7[0]) == 7
    else:
        assert v7_payload.get("compaction_mode") == "wide_catalog_summary"
        assert len(g7["table_summaries"]) == 200
        assert sum(x[1] for x in g7["table_summaries"]) == 1600
        if g7["flagged_columns_7tuple"]:
            assert len(g7["flagged_columns_7tuple"][0]) == 7


@pytest.mark.asyncio
async def test_uat_v25_agt_10_multiwave_w1_to_w2_locks_otc_hold_and_proposals() -> None:
    """UAT-V25-AGT-10: W1 (am_sec_treasury_ibor.sqlite) -> W2 (custody/nav/otc) locks, hold, proposal."""
    w1_prof = profile_source("am_sqlite", "sqlite", str(PUBLIC_AM / "am_sec_treasury_ibor.sqlite"))
    w1_ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="asset_management",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am:Issuer",
                concept_name="Issuer",
                description="Security issuer",
                key_authority_tier="TIER_2_ENTERPRISE_SOR",
                bkcc_default="SEC_EDGAR",
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am:Security",
                concept_name="Security",
                description="Financial security",
                key_authority_tier="TIER_2_ENTERPRISE_SOR",
                bkcc_default="MARKET",
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am:IssuerLei",
                concept_name="IssuerLei",
                description="Legal Entity Identifier alias",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                bkcc_default="GLOBAL",
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am:SecurityIsin",
                concept_name="SecurityIsin",
                description="Security ISIN identifier",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                bkcc_default="GLOBAL",
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am:NavFund",
                concept_name="NavFund",
                description="Fund NAV valuation",
                key_authority_tier="TIER_2_ENTERPRISE_SOR",
                bkcc_default="FUND_ADMIN",
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am:OtcCollateral",
                concept_name="OtcCollateral",
                description="OTC collateral pledge",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                bkcc_default="GLOBAL",
            ),
        ],
    )
    iss_id = next(t.table_id for t in w1_prof.tables if t.table_name == "issuers")
    sec_id = next(t.table_id for t in w1_prof.tables if t.table_name == "securities")

    w1_draft = DraftVaultIR(
        project_id="proj_am_multi",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_issuer",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=iss_id,
                        col_name="cik",
                        source_id="am_sqlite",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="SEC_EDGAR",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="am_sqlite", table_id=iss_id, col_mappings={"cik": "cik"})
                ],
            ),
            DraftHubSpec(
                hub_name="hub_security",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=sec_id,
                        col_name="ticker",
                        source_id="am_sqlite",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="MARKET",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="am_sqlite", table_id=sec_id, col_mappings={"ticker": "ticker"})
                ],
            ),
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_issuers",
                sat_kind="STANDARD",
                parent_name="hub_issuer",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="am_sqlite",
                        table_id=iss_id,
                        col_mappings={"company_name": "company_name"},
                    )
                ],
                attributes=[
                    DraftField(name="company_name", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                ],
            ),
            DraftSatelliteSpec(
                sat_name="sat_securities",
                sat_kind="STANDARD",
                parent_name="hub_security",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="am_sqlite",
                        table_id=sec_id,
                        col_mappings={"security_type": "security_type"},
                    )
                ],
                attributes=[
                    DraftField(name="security_type", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                ],
            )
        ],
        column_triage=_triage_all([w1_prof]),
    )
    prover = DuckDbProverAdapter()
    w1_res = await model_vault(
        DVModelingRequest(
            run_id="run-w1",
            project_id="proj_am_multi",
            ontology=w1_ont,
            profiles=[w1_prof],
            policy="balanced",
            prover=prover,
            harness=DeterministicMockHarness(
                scripts_by_agent={
                    "ship-vault": [w1_draft],
                    "dv-semantic-critic": [SemanticCriticVerdict(score=95)],
                    "dv-horizon-critic": [HorizonCriticVerdict(score=92, classification="REASONABLY_STABLE")],
                }
            ),
        )
    )
    assert w1_res.status == "CERTIFIED"
    assert w1_res.certified_ir is not None and w1_res.evolution_lock is not None

    # Wave 2 attaches EU custody parquet + bitemporal NAV + ambiguous OTC collateral
    w2_eu = profile_source("w2_eu", "parquet", str(PUBLIC_AM / "wave2_eu_custody_lei_and_drift.parquet"))
    w2_nav = profile_source("w2_nav", "csv", str(PUBLIC_AM / "wave2_bitemporal_nav_restatements.csv"))
    w2_otc = profile_source("w2_otc", "csv", str(PUBLIC_AM / "wave2_ambiguous_otc_collateral.csv"))
    w2_profiles = [w1_prof, w2_eu, w2_nav, w2_otc]

    eu_id = w2_eu.tables[0].table_id
    nav_id = w2_nav.tables[0].table_id
    otc_id = w2_otc.tables[0].table_id

    otc_q = ClarificationQuestion(
        question_id="q-otc-nk",
        concept_name="OtcCollateral",
        title="Select natural key",
        prompt="Choose between uti_id and isda_master_id",
        sample_rows=["uti_id=UTI-1, isda_master_id=ISDA-1"],
        impact_if_locked="Locks Wave 2 OTC Hub BK.",
        recommended_answer="uti_id",
    )

    w2_hold_draft = w1_draft.model_copy(
        update={"wave_number": 2, "pending_clarifications": [otc_q], "column_triage": _triage_all(w2_profiles)}
    )
    cp_store = InMemoryRunCheckpointStore()
    w2_hold_res = await model_vault(
        DVModelingRequest(
            run_id="run-w2",
            project_id="proj_am_multi",
            ontology=w1_ont,
            profiles=w2_profiles,
            policy="balanced",
            wave=WaveEvolutionInput(
                evolution_locks=w1_res.evolution_lock,
                prior_certified_ir=w1_res.certified_ir,
            ),
            prover=prover,
            checkpoint_store=cp_store,
            harness=DeterministicMockHarness(scripts_by_agent={"ship-vault": [w2_hold_draft]}),
        )
    )
    assert w2_hold_res.status == "HOLD_CHECKPOINTED"
    assert w2_hold_res.checkpoint is not None
    assert w2_hold_res.checkpoint.pending_questions[0].question_id == "q-otc-nk"

    # Resume Wave 2 with uti_id answer -> emits sal_issuer_lei, sat_securities_ext_v2, OntologyProposalSpec
    w2_clean_draft = DraftVaultIR(
        project_id="proj_am_multi",
        ontology_version="1.0.0",
        wave_number=2,
        hubs=[
            *w1_draft.hubs,
            DraftHubSpec(
                hub_name="hub_issuer_lei",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id=eu_id,
                        col_name="lei_code",
                        source_id="w2_eu",
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLEIF",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="w2_eu", table_id=eu_id, col_mappings={"lei_code": "lei_code"})
                ],
            ),
            DraftHubSpec(
                hub_name="hub_security_isin",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id=eu_id,
                        col_name="isin",
                        source_id="w2_eu",
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="w2_eu", table_id=eu_id, col_mappings={"isin": "isin"})
                ],
            ),
            DraftHubSpec(
                hub_name="hub_nav_fund",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=nav_id,
                        col_name="fund_code",
                        source_id="w2_nav",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="FUND_ADMIN",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="w2_nav", table_id=nav_id, col_mappings={"fund_code": "fund_code"})
                ],
            ),
            DraftHubSpec(
                hub_name="hub_otc_collateral",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id=otc_id,
                        col_name="uti_id",
                        source_id="w2_otc",
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="EMIR",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="w2_otc", table_id=otc_id, col_mappings={"uti_id": "uti_id"})
                ],
            ),
        ],
        links=[
            DraftLinkSpec(
                link_name="sal_issuer_lei",
                link_kind="SAME_AS",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_security_isin",
                        role_name="PRIMARY",
                        fk_col_name="hk_security_isin",
                        source_col_names=["isin"],
                    ),
                    LinkParticipantSpec(
                        hub_name="hub_issuer_lei",
                        role_name="ALIAS",
                        fk_col_name="hk_issuer_lei",
                        source_col_names=["lei_code"],
                    ),
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="w2_eu",
                        table_id=eu_id,
                        col_mappings={"isin": "isin", "lei_code": "lei_code"},
                    )
                ],
            )
        ],
        satellites=[
            *w1_draft.satellites,
            DraftSatelliteSpec(
                sat_name="sat_securities_ext_v2",
                sat_kind="EXT",
                parent_name="hub_security",
                extends_sat_name="sat_securities",
                companion_epoch=2,
                source_bindings=[
                    DraftSourceBinding(
                        source_id="am_sqlite",
                        table_id=sec_id,
                        col_mappings={"cusip": "cusip"},
                    )
                ],
                attributes=[
                    DraftField(name="cusip", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                ],
            ),
            DraftSatelliteSpec(
                sat_name="sat_nav_bitemp",
                sat_kind="STANDARD",
                parent_name="hub_nav_fund",
                is_bitemporal=True,
                applied_dts_col="applied_dts",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="w2_nav",
                        table_id=nav_id,
                        col_mappings={"nav_per_share": "nav_per_share"},
                    )
                ],
                attributes=[
                    DraftField(
                        name="nav_per_share",
                        logical_type=DecimalType(precision=18, scale=4),
                        nullable=True,
                        is_pii=False,
                    )
                ],
            ),
            DraftSatelliteSpec(
                sat_name="sat_otc_collateral",
                sat_kind="STANDARD",
                parent_name="hub_otc_collateral",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="w2_otc",
                        table_id=otc_id,
                        col_mappings={"pledge_amount": "pledge_amount"},
                    )
                ],
                attributes=[
                    DraftField(
                        name="pledge_amount",
                        logical_type=DecimalType(precision=18, scale=4),
                        nullable=True,
                        is_pii=False,
                    )
                ],
            ),
        ],
        column_triage=_triage_all(w2_profiles),
    )

    concept_count_before = len(w1_ont.concepts)
    w2_prop = {
        "proposal_id": "prop-w2-otc",
        "summary": "Propose OtcCollateral concept from Wave 2 collateral feed",
        "delta_ops": [
            AddConcept(
                concept_uri="urn:opendv:am:CustodyAccount",
                concept_name="CustodyAccount",
                description="EU custody account",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                attributes=[
                    ConceptAttribute(
                        name="custody_acct_id",
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
    w2_res = await model_vault(
        DVModelingRequest(
            run_id="run-w2",
            project_id="proj_am_multi",
            ontology=w1_ont,
            profiles=w2_profiles,
            policy="balanced",
            wave=WaveEvolutionInput(
                evolution_locks=w1_res.evolution_lock,
                prior_certified_ir=w1_res.certified_ir,
            ),
            prover=prover,
            checkpoint_store=cp_store,
            resume=ModelingResumeInput(
                checkpoint=w2_hold_res.checkpoint,
                hold_answers=HoldAnswersReq(answers={"q-otc-nk": "uti_id"}),
            ),
            harness=DeterministicMockHarness(
                scripts_by_agent={
                    "ship-vault": [
                        MockTurnScript(
                            structured_output=w2_clean_draft,
                            tool_events=[{"ontology_proposals": [w2_prop]}],
                        )
                    ],
                    "dv-semantic-critic": [SemanticCriticVerdict(score=95)],
                    "dv-horizon-critic": [HorizonCriticVerdict(score=93, classification="REASONABLY_STABLE")],
                }
            ),
        )
    )
    assert w2_res.status == "CERTIFIED"
    assert w2_res.certified_ir is not None
    assert w2_res.certified_ir.wave_delta is not None
    assert w2_res.certified_ir.wave_delta.from_wave == 1
    assert w2_res.certified_ir.wave_delta.to_wave == 2
    assert "sat_securities_ext_v2" in w2_res.certified_ir.wave_delta.added_sat_extensions
    assert "sal_issuer_lei" in w2_res.certified_ir.wave_delta.added_entities
    # Committed Graph A untouched; suggestion captured as PENDING_STEWARD proposal
    assert len(w1_ont.concepts) == concept_count_before
    assert len(w2_res.ontology_proposals) == 1
    assert w2_res.ontology_proposals[0].status == "PENDING_STEWARD"

    # WaveMigrator parity check
    migration_sqls = WaveMigrator().compile_migration(
        w1_res.certified_ir,
        w2_res.certified_ir,
        "duckdb_local",
    )
    assert migration_sqls


@pytest.mark.asyncio
async def test_cegar_v8_low_score_pause_v7_critique_and_model_override_forwarding() -> None:
    """Regression: V8 < 85 pauses for Steward review, V7 critique feeds Attempt k+1, overrides forward."""
    prof = profile_source("trap_db", "duckdb", str(PUBLIC_AM / "ds_cegar_12trap.duckdb"))
    profiles = [prof]
    ont = _build_12trap_ontology()
    prover = DuckDbProverAdapter()
    mgr_id = next(t.table_id for t in prof.tables if t.table_name == "trap_manager_eff")
    clean_draft = _build_12trap_draft(profiles, defective=False)

    # 1. V7 fails floor (80 < 90) on Attempt 1 -> Attempt 2 prompt receives prior_v7_verdict,
    #    V7 reported tokens promote tok_source, model_overrides populate AgentTurnRequest.model_override,
    #    and V8 < 85 (empty Bucket A/B) pauses at S6-06 with Accept/Steer question.
    h = DeterministicMockHarness(
        scripts_by_agent={
            "ship-vault": [
                MockTurnScript(structured_output=clean_draft, token_count_source="estimated"),
                MockTurnScript(structured_output=clean_draft, token_count_source="estimated"),
            ],
            "dv-semantic-critic": [
                MockTurnScript(
                    structured_output=SemanticCriticVerdict(
                        score=80,
                        findings=["Missing Link lnk_foo"],
                        accepted_waiver_ids=[],
                    ),
                    token_count_source="reported",
                ),
                MockTurnScript(
                    structured_output=SemanticCriticVerdict(
                        score=94,
                        findings=[],
                        accepted_waiver_ids=[],
                    ),
                    token_count_source="reported",
                ),
            ],
            "dv-horizon-critic": [
                HorizonCriticVerdict(
                    score=75,
                    classification="REASONABLY_STABLE",
                    bucket_a_ops=[],
                    bucket_b_questions=[],
                )
            ],
        }
    )
    res_hold = await model_vault(
        DVModelingRequest(
            run_id="run-v7-v8-reg",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            verifier_ctx=VerifierContext(
                profiles=profiles,
                fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            ),
            prover=prover,
            harness=h,
            model_overrides={
                "ship-vault": "ovr-ship",
                "dv-semantic-critic": "ovr-v7",
                "dv-horizon-critic": "ovr-v8",
            },
        )
    )
    assert res_hold.status == "HOLD_CHECKPOINTED"
    assert res_hold.checkpoint is not None
    assert res_hold.checkpoint.phase == "S6-06"
    assert res_hold.v8_verdict is not None
    assert res_hold.v8_verdict.classification == "HUMAN_REVIEW_REQUIRED"
    assert res_hold.checkpoint.pending_questions[0].question_id == "hq-v8-low-score"
    assert [opt.option_id for opt in res_hold.checkpoint.pending_questions[0].options] == [
        "accept",
        "steer",
    ]
    assert res_hold.token_count_source == "reported"

    ship_reqs = [r for r in h.requests if r.agent_id == "ship-vault"]
    assert len(ship_reqs) == 2
    assert ship_reqs[0].model_override == "ovr-ship"
    att2_payload = json.loads(ship_reqs[1].prompt)
    assert att2_payload["prior_v7_verdict"] == {
        "score": 80,
        "findings": ["Missing Link lnk_foo"],
        "accepted_waiver_ids": [],
    }
    assert next(r for r in h.requests if r.agent_id == "dv-semantic-critic").model_override == "ovr-v7"
    assert next(r for r in h.requests if r.agent_id == "dv-horizon-critic").model_override == "ovr-v8"

    # Resume with "accept" -> seals CERTIFIED without infinite low-score hold
    res_accepted = await model_vault(
        DVModelingRequest(
            run_id="run-v7-v8-reg",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            verifier_ctx=VerifierContext(
                profiles=profiles,
                fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            ),
            prover=prover,
            resume=ModelingResumeInput(
                checkpoint=res_hold.checkpoint,
                hold_answers=HoldAnswersReq(answers={"hq-v8-low-score": "accept"}),
                resumed_draft_ir=res_hold.draft_ir,
                resumed_certified_ir=res_hold.certified_ir,
            ),
            harness=DeterministicMockHarness(
                scripts_by_agent={
                    "dv-horizon-critic": [
                        HorizonCriticVerdict(score=75, classification="REASONABLY_STABLE")
                    ]
                }
            ),
        )
    )
    assert res_accepted.status == "CERTIFIED"
    assert any(a.assumption_id == "ha-hq-v8-low-score" and a.steward_decision == "ACCEPT" for a in res_accepted.horizon_assumptions)

    # 2. Post-steer V8 < 85 (with empty Bucket A/B) also pauses at S6-06
    alloc_prof = profile_source("src_alloc", "csv", str(PUBLIC_AM / "prime_allocations_w2.csv"))
    tbl_id = alloc_prof.tables[0].table_id
    alloc_ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="asset_management",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am:Block",
                concept_name="Block",
                description="Block",
                key_authority_tier="TIER_2_ENTERPRISE_SOR",
                bkcc_default="OMS",
            )
        ],
    )
    alloc_draft = DraftVaultIR(
        project_id="proj_post_steer",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_block",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=tbl_id,
                        col_name="block_id",
                        source_id="src_alloc",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="OMS",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_alloc", table_id=tbl_id, col_mappings={"block_id": "block_id"}
                    )
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="mas_allocations",
                sat_kind="MAS",
                parent_name="hub_block",
                child_dependent_keys=["allocation_seq"],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_alloc",
                        table_id=tbl_id,
                        col_mappings={"allocation_seq": "allocation_seq", "shares_allocated": "shares_allocated"},
                    )
                ],
                attributes=[
                    DraftField(name="allocation_seq", logical_type="INT32", nullable=False, is_pii=False),
                    DraftField(name="shares_allocated", logical_type="INT32", nullable=False, is_pii=False),
                ],
            )
        ],
        column_triage=_triage_all([alloc_prof]),
    )
    post_steer_res = await model_vault(
        DVModelingRequest(
            run_id="run-post-steer-low",
            project_id="proj_post_steer",
            ontology=alloc_ont,
            profiles=[alloc_prof],
            policy="balanced",
            prover=prover,
            harness=DeterministicMockHarness(
                scripts_by_agent={
                    "ship-vault": [alloc_draft],
                    "dv-semantic-critic": [SemanticCriticVerdict(score=95)],
                    "dv-horizon-critic": [
                        HorizonCriticVerdict(
                            score=70,
                            classification="NEEDS_STEER",
                            bucket_a_ops=[
                                BucketAOperatorCall(
                                    operator="WIDEN_TYPE_TO_DECIMAL",
                                    sat_name="mas_allocations",
                                    source_column="shares_allocated",
                                    params={"target_column": "shares_allocated", "precision": "28", "scale": "4"},
                                )
                            ],
                        ),
                        HorizonCriticVerdict(score=80, classification="REASONABLY_STABLE"),
                    ],
                }
            ),
        )
    )
    assert post_steer_res.status == "HOLD_CHECKPOINTED"
    assert post_steer_res.horizon_auto_steers_used == 1
    assert post_steer_res.checkpoint is not None
    assert post_steer_res.checkpoint.pending_questions[0].question_id == "hq-v8-low-score"


@pytest.mark.asyncio
async def test_cegar_strict_bucket_a_waiver_preservation_and_v6b_regression_ratchet() -> None:
    """Regression: Bucket A preserves V7-reviewed waivers and strict V6b regression triggers ratchet."""
    prof = profile_source("trap_db", "duckdb", str(PUBLIC_AM / "ds_cegar_12trap.duckdb"))
    profiles = [prof]
    ont = _build_12trap_ontology()
    prover = DuckDbProverAdapter()
    mgr_id = next(t.table_id for t in prof.tables if t.table_name == "trap_manager_eff")
    clean_draft = _build_12trap_draft(profiles, defective=False)

    waived_draft = clean_draft.model_copy(
        update={
            "justifications": [
                AntiPatternJustification(
                    waiver_id="WAIV-c981d86d",
                    rule_id="DV-AP-004",
                    entity_name="hub_broker",
                    rationale="Sparse optional broker LEI modeled with IS_NOT_NULL filter.",
                    actor="agent",
                    reviewed_by_v7=False,
                )
            ]
        }
    )
    res_ok = await model_vault(
        DVModelingRequest(
            run_id="run-strict-ba-ok",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="strict",
            verifier_ctx=VerifierContext(
                profiles=profiles,
                fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            ),
            prover=prover,
            harness=DeterministicMockHarness(
                scripts_by_agent={
                    "ship-vault": [waived_draft],
                    "dv-semantic-critic": [
                        SemanticCriticVerdict(score=96, accepted_waiver_ids=["WAIV-c981d86d"])
                    ],
                    "dv-horizon-critic": [
                        HorizonCriticVerdict(
                            score=78,
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
                        HorizonCriticVerdict(score=94, classification="REASONABLY_STABLE"),
                    ],
                }
            ),
        )
    )
    assert res_ok.status == "CERTIFIED"
    assert res_ok.verifier_report is not None
    assert res_ok.verifier_report.v6b_fail_count == 0
    assert res_ok.certified_ir is not None and res_ok.certified_ir.justifications[0].reviewed_by_v7 is True
    assert res_ok.draft_ir is not None and res_ok.draft_ir.justifications[0].reviewed_by_v7 is True

    # Strict V6b regression during Bucket A (promoting into hub_crm_owner triggers DV-AP-001 with V1-V6=0)
    crm_id = next(t.table_id for t in prof.tables if t.table_name == "trap_crm_accounts")
    ont_with_crm = ont.model_copy(
        update={
            "concepts": [
                *ont.concepts,
                CanonicalBusinessConcept(
                    concept_uri="urn:opendv:am:CrmOwner",
                    concept_name="CrmOwner",
                    description="CRM beneficial owner",
                    key_authority_tier="TIER_3_LOCAL_SCOPED",
                    bkcc_default="CRM",
                ),
            ]
        }
    )
    strict_reg_res = await model_vault(
        DVModelingRequest(
            run_id="run-strict-ba-reg",
            project_id="proj_12trap",
            ontology=ont_with_crm,
            profiles=profiles,
            policy="strict",
            verifier_ctx=VerifierContext(
                profiles=profiles,
                fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            ),
            prover=prover,
            harness=DeterministicMockHarness(
                scripts_by_agent={
                    "ship-vault": [waived_draft],
                    "dv-semantic-critic": [
                        SemanticCriticVerdict(score=96, accepted_waiver_ids=["WAIV-c981d86d"])
                    ],
                    "dv-horizon-critic": [
                        HorizonCriticVerdict(
                            score=78,
                            classification="NEEDS_STEER",
                            bucket_a_ops=[
                                BucketAOperatorCall(
                                    operator="PROMOTE_COLUMN_TO_HUB",
                                    sat_name="sat_account_crm",
                                    table_id=crm_id,
                                    source_column="beneficial_owner_tax_id",
                                    params={
                                        "new_hub_name": "hub_crm_owner",
                                        "new_link_name": "lnk_account_crm_owner",
                                    },
                                )
                            ],
                        ),
                    ],
                }
            ),
        )
    )
    assert strict_reg_res.status == "HOLD_CHECKPOINTED"
    assert strict_reg_res.horizon_auto_steers_used == 0
    assert strict_reg_res.checkpoint is not None
    assert strict_reg_res.checkpoint.pending_questions[0].question_id == "hq-v8-regression"


@pytest.mark.asyncio
async def test_v8_horizon_review_guards_seq_wave_delta_fast_resume_watchlist_and_shard_merge() -> None:
    """Regression: V8 review guards, seq continuity, wave_delta refresh, fast S6-06 resume, watchlist, shards."""
    alloc_prof = profile_source("src_alloc", "csv", str(PUBLIC_AM / "prime_allocations_w2.csv"))
    tbl_id = alloc_prof.tables[0].table_id
    prover = DuckDbProverAdapter()
    ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="asset_management",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am:Block",
                concept_name="Block",
                description="Block",
                key_authority_tier="TIER_2_ENTERPRISE_SOR",
                bkcc_default="OMS",
            )
        ],
    )
    w1_draft = DraftVaultIR(
        project_id="proj_hzn_rev",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_block",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=tbl_id,
                        col_name="block_id",
                        source_id="src_alloc",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="OMS",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="src_alloc", table_id=tbl_id, col_mappings={"block_id": "block_id"})
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="mas_allocations",
                sat_kind="MAS",
                parent_name="hub_block",
                child_dependent_keys=["allocation_seq"],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_alloc",
                        table_id=tbl_id,
                        col_mappings={"allocation_seq": "allocation_seq", "shares_allocated": "shares_allocated"},
                    )
                ],
                attributes=[
                    DraftField(name="allocation_seq", logical_type="INT32", nullable=False, is_pii=False),
                    DraftField(name="shares_allocated", logical_type="INT32", nullable=False, is_pii=False),
                ],
            )
        ],
        column_triage=_triage_all([alloc_prof]),
    )
    _, w1_cert, _ = run_l1_codemods(w1_draft, profiles=[alloc_prof])
    w1_locks = build_evolution_lock_from_certified(w1_cert)

    # 1. Watchlist glob/regex filtering in run_v8_horizon_critic
    verdict_w, _ = await run_v8_horizon_critic(
        run_id="r-wl",
        ir=w1_cert,
        profiles=[alloc_prof],
        locks=None,
        horizon_assumptions=[],
        policy="balanced",
        budget=HarnessBudget(max_turns=2, max_tokens=5000, timeout_seconds=30),
        harness=DeterministicMockHarness(
            scripts_by_agent={
                "dv-horizon-critic": [
                    HorizonCriticVerdict(
                        score=91,
                        classification="REASONABLY_STABLE",
                        absorbed_natively=["mas_allocations"],
                        bucket_c_watchlist=[
                            "Monitor sat_securities_ext_v2 growth",
                            "Check sal_issuer_lei mapping",
                            "Watch sat_ballot_pii encryption",
                            "ABSORBED_NATIVELY: extra sat",
                            "EVO-02 soft-null handled",
                            "Review mas_allocations retention",
                        "Genuine long-term risk: currency aggregation",
                        ],
                    )
                ]
            }
        ),
    )
    assert verdict_w.bucket_c_watchlist == ["Genuine long-term risk: currency aggregation"]

    # 2. Guard execute_v8_horizon_review against UNCERTIFIED_DRAFT
    broken_draft = w1_draft.model_copy(update={"column_triage": []})
    uncert_req = DVModelingRequest(
        run_id="r-uncert",
        project_id="proj_hzn_rev",
        ontology=ont,
        profiles=[alloc_prof],
        policy="symbolic_only",
        prover=prover,
        harness=DeterministicMockHarness(scripts_by_agent={"ship-vault": [broken_draft]}),
    )
    uncert_res = await model_vault(uncert_req)
    assert uncert_res.status == "UNCERTIFIED_DRAFT"
    with pytest.raises(OpenDVError) as exc_uncert:
        await run_v8_horizon_review(uncert_req, uncert_res)
    assert exc_uncert.value.error_code == ErrorCode.VERIFICATION_GATE_FAILED

    # 3. Wave 2 CERTIFIED_FAST -> run_v8_horizon_review: monotonic seq, Bucket A wave_delta, S6-07 + RUN_COMPLETED
    sink_rev = InMemoryRunEventSink()
    fast_w2_req = DVModelingRequest(
        run_id="r-fast-w2",
        project_id="proj_hzn_rev",
        ontology=ont,
        profiles=[alloc_prof],
        policy="fast",
        wave=WaveEvolutionInput(
            evolution_locks=w1_locks,
            prior_certified_ir=w1_cert,
        ),
        prover=prover,
        event_sink=sink_rev,
        harness=DeterministicMockHarness(
            scripts_by_agent={"ship-vault": [w1_draft.model_copy(update={"wave_number": 2})]}
        ),
    )
    fast_w2_res = await model_vault(fast_w2_req)
    assert fast_w2_res.status == "CERTIFIED_FAST"
    prior_max_seq = sink_rev.events[-1].seq
    assert fast_w2_res.last_seq == prior_max_seq

    rev_harness = DeterministicMockHarness(
        scripts_by_agent={
            "dv-horizon-critic": [
                HorizonCriticVerdict(
                    score=76,
                    classification="NEEDS_STEER",
                    bucket_a_ops=[
                        BucketAOperatorCall(
                            operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                            target_hub="hub_block",
                            table_id=tbl_id,
                            source_column="block_id",
                            params={"new_bk_col": "fund_account", "source_id": "src_alloc"},
                        )
                    ],
                ),
                HorizonCriticVerdict(score=93, classification="REASONABLY_STABLE"),
            ]
        }
    )
    upgraded = await run_v8_horizon_review(
        fast_w2_req.model_copy(update={"harness": rev_harness}),
        fast_w2_res,
    )
    assert upgraded.status == "CERTIFIED"
    assert upgraded.certified_ir is not None and upgraded.certified_ir.wave_delta is not None
    assert "hub_block_canonical" in upgraded.certified_ir.wave_delta.added_entities
    assert "sal_block" in upgraded.certified_ir.wave_delta.added_entities
    all_seqs = [ev.seq for ev in sink_rev.events]
    assert all_seqs == sorted(all_seqs) and len(all_seqs) == len(set(all_seqs))
    post_events = [ev for ev in sink_rev.events if ev.seq > prior_max_seq]
    assert any(
        ev.event_type == "STAGE_UPDATE" and ev.payload.get("stage_id") == "S6-07"
        for ev in post_events
    )
    assert any(ev.event_type == "RUN_COMPLETED" for ev in post_events)

    # 4. policy="fast" + horizon-review hitting Bucket B hold -> S6-06 resume preserves horizon_review_requested
    #    and seeds best from resumed_certified_ir/resumed_draft_ir even at tight budget
    q_b = HorizonQuestion(
        question_id="hq-fast-b",
        brk_code="BRK-02",
        title="Custodian key choice",
        prompt="Confirm block split behavior",
        options=[
            HorizonOptionSpec(
                option_id="allow_split",
                label="Allow split",
                description="Allow multi-custodian leg",
                implications="Keeps MAS CDK",
            )
        ],
        impact_summary="Preserves key.",
    )
    ctrl_fast = InMemoryControlSignalReader(
        [
            ControlSignalRecord(
                signal_id="sig-hr-1",
                run_id="r-fast-hold",
                action="horizon-review",
                created_at="2026-11-23T00:00:00Z",
            )
        ]
    )
    fast_hold = await model_vault(
        DVModelingRequest(
            run_id="r-fast-hold",
            project_id="proj_hzn_rev",
            ontology=ont,
            profiles=[alloc_prof],
            policy="fast",
            budget=HarnessBudget(max_turns=2, max_tokens=50000, timeout_seconds=30),
            prover=prover,
            control_reader=ctrl_fast,
            harness=DeterministicMockHarness(
                scripts_by_agent={
                    "ship-vault": [w1_draft],
                    "dv-horizon-critic": [
                        HorizonCriticVerdict(
                            score=82,
                            classification="HUMAN_REVIEW_REQUIRED",
                            bucket_b_questions=[q_b],
                        )
                    ],
                }
            ),
        )
    )
    assert fast_hold.status == "HOLD_CHECKPOINTED"
    assert fast_hold.checkpoint is not None and fast_hold.checkpoint.phase == "S6-06"
    assert fast_hold.checkpoint.resume_payload.get("horizon_review_requested") is True

    fast_resumed = await model_vault(
        DVModelingRequest(
            run_id="r-fast-hold",
            project_id="proj_hzn_rev",
            ontology=ont,
            profiles=[alloc_prof],
            policy="fast",
            budget=HarnessBudget(max_turns=2, max_tokens=50000, timeout_seconds=30),
            prover=prover,
            resume=ModelingResumeInput(
                checkpoint=fast_hold.checkpoint,
                hold_answers=HoldAnswersReq(answers={"hq-fast-b": "allow_split"}),
                resumed_draft_ir=fast_hold.draft_ir,
                resumed_certified_ir=fast_hold.certified_ir,
            ),
            harness=DeterministicMockHarness(
                scripts_by_agent={
                    "dv-horizon-critic": [
                        HorizonCriticVerdict(score=92, classification="REASONABLY_STABLE")
                    ]
                }
            ),
        )
    )
    assert fast_resumed.status == "CERTIFIED"
    assert fast_resumed.v8_verdict is not None
    assert fast_resumed.v8_verdict.score == 92

    # 5. merge_draft_shards preserves satellite metadata across shards sharing sat_name
    shard_a = DraftShardIR(
        cluster_id="c0",
        satellites=[
            DraftSatelliteSpec(
                sat_name="mas_allocations",
                sat_kind="STANDARD",
                parent_name="hub_block",
                source_bindings=[DraftSourceBinding(source_id="s1", table_id="t1", col_mappings={"a": "a"})],
                attributes=[DraftField(name="a", logical_type="INT32", nullable=True, is_pii=False)],
            )
        ],
    )
    shard_b = DraftShardIR(
        cluster_id="c1",
        satellites=[
            DraftSatelliteSpec(
                sat_name="mas_allocations",
                sat_kind="MAS",
                parent_name="hub_block",
                extends_sat_name="sat_allocations",
                companion_epoch=2,
                is_bitemporal=True,
                applied_dts_col="applied_dts",
                child_dependent_keys=["allocation_seq"],
                driving_keys=["hk_block"],
                source_bindings=[DraftSourceBinding(source_id="s1", table_id="t2", col_mappings={"b": "b"})],
                attributes=[DraftField(name="b", logical_type="INT32", nullable=False, is_pii=False)],
            )
        ],
    )
    merged = merge_draft_shards(
        [shard_a, shard_b],
        project_id="proj_shard",
        ontology_version="1.0.0",
        wave_number=2,
    )
    assert len(merged.satellites) == 1
    msat = merged.satellites[0]
    assert msat.sat_kind == "MAS"
    assert msat.child_dependent_keys == ["allocation_seq"]
    assert msat.driving_keys == ["hk_block"]
    assert msat.is_bitemporal is True
    assert msat.applied_dts_col == "applied_dts"
    assert msat.extends_sat_name == "sat_allocations"
    assert msat.companion_epoch == 2


@pytest.mark.asyncio
async def test_m1_remediation_monotonic_seq_steer_accept_multitable_hub_and_waiver_forgery() -> None:
    """Regression: monotonic seq across S6-05/S6-06 holds, STEER->ACCEPT, unlocked multi-table hub, V7 forgery."""
    prof = profile_source("trap_db", "duckdb", str(PUBLIC_AM / "ds_cegar_12trap.duckdb"))
    profiles = [prof]
    ont = _build_12trap_ontology()
    prover = DuckDbProverAdapter()
    mgr_id = next(t.table_id for t in prof.tables if t.table_name == "trap_manager_eff")
    ibor_id = next(t.table_id for t in prof.tables if t.table_name == "trap_ibor_accounts")
    clean_draft = _build_12trap_draft(profiles, defective=False)

    # 1. Strict monotonic 1..N RunStreamEvent.seq across S6-05 hold + resume
    clar_q = ClarificationQuestion(
        question_id="q-s605-seq",
        concept_name="Account",
        title="Confirm BKCC",
        prompt="Select account BKCC",
        sample_rows=["account_id=A1"],
        impact_if_locked="Locks BKCC",
    )
    draft_s605 = clean_draft.model_copy(update={"pending_clarifications": [clar_q]})
    sink_s605 = InMemoryRunEventSink()
    hold_s605 = await model_vault(
        DVModelingRequest(
            run_id="r-seq-s605",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="fast",
            verifier_ctx=VerifierContext(
                profiles=profiles,
                fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            ),
            prover=prover,
            event_sink=sink_s605,
            harness=DeterministicMockHarness(scripts_by_agent={"ship-vault": [draft_s605]}),
        )
    )
    assert hold_s605.status == "HOLD_CHECKPOINTED"
    assert hold_s605.checkpoint is not None
    hold_ev_s605 = next(ev for ev in sink_s605.events if ev.event_type == "HOLD_CHECKPOINTED")
    assert hold_s605.checkpoint.last_seq == hold_ev_s605.seq

    res_s605 = await model_vault(
        DVModelingRequest(
            run_id="r-seq-s605",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="fast",
            verifier_ctx=VerifierContext(
                profiles=profiles,
                fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            ),
            prover=prover,
            event_sink=sink_s605,
            resume=ModelingResumeInput(
                checkpoint=hold_s605.checkpoint,
                hold_answers=HoldAnswersReq(answers={"q-s605-seq": "CRM"}),
            ),
            harness=DeterministicMockHarness(scripts_by_agent={"ship-vault": [draft_s605]}),
        )
    )
    assert res_s605.status == "CERTIFIED_FAST"
    seqs_s605 = [ev.seq for ev in sink_s605.events]
    assert seqs_s605 == list(range(1, len(seqs_s605) + 1))

    # 2. Strict monotonic 1..N across S6-06 STEER -> ACCEPT (fixes STEER->ACCEPT lockout too)
    sink_s606 = InMemoryRunEventSink()
    hold_low_1 = await model_vault(
        DVModelingRequest(
            run_id="r-seq-s606-steer-accept",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            verifier_ctx=VerifierContext(
                profiles=profiles,
                fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            ),
            prover=prover,
            event_sink=sink_s606,
            harness=DeterministicMockHarness(
                scripts_by_agent={
                    "ship-vault": [clean_draft],
                    "dv-semantic-critic": [SemanticCriticVerdict(score=95)],
                    "dv-horizon-critic": [HorizonCriticVerdict(score=75, classification="REASONABLY_STABLE")],
                }
            ),
        )
    )
    assert hold_low_1.status == "HOLD_CHECKPOINTED"
    assert hold_low_1.checkpoint is not None
    assert hold_low_1.checkpoint.last_seq == sink_s606.events[-1].seq

    # Answer "steer" first -> remains HOLD_CHECKPOINTED with STEER assumption
    hold_low_2 = await model_vault(
        DVModelingRequest(
            run_id="r-seq-s606-steer-accept",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            verifier_ctx=VerifierContext(
                profiles=profiles,
                fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            ),
            prover=prover,
            event_sink=sink_s606,
            horizon_assumptions=hold_low_1.horizon_assumptions,
            resume=ModelingResumeInput(
                checkpoint=hold_low_1.checkpoint,
                hold_answers=HoldAnswersReq(answers={"hq-v8-low-score": "steer"}),
                resumed_draft_ir=hold_low_1.draft_ir,
                resumed_certified_ir=hold_low_1.certified_ir,
            ),
            harness=DeterministicMockHarness(
                scripts_by_agent={
                    "ship-vault": [clean_draft],
                    "dv-semantic-critic": [
                        SemanticCriticVerdict(score=95, findings=[], accepted_waiver_ids=[])
                    ],
                    "dv-horizon-critic": [
                        HorizonCriticVerdict(score=78, classification="REASONABLY_STABLE")
                    ],
                }
            ),
        )
    )
    assert hold_low_2.status == "HOLD_CHECKPOINTED"
    assert hold_low_2.checkpoint is not None
    assert any(
        a.assumption_id == "ha-hq-v8-low-score" and a.steward_decision == "STEER"
        for a in hold_low_2.horizon_assumptions
    )

    # Answer "accept" next -> overwrites STEER -> ACCEPT and seals CERTIFIED
    res_accepted = await model_vault(
        DVModelingRequest(
            run_id="r-seq-s606-steer-accept",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            verifier_ctx=VerifierContext(
                profiles=profiles,
                fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            ),
            prover=prover,
            event_sink=sink_s606,
            horizon_assumptions=hold_low_2.horizon_assumptions,
            resume=ModelingResumeInput(
                checkpoint=hold_low_2.checkpoint,
                hold_answers=HoldAnswersReq(answers={"hq-v8-low-score": "accept"}),
                resumed_draft_ir=hold_low_2.draft_ir,
                resumed_certified_ir=hold_low_2.certified_ir,
            ),
            harness=DeterministicMockHarness(
                scripts_by_agent={
                    "ship-vault": [clean_draft],
                    "dv-semantic-critic": [
                        SemanticCriticVerdict(score=95, findings=[], accepted_waiver_ids=[])
                    ],
                    "dv-horizon-critic": [
                        HorizonCriticVerdict(score=78, classification="REASONABLY_STABLE")
                    ],
                }
            ),
        )
    )
    assert res_accepted.status == "CERTIFIED"
    ha_low = [a for a in res_accepted.horizon_assumptions if a.assumption_id == "ha-hq-v8-low-score"]
    assert len(ha_low) == 1
    assert ha_low[0].steward_decision == "ACCEPT"
    seqs_s606 = [ev.seq for ev in sink_s606.events]
    assert seqs_s606 == list(range(1, len(seqs_s606) + 1))

    # Also verify run_v8_horizon_review hold + resume monotonic seq
    sink_rev_hold = InMemoryRunEventSink()
    fast_base_req = DVModelingRequest(
        run_id="r-rev-seq",
        project_id="proj_12trap",
        ontology=ont,
        profiles=profiles,
        policy="fast",
        verifier_ctx=VerifierContext(
            profiles=profiles,
            fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
        ),
        prover=prover,
        event_sink=sink_rev_hold,
        harness=DeterministicMockHarness(scripts_by_agent={"ship-vault": [clean_draft]}),
    )
    fast_base_res = await model_vault(fast_base_req)
    rev_hold = await run_v8_horizon_review(
        fast_base_req.model_copy(
            update={
                "harness": DeterministicMockHarness(
                    scripts_by_agent={
                        "dv-horizon-critic": [
                            HorizonCriticVerdict(score=79, classification="REASONABLY_STABLE")
                        ]
                    }
                )
            }
        ),
        fast_base_res,
    )
    assert rev_hold.status == "HOLD_CHECKPOINTED"
    assert rev_hold.checkpoint is not None
    assert rev_hold.checkpoint.last_seq == sink_rev_hold.events[-1].seq
    rev_resumed = await model_vault(
        fast_base_req.model_copy(
            update={
                "horizon_assumptions": rev_hold.horizon_assumptions,
                "resume": ModelingResumeInput(
                    checkpoint=rev_hold.checkpoint,
                    hold_answers=HoldAnswersReq(answers={"hq-v8-low-score": "accept"}),
                    resumed_draft_ir=rev_hold.draft_ir,
                    resumed_certified_ir=rev_hold.certified_ir,
                ),
                "harness": DeterministicMockHarness(
                    scripts_by_agent={
                        "dv-horizon-critic": [
                            HorizonCriticVerdict(score=79, classification="REASONABLY_STABLE")
                        ]
                    }
                ),
            }
        )
    )
    assert rev_resumed.status == "CERTIFIED"
    seqs_rev = [ev.seq for ev in sink_rev_hold.events]
    assert seqs_rev == list(range(1, len(seqs_rev) + 1))

    # 3. Unlocked multi-table Hub PROMOTE_HUB_BK_TO_IMMUTABLE with Links (ds_cegar_12trap.duckdb)
    # 3a: Partial-coverage new_bk escalates cleanly without dropping source_bindings / BinderException
    _, cert_partial, bkt_partial = run_l1_codemods(
        clean_draft,
        profiles=profiles,
        locks=None,
        bucket_a_ops=[
            BucketAOperatorCall(
                operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                target_hub="hub_account",
                table_id=ibor_id,
                source_column="account_id",
                params={"new_bk_col": "issuer_cik", "source_id": "trap_db"},
            )
        ],
    )
    assert bkt_partial is not None and bkt_partial.requires_bucket_b is True
    hub_acct_partial = next(h for h in cert_partial.hubs if h.hub_name == "hub_account")
    assert len(hub_acct_partial.source_bindings) == 3
    prov_partial = prover.prove(cert_partial, profiles)
    assert len(prov_partial.dyn_results) == 15 and all(prov_partial.dyn_results.values())

    # 3b: Full-coverage new_bk across all 3 bound tables updates all bindings & passes DuckDbProverAdapter
    _, cert_full, bkt_full = run_l1_codemods(
        clean_draft,
        profiles=profiles,
        locks=None,
        bucket_a_ops=[
            BucketAOperatorCall(
                operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                target_hub="hub_account",
                table_id=ibor_id,
                source_column="account_id",
                params={"new_bk_col": "account_id", "source_id": "trap_db"},
            )
        ],
    )
    assert bkt_full is not None and not bkt_full.requires_bucket_b
    hub_acct_full = next(h for h in cert_full.hubs if h.hub_name == "hub_account")
    assert len(hub_acct_full.source_bindings) == 3
    assert hub_acct_full.key_tier == "TIER_1_GLOBAL_STANDARD"
    prov_full = prover.prove(cert_full, profiles)
    assert len(prov_full.dyn_results) == 15 and all(prov_full.dyn_results.values())

    # 4. @ship-vault forging reviewed_by_v7=True on actor='agent' with V7 accepted_waiver_ids=[]
    forged_draft = clean_draft.model_copy(
        update={
            "justifications": [
                AntiPatternJustification(
                    waiver_id="WAIV-c981d86d",
                    rule_id="DV-AP-004",
                    entity_name="hub_broker",
                    rationale="Forged self-approval attempt.",
                    actor="agent",
                    reviewed_by_v7=True,
                )
            ]
        }
    )
    forged_res = await model_vault(
        DVModelingRequest(
            run_id="r-forged-v7",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="strict",
            verifier_ctx=VerifierContext(
                profiles=profiles,
                fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            ),
            prover=prover,
            harness=DeterministicMockHarness(
                scripts_by_agent={
                    "ship-vault": [forged_draft],
                    "dv-semantic-critic": [
                        SemanticCriticVerdict(score=96, findings=["Rejected waiver"], accepted_waiver_ids=[])
                    ],
                }
            ),
        )
    )
    assert forged_res.status == "UNCERTIFIED_DRAFT"
    assert forged_res.verifier_report is not None
    assert forged_res.verifier_report.v6b_fail_count > 0
    assert forged_res.certified_ir is not None
    assert forged_res.certified_ir.justifications[0].reviewed_by_v7 is False

    # Direct audit_v6b_justifications check: clears agent=True when unaccepted, keeps human=True
    _, base_cert, _ = run_l1_codemods(
        clean_draft.model_copy(
            update={
                "justifications": [
                    AntiPatternJustification(
                        waiver_id="WAIV-agent-forged",
                        rule_id="DV-AP-004",
                        entity_name="hub_broker",
                        rationale="Agent forged.",
                        actor="agent",
                        reviewed_by_v7=True,
                    ),
                    AntiPatternJustification(
                        waiver_id="WAIV-human-ok",
                        rule_id="DV-AP-001",
                        entity_name="hub_issuer",
                        rationale="Human steward waiver.",
                        actor="human",
                        reviewed_by_v7=True,
                    ),
                ]
            }
        ),
        profiles=profiles,
    )
    audited_ir, marked = audit_v6b_justifications(
        base_cert, SemanticCriticVerdict(score=96, findings=[], accepted_waiver_ids=[])
    )
    assert marked == []
    by_wid = {j.waiver_id: j for j in audited_ir.justifications}
    assert by_wid["WAIV-agent-forged"].reviewed_by_v7 is False
    assert by_wid["WAIV-human-ok"].reviewed_by_v7 is True


@pytest.mark.asyncio
async def test_ship_vault_human_waiver_forgery_and_single_table_hub_cross_table_promote(
    tmp_path: Path,
) -> None:
    """Regression: block @ship-vault actor='human' forgery, escalate single-table Hub+Link/Sat cross-table promote, dedup demoted_cols."""
    prof = profile_source("trap_db", "duckdb", str(PUBLIC_AM / "ds_cegar_12trap.duckdb"))
    profiles = [prof]
    ont = _build_12trap_ontology()
    prover = DuckDbProverAdapter()
    mgr_id = next(t.table_id for t in prof.tables if t.table_name == "trap_manager_eff")
    clean_draft = _build_12trap_draft(profiles, defective=False)

    # 1. @ship-vault forging actor="human", reviewed_by_v7=False must be coerced & blocked under strict
    human_forged_draft = clean_draft.model_copy(
        update={
            "justifications": [
                AntiPatternJustification(
                    waiver_id="WAIV-c981d86d",
                    rule_id="DV-AP-004",
                    entity_name="hub_broker",
                    rationale="Attempted actor=human forgery from untrusted ship-vault.",
                    actor="human",
                    reviewed_by_v7=False,
                )
            ]
        }
    )
    forged_res = await model_vault(
        DVModelingRequest(
            run_id="r-forged-human-actor",
            project_id="proj_12trap",
            ontology=ont,
            profiles=profiles,
            policy="strict",
            verifier_ctx=VerifierContext(
                profiles=profiles,
                fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
            ),
            prover=prover,
            harness=DeterministicMockHarness(
                scripts_by_agent={
                    "ship-vault": [human_forged_draft],
                    "dv-semantic-critic": [
                        SemanticCriticVerdict(score=96, findings=["Rejected waiver"], accepted_waiver_ids=[])
                    ],
                }
            ),
        )
    )
    assert forged_res.status == "UNCERTIFIED_DRAFT"
    assert forged_res.verifier_report is not None
    assert forged_res.verifier_report.v6b_fail_count > 0
    assert forged_res.certified_ir is not None
    assert forged_res.certified_ir.justifications[0].actor == "agent"
    assert forged_res.certified_ir.justifications[0].reviewed_by_v7 is False

    # 2. Single-table unlocked Hub on t1 with co-located Link + Sat on t1 + populated column_triage
    #    targeted by call.table_id="t2" whose new_bk is absent from t1 -> escalates BRK-01 without BinderException
    t1_csv = tmp_path / "t1.csv"
    t2_csv = tmp_path / "t2.csv"
    t1_csv.write_text("account_id,broker_id,acct_note\nA1,B1,ok\n", encoding="utf-8")
    t2_csv.write_text("global_acct_uuid,region\nG1,US\n", encoding="utf-8")
    p1 = profile_source("s1", "csv", str(t1_csv))
    p2 = profile_source("s2", "csv", str(t2_csv))
    t1_id = p1.tables[0].table_id
    t2_id = p2.tables[0].table_id
    pair_profiles = [p1, p2]

    single_hub_draft = DraftVaultIR(
        project_id="proj_probe3f",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=t1_id,
                        col_name="account_id",
                        source_id="s1",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="S1",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="s1", table_id=t1_id, col_mappings={"account_id": "account_id"})
                ],
            ),
            DraftHubSpec(
                hub_name="hub_broker",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=t1_id,
                        col_name="broker_id",
                        source_id="s1",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="S1",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="s1", table_id=t1_id, col_mappings={"broker_id": "broker_id"})
                ],
            ),
        ],
        links=[
            DraftLinkSpec(
                link_name="lnk_account_broker",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_account",
                        role_name="ACCOUNT",
                        fk_col_name="hk_account",
                        source_col_names=["account_id"],
                    ),
                    LinkParticipantSpec(
                        hub_name="hub_broker",
                        role_name="BROKER",
                        fk_col_name="hk_broker",
                        source_col_names=["broker_id"],
                    ),
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="s1",
                        table_id=t1_id,
                        col_mappings={"account_id": "account_id", "broker_id": "broker_id"},
                    )
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_account_t1",
                sat_kind="STANDARD",
                parent_name="hub_account",
                source_bindings=[
                    DraftSourceBinding(source_id="s1", table_id=t1_id, col_mappings={"acct_note": "acct_note"})
                ],
                attributes=[
                    DraftField(name="acct_note", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                ],
            )
        ],
        column_triage=_triage_all(pair_profiles),
    )

    _, cert_3f, bkt_3f = run_l1_codemods(
        single_hub_draft,
        profiles=pair_profiles,
        locks=None,
        bucket_a_ops=[
            BucketAOperatorCall(
                operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                target_hub="hub_account",
                table_id=t2_id,
                source_column="account_id",
                params={"new_bk_col": "global_acct_uuid", "source_id": "s2"},
            )
        ],
    )
    assert bkt_3f is not None and bkt_3f.requires_bucket_b is True
    assert bkt_3f.bucket_b_escalations[0].brk_code == "BRK-01"
    hub_acct_3f = next(h for h in cert_3f.hubs if h.hub_name == "hub_account")
    assert [sb.table_id for sb in hub_acct_3f.source_bindings] == [t1_id]
    prov_3f = prover.prove(cert_3f, pair_profiles)
    assert len(prov_3f.dyn_results) == 15 and all(prov_3f.dyn_results.values())

    # 3. demote_old_bk_to_sat="true" on multi-table Hub deduplicates demoted DraftFields
    d1_csv = tmp_path / "d1.csv"
    d2_csv = tmp_path / "d2.csv"
    d1_csv.write_text("account_id,global_acct_uuid,acct_note\nA1,G1,ok\n", encoding="utf-8")
    d2_csv.write_text("account_id,global_acct_uuid,region\nA2,G2,US\n", encoding="utf-8")
    dp1 = profile_source("ds1", "csv", str(d1_csv))
    dp2 = profile_source("ds2", "csv", str(d2_csv))
    dt1_id, dt2_id = dp1.tables[0].table_id, dp2.tables[0].table_id
    demote_draft = DraftVaultIR(
        project_id="proj_demote",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=dt1_id,
                        col_name="account_id",
                        source_id="ds1",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="DS1",
                    ),
                    BusinessKeyPart(
                        table_id=dt2_id,
                        col_name="account_id",
                        source_id="ds2",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="DS2",
                    ),
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="ds1", table_id=dt1_id, col_mappings={"account_id": "account_id"}),
                    DraftSourceBinding(source_id="ds2", table_id=dt2_id, col_mappings={"account_id": "account_id"}),
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_account",
                sat_kind="STANDARD",
                parent_name="hub_account",
                source_bindings=[
                    DraftSourceBinding(source_id="ds1", table_id=dt1_id, col_mappings={"acct_note": "acct_note"})
                ],
                attributes=[
                    DraftField(name="acct_note", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                ],
            )
        ],
        column_triage=_triage_all([dp1, dp2]),
    )
    _, cert_demote, bkt_demote = run_l1_codemods(
        demote_draft,
        profiles=[dp1, dp2],
        locks=None,
        bucket_a_ops=[
            BucketAOperatorCall(
                operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                target_hub="hub_account",
                table_id=dt1_id,
                source_column="account_id",
                params={
                    "new_bk_col": "global_acct_uuid",
                    "source_id": "ds1",
                    "demote_old_bk_to_sat": "true",
                },
            )
        ],
    )
    assert bkt_demote is not None and not bkt_demote.requires_bucket_b
    sat_dem = next(s for s in cert_demote.satellites if s.sat_name == "sat_account")
    attr_names = [a.name for a in sat_dem.attributes]
    assert attr_names.count("account_id") == 1
    assert len(attr_names) == len(set(attr_names))


@pytest.mark.asyncio
async def test_demote_old_bk_per_satellite_routing_and_partial_sat_hub(
    tmp_path: Path,
) -> None:
    """Regression: demote_old_bk_to_sat='true' routes differing old BKs per Satellite without BinderException."""
    prover = DuckDbProverAdapter()

    # 1. 2-table unlocked Hub (crm: crm_acct_id, ibor: ibor_acct_id) with per-table Satellites
    crm_csv = tmp_path / "crm.csv"
    ibor_csv = tmp_path / "ibor.csv"
    crm_csv.write_text("crm_acct_id,global_acct_uuid,crm_note\nC1,G1,note1\n", encoding="utf-8")
    ibor_csv.write_text("ibor_acct_id,global_acct_uuid,ibor_note\nI1,G1,note2\n", encoding="utf-8")
    p_crm = profile_source("crm", "csv", str(crm_csv))
    p_ibor = profile_source("ibor", "csv", str(ibor_csv))
    t_crm = p_crm.tables[0].table_id
    t_ibor = p_ibor.tables[0].table_id

    draft_2f = DraftVaultIR(
        project_id="proj_2f",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=t_crm,
                        col_name="crm_acct_id",
                        source_id="crm",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="CRM",
                    ),
                    BusinessKeyPart(
                        table_id=t_ibor,
                        col_name="ibor_acct_id",
                        source_id="ibor",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="IBOR",
                    ),
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="crm",
                        table_id=t_crm,
                        col_mappings={"crm_acct_id": "crm_acct_id", "ibor_acct_id": "crm_acct_id"},
                    ),
                    DraftSourceBinding(
                        source_id="ibor",
                        table_id=t_ibor,
                        col_mappings={"crm_acct_id": "ibor_acct_id", "ibor_acct_id": "ibor_acct_id"},
                    ),
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
                        source_id="crm", table_id=t_crm, col_mappings={"crm_note": "crm_note"}
                    )
                ],
                attributes=[
                    DraftField(name="crm_note", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                ],
            ),
            DraftSatelliteSpec(
                sat_name="sat_account_ibor",
                sat_kind="STANDARD",
                parent_name="hub_account",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="ibor", table_id=t_ibor, col_mappings={"ibor_note": "ibor_note"}
                    )
                ],
                attributes=[
                    DraftField(name="ibor_note", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                ],
            ),
        ],
        column_triage=_triage_all([p_crm, p_ibor]),
    )

    _, cert_2f, bkt_2f = run_l1_codemods(
        draft_2f,
        profiles=[p_crm, p_ibor],
        locks=None,
        bucket_a_ops=[
            BucketAOperatorCall(
                operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                target_hub="hub_account",
                table_id=t_crm,
                source_column="crm_acct_id",
                params={
                    "new_bk_col": "global_acct_uuid",
                    "source_id": "crm",
                    "demote_old_bk_to_sat": "true",
                },
            )
        ],
    )
    assert bkt_2f is not None and not bkt_2f.requires_bucket_b
    sat_crm = next(s for s in cert_2f.satellites if s.sat_name == "sat_account_crm")
    sat_ibor = next(s for s in cert_2f.satellites if s.sat_name == "sat_account_ibor")
    assert {a.name for a in sat_crm.attributes} == {"crm_note", "crm_acct_id"}
    assert {a.name for a in sat_ibor.attributes} == {"ibor_note", "ibor_acct_id"}
    prov_2f = prover.prove(cert_2f, [p_crm, p_ibor])
    assert len(prov_2f.dyn_results) == 15 and all(prov_2f.dyn_results.values())

    # Also verify end-to-end CEGAR with V8 auto-steer emitting demote_old_bk_to_sat="true"
    ont = _build_12trap_ontology()
    res_2e = await model_vault(
        DVModelingRequest(
            run_id="r-2e-demote",
            project_id="proj_2f",
            ontology=ont,
            profiles=[p_crm, p_ibor],
            policy="strict",
            verifier_ctx=VerifierContext(profiles=[p_crm, p_ibor]),
            prover=prover,
            harness=DeterministicMockHarness(
                scripts_by_agent={
                    "ship-vault": [draft_2f],
                    "dv-semantic-critic": [
                        SemanticCriticVerdict(score=96, findings=[], accepted_waiver_ids=[]),
                        SemanticCriticVerdict(score=97, findings=[], accepted_waiver_ids=[]),
                    ],
                    "dv-horizon-critic": [
                        HorizonCriticVerdict(
                            score=70,
                            classification="NEEDS_STEER",
                            bucket_a_ops=[
                                BucketAOperatorCall(
                                    operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                                    target_hub="hub_account",
                                    table_id=t_crm,
                                    source_column="crm_acct_id",
                                    params={
                                        "new_bk_col": "global_acct_uuid",
                                        "source_id": "crm",
                                        "demote_old_bk_to_sat": "true",
                                    },
                                )
                            ],
                            bucket_b_questions=[],
                        ),
                        HorizonCriticVerdict(score=96, classification="REASONABLY_STABLE"),
                    ],
                }
            ),
        )
    )
    assert res_2e.status == "CERTIFIED"
    assert res_2e.certified_ir is not None
    assert {
        a.name
        for a in next(
            s for s in res_2e.certified_ir.satellites if s.sat_name == "sat_account_crm"
        ).attributes
    } == {"crm_note", "crm_acct_id"}
    assert {
        a.name
        for a in next(
            s for s in res_2e.certified_ir.satellites if s.sat_name == "sat_account_ibor"
        ).attributes
    } == {"ibor_note", "ibor_acct_id"}

    # 2. 3-table partial-Satellite Hub: d1 (account_id), d2 (account_id), d3 (legacy_acct_no),
    #    single sat_account bound only to d1 -> gets account_id only (no BinderException)
    d1_csv = tmp_path / "p2c_d1.csv"
    d2_csv = tmp_path / "p2c_d2.csv"
    d3_csv = tmp_path / "p2c_d3.csv"
    d1_csv.write_text("account_id,global_acct_uuid,acct_note\nA1,G1,ok\n", encoding="utf-8")
    d2_csv.write_text("account_id,global_acct_uuid,region\nA2,G2,US\n", encoding="utf-8")
    d3_csv.write_text("legacy_acct_no,global_acct_uuid,country\nL3,G3,UK\n", encoding="utf-8")
    dp1 = profile_source("p2c_1", "csv", str(d1_csv))
    dp2 = profile_source("p2c_2", "csv", str(d2_csv))
    dp3 = profile_source("p2c_3", "csv", str(d3_csv))
    dt1, dt2, dt3 = dp1.tables[0].table_id, dp2.tables[0].table_id, dp3.tables[0].table_id
    draft_2c = DraftVaultIR(
        project_id="proj_2c",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=dt1,
                        col_name="account_id",
                        source_id="p2c_1",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="P1",
                    ),
                    BusinessKeyPart(
                        table_id=dt2,
                        col_name="account_id",
                        source_id="p2c_2",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="P2",
                    ),
                    BusinessKeyPart(
                        table_id=dt3,
                        col_name="legacy_acct_no",
                        source_id="p2c_3",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="P3",
                    ),
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="p2c_1",
                        table_id=dt1,
                        col_mappings={"account_id": "account_id", "legacy_acct_no": "account_id"},
                    ),
                    DraftSourceBinding(
                        source_id="p2c_2",
                        table_id=dt2,
                        col_mappings={"account_id": "account_id", "legacy_acct_no": "account_id"},
                    ),
                    DraftSourceBinding(
                        source_id="p2c_3",
                        table_id=dt3,
                        col_mappings={"account_id": "legacy_acct_no", "legacy_acct_no": "legacy_acct_no"},
                    ),
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_account",
                sat_kind="STANDARD",
                parent_name="hub_account",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="p2c_1", table_id=dt1, col_mappings={"acct_note": "acct_note"}
                    )
                ],
                attributes=[
                    DraftField(name="acct_note", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                ],
            )
        ],
        column_triage=_triage_all([dp1, dp2, dp3]),
    )
    _, cert_2c, bkt_2c = run_l1_codemods(
        draft_2c,
        profiles=[dp1, dp2, dp3],
        locks=None,
        bucket_a_ops=[
            BucketAOperatorCall(
                operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                target_hub="hub_account",
                table_id=dt1,
                source_column="account_id",
                params={
                    "new_bk_col": "global_acct_uuid",
                    "source_id": "p2c_1",
                    "demote_old_bk_to_sat": "true",
                },
            )
        ],
    )
    assert bkt_2c is not None and not bkt_2c.requires_bucket_b
    sat_2c = next(s for s in cert_2c.satellites if s.sat_name == "sat_account")
    assert {a.name for a in sat_2c.attributes} == {"acct_note", "account_id"}
    prov_2c = prover.prove(cert_2c, [dp1, dp2, dp3])
    assert len(prov_2c.dyn_results) == 15 and all(prov_2c.dyn_results.values())


@pytest.mark.asyncio
async def test_unlocked_link_grain_sync_and_locked_missing_old_bk_escalation(
    tmp_path: Path,
) -> None:
    """Regression: unlocked Hub BK promotion updates attached Links (DV-DYN-003); locked Hub missing old_bk escalates BRK-01."""
    prover = DuckDbProverAdapter()

    # 1. Bug 4: 2-table unlocked Hub with attached Link promoted from account_id -> global_acct_uuid
    u1_csv = tmp_path / "u1.csv"
    u2_csv = tmp_path / "u2.csv"
    u1_csv.write_text(
        "account_id,global_acct_uuid,acct_note\nA1,G1,note1\nA3,G2,note2\n",
        encoding="utf-8",
    )
    u2_csv.write_text(
        "account_id,global_acct_uuid,broker_id\nA1,G1,B1\nA2,G1,B1\n",
        encoding="utf-8",
    )
    up1 = profile_source("u1", "csv", str(u1_csv))
    up2 = profile_source("u2", "csv", str(u2_csv))
    ut1, ut2 = up1.tables[0].table_id, up2.tables[0].table_id

    link_draft = DraftVaultIR(
        project_id="proj_lnk_sync",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(table_id=ut1, col_name="account_id", source_id="u1", key_tier="TIER_3_LOCAL_SCOPED", bkcc_value="U1"),
                    BusinessKeyPart(table_id=ut2, col_name="account_id", source_id="u2", key_tier="TIER_3_LOCAL_SCOPED", bkcc_value="U2"),
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="u1", table_id=ut1, col_mappings={"account_id": "account_id"}),
                    DraftSourceBinding(source_id="u2", table_id=ut2, col_mappings={"account_id": "account_id"}),
                ],
            ),
            DraftHubSpec(
                hub_name="hub_broker",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(table_id=ut2, col_name="broker_id", source_id="u2", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL"),
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="u2", table_id=ut2, col_mappings={"broker_id": "broker_id"}),
                ],
            ),
        ],
        links=[
            DraftLinkSpec(
                link_name="lnk_account_broker",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(hub_name="hub_account", role_name="ACCOUNT", fk_col_name="hk_account", source_col_names=["account_id"]),
                    LinkParticipantSpec(hub_name="hub_broker", role_name="BROKER", fk_col_name="hk_broker", source_col_names=["broker_id"]),
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="u2", table_id=ut2, col_mappings={"account_id": "account_id", "broker_id": "broker_id"}),
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_account_u1",
                sat_kind="STANDARD",
                parent_name="hub_account",
                source_bindings=[
                    DraftSourceBinding(source_id="u1", table_id=ut1, col_mappings={"acct_note": "acct_note"})
                ],
                attributes=[
                    DraftField(name="acct_note", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                ],
            )
        ],
        column_triage=_triage_all([up1, up2]),
    )

    rows_by_tbl = {
        ut1: [
            {"account_id": "A1", "global_acct_uuid": "G1", "acct_note": "note1"},
            {"account_id": "A3", "global_acct_uuid": "G2", "acct_note": "note2"},
        ],
        ut2: [
            {"account_id": "A1", "global_acct_uuid": "G1", "broker_id": "B1"},
            {"account_id": "A2", "global_acct_uuid": "G1", "broker_id": "B1"},
        ],
    }
    for demote_flag in ("false", "true"):
        _, cert_lnk, bkt_lnk = run_l1_codemods(
            link_draft,
            profiles=[up1, up2],
            locks=None,
            bucket_a_ops=[
                BucketAOperatorCall(
                    operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                    target_hub="hub_account",
                    table_id=ut1,
                    source_column="account_id",
                    params={
                        "new_bk_col": "global_acct_uuid",
                        "source_id": "u1",
                        "demote_old_bk_to_sat": demote_flag,
                    },
                )
            ],
        )
        assert bkt_lnk is not None and not bkt_lnk.requires_bucket_b
        lnk_cert = next(l for l in cert_lnk.links if l.link_name == "lnk_account_broker")
        assert lnk_cert.participants[0].source_col_names == ["global_acct_uuid"]
        assert "global_acct_uuid" in lnk_cert.source_bindings[0].col_mappings
        assert "account_id" not in lnk_cert.source_bindings[0].col_mappings
        prov_lnk = prover.prove(cert_lnk, [up1, up2], source_rows_by_table=rows_by_tbl)
        assert prov_lnk.dyn_results["DV-DYN-003"] is True, prov_lnk.counterexamples
        assert len(prov_lnk.dyn_results) == 15 and all(prov_lnk.dyn_results.values())

    # 2. Bug 5: Locked Hub targeted by table lacking old_bk escalates BRK-01 (0 BinderException)
    w1_csv = tmp_path / "l_t1.csv"
    w2_csv = tmp_path / "l_t2.csv"
    w1_csv.write_text("account_id,acct_note\nA1,ok\n", encoding="utf-8")
    w2_csv.write_text("global_acct_uuid,note2\nG1,extra\n", encoding="utf-8")
    lp1 = profile_source("ls1", "csv", str(w1_csv))
    lp2 = profile_source("ls2", "csv", str(w2_csv))
    lt1, lt2 = lp1.tables[0].table_id, lp2.tables[0].table_id

    w1_locked_draft = DraftVaultIR(
        project_id="proj_locked_miss",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(table_id=lt1, col_name="account_id", source_id="ls1", key_tier="TIER_3_LOCAL_SCOPED", bkcc_value="CRM"),
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="ls1", table_id=lt1, col_mappings={"account_id": "account_id"}),
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_account_l1",
                sat_kind="STANDARD",
                parent_name="hub_account",
                source_bindings=[
                    DraftSourceBinding(source_id="ls1", table_id=lt1, col_mappings={"acct_note": "acct_note"})
                ],
                attributes=[
                    DraftField(name="acct_note", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                ],
            )
        ],
        column_triage=_triage_all([lp1]),
    )
    _, cert_l1, _ = run_l1_codemods(w1_locked_draft, profiles=[lp1])
    locks_l1 = build_evolution_lock_from_certified(cert_l1)

    w2_locked_draft = w1_locked_draft.model_copy(
        update={
            "wave_number": 2,
            "hubs": [
                *w1_locked_draft.hubs,
                DraftHubSpec(
                    hub_name="hub_global_acct",
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    business_keys=[
                        BusinessKeyPart(table_id=lt2, col_name="global_acct_uuid", source_id="ls2", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL"),
                    ],
                    source_bindings=[
                        DraftSourceBinding(source_id="ls2", table_id=lt2, col_mappings={"global_acct_uuid": "global_acct_uuid"}),
                    ],
                ),
            ],
            "satellites": [
                *w1_locked_draft.satellites,
                DraftSatelliteSpec(
                    sat_name="sat_global_acct_l2",
                    sat_kind="STANDARD",
                    parent_name="hub_global_acct",
                    source_bindings=[
                        DraftSourceBinding(source_id="ls2", table_id=lt2, col_mappings={"note2": "note2"})
                    ],
                    attributes=[
                        DraftField(name="note2", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                    ],
                ),
            ],
            "column_triage": _triage_all([lp1, lp2]),
        }
    )

    base_ont = _build_12trap_ontology()
    ont = base_ont.model_copy(
        update={
            "concepts": [
                *base_ont.concepts,
                CanonicalBusinessConcept(
                    concept_uri="urn:opendv:am:GlobalAcct",
                    concept_name="GlobalAcct",
                    description="Global account",
                    key_authority_tier="TIER_1_GLOBAL_STANDARD",
                    bkcc_default="GLOBAL",
                ),
            ]
        }
    )
    for src_col in (None, "account_id"):
        res_locked_miss = await model_vault(
            DVModelingRequest(
                run_id=f"r-locked-miss-{src_col or 'none'}",
                project_id="proj_locked_miss",
                ontology=ont,
                profiles=[lp1, lp2],
                policy="strict",
                wave=WaveEvolutionInput(
                    evolution_locks=locks_l1,
                    prior_certified_ir=cert_l1,
                ),
                verifier_ctx=VerifierContext(profiles=[lp1, lp2], evolution_locks=locks_l1, prior_certified_ir=cert_l1),
                prover=prover,
                harness=DeterministicMockHarness(
                    scripts_by_agent={
                        "ship-vault": [w2_locked_draft],
                        "dv-semantic-critic": [SemanticCriticVerdict(score=96, findings=[], accepted_waiver_ids=[])],
                        "dv-horizon-critic": [
                            HorizonCriticVerdict(
                                score=70,
                                classification="NEEDS_STEER",
                                bucket_a_ops=[
                                    BucketAOperatorCall(
                                        operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                                        target_hub="hub_account",
                                        table_id=lt2,
                                        source_column=src_col,
                                        params={"new_bk_col": "global_acct_uuid", "source_id": "ls2"},
                                    )
                                ],
                            )
                        ],
                    }
                ),
            )
        )
        assert res_locked_miss.status == "HOLD_CHECKPOINTED"
        assert res_locked_miss.checkpoint is not None
        assert res_locked_miss.checkpoint.pending_questions
        assert res_locked_miss.checkpoint.pending_questions[0].brk_code == "BRK-01"

    # 3. Locked Link blocks unlocked Hub BK promotion with BRK-01
    from opendv.verifier import apply_bucket_a_operators

    locked_lnk_res = apply_bucket_a_operators(
        link_draft,
        [
            BucketAOperatorCall(
                operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                target_hub="hub_account",
                table_id=ut1,
                source_column="account_id",
                params={"new_bk_col": "global_acct_uuid", "source_id": "u1"},
            )
        ],
        locks=EvolutionLockSpec(wave_number=1, locked_links={"lnk_account_broker": ["hk_account", "hk_broker"]}),
    )
    assert locked_lnk_res.requires_bucket_b is True
    assert locked_lnk_res.bucket_b_escalations[0].brk_code == "BRK-01"

    # 4. Locked Hub where t1 (old_bk owner) has both old_bk & new_bk sources sal_account from t1 cleanly
    b1_csv = tmp_path / "b_t1.csv"
    b2_csv = tmp_path / "b_t2.csv"
    b1_csv.write_text("account_id,global_acct_uuid,acct_note\nA1,G1,ok\n", encoding="utf-8")
    b2_csv.write_text("global_acct_uuid,note2\nG1,extra\n", encoding="utf-8")
    bp1 = profile_source("bs1", "csv", str(b1_csv))
    bp2 = profile_source("bs2", "csv", str(b2_csv))
    bt1, bt2 = bp1.tables[0].table_id, bp2.tables[0].table_id
    w1_bridge = w1_locked_draft.model_copy(
        update={
            "hubs": [
                DraftHubSpec(
                    hub_name="hub_account",
                    key_tier="TIER_3_LOCAL_SCOPED",
                    business_keys=[
                        BusinessKeyPart(table_id=bt1, col_name="account_id", source_id="bs1", key_tier="TIER_3_LOCAL_SCOPED", bkcc_value="CRM"),
                    ],
                    source_bindings=[
                        DraftSourceBinding(source_id="bs1", table_id=bt1, col_mappings={"account_id": "account_id"}),
                    ],
                )
            ],
            "satellites": [
                DraftSatelliteSpec(
                    sat_name="sat_account_b1",
                    sat_kind="STANDARD",
                    parent_name="hub_account",
                    source_bindings=[
                        DraftSourceBinding(source_id="bs1", table_id=bt1, col_mappings={"acct_note": "acct_note"})
                    ],
                    attributes=[
                        DraftField(name="acct_note", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                    ],
                )
            ],
            "column_triage": _triage_all([bp1]),
        }
    )
    _, cert_b1, _ = run_l1_codemods(w1_bridge, profiles=[bp1])
    locks_b1 = build_evolution_lock_from_certified(cert_b1)
    w2_bridge = w1_bridge.model_copy(
        update={
            "wave_number": 2,
            "column_triage": _triage_all([bp1, bp2]),
        }
    )
    _, cert_b2, bkt_b2 = run_l1_codemods(
        w2_bridge,
        profiles=[bp1, bp2],
        locks=locks_b1,
        prior_ir=cert_b1,
        bucket_a_ops=[
            BucketAOperatorCall(
                operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                target_hub="hub_account",
                table_id=bt2,
                source_column=None,
                params={"new_bk_col": "global_acct_uuid", "source_id": "bs2"},
            )
        ],
    )
    assert bkt_b2 is not None and not bkt_b2.requires_bucket_b
    sal_b2 = next(l for l in cert_b2.links if l.link_name == "sal_account")
    assert sal_b2.source_bindings[0].table_id == bt1
    prov_b2 = prover.prove(cert_b2, [bp1, bp2])
    assert len(prov_b2.dyn_results) == 15 and all(prov_b2.dyn_results.values())


@pytest.mark.asyncio
async def test_locked_crosswalk_preexisting_canonical_and_unbound_link_table_duckdb(
    tmp_path: Path,
) -> None:
    """Regression: Wave-2 crosswalk bind, pre-existing canonical_hub merge, and Probe 1c unbound Link."""
    prover = DuckDbProverAdapter()
    base_ont = _build_12trap_ontology()
    ont = base_ont.model_copy(
        update={
            "concepts": [
                *base_ont.concepts,
                CanonicalBusinessConcept(
                    concept_uri="urn:opendv:am:GlobalAcct",
                    concept_name="GlobalAcct",
                    description="Global account",
                    key_authority_tier="TIER_1_GLOBAL_STANDARD",
                    bkcc_default="GLOBAL",
                ),
            ]
        }
    )

    # 1. Task 3: Wave-2 crosswalk (account_id, global_acct_id) not yet in locked hub_account
    w1_csv = tmp_path / "xw_w1.csv"
    xw_csv = tmp_path / "xw_w2.csv"
    w1_csv.write_text("account_id,acct_note\nA1,ok\n", encoding="utf-8")
    xw_csv.write_text("account_id,global_acct_id\nA1,G1\nA2,G1\n", encoding="utf-8")
    xp1 = profile_source("xw1", "csv", str(w1_csv))
    xp2 = profile_source("xw2", "csv", str(xw_csv))
    xt1, xt2 = xp1.tables[0].table_id, xp2.tables[0].table_id
    xw_w1_draft = DraftVaultIR(
        project_id="proj_xw",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(
                        table_id=xt1,
                        col_name="account_id",
                        source_id="xw1",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="CRM",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="xw1", table_id=xt1, col_mappings={"account_id": "account_id"}
                    )
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_account_xw1",
                sat_kind="STANDARD",
                parent_name="hub_account",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="xw1", table_id=xt1, col_mappings={"acct_note": "acct_note"}
                    )
                ],
                attributes=[
                    DraftField(name="acct_note", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                ],
            )
        ],
        column_triage=_triage_all([xp1]),
    )
    _, cert_xw1, _ = run_l1_codemods(xw_w1_draft, profiles=[xp1])
    locks_xw1 = build_evolution_lock_from_certified(cert_xw1)
    xw_w2_draft = xw_w1_draft.model_copy(
        update={"wave_number": 2, "column_triage": _triage_all([xp1, xp2])}
    )
    for src_col in (None, "account_id"):
        res_xw = await model_vault(
            DVModelingRequest(
                run_id=f"r-xw-{src_col or 'none'}",
                project_id="proj_xw",
                ontology=ont,
                profiles=[xp1, xp2],
                policy="strict",
                wave=WaveEvolutionInput(
                    evolution_locks=locks_xw1,
                    prior_certified_ir=cert_xw1,
                ),
                verifier_ctx=VerifierContext(
                    profiles=[xp1, xp2], evolution_locks=locks_xw1, prior_certified_ir=cert_xw1
                ),
                prover=prover,
                harness=DeterministicMockHarness(
                    scripts_by_agent={
                        "ship-vault": [xw_w2_draft],
                        "dv-semantic-critic": [
                            SemanticCriticVerdict(score=96, findings=[], accepted_waiver_ids=[])
                        ],
                        "dv-horizon-critic": [
                            HorizonCriticVerdict(
                                score=70,
                                classification="NEEDS_STEER",
                                bucket_a_ops=[
                                    BucketAOperatorCall(
                                        operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                                        target_hub="hub_account",
                                        table_id=xt2,
                                        source_column=src_col,
                                        params={"new_bk_col": "global_acct_id", "source_id": "xw2"},
                                    )
                                ],
                            ),
                            HorizonCriticVerdict(score=92, classification="REASONABLY_STABLE"),
                        ],
                    }
                ),
            )
        )
        assert res_xw.status == "CERTIFIED"
        assert res_xw.certified_ir is not None
        hub_xw = next(h for h in res_xw.certified_ir.hubs if h.hub_name == "hub_account")
        assert {sb.table_id for sb in hub_xw.source_bindings} == {xt1, xt2}
        assert any(lnk.link_name == "sal_account" for lnk in res_xw.certified_ir.links)

    # Mismatched source_column on unbound crosswalk escalates BRK-01 instead of BinderException
    from opendv.verifier import apply_bucket_a_operators

    bad_xw = apply_bucket_a_operators(
        xw_w2_draft,
        [
            BucketAOperatorCall(
                operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                target_hub="hub_account",
                table_id=xt2,
                source_column="wrong_col",
                params={"new_bk_col": "global_acct_id", "source_id": "xw2"},
            )
        ],
        locks=locks_xw1,
    )
    assert bad_xw.requires_bucket_b is True and bad_xw.bucket_b_escalations[0].brk_code == "BRK-01"

    # 2. Task 4: Pre-existing canonical_hub in Wave 2 draft when sal_table_id == t1 (bridge)
    f1_csv = tmp_path / "f1.csv"
    f2_csv = tmp_path / "f2.csv"
    f1_csv.write_text("account_id,global_acct_id,acct_note\nA1,G1,ok\n", encoding="utf-8")
    f2_csv.write_text("global_acct_id,note2\nG1,extra\n", encoding="utf-8")
    fp1 = profile_source("fs1", "csv", str(f1_csv))
    fp2 = profile_source("fs2", "csv", str(f2_csv))
    ft1, ft2 = fp1.tables[0].table_id, fp2.tables[0].table_id
    f_w1_draft = DraftVaultIR(
        project_id="proj_precanon",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(
                        table_id=ft1,
                        col_name="account_id",
                        source_id="fs1",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="CRM",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="fs1", table_id=ft1, col_mappings={"account_id": "account_id"}
                    )
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_account_f1",
                sat_kind="STANDARD",
                parent_name="hub_account",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="fs1", table_id=ft1, col_mappings={"acct_note": "acct_note"}
                    )
                ],
                attributes=[
                    DraftField(name="acct_note", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                ],
            )
        ],
        column_triage=_triage_all([fp1]),
    )
    _, cert_f1, _ = run_l1_codemods(f_w1_draft, profiles=[fp1])
    locks_f1 = build_evolution_lock_from_certified(cert_f1)
    f_w2_draft = f_w1_draft.model_copy(
        update={
            "wave_number": 2,
            "hubs": [
                *f_w1_draft.hubs,
                DraftHubSpec(
                    hub_name="hub_account_canonical",
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    business_keys=[
                        BusinessKeyPart(
                            table_id=ft2,
                            col_name="global_acct_id",
                            source_id="fs2",
                            key_tier="TIER_1_GLOBAL_STANDARD",
                            bkcc_value="GLOBAL",
                        )
                    ],
                    source_bindings=[
                        DraftSourceBinding(
                            source_id="fs2",
                            table_id=ft2,
                            col_mappings={"global_acct_id": "global_acct_id"},
                        )
                    ],
                ),
            ],
            "satellites": [
                *f_w1_draft.satellites,
                DraftSatelliteSpec(
                    sat_name="sat_acct_canon_f2",
                    sat_kind="STANDARD",
                    parent_name="hub_account_canonical",
                    source_bindings=[
                        DraftSourceBinding(
                            source_id="fs2", table_id=ft2, col_mappings={"note2": "note2"}
                        )
                    ],
                    attributes=[
                        DraftField(name="note2", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                    ],
                ),
            ],
            "column_triage": _triage_all([fp1, fp2]),
        }
    )
    res_f2 = await model_vault(
        DVModelingRequest(
            run_id="r-precanon",
            project_id="proj_precanon",
            ontology=ont,
            profiles=[fp1, fp2],
            policy="strict",
            wave=WaveEvolutionInput(
                evolution_locks=locks_f1,
                prior_certified_ir=cert_f1,
            ),
            verifier_ctx=VerifierContext(
                profiles=[fp1, fp2], evolution_locks=locks_f1, prior_certified_ir=cert_f1
            ),
            prover=prover,
            harness=DeterministicMockHarness(
                scripts_by_agent={
                    "ship-vault": [f_w2_draft],
                    "dv-semantic-critic": [
                        SemanticCriticVerdict(score=96, findings=[], accepted_waiver_ids=[])
                    ],
                    "dv-horizon-critic": [
                        HorizonCriticVerdict(
                            score=70,
                            classification="NEEDS_STEER",
                            bucket_a_ops=[
                                BucketAOperatorCall(
                                    operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                                    target_hub="hub_account",
                                    table_id=ft2,
                                    source_column=None,
                                    params={"new_bk_col": "global_acct_id", "source_id": "fs2"},
                                )
                            ],
                        ),
                        HorizonCriticVerdict(score=92, classification="REASONABLY_STABLE"),
                    ],
                }
            ),
        )
    )
    assert res_f2.status == "CERTIFIED"
    assert res_f2.certified_ir is not None
    canon_f2 = next(h for h in res_f2.certified_ir.hubs if h.hub_name == "hub_account_canonical")
    assert {sb.table_id for sb in canon_f2.source_bindings} == {ft1, ft2}

    # Incompatible or locked pre-existing canonical_hub escalates BRK-01
    for bad_op, bad_locks in (
        (
            BucketAOperatorCall(
                operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                target_hub="hub_account",
                table_id=ft2,
                source_column=None,
                params={"new_bk_col": "note2", "source_id": "fs2"},
            ),
            locks_f1,
        ),
        (
            BucketAOperatorCall(
                operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                target_hub="hub_account",
                table_id=ft2,
                source_column=None,
                params={"new_bk_col": "global_acct_id", "source_id": "fs2"},
            ),
            locks_f1.model_copy(
                update={
                    "locked_hub_bks": {
                        **locks_f1.locked_hub_bks,
                        "hub_account_canonical": ["global_acct_id"],
                    }
                }
            ),
        ),
    ):
        bad_canon = apply_bucket_a_operators(f_w2_draft, [bad_op], locks=bad_locks)
        assert bad_canon.requires_bucket_b is True and bad_canon.bucket_b_escalations[0].brk_code == "BRK-01"

    # 3. Task 5 (Probe 1c): Single-table unlocked hub on t1 + Link on unbound t2
    p1c_1 = tmp_path / "p1c_t1.csv"
    p1c_2 = tmp_path / "p1c_t2.csv"
    p1c_1.write_text("account_id,global_acct_uuid,acct_note\nA1,G1,n1\n", encoding="utf-8")
    p1c_2.write_text("account_id,global_acct_uuid,broker_id\nA1,G1,B1\nA2,G2,B2\n", encoding="utf-8")
    cp1 = profile_source("cs1", "csv", str(p1c_1))
    cp2 = profile_source("cs2", "csv", str(p1c_2))
    ct1, ct2 = cp1.tables[0].table_id, cp2.tables[0].table_id
    d1c = DraftVaultIR(
        project_id="proj_p1c",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(
                        table_id=ct1,
                        col_name="account_id",
                        source_id="cs1",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="CRM",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="cs1", table_id=ct1, col_mappings={"account_id": "account_id"}
                    )
                ],
            ),
            DraftHubSpec(
                hub_name="hub_broker",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id=ct2,
                        col_name="broker_id",
                        source_id="cs2",
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="cs2", table_id=ct2, col_mappings={"broker_id": "broker_id"}
                    )
                ],
            ),
        ],
        links=[
            DraftLinkSpec(
                link_name="lnk_acct_broker",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_account",
                        role_name="ACCOUNT",
                        fk_col_name="hk_account",
                        source_col_names=["account_id"],
                    ),
                    LinkParticipantSpec(
                        hub_name="hub_broker",
                        role_name="BROKER",
                        fk_col_name="hk_broker",
                        source_col_names=["broker_id"],
                    ),
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="cs2",
                        table_id=ct2,
                        col_mappings={"account_id": "account_id", "broker_id": "broker_id"},
                    )
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_account_c1",
                sat_kind="STANDARD",
                parent_name="hub_account",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="cs1", table_id=ct1, col_mappings={"acct_note": "acct_note"}
                    )
                ],
                attributes=[
                    DraftField(name="acct_note", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                ],
            )
        ],
        column_triage=_triage_all([cp1, cp2]),
    )
    _, cert_1c, bkt_1c = run_l1_codemods(
        d1c,
        profiles=[cp1, cp2],
        locks=None,
        bucket_a_ops=[
            BucketAOperatorCall(
                operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                target_hub="hub_account",
                table_id=ct1,
                source_column="account_id",
                params={"new_bk_col": "global_acct_uuid", "source_id": "cs1"},
            )
        ],
    )
    assert bkt_1c is not None and not bkt_1c.requires_bucket_b
    prov_1c = prover.prove(cert_1c, [cp1, cp2])
    assert len(prov_1c.dyn_results) == 15 and all(prov_1c.dyn_results.values()), prov_1c.counterexamples


@pytest.mark.asyncio
async def test_ship_vault_synthesis_retries_once_on_invalid_structured_json(tmp_path: Path) -> None:
    """run_ship_vault_synthesis retries once on invalid structured output and fails closed on 2nd failure."""
    csv_path = tmp_path / "acct_retry.csv"
    csv_path.write_text("account_id,acct_name\nA1,Alpha\n", encoding="utf-8")
    prof = profile_source("src_retry", "csv", str(csv_path))
    tbl_id = prof.tables[0].table_id
    onto = _build_12trap_ontology()
    valid_draft = DraftVaultIR(
        project_id="proj_retry",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(
                        table_id=tbl_id,
                        col_name="account_id",
                        source_id="src_retry",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="CRM",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_retry", table_id=tbl_id, col_mappings={"account_id": "account_id"}
                    )
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_account_core",
                sat_kind="STANDARD",
                parent_name="hub_account",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_retry", table_id=tbl_id, col_mappings={"acct_name": "acct_name"}
                    )
                ],
                attributes=[
                    DraftField(name="acct_name", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                ],
            )
        ],
        column_triage=_triage_all([prof]),
    )

    class _ScriptedShipHarness:
        def __init__(self, outcomes: list[DraftVaultIR | OpenDVError]) -> None:
            self._outcomes = list(outcomes)
            self.calls: list[ModelingTurnRequest] = []

        @property
        def provider_name(self) -> HarnessProvider:
            return "agy"

        async def run_turn(
            self,
            request: ModelingTurnRequest,
            *,
            event_sink: ModelingEventSink | None = None,
            scout_dispatcher: ModelingScoutDispatcher | None = None,
        ) -> ModelingTurnResult:
            self.calls.append(request)
            item = self._outcomes.pop(0)
            if isinstance(item, OpenDVError):
                raise item
            return ModelingTurnResult(
                agent_id=request.agent_id,
                provider="agy",
                model_id="gemini-3.8-flash-high",
                structured_output=item,
                turns_used=1,
                total_tokens=100,
                token_count_source="reported",
            )

    err1 = OpenDVError(
        "agy CLI returned invalid structured output for DraftVaultIR: expected ':' at line 412",
        error_code=ErrorCode.AGENT_EXECUTION_FAILED,
    )
    ok_harness = _ScriptedShipHarness([err1, valid_draft])
    draft, _, turns, _, _ = await run_ship_vault_synthesis(
        run_id="run-retry-ok",
        project_id="proj_retry",
        wave_number=1,
        attempt=1,
        ontology=onto,
        profiles=[prof],
        clusters=[],
        locks=None,
        prior_report=None,
        hints=[],
        hold_answers=None,
        policy="fast",
        budget=HarnessBudget(max_turns=2, max_tokens=50000, timeout_seconds=30),
        harness=ok_harness,
    )
    assert draft == valid_draft
    assert turns >= 2
    assert len(ok_harness.calls) == 2
    assert "INVALID OUTPUT RETRY" in ok_harness.calls[1].prompt
    assert "Return ONLY one valid compact JSON object." in ok_harness.calls[1].prompt

    err2 = OpenDVError(
        "agy CLI returned invalid structured output for DraftVaultIR: truncated JSON",
        error_code=ErrorCode.AGENT_EXECUTION_FAILED,
    )
    fail_harness = _ScriptedShipHarness([err1, err2])
    with pytest.raises(OpenDVError) as exc_fail:
        await run_ship_vault_synthesis(
            run_id="run-retry-fail",
            project_id="proj_retry",
            wave_number=1,
            attempt=1,
            ontology=onto,
            profiles=[prof],
            clusters=[],
            locks=None,
            prior_report=None,
            hints=[],
            hold_answers=None,
            policy="fast",
            budget=HarnessBudget(max_turns=2, max_tokens=50000, timeout_seconds=30),
            harness=fail_harness,
        )
    assert exc_fail.value.error_code == ErrorCode.AGENT_EXECUTION_FAILED
    assert len(fail_harness.calls) == 2
