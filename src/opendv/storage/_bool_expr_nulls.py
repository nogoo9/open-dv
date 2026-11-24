"""Empty-subquery checks, nullability helpers, and ancestor smap builder.

Strictly Layer 2a: imports only sqlglot, stdlib, `_ast_stmt_rules`,
`_bool_expr_containers`, and `_bool_expr_vocab`.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from decimal import Decimal

from sqlglot import exp

from opendv.storage._ast_stmt_rules import unwrap_parens
from opendv.storage._bool_expr_containers import (
    ResolvedItem,
    SmapType,
    extend_cte_nodes,
    merge_select_smap,
)
from opendv.storage._bool_expr_vocab import UNKNOWN, int_float_lossy

EvalBoolFn = Callable[
    [exp.Expr | None, dict[str, exp.CTE], frozenset[str], SmapType | None], bool | None
]
EvalLitFn = Callable[
    [exp.Expr | None, dict[str, exp.CTE], frozenset[str], SmapType | None], object
]

_TRY_CLS = getattr(exp, "Try", None)
_EMPTY_CLAUSE_KEYS: tuple[str, ...] = (
    "where", "having", "qualify", "prewhere", "sample", "offset", "pivots", "laterals",
)


def _is_positive_limit(
    lim: object,
    eval_lit_fn: EvalLitFn,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
) -> bool:
    if lim is None:
        return True
    lim_e = (
        lim.expression
        if isinstance(lim, exp.Limit)
        else (lim if isinstance(lim, exp.Expr) else None)
    )
    lv = eval_lit_fn(lim_e, ctes, vis, smap)
    return isinstance(lv, int) and not isinstance(lv, bool) and lv >= 1


UnpackArrFn = Callable[
    [exp.Expr | None, dict[str, exp.CTE], frozenset[str], SmapType | None],
    tuple[list[ResolvedItem], bool],
]


def select_may_be_empty(
    node: exp.Expr | None,
    eval_lit_fn: EvalLitFn,
    eval_bool_fn: EvalBoolFn,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    depth: int = 0,
    unpack_fn: UnpackArrFn | None = None,
) -> bool:
    if depth > 12 or (u := unwrap_parens(node)) is None:
        return False
    if isinstance(u, exp.Subquery):
        if any(u.args.get(k) for k in _EMPTY_CLAUSE_KEYS):
            return True
        nc = extend_cte_nodes(u, ctes)
        if not _is_positive_limit(u.args.get("limit"), eval_lit_fn, nc, vis, smap):
            return True
        if u.args.get("joins"):
            return True
        return select_may_be_empty(
            u.this, eval_lit_fn, eval_bool_fn, nc, vis, smap, depth + 1, unpack_fn=unpack_fn,
        )
    if isinstance(u, exp.Select):
        nc = extend_cte_nodes(u, ctes)
        if any(u.args.get(k) for k in _EMPTY_CLAUSE_KEYS):
            return True
        if not _is_positive_limit(u.args.get("limit"), eval_lit_fn, nc, vis, smap):
            return True
        for j in u.args.get("joins") or ():
            if not isinstance(j, exp.Join):
                return True
            side, kind = (j.side or "").upper(), (j.kind or "").upper()
            on_e, using_e = j.args.get("on"), j.args.get("using")
            if using_e:
                return True
            is_comma_cross = side == "" and kind == "" and on_e is None
            if (
                side != "LEFT"
                and kind != "CROSS"
                and not is_comma_cross
                and not (on_e is not None and eval_bool_fn(on_e, nc, vis, smap) is True)
            ):
                return True
            if select_may_be_empty(
                j.this, eval_lit_fn, eval_bool_fn, nc, vis, smap, depth + 1, unpack_fn=unpack_fn,
            ):
                return True
        fm = u.args.get("from_")
        if isinstance(fm, exp.From):
            if fm.this is not None and select_may_be_empty(
                fm.this, eval_lit_fn, eval_bool_fn, nc, vis, smap, depth + 1, unpack_fn=unpack_fn,
            ):
                return True
            if any(
                select_may_be_empty(
                    e, eval_lit_fn, eval_bool_fn, nc, vis, smap, depth + 1, unpack_fn=unpack_fn,
                )
                for e in (fm.expressions or ())
                if isinstance(e, exp.Expr)
            ):
                return True
        return False
    if isinstance(u, exp.SetOperation):
        nc = extend_cte_nodes(u, ctes)
        if any(u.args.get(k) for k in _EMPTY_CLAUSE_KEYS) or not _is_positive_limit(
            u.args.get("limit"), eval_lit_fn, nc, vis, smap,
        ):
            return True
        if isinstance(u, exp.Union):
            return select_may_be_empty(
                u.this, eval_lit_fn, eval_bool_fn, nc, vis, smap, depth + 1, unpack_fn=unpack_fn,
            ) and select_may_be_empty(
                u.expression, eval_lit_fn, eval_bool_fn, nc, vis, smap, depth + 1,
                unpack_fn=unpack_fn,
            )
        return True
    if isinstance(u, exp.Unnest):
        if not u.expressions:
            return True
        if unpack_fn is not None:
            return any(unpack_fn(e, ctes, vis, smap)[1] for e in u.expressions)
        return any(
            isinstance(eu := unwrap_parens(e), exp.Array) and not eu.expressions
            for e in u.expressions
        )
    if isinstance(u, exp.Table) and not u.db and not u.catalog and u.name:
        cn = u.name.lower()
        if cn in ctes and cn not in vis:
            return select_may_be_empty(
                ctes[cn].this, eval_lit_fn, eval_bool_fn, ctes, vis | {cn}, smap, depth + 1,
                unpack_fn=unpack_fn,
            )
    return False


def can_expr_eval_null(
    node: exp.Expr | None,
    eval_lit_fn: EvalLitFn,
    eval_bool_fn: EvalBoolFn,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    depth: int = 0,
) -> bool:
    if depth > 12 or (u := unwrap_parens(node)) is None:
        return False
    if isinstance(u, exp.Null):
        return True
    if eval_lit_fn(u, ctes, vis, smap) is None:
        return True
    if isinstance(u, exp.Subquery):
        nc = extend_cte_nodes(u, ctes)
        if select_may_be_empty(u, eval_lit_fn, eval_bool_fn, nc, vis, smap, depth + 1):
            return True
        return can_expr_eval_null(u.this, eval_lit_fn, eval_bool_fn, nc, vis, smap, depth + 1)
    if isinstance(u, exp.Select):
        nc = extend_cte_nodes(u, ctes)
        if select_may_be_empty(u, eval_lit_fn, eval_bool_fn, nc, vis, smap, depth + 1):
            return True
        if len(u.expressions) == 1:
            p = u.expressions[0]
            pe = p.this if isinstance(p, exp.Alias) else p
            _, sel_smap, _, _ = merge_select_smap(u, nc, smap)
            return can_expr_eval_null(pe, eval_lit_fn, eval_bool_fn, nc, vis, sel_smap, depth + 1)
        return False
    if isinstance(u, exp.Case):
        ifs = [br for br in (u.args.get("ifs") or ()) if isinstance(br, exp.If)]
        dflt, subj = u.args.get("default"), u.args.get("this")
        live: list[exp.Expr | None] = []
        stopped = False
        if subj is None:
            for br in ifs:
                cb = eval_bool_fn(br.this, ctes, vis, smap)
                if cb is False:
                    continue
                live.append(br.args.get("true"))
                if cb is True:
                    stopped = True
                    break
        else:
            sv = eval_lit_fn(subj, ctes, vis, smap)
            for br in ifs:
                if sv is None:
                    continue
                if sv is not UNKNOWN and (
                    wv := eval_lit_fn(br.this, ctes, vis, smap)
                ) is not UNKNOWN:
                    if wv is None:
                        continue
                    same_k = (type(sv) is bool) == (type(wv) is bool) and (
                        isinstance(sv, str) == isinstance(wv, str)
                    )
                    if same_k and not int_float_lossy(sv, wv):
                        if sv != wv:
                            continue
                        live.append(br.args.get("true"))
                        stopped = True
                        break
                live.append(br.args.get("true"))
        if not stopped:
            live.append(dflt if dflt is not None else exp.Null())
        return any(
            can_expr_eval_null(b, eval_lit_fn, eval_bool_fn, ctes, vis, smap, depth + 1)
            for b in live
        )
    if isinstance(u, exp.If) or (
        isinstance(u, exp.Anonymous)
        and u.name.upper() in ("IF", "IFF", "IIF")
        and len(u.expressions) >= 2
    ):
        fb_arg = u.expressions[2] if len(u.expressions) >= 3 else None
        cond, tb, fb = (
            (u.this, u.args.get("true"), u.args.get("false"))
            if isinstance(u, exp.If)
            else (u.expressions[0], u.expressions[1], fb_arg)
        )
        cb = eval_bool_fn(cond, ctes, vis, smap)
        if cb is True:
            return can_expr_eval_null(tb, eval_lit_fn, eval_bool_fn, ctes, vis, smap, depth + 1)
        if cb is False:
            return (
                can_expr_eval_null(fb, eval_lit_fn, eval_bool_fn, ctes, vis, smap, depth + 1)
                if fb
                else True
            )
        tb_null = can_expr_eval_null(tb, eval_lit_fn, eval_bool_fn, ctes, vis, smap, depth + 1)
        fb_null = (
            can_expr_eval_null(fb, eval_lit_fn, eval_bool_fn, ctes, vis, smap, depth + 1)
            if fb
            else True
        )
        return tb_null or fb_null
    if isinstance(u, exp.Coalesce):
        args = [a for a in (u.this, *u.expressions) if a is not None]
        return bool(args) and all(
            can_expr_eval_null(a, eval_lit_fn, eval_bool_fn, ctes, vis, smap, depth + 1)
            for a in args
        )
    if isinstance(u, exp.Nullif):
        ua, ub = unwrap_parens(u.this), unwrap_parens(u.expression)
        return (ua is not None and ua == ub) or can_expr_eval_null(
            u.this, eval_lit_fn, eval_bool_fn, ctes, vis, smap, depth + 1,
        )
    if (
        isinstance(u, exp.TryCast)
        or (_TRY_CLS is not None and isinstance(u, _TRY_CLS))
        or (isinstance(u, exp.Convert) and u.args.get("safe"))
        or (isinstance(u, exp.Anonymous) and u.name.upper() == "TRY")
    ):
        return eval_lit_fn(u, ctes, vis, smap) in (None, UNKNOWN)
    return False


def build_ancestor_smap(node: exp.Expr | None) -> tuple[dict[str, exp.CTE], SmapType | None]:
    init_ctes: dict[str, exp.CTE] = {}
    init_smap: SmapType | None = None
    if node is None:
        return init_ctes, init_smap
    ancestors: list[exp.Expr] = []
    cur_p = node.parent
    while cur_p is not None:
        ancestors.append(cur_p)
        cur_p = cur_p.parent
    path = list(reversed(ancestors))
    for idx, anc in enumerate(path):
        init_ctes = extend_cte_nodes(anc, init_ctes)
        child = path[idx + 1] if idx + 1 < len(path) else node
        if isinstance(anc, (exp.With, exp.CTE)):
            init_smap = None
        elif isinstance(anc, exp.Select):
            if isinstance(child, exp.From):
                grand = path[idx + 2] if idx + 2 < len(path) else node
                if grand.arg_key == "this" and not isinstance(grand, (exp.Lateral, exp.Unnest)):
                    init_smap = None
                else:
                    _, init_smap, _, _ = merge_select_smap(anc, init_ctes, init_smap)
            elif isinstance(child, exp.Join):
                grand = path[idx + 2] if idx + 2 < len(path) else node
                if grand.arg_key in ("on", "using") or isinstance(
                    grand, (exp.Lateral, exp.Unnest)
                ):
                    _, init_smap, _, _ = merge_select_smap(anc, init_ctes, init_smap)
                else:
                    init_smap = None
            else:
                _, init_smap, _, _ = merge_select_smap(anc, init_ctes, init_smap)
    return init_ctes, init_smap


def live_container_case_branches(
    u: exp.Case,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    hooks: tuple[EvalBoolFn, EvalLitFn] | None,
) -> list[exp.Expr | None]:
    ifs = [br for br in (u.args.get("ifs") or ()) if isinstance(br, exp.If)]
    dflt, subj = u.args.get("default"), u.args.get("this")
    live: list[exp.Expr | None] = []
    stopped = False
    if subj is None:
        for br in ifs:
            cb = hooks[0](br.this, ctes, vis, smap) if hooks is not None else None
            if cb is False:
                continue
            live.append(br.args.get("true"))
            if cb is True:
                stopped = True
                break
    else:
        sv = hooks[1](subj, ctes, vis, smap) if hooks is not None else None
        for br in ifs:
            if hooks is not None:
                if sv is None:
                    continue
                wv = hooks[1](br.this, ctes, vis, smap)
                if wv is None:
                    continue
                if (
                    isinstance(sv, (bool, int, float, str))
                    and isinstance(wv, (bool, int, float, str))
                    and (type(sv) is bool) == (type(wv) is bool)
                    and isinstance(sv, str) == isinstance(wv, str)
                    and not int_float_lossy(sv, wv)
                ):
                    if sv != wv:
                        continue
                    live.append(br.args.get("true"))
                    stopped = True
                    break
            live.append(br.args.get("true"))
    if not stopped:
        live.append(dflt if dflt is not None else exp.Null())
    return live


def adjusted_bracket_map_key(node: exp.Expr, key_expr: exp.Expr | None) -> exp.Expr | None:
    if (
        isinstance(node, exp.Bracket)
        and len(node.expressions) == 1
        and node.args.get("offset") != 1
        and node.this is not None
        and node.this.type is not None
        and node.this.type.this in (exp.DataType.Type.UNKNOWN, exp.DataType.Type.ARRAY)
        and (be := node.expressions[0]).type is None
        and not isinstance(be, exp.Null)
    ):
        return exp.Add(this=be.copy(), expression=exp.Literal.number(1))
    return key_expr


def _is_inexact_float(v: object) -> bool:
    if not isinstance(v, float):
        return False
    return not math.isfinite(v) or abs(v) >= (1 << 53) or Decimal(v) != Decimal(str(v))


def eval_const_arith(u: exp.Expr, lv: object, rv: object) -> object:
    if lv is UNKNOWN or rv is UNKNOWN:
        return UNKNOWN
    if lv is None or rv is None:
        return None
    if not (
        isinstance(lv, (int, float))
        and not isinstance(lv, bool)
        and isinstance(rv, (int, float))
        and not isinstance(rv, bool)
    ):
        return UNKNOWN
    if int_float_lossy(lv, rv) or _is_inexact_float(lv) or _is_inexact_float(rv):
        return UNKNOWN
    try:
        arith_res: int | float
        if isinstance(u, exp.Add):
            arith_res = lv + rv
        elif isinstance(u, exp.Sub):
            arith_res = lv - rv
        elif isinstance(u, exp.Mul):
            arith_res = lv * rv
        elif rv == 0:
            return UNKNOWN
        elif isinstance(u, exp.Div):
            if isinstance(lv, int) and isinstance(rv, int):
                if lv % rv != 0 or abs(lv // rv) >= (1 << 53):
                    return UNKNOWN
                arith_res = float(lv // rv)
            else:
                arith_res = lv / rv
        elif isinstance(lv, int) and isinstance(rv, int):
            rem_i = abs(lv) % abs(rv)
            arith_res = -rem_i if lv < 0 and rem_i else rem_i
        else:
            arith_res = math.fmod(lv, rv)
    except OverflowError:
        return UNKNOWN
    if isinstance(arith_res, float):
        return UNKNOWN if _is_inexact_float(arith_res) else arith_res
    return UNKNOWN if abs(arith_res) > (1 << 63) - 1 else arith_res


__all__ = [
    "adjusted_bracket_map_key",
    "build_ancestor_smap",
    "can_expr_eval_null",
    "eval_const_arith",
    "live_container_case_branches",
    "select_may_be_empty",
]
