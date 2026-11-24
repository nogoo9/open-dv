"""AST-hardened read-only SQL query validation guard for DuckDB storage queries.

Strictly Layer 2a: imports only opendv.models, sqlglot, and stdlib.
"""

from __future__ import annotations

import sqlglot
from sqlglot import TokenType, exp

from opendv.models.errors import ErrorCode, OpenDVError

_FORBIDDEN_SQL_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,
    exp.Set,
    exp.Pragma,
    exp.Attach,
    exp.Detach,
    exp.Copy,
    exp.Use,
    exp.Merge,
    exp.TruncateTable,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.LoadData,
    exp.Export,
    exp.Into,
    exp.Describe,
    exp.Show,
)

_FORBIDDEN_SQL_FUNCTIONS: frozenset[str] = frozenset(
    {
        "glob",
        "duckdb_secrets",
        "copy",
        "attach",
        "detach",
        "getenv",
        "current_setting",
        "http_get",
        "http_post",
        "query",
        "query_table",
        "iceberg_scan",
        "delta_scan",
        "parquet_scan",
        "csv_scan",
        "sqlite_attach",
        "postgres_attach",
        "mysql_attach",
        "read_csv",
        "read_csv_auto",
        "read_parquet",
        "read_json",
        "read_json_auto",
        "read_text",
        "read_blob",
    }
)

_FORBIDDEN_FUNC_PREFIXES: tuple[str, ...] = (
    "read_",
    "write_",
    "duckdb_",
    "pragma_",
    "sqlite_",
    "pg_",
    "postgres_",
    "mysql_",
    "iceberg_",
    "delta_",
    "s3_",
    "httpfs_",
)

_ALLOWED_SQL_FUNCTIONS: frozenset[str] = frozenset(
    {
        "abs",
        "any_value",
        "array",
        "array_agg",
        "avg",
        "case",
        "cast",
        "ceil",
        "ceiling",
        "coalesce",
        "concat",
        "concat_ws",
        "count",
        "count_if",
        "current_date",
        "date_diff",
        "datediff",
        "date_part",
        "date_trunc",
        "dense_rank",
        "distinct",
        "encode",
        "extract",
        "first",
        "first_value",
        "floor",
        "greatest",
        "group_concat",
        "hex",
        "if",
        "ifnull",
        "instr",
        "json_extract",
        "lag",
        "last",
        "last_value",
        "lead",
        "least",
        "left",
        "length",
        "list_agg",
        "lower",
        "lower_hex",
        "ltrim",
        "map",
        "max",
        "min",
        "nullif",
        "octet_length",
        "rank",
        "regexp_matches",
        "regexp_replace",
        "replace",
        "right",
        "round",
        "row_number",
        "rtrim",
        "sha2",
        "sha256",
        "split_part",
        "str_position",
        "strftime",
        "string_agg",
        "struct",
        "substr",
        "substring",
        "sum",
        "time_to_str",
        "timestamp_trunc",
        "to_map",
        "trim",
        "try_cast",
        "typeof",
        "unhex",
        "upper",
    }
)

_FILE_EXT_SUFFIXES: tuple[str, ...] = (
    ".parquet",
    ".csv",
    ".json",
    ".jsonl",
    ".ndjson",
    ".orc",
    ".avro",
    ".txt",
    ".ipc",
    ".feather",
    ".arrow",
    ".xml",
    ".yaml",
    ".yml",
    ".duckdb",
    ".sqlite",
    ".db",
    ".tsv",
    ".xlsx",
    ".gz",
    ".bz2",
    ".xz",
    ".zst",
    ".zip",
    ".tar",
)

_COMPRESSION_SUFFIXES: tuple[str, ...] = (".gz", ".bz2", ".xz", ".zst", ".zip")


def _has_file_extension(name: str) -> bool:
    cur = name.lower()
    if cur.endswith(_FILE_EXT_SUFFIXES):
        return True
    while True:
        stripped = False
        for cext in _COMPRESSION_SUFFIXES:
            if cur.endswith(cext):
                cur = cur[: -len(cext)]
                stripped = True
                if cur.endswith(_FILE_EXT_SUFFIXES):
                    return True
        if not stripped:
            return False


def _reject_sql(msg: str) -> None:
    raise OpenDVError(msg, error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT)


def validate_readonly_query_sql(sql: str) -> None:
    """Enforce read-only SELECT/WITH AST allowlist and file/system-function denylist."""
    if not sql or not sql.strip():
        _reject_sql("SQL query cannot be empty.")

    try:
        tokens = sqlglot.tokenize(sql, read="duckdb")
    except Exception as exc:  # noqa: BLE001
        _reject_sql(f"SQL tokenization error: {exc}")

    tok_by_start: dict[int, TokenType] = {}
    prev_tok_type: TokenType | None = None
    for tok in tokens:
        tok_by_start[tok.start] = tok.token_type
        if tok.token_type == TokenType.SEMICOLON:
            _reject_sql("Semicolons and multi-statement SQL are forbidden in storage/query.")
        if tok.token_type == TokenType.STRING and prev_tok_type in (
            TokenType.FROM,
            TokenType.JOIN,
        ):
            _reject_sql("Direct file-path or literal table targets are forbidden.")
        prev_tok_type = tok.token_type

    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception as exc:  # noqa: BLE001
        _reject_sql(f"SQL parse error: {exc}")

    valid_stmts = [s for s in statements if s is not None]
    if len(valid_stmts) != 1:
        _reject_sql("storage/query requires exactly one SELECT statement.")

    stmt = valid_stmts[0]
    if not isinstance(stmt, (exp.Select, exp.Union)):
        _reject_sql(f"Only SELECT queries are permitted (got {type(stmt).__name__}).")

    for node in stmt.walk():
        if isinstance(node, _FORBIDDEN_SQL_TYPES):
            _reject_sql(f"Forbidden SQL statement/clause: {type(node).__name__}.")
        if isinstance(node, exp.Table):
            if isinstance(node.this, (exp.Literal, exp.Paren)) or any(
                isinstance(part, exp.Identifier)
                and part.quoted
                and tok_by_start.get(part.meta.get("start", -1)) == TokenType.STRING
                for part in (node.this, node.args.get("db"), node.args.get("catalog"))
            ):
                _reject_sql("Direct file-path or literal table targets are forbidden.")
            tname = (node.name or "").lower()
            tcat = (node.catalog or "").lower()
            tdb = (node.db or "").lower()
            if any(
                ch in part for part in (tname, tdb, tcat) for ch in ("/", "\\", ":")
            ) or _has_file_extension(tname):
                _reject_sql(f"File-backed table reference '{node.name}' is forbidden.")
            if (
                tname in _FORBIDDEN_SQL_FUNCTIONS
                or tname in ("sqlite_master", "sqlite_temp_master")
                or tname.startswith(_FORBIDDEN_FUNC_PREFIXES)
                or tname.endswith("_scan")
                or tcat == "system"
                or tdb in ("pg_catalog", "sys", "system")
            ):
                _reject_sql(f"System or table-valued view '{node.name}' is forbidden.")
        if isinstance(node, (exp.Func, exp.Anonymous)) and not isinstance(
            node, (exp.Binary, exp.Unary, exp.Connector)
        ):
            # Check Anonymous BEFORE sql_name() (exp.Anonymous.sql_name() == 'ANONYMOUS')
            fname = (
                node.name.lower() if isinstance(node, exp.Anonymous) else node.sql_name().lower()
            )
            if (
                fname in _FORBIDDEN_SQL_FUNCTIONS
                or fname.startswith(_FORBIDDEN_FUNC_PREFIXES)
                or fname.endswith("_scan")
            ):
                _reject_sql(f"Forbidden SQL function '{fname}' in storage/query.")
            if fname not in _ALLOWED_SQL_FUNCTIONS:
                _reject_sql(f"SQL function '{fname}' is not in the read-only allowlist.")


__all__ = ["validate_readonly_query_sql"]
