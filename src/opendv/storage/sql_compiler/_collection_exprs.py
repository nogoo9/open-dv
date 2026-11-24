"""Algorithm A.5 collection (ArrayType / MapType) SQL normalization helpers.

Strictly Layer 2a: imports only opendv.models and stdlib.
"""

from __future__ import annotations

from opendv.models.project import DbtSqlDialect
from opendv.models.vault_ir import ArrayType, DecimalType, MapType, PortableLogicalType

TSQL_COLLATION = "Latin1_General_100_BIN2_UTF8"


def duckdb_decimal_norm_sql(col_sql: str) -> str:
    """Normalize DuckDB DECIMAL into canonical string matching ir_kernel._normalize_decimal."""
    c = f"CAST({col_sql} AS VARCHAR)"
    return (
        f"CASE WHEN {col_sql} IS NULL THEN NULL WHEN {col_sql} = 0 THEN '0' "
        f"WHEN INSTR({c}, '.') > 0 THEN RTRIM(RTRIM({c}, '0'), '.') ELSE {c} END"
    )


def _needs_duckdb_collection_transform(ltype: PortableLogicalType) -> bool:
    if ltype == "TIMESTAMP_UTC_US" or isinstance(ltype, (DecimalType, MapType)):
        return True
    if isinstance(ltype, ArrayType):
        return _needs_duckdb_collection_transform(ltype.element_type)
    return False


def _duckdb_map_key_expr(key_sql: str, key_type: PortableLogicalType) -> str:
    if key_type == "UTF8_STRING":
        return key_sql
    if key_type == "BOOLEAN":
        return f"CASE WHEN {key_sql} THEN 'true' ELSE 'false' END"
    if key_type == "TIMESTAMP_UTC_US":
        return (
            f"STRFTIME(CAST({key_sql} AS TIMESTAMPTZ) AT TIME ZONE 'UTC', "
            f"'%Y-%m-%dT%H:%M:%S.%fZ')"
        )
    if key_type == "DATE32":
        return f"STRFTIME(CAST({key_sql} AS DATE), '%Y-%m-%d')"
    if isinstance(key_type, DecimalType):
        return duckdb_decimal_norm_sql(key_sql)
    return f"CAST({key_sql} AS VARCHAR)"


def _duckdb_collection_expr(
    col_sql: str,
    ltype: PortableLogicalType,
    depth: int = 0,
) -> str:
    if ltype == "TIMESTAMP_UTC_US":
        return (
            f"CASE WHEN {col_sql} IS NULL THEN NULL "
            f"ELSE STRFTIME(CAST({col_sql} AS TIMESTAMPTZ) AT TIME ZONE 'UTC', "
            f"'%Y-%m-%dT%H:%M:%S.%fZ') END"
        )
    if isinstance(ltype, DecimalType):
        return duckdb_decimal_norm_sql(col_sql)
    if isinstance(ltype, ArrayType):
        if _needs_duckdb_collection_transform(ltype.element_type):
            var = f"_el{depth}"
            inner = _duckdb_collection_expr(var, ltype.element_type, depth + 1)
            return f"LIST_TRANSFORM({col_sql}, lambda {var}: {inner})"
        return col_sql
    if isinstance(ltype, MapType):
        var = f"_kv{depth}"
        if ltype.key_type != "UTF8_STRING" or _needs_duckdb_collection_transform(ltype.value_type):
            k_expr = _duckdb_map_key_expr(f"{var}.key", ltype.key_type)
            v_expr = _duckdb_collection_expr(f"{var}.value", ltype.value_type, depth + 1)
            return (
                f"MAP_FROM_ENTRIES(LIST_SORT(LIST_TRANSFORM(MAP_ENTRIES({col_sql}), "
                f"lambda {var}: STRUCT_PACK(key := {k_expr}, value := {v_expr}))))"
            )
        return f"MAP_FROM_ENTRIES(LIST_SORT(MAP_ENTRIES({col_sql})))"
    return col_sql


def collection_norm_sql(
    col_sql: str,
    logical_type: ArrayType | MapType,
    dialect: DbtSqlDialect,
) -> str:
    """Render canonical JSON serialization SQL for ArrayType/MapType across 8 SQL dialects."""
    if dialect == "duckdb":
        expr = _duckdb_collection_expr(col_sql, logical_type)
        return f"CAST(TO_JSON({expr}) AS VARCHAR)"
    if dialect == "databricks":
        return f"TO_JSON({col_sql})"
    if dialect == "snowflake":
        return f"CAST(TO_JSON({col_sql}) AS VARCHAR)"
    if dialect == "postgres":
        return f"CAST(TO_JSON({col_sql}) AS TEXT)"
    if dialect == "bigquery":
        return f"TO_JSON_STRING({col_sql})"
    if dialect == "tsql":
        return f"CAST({col_sql} AS VARCHAR(MAX)) COLLATE {TSQL_COLLATION}"
    if dialect == "redshift":
        return f"CAST(JSON_SERIALIZE({col_sql}) AS VARCHAR(65535))"
    return f"toJSONString({col_sql})"


__all__ = ["TSQL_COLLATION", "collection_norm_sql", "duckdb_decimal_norm_sql"]
