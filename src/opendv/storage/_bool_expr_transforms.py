"""Container-transform dispatch and map/zip helpers for `_bool_expr_ops.py`.

Strictly Layer 2a: imports only sqlglot, stdlib, `_ast_stmt_rules`,
`_bool_expr_containers`, `_bool_expr_extract`, `_bool_expr_lambdas`, and
`_bool_expr_vocab`.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlglot import exp

from opendv.storage._ast_stmt_rules import unwrap_parens
from opendv.storage._bool_expr_containers import (
    ResolvedItem,
    SmapType,
    is_array_ctor,
    is_known_container_ctor,
    is_single_row_const_rel,
    is_unknown_container_or_lambda,
)
from opendv.storage._bool_expr_lambdas import (
    apply_lambda_filter,
    apply_lambda_reduce,
    apply_lambda_transform,
)
from opendv.storage._bool_expr_maps import (
    extract_map_keys,
    extract_map_values,
    map_from_entries_kv,
    map_to_entry_structs,
    match_map_by_key_expr,
    unpack_container_ctor,
)
from opendv.storage._bool_expr_quantiles import (
    apply_seq_transform,
    is_array_or_quantile_target,
    unwrap_quantile_target,
)
from opendv.storage._bool_expr_slicing import (
    apply_concat_transform,
    apply_intersect_transform,
    apply_range_transform,
    apply_resize_transform,
    apply_slice_transform,
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
ResFn = Callable[[exp.Expr | None], list[ResolvedItem]]

_MAP_CLASSES: tuple[type[exp.Expr], ...] = tuple(
    c for c in (getattr(exp, "Map", None), getattr(exp, "VarMap", None)) if c is not None
)


def is_container_item(ex: exp.Expr | None) -> bool:
    u = unwrap_parens(ex)
    return bool(
        is_array_ctor(u)
        or is_known_container_ctor(u)
        or bool(unpack_container_ctor(u, None))
        or is_unknown_container_or_lambda(u)
        or is_array_or_quantile_target(u)
    )


def apply_map_to_entries(args: tuple[exp.Expr | None, ...], res_fn: ResFn) -> list[ResolvedItem]:
    out: list[ResolvedItem] = []
    for ex, ec, ev, es in res_fn(args[0]):
        ex_u = unwrap_parens(ex)
        if isinstance(ex_u, exp.Null):
            out.append((ex_u, ec, ev, es))
        elif (structs := map_to_entry_structs(ex)) is not None:
            out.append((exp.Array(expressions=structs), ec, ev, es))
        elif is_unknown_container_or_lambda(ex):
            out.append((ex, ec, ev, es))
    return out


def apply_zip_transform(
    args: tuple[exp.Expr | None, ...],
    res_fn: ResFn,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
) -> list[ResolvedItem]:
    zip_fields: list[exp.Expr] = []
    has_unk = False
    for za in args:
        for ex, *_ in res_fn(za):
            ex_u = unwrap_parens(ex)
            if (els := unpack_container_ctor(ex_u, None)):
                zip_fields.extend(els)
            elif is_unknown_container_or_lambda(ex_u):
                has_unk = True
    if zip_fields:
        return [(exp.Array(expressions=[exp.Struct(expressions=zip_fields)]), ctes, vis, smap)]
    return [(exp.Boolean(this=True), ctes, vis, smap)] if has_unk else []


def wrap_array_arg(
    arg: exp.Expr | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    depth: int,
    hooks: EvalHooks | None,
    resolve_fn: ResolveItemsFn,
) -> list[ResolvedItem]:
    if arg is None:
        return []
    res = resolve_fn(arg, ctes, vis, smap, depth + 1, hooks)
    if any(
        is_container_item(ex) or unwrap_quantile_target(unwrap_parens(ex)) is not None
        for ex, *_ in res
    ):
        return res
    return [(exp.Array(expressions=[arg]), ctes, vis, smap)]


def unwrap_resolved_container(
    ex: exp.Expr,
    ec: dict[str, exp.CTE],
    ev: frozenset[str],
    es: SmapType | None,
    depth: int,
    hooks: EvalHooks | None,
    resolve_fn: ResolveItemsFn,
) -> list[ResolvedItem]:
    ex_u = unwrap_parens(ex)
    if ex_u is None or depth > 16:
        return []
    if is_known_container_ctor(ex_u) or isinstance(ex_u, (exp.Boolean, exp.Literal, exp.Null)):
        return [(ex_u, ec, ev, es)]
    return resolve_fn(ex_u, ec, ev, es, depth + 1, hooks)


def apply_transform(
    kind: str,
    args: tuple[exp.Expr | None, ...],
    fk: str | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    depth: int,
    hooks: EvalHooks | None,
    resolve_fn: ResolveItemsFn,
) -> list[ResolvedItem]:
    def _res(x: exp.Expr | None, d: int = 1) -> list[ResolvedItem]:
        return resolve_fn(x, ctes, vis, smap, depth + d, hooks)

    if kind == "empty":
        return []
    if kind in ("pass", "reverse", "pop_front", "pop_back", "reorder"):
        return apply_seq_transform(kind, _res(args[0])) if args else []
    if kind == "dpipe":
        li, ri = _res(args[0]), _res(args[1])
        if any(is_container_item(ex) for ex, *_ in (li + ri)):
            return wrap_array_arg(
                args[0], ctes, vis, smap, depth, hooks, resolve_fn,
            ) + wrap_array_arg(args[1], ctes, vis, smap, depth, hooks, resolve_fn)
        return [(exp.DPipe(this=args[0], expression=args[1]), ctes, vis, smap)]
    if kind == "concat":
        return apply_concat_transform(args, _res, ctes, vis, smap)
    if kind == "intersect":
        return apply_intersect_transform(args, _res, hooks, ctes, vis, smap)
    if kind in ("slice_1b", "slice_0b"):
        return apply_slice_transform(kind, args, _res, hooks, ctes, vis, smap)
    if kind in ("range_excl", "range_incl"):
        return apply_range_transform(kind, args, hooks, ctes, vis, smap)
    if kind == "append":
        res = _res(args[0])
        if not args[1]:
            return res
        if len(res) == 1 and is_array_ctor(ex_u := unwrap_parens(res[0][0])) and ex_u is not None:
            return [(
                exp.Array(expressions=[*ex_u.expressions, args[1]]),
                res[0][1], res[0][2], res[0][3],
            )]
        return [*res, (exp.Array(expressions=[args[1]]), ctes, vis, smap)]
    if kind == "prepend":
        r0 = _res(args[0]) if args[0] is not None else []
        r1 = _res(args[1]) if len(args) >= 2 and args[1] is not None else []
        if (
            args[0] is not None
            and len(r1) == 1
            and is_array_ctor(u1 := unwrap_parens(r1[0][0]))
            and u1 is not None
            and not is_container_item(args[0])
        ):
            return [(
                exp.Array(expressions=[args[0], *u1.expressions]), r1[0][1], r1[0][2], r1[0][3],
            )]
        if (
            len(args) >= 2
            and args[1] is not None
            and len(r0) == 1
            and is_array_ctor(u0 := unwrap_parens(r0[0][0]))
            and u0 is not None
            and not is_container_item(args[1])
        ):
            return [(
                exp.Array(expressions=[args[1], *u0.expressions]), r0[0][1], r0[0][2], r0[0][3],
            )]
        return wrap_array_arg(
            args[0], ctes, vis, smap, depth, hooks, resolve_fn,
        ) + wrap_array_arg(args[1], ctes, vis, smap, depth, hooks, resolve_fn)
    if kind == "unnest":
        u_out: list[ResolvedItem] = []
        for ua in args:
            for ex, ec, ev, es in _res(ua):
                if (els := unpack_container_ctor(unwrap_parens(ex), None)):
                    for el in els:
                        u_out.extend(
                            unwrap_resolved_container(el, ec, ev, es, depth + 1, hooks, resolve_fn)
                        )
                elif is_unknown_container_or_lambda(ex):
                    u_out.append((ex, ec, ev, es))
        return u_out
    if kind == "list_aggr_list":
        la_out: list[ResolvedItem] = []
        for ex, ec, ev, es in _res(args[0]):
            ex_u = unwrap_parens(ex)
            if is_array_ctor(ex_u) and ex_u is not None and not ex_u.expressions:
                la_out.append((exp.Null(), ec, ev, es))
            else:
                la_out.append((ex, ec, ev, es))
        return la_out
    if kind == "flatten":
        f_out: list[ResolvedItem] = []
        for ex, ec, ev, es in _res(args[0]):
            ex_u = unwrap_parens(ex)
            if isinstance(ex_u, exp.Null):
                f_out.append((ex_u, ec, ev, es))
            elif (els := unpack_container_ctor(ex_u, None)):
                for sub_el in els:
                    f_out.extend(resolve_fn(sub_el, ec, ev, es, depth + 2, hooks))
            elif is_unknown_container_or_lambda(ex):
                f_out.append((ex, ec, ev, es))
        return f_out
    if kind == "map_entries":
        m_out: list[ResolvedItem] = []
        for ex, ec, ev, es in _res(args[0]):
            ex_u = unwrap_parens(ex)
            if isinstance(ex_u, exp.Null):
                m_out.append((ex_u, ec, ev, es))
            elif (kv := map_from_entries_kv(ex)) is not None:
                m_out.append((
                    exp.Map(keys=exp.Array(expressions=kv[0]), values=exp.Array(expressions=kv[1])),
                    ec, ev, es,
                ))
            elif is_unknown_container_or_lambda(ex):
                m_out.append((ex, ec, ev, es))
        return m_out
    if kind == "map_to_entries":
        return apply_map_to_entries(args, _res)
    if kind == "map_vals":
        mv_out: list[ResolvedItem] = []
        key_expr = args[1] if len(args) >= 2 else None
        ev_fn = hooks[1] if hooks is not None else None
        for ex, ec, ev, es in _res(args[0]):
            ex_u = unwrap_parens(ex) or ex
            if isinstance(ex_u, exp.Null):
                mv_out.append((ex_u, ec, ev, es))
            elif is_known_container_ctor(ex_u):
                if key_expr is not None and (
                    mres := match_map_by_key_expr(ex_u, key_expr, fk, ev_fn, ec, ev, es)
                ) is not None:
                    mv_out.append((exp.Array(expressions=mres[0]), ec, ev, es))
                else:
                    mv_out.append((exp.Array(expressions=extract_map_values(ex_u, fk)), ec, ev, es))
            elif is_unknown_container_or_lambda(ex_u):
                mv_out.append((ex_u, ec, ev, es))
        return mv_out
    if kind == "map_keys":
        mk_out: list[ResolvedItem] = []
        for ex, ec, ev, es in _res(args[0]):
            ex_u = unwrap_parens(ex) or ex
            if isinstance(ex_u, exp.Null):
                mk_out.append((ex_u, ec, ev, es))
            elif is_known_container_ctor(ex_u):
                mk_out.append((exp.Array(expressions=extract_map_keys(ex_u)), ec, ev, es))
            elif is_unknown_container_or_lambda(ex_u):
                mk_out.append((ex_u, ec, ev, es))
        return mk_out
    if kind == "resize":
        return apply_resize_transform(args, _res, hooks, ctes, vis, smap)
    if kind == "zip":
        return apply_zip_transform(args, _res, ctes, vis, smap)
    if kind == "filter":
        return apply_lambda_filter(
            args, _res, hooks[0] if hooks is not None else None, ctes, vis, smap,
        )
    if kind == "transform":
        return apply_lambda_transform(args, _res, ctes, vis, smap)
    if kind == "reduce":
        return apply_lambda_reduce(
            args, _res, ctes, vis, smap, hooks[1] if hooks is not None else None,
        )
    if kind == "struct_insert":
        base = _res(args[0])
        extra = [a for a in args[1:] if a is not None]
        if not base:
            return [(exp.Struct(expressions=extra), ctes, vis, smap)]
        return [
            (
                exp.Struct(
                    expressions=[
                        *(
                            ex_s.expressions
                            if isinstance(ex_s := unwrap_parens(ex), exp.Struct)
                            else ()
                        ),
                        *extra,
                    ]
                ),
                ec, ev, es,
            )
            for ex, ec, ev, es in base
        ]
    if kind == "if":
        cb = hooks[0](args[0], ctes, vis, smap) if hooks is not None else None
        if cb is True:
            return _res(args[1])
        f_res: list[ResolvedItem] = (
            _res(args[2]) if args[2] is not None else [(exp.Null(), ctes, vis, smap)]
        )
        if cb is False:
            return f_res
        return _res(args[1]) + f_res
    if kind == "coalesce":
        c_out: list[ResolvedItem] = []
        for a in args:
            au = unwrap_parens(a)
            if au is None or isinstance(au, exp.Null):
                continue
            res_a = _res(au)
            c_out.extend(res_a)
            definite_src = is_known_container_ctor(au) or (
                isinstance(au, (exp.Subquery, exp.Select))
                and is_single_row_const_rel(au, ctes, vis)
            )
            if definite_src and res_a and all(
                is_known_container_ctor(unwrap_parens(ex)) for ex, *_ in res_a
            ):
                break
        return c_out
    return []


def extract_range_index_items(
    gs: exp.Expr,
    mode: str,
    has_idx_expr: bool,
    idx_val: object,
    ec: dict[str, exp.CTE],
    ev: frozenset[str],
    es: SmapType | None,
    hooks: EvalHooks | None,
) -> tuple[list[ResolvedItem], bool] | None:
    if hooks is None:
        return None
    sv = hooks[1](gs.args.get("start"), ec, ev, es)
    end_v = hooks[1](gs.args.get("end"), ec, ev, es)
    step_e = gs.args.get("step")
    step_v = hooks[1](step_e, ec, ev, es) if step_e is not None else 1
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in (sv, end_v, step_v)):
        return None
    assert isinstance(sv, int) and isinstance(end_v, int) and isinstance(step_v, int)
    if step_v == 0:
        return [], True
    stop = (end_v if gs.args.get("is_end_exclusive") else end_v + 1) if step_v > 0 else (
        end_v if gs.args.get("is_end_exclusive") else end_v - 1
    )
    if (step_v > 0 and stop <= sv) or (step_v < 0 and stop >= sv):
        return [], True
    span = (stop - sv) if step_v > 0 else (sv - stop)
    abs_step = step_v if step_v > 0 else -step_v
    cnt = (span + abs_step - 1) // abs_step
    if mode == "first":
        return [(exp.Literal.number(sv), ec, ev, es)], False
    if mode == "last":
        return [(exp.Literal.number(sv + (cnt - 1) * step_v), ec, ev, es)], False
    if has_idx_expr and idx_val is None:
        return [(exp.Null(), ec, ev, es)], False
    if isinstance(idx_val, int) and not isinstance(idx_val, bool):
        if mode in ("anon_1b", "bracket_1b"):
            pos = (
                idx_val - 1
                if 1 <= idx_val <= cnt
                else (cnt + idx_val if -cnt <= idx_val <= -1 else -1)
            )
            return (
                ([(exp.Literal.number(sv + pos * step_v), ec, ev, es)], False)
                if pos >= 0
                else ([], True)
            )
        if mode == "anon_0b":
            return (
                ([(exp.Literal.number(sv + idx_val * step_v), ec, ev, es)], False)
                if 0 <= idx_val < cnt
                else ([], True)
            )
        if mode == "bracket_0b":
            if 0 <= idx_val < cnt:
                return [(exp.Literal.number(sv + idx_val * step_v), ec, ev, es)], False
            if idx_val == -1 or idx_val < -(cnt + 1) or idx_val >= cnt:
                return [], True
    return [(exp.Literal.number(sv), ec, ev, es)], False


__all__ = [
    "apply_map_to_entries",
    "apply_transform",
    "apply_zip_transform",
    "extract_range_index_items",
    "is_container_item",
    "unwrap_resolved_container",
    "wrap_array_arg",
]
