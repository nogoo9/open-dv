"""Pure-stdlib 5-fold OOF calibration and held-out verifier for FIBO grounding."""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from opendv.grounding.kernel.calibration import (
    CandidateChannelInputs,
    ColumnCalibrationContext,
    compute_active_channels,
    compute_logit_normal_ci,
    compute_observed_channels,
    compute_phi_anchor,
    compute_phi_lex,
    select_and_calibrate,
)
from opendv.grounding.providers.fibo import FiboKnowledgeSource
from opendv.grounding.structured import ColumnGroundingEngine
from opendv.grounding.structured import steward_context as sc
from opendv.models.grounding import (
    ColumnEvidence,
    GroundingPolicy,
    GroundingRequest,
    InferredValueProfile,
    TableEvidence,
)
from opendv.models.grounding_spi import CalibrationParams
from opendv.sources.starter_packs import STARTER_PACK_IDS, get_starter_pack_ontology

SPLIT_SEED = 20261126
DEFAULT_BENCHMARK = Path("tests/grounding/benchmarks/fibo_stratified_benchmark_v1.json")
DEFAULT_OUT = Path("src/opendv/grounding/providers/fibo/fibo_calibration.json")
EPS = 1e-6
_SEQ_RE = re.compile(r"(?i)(?:^line_no$|^seq_no$|^item_idx$|_seq$|_line_no$)")

BOUNDS: dict[str, tuple[float, float]] = {
    "T": (0.5, 2.5),
    "s_null_base": (-2.0, 2.0),
    "gamma_null": (0.0, 0.5),
    "c_cal": (0.5, 3.0),
}
DEFAULTS: dict[str, float] = {
    "T": 1.0,
    "s_null_base": 0.0,
    "gamma_null": 0.15,
    "c_cal": 1.0,
}


@dataclass(frozen=True, slots=True)
class SiblingOwnerSpec:
    """Metadata needed for bit-for-bit parity on engine.py:316-329 owner check."""

    col: ColumnEvidence
    profile: InferredValueProfile
    base_ctx: ColumnCalibrationContext
    candidates: list[CandidateChannelInputs]
    turi: str | None
    tancs: frozenset[str]
    tplay: frozenset[str]


@dataclass(frozen=True, slots=True)
class FastObjRow:
    """Precomputed constant log-odds terms for exact (T, s_null_base, gamma_null) search."""

    fold_id: int
    is_no_match: bool
    is_prescreened: bool
    w_sum_obs: float
    z_list: tuple[float, ...]
    gold_idx: int | None


@dataclass(frozen=True, slots=True)
class CachedView:
    """Pre-extracted stage-1 grounding inputs for one benchmark view."""

    sample_id: str
    table_group: str
    stratum: str
    fold_id: int
    gold_uri: str | None
    gold_no_match_category: str | None
    base_ctx: ColumnCalibrationContext
    candidates: list[CandidateChannelInputs]
    target_surrogate_kind: str
    target_is_fk: bool
    siblings_for_owner: tuple[SiblingOwnerSpec, ...]


def _clamp_param(name: str, val: float) -> float:
    lo, hi = BOUNDS[name]
    return max(lo, min(hi, val))


def _resolve_repo_path(path: Path) -> Path:
    if path.is_absolute() or path.exists():
        return path
    repo_root = Path(__file__).resolve().parent.parent
    cand = repo_root / path
    return cand if cand.exists() or not path.parent.exists() else path


def load_benchmark(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"Benchmark at {path} must be a non-empty JSON list")
    return [dict(item) for item in raw]


def _pick_heldout_groups(
    cands: list[str],
    by_group: dict[str, list[dict[str, Any]]],
    tgt: int,
) -> list[str]:
    if len(cands) > 16:
        best_sub: list[str] = []
        acc = 0
        for g in cands:
            if acc + len(by_group[g]) <= tgt + 1:
                best_sub.append(g)
                acc += len(by_group[g])
        return best_sub

    best_diff = 10**9
    best_sub = []

    def _search(idx: int, cur_sum: int, cur: list[str]) -> None:
        nonlocal best_diff, best_sub
        diff = abs(cur_sum - tgt)
        if diff < best_diff:
            best_diff = diff
            best_sub = list(cur)
            if diff == 0:
                return
        if idx >= len(cands) or cur_sum >= tgt + best_diff:
            return
        g = cands[idx]
        cur.append(g)
        _search(idx + 1, cur_sum + len(by_group[g]), cur)
        cur.pop()
        if best_diff > 0:
            _search(idx + 1, cur_sum, cur)

    _search(0, 0, [])
    return best_sub


def split_dev_heldout_and_folds(
    records: list[dict[str, Any]],
    *,
    seed: int = SPLIT_SEED,
    n_folds: int = 5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Deterministic table_group-level stratified split into dev (~130) + held-out + 5 folds."""
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        by_group[str(rec["table_group"])].append(rec)

    groups_by_stratum: dict[str, list[str]] = defaultdict(list)
    for tg in sorted(by_group):
        strata = Counter(str(r["stratum"]) for r in by_group[tg])
        primary = sorted(strata.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        groups_by_stratum[primary].append(tg)

    total_n = len(records)
    target_heldout = (
        min(60, max(1, round(60 * total_n / 190)))
        if total_n >= 100
        else max(1, round(0.3 * total_n))
    )

    rng = random.Random(seed)
    strata_sorted = sorted(groups_by_stratum)
    raw_targets = {
        s: (target_heldout * sum(len(by_group[g]) for g in groups_by_stratum[s])) / total_n
        for s in strata_sorted
    }
    stratum_targets = {s: math.floor(raw_targets[s]) for s in strata_sorted}
    rem = target_heldout - sum(stratum_targets.values())
    for s in sorted(strata_sorted, key=lambda k: -(raw_targets[k] - stratum_targets[k])):
        if rem <= 0:
            break
        stratum_targets[s] += 1
        rem -= 1

    heldout_groups: set[str] = set()
    for s in strata_sorted:
        cands = list(groups_by_stratum[s])
        rng.shuffle(cands)
        heldout_groups.update(_pick_heldout_groups(cands, by_group, stratum_targets[s]))

    dev_records = [r for r in records if str(r["table_group"]) not in heldout_groups]
    heldout_records = [r for r in records if str(r["table_group"]) in heldout_groups]

    # 5-fold GroupKFold on dev_records by table_group
    dev_groups = sorted({str(r["table_group"]) for r in dev_records})
    fold_rng = random.Random(seed + 1)
    fold_rng.shuffle(dev_groups)
    dev_groups.sort(key=lambda g: -len(by_group[g]))
    fold_counts = [0] * n_folds
    group_to_fold: dict[str, int] = {}
    for tg in dev_groups:
        f_idx = min(range(n_folds), key=lambda f: (fold_counts[f], f))
        group_to_fold[tg] = f_idx
        fold_counts[f_idx] += len(by_group[tg])

    return dev_records, heldout_records, group_to_fold


def _is_owner_bk_with_params(
    spec: SiblingOwnerSpec,
    params: CalibrationParams,
    policy: GroundingPolicy,
    source: FiboKnowledgeSource,
) -> bool:
    c, p = spec.col, spec.profile
    sh = p.shape
    if (
        spec.base_ctx.prescreen_category
        or sh.surrogate_kind in ("monotonic_int", "random_uuid_v4")
        or p.pii_tags
        or ColumnGroundingEngine._is_fk(c)
        or bool(_SEQ_RE.search(c.column_name.strip()))
    ):
        return False
    if c.is_pk:
        return True
    if sh.distinct_ratio < 0.90:
        return False
    out = select_and_calibrate(spec.base_ctx, spec.candidates, params, policy)
    if out.choices and out.choices[0].semantic_context.archetype == "identifier":
        u = out.choices[0].semantic_context.identified_entity_uri
        if u is None or spec.turi is None:
            return True
        if u == spec.turi or u in spec.tancs or u in spec.tplay:
            return True
        ua = set(source.get_ancestors(u, max_depth=8))
        if spec.turi in ua or bool(ua & spec.tplay):
            return True
    return sh.surrogate_kind == "prefixed_operational_id" or any(
        sh.structural_patterns.get(k, 0.0) >= 0.90 for k in sc.NON_PII_BK_PATTERNS
    )


def _extract_view(
    rec: dict[str, Any],
    fold_id: int,
    engine: ColumnGroundingEngine,
    source: FiboKnowledgeSource,
    *,
    drop_shape: bool = False,
    drop_anchors: bool = False,
) -> CachedView:
    tbl = TableEvidence.model_validate(rec["table"])
    if drop_shape:
        cols = [c.model_copy(update={"precomputed_shape": None}) for c in tbl.columns]
        tbl = tbl.model_copy(update={"columns": cols})
    pol = GroundingPolicy(
        mode="deterministic_only",
        skip_reason="calibration_stage1_extract",
        enable_table_context=not drop_anchors,
    )
    req = GroundingRequest(tables=[tbl], policy=pol)

    # Extract pre-surrogate stage-1 state directly via ground_symbolic components
    kinds = source.metadata.anchor_entry_kinds
    anc_on = bool(kinds) and pol.enable_table_context
    sctx = sc.extract_steward_table_context(tbl, req, source)
    anchors = engine._recall_anchors(tbl, sctx, kinds) if anc_on else []
    top = anchors[0] if anchors else None
    turi = top.entry_id if top else None
    tancs = frozenset(source.get_ancestors(turi, max_depth=8)) if turi else frozenset()
    tplay = frozenset(engine._played(turi))

    abn = {tbl.table_name.strip().lower(): turi} if turi else {}
    profs: dict[str, InferredValueProfile] = {}
    unm: dict[str, list[str]] = {}
    pre: dict[str, tuple[str | None, str | None]] = {}
    fks: dict[str, str] = {}
    for col in tbl.columns:
        cn = col.column_name
        profs[cn], unm[cn] = sc.profile_column_evidence(col, tbl)
        pre[cn] = sc.check_etl_prescreen(col, tbl.table_name)
        if col.fk_ref and (
            tgt := col.fk_ref.split("(", 1)[0].split(".", 1)[0].strip().lower()
        ) in abn:
            fks[cn] = abn[tgt]

    from opendv.models.grounding_spi import TableReasoningContext

    tctx = TableReasoningContext(
        table_name=tbl.table_name,
        table_id=tbl.table_id,
        anchor_hypotheses=anchors,
        sibling_summaries=[f"{c.column_name}:{c.data_type}" for c in tbl.columns[:50]],
        fk_targets=fks,
        steward_notes=list(sctx.cleaned_table_notes),
        paired_columns=sc.detect_paired_columns(
            tbl, profs, {}, {k: v[0] for k, v in pre.items()}
        ),
    )

    col_specs: list[SiblingOwnerSpec] = []
    for col in tbl.columns:
        cn = col.column_name
        prof, (pc, pd) = profs[cn], pre[cn]
        isb = "BOOLEAN" in (prof.declared_logical_type, prof.inferred_logical_type)
        bq, etoks, _ = sc.build_column_query(col, sctx, source)
        sh = prof.shape
        cands: list[CandidateChannelInputs] = []
        has_surf = chk = nochk = False
        if pc is None:
            th = {h.entry_id: h for h in source.recall_by_text(bq, top_k=25)}
            ct = sc.split_ident_tokens(cn)
            if etoks and set(etoks) != set(ct):
                engine._merge(
                    th,
                    source.recall_by_text(" ".join(dict.fromkeys([*ct, *etoks])), top_k=15),
                )
            sf = (
                []
                if (prof.pii_tags or sh.pii_suppressed)
                else (sh.observed_enum_values or unm[cn][:50])
            )
            pats = sh.structural_patterns
            shits = source.match_values_or_mentions(
                sf,
                pii_tags=prof.pii_tags,
                context_hint=sctx.context_text,
                structural_patterns=pats,
            )
            mm = dict(th)
            engine._merge(mm, shits)
            merged = sorted(mm.values(), key=lambda h: (-h.raw_score, h.entry_id))[:25]
            evl = source.evaluate_structured_candidates(col, prof, merged, table_context=tctx)
            chk = any(
                pats.get(k, 0.0) >= 0.50
                for k in (*sc.NON_PII_BK_PATTERNS[:5], "iban", "aba_routing")
            )
            nochk = any(
                pats.get(k, 0.0) >= 0.50
                for k in ("swift_bic", "iso_4217_ccy", "iso_3166_country")
            )
            has_surf = chk or nochk or bool(shits)
            cands = sc.build_candidate_channel_inputs(
                source,
                col,
                prof,
                merged,
                {k: float(v.raw_score) for k, v in th.items()},
                {h.entry_id: float(h.raw_score) for h in shits},
                {r.entry_id: r for r in evl},
                bq,
                engine._aligns,
                anc_on=anc_on,
                top_uri=turi,
                top_ancs=set(tancs),
                top_played=set(tplay),
                has_chk=chk,
                has_nochk=nochk,
                is_bool=isb,
            )
        bc = ColumnCalibrationContext(
            cn,
            sh.profiled_non_null_count,
            prof.masked_count,
            True,
            has_surf,
            chk,
            nochk,
            anc_on,
            top is not None,
            None,
            False,
            False,
            pc,  # type: ignore[arg-type]
            pd,
        )
        col_specs.append(SiblingOwnerSpec(col, prof, bc, cands, turi, tancs, tplay))

    target = col_specs[0]
    surr_kind = target.profile.shape.surrogate_kind
    sibs = (
        tuple(s for s in col_specs[1:] if s.col.column_name != target.col.column_name)
        if surr_kind in ("monotonic_int", "random_uuid_v4")
        and target.base_ctx.prescreen_category is None
        and not ColumnGroundingEngine._is_fk(target.col)
        and not _SEQ_RE.search(target.col.column_name.strip())
        else ()
    )
    return CachedView(
        sample_id=str(rec["sample_id"]),
        table_group=str(rec["table_group"]),
        stratum=str(rec["stratum"]),
        fold_id=fold_id,
        gold_uri=rec["gold_uri"],
        gold_no_match_category=rec["gold_no_match_category"],
        base_ctx=target.base_ctx,
        candidates=target.candidates,
        target_surrogate_kind=surr_kind,
        target_is_fk=ColumnGroundingEngine._is_fk(target.col),
        siblings_for_owner=sibs,
    )


def _eval_view(
    view: CachedView,
    params: CalibrationParams,
    policy: GroundingPolicy,
    source: FiboKnowledgeSource,
) -> tuple[Any, float]:
    ctx = view.base_ctx
    if view.siblings_for_owner:
        has_owner = any(
            _is_owner_bk_with_params(sib, params, policy, source)
            for sib in view.siblings_for_owner
        )
        if has_owner:
            ctx = replace(
                ctx,
                prescreen_category="technical_surrogate",
                prescreen_detail="Surrogate with natural key.",
            )
    out = select_and_calibrate(ctx, view.candidates, params, policy)
    return out, (1.0 if ctx.prescreen_category is not None else out.no_match_probability)


def compute_abstain_ece_10bin(is_no_match: list[float], p_nulls: list[float]) -> float:
    n = len(is_no_match)
    if n == 0:
        return 0.0
    bins: list[list[tuple[float, float]]] = [[] for _ in range(10)]
    for y, p in zip(is_no_match, p_nulls, strict=True):
        idx = min(9, max(0, int(p * 10.0)))
        bins[idx].append((y, p))
    ece = 0.0
    for b in bins:
        if not b:
            continue
        acc = sum(y for y, _ in b) / len(b)
        conf = sum(p for _, p in b) / len(b)
        ece += (len(b) / n) * abs(acc - conf)
    return ece


def compute_ci_coverage(
    items: list[tuple[float, float, float, float]],
    *,
    window_size: int = 12,
) -> float:
    """Evaluate empirical 90% CI coverage over sliding probability-sorted windows."""
    if not items:
        return 0.0
    ordered = sorted(items, key=lambda x: x[0])
    n = len(ordered)
    w = min(n, max(6, window_size))
    half = w // 2
    covered = 0
    for i, (_, lo, hi, _) in enumerate(ordered):
        start = max(0, min(n - w, i - half))
        end = start + w
        win = ordered[start:end]
        # Laplace-smoothed empirical accuracy in window avoids 1.0 boundary singularity
        emp = (sum(x[3] for x in win) + 0.5) / (len(win) + 1.0)
        if lo <= emp <= hi:
            covered += 1
    return covered / n


def _build_fast_rows(
    views: list[CachedView],
    source: FiboKnowledgeSource,
    base_params: CalibrationParams,
) -> list[FastObjRow]:
    pol = GroundingPolicy(mode="deterministic_only", skip_reason="fast_obj")
    w = base_params.channel_weights
    rows: list[FastObjRow] = []
    for v in views:
        is_pre = v.base_ctx.prescreen_category is not None
        if not is_pre and v.siblings_for_owner:
            is_pre = any(
                _is_owner_bk_with_params(sib, base_params, pol, source)
                for sib in v.siblings_for_owner
            )
        active = [c for c in v.candidates if not c.hard_veto]
        if not active:
            rows.append(
                FastObjRow(v.fold_id, v.gold_uri is None, is_pre, 0.0, (), None)
            )
            continue
        m_obs = compute_observed_channels(v.base_ctx, active)
        w_sum_obs = sum(w.get(m, 0.0) for m in m_obs)
        zs: list[float] = []
        gold_idx: int | None = None
        for idx, c in enumerate(active):
            _ = compute_active_channels(v.base_ctx, c)
            raw = {
                "lex": compute_phi_lex(c.lex_score),
                "surface": max(-1.0, min(1.0, c.surface_score)) if c.has_surface_rule else 0.0,
                "shape_range": max(-1.0, min(1.0, c.shape_range_score)),
                "anchor": compute_phi_anchor(c.base_anchor_score, c.inverse_direction_score),
            }
            z_i = base_params.beta_0 + sum(w.get(m, 0.0) * raw[m] for m in m_obs if m in raw)
            zs.append(z_i)
            if v.gold_uri is not None and c.entry_id == v.gold_uri:
                gold_idx = idx
        rows.append(
            FastObjRow(
                v.fold_id,
                v.gold_uri is None,
                is_pre,
                w_sum_obs,
                tuple(zs),
                gold_idx,
            )
        )
    return rows


def evaluate_fast_objective(
    rows: list[FastObjRow],
    temp_val: float,
    s_null_base: float,
    gamma_null: float,
) -> tuple[float, float, float, float]:
    nll_sum = 0.0
    is_nm: list[float] = []
    p_nulls: list[float] = []
    temp = max(EPS, temp_val)
    for r in rows:
        if not r.z_list:
            p_null = 1.0
            p_gold = 1.0 if r.is_no_match else EPS
        else:
            s_n = (s_null_base + gamma_null * r.w_sum_obs) / temp
            s_cs = [z / temp for z in r.z_list]
            m_max = max(s_n, *s_cs)
            e_n = math.exp(s_n - m_max)
            e_cs = [math.exp(s - m_max) for s in s_cs]
            denom = e_n + sum(e_cs)
            p_null = min(1.0, max(0.0, e_n / denom))
            if r.is_no_match:
                p_gold = max(EPS, 1.0 if r.is_prescreened else p_null)
            elif r.gold_idx is None:
                p_gold = EPS
            else:
                p_gold = max(EPS, min(1.0, e_cs[r.gold_idx] / denom))
        p_null_eff = 1.0 if r.is_prescreened else p_null
        is_nm.append(1.0 if r.is_no_match else 0.0)
        p_nulls.append(min(1.0, max(0.0, p_null_eff)))
        nll_sum -= math.log(p_gold)

    n = max(1, len(rows))
    nll = nll_sum / n
    ece = compute_abstain_ece_10bin(is_nm, p_nulls)
    l2 = (
        (temp_val - DEFAULTS["T"]) ** 2
        + (s_null_base - DEFAULTS["s_null_base"]) ** 2
        + (gamma_null - DEFAULTS["gamma_null"]) ** 2
    )
    loss = nll + 0.5 * ece + 1e-3 * l2
    return loss, nll, ece, l2


def fit_three_scalars(
    train_rows: list[FastObjRow],
) -> tuple[float, float, float]:
    """Coarse grid search + coordinate descent over (T, s_null_base, gamma_null)."""
    t_grid = [0.55, 0.70, 0.85, 1.0, 1.25, 1.6, 2.1]
    s_grid = [-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5]
    g_grid = [0.0, 0.1, 0.2, 0.35, 0.48]

    scored_grid: list[tuple[float, float, float, float]] = []
    for t_val in t_grid:
        for s_val in s_grid:
            for g_val in g_grid:
                loss, _, _, _ = evaluate_fast_objective(train_rows, t_val, s_val, g_val)
                scored_grid.append((loss, t_val, s_val, g_val))
    scored_grid.sort(key=lambda x: x[0])

    best_loss = scored_grid[0][0]
    best = (scored_grid[0][1], scored_grid[0][2], scored_grid[0][3])

    for _, t0, s0, g0 in scored_grid[:3]:
        cur_t, cur_s, cur_g = t0, s0, g0
        cur_loss, _, _, _ = evaluate_fast_objective(train_rows, cur_t, cur_s, cur_g)
        for step_scale in (0.20, 0.10, 0.05, 0.02, 0.01):
            improved = True
            while improved:
                improved = False
                for dim, delta in (
                    ("T", step_scale),
                    ("T", -step_scale),
                    ("s_null_base", step_scale * 2.0),
                    ("s_null_base", -step_scale * 2.0),
                    ("gamma_null", step_scale * 0.5),
                    ("gamma_null", -step_scale * 0.5),
                ):
                    nt = _clamp_param("T", cur_t + (delta if dim == "T" else 0.0))
                    ns = _clamp_param(
                        "s_null_base", cur_s + (delta if dim == "s_null_base" else 0.0)
                    )
                    ng = _clamp_param(
                        "gamma_null", cur_g + (delta if dim == "gamma_null" else 0.0)
                    )
                    cand_loss, _, _, _ = evaluate_fast_objective(train_rows, nt, ns, ng)
                    if cand_loss + 1e-7 < cur_loss:
                        cur_t, cur_s, cur_g = nt, ns, ng
                        cur_loss = cand_loss
                        improved = True
        if cur_loss < best_loss:
            best_loss = cur_loss
            best = (cur_t, cur_s, cur_g)
    return best


def tune_c_cal(
    oof_scored_rows: list[tuple[float, float, float]],
    base_params: CalibrationParams,
    *,
    target_coverage: float = 0.90,
) -> tuple[float, float]:
    """Tune c_cal in [0.5, 3.0] for ~90% OOF empirical CI coverage."""
    best_c = 1.0
    best_cov = 0.0
    best_score = 10**9
    for step in range(51):
        c_val = 0.5 + step * (2.5 / 50.0)
        p_test = base_params.model_copy(update={"c_cal": c_val})
        items: list[tuple[float, float, float, float]] = []
        for prob, s_eff, y in oof_scored_rows:
            ci = compute_logit_normal_ci(prob, s_eff, p_test, ci_level=0.90)
            items.append((prob, ci.lower, ci.upper, y))
        cov = compute_ci_coverage(items)
        # Target 0.90 coverage with gentle regularization toward default c_cal=1.0
        score = abs(cov - target_coverage) + 0.01 * abs(c_val - DEFAULTS["c_cal"])
        if score < best_score:
            best_score = score
            best_c = c_val
            best_cov = cov
    return round(best_c, 4), best_cov


def compute_f1(tp: int, fp: int, fn: int) -> float:
    denom = 2 * tp + fp + fn
    return (2.0 * tp / denom) if denom > 0 else 0.0


def evaluate_full_uncached_heldout(
    heldout_records: list[dict[str, Any]],
    source: FiboKnowledgeSource,
    params: CalibrationParams,
) -> dict[str, Any]:
    """Run full uncached ColumnGroundingEngine.ground() on held-out records."""
    engine = ColumnGroundingEngine(source, calibration=params)
    pol = GroundingPolicy(mode="deterministic_only", skip_reason="m3_heldout_verify")

    pos_n = 0
    pos_top1 = 0
    pos_top3 = 0
    overall_correct = 0

    nm_tp = nm_fp = nm_fn = 0
    oov_tp = oov_fp = oov_fn = 0

    is_nm: list[float] = []
    p_nulls: list[float] = []
    ci_items: list[tuple[float, float, float, float]] = []

    stratum_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "ok": 0})

    for rec in heldout_records:
        tbl = TableEvidence.model_validate(rec["table"])
        req = GroundingRequest(tables=[tbl], policy=pol)
        draft = engine.ground_symbolic(req)
        resp = engine.finalize(req, draft, rerank_results=None)

        td = next(iter(draft.table_drafts.values()))
        cd = td.column_drafts[0]
        col_res = resp.tables[0].columns[0]
        scored = cd.symbolic_outcome.scored_candidates

        g_uri: str | None = rec["gold_uri"]
        g_cat: str | None = rec["gold_no_match_category"]
        strat = str(rec["stratum"])
        stratum_stats[strat]["n"] += 1

        p_null_eff = (
            1.0 if cd.base_ctx.prescreen_category is not None else col_res.no_match_probability
        )
        p_nulls.append(min(1.0, max(0.0, p_null_eff)))
        is_nm.append(1.0 if g_uri is None else 0.0)

        if g_uri is not None:
            pos_n += 1
            top1_uri = (
                col_res.choices[0].entry_id
                if col_res.status != "NO_MATCH" and col_res.choices
                else None
            )
            top3_uris = [c.entry_id for c in scored[:3]]
            if top1_uri == g_uri:
                pos_top1 += 1
                overall_correct += 1
                stratum_stats[strat]["ok"] += 1
            if g_uri in top3_uris:
                pos_top3 += 1
        else:
            if col_res.status == "NO_MATCH":
                overall_correct += 1
                stratum_stats[strat]["ok"] += 1

        # NO_MATCH F1 & Path B1 OOV F1
        pred_nm = col_res.status == "NO_MATCH"
        gold_nm = g_uri is None
        if pred_nm and gold_nm:
            nm_tp += 1
        elif pred_nm and not gold_nm:
            nm_fp += 1
        elif not pred_nm and gold_nm:
            nm_fn += 1

        pred_oov = (
            col_res.status == "NO_MATCH"
            and col_res.no_match_category == "out_of_vocabulary_domain"
        )
        gold_oov = g_uri is None and g_cat == "out_of_vocabulary_domain"
        if pred_oov and gold_oov:
            oov_tp += 1
        elif pred_oov and not gold_oov:
            oov_fp += 1
        elif not pred_oov and gold_oov:
            oov_fn += 1

        if scored:
            top_sc = scored[0]
            y_ci = 1.0 if (g_uri is not None and top_sc.entry_id == g_uri) else 0.0
            ci_items.append((top_sc.probability, top_sc.lower, top_sc.upper, y_ci))

    # Starter pack & cusip fast-path regression sanity check
    cusip_tbl = TableEvidence(
        table_name="securities",
        columns=[
            ColumnEvidence(
                column_name="cusip",
                data_type="VARCHAR",
                is_pk=True,
                sample_values=["037833100"] * 100,
            )
        ],
    )
    cusip_draft = engine.ground_symbolic(
        GroundingRequest(tables=[cusip_tbl], policy=GroundingPolicy(mode="agentic"))
    )
    cusip_fast_ok = "cusip" in cusip_draft.fast_path_mask.get("securities", set())

    starter_ok = True
    for pid in STARTER_PACK_IDS:
        ont = get_starter_pack_ontology(pid)
        for concept in ont.concepts:
            for mapping in concept.external_mappings:
                if float(source.score(concept.concept_name, mapping.standard_uri)) < 0.0:
                    starter_ok = False

    per_stratum: dict[str, dict[str, float]] = {}
    for s_name, st in sorted(stratum_stats.items()):
        n_s = max(1, st["n"])
        p_s = st["ok"] / n_s
        se_95 = 1.96 * math.sqrt(p_s * (1.0 - p_s) / n_s)
        per_stratum[s_name] = {
            "n": float(st["n"]),
            "acc": round(p_s, 4),
            "approx_95ci_halfwidth": round(se_95, 4),
        }

    return {
        "n_heldout": len(heldout_records),
        "n_positive": pos_n,
        "top1_acc": round(pos_top1 / max(1, pos_n), 4),
        "overall_acc": round(overall_correct / max(1, len(heldout_records)), 4),
        "top3_recall": round(pos_top3 / max(1, pos_n), 4),
        "no_match_f1": round(compute_f1(nm_tp, nm_fp, nm_fn), 4),
        "path_b1_f1": round(compute_f1(oov_tp, oov_fp, oov_fn), 4),
        "abstain_ECE": round(compute_abstain_ece_10bin(is_nm, p_nulls), 4),
        "ci90_coverage": round(compute_ci_coverage(ci_items), 4),
        "cusip_fast_path_ok": cusip_fast_ok,
        "starter_packs_ok": starter_ok,
        "per_stratum": per_stratum,
    }


def run_calibration(
    benchmark_path: Path,
    out_path: Path,
    *,
    verify_heldout: bool = False,
) -> tuple[dict[str, Any], bool]:
    records = load_benchmark(benchmark_path)
    dev_records, heldout_records, group_to_fold = split_dev_heldout_and_folds(
        records, seed=SPLIT_SEED, n_folds=5
    )

    # Load uncalibrated source for stage-1 extraction
    base_source = FiboKnowledgeSource(calibration_path=out_path.with_name("__none_cal__.json"))
    base_params = CalibrationParams.analytic_defaults()
    engine = ColumnGroundingEngine(base_source, calibration=base_params)

    drop_rng = random.Random(SPLIT_SEED + 2)
    n_dev = len(dev_records)
    drop_n = max(1, round(0.25 * n_dev))
    shape_drop_idxs = set(drop_rng.sample(range(n_dev), drop_n))
    anchor_drop_idxs = set(drop_rng.sample(range(n_dev), drop_n))

    dev_views: list[CachedView] = []
    for idx, rec in enumerate(dev_records):
        fid = group_to_fold[str(rec["table_group"])]
        dev_views.append(_extract_view(rec, fid, engine, base_source))
        if idx in shape_drop_idxs:
            dev_views.append(_extract_view(rec, fid, engine, base_source, drop_shape=True))
        if idx in anchor_drop_idxs:
            dev_views.append(_extract_view(rec, fid, engine, base_source, drop_anchors=True))

    # 5-fold GroupKFold OOF predictions
    fast_rows = _build_fast_rows(dev_views, base_source, base_params)
    pol = GroundingPolicy(mode="deterministic_only", skip_reason="calib_oof")
    oof_scored_rows: list[tuple[float, float, float]] = []
    for fid in range(5):
        train_r = [r for r in fast_rows if r.fold_id != fid]
        val_v = [v for v in dev_views if v.fold_id == fid]
        t_f, s_f, g_f = fit_three_scalars(train_r)
        p_f = base_params.model_copy(update={"T": t_f, "s_null_base": s_f, "gamma_null": g_f})
        for v in val_v:
            out, _ = _eval_view(v, p_f, pol, base_source)
            if out.scored_candidates:
                top_sc = out.scored_candidates[0]
                y = 1.0 if (v.gold_uri is not None and top_sc.entry_id == v.gold_uri) else 0.0
                oof_scored_rows.append((top_sc.probability, top_sc.sigma_eff, y))

    # Final 3-scalar fit on all dev views
    t_final, s_final, g_final = fit_three_scalars(fast_rows)
    c_final, oof_cov = tune_c_cal(oof_scored_rows, base_params)

    fitted_params = base_params.model_copy(
        update={
            "calibration_mode": "empirical_calibrated",
            "T": round(t_final, 4),
            "s_null_base": round(s_final, 4),
            "gamma_null": round(g_final, 4),
            "c_cal": round(c_final, 4),
        }
    )

    # Write artifact first so --verify-heldout loads via FiboKnowledgeSource(calibration_path=...)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "split_seed": SPLIT_SEED,
        "T": fitted_params.T,
        "s_null_base": fitted_params.s_null_base,
        "gamma_null": fitted_params.gamma_null,
        "c_cal": fitted_params.c_cal,
        "channel_weights": dict(fitted_params.channel_weights),
        "heldout_metrics": {},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    cal_source = FiboKnowledgeSource(calibration_path=out_path)
    loaded_cal = cal_source.calibration_params
    assert loaded_cal is not None
    heldout_metrics = evaluate_full_uncached_heldout(heldout_records, cal_source, loaded_cal)
    heldout_metrics["oof_ci90_coverage"] = round(oof_cov, 4)
    heldout_metrics["n_dev"] = n_dev
    heldout_metrics["n_dev_views"] = len(dev_views)

    payload["heldout_metrics"] = heldout_metrics
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    passed = (
        heldout_metrics["top1_acc"] >= 0.80
        and heldout_metrics["overall_acc"] >= 0.80
        and heldout_metrics["top3_recall"] >= 0.92
        and heldout_metrics["no_match_f1"] >= 0.80
        and heldout_metrics["path_b1_f1"] >= 0.80
        and heldout_metrics["abstain_ECE"] <= 0.08
        and 0.82 <= heldout_metrics["ci90_coverage"] <= 0.96
        and bool(heldout_metrics["cusip_fast_path_ok"])
        and bool(heldout_metrics["starter_packs_ok"])
    )
    if verify_heldout:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return payload, passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="5-fold OOF Logit-Normal calibration for FIBO grounding"
    )
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--verify-heldout", action="store_true")
    args = parser.parse_args(argv)

    bench = _resolve_repo_path(args.benchmark)
    out = _resolve_repo_path(args.out)
    _, passed = run_calibration(bench, out, verify_heldout=args.verify_heldout)
    if args.verify_heldout and not passed:
        print("HELD-OUT VERIFICATION FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
