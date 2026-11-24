"""Algorithm A.5 scalar normalization, byte-length prefixing, and PredicateNode AST builders.

Strictly Layer 2a: imports only opendv.models, sqlglot, and stdlib.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from opendv.models.project import DbtSqlDialect
from opendv.models.vault_ir import (
    ArrayType,
    DecimalType,
    MapType,
    PortableLogicalType,
    PredicateBranch,
    PredicateLeaf,
    PredicateNode,
)
from opendv.storage.sql_compiler._collection_exprs import (
    TSQL_COLLATION,
    collection_norm_sql,
    duckdb_decimal_norm_sql,
)


def _parse_expr(sql: str, dialect: DbtSqlDialect) -> exp.Expr:
    node = sqlglot.parse_one(sql, read=dialect)
    if any(isinstance(sub, exp.Command) for sub in node.walk()):
        raise ValueError(f"exp.Command generated while parsing {dialect} expression: {sql}")
    return node


def _with_tsql_collation(expr_sql: str) -> str:
    return f"({expr_sql}) COLLATE {TSQL_COLLATION}"


def _text_type_sql(dialect: DbtSqlDialect) -> str:
    return {
        "duckdb": "VARCHAR",
        "databricks": "STRING",
        "snowflake": "VARCHAR",
        "postgres": "TEXT",
        "bigquery": "STRING",
        "tsql": f"VARCHAR(MAX) COLLATE {TSQL_COLLATION}",
        "redshift": "VARCHAR(65535)",
        "clickhouse": "String",
    }[dialect]


def _cast_to_text_sql(col_sql: str, dialect: DbtSqlDialect) -> str:
    if dialect == "clickhouse":
        return f"toString({col_sql})"
    if dialect == "tsql":
        return f"CAST({col_sql} AS VARCHAR(MAX)) COLLATE {TSQL_COLLATION}"
    return f"CAST({col_sql} AS {_text_type_sql(dialect)})"


def trim_ws_sql(expr_sql: str, dialect: DbtSqlDialect) -> str:
    """Trim spaces, tabs, CR, and LF across all 8 SQL dialects matching Python str.strip()."""
    if dialect == "clickhouse":
        return f"trim(BOTH concat(char(10), char(13), char(9), ' ') FROM {expr_sql})"
    if dialect == "tsql":
        return f"TRIM(CHAR(10) + CHAR(13) + CHAR(9) + ' ' FROM {expr_sql})"
    if dialect == "bigquery":
        return f"TRIM({expr_sql}, '\\n\\r\\t ')"
    if dialect == "databricks":
        return f"BTRIM({expr_sql}, '\\n\\r\\t ')"
    chars = "CHR(10) || CHR(13) || CHR(9) || ' '"
    if dialect in ("postgres", "redshift"):
        return f"BTRIM({expr_sql}, {chars})"
    return f"TRIM({expr_sql}, {chars})"


def _text_norm_sql(col_sql: str, dialect: DbtSqlDialect, *, is_business_key: bool) -> str:
    if dialect == "clickhouse":
        base = trim_ws_sql(
            f"replaceAll(replaceAll(toString({col_sql}), "
            "concat(char(13), char(10)), char(10)), char(13), char(10))",
            dialect,
        )
        cased = f"upper({base})" if is_business_key else base
        return f"nullIf({cased}, '')"

    if dialect == "tsql":
        casted = f"CAST({col_sql} AS VARCHAR(MAX)) COLLATE {TSQL_COLLATION}"
        replaced = f"REPLACE(REPLACE({casted}, CHAR(13) + CHAR(10), CHAR(10)), CHAR(13), CHAR(10))"
        trimmed = trim_ws_sql(replaced, dialect)
        cased = f"UPPER({trimmed})" if is_business_key else trimmed
        return f"NULLIF({cased}, '') COLLATE {TSQL_COLLATION}"

    if dialect in ("databricks", "bigquery"):
        casted = _cast_to_text_sql(col_sql, dialect)
        replaced = f"REPLACE(REPLACE({casted}, '\\r\\n', '\\n'), '\\r', '\\n')"
    else:
        casted = _cast_to_text_sql(col_sql, dialect)
        replaced = f"REPLACE(REPLACE({casted}, CHR(13) || CHR(10), CHR(10)), CHR(13), CHR(10))"
    trimmed = trim_ws_sql(replaced, dialect)
    cased = f"UPPER({trimmed})" if is_business_key else trimmed
    return f"NULLIF({cased}, '')"


def _boolean_norm_sql(col_sql: str, dialect: DbtSqlDialect) -> str:
    if dialect == "clickhouse":
        return f"if(isNull({col_sql}), NULL, if({col_sql}, 'true', 'false'))"
    if dialect == "tsql":
        return (
            f"CASE WHEN {col_sql} IS NULL THEN NULL WHEN {col_sql} = 1 "
            f"THEN 'true' ELSE 'false' END COLLATE {TSQL_COLLATION}"
        )
    return f"CASE WHEN {col_sql} IS NULL THEN NULL WHEN {col_sql} THEN 'true' ELSE 'false' END"


def _date_norm_sql(col_sql: str, dialect: DbtSqlDialect) -> str:
    if dialect == "duckdb":
        return f"STRFTIME(CAST({col_sql} AS DATE), '%Y-%m-%d')"
    if dialect == "databricks":
        return f"DATE_FORMAT(CAST({col_sql} AS DATE), 'yyyy-MM-dd')"
    if dialect in ("snowflake", "postgres", "redshift"):
        return f"TO_CHAR(CAST({col_sql} AS DATE), 'YYYY-MM-DD')"
    if dialect == "bigquery":
        return f"FORMAT_DATE('%Y-%m-%d', CAST({col_sql} AS DATE))"
    if dialect == "tsql":
        return f"CONVERT(CHAR(10), CAST({col_sql} AS DATE), 23) COLLATE {TSQL_COLLATION}"
    return (
        f"if(isNull({col_sql}), NULL, "
        f"formatDateTime(toDate32(assumeNotNull({col_sql})), '%Y-%m-%d'))"
    )


def _timestamp_norm_sql(col_sql: str, dialect: DbtSqlDialect) -> str:
    if dialect == "duckdb":
        return (
            f"STRFTIME(CAST({col_sql} AS TIMESTAMPTZ) AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ')"
        )
    if dialect == "databricks":
        return (
            f"DATE_FORMAT(TO_UTC_TIMESTAMP(CAST({col_sql} AS TIMESTAMP), 'UTC'), "
            "'yyyy-MM-dd\\'T\\'HH:mm:ss.SSSSSS\\'Z\\'')"
        )
    if dialect == "snowflake":
        return (
            f"TO_CHAR(CONVERT_TIMEZONE('UTC', CAST({col_sql} AS TIMESTAMP_TZ(6))), "
            '\'YYYY-MM-DD"T"HH24:MI:SS.FF6"Z"\')'
        )
    if dialect == "postgres":
        return (
            f"TO_CHAR(CAST({col_sql} AS TIMESTAMPTZ(6)) AT TIME ZONE 'UTC', "
            '\'YYYY-MM-DD"T"HH24:MI:SS.US"Z"\')'
        )
    if dialect == "bigquery":
        return f"FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%E6SZ', CAST({col_sql} AS TIMESTAMP), 'UTC')"
    if dialect == "tsql":
        return (
            f"REPLACE(CONVERT(VARCHAR(30), CAST({col_sql} AS DATETIMEOFFSET(6)) "
            f"AT TIME ZONE 'UTC', 127), '+00:00', 'Z') COLLATE {TSQL_COLLATION}"
        )
    if dialect == "redshift":
        return (
            f"TO_CHAR(CONVERT_TIMEZONE('UTC', CAST({col_sql} AS TIMESTAMPTZ)), "
            '\'YYYY-MM-DD"T"HH24:MI:SS.US"Z"\')'
        )
    dt64 = f"toDateTime64(assumeNotNull({col_sql}), 6, 'UTC')"
    return (
        f"if(isNull({col_sql}), NULL, concat(formatDateTime({dt64}, '%Y-%m-%dT%H:%i:%s'), "
        f"'.', substring(toString({dt64}), 21, 6), 'Z'))"
    )


def _decimal_norm_sql(col_sql: str, dialect: DbtSqlDialect) -> str:
    if dialect == "duckdb":
        return duckdb_decimal_norm_sql(col_sql)
    if dialect == "clickhouse":
        an = f"assumeNotNull({col_sql})"
        return (
            f"if(isNull({col_sql}), NULL, if({an} = 0, '0', "
            f"replaceRegexpOne(replaceRegexpOne(toString({an}), "
            "'(\\.\\d*?)0+$', '\\1'), '\\.$', '')))"
        )
    if dialect == "tsql":
        c = f"CAST({col_sql} AS VARCHAR(MAX))"
        stripped = f"RTRIM(REPLACE(RTRIM(REPLACE({c}, '0', ' ')), ' ', '0'))"
        no_dot = (
            f"CASE WHEN RIGHT({stripped}, 1) = '.' "
            f"THEN SUBSTRING({stripped}, 1, DATALENGTH({stripped}) - 1) ELSE {stripped} END"
        )
        return (
            f"CASE WHEN {col_sql} IS NULL THEN NULL WHEN {col_sql} = 0 THEN '0' "
            f"WHEN CHARINDEX('.', {c}) > 0 THEN {no_dot} ELSE {c} END "
            f"COLLATE {TSQL_COLLATION}"
        )
    c = _cast_to_text_sql(col_sql, dialect)
    if dialect == "databricks":
        r1 = f"REGEXP_REPLACE({c}, '(\\\\.\\\\d*?)0+$', '$1')"
        r2 = f"REGEXP_REPLACE({r1}, '\\\\.$', '')"
    elif dialect == "bigquery":
        r1 = f"REGEXP_REPLACE({c}, r'(\\.\\d*?)0+$', r'\\1')"
        r2 = f"REGEXP_REPLACE({r1}, r'\\.$', '')"
    else:
        r1 = f"REGEXP_REPLACE({c}, '(\\\\.\\\\d*?)0+$', '\\\\1')"
        r2 = f"REGEXP_REPLACE({r1}, '\\\\.$', '')"
    return f"CASE WHEN {col_sql} IS NULL THEN NULL WHEN {col_sql} = 0 THEN '0' ELSE {r2} END"


def format_scalar_sql(
    col_sql: str,
    logical_type: PortableLogicalType | None,
    dialect: DbtSqlDialect,
    *,
    is_business_key: bool = True,
) -> str:
    """Render canonical Alg A.5 scalar normalization SQL for a column expression."""
    if isinstance(logical_type, (ArrayType, MapType)):
        return collection_norm_sql(col_sql, logical_type, dialect)
    if logical_type == "BOOLEAN":
        return _boolean_norm_sql(col_sql, dialect)
    if logical_type == "DATE32":
        return _date_norm_sql(col_sql, dialect)
    if logical_type == "TIMESTAMP_UTC_US":
        return _timestamp_norm_sql(col_sql, dialect)
    if isinstance(logical_type, DecimalType):
        return _decimal_norm_sql(col_sql, dialect)
    if logical_type in ("INT32", "INT64"):
        return _cast_to_text_sql(col_sql, dialect)
    return _text_norm_sql(col_sql, dialect, is_business_key=is_business_key)


def encode_token_sql(norm_sql: str, dialect: DbtSqlDialect) -> str:
    """Wrap normalized scalar SQL into `<utf8_octet_len>:<val>` or `'-1:^^'`."""
    if dialect == "duckdb":
        blen = f"CAST(OCTET_LENGTH(ENCODE({norm_sql})) AS VARCHAR)"
        return f"COALESCE({blen} || ':' || {norm_sql}, '-1:^^')"
    if dialect == "databricks":
        blen = f"CAST(OCTET_LENGTH(CAST({norm_sql} AS STRING)) AS STRING)"
        return f"COALESCE({blen} || ':' || {norm_sql}, '-1:^^')"
    if dialect == "snowflake":
        blen = f"CAST(OCTET_LENGTH(CAST({norm_sql} AS VARCHAR)) AS VARCHAR)"
        return f"COALESCE({blen} || ':' || {norm_sql}, '-1:^^')"
    if dialect == "postgres":
        blen = f"CAST(OCTET_LENGTH(CAST({norm_sql} AS TEXT)) AS TEXT)"
        return f"COALESCE({blen} || ':' || {norm_sql}, '-1:^^')"
    if dialect == "bigquery":
        blen = f"CAST(BYTE_LENGTH(CAST({norm_sql} AS STRING)) AS STRING)"
        return f"COALESCE(CONCAT({blen}, ':', {norm_sql}), '-1:^^')"
    if dialect == "tsql":
        blen = f"CAST(DATALENGTH({norm_sql}) AS VARCHAR(20)) COLLATE {TSQL_COLLATION}"
        null_tok = f"'-1:^^' COLLATE {TSQL_COLLATION}"
        return f"COALESCE({blen} + ':' + {norm_sql}, {null_tok})"
    if dialect == "redshift":
        blen = f"CAST(OCTET_LENGTH(CAST({norm_sql} AS VARCHAR(65535))) AS VARCHAR)"
        return f"COALESCE({blen} || ':' || {norm_sql}, '-1:^^')"
    return (
        f"if(isNull({norm_sql}), '-1:^^', "
        f"concat(toString(length(assumeNotNull({norm_sql}))), ':', assumeNotNull({norm_sql})))"
    )


def format_scalar_ast(
    col_expr: exp.Expr,
    logical_type: PortableLogicalType | None,
    dialect: DbtSqlDialect,
    *,
    is_business_key: bool = True,
) -> exp.Expr:
    """Build a parsed sqlglot AST for Alg A.5 scalar normalization."""
    col_sql = col_expr.sql(dialect=dialect)
    return _parse_expr(
        format_scalar_sql(col_sql, logical_type, dialect, is_business_key=is_business_key),
        dialect,
    )


def encode_token_ast(norm_expr: exp.Expr, dialect: DbtSqlDialect) -> exp.Expr:
    """Build a parsed sqlglot AST for `<utf8_octet_len>:<val>` token wrapping."""
    return _parse_expr(encode_token_sql(norm_expr.sql(dialect=dialect), dialect), dialect)


def predicate_to_sqlglot(
    node: PredicateNode,
    *,
    table_alias: str | None = None,
) -> exp.Expr:
    """Compile a PredicateNode AST into a sqlglot boolean condition expression."""
    if isinstance(node, PredicateBranch):
        if not node.children:
            return exp.true() if node.op == "AND" else exp.false()
        compiled = [predicate_to_sqlglot(child, table_alias=table_alias) for child in node.children]
        acc = compiled[0]
        for nxt in compiled[1:]:
            acc = (
                exp.And(this=acc, expression=nxt)
                if node.op == "AND"
                else exp.Or(this=acc, expression=nxt)
            )
        return exp.Paren(this=acc)

    if isinstance(node, PredicateLeaf):
        col = exp.column(node.column, table=table_alias)
        if node.op == "IS_NOT_NULL":
            return exp.Not(this=exp.Is(this=col, expression=exp.Null()))
        lit = node.literal
        if lit is None:
            return exp.false()
        if isinstance(lit, bool):
            val_expr: exp.Expr = exp.Boolean(this=lit)
        elif isinstance(lit, (int, float)):
            val_expr = exp.Literal.number(lit)
        else:
            val_expr = exp.Literal.string(str(lit))
        return exp.EQ(this=col, expression=val_expr)

    raise TypeError(f"Unsupported PredicateNode: {type(node).__name__}")


__all__ = [
    "TSQL_COLLATION",
    "encode_token_ast",
    "encode_token_sql",
    "format_scalar_ast",
    "format_scalar_sql",
    "predicate_to_sqlglot",
    "trim_ws_sql",
]
