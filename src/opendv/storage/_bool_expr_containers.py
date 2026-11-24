"""Container & relation-scoping helpers for `_bool_expr_resolver.py` and `_bool_expr_guard.py`.

Strictly Layer 2a: imports only sqlglot and stdlib (plus `_ast_stmt_rules`).
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlglot import exp

from opendv.storage._ast_stmt_rules import unwrap_parens
from opendv.storage._bool_expr_vocab import (
    BOOL_FUNC_NAMES,
    BOOL_PREDICATE_EXPRS,
    CONTAINER_FUNC_PREFIXES,
    KNOWN_CONTAINER_ANON_CTORS,
    LAG_LEAD_EXPRS,
    SAFE_CONTAINER_SCALAR_FUNCS,
    SELECT_DISALLOWED_CLAUSES,
    UNKNOWN_CONTAINER_FUNCS,
    VALUE_PASSTHROUGH_EXPRS,
    VALUE_PASSTHROUGH_NAMES,
)

SmapType = dict[str, exp.Expr]
ResolvedItem = tuple[exp.Expr, dict[str, exp.CTE], frozenset[str], SmapType | None]
ColEntry = tuple[str | None, list[ResolvedItem] | None]


class ScopedSmap(dict[str, exp.Expr]):
    """Smap dict carrying an ordered lexical scope stack [(scope_map, is_opaque), ...]."""

    __slots__ = ("scopes",)

    def __init__(
        self,
        mapping: SmapType | None = None,
        *,
        scopes: tuple[tuple[dict[str, exp.Expr], bool], ...] = (),
    ) -> None:
        super().__init__(mapping or {})
        self.scopes: tuple[tuple[dict[str, exp.Expr], bool], ...] = scopes or (
            (dict(mapping) if mapping else {}, False),
        )

ARRAY_CTOR_NAMES: frozenset[str] = frozenset({
    "ARRAY", "ARRAY_VALUE", "ARRAY_CONSTRUCT", "LIST_PACK",
})
_ARRAY_COMPACT_CTOR_CLS = getattr(exp, "ArrayConstructCompact", None)
_JSON_OBJECT_CLS = getattr(exp, "JSONObject", None)
_JSON_KV_CLS = getattr(exp, "JSONKeyValue", None)
_JSON_OBJ_ANON: frozenset[str] = frozenset({
    "JSON_BUILD_OBJECT", "JSONB_BUILD_OBJECT", "JSON_OBJECT", "OBJECT_CONSTRUCT_KEEP_NULL",
})
_MAP_CLASSES: tuple[type[exp.Expr], ...] = tuple(
    c for c in (getattr(exp, "Map", None), getattr(exp, "VarMap", None)) if c is not None
)
_ARRAY_SLICE_CLS = getattr(exp, "ArraySlice", None)
_MAP_FROM_ENTRIES_CLS = getattr(exp, "MapFromEntries", None)
_PARAM_AGG_CLS = getattr(exp, "ParameterizedAgg", None)
_COMBINED_AGG_CLS = getattr(exp, "CombinedAggFunc", None)
PERCENTILE_DISC_CLS = getattr(exp, "PercentileDisc", None)
_PERCENTILE_CONT_CLS = getattr(exp, "PercentileCont", None)
_QUANTILE_CLS = getattr(exp, "Quantile", None)
_MEDIAN_CLS = getattr(exp, "Median", None)
_WG_Q_CLSES: tuple[type[exp.Expr], ...] = tuple(
    c
    for c in (PERCENTILE_DISC_CLS, _PERCENTILE_CONT_CLS, _QUANTILE_CLS, _MEDIAN_CLS)
    if c is not None
)
_WG_PASSTHROUGH_CLSES: tuple[type[exp.Expr], ...] = tuple(
    c
    for n in ("Median", "Mode", "PercentileDisc", "Quantile")
    if (c := getattr(exp, n, None)) is not None
)
_WG_PASSTHROUGH_ANON: frozenset[str] = frozenset({
    "MEDIAN", "MODE", "PERCENTILE_DISC", "QUANTILE", "QUANTILE_DISC",
})
_WG_QUANTILE_ANON: frozenset[str] = frozenset({
    "PERCENTILE_DISC", "PERCENTILE_CONT", "QUANTILE_DISC", "QUANTILE_CONT", "QUANTILE", "MEDIAN",
})
_SLICE_ANON: frozenset[str] = frozenset({"ARRAY_SLICE", "LIST_SLICE"})


def is_wg_passthrough(node: exp.Expr | None) -> bool:
    return isinstance(node, _WG_PASSTHROUGH_CLSES) or (
        isinstance(node, exp.Anonymous) and node.name.upper() in _WG_PASSTHROUGH_ANON
    )


def within_group_quantile_arg(node: exp.WithinGroup) -> exp.Expr | None:
    inner = unwrap_parens(node.this)
    if inner is None:
        return None
    if _WG_Q_CLSES and isinstance(inner, _WG_Q_CLSES):
        th = inner.this
        return th if isinstance(th, exp.Expr) else None
    if isinstance(inner, exp.Anonymous) and inner.name.upper() in _WG_QUANTILE_ANON:
        return inner.expressions[0] if inner.expressions else None
    return None


_NON_PARAM_Q_TYPES: tuple[type[exp.Expr], ...] = (
    exp.DataType, exp.Var, exp.Identifier, exp.Order,
)


def param_agg_q_args(cur: exp.Expr) -> list[exp.Expr]:
    if _PARAM_AGG_CLS is not None and isinstance(cur, _PARAM_AGG_CLS) and cur.args.get("params"):
        return [
            x
            for x in cur.expressions
            if isinstance(x, exp.Expr) and not isinstance(x, _NON_PARAM_Q_TYPES)
        ]
    if _PERCENTILE_CONT_CLS is not None and isinstance(cur, _PERCENTILE_CONT_CLS):
        q = cur.expression or cur.this
        return [q] if isinstance(q, exp.Expr) else []
    return []


def is_quantile_passthrough_fn(base_fn: str) -> bool:
    return base_fn == "PERCENTILE_DISC" or (
        base_fn.startswith(("QUANTILE", "MEDIAN")) and base_fn != "QUANTILE_CONT"
    )


def is_array_ctor(node: exp.Expr | None) -> bool:
    return (
        isinstance(node, exp.Array)
        or (_ARRAY_COMPACT_CTOR_CLS is not None and isinstance(node, _ARRAY_COMPACT_CTOR_CLS))
        or (isinstance(node, exp.Anonymous) and node.name.upper() in ARRAY_CTOR_NAMES)
    )


def is_slice_node(node: exp.Expr) -> bool:
    return (
        (_ARRAY_SLICE_CLS is not None and isinstance(node, _ARRAY_SLICE_CLS))
        or (isinstance(node, exp.Anonymous) and node.name.upper() in _SLICE_ANON)
        or (
            isinstance(node, exp.Bracket)
            and any(isinstance(e, exp.Slice) for e in node.expressions)
        )
    )


def extend_cte_nodes(node: exp.Expr | None, base: dict[str, exp.CTE]) -> dict[str, exp.CTE]:
    if node is None or not isinstance(w := node.args.get("with_"), exp.With):
        return base
    out = dict(base)
    for c in w.expressions:
        if isinstance(c, exp.CTE) and (n := (c.alias_or_name or "").lower()):
            out[n] = c
    return out


def alias_cols(node: exp.Expr | None) -> tuple[str, ...]:
    if node is None:
        return ()
    ta = node.args.get("alias")
    if isinstance(ta, exp.TableAlias) and ta.columns:
        return tuple(c.name.lower() for c in ta.columns if c.name)
    return ()


def apply_alias_cols(cols: list[ColEntry], ac: tuple[str, ...]) -> list[ColEntry]:
    if not ac:
        return cols
    out: list[ColEntry] = []
    for idx, nm in enumerate(ac):
        if idx >= len(cols) or cols[idx][1] is None:
            return [(None, None)]
        out.append((nm, cols[idx][1]))
    if len(ac) < len(cols):
        out.extend(cols[len(ac) :])
    return out


def _collect_select_rel(node: exp.Expr | None, srcs: list[exp.Expr]) -> None:
    if node is None:
        return
    if isinstance(node, exp.Join):
        _collect_select_rel(node.this, srcs)
    elif isinstance(node, (exp.Subquery, exp.Paren)):
        if isinstance(node.this, (exp.Select, exp.SetOperation, exp.Values)) or node.alias:
            srcs.append(node)
        else:
            _collect_select_rel(node.this, srcs)
    else:
        srcs.append(node)
    for j in node.args.get("joins") or ():
        if isinstance(j, exp.Expr):
            _collect_select_rel(j, srcs)


def select_sources(sel: exp.Select, outer_smap: SmapType | None = None) -> SmapType:
    srcs: list[exp.Expr] = []
    fm = sel.args.get("from_")
    if isinstance(fm, exp.From):
        _collect_select_rel(fm.this, srcs)
        for e in fm.expressions or ():
            if isinstance(e, exp.Expr):
                _collect_select_rel(e, srcs)
    for j in sel.args.get("joins") or ():
        if isinstance(j, exp.Expr):
            _collect_select_rel(j, srcs)
    out: SmapType = dict(outer_smap) if outer_smap else {}
    for idx, s in enumerate(srcs):
        key = ""
        if isinstance(s, exp.Table):
            key = (s.alias or s.name or "").lower()
        elif isinstance(s, exp.Lateral):
            inner_a = s.this.alias if isinstance(s.this, exp.Expr) else ""
            key = (s.alias or inner_a or "").lower()
        elif isinstance(s, (exp.Subquery, exp.Values, exp.Anonymous, exp.Unnest)):
            key = (s.alias or "").lower()
        out[key or f"__anon_{idx}"] = s
    return out


def is_smap_opaque(smap: SmapType, ctes: dict[str, exp.CTE]) -> bool:
    return any(
        isinstance(v, exp.Table)
        and (bool(v.db or v.catalog) or (v.name or "").lower() not in ctes)
        for v in smap.values()
    )


def smap_scopes(smap: SmapType | None) -> tuple[tuple[dict[str, exp.Expr], bool], ...]:
    if not smap:
        return ()
    if isinstance(smap, ScopedSmap):
        return smap.scopes
    return ((dict(smap), False),)


def merge_select_smap(
    sel: exp.Select,
    ctes: dict[str, exp.CTE],
    outer_smap: SmapType | None = None,
) -> tuple[dict[str, exp.CTE], SmapType, SmapType, bool]:
    nc = extend_cte_nodes(sel, ctes)
    own_smap = select_sources(sel)
    has_opaque = is_smap_opaque(own_smap, nc)
    own_scope = (dict(own_smap), has_opaque)
    if not outer_smap:
        return nc, ScopedSmap(own_smap, scopes=(own_scope,)), own_smap, has_opaque
    outer_scopes = smap_scopes(outer_smap)
    merged: dict[str, exp.Expr] = {}
    for sc, _ in reversed(outer_scopes):
        merged.update(sc)
    merged.update(own_smap)
    smap: SmapType = ScopedSmap(merged, scopes=(own_scope, *outer_scopes))
    return nc, smap, own_smap, has_opaque


def values_row_cells(node: exp.Expr) -> list[Sequence[exp.Expr]] | None:
    if not (
        isinstance(node, exp.Values)
        or (isinstance(node, exp.Anonymous) and node.name.upper() == "VALUES")
    ):
        return None
    rows: list[Sequence[exp.Expr]] = []
    for row in node.expressions:
        cells = row.expressions if isinstance(row, exp.Tuple) else [row]
        if len(cells) == 1 and isinstance(cells[0], exp.Tuple):
            cells = cells[0].expressions
        rows.append(cells)
    return rows


def flatten_set_op(
    node: exp.Expr | None,
    ctes: dict[str, exp.CTE],
    *,
    by_name: bool,
) -> list[tuple[exp.Expr | None, dict[str, exp.CTE]]]:
    out: list[tuple[exp.Expr | None, dict[str, exp.CTE]]] = []
    stack: list[tuple[exp.Expr | None, dict[str, exp.CTE]]] = [(node, ctes)]
    while stack:
        cur, cc = stack.pop(0)
        cc = extend_cte_nodes(cur, cc)
        while isinstance(cur, (exp.Subquery, exp.Paren)):
            cur = cur.this
            cc = extend_cte_nodes(cur, cc)
        if isinstance(cur, exp.SetOperation) and bool(cur.args.get("by_name")) == by_name:
            stack.extend(((cur.this, cc), (cur.expression, cc)))
        else:
            out.append((cur, cc))
    return out


def is_single_row_const_rel(
    rel: exp.Expr | None, ctes: dict[str, exp.CTE], vis: frozenset[str],
) -> bool:
    rel_u = unwrap_parens(rel)
    if rel_u is None:
        return False
    if isinstance(rel_u, exp.Subquery):
        if any(rel_u.args.get(k) for k in SELECT_DISALLOWED_CLAUSES):
            return False
        return is_single_row_const_rel(rel_u.this, extend_cte_nodes(rel_u, ctes), vis)
    if isinstance(rel_u, exp.Table):
        if any(rel_u.args.get(k) for k in SELECT_DISALLOWED_CLAUSES):
            return False
        if not rel_u.db and not rel_u.catalog and rel_u.name:
            cn = rel_u.name.lower()
            if cn in ctes and cn not in vis:
                return is_single_row_const_rel(ctes[cn].this, ctes, vis | {cn})
        return False
    if isinstance(rel_u, exp.Select):
        nc = extend_cte_nodes(rel_u, ctes)
        if any(rel_u.args.get(k) for k in SELECT_DISALLOWED_CLAUSES) or any(
            isinstance(p, (exp.Explode, exp.Unnest))
            or p.find(exp.Explode, exp.Unnest) is not None
            for p in rel_u.expressions
        ):
            return False
        for p in rel_u.expressions:
            pe = unwrap_parens(p.this if isinstance(p, exp.Alias) else p)
            if isinstance(pe, (exp.Select, exp.Subquery)) and not is_single_row_const_rel(
                pe, nc, vis,
            ):
                return False
        fm = rel_u.args.get("from_")
        if fm is None:
            return True
        if not isinstance(fm, exp.From) or fm.this is None:
            return False
        return is_single_row_const_rel(fm.this, nc, vis) and all(
            is_single_row_const_rel(e, nc, vis) for e in (fm.expressions or ())
        )
    return (vrows := values_row_cells(rel_u)) is not None and len(vrows) == 1


def container_func_name(node: exp.Expr) -> str:
    if (
        isinstance(node, (exp.Anonymous, exp.AnonymousAggFunc))
        or (_PARAM_AGG_CLS is not None and isinstance(node, _PARAM_AGG_CLS))
        or (_COMBINED_AGG_CLS is not None and isinstance(node, _COMBINED_AGG_CLS))
    ):
        raw = node.this
        nm = raw.name if isinstance(raw, exp.Expr) else (raw or node.name)
        return str(nm).upper()
    if isinstance(node, exp.Func):
        try:
            return node.sql_name().upper().replace("J_S_O_N_", "JSON_")
        except NotImplementedError:
            return type(node).__name__.upper()
    return ""


def is_known_container_ctor(ex_u: exp.Expr | None) -> bool:
    if ex_u is None:
        return False
    return (
        is_array_ctor(ex_u)
        or isinstance(ex_u, (exp.Struct, exp.ToMap))
        or bool(_MAP_CLASSES and isinstance(ex_u, _MAP_CLASSES))
        or (_MAP_FROM_ENTRIES_CLS is not None and isinstance(ex_u, _MAP_FROM_ENTRIES_CLS))
        or (_JSON_OBJECT_CLS is not None and isinstance(ex_u, _JSON_OBJECT_CLS))
        or (
            _ARRAY_COMPACT_CTOR_CLS is not None
            and isinstance(ex_u, _ARRAY_COMPACT_CTOR_CLS)
        )
        or (isinstance(ex_u, exp.Anonymous) and ex_u.name.upper() in KNOWN_CONTAINER_ANON_CTORS)
    )


def is_unknown_container_or_lambda(node: exp.Expr | None) -> bool:
    cur = unwrap_parens(node)
    if cur is None:
        return False
    fname = container_func_name(cur)
    if fname in SAFE_CONTAINER_SCALAR_FUNCS:
        return False
    if isinstance(cur, exp.Lambda) or cur.find(exp.Lambda) is not None:
        return True
    if is_known_container_ctor(cur):
        return False
    return bool(
        fname
        and (
            fname.startswith(CONTAINER_FUNC_PREFIXES)
            or fname in ("TRANSFORM", "FILTER", "REDUCE")
            or fname in UNKNOWN_CONTAINER_FUNCS
        )
    )


__all__ = [
    "ARRAY_CTOR_NAMES",
    "BOOL_FUNC_NAMES",
    "BOOL_PREDICATE_EXPRS",
    "LAG_LEAD_EXPRS",
    "PERCENTILE_DISC_CLS",
    "VALUE_PASSTHROUGH_EXPRS",
    "VALUE_PASSTHROUGH_NAMES",
    "ColEntry",
    "ResolvedItem",
    "ScopedSmap",
    "SmapType",
    "alias_cols",
    "apply_alias_cols",
    "container_func_name",
    "extend_cte_nodes",
    "flatten_set_op",
    "is_array_ctor",
    "is_known_container_ctor",
    "is_quantile_passthrough_fn",
    "is_single_row_const_rel",
    "is_slice_node",
    "is_smap_opaque",
    "is_unknown_container_or_lambda",
    "is_wg_passthrough",
    "merge_select_smap",
    "param_agg_q_args",
    "select_sources",
    "smap_scopes",
    "values_row_cells",
    "within_group_quantile_arg",
]
