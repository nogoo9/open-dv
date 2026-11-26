"""Comprehensive unit, real-data, and wave-diff E2E tests for L1 codemods and V1-V6 gates."""

from __future__ import annotations

from pathlib import Path

from opendv.models.diagnostics import (
    DocumentCitationRef,
    ExternalMcpEvidenceRef,
    IRProverReport,
    VerifierDiagnostic,
)
from opendv.models.events import ExternalMcpReceipt, OcrPageReceipt
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ConceptTableBinding,
    GraphAOntology,
    OntologyEnumeration,
)
from opendv.models.project import EvolutionLockSpec
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
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
    PredicateLeaf,
)
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_vault import BucketAOperatorCall
from opendv.sources.clustering import cluster_tables
from opendv.sources.document_map import DocumentMapBuilder
from opendv.sources.profiler import profile_source
from opendv.storage import DuckDbProverAdapter
from opendv.verifier import (
    L1CodemodOptions,
    VerifierContext,
    apply_bucket_a_operators,
    build_evolution_lock_from_certified,
    compute_canonical_text_sha256,
    inject_unresolved_triage,
    merge_draft_shards,
    normalize_page_text,
    run_l1_codemods,
    run_v1_to_v6_gates,
    verify_v1_structure,
    verify_v2_hash_and_bkcc,
    verify_v3_sentinels_and_temporal,
    verify_v4_unstructured,
    verify_v5_evolution,
)
from tests.fixtures.scale_200 import build_scale_200_table_profiles

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_AM = REPO_ROOT / "data" / "public_am"
SAMPLE_PDFS = REPO_ROOT / "examples" / "sample_pdfs"


def _triage_all(
    profiles: list[SourceProfile],
    target_map: dict[tuple[str, str], str] | None = None,
    system_cols: set[str] | None = None,
) -> list[ColumnTriageEntry]:
    tmap = target_map or {}
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
                            rationale="Technical audit/snapshot timestamp excluded from payload.",
                        )
                    )
                else:
                    entries.append(
                        ColumnTriageEntry(
                            table_id=tbl.table_id,
                            col_name=col.col_name,
                            disposition="MODELED",
                            target_ref=tmap.get((tbl.table_id, col.col_name), "modeled"),
                            rationale="",
                        )
                    )
    return entries


def test_e2e1_real_am_wave1_to_wave2_to_wave3_locks_and_bucket_a(tmp_path: Path) -> None:
    """E2E-1: Real AM sqlite+parquet Wave 1 -> Wave 2 -> Wave 3 diff, locks, and Bucket A ops."""
    sqlite_prof = profile_source(
        "am_sqlite", "sqlite", str(PUBLIC_AM / "am_sec_treasury_ibor.sqlite")
    )
    esg_prof = profile_source(
        "am_esg", "parquet", str(PUBLIC_AM / "am_esg_stewardship_proxy.parquet")
    )
    w1_profiles = [sqlite_prof, esg_prof]

    # Map ESG wide table into 4 Hubs + Link (UAT-S6-01), plus securities sparse cusip (BUG-02)
    sec_tbl_id = next(t.table_id for t in sqlite_prof.tables if t.table_name == "securities")
    iss_tbl_id = next(t.table_id for t in sqlite_prof.tables if t.table_name == "issuers")
    esg_tbl_id = esg_prof.tables[0].table_id

    hubs_w1 = [
        DraftHubSpec(
            hub_name="hub_issuer",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id=iss_tbl_id,
                    col_name="cik",
                    source_id="am_sqlite",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    bkcc_value="SEC_EDGAR",
                )
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id="am_sqlite",
                    table_id=iss_tbl_id,
                    col_mappings={"cik": "cik"},
                )
            ],
        ),
        DraftHubSpec(
            hub_name="hub_security",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id=sec_tbl_id,
                    col_name="ticker",
                    source_id="am_sqlite",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    bkcc_value="NASDAQ",
                )
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id="am_sqlite",
                    table_id=sec_tbl_id,
                    col_mappings={"ticker": "ticker"},
                )
            ],
        ),
        DraftHubSpec(
            hub_name="hub_cusip_ref",
            key_tier="TIER_1_GLOBAL_STANDARD",
            business_keys=[
                BusinessKeyPart(
                    table_id=sec_tbl_id,
                    col_name="cusip",
                    source_id="am_sqlite",
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    bkcc_value="GLOBAL",
                )
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id="am_sqlite",
                    table_id=sec_tbl_id,
                    col_mappings={"cusip": "cusip"},
                )
            ],
        ),
        DraftHubSpec(
            hub_name="hub_esg_rating",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id=esg_tbl_id,
                    col_name="esg_rating_id",
                    source_id="am_esg",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    bkcc_value="ESG_VENDOR",
                )
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id="am_esg",
                    table_id=esg_tbl_id,
                    col_mappings={"esg_rating_id": "esg_rating_id"},
                )
            ],
        ),
        DraftHubSpec(
            hub_name="hub_carbon_disclosure",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id=esg_tbl_id,
                    col_name="carbon_disclosure_id",
                    source_id="am_esg",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    bkcc_value="CDP",
                )
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id="am_esg",
                    table_id=esg_tbl_id,
                    col_mappings={"carbon_disclosure_id": "carbon_disclosure_id"},
                )
            ],
        ),
        DraftHubSpec(
            hub_name="hub_proxy_ballot",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id=esg_tbl_id,
                    col_name="proxy_ballot_id",
                    source_id="am_esg",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    bkcc_value="PROXY",
                )
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id="am_esg",
                    table_id=esg_tbl_id,
                    col_mappings={"proxy_ballot_id": "proxy_ballot_id"},
                )
            ],
        ),
        DraftHubSpec(
            hub_name="hub_mandate_breach",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id=esg_tbl_id,
                    col_name="mandate_rule_id",
                    source_id="am_esg",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    bkcc_value="STEWARDSHIP",
                )
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id="am_esg",
                    table_id=esg_tbl_id,
                    col_mappings={"mandate_rule_id": "mandate_rule_id"},
                )
            ],
        ),
    ]
    links_w1 = [
        # BUG-02: binary Link where cusip has null_ratio ~0.6774 in am_sec_treasury_ibor.sqlite
        DraftLinkSpec(
            link_name="lnk_security_cusip",
            link_kind="STANDARD",
            participants=[
                LinkParticipantSpec(
                    hub_name="hub_security",
                    role_name="SECURITY",
                    fk_col_name="hk_security",
                    source_col_names=["ticker"],
                ),
                LinkParticipantSpec(
                    hub_name="hub_cusip_ref",
                    role_name="CUSIP",
                    fk_col_name="hk_cusip_ref",
                    source_col_names=["cusip"],
                ),
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id="am_sqlite",
                    table_id=sec_tbl_id,
                    col_mappings={"ticker": "ticker", "cusip": "cusip"},
                )
            ],
        ),
        DraftLinkSpec(
            link_name="lnk_esg_stewardship_event",
            link_kind="STANDARD",
            participants=[
                LinkParticipantSpec(
                    hub_name="hub_esg_rating",
                    role_name="RATING",
                    fk_col_name="hk_esg_rating",
                    source_col_names=["esg_rating_id"],
                ),
                LinkParticipantSpec(
                    hub_name="hub_carbon_disclosure",
                    role_name="CARBON",
                    fk_col_name="hk_carbon_disclosure",
                    source_col_names=["carbon_disclosure_id"],
                ),
                LinkParticipantSpec(
                    hub_name="hub_proxy_ballot",
                    role_name="BALLOT",
                    fk_col_name="hk_proxy_ballot",
                    source_col_names=["proxy_ballot_id"],
                ),
                LinkParticipantSpec(
                    hub_name="hub_mandate_breach",
                    role_name="BREACH",
                    fk_col_name="hk_mandate_breach",
                    source_col_names=["mandate_rule_id"],
                ),
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id="am_esg",
                    table_id=esg_tbl_id,
                    col_mappings={"esg_rating_id": "esg_rating_id"},
                )
            ],
        ),
    ]
    sats_w1 = [
        DraftSatelliteSpec(
            sat_name="sat_issuer_sqlite",
            sat_kind="STANDARD",
            parent_name="hub_issuer",
            source_bindings=[
                DraftSourceBinding(
                    source_id="am_sqlite",
                    table_id=iss_tbl_id,
                    col_mappings={"company_name": "company_name", "exchange": "exchange"},
                )
            ],
            attributes=[
                DraftField(name="company_name", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                DraftField(name="exchange", logical_type="UTF8_STRING", nullable=False, is_pii=False),
            ],
        )
    ]
    draft_w1 = DraftVaultIR(
        project_id="am_v2_test",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=hubs_w1,
        links=links_w1,
        satellites=sats_w1,
        column_triage=_triage_all(w1_profiles),
    )

    _, cert_w1, _ = run_l1_codemods(draft_w1, profiles=w1_profiles)
    assert cert_w1.wave_delta is None
    assert any(m.view_name.startswith("information_mart.vw_") for m in cert_w1.information_marts)
    # BRK-02b: binary sparse Link preserves nullable participant & sets is_optional_inapplicable (no IS_NOT_NULL drop)
    sec_lnk = next(l for l in cert_w1.links if l.link_name == "lnk_security_cusip")
    assert sec_lnk.load_semantics is not None
    assert sec_lnk.load_semantics.filter_ast is None
    cusip_spec = next(
        ps
        for ps in (sec_lnk.hash_recipe.participant_specs if sec_lnk.hash_recipe else [])
        if ps.hub_name == "hub_cusip_ref"
    )
    assert cusip_spec.is_nullable is True

    rep_w1 = run_v1_to_v6_gates(cert_w1, VerifierContext(profiles=w1_profiles))
    assert rep_w1.v1_v6_fail_count == 0, rep_w1.v1_v6_diagnostics

    # Seal Wave 1 locks & evolve to Wave 2 with wave2 parquet + bitemporal + prime_allocations
    locks_w1 = build_evolution_lock_from_certified(cert_w1)
    w2_eu = profile_source("w2_eu", "parquet", str(PUBLIC_AM / "wave2_eu_custody_lei_and_drift.parquet"))
    w2_prime = profile_source("w2_prime", "csv", str(PUBLIC_AM / "prime_allocations_w2.csv"))
    w2_nav = profile_source("w2_nav", "csv", str(PUBLIC_AM / "wave2_bitemporal_nav_restatements.csv"))
    xwalk_csv = tmp_path / "w2_issuer_lei_xwalk.csv"
    xwalk_csv.write_text("cik,lei_code\n0000320193,HWUPKR0MPOU8FGXBT394\n", encoding="utf-8")
    w2_xwalk = profile_source("w2_xwalk", "csv", str(xwalk_csv))
    w2_profiles = [*w1_profiles, w2_eu, w2_prime, w2_nav, w2_xwalk]

    prime_tbl = w2_prime.tables[0].table_id
    eu_tbl = w2_eu.tables[0].table_id
    nav_tbl = w2_nav.tables[0].table_id
    xwalk_tbl = w2_xwalk.tables[0].table_id

    # Introduce Wave-2 added column + simulate locked sat column addition + prime allocations
    sat_issuer_w2 = sats_w1[0].model_copy(
        update={
            "attributes": [
                *sats_w1[0].attributes,
                DraftField(name="country_iso", logical_type="UTF8_STRING", nullable=True, is_pii=False),
            ]
        }
    )
    sat_prime = DraftSatelliteSpec(
        sat_name="sat_prime_allocation",
        sat_kind="STANDARD",
        parent_name="hub_security",
        source_bindings=[
            DraftSourceBinding(
                source_id="w2_prime",
                table_id=prime_tbl,
                col_mappings={"avg_price_dec": "avg_price_dec", "allocation_seq": "allocation_seq"},
            )
        ],
        attributes=[
            DraftField(
                name="avg_price_dec",
                logical_type=DecimalType(precision=18, scale=4),
                nullable=True,
                is_pii=False,
            ),
            DraftField(name="allocation_seq", logical_type="INT64", nullable=False, is_pii=False),
        ],
    )
    sat_nav = DraftSatelliteSpec(
        sat_name="sat_nav_restatement",
        sat_kind="STANDARD",
        parent_name="hub_security",
        source_bindings=[
            DraftSourceBinding(
                source_id="w2_nav",
                table_id=nav_tbl,
                col_mappings={"applied_dts": "applied_dts", "nav_per_share": "nav_per_share"},
            )
        ],
        is_bitemporal=True,
        applied_dts_col="applied_dts",
        attributes=[
            DraftField(
                name="nav_per_share",
                logical_type=DecimalType(precision=18, scale=4),
                nullable=True,
                is_pii=False,
            )
        ],
    )
    draft_w2 = DraftVaultIR(
        project_id="am_v2_test",
        ontology_version="1.0.0",
        wave_number=2,
        hubs=hubs_w1,
        links=links_w1,
        satellites=[sat_issuer_w2, sat_prime, sat_nav],
        column_triage=_triage_all(w2_profiles),
    )

    # Exercise Bucket A operators (including locked PROMOTE_HUB_BK_TO_IMMUTABLE -> sal_issuer)
    bucket_calls = [
        BucketAOperatorCall(
            operator="WIDEN_TYPE_TO_DECIMAL",
            sat_name="sat_prime_allocation",
            source_column="avg_price_dec",
            params={"target_column": "avg_price_dec", "precision": "38", "scale": "10"},
        ),
        BucketAOperatorCall(
            operator="INJECT_MAS_CDK",
            sat_name="sat_prime_allocation",
            source_column="allocation_seq",
            params={"cdk_col": "allocation_seq"},
        ),
        BucketAOperatorCall(
            operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
            target_hub="hub_issuer",
            table_id=xwalk_tbl,
            source_column="cik",
            params={"new_bk_col": "lei_code", "source_id": "w2_xwalk", "demote_old_bk_to_sat": "true"},
        ),
    ]
    # Note: eu_tbl lacks 'cik', so targeting eu_tbl must escalate BRK-01 instead of invalid sal_*
    missing_res = apply_bucket_a_operators(
        draft_w2,
        [
            BucketAOperatorCall(
                operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                target_hub="hub_issuer",
                table_id=eu_tbl,
                source_column="cik",
                params={"new_bk_col": "lei_code", "source_id": "w2_eu"},
            )
        ],
        locks=locks_w1,
    )
    assert missing_res.requires_bucket_b is True
    assert missing_res.bucket_b_escalations[0].brk_code == "BRK-01"
    draft_w2_out, cert_w2, bucket_res = run_l1_codemods(
        draft_w2,
        profiles=w2_profiles,
        locks=locks_w1,
        prior_ir=cert_w1,
        bucket_a_ops=bucket_calls,
    )
    assert bucket_res is not None
    assert not bucket_res.requires_bucket_b
    assert "hub_issuer_canonical" in bucket_res.rerouted_entities
    assert "sal_issuer" in bucket_res.rerouted_entities
    assert any(s.sat_name == "sat_issuer_sqlite_ext_v2" and s.companion_epoch == 2 for s in cert_w2.satellites)
    assert any(lnk.link_name == "sal_issuer" and lnk.zone_id == "Z3" for lnk in cert_w2.links)
    assert cert_w2.wave_delta is not None
    assert cert_w2.wave_delta.to_wave == 2

    # Check V5 passes Wave 1 -> Wave 2, DuckDbProverAdapter proves sal_issuer, and blocks __cold_start__
    rep_w2 = run_v1_to_v6_gates(
        cert_w2,
        VerifierContext(profiles=w2_profiles, evolution_locks=locks_w1, prior_certified_ir=cert_w1),
    )
    assert rep_w2.v1_v6_fail_count == 0, rep_w2.v1_v6_diagnostics
    _, cert_w2_duck, _ = run_l1_codemods(
        draft_w2.model_copy(update={"satellites": [sats_w1[0]]}),
        profiles=w2_profiles,
        locks=locks_w1,
        prior_ir=cert_w1,
        bucket_a_ops=[bucket_calls[2]],
    )
    prov_w2 = DuckDbProverAdapter().prove(cert_w2_duck, w2_profiles)
    assert len(prov_w2.dyn_results) == 15 and all(prov_w2.dyn_results.values()), prov_w2.counterexamples

    cold_diags = verify_v5_evolution(cert_w2, locks=locks_w1, cold_start_requested=True)
    assert any(d.rule_id == "DV-EVO-002" and d.entity_name == "__cold_start__" for d in cold_diags)

    # Wave 2 -> Wave 3 progression: sat_epochs advance v2 -> v3 and field_ids stay > HWM
    locks_w2 = build_evolution_lock_from_certified(cert_w2)
    assert locks_w2.sat_epochs["sat_issuer_sqlite"] == 2
    sat_w3_ext = DraftSatelliteSpec(
        sat_name="sat_issuer_sqlite_ext_v3",
        sat_kind="EXT",
        parent_name="hub_issuer",
        source_bindings=sat_issuer_w2.source_bindings,
        extends_sat_name="sat_issuer_sqlite",
        companion_epoch=3,
        attributes=[DraftField(name="lei_status", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
    )
    draft_w3 = draft_w2_out.model_copy(
        update={"wave_number": 3, "satellites": [*draft_w2_out.satellites, sat_w3_ext]}
    )
    _, cert_w3, _ = run_l1_codemods(draft_w3, profiles=w2_profiles, locks=locks_w2, prior_ir=cert_w2)
    assert any(
        s.sat_name == "sat_issuer_sqlite_ext_v2" and s.companion_epoch == 2
        for s in cert_w3.satellites
    )
    assert any(
        s.sat_name == "sat_issuer_sqlite_ext_v3" and s.companion_epoch == 3
        for s in cert_w3.satellites
    )
    rep_w3 = run_v1_to_v6_gates(
        cert_w3,
        VerifierContext(profiles=w2_profiles, evolution_locks=locks_w2, prior_certified_ir=cert_w2),
    )
    assert rep_w3.v1_v6_fail_count == 0, rep_w3.v1_v6_diagnostics


def test_bucket_a_remaining_operators_and_locked_rerouting_and_escalation() -> None:
    """Exercise all 6 Bucket A operators including SET_BKCC, SPLIT, PROMOTE_COL, and Bucket B."""
    base = DraftVaultIR(
        project_id="bkt_test",
        ontology_version="1.0.0",
        wave_number=2,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(
                        table_id="crm:acct",
                        col_name="account_id",
                        source_id="crm",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="CRM",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="crm", table_id="crm:acct", col_mappings={"account_id": "account_id"})
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
                        source_id="crm",
                        table_id="crm:acct",
                        col_mappings={"email": "email", "region": "region", "score": "score"},
                    )
                ],
                attributes=[
                    DraftField(name="email", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                    DraftField(name="region", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                    DraftField(name="score", logical_type="INT32", nullable=True, is_pii=False),
                ],
            )
        ],
    )
    locks = EvolutionLockSpec(
        wave_number=1,
        locked_hub_bks={"hub_account": ["account_id"]},
        locked_sat_hash_diffs={"sat_account_crm": ["email", "region", "score"]},
        sat_epochs={"sat_account_crm": 1},
    )

    # Unlocked SET_BKCC & SPLIT & PROMOTE_COLUMN_TO_HUB
    res_unlocked = apply_bucket_a_operators(
        base,
        [
            BucketAOperatorCall(
                operator="SET_BKCC",
                target_hub="hub_account",
                table_id="crm:acct",
                source_column="account_id",
                params={"bkcc_value": "CRM_V2", "bkcc_col": "tenant_id"},
            ),
            BucketAOperatorCall(
                operator="SPLIT_HIGH_FREQ_OR_PII_SAT",
                sat_name="sat_account_crm",
                target_hub="hub_account",
                params={"new_sat_name": "sat_account_crm_pii", "split_reason": "PII", "columns_csv": "email"},
            ),
            BucketAOperatorCall(
                operator="PROMOTE_COLUMN_TO_HUB",
                sat_name="sat_account_crm",
                table_id="crm:acct",
                source_column="region",
                params={"new_hub_name": "hub_region", "new_link_name": "lnk_account_region"},
            ),
        ],
        locks=None,
    )
    assert not res_unlocked.requires_bucket_b
    assert res_unlocked.draft_ir.hubs[0].business_keys[0].bkcc_value == "CRM_V2"
    assert any(s.sat_name == "sat_account_crm_pii" and s.sat_kind == "PII" for s in res_unlocked.draft_ir.satellites)
    assert any(h.hub_name == "hub_region" for h in res_unlocked.draft_ir.hubs)

    # Locked SPLIT + locked INJECT_MAS_CDK reroute cleanly; conflicting SET_BKCC escalates to Bucket B
    res_locked = apply_bucket_a_operators(
        base,
        [
            BucketAOperatorCall(
                operator="SPLIT_HIGH_FREQ_OR_PII_SAT",
                sat_name="sat_account_crm",
                target_hub="hub_account",
                params={"new_sat_name": "sat_account_crm_pii", "split_reason": "PII", "columns_csv": "email"},
            ),
            BucketAOperatorCall(
                operator="INJECT_MAS_CDK",
                sat_name="sat_account_crm",
                source_column="seq_no",
                params={"cdk_col": "seq_no"},
            ),
            BucketAOperatorCall(
                operator="SET_BKCC",
                target_hub="hub_account",
                table_id="crm:acct",
                source_column="account_id",
                params={"bkcc_value": "DIFFERENT_BKCC"},
            ),
        ],
        locks=locks,
    )
    assert res_locked.requires_bucket_b
    assert len(res_locked.bucket_b_escalations) == 1
    assert "sat_account_crm_ext_v2" in res_locked.rerouted_entities
    assert "mas_account_crm" in res_locked.rerouted_entities


def test_e2e2_ds_cegar_12trap_l1_v1_v5_and_v6_port() -> None:
    """E2E-2: Real ds_cegar_12trap.duckdb + wave2_ambiguous_otc_collateral + V6 mock port."""
    trap_prof = profile_source("trap_db", "duckdb", str(PUBLIC_AM / "ds_cegar_12trap.duckdb"))
    otc_prof = profile_source("w2_otc", "csv", str(PUBLIC_AM / "wave2_ambiguous_otc_collateral.csv"))
    profiles = [trap_prof, otc_prof]
    tbl_by_name = {t.table_name: t for t in trap_prof.tables}

    crm_id = tbl_by_name["trap_crm_accounts"].table_id
    ibor_id = tbl_by_name["trap_ibor_accounts"].table_id
    ballot_id = tbl_by_name["trap_ballots_mas"].table_id
    snap_id = tbl_by_name["trap_snapshots_churn"].table_id
    mgr_id = tbl_by_name["trap_manager_eff"].table_id
    trd_id = tbl_by_name["trap_early_trades"].table_id

    draft = DraftVaultIR(
        project_id="trap_suite",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
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
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="trap_db", table_id=crm_id, col_mappings={"account_id": "account_id"}),
                    DraftSourceBinding(source_id="trap_db", table_id=ibor_id, col_mappings={"account_id": "account_id"}),
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
                    DraftSourceBinding(source_id="trap_db", table_id=ibor_id, col_mappings={"executing_broker_lei": "executing_broker_lei"})
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
                    DraftSourceBinding(source_id="trap_db", table_id=ballot_id, col_mappings={"iso_currency_code": "iso_currency_code"})
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
                    DraftSourceBinding(source_id="trap_db", table_id=mgr_id, col_mappings={"portfolio_manager_id": "portfolio_manager_id"})
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
            # TRAP-12: is_planned_concept=True hub exempted from DV-COL-002 / DV-STR-013 / physical_hints
            DraftHubSpec(
                hub_name="hub_planned_clearing_house",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[],
                source_bindings=[],
                is_planned_concept=True,
            ),
        ],
        links=[
            # TRAP-07 + BUG-02: sparse binary link (executing_broker_lei null_ratio=0.5)
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
            # TRAP-08: Boomerang link -> Alg A.3 injects esat_account_manager
            DraftLinkSpec(
                link_name="lnk_account_manager",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_account",
                        role_name="ACCOUNT",
                        fk_col_name="hk_account",
                        source_col_names=["account_id"],
                        target_hub_binding_table_id=crm_id,
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
        ],
        satellites=[
            # TRAP-09: 3 PII cols + mixed volatility cols on trap_crm_accounts
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
            # TRAP-02: UTF-8 issuer satellite on hub_issuer ('Nordisk Suverän Fond')
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
            # TRAP-04: MAS with proposal_seq CDK
            DraftSatelliteSpec(
                sat_name="mas_ballot_proposals",
                sat_kind="MAS",
                parent_name="hub_ballot",
                child_dependent_keys=["proposal_seq"],
                source_bindings=[
                    DraftSourceBinding(source_id="trap_db", table_id=ballot_id, col_mappings={"proposal_seq": "proposal_seq", "shares_voted": "shares_voted"})
                ],
                attributes=[
                    DraftField(name="proposal_seq", logical_type="INT32", nullable=True, is_pii=False),
                    DraftField(name="shares_voted", logical_type="INT64", nullable=True, is_pii=False),
                ],
            ),
            # TRAP-11: Bi-temporal satellite with applied_dts_col
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
        ],
        column_triage=_triage_all(profiles, system_cols={"etl_snapshot_ts"}),
    )

    _, cert, _ = run_l1_codemods(
        draft,
        profiles=profiles,
        fk_turnover_by_table={mgr_id: {"portfolio_manager_id": 0.67}},
    )
    # TRAP-06: iso_currency_code demoted to ref_currency_code
    assert any(r.ref_name == "ref_currency_code" for r in cert.reference_tables)
    # TRAP-09: 3 PII columns isolated into sat_account_crm_pii
    pii_sat = next(s for s in cert.satellites if s.sat_name == "sat_account_crm_pii")
    assert {a.name for a in pii_sat.attributes} == {
        "beneficial_owner_tax_id",
        "client_phone",
        "stewardship_officer_email",
    }
    # TRAP-08: esat_account_manager injected with strict-subset driving_keys
    esat = next(s for s in cert.satellites if s.sat_kind == "ESAT")
    assert esat.driving_keys == ["hk_portfolio_manager"] or esat.driving_keys == ["hk_account"]
    # TRAP-12: planned concept hub has physical_hints=None and passes V1
    planned = next(h for h in cert.hubs if h.hub_name == "hub_planned_clearing_house")
    assert planned.is_planned_concept and planned.physical_hints is None

    # Mock IRRuntimeProver port (V6)
    class _MockProver:
        def prove(self, certified_ir: CertifiedVaultIR, profs: list[SourceProfile], *, policy: str = "balanced", **kwargs: object) -> IRProverReport:
            _ = (certified_ir, profs, policy, kwargs)
            return IRProverReport(
                dyn_results={"DV-DYN-001": True, "DV-DYN-004": False},
                ast_violations=[],
                empirical_smells=[
                    VerifierDiagnostic(
                        gate="V6",
                        rule_id="DV-AP-011",
                        severity="WARN",
                        entity_name="sat_account_crm",
                        waivable=True,
                        message="100% delta churn from technical column.",
                    )
                ],
                counterexamples=[{"table": snap_id, "col": "etl_snapshot_ts", "churn_pct": 100.0}],
            )

    rep_clean = run_v1_to_v6_gates(cert, VerifierContext(profiles=profiles))
    assert rep_clean.v1_v6_fail_count == 0, rep_clean.v1_v6_diagnostics

    rep_with_prover = run_v1_to_v6_gates(
        cert, VerifierContext(profiles=profiles), prover=_MockProver()  # type: ignore[arg-type]
    )
    assert rep_with_prover.v1_v6_fail_count == 1
    assert any(d.gate == "V6" and d.rule_id == "DV-DYN-004" for d in rep_with_prover.v1_v6_diagnostics)
    assert any(d.rule_id == "DV-AP-011" for d in rep_with_prover.v6b_diagnostics)


def test_alg_a1_3axis_velocity_split_triggers_above_50mb_and_skips_below() -> None:
    """Verify Alg A.1 3-part YAGNI guard (50 MB/day) and micro-satellite merge."""
    tbl_id = "src:wide_tbl"
    cols = [
        ColumnProfile(col_name=f"c_{i:02d}", sql_type="VARCHAR", nullable=True, is_pk=False, null_ratio=0.0, distinct_count_est=10)
        for i in range(12)
    ]
    prof = SourceProfile(
        source_id="src",
        schema_fingerprint="fp1",
        table_count=1,
        tables=[TableProfile(table_id=tbl_id, table_name="wide_tbl", row_count_est=100, columns=cols)],
    )
    attrs = [
        DraftField(
            name=f"c_{i:02d}",
            logical_type="UTF8_STRING",
            nullable=True,
            is_pii=False,
            classification_tag="HIGH_FREQ" if i >= 10 else "STATIC",
        )
        for i in range(12)
    ]
    draft = DraftVaultIR(
        project_id="yagni",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_entity",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[BusinessKeyPart(table_id=tbl_id, col_name="c_00", source_id="src", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_entity",
                sat_kind="STANDARD",
                parent_name="hub_entity",
                source_bindings=[DraftSourceBinding(source_id="src", table_id=tbl_id, col_mappings={"c_00": "c_00"})],
                attributes=attrs,
            )
        ],
    )
    # Small entity count (< 50 MB/day): YAGNI guard keeps single Satellite
    _, cert_small, _ = run_l1_codemods(draft, profiles=[prof])
    assert [s.sat_name for s in cert_small.satellites] == ["sat_entity"]

    # High entity count (> 50 MB/day): splits into _static and _high_freq
    _, cert_big, _ = run_l1_codemods(
        draft, profiles=[prof], entity_count_overrides={tbl_id: 5_000_000}
    )
    sat_names = {s.sat_name for s in cert_big.satellites}
    assert "sat_entity_static" in sat_names
    assert "sat_entity_high_freq" in sat_names


def test_e2e3_real_pdfs_occurrence2_crlf_and_offline_receipts() -> None:
    """E2E-3: Real PDFs (am_custody_and_voting_guide + sample_pdfs) & METADATA_ONLY_BINARY_ABSENT."""
    builder = DocumentMapBuilder()
    doc_map, pages = builder.build_from_path(
        source_id="custody_guide",
        path=PUBLIC_AM / "am_custody_and_voting_guide.pdf",
        doc_format="pdf",
        run_ocr=False,
    )
    scan_map, _ = builder.build_from_path(
        source_id="scanned_schedule",
        path=SAMPLE_PDFS / "institutional_scanned_schedule_ocr_test.pdf",
        doc_format="pdf",
        run_ocr=False,
    )

    quote = "CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE"
    # Inject \r\n + multiple spaces to exercise BUG-04 normalization
    raw_p5_crlf = pages[4].replace("\n", "\r\n").replace(" ", "   ")
    norm_p5 = normalize_page_text(raw_p5_crlf)
    assert norm_p5[258:299] == quote

    valid_prov_attrs = [
        DraftField(name="extractor_version", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        DraftField(name="prompt_or_rule_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        DraftField(name="confidence_score", logical_type=DecimalType(precision=5, scale=4), nullable=False, is_pii=False),
        DraftField(name="review_status", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        DraftField(name="citation_span", logical_type="JSON_DOCUMENT", nullable=False, is_pii=False),
        DraftField(name="source_doc_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
    ]
    cit_p5 = DocumentCitationRef(
        doc_id=doc_map.doc_id,
        page_number=5,
        verbatim_quote=quote,
        occurrence=2,
        char_start=258,
        char_end=299,
        content_sha256=doc_map.pages[4].canonical_text_sha256,
    )
    cit_p4_ocr = DocumentCitationRef(
        doc_id=doc_map.doc_id,
        page_number=4,
        verbatim_quote="Custody Schedule Tier 1",
        occurrence=1,
        char_start=0,
        char_end=23,
        content_sha256=compute_canonical_text_sha256("Custody Schedule Tier 1"),
    )
    ocr_rec = OcrPageReceipt(
        receipt_id="ocr-p4",
        doc_id=doc_map.doc_id,
        page_number=4,
        ocr_text_masked="Custody Schedule Tier 1",
        content_sha256=compute_canonical_text_sha256("Custody Schedule Tier 1"),
        ocr_box_count=2,
    )
    mcp_rec = ExternalMcpReceipt(
        receipt_id="mcp-001",
        server_id="sec-edgar",
        tool_name="fetch_filing",
        doc_id=scan_map.doc_id,
        page_number=2,
        args_sha256="a" * 64,
        content_sha256="b" * 64,
        masked_excerpt="Collateral haircut schedule approved.",
    )
    mcp_ref = ExternalMcpEvidenceRef(
        receipt_id="mcp-001",
        server_id="sec-edgar",
        tool_name="fetch_filing",
        content_sha256="b" * 64,
        masked_excerpt="Collateral haircut schedule approved.",
    )

    draft = DraftVaultIR(
        project_id="pdf_v4",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_document",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[BusinessKeyPart(table_id="doc", col_name="doc_id", source_id=doc_map.doc_id, key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
            ),
            DraftHubSpec(
                hub_name="hub_clause",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[BusinessKeyPart(table_id="doc", col_name="clause_id", source_id=doc_map.doc_id, key_tier="TIER_2_ENTERPRISE_SOR", bkcc_value="STEWARDSHIP")],
            ),
        ],
        links=[
            DraftLinkSpec(
                link_name="lnk_doc_clause",
                link_kind="UNSTRUCTURED_EXTRACTION_LINK",
                participants=[
                    LinkParticipantSpec(hub_name="hub_document", role_name="DOC", fk_col_name="hk_document", source_col_names=["doc_id"]),
                    LinkParticipantSpec(hub_name="hub_clause", role_name="CLAUSE", fk_col_name="hk_clause", source_col_names=["clause_id"]),
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=doc_map.doc_id,
                        table_id="doc",
                        col_mappings={"doc_id": "doc_id"},
                        doc_citations=[cit_p5],
                        mcp_evidence=[mcp_ref],
                    )
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_doc_clause_prov",
                sat_kind="AI_PROVENANCE_SATELLITE",
                parent_name="lnk_doc_clause",
                source_bindings=[
                    DraftSourceBinding(source_id=doc_map.doc_id, table_id="doc", col_mappings={"review_status": "review_status"})
                ],
                attributes=valid_prov_attrs,
            )
        ],
    )
    _, cert, _ = run_l1_codemods(draft)
    # BUG-03: L1 enforced require_review_status="APPROVED"
    lnk_cert = cert.links[0]
    assert lnk_cert.load_semantics is not None
    assert lnk_cert.load_semantics.require_review_status == "APPROVED"

    # Mode 1: Raw normalized page text present -> PASS
    v4_raw = verify_v4_unstructured(
        cert,
        VerifierContext(
            document_maps=[doc_map, scan_map],
            parsed_doc_pages={doc_map.doc_id: {5: raw_p5_crlf}},
            mcp_receipts=[mcp_rec],
        ),
    )
    assert v4_raw == [], v4_raw

    # Mode 2: METADATA_ONLY_BINARY_ABSENT (no parsed_doc_pages) -> stored digest + OCR + MCP receipt PASS
    cert_ocr = cert.model_copy(
        update={
            "links": [
                lnk_cert.model_copy(
                    update={
                        "source_bindings": [
                            lnk_cert.source_bindings[0].model_copy(update={"doc_citations": [cit_p5, cit_p4_ocr]})
                        ]
                    }
                )
            ]
        }
    )
    # For OCR page 4 we align DocumentMap page 4 sha256 to the OCR receipt in offline bundle
    doc_map_ocr = doc_map.model_copy(
        update={
            "pages": [
                p.model_copy(update={"canonical_text_sha256": ocr_rec.content_sha256}) if p.page_number == 4 else p
                for p in doc_map.pages
            ]
        }
    )
    v4_offline = verify_v4_unstructured(
        cert_ocr,
        VerifierContext(
            document_maps=[doc_map_ocr],
            ocr_receipts=[ocr_rec],
            mcp_receipts=[mcp_rec],
            metadata_only_binary_absent=True,
        ),
    )
    assert v4_offline == [], v4_offline

    # Mode 3: Tampered sha256, <MASKED_PII> leak, Rule U1 raw_blob, and MCP digest/leak fail V4
    bad_cit_leak = cit_p5.model_copy(update={"verbatim_quote": "Email: <MASKED_PII>"})
    bad_cit_sha = cit_p5.model_copy(update={"content_sha256": "0" * 64})
    bad_mcp_sha = mcp_ref.model_copy(update={"content_sha256": "c" * 64})
    bad_sat = cert.satellites[0].model_copy(
        update={
            "sat_kind": "STANDARD",
            "attributes": [DraftField(name="raw_blob_pdf", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
        }
    )
    bad_cert = cert.model_copy(
        update={
            "links": [
                lnk_cert.model_copy(
                    update={
                        "source_bindings": [
                            lnk_cert.source_bindings[0].model_copy(
                                update={"doc_citations": [bad_cit_leak, bad_cit_sha], "mcp_evidence": [bad_mcp_sha]}
                            )
                        ]
                    }
                )
            ],
            "satellites": [bad_sat],
        }
    )
    v4_fail = verify_v4_unstructured(
        bad_cert,
        VerifierContext(document_maps=[doc_map], mcp_receipts=[mcp_rec]),
    )
    rule_ids = {d.rule_id for d in v4_fail}
    assert "REDACTED_PLACEHOLDER_LEAK" in rule_ids
    assert "CITATION_SPAN_UNRESOLVED" in rule_ids
    assert "BLOB_OR_VEC_IN_RAW_SAT" in rule_ids
    assert "AI_PROVENANCE_MISSING" in rule_ids


def test_e2e4_scale_200_shard_merge_and_s6_03_unresolved_triage() -> None:
    """E2E-4: 220 tables (1,760 cols) cluster merge + S6-03 UNRESOLVED triage -> DV-COL-001."""
    tables = build_scale_200_table_profiles()
    assert len(tables) == 220
    clusters = cluster_tables(tables)
    assert len(clusters.clusters) == 11
    prof = SourceProfile(source_id="scale_200", schema_fingerprint="scale220", table_count=220, tables=tables)

    shards: list[DraftShardIR] = []
    for c_idx, c_tbls in enumerate(clusters.clusters):
        cid = f"cluster_{c_idx:02d}"
        tbl_map = {t.table_id: t for t in tables if t.table_id in c_tbls or t.table_name in c_tbls}
        domain = next(iter(tbl_map.values())).table_name.split("_")[1]
        anchor_tbl = next(t for t in tbl_map.values() if t.table_name.endswith("_tbl_000"))
        shards.append(
            DraftShardIR(
                cluster_id=cid,
                hubs=[
                    DraftHubSpec(
                        hub_name=f"hub_{domain}",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        business_keys=[
                            BusinessKeyPart(
                                table_id=anchor_tbl.table_id,
                                col_name=f"{domain}_id",
                                source_id="scale_200",
                                key_tier="TIER_3_LOCAL_SCOPED",
                                bkcc_value=domain.upper(),
                            )
                        ],
                        source_bindings=[
                            DraftSourceBinding(
                                source_id="scale_200",
                                table_id=anchor_tbl.table_id,
                                col_mappings={f"{domain}_id": f"{domain}_id"},
                            )
                        ],
                    )
                ],
                # Deliberately omit columns from column_triage so S6-03 injects UNRESOLVED
                column_triage=[],
            )
        )

    merged = merge_draft_shards(
        shards, project_id="scale220", ontology_version="1.0.0", wave_number=1
    )
    assert len(merged.hubs) == 11

    with_unresolved = inject_unresolved_triage(merged, [prof])
    assert len(with_unresolved.column_triage) == 1760
    assert all(e.disposition == "UNRESOLVED" for e in with_unresolved.column_triage)

    _, cert_unresolved, _ = run_l1_codemods(merged, profiles=[prof])
    v1_block = verify_v1_structure(cert_unresolved, profiles=[prof])
    assert sum(1 for d in v1_block if d.rule_id == "DV-COL-001") == 1760

    # Once all 1,760 columns are triaged, V1 passes cleanly
    triaged = merged.model_copy(update={"column_triage": _triage_all([prof])})
    _, cert_ok, _ = run_l1_codemods(triaged, profiles=[prof])
    v1_ok = [d for d in verify_v1_structure(cert_ok, profiles=[prof]) if d.severity == "BLOCKER"]
    assert v1_ok == []


def test_v1_v2_v3_v5_negative_invariants_and_sal_and_z1_purity() -> None:
    """Verify V1 DV-STR-*, V2 BUG-08/sort, V3 load_end_dts/applied_dts, and V5 DV-AP-010."""
    draft = DraftVaultIR(
        project_id="neg_test",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_alpha",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id="s:t1",
                        col_name="alpha_code",
                        source_id="s",
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t1", col_mappings={"alpha_code": "alpha_code"})],
            ),
            DraftHubSpec(
                hub_name="hub_beta",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id="s:t1",
                        col_name="beta_code",
                        source_id="s",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="SOR",
                    )
                ],
                source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t1", col_mappings={"beta_code": "beta_code"})],
            ),
        ],
        links=[
            DraftLinkSpec(
                link_name="lnk_ab",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(hub_name="hub_alpha", role_name="A", fk_col_name="hk_alpha", source_col_names=["alpha_code"]),
                    LinkParticipantSpec(hub_name="hub_beta", role_name="B", fk_col_name="hk_beta", source_col_names=["beta_code"]),
                ],
                source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t1", col_mappings={"alpha_code": "alpha_code"})],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_alpha",
                sat_kind="STANDARD",
                parent_name="hub_alpha",
                is_bitemporal=True,
                applied_dts_col="load_dts",  # Invalid TMP-02!
                source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t1", col_mappings={"val": "val"})],
                attributes=[
                    DraftField(name="val", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                    DraftField(name="load_end_dts", logical_type="TIMESTAMP_UTC_US", nullable=True, is_pii=False),
                ],
            )
        ],
    )
    _, cert, _ = run_l1_codemods(draft)

    # V3 catches load_end_dts (DV-COL-011) and applied_dts == load_dts (TMP-02)
    v3_diags = verify_v3_sentinels_and_temporal(cert)
    v3_rules = {d.rule_id for d in v3_diags}
    assert "DV-COL-011" in v3_rules
    assert "TMP-02" in v3_rules

    # V5 catches IR-only DV-AP-010 on StagingViewSpec and Z3 1:1 raw satellite mirror
    from opendv.models.vault_ir_certified import StagingViewSpec

    bad_cert = cert.model_copy(
        update={
            "staging_views": [
                StagingViewSpec(
                    view_name="stg_t1",
                    source_id="s",
                    source_table_id="s:t1",
                    col_lineage_mappings={"status": "CASE WHEN x=1 THEN 'A' ELSE 'B' END"},
                )
            ],
            "satellites": [
                *cert.satellites,
                cert.satellites[0].model_copy(update={"sat_name": "sat_alpha_z3_copy", "zone_id": "Z3"}),
            ],
        }
    )
    v5_diags = verify_v5_evolution(bad_cert)
    assert sum(1 for d in v5_diags if d.rule_id == "DV-AP-010") == 2

    # V2 catches unsorted SAT_HASH_DIFF and missing TIER_1 GLOBAL
    bad_sat = cert.satellites[0].model_copy(
        update={
            "hash_recipe": cert.satellites[0].hash_recipe.model_copy(  # type: ignore[union-attr]
                update={"sort_alphabetically": False, "source_columns": ["z", "a"]}
            )
        }
    )
    bad_hub = cert.hubs[0].model_copy(
        update={
            "hash_recipe": cert.hubs[0].hash_recipe.model_copy(update={"bkcc_literal": "WRONG"})  # type: ignore[union-attr]
        }
    )
    v2_diags = verify_v2_hash_and_bkcc(
        cert.model_copy(update={"hubs": [bad_hub, cert.hubs[1]], "satellites": [bad_sat]})
    )
    v2_rules = {d.rule_id for d in v2_diags}
    assert "DV-COL-008" in v2_rules
    assert "DV-COL-013" in v2_rules


def test_alg_a4_nary_split_alg_a9a_drop_rename_and_v1_sal_edge_cases() -> None:
    """Cover Alg A.4 N-ary sparse FK split, Alg A.9a EVO-02/03/04/05, and V1 sal_*/DAG checks."""
    tbl_id = "s:trade_event"
    prof_w1 = SourceProfile(
        source_id="s",
        schema_fingerprint="w1",
        table_count=1,
        tables=[
            TableProfile(
                table_id=tbl_id,
                table_name="trade_event",
                row_count_est=100,
                columns=[
                    ColumnProfile(col_name="account_id", sql_type="VARCHAR", nullable=False, is_pk=True, null_ratio=0.0, distinct_count_est=100),
                    ColumnProfile(col_name="security_id", sql_type="VARCHAR", nullable=False, is_pk=False, null_ratio=0.0, distinct_count_est=50),
                    ColumnProfile(col_name="advisor_id", sql_type="VARCHAR", nullable=True, is_pk=False, null_ratio=0.40, distinct_count_est=10),
                    ColumnProfile(col_name="trade_notes", sql_type="VARCHAR", nullable=True, is_pk=False, null_ratio=0.0, distinct_count_est=80),
                    ColumnProfile(col_name="legacy_flag", sql_type="VARCHAR", nullable=True, is_pk=False, null_ratio=0.0, distinct_count_est=2),
                    ColumnProfile(col_name="qty_int", sql_type="INTEGER", nullable=True, is_pk=False, null_ratio=0.0, distinct_count_est=50),
                ],
            )
        ],
    )
    draft_w1 = DraftVaultIR(
        project_id="nary_a9a",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[BusinessKeyPart(table_id=tbl_id, col_name="account_id", source_id="s", key_tier="TIER_3_LOCAL_SCOPED", bkcc_value="OMS")],
                source_bindings=[DraftSourceBinding(source_id="s", table_id=tbl_id, col_mappings={"account_id": "account_id"})],
            ),
            DraftHubSpec(
                hub_name="hub_sec",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[BusinessKeyPart(table_id=tbl_id, col_name="security_id", source_id="s", key_tier="TIER_2_ENTERPRISE_SOR", bkcc_value="OMS")],
                source_bindings=[DraftSourceBinding(source_id="s", table_id=tbl_id, col_mappings={"security_id": "security_id"})],
            ),
            DraftHubSpec(
                hub_name="hub_advisor",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[BusinessKeyPart(table_id=tbl_id, col_name="advisor_id", source_id="s", key_tier="TIER_2_ENTERPRISE_SOR", bkcc_value="CRM")],
                source_bindings=[DraftSourceBinding(source_id="s", table_id=tbl_id, col_mappings={"advisor_id": "advisor_id"})],
            ),
        ],
        links=[
            # 3-way Link: 2 mandatory (account_id, security_id) + 1 sparse (advisor_id U_nn=0.60)
            DraftLinkSpec(
                link_name="lnk_trade",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(hub_name="hub_account", role_name="ACCOUNT", fk_col_name="hk_account", source_col_names=["account_id"]),
                    LinkParticipantSpec(hub_name="hub_sec", role_name="SEC", fk_col_name="hk_sec", source_col_names=["security_id"]),
                    LinkParticipantSpec(hub_name="hub_advisor", role_name="ADVISOR", fk_col_name="hk_advisor", source_col_names=["advisor_id"]),
                ],
                source_bindings=[DraftSourceBinding(source_id="s", table_id=tbl_id, col_mappings={"account_id": "account_id"})],
            ),
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_account_oms",
                sat_kind="STANDARD",
                parent_name="hub_account",
                source_bindings=[DraftSourceBinding(source_id="s", table_id=tbl_id, col_mappings={"trade_notes": "trade_notes", "legacy_flag": "legacy_flag", "qty_int": "qty_int"})],
                attributes=[
                    DraftField(name="trade_notes", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                    DraftField(name="legacy_flag", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                    DraftField(name="qty_int", logical_type="INT32", nullable=True, is_pii=False),
                ],
            )
        ],
        column_triage=_triage_all([prof_w1]),
    )
    draft_w1_out, cert_w1, _ = run_l1_codemods(draft_w1, profiles=[prof_w1])
    # Alg A.4 BRK-02b: retains all 3 participants on lnk_trade with nullable advisor_id preserved
    kernel = next(l for l in cert_w1.links if l.link_name == "lnk_trade")
    assert len(kernel.participants) == 3
    adv_spec = next(
        ps
        for ps in (kernel.hash_recipe.participant_specs if kernel.hash_recipe else [])
        if ps.hub_name == "hub_advisor"
    )
    assert adv_spec.is_nullable is True

    # Wave 2: rename trade_notes -> trade_note (EVO-03), drop legacy_flag (EVO-02), widen qty_int -> INT64 (EVO-04)
    locks_w1 = build_evolution_lock_from_certified(cert_w1)
    prof_w2 = SourceProfile(
        source_id="s",
        schema_fingerprint="w2",
        table_count=1,
        tables=[
            TableProfile(
                table_id=tbl_id,
                table_name="trade_event",
                row_count_est=120,
                columns=[
                    ColumnProfile(col_name="account_id", sql_type="VARCHAR", nullable=False, is_pk=True, null_ratio=0.0, distinct_count_est=120),
                    ColumnProfile(col_name="security_id", sql_type="VARCHAR", nullable=False, is_pk=False, null_ratio=0.0, distinct_count_est=50),
                    ColumnProfile(col_name="advisor_id", sql_type="VARCHAR", nullable=True, is_pk=False, null_ratio=0.40, distinct_count_est=10),
                    ColumnProfile(col_name="trade_note", sql_type="VARCHAR", nullable=True, is_pk=False, null_ratio=0.0, distinct_count_est=80),
                    ColumnProfile(col_name="qty_int", sql_type="BIGINT", nullable=True, is_pk=False, null_ratio=0.0, distinct_count_est=50),
                ],
            )
        ],
    )
    sat_w2 = draft_w1_out.satellites[0].model_copy(
        update={
            "attributes": [
                DraftField(name="trade_notes", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                DraftField(name="qty_int", logical_type="INT64", nullable=True, is_pii=False),
            ]
        }
    )
    draft_w2 = draft_w1_out.model_copy(
        update={
            "wave_number": 2,
            "satellites": [sat_w2],
            "column_triage": _triage_all([prof_w2]),
        }
    )
    _, cert_w2, _ = run_l1_codemods(draft_w2, profiles=[prof_w2], locks=locks_w1, prior_ir=cert_w1)
    stg = cert_w2.staging_views[0]
    assert stg.col_lineage_mappings["trade_notes"] == "trade_note AS trade_notes"
    assert stg.col_lineage_mappings["legacy_flag"].startswith("CAST(NULL AS ")
    assert verify_v5_evolution(cert_w2, locks=locks_w1, prior_ir=cert_w1) == []

    # V1 rejects inverted (DUPLICATE, MASTER) sal_* ordering (DV-STR-015) and ESAT non-strict driving_keys (DV-STR-007)
    bad_sal = cert_w1.links[0].model_copy(
        update={
            "link_name": "sal_bad",
            "link_kind": "SAME_AS",
            "zone_id": "Z3",
            "participants": [
                LinkParticipantSpec(hub_name="hub_account", role_name="DUPLICATE", fk_col_name="hk_dup", source_col_names=["account_id"]),
                LinkParticipantSpec(hub_name="hub_account", role_name="MASTER", fk_col_name="hk_mst", source_col_names=["account_id"]),
            ],
        }
    )
    bad_esat = cert_w1.satellites[0].model_copy(
        update={
            "sat_name": "esat_bad",
            "sat_kind": "ESAT",
            "parent_name": "lnk_trade",
            "driving_keys": ["hk_account", "hk_sec", "hk_advisor"],  # Not a strict subset!
        }
    )
    v1_bad = verify_v1_structure(
        cert_w1.model_copy(
            update={"links": [*cert_w1.links, bad_sal], "satellites": [*cert_w1.satellites, bad_esat]}
        )
    )
    v1_rules = {d.rule_id for d in v1_bad}
    assert "DV-STR-015" in v1_rules
    assert "DV-STR-007" in v1_rules


def test_remediation_l1_evo02_pii_alg_a1_a2_and_bucket_a() -> None:
    """Regression coverage for EVO-02 typed NULL, Wave-2 PII/nullability, A.1/A.2, and Bucket A fixes."""
    tbl_a = "s:orders_a"
    tbl_b = "s:orders_b"
    prof_w1 = SourceProfile(
        source_id="s",
        schema_fingerprint="remed_w1",
        table_count=2,
        tables=[
            TableProfile(
                table_id=tbl_a,
                table_name="orders_a",
                row_count_est=10,
                columns=[
                    ColumnProfile(col_name="order_id", sql_type="VARCHAR", nullable=False, is_pk=True, null_ratio=0.0, distinct_count_est=10),
                    ColumnProfile(col_name="qty_i32", sql_type="INTEGER", nullable=True, is_pk=False, null_ratio=0.0, distinct_count_est=5),
                    ColumnProfile(col_name="amt_dec", sql_type="DECIMAL(18,4)", nullable=True, is_pk=False, null_ratio=0.0, distinct_count_est=5),
                    ColumnProfile(col_name="keep_null", sql_type="VARCHAR", nullable=True, is_pk=False, null_ratio=0.1, distinct_count_est=5),
                ],
            ),
            TableProfile(
                table_id=tbl_b,
                table_name="orders_b",
                row_count_est=10,
                columns=[
                    ColumnProfile(col_name="order_id", sql_type="VARCHAR", nullable=False, is_pk=True, null_ratio=0.0, distinct_count_est=10),
                    ColumnProfile(col_name="notes_b", sql_type="VARCHAR", nullable=True, is_pk=False, null_ratio=0.0, distinct_count_est=5),
                ],
            ),
        ],
    )
    draft_w1 = DraftVaultIR(
        project_id="remed_l1",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_order",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(table_id=tbl_a, col_name="order_id", source_id="s", key_tier="TIER_3_LOCAL_SCOPED", bkcc_value="OMS"),
                    BusinessKeyPart(table_id=tbl_b, col_name="order_id", source_id="s", key_tier="TIER_3_LOCAL_SCOPED", bkcc_value="OMS_B"),
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="s", table_id=tbl_a, col_mappings={"order_id": "order_id"}),
                    DraftSourceBinding(source_id="s", table_id=tbl_b, col_mappings={"order_id": "order_id"}),
                ],
            ),
            DraftHubSpec(
                hub_name="hub_issuer_cik",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(table_id=tbl_a, col_name="cik", source_id="s", key_tier="TIER_2_ENTERPRISE_SOR", bkcc_value="SEC_EDGAR"),
                ],
                source_bindings=[DraftSourceBinding(source_id="s", table_id=tbl_a, col_mappings={"cik": "cik"})],
            ),
            # Empty-BK hub matching ontology enum must not crash Alg A.2
            DraftHubSpec(hub_name="hub_empty_enum", key_tier="TIER_1_GLOBAL_STANDARD", business_keys=[]),
        ],
        satellites=[
            # Multi-table same-source_id sat -> disambiguated sat_names
            DraftSatelliteSpec(
                sat_name="sat_order_multi",
                sat_kind="STANDARD",
                parent_name="hub_order",
                source_bindings=[
                    DraftSourceBinding(source_id="s", table_id=tbl_a, col_mappings={"qty_i32": "qty_i32", "amt_dec": "amt_dec", "keep_null": "keep_null"}),
                    DraftSourceBinding(source_id="s", table_id=tbl_b, col_mappings={"notes_b": "notes_b"}),
                ],
                attributes=[
                    DraftField(name="qty_i32", logical_type="INT32", nullable=True, is_pii=False),
                    DraftField(name="amt_dec", logical_type=DecimalType(precision=18, scale=4), nullable=True, is_pii=False),
                    DraftField(name="keep_null", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                    DraftField(name="notes_b", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                ],
            ),
            # Micro-sats with entity_count_overrides > 50MB and one bitemporal sat
            DraftSatelliteSpec(
                sat_name="sat_m0",
                sat_kind="STANDARD",
                parent_name="hub_issuer_cik",
                source_bindings=[DraftSourceBinding(source_id="s", table_id=tbl_a, col_mappings={"m0": "m0"})],
                attributes=[DraftField(name="m0", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
            ),
            DraftSatelliteSpec(
                sat_name="sat_m1",
                sat_kind="STANDARD",
                parent_name="hub_issuer_cik",
                source_bindings=[DraftSourceBinding(source_id="s", table_id=tbl_a, col_mappings={"m1": "m1"})],
                attributes=[DraftField(name="m1", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
            ),
            DraftSatelliteSpec(
                sat_name="sat_m2_bi",
                sat_kind="STANDARD",
                parent_name="hub_issuer_cik",
                is_bitemporal=True,
                applied_dts_col="applied_dts",
                source_bindings=[DraftSourceBinding(source_id="s", table_id=tbl_a, col_mappings={"m2": "m2"})],
                attributes=[DraftField(name="m2", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
            ),
        ],
        column_triage=[
            # Pre-existing duplicate triage preferring MODELED over UNRESOLVED
            ColumnTriageEntry(table_id=tbl_a, col_name="order_id", disposition="UNRESOLVED", target_ref=None, rationale=""),
            ColumnTriageEntry(table_id=tbl_a, col_name="order_id", disposition="MODELED", target_ref="hub_order", rationale=""),
            *_triage_all([prof_w1])[1:],
        ],
    )
    ont_enum = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="test",
        enumerations=[
            OntologyEnumeration(
                enumeration_name="empty_enum",
                enum_uri="urn:test:empty_enum",
                target_reference_table="ref_empty_enum",
                codes={"A": "A"},
                description="Empty BK enum fixture.",
            )
        ],
    )
    draft_w1_out, cert_w1, _ = run_l1_codemods(
        draft_w1,
        profiles=[prof_w1],
        ontology=ont_enum,
        entity_count_overrides={tbl_a: 10_000_000},
    )
    # No duplicate sat_names
    sat_names = [s.sat_name for s in cert_w1.satellites]
    assert len(sat_names) == len(set(sat_names))
    # TIER_2 cik keeps SEC_EDGAR (not promoted to GLOBAL)
    cik_hub = next(h for h in cert_w1.hubs if h.hub_name == "hub_issuer_cik")
    assert cik_hub.key_tier == "TIER_2_ENTERPRISE_SOR"
    assert cik_hub.business_keys[0].bkcc_value == "SEC_EDGAR"
    # Micro-sat merge did not merge when entity_count_overrides > 50MB and preserved bitemporal sat_m2_bi
    assert any(s.sat_name == "sat_m2_bi" and s.is_bitemporal for s in cert_w1.satellites)
    # Hub TIER_3 recipe deduplicates identical col_name "order_id"
    ord_hub = next(h for h in cert_w1.hubs if h.hub_name == "hub_order")
    assert ord_hub.hash_recipe is not None
    assert ord_hub.hash_recipe.source_columns == ["order_id"]
    # Duplicate pre-existing triage resolved to MODELED
    ord_triage = [e for e in cert_w1.column_triage if e.table_id == tbl_a and e.col_name == "order_id"]
    assert len(ord_triage) == 1 and ord_triage[0].disposition == "MODELED"

    # Wave 2: drop qty_i32 (INT32) & amt_dec (DECIMAL), tighten keep_null->False, add customer_email (PII)
    locks_w1 = build_evolution_lock_from_certified(cert_w1)
    sat_a_name = next(s.sat_name for s in cert_w1.satellites if s.source_table_id == tbl_a and "qty_i32" in [a.name for a in s.attributes])
    prof_w2 = SourceProfile(
        source_id="s",
        schema_fingerprint="remed_w2",
        table_count=1,
        tables=[
            TableProfile(
                table_id=tbl_a,
                table_name="orders_a",
                row_count_est=10,
                columns=[
                    ColumnProfile(col_name="order_id", sql_type="VARCHAR", nullable=False, is_pk=True, null_ratio=0.0, distinct_count_est=10),
                    ColumnProfile(col_name="keep_null", sql_type="VARCHAR", nullable=False, is_pk=False, null_ratio=0.0, distinct_count_est=5),
                    ColumnProfile(col_name="customer_email", sql_type="VARCHAR", nullable=True, is_pk=False, null_ratio=0.0, distinct_count_est=10),
                ],
            )
        ],
    )
    sat_w2_draft = DraftSatelliteSpec(
        sat_name=sat_a_name,
        sat_kind="STANDARD",
        parent_name="hub_order",
        source_bindings=[DraftSourceBinding(source_id="s", table_id=tbl_a, col_mappings={"keep_null": "keep_null", "customer_email": "customer_email"})],
        attributes=[
            DraftField(name="keep_null", logical_type="UTF8_STRING", nullable=False, is_pii=False),
            DraftField(name="customer_email", logical_type="UTF8_STRING", nullable=True, is_pii=False),
        ],
    )
    _, cert_w2, _ = run_l1_codemods(
        draft_w1_out.model_copy(
            update={
                "wave_number": 2,
                "satellites": [
                    sat_w2_draft if s.sat_name == sat_a_name else s
                    for s in draft_w1_out.satellites
                ],
                "column_triage": _triage_all([prof_w2]),
            }
        ),
        profiles=[prof_w2],
        locks=locks_w1,
        prior_ir=cert_w1,
    )
    stg_a = next(st for st in cert_w2.staging_views if st.source_table_id == tbl_a)
    assert stg_a.col_lineage_mappings["qty_i32"] == "CAST(NULL AS INTEGER) AS qty_i32"
    assert stg_a.col_lineage_mappings["amt_dec"] == "CAST(NULL AS DECIMAL(18,4)) AS amt_dec"
    # PII routed to sat_*_pii (not EXT) and keep_null preserved as nullable=True
    pii_w2 = next(s for s in cert_w2.satellites if s.sat_kind == "PII")
    assert [a.name for a in pii_w2.attributes] == ["customer_email"]
    sat_w2_cert = next(s for s in cert_w2.satellites if s.sat_name == sat_a_name)
    assert next(a for a in sat_w2_cert.attributes if a.name == "keep_null").nullable is True
    assert verify_v5_evolution(cert_w2, locks=locks_w1, prior_ir=cert_w1) == []

    # Bucket A checks: PROMOTE_COLUMN_TO_HUB parent BK, unlocked PROMOTE_HUB_BK_TO_IMMUTABLE binding,
    # 3x locked INJECT_MAS_CDK + dedup_order_col, locked SPLIT(PII)->PII, unmatched locked SET_BKCC escalates
    bkt_draft = DraftVaultIR(
        project_id="bkt_remed",
        ontology_version="1.0.0",
        wave_number=2,
        hubs=[
            DraftHubSpec(
                hub_name="hub_acct",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[BusinessKeyPart(table_id="s:t1", col_name="acct_id", source_id="s", key_tier="TIER_2_ENTERPRISE_SOR", bkcc_value="CRM")],
                source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t1", col_mappings={"acct_id": "acct_id"})],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_acct",
                sat_kind="STANDARD",
                parent_name="hub_acct",
                source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t1", col_mappings={"custodian": "custodian", "email": "email"})],
                attributes=[
                    DraftField(name="custodian", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                    DraftField(name="email", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                ],
            )
        ],
    )
    _, cert_prom, bres1 = run_l1_codemods(
        bkt_draft,
        bucket_a_ops=[
            BucketAOperatorCall(
                operator="PROMOTE_COLUMN_TO_HUB",
                sat_name="sat_acct",
                table_id="s:t1",
                source_column="custodian",
                params={"new_hub_name": "hub_custodian", "new_link_name": "lnk_acct_cust"},
            ),
            BucketAOperatorCall(
                operator="PROMOTE_HUB_BK_TO_IMMUTABLE",
                target_hub="hub_acct",
                table_id="s:t2_new",
                source_column="acct_id",
                params={"new_bk_col": "lei_code", "source_id": "s2"},
            ),
        ],
    )
    assert bres1 is not None
    lnk_prom = next(l for l in cert_prom.links if l.link_name == "lnk_acct_cust")
    assert lnk_prom.hash_recipe is not None
    assert "hk_acct" not in lnk_prom.hash_recipe.source_columns
    assert lnk_prom.hash_recipe.source_columns == ["acct_id", "custodian"]
    acct_prom = next(h for h in cert_prom.hubs if h.hub_name == "hub_acct")
    assert acct_prom.source_bindings[0].table_id == "s:t2_new"

    locks_bkt = EvolutionLockSpec(
        wave_number=1,
        locked_hub_bks={"hub_acct": ["acct_id"]},
        locked_sat_hash_diffs={"sat_acct": ["custodian", "email"]},
        sat_epochs={"sat_acct": 1},
    )
    res_3mas = apply_bucket_a_operators(
        bkt_draft,
        [
            BucketAOperatorCall(operator="INJECT_MAS_CDK", sat_name="sat_acct", params={"cdk_col": "seq1", "dedup_order_col": "ts1"}),
            BucketAOperatorCall(operator="INJECT_MAS_CDK", sat_name="sat_acct", params={"cdk_col": "seq2", "dedup_order_col": "ts2"}),
            BucketAOperatorCall(operator="INJECT_MAS_CDK", sat_name="sat_acct", params={"cdk_col": "seq3"}),
            BucketAOperatorCall(
                operator="SPLIT_HIGH_FREQ_OR_PII_SAT",
                sat_name="sat_acct",
                target_hub="hub_acct",
                params={"new_sat_name": "sat_acct_pii", "split_reason": "PII", "columns_csv": "email"},
            ),
            BucketAOperatorCall(
                operator="SET_BKCC",
                target_hub="hub_acct",
                table_id="s:missing_tbl",
                params={"bkcc_value": "NEW_BKCC"},
            ),
        ],
        locks=locks_bkt,
    )
    mas_names = [s.sat_name for s in res_3mas.draft_ir.satellites if s.sat_kind == "MAS"]
    assert mas_names == ["mas_acct", "mas_acct_v2", "mas_acct_v3"]
    assert res_3mas.dedup_order_cols_by_sat["mas_acct"] == ["ts1"]
    pii_split = next(s for s in res_3mas.draft_ir.satellites if s.sat_name == "sat_acct_ext_v2")
    assert pii_split.sat_kind == "PII"
    assert res_3mas.requires_bucket_b and "SET_BKCC" not in res_3mas.applied_ops

    # SPLIT_HIGH_FREQ_OR_PII_SAT (VELOCITY) is not re-merged by Wave-1 micro-sat merge
    hf_draft = DraftVaultIR(
        project_id="hf_keep",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=bkt_draft.hubs,
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_main",
                sat_kind="STANDARD",
                parent_name="hub_acct",
                source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t1", col_mappings={"a": "a", "hf": "hf"})],
                attributes=[
                    DraftField(name="a", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                    DraftField(name="hf", logical_type="INT32", nullable=True, is_pii=False),
                ],
            ),
            DraftSatelliteSpec(
                sat_name="sat_extra1",
                sat_kind="STANDARD",
                parent_name="hub_acct",
                source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t1", col_mappings={"b": "b"})],
                attributes=[DraftField(name="b", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
            ),
            DraftSatelliteSpec(
                sat_name="sat_extra2",
                sat_kind="STANDARD",
                parent_name="hub_acct",
                source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t1", col_mappings={"c": "c"})],
                attributes=[DraftField(name="c", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
            ),
        ],
    )
    _, cert_hf, _ = run_l1_codemods(
        hf_draft,
        bucket_a_ops=[
            BucketAOperatorCall(
                operator="SPLIT_HIGH_FREQ_OR_PII_SAT",
                sat_name="sat_main",
                params={"new_sat_name": "sat_main_hf", "split_reason": "VELOCITY", "columns_csv": "hf"},
            )
        ],
    )
    assert {s.sat_name for s in cert_hf.satellites} >= {"sat_main", "sat_main_hf"}

    # Alg A.2 clears driving_hub and synthesizes sat + updates column_triage when dissolving binary link
    from opendv.verifier._l1_rules_alg import apply_alg_a2_ref_demotion

    p_enum = SourceProfile(
        source_id="s",
        schema_fingerprint="enum1",
        table_count=1,
        tables=[
            TableProfile(
                table_id="s:t",
                table_name="t",
                row_count_est=100,
                columns=[
                    ColumnProfile(col_name="item_id", sql_type="VARCHAR", nullable=False, is_pk=True, null_ratio=0.0, distinct_count_est=100),
                    ColumnProfile(col_name="other_id", sql_type="VARCHAR", nullable=False, is_pk=True, null_ratio=0.0, distinct_count_est=100),
                    ColumnProfile(col_name="status_code", sql_type="VARCHAR", nullable=False, is_pk=False, null_ratio=0.0, distinct_count_est=3, sample_values=["A", "B", "C"]),
                ],
            )
        ],
    )
    d_a2 = DraftVaultIR(
        project_id="a2_remed",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(hub_name="hub_item", key_tier="TIER_3_LOCAL_SCOPED", business_keys=[BusinessKeyPart(table_id="s:t", col_name="item_id", source_id="s", key_tier="TIER_3_LOCAL_SCOPED", bkcc_value="S")], source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t", col_mappings={"item_id": "item_id"})]),
            DraftHubSpec(hub_name="hub_other", key_tier="TIER_3_LOCAL_SCOPED", business_keys=[BusinessKeyPart(table_id="s:t", col_name="other_id", source_id="s", key_tier="TIER_3_LOCAL_SCOPED", bkcc_value="S")], source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t", col_mappings={"other_id": "other_id"})]),
            DraftHubSpec(hub_name="hub_status_code", key_tier="TIER_3_LOCAL_SCOPED", business_keys=[BusinessKeyPart(table_id="s:t", col_name="status_code", source_id="s", key_tier="TIER_3_LOCAL_SCOPED", bkcc_value="S")], source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t", col_mappings={"status_code": "status_code"})]),
        ],
        links=[
            DraftLinkSpec(
                link_name="lnk_nary",
                link_kind="STANDARD",
                driving_hub="hub_status_code",
                participants=[
                    LinkParticipantSpec(hub_name="hub_item", role_name="I", fk_col_name="hk_item", source_col_names=["item_id"]),
                    LinkParticipantSpec(hub_name="hub_other", role_name="O", fk_col_name="hk_other", source_col_names=["other_id"]),
                    LinkParticipantSpec(hub_name="hub_status_code", role_name="ST", fk_col_name="hk_status_code", source_col_names=["status_code"]),
                ],
                source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t", col_mappings={"item_id": "item_id"})],
            ),
            DraftLinkSpec(
                link_name="lnk_bin_dissolve",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(hub_name="hub_item", role_name="I", fk_col_name="hk_item", source_col_names=["item_id"]),
                    LinkParticipantSpec(hub_name="hub_status_code", role_name="ST", fk_col_name="hk_status_code", source_col_names=["status_code"]),
                ],
                source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t", col_mappings={"status_code": "status_code"})],
            ),
        ],
        column_triage=[
            ColumnTriageEntry(table_id="s:t", col_name="status_code", disposition="MODELED", target_ref="lnk_bin_dissolve", rationale=""),
        ],
    )
    d_a2_out, refs_a2 = apply_alg_a2_ref_demotion(d_a2, [p_enum])
    assert any(r.ref_name == "ref_status_code" for r in refs_a2)
    assert d_a2_out.links[0].driving_hub is None
    assert len(d_a2_out.links) == 1
    assert any("status_code" in [a.name for a in s.attributes] for s in d_a2_out.satellites)
    assert d_a2_out.column_triage[0].target_ref != "lnk_bin_dissolve"


def test_remediation_v1_v2_v4_v5_negative_and_boundary_gates() -> None:
    """Regression coverage for all Iteration-1 V1, V2, V4, and V5 bugs and negative paths."""
    from opendv.models.vault_ir_certified import StagingViewSpec

    draft = DraftVaultIR(
        project_id="neg_gates",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_a",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[BusinessKeyPart(table_id="s:t", col_name="a_id", source_id="s", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
                source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t", col_mappings={"a_id": "a_id"})],
            ),
            DraftHubSpec(
                hub_name="hub_b",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[BusinessKeyPart(table_id="s:t", col_name="b_id", source_id="s", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
                source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t", col_mappings={"b_id": "b_id"})],
            ),
        ],
        links=[
            DraftLinkSpec(
                link_name="lnk_ab",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(hub_name="hub_a", role_name="A", fk_col_name="hk_a", source_col_names=["a_id"]),
                    LinkParticipantSpec(hub_name="hub_b", role_name="B", fk_col_name="hk_b", source_col_names=["b_id"]),
                ],
                source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t", col_mappings={"a_id": "a_id"})],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_a",
                sat_kind="STANDARD",
                parent_name="hub_a",
                source_bindings=[DraftSourceBinding(source_id="s", table_id="s:t", col_mappings={"v1": "v1"})],
                attributes=[DraftField(name="v1", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
            )
        ],
    )
    _, cert, _ = run_l1_codemods(draft)

    # V1: DV-STR-001 (foreign hk_b on hub_a), DV-STR-006 (extends_sat_name cycle),
    # DV-STR-009 (t_lnk + PII), DV-STR-014 (cyclic HIERARCHICAL), DV-STR-015 (reflexive sal_*),
    # ANTI_SILO_VIOLATION + DV-G4-001
    bad_hub_fk = cert.hubs[0].model_copy(
        update={"attributes": [DraftField(name="hk_b", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False)]}
    )
    t_lnk = cert.links[0].model_copy(update={"link_name": "t_lnk_ab", "link_kind": "TRANSACTIONAL"})
    hal_ab = cert.links[0].model_copy(
        update={
            "link_name": "hal_ab",
            "link_kind": "HIERARCHICAL",
            "participants": [
                LinkParticipantSpec(hub_name="hub_a", role_name="PARENT", fk_col_name="hk_a_p", source_col_names=["a_id"]),
                LinkParticipantSpec(hub_name="hub_b", role_name="CHILD", fk_col_name="hk_b_c", source_col_names=["b_id"]),
            ],
        }
    )
    hal_ba = cert.links[0].model_copy(
        update={
            "link_name": "hal_ba",
            "link_kind": "HIERARCHICAL",
            "participants": [
                LinkParticipantSpec(hub_name="hub_b", role_name="PARENT", fk_col_name="hk_b_p", source_col_names=["b_id"]),
                LinkParticipantSpec(hub_name="hub_a", role_name="CHILD", fk_col_name="hk_a_c", source_col_names=["a_id"]),
            ],
        }
    )
    sal_refl = cert.links[0].model_copy(
        update={
            "link_name": "sal_refl",
            "link_kind": "SAME_AS",
            "zone_id": "Z3",
            "participants": [
                LinkParticipantSpec(hub_name="hub_a", role_name="MASTER", fk_col_name="hk_m", source_col_names=["a_id"]),
                LinkParticipantSpec(hub_name="hub_a", role_name="DUPLICATE", fk_col_name="hk_d", source_col_names=["a_id"]),
            ],
        }
    )
    sat_t_pii = cert.satellites[0].model_copy(
        update={"sat_name": "sat_t_pii", "sat_kind": "PII", "parent_name": "t_lnk_ab"}
    )
    sat_c1 = cert.satellites[0].model_copy(
        update={"sat_name": "sat_c1", "sat_kind": "EXT", "extends_sat_name": "sat_c2", "companion_epoch": 2}
    )
    sat_c2 = cert.satellites[0].model_copy(
        update={"sat_name": "sat_c2", "sat_kind": "EXT", "extends_sat_name": "sat_c1", "companion_epoch": 3}
    )
    ont_silo = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="t",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:t:shared",
                concept_name="ConceptA",
                description="Shared",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                table_bindings=[ConceptTableBinding(source_id="s", table_id="s:t", col_name="a_id")],
            )
        ],
    )
    hub_a_silo = cert.hubs[0].model_copy(update={"hub_name": "hub_alpha_silo"})
    hub_b_silo = cert.hubs[1].model_copy(
        update={
            "business_keys": [BusinessKeyPart(table_id="s:t", col_name="a_id", source_id="s", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")]
        }
    )
    hub_ungrounded = cert.hubs[1].model_copy(
        update={
            "hub_name": "hub_ungrounded",
            "business_keys": [BusinessKeyPart(table_id="s:t", col_name="z_id", source_id="s", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
        }
    )
    hal_refl = cert.links[0].model_copy(
        update={
            "link_name": "hal_refl",
            "link_kind": "HIERARCHICAL",
            "participants": [
                LinkParticipantSpec(hub_name="hub_a", role_name="PARENT", fk_col_name="hk_a_p", source_col_names=["a_id"]),
                LinkParticipantSpec(hub_name="hub_a", role_name="CHILD", fk_col_name="hk_a_c", source_col_names=["a_id"]),
            ],
        }
    )
    sat_t_ext = cert.satellites[0].model_copy(
        update={"sat_name": "sat_t_ext", "sat_kind": "EXT", "parent_name": "t_lnk_ab", "extends_sat_name": "sat_t_pii", "companion_epoch": 2}
    )
    v1_all = verify_v1_structure(
        cert.model_copy(
            update={
                "hubs": [bad_hub_fk, cert.hubs[1]],
                "links": [t_lnk, hal_ab, hal_ba, sal_refl],
                "satellites": [sat_t_pii, sat_t_ext, sat_c1, sat_c2],
            }
        )
    )
    assert {"DV-STR-001", "DV-STR-006", "DV-STR-009", "DV-STR-014", "DV-STR-015"}.issubset(
        {d.rule_id for d in v1_all}
    )
    v1_refl = verify_v1_structure(cert.model_copy(update={"links": [hal_refl]}))
    assert any(d.rule_id == "DV-STR-014" for d in v1_refl)
    v1_ont = verify_v1_structure(
        cert.model_copy(update={"hubs": [hub_a_silo, hub_b_silo, hub_ungrounded]}),
        ontology=ont_silo,
    )
    assert {"DV-G4-001", "ANTI_SILO_VIOLATION"}.issubset({d.rule_id for d in v1_ont})

    # V2: DV-COL-006 rejects empty SAT_HASH_DIFF.source_columns when sat has attributes
    sat_empty_hd = cert.satellites[0].model_copy(
        update={"hash_recipe": cert.satellites[0].hash_recipe.model_copy(update={"source_columns": []})}  # type: ignore[union-attr]
    )
    v2_empty = verify_v2_hash_and_bkcc(cert.model_copy(update={"satellites": [sat_empty_hd]}))
    assert any(d.rule_id == "DV-COL-006" for d in v2_empty)

    # V4: EXT Rule U1, masked_excerpt leak, and offline span length mismatch
    ext_blob = cert.satellites[0].model_copy(
        update={
            "sat_name": "sat_a_ext_v2",
            "sat_kind": "EXT",
            "extends_sat_name": "sat_a",
            "companion_epoch": 2,
            "attributes": [DraftField(name="embedding_vec", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
        }
    )
    builder = DocumentMapBuilder()
    dmap, _ = builder.build_from_path(
        source_id="cg",
        path=PUBLIC_AM / "am_custody_and_voting_guide.pdf",
        doc_format="pdf",
        run_ocr=False,
    )
    bad_span_cit = DocumentCitationRef(
        doc_id=dmap.doc_id,
        page_number=5,
        verbatim_quote="CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE",
        occurrence=2,
        char_start=258,
        char_end=265,
        content_sha256=dmap.pages[4].canonical_text_sha256,
    )
    leak_mcp_ref = ExternalMcpEvidenceRef(
        receipt_id="m1", server_id="srv", tool_name="t", content_sha256="a" * 64, masked_excerpt="SSN [REDACTED_SSN]"
    )
    leak_mcp_rec = ExternalMcpReceipt(
        receipt_id="m1", server_id="srv", tool_name="t", args_sha256="b" * 64, content_sha256="a" * 64, masked_excerpt="SSN [REDACTED_SSN]"
    )
    v4_ir = cert.model_copy(
        update={
            "satellites": [ext_blob],
            "links": [
                cert.links[0].model_copy(
                    update={
                        "source_bindings": [
                            cert.links[0].source_bindings[0].model_copy(
                                update={"doc_citations": [bad_span_cit], "mcp_evidence": [leak_mcp_ref]}
                            )
                        ]
                    }
                )
            ],
        }
    )
    v4_neg = verify_v4_unstructured(
        v4_ir,
        VerifierContext(document_maps=[dmap], mcp_receipts=[leak_mcp_rec], metadata_only_binary_absent=True),
    )
    assert {"BLOB_OR_VEC_IN_RAW_SAT", "SPAN_HALLUCINATION", "REDACTED_PLACEHOLDER_LEAK"}.issubset(
        {d.rule_id for d in v4_neg}
    )

    # V4: OcrPageReceipt missing bounds & occurrence mismatch, and ExternalMcpReceipt excerpt mismatch
    ocr_rec_dup = OcrPageReceipt(
        receipt_id="ocr-dup",
        doc_id=dmap.doc_id,
        page_number=4,
        ocr_text_masked="ALPHA ALPHA",
        content_sha256=compute_canonical_text_sha256("ALPHA ALPHA"),
        ocr_box_count=2,
    )
    cit_no_bounds = DocumentCitationRef(
        doc_id=dmap.doc_id, page_number=4, verbatim_quote="ALPHA", occurrence=1, content_sha256=ocr_rec_dup.content_sha256
    )
    cit_bad_occ = DocumentCitationRef(
        doc_id=dmap.doc_id, page_number=4, verbatim_quote="ALPHA", occurrence=2, char_start=0, char_end=5, content_sha256=ocr_rec_dup.content_sha256
    )
    mcp_clean_ref = ExternalMcpEvidenceRef(
        receipt_id="m2", server_id="srv", tool_name="t", content_sha256="a" * 64, masked_excerpt="Excerpt A"
    )
    mcp_mismatch_rec = ExternalMcpReceipt(
        receipt_id="m2", server_id="srv", tool_name="t", args_sha256="b" * 64, content_sha256="a" * 64, masked_excerpt="Different excerpt"
    )
    v4_ocr_ir = cert.model_copy(
        update={
            "links": [
                cert.links[0].model_copy(
                    update={
                        "source_bindings": [
                            cert.links[0].source_bindings[0].model_copy(
                                update={"doc_citations": [cit_no_bounds, cit_bad_occ], "mcp_evidence": [mcp_clean_ref]}
                            )
                        ]
                    }
                )
            ]
        }
    )
    v4_ocr_diags = verify_v4_unstructured(
        v4_ocr_ir,
        VerifierContext(ocr_receipts=[ocr_rec_dup], mcp_receipts=[mcp_mismatch_rec], metadata_only_binary_absent=True),
    )
    assert {"CITATION_SPAN_UNRESOLVED", "SPAN_HALLUCINATION"}.issubset({d.rule_id for d in v4_ocr_diags})

    # V5: prior_ir=None unmodified IR passes, Hub/Link HWM regression caught, order-insensitive DV-AP-010, simple CASE
    locks_w1 = build_evolution_lock_from_certified(cert)
    assert verify_v5_evolution(cert, locks=locks_w1, prior_ir=None) == []

    from opendv.models.vault_ir_certified import IRField

    max_hub_fid = max(c.field_id for c in cert.hubs[0].columns)
    locks_hwm = locks_w1.model_copy(update={"iceberg_high_water_marks": {**locks_w1.iceberg_high_water_marks, "hub_a": max_hub_fid + 5}})
    hub_bad_fid = cert.hubs[0].model_copy(
        update={
            "columns": [
                *cert.hubs[0].columns,
                IRField(field_id=max_hub_fid + 1, name="extra_meta", logical_type="UTF8_STRING", nullable=True, is_pii=False),
            ]
        }
    )
    assert any(
        d.rule_id == "DV-EVO-006" and d.entity_name == "hub_a"
        for d in verify_v5_evolution(cert.model_copy(update={"hubs": [hub_bad_fid, cert.hubs[1]]}), locks=locks_hwm, prior_ir=cert)
    )

    # Order-insensitive DV-AP-010 ([sat_z3, sat_z2]) and simple CASE col WHEN
    sat_z3 = cert.satellites[0].model_copy(update={"sat_name": "sat_a_z3", "zone_id": "Z3"})
    stg_case = StagingViewSpec(
        view_name="stg_t",
        source_id="s",
        source_table_id="s:t",
        col_lineage_mappings={"y": "CASE status WHEN 'A' THEN 1 ELSE 0 END"},
    )
    ap10_diags = verify_v5_evolution(
        cert.model_copy(update={"satellites": [sat_z3, cert.satellites[0]], "staging_views": [stg_case]})
    )
    assert sum(1 for d in ap10_diags if d.rule_id == "DV-AP-010") == 2

    # DV-EVO-001, 003, 004, 006, 007, 008
    evo_bad = cert.model_copy(
        update={
            "seed_records": cert.seed_records.model_copy(update={"default_num": -99, "ghost_load_dts": "2000-01-01T00:00:00Z"}),
            "hubs": [cert.hubs[0].model_copy(update={"business_keys": [BusinessKeyPart(table_id="s:t", col_name="other_id", source_id="s", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")]}), cert.hubs[1]],
            "satellites": [
                cert.satellites[0].model_copy(
                    update={
                        "attributes": [DraftField(name="v1", logical_type="INT32", nullable=False, is_pii=False)],
                        "columns": [c.model_copy(update={"field_id": c.field_id + 1}) for c in cert.satellites[0].columns],
                    }
                )
            ],
        }
    )
    evo_rules = {d.rule_id for d in verify_v5_evolution(evo_bad, locks=locks_w1, prior_ir=cert)}
    assert {"DV-EVO-001", "DV-EVO-003", "DV-EVO-006", "DV-EVO-007", "DV-EVO-008"}.issubset(evo_rules)
    evo_drop = cert.model_copy(update={"satellites": [cert.satellites[0].model_copy(update={"attributes": []})]})
    assert any(d.rule_id == "DV-EVO-004" for d in verify_v5_evolution(evo_drop, locks=locks_w1, prior_ir=cert))

    # Retained EXT sat_a_ext_v2 is locked in Wave 2 -> Wave 3: mutating epoch or dropping col fails V5
    cert_w2_ext = cert.model_copy(
        update={
            "wave_number": 2,
            "satellites": [cert.satellites[0], ext_blob],
        }
    )
    locks_w2_ext = build_evolution_lock_from_certified(cert_w2_ext)
    assert "sat_a_ext_v2" in locks_w2_ext.locked_sat_hash_diffs
    bad_w3_mut_epoch = cert_w2_ext.model_copy(
        update={"wave_number": 3, "satellites": [cert.satellites[0], ext_blob.model_copy(update={"companion_epoch": 3})]}
    )
    assert any(
        d.rule_id == "DV-EVO-006" and d.entity_name == "sat_a_ext_v2"
        for d in verify_v5_evolution(bad_w3_mut_epoch, locks=locks_w2_ext, prior_ir=cert_w2_ext)
    )
    bad_w3_drop_ext = cert_w2_ext.model_copy(
        update={"wave_number": 3, "satellites": [cert.satellites[0], ext_blob.model_copy(update={"attributes": []})]}
    )
    assert any(
        d.rule_id == "DV-EVO-004" and d.entity_name == "sat_a_ext_v2"
        for d in verify_v5_evolution(bad_w3_drop_ext, locks=locks_w2_ext, prior_ir=cert_w2_ext)
    )


def test_iter3_alg_a1_unique_sat_name_v1_dup_and_pii_epoch_lock() -> None:
    """Regression: Probe 9A unique sat_name + triage sync, V1 duplicate entity BLOCKER, PII companion epoch."""
    # 1. Probe 9A: single-binding sat_a (email PII + note) alongside pre-existing sat_a_pii (ssn PII)
    draft_9a = DraftVaultIR(
        project_id="probe_9a",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_a",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id="s:t",
                        col_name="a_id",
                        source_id="s",
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="s", table_id="s:t", col_mappings={"a_id": "a_id"})
                ],
            ),
            DraftHubSpec(
                hub_name="hub_b",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id="s:t",
                        col_name="b_id",
                        source_id="s",
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="s", table_id="s:t", col_mappings={"b_id": "b_id"})
                ],
            ),
        ],
        links=[
            DraftLinkSpec(
                link_name="lnk_ab",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(hub_name="hub_a", role_name="A", fk_col_name="hk_a", source_col_names=["a_id"]),
                    LinkParticipantSpec(hub_name="hub_b", role_name="B", fk_col_name="hk_b", source_col_names=["b_id"]),
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="s", table_id="s:t", col_mappings={"a_id": "a_id", "b_id": "b_id"})
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_a",
                sat_kind="STANDARD",
                parent_name="hub_a",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="s",
                        table_id="s:t",
                        col_mappings={"email": "email", "note": "note"},
                    )
                ],
                attributes=[
                    DraftField(name="email", logical_type="UTF8_STRING", nullable=True, is_pii=True),
                    DraftField(name="note", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                ],
            ),
            DraftSatelliteSpec(
                sat_name="sat_a_pii",
                sat_kind="PII",
                parent_name="hub_a",
                source_bindings=[
                    DraftSourceBinding(source_id="s", table_id="s:t", col_mappings={"ssn": "ssn"})
                ],
                attributes=[
                    DraftField(name="ssn", logical_type="UTF8_STRING", nullable=True, is_pii=True),
                ],
            ),
        ],
        column_triage=[
            ColumnTriageEntry(table_id="s:t", col_name="a_id", disposition="MODELED", target_ref="hub_a", rationale=""),
            ColumnTriageEntry(table_id="s:t", col_name="b_id", disposition="MODELED", target_ref="hub_b", rationale=""),
            ColumnTriageEntry(table_id="s:t", col_name="email", disposition="MODELED", target_ref="sat_a", rationale=""),
            ColumnTriageEntry(table_id="s:t", col_name="note", disposition="MODELED", target_ref="sat_a", rationale=""),
            ColumnTriageEntry(table_id="s:t", col_name="ssn", disposition="MODELED", target_ref="sat_a_pii", rationale=""),
        ],
    )
    _, cert_9a, _ = run_l1_codemods(draft_9a)
    sat_names_9a = [s.sat_name for s in cert_9a.satellites]
    assert len(sat_names_9a) == len(set(sat_names_9a))
    assert set(sat_names_9a) == {"sat_a", "sat_a_pii", "sat_a_pii_2"}
    sats_map_9a = {s.sat_name: [a.name for a in s.attributes] for s in cert_9a.satellites}
    assert sats_map_9a["sat_a_pii"] == ["ssn"]
    assert sats_map_9a["sat_a_pii_2"] == ["email"]
    assert sats_map_9a["sat_a"] == ["note"]
    triage_9a = {e.col_name: e.target_ref for e in cert_9a.column_triage}
    assert triage_9a["email"] == "sat_a_pii_2"
    assert triage_9a["ssn"] == "sat_a_pii"
    assert triage_9a["note"] == "sat_a"
    rep_9a = run_v1_to_v6_gates(cert_9a)
    assert rep_9a.v1_v6_fail_count == 0, rep_9a.v1_v6_diagnostics

    # 2. Direct CertifiedVaultIR duplicate hub_name, link_name, sat_name -> V1 BLOCKER
    dup_ir = cert_9a.model_copy(
        update={
            "hubs": [cert_9a.hubs[0], cert_9a.hubs[0], cert_9a.hubs[1]],
            "links": [cert_9a.links[0], cert_9a.links[0]],
            "satellites": [cert_9a.satellites[0], cert_9a.satellites[0], *cert_9a.satellites[1:]],
        }
    )
    dup_diags = [d for d in verify_v1_structure(dup_ir) if d.rule_id == "DV-STR-DUP"]
    assert all(d.gate == "V1" and d.severity == "BLOCKER" for d in dup_diags)
    assert {d.entity_name for d in dup_diags} == {
        cert_9a.hubs[0].hub_name,
        cert_9a.links[0].link_name,
        cert_9a.satellites[0].sat_name,
    }

    # 3. build_evolution_lock_from_certified bumps sat_epochs for sat_kind="PII" companion
    base_sat = next(s for s in cert_9a.satellites if s.sat_name == "sat_a")
    pii_comp = next(s for s in cert_9a.satellites if s.sat_name == "sat_a_pii_2").model_copy(
        update={"sat_name": "sat_a_ext_v2", "sat_kind": "PII", "extends_sat_name": "sat_a", "companion_epoch": 2}
    )
    cert_pii_comp = cert_9a.model_copy(update={"wave_number": 2, "satellites": [base_sat, pii_comp]})
    locks_pii_comp = build_evolution_lock_from_certified(cert_pii_comp)
    assert locks_pii_comp.sat_epochs["sat_a"] == 2


def test_l1_promote_sanitizes_link_raw_fk_mappings_and_bitemporal_applied_dts() -> None:
    """Link raw col_mappings resolve to HUB_HK targets and bitemporal applied_dts_col leaves hash_diff."""
    tbl_id = "s:positions"
    prof = SourceProfile(
        source_id="s",
        schema_fingerprint="fp-link-bitemp",
        table_count=1,
        tables=[
            TableProfile(
                table_id=tbl_id,
                table_name="positions",
                row_count_est=2,
                columns=[
                    ColumnProfile(
                        col_name="holding_id",
                        sql_type="VARCHAR",
                        nullable=False,
                        null_ratio=0.0,
                        distinct_count_est=2,
                        is_pk=True,
                        sample_values=["HLD_001", "HLD_002"],
                    ),
                    ColumnProfile(
                        col_name="ticker",
                        sql_type="VARCHAR",
                        nullable=False,
                        is_pk=False,
                        null_ratio=0.0,
                        distinct_count_est=2,
                        sample_values=["AAPL", "MSFT"],
                    ),
                    ColumnProfile(
                        col_name="observation_date",
                        sql_type="TIMESTAMP",
                        nullable=False,
                        is_pk=False,
                        null_ratio=0.0,
                        distinct_count_est=1,
                        sample_values=["2025-01-02T00:00:00Z"],
                    ),
                    ColumnProfile(
                        col_name="yield_pct",
                        sql_type="DECIMAL(18,4)",
                        nullable=False,
                        is_pk=False,
                        null_ratio=0.0,
                        distinct_count_est=2,
                        sample_values=["4.1250", "4.3750"],
                    ),
                ],
            )
        ],
    )
    draft = DraftVaultIR(
        project_id="p_promote",
        ontology_version="0.1.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_holding",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=tbl_id,
                        col_name="holding_id",
                        source_id="s",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="SOR",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="s", table_id=tbl_id, col_mappings={"holding_id": "holding_id"})
                ],
            ),
            DraftHubSpec(
                hub_name="hub_security",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id=tbl_id,
                        col_name="ticker",
                        source_id="s",
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="s", table_id=tbl_id, col_mappings={"ticker": "ticker"})
                ],
            ),
        ],
        links=[
            DraftLinkSpec(
                link_name="lnk_holding_security",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_holding",
                        role_name="HOLDING",
                        fk_col_name="hk_holding",
                        source_col_names=["holding_id"],
                    ),
                    LinkParticipantSpec(
                        hub_name="hub_security",
                        role_name="PRIMARY_SEC",
                        fk_col_name="hk_sec_role",
                        source_col_names=["ticker"],
                    ),
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="s",
                        table_id=tbl_id,
                        col_mappings={
                            "hk_holding_security": "holding_id",
                            "hk_holding": "holding_id",
                            "hk_sec_role": "ticker",
                        },
                    )
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_holding",
                sat_kind="STANDARD",
                parent_name="hub_holding",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="s",
                        table_id=tbl_id,
                        col_mappings={"observation_date": "observation_date", "yield_pct": "yield_pct"},
                    )
                ],
                is_bitemporal=True,
                applied_dts_col="observation_date",
                attributes=[
                    DraftField(
                        name="observation_date",
                        logical_type="TIMESTAMP_UTC_US",
                        nullable=False,
                        is_pii=False,
                    ),
                    DraftField(
                        name="yield_pct",
                        logical_type=DecimalType(precision=18, scale=4),
                        nullable=False,
                        is_pii=False,
                    ),
                ],
            )
        ],
        column_triage=[
            ColumnTriageEntry(table_id=tbl_id, col_name="holding_id", disposition="MODELED", target_ref="hub_holding", rationale=""),
            ColumnTriageEntry(table_id=tbl_id, col_name="ticker", disposition="MODELED", target_ref="hub_security", rationale=""),
            ColumnTriageEntry(table_id=tbl_id, col_name="observation_date", disposition="MODELED", target_ref="sat_holding", rationale=""),
            ColumnTriageEntry(table_id=tbl_id, col_name="yield_pct", disposition="MODELED", target_ref="sat_holding", rationale=""),
        ],
    )
    _, cert, _ = run_l1_codemods(draft, profiles=[prof])
    lnk_maps = cert.links[0].source_bindings[0].col_mappings
    assert lnk_maps == {"hk_sec_role": "hk_security"}
    sat = cert.satellites[0]
    assert sat.hash_recipe is not None and sat.hash_recipe.source_columns == ["yield_pct"]
    assert [a.name for a in sat.attributes] == ["yield_pct"]
    assert any(c.name == "observation_date" and c.classification_tag == "APPLIED_DTS" for c in sat.columns)
    rep = run_v1_to_v6_gates(cert)
    assert rep.v1_v6_fail_count == 0, rep.v1_v6_diagnostics
    prov = DuckDbProverAdapter().prove(cert, [prof])
    assert len(prov.dyn_results) == 15 and all(prov.dyn_results.values()), prov.counterexamples

