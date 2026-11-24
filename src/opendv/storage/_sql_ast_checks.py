"""Private AST-inspection helpers for Unified SQL AST Verifier (`sql_ast_verifier.py`).

Strictly Layer 2a: imports only sqlglot and stdlib.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlglot import exp

from opendv.storage._ast_stmt_rules import (
    RAW_VAULT_PREFIXES,
    RAW_VAULT_SCHEMAS,
    SAT_PREFIXES,
    check_alter_stmt,
    check_create_stmt,
    check_detach_stmt,
    check_drop_schema_or_db,
    func_name,
    has_external_copy_args,
    is_raw_vault_table,
    is_unsafe_external_table_ref,
    table_has_raw_vault_schema,
    table_label,
    unwrap_parens,
    unwrap_target_table,
)
from opendv.storage._dml_target_resolver import (
    nontable_insert_label,
    resolve_dml_targets,
    resolve_merge_targets,
    resolve_write_targets,
)
from opendv.storage._guard_common import (
    has_unsafe_group_by,
    select_has_scalar_aggregate,
)
from opendv.storage._steward_guard import (
    HASH_DIGEST_FUNCS,
    has_steward_approved_guard,
    is_zero_key_system_seed,
)

_CteAsts = dict[str, exp.Expr | None]


def _extend_cte_asts(node: exp.Expr | None, base_map: _CteAsts) -> _CteAsts:
    if node is None or not isinstance(w := node.args.get("with_"), exp.With):
        return base_map
    out = dict(base_map)
    for c in w.expressions:
        if isinstance(c, exp.CTE) and (cn := (c.alias_or_name or "").lower()):
            out[cn] = c.this
    return out


def _rf_join(j: exp.Join) -> bool:
    s = (j.side or "").upper()
    return s in ("RIGHT", "FULL") or ("OUTER" in (j.kind or "").upper() and s != "LEFT")


def _from_rels(sel: exp.Select) -> list[exp.Expr]:
    fm = sel.args.get("from_")
    if not isinstance(fm, exp.From):
        return []
    return ([fm.this] if fm.this is not None else []) + list(fm.expressions or ())


_SUBQ_CLAUSES: tuple[str, ...] = (
    "order", "limit", "offset", "where", "qualify", "having",
    "prewhere", "group", "distinct", "laterals", "windows", "sample", "with_",
)
_SUBQ_AGG_CLAUSES: frozenset[str] = frozenset({
    "order", "limit", "offset", "where", "qualify", "having",
    "prewhere", "distinct", "windows",
})


def _is_subquery_show_table(node: exp.Table) -> bool:
    ta = node.args.get("alias")
    return (
        not node.db
        and not node.catalog
        and isinstance(node.this, exp.Identifier)
        and not node.this.quoted
        and node.this.name.upper() == "SHOW"
        and isinstance(node.parent, exp.Subquery)
        and (
            not node.alias
            or (
                isinstance(ta, exp.TableAlias)
                and not ta.columns
                and isinstance(ta.this, exp.Identifier)
            )
        )
    )


def _table_base_flags(
    node: exp.Table, ctes: _CteAsts, vis: frozenset[str] = frozenset(),
) -> tuple[bool, bool]:
    if is_unsafe_external_table_ref(node) or _is_subquery_show_table(node):
        return False, False
    if not node.db and not node.catalog and node.name:
        cn = node.name.lower()
        if cn == "dual":
            return False, False
        if cn in ctes:
            return (False, False) if cn in vis else _rel_flags(ctes[cn], ctes, vis | {cn})
    return bool(node.name), (
        is_raw_vault_table(node) or (node.name or "").lower().startswith(("hub_", "ref_"))
    )


def _apply_joins_flags(
    safe: bool, rv: bool, joins: object, ctes: _CteAsts, vis: frozenset[str],
) -> tuple[bool, bool]:
    for j in (joins if isinstance(joins, list) else ()):
        if not isinstance(j, exp.Join):
            return False, rv
        jb, jrv = _rel_flags(j.this, ctes, vis)
        rv, sd = rv or jrv, (j.side or "").upper()
        if sd == "RIGHT":
            safe = jb
        elif sd != "LEFT":
            safe = safe and jb
        safe, rv = _apply_joins_flags(safe, rv, j.args.get("joins"), ctes, vis)
    return safe, rv


def _rel_flags(
    node: exp.Expr | None, ctes: _CteAsts, vis: frozenset[str] = frozenset(),
) -> tuple[bool, bool]:
    if isinstance(node, (exp.Subquery, exp.Paren)):
        ctes = _extend_cte_asts(node, ctes)
        safe, rv = _rel_flags(node.this, ctes, vis)
        return _apply_joins_flags(safe, rv, node.args.get("joins"), ctes, vis)
    if node is None or isinstance(
        node,
        (
            exp.Values, exp.Unnest, exp.TableFromRows, exp.Lateral,
            exp.Pivot, exp.Summarize, exp.Describe, exp.Show,
        ),
    ):
        return False, False
    if isinstance(node, exp.Table):
        safe, rv = _table_base_flags(node, ctes, vis)
        return _apply_joins_flags(safe, rv, node.args.get("joins"), ctes, vis)
    if isinstance(node, exp.Select):
        ctes = _extend_cte_asts(node, ctes)
        rels = _from_rels(node)
        if not rels:
            return False, False
        ff = [_rel_flags(r, ctes, vis) for r in rels]
        safe, rv = all(b for b, _ in ff), any(s for _, s in ff)
        return _apply_joins_flags(safe, rv, node.args.get("joins"), ctes, vis)
    if isinstance(node, exp.SetOperation):
        ctes = _extend_cte_asts(node, ctes)
        b1, s1 = _rel_flags(node.this, ctes, vis)
        b2, s2 = _rel_flags(node.expression, ctes, vis)
        return (b1 and b2), (s1 or s2)
    return False, False


def _is_safe_grouped_left_join(
    j: exp.Join, left_safe: bool, ctes: _CteAsts,
) -> exp.Select | None:
    if (
        not left_safe
        or (j.side or "").upper() != "LEFT"
        or "NATURAL" in ((j.kind or "").upper(), (j.args.get("method") or "").upper())
        or isinstance(j.this, exp.Lateral)
        or j.args.get("pivots")
        or j.args.get("sample")
    ):
        return None
    cur: exp.Expr | None = j.this
    while isinstance(cur, (exp.Subquery, exp.Paren)):
        if cur.this is None or any(cur.args.get(k) for k in _SUBQ_CLAUSES) or cur.args.get("joins"):
            return None
        cur = cur.this
    if not isinstance(cur, exp.Select) or any(
        cur.args.get(k) is not None for k in ("with_", "having", "qualify", "pivots", "sample")
    ):
        return None
    rels = _from_rels(cur)
    if not rels or not all(_rel_flags(r, ctes)[0] for r in rels):
        return None
    jstack = list(cur.args.get("joins") or ())
    while jstack:
        ij = jstack.pop()
        if (
            not isinstance(ij, exp.Join)
            or _rf_join(ij)
            or "NATURAL" in ((ij.kind or "").upper(), (ij.args.get("method") or "").upper())
            or not _rel_flags(ij.this, ctes)[0]
        ):
            return None
        jstack.extend(ij.args.get("joins") or ())
    grp = cur.args.get("group")
    if not isinstance(grp, exp.Group) or has_unsafe_group_by(grp):
        return None
    return cur if all(isinstance(unwrap_parens(g), exp.Column) for g in grp.expressions) else None


def iter_query_and_cte_selects(
    stmt: exp.Insert | exp.Merge,
) -> tuple[list[exp.Select], list[exp.Pivot], bool]:
    out: list[exp.Select] = []
    pivots_out: list[exp.Pivot] = []
    bad_outer_join = False
    seen_ids: set[int] = set()
    exempt_ids: set[int] = set()
    stack: list[tuple[exp.Expr | None, _CteAsts]] = []

    def _push_with(node: exp.Expr | None, cc: _CteAsts) -> _CteAsts:
        if node is not None and isinstance(w := node.args.get("with_"), exp.With):
            nc = _extend_cte_asts(node, cc)
            stack.extend((c.this, nc) for c in w.expressions if isinstance(c, exp.CTE) and c.this)
            return nc
        return cc

    def _scan(val: object, cc: _CteAsts, *, check_agg: bool = False) -> None:
        nonlocal bad_outer_join
        for n in (val if isinstance(val, list) else (val,)):
            if isinstance(n, exp.Expr):
                stack.extend((sub, cc) for sub in n.find_all(exp.Subquery, exp.Select))
                if (
                    check_agg
                    and not isinstance(n, (exp.Subquery, exp.Select))
                    and select_has_scalar_aggregate(n)
                ):
                    bad_outer_join = True

    def _extras(node: exp.Expr, cc: _CteAsts) -> None:
        stack.extend((p, cc) for p in (node.args.get("pivots") or ()))
        _scan(node.args.get("sample"), cc)

    def _proc_joins(jseq: Sequence[object], cum_safe: bool, cc: _CteAsts) -> None:
        nonlocal bad_outer_join
        jl = [j for j in jseq if isinstance(j, exp.Join)]
        for idx, j in enumerate(jl):
            jb, jrv = _rel_flags(j.this, cc)
            if _rf_join(j) and (not jb or jrv):
                bad_outer_join = True
            later_rf = any(_rf_join(jk) for jk in jl[idx + 1 :])
            if (
                cum_safe
                and not later_rf
                and (s := _is_safe_grouped_left_join(j, True, cc)) is not None
            ):
                exempt_ids.add(id(s))
            _extras(j, cc)
            stack.append((j.this, cc))
            _scan(j.args.get("on"), cc)
            _proc_joins(j.args.get("joins") or (), cum_safe, cc)
            js = (j.side or "").upper()
            if js == "RIGHT":
                cum_safe = jb and not jrv
            elif js != "LEFT":
                cum_safe = cum_safe and jb

    root_ctes = _push_with(stmt, {})
    if isinstance(stmt, exp.Insert):
        stack.append((stmt.expression, root_ctes))
    else:
        stack.append((stmt.args.get("using"), root_ctes))
        for k in ("on", "whens"):
            _scan(stmt.args.get(k), root_ctes)
    while stack:
        cur, cc = stack.pop()
        if cur is None or id(cur) in seen_ids:
            continue
        seen_ids.add(id(cur))
        cc = _push_with(cur, cc)
        while isinstance(cur, (exp.Subquery, exp.Paren, exp.Lateral)):
            _extras(cur, cc)
            if isinstance(cur, exp.Subquery):
                if cur.args.get("group") is not None or cur.args.get("having") is not None:
                    bad_outer_join = True
                for k in _SUBQ_CLAUSES:
                    _scan(cur.args.get(k), cc, check_agg=(k in _SUBQ_AGG_CLAUSES))
            ls = False if isinstance(cur, exp.Lateral) else _rel_flags(cur.this, cc)[0]
            _proc_joins(cur.args.get("joins") or (), ls, cc)
            if cur.this is None:
                break
            cur = cur.this
            cc = _push_with(cur, cc)
        if isinstance(cur, exp.Table):
            if _is_subquery_show_table(cur):
                bad_outer_join = True
            _extras(cur, cc)
            _proc_joins(cur.args.get("joins") or (), _table_base_flags(cur, cc)[0], cc)
        elif isinstance(cur, exp.Join):
            _proc_joins((cur,), False, cc)
        elif isinstance(cur, (exp.Summarize, exp.Describe, exp.Show)):
            bad_outer_join = True
        elif isinstance(cur, exp.Pivot):
            pivots_out.append(cur)
            stack.append((cur.this, cc))
            for k in ("expressions", "using", "fields", "group"):
                _scan(cur.args.get(k), cc)
        elif isinstance(cur, exp.Select):
            if id(cur) not in exempt_ids:
                out.append(cur)
            _extras(cur, cc)
            rels = _from_rels(cur)
            stack.extend((r, cc) for r in rels)
            for k in (
                "expressions", "where", "qualify", "having", "prewhere", "order",
                "limit", "offset", "distinct", "group", "laterals", "windows", "sample",
            ):
                _scan(cur.args.get(k), cc)
            cs = bool(rels) and all(_rel_flags(r, cc)[0] for r in rels)
            _proc_joins(cur.args.get("joins") or (), cs, cc)
        elif isinstance(cur, exp.SetOperation):
            stack.extend(((cur.this, cc), (cur.expression, cc)))
            for k in ("order", "limit", "offset", "with_"):
                _scan(cur.args.get(k), cc, check_agg=(k != "with_"))
        elif isinstance(cur, (exp.Values, exp.Unnest)):
            _scan(cur.expressions, cc)
    return out, pivots_out, bad_outer_join


__all__ = [
    "HASH_DIGEST_FUNCS", "RAW_VAULT_PREFIXES", "RAW_VAULT_SCHEMAS", "SAT_PREFIXES",
    "check_alter_stmt", "check_create_stmt", "check_detach_stmt", "check_drop_schema_or_db",
    "func_name", "has_external_copy_args", "has_steward_approved_guard", "is_raw_vault_table",
    "is_unsafe_external_table_ref", "is_zero_key_system_seed", "iter_query_and_cte_selects",
    "nontable_insert_label", "resolve_dml_targets", "resolve_merge_targets",
    "resolve_write_targets", "select_has_scalar_aggregate", "table_has_raw_vault_schema",
    "table_label", "unwrap_target_table",
]
