"""Quantile-target unwrapping, passthrough & array-cast helpers for `_bool_expr_*`.

Strictly Layer 2a: imports only sqlglot, stdlib, `_ast_stmt_rules`,
`_bool_expr_containers`, `_bool_expr_funcs`, and `_bool_expr_maps`.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlglot import exp

from opendv.storage._ast_stmt_rules import unwrap_parens
from opendv.storage._bool_expr_containers import (
    LAG_LEAD_EXPRS,
    PERCENTILE_DISC_CLS,
    VALUE_PASSTHROUGH_EXPRS,
    VALUE_PASSTHROUGH_NAMES,
    ResolvedItem,
    SmapType,
    container_func_name,
    is_array_ctor,
    is_known_container_ctor,
    is_quantile_passthrough_fn,
    is_unknown_container_or_lambda,
    is_wg_passthrough,
)
from opendv.storage._bool_expr_funcs import expr_value_args, split_ch_combinators
from opendv.storage._bool_expr_maps import unpack_container_ctor
from opendv.storage._bool_expr_vocab import ARRAY_PASS_ANON

_QUANTILE_CLS = getattr(exp, "Quantile", None)
_GENERATE_SERIES_CLS = getattr(exp, "GenerateSeries", None)
_LOW_CARD_TYPE = getattr(exp.DataType.Type, "LOWCARDINALITY", None)
_WINDOW_OFFSET_NAMES = frozenset({"NTH_VALUE", "LAG", "LEAD", "LAGINFRAME", "LEADINFRAME"})
_ARRAY_REV_CLSES: tuple[type[exp.Expr], ...] = tuple(
    c for c in (getattr(exp, "ArrayReverse", None), getattr(exp, "Reverse", None)) if c is not None
)
_ARRAY_REORDER_CLSES: tuple[type[exp.Expr], ...] = tuple(
    c
    for n in (
        "ArrayDistinct", "ArrayRemove", "ArraySort", "ArrayCompact",
        "ArrayRemoveAt", "Shuffle", "ArrayExcept",
    )
    if (c := getattr(exp, n, None)) is not None
)

EvalBoolFn = Callable[
    [exp.Expr | None, dict[str, exp.CTE], frozenset[str], SmapType | None], bool | None
]
EvalLitFn = Callable[
    [exp.Expr | None, dict[str, exp.CTE], frozenset[str], SmapType | None], object
]
EvalHooks = tuple[EvalBoolFn, EvalLitFn]
ResolveItemsFn = Callable[
    [exp.Expr | None, dict[str, exp.CTE], frozenset[str], SmapType | None, int, EvalHooks | None],
    list[ResolvedItem],
]


def passthrough_inner_args(cur: exp.Expr) -> tuple[list[exp.Expr], bool] | None:
    if isinstance(cur, (exp.Paren, exp.IgnoreNulls)):
        return ([cur.this], False) if isinstance(cur.this, exp.Expr) else None
    if isinstance(cur, (exp.Window, exp.Filter)):
        return ([cur.this], True) if isinstance(cur.this, exp.Expr) else None
    if isinstance(cur, exp.WithinGroup):
        wg = unwrap_parens(cur.this)
        if wg is None:
            return None
        wg_fn = container_func_name(wg)
        if is_quantile_passthrough_fn(wg_fn) or wg_fn in ("PERCENTILE_CONT", "QUANTILE_CONT"):
            return None
        if is_wg_passthrough(wg) or wg_fn in VALUE_PASSTHROUGH_NAMES:
            if expr_value_args(wg):
                return ([wg], True)
            if isinstance(cur.expression, exp.Order):
                ords = [
                    o.this
                    for o in cur.expression.expressions
                    if isinstance(o, exp.Ordered) and isinstance(o.this, exp.Expr)
                ]
                return (ords, True) if ords else None
        return None
    if isinstance(cur, VALUE_PASSTHROUGH_EXPRS):
        if is_quantile_passthrough_fn(container_func_name(cur)):
            return None
        return ([cur.this], True) if isinstance(cur.this, exp.Expr) else None
    if isinstance(cur, LAG_LEAD_EXPRS):
        args = [a for a in (cur.this, cur.args.get("default")) if isinstance(a, exp.Expr)]
        return (args, cur.args.get("default") is None) if args else None
    if isinstance(cur, exp.Func):
        fn = container_func_name(cur)
        base_fn, _, if_cnt, has_or_default = split_ch_combinators(fn, _WINDOW_OFFSET_NAMES)
        if is_quantile_passthrough_fn(base_fn) or base_fn in ("PERCENTILE_CONT", "QUANTILE_CONT"):
            return None
        raw_args = expr_value_args(cur)
        vargs = raw_args[:-if_cnt] if if_cnt and len(raw_args) > if_cnt else raw_args
        if not vargs:
            return None
        if base_fn in ("LAG", "LEAD", "LAGINFRAME", "LEADINFRAME"):
            args = [vargs[0], vargs[2]] if len(vargs) >= 3 else [vargs[0]]
            return (args, len(vargs) < 3 and not has_or_default)
        if base_fn == "NTH_VALUE" or base_fn in VALUE_PASSTHROUGH_NAMES:
            return ([vargs[0]], True)
    return None


def unwrap_quantile_target(node: exp.Expr | None) -> exp.Expr | None:
    cur = node
    for _ in range(16):
        if cur is None:
            return None
        if (
            (PERCENTILE_DISC_CLS is not None and isinstance(cur, PERCENTILE_DISC_CLS))
            or (_QUANTILE_CLS is not None and isinstance(cur, _QUANTILE_CLS))
            or is_quantile_passthrough_fn(container_func_name(cur))
        ):
            return cur
        if isinstance(cur, exp.WithinGroup):
            wg = unwrap_parens(cur.this)
            if wg is not None and is_quantile_passthrough_fn(container_func_name(wg)):
                return cur
        pt = passthrough_inner_args(cur)
        if pt is None:
            return None
        if len(pt[0]) == 1:
            cur = pt[0][0]
            continue
        for cand in pt[0]:
            if (res := unwrap_quantile_target(cand)) is not None:
                return res
        return None
    return None


def is_quantile_array_target(node: exp.Expr | None) -> bool:
    qt = unwrap_quantile_target(node)
    if qt is None:
        return False
    if isinstance(qt, exp.WithinGroup):
        wg = unwrap_parens(qt.this)
        return wg is not None and any(
            is_array_ctor(unwrap_parens(a))
            for a in (getattr(wg, "this", None), *(wg.expressions or ()))
            if isinstance(a, exp.Expr)
        )
    if container_func_name(qt).startswith("QUANTILES"):
        return True
    q = qt.args.get("quantile") or getattr(qt, "expression", None)
    if isinstance(q, exp.Expr) and is_array_ctor(unwrap_parens(q)):
        return True
    return any(
        is_array_ctor(unwrap_parens(a))
        for a in (qt.expressions or ())
        if isinstance(a, exp.Expr)
    )


def unwrap_array_elem_dt(dt: exp.DataType) -> exp.DataType | None:
    if not dt.is_type(exp.DataType.Type.ARRAY) or not dt.expressions:
        return None
    ed = dt.expressions[0]
    while (
        isinstance(ed, exp.DataType)
        and _LOW_CARD_TYPE is not None
        and ed.is_type(_LOW_CARD_TYPE)
        and ed.expressions
    ):
        ed = ed.expressions[0]
    return ed if isinstance(ed, exp.DataType) else None


def is_array_cast_node(node: exp.Expr | None) -> bool:
    u = unwrap_parens(node)
    return (
        isinstance(u, (exp.Cast, exp.TryCast))
        and isinstance(dt := u.args.get("to"), exp.DataType)
        and unwrap_array_elem_dt(dt) is not None
    )


def extract_array_cast_elem(ex_u: exp.Expr | None) -> exp.Expr | None:
    if not isinstance(ex_u, (exp.Cast, exp.TryCast)):
        return None
    dt = ex_u.args.get("to")
    if not isinstance(dt, exp.DataType):
        return None
    ed = unwrap_array_elem_dt(dt)
    if ed is None or ex_u.this is None:
        return None
    cls = exp.TryCast if isinstance(ex_u, exp.TryCast) else exp.Cast
    return cls(this=ex_u.this, to=ed.copy())


def cast_container_items(
    u: exp.Cast | exp.TryCast,
    dt: exp.DataType,
    inner_res: list[ResolvedItem],
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
) -> list[ResolvedItem]:
    ed = unwrap_array_elem_dt(dt)
    if ed is None:
        return inner_res
    cls = exp.TryCast if isinstance(u, exp.TryCast) else exp.Cast
    if not inner_res:
        if ed.is_type(exp.DataType.Type.BOOLEAN, exp.DataType.Type.BIT) and u.this is not None:
            res: list[ResolvedItem] = [(cls(this=u.this, to=dt.copy()), ctes, vis, smap)]
            if isinstance(u, exp.TryCast):
                res.append((exp.Null(), ctes, vis, smap))
            return res
        return []
    out_cast: list[ResolvedItem] = []
    for ex, ec, ev, es in inner_res:
        ex_u = unwrap_parens(ex)
        if isinstance(ex_u, exp.Null):
            out_cast.append((ex_u, ec, ev, es))
        elif is_array_ctor(ex_u) and ex_u is not None:
            if ex_u.expressions:
                cast_els = [cls(this=el, to=ed.copy()) for el in ex_u.expressions]
                out_cast.append((exp.Array(expressions=cast_els), ec, ev, es))
            else:
                out_cast.append((ex_u, ec, ev, es))
        else:
            out_cast.append((cls(this=ex, to=dt.copy()), ec, ev, es))
    if isinstance(u, exp.TryCast) and not any(
        isinstance(unwrap_parens(ex), exp.Null) for ex, *_ in out_cast
    ):
        out_cast.append((exp.Null(), ctes, vis, smap))
    return out_cast


def _is_resolved_container(ex: exp.Expr | None) -> bool:
    u = unwrap_parens(ex)
    return bool(
        is_array_ctor(u)
        or is_known_container_ctor(u)
        or bool(unpack_container_ctor(u, None))
        or is_unknown_container_or_lambda(u)
        or is_quantile_array_target(u)
        or is_array_cast_node(u)
        or (_GENERATE_SERIES_CLS is not None and isinstance(u, _GENERATE_SERIES_CLS))
    )


def resolve_passthrough_container(
    u: exp.Expr,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    depth: int,
    hooks: EvalHooks | None,
    resolve_fn: ResolveItemsFn,
) -> list[ResolvedItem] | None:
    pt = passthrough_inner_args(u)
    if pt is None:
        return None
    inner_args, can_null = pt
    out: list[ResolvedItem] = []
    saw_container = False
    for arg in inner_args:
        res = resolve_fn(arg, ctes, vis, smap, depth + 1, hooks)
        if any(_is_resolved_container(ex) for ex, *_ in res):
            saw_container = True
        out.extend(res)
    if not saw_container:
        return None
    if can_null and not any(isinstance(unwrap_parens(ex), exp.Null) for ex, *_ in out):
        out.append((exp.Null(), ctes, vis, smap))
    return out


def is_array_or_quantile_target(u: exp.Expr | None) -> bool:
    return is_quantile_array_target(u) or is_array_cast_node(u)


def classify_seq_transform(
    u: exp.Expr,
) -> tuple[str, tuple[exp.Expr | None, ...], str | None] | None:
    if _ARRAY_REV_CLSES and isinstance(u, _ARRAY_REV_CLSES):
        return "reverse", (u.this,), None
    if _ARRAY_REORDER_CLSES and isinstance(u, _ARRAY_REORDER_CLSES):
        return "reorder", (u.this,), None
    if isinstance(u, exp.Anonymous) and u.expressions:
        un = u.name.upper()
        if un in ("ARRAY_REVERSE", "LIST_REVERSE", "REVERSE"):
            return "reverse", (u.expressions[0],), None
        if un in ("ARRAY_POP_FRONT", "LIST_POP_FRONT", "ARRAYPOPFRONT"):
            return "pop_front", (u.expressions[0],), None
        if un in ("ARRAY_POP_BACK", "LIST_POP_BACK", "ARRAYPOPBACK"):
            return "pop_back", (u.expressions[0],), None
        if un in ARRAY_PASS_ANON:
            return "reorder", (u.expressions[0],), None
    return None


def apply_seq_transform(kind: str, items: list[ResolvedItem]) -> list[ResolvedItem]:
    if kind == "pass":
        return items
    out: list[ResolvedItem] = []
    for ex, ec, ev, es in items:
        ex_u = unwrap_parens(ex)
        if not (is_array_ctor(ex_u) and ex_u is not None):
            out.append((ex, ec, ev, es))
        elif kind == "reverse":
            out.append((exp.Array(expressions=list(reversed(ex_u.expressions))), ec, ev, es))
        elif kind == "pop_front":
            out.append((exp.Array(expressions=list(ex_u.expressions[1:])), ec, ev, es))
        elif kind == "pop_back":
            out.append((exp.Array(expressions=list(ex_u.expressions[:-1])), ec, ev, es))
        elif (
            kind == "reorder"
            and 1 <= len(ex_u.expressions) <= 16
            and any(isinstance(unwrap_parens(el), exp.Null) for el in ex_u.expressions)
        ):
            n = len(ex_u.expressions)
            out.extend((exp.Array(expressions=[el] * n), ec, ev, es) for el in ex_u.expressions)
        else:
            out.append((ex, ec, ev, es))
    return out


__all__ = [
    "apply_seq_transform",
    "cast_container_items",
    "classify_seq_transform",
    "extract_array_cast_elem",
    "is_array_cast_node",
    "is_array_or_quantile_target",
    "is_quantile_array_target",
    "passthrough_inner_args",
    "resolve_passthrough_container",
    "unwrap_array_elem_dt",
    "unwrap_quantile_target",
]
