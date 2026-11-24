"""Array concat, intersect, slice, range, and resize helpers for `_bool_expr_transforms.py`.

Strictly Layer 2a: imports only sqlglot, stdlib, `_ast_stmt_rules`,
`_bool_expr_containers`, `_bool_expr_lambdas`, `_bool_expr_maps`, and
`_bool_expr_vocab`.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable

from sqlglot import exp

from opendv.storage._ast_stmt_rules import unwrap_parens
from opendv.storage._bool_expr_containers import ResolvedItem, SmapType, is_array_ctor
from opendv.storage._bool_expr_lambdas import is_known_container_ctor
from opendv.storage._bool_expr_maps import extract_map_kv_pairs, is_empty_container_ctor
from opendv.storage._bool_expr_vocab import UNKNOWN, same_const_val

EvalBoolFn = Callable[
    [exp.Expr | None, dict[str, exp.CTE], frozenset[str], SmapType | None], bool | None
]
EvalLitFn = Callable[
    [exp.Expr | None, dict[str, exp.CTE], frozenset[str], SmapType | None], object
]
EvalHooks = tuple[EvalBoolFn, EvalLitFn]
ResFn = Callable[[exp.Expr | None], list[ResolvedItem]]


def apply_concat_transform(
    args: tuple[exp.Expr | None, ...],
    res_fn: ResFn,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
) -> list[ResolvedItem]:
    all_res = [res_fn(a) for a in args if a is not None]
    prod = 1
    for ra in all_res:
        prod *= len(ra)
    if all_res and 1 <= prod <= 32:
        if all(is_array_ctor(unwrap_parens(it[0])) for ra in all_res for it in ra):
            arr_out: list[ResolvedItem] = []
            for combo in itertools.product(*all_res):
                merged_els = [
                    el
                    for ex, *_ in combo
                    if (u := unwrap_parens(ex)) is not None
                    for el in u.expressions
                ]
                arr_out.append((exp.Array(expressions=merged_els), ctes, vis, smap))
            return arr_out
        if all(
            extract_map_kv_pairs(unwrap_parens(it[0])) is not None
            for ra in all_res
            for it in ra
        ):
            map_out: list[ResolvedItem] = []
            for combo in itertools.product(*all_res):
                all_ks: list[exp.Expr] = []
                all_vs: list[exp.Expr] = []
                for ex, *_ in combo:
                    kv = extract_map_kv_pairs(unwrap_parens(ex))
                    assert kv is not None
                    all_ks.extend(kv[0])
                    all_vs.extend(kv[1])
                m_node = exp.Map(
                    keys=exp.Array(expressions=all_ks),
                    values=exp.Array(expressions=all_vs),
                )
                map_out.append((m_node, ctes, vis, smap))
            return map_out
    has_non_empty = any(
        ra
        and all(
            is_known_container_ctor(u := unwrap_parens(ex))
            and not is_empty_container_ctor(u)
            for ex, *_ in ra
        )
        for ra in all_res
    )
    combined = [it for ra in all_res for it in ra]
    if has_non_empty:
        combined = [it for it in combined if not is_empty_container_ctor(unwrap_parens(it[0]))]
    return combined


def apply_intersect_transform(
    args: tuple[exp.Expr | None, ...],
    res_fn: ResFn,
    hooks: EvalHooks | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
) -> list[ResolvedItem]:
    all_res = [res_fn(a) for a in args if a is not None]
    if any(isinstance(unwrap_parens(it[0]), exp.Null) for ra in all_res for it in ra):
        return [(exp.Null(), ctes, vis, smap)]
    if all_res and hooks and all(len(ra) == 1 for ra in all_res):
        u_arrs = [unwrap_parens(ra[0][0]) for ra in all_res]
        if all(is_array_ctor(u) and u is not None for u in u_arrs):
            eval_lit = hooks[1]
            val_lists = [
                [
                    eval_lit(el, ra[0][1], ra[0][2], ra[0][3])
                    for el in (u.expressions if u is not None else ())
                ]
                for u, ra in zip(u_arrs, all_res, strict=True)
            ]
            if all(all(v is not UNKNOWN for v in vl) for vl in val_lists):
                a0 = u_arrs[0]
                assert a0 is not None
                common = [
                    el
                    for el, v0 in zip(a0.expressions, val_lists[0], strict=True)
                    if all(any(same_const_val(v0, vk) for vk in vl) for vl in val_lists[1:])
                ]
                return [(exp.Array(expressions=common), ctes, vis, smap)]
    return [(exp.Array(expressions=[]), ctes, vis, smap)] + [
        it for ra in all_res for it in ra
    ]


def apply_slice_transform(
    kind: str,
    args: tuple[exp.Expr | None, ...],
    res_fn: ResFn,
    hooks: EvalHooks | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
) -> list[ResolvedItem]:
    s_expr = args[1] if len(args) >= 2 else None
    e_expr = args[2] if len(args) >= 3 else None
    step_expr = args[3] if len(args) >= 4 else None
    sv = hooks[1](s_expr, ctes, vis, smap) if (s_expr is not None and hooks) else (
        0 if kind == "slice_0b" else 1
    )
    ev = hooks[1](e_expr, ctes, vis, smap) if (e_expr is not None and hooks) else UNKNOWN
    step_v = hooks[1](step_expr, ctes, vis, smap) if (step_expr is not None and hooks) else 1
    if sv is None or (e_expr is not None and ev is None) or step_v is None:
        return [(exp.Null(), ctes, vis, smap)]
    step_ok = isinstance(step_v, int) and not isinstance(step_v, bool) and step_v != 0
    sl_out: list[ResolvedItem] = []
    for ex, ec, ev2, es in res_fn(args[0]):
        ex_u = unwrap_parens(ex)
        if isinstance(ex_u, exp.Null):
            sl_out.append((ex_u, ec, ev2, es))
        elif is_array_ctor(ex_u) and ex_u is not None:
            n = len(ex_u.expressions)
            if step_v == 0:
                sl_out.extend(((exp.Null(), ec, ev2, es), (exp.Array(expressions=[]), ec, ev2, es)))
            elif (
                not step_ok
                or (s_expr is not None and sv is UNKNOWN)
                or (e_expr is not None and ev is UNKNOWN)
            ):
                sl_out.extend(((exp.Array(expressions=[]), ec, ev2, es), (ex_u, ec, ev2, es)))
            elif (
                isinstance(step_v, int)
                and not isinstance(step_v, bool)
                and (s_expr is None or (isinstance(sv, int) and not isinstance(sv, bool)))
                and (e_expr is None or (isinstance(ev, int) and not isinstance(ev, bool)))
            ):
                sv_i = sv if (isinstance(sv, int) and not isinstance(sv, bool)) else None
                ev_i = ev if (isinstance(ev, int) and not isinstance(ev, bool)) else None
                if kind == "slice_0b":
                    if step_v > 0:
                        lo = 0 if sv_i is None else max(0, n + sv_i if sv_i < 0 else sv_i)
                        hi = n if ev_i is None else min(n, n + ev_i if ev_i < 0 else ev_i)
                        sliced = list(ex_u.expressions[lo:hi:step_v]) if lo < hi else []
                    else:
                        sliced = []
                else:
                    if step_v > 0:
                        s1 = 1 if sv_i is None else (n + 1 + sv_i if sv_i < 0 else sv_i)
                        e1 = n if ev_i is None else (n + 1 + ev_i if ev_i < 0 else ev_i)
                        lo, hi = max(1, s1), min(n, e1)
                        sliced = list(ex_u.expressions[lo - 1 : hi : step_v]) if lo <= hi else []
                    else:
                        s1 = n if sv_i is None else (n + 1 + sv_i if sv_i < 0 else sv_i)
                        e1 = 1 if ev_i is None else (n + 1 + ev_i if ev_i < 0 else ev_i)
                        s1_c, e1_c = min(n, s1), max(1, e1)
                        if s1_c < e1_c or s1_c < 1 or e1_c > n:
                            sliced = []
                        else:
                            sliced = [
                                ex_u.expressions[i] for i in range(s1_c - 1, e1_c - 2, step_v)
                            ]
                sl_out.append((exp.Array(expressions=sliced), ec, ev2, es))
            else:
                sl_out.extend(((exp.Array(expressions=[]), ec, ev2, es), (ex_u, ec, ev2, es)))
        else:
            sl_out.append((ex, ec, ev2, es))
    return sl_out


_GENERATE_SERIES_CLS = getattr(exp, "GenerateSeries", None)


def apply_range_transform(
    kind: str,
    args: tuple[exp.Expr | None, ...],
    hooks: EvalHooks | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
) -> list[ResolvedItem]:
    sv = hooks[1](args[0], ctes, vis, smap) if (args[0] is not None and hooks) else UNKNOWN
    ev = (
        hooks[1](args[1], ctes, vis, smap)
        if (len(args) >= 2 and args[1] is not None and hooks)
        else UNKNOWN
    )
    step_expr = args[2] if len(args) >= 3 else None
    step_v = hooks[1](step_expr, ctes, vis, smap) if (step_expr is not None and hooks) else 1
    if sv is None or ev is None or step_v is None:
        return [(exp.Null(), ctes, vis, smap)]
    if step_v == 0:
        return [(exp.Array(expressions=[]), ctes, vis, smap)]
    if all(isinstance(x, int) and not isinstance(x, bool) for x in (sv, ev, step_v)):
        assert isinstance(sv, int) and isinstance(ev, int) and isinstance(step_v, int)
        stop = (ev if kind == "range_excl" else ev + 1) if step_v > 0 else (
            ev if kind == "range_excl" else ev - 1
        )
        if (step_v > 0 and stop <= sv) or (step_v < 0 and stop >= sv):
            return [(exp.Array(expressions=[]), ctes, vis, smap)]
        span = (stop - sv) if step_v > 0 else (sv - stop)
        abs_step = step_v if step_v > 0 else -step_v
        cnt = (span + abs_step - 1) // abs_step
        if cnt <= 64:
            return [(
                exp.Array(expressions=[exp.Literal.number(i) for i in range(sv, stop, step_v)]),
                ctes, vis, smap,
            )]
        if _GENERATE_SERIES_CLS is not None:
            return [(
                _GENERATE_SERIES_CLS(
                    start=exp.Literal.number(sv),
                    end=exp.Literal.number(stop),
                    step=exp.Literal.number(step_v),
                    is_end_exclusive=True,
                ),
                ctes, vis, smap,
            )]
    return [(exp.Array(expressions=[]), ctes, vis, smap)]


def apply_resize_transform(
    args: tuple[exp.Expr | None, ...],
    res_fn: ResFn,
    hooks: EvalHooks | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
) -> list[ResolvedItem]:
    res0 = res_fn(args[0])
    sz_v = (
        hooks[1](args[1], ctes, vis, smap)
        if (len(args) >= 2 and args[1] and hooks)
        else UNKNOWN
    )
    if sz_v is None:
        return [(exp.Null(), ctes, vis, smap)]
    if isinstance(sz_v, int) and not isinstance(sz_v, bool) and sz_v <= 0:
        return [(exp.Array(expressions=[]), ctes, vis, smap)]
    if isinstance(sz_v, int) and not isinstance(sz_v, bool) and sz_v <= 64 and res0 and all(
        is_array_ctor(unwrap_parens(ex)) for ex, *_ in res0
    ):
        fill_e = args[2] if len(args) >= 3 and args[2] is not None else exp.Null()
        rz_out: list[ResolvedItem] = []
        for ex, ec, ev, es in res0:
            rx_u = unwrap_parens(ex)
            assert rx_u is not None
            els = list(rx_u.expressions)
            rz_out.append((
                exp.Array(expressions=els[:sz_v] + [fill_e] * max(0, sz_v - len(els))),
                ec, ev, es,
            ))
        return rz_out
    if len(args) >= 3 and args[2] is not None:
        return [*res0, (exp.Array(expressions=[args[2]]), ctes, vis, smap)]
    if (
        isinstance(sz_v, int)
        and not isinstance(sz_v, bool)
        and res0
        and all(is_array_ctor(unwrap_parens(ex)) for ex, *_ in res0)
        and any(
            len(rx_u.expressions) < sz_v
            for ex, *_ in res0
            if (rx_u := unwrap_parens(ex)) is not None
        )
    ):
        return [*res0, (exp.Array(expressions=[exp.Null()]), ctes, vis, smap)]
    return res0


def extract_array_index_items(
    els: list[exp.Expr],
    mode: str,
    has_idx_expr: bool,
    idx_val: object,
    ec: dict[str, exp.CTE],
    ev: frozenset[str],
    es: SmapType | None,
) -> tuple[list[ResolvedItem], bool]:
    n_els = len(els)
    if n_els == 0:
        return [], True
    if mode == "first":
        return [(els[0], ec, ev, es)], False
    if mode == "last":
        return [(els[-1], ec, ev, es)], False
    if has_idx_expr and idx_val is None:
        return [(exp.Null(), ec, ev, es)], False
    if isinstance(idx_val, int) and not isinstance(idx_val, bool):
        if mode in ("anon_1b", "bracket_1b", "ch_1b"):
            if 1 <= idx_val <= n_els:
                return [(els[idx_val - 1], ec, ev, es)], False
            if -n_els <= idx_val <= -1:
                return [(els[n_els + idx_val], ec, ev, es)], False
            if mode == "ch_1b":
                return [(el, ec, ev, es) for el in els], True
            return [], True
        if mode == "anon_0b":
            if 0 <= idx_val < n_els:
                return [(els[idx_val], ec, ev, es)], False
            return [], True
        if mode == "bracket_0b":
            if 0 <= idx_val < n_els:
                return [(els[idx_val], ec, ev, es)], False
            if idx_val == -1 or idx_val < -(n_els + 1) or idx_val >= n_els:
                return [], True
            return [(el, ec, ev, es) for el in els], False
    return [(el, ec, ev, es) for el in els], False


def is_safe_window_spec(spec: exp.Expr | None) -> bool:
    if spec is None:
        return True
    if not isinstance(spec, exp.WindowSpec):
        return False
    excl = spec.args.get("exclude")
    if excl is not None:
        excl_s = (excl.name if isinstance(excl, (exp.Var, exp.Identifier)) else str(excl)).upper()
        if excl_s not in ("TIES", "NO OTHERS"):
            return False
    start = spec.args.get("start")
    start_side = (spec.args.get("start_side") or "").upper()
    end = spec.args.get("end")
    end_side = (spec.args.get("end_side") or "").upper()
    start_ok = (start == "CURRENT ROW") or (
        start_side == "PRECEDING"
        and (start == "UNBOUNDED" or (isinstance(start, exp.Literal) and not start.is_string))
    )
    end_ok = (
        (end is None)
        or (end == "CURRENT ROW")
        or (
            end_side == "FOLLOWING"
            and (end == "UNBOUNDED" or (isinstance(end, exp.Literal) and not end.is_string))
        )
    )
    return bool(start_ok and end_ok)


__all__ = [
    "apply_concat_transform",
    "apply_intersect_transform",
    "apply_range_transform",
    "apply_resize_transform",
    "apply_slice_transform",
    "extract_array_index_items",
    "is_safe_window_spec",
]
