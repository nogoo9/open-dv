"""Shared AST guard helpers for steward-gate, external-table, and aggregate checks.

Strictly Layer 2a: imports only sqlglot and stdlib.
"""

from __future__ import annotations

from sqlglot import exp

from opendv.storage._ast_stmt_rules import (
    has_external_copy_args,
    has_file_extension,
    is_unsafe_external_table_ref,
    is_unsafe_table_node,
    unwrap_parens,
)
from opendv.storage._bool_expr_funcs import ALL_KNOWN_AGG_NAMES
from opendv.storage._bool_expr_guard import is_bool_or_null_expr
from opendv.storage._bool_expr_vocab import CH_AGG_COMBINATORS


def is_steward_approved_eq(eq: exp.EQ) -> bool:
    left = unwrap_parens(eq.this)
    right = unwrap_parens(eq.expression)
    for col_node, lit_node in ((left, right), (right, left)):
        if (
            isinstance(col_node, exp.Column)
            and col_node.name.lower() == "steward_gate_status"
            and isinstance(lit_node, exp.Literal)
            and lit_node.is_string
            and str(lit_node.this).upper() == "APPROVED"
        ):
            return True
    return False


def extract_where_approved_qualifiers(expr: exp.Expr | None) -> tuple[bool, set[str]]:
    """Return (has_unqualified_approved, qualified_table_aliases) from a pure-AND tree."""
    expr = unwrap_parens(expr)
    if expr is None:
        return False, set()
    if isinstance(expr, exp.And):
        u1, q1 = extract_where_approved_qualifiers(expr.this)
        u2, q2 = extract_where_approved_qualifiers(expr.expression)
        return u1 or u2, q1 | q2
    if isinstance(expr, exp.EQ) and is_steward_approved_eq(expr):
        left = unwrap_parens(expr.this)
        right = unwrap_parens(expr.expression)
        for node in (left, right):
            if isinstance(node, exp.Column) and node.name.lower() == "steward_gate_status":
                tbl = (node.table or "").lower()
                return (False, {tbl}) if tbl else (True, set())
    return False, set()


def has_cross_table_col_eq(
    expr: exp.Expr | None,
    left_aliases: set[str],
    right_aliases: set[str],
) -> bool:
    expr = unwrap_parens(expr)
    if expr is None or not left_aliases or not right_aliases:
        return False
    if isinstance(expr, exp.And):
        return has_cross_table_col_eq(
            expr.this, left_aliases, right_aliases
        ) or has_cross_table_col_eq(expr.expression, left_aliases, right_aliases)
    if isinstance(expr, exp.EQ):
        left = unwrap_parens(expr.this)
        right = unwrap_parens(expr.expression)
        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            return False
        lt, rt = (left.table or "").lower(), (right.table or "").lower()
        if not (lt and rt and lt != rt):
            return False
        return (lt in left_aliases and rt in right_aliases) or (
            rt in left_aliases and lt in right_aliases
        )
    return False


def collect_merge_insert_whens(stmt: exp.Merge) -> list[exp.When]:
    whens_node = stmt.args.get("whens")
    whens = (
        [w for w in whens_node.expressions if isinstance(w, exp.When)]
        if isinstance(whens_node, exp.Whens)
        else list(stmt.find_all(exp.When))
    )
    return [
        w
        for w in whens
        if (then_n := w.args.get("then")) is not None
        and (isinstance(then_n, exp.Insert) or then_n.find(exp.Insert) is not None)
    ]

_UNSAFE_REL_ARGS: tuple[str, ...] = (
    "pivots", "laterals", "sample", "match", "exclude", "changes", "rows_from",
)
_UNSAFE_WRAPPER_ARGS: tuple[str, ...] = (
    *_UNSAFE_REL_ARGS, "joins", "where", "group", "having", "qualify", "prewhere", "windows",
)

_AGG_ANONYMOUS_NAMES: frozenset[str] = ALL_KNOWN_AGG_NAMES


def has_col_alias_list(node: exp.Expr | None) -> bool:
    if node is None:
        return False
    alias_node = node.args.get("alias")
    return isinstance(alias_node, exp.TableAlias) and bool(alias_node.columns)


def has_star_modifiers(star: exp.Star) -> bool:
    return any(bool(star.args.get(k)) for k in ("except_", "replace", "rename", "ilike"))


def _is_empty_grouping_expr(node: exp.Expr | None) -> bool:
    inner = unwrap_parens(node)
    if inner is None:
        return True
    if isinstance(inner, exp.Tuple):
        return all(_is_empty_grouping_expr(e) for e in inner.expressions)
    return False


def has_unsafe_group_by(group: exp.Group | None) -> bool:
    if group is None:
        return False
    if not group.expressions:
        return True
    if any(group.args.get(k) for k in ("grouping_sets", "rollup", "cube", "totals", "all")):
        return True
    for g_expr in group.expressions:
        inner = unwrap_parens(g_expr)
        if inner is None:
            return True
        if isinstance(inner, (exp.Cube, exp.Rollup, exp.GroupingSets)):
            return True
        if inner.find(exp.Cube, exp.Rollup, exp.GroupingSets) is not None:
            return True
        if _is_empty_grouping_expr(inner):
            return True
    return False


def _is_agg_func_name(fname: str) -> bool:
    if fname in _AGG_ANONYMOUS_NAMES:
        return True
    cur = fname
    while True:
        matched = False
        for suf in CH_AGG_COMBINATORS:
            if cur.endswith(suf) and len(cur) > len(suf):
                cur = cur[: -len(suf)]
                if cur in _AGG_ANONYMOUS_NAMES:
                    return True
                matched = True
                break
        if not matched:
            return False


def select_has_scalar_aggregate(sel: exp.Expr) -> bool:
    """Return True if sel calls an aggregate outside Window wrappers and nested queries."""

    def _prune(node: exp.Expr) -> bool:
        return node is not sel and isinstance(node, (exp.Select, exp.Subquery, exp.CTE))

    exempt_win: set[int] = set()
    for node in sel.walk(prune=_prune):
        if node is sel:
            continue
        if isinstance(node, exp.Window):
            w_fn = node.this
            if isinstance(w_fn, (exp.Filter, exp.IgnoreNulls, exp.WithinGroup)):
                exempt_win.add(id(w_fn))
                w_fn = w_fn.this
            if isinstance(w_fn, (exp.AggFunc, exp.Anonymous, exp.Func)):
                exempt_win.add(id(w_fn))
            continue
        if id(node) in exempt_win:
            continue
        if isinstance(node, (exp.AggFunc, exp.Filter, exp.WithinGroup)):
            return True
        if isinstance(node, exp.IgnoreNulls) and isinstance(node.this, exp.AggFunc):
            return True
        if isinstance(node, (exp.Any, exp.All)) or (
            isinstance(node, exp.Anonymous) and node.name.upper() == "ALL"
        ):
            inner_q = (
                unwrap_parens(node.expressions[0]) if len(node.expressions) == 1 else None
            ) if isinstance(node, exp.Anonymous) else unwrap_parens(node.this)
            lhs_cmp = unwrap_parens(node.parent.this) if node.parent is not None else None
            is_rhs_cmp = (
                node.arg_key == "expression"
                and isinstance(
                    node.parent,
                    (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike),
                )
                and isinstance(inner_q, (exp.Array, exp.Select, exp.Subquery, exp.Column))
                and not is_bool_or_null_expr(lhs_cmp)
            )
            if not is_rhs_cmp:
                return True
            if isinstance(node, exp.Anonymous):
                continue
        if isinstance(node, exp.Anonymous):
            if _is_agg_func_name(node.name.upper()):
                return True
        elif isinstance(node, exp.Func) and _is_agg_func_name(node.sql_name().upper()):
            return True
    return False


def has_unsafe_grouping_or_scalar_agg(sel: exp.Select) -> bool:
    """Return True if sel can synthesize rows from 0 input rows via grouping/aggregates."""
    group = sel.args.get("group")
    if isinstance(group, exp.Group):
        return has_unsafe_group_by(group)
    if group is not None:
        return True
    return sel.args.get("having") is not None or select_has_scalar_aggregate(sel)


def has_unsafe_rel_args(
    node: exp.Expr | None,
    string_token_starts: frozenset[int] | set[int] = frozenset(),
) -> bool:
    if node is None:
        return False
    if isinstance(node, exp.Table):
        if is_unsafe_table_node(node, string_token_starts):
            return True
        return any(bool(node.args.get(k)) for k in _UNSAFE_WRAPPER_ARGS)
    if isinstance(node, exp.Subquery):
        return any(bool(node.args.get(k)) for k in _UNSAFE_WRAPPER_ARGS)
    if isinstance(node, exp.Join):
        return bool(node.args.get("pivots")) or has_unsafe_rel_args(node.this, string_token_starts)
    return any(bool(node.args.get(k)) for k in _UNSAFE_REL_ARGS)


def source_aliases(
    src: exp.Expr | None,
    string_token_starts: frozenset[int] | set[int] = frozenset(),
) -> set[str]:
    if src is None or has_unsafe_rel_args(src, string_token_starts):
        return set()
    if isinstance(src, exp.Table):
        if is_unsafe_table_node(src, string_token_starts):
            return set()
        eff = (src.alias or src.name or "").lower()
        return {eff} if eff else set()
    if isinstance(src, exp.Subquery):
        eff = (src.alias or "").lower()
        if eff:
            return {eff}
        if (
            isinstance(src.this, (exp.Select, exp.SetOperation, exp.Subquery))
            or src.args.get("with_") is not None
        ):
            return set()
        return source_aliases(src.this, string_token_starts)
    return set()


def output_steward_ordinal(sel: exp.Expr | None) -> int | None:
    if not isinstance(sel, exp.Select):
        return None
    indices: list[int] = []
    for idx, proj in enumerate(sel.expressions):
        if isinstance(proj, exp.Star) or (
            isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star)
        ):
            return None
        if isinstance(proj, exp.Alias):
            out_name = (proj.alias or "").lower()
        elif isinstance(proj, exp.Column):
            out_name = (proj.name or "").lower()
        else:
            out_name = ""
        if out_name == "steward_gate_status":
            indices.append(idx)
    return indices[0] if len(indices) == 1 else None


__all__ = [
    "collect_merge_insert_whens", "extract_where_approved_qualifiers", "has_col_alias_list",
    "has_cross_table_col_eq", "has_external_copy_args", "has_file_extension",
    "has_star_modifiers", "has_unsafe_group_by", "has_unsafe_grouping_or_scalar_agg",
    "has_unsafe_rel_args", "is_steward_approved_eq", "is_unsafe_external_table_ref",
    "is_unsafe_table_node", "output_steward_ordinal", "select_has_scalar_aggregate",
    "source_aliases", "unwrap_parens",
]
