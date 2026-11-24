"""Structural DV-AST-007 steward_gate_status guard and Zero-Key SYSTEM_SEED verifier.

Strictly Layer 2a: imports only sqlglot and stdlib.
"""

from __future__ import annotations

from sqlglot import exp

from opendv.storage._guard_common import (
    collect_merge_insert_whens,
    extract_where_approved_qualifiers,
    has_col_alias_list,
    has_cross_table_col_eq,
    has_star_modifiers,
    has_unsafe_grouping_or_scalar_agg,
    has_unsafe_rel_args,
    is_unsafe_table_node,
    output_steward_ordinal,
    source_aliases,
    unwrap_parens,
)
from opendv.storage._hash_funcs import HASH_DIGEST_FUNCS
from opendv.storage._seed_guard import is_zero_key_system_seed

_CteEntry = tuple[exp.Expr | None, bool]


def _extend_cte_defs(
    node: exp.Expr,
    base_defs: dict[str, _CteEntry],
    string_token_starts: frozenset[int] | set[int] = frozenset(),
) -> dict[str, _CteEntry]:
    w = node.args.get("with_")
    if not isinstance(w, exp.With):
        return base_defs
    cte_defs = dict(base_defs)
    for cte in w.expressions:
        if not isinstance(cte, exp.CTE):
            continue
        name = (cte.alias_or_name or "").lower()
        if not name:
            continue
        cte_defs[name] = (None, False)
        guarded = _is_query_guarded(
            cte.this, cte_defs, string_token_starts=string_token_starts
        )
        cte_defs[name] = (None if has_col_alias_list(cte) else cte.this, guarded)
    return cte_defs


def _is_source_guarded(
    src: exp.Expr | None,
    cte_defs: dict[str, _CteEntry],
    visiting: frozenset[str] = frozenset(),
    string_token_starts: frozenset[int] | set[int] = frozenset(),
) -> bool:
    if src is None or has_unsafe_rel_args(src, string_token_starts):
        return False
    if isinstance(src, exp.Subquery):
        if (
            isinstance(src.this, (exp.Select, exp.SetOperation, exp.Subquery))
            or src.args.get("with_") is not None
        ):
            return _is_query_guarded(
                src, cte_defs, visiting=visiting, string_token_starts=string_token_starts
            )
        return _is_source_guarded(src.this, cte_defs, visiting, string_token_starts)
    if isinstance(src, exp.Table):
        if is_unsafe_table_node(src, string_token_starts):
            return False
        if not src.db and not src.catalog and src.name:
            entry = cte_defs.get(src.name.lower())
            return bool(entry[1]) if entry is not None else False
        return False
    return False


def _source_allows_outer_guard(
    src: exp.Expr | None,
    cte_defs: dict[str, _CteEntry],
    visiting: frozenset[str] = frozenset(),
    string_token_starts: frozenset[int] | set[int] = frozenset(),
) -> bool:
    """Return True iff an outer `steward_gate_status = 'APPROVED'` genuinely guards base rows."""
    if src is None or has_unsafe_rel_args(src, string_token_starts):
        return False
    if isinstance(src, exp.Subquery):
        if has_col_alias_list(src):
            return _is_source_guarded(src, cte_defs, visiting, string_token_starts)
        if (
            isinstance(src.this, (exp.Select, exp.SetOperation, exp.Subquery))
            or src.args.get("with_") is not None
        ):
            return _query_allows_outer_steward_guard(
                src, cte_defs, visiting=visiting, string_token_starts=string_token_starts
            )
        return _source_allows_outer_guard(src.this, cte_defs, visiting, string_token_starts)
    if isinstance(src, exp.Table):
        if is_unsafe_table_node(src, string_token_starts):
            return False
        if not src.db and not src.catalog and src.name:
            cname = src.name.lower()
            if cname in cte_defs:
                if cname in visiting:
                    return False
                cte_expr, cte_guarded = cte_defs[cname]
                if cte_guarded:
                    return True
                if has_col_alias_list(src):
                    return False
                return _query_allows_outer_steward_guard(
                    cte_expr,
                    cte_defs,
                    visiting=visiting | {cname},
                    string_token_starts=string_token_starts,
                )
        return not has_col_alias_list(src)
    return False


def _flatten_set_op(
    expr: exp.Expr | None,
    cte_defs: dict[str, _CteEntry],
    string_token_starts: frozenset[int] | set[int] = frozenset(),
) -> list[tuple[exp.Expr | None, dict[str, _CteEntry]]]:
    if expr is None or has_unsafe_rel_args(expr, string_token_starts):
        return [(None, cte_defs)]
    local_ctes = _extend_cte_defs(expr, cte_defs, string_token_starts)
    if isinstance(expr, exp.Subquery):
        if has_col_alias_list(expr):
            return [(expr, local_ctes)]
        return _flatten_set_op(expr.this, local_ctes, string_token_starts)
    if isinstance(expr, exp.SetOperation):
        return _flatten_set_op(expr.this, local_ctes, string_token_starts) + _flatten_set_op(
            expr.expression, local_ctes, string_token_starts
        )
    return [(expr, local_ctes)]


def _select_outer_guard(
    expr: exp.Select,
    local_ctes: dict[str, _CteEntry],
    visiting: frozenset[str],
    *,
    allow_star: bool,
    string_token_starts: frozenset[int] | set[int] = frozenset(),
) -> tuple[bool, int | None]:
    if has_unsafe_rel_args(expr, string_token_starts) or has_unsafe_grouping_or_scalar_agg(expr):
        return False, None
    from_ = expr.args.get("from_")
    if (
        not isinstance(from_, exp.From)
        or from_.this is None
        or has_unsafe_rel_args(from_.this, string_token_starts)
    ):
        return False, None
    joins = [j for j in (expr.args.get("joins") or ()) if isinstance(j, exp.Join)]

    matched_col_tables: list[tuple[int, str]] = []
    star_tables: list[str] = []
    for idx, proj in enumerate(expr.expressions):
        if isinstance(proj, exp.Alias):
            if (proj.alias or "").lower() == "steward_gate_status":
                inner = unwrap_parens(proj.this)
                if (
                    isinstance(inner, exp.Column)
                    and not isinstance(inner.this, exp.Star)
                    and inner.name.lower() == "steward_gate_status"
                ):
                    matched_col_tables.append((idx, (inner.table or "").lower()))
                else:
                    return False, None
        elif isinstance(proj, exp.Column):
            if isinstance(proj.this, exp.Star):
                if has_star_modifiers(proj.this):
                    return False, None
                star_tables.append((proj.table or "").lower())
            elif proj.name.lower() == "steward_gate_status":
                matched_col_tables.append((idx, (proj.table or "").lower()))
        elif isinstance(proj, exp.Star):
            if has_star_modifiers(proj):
                return False, None
            star_tables.append("")

    if not allow_star and star_tables:
        return False, None

    matched_ord: int | None = None
    if matched_col_tables:
        if star_tables or len(matched_col_tables) != 1:
            return False, None
        matched_ord, q_tbl = matched_col_tables[0]
        if not q_tbl:
            if joins:
                return False, None
            guarded_quals = source_aliases(from_.this, string_token_starts)
        else:
            guarded_quals = {q_tbl}
    elif star_tables:
        if joins:
            if len(star_tables) != 1 or not star_tables[0]:
                return False, None
            guarded_quals = {star_tables[0]}
        else:
            fa = source_aliases(from_.this, string_token_starts)
            if star_tables[0] and star_tables[0] not in fa:
                return False, None
            guarded_quals = fa
    else:
        return False, None

    if not guarded_quals:
        return False, None
    ok = _is_query_guarded(
        expr,
        local_ctes,
        extra_where_quals=guarded_quals,
        visiting=visiting,
        string_token_starts=string_token_starts,
    )
    return ok, (matched_ord if ok and not star_tables else None)


def _query_allows_outer_steward_guard(
    expr: exp.Expr | None,
    cte_defs: dict[str, _CteEntry],
    visiting: frozenset[str] = frozenset(),
    string_token_starts: frozenset[int] | set[int] = frozenset(),
) -> bool:
    if expr is None or has_unsafe_rel_args(expr, string_token_starts):
        return False
    local_ctes = _extend_cte_defs(expr, cte_defs, string_token_starts)
    while isinstance(expr, exp.Subquery):
        if has_unsafe_rel_args(expr, string_token_starts) or expr.this is None:
            return False
        if has_col_alias_list(expr):
            return _is_query_guarded(
                expr.this,
                local_ctes,
                visiting=visiting,
                string_token_starts=string_token_starts,
            )
        expr = expr.this
        if has_unsafe_rel_args(expr, string_token_starts):
            return False
        local_ctes = _extend_cte_defs(expr, local_ctes, string_token_starts)
    if isinstance(expr, exp.SetOperation):
        if _is_query_guarded(
            expr, local_ctes, visiting=visiting, string_token_starts=string_token_starts
        ):
            return True
        branches = _flatten_set_op(expr, local_ctes, string_token_starts)
        b0, ctes0 = branches[0]
        if not isinstance(b0, exp.Select):
            return False
        target_ord = (
            output_steward_ordinal(b0)
            if _is_query_guarded(
                b0, ctes0, visiting=visiting, string_token_starts=string_token_starts
            )
            else _select_outer_guard(
                b0, ctes0, visiting, allow_star=False, string_token_starts=string_token_starts
            )[1]
        )
        if target_ord is None:
            return False
        for bi, ctesi in branches[1:]:
            if not isinstance(bi, exp.Select):
                return False
            if not _is_query_guarded(
                bi, ctesi, visiting=visiting, string_token_starts=string_token_starts
            ):
                oki, ordi = _select_outer_guard(
                    bi,
                    ctesi,
                    visiting,
                    allow_star=False,
                    string_token_starts=string_token_starts,
                )
                if not oki or ordi != target_ord:
                    return False
        return True
    if not isinstance(expr, exp.Select):
        return False
    if _is_query_guarded(
        expr, local_ctes, visiting=visiting, string_token_starts=string_token_starts
    ):
        return True
    ok, _ = _select_outer_guard(
        expr,
        local_ctes,
        visiting,
        allow_star=True,
        string_token_starts=string_token_starts,
    )
    return ok


def _is_query_guarded(
    expr: exp.Expr | None,
    cte_defs: dict[str, _CteEntry],
    *,
    extra_where_quals: frozenset[str] | set[str] = frozenset(),
    visiting: frozenset[str] = frozenset(),
    string_token_starts: frozenset[int] | set[int] = frozenset(),
) -> bool:
    if expr is None or has_unsafe_rel_args(expr, string_token_starts):
        return False
    local_ctes = _extend_cte_defs(expr, cte_defs, string_token_starts)
    while isinstance(expr, exp.Subquery):
        if has_unsafe_rel_args(expr, string_token_starts) or expr.this is None:
            return False
        expr = expr.this
        if has_unsafe_rel_args(expr, string_token_starts):
            return False
        local_ctes = _extend_cte_defs(expr, local_ctes, string_token_starts)
    if isinstance(expr, exp.SetOperation):
        return _is_query_guarded(
            expr.this,
            local_ctes,
            extra_where_quals=extra_where_quals,
            visiting=visiting,
            string_token_starts=string_token_starts,
        ) and _is_query_guarded(
            expr.expression,
            local_ctes,
            extra_where_quals=extra_where_quals,
            visiting=visiting,
            string_token_starts=string_token_starts,
        )
    if not isinstance(expr, exp.Select) or has_unsafe_grouping_or_scalar_agg(expr):
        return False

    from_ = expr.args.get("from_")
    if not isinstance(from_, exp.From) or from_.this is None:
        return False
    joins = [j for j in (expr.args.get("joins") or ()) if isinstance(j, exp.Join)]

    where = expr.args.get("where")
    has_unqual_where, where_quals = extract_where_approved_qualifiers(
        where.this if isinstance(where, exp.Where) else None
    )
    eff_quals = where_quals | set(extra_where_quals)

    def _src_guarded(src: exp.Expr | None) -> bool:
        if _is_source_guarded(src, local_ctes, visiting, string_token_starts):
            return True
        sa = source_aliases(src, string_token_starts)
        return bool(eff_quals & sa) and _source_allows_outer_guard(
            src, local_ctes, visiting, string_token_starts
        )

    if not joins:
        from_src = from_.this
        if _is_source_guarded(from_src, local_ctes, visiting, string_token_starts):
            return True
        from_aliases = source_aliases(from_src, string_token_starts)
        if has_unqual_where or bool(eff_quals & from_aliases):
            return _source_allows_outer_guard(
                from_src, local_ctes, visiting, string_token_starts
            )
        return False

    acc_aliases = source_aliases(from_.this, string_token_starts)
    if not acc_aliases:
        return False
    acc_guarded = _src_guarded(from_.this)
    poisoned = False

    for j in joins:
        if isinstance(j.this, (exp.Lateral, exp.Unnest)) or has_unsafe_rel_args(
            j, string_token_starts
        ):
            return False
        side = (j.side or "").upper()
        kind = (j.kind or "").upper()
        method = (j.args.get("method") or "").upper()
        if side in ("RIGHT", "FULL") or ("OUTER" in kind and side != "LEFT"):
            return False

        j_aliases = source_aliases(j.this, string_token_starts)
        if not j_aliases or (acc_aliases & j_aliases):
            return False
        j_guarded = _src_guarded(j.this)
        has_using = bool(j.args.get("using")) and bool(j_aliases)
        is_natural = method == "NATURAL" or kind == "NATURAL"
        is_constrained = (not is_natural) and (
            has_using or has_cross_table_col_eq(j.args.get("on"), acc_aliases, j_aliases)
        )

        if side == "LEFT":
            if acc_guarded and j_guarded:
                acc_aliases |= j_aliases
                continue
            if acc_guarded and not j_guarded:
                if not is_constrained:
                    poisoned = True
                acc_aliases |= j_aliases
                continue
            acc_guarded = False
            poisoned = True
            acc_aliases |= j_aliases
            continue

        if is_natural or kind == "CROSS" or (
            side == "" and kind == "" and j.args.get("on") is None and not j.args.get("using")
        ):
            if not (acc_guarded and j_guarded):
                acc_guarded = False
                poisoned = True
            acc_aliases |= j_aliases
            continue

        if side == "" and kind in ("", "INNER"):
            if acc_guarded and j_guarded:
                acc_aliases |= j_aliases
                continue
            if not acc_guarded and not j_guarded:
                if not is_constrained:
                    poisoned = True
                acc_aliases |= j_aliases
                continue
            if is_constrained:
                acc_guarded = True
            else:
                acc_guarded = False
                poisoned = True
            acc_aliases |= j_aliases
            continue

        return False

    return acc_guarded and not poisoned


def has_steward_approved_guard(
    stmt: exp.Expr,
    *,
    string_token_starts: frozenset[int] | set[int] = frozenset(),
) -> bool:
    """Return True iff every row path into INSERT/MERGE enforces steward_gate_status='APPROVED'."""
    local_ctes = _extend_cte_defs(stmt, {}, string_token_starts)
    if isinstance(stmt, exp.Insert):
        if isinstance(stmt.expression, exp.Values):
            return False
        return _is_query_guarded(
            stmt.expression, local_ctes, string_token_starts=string_token_starts
        )
    if isinstance(stmt, exp.Merge):
        using_node = stmt.args.get("using")
        if _is_source_guarded(using_node, local_ctes, string_token_starts=string_token_starts):
            return True
        insert_whens = collect_merge_insert_whens(stmt)
        if not insert_whens or not _source_allows_outer_guard(
            using_node, local_ctes, string_token_starts=string_token_starts
        ):
            return False
        using_aliases = source_aliases(using_node, string_token_starts)
        for w in insert_whens:
            has_unqual, qual_set = extract_where_approved_qualifiers(w.args.get("condition"))
            if qual_set:
                if not (qual_set <= using_aliases):
                    return False
            elif not has_unqual:
                return False
        return True
    return False


__all__ = ["HASH_DIGEST_FUNCS", "has_steward_approved_guard", "is_zero_key_system_seed"]
