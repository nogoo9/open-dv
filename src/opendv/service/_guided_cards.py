"""Plain-English entity/explainer/decision-card builders and jargon sanitizer for Guided UX."""

from __future__ import annotations

import hashlib
from typing import Literal

from opendv.models.diagnostics import (
    ClarificationQuestion,
    HorizonOptionSpec,
    HorizonQuestion,
    VerifierDiagnostic,
)
from opendv.models.vault_ir import (
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
    DraftVaultIR,
    LinkKind,
    SatKind,
)
from opendv.models.vault_ir_certified import (
    CertifiedHubSpec,
    CertifiedLinkSpec,
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
)
from opendv.models.wire_dtos_vault import (
    GuidedDecisionCard,
    GuidedEntityCardSpec,
    GuidedTableExplainerSpec,
    GuidedWaiverTargetSpec,
)
from opendv.service._guided_errors import (
    FALLBACK_TEMPLATES,
    FORBIDDEN_GUIDED_JARGON,
    GUIDED_MILESTONES,
    TemplateKey,
    contains_forbidden_jargon,
    filter_jargon_free_rows,
    safe_entity_label,
    sanitize_or_fallback,
)


def _hub_card_and_explainer(
    hub: CertifiedHubSpec | DraftHubSpec,
) -> tuple[GuidedEntityCardSpec, GuidedTableExplainerSpec]:
    label = safe_entity_label(hub.hub_name, fallback="Business Entity")
    impact: Literal["BUSINESS_READY", "LOCK_IN"] = (
        "BUSINESS_READY" if hub.is_planned_concept else "LOCK_IN"
    )
    badge = "Planned Concept" if hub.is_planned_concept else "Core Business Entity"
    summary = (
        f"Planned business entity '{label}' awaiting source attachment."
        if hub.is_planned_concept
        else f"Tracks unique '{label}' records across attached data sources."
    )
    card = GuidedEntityCardSpec(
        entity_id=hub.hub_name,
        title=label,
        badge_label=badge,
        summary=summary,
        change_impact=impact,
    )
    explainer = GuidedTableExplainerSpec(
        business_purpose=(
            f"Represents the core business entity '{label}' and anchors its unique business keys."
        ),
        history_protection=(
            "Never overwrites existing records; each new business key is recorded once "
            "with source lineage."
        ),
        flexible_here=(
            "New descriptive attributes and companion extension tables can be added "
            "in future waves."
        ),
        lock_in_contrast=(
            "The core business key columns are locked once certified to preserve historical joins."
        ),
    )
    return card, explainer


def _link_card_and_explainer(
    lnk: CertifiedLinkSpec | DraftLinkSpec,
) -> tuple[GuidedEntityCardSpec, GuidedTableExplainerSpec]:
    label = safe_entity_label(lnk.link_name, fallback="Business Relationship")
    badge_map: dict[LinkKind, str] = {
        "SAME_AS": "ID Crosswalk",
        "TRANSACTIONAL": "Immutable Event",
        "HIERARCHICAL": "Entity Hierarchy",
        "UNSTRUCTURED_EXTRACTION_LINK": "Document Relationship",
        "STANDARD": "Business Relationship",
    }
    badge = badge_map.get(lnk.link_kind, "Business Relationship")
    joined = ", ".join(safe_entity_label(p.hub_name, fallback="Entity") for p in lnk.participants)
    summary = (
        f"Connects {joined} into a unified relationship history."
        if joined
        else f"Tracks '{label}' relationships across business entities."
    )
    card = GuidedEntityCardSpec(
        entity_id=lnk.link_name,
        title=label,
        badge_label=badge,
        summary=summary,
        change_impact="LOCK_IN",
    )
    explainer = GuidedTableExplainerSpec(
        business_purpose=(
            f"Captures how {joined or 'business entities'} relate to one another in '{label}'."
        ),
        history_protection=(
            "Relationship rows are append-only so past associations remain fully auditable."
        ),
        flexible_here=(
            "Additional descriptive relationship attributes or source feeds can be attached later."
        ),
        lock_in_contrast=(
            "The participating entities defining each link grain stay locked after certification."
        ),
    )
    return card, explainer


def _sat_card_and_explainer(
    sat: CertifiedSatelliteSpec | DraftSatelliteSpec,
) -> tuple[GuidedEntityCardSpec, GuidedTableExplainerSpec]:
    label = safe_entity_label(sat.sat_name, fallback="Attribute History")
    parent_label = safe_entity_label(sat.parent_name, fallback="parent entity")
    sat_meta: dict[
        SatKind,
        tuple[
            str,
            Literal["FLEXIBLE", "PRIVACY_ISOLATED", "COMPANION_TABLE"],
            str,
        ],
    ] = {
        "PII": (
            "Privacy-Protected",
            "PRIVACY_ISOLATED",
            f"Isolates sensitive personal attributes for '{parent_label}' under strict privacy.",
        ),
        "EXT": (
            "Wave Extension",
            "COMPANION_TABLE",
            f"Adds new wave attributes alongside existing '{parent_label}' history.",
        ),
        "MAS": (
            "Multi-Row History",
            "FLEXIBLE",
            f"Stores multiple concurrent items per '{parent_label}' at the same load timestamp.",
        ),
        "ESAT": (
            "Membership Timeline",
            "FLEXIBLE",
            f"Tracks when '{parent_label}' relationships begin and end over time.",
        ),
        "AI_PROVENANCE_SATELLITE": (
            "Document Provenance",
            "FLEXIBLE",
            f"Stores document citations, review status, and confidence for '{parent_label}'.",
        ),
        "STANDARD": (
            "Change History",
            "FLEXIBLE",
            f"Tracks descriptive attribute changes over time for '{parent_label}'.",
        ),
    }
    badge, impact, purpose = sat_meta[sat.sat_kind]
    card = GuidedEntityCardSpec(
        entity_id=sat.sat_name,
        title=label,
        badge_label=badge,
        summary=purpose,
        change_impact=impact,
    )
    explainer = GuidedTableExplainerSpec(
        business_purpose=purpose,
        history_protection=(
            "Records a new version only when descriptive values change, preserving prior snapshots."
        ),
        flexible_here=(
            "New columns in future waves go into companion extension tables without touching "
            "existing history."
        ),
        lock_in_contrast=(
            "Existing columns in this table stay fixed so historical comparisons remain valid."
        ),
    )
    return card, explainer


def build_entities_and_explainers(
    ir: CertifiedVaultIR | DraftVaultIR | None,
) -> tuple[list[GuidedEntityCardSpec], dict[str, GuidedTableExplainerSpec]]:
    """Build plain-English entity cards and 4-field explainers from Vault IR."""
    if ir is None:
        return [], {}
    entities: list[GuidedEntityCardSpec] = []
    explainers: dict[str, GuidedTableExplainerSpec] = {}
    for hub in ir.hubs:
        card, exp = _hub_card_and_explainer(hub)
        entities.append(card)
        explainers[card.entity_id] = exp
    for lnk in ir.links:
        card, exp = _link_card_and_explainer(lnk)
        entities.append(card)
        explainers[card.entity_id] = exp
    for sat in ir.satellites:
        card, exp = _sat_card_and_explainer(sat)
        entities.append(card)
        explainers[card.entity_id] = exp
    return entities, explainers


def build_clarification_card(
    q: ClarificationQuestion,
    *,
    source_step: Literal["step4", "step6_hold"],
) -> GuidedDecisionCard:
    """Project a Step 4 or Step 6 ClarificationQuestion into a GuidedDecisionCard."""
    t_key: TemplateKey = "step4_hold" if source_step == "step4" else "step6_hold"
    label = safe_entity_label(q.concept_name, fallback="this concept")
    title = sanitize_or_fallback(q.title, t_key, field="title", entity_label=label)
    if source_step == "step4":
        summary = sanitize_or_fallback(q.prompt, "step4_hold", field="prompt", entity_label=label)
        why = sanitize_or_fallback(
            q.impact_if_locked, "step4_hold", field="impact", entity_label=label
        )
    else:
        summary = sanitize_or_fallback(q.prompt, "step6_hold", field="summary", entity_label=label)
        why = sanitize_or_fallback(
            q.impact_if_locked, "step6_hold", field="why_it_matters", entity_label=label
        )
    rec = q.recommended_answer
    rec_id = (
        rec
        if rec and not contains_forbidden_jargon(rec) and rec in ("lock_shared", "keep_scoped")
        else "lock_shared"
    )
    options = [
        HorizonOptionSpec(
            option_id="lock_shared",
            label="Lock shared business key",
            description="Unify records using a single shared identifier across feeds.",
            implications="Locks this identifier grain across future waves.",
        ),
        HorizonOptionSpec(
            option_id="keep_scoped",
            label="Keep source-scoped",
            description="Keep identifiers scoped per source system and link via crosswalks.",
            implications="Preserves flexibility if feeds later diverge.",
        ),
    ]
    return GuidedDecisionCard(
        card_id=f"step4-{q.question_id}" if source_step == "step4" else q.question_id,
        source_step=source_step,
        title=title,
        plain_summary=summary,
        why_it_matters=why,
        lock_in_option_label="Lock shared business key",
        flexible_option_label="Keep source-scoped",
        recommended_option_id=rec_id,
        options=options,
        sample_evidence=filter_jargon_free_rows(q.sample_rows, limit=5),
    )


def build_horizon_card(q: HorizonQuestion) -> GuidedDecisionCard:
    """Project a HorizonQuestion into a jargon-free GuidedDecisionCard preserving option_ids."""
    label = safe_entity_label(q.title, fallback="this model")
    title = sanitize_or_fallback(q.title, "step6_v8_horizon", field="title", entity_label=label)
    summary = sanitize_or_fallback(
        q.prompt, "step6_v8_horizon", field="summary", entity_label=label
    )
    why = sanitize_or_fallback(
        q.impact_summary, "step6_v8_horizon", field="why_it_matters", entity_label=label
    )
    clean_options: list[HorizonOptionSpec] = []
    for idx, opt in enumerate(q.options):
        opt_label = opt.label.strip() if opt.label.strip() else f"Option {idx + 1}"
        if contains_forbidden_jargon(opt_label):
            opt_label = f"Option {idx + 1}"
        opt_desc = sanitize_or_fallback(
            opt.description, "step6_v8_horizon", field="summary", entity_label=label
        )
        opt_imp = sanitize_or_fallback(
            opt.implications, "step6_v8_horizon", field="why_it_matters", entity_label=label
        )
        clean_hint = (
            opt.operator_hint
            if opt.operator_hint and not contains_forbidden_jargon(opt.operator_hint)
            else None
        )
        clean_options.append(
            HorizonOptionSpec(
                option_id=opt.option_id,
                label=opt_label,
                description=opt_desc,
                implications=opt_imp,
                operator_hint=clean_hint,
            )
        )
    if not clean_options:
        clean_options = [
            HorizonOptionSpec(
                option_id="accept",
                label="Keep current design",
                description="Proceed with the current verified schema for this wave.",
                implications="Documents your review decision for future waves.",
            ),
            HorizonOptionSpec(
                option_id="steer",
                label="Harden for future feeds",
                description="Apply safer additive isolation before certifying.",
                implications="Reduces breaking-change risk in later waves.",
            ),
        ]
    rec_id = clean_options[0].option_id
    lock_lbl = clean_options[0].label
    flex_lbl = clean_options[-1].label if len(clean_options) > 1 else "Keep flexible"
    return GuidedDecisionCard(
        card_id=q.question_id,
        source_step="step6_horizon",
        title=title,
        plain_summary=summary,
        why_it_matters=why,
        lock_in_option_label=lock_lbl,
        flexible_option_label=flex_lbl,
        recommended_option_id=rec_id,
        options=clean_options,
        sample_evidence=[],
    )


def build_v6b_card_and_target(
    diag: VerifierDiagnostic,
) -> tuple[GuidedDecisionCard, GuidedWaiverTargetSpec]:
    """Project a waivable V6b VerifierDiagnostic into a GuidedDecisionCard + waiver target."""
    prefix = "ONTW" if diag.rule_id.startswith("ONT-") else "WAIV"
    digest = hashlib.sha256(f"{diag.rule_id}:{diag.entity_name}".encode()).hexdigest()[:8]
    waiver_id = diag.suggested_waiver_id or f"{prefix}-{digest}"
    card_id = f"v6b-{waiver_id}"
    label = safe_entity_label(diag.entity_name, fallback="this table")
    title = FALLBACK_TEMPLATES["step6_v6b_waiver"]["title"].format(entity_label=label)
    summary = sanitize_or_fallback(
        diag.message, "step6_v6b_waiver", field="summary", entity_label=label
    )
    why = FALLBACK_TEMPLATES["step6_v6b_waiver"]["why_it_matters"].format(entity_label=label)
    card = GuidedDecisionCard(
        card_id=card_id,
        source_step="step6_v6b",
        title=title,
        plain_summary=summary,
        why_it_matters=why,
        lock_in_option_label="Sign business waiver",
        flexible_option_label="Ask AI to refactor instead",
        recommended_option_id="refactor",
        options=[
            HorizonOptionSpec(
                option_id="refactor",
                label="Ask AI to refactor instead",
                description="Let the synthesis loop redesign this table automatically.",
                implications="Resolves the modeling warning without a manual waiver.",
            ),
            HorizonOptionSpec(
                option_id="waive",
                label="Sign business waiver",
                description="Keep the current layout and record an audited business rationale.",
                implications="Requires at least a 20-character business explanation.",
            ),
        ],
        sample_evidence=[],
    )
    target = GuidedWaiverTargetSpec(
        waiver_id=waiver_id,
        rule_id=diag.rule_id,
        entity_name=diag.entity_name,
    )
    return card, target


__all__ = [
    "FALLBACK_TEMPLATES",
    "FORBIDDEN_GUIDED_JARGON",
    "GUIDED_MILESTONES",
    "TemplateKey",
    "build_clarification_card",
    "build_entities_and_explainers",
    "build_horizon_card",
    "build_v6b_card_and_target",
    "contains_forbidden_jargon",
    "safe_entity_label",
    "sanitize_or_fallback",
]
