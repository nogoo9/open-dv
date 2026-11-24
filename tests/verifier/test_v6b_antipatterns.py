"""Comprehensive V6b anti-pattern tests: ONT-AP-001..005, DV-AP-001..010/012, & waivers."""

from __future__ import annotations

from pathlib import Path

from opendv.models.diagnostics import AntiPatternJustification
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ConceptAttribute,
    ConceptTableBinding,
    GraphAOntology,
    KeyAuthorityPattern,
    OntologyRelationship,
)
from opendv.models.project import OntologyWaiverSpec
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
from opendv.models.vault_ir import (
    BusinessKeyPart,
    DraftField,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
    LinkParticipantSpec,
)
from opendv.models.vault_ir_certified import CertifiedVaultIR, MartViewSpec, StagingViewSpec
from opendv.sources.profiler import profile_source
from opendv.verifier import (
    VerifierContext,
    evaluate_v6b_accounting,
    lint_ontology_antipatterns,
    lint_v6b_antipatterns,
    lint_vault_antipatterns,
    run_l1_codemods,
    run_v1_to_v6_gates,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_AM = REPO_ROOT / "data" / "public_am"


def _minimal_certified_ir() -> CertifiedVaultIR:
    return CertifiedVaultIR(
        project_id="v6b_test",
        ontology_version="1.0.0",
        wave_number=1,
    )


def test_uat_s5_05_ont_ap_001_to_005_and_waiver_suppression() -> None:
    """UAT-S5-05: Detect ONT-AP-001..005 on GraphAOntology + SourceProfile and test waivers."""
    esg_prof = profile_source(
        "am_esg", "parquet", str(PUBLIC_AM / "am_esg_stewardship_proxy.parquet")
    )
    trap_prof = profile_source("trap_db", "duckdb", str(PUBLIC_AM / "ds_cegar_12trap.duckdb"))
    esg_tbl = esg_prof.tables[0].table_id
    crm_tbl = next(t.table_id for t in trap_prof.tables if t.table_name == "trap_crm_accounts")
    ballot_tbl = next(t.table_id for t in trap_prof.tables if t.table_name == "trap_ballots_mas")

    # 15+ attrs on wide ESG table + source prefix -> ONT-AP-001
    wide_attrs = [
        ConceptAttribute(
            name=col.col_name,
            logical_type="UTF8_STRING",
            sql_type_hint="VARCHAR",
            nullable=True,
            is_business_key=(i < 4),  # 4 BKs -> ONT-AP-003
            is_pii=False,
        )
        for i, col in enumerate(esg_prof.tables[0].columns[:16])
    ]
    c_wide = CanonicalBusinessConcept(
        concept_uri="urn:opendv:test:sfdc_esg_clone",
        concept_name="sfdc_esg_clone",
        description="Wide single-table clone with source prefix.",
        key_authority_tier="TIER_3_LOCAL_SCOPED",
        key_authority_patterns=[
            KeyAuthorityPattern(
                tier="TIER_3_LOCAL_SCOPED",
                authority_name="MONOTONIC_INT",
                column_patterns=["id"],
                bkcc_policy="LOCAL",
            )
        ],
        attributes=wide_attrs,
        table_bindings=[ConceptTableBinding(source_id="am_esg", table_id=esg_tbl, col_name="esg_rating_id")],
    )
    # Overlapping silo concept (>=70% attr overlap) -> ONT-AP-001
    c_dup = CanonicalBusinessConcept(
        concept_uri="urn:opendv:test:esg_dup",
        concept_name="EsgCloneCopy",
        description="Duplicate concept.",
        key_authority_tier="TIER_2_ENTERPRISE_SOR",
        attributes=wide_attrs[:12],
        table_bindings=[ConceptTableBinding(source_id="am_esg", table_id=esg_tbl, col_name="ticker")],
    )
    # Disguised lookup (iso_currency_code distinct=5) -> ONT-AP-002
    c_lookup = CanonicalBusinessConcept(
        concept_uri="urn:opendv:test:currency_lookup",
        concept_name="CurrencyLookup",
        description="Static code promoted to concept.",
        key_authority_tier="TIER_1_GLOBAL_STANDARD",
        attributes=[
            ConceptAttribute(
                name="iso_currency_code",
                logical_type="UTF8_STRING",
                sql_type_hint="VARCHAR",
                nullable=False,
                is_business_key=True,
                is_pii=False,
            )
        ],
        table_bindings=[
            ConceptTableBinding(source_id="trap_db", table_id=ballot_tbl, col_name="iso_currency_code")
        ],
    )
    # Unclassified PII on trap_crm_accounts -> ONT-AP-005 + Orphan degree=0 on same table -> ONT-AP-004
    c_pii = CanonicalBusinessConcept(
        concept_uri="urn:opendv:test:crm_account",
        concept_name="CrmAccount",
        description="Account missing PII_ISOLATION constraint.",
        key_authority_tier="TIER_3_LOCAL_SCOPED",
        attributes=[
            ConceptAttribute(
                name="account_id",
                logical_type="UTF8_STRING",
                sql_type_hint="VARCHAR",
                nullable=False,
                is_business_key=True,
                is_pii=False,
            ),
            ConceptAttribute(
                name="stewardship_officer_email",
                logical_type="UTF8_STRING",
                sql_type_hint="VARCHAR",
                nullable=True,
                is_business_key=False,
                is_pii=True,
            ),
        ],
        table_bindings=[
            ConceptTableBinding(source_id="trap_db", table_id=crm_tbl, col_name="account_id"),
            ConceptTableBinding(source_id="trap_db", table_id=crm_tbl, col_name="stewardship_officer_email"),
        ],
    )

    ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="asset_mgmt",
        concepts=[c_wide, c_dup, c_lookup, c_pii],
        relationships=[],
    )
    diags = lint_ontology_antipatterns(ont, [esg_prof, trap_prof])
    found_rules = {d.rule_id for d in diags}
    assert {"ONT-AP-001", "ONT-AP-002", "ONT-AP-003", "ONT-AP-004", "ONT-AP-005"}.issubset(found_rules)

    # OntologyWaiverSpec suppresses matching ONT-AP-* from |V6b_fail|
    waivers = [
        OntologyWaiverSpec(
            waiver_id=d.suggested_waiver_id or "",
            rule_id=d.rule_id,
            concept_uri=d.concept_uri or "",
            rationale="Approved by domain steward for Wave 1 ingestion.",
        )
        for d in diags
        if d.rule_id == "ONT-AP-002"
    ]
    fail_cnt, waived_cnt, _ = evaluate_v6b_accounting(diags, ontology_waivers=waivers)
    assert waived_cnt >= 1
    unwaived_all, _, _ = evaluate_v6b_accounting(diags)
    assert fail_cnt < unwaived_all


def test_uat_s6_15_dv_ap_001_to_010_and_012_and_advisory_and_v7_waivers() -> None:
    """UAT-S6-15: Verify DV-AP-001..010, 012, ADVISORY vs strict, and human/V7 waivers."""
    tbl_id = "src:mega"
    cols = [
        ColumnProfile(col_name="seq_id", sql_type="DOUBLE", nullable=False, is_pk=True, null_ratio=0.0, distinct_count_est=100),
        ColumnProfile(col_name="status_code", sql_type="VARCHAR", nullable=True, is_pk=False, null_ratio=0.0, distinct_count_est=4),
        ColumnProfile(col_name="fk_a", sql_type="VARCHAR", nullable=True, is_pk=False, null_ratio=0.5, distinct_count_est=10),
        ColumnProfile(col_name="event_dts", sql_type="TIMESTAMP", nullable=False, is_pk=False, null_ratio=0.0, distinct_count_est=100),
        ColumnProfile(col_name="client_email", sql_type="VARCHAR", nullable=True, is_pk=False, null_ratio=0.0, distinct_count_est=50, pii_tags=["EMAIL"]),
        *(
            ColumnProfile(col_name=f"attr_{i:02d}", sql_type="VARCHAR", nullable=True, is_pk=False, null_ratio=0.0, distinct_count_est=10)
            for i in range(25)
        ),
    ]
    prof = SourceProfile(
        source_id="src",
        schema_fingerprint="mega",
        table_count=1,
        tables=[TableProfile(table_id=tbl_id, table_name="mega", row_count_est=100, columns=cols)],
    )

    # Build CertifiedVaultIR deliberately triggering DV-AP-001..010 and DV-AP-012
    draft = DraftVaultIR(
        project_id="ap_all",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_sfdc_account",  # DV-AP-001 source prefix
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(table_id=tbl_id, col_name="seq_id", source_id="src", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")
                ],
                source_bindings=[DraftSourceBinding(source_id="src", table_id=tbl_id, col_mappings={"seq_id": "seq_id"})],
                attributes=[DraftField(name="shared_a", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
            ),
            DraftHubSpec(
                hub_name="hub_account_clone",  # DV-AP-001 Jaccard >= 80% + DV-AP-004 lookup
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(table_id=tbl_id, col_name="status_code", source_id="src", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")
                ],
                source_bindings=[DraftSourceBinding(source_id="src", table_id=tbl_id, col_mappings={"status_code": "status_code"})],
                attributes=[DraftField(name="shared_a", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
            ),
            *(
                DraftHubSpec(
                    hub_name=f"hub_p{i}",
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    business_keys=[BusinessKeyPart(table_id=tbl_id, col_name=f"attr_{i:02d}", source_id="src", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
                    source_bindings=[DraftSourceBinding(source_id="src", table_id=tbl_id, col_mappings={f"attr_{i:02d}": f"attr_{i:02d}"})],
                )
                for i in range(4)
            ),
        ],
        links=[
            # Mega-Link (5+ hubs with sparse fk_a) -> DV-AP-002; event_dts on STANDARD -> DV-AP-007; turnover w/o ESAT -> DV-AP-008
            DraftLinkSpec(
                link_name="lnk_mega_event",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(hub_name="hub_sfdc_account", role_name="ACC", fk_col_name="hk_sfdc_account", source_col_names=["fk_a"]),
                    *(
                        LinkParticipantSpec(hub_name=f"hub_p{i}", role_name=f"P{i}", fk_col_name=f"hk_p{i}", source_col_names=[f"attr_{i:02d}"])
                        for i in range(4)
                    ),
                ],
                source_bindings=[DraftSourceBinding(source_id="src", table_id=tbl_id, col_mappings={"fk_a": "fk_a"})],
            )
        ],
        satellites=[
            # God Satellite (25 attrs + unisolated client_email) -> DV-AP-005 & DV-AP-009
            DraftSatelliteSpec(
                sat_name="sat_god_blob",
                sat_kind="STANDARD",
                parent_name="hub_sfdc_account",
                source_bindings=[DraftSourceBinding(source_id="src", table_id=tbl_id, col_mappings={"client_email": "client_email"})],
                attributes=[
                    DraftField(name="client_email", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                    *(
                        DraftField(name=f"attr_{i:02d}", logical_type="UTF8_STRING", nullable=True, is_pii=False)
                        for i in range(25)
                    ),
                ],
            ),
            # 3 micro-satellites on hub_p0 -> DV-AP-006
            *(
                DraftSatelliteSpec(
                    sat_name=f"sat_micro_{k}",
                    sat_kind="STANDARD",
                    parent_name="hub_p0",
                    source_bindings=[DraftSourceBinding(source_id="src", table_id=tbl_id, col_mappings={f"attr_{k:02d}": f"attr_{k:02d}"})],
                    attributes=[DraftField(name=f"attr_{k:02d}", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
                )
                for k in range(3)
            ),
        ],
    )

    from opendv.verifier._l1_promote import promote_draft_to_certified

    # Promote directly without running L1 auto-fixes so raw anti-patterns stay observable in V6b
    raw_cert = promote_draft_to_certified(draft, profiles=[prof])
    # Inject StagingViewSpec soft-SQL leak -> DV-AP-010, and clear pit_bridges -> DV-AP-012
    raw_cert = raw_cert.model_copy(
        update={
            "pit_bridges": [],
            "staging_views": [
                StagingViewSpec(
                    view_name="stg_mega",
                    source_id="src",
                    source_table_id=tbl_id,
                    col_lineage_mappings={"bad": "SELECT a JOIN b ON a.id = b.id"},
                )
            ],
            "information_marts": [
                MartViewSpec(
                    view_name="information_mart.vw_mega",
                    root_entity="hub_sfdc_account",
                    joined_entities=["hub_p0", "hub_p1", "hub_p2", "hub_p3"],
                    select_exprs={"hk": "hk"},
                )
            ],
        }
    )

    diags_bal = lint_vault_antipatterns(
        raw_cert,
        [prof],
        policy="balanced",
        fk_turnover_by_table={tbl_id: {"fk_a": 0.5}},
    )
    rules_bal = {d.rule_id for d in diags_bal}
    for expected in (
        "DV-AP-001",
        "DV-AP-002",
        "DV-AP-003",
        "DV-AP-004",
        "DV-AP-005",
        "DV-AP-006",
        "DV-AP-007",
        "DV-AP-008",
        "DV-AP-009",
        "DV-AP-010",
        "DV-AP-012",
    ):
        assert expected in rules_bal, f"Missing {expected} in {sorted(rules_bal)}"

    # Under balanced: DV-AP-012 is ADVISORY and excluded from |V6b_fail|
    pit_diag_bal = next(d for d in diags_bal if d.rule_id == "DV-AP-012")
    assert pit_diag_bal.severity == "ADVISORY"
    fail_bal, _, adv_bal = evaluate_v6b_accounting(diags_bal)
    assert adv_bal >= 1

    # Under strict: DV-AP-012 escalates to WARN and counts in |V6b_fail|
    diags_strict = lint_vault_antipatterns(
        raw_cert,
        [prof],
        policy="strict",
        fk_turnover_by_table={tbl_id: {"fk_a": 0.5}},
    )
    pit_diag_strict = next(d for d in diags_strict if d.rule_id == "DV-AP-012")
    assert pit_diag_strict.severity == "WARN"
    fail_strict, _, adv_strict = evaluate_v6b_accounting(diags_strict)
    assert adv_strict == 0
    assert fail_strict > fail_bal

    # Waiver accounting: unreviewed agent justification does NOT waive; V7-reviewed / human DOES waive
    target = next(d for d in diags_bal if d.rule_id == "DV-AP-005")
    unreviewed = AntiPatternJustification(
        waiver_id="W-UNREV",
        rule_id="DV-AP-005",
        entity_name=target.entity_name,
        rationale="Agent claims uniform volatility without V7 approval.",
        actor="agent",
        reviewed_by_v7=False,
    )
    fail_unrev, waived_unrev, _ = evaluate_v6b_accounting(diags_bal, justifications=[unreviewed])
    assert fail_unrev == fail_bal and waived_unrev == 0

    reviewed = unreviewed.model_copy(update={"reviewed_by_v7": True})
    fail_rev, waived_rev, _ = evaluate_v6b_accounting(diags_bal, justifications=[reviewed])
    assert waived_rev == 1
    assert fail_rev == fail_bal - 1

    # Confirm L1 codemods auto-repair DV-AP-006 (micro-sat merge) and DV-AP-009 (sat_*_pii isolation)
    _, fixed_cert, _ = run_l1_codemods(
        draft.model_copy(
            update={
                "column_triage": [
                    # minimal triage for test columns
                ]
            }
        ),
        profiles=[prof],
    )
    fixed_diags = lint_v6b_antipatterns(fixed_cert, profiles=[prof])
    fixed_rules = {d.rule_id for d in fixed_diags}
    assert "DV-AP-009" not in fixed_rules
    assert "DV-AP-006" not in fixed_rules
    assert any(r.ref_name == "ref_account_clone" for r in fixed_cert.reference_tables)
    assert run_v1_to_v6_gates(_minimal_certified_ir(), VerifierContext(policy="balanced")).v6b_fail_count == 0


def test_ont_ap_004_asymmetric_fk_orphan_and_dv_ap_010_simple_case() -> None:
    """Detect asymmetric incoming FK orphan (ONT-AP-004) and simple CASE WHEN (DV-AP-010)."""
    prof = SourceProfile(
        source_id="src",
        schema_fingerprint="asym_fk",
        table_count=2,
        tables=[
            TableProfile(
                table_id="src:conn",
                table_name="conn",
                row_count_est=10,
                columns=[
                    ColumnProfile(col_name="conn_id", sql_type="VARCHAR", nullable=False, is_pk=True, null_ratio=0.0, distinct_count_est=10),
                    ColumnProfile(col_name="o_ref", sql_type="VARCHAR", nullable=True, is_pk=False, null_ratio=0.0, distinct_count_est=5, fk_ref="orphan.pk_o"),
                ],
            ),
            TableProfile(
                table_id="src:orphan",
                table_name="orphan",
                row_count_est=5,
                columns=[
                    ColumnProfile(col_name="pk_o", sql_type="VARCHAR", nullable=False, is_pk=True, null_ratio=0.0, distinct_count_est=5),
                ],
            ),
        ],
    )
    c_conn = CanonicalBusinessConcept(
        concept_uri="urn:t:conn",
        concept_name="Connector",
        description="Connected",
        key_authority_tier="TIER_2_ENTERPRISE_SOR",
        table_bindings=[ConceptTableBinding(source_id="src", table_id="src:conn", col_name="conn_id")],
    )
    c_other = CanonicalBusinessConcept(
        concept_uri="urn:t:other",
        concept_name="Other",
        description="Other",
        key_authority_tier="TIER_2_ENTERPRISE_SOR",
        table_bindings=[ConceptTableBinding(source_id="src", table_id="src:conn", col_name="o_ref")],
    )
    c_orphan = CanonicalBusinessConcept(
        concept_uri="urn:t:orphan",
        concept_name="OrphanTarget",
        description="Asymmetric FK target",
        key_authority_tier="TIER_2_ENTERPRISE_SOR",
        table_bindings=[ConceptTableBinding(source_id="src", table_id="src:orphan", col_name="pk_o")],
    )
    ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="t",
        concepts=[c_conn, c_other, c_orphan],
        relationships=[
            OntologyRelationship(
                relationship_uri="urn:t:rel_links_to",
                relationship_name="LINKS_TO",
                source_concept_uri="urn:t:conn",
                target_concept_uri="urn:t:other",
                cardinality="MANY_TO_ONE",
                target_link="lnk_conn_other",
                grain_semantics="One link per connector row.",
                description="Connector to other concept.",
            )
        ],
    )
    ont_diags = lint_ontology_antipatterns(ont, [prof])
    assert any(d.rule_id == "ONT-AP-004" and d.entity_name == "OrphanTarget" for d in ont_diags)

    ir_case = _minimal_certified_ir().model_copy(
        update={
            "staging_views": [
                StagingViewSpec(
                    view_name="stg_case",
                    source_id="src",
                    source_table_id="src:conn",
                    col_lineage_mappings={"flag": "CASE o_ref WHEN 'X' THEN 1 ELSE 0 END"},
                )
            ]
        }
    )
    assert any(d.rule_id == "DV-AP-010" for d in lint_vault_antipatterns(ir_case))
