"""Recursive container/JSON/transform resolution for `_bool_expr_guard.py` & `_bool_expr_const.py`.

Strictly Layer 2a: imports only sqlglot, stdlib, `_ast_stmt_rules`,
`_bool_expr_containers`, `_bool_expr_extract`, `_bool_expr_lambdas`, and
`_bool_expr_resolver`.
"""

from __future__ import annotations

from sqlglot import exp

from opendv.storage._ast_stmt_rules import unwrap_parens
from opendv.storage._bool_expr_containers import (
    ResolvedItem,
    SmapType,
    extend_cte_nodes,
    is_array_ctor,
    is_known_container_ctor,
    is_slice_node,
    is_unknown_container_or_lambda,
)
from opendv.storage._bool_expr_extract import (
    classify_container_transform,
    extract_container_spec,
    extract_index_expr,
    extract_lookup_key_expr,
)
from opendv.storage._bool_expr_json import (
    ProvItem,
    extract_json_extract_items_prov,
    resolve_json_keys_items,
)
from opendv.storage._bool_expr_maps import match_map_by_key_expr, unpack_container_ctor
from opendv.storage._bool_expr_nulls import (
    adjusted_bracket_map_key,
    can_expr_eval_null,
    live_container_case_branches,
    select_may_be_empty,
)
from opendv.storage._bool_expr_quantiles import (
    cast_container_items,
    extract_array_cast_elem,
    resolve_passthrough_container,
    unwrap_quantile_target,
)
from opendv.storage._bool_expr_resolver import (
    expand_cols,
    resolve_col_from_smap,
    resolve_source_col,
)
from opendv.storage._bool_expr_slicing import extract_array_index_items
from opendv.storage._bool_expr_transforms import (
    EvalHooks,
    apply_transform,
    extract_range_index_items,
    is_container_item,
    unwrap_resolved_container,
)
from opendv.storage._bool_expr_vocab import (
    UNKNOWN,
    is_json_extract_node,
    unpack_json_path_steps,
)

_JSON_EXTRACT_SCALAR_CLS = getattr(exp, "JSONExtractScalar", None)
_JSONB_EXTRACT_SCALAR_CLS = getattr(exp, "JSONBExtractScalar", None)
_JSON_KEYS_CLS = getattr(exp, "JSONKeys", None)
_GENERATE_SERIES_CLS = getattr(exp, "GenerateSeries", None)
_JSON_KEYS_ANON: frozenset[str] = frozenset({
    "JSON_KEYS", "JSON_OBJECT_KEYS", "JSONB_OBJECT_KEYS", "OBJECT_KEYS",
})


def resolve_container_items(
    expr: exp.Expr | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    depth: int = 0,
    hooks: EvalHooks | None = None,
) -> list[ResolvedItem]:
    u = unwrap_parens(expr)
    if u is None or depth > 16:
        return []

    def _cfn(
        e: exp.Expr | None, cc: dict[str, exp.CTE], vv: frozenset[str], sm: SmapType | None, d: int,
    ) -> list[ResolvedItem]:
        return resolve_container_items(e, cc, vv, sm, d, hooks)

    if isinstance(u, exp.Column):
        if not smap:
            return []
        raw = resolve_col_from_smap(u, smap, ctes, vis, _cfn)
        return [
            item
            for ex, ec, ev, es in raw
            for item in unwrap_resolved_container(
                ex, ec, ev, es, depth + 1, hooks, resolve_container_items,
            )
        ]
    if isinstance(u, exp.Subquery):
        nc = extend_cte_nodes(u, ctes)
        res = resolve_container_items(u.this, nc, vis, smap, depth + 1, hooks)
        if hooks is not None and select_may_be_empty(
            u, hooks[1], hooks[0], ctes, vis, smap, unpack_fn=unpack_array_with_emptiness,
        ):
            return [*res, (exp.Null(), ctes, vis, smap)]
        return res
    if isinstance(u, exp.SetOperation):
        nc = extend_cte_nodes(u, ctes)
        res_so = (
            resolve_container_items(u.this, nc, vis, smap, depth + 1, hooks)
            + resolve_container_items(u.expression, nc, vis, smap, depth + 1, hooks)
        )
        if hooks is not None and select_may_be_empty(
            u, hooks[1], hooks[0], nc, vis, smap, unpack_fn=unpack_array_with_emptiness,
        ):
            return [*res_so, (exp.Null(), nc, vis, smap)]
        return res_so
    if isinstance(u, exp.Select):
        nc = extend_cte_nodes(u, ctes)
        cols = expand_cols(u, nc, vis, smap, _cfn)
        out_sel = (
            [
                item
                for ex, ec, ev, es in cols[0][1]
                for item in unwrap_resolved_container(
                    ex, ec, ev, es, depth + 1, hooks, resolve_container_items,
                )
            ]
            if len(cols) == 1 and cols[0][1] is not None
            else []
        )
        if hooks is not None and select_may_be_empty(
            u, hooks[1], hooks[0], nc, vis, smap, unpack_fn=unpack_array_with_emptiness,
        ):
            return [*out_sel, (exp.Null(), nc, vis, smap)]
        return out_sel
    if (
        isinstance(u, (exp.Cast, exp.TryCast))
        and isinstance(dt := u.args.get("to"), exp.DataType)
        and dt.is_type(exp.DataType.Type.ARRAY, exp.DataType.Type.MAP, exp.DataType.Type.STRUCT)
    ):
        inner_res = resolve_container_items(u.this, ctes, vis, smap, depth + 1, hooks)
        return cast_container_items(u, dt, inner_res, ctes, vis, smap)
    if isinstance(u, exp.Case):
        return [
            it
            for br_e in live_container_case_branches(u, ctes, vis, smap, hooks)
            for it in resolve_container_items(br_e, ctes, vis, smap, depth + 1, hooks)
        ]
    if (tx := classify_container_transform(u)) is not None:
        kind, args, fk = tx
        if kind == "concat_maybe":
            all_res = [
                resolve_container_items(a, ctes, vis, smap, depth + 1, hooks)
                for a in args if a is not None
            ]
            if any(is_container_item(ex) for ra in all_res for ex, *_ in ra):
                return [it for ra in all_res for it in ra]
            return [(u, ctes, vis, smap)]
        if kind == "repeat":
            res0 = resolve_container_items(args[0], ctes, vis, smap, depth + 1, hooks)
            if any(is_container_item(ex) for ex, *_ in res0):
                cnt = (
                    hooks[1](args[1], ctes, vis, smap)
                    if (len(args) >= 2 and args[1] and hooks)
                    else UNKNOWN
                )
                if cnt is None:
                    return [(exp.Null(), ctes, vis, smap)]
                if isinstance(cnt, int) and not isinstance(cnt, bool) and cnt <= 0:
                    return [(exp.Array(expressions=[]), ctes, vis, smap)]
                return res0
            return [(u, ctes, vis, smap)]
        return apply_transform(
            kind, args, fk, ctes, vis, smap, depth, hooks, resolve_container_items,
        )
    if (
        (_JSON_KEYS_CLS is not None and isinstance(u, _JSON_KEYS_CLS))
        or (isinstance(u, exp.Anonymous) and u.name.upper() in _JSON_KEYS_ANON)
    ):
        a0 = u.this if not isinstance(u, exp.Anonymous) else (
            u.expressions[0] if u.expressions else None
        )
        a1 = u.expression if not isinstance(u, exp.Anonymous) else (
            u.expressions[1] if len(u.expressions) >= 2 else None
        )
        return resolve_json_keys_items(a0, a1, ctes, vis, smap, hooks[1] if hooks else None)
    if (ext := extract_container_items(u, ctes, vis, smap, depth + 1, hooks)) is not None:
        return [
            item
            for ex, ec, ev, es in ext
            for item in unwrap_resolved_container(
                ex, ec, ev, es, depth + 1, hooks, resolve_container_items,
            )
        ]
    if (
        pt_res := resolve_passthrough_container(
            u, ctes, vis, smap, depth, hooks, resolve_container_items,
        )
    ) is not None:
        return pt_res
    return [(u, ctes, vis, smap)]


def _extract_json_node_prov(
    node: exp.Expr,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    depth: int,
    hooks: EvalHooks | None,
) -> list[ProvItem] | None:
    if depth > 16 or (steps := unpack_json_path_steps(node)) is None:
        return None
    is_scalar_only = any(
        cls is not None and isinstance(node, cls)
        for cls in (_JSON_EXTRACT_SCALAR_CLS, _JSONB_EXTRACT_SCALAR_CLS)
    )
    src_u = unwrap_parens(node.this)
    cur_prov: list[ProvItem] | None = None
    if src_u is not None and is_json_extract_node(src_u):
        cur_prov = _extract_json_node_prov(src_u, ctes, vis, smap, depth + 1, hooks)
    if cur_prov is None:
        cur_prov = [
            (*it, False)
            for it in resolve_container_items(node.this, ctes, vis, smap, depth + 1, hooks)
        ]
    ev_fn = hooks[1] if hooks is not None else None
    return extract_json_extract_items_prov(
        cur_prov,
        steps,
        ctes,
        vis,
        smap,
        is_scalar_only=is_scalar_only,
        ev_fn=ev_fn,
        unwrap_fn=lambda vx, vc, vv, vs: unwrap_resolved_container(
            vx, vc, vv, vs, depth + 1, hooks, resolve_container_items,
        ),
    )


def extract_container_items(
    node: exp.Expr,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    depth: int = 0,
    hooks: EvalHooks | None = None,
    *,
    force_map: bool = False,
) -> list[ResolvedItem] | None:
    """Return resolved element/field items if node extracts from an Array/Struct/Map."""
    if depth > 16 or is_slice_node(node):
        return None
    if isinstance(node, exp.ToMap):
        inner = unwrap_parens(node.this)
        if inner is not None and not isinstance(inner, exp.Struct):
            return extract_container_items(
                inner, ctes, vis, smap, depth + 1, hooks, force_map=True,
            )
        return None
    if is_json_extract_node(node):
        res_p = _extract_json_node_prov(node, ctes, vis, smap, depth, hooks)
        return [it[:4] for it in res_p] if res_p is not None else None
    if (spec := extract_container_spec(node)) is None:
        return None
    container, field_key = spec
    key_expr = extract_lookup_key_expr(node)
    idx_info = extract_index_expr(node)
    idx_val: object = UNKNOWN
    if idx_info is not None and idx_info[0] is not None and hooks is not None:
        idx_val = hooks[1](idx_info[0], ctes, vis, smap)
    eff_fk = field_key
    if eff_fk is None and key_expr is not None and hooks is not None:
        kv = hooks[1](key_expr, ctes, vis, smap)
        if isinstance(kv, str):
            eff_fk = kv
    map_key_expr = adjusted_bracket_map_key(node, key_expr)
    is_element_at = isinstance(node, exp.Anonymous) and node.name.upper() == "ELEMENT_AT"
    c_items = resolve_container_items(container, ctes, vis, smap, depth + 1, hooks)
    out: list[ResolvedItem] = []
    if (
        (idx_info is not None and idx_info[0] is not None and idx_val is None)
        or (
            not is_element_at
            and key_expr is not None
            and hooks is not None
            and can_expr_eval_null(key_expr, hooks[1], hooks[0], ctes, vis, smap)
        )
    ):
        out.append((exp.Null(), ctes, vis, smap))
    saw_ctor = False
    ev_fn = hooks[1] if hooks is not None else None
    for ex, ec, ev, es in c_items:
        ex_u = unwrap_parens(ex)
        ex_map = exp.ToMap(this=ex_u) if (force_map and isinstance(ex_u, exp.Struct)) else ex_u
        if isinstance(ex_u, exp.Null):
            out.append((ex_u, ec, ev, es))
        elif is_array_ctor(ex_u) and ex_u is not None and idx_info is not None:
            a_its, a_missed = extract_array_index_items(
                ex_u.expressions, idx_info[1], idx_info[0] is not None, idx_val, ec, ev, es,
            )
            out.extend(a_its)
            if a_missed:
                saw_ctor = True
        elif (
            _GENERATE_SERIES_CLS is not None
            and isinstance(ex_u, _GENERATE_SERIES_CLS)
            and idx_info is not None
            and (
                gs_res := extract_range_index_items(
                    ex_u, idx_info[1], idx_info[0] is not None, idx_val, ec, ev, es, hooks,
                )
            )
            is not None
        ):
            out.extend(gs_res[0])
            if gs_res[1]:
                saw_ctor = True
        elif (
            m_res := match_map_by_key_expr(ex_map, map_key_expr, field_key, ev_fn, ec, ev, es)
        ) is not None:
            if is_element_at:
                if m_res[0]:
                    out.append((exp.Array(expressions=m_res[0]), ec, ev, es))
                if not m_res[0] or m_res[1]:
                    out.append((exp.Array(expressions=[]), ec, ev, es))
            else:
                out.extend((val, ec, ev, es) for val in m_res[0])
                if m_res[1]:
                    saw_ctor = True
        elif (vals := unpack_container_ctor(ex_u, eff_fk)):
            out.extend((val, ec, ev, es) for val in vals)
        elif is_known_container_ctor(ex_u):
            saw_ctor = True
        elif is_unknown_container_or_lambda(ex_u):
            out.append((ex, ec, ev, es))
        elif unwrap_quantile_target(ex_u) is not None:
            out.append((ex, ec, ev, es))
            saw_ctor = True
        elif (el_cast := extract_array_cast_elem(ex_u)) is not None:
            out.append((el_cast, ec, ev, es))
            saw_ctor = True
        elif not isinstance(ex_u, exp.Column) and not (
            isinstance(ex_u, exp.Literal) and ex_u.is_string
        ):
            out.append((exp.Null(), ec, ev, es))
    if saw_ctor:
        out.append((exp.Null(), ctes, vis, smap))
    return out


def resolve_col_with_containers(
    col: exp.Column,
    smap: SmapType,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    hooks: EvalHooks | None = None,
) -> list[ResolvedItem]:
    def _cfn(
        e: exp.Expr | None, cc: dict[str, exp.CTE], vv: frozenset[str], sm: SmapType | None, d: int,
    ) -> list[ResolvedItem]:
        return resolve_container_items(e, cc, vv, sm, d, hooks)

    return resolve_col_from_smap(col, smap, ctes, vis, _cfn)


def resolve_source_col_with_containers(
    src: exp.Expr | None,
    col_name: str | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    outer_smap: SmapType | None = None,
    hooks: EvalHooks | None = None,
) -> list[ResolvedItem]:
    def _cfn(
        e: exp.Expr | None, cc: dict[str, exp.CTE], vv: frozenset[str], sm: SmapType | None, d: int,
    ) -> list[ResolvedItem]:
        return resolve_container_items(e, cc, vv, sm, d, hooks)

    return resolve_source_col(src, col_name, ctes, vis, outer_smap, _cfn)


def unpack_array_with_emptiness(
    ue: exp.Expr | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    hooks: EvalHooks | None = None,
) -> tuple[list[ResolvedItem], bool]:
    resolved = resolve_container_items(unwrap_parens(ue), ctes, vis, smap, 1, hooks)
    out: list[ResolvedItem] = []
    has_empty = False
    for ex, ec, ev, es in resolved:
        ex_u = unwrap_parens(ex)
        if ex_u is None:
            continue
        if is_array_ctor(ex_u):
            if ex_u.expressions:
                out.extend((el, ec, ev, es) for el in ex_u.expressions)
            else:
                has_empty = True
        elif isinstance(ex_u, exp.Null):
            has_empty = True
        elif (el_cast := extract_array_cast_elem(ex_u)) is not None:
            out.append((el_cast, ec, ev, es))
        elif not isinstance(ex_u, exp.Column):
            out.append((ex, ec, ev, es))
    return out, has_empty


def unpack_array_with_containers(
    ue: exp.Expr | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    hooks: EvalHooks | None = None,
) -> list[ResolvedItem]:
    return unpack_array_with_emptiness(ue, ctes, vis, smap, hooks)[0]


__all__ = [
    "extract_container_items",
    "resolve_col_with_containers",
    "resolve_container_items",
    "resolve_source_col_with_containers",
    "unpack_array_with_containers",
    "unpack_array_with_emptiness",
]
