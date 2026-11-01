"""Algorithm A.5: SQLGlot AST Canonical Length-Prefixed SHA-256 Hash Recipe Builder.

Synthesizes and rewrites SQL staging projections to guarantee injective,
collision-free Data Vault 2.0 `hk_*`, `hk_lnk_*`, and `hash_diff` expressions:
  - Length-prefixed encoding: `CAST(OCTET_LENGTH(val) AS VARCHAR) || ':' || val`
  - Positional null token: `'-1:^^'`
  - Inter-column delimiter: `'||'`
  - All-null business key zero key: `64` hex `'0'` characters
  - Alphabetical column sorting on `hash_diff`
"""

from __future__ import annotations

from functools import reduce
from typing import Any

import sqlglot
from sqlglot import exp

NULL_TOKEN: str = "-1:^^"
ZERO_KEY: str = "0" * 64
DELIMITER: str = "||"


def build_canonical_column_standardization_ast(
    col_name: str,
    is_business_key: bool = True,
) -> exp.Expression:
    """Build `UPPER(TRIM(CAST(col AS VARCHAR)))` for BKs or `TRIM(CAST(col AS VARCHAR))`
    for Satellite payloads.
    """
    col_expr: exp.Expression = exp.cast(exp.column(col_name), to="VARCHAR")
    trimmed: exp.Expression = exp.Anonymous(this="TRIM", expressions=[col_expr])
    if is_business_key:
        return exp.Upper(this=trimmed)
    return trimmed


def build_length_prefixed_ast(
    col_name: str,
    is_business_key: bool = True,
) -> exp.Expression:
    """Build:
    `CASE WHEN col IS NULL OR TRIM(CAST(col AS VARCHAR)) = ''
          THEN '-1:^^'
          ELSE CAST(OCTET_LENGTH(std_col) AS VARCHAR) || ':' || std_col
     END`
    """
    col_ident = exp.column(col_name)
    std_expr = build_canonical_column_standardization_ast(col_name, is_business_key)
    trimmed_raw = exp.Anonymous(this="TRIM", expressions=[exp.cast(col_ident, to="VARCHAR")])

    is_null_or_empty = exp.Or(
        this=exp.Is(this=col_ident, expression=exp.Null()),
        expression=exp.EQ(this=trimmed_raw, expression=exp.Literal.string("")),
    )

    octet_len = exp.cast(
        exp.Anonymous(this="OCTET_LENGTH", expressions=[std_expr.copy()]),
        to="VARCHAR",
    )
    len_prefixed = exp.DPipe(
        this=exp.DPipe(this=octet_len, expression=exp.Literal.string(":")),
        expression=std_expr,
    )

    return exp.Case(
        ifs=[exp.If(this=is_null_or_empty, true=exp.Literal.string(NULL_TOKEN))],
        default=len_prefixed,
    )


def build_canonical_sha256_hash_ast(
    columns: list[str],
    *,
    is_business_key: bool = True,
    bkcc_prefix: str | None = None,
    sort_alphabetically: bool = False,
) -> exp.Expression:
    """Construct the canonical length-prefixed SHA-256 SQLGlot AST expression."""
    if not columns:
        raise ValueError("columns must be a non-empty list of column names")

    ordered_cols = sorted(columns) if sort_alphabetically else list(columns)
    parts: list[exp.Expression] = []

    if bkcc_prefix:
        prefix_str = f"{len(bkcc_prefix)}:{bkcc_prefix.upper()}"
        parts.append(exp.Literal.string(prefix_str))

    for col in ordered_cols:
        parts.append(build_length_prefixed_ast(col, is_business_key=is_business_key))

    interleaved: list[exp.Expression] = []
    for idx, part in enumerate(parts):
        if idx > 0:
            interleaved.append(exp.Literal.string(DELIMITER))
        interleaved.append(part)

    concat_expr = reduce(lambda a, b: exp.DPipe(this=a, expression=b), interleaved)
    sha256_upper = exp.Upper(
        this=exp.Anonymous(
            this="SHA2",
            expressions=[concat_expr, exp.Literal.number(256)],
        )
    )

    if is_business_key:
        all_null_checks: list[exp.Expression] = [
            exp.Is(this=exp.column(c), expression=exp.Null()) for c in ordered_cols
        ]
        all_null_pred = reduce(lambda a, b: exp.And(this=a, expression=b), all_null_checks)
        return exp.Case(
            ifs=[exp.If(this=all_null_pred, true=exp.Literal.string(ZERO_KEY))],
            default=sha256_upper,
        )

    return sha256_upper


def codemod_enforce_staging_hashes_sql(
    sql_query: str,
    hash_specs: dict[str, dict[str, Any]],
    dialect: str = "duckdb",
) -> str:
    """Parse a staging `SELECT` statement with SQLGlot and replace or append
    every `hk_*`, `hk_lnk_*`, and `hash_diff` projection with the canonical
    Algorithm A.5 length-prefixed SHA-256 AST.
    """
    parsed = sqlglot.parse_one(sql_query, read=dialect)
    select_node = parsed.find(exp.Select)
    if select_node is None:
        raise ValueError("No SELECT projection found in staging SQL")

    existing_aliases: dict[str, int] = {}
    for idx, proj in enumerate(select_node.expressions):
        alias_name = proj.alias_or_name.lower()
        existing_aliases[alias_name] = idx

    for target_alias, spec in hash_specs.items():
        canonical_expr = build_canonical_sha256_hash_ast(
            columns=list(spec["columns"]),
            is_business_key=bool(spec.get("is_business_key", True)),
            bkcc_prefix=spec.get("bkcc_prefix"),
            sort_alphabetically=bool(spec.get("sort_alphabetically", False)),
        )
        aliased_node = exp.alias_(canonical_expr, target_alias)
        key_lower = target_alias.lower()
        if key_lower in existing_aliases:
            select_node.expressions[existing_aliases[key_lower]] = aliased_node
        else:
            select_node.expressions.append(aliased_node)

    return parsed.sql(dialect=dialect, pretty=True)


codemod_5_rewrite_canonical_sha256_hash_ast = codemod_enforce_staging_hashes_sql
