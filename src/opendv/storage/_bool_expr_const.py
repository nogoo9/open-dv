"""Kleene 3VL constant-truth and DECODE-pruning helpers for `_bool_expr_guard.py`.

Strictly Layer 2a: imports only sqlglot, stdlib, `_ast_stmt_rules`,
`_bool_expr_containers`, `_bool_expr_lambdas`, `_bool_expr_ops`, and
`_bool_expr_vocab`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from sqlglot import exp

from opendv.storage._ast_stmt_rules import unwrap_parens
from opendv.storage._bool_expr_containers import (
    SmapType,
    extend_cte_nodes,
    merge_select_smap,
)
from opendv.storage._bool_expr_fold import fold_cast_val, fold_concat_ws, fold_scalar_func
from opendv.storage._bool_expr_lambdas import (
    SELECT_DISALLOWED_CLAUSES,
    is_single_row_const_rel,
    try_node_unsafe,
)
from opendv.storage._bool_expr_nulls import eval_const_arith
from opendv.storage._bool_expr_ops import resolve_col_with_containers
from opendv.storage._bool_expr_vocab import (
    UNKNOWN,
    int_float_lossy,
    kleene_and,
    kleene_cmp,
    kleene_or,
    like_match,
    parse_bool_lit,
    same_const_val,
    same_scalar_kind,
    scalar_to_str,
)

_TRY_CLS = getattr(exp, "Try", None)
_TO_BOOLEAN_CLS = getattr(exp, "ToBoolean", None)
_CMP_EXPRS: tuple[type[exp.Expr], ...] = tuple(
    c
    for c in (
        exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
        getattr(exp, "NullSafeEQ", None), getattr(exp, "NullSafeNEQ", None),
    )
    if c is not None
)


def _eval_select_lit(
    u: exp.Select,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    depth: int,
) -> object:
    nc = extend_cte_nodes(u, ctes)
    if len(u.expressions) != 1 or any(u.args.get(k) for k in SELECT_DISALLOWED_CLAUSES):
        return UNKNOWN
    fm = u.args.get("from_")
    if fm is not None and (
        not isinstance(fm, exp.From)
        or fm.this is None
        or not is_single_row_const_rel(fm.this, nc, vis)
        or any(not is_single_row_const_rel(e, nc, vis) for e in (fm.expressions or ()))
    ):
        return UNKNOWN
    p = u.expressions[0]
    pe = p.this if isinstance(p, exp.Alias) else p
    if isinstance(pe, (exp.Star, exp.Explode, exp.Unnest)) or (
        isinstance(pe, exp.Column) and isinstance(pe.this, exp.Star)
    ):
        return UNKNOWN
    _, sel_smap, _, _ = merge_select_smap(u, nc, smap)
    return eval_const_lit(pe, nc, vis, sel_smap, depth + 1)


def _eval_bool_3vl(
    node: exp.Expr | None,
    ctes: dict[str, exp.CTE] | None = None,
    vis: frozenset[str] = frozenset(),
    smap: SmapType | None = None,
    depth: int = 0,
) -> object:
    v = eval_const_lit(node, ctes, vis, smap, depth)
    return v if (v is True or v is False or v is None) else UNKNOWN


def _eval_case_lit(
    u: exp.Case,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    depth: int,
) -> object:
    ifs = [br for br in (u.args.get("ifs") or ()) if isinstance(br, exp.If)]
    dflt, subj = u.args.get("default"), u.args.get("this")
    if subj is None:
        for br in ifs:
            cb = _eval_bool_3vl(br.this, ctes, vis, smap, depth + 1)
            if cb is UNKNOWN:
                return UNKNOWN
            if cb is True:
                return eval_const_lit(br.args.get("true"), ctes, vis, smap, depth + 1)
    else:
        sv = eval_const_lit(subj, ctes, vis, smap, depth + 1)
        if sv is UNKNOWN:
            return UNKNOWN
        if sv is not None:
            for br in ifs:
                wv = eval_const_lit(br.this, ctes, vis, smap, depth + 1)
                if (
                    wv is UNKNOWN
                    or (
                        wv is not None
                        and (not same_scalar_kind(sv, wv) or int_float_lossy(sv, wv))
                    )
                ):
                    return UNKNOWN
                if wv is not None and sv == wv:
                    return eval_const_lit(br.args.get("true"), ctes, vis, smap, depth + 1)
    return eval_const_lit(dflt, ctes, vis, smap, depth + 1) if dflt is not None else None


def _eval_try_inner(
    inner: exp.Expr | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    depth: int,
) -> object:
    if inner is None:
        return UNKNOWN
    res = eval_const_lit(inner, ctes, vis, smap, depth + 1)
    if res is UNKNOWN or try_node_unsafe(
        inner,
        ctes,
        vis,
        smap,
        eval_const_lit,
        lambda c, sm, cc, vv: resolve_col_with_containers(
            c, sm, cc, vv, hooks=(eval_const_bool, eval_const_lit),
        ),
    ):
        return UNKNOWN
    return res


def eval_const_lit(
    node: exp.Expr | None,
    ctes: dict[str, exp.CTE] | None = None,
    vis: frozenset[str] = frozenset(),
    smap: SmapType | None = None,
    depth: int = 0,
) -> object:
    if depth > 14 or (u := unwrap_parens(node)) is None:
        return UNKNOWN
    ec = ctes if ctes is not None else {}

    def _ev(x: exp.Expr | None) -> object:
        return eval_const_lit(x, ec, vis, smap, depth + 1)

    def _eb(x: exp.Expr | None) -> object:
        return _eval_bool_3vl(x, ec, vis, smap, depth + 1)

    if isinstance(u, exp.Null):
        return None
    if isinstance(u, exp.Boolean):
        return bool(u.this)
    if isinstance(u, exp.Literal):
        if u.is_string:
            return str(u.this)
        try:
            if u.is_int:
                int_v = int(str(u.this))
                return int_v if abs(int_v) <= (1 << 63) - 1 else UNKNOWN
            if u.is_number:
                s_lit = str(u.this)
                lit_fv = float(s_lit)
                if not math.isfinite(lit_fv) or Decimal(str(lit_fv)) != Decimal(s_lit):
                    return UNKNOWN
                return lit_fv
            return UNKNOWN
        except (ValueError, InvalidOperation):
            return UNKNOWN
    if isinstance(u, exp.Column):
        if not smap or not (
            res := resolve_col_with_containers(
                u, smap, ec, vis, hooks=(eval_const_bool, eval_const_lit),
            )
        ):
            return UNKNOWN
        vals = [eval_const_lit(ex, c2, v2, s2, depth + 1) for ex, c2, v2, s2 in res]
        if any(v is UNKNOWN for v in vals):
            return UNKNOWN
        return vals[0] if all(same_const_val(v, vals[0]) for v in vals[1:]) else UNKNOWN
    if isinstance(u, exp.Neg):
        v = _ev(u.this)
        if v is None:
            return None
        return -v if isinstance(v, (int, float)) and not isinstance(v, bool) else UNKNOWN
    if isinstance(u, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)):
        return eval_const_arith(u, _ev(u.this), _ev(u.expression))
    if isinstance(u, exp.DPipe):
        lv, rv = _ev(u.this), _ev(u.expression)
        if lv is UNKNOWN or rv is UNKNOWN:
            return UNKNOWN
        if lv is None or rv is None:
            return None
        if (
            (isinstance(lv, str) or isinstance(rv, str))
            and (ls := scalar_to_str(lv)) is not None
            and (rs := scalar_to_str(rv)) is not None
        ):
            return ls + rs
        return UNKNOWN
    if isinstance(u, exp.ConcatWs) or (
        isinstance(u, exp.Anonymous) and u.name.upper() == "CONCAT_WS"
    ):
        return fold_concat_ws(u, _ev)
    if isinstance(u, exp.Concat) or (
        isinstance(u, exp.Anonymous) and u.name.upper() == "CONCAT"
    ):
        parts: list[str] = []
        coalesce_nulls = bool(u.args.get("coalesce"))
        for a in u.expressions:
            av = _ev(a)
            if av is UNKNOWN:
                return UNKNOWN
            if av is None:
                if coalesce_nulls:
                    continue
                return None
            if (sv := scalar_to_str(av)) is None:
                return UNKNOWN
            parts.append(sv)
        return "".join(parts)
    if isinstance(u, exp.Repeat):
        if _ev(u.this) is None or _ev(u.args.get("times")) is None:
            return None
        return UNKNOWN
    if isinstance(u, exp.Not):
        b = _eb(u.this)
        return UNKNOWN if b is UNKNOWN else (None if b is None else (not b))
    if isinstance(u, exp.And):
        lb = _eb(u.this)
        return False if lb is False else kleene_and(lb, _eb(u.expression))
    if isinstance(u, exp.Or):
        lb = _eb(u.this)
        return True if lb is True else kleene_or(lb, _eb(u.expression))
    if isinstance(u, _CMP_EXPRS):
        return kleene_cmp(u, _ev(u.this), _ev(u.expression))
    if isinstance(u, exp.Is):
        if (lv := _ev(u.this)) is UNKNOWN:
            return UNKNOWN
        rn = unwrap_parens(u.expression)
        if isinstance(rn, exp.Null):
            return lv is None
        return (lv is bool(rn.this)) if isinstance(rn, exp.Boolean) else UNKNOWN
    if isinstance(u, exp.In):
        if u.args.get("query") or u.args.get("unnest") or not u.expressions:
            return UNKNOWN
        if (lv := _ev(u.this)) is UNKNOWN:
            return UNKNOWN
        saw_unk, saw_null = False, False
        for e in u.expressions:
            ev = _ev(e)
            if ev is UNKNOWN:
                saw_unk = True
            elif ev is None:
                saw_null = True
            elif lv is not None and same_scalar_kind(lv, ev) and not int_float_lossy(lv, ev):
                if lv == ev:
                    return True
            elif lv is not None:
                saw_unk = True
        return UNKNOWN if saw_unk else (None if (lv is None or saw_null) else False)
    if isinstance(u, exp.Between):
        lv, lo, hi = _ev(u.this), _ev(u.args.get("low")), _ev(u.args.get("high"))
        return kleene_and(kleene_cmp(exp.GTE(), lv, lo), kleene_cmp(exp.LTE(), lv, hi))
    if isinstance(u, exp.Escape):
        if _ev(u.expression) is None or _ev(u.this) is None:
            return None
        return UNKNOWN
    if isinstance(u, (exp.Like, exp.ILike)):
        if u.args.get("escape") is not None:
            return UNKNOWN
        lv, rv = _ev(u.this), _ev(u.expression)
        if lv is UNKNOWN or rv is UNKNOWN:
            return UNKNOWN
        if lv is None or rv is None:
            return None
        if isinstance(lv, str) and isinstance(rv, str):
            return like_match(lv, rv, case_insensitive=isinstance(u, exp.ILike))
        return UNKNOWN
    if isinstance(u, exp.Coalesce):
        for a in (u.this, *u.expressions):
            if a is not None and (av := _ev(a)) is not None:
                return av
        return None
    if isinstance(u, exp.Nullif):
        if (av := _ev(u.this)) is UNKNOWN or av is None:
            return av
        bv = _ev(u.expression)
        if (
            bv is UNKNOWN
            or (bv is not None and (not same_scalar_kind(av, bv) or int_float_lossy(av, bv)))
        ):
            return UNKNOWN
        return av if bv is None else (None if av == bv else av)
    if isinstance(u, exp.If) or (
        isinstance(u, exp.Anonymous)
        and u.name.upper() in ("IF", "IFF", "IIF")
        and len(u.expressions) >= 2
    ):
        cond, tb, fb = (
            (u.this, u.args.get("true"), u.args.get("false"))
            if isinstance(u, exp.If)
            else (
                u.expressions[0],
                u.expressions[1],
                u.expressions[2] if len(u.expressions) >= 3 else None,
            )
        )
        cb = _eb(cond)
        if cb is True:
            return _ev(tb)
        if cb is False or cb is None:
            return _ev(fb) if fb is not None else None
        return UNKNOWN
    if isinstance(u, exp.Case):
        return _eval_case_lit(u, ec, vis, smap, depth)
    if isinstance(u, exp.Subquery):
        if any(u.args.get(k) for k in SELECT_DISALLOWED_CLAUSES):
            return UNKNOWN
        return eval_const_lit(u.this, extend_cte_nodes(u, ec), vis, smap, depth + 1)
    if isinstance(u, exp.Select):
        return _eval_select_lit(u, ec, vis, smap, depth)
    if isinstance(u, exp.Convert) and u.args.get("safe") and isinstance(
        dt := u.this, exp.DataType
    ):
        iv = _ev(u.expression)
        if dt.is_type(exp.DataType.Type.BOOLEAN):
            return parse_bool_lit(iv, allow_int=True)
        return None if iv is None else UNKNOWN
    if isinstance(u, exp.Anonymous) and u.name.upper() == "CHOOSE" and len(u.expressions) >= 2:
        iv = _ev(u.expressions[0])
        if iv is None:
            return None
        if isinstance(iv, int) and not isinstance(iv, bool):
            return _ev(u.expressions[iv]) if 1 <= iv <= len(u.expressions) - 1 else None
        return UNKNOWN
    if isinstance(u, exp.Cast) and isinstance(dt := u.args.get("to"), exp.DataType):
        return fold_cast_val(u, dt, _ev)
    if _TRY_CLS is not None and isinstance(u, _TRY_CLS):
        return _eval_try_inner(u.this, ec, vis, smap, depth)
    if isinstance(u, exp.Anonymous) and u.name.upper() == "TRY" and u.expressions:
        return _eval_try_inner(u.expressions[0], ec, vis, smap, depth)
    if _TO_BOOLEAN_CLS is not None and isinstance(u, _TO_BOOLEAN_CLS):
        return parse_bool_lit(_ev(u.this), allow_int=True)
    if (
        isinstance(u, exp.Anonymous)
        and u.name.upper() in ("TO_BOOLEAN", "TRY_TO_BOOLEAN")
        and u.expressions
    ):
        return parse_bool_lit(_ev(u.expressions[0]), allow_int=True)
    if (fv := fold_scalar_func(u, _ev)) is not UNKNOWN:
        return fv
    return UNKNOWN


def eval_const_bool(
    node: exp.Expr | None,
    ctes: dict[str, exp.CTE] | None = None,
    vis: frozenset[str] = frozenset(),
    smap: SmapType | None = None,
) -> bool | None:
    v = _eval_bool_3vl(node, ctes, vis, smap)
    return True if v is True else (False if (v is False or v is None) else None)


def is_ch_if_cond_true(
    node: exp.Expr | None,
    ctes: dict[str, exp.CTE] | None = None,
    vis: frozenset[str] = frozenset(),
    smap: SmapType | None = None,
) -> bool:
    v = eval_const_lit(node, ctes, vis, smap)
    return v is True or (type(v) is int and v == 1)


def decode_branches(
    exprs: Sequence[exp.Expr],
    ctes: dict[str, exp.CTE] | None = None,
    vis: frozenset[str] = frozenset(),
    smap: SmapType | None = None,
) -> list[exp.Expr]:
    if len(exprs) < 3:
        return []
    pairs = [(exprs[i], exprs[i + 1]) for i in range(1, len(exprs) - 1, 2)]
    dflt: exp.Expr = exprs[-1] if len(exprs) % 2 == 0 else exp.Null()
    if (kv := eval_const_lit(exprs[0], ctes, vis, smap)) is UNKNOWN:
        return [r for _, r in pairs] + [dflt]
    live: list[exp.Expr] = []
    matched = False
    for vi, ri in pairs:
        vv = eval_const_lit(vi, ctes, vis, smap)
        if vv is UNKNOWN:
            live.append(ri)
        elif kv is None and vv is None:
            live.append(ri)
            matched = True
            break
        elif kv is None or vv is None:
            continue
        elif same_scalar_kind(kv, vv) and not int_float_lossy(kv, vv):
            if kv == vv:
                live.append(ri)
                matched = True
                break
        else:
            live.append(ri)
    if not matched:
        live.append(dflt)
    return live


__all__ = [
    "UNKNOWN",
    "decode_branches",
    "eval_const_bool",
    "eval_const_lit",
    "is_ch_if_cond_true",
    "same_scalar_kind",
]
