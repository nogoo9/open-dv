"""Deterministic zero-LLM Guided UX translator and FORBIDDEN_GUIDED_JARGON guard."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from opendv.models.diagnostics import (
    ClarificationQuestion,
    HorizonQuestion,
    VerifierDiagnostic,
)
from opendv.models.errors import ErrorCode, ProblemDetails
from opendv.models.events import RunCheckpointRecord
from opendv.models.policy import CegarPolicy
from opendv.models.vault_ir import DraftVaultIR
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_discovery import RunStateResp
from opendv.models.wire_dtos_vault import (
    GuidedDecisionCard,
    GuidedReadinessBrief,
    GuidedRunView,
    GuidedWaiverTargetSpec,
    HorizonCriticVerdict,
    SemanticCriticVerdict,
)
from opendv.service._guided_cards import (
    FALLBACK_TEMPLATES,
    FORBIDDEN_GUIDED_JARGON,
    GUIDED_MILESTONES,
    TemplateKey,
    build_clarification_card,
    build_entities_and_explainers,
    build_horizon_card,
    build_v6b_card_and_target,
    contains_forbidden_jargon,
    safe_entity_label,
    sanitize_or_fallback,
)
from opendv.service._guided_errors import (
    ERROR_UX_TRANSLATIONS,
    GuidedErrorTranslation,
    filter_jargon_free_rows,
    translate_error_code,
    translate_problem_details,
)

CertificationLabel = Literal["UNCERTIFIED", "CERTIFIED_FAST", "CERTIFIED"]


def translate_clarification_question(question: ClarificationQuestion) -> ClarificationQuestion:
    """Sanitize a Step 4/6 ClarificationQuestion so primary prompts are jargon-free."""
    label = safe_entity_label(question.concept_name, fallback="this business concept")
    clean_title = sanitize_or_fallback(
        question.title, "step4_hold", field="title", entity_label=label
    )
    clean_prompt = sanitize_or_fallback(
        question.prompt, "step4_hold", field="prompt", entity_label=label
    )
    clean_impact = sanitize_or_fallback(
        question.impact_if_locked, "step4_hold", field="impact", entity_label=label
    )
    clean_rec: str | None = question.recommended_answer
    if clean_rec is not None and contains_forbidden_jargon(clean_rec):
        clean_rec = "Use the shared business identifier option"
    return ClarificationQuestion(
        question_id=question.question_id,
        concept_name=label,
        title=clean_title,
        prompt=clean_prompt,
        sample_rows=filter_jargon_free_rows(question.sample_rows),
        impact_if_locked=clean_impact,
        recommended_answer=clean_rec,
    )


def _resolve_cert_label(
    run_state: RunStateResp,
    explicit: CertificationLabel | None,
) -> CertificationLabel:
    if explicit is not None:
        return explicit
    if run_state.status == "COMPLETED":
        return "CERTIFIED_FAST" if run_state.policy == "fast" else "CERTIFIED"
    return "UNCERTIFIED"


def _resolve_milestone_index(
    run_state: RunStateResp,
    cert_label: CertificationLabel,
    has_pending_decisions: bool,
) -> int:
    if run_state.step == "step4":
        return 1
    if cert_label in ("CERTIFIED", "CERTIFIED_FAST") and not has_pending_decisions:
        return 3
    return 2


class GuidedUxTranslator:
    """Deterministic zero-LLM translator projecting Step 4/6 technical state to Guided UX."""

    def translate_clarification_question(
        self, question: ClarificationQuestion
    ) -> ClarificationQuestion:
        """Sanitize a ClarificationQuestion against FORBIDDEN_GUIDED_JARGON."""
        return translate_clarification_question(question)

    def translate_error_code(
        self, code: ErrorCode, *, detail: str | None = None
    ) -> GuidedErrorTranslation:
        """Translate a closed V2 ErrorCode into plain-language Guided UX copy."""
        return translate_error_code(code, detail=detail)

    def translate_problem_details(self, problem: ProblemDetails) -> GuidedErrorTranslation:
        """Translate an RFC 9457 ProblemDetails payload into plain-language Guided UX copy."""
        return translate_problem_details(problem)

    def all_error_translations(self) -> dict[ErrorCode, GuidedErrorTranslation]:
        """Return the complete 34-code plain-language UX translation catalog."""
        return dict(ERROR_UX_TRANSLATIONS)

    def translate_run_view(
        self,
        run_state: RunStateResp,
        *,
        certified_ir: CertifiedVaultIR | None = None,
        draft_ir: DraftVaultIR | None = None,
        diagnostics: Sequence[VerifierDiagnostic] = (),
        v7_verdict: SemanticCriticVerdict | None = None,
        v8_verdict: HorizonCriticVerdict | None = None,
        checkpoint: RunCheckpointRecord | None = None,
        step4_run_id: str | None = None,
        step4_pending_questions: Sequence[ClarificationQuestion] = (),
        ontology_rev_id: str | None = None,
        policy: CegarPolicy | None = None,
        certification_status: CertificationLabel | None = None,
        wave_number: int | None = None,
        attempt: int | None = None,
        energy_e: int | None = None,
        waiver_ids: Sequence[str] = (),
    ) -> GuidedRunView:
        """Build a jargon-free GuidedRunView while keeping raw telemetry in technical_drawer."""
        active_ir: CertifiedVaultIR | DraftVaultIR | None = certified_ir or draft_ir
        eff_policy: CegarPolicy = policy or run_state.policy or "balanced"
        eff_wave = wave_number or (active_ir.wave_number if active_ir is not None else 1)
        cert_label = _resolve_cert_label(run_state, certification_status)
        eff_attempt = attempt if attempt is not None else run_state.attempt

        entities, explainers = build_entities_and_explainers(active_ir)

        decision_cards: list[GuidedDecisionCard] = []
        v6b_targets: dict[str, GuidedWaiverTargetSpec] = {}
        waived_set = set(waiver_ids)
        if v7_verdict is not None:
            waived_set.update(v7_verdict.accepted_waiver_ids)
        if active_ir is not None:
            waived_set.update(j.waiver_id for j in active_ir.justifications)

        for q4 in step4_pending_questions:
            decision_cards.append(build_clarification_card(q4, source_step="step4"))

        raw_pending: list[ClarificationQuestion | HorizonQuestion] = list(
            run_state.pending_questions
        )
        if not raw_pending and checkpoint is not None:
            raw_pending = list(checkpoint.pending_questions)
        seen_q_ids: set[str] = set()
        for q_item in raw_pending:
            if q_item.question_id in seen_q_ids:
                continue
            seen_q_ids.add(q_item.question_id)
            if isinstance(q_item, ClarificationQuestion):
                decision_cards.append(build_clarification_card(q_item, source_step="step6_hold"))
            else:
                decision_cards.append(build_horizon_card(q_item))

        if v8_verdict is not None:
            for hq in v8_verdict.bucket_b_questions:
                if hq.question_id not in seen_q_ids:
                    seen_q_ids.add(hq.question_id)
                    decision_cards.append(build_horizon_card(hq))

        for diag in diagnostics:
            if diag.gate == "V6b" and diag.waivable and diag.severity != "ADVISORY":
                card, target = build_v6b_card_and_target(diag)
                if target.waiver_id not in waived_set:
                    decision_cards.append(card)
                    v6b_targets[card.card_id] = target

        milestone_idx = _resolve_milestone_index(run_state, cert_label, bool(decision_cards))
        steps_summary = [
            f"{idx + 1}. {name}: "
            + (
                "Completed"
                if idx < milestone_idx
                else ("Ready" if idx == milestone_idx and cert_label != "UNCERTIFIED" else "Active")
                if idx == milestone_idx
                else "Pending"
            )
            for idx, name in enumerate(GUIDED_MILESTONES)
        ]

        confidence_summary = FALLBACK_TEMPLATES["step6_readiness"][cert_label].format(
            wave_number=eff_wave
        )
        plain_highlights: list[str] = []
        if active_ir is not None:
            plain_highlights.append(
                f"Modeled {len(active_ir.hubs)} core business entities, "
                f"{len(active_ir.links)} relationships, and "
                f"{len(active_ir.satellites)} history tables."
            )
        core_fails = [
            d for d in diagnostics if d.gate in ("V1", "V2", "V3", "V4", "V5", "V6")
        ]
        if not core_fails and active_ir is not None:
            plain_highlights.append("All structural and runtime verification checks passed.")
        if v7_verdict is not None:
            if v7_verdict.score >= 90:
                plain_highlights.append("Semantic modeling review passed.")
            for finding in v7_verdict.findings:
                plain_highlights.append(
                    sanitize_or_fallback(
                        finding, "step6_v6b_waiver", field="summary", entity_label="this model"
                    )
                )

        watchlist: list[str] = []
        if v8_verdict is not None:
            for item in v8_verdict.bucket_c_watchlist:
                watchlist.append(
                    sanitize_or_fallback(
                        item, "step6_v8_horizon", field="summary", entity_label="this wave"
                    )
                )

        blocked_reasons: list[str] = []
        if cert_label == "UNCERTIFIED":
            if core_fails:
                blocked_reasons.append(
                    f"{len(core_fails)} core verification check(s) need attention."
                )
            if v6b_targets:
                blocked_reasons.append(
                    f"{len(v6b_targets)} design warning(s) require refactoring or a signed waiver."
                )
            if decision_cards and not core_fails and not v6b_targets:
                blocked_reasons.append("Waiting for steward confirmation on open decision cards.")
            if run_state.status == "UNCERTIFIED_DRAFT":
                blocked_reasons.append("Run paused at turn, token, or repair budget limit.")
            if run_state.status == "FAILED" and not blocked_reasons:
                blocked_reasons.append("Run ended with an unexpected execution failure.")

        headline_map: dict[CertificationLabel, str] = {
            "CERTIFIED": f"Wave {eff_wave} blueprint certified and ready for target build",
            "CERTIFIED_FAST": f"Wave {eff_wave} quick blueprint ready for target build",
            "UNCERTIFIED": (
                f"Wave {eff_wave} requires your review before target build"
                if decision_cards
                else f"Wave {eff_wave} synthesis in progress"
            ),
        }
        if run_state.step == "step4":
            headline = (
                "Discovery paused for business clarification"
                if decision_cards
                else "Discovery and ontology mapping in progress"
            )
        else:
            headline = headline_map[cert_label]

        technical_drawer: dict[str, str | int | list[str]] = {
            "run_id": run_state.run_id,
            "step": run_state.step,
            "status": run_state.status,
            "policy": eff_policy,
            "phase": run_state.phase,
            "attempt": eff_attempt,
            "diagnostic_codes": [f"{d.gate}:{d.rule_id}:{d.entity_name}" for d in diagnostics],
            "waiver_ids": sorted(waived_set),
        }
        if energy_e is not None:
            technical_drawer["energy_e"] = energy_e
        if v7_verdict is not None:
            technical_drawer["v7_score"] = v7_verdict.score
            technical_drawer["v7_findings"] = list(v7_verdict.findings)
        if v8_verdict is not None:
            technical_drawer["v8_score"] = v8_verdict.score
            technical_drawer["v8_classification"] = v8_verdict.classification
            technical_drawer["v8_watchlist_raw"] = list(v8_verdict.bucket_c_watchlist)

        return GuidedRunView(
            headline=headline,
            milestone_index=milestone_idx,
            steps_summary=steps_summary,
            decision_cards=decision_cards,
            readiness_brief=GuidedReadinessBrief(
                wave_number=eff_wave,
                certification_label=cert_label,
                confidence_summary=confidence_summary,
                plain_highlights=plain_highlights,
                watchlist=watchlist,
                blocked_reasons=blocked_reasons,
            ),
            ontology_rev_id=ontology_rev_id,
            policy=eff_policy,
            step4_run_id=step4_run_id,
            entities=entities,
            explainers=explainers,
            v6b_waiver_targets=v6b_targets,
            technical_drawer=technical_drawer,
        )


def build_guided_run_view(
    run_state: RunStateResp,
    **kwargs: object,
) -> GuidedRunView:
    """Convenience functional wrapper around GuidedUxTranslator.translate_run_view."""
    translator = GuidedUxTranslator()
    return translator.translate_run_view(run_state, **kwargs)  # type: ignore[arg-type]


__all__ = [
    "ERROR_UX_TRANSLATIONS",
    "FALLBACK_TEMPLATES",
    "FORBIDDEN_GUIDED_JARGON",
    "GUIDED_MILESTONES",
    "CertificationLabel",
    "GuidedErrorTranslation",
    "GuidedRunView",
    "GuidedUxTranslator",
    "TemplateKey",
    "build_guided_run_view",
    "contains_forbidden_jargon",
    "sanitize_or_fallback",
    "translate_clarification_question",
    "translate_error_code",
    "translate_problem_details",
]
