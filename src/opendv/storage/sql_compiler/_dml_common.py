"""Shared DML helpers and Satellite Ghost record builders.

Strictly Layer 2a: imports only opendv.models, dialects, sqlglot, _ddl_common, dialect_hashing.
"""

from __future__ import annotations

from collections.abc import Mapping

import sqlglot
from sqlglot import exp

from opendv.models.project import DbtSqlDialect
from opendv.models.vault_ir import ArrayType, DecimalType, MapType, PortableLogicalType
from opendv.models.vault_ir_certified import CertifiedSatelliteSpec, SeedRecordsSpec
from opendv.storage.dialects import SqlDialectProfile, lower_logical_type
from opendv.storage.sql_compiler._ddl_common import qualify_name
from opendv.storage.sql_compiler.dialect_hashing import zero_key_hex_sql


def assert_clean_dml(sql: str, dialect: DbtSqlDialect) -> str:
    """Verify DML parses via sqlglot with zero exp.Command nodes."""
    nodes = sqlglot.parse(sql, read=dialect)
    for node in nodes:
        if node is not None and any(isinstance(sub, exp.Command) for sub in node.walk()):
            raise ValueError(f"exp.Command emitted in {dialect} DML: {sql}")
    return sql


def ts_literal_sql(iso_ts: str, target_id: str, dialect: DbtSqlDialect) -> str:
    """Render deterministic UTC timestamp literal for target dialect."""
    ts_type = lower_logical_type("TIMESTAMP_UTC_US", target_id)
    if dialect == "clickhouse":
        clean = iso_ts.replace("T", " ").rstrip("Z")
        return f"parseDateTime64BestEffort('{clean}', 6, 'UTC')"
    if dialect == "tsql":
        return f"CAST('{iso_ts}' AS DATETIMEOFFSET(6))"
    return f"CAST('{iso_ts}' AS {ts_type})"


def ghost_col_default_sql(
    col_name: str,
    ltype: PortableLogicalType,
    nullable: bool,
    seed: SeedRecordsSpec,
    target_id: str,
    dialect: DbtSqlDialect,
) -> str:
    """Return canonical Ghost/Zero-Key default literal for a column."""
    if nullable and col_name not in ("load_dts", "rec_src", "hash_diff"):
        return "NULL"
    if ltype == "FIXED_BINARY_32":
        return zero_key_hex_sql(dialect, is_error=False)
    if ltype == "TIMESTAMP_UTC_US":
        return ts_literal_sql(seed.ghost_load_dts, target_id, dialect)
    if ltype == "DATE32":
        return "CAST('1900-01-01' AS DATE)"
    if ltype == "BOOLEAN":
        return "0" if dialect == "tsql" else "FALSE"
    if ltype in ("INT32", "INT64") or isinstance(ltype, DecimalType):
        return f"CAST({seed.default_num} AS {lower_logical_type(ltype, target_id)})"
    if ltype == "JSON_DOCUMENT":
        return f"CAST('{{}}' AS {lower_logical_type(ltype, target_id)})"
    if isinstance(ltype, ArrayType):
        dt = lower_logical_type(ltype, target_id)
        if dialect == "duckdb":
            return f"CAST([] AS {dt})"
        if dialect == "databricks":
            return f"CAST(ARRAY() AS {dt})"
        if dialect == "snowflake":
            return "ARRAY_CONSTRUCT()"
        if dialect == "postgres":
            return "CAST('[]' AS JSONB)" if dt == "JSONB" else f"CAST(ARRAY[] AS {dt})"
        if dialect == "bigquery":
            return "PARSE_JSON('[]')" if dt == "JSON" else f"CAST([] AS {dt})"
        if dialect == "tsql":
            return "CAST('[]' AS NVARCHAR(MAX))"
        if dialect == "redshift":
            return "JSON_PARSE('[]')"
        return f"CAST([] AS {dt})"
    if isinstance(ltype, MapType):
        dt = lower_logical_type(ltype, target_id)
        if dialect == "duckdb":
            return f"CAST(MAP([], []) AS {dt})"
        if dialect == "databricks":
            return f"CAST(MAP() AS {dt})"
        if dialect == "snowflake":
            return "OBJECT_CONSTRUCT()"
        if dialect == "postgres":
            return "CAST('{}' AS JSONB)"
        if dialect == "bigquery":
            return "PARSE_JSON('{}')" if dt == "JSON" else f"CAST([] AS {dt})"
        if dialect == "tsql":
            return "CAST('{}' AS NVARCHAR(MAX))"
        if dialect == "redshift":
            return "JSON_PARSE('{}')"
        return f"CAST(map() AS {dt})"
    return f"'{seed.default_string}'"


def idempotent_insert_from_select(
    target_full: str,
    cols: list[str],
    pk_cols: list[str],
    select_sql: str,
    profile: SqlDialectProfile,
    *,
    extra_not_exists: str = "",
) -> str:
    """Wrap a SELECT query in dialect-specific idempotent INSERT/MERGE/ON CONFLICT."""
    dialect = profile.sqlglot_dialect or "duckdb"
    cols_csv = ", ".join(cols)

    if dialect == "postgres":
        if extra_not_exists:
            sel_q = ", ".join(f"src_q.{c}" for c in cols)
            body = (
                f"SELECT {sel_q} FROM (\n{select_sql}\n) AS src_q\n"
                f"WHERE NOT EXISTS (SELECT 1 FROM {target_full} AS ex WHERE {extra_not_exists})"
            )
        else:
            body = select_sql
        sql = (
            f"INSERT INTO {target_full} ({cols_csv})\n{body}\n"
            f"ON CONFLICT ({', '.join(pk_cols)}) DO NOTHING"
        )
        return assert_clean_dml(sql, dialect)

    if dialect in ("databricks", "snowflake", "bigquery", "tsql"):
        hold = " WITH (HOLDLOCK)" if dialect == "tsql" else ""
        on_cond = " AND ".join(f"tgt.{k} = s.{k}" for k in pk_cols)
        src_vals = ", ".join(f"s.{c}" for c in cols)
        sql = (
            f"MERGE INTO {target_full}{hold} AS tgt USING (\n{select_sql}\n) AS s "
            f"ON {on_cond} WHEN NOT MATCHED THEN INSERT ({cols_csv}) VALUES ({src_vals})"
        )
        if dialect == "tsql":
            sql += ";"
        return assert_clean_dml(sql, dialect)

    if dialect == "clickhouse":
        on_cond = " AND ".join(f"src_q.{k} = ex.{k}" for k in pk_cols)
        inv_join = (
            f"\nLEFT ANTI JOIN {target_full} AS ex_inv "
            f"ON {extra_not_exists.replace('ex.', 'ex_inv.')}"
            if extra_not_exists
            else ""
        )
        sel_q = ", ".join(f"src_q.{c}" for c in cols)
        sql = (
            f"INSERT INTO {target_full} ({cols_csv})\n"
            f"SELECT {sel_q} FROM (\n{select_sql}\n) AS src_q\n"
            f"LEFT ANTI JOIN {target_full} AS ex ON {on_cond}{inv_join}\n"
            f"SETTINGS join_use_nulls = 1"
        )
        return assert_clean_dml(sql, dialect)

    ex_cond = " AND ".join(f"ex.{k} = src_q.{k}" for k in pk_cols)
    if extra_not_exists:
        ex_cond = f"({ex_cond}) OR ({extra_not_exists})"
    sel_q = ", ".join(f"src_q.{c}" for c in cols)
    sql = (
        f"INSERT INTO {target_full} ({cols_csv})\n"
        f"SELECT {sel_q} FROM (\n{select_sql}\n) AS src_q\n"
        f"WHERE NOT EXISTS (SELECT 1 FROM {target_full} AS ex WHERE {ex_cond})"
    )
    return assert_clean_dml(sql, dialect)


def build_sat_ghost_dml(
    sat: CertifiedSatelliteSpec,
    seed: SeedRecordsSpec,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> str:
    """Emit idempotent Ghost Record (0x00..00, 1900-01-01T00:00:00Z) insert for a Satellite."""
    dialect = profile.sqlglot_dialect or "duckdb"
    sat_full = qualify_name(sat.zone_id, sat.sat_name, aliases)
    parent_hk = sat.columns[0].name if sat.columns else "parent_hk"
    zero_00 = zero_key_hex_sql(dialect, is_error=False)
    ghost_ts = ts_literal_sql(seed.ghost_load_dts, profile.target_id, dialect)

    app_col = sat.applied_dts_col if (sat.is_bitemporal and sat.applied_dts_col) else None
    app_ghost_val = ghost_ts
    if app_col is not None:
        app_f = next((c for c in sat.columns if c.name == app_col), None)
        if app_f is not None:
            if app_f.logical_type == "TIMESTAMP_UTC_US":
                app_ghost_val = ghost_ts
            elif app_f.logical_type == "UTF8_STRING":
                app_ghost_val = f"'{seed.ghost_load_dts}'"
            else:
                app_ghost_val = ghost_col_default_sql(
                    app_f.name, app_f.logical_type, False, seed, profile.target_id, dialect
                )

    cols = [c.name for c in sat.columns]
    vals: list[str] = []
    for c in sat.columns:
        if c.name == parent_hk:
            vals.append(zero_00)
        elif c.name == "load_dts":
            vals.append(ghost_ts)
        elif c.name == "rec_src":
            vals.append(f"'{seed.ghost_rec_src}'")
        elif c.name == "hash_diff":
            vals.append(zero_00)
        elif app_col is not None and c.name == app_col:
            vals.append(app_ghost_val)
        else:
            vals.append(
                ghost_col_default_sql(
                    c.name, c.logical_type, c.nullable, seed, profile.target_id, dialect
                )
            )

    col_list = ", ".join(cols)
    val_list = ", ".join(vals)
    is_mas = sat.sat_kind == "MAS" or bool(sat.child_dependent_keys)
    cond_parts = [f"tgt.{parent_hk} = {zero_00}", f"tgt.load_dts = {ghost_ts}"]
    if app_col is not None:
        cond_parts.append(f"tgt.{app_col} = {app_ghost_val}")
    if is_mas and sat.child_dependent_keys:
        for cdk in sat.child_dependent_keys:
            cdk_f = next((c for c in sat.columns if c.name == cdk), None)
            if cdk_f is not None:
                cdk_def = ghost_col_default_sql(
                    cdk_f.name, cdk_f.logical_type, cdk_f.nullable, seed, profile.target_id, dialect
                )
                cond_parts.append(
                    f"tgt.{cdk} IS NULL" if cdk_def == "NULL" else f"tgt.{cdk} = {cdk_def}"
                )
    cond = " AND ".join(cond_parts)

    if dialect == "postgres":
        pk_cols = [parent_hk, "load_dts"]
        if sat.is_bitemporal and sat.applied_dts_col:
            pk_cols.append(sat.applied_dts_col)
        if is_mas and sat.child_dependent_keys:
            pk_cols.extend(sat.child_dependent_keys)
        sql = (
            f"INSERT INTO {sat_full} ({col_list}) VALUES ({val_list}) "
            f"ON CONFLICT ({', '.join(pk_cols)}) DO NOTHING"
        )
        return assert_clean_dml(sql, dialect)

    if dialect in ("databricks", "snowflake", "bigquery", "tsql"):
        hold = " WITH (HOLDLOCK)" if dialect == "tsql" else ""
        src_aliases = ", ".join(f"{v} AS {c}" for c, v in zip(cols, vals, strict=True))
        src_vals = ", ".join(f"src.{c}" for c in cols)
        on_parts = [f"tgt.{parent_hk} = src.{parent_hk}", "tgt.load_dts = src.load_dts"]
        if sat.is_bitemporal and sat.applied_dts_col:
            on_parts.append(f"tgt.{sat.applied_dts_col} = src.{sat.applied_dts_col}")
        if is_mas and sat.child_dependent_keys:
            on_parts.extend(f"tgt.{cdk} = src.{cdk}" for cdk in sat.child_dependent_keys)
        sql = (
            f"MERGE INTO {sat_full}{hold} AS tgt USING (SELECT {src_aliases}) AS src "
            f"ON {' AND '.join(on_parts)} "
            f"WHEN NOT MATCHED THEN INSERT ({col_list}) VALUES ({src_vals})"
        )
        if dialect == "tsql":
            sql += ";"
        return assert_clean_dml(sql, dialect)

    sql = (
        f"INSERT INTO {sat_full} ({col_list})\n"
        f"SELECT {val_list}\n"
        f"WHERE NOT EXISTS (SELECT 1 FROM {sat_full} AS tgt WHERE {cond})"
    )
    return assert_clean_dml(sql, dialect)


__all__ = [
    "assert_clean_dml",
    "build_sat_ghost_dml",
    "ghost_col_default_sql",
    "idempotent_insert_from_select",
    "ts_literal_sql",
]
