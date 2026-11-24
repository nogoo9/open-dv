"""DML & MERGE/INSERT write-target resolution helpers for Unified SQL AST Verifier.

Strictly Layer 2a: imports only sqlglot and stdlib (plus `_ast_stmt_rules`).
"""

from __future__ import annotations

from sqlglot import exp

from opendv.storage._ast_stmt_rules import collect_source_tables, table_label, unwrap_target_table

_CteMap = dict[str, list[exp.Table]]


def _resolve_with_clause(with_clause: exp.With, base_map: _CteMap) -> _CteMap:
    cte_map = dict(base_map)
    for cte in with_clause.expressions:
        if isinstance(cte, exp.CTE) and (cname := (cte.alias_or_name or "").lower()):
            cte_map[cname] = _extract_query_source_tables(cte.this, cte_map)
    return cte_map


def _extract_query_source_tables(expr: exp.Expr | None, cte_map: _CteMap) -> list[exp.Table]:
    if expr is None:
        return []
    out: list[exp.Table] = []
    stack: list[tuple[exp.Expr, _CteMap]] = [(expr, cte_map)]
    while stack:
        cur, cur_ctes = stack.pop()
        while isinstance(cur, exp.Subquery):
            if isinstance(cur_w := cur.args.get("with_"), exp.With):
                cur_ctes = _resolve_with_clause(cur_w, cur_ctes)
            if cur.this is None:
                break
            cur = cur.this
        if isinstance(node_w := cur.args.get("with_"), exp.With):
            cur_ctes = _resolve_with_clause(node_w, cur_ctes)
        if isinstance(cur, exp.SetOperation):
            for part in (cur.this, cur.expression):
                if part is not None:
                    stack.append((part, cur_ctes))
        elif isinstance(cur, exp.Select):
            src_entries: list[tuple[str, exp.Table]] = []
            subqueries: list[tuple[str, exp.Subquery]] = []
            collect_source_tables(cur.args.get("from_"), src_entries, subqueries_out=subqueries)
            for j in cur.args.get("joins") or ():
                collect_source_tables(j, src_entries, subqueries_out=subqueries)
            for _, tbl in src_entries:
                if not tbl.db and tbl.name and tbl.name.lower() in cur_ctes:
                    out.extend(cur_ctes[tbl.name.lower()])
                else:
                    out.append(tbl)
            stack.extend((sq, cur_ctes) for _, sq in subqueries if sq.this is not None)
    return out


def _resolve_cte_table_map(stmt: exp.Expr) -> _CteMap:
    withs: list[exp.With] = []
    cur: exp.Expr | None = stmt
    while cur is not None:
        w = cur.args.get("with_")
        if isinstance(w, exp.With):
            withs.append(w)
        cur = cur.parent
    if not withs:
        return {}
    cte_map: dict[str, list[exp.Table]] = {}
    for w in reversed(withs):
        cte_map = _resolve_with_clause(w, cte_map)
    return cte_map


def _is_tsql_top_pseudo_table(node: exp.Expr) -> bool:
    if not isinstance(node, exp.Table):
        return False
    alias = node.args.get("alias")
    return (
        not node.db
        and node.name.upper() == "TOP"
        and isinstance(node.this, exp.Identifier)
        and not node.this.quoted
        and isinstance(alias, exp.TableAlias)
        and bool(alias.columns)
    )


def resolve_dml_targets(
    stmt: exp.Drop | exp.TruncateTable | exp.Update | exp.Delete,
    *,
    dialect: str = "",
) -> list[exp.Table]:
    alias_map: dict[str, exp.Table] = {}
    sub_alias_map: dict[str, list[exp.Table]] = {}
    cte_map: dict[str, list[exp.Table]] = {}
    real_delete_tables = (
        [t for t in (stmt.args.get("tables") or ()) if not _is_tsql_top_pseudo_table(t)]
        if isinstance(stmt, exp.Delete)
        else []
    )
    if isinstance(stmt, (exp.Update, exp.Delete)):
        src_entries: list[tuple[str, exp.Table]] = []
        src_subqueries: list[tuple[str, exp.Subquery]] = []
        if real_delete_tables:
            collect_source_tables(stmt.this, src_entries, subqueries_out=src_subqueries)
        elif isinstance(stmt.this, exp.Expr):
            for j in stmt.this.args.get("joins") or ():
                collect_source_tables(j, src_entries, subqueries_out=src_subqueries)
        collect_source_tables(stmt.args.get("from_"), src_entries, subqueries_out=src_subqueries)
        using_arg = stmt.args.get("using")
        for u in (using_arg if isinstance(using_arg, list) else (using_arg,)):
            if isinstance(u, exp.Expr):
                collect_source_tables(u, src_entries, subqueries_out=src_subqueries)
        for j in stmt.args.get("joins") or ():
            collect_source_tables(j, src_entries, subqueries_out=src_subqueries)
        for k, st in src_entries:
            prev = alias_map.get(k)
            if prev is None or st.db or st.alias or not (prev.db or prev.alias):
                alias_map[k] = st
        cte_map = _resolve_cte_table_map(stmt)
        for sq_alias, sq in src_subqueries:
            if sq_alias:
                sub_alias_map.setdefault(sq_alias.lower(), []).extend(
                    _extract_query_source_tables(sq, cte_map)
                )

    if real_delete_tables:
        cand_nodes: list[exp.Expr | None] = list(real_delete_tables)
    else:
        extra_tbls = () if isinstance(stmt, exp.Delete) else (stmt.args.get("tables") or ())
        cand_nodes = [stmt.this, *extra_tbls, *(stmt.args.get("expressions") or ())]

    resolved: list[exp.Table] = []
    for n in cand_nodes:
        t = unwrap_target_table(n)
        if t is None:
            if isinstance(n, exp.Subquery):
                resolved.extend(_extract_query_source_tables(n, cte_map))
            continue
        resolved.append(t)
        if not t.db and t.name:
            k = t.name.lower()
            prefer_cte = k in cte_map and (
                (isinstance(stmt, exp.Delete) and not real_delete_tables)
                or (isinstance(stmt, exp.Update) and dialect != "tsql")
            )
            if prefer_cte:
                resolved.extend(c for c in cte_map[k] if c is not t)
            elif (mapped := alias_map.get(k)) is not None:
                if mapped is not t:
                    resolved.append(mapped)
                if not mapped.db and mapped.name:
                    resolved.extend(cte_map.get(mapped.name.lower(), ()))
            elif k in sub_alias_map:
                resolved.extend(sub_alias_map[k])
            else:
                resolved.extend(c for c in cte_map.get(k, ()) if c is not t)
    return resolved


def resolve_write_targets(stmt: exp.Merge | exp.Insert) -> list[exp.Table]:
    cte_map = _resolve_cte_table_map(stmt)
    target_node = stmt.this.this if isinstance(stmt.this, exp.Schema) else stmt.this
    m_target = unwrap_target_table(target_node)
    if m_target is None:
        if isinstance(target_node, exp.Subquery):
            return _extract_query_source_tables(target_node, cte_map)
        return []
    targets = [m_target]
    if not m_target.db and m_target.name:
        for cte_tbl in cte_map.get(m_target.name.lower(), ()):
            if cte_tbl is not m_target:
                targets.append(cte_tbl)
    return targets


_EXTERNAL_INSERT_SINKS: frozenset[str] = frozenset({
    "s3", "s3cluster", "file", "url", "remote", "remotesecure", "hdfs", "azure",
    "azureblobstorage", "gcs", "read_parquet", "read_csv", "read_csv_auto",
    "read_json", "read_json_auto", "read_ndjson", "read_text", "parquet_scan",
    "csv_scan", "iceberg_scan", "delta_scan", "sqlite_scan", "postgres_scan",
    "mysql_scan", "odbc", "jdbc", "mysql", "postgresql", "sqlite", "kafka",
})


def nontable_insert_label(stmt: exp.Insert, dialect: str) -> str | None:
    """Return offending label if INSERT targets a table function, subquery, or non-table sink."""
    ins_tgt = stmt.this.this if isinstance(stmt.this, exp.Schema) else stmt.this
    bad_schema = isinstance(stmt.this, exp.Schema) and (
        any(not isinstance(e, (exp.Identifier, exp.Column)) for e in stmt.this.expressions)
        or (
            isinstance(ins_tgt, exp.Table)
            and not ins_tgt.db
            and (ins_tgt.name or "").lower() in _EXTERNAL_INSERT_SINKS
        )
    )
    if (
        stmt.args.get("is_function")
        or bad_schema
        or (ins_tgt is not None and unwrap_target_table(ins_tgt) is None)
    ):
        if isinstance(ins_tgt, exp.Table):
            return table_label(ins_tgt)
        return ins_tgt.sql(dialect=dialect)[:80] if isinstance(ins_tgt, exp.Expr) else "sql_ast"
    return None


resolve_merge_targets = resolve_write_targets

__all__ = [
    "nontable_insert_label",
    "resolve_dml_targets",
    "resolve_merge_targets",
    "resolve_write_targets",
]
