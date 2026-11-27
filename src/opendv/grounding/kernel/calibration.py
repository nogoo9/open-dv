"""Modality-aware Logit-Normal calibration, CI calculation, fast-path gating & selection."""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Literal

from opendv.models.grounding import (
    ArchetypeKind,
    CandidateMapping,
    CandidatePredicate,
    ConfidenceInterval,
    EntryKind,
    EvidenceContribution,
    GroundingPolicy,
    NoMatchCategory,
    ReviewReason,
    SelectionStatus,
    SemanticContext,
)
from opendv.models.grounding_spi import CalibrationParams, TableRerankAssignment

_EPS = 1e-6
_CLOSE_PROB = 0.45
_CHANNELS = ("lex", "surface", "shape_range", "anchor", "agent")
_OPAQUE_RE = re.compile(r"(?i)^(?:[a-z]|(?:col|c|f|field|attr|column|var|v)_?\d+)$")
_JARGON_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(DV-(?:STR|DYN|COL|AST|EVO|AP|G)-\d*|ONT-(?:AP|EVO)-\d+"
    r"|ALG\.?\s*A\.[1-9][ab]?|EVAL-(?:AST|RUN|FMT)-\d+|(?:TRAP|BRK|KEY|TMP|INV|EVO)-\d+"
    r"|BKCC|CDK|CEGAR|SQLGlot|Hash[\s_]*Diff|TIER_U3)(?![A-Za-z0-9])"
)
_SUFFIX_BY_ARCH: dict[str, frozenset[str]] = {
    "temporal": frozenset({"dt", "dts", "ts", "utc"}),
    "code_classifier": frozenset({"cd", "code"}),
    "identifier": frozenset({"id", "num", "code"}),
}
_BOOL_SUFFIXES = frozenset({"flg", "flag", "ind"})


@dataclass(frozen=True, slots=True)
class CandidateChannelInputs:
    entry_id: str
    label: str
    entry_kind: EntryKind
    origin_kind: Literal["project_graph_a", "reference_standard"]
    semantic_context: SemanticContext
    aliases: tuple[str, ...] = ()
    replaces_uris: tuple[str, ...] = ()
    lex_score: float = 0.0
    surface_score: float = 0.0
    has_surface_rule: bool = False
    shape_range_score: float = 0.0
    hard_veto: bool = False
    base_anchor_score: float = 0.0
    inverse_direction_score: float = 0.0
    suggested_predicate_hint: Literal["exact", "close", "related"] | None = None
    surface_detail: str = ""
    shape_detail: str = ""
    anchor_detail: str = ""
    corroborated: bool = True
    is_boolean_datatype: bool = False
    is_qualifier_narrowed: bool = False
    matched_via_non_exact_synonym: bool = False
    rerank_index: int | None = None


@dataclass(frozen=True, slots=True)
class ColumnCalibrationContext:
    column_name: str
    n_eff: int
    masked_count: int = 0
    has_shape_or_declared_type: bool = True
    has_column_surface_pattern: bool = False
    has_checksum_pattern: bool = False
    has_checksumless_pattern: bool = False
    anchor_enabled: bool = False
    has_top_table_anchor: bool = False
    rerank_assignment: TableRerankAssignment | None = None
    rerank_expected: bool = False
    is_fast_pathed: bool = False
    prescreen_category: Literal["system_audit_metadata", "technical_surrogate"] | None = None
    prescreen_detail: str | None = None


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    entry_id: str
    probability: float
    lower: float
    upper: float
    sigma_eff: float
    rho_mod: float
    sigma_disagree: float
    phi_channels: dict[str, float]
    m_obs: frozenset[str]
    m_active: frozenset[str]
    contributions: list[EvidenceContribution]
    source_input: CandidateChannelInputs


@dataclass(frozen=True, slots=True)
class CalibrationOutcome:
    status: SelectionStatus
    review_reason: ReviewReason | None
    choices: list[CandidateMapping]
    no_match_probability: float
    no_match_category: NoMatchCategory | None
    no_match_detail: str | None
    best_rejected_candidate: CandidateMapping | None
    fast_path_eligible: bool
    selection_rule_applied: str
    steward_summary: str
    scored_candidates: list[ScoredCandidate]
    has_any_hard_veto: bool


def _clamp(val: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return 0.0 if not math.isfinite(val) else max(lo, min(hi, val))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if x >= 0.0 else (z := math.exp(x)) / (1.0 + z)


def _split_tokens(text: str) -> list[str]:
    if not text:
        return []
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text.strip())
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return [t.lower() for t in re.findall(r"[A-Za-z0-9]+", s)]


def normalize_property_label(label: str) -> list[str]:
    toks = _split_tokens(label)
    return toks[1:] if len(toks) > 1 and toks[0] in ("has", "is") else toks


def normalize_column_tokens(
    column_name: str, *, archetype: ArchetypeKind | None, is_boolean_datatype: bool = False
) -> list[str]:
    toks = _split_tokens(column_name)
    if len(toks) <= 1:
        return toks
    suf = _SUFFIX_BY_ARCH.get(archetype or "", _BOOL_SUFFIXES if is_boolean_datatype else None)
    return toks[:-1] if suf and toks[-1] in suf else toks


def determine_suggested_predicate(
    column_name: str, cand: CandidateChannelInputs, prob: float, status: SelectionStatus
) -> CandidatePredicate:
    if cand.suggested_predicate_hint == "related":
        return "skos:relatedMatch"
    c_toks = normalize_column_tokens(
        column_name, archetype=cand.semantic_context.archetype,
        is_boolean_datatype=cand.is_boolean_datatype,
    )
    l_toks = normalize_property_label(cand.label)
    a_toks = [normalize_property_label(a) for a in cand.aliases if a.strip()]
    if (
        status == "MATCHED" and cand.suggested_predicate_hint != "close"
        and not cand.matched_via_non_exact_synonym and not cand.is_qualifier_narrowed
        and c_toks and (c_toks == l_toks or c_toks in a_toks)
    ):
        return "skos:exactMatch"
    if (
        cand.is_qualifier_narrowed or cand.matched_via_non_exact_synonym
        or cand.suggested_predicate_hint == "close" or prob >= _CLOSE_PROB
    ):
        return "skos:closeMatch"
    return "skos:relatedMatch"


def infer_no_match_category(
    ctx: ColumnCalibrationContext, scored: Sequence[ScoredCandidate], params: CalibrationParams
) -> tuple[NoMatchCategory, str]:
    if ctx.prescreen_category is not None:
        cat = ctx.prescreen_category
        return (cat, ctx.prescreen_detail or f"Classified as {cat.replace('_', ' ')}.")
    if ctx.n_eff == 0 or _OPAQUE_RE.fullmatch(ctx.column_name.strip().lower()):
        return ("insufficient_evidence", "Column lacks unmasked values or uses an opaque header.")
    if scored and scored[0].probability >= params.min_top1_probability:
        return ("below_threshold", "Top candidate probability did not clear the no-match gate.")
    return ("out_of_vocabulary_domain", "No reference concept or property matched the column.")


def build_steward_summary(
    col_name: str, status: SelectionStatus, choices: Sequence[CandidateMapping],
    review_reason: ReviewReason | None, no_match_category: NoMatchCategory | None,
) -> str:
    """Build a plain-English steward summary free of internal verifier/rule jargon."""
    col = _JARGON_RE.sub("business field", col_name)
    if status in ("MATCHED", "AMBIGUOUS") and choices:
        top = choices[0]
        lbl = _JARGON_RE.sub("business term", top.label)
        pct = f"{top.confidence.point:.0%} confidence"
        if status == "MATCHED":
            if review_reason == "close_match_nuance":
                return f"Column '{col}' closely matches '{lbl}' ({pct}); confirm exact scope."
            return f"Column '{col}' matched '{lbl}' ({pct})."
        why = (review_reason or "wide_confidence_interval").replace("_", " ")
        return f"Column '{col}' needs review ({why}); leading option is '{lbl}' ({pct})."
    cat = (no_match_category or "out_of_vocabulary_domain").replace("_", " ")
    return f"Column '{col}' did not match a standard term ({cat})."


def compute_phi_lex(lex_score: float) -> float:
    return _clamp(2.0 * lex_score - 1.0)


def compute_phi_anchor(base_anchor: float, inverse_direction_score: float) -> float:
    return _clamp(base_anchor + inverse_direction_score)


def compute_phi_agent(
    candidate_idx: int, assignment: TableRerankAssignment, *, w_agent: float = 1.0
) -> tuple[float, float]:
    conf = _clamp(assignment.confidence, 0.0, 1.0)
    if assignment.abstain:
        return (-0.5 * conf, w_agent * (0.5 + conf))
    return (conf if candidate_idx == assignment.selected_index else -conf, 0.0)


def compute_observed_channels(
    ctx: ColumnCalibrationContext, candidates: Sequence[CandidateChannelInputs]
) -> frozenset[str]:
    obs = {"lex"}
    if ctx.n_eff > 0 and (
        ctx.has_column_surface_pattern or any(c.has_surface_rule for c in candidates)
    ):
        obs.add("surface")
    if ctx.has_shape_or_declared_type:
        obs.add("shape_range")
    if ctx.anchor_enabled and ctx.has_top_table_anchor:
        obs.add("anchor")
    if ctx.rerank_assignment is not None:
        obs.add("agent")
    return frozenset(obs)


def compute_active_channels(
    ctx: ColumnCalibrationContext, cand: CandidateChannelInputs
) -> frozenset[str]:
    active = {"lex", "shape_range"}
    if cand.has_surface_rule:
        active.add("surface")
    if ctx.anchor_enabled:
        active.add("anchor")
    if ctx.rerank_expected and not ctx.is_fast_pathed:
        active.add("agent")
    return frozenset(active)


def compute_sigma_disagree(
    phi: Mapping[str, float], m_obs: AbstractSet[str], m_active: AbstractSet[str]
) -> float:
    vals = [
        phi[m] for m in _CHANNELS
        if m in m_obs and m in m_active and not (m == "anchor" and phi[m] == 0.0)
    ]
    return 0.0 if len(vals) < 2 else statistics.pstdev(vals)


def compute_sigma_eff(
    params: CalibrationParams, n_eff: int, rho_mod: float, sigma_disagree: float
) -> float:
    n_cap = min(params.max_effective_sample_count, max(0, n_eff))
    return (
        params.sigma_base + params.alpha_n / math.sqrt(n_cap + 1.0)
        + params.alpha_mod * (1.0 - rho_mod) + params.alpha_disagree * sigma_disagree
    )


def compute_logit_normal_ci(
    prob: float, sigma_eff: float, params: CalibrationParams, *, ci_level: float = 0.90
) -> ConfidenceInterval:
    p_safe = min(1.0, max(0.0, prob))
    p_c = min(1.0 - _EPS, max(_EPS, p_safe))
    lp = math.log(p_c / (1.0 - p_c))
    z = statistics.NormalDist().inv_cdf((1.0 + ci_level) / 2.0)
    hw = params.c_cal * z * max(0.0, sigma_eff)
    return ConfidenceInterval(
        point=p_safe, lower=min(p_safe, _sigmoid(lp - hw)), upper=max(p_safe, _sigmoid(lp + hw)),
        level=ci_level, calibration_mode=params.calibration_mode,
    )


def softmax_with_null(
    ctx: ColumnCalibrationContext, candidates: Sequence[CandidateChannelInputs],
    params: CalibrationParams, *, ci_level: float = 0.90,
) -> tuple[list[ScoredCandidate], float, bool]:
    """Run log-sum-exp softmax with null hypothesis, returning (scored, p_null, any_hard_veto)."""
    active = [c for c in candidates if not c.hard_veto]
    any_veto = len(active) < len(candidates)
    if not active:
        return ([], 1.0, any_veto)

    m_obs = compute_observed_channels(ctx, active)
    w, asgn = params.channel_weights, ctx.rerank_assignment
    w_ag = w.get("agent", 2.0)
    s_abs = (
        compute_phi_agent(0, asgn, w_agent=w_ag)[1]
        if "agent" in m_obs and asgn is not None else 0.0
    )
    s_null = params.s_null_base + params.gamma_null * sum(w.get(m, 0.0) for m in m_obs) + s_abs

    staged: list[tuple[CandidateChannelInputs, float, dict[str, float], frozenset[str]]] = []
    for idx, c in enumerate(active):
        m_act = compute_active_channels(ctx, c)
        raw = {
            "lex": compute_phi_lex(c.lex_score),
            "surface": _clamp(c.surface_score) if c.has_surface_rule else 0.0,
            "shape_range": _clamp(c.shape_range_score),
            "anchor": compute_phi_anchor(c.base_anchor_score, c.inverse_direction_score),
        }
        if "agent" in m_obs and asgn is not None:
            r_idx = idx if c.rerank_index is None else c.rerank_index
            raw["agent"], _ = compute_phi_agent(r_idx, asgn, w_agent=w_ag)
        phi = {m: raw[m] for m in _CHANNELS if m in m_obs and m in raw}
        staged.append((c, params.beta_0 + sum(w.get(m, 0.0) * phi[m] for m in phi), phi, m_act))

    temp = max(_EPS, params.T)
    s_n, s_cs = s_null / temp, [row[1] / temp for row in staged]
    m_max = max([s_n, *s_cs])
    e_n, e_cs = math.exp(s_n - m_max), [math.exp(s - m_max) for s in s_cs]
    denom = e_n + sum(e_cs)
    p_null = min(1.0, max(0.0, e_n / denom))

    scored: list[ScoredCandidate] = []
    for (c, _, phi, m_act), e_val in zip(staged, e_cs, strict=True):
        prob = min(1.0, max(0.0, e_val / denom))
        rho = len(m_act & m_obs) / max(1, len(m_act))
        s_dis = compute_sigma_disagree(phi, m_obs, m_act)
        s_eff = compute_sigma_eff(params, ctx.n_eff, rho, s_dis)
        ci = compute_logit_normal_ci(prob, s_eff, params, ci_level=ci_level)
        dets = {"lex": f"lexical_score={c.lex_score:.3f}", "surface": c.surface_detail,
                "shape_range": c.shape_detail, "anchor": c.anchor_detail}
        contribs = [
            EvidenceContribution(
                channel=ch, score=phi[ch], weight=w.get(ch, 0.0),
                detail=dets.get(ch) or f"{ch.split('_')[0]}_phi={phi[ch]:.2f}",
            )
            for ch in phi
        ]
        scored.append(ScoredCandidate(
            c.entry_id, ci.point, ci.lower, ci.upper, s_eff, rho, s_dis, phi, m_obs, m_act,
            contribs, c,
        ))
    scored.sort(key=lambda item: (-item.probability, item.entry_id))
    return (scored, p_null, any_veto)


def check_fast_path_eligible(
    ctx: ColumnCalibrationContext, scored: Sequence[ScoredCandidate],
    params: CalibrationParams, *, any_hard_veto: bool,
) -> bool:
    if any_hard_veto or not scored or ctx.n_eff <= 0:
        return False
    top = scored[0]
    if (
        top.probability < params.fast_path_min_prob
        or (top.upper - top.lower) > params.fast_path_max_ci_width
        or top.sigma_eff > params.fast_path_max_eff_sigma
        or top.sigma_disagree > params.fast_path_max_disagree
    ):
        return False
    return not (
        ctx.has_checksumless_pattern and not ctx.has_checksum_pattern
        and not top.source_input.corroborated
    )


def _to_mapping(
    rank: int, sc: ScoredCandidate, status: SelectionStatus,
    ctx: ColumnCalibrationContext, params: CalibrationParams, ci_level: float,
) -> CandidateMapping:
    c = sc.source_input
    ci = ConfidenceInterval(point=sc.probability, lower=sc.lower, upper=sc.upper,
                            level=ci_level, calibration_mode=params.calibration_mode)
    pred = determine_suggested_predicate(ctx.column_name, c, sc.probability, status)
    return CandidateMapping(
        rank=rank, entry_id=sc.entry_id, label=c.label, entry_kind=c.entry_kind,
        origin_kind=c.origin_kind, confidence=ci, suggested_predicate=pred,
        semantic_context=c.semantic_context, replaces_uris=list(c.replaces_uris),
        evidence_contributions=list(sc.contributions),
    )


def select_and_calibrate(
    ctx: ColumnCalibrationContext, candidates: Sequence[CandidateChannelInputs],
    params: CalibrationParams, policy: GroundingPolicy,
) -> CalibrationOutcome:
    """Compute calibrated probabilities, CIs, selection status, and steward guidance."""
    scored, p_null, any_veto = softmax_with_null(ctx, candidates, params, ci_level=policy.ci_level)
    fast_ok = ctx.prescreen_category is None and check_fast_path_eligible(
        ctx, scored, params, any_hard_veto=any_veto
    )
    reason: ReviewReason | None = None
    choices: list[CandidateMapping] = []
    cat: NoMatchCategory | None = None
    detail: str | None = None
    rejected: CandidateMapping | None = None

    if (
        ctx.prescreen_category is not None or not scored
        or p_null >= policy.no_match_threshold
        or scored[0].probability < params.min_top1_probability
    ):
        status: SelectionStatus = "NO_MATCH"
        fast_ok = False
        cat, detail = infer_no_match_category(ctx, scored, params)
        if scored:
            rejected = _to_mapping(1, scored[0], "NO_MATCH", ctx, params, policy.ci_level)
        rule = f"prescreen_{cat}" if ctx.prescreen_category else "logit_normal_no_match"
    else:
        total_mass = max(_EPS, 1.0 - p_null)
        selected: list[ScoredCandidate] = []
        cum = 0.0
        for sc in scored[: max(0, policy.max_choices)]:
            selected.append(sc)
            cum += sc.probability / total_mass
            if cum >= policy.cumulative_mass_target:
                break
        pruned = [
            sc for i, sc in enumerate(selected)
            if i == 0 or sc.probability >= params.min_tail_probability
        ]
        top = pruned[0]
        matched = (
            len(pruned) == 1 and p_null < params.ambiguous_null_threshold
            and top.probability >= params.matched_min_top1_probability
            and top.lower >= params.matched_min_lower_bound and ctx.n_eff > 0
        )
        status = "MATCHED" if matched else "AMBIGUOUS"
        choices = [
            _to_mapping(r, sc, status, ctx, params, policy.ci_level)
            for r, sc in enumerate(pruned, 1)
        ]
        if status == "AMBIGUOUS":
            reason = (
                "unverified_masked_values" if ctx.n_eff == 0 and ctx.masked_count > 0
                else "elevated_null_probability" if p_null >= params.ambiguous_null_threshold
                else "competing_candidates" if len(pruned) > 1 else "wide_confidence_interval"
            )
        elif choices[0].suggested_predicate == "skos:closeMatch":
            reason = "close_match_nuance"
        rule = "logit_normal_matched" if status == "MATCHED" else "logit_normal_ambiguous"

    summary = build_steward_summary(ctx.column_name, status, choices, reason, cat)
    return CalibrationOutcome(
        status, reason, choices, p_null, cat, detail, rejected, fast_ok, rule, summary,
        scored, any_veto,
    )


__all__ = [
    "CalibrationOutcome", "CandidateChannelInputs", "ColumnCalibrationContext", "ScoredCandidate",
    "build_steward_summary", "check_fast_path_eligible", "compute_active_channels",
    "compute_logit_normal_ci", "compute_observed_channels", "compute_phi_agent",
    "compute_phi_anchor", "compute_phi_lex", "compute_sigma_disagree", "compute_sigma_eff",
    "determine_suggested_predicate", "infer_no_match_category", "normalize_column_tokens",
    "normalize_property_label", "select_and_calibrate", "softmax_with_null",
]
