"""Boolean/NULL scalar-expression guard for ANY/ALL checks."""

from __future__ import annotations

from collections.abc import Sequence

from sqlglot import exp

from opendv.storage._ast_stmt_rules import is_safe_or_try_func, unwrap_parens
from opendv.storage._bool_expr_const import (
    UNKNOWN,
    decode_branches,
    eval_const_bool,
    eval_const_lit,
    is_ch_if_cond_true,
)
from opendv.storage._bool_expr_containers import (
    BOOL_PREDICATE_EXPRS,
    LAG_LEAD_EXPRS,
    PERCENTILE_DISC_CLS,
    VALUE_PASSTHROUGH_EXPRS,
    ResolvedItem,
    SmapType,
    container_func_name,
    extend_cte_nodes,
    is_array_ctor,
    is_known_container_ctor,
    is_unknown_container_or_lambda,
    is_wg_passthrough,
    param_agg_q_args,
    within_group_quantile_arg,
)
from opendv.storage._bool_expr_funcs import (
    expr_value_args,
    is_nullable_agg_expr,
    null_strict_arg_nodes,
    resolve_window_spec,
)
from opendv.storage._bool_expr_nulls import build_ancestor_smap, select_may_be_empty
from opendv.storage._bool_expr_ops import (
    extract_container_items,
    resolve_container_items,
    unpack_array_with_emptiness,
)
from opendv.storage._bool_expr_ops import (
    resolve_col_with_containers as resolve_col_from_smap,
)
from opendv.storage._bool_expr_ops import (
    resolve_source_col_with_containers as resolve_source_col,
)
from opendv.storage._bool_expr_preds import eval_bool_pred_null, eval_nullif_null
from opendv.storage._bool_expr_scalar import (
    eval_anon_guard,
    eval_ch_passthrough_or_window,
    eval_safe_scalar_null,
    live_case_branches,
)
from opendv.storage._bool_expr_slicing import is_safe_window_spec
from opendv.storage._bool_expr_vocab import SAFE_CONTAINER_SCALAR_FUNCS

_HOOKS = (eval_const_bool, eval_const_lit)
_TRY_CLS = getattr(exp, "Try", None)
_QUANTILE_CLS = getattr(exp, "Quantile", None)
_NTH_VALUE_CLS = getattr(exp, "NthValue", None)
_MAP_KEYS_CLS = getattr(exp, "MapKeys", None)
_FLATTEN_CLS = getattr(exp, "Flatten", None)
_TRANSFORM_CLS = getattr(exp, "Transform", None)
_ARRAY_FILTER_CLS = getattr(exp, "ArrayFilter", None)
_REDUCE_CLS = getattr(exp, "Reduce", None)
_DIRECT_NULL_CONTAINER_CLSES: tuple[type[exp.Expr], ...] = tuple(
    c
    for n in ("MapFromEntries", "Map", "VarMap", "GenerateSeries")
    if (c := getattr(exp, n, None)) is not None
)
_HOF_ARRAY_ANON: frozenset[str] = frozenset({
    "LIST_TRANSFORM", "ARRAY_TRANSFORM", "LIST_APPLY", "ARRAY_APPLY", "TRANSFORM",
    "LIST_FILTER", "ARRAY_FILTER", "FILTER",
})
_HOF_REDUCE_ANON: frozenset[str] = frozenset({"LIST_REDUCE", "ARRAY_REDUCE", "REDUCE"})


def _rsrc(
    u: exp.Expr | None,
    cn: str | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
) -> list[ResolvedItem]:
    return resolve_source_col(u, cn, ctes, vis, smap, hooks=_HOOKS)


def _unpack_arr(
    ue: exp.Expr | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
) -> tuple[list[ResolvedItem], bool]:
    return unpack_array_with_emptiness(ue, ctes, vis, smap, hooks=_HOOKS)


def is_bool_or_null_expr(node: exp.Expr | None, *, allow_null: bool = True) -> bool:
    def _chk(
        its: Sequence[exp.Expr | None],
        an: bool,
        ctes: dict[str, exp.CTE],
        vis: frozenset[str],
        smap: SmapType | None,
        *,
        ab: bool = True,
    ) -> bool:
        return bool(its) and any(_w(x, an, ctes, vis, smap, ab=ab) for x in its)

    def _chk_res(its: list[ResolvedItem], an: bool, *, ab: bool = True) -> bool:
        return bool(its) and any(_w(x, an, cc, nv, sm, ab=ab) for x, cc, nv, sm in its)

    def _eval_if(
        cond: exp.Expr | None,
        t_br: exp.Expr | None,
        f_br: exp.Expr | None,
        an: bool,
        ctes: dict[str, exp.CTE],
        vis: frozenset[str],
        smap: SmapType | None,
        *,
        ab: bool = True,
    ) -> bool:
        cb = eval_const_bool(cond, ctes, vis, smap)
        if cb is False:
            return _w(f_br, an, ctes, vis, smap, ab=ab) if f_br is not None else an
        if cb is True:
            return _w(t_br, an, ctes, vis, smap, ab=ab)
        brs: list[exp.Expr | None] = [t_br, f_br if f_br is not None else exp.Null()]
        return _chk(brs, an, ctes, vis, smap, ab=ab)

    def _has_null_q(
        qe: exp.Expr | None, ctes: dict[str, exp.CTE], vis: frozenset[str], smap: SmapType | None,
    ) -> bool:
        return qe is not None and (
            _w(qe, True, ctes, vis, smap, ab=False)
            or any(
                _w(x, True, cc, nv, sm, ab=False)
                for x, cc, nv, sm in _unpack_arr(qe, ctes, vis, smap)[0]
            )
        )

    def _w(
        nd: exp.Expr | None,
        an: bool,
        ctes: dict[str, exp.CTE],
        vis: frozenset[str],
        smap: SmapType | None,
        *,
        ab: bool = True,
    ) -> bool:
        cur = unwrap_parens(nd)
        if cur is None:
            return False
        if isinstance(cur, exp.Null):
            return an
        if isinstance(cur, exp.Column):
            if smap and (res := resolve_col_from_smap(cur, smap, ctes, vis, hooks=_HOOKS)):
                return _chk_res(res, an, ab=ab)
            return False
        if isinstance(cur, BOOL_PREDICATE_EXPRS):
            if ab:
                return True
            return eval_bool_pred_null(cur, an, ctes, vis, smap, _w, _rsrc, _unpack_arr)
        if isinstance(cur, exp.Filter):
            cond = cur.expression.this if isinstance(cur.expression, exp.Where) else cur.expression
            if an and eval_const_bool(cond, ctes, vis, smap) is not True:
                return True
            return _w(cur.this, an, ctes, vis, smap, ab=ab)
        if isinstance(cur, exp.Window):
            w_spec, spec_ok = resolve_window_spec(cur)
            if an and (not spec_ok or not is_safe_window_spec(w_spec)):
                return True
            return _w(cur.this, an, ctes, vis, smap, ab=ab)
        if isinstance(cur, exp.IgnoreNulls):
            return _w(cur.this, an, ctes, vis, smap, ab=ab)
        if isinstance(cur, exp.WithinGroup):
            if _w(cur.this, an, ctes, vis, smap, ab=ab) or (
                an and _has_null_q(within_group_quantile_arg(cur), ctes, vis, smap)
            ):
                return True
            wg_ab = ab and is_wg_passthrough(cur.this)
            if isinstance(cur.expression, exp.Order) and (an or wg_ab):
                return any(
                    _w(o.this, an, ctes, vis, smap, ab=wg_ab)
                    for o in cur.expression.expressions
                    if isinstance(o, exp.Ordered)
                )
            return False
        if _NTH_VALUE_CLS is not None and isinstance(cur, _NTH_VALUE_CLS):
            if _w(cur.this, an, ctes, vis, smap, ab=ab):
                return True
            return bool(an and eval_const_lit(cur.args.get("offset"), ctes, vis, smap) != 1)
        if isinstance(cur, VALUE_PASSTHROUGH_EXPRS):
            if _w(cur.this, an, ctes, vis, smap, ab=ab):
                return True
            if an and (
                (is_pd := PERCENTILE_DISC_CLS is not None and isinstance(cur, PERCENTILE_DISC_CLS))
                or (_QUANTILE_CLS is not None and isinstance(cur, _QUANTILE_CLS))
            ):
                q_arg = (cur.expression if is_pd else cur.args.get("quantile")) or cur.this
                return _has_null_q(q_arg, ctes, vis, smap)
            return False
        if isinstance(cur, LAG_LEAD_EXPRS):
            dflt_arg = cur.args.get("default")
            if dflt_arg is None:
                if _w(cur.this, an, ctes, vis, smap, ab=ab):
                    return True
                return bool(an and eval_const_lit(cur.args.get("offset"), ctes, vis, smap) != 0)
            return _chk([cur.this, dflt_arg], an, ctes, vis, smap, ab=ab) or bool(
                an and _w(cur.args.get("offset"), True, ctes, vis, smap, ab=False)
            )
        if isinstance(cur, exp.Explode):
            if cur.this is None:
                return False
            its, has_empty = unpack_array_with_emptiness(cur.this, ctes, vis, smap, hooks=_HOOKS)
            return (an and not its and has_empty) or _chk_res(its, an, ab=ab)
        if isinstance(cur, exp.Unnest):
            if not cur.expressions:
                return an
            for e in cur.expressions:
                its, has_empty = unpack_array_with_emptiness(e, ctes, vis, smap, hooks=_HOOKS)
                if (an and not its and has_empty) or _chk_res(its, an, ab=ab):
                    return True
            return False
        if (
            ext_items := extract_container_items(cur, ctes, vis, smap, hooks=_HOOKS)
        ) is not None:
            return _chk_res(ext_items, an, ab=ab)
        if (_REDUCE_CLS is not None and isinstance(cur, _REDUCE_CLS)) or (
            isinstance(cur, exp.Anonymous) and cur.name.upper() in _HOF_REDUCE_ANON
        ):
            r_items = resolve_container_items(cur, ctes, vis, smap, hooks=_HOOKS)
            return (ab or an) if not r_items else _chk_res(r_items, an, ab=ab)
        if (
            (_TRANSFORM_CLS is not None and isinstance(cur, _TRANSFORM_CLS))
            or (_ARRAY_FILTER_CLS is not None and isinstance(cur, _ARRAY_FILTER_CLS))
            or (_FLATTEN_CLS is not None and isinstance(cur, _FLATTEN_CLS))
            or (isinstance(cur, exp.Anonymous) and (
                cur.name.upper() in _HOF_ARRAY_ANON
                or cur.name.upper() in ("FLATTEN", "LIST_FLATTEN", "ARRAY_FLATTEN")
            ))
        ):
            arr_items = resolve_container_items(cur, ctes, vis, smap, hooks=_HOOKS)
            return any(
                is_unknown_container_or_lambda(ex)
                or (an and not is_array_ctor(unwrap_parens(ex)) and _w(ex, True, ec, nv, es, ab=ab))
                for ex, ec, nv, es in arr_items
            )
        if isinstance(cur, exp.ModelAttribute) and isinstance(
            unwrap_parens(cur.expression), exp.BitwiseNot
        ):
            return ab
        if _TRY_CLS is not None and isinstance(cur, _TRY_CLS):
            return _w(cur.this, False, ctes, vis, smap, ab=ab) or bool(
                an and eval_const_lit(cur, ctes, vis, smap) in (None, UNKNOWN)
            )
        if isinstance(cur, exp.TryCast) or (
            isinstance(cur, exp.Convert) and cur.args.get("safe")
        ):
            dt = cur.this if isinstance(cur, exp.Convert) else cur.args.get("to")
            src_val = cur.expression if isinstance(cur, exp.Convert) else cur.this
            if isinstance(dt, exp.DataType) and dt.is_type(
                exp.DataType.Type.BOOLEAN, exp.DataType.Type.BIT,
            ):
                return ab or bool(an and eval_const_lit(cur, ctes, vis, smap) in (None, UNKNOWN))
            return _w(src_val, False, ctes, vis, smap, ab=False) or bool(
                an and eval_const_lit(cur, ctes, vis, smap) in (None, UNKNOWN)
            )
        if isinstance(cur, (exp.Cast, exp.Convert)):
            dt = cur.this if isinstance(cur, exp.Convert) else cur.args.get("to")
            src_val = cur.expression if isinstance(cur, exp.Convert) else cur.this
            if isinstance(dt, exp.DataType) and dt.is_type(
                exp.DataType.Type.BOOLEAN, exp.DataType.Type.BIT,
            ):
                return True if ab else _w(src_val, an, ctes, vis, smap, ab=False)
            return _w(src_val, an, ctes, vis, smap, ab=False) if an else False
        if isinstance(cur, exp.Nullif):
            return eval_nullif_null(
                cur, an, ctes, vis, smap, _w,
                lambda u, sm, cc, vv: resolve_col_from_smap(u, sm, cc, vv, hooks=_HOOKS),
                _rsrc, ab=ab,
            )
        if isinstance(cur, exp.Nvl2):
            return _chk((cur.args.get("true"), cur.args.get("false")), an, ctes, vis, smap, ab=ab)
        if isinstance(cur, exp.DecodeCase):
            return _chk(
                decode_branches(cur.expressions, ctes, vis, smap), an, ctes, vis, smap, ab=ab,
            )
        if isinstance(cur, exp.Anonymous):
            return eval_anon_guard(cur, an, ctes, vis, smap, _w, _chk, _chk_res, ab=ab)
        if _MAP_KEYS_CLS is not None and isinstance(cur, _MAP_KEYS_CLS):
            return bool(
                an and cur.this is not None and _w(cur.this, True, ctes, vis, smap, ab=False)
            )
        if isinstance(cur, exp.Func):
            if is_safe_or_try_func(cur):
                return bool(an and eval_const_lit(cur, ctes, vis, smap) in (None, UNKNOWN))
            fname = container_func_name(cur)
            if fname in SAFE_CONTAINER_SCALAR_FUNCS:
                return eval_safe_scalar_null(
                    fname, cur.this, cur.expression, an, ctes, vis, smap, _w,
                )
            if an and any(_has_null_q(q, ctes, vis, smap) for q in param_agg_q_args(cur)):
                return True
            if (
                pt := eval_ch_passthrough_or_window(
                    fname, expr_value_args(cur), an, ctes, vis, smap, _w, ab=ab,
                )
            ) is not None:
                return pt
            if an and is_nullable_agg_expr(
                cur,
                lambda a: _w(a, True, ctes, vis, smap, ab=False),
                lambda c: not is_ch_if_cond_true(c, ctes, vis, smap),
            ):
                return True
        if isinstance(cur, (exp.Subquery, exp.SetOperation, exp.Select)):
            nc = extend_cte_nodes(cur, ctes)
            if an and select_may_be_empty(
                cur, eval_const_lit, eval_const_bool, nc, vis, smap, unpack_fn=_unpack_arr,
            ):
                return True
            if isinstance(cur, exp.Subquery):
                return _w(cur.this, an, nc, vis, smap, ab=ab)
            if isinstance(cur, exp.SetOperation):
                return _chk((cur.this, cur.expression), an, nc, vis, smap, ab=ab)
            return _chk_res(resolve_source_col(cur, None, nc, vis, smap, hooks=_HOOKS), an, ab=ab)
        if isinstance(cur, exp.Values):
            return _chk_res(resolve_source_col(cur, None, ctes, vis, smap, hooks=_HOOKS), an, ab=ab)
        if isinstance(cur, exp.Case):
            return _chk(live_case_branches(cur, ctes, vis, smap), an, ctes, vis, smap, ab=ab)
        if isinstance(cur, exp.If):
            return _eval_if(
                cur.this, cur.args.get("true"), cur.args.get("false"), an, ctes, vis, smap, ab=ab,
            )
        if isinstance(cur, exp.Coalesce):
            args = [a for a in (cur.this, *cur.expressions) if a is not None]
            return bool(args) and (
                any(_w(a, False, ctes, vis, smap, ab=ab) for a in args)
                or (an and all(_w(a, True, ctes, vis, smap, ab=False) for a in args))
            )
        if isinstance(cur, (exp.Greatest, exp.Least)):
            return _chk(
                [a for a in (cur.this, *cur.expressions) if a is not None],
                an, ctes, vis, smap, ab=ab,
            )
        if (
            an
            and _DIRECT_NULL_CONTAINER_CLSES
            and isinstance(cur, _DIRECT_NULL_CONTAINER_CLSES)
            and any(_w(a, True, ctes, vis, smap, ab=False) for a in expr_value_args(cur))
        ):
            return True
        if an and (
            ns_args := null_strict_arg_nodes(
                cur, is_container_fn=lambda x: is_known_container_ctor(unwrap_parens(x)),
            )
        ) is not None and any(_w(a, True, ctes, vis, smap, ab=False) for a in ns_args):
            return True
        if an and eval_const_lit(cur, ctes, vis, smap) is None:
            return True
        return is_unknown_container_or_lambda(cur)

    init_ctes, init_smap = build_ancestor_smap(node)
    return _w(node, allow_null, init_ctes, frozenset(), init_smap)


__all__ = ["is_bool_or_null_expr"]
