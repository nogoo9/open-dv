"""Tests for deterministic zero-LLM modeling hint survey (P1-P4 & pagination)."""

from __future__ import annotations

from opendv.models.modeling_preferences import PkContainmentOverlap
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ConceptTableBinding,
    GraphAOntology,
    OntologyRelationship,
)
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
from opendv.models.vault_ir import (
    BridgeHopSpec,
    BusinessKeyPart,
    ColumnTriageEntry,
    DraftBridgeSpec,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
    LinkParticipantSpec,
)
from opendv.verifier import survey_modeling_hints


def _col(
    name: str,
    sql_type: str = "VARCHAR",
    *,
    nullable: bool = False,
    is_pk: bool = False,
    fk_ref: str | None = None,
    distinct_count_est: int = 100,
    pii_tags: list[str] | None = None,
    format_shape: str = "MIXED",
    surrogate_score: float = 0.0,
) -> ColumnProfile:
    return ColumnProfile(
        col_name=name,
        sql_type=sql_type,
        nullable=nullable,
        is_pk=is_pk,
        fk_ref=fk_ref,
        null_ratio=0.1 if nullable else 0.0,
        distinct_count_est=distinct_count_est,
        pii_tags=pii_tags or [],
        format_shape=format_shape,  # type: ignore[arg-type]
        surrogate_score=surrogate_score,
    )


def test_p1_survivorship_with_ontology_and_stem_fallback_audit_exclusion_and_pii_masking() -> None:
    """P1 fires with GraphAOntology or normalized table-stem fallback, excludes audit cols, & masks PII."""
    # Case A: With GraphAOntology binding disparate table names (party vs account_holder)
    prof_erp = SourceProfile(
        source_id="erp",
        schema_fingerprint="fp_erp",
        table_count=1,
        tables=[
            TableProfile(
                table_id="erp:party",
                table_name="party",
                row_count_est=50,
                columns=[
                    _col("party_code", is_pk=True),
                    _col("segment"),
                    _col("email", pii_tags=["EMAIL"]),
                    _col("contact_note"),
                    _col("updated_at", sql_type="TIMESTAMP"),
                    _col("created_by"),
                    _col("id", sql_type="INTEGER"),
                ],
            )
        ],
    )
    prof_crm = SourceProfile(
        source_id="crm",
        schema_fingerprint="fp_crm",
        table_count=1,
        tables=[
            TableProfile(
                table_id="crm:account_holder",
                table_name="account_holder",
                row_count_est=50,
                columns=[
                    _col("party_code", is_pk=True),
                    _col("segment"),
                    _col("email", nullable=True, pii_tags=["EMAIL"]),
                    _col("contact_note"),
                    _col("updated_at", sql_type="TIMESTAMP"),
                    _col("created_by"),
                    _col("id", sql_type="INTEGER"),
                ],
            )
        ],
    )
    ont = GraphAOntology(
        ontology_version="1.0",
        domain_namespace="test",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:test:Party",
                concept_name="Party",
                description="Business party",
                key_authority_tier="TIER_2_ENTERPRISE_SOR",
                table_bindings=[
                    ConceptTableBinding(source_id="erp", table_id="erp:party", col_name="party_code"),
                    ConceptTableBinding(source_id="crm", table_id="crm:account_holder", col_name="party_code"),
                ],
            )
        ],
    )
    rows = {
        "erp:party": [
            {
                "party_code": "P-01",
                "segment": "ENTERPRISE",
                "email": "alice@secret-corp.com",
                "contact_note": "123-45-6789",
                "updated_at": "2026-01-01",
                "created_by": "sys_erp",
                "id": 1,
            }
        ],
        "crm:account_holder": [
            {
                "party_code": "P-01",
                "segment": "SMB",
                "email": None,
                "contact_note": "bob@other-corp.com",
                "updated_at": "2026-01-02",
                "created_by": "sys_crm",
                "id": 99,
            }
        ],
    }
    qs_ont, meta_ont = survey_modeling_hints(
        [prof_erp, prof_crm],
        ontology=ont,
        source_rows_by_table=rows,
        max_questions=None,
    )
    assert meta_ont["P1"] == 1
    p1 = qs_ont[0]
    assert p1.question_id.startswith("Q-P1-party-")
    assert "segment" in p1.prompt
    # Audit/surrogate columns excluded
    for audit_col in ("updated_at", "created_by", "id"):
        assert audit_col not in p1.prompt
        assert not any(audit_col in s for s in p1.sample_rows)
    joined_samples = "\n".join(p1.sample_rows)
    # Non-PII conflict shows scalar values
    assert "segment: crm=SMB vs erp=ENTERPRISE" in joined_samples or "segment: erp=ENTERPRISE vs crm=SMB" in joined_samples
    # PII columns & PII-shaped values masked to <NON_NULL> / <NULL>; raw PII never leaked
    assert "<NON_NULL>" in joined_samples
    assert "<NULL>" in joined_samples
    assert "alice@secret-corp.com" not in joined_samples
    assert "bob@other-corp.com" not in joined_samples
    assert "123-45-6789" not in joined_samples

    # Case B: Without ontology (ontology=None), normalized table-stem fallback (erp_customers vs crm_customers)
    p_erp_c = SourceProfile(
        source_id="erp",
        schema_fingerprint="fp_ec",
        table_count=1,
        tables=[
            TableProfile(
                table_id="erp:erp_customers",
                table_name="erp_customers",
                row_count_est=30,
                columns=[_col("cust_code", is_pk=True), _col("tier_name"), _col("load_dts")],
            )
        ],
    )
    p_crm_c = SourceProfile(
        source_id="crm",
        schema_fingerprint="fp_cc",
        table_count=1,
        tables=[
            TableProfile(
                table_id="crm:crm_customers",
                table_name="crm_customers",
                row_count_est=30,
                columns=[_col("cust_code", is_pk=True), _col("tier_name"), _col("load_dts")],
            )
        ],
    )
    qs_fb, meta_fb = survey_modeling_hints([p_erp_c, p_crm_c], ontology=None, max_questions=None)
    assert meta_fb["P1"] == 1
    assert qs_fb[0].question_id.startswith("Q-P1-customer-")
    assert "tier_name" in qs_fb[0].prompt
    assert "load_dts" not in qs_fb[0].prompt

    # Suppression via draft_ir Hub with survivorship_priority >= 2
    draft_suppressed = DraftVaultIR(
        project_id="p_sup",
        ontology_version="1.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_customer",
                key_tier="TIER_2_ENTERPRISE_SOR",
                survivorship_priority=["erp", "crm"],
                source_bindings=[
                    DraftSourceBinding(source_id="erp", table_id="erp:erp_customers", col_mappings={}),
                    DraftSourceBinding(source_id="crm", table_id="crm:crm_customers", col_mappings={}),
                ],
            )
        ],
    )
    _, meta_sup = survey_modeling_hints(
        [p_erp_c, p_crm_c], ontology=None, draft_ir=draft_suppressed, max_questions=None
    )
    assert meta_sup["P1"] == 0


def test_p2_multihop_chains_audit_fk_exclusion_and_pk_orientation() -> None:
    """P2 discovers 2..4 hop chains, orients overlaps toward PKs, & excludes actor audit FKs unless multi-rel."""
    # Chain via pk_containment_overlaps (oriented toward PK) + fk_ref:
    # invoices --(overlap inv.ord_ref -> orders.order_id)--> orders -> products -> categories (3 hops)
    # Plus audit FKs created_by/updated_by/modified_by/deleted_by -> users -> orgs (excluded unless multi-rel!)
    prof = SourceProfile(
        source_id="erp",
        schema_fingerprint="fp_p2",
        table_count=6,
        tables=[
            TableProfile(
                table_id="erp:invoices",
                table_name="invoices",
                row_count_est=100,
                columns=[
                    _col("invoice_id", is_pk=True),
                    _col("ord_ref"),  # linked to orders.order_id via pk_containment_overlaps
                    _col("created_by", fk_ref="users.user_id"),
                    _col("updated_by", fk_ref="users.user_id"),
                    _col("modified_by", fk_ref="users.user_id"),
                    _col("deleted_by", fk_ref="users.user_id"),
                ],
            ),
            TableProfile(
                table_id="erp:orders",
                table_name="orders",
                row_count_est=300,
                columns=[
                    _col("order_id", is_pk=True),
                    _col("product_id", fk_ref="products.product_id"),
                ],
            ),
            TableProfile(
                table_id="erp:products",
                table_name="products",
                row_count_est=50,
                columns=[
                    _col("product_id", is_pk=True),
                    _col("category_id", fk_ref="categories.category_id"),
                ],
            ),
            TableProfile(
                table_id="erp:categories",
                table_name="categories",
                row_count_est=10,
                columns=[_col("category_id", is_pk=True)],
            ),
            TableProfile(
                table_id="erp:users",
                table_name="users",
                row_count_est=20,
                columns=[
                    _col("user_id", is_pk=True),
                    _col("org_id", fk_ref="orgs.org_id"),
                ],
            ),
            TableProfile(
                table_id="erp:orgs",
                table_name="orgs",
                row_count_est=5,
                columns=[_col("org_id", is_pk=True)],
            ),
        ],
        pk_containment_overlaps=[
            # Intentionally place PK on left and FK on right to verify orientation toward PK!
            PkContainmentOverlap(
                left_source_id="erp",
                left_table_id="erp:orders",
                left_col="order_id",
                right_source_id="erp",
                right_table_id="erp:invoices",
                right_col="ord_ref",
                overlap_ratio=0.95,
                compared_hashes=50,
            )
        ],
    )

    qs, meta = survey_modeling_hints([prof], ontology=None, max_questions=None)
    p2_prompts = [q.prompt for q in qs if q.question_id.startswith("Q-P2-")]
    assert meta["P2"] == 3
    assert any("invoice -> order -> product -> category" in p for p in p2_prompts)
    assert any("invoice -> order -> product as Z3" in p for p in p2_prompts)
    assert any("order -> product -> category as Z3" in p for p in p2_prompts)
    # Audit FKs excluded so no chain invoice -> user -> org
    assert not any("invoice -> user -> org" in p for p in p2_prompts)

    # When GraphAOntology declares >= 2 relationships between Invoice and User, actor FK is permitted
    c_inv = CanonicalBusinessConcept(
        concept_uri="urn:t:Invoice",
        concept_name="Invoice",
        description="Invoice",
        key_authority_tier="TIER_2_ENTERPRISE_SOR",
    )
    c_usr = CanonicalBusinessConcept(
        concept_uri="urn:t:User",
        concept_name="User",
        description="User",
        key_authority_tier="TIER_2_ENTERPRISE_SOR",
    )
    ont_multi = GraphAOntology(
        ontology_version="1.0",
        domain_namespace="t",
        concepts=[c_inv, c_usr],
        relationships=[
            OntologyRelationship(
                relationship_uri="urn:t:r1",
                relationship_name="created_by",
                source_concept_uri="urn:t:Invoice",
                target_concept_uri="urn:t:User",
                cardinality="MANY_TO_ONE",
                target_link="lnk_inv_creator",
                grain_semantics="Creator",
                description="Creator",
            ),
            OntologyRelationship(
                relationship_uri="urn:t:r2",
                relationship_name="updated_by",
                source_concept_uri="urn:t:Invoice",
                target_concept_uri="urn:t:User",
                cardinality="MANY_TO_ONE",
                target_link="lnk_inv_updater",
                grain_semantics="Updater",
                description="Updater",
            ),
        ],
    )
    qs_multi, _ = survey_modeling_hints([prof], ontology=ont_multi, max_questions=None)
    p2_multi_prompts = [q.prompt for q in qs_multi if q.question_id.startswith("Q-P2-")]
    assert any("invoice -> user -> org" in p for p in p2_multi_prompts)

    # Suppression via draft_ir DraftBridgeSpec
    draft_brg = DraftVaultIR(
        project_id="p_brg",
        ontology_version="1.0",
        wave_number=1,
        bridges=[
            DraftBridgeSpec(
                bridge_name="brg_invoice_category",
                root_hub="hub_invoice",
                hops=[BridgeHopSpec(link_name="lnk_inv_ord")],
            )
        ],
    )
    qs_sup, _ = survey_modeling_hints([prof], draft_ir=draft_brg, max_questions=None)
    assert not any(q.question_id.startswith("Q-P2-invoice-") for q in qs_sup)


def test_p3_link_mutability_signals_and_immutable_created_at_tables() -> None:
    """P3 fires on mutability signals (cols, turnover, FK reassignment) and skips immutable created_at tables."""
    prof = SourceProfile(
        source_id="ops",
        schema_fingerprint="fp_p3",
        table_count=5,
        tables=[
            TableProfile(
                table_id="ops:employees",
                table_name="employees",
                row_count_est=20,
                columns=[_col("emp_id", is_pk=True)],
            ),
            TableProfile(
                table_id="ops:departments",
                table_name="departments",
                row_count_est=10,
                columns=[_col("dept_id", is_pk=True)],
            ),
            # 1. Mutability via status/updated_at column
            TableProfile(
                table_id="ops:emp_status_rel",
                table_name="emp_status_rel",
                row_count_est=20,
                columns=[
                    _col("emp_id", is_pk=True, fk_ref="employees.emp_id"),
                    _col("dept_id", fk_ref="departments.dept_id"),
                    _col("status"),
                    _col("updated_at", sql_type="TIMESTAMP"),
                ],
            ),
            # 2. Mutability via fk_turnover_by_table > 0 (no mutability columns)
            TableProfile(
                table_id="ops:emp_turnover_rel",
                table_name="emp_turnover_rel",
                row_count_est=20,
                columns=[
                    _col("emp_id", is_pk=True, fk_ref="employees.emp_id"),
                    _col("dept_id", fk_ref="departments.dept_id"),
                ],
            ),
            # 3. Mutability via observed FK reassignment in source_rows_by_table
            TableProfile(
                table_id="ops:emp_row_reass",
                table_name="emp_row_reass",
                row_count_est=20,
                columns=[
                    _col("assign_id", is_pk=True),
                    _col("emp_id", fk_ref="employees.emp_id"),
                    _col("dept_id", fk_ref="departments.dept_id"),
                ],
            ),
            # 4. Immutable created_at-only transactional table -> must produce ZERO P3 questions
            TableProfile(
                table_id="ops:audit_events",
                table_name="audit_events",
                row_count_est=100,
                columns=[
                    _col("event_id", is_pk=True),
                    _col("emp_id", fk_ref="employees.emp_id"),
                    _col("created_at", sql_type="TIMESTAMP"),
                ],
            ),
        ],
    )
    rows = {
        "ops:emp_row_reass": [
            {"assign_id": "A1", "emp_id": "E1", "dept_id": "D1"},
            {"assign_id": "A1", "emp_id": "E1", "dept_id": "D2"},
        ],
        "ops:audit_events": [
            {"event_id": "EV1", "emp_id": "E1", "created_at": "2026-01-01T00:00:00Z"},
            {"event_id": "EV2", "emp_id": "E2", "created_at": "2026-01-02T00:00:00Z"},
        ],
    }
    qs, meta = survey_modeling_hints(
        [prof],
        source_rows_by_table=rows,
        fk_turnover_by_table={
            "ops:emp_turnover_rel": {"dept_id": 0.12},
            "ops:audit_events": {"emp_id": 0.0},
        },
        max_questions=None,
    )
    p3 = [q for q in qs if q.question_id.startswith("Q-P3-")]
    assert meta["P3"] == 3
    titles = "\n".join(q.title for q in p3)
    assert "emp_status_rel" in titles
    assert "emp_turnover_rel" in titles
    assert "emp_row_reass" in titles
    assert "audit_events" not in titles

    # Suppression via draft_ir (TRANSACTIONAL link or ESAT)
    draft_sup = DraftVaultIR(
        project_id="p_p3_sup",
        ontology_version="1.0",
        wave_number=1,
        links=[
            DraftLinkSpec(
                link_name="t_lnk_emp_status",
                link_kind="TRANSACTIONAL",
                participants=[
                    LinkParticipantSpec(hub_name="hub_emp", role_name="EMP", fk_col_name="hk_emp", source_col_names=["emp_id"]),
                    LinkParticipantSpec(hub_name="hub_dept", role_name="DEPT", fk_col_name="hk_dept", source_col_names=["dept_id"]),
                ],
                source_bindings=[DraftSourceBinding(source_id="ops", table_id="ops:emp_status_rel", col_mappings={})],
            ),
            DraftLinkSpec(
                link_name="lnk_emp_turnover",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(hub_name="hub_emp", role_name="EMP", fk_col_name="hk_emp", source_col_names=["emp_id"]),
                    LinkParticipantSpec(hub_name="hub_dept", role_name="DEPT", fk_col_name="hk_dept", source_col_names=["dept_id"]),
                ],
                source_bindings=[DraftSourceBinding(source_id="ops", table_id="ops:emp_turnover_rel", col_mappings={})],
            ),
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="esat_emp_turnover",
                sat_kind="ESAT",
                parent_name="lnk_emp_turnover",
                driving_keys=["hk_emp"],
                source_bindings=[DraftSourceBinding(source_id="ops", table_id="ops:emp_turnover_rel", col_mappings={})],
            )
        ],
    )
    qs_sup, meta_sup = survey_modeling_hints(
        [prof],
        source_rows_by_table=rows,
        fk_turnover_by_table={"ops:emp_turnover_rel": {"dept_id": 0.12}},
        draft_ir=draft_sup,
        max_questions=None,
    )
    p3_sup = [q for q in qs_sup if q.question_id.startswith("Q-P3-")]
    assert meta_sup["P3"] == 1
    assert len(p3_sup) == 1
    assert "emp_row_reass" in p3_sup[0].title


def test_p4_structural_ambiguity_surrogate_and_low_cardinality() -> None:
    """P4 detects low-cardinality (2..20) non-PK columns and surrogate id + natural BK pairs."""
    prof = SourceProfile(
        source_id="crm",
        schema_fingerprint="fp_p4",
        table_count=1,
        tables=[
            TableProfile(
                table_id="crm:accounts",
                table_name="accounts",
                row_count_est=200,
                best_non_surrogate_bk_candidate_col="acct_code",
                best_non_surrogate_bk_uniqueness_ratio=1.0,
                columns=[
                    _col("id", sql_type="INTEGER", is_pk=True),
                    _col("acct_code", distinct_count_est=200),
                    _col("account_tier", distinct_count_est=4, format_shape="CODE_UPPER"),
                    _col("is_active", sql_type="BOOLEAN", distinct_count_est=2),  # skipped
                    _col("region_single", distinct_count_est=1),  # distinct < 2 -> skipped
                    _col("wide_cat", distinct_count_est=25),  # distinct > 20 -> skipped
                ],
            )
        ],
    )
    qs, meta = survey_modeling_hints([prof], max_questions=None)
    assert meta["P4"] == 2
    qids = [q.question_id for q in qs]
    assert any(qid.startswith("Q-P4-SURR-account-id-") for qid in qids)
    assert any(qid.startswith("Q-P4-ENUM-account-account_tier-") for qid in qids)

    # DraftVaultIR suppresses both via business_keys and column_triage
    draft_sup = DraftVaultIR(
        project_id="p_p4_sup",
        ontology_version="1.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id="crm:accounts",
                        col_name="acct_code",
                        source_id="crm",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="CRM",
                    )
                ],
            )
        ],
        column_triage=[
            ColumnTriageEntry(
                table_id="crm:accounts",
                col_name="account_tier",
                disposition="MODELED",
                target_ref="ref_account_tier",
            )
        ],
    )
    qs_sup, meta_sup = survey_modeling_hints([prof], draft_ir=draft_sup, max_questions=None)
    assert meta_sup["P4"] == 0
    assert qs_sup == []


def test_pagination_remaining_count_and_answered_suppression() -> None:
    """Pagination (max_questions, offset, 0/None), remaining_count, & answered_question_ids suppression."""
    prof = SourceProfile(
        source_id="erp",
        schema_fingerprint="fp_page",
        table_count=3,
        tables=[
            TableProfile(
                table_id="erp:erp_orders",
                table_name="erp_orders",
                row_count_est=500,
                best_non_surrogate_bk_candidate_col="order_num",
                columns=[
                    _col("id", sql_type="INTEGER", is_pk=True),
                    _col("order_num", distinct_count_est=500),
                    _col("order_status", distinct_count_est=5, format_shape="CODE_UPPER"),
                    _col("priority_code", distinct_count_est=3, format_shape="CODE_UPPER"),
                    _col("channel_type", distinct_count_est=4, format_shape="CODE_UPPER"),
                    _col("region_code", distinct_count_est=6, format_shape="CODE_UPPER"),
                ],
            ),
            TableProfile(
                table_id="crm:crm_orders",
                table_name="crm_orders",
                row_count_est=500,
                columns=[
                    _col("order_num", is_pk=True),
                    _col("order_status", distinct_count_est=5, format_shape="CODE_UPPER"),
                ],
            ),
        ],
    )

    all_qs, meta_all_none = survey_modeling_hints([prof], max_questions=None)
    all_qs_zero, meta_all_zero = survey_modeling_hints([prof], max_questions=0)
    total = meta_all_none["total_candidates"]
    assert total == 7  # 1 P1 + 6 P4 (1 SURR + 5 ENUM)
    assert len(all_qs) == total
    assert meta_all_none["returned_count"] == total
    assert meta_all_none["remaining_count"] == 0
    assert [q.question_id for q in all_qs_zero] == [q.question_id for q in all_qs]
    assert meta_all_zero["remaining_count"] == 0

    page1, m1 = survey_modeling_hints([prof], max_questions=2, offset=0)
    page2, m2 = survey_modeling_hints([prof], max_questions=2, offset=2)
    page3, m3 = survey_modeling_hints([prof], max_questions=2, offset=4)
    page4, m4 = survey_modeling_hints([prof], max_questions=2, offset=6)
    page_end, m_end = survey_modeling_hints([prof], max_questions=2, offset=total)

    assert m1 == {
        "total_candidates": total,
        "returned_count": 2,
        "offset": 0,
        "remaining_count": 5,
        "P1": 1,
        "P2": 0,
        "P3": 0,
        "P4": 6,
    }
    assert m2["returned_count"] == 2 and m2["remaining_count"] == 3
    assert m3["returned_count"] == 2 and m3["remaining_count"] == 1
    assert m4["returned_count"] == 1 and m4["remaining_count"] == 0
    assert page_end == [] and m_end["returned_count"] == 0 and m_end["remaining_count"] == 0
    assert [q.question_id for q in (*page1, *page2, *page3, *page4)] == [
        q.question_id for q in all_qs
    ]

    # answered_question_ids removes answered items before counting & pagination
    answered = {all_qs[0].question_id, all_qs[1].question_id}
    qs_rem, m_rem = survey_modeling_hints(
        [prof], answered_question_ids=answered, max_questions=2, offset=0
    )
    assert m_rem["total_candidates"] == total - 2
    assert m_rem["returned_count"] == 2
    assert m_rem["remaining_count"] == total - 4
    assert {q.question_id for q in qs_rem}.isdisjoint(answered)
