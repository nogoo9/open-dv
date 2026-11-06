"""SQL Hashing, Identifier Validation, and Type Normalization Helpers."""

from __future__ import annotations

import re

from opendv_core.constants import (
    DEFAULT_DELIMITER,
    NULL_TOKEN,
    ZERO_KEY_ERROR_TOKEN,
    ZERO_KEY_UNKNOWN_TOKEN,
)

_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_ident(name: str) -> str:
    """Validate that a SQL identifier matches `^[A-Za-z_][A-Za-z0-9_]*$`."""
    if not _SAFE_IDENT_RE.match(name):
        msg = f"Unsafe SQL identifier: {name!r}"
        raise ValueError(msg)
    return name


def _sql_lit(val: str) -> str:
    """Escape single quotes for inclusion in a SQL literal string."""
    return val.replace("'", "''")


def _normalize_duckdb_type(data_type: str) -> str:
    """Normalize an `IRColumnDef.data_type` into a DuckDB-compatible SQL column type."""
    upper = data_type.strip().upper()
    if upper == "CHAR(64)":
        return "CHAR(64)"
    if upper.startswith(("VARCHAR", "TEXT", "STRING", "CHAR", "BINARY", "VARBINARY")):
        return "VARCHAR"
    if upper.startswith("TIMESTAMP"):
        return "TIMESTAMP"
    if upper == "DATE":
        return "DATE"
    if upper.startswith(("DECIMAL", "NUMERIC")):
        return upper
    if upper.startswith(("BIGINT", "INT8")):
        return "BIGINT"
    if upper.startswith(("SMALLINT", "TINYINT", "INT", "INTEGER")):
        return "INTEGER"
    if upper.startswith("BOOL"):
        return "BOOLEAN"
    if upper.startswith(("DOUBLE", "FLOAT", "REAL")):
        return "DOUBLE"
    return "VARCHAR"


def build_length_prefixed_token_sql(
    col_expr: str,
    *,
    is_business_key: bool = False,
    null_token: str = NULL_TOKEN,
) -> str:
    """Build a DuckDB SQL expression encoding `col_expr` as `<len>:<val>` or `'-1:^^'`."""
    base_cast = f"TRIM(CAST({col_expr} AS VARCHAR))"
    norm_expr = f"UPPER({base_cast})" if is_business_key else base_cast
    nullif_expr = f"NULLIF({norm_expr}, '')"
    return (
        f"CASE WHEN {nullif_expr} IS NULL THEN '{null_token}' "
        f"ELSE CAST(OCTET_LENGTH(ENCODE({nullif_expr})) AS VARCHAR) || ':' || {nullif_expr} END"
    )


def build_algorithm_a5_sha256_sql(
    columns: list[str],
    *,
    is_business_key: bool = False,
    bkcc_literal: str | None = None,
    bkcc_column_expr: str | None = None,
    sort_alphabetically: bool = False,
    delimiter: str = DEFAULT_DELIMITER,
    null_token: str = NULL_TOKEN,
    error_predicate: str | None = None,
    unknown_predicate: str | None = None,
    key_columns: list[str] | None = None,
) -> str:
    """Build a DuckDB Algorithm A.5 length-prefixed SHA-256 SQL expression.

    When `is_business_key=True`, emits a 3-way `CASE` expression routing `'-2'` /
    `error_predicate` to `REPEAT('f', 64)` (`ZERO_KEY_ERROR`) and `NULL` / `''` / `'-1'` /
    `unknown_predicate` on `key_columns` (excluding constant `bkcc` / `*__bkcc` columns by
    default) to `REPEAT('0', 64)` (`ZERO_KEY_UNKNOWN`).
    """
    ordered = sorted(columns) if sort_alphabetically else list(columns)
    tokens: list[str] = []
    if is_business_key and bkcc_column_expr and bkcc_column_expr.strip():
        clean_bkcc_col = bkcc_column_expr.strip()
        ordered = [c for c in ordered if c.strip() != clean_bkcc_col] or ordered
        tokens.append(
            build_length_prefixed_token_sql(
                clean_bkcc_col,
                is_business_key=True,
                null_token=null_token,
            )
        )
    elif is_business_key and bkcc_literal and bkcc_literal.strip():
        clean_bkcc = bkcc_literal.strip().upper()
        tokens.append(f"'{len(clean_bkcc.encode('utf-8'))}:{clean_bkcc}'")
    for col in ordered:
        tokens.append(
            build_length_prefixed_token_sql(
                col,
                is_business_key=is_business_key,
                null_token=null_token,
            )
        )
    concat_sql = f" || '{delimiter}' || ".join(tokens)
    sha_expr = f"LOWER(SHA256({concat_sql}))"
    if is_business_key and ordered:
        if key_columns is not None:
            pred_cols = list(key_columns)
        else:
            non_bkcc = [
                c for c in ordered if c.lower() != "bkcc" and not c.lower().endswith("__bkcc")
            ]
            pred_cols = non_bkcc or ordered
        default_err_pred = " OR ".join(
            f"TRIM(CAST({c} AS VARCHAR)) = '{ZERO_KEY_ERROR_TOKEN}'" for c in pred_cols
        )
        err_branch_pred = (
            f"({error_predicate}) OR ({default_err_pred})" if error_predicate else default_err_pred
        )
        default_unk_pred = " OR ".join(
            f"(NULLIF(TRIM(CAST({c} AS VARCHAR)), '') IS NULL "
            f"OR TRIM(CAST({c} AS VARCHAR)) = '{ZERO_KEY_UNKNOWN_TOKEN}')"
            for c in pred_cols
        )
        unk_branch_pred = (
            f"({unknown_predicate}) OR ({default_unk_pred})"
            if unknown_predicate
            else default_unk_pred
        )
        return (
            f"CASE WHEN {err_branch_pred} THEN REPEAT('f', 64) "
            f"WHEN {unk_branch_pred} THEN REPEAT('0', 64) "
            f"ELSE {sha_expr} END"
        )
    return sha_expr


__all__ = [
    "_normalize_duckdb_type",
    "_sql_lit",
    "_validate_ident",
    "build_algorithm_a5_sha256_sql",
    "build_length_prefixed_token_sql",
]
