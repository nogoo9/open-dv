"""PortableLogicalType lowering matrix across all 11 Open-DV V2 targets.

Strictly Layer 2a: imports only opendv.models and Python stdlib.
"""

from __future__ import annotations

from typing import Literal

from opendv.models.project import DbtSqlDialect
from opendv.models.vault_ir import ArrayType, DecimalType, MapType, PortableLogicalType

TargetId = Literal[
    "duckdb_local",
    "databricks_uc",
    "snowflake",
    "postgres",
    "bigquery",
    "sqlserver_fabric",
    "redshift",
    "clickhouse",
    "iceberg",
    "parquet",
    "dbt",
]

SqlTargetId = Literal[
    "duckdb_local",
    "databricks_uc",
    "snowflake",
    "postgres",
    "bigquery",
    "sqlserver_fabric",
    "redshift",
    "clickhouse",
]

SQL_TARGET_IDS: tuple[SqlTargetId, ...] = (
    "duckdb_local",
    "databricks_uc",
    "snowflake",
    "postgres",
    "bigquery",
    "sqlserver_fabric",
    "redshift",
    "clickhouse",
)

ALL_TARGET_IDS: tuple[TargetId, ...] = (*SQL_TARGET_IDS, "iceberg", "parquet", "dbt")

_DIALECT_TO_TARGET: dict[str, TargetId] = {
    "duckdb": "duckdb_local",
    "databricks": "databricks_uc",
    "snowflake": "snowflake",
    "postgres": "postgres",
    "bigquery": "bigquery",
    "tsql": "sqlserver_fabric",
    "redshift": "redshift",
    "clickhouse": "clickhouse",
    **{tid: tid for tid in ALL_TARGET_IDS},
}


def resolve_target_id(target_or_dialect: str) -> TargetId:
    """Resolve either a TargetId or a DbtSqlDialect string into canonical TargetId."""
    key = target_or_dialect.strip().lower()
    if key not in _DIALECT_TO_TARGET:
        raise ValueError(f"Unsupported storage target or dialect: {target_or_dialect!r}")
    return _DIALECT_TO_TARGET[key]


_SCALAR_TYPE_MATRIX: dict[TargetId, dict[str, str]] = {
    "duckdb_local": {
        "FIXED_BINARY_32": "BLOB",
        "UTF8_STRING": "VARCHAR",
        "INT32": "INTEGER",
        "INT64": "BIGINT",
        "DATE32": "DATE",
        "TIMESTAMP_UTC_US": "TIMESTAMPTZ",
        "BOOLEAN": "BOOLEAN",
        "JSON_DOCUMENT": "JSON",
    },
    "databricks_uc": {
        "FIXED_BINARY_32": "BINARY",
        "UTF8_STRING": "STRING",
        "INT32": "INT",
        "INT64": "BIGINT",
        "DATE32": "DATE",
        "TIMESTAMP_UTC_US": "TIMESTAMP_NTZ",
        "BOOLEAN": "BOOLEAN",
        "JSON_DOCUMENT": "STRING",
    },
    "snowflake": {
        "FIXED_BINARY_32": "BINARY(32)",
        "UTF8_STRING": "VARCHAR",
        "INT32": "NUMBER(10,0)",
        "INT64": "NUMBER(19,0)",
        "DATE32": "DATE",
        "TIMESTAMP_UTC_US": "TIMESTAMP_TZ(6)",
        "BOOLEAN": "BOOLEAN",
        "JSON_DOCUMENT": "VARIANT",
    },
    "postgres": {
        "FIXED_BINARY_32": "BYTEA",
        "UTF8_STRING": "TEXT",
        "INT32": "INTEGER",
        "INT64": "BIGINT",
        "DATE32": "DATE",
        "TIMESTAMP_UTC_US": "TIMESTAMPTZ(6)",
        "BOOLEAN": "BOOLEAN",
        "JSON_DOCUMENT": "JSONB",
    },
    "bigquery": {
        "FIXED_BINARY_32": "BYTES",
        "UTF8_STRING": "STRING",
        "INT32": "INT64",
        "INT64": "INT64",
        "DATE32": "DATE",
        "TIMESTAMP_UTC_US": "TIMESTAMP",
        "BOOLEAN": "BOOL",
        "JSON_DOCUMENT": "JSON",
    },
    "sqlserver_fabric": {
        "FIXED_BINARY_32": "BINARY(32)",
        "UTF8_STRING": "VARCHAR(MAX)",
        "INT32": "INT",
        "INT64": "BIGINT",
        "DATE32": "DATE",
        "TIMESTAMP_UTC_US": "DATETIMEOFFSET(6)",
        "BOOLEAN": "BIT",
        "JSON_DOCUMENT": "NVARCHAR(MAX)",
    },
    "redshift": {
        "FIXED_BINARY_32": "VARBYTE(32)",
        "UTF8_STRING": "VARCHAR(65535)",
        "INT32": "INTEGER",
        "INT64": "BIGINT",
        "DATE32": "DATE",
        "TIMESTAMP_UTC_US": "TIMESTAMPTZ",
        "BOOLEAN": "BOOLEAN",
        "JSON_DOCUMENT": "SUPER",
    },
    "clickhouse": {
        "FIXED_BINARY_32": "FixedString(32)",
        "UTF8_STRING": "String",
        "INT32": "Int32",
        "INT64": "Int64",
        "DATE32": "Date32",
        "TIMESTAMP_UTC_US": "DateTime64(6, 'UTC')",
        "BOOLEAN": "Bool",
        "JSON_DOCUMENT": "JSON",
    },
    "iceberg": {
        "FIXED_BINARY_32": "fixed[32]",
        "UTF8_STRING": "string",
        "INT32": "int",
        "INT64": "long",
        "DATE32": "date",
        "TIMESTAMP_UTC_US": "timestamptz",
        "BOOLEAN": "boolean",
        "JSON_DOCUMENT": "string",
    },
    "parquet": {
        "FIXED_BINARY_32": "fixed_size_binary[32]",
        "UTF8_STRING": "string",
        "INT32": "int32",
        "INT64": "int64",
        "DATE32": "date32",
        "TIMESTAMP_UTC_US": "timestamp[us, tz=UTC]",
        "BOOLEAN": "bool",
        "JSON_DOCUMENT": "string",
    },
}


def _has_nested_collection(ltype: PortableLogicalType) -> bool:
    if isinstance(ltype, ArrayType):
        return isinstance(ltype.element_type, (ArrayType, MapType)) or _has_nested_collection(
            ltype.element_type
        )
    if isinstance(ltype, MapType):
        return (
            isinstance(ltype.key_type, (ArrayType, MapType))
            or isinstance(ltype.value_type, (ArrayType, MapType))
            or _has_nested_collection(ltype.key_type)
            or _has_nested_collection(ltype.value_type)
        )
    return False


def lower_logical_type(
    ltype: PortableLogicalType,
    target_or_dialect: str,
    *,
    dbt_sql_dialect: DbtSqlDialect = "duckdb",
    include_tsql_collation: bool = False,
) -> str:
    """Lower a PortableLogicalType into the exact target SQL/format type string."""
    tid = resolve_target_id(target_or_dialect)
    if tid == "dbt":
        tid = resolve_target_id(dbt_sql_dialect)

    if isinstance(ltype, DecimalType):
        p, s = ltype.precision, ltype.scale
        prefixes = {
            "snowflake": "NUMBER",
            "postgres": "NUMERIC",
            "bigquery": "BIGNUMERIC",
            "clickhouse": "Decimal",
            "iceberg": "decimal",
            "parquet": "decimal128",
        }
        return f"{prefixes.get(tid, 'DECIMAL')}({p},{s})"

    if isinstance(ltype, ArrayType):
        if tid == "bigquery":
            if isinstance(ltype.element_type, (ArrayType, MapType)) or _has_nested_collection(
                ltype.element_type
            ):
                return "JSON"
            return f"ARRAY<{lower_logical_type(ltype.element_type, tid)}>"
        if tid == "duckdb_local":
            return f"{lower_logical_type(ltype.element_type, tid)}[]"
        if tid == "databricks_uc":
            return f"ARRAY<{lower_logical_type(ltype.element_type, tid)}>"
        if tid == "snowflake":
            return "ARRAY"
        if tid == "postgres":
            if isinstance(ltype.element_type, (ArrayType, MapType)):
                return "JSONB"
            return f"{lower_logical_type(ltype.element_type, tid)}[]"
        if tid == "sqlserver_fabric":
            return "NVARCHAR(MAX)"
        if tid == "redshift":
            return "SUPER"
        if tid == "clickhouse":
            return f"Array({lower_logical_type(ltype.element_type, tid)})"
        return f"list<{lower_logical_type(ltype.element_type, tid)}>"

    if isinstance(ltype, MapType):
        if tid == "bigquery" and _has_nested_collection(ltype):
            return "JSON"
        kt = lower_logical_type(ltype.key_type, tid)
        vt = lower_logical_type(ltype.value_type, tid)
        if tid == "bigquery":
            return f"ARRAY<STRUCT<key {kt}, value {vt}>>"
        if tid == "duckdb_local":
            return f"MAP({kt},{vt})"
        if tid == "databricks_uc":
            return f"MAP<{kt},{vt}>"
        if tid == "snowflake":
            return "OBJECT"
        if tid == "postgres":
            return "JSONB"
        if tid == "sqlserver_fabric":
            return "NVARCHAR(MAX)"
        if tid == "redshift":
            return "SUPER"
        if tid == "clickhouse":
            return f"Map({kt},{vt})"
        return f"map<{kt},{vt}>"

    base = _SCALAR_TYPE_MATRIX[tid][str(ltype)]
    if include_tsql_collation and tid == "sqlserver_fabric" and ltype == "UTF8_STRING":
        return f"{base} COLLATE Latin1_General_100_BIN2_UTF8"
    return base


__all__ = [
    "ALL_TARGET_IDS",
    "SQL_TARGET_IDS",
    "SqlTargetId",
    "TargetId",
    "lower_logical_type",
    "resolve_target_id",
]
