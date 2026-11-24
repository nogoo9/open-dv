"""Unified SQL AST Verifier (`EVAL-AST-01`, `Alg A.6` `DV-AST-001..007`, `<50ms`/req).

Strictly Layer 2a: imports only opendv.models, dialects, sqlglot, and stdlib.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

import sqlglot
from sqlglot import TokenType, exp
from sqlglot import errors as sqlglot_errors

from opendv.models.diagnostics import VerifierDiagnostic
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.storage import _sql_ast_checks as _chk
from opendv.storage.dialects import get_dialect_profile

_TOKEN_SCAN_RE = re.compile(
    r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|\\.|[^'])*'|\"(?:\"\"|[^\"])*\"|"
    r"`[^`]*`|\[[^\]]*\]|\$(?P<dtag>(?:[A-Za-z_][A-Za-z0-9_]*)?)\$[\s\S]*?\$(?P=dtag)\$|"
    r"\b(?:BEGIN|CASE|END)\b|;",
    re.IGNORECASE,
)

_EXEC_FUNCS: frozenset[str] = frozenset({"EXEC", "EXECUTE", "EXECUTE_IMMEDIATE"})

_NONDETERMINISTIC_FUNCS: frozenset[str] = frozenset(
    {
        "NOW", "CURRENT_TIMESTAMP", "SYSDATE", "GETDATE", "GETUTCDATE",
        "RANDOM", "RAND", "UUID", "GEN_RANDOM_UUID", "NEWID",
    }
)

_CHAR_LEN_FUNCS: frozenset[str] = frozenset({"STRLEN", "CHAR_LENGTH", "CHARACTER_LENGTH"})

_BYTE_LEN_FUNCS: frozenset[str] = frozenset({"OCTET_LENGTH", "BYTE_LENGTH", "DATALENGTH", "LENGTH"})

_FORBIDDEN_STMT_NODES: tuple[type[exp.Expr], ...] = tuple(
    c
    for c in (
        exp.Execute,
        exp.ExecuteSql,
        exp.Set,
        exp.Pragma,
        exp.Use,
        exp.Export,
        exp.Attach,
        exp.LoadData,
        getattr(exp, "Install", None),
        getattr(exp, "Put", None),
        getattr(exp, "ListStage", None),
        getattr(exp, "Get", None),
        getattr(exp, "Remove", None),
    )
    if c is not None
)

_HASH_DIGEST_FUNCS: frozenset[str] = _chk.HASH_DIGEST_FUNCS

_has_steward_approved_guard = _chk.has_steward_approved_guard
_is_zero_key_system_seed = _chk.is_zero_key_system_seed


def _diag(
    rule_id: str,
    entity_name: str,
    message: str,
    *,
    severity: str = "BLOCKER",
    gate: str = "V6",
) -> VerifierDiagnostic:
    return VerifierDiagnostic(
        gate=gate,  # type: ignore[arg-type]
        rule_id=rule_id,
        severity=severity,  # type: ignore[arg-type]
        entity_name=entity_name,
        waivable=False,
        message=message,
    )


def _check_hash_expr_purity(
    alias_node: exp.Alias,
    dialect: str,
    entity_label: str,
    diags: list[tuple[str, str, str]],
) -> None:
    alias_name = alias_node.alias.lower()
    expr_body = alias_node.this
    if isinstance(expr_body, (exp.Column, exp.Literal)):
        return

    has_byte_len = False
    has_null_token = False
    has_hash_func = False
    has_cols = False
    for sub in expr_body.walk():
        if isinstance(sub, exp.Column):
            has_cols = True
        elif isinstance(sub, (exp.Func, exp.Anonymous, exp.CurrentTimestamp)):
            if isinstance(sub, exp.CurrentTimestamp):
                msg = f"Non-deterministic timestamp inside hash expression '{alias_name}'."
                diags.append(("DV-AST-004", entity_label, msg))
                continue
            fname = _chk.func_name(sub)
            if fname in _HASH_DIGEST_FUNCS:
                has_hash_func = True
            if fname in _NONDETERMINISTIC_FUNCS:
                msg = f"Non-deterministic function {fname}() forbidden inside '{alias_name}'."
                diags.append(("DV-AST-004", entity_label, msg))
            if fname in _CHAR_LEN_FUNCS or (fname == "LENGTH" and dialect != "clickhouse"):
                msg = f"Char-length function {fname}() forbidden in Alg A.5 '{alias_name}'."
                diags.append(("DV-AST-002", entity_label, msg))
            if fname in _BYTE_LEN_FUNCS and (fname != "LENGTH" or dialect == "clickhouse"):
                has_byte_len = True
        elif isinstance(sub, exp.Literal) and sub.is_string and sub.this == "-1:^^":
            has_null_token = True

    if has_cols and has_hash_func and not has_byte_len:
        msg = f"Hash expression '{alias_name}' missing UTF-8 byte-length prefix."
        diags.append(("DV-AST-002", entity_label, msg))
    if has_cols and has_hash_func and not has_null_token:
        msg = f"Hash expression '{alias_name}' missing '-1:^^' null token."
        diags.append(("DV-AST-003", entity_label, msg))


@lru_cache(maxsize=16)
def _get_dialect_tools(dialect: str) -> tuple[Any, Any]:
    d_obj = sqlglot.Dialect.get_or_raise(dialect)
    return d_obj.tokenizer(), d_obj.parser(error_level=sqlglot_errors.ErrorLevel.RAISE)


def _split_sql_statements(sql: str, *, track_blocks: bool = False) -> list[str]:
    chunks: list[str] = []
    start = 0
    depth = 0
    for m in _TOKEN_SCAN_RE.finditer(sql):
        tok = m.group(0)
        if tok == ";":
            if depth == 0:
                part = sql[start : m.start()].strip()
                if part:
                    chunks.append(part)
                start = m.end()
        elif track_blocks:
            up = tok.upper()
            if up in ("BEGIN", "CASE"):
                depth += 1
            elif up == "END" and depth > 0:
                depth -= 1
    tail = sql[start:].strip()
    if tail:
        chunks.append(tail)
    return chunks


def _check_sat_dv_ast_006(
    stmt: exp.Insert | exp.Merge,
    rv_targets: list[exp.Table],
    diags: list[tuple[str, str, str]],
) -> None:
    sat_targets = [t for t in rv_targets if (t.name or "").lower().startswith(_chk.SAT_PREFIXES)]
    if not sat_targets:
        return
    sat_lbl = _chk.table_label(sat_targets[0])
    selects, pivots, bad_outer_join = _chk.iter_query_and_cte_selects(stmt)
    has_piv = any(not p.args.get("unpivot") for p in pivots)
    has_sel = any(
        sel.args.get("group") is not None
        or sel.args.get("having") is not None
        or _chk.select_has_scalar_aggregate(sel)
        for sel in selects
    )
    if bad_outer_join or has_piv or has_sel:
        msg = f"Top-level GROUP BY/HAVING or scalar aggregate forbidden on {sat_lbl}."
        diags.append(("DV-AST-006", sat_lbl, msg))


def _inspect_statement(
    stmt: exp.Expr,
    dialect: str,
    diags: list[tuple[str, str, str]],
    tables_seen: set[str],
    dml_targets: list[tuple[str, bool]],
    string_token_starts: frozenset[int] = frozenset(),
) -> None:
    if isinstance(stmt, (exp.Alias, exp.Column)):
        msg = f"Unsupported SQL statement in {dialect}: {stmt.sql(dialect=dialect)[:120]}"
        diags.append(("EVAL-AST-01", "sql_ast", msg))
        return
    tbl_ctx: exp.Table | None = None
    for sub in stmt.walk():
        if isinstance(sub, exp.Command):
            snippet = sub.sql(dialect=dialect)[:120]
            msg = f"Unsupported SQL syntax parsed as exp.Command in {dialect}: {snippet}"
            diags.append(("EVAL-AST-01", "sql_ast", msg))
            return
        if isinstance(sub, _FORBIDDEN_STMT_NODES):
            msg = f"Dynamic SQL or session/external statement {type(sub).__name__} forbidden."
            diags.append(("EVAL-AST-01", "sql_ast", msg))
            return
        if isinstance(sub, exp.Table):
            if tbl_ctx is None:
                tbl_ctx = sub
            if sub.name:
                tables_seen.add(sub.name.lower())
            if _chk.is_unsafe_external_table_ref(sub, string_token_starts):
                lbl = _chk.table_label(sub)
                msg = f"External file-path or string-literal table reference forbidden: {lbl}."
                diags.append(("EVAL-AST-01", lbl, msg))
        elif isinstance(sub, exp.TableFromRows):
            lbl = sub.sql(dialect=dialect)[:80]
            diags.append(("EVAL-AST-01", lbl, f"External TVF table reference forbidden: {lbl}."))
        elif isinstance(sub, exp.Lateral):
            lat_target = sub.this
            while isinstance(lat_target, (exp.Subquery, exp.Paren)):
                lat_target = lat_target.this
            if not isinstance(lat_target, (exp.Select, exp.SetOperation)):
                lbl = sub.sql(dialect=dialect)[:80]
                diags.append(
                    ("EVAL-AST-01", lbl, f"External LATERAL/APPLY expression forbidden: {lbl}.")
                )
        elif isinstance(sub, exp.Star):
            if not isinstance(sub.parent, exp.Count):
                msg = "SELECT * is forbidden by Alg A.6; project explicit columns."
                diags.append(("DV-AST-004", "sql_ast", msg))
        elif isinstance(sub, exp.Alias):
            aname = sub.alias
            if aname[:3].lower() == "hk_" or aname[:9].lower() == "hash_diff":
                _check_hash_expr_purity(sub, dialect, _chk.table_label(tbl_ctx), diags)
        elif isinstance(sub, (exp.Func, exp.Anonymous)):
            fname = _chk.func_name(sub)
            if fname in _EXEC_FUNCS:
                msg = f"Dynamic SQL execution {fname}() forbidden."
                diags.append(("EVAL-AST-01", "sql_ast", msg))
            if fname in _CHAR_LEN_FUNCS:
                msg = f"Character-length {fname}() forbidden by Alg A.6; use UTF-8 byte length."
                diags.append(("DV-AST-002", "sql_ast", msg))
        elif isinstance(sub, exp.Detach):
            _chk.check_detach_stmt(sub, diags)
        elif isinstance(sub, exp.Copy):
            c_target = _chk.unwrap_target_table(sub.this)
            if _chk.is_raw_vault_table(c_target) or _chk.has_external_copy_args(
                sub, c_target, string_token_starts
            ):
                lbl = _chk.table_label(c_target) if c_target is not None else "sql_ast"
                diags.append(("EVAL-AST-01", lbl, f"External COPY forbidden on {lbl}."))
        elif isinstance(sub, exp.Create):
            _chk.check_create_stmt(sub, diags)
        elif isinstance(sub, exp.Into):
            into_target = _chk.unwrap_target_table(sub.this)
            if _chk.is_raw_vault_table(into_target):
                lbl = _chk.table_label(into_target)
                diags.append(("EVAL-AST-01", lbl, f"SELECT INTO forbidden on Z2 Raw Vault {lbl}."))
        elif isinstance(sub, (exp.Drop, exp.TruncateTable, exp.Update, exp.Delete)):
            if isinstance(sub, exp.Drop) and isinstance(sub.parent, exp.Alter):
                continue
            if (
                isinstance(sub, (exp.Update, exp.Delete))
                and sub.find_ancestor(exp.When) is not None
            ):
                continue
            targets = _chk.resolve_dml_targets(sub, dialect=dialect)
            if not targets and tbl_ctx is not None:
                targets = [tbl_ctx]
            if isinstance(sub, exp.Drop):
                _chk.check_drop_schema_or_db(sub, targets, diags)
            for target in targets:
                if _chk.is_raw_vault_table(target):
                    lbl = _chk.table_label(target)
                    msg = f"Destructive {type(sub).__name__} forbidden on Z2 Raw Vault {lbl}."
                    diags.append(("EVAL-AST-01", lbl, msg))
        elif isinstance(sub, exp.Merge):
            targets = _chk.resolve_write_targets(sub)
            target_names = {(t.name or "").lower() for t in targets if t.name}
            has_guard = _has_steward_approved_guard(
                sub, string_token_starts=string_token_starts
            ) or _is_zero_key_system_seed(sub, target_names)
            for tgt in targets:
                if tgt.name:
                    dml_targets.append((tgt.name.lower(), has_guard))
            rv_targets = [t for t in targets if _chk.is_raw_vault_table(t)]
            if rv_targets:
                lbl = _chk.table_label(rv_targets[0])
                for when in sub.find_all(exp.When):
                    then_node = when.args.get("then")
                    is_del_var = (
                        isinstance(then_node, exp.Var) and str(then_node.this).upper() == "DELETE"
                    )
                    if is_del_var or when.find(exp.Update, exp.Delete) is not None:
                        msg = f"MERGE WHEN MATCHED UPDATE/DELETE forbidden on Z2 {lbl}."
                        diags.append(("EVAL-AST-01", lbl, msg))
                _check_sat_dv_ast_006(sub, rv_targets, diags)
        elif isinstance(sub, exp.Alter):
            _chk.check_alter_stmt(sub, diags)
        elif isinstance(sub, exp.Insert):
            if sub.find_ancestor(exp.When) is not None:
                continue
            if (nt_lbl := _chk.nontable_insert_label(sub, dialect)) is not None:
                diags.append(
                    ("EVAL-AST-01", nt_lbl, f"Non-table INSERT target forbidden: {nt_lbl}.")
                )
                continue
            targets = _chk.resolve_write_targets(sub)
            target_names = {(t.name or "").lower() for t in targets if t.name}
            has_guard = _has_steward_approved_guard(
                sub, string_token_starts=string_token_starts
            ) or _is_zero_key_system_seed(sub, target_names)
            for tgt in targets:
                if tgt.name:
                    dml_targets.append((tgt.name.lower(), has_guard))
            rv_targets = [t for t in targets if _chk.is_raw_vault_table(t)]
            if rv_targets:
                lbl = _chk.table_label(rv_targets[0])
                alt = str(sub.args.get("alternative") or "").upper()
                if sub.args.get("overwrite") or (alt and alt != "IGNORE"):
                    kind_txt = "OVERWRITE" if sub.args.get("overwrite") else f"OR {alt}"
                    msg = f"INSERT {kind_txt} forbidden on Z2 Raw Vault {lbl}."
                    diags.append(("EVAL-AST-01", lbl, msg))
                conflict = sub.args.get("conflict")
                if isinstance(conflict, exp.OnConflict):
                    act_node = conflict.args.get("action")
                    act_txt = str(
                        act_node.this if isinstance(act_node, exp.Var) else act_node or ""
                    ).upper()
                    if (
                        conflict.expressions
                        or "UPDATE" in act_txt
                        or conflict.args.get("duplicate")
                    ):
                        msg = f"INSERT ON CONFLICT UPDATE forbidden on Z2 Raw Vault {lbl}."
                        diags.append(("EVAL-AST-01", lbl, msg))
                _check_sat_dv_ast_006(sub, rv_targets, diags)


@lru_cache(maxsize=2048)
def _verify_chunk_cached(
    dialect: str,
    chunk: str,
) -> tuple[tuple[tuple[str, str, str], ...], tuple[str, ...], tuple[tuple[str, bool], ...]]:
    tokenizer, parser = _get_dialect_tools(dialect)
    try:
        tokens = tokenizer.tokenize(chunk)
        statements = parser.parse(tokens, chunk)
    except (sqlglot_errors.ParseError, sqlglot_errors.TokenError, ValueError) as exc:
        return ((("EVAL-AST-01", "sql_ast", f"SQL parse failure in {dialect}: {exc}"),), (), ())

    string_token_starts = frozenset(t.start for t in tokens if t.token_type == TokenType.STRING)
    diags: list[tuple[str, str, str]] = []
    tables_seen: set[str] = set()
    dml_targets: list[tuple[str, bool]] = []
    for stmt in statements:
        if stmt is not None:
            _inspect_statement(
                stmt, dialect, diags, tables_seen, dml_targets, string_token_starts
            )
    return tuple(diags), tuple(sorted(tables_seen)), tuple(dml_targets)


class SqlAstVerifier:
    """Deterministic single-pass SQL AST verifier for EVAL-AST-01 and Alg A.6."""

    def verify_sql(
        self,
        sql: str,
        target_or_dialect: str,
        *,
        ir: CertifiedVaultIR | None = None,
        extra_unstructured_links: Sequence[str] = (),
    ) -> list[VerifierDiagnostic]:
        """Verify SQL script against EVAL-AST-01 and Alg A.6 rules in a single AST pass."""
        profile = get_dialect_profile(target_or_dialect)
        dialect = profile.sqlglot_dialect or "duckdb"
        diags: list[VerifierDiagnostic] = []
        planned_hub_map = (
            {h.hub_name.lower(): h.hub_name for h in ir.hubs if h.is_planned_concept}
            if ir
            else {}
        )
        dml_targets_all: list[tuple[str, bool]] = []

        for chunk in _split_sql_statements(sql, track_blocks=(dialect == "tsql")):
            cdiags, ctables, cdml = _verify_chunk_cached(dialect, chunk)
            diags.extend(_diag(r, e, m) for r, e, m in cdiags)
            for tname in ctables:
                if (canon_name := planned_hub_map.get(tname)) is not None:
                    msg = f"Planned concept Hub '{canon_name}' must not appear in compiled SQL."
                    diags.append(_diag("DV-AST-001", canon_name, msg))
            dml_targets_all.extend(cdml)

        unstructured_map = {u.lower(): u for u in extra_unstructured_links}
        if ir is not None:
            for lnk in ir.links:
                if lnk.link_kind == "UNSTRUCTURED_EXTRACTION_LINK":
                    unstructured_map[lnk.link_name.lower()] = lnk.link_name

        if unstructured_map and dml_targets_all:
            for u_key in sorted(unstructured_map):
                u_name = unstructured_map[u_key]
                for tgt_name, has_guard in dml_targets_all:
                    if tgt_name == u_key and not has_guard:
                        msg = f"Link '{u_name}' lacks steward_gate_status = 'APPROVED' guard."
                        diags.append(_diag("DV-AST-007", u_name, msg))

        return diags


__all__ = ["SqlAstVerifier"]
