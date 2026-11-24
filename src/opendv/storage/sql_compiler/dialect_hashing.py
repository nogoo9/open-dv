"""Dialect-specific Algorithm A.5 pre-hash, BKCC, sentinel, and SHA-256 AST compilers.

Strictly Layer 2a: imports only opendv.models, sqlglot, _scalar_exprs, and stdlib.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import sqlglot
from sqlglot import exp

from opendv.models.diagnostics import KeyTierEnum
from opendv.models.ir_kernel import resolve_bkcc
from opendv.models.project import DbtSqlDialect
from opendv.models.vault_ir import PortableLogicalType
from opendv.models.vault_ir_certified import HashRecipeSpec
from opendv.storage.sql_compiler._scalar_exprs import (
    TSQL_COLLATION,
    encode_token_sql,
    format_scalar_sql,
    predicate_to_sqlglot,
    trim_ws_sql,
)


def _col_ref_sql(col_name: str, dialect: DbtSqlDialect, table_alias: str | None = None) -> str:
    return exp.column(col_name, table=table_alias).sql(dialect=dialect)


def zero_key_hex_sql(dialect: DbtSqlDialect, *, is_error: bool = False) -> str:
    """Return dialect-native FIXED_BINARY_32 literal for -1 Unknown (0x00) or -2 Error (0xff)."""
    byte_hex = "ff" if is_error else "00"
    if dialect in ("duckdb", "databricks"):
        return f"UNHEX(REPEAT('{byte_hex}', 32))"
    if dialect == "snowflake":
        return f"TO_BINARY(REPEAT('{byte_hex}', 32), 'HEX')"
    if dialect == "postgres":
        return f"DECODE(REPEAT('{byte_hex}', 32), 'hex')"
    if dialect in ("bigquery", "redshift"):
        return f"FROM_HEX(REPEAT('{byte_hex}', 32))"
    if dialect == "tsql":
        return f"CONVERT(BINARY(32), REPLICATE('{byte_hex}', 32), 2)"
    return f"CAST(unhex(repeat('{byte_hex}', 32)) AS FixedString(32))"


def sha256_binary_sql(prehash_sql: str, dialect: DbtSqlDialect) -> str:
    """Wrap pre-hash UTF-8 string SQL into a dialect-native FIXED_BINARY_32 SHA-256 digest."""
    if dialect == "duckdb":
        return f"UNHEX(SHA256({prehash_sql}))"
    if dialect == "databricks":
        return f"UNHEX(SHA2({prehash_sql}, 256))"
    if dialect == "snowflake":
        return f"SHA2_BINARY({prehash_sql})"
    if dialect == "postgres":
        return f"SHA256(CONVERT_TO({prehash_sql}, 'UTF8'))"
    if dialect == "bigquery":
        return f"SHA256({prehash_sql})"
    if dialect == "tsql":
        return (
            f"CAST(HASHBYTES('SHA2_256', CAST({prehash_sql} AS VARCHAR(MAX)) "
            f"COLLATE {TSQL_COLLATION}) AS BINARY(32))"
        )
    if dialect == "redshift":
        return f"FROM_HEX(SHA2({prehash_sql}, 256))"
    return f"SHA256({prehash_sql})"


def _join_tokens_sql(tokens: Sequence[str], dialect: DbtSqlDialect) -> str:
    if not tokens:
        if dialect == "tsql":
            return f"'' COLLATE {TSQL_COLLATION}"
        return "''"
    if len(tokens) == 1:
        return tokens[0]
    if dialect == "tsql":
        sep = f" + '||' COLLATE {TSQL_COLLATION} + "
        return sep.join(tokens)
    if dialect == "bigquery":
        args = ", '||', ".join(tokens)
        return f"CONCAT({args})"
    if dialect == "clickhouse":
        args = ", '||', ".join(tokens)
        return f"concat({args})"
    return " || '||' || ".join(tokens)


def _bkcc_token_sql(
    recipe: HashRecipeSpec,
    key_tier: KeyTierEnum,
    dialect: DbtSqlDialect,
    table_alias: str | None = None,
) -> str:
    fallback = resolve_bkcc(key_tier, recipe.bkcc_literal)
    lit_tok = f"{len(fallback.encode('utf-8'))}:{fallback}"
    if key_tier == "TIER_1_GLOBAL_STANDARD" or not recipe.bkcc_col:
        if dialect == "tsql":
            return f"'{lit_tok}' COLLATE {TSQL_COLLATION}"
        return f"'{lit_tok}'"

    col_sql = _col_ref_sql(recipe.bkcc_col, dialect, table_alias=table_alias)
    norm_col = format_scalar_sql(col_sql, "UTF8_STRING", dialect, is_business_key=True)
    if dialect == "tsql":
        lit_expr = f"'{fallback}' COLLATE {TSQL_COLLATION}"
        resolved = f"COALESCE({norm_col}, {lit_expr})"
    elif dialect == "clickhouse":
        resolved = f"if(isNull({norm_col}), '{fallback}', assumeNotNull({norm_col}))"
    else:
        resolved = f"COALESCE({norm_col}, '{fallback}')"
    return encode_token_sql(resolved, dialect)


def _sentinel_eq_sql(col_sql: str, sentinel: str, dialect: DbtSqlDialect) -> str:
    if dialect == "clickhouse":
        trimmed = trim_ws_sql(f"toString({col_sql})", dialect)
        return f"(isNotNull({col_sql}) AND {trimmed} = '{sentinel}')"
    if dialect == "tsql":
        cast_c = f"CAST({col_sql} AS VARCHAR(MAX))"
        return f"({col_sql} IS NOT NULL AND {trim_ws_sql(cast_c, dialect)} = '{sentinel}')"
    if dialect in ("databricks", "bigquery"):
        cast_sql = f"CAST({col_sql} AS STRING)"
    elif dialect == "postgres":
        cast_sql = f"CAST({col_sql} AS TEXT)"
    elif dialect == "redshift":
        cast_sql = f"CAST({col_sql} AS VARCHAR(65535))"
    else:
        cast_sql = f"CAST({col_sql} AS VARCHAR)"
    return f"({col_sql} IS NOT NULL AND {trim_ws_sql(cast_sql, dialect)} = '{sentinel}')"


def compile_prehash_sql(
    recipe: HashRecipeSpec,
    dialect: DbtSqlDialect,
    *,
    key_tier: KeyTierEnum = "TIER_2_ENTERPRISE_SOR",
    col_types: Mapping[str, PortableLogicalType] | None = None,
    table_alias: str | None = None,
) -> tuple[str, list[str]]:
    """Build Alg A.5 `||`-delimited pre-hash SQL and normalized scalar column expressions."""
    is_bk = recipe.recipe_kind in ("HUB_HK", "LINK_HK")
    cols = (
        list(recipe.key_columns or recipe.source_columns)
        if is_bk
        else sorted(recipe.source_columns)
    )
    tokens: list[str] = []
    if is_bk:
        tokens.append(_bkcc_token_sql(recipe, key_tier, dialect, table_alias=table_alias))

    norm_exprs: list[str] = []
    for col_name in cols:
        col_sql = _col_ref_sql(col_name, dialect, table_alias=table_alias)
        ltype = col_types.get(col_name) if col_types else None
        norm_sql = format_scalar_sql(col_sql, ltype, dialect, is_business_key=is_bk)
        norm_exprs.append(norm_sql)
        tokens.append(encode_token_sql(norm_sql, dialect))
    return _join_tokens_sql(tokens, dialect), norm_exprs


def compile_hash_recipe_sql(
    recipe: HashRecipeSpec,
    dialect: DbtSqlDialect,
    *,
    key_tier: KeyTierEnum = "TIER_2_ENTERPRISE_SOR",
    col_types: Mapping[str, PortableLogicalType] | None = None,
    table_alias: str | None = None,
) -> str:
    """Compile a HashRecipeSpec into a dialect-native FIXED_BINARY_32 Alg A.5 SQL expression."""
    is_bk = recipe.recipe_kind in ("HUB_HK", "LINK_HK")
    prehash_sql, norm_exprs = compile_prehash_sql(
        recipe,
        dialect,
        key_tier=key_tier,
        col_types=col_types,
        table_alias=table_alias,
    )
    digest_sql = sha256_binary_sql(prehash_sql, dialect)
    if not is_bk:
        return digest_sql

    cols = list(recipe.key_columns or recipe.source_columns)
    err_hex = zero_key_hex_sql(dialect, is_error=True)
    unk_hex = zero_key_hex_sql(dialect, is_error=False)
    whens: list[str] = []

    if recipe.error_predicate is not None:
        pred_sql = predicate_to_sqlglot(recipe.error_predicate, table_alias=table_alias).sql(
            dialect=dialect
        )
        whens.append(f"WHEN {pred_sql} THEN {err_hex}")
    if len(cols) == 1:
        col_sql = _col_ref_sql(cols[0], dialect, table_alias=table_alias)
        whens.append(f"WHEN {_sentinel_eq_sql(col_sql, '-2', dialect)} THEN {err_hex}")

    if recipe.unknown_predicate is not None:
        pred_sql = predicate_to_sqlglot(recipe.unknown_predicate, table_alias=table_alias).sql(
            dialect=dialect
        )
        whens.append(f"WHEN {pred_sql} THEN {unk_hex}")
    if len(cols) == 1:
        col_sql = _col_ref_sql(cols[0], dialect, table_alias=table_alias)
        whens.append(f"WHEN {_sentinel_eq_sql(col_sql, '-1', dialect)} THEN {unk_hex}")

    if not norm_exprs:
        blank_cond = "1 = 1"
    elif dialect == "clickhouse":
        blank_cond = " AND ".join(f"isNull({n})" for n in norm_exprs)
    else:
        blank_cond = " AND ".join(f"({n}) IS NULL" for n in norm_exprs)
    blank_hex = err_hex if recipe.is_optional_inapplicable else unk_hex
    whens.append(f"WHEN {blank_cond} THEN {blank_hex}")

    return f"CASE {' '.join(whens)} ELSE {digest_sql} END"


def compile_hash_recipe_ast(
    recipe: HashRecipeSpec,
    dialect: DbtSqlDialect,
    *,
    key_tier: KeyTierEnum = "TIER_2_ENTERPRISE_SOR",
    col_types: Mapping[str, PortableLogicalType] | None = None,
    table_alias: str | None = None,
) -> exp.Expr:
    """Compile and parse a HashRecipeSpec into a verified sqlglot AST (0 exp.Command)."""
    sql = compile_hash_recipe_sql(
        recipe,
        dialect,
        key_tier=key_tier,
        col_types=col_types,
        table_alias=table_alias,
    )
    node = sqlglot.parse_one(sql, read=dialect)
    if any(isinstance(sub, exp.Command) for sub in node.walk()):
        raise ValueError(f"exp.Command generated for hash recipe {recipe.target_col} in {dialect}")
    return node


__all__ = [
    "compile_hash_recipe_ast",
    "compile_hash_recipe_sql",
    "compile_prehash_sql",
    "sha256_binary_sql",
    "zero_key_hex_sql",
]
