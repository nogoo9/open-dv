"""Scalar container-length, LIST_AGGR, and CHOOSE helpers for `_bool_expr_guard.py`.

Strictly Layer 2a: imports only sqlglot, stdlib, `_bool_expr_const`,
`_bool_expr_containers`, `_bool_expr_json`, `_bool_expr_ops`, and `_bool_expr_vocab`.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlglot import exp

from opendv.storage._ast_stmt_rules import unwrap_parens
from opendv.storage._bool_expr_const import (
    UNKNOWN,
    decode_branches,
    eval_const_bool,
    eval_const_lit,
    is_ch_if_cond_true,
    same_scalar_kind,
)
from opendv.storage._bool_expr_containers import (
    BOOL_FUNC_NAMES,
    VALUE_PASSTHROUGH_NAMES,
    SmapType,
    is_array_ctor,
    is_known_container_ctor,
    is_quantile_passthrough_fn,
    is_unknown_container_or_lambda,
)
from opendv.storage._bool_expr_funcs import (
    CheckFn,
    CheckListFn,
    CheckResFn,
    is_nullable_agg_expr,
    null_strict_arg_nodes,
    split_ch_combinators,
)
from opendv.storage._bool_expr_json import (
    eval_json_path_raw,
    is_json_array_literal,
    parse_json_path_arg,
)
from opendv.storage._bool_expr_ops import resolve_container_items, unpack_array_with_emptiness
from opendv.storage._bool_expr_vocab import (
    NON_NULL_BOOL_FUNCS,
    NULLABLE_EMPTY_CONTAINER_SCALAR_FUNCS,
    SAFE_CONTAINER_SCALAR_FUNCS,
    classify_list_aggr,
    int_float_lossy,
    same_const_val,
)

_HOOKS = (eval_const_bool, eval_const_lit)
_LIST_AGGR_ANON: frozenset[str] = frozenset({
    "LIST_AGGR", "ARRAY_AGGR", "LIST_AGGREGATE", "ARRAY_AGGREGATE", "AGGREGATE",
})
_JSON_LEN_FUNCS = frozenset({"JSON_ARRAY_LENGTH", "JSONB_ARRAY_LENGTH"})
_JSON_KEYS_FUNCS = frozenset({"JSON_KEYS", "JSONB_OBJECT_KEYS", "JSON_OBJECT_KEYS", "OBJECT_KEYS"})
_JSON_TYPE_FUNCS = frozenset({"JSON_TYPE", "JSON_TYPEOF", "JSONB_TYPEOF"})
_WINDOW_OFFSET_NAMES = frozenset({"NTH_VALUE", "LAG", "LEAD", "LAGINFRAME", "LEADINFRAME"})


def eval_safe_scalar_null(
    fname: str,
    arg0: exp.Expr | None,
    arg1: exp.Expr | None,
    an: bool,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    w_fn: CheckFn,
) -> bool:
    if not an or arg0 is None:
        return False
    is_k, is_l = fname in _JSON_KEYS_FUNCS, fname in _JSON_LEN_FUNCS
    if is_k or is_l or fname in _JSON_TYPE_FUNCS:
        steps: list[str | int | None] = []
        if arg1 is not None:
            if w_fn(arg1, True, ctes, vis, smap, ab=False) or (
                steps_p := parse_json_path_arg(arg1, eval_const_lit, ctes, vis, smap)
            ) is None:
                return True
            steps = steps_p
        if arg1 is not None or is_k:
            raw = eval_json_path_raw(arg0, steps, ctes, vis, smap, eval_lit_fn=eval_const_lit)
            if raw is None:
                return True
            if is_k:
                return any(not isinstance(v, dict) for v in raw)
            if is_l:
                return any(not isinstance(v, list) for v in raw)
            return any(v is None for v in raw)
        if is_l:
            return not is_json_array_literal(arg0, eval_const_lit, ctes, vis, smap)
        return w_fn(arg0, True, ctes, vis, smap, ab=False)
    if fname in NULLABLE_EMPTY_CONTAINER_SCALAR_FUNCS:
        its, has_empty = unpack_array_with_emptiness(arg0, ctes, vis, smap, hooks=_HOOKS)
        if (not its and has_empty) or (
            its and all(w_fn(x, True, cc, nv, sm, ab=False) for x, cc, nv, sm in its)
        ):
            return True
        if fname.endswith(("_POSITION", "_INDEXOF")) and arg1 is not None:
            v1 = eval_const_lit(arg1, ctes, vis, smap)
            if v1 is None:
                return True
            if its and not any(
                v1 is not UNKNOWN and same_const_val(v1, eval_const_lit(x, cc, nv, sm))
                for x, cc, nv, sm in its
            ):
                return True
        return False
    return w_fn(arg0, True, ctes, vis, smap, ab=False)


def _check_skew_kurt(
    arr_expr: exp.Expr | None,
    min_n: int,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    w_fn: CheckFn,
) -> bool:
    res = resolve_container_items(arr_expr, ctes, vis, smap, hooks=_HOOKS)
    if not res:
        return True
    for ex, ec, nv, sm in res:
        if not is_array_ctor(ex_u := unwrap_parens(ex)) or ex_u is None:
            return True
        vals: list[object] = []
        for el in ex_u.expressions:
            v = eval_const_lit(el, ec, nv, sm)
            if v is None:
                continue
            if v is UNKNOWN or w_fn(el, True, ec, nv, sm, ab=False):
                return True
            vals.append(v)
        if (
            len(vals) < min_n
            or any(int_float_lossy(v, vals[0]) for v in vals[1:])
            or all(same_const_val(v, vals[0]) for v in vals[1:])
        ):
            return True
    return False


def eval_list_aggr(
    cur: exp.Anonymous,
    an: bool,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    w_fn: CheckFn,
    chk_res_fn: CheckResFn,
    *,
    ab: bool = True,
) -> bool:
    exps = cur.expressions
    if len(exps) < 2:
        return ab or an
    kind = classify_list_aggr(exps[1])
    if kind == "unknown":
        return True
    if kind == "bool" and (ab or not an):
        return ab
    if kind == "list":
        if not an:
            return False
        res = resolve_container_items(exps[0], ctes, vis, smap, hooks=_HOOKS)
        return not res or any(
            not is_array_ctor(ex_u := unwrap_parens(ex))
            or ex_u is None
            or not ex_u.expressions
            for ex, *_ in res
        )
    if kind in ("skewness", "kurtosis"):
        if not an:
            return False
        return _check_skew_kurt(
            exps[0], 3 if kind == "skewness" else 4, ctes, vis, smap, w_fn,
        )
    its, has_empty = unpack_array_with_emptiness(
        exps[0], ctes, vis, smap, hooks=_HOOKS,
    )
    if kind == "passthrough":
        return chk_res_fn(its, an, ab=ab) or bool(an and not its and has_empty)
    if not an:
        return False
    if kind == "min2_scalar":
        return bool(
            (not its and has_empty)
            or (
                its
                and sum(1 for x, cc, nv, sm in its if not w_fn(x, True, cc, nv, sm, ab=False)) < 2
            )
        )
    return bool(
        (not its and has_empty)
        or (its and all(w_fn(x, True, cc, nv, sm, ab=False) for x, cc, nv, sm in its))
    )


def eval_choose(
    cur: exp.Anonymous,
    an: bool,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    w_fn: CheckFn,
    chk_fn: CheckListFn,
    *,
    ab: bool = True,
) -> bool:
    exps = cur.expressions
    if len(exps) < 2:
        return an
    iv = eval_const_lit(exps[0], ctes, vis, smap)
    vals: list[exp.Expr | None] = list(exps[1:])
    if iv is None:
        return an
    if isinstance(iv, int) and not isinstance(iv, bool):
        return w_fn(vals[iv - 1], an, ctes, vis, smap, ab=ab) if 1 <= iv <= len(vals) else an
    return chk_fn([*vals, exp.Null()], an, ctes, vis, smap, ab=ab)


def eval_ch_passthrough_or_window(
    fn: str,
    args: Sequence[exp.Expr],
    an: bool,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    w_fn: CheckFn,
    *,
    ab: bool = True,
) -> bool | None:
    base_fn, has_or_null, if_cnt, has_or_default = split_ch_combinators(
        fn, _WINDOW_OFFSET_NAMES | BOOL_FUNC_NAMES,
    )
    is_bool = base_fn in BOOL_FUNC_NAMES
    is_lag = base_fn != "NTH_VALUE" and base_fn in _WINDOW_OFFSET_NAMES
    is_q = is_quantile_passthrough_fn(base_fn)
    is_cont_q = base_fn in ("QUANTILE_CONT", "PERCENTILE_CONT")
    if not is_bool and (
        not args
        or (not is_lag and not is_q and not is_cont_q and base_fn not in VALUE_PASSTHROUGH_NAMES)
    ):
        return None
    if is_bool and ab:
        return True
    vargs = args[:-if_cnt] if if_cnt else args

    def _w(x: exp.Expr | None, a: bool = an, b: bool = ab) -> bool:
        return w_fn(x, a, ctes, vis, smap, ab=b)

    no_dflt = not (has_or_default and not has_or_null)
    if an and (
        has_or_null
        or (
            if_cnt
            and no_dflt
            and any(
                _w(c, True, False) or not is_ch_if_cond_true(c, ctes, vis, smap)
                for c in args[-if_cnt:]
            )
        )
    ):
        return True
    if is_bool:
        return bool(
            an
            and no_dflt
            and base_fn not in NON_NULL_BOOL_FUNCS
            and any(_w(a, True, False) for a in vargs)
        )
    if not vargs:
        return an
    if not is_lag and base_fn != "NTH_VALUE":
        if _w(vargs[0], an, False if is_cont_q else ab):
            return True
        if not an or not (
            base_fn.startswith(("ARG_M", "ARGM"))
            or base_fn in ("MAX_BY", "MIN_BY")
            or is_q
            or is_cont_q
        ):
            return False
        q_args = vargs if (is_cont_q and len(vargs) == 1) else vargs[1:]
        for a in q_args:
            if _w(a, True, False) or (
                (is_q or is_cont_q)
                and any(
                    w_fn(x, True, cc, nv, sm, ab=False)
                    for x, cc, nv, sm in unpack_array_with_emptiness(
                        a, ctes, vis, smap, hooks=_HOOKS,
                    )[0]
                )
            ):
                return True
        return False
    if is_lag and len(vargs) >= 3:
        return _w(vargs[0]) or _w(vargs[2]) or bool(an and _w(vargs[1], True, False))
    if _w(vargs[0]):
        return True
    off_e = vargs[1] if len(vargs) >= 2 else None
    return bool(an and no_dflt and eval_const_lit(off_e, ctes, vis, smap) != (0 if is_lag else 1))


def eval_anon_guard(
    cur: exp.Anonymous,
    an: bool,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    w_fn: CheckFn,
    chk_fn: CheckListFn,
    chk_res_fn: CheckResFn,
    *,
    ab: bool = True,
) -> bool:
    un = cur.name.upper()
    exps = cur.expressions
    if un in ("IFF", "IIF", "IF") and len(exps) >= 2:
        f_br = exps[2] if len(exps) >= 3 else None
        cb = eval_const_bool(exps[0], ctes, vis, smap)
        if cb is False:
            return w_fn(f_br, an, ctes, vis, smap, ab=ab) if f_br is not None else an
        if cb is True:
            return w_fn(exps[1], an, ctes, vis, smap, ab=ab)
        return chk_fn(
            [exps[1], f_br if f_br is not None else exp.Null()], an, ctes, vis, smap, ab=ab,
        )
    if un == "NVL2":
        return chk_fn(list(exps[1:3]), an, ctes, vis, smap, ab=ab)
    if un == "DECODE":
        return chk_fn(decode_branches(exps, ctes, vis, smap), an, ctes, vis, smap, ab=ab)
    if un in _LIST_AGGR_ANON:
        return eval_list_aggr(cur, an, ctes, vis, smap, w_fn, chk_res_fn, ab=ab)
    if un == "CHOOSE":
        return eval_choose(cur, an, ctes, vis, smap, w_fn, chk_fn, ab=ab)
    if un == "CONSTANT_OR_NULL":
        if not exps:
            return ab or an
        return w_fn(exps[0], an, ctes, vis, smap, ab=ab) or bool(
            an and any(w_fn(a, True, ctes, vis, smap, ab=False) for a in exps[1:])
        )
    if un == "TRY" and exps:
        return w_fn(exps[0], False, ctes, vis, smap, ab=ab) or bool(
            an and eval_const_lit(cur, ctes, vis, smap) in (None, UNKNOWN)
        )
    if un.startswith(("TRY_", "SAFE_")):
        return (ab and un in BOOL_FUNC_NAMES) or bool(
            an and eval_const_lit(cur, ctes, vis, smap) in (None, UNKNOWN)
        )
    if (
        pt := eval_ch_passthrough_or_window(un, exps, an, ctes, vis, smap, w_fn, ab=ab)
    ) is not None:
        return pt
    if un == "VALUES":
        rows: list[exp.Expr | None] = [
            r.expressions[0] if isinstance(r, exp.Tuple) and len(r.expressions) == 1 else r
            for r in exps
            if not isinstance(r, exp.Tuple) or len(r.expressions) == 1
        ]
        return chk_fn(rows, an, ctes, vis, smap, ab=ab)
    if un in SAFE_CONTAINER_SCALAR_FUNCS:
        return eval_safe_scalar_null(
            un,
            exps[0] if exps else None,
            exps[1] if len(exps) >= 2 else None,
            an, ctes, vis, smap, w_fn,
        )
    if an and un in ("MAP_KEYS", "MAP_FROM_ENTRIES", "MAP", "RANGE", "GENERATE_SERIES"):
        return any(w_fn(a, True, ctes, vis, smap, ab=False) for a in exps)
    if an and is_nullable_agg_expr(
        cur,
        lambda a: w_fn(a, True, ctes, vis, smap, ab=False),
        lambda c: not is_ch_if_cond_true(c, ctes, vis, smap),
    ):
        return True
    if an and (
        ns_args := null_strict_arg_nodes(
            cur, is_container_fn=lambda x: is_known_container_ctor(unwrap_parens(x)),
        )
    ) is not None and any(w_fn(a, True, ctes, vis, smap, ab=False) for a in ns_args):
        return True
    return is_unknown_container_or_lambda(cur)


def live_case_branches(
    cur: exp.Case,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
) -> list[exp.Expr | None]:
    ifs = [br for br in (cur.args.get("ifs") or ()) if isinstance(br, exp.If)]
    dflt = cur.args.get("default")
    subj = cur.args.get("this")
    live_brs: list[exp.Expr | None] = []
    if subj is None:
        for br in ifs:
            cb = eval_const_bool(br.this, ctes, vis, smap)
            if cb is False:
                continue
            live_brs.append(br.args.get("true"))
            if cb is True:
                return live_brs
    else:
        sv = eval_const_lit(subj, ctes, vis, smap)
        for br in ifs:
            if sv is None:
                continue
            if (
                sv is not UNKNOWN
                and (wv := eval_const_lit(br.this, ctes, vis, smap)) is not UNKNOWN
            ):
                if wv is None:
                    continue
                if same_scalar_kind(sv, wv) and not int_float_lossy(sv, wv):
                    if sv != wv:
                        continue
                    live_brs.append(br.args.get("true"))
                    return live_brs
            live_brs.append(br.args.get("true"))
    live_brs.append(dflt if dflt is not None else exp.Null())
    return live_brs


__all__ = [
    "eval_anon_guard",
    "eval_ch_passthrough_or_window",
    "eval_choose",
    "eval_list_aggr",
    "eval_safe_scalar_null",
    "live_case_branches",
]
