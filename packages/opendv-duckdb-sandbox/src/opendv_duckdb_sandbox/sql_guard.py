"""Strict AST-based SQL allowlist guard and DuckDB external-access verification."""

from __future__ import annotations

import duckdb
import sqlglot
from sqlglot import TokenType, exp

FORBIDDEN_SQL_TYPE_NAMES: tuple[str, ...] = (
    "Insert",
    "Update",
    "Delete",
    "Drop",
    "Create",
    "Alter",
    "Command",
    "Set",
    "Pragma",
    "Attach",
    "Detach",
    "Copy",
    "Use",
    "Merge",
    "TruncateTable",
    "Transaction",
    "Commit",
    "Rollback",
    "LoadData",
    "Export",
    "Import",
)

FORBIDDEN_SQL_TYPES: tuple[type[exp.Expression], ...] = tuple(
    t for name in FORBIDDEN_SQL_TYPE_NAMES if (t := getattr(exp, name, None)) is not None
)

FORBIDDEN_SQL_FUNCTIONS: frozenset[str] = frozenset(
    {
        "read_csv",
        "read_json",
        "read_ndjson",
        "read_ndjson_auto",
        "read_text",
        "read_blob",
        "scan_parquet",
        "scan_csv",
        "parquet_scan",
        "csv_scan",
        "json_scan",
        "duckdb_query",
        "query",
        "query_table",
        "load",
        "install",
        "copy",
        "attach",
        "detach",
        "export_database",
        "import_database",
        "write_blob",
        "write_parquet",
        "write_csv",
        "getenv",
        "current_setting",
        "glob",
        "http_get",
        "http_post",
    }
)

FORBIDDEN_FUNCTION_PREFIXES: tuple[str, ...] = (
    "read_",
    "write_",
    "duckdb_",
    "pragma_",
    "sqlite_",
    "postgres_",
    "mysql_",
    "iceberg_",
    "delta_",
    "s3_",
    "httpfs_",
)

ALLOWED_SQL_FUNCTIONS: frozenset[str] = frozenset(
    {
        # Aggregates
        "count",
        "count_if",
        "sum",
        "avg",
        "min",
        "max",
        "any_value",
        "bool_and",
        "bool_or",
        "string_agg",
        "group_concat",
        "array_agg",
        "list",
        "approx_count_distinct",
        "median",
        "stddev",
        "variance",
        # Window functions
        "row_number",
        "rank",
        "dense_rank",
        "ntile",
        "lag",
        "lead",
        "first_value",
        "last_value",
        # Null / conditional / type
        "coalesce",
        "nullif",
        "ifnull",
        "if",
        "iif",
        "case",
        "cast",
        "try_cast",
        "typeof",
        "greatest",
        "least",
        # String functions
        "lower",
        "upper",
        "trim",
        "ltrim",
        "rtrim",
        "substr",
        "substring",
        "length",
        "char_length",
        "octet_length",
        "concat",
        "concat_ws",
        "replace",
        "split_part",
        "left",
        "right",
        "strpos",
        "instr",
        "lpad",
        "rpad",
        "reverse",
        "like",
        "ilike",
        "regexp_matches",
        "regexp_replace",
        "regexp_extract",
        # Numeric / math functions
        "abs",
        "round",
        "floor",
        "ceil",
        "ceiling",
        "mod",
        "power",
        "pow",
        "sqrt",
        "sign",
        "ln",
        "log",
        "log10",
        "exp",
        # Date / time functions
        "strftime",
        "strptime",
        "date_trunc",
        "date_part",
        "extract",
        "year",
        "month",
        "day",
        "hour",
        "minute",
        "second",
        "now",
        "current_date",
        "current_timestamp",
        "date_diff",
        "datediff",
        "age",
        # Hashing / JSON / safe struct helpers
        "sha256",
        "md5",
        "hex",
        "encode",
        "json_extract",
        "json_extract_string",
        "json_type",
        "json_valid",
        "struct_pack",
        "len",
        "array_length",
    }
)


def _extract_func_name(node: exp.Expression) -> str:
    if hasattr(node, "sql_name"):
        name = str(node.sql_name() or "").strip().lower()
        if name:
            return name
    raw = getattr(node, "name", "")
    return str(raw or "").strip().lower()


def _is_forbidden_function_name(func_name: str) -> bool:
    if func_name in FORBIDDEN_SQL_FUNCTIONS:
        return True
    if any(func_name.startswith(prefix) for prefix in FORBIDDEN_FUNCTION_PREFIXES):
        return True
    return bool(func_name.endswith("_scan"))


_FROM_CLAUSE_END_TOKENS: frozenset[TokenType] = frozenset(
    {
        TokenType.WHERE,
        TokenType.GROUP_BY,
        TokenType.HAVING,
        TokenType.ORDER_BY,
        TokenType.LIMIT,
        TokenType.OFFSET,
        TokenType.QUALIFY,
        TokenType.WINDOW,
        TokenType.UNION,
        TokenType.EXCEPT,
        TokenType.INTERSECT,
        TokenType.ON,
        TokenType.USING,
        TokenType.SET,
        TokenType.VALUES,
        TokenType.SEMICOLON,
    }
)


_FORBIDDEN_PATH_EXTS: tuple[str, ...] = (
    ".csv",
    ".tsv",
    ".parquet",
    ".pq",
    ".json",
    ".jsonl",
    ".ndjson",
    ".duckdb",
    ".ddb",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".xlsx",
    ".xls",
    ".txt",
    ".log",
    ".orc",
    ".avro",
    ".ipc",
    ".feather",
)


_COMPRESSION_EXTS: tuple[str, ...] = (
    ".gz",
    ".zst",
    ".zstd",
    ".bz2",
    ".xz",
    ".lz4",
)


def _is_suspicious_file_identifier(raw_ident: str) -> bool:
    clean = raw_ident.strip()
    if not clean:
        return False
    lower = clean.lower()
    if clean.startswith("'") or clean.endswith("'"):
        return True
    if "/" in clean or "\\" in clean or ":" in clean:
        return True
    while True:
        comp = next((ext for ext in _COMPRESSION_EXTS if lower.endswith(ext)), None)
        if not comp:
            break
        lower = lower[: -len(comp)]
    return lower.endswith(_FORBIDDEN_PATH_EXTS)


def _reject_string_literal_table_tokens(sql: str) -> None:
    """Reject string literals used directly or parenthesized as FROM/JOIN table targets."""
    in_from_target_depth: int | None = None
    in_func_arg_depth: int | None = None
    depth = 0
    prev_tt: TokenType | None = None
    for tok in sqlglot.tokenize(sql, read="duckdb"):
        tt = tok.token_type
        if tt == TokenType.L_PAREN:
            depth += 1
            if (
                in_from_target_depth is not None
                and in_func_arg_depth is None
                and prev_tt in (TokenType.VAR, TokenType.IDENTIFIER)
            ):
                in_func_arg_depth = depth
            prev_tt = tt
            continue
        if tt == TokenType.R_PAREN:
            if in_func_arg_depth is not None and depth == in_func_arg_depth:
                in_func_arg_depth = None
            if in_from_target_depth is not None and depth <= in_from_target_depth:
                in_from_target_depth = None
            depth = max(0, depth - 1)
            prev_tt = tt
            continue
        if tt in (TokenType.FROM, TokenType.JOIN):
            in_from_target_depth = depth
            in_func_arg_depth = None
            prev_tt = tt
            continue
        if in_from_target_depth is not None:
            is_subquery = (
                tt in (TokenType.SELECT, TokenType.WITH, TokenType.VALUES)
                and depth > in_from_target_depth
            )
            is_clause_end = (
                tt in _FROM_CLAUSE_END_TOKENS and depth == in_from_target_depth
            )
            if is_subquery or is_clause_end:
                in_from_target_depth = None
                in_func_arg_depth = None
            elif tt == TokenType.STRING and in_func_arg_depth is None:
                raise ValueError(
                    "Forbidden SQL table target: string-literal file/table paths are not permitted"
                )
        prev_tt = tt


def validate_safe_sql(sql: str) -> None:
    """Validate that a SQL string is a single read-only SELECT / UNION / CTE query."""
    if not sql or not sql.strip():
        raise ValueError("SQL query cannot be empty")
    _reject_string_literal_table_tokens(sql)
    try:
        statements = [stmt for stmt in sqlglot.parse(sql, read="duckdb") if stmt is not None]
    except Exception as exc:
        raise ValueError(f"Invalid SQL syntax: {exc}") from exc

    if len(statements) != 1:
        raise ValueError(
            f"CUSTOM_SQL permits exactly one statement, but {len(statements)} "
            "statements were provided"
        )

    stmt = statements[0]
    if not isinstance(stmt, (exp.Select, exp.Union)):
        raise ValueError(
            f"CUSTOM_SQL permits only read-only SELECT or UNION queries; got {type(stmt).__name__}"
        )

    for node in stmt.walk():
        if isinstance(node, FORBIDDEN_SQL_TYPES):
            raise ValueError(f"Forbidden SQL operation in sandbox: {type(node).__name__}")
        if isinstance(node, exp.Select) and node.args.get("into") is not None:
            raise ValueError("Forbidden SQL operation in sandbox: SELECT INTO is not permitted")
        if isinstance(node, (exp.From, exp.Join)):
            target = node.this
            while isinstance(target, (exp.Paren, exp.Subquery)):
                inner = target.this
                if isinstance(inner, (exp.Select, exp.Union, exp.Table)):
                    break
                target = inner
            if isinstance(target, exp.Literal):
                raise ValueError(
                    "Forbidden SQL table target: string-literal file/table paths are not permitted"
                )
        if isinstance(node, exp.Table):
            if isinstance(node.this, (exp.Literal, exp.Paren)):
                raise ValueError(
                    "Forbidden SQL table target: string-literal or parenthesized "
                    "table targets are not permitted"
                )
            for part_attr in ("this", "db", "catalog"):
                part = node.args.get(part_attr)
                if isinstance(part, exp.Identifier):
                    raw_ident = str(part.this or "")
                    if _is_suspicious_file_identifier(raw_ident):
                        raise ValueError(
                            f"Forbidden SQL table target: file/path identifier '{raw_ident}' "
                            "is not permitted"
                        )
            if node.this is not None and not isinstance(
                node.this, (exp.Identifier, exp.Anonymous, exp.Func)
            ):
                raise ValueError(
                    f"Forbidden SQL table target in sandbox: {type(node.this).__name__}"
                )
        if isinstance(node, (exp.Func, exp.Anonymous)) and not isinstance(node, exp.Connector):
            func_name = _extract_func_name(node)
            if _is_forbidden_function_name(func_name):
                raise ValueError(f"Forbidden SQL function in sandbox: {func_name}")
            if func_name not in ALLOWED_SQL_FUNCTIONS:
                raise ValueError(
                    f"SQL function '{func_name}' is not in the sandbox allowlist"
                )


def assert_external_access_disabled(conn: duckdb.DuckDBPyConnection) -> None:
    """Verify that DuckDB ``enable_external_access`` is disabled on ``conn``."""
    row = conn.execute(
        "SELECT value FROM duckdb_settings() WHERE name = 'enable_external_access'"
    ).fetchone()
    if row is None:
        raise RuntimeError("Unable to verify DuckDB enable_external_access setting")
    if str(row[0]).strip().lower() != "false":
        raise RuntimeError(
            f"DuckDB sandbox requires enable_external_access=false, got {row[0]!r}"
        )


def enforce_external_access_disabled(conn: duckdb.DuckDBPyConnection) -> None:
    """Force ``enable_external_access = false`` when unset and assert isolation."""
    row = conn.execute(
        "SELECT value FROM duckdb_settings() WHERE name = 'enable_external_access'"
    ).fetchone()
    if row is not None and str(row[0]).strip().lower() != "false":
        conn.execute("SET enable_external_access = false")
    assert_external_access_disabled(conn)


__all__ = [
    "ALLOWED_SQL_FUNCTIONS",
    "FORBIDDEN_FUNCTION_PREFIXES",
    "FORBIDDEN_SQL_FUNCTIONS",
    "FORBIDDEN_SQL_TYPES",
    "assert_external_access_disabled",
    "enforce_external_access_disabled",
    "validate_safe_sql",
]
