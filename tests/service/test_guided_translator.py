"""UAT-V25-SVC-06 tests for GuidedUxTranslator, FORBIDDEN_GUIDED_JARGON, and 34 ErrorCodes."""

from __future__ import annotations

from opendv.models import (
    BusinessKeyPart,
    CertifiedHubSpec,
    CertifiedLinkSpec,
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    ClarificationQuestion,
    ErrorCode,
    HorizonCriticVerdict,
    HorizonOptionSpec,
    HorizonQuestion,
    LinkParticipantSpec,
    PhysicalLayoutHints,
    RunStateResp,
    SemanticCriticVerdict,
    VerifierDiagnostic,
    create_problem_details,
)
from opendv.service.guided_translator import (
    ERROR_UX_TRANSLATIONS,
    FALLBACK_TEMPLATES,
    FORBIDDEN_GUIDED_JARGON,
    GUIDED_MILESTONES,
    GuidedRunView,
    GuidedUxTranslator,
    contains_forbidden_jargon,
)


def _sample_certified_ir() -> CertifiedVaultIR:
    hints = PhysicalLayoutHints(cluster_by_cols=[], partition_grain=None, iceberg_tags={})
    return CertifiedVaultIR(
        project_id="am-ibor-2026",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_account",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id="src:acct",
                        col_name="account_id",
                        source_id="src",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="IBOR",
                    )
                ],
                physical_hints=hints,
            ),
            CertifiedHubSpec(
                hub_name="hub_planned_benchmark",
                key_tier="TIER_1_GLOBAL_STANDARD",
                is_planned_concept=True,
                physical_hints=None,
            ),
        ],
        links=[
            CertifiedLinkSpec(
                link_name="lnk_account_trade",
                link_kind="TRANSACTIONAL",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_account",
                        role_name="account",
                        fk_col_name="hk_account",
                        source_col_names=["account_id"],
                    )
                ],
                physical_hints=hints,
            )
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name="sat_account_pii",
                sat_kind="PII",
                parent_name="hub_account",
                source_id="src",
                source_table_id="src:acct",
                physical_hints=hints,
            ),
            CertifiedSatelliteSpec(
                sat_name="sat_account_ext_v2",
                sat_kind="EXT",
                parent_name="hub_account",
                source_id="src",
                source_table_id="src:acct",
                extends_sat_name="sat_account",
                companion_epoch=2,
                physical_hints=hints,
            ),
        ],
    )


def _assert_guided_view_jargon_free(view: GuidedRunView) -> None:
    texts: list[str] = [
        view.headline,
        *view.steps_summary,
        view.readiness_brief.confidence_summary,
        *view.readiness_brief.plain_highlights,
        *view.readiness_brief.watchlist,
        *view.readiness_brief.blocked_reasons,
    ]
    for ent in view.entities:
        texts.extend([ent.title, ent.badge_label, ent.summary])
    for exp in view.explainers.values():
        texts.extend(
            [
                exp.business_purpose,
                exp.history_protection,
                exp.flexible_here,
                exp.lock_in_contrast,
            ]
        )
    for card in view.decision_cards:
        texts.extend(
            [
                card.title,
                card.plain_summary,
                card.why_it_matters,
                card.lock_in_option_label,
                card.flexible_option_label,
                *card.sample_evidence,
            ]
        )
        for opt in card.options:
            texts.extend([opt.label, opt.description, opt.implications])
            if opt.operator_hint:
                texts.append(opt.operator_hint)

    for txt in texts:
        assert not contains_forbidden_jargon(txt), f"Forbidden jargon leaked into Guided UI: {txt!r}"


def test_clean_certified_run_populates_entities_explainers_and_metadata() -> None:
    translator = GuidedUxTranslator()
    run_state = RunStateResp(
        run_id="run-step6-01",
        project_id="am-ibor-2026",
        step="step6",
        status="COMPLETED",
        policy="balanced",
        phase="S6-07",
        attempt=2,
        budget_turns_used=4,
        budget_tokens_used=12000,
    )
    view = translator.translate_run_view(
        run_state,
        certified_ir=_sample_certified_ir(),
        v7_verdict=SemanticCriticVerdict(score=96, findings=["Clean 5-zone vault structure."]),
        v8_verdict=HorizonCriticVerdict(
            score=91,
            classification="REASONABLY_STABLE",
            bucket_c_watchlist=["Monitor equity ticker feeds in future waves."],
        ),
        ontology_rev_id="rev-v1.0.0",
        step4_run_id="run-step4-01",
        energy_e=0,
    )

    assert len(GUIDED_MILESTONES) == 4
    assert view.milestone_index == 3
    assert view.ontology_rev_id == "rev-v1.0.0"
    assert view.policy == "balanced"
    assert view.step4_run_id == "run-step4-01"
    assert view.readiness_brief.certification_label == "CERTIFIED"
    assert len(view.entities) == 5
    assert set(view.explainers) == {
        "hub_account",
        "hub_planned_benchmark",
        "lnk_account_trade",
        "sat_account_pii",
        "sat_account_ext_v2",
    }
    by_id = {e.entity_id: e for e in view.entities}
    assert by_id["hub_account"].change_impact == "LOCK_IN"
    assert by_id["hub_planned_benchmark"].change_impact == "BUSINESS_READY"
    assert by_id["sat_account_pii"].change_impact == "PRIVACY_ISOLATED"
    assert by_id["sat_account_ext_v2"].change_impact == "COMPANION_TABLE"
    assert view.technical_drawer["v7_score"] == 96
    assert view.technical_drawer["v8_score"] == 91
    _assert_guided_view_jargon_free(view)


def test_jargon_injection_falls_back_to_plain_templates_without_dropping_verdicts() -> None:
    translator = GuidedUxTranslator()
    q_step4 = ClarificationQuestion(
        question_id="q-s4-1",
        concept_name="Account",
        title="Resolve TIER_U3 BKCC key for DV-DYN-004",
        prompt="CEGAR hit TRAP-02 on Hash_Diff and SQLGlot Alg A.5.",
        sample_rows=["Account 1001 (IBOR)"],
        impact_if_locked="Locks BKCC across EVO-01.",
        recommended_answer="BKCC=IBOR",
    )
    sanitized_q4 = translator.translate_clarification_question(q_step4)
    assert sanitized_q4.question_id == "q-s4-1"
    assert not contains_forbidden_jargon(sanitized_q4.title)
    assert not contains_forbidden_jargon(sanitized_q4.prompt)
    assert not contains_forbidden_jargon(sanitized_q4.impact_if_locked)
    assert sanitized_q4.recommended_answer is not None
    assert not contains_forbidden_jargon(sanitized_q4.recommended_answer)

    q_step6_hold = ClarificationQuestion(
        question_id="q-s6-1",
        concept_name="Collateral",
        title="DV-STR-005 ambiguity",
        prompt="Select CDK or BKCC per INV-03.",
        sample_rows=["COLL-99"],
        impact_if_locked="Breaks EVAL-RUN-01 if changed.",
    )
    q_horizon = HorizonQuestion(
        question_id="hq-brk-02",
        brk_code="BRK-02",
        title="BRK-02 CDK widening risk",
        prompt="Check TRAP-08 and DV-AP-005 with Alg A.4.",
        options=[
            HorizonOptionSpec(
                option_id="opt_keep",
                label="Keep current BKCC",
                description="Avoids CEGAR re-run for EVAL-AST-01.",
                implications="May hit BRK-02 later.",
                operator_hint="SET_BKCC",
            ),
            HorizonOptionSpec(
                option_id="opt_harden",
                label="Harden grain",
                description="Protects against future wave drift safely.",
                implications="Adds a companion extension table.",
            ),
        ],
        impact_summary="Impacts TMP-01 and KEY-02.",
    )
    v6b_diag = VerifierDiagnostic(
        gate="V6b",
        rule_id="DV-AP-005",
        severity="WARN",
        entity_name="sat_account_pii",
        waivable=True,
        suggested_waiver_id="WAIV-deadbeef",
        message="DV-AP-005 triggered by Hash_Diff and ONT-AP-002.",
    )
    run_state = RunStateResp(
        run_id="run-step6-hold",
        step="step6",
        status="HOLD_CHECKPOINTED",
        policy="strict",
        phase="S6-06",
        attempt=1,
        budget_turns_used=5,
        budget_tokens_used=20000,
        pending_questions=[q_step6_hold, q_horizon],
    )
    view = translator.translate_run_view(
        run_state,
        certified_ir=_sample_certified_ir(),
        diagnostics=[v6b_diag],
        v7_verdict=SemanticCriticVerdict(
            score=88,
            findings=["Check DV-DYN-004 and BKCC alignment."],
        ),
        v8_verdict=HorizonCriticVerdict(
            score=78,
            classification="NEEDS_STEER",
            bucket_b_questions=[q_horizon],
            bucket_c_watchlist=["Watch BRK-02 CDK."],
        ),
        step4_run_id="run-s4-backlink",
        step4_pending_questions=[q_step4],
        ontology_rev_id="rev-v1.0.0",
    )

    assert len(FALLBACK_TEMPLATES) == 5
    assert view.readiness_brief.certification_label == "UNCERTIFIED"
    assert view.milestone_index == 2
    assert [c.source_step for c in view.decision_cards] == [
        "step4",
        "step6_hold",
        "step6_horizon",
        "step6_v6b",
    ]
    assert "v6b-WAIV-deadbeef" in view.v6b_waiver_targets
    target = view.v6b_waiver_targets["v6b-WAIV-deadbeef"]
    assert (target.waiver_id, target.rule_id, target.entity_name) == (
        "WAIV-deadbeef",
        "DV-AP-005",
        "sat_account_pii",
    )
    # Option IDs & verdicts preserved despite jargon fallback
    horizon_card = view.decision_cards[2]
    assert horizon_card.card_id == "hq-brk-02"
    assert [o.option_id for o in horizon_card.options] == ["opt_keep", "opt_harden"]
    # Raw technical strings preserved in technical_drawer
    assert view.technical_drawer["v7_score"] == 88
    assert view.technical_drawer["v7_findings"] == ["Check DV-DYN-004 and BKCC alignment."]
    assert view.technical_drawer["v8_watchlist_raw"] == ["Watch BRK-02 CDK."]
    assert view.technical_drawer["diagnostic_codes"] == ["V6b:DV-AP-005:sat_account_pii"]
    _assert_guided_view_jargon_free(view)


def test_fast_and_uncertified_draft_readiness_templates() -> None:
    translator = GuidedUxTranslator()
    fast_state = RunStateResp(
        run_id="run-fast",
        step="step6",
        status="COMPLETED",
        policy="fast",
        phase="S6-07",
        attempt=1,
        budget_turns_used=1,
        budget_tokens_used=1000,
    )
    fast_view = translator.translate_run_view(fast_state, certified_ir=_sample_certified_ir())
    assert fast_view.readiness_brief.certification_label == "CERTIFIED_FAST"
    assert fast_view.milestone_index == 3
    _assert_guided_view_jargon_free(fast_view)

    draft_state = RunStateResp(
        run_id="run-draft",
        step="step6",
        status="UNCERTIFIED_DRAFT",
        policy="balanced",
        phase="S6-04",
        attempt=4,
        budget_turns_used=40,
        budget_tokens_used=500000,
    )
    draft_view = translator.translate_run_view(draft_state, certified_ir=_sample_certified_ir())
    assert draft_view.readiness_brief.certification_label == "UNCERTIFIED"
    assert draft_view.milestone_index == 2
    assert draft_view.readiness_brief.blocked_reasons
    _assert_guided_view_jargon_free(draft_view)


def test_all_34_error_codes_have_jargon_free_ux_translations() -> None:
    translator = GuidedUxTranslator()
    catalog = translator.all_error_translations()
    assert set(catalog) == set(ErrorCode)
    assert len(catalog) == 34
    assert set(ERROR_UX_TRANSLATIONS) == set(ErrorCode)

    for code, entry in catalog.items():
        assert entry.plain_title.strip()
        assert entry.plain_summary.strip()
        assert entry.next_step.strip()
        for field_val in (entry.plain_title, entry.plain_summary, entry.next_step):
            assert not FORBIDDEN_GUIDED_JARGON.search(field_val), (
                f"ErrorCode {code} UX text contains forbidden jargon: {field_val!r}"
            )

    problem = create_problem_details(
        ErrorCode.PROJECT_ID_CONFLICT,
        detail="Project 'am-ibor-2026' already exists",
        instance="/api/v1/projects/import",
    )
    translated = translator.translate_problem_details(problem)
    assert translated.error_code == ErrorCode.PROJECT_ID_CONFLICT
    assert translated.http_status == 409
    assert translated.exit_code == 4


def test_sample_rows_and_error_detail_filter_forbidden_jargon() -> None:
    translator = GuidedUxTranslator()
    q = ClarificationQuestion(
        question_id="q-sample-jargon",
        concept_name="Account",
        title="Confirm account identifier",
        prompt="Select which identifier identifies each customer account.",
        sample_rows=[
            "Account 1001",
            "BKCC=IBOR; CEGAR DV-STR-001; Hash_Diff",
            "TRAP-02 fallback row",
        ],
        impact_if_locked="Keeps account records aligned across future updates.",
    )
    sanitized_q = translator.translate_clarification_question(q)
    assert sanitized_q.sample_rows == ["Account 1001"]
    assert all(not contains_forbidden_jargon(r) for r in sanitized_q.sample_rows)

    view = translator.translate_run_view(
        RunStateResp(
            run_id="run-s4-sample",
            step="step4",
            status="HOLD_CHECKPOINTED",
            policy="balanced",
            phase="S4-HOLD",
            attempt=1,
            budget_turns_used=1,
            budget_tokens_used=100,
        ),
        step4_pending_questions=[q],
    )
    assert view.decision_cards[0].sample_evidence == ["Account 1001"]
    _assert_guided_view_jargon_free(view)

    jargon_err = translator.translate_error_code(
        ErrorCode.VERIFICATION_GATE_FAILED,
        detail="CEGAR failed DV-STR-001 on BKCC",
    )
    assert not contains_forbidden_jargon(jargon_err.plain_summary)
    assert "DV-STR-001" not in jargon_err.plain_summary
    assert "BKCC" not in jargon_err.plain_summary

    clean_err = translator.translate_error_code(
        ErrorCode.VERIFICATION_GATE_FAILED,
        detail="Missing column account_id",
    )
    assert "Missing column account_id" in clean_err.plain_summary
    assert not contains_forbidden_jargon(clean_err.plain_summary)
