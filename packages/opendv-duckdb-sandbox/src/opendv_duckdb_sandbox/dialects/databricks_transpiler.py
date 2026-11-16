"""Databricks Delta Lake SQL Transpiler and Compiler.

Compiles and transpiles verified CanonicalIR and 4-zone DuckDB SQL bundles
into Databricks Delta SQL:
  - Delta Lake DDL (`CREATE TABLE IF NOT EXISTS ... USING DELTA`)
  - Canonical `SHA2(CONCAT_WS('||', ...), 256)` hash keys and `hash_diff`
  - Idempotent Delta `MERGE INTO` loading patterns for Hubs, Links, and Satellites
  - `sqlglot` (`read="duckdb", write="databricks"`) query transpilation
"""

from __future__ import annotations

import re
from typing import Literal

import sqlglot
from pydantic import BaseModel, ConfigDict, Field

from opendv_core.constants import (
    DEFAULT_DELIMITER,
    GHOST_DTS,
    NULL_TOKEN,
    ZERO_KEY_ERROR,
    ZERO_KEY_ERROR_TOKEN,
    ZERO_KEY_REC_SRC,
    ZERO_KEY_UNKNOWN,
    ZERO_KEY_UNKNOWN_TOKEN,
)
from opendv_core.models.canonical_ir import (
    CanonicalIR,
    IRHub,
    IRLink,
    IRSatellite,
)
from opendv_duckdb_sandbox.compiler_4zone.bundle import Compiled4ZoneBundle

_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CREATE_TABLE_RE = re.compile(
    r"(CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([^\s(]+)\s*\([^;]+\))(?!\s*USING\s+DELTA)",
    re.IGNORECASE,
)


def _validate_ident(name: str) -> str:
    clean = name.strip('`" ')
    if not _SAFE_IDENT_RE.match(clean):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return clean


def normalize_databricks_type(data_type: str) -> str:
    upper = data_type.strip().upper()
    if upper.startswith(("CHAR", "VARCHAR", "TEXT", "STRING", "BINARY", "VARBINARY")):
        return "STRING"
    if upper.startswith("TIMESTAMP"):
        return "TIMESTAMP"
    if upper == "DATE":
        return "DATE"
    if upper.startswith(("DECIMAL", "NUMERIC")):
        return upper
    if upper.startswith(("BIGINT", "INT8")):
        return "BIGINT"
    if upper.startswith(("SMALLINT", "TINYINT", "INT", "INTEGER", "INT4", "INT2")):
        return "INT"
    if upper.startswith(("BOOL", "BOOLEAN")):
        return "BOOLEAN"
    if upper.startswith(("DOUBLE", "FLOAT", "REAL")):
        return "DOUBLE"
    return "STRING"


def build_databricks_length_prefixed_token_sql(
    col_expr: str,
    *,
    is_business_key: bool = False,
    null_token: str = NULL_TOKEN,
) -> str:
    base_cast = f"TRIM(CAST({col_expr} AS STRING))"
    norm_expr = f"UPPER({base_cast})" if is_business_key else base_cast
    nullif_expr = f"NULLIF({norm_expr}, '')"
    return (
        f"CASE WHEN {nullif_expr} IS NULL THEN '{null_token}' "
        f"ELSE CONCAT(CAST(OCTET_LENGTH({nullif_expr}) AS STRING), ':', {nullif_expr}) END"
    )


def build_databricks_algorithm_a5_sha256_sql(
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
    ordered = sorted(columns) if sort_alphabetically else list(columns)
    tokens: list[str] = []

    if is_business_key and bkcc_column_expr and bkcc_column_expr.strip():
        clean_bkcc_col = bkcc_column_expr.strip()
        ordered = [c for c in ordered if c.strip() != clean_bkcc_col] or ordered
        tokens.append(
            build_databricks_length_prefixed_token_sql(
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
            build_databricks_length_prefixed_token_sql(
                col,
                is_business_key=is_business_key,
                null_token=null_token,
            )
        )

    concat_sql = f"CONCAT_WS('{delimiter}', {', '.join(tokens)})"
    sha_expr = f"LOWER(SHA2({concat_sql}, 256))"

    if is_business_key and ordered:
        pred_cols = (
            list(key_columns)
            if key_columns is not None
            else [c for c in ordered if c.lower() != "bkcc" and not c.lower().endswith("__bkcc")]
            or ordered
        )

        default_err_pred = " OR ".join(
            f"TRIM(CAST({c} AS STRING)) = '{ZERO_KEY_ERROR_TOKEN}'" for c in pred_cols
        )
        err_branch_pred = (
            f"({error_predicate}) OR ({default_err_pred})" if error_predicate else default_err_pred
        )
        default_unk_pred = " OR ".join(
            f"(NULLIF(TRIM(CAST({c} AS STRING)), '') IS NULL "
            f"OR TRIM(CAST({c} AS STRING)) = '{ZERO_KEY_UNKNOWN_TOKEN}')"
            for c in pred_cols
        )
        unk_branch_pred = (
            f"({unknown_predicate}) OR ({default_unk_pred})"
            if unknown_predicate
            else default_unk_pred
        )
        return (
            f"CASE WHEN {err_branch_pred} THEN REPEAT('f', 64) "
            f"WHEN {unk_branch_pred} THEN REPEAT('0', 64) ELSE {sha_expr} END"
        )

    return sha_expr


def transpile_duckdb_to_databricks_sql(sql: str) -> str:
    if not sql.strip():
        return ""

    statements: list[str] = []
    for raw in [s.strip() for s in sql.split(";") if s.strip()]:
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if all(line.startswith("--") for line in lines):
            statements.append(raw + ";")
            continue

        try:
            transpiled = sqlglot.transpile(raw, read="duckdb", write="databricks", pretty=True)
            for t_stmt in transpiled:
                clean_t = t_stmt.strip()
                if (
                    clean_t.upper().startswith("CREATE TABLE")
                    and "USING DELTA" not in clean_t.upper()
                ):
                    clean_t = f"{clean_t} USING DELTA"
                statements.append(clean_t + ";")
        except (sqlglot.errors.SqlglotError, ValueError, TypeError):
            clean_raw = raw.replace("LOWER(SHA256(", "LOWER(SHA2(")
            if (
                clean_raw.upper().startswith("CREATE TABLE")
                and "USING DELTA" not in clean_raw.upper()
            ):
                clean_raw = f"{clean_raw} USING DELTA"
            statements.append(clean_raw + ";")

    return "\n\n".join(statements)


def compile_databricks_hub_ddl_and_zero_keys(hub: IRHub) -> str:
    tbl = _validate_ident(hub.name)
    hk = _validate_ident(hub.hk_column.name)
    bk_defs = [f"    {_validate_ident(bk.name)} STRING NOT NULL," for bk in hub.business_keys]

    ddl = "\n".join(
        [
            f"CREATE TABLE IF NOT EXISTS raw_vault.{tbl} (",
            f"    {hk} STRING NOT NULL,",
            *bk_defs,
            "    load_dts TIMESTAMP NOT NULL,",
            "    rec_src STRING NOT NULL",
            ") USING DELTA;",
        ]
    )

    bk_cols = [_validate_ident(bk.name) for bk in hub.business_keys]
    bk_csv = ", ".join(bk_cols)
    src_bk_csv = ", ".join(f"source.{c}" for c in bk_cols)
    unknown_bks_csv = ", ".join(
        f"'{'SYSTEM' if bk.is_bkcc else '-1'}' AS {_validate_ident(bk.name)}"
        for bk in hub.business_keys
    )
    error_bks_csv = ", ".join(
        f"'{'SYSTEM' if bk.is_bkcc else '-2'}' AS {_validate_ident(bk.name)}"
        for bk in hub.business_keys
    )

    zero_key_merge = (
        f"MERGE INTO raw_vault.{tbl} AS target\n"
        f"USING (\n"
        f"    SELECT '{ZERO_KEY_UNKNOWN}' AS {hk}, {unknown_bks_csv}, "
        f"TIMESTAMP '{GHOST_DTS}' AS load_dts, '{ZERO_KEY_REC_SRC}' AS rec_src\n"
        f"    UNION ALL\n"
        f"    SELECT '{ZERO_KEY_ERROR}' AS {hk}, {error_bks_csv}, "
        f"TIMESTAMP '{GHOST_DTS}' AS load_dts, '{ZERO_KEY_REC_SRC}' AS rec_src\n"
        f") AS source\n"
        f"ON target.{hk} = source.{hk}\n"
        f"WHEN NOT MATCHED THEN\n"
        f"  INSERT ({hk}, {bk_csv}, load_dts, rec_src)\n"
        f"  VALUES (source.{hk}, {src_bk_csv}, source.load_dts, source.rec_src);"
    )
    return f"{ddl}\n\n{zero_key_merge}"


def compile_databricks_link_ddl(link: IRLink) -> str:
    tbl = _validate_ident(link.name)
    hk = _validate_ident(link.hk_column.name)
    fk_defs = [
        f"    {_validate_ident(ref.fk_column_name)} STRING NOT NULL," for ref in link.hub_references
    ]
    return "\n".join(
        [
            f"CREATE TABLE IF NOT EXISTS raw_vault.{tbl} (",
            f"    {hk} STRING NOT NULL,",
            *fk_defs,
            "    load_dts TIMESTAMP NOT NULL,",
            "    rec_src STRING NOT NULL",
            ") USING DELTA;",
        ]
    )


def compile_databricks_satellite_ddl(sat: IRSatellite) -> str:
    tbl = _validate_ident(sat.name)
    parent_hk = _validate_ident(sat.parent_hk_column.name)
    diff_col = _validate_ident(sat.hash_diff_column.name)

    data_col_defs = []
    for col in sat.payload_columns:
        c_name = _validate_ident(col.name)
        c_type = normalize_databricks_type(col.data_type)
        not_null = "NOT NULL" if not col.nullable else ""
        data_col_defs.append(f"    {c_name} {c_type} {not_null},".rstrip())

    return "\n".join(
        [
            f"CREATE TABLE IF NOT EXISTS raw_vault.{tbl} (",
            f"    {parent_hk} STRING NOT NULL,",
            "    load_dts TIMESTAMP NOT NULL,",
            f"    {diff_col} STRING NOT NULL,",
            *data_col_defs,
            "    rec_src STRING NOT NULL",
            ") USING DELTA;",
        ]
    )


def compile_databricks_zone_2_merge(ir_obj: CanonicalIR) -> str:
    parts: list[str] = [
        "-- =============================================================================",
        "-- ZONE 2: IDEMPOTENT DELTA MERGE INTO RAW VAULT LOADS (DATABRICKS)",
        "-- =============================================================================",
    ]

    for hub in ir_obj.hubs:
        tbl = _validate_ident(hub.name)
        hk = _validate_ident(hub.hk_column.name)
        bk_cols = [_validate_ident(bk.name) for bk in hub.business_keys]
        bk_csv = ", ".join(bk_cols)
        src_bk_csv = ", ".join(f"src.{c}" for c in bk_cols)
        source_bk_csv = ", ".join(f"source.{c}" for c in bk_cols)

        parts.append(
            f"MERGE INTO raw_vault.{tbl} AS target\n"
            f"USING (\n"
            f"    SELECT src.{hk}, {src_bk_csv}, "
            f"MIN(src.load_dts) AS load_dts, ANY_VALUE(src.rec_src) AS rec_src\n"
            f"    FROM staging.stg_{tbl} AS src\n"
            f"    WHERE src.{hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')\n"
            f"    GROUP BY src.{hk}, {src_bk_csv}\n"
            f") AS source\n"
            f"ON target.{hk} = source.{hk}\n"
            f"WHEN NOT MATCHED THEN\n"
            f"  INSERT ({hk}, {bk_csv}, load_dts, rec_src)\n"
            f"  VALUES (source.{hk}, {source_bk_csv}, source.load_dts, source.rec_src);"
        )

    for link in ir_obj.links:
        tbl = _validate_ident(link.name)
        hk = _validate_ident(link.hk_column.name)
        fk_cols = [_validate_ident(ref.fk_column_name) for ref in link.hub_references]
        fk_csv = ", ".join(fk_cols)
        src_fk_csv = ", ".join(f"src.{c}" for c in fk_cols)
        source_fk_csv = ", ".join(f"source.{c}" for c in fk_cols)

        parts.append(
            f"MERGE INTO raw_vault.{tbl} AS target\n"
            f"USING (\n"
            f"    SELECT src.{hk}, {src_fk_csv}, "
            f"MIN(src.load_dts) AS load_dts, ANY_VALUE(src.rec_src) AS rec_src\n"
            f"    FROM staging.stg_{tbl} AS src\n"
            f"    WHERE src.{hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')\n"
            f"    GROUP BY src.{hk}, {src_fk_csv}\n"
            f") AS source\n"
            f"ON target.{hk} = source.{hk}\n"
            f"WHEN NOT MATCHED THEN\n"
            f"  INSERT ({hk}, {fk_csv}, load_dts, rec_src)\n"
            f"  VALUES (source.{hk}, {source_fk_csv}, source.load_dts, source.rec_src);"
        )

    for sat in ir_obj.satellites:
        tbl = _validate_ident(sat.name)
        parent_hk = _validate_ident(sat.parent_hk_column.name)
        diff_col = _validate_ident(sat.hash_diff_column.name)
        data_cols = [_validate_ident(col.name) for col in sat.payload_columns]

        all_cols = [parent_hk, "load_dts", diff_col, *data_cols, "rec_src"]
        cols_csv = ", ".join(all_cols)
        source_cols_csv = ", ".join(f"source.{c}" for c in all_cols)
        src_cols_csv = ", ".join(f"src.{c}" for c in all_cols)

        parts.append(
            f"MERGE INTO raw_vault.{tbl} AS target\n"
            f"USING (\n"
            f"    SELECT {src_cols_csv} FROM staging.stg_{tbl} AS src\n"
            f"    WHERE src.{parent_hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')\n"
            f") AS source\n"
            f"ON target.{parent_hk} = source.{parent_hk} AND target.load_dts = source.load_dts\n"
            f"WHEN NOT MATCHED THEN\n"
            f"  INSERT ({cols_csv})\n"
            f"  VALUES ({source_cols_csv});"
        )

    return "\n\n".join(parts)


class CompiledDatabricksBundle(BaseModel):
    """Structured Databricks Delta Lake 4-Zone SQL output bundle."""

    model_config = ConfigDict(extra="forbid")

    model_name: str
    dialect: Literal["databricks"] = "databricks"
    topological_load_order: list[str] = Field(default_factory=list)
    zone_0_sql: str
    zone_1_sql: str
    zone_2_sql: str
    zone_3_sql: str
    zone_4_sql: str
    canonical_ir: CanonicalIR | None = None

    @property
    def full_sql_bundle(self) -> str:
        sections = [
            self.zone_0_sql,
            self.zone_1_sql,
            self.zone_2_sql,
            self.zone_3_sql,
            self.zone_4_sql,
        ]
        return "\n\n".join(s for s in sections if s.strip())

    @property
    def full_sql(self) -> str:
        return self.full_sql_bundle


def transpile_bundle_to_databricks(bundle: Compiled4ZoneBundle) -> CompiledDatabricksBundle:
    zone_0_db = transpile_duckdb_to_databricks_sql(bundle.zone_0_sql)
    zone_1_db = transpile_duckdb_to_databricks_sql(bundle.zone_1_sql)
    zone_2_db = (
        compile_databricks_zone_2_merge(bundle.canonical_ir)
        if bundle.canonical_ir is not None
        else transpile_duckdb_to_databricks_sql(bundle.zone_2_sql)
    )
    zone_3_db = transpile_duckdb_to_databricks_sql(bundle.zone_3_sql)
    zone_4_db = transpile_duckdb_to_databricks_sql(bundle.zone_4_sql)

    return CompiledDatabricksBundle(
        model_name=bundle.model_name,
        dialect="databricks",
        topological_load_order=bundle.topological_load_order,
        zone_0_sql=zone_0_db,
        zone_1_sql=zone_1_db,
        zone_2_sql=zone_2_db,
        zone_3_sql=zone_3_db,
        zone_4_sql=zone_4_db,
        canonical_ir=bundle.canonical_ir,
    )


def compile_canonical_ir_to_databricks(ir_obj: CanonicalIR) -> CompiledDatabricksBundle:
    ddl_parts: list[str] = [
        "CREATE SCHEMA IF NOT EXISTS staging;",
        "CREATE SCHEMA IF NOT EXISTS raw_vault;",
        "CREATE SCHEMA IF NOT EXISTS business_vault;",
    ]
    for hub in ir_obj.hubs:
        ddl_parts.append(compile_databricks_hub_ddl_and_zero_keys(hub))
    for link in ir_obj.links:
        ddl_parts.append(compile_databricks_link_ddl(link))
    for sat in ir_obj.satellites:
        ddl_parts.append(compile_databricks_satellite_ddl(sat))

    topological_order = (
        [h.name for h in ir_obj.hubs]
        + [lnk.name for lnk in ir_obj.links]
        + [s.name for s in ir_obj.satellites]
    )

    return CompiledDatabricksBundle(
        model_name=ir_obj.model_name,
        dialect="databricks",
        topological_load_order=topological_order,
        zone_0_sql="\n\n".join(ddl_parts),
        zone_1_sql="",
        zone_2_sql=compile_databricks_zone_2_merge(ir_obj),
        zone_3_sql="",
        zone_4_sql="",
        canonical_ir=ir_obj,
    )


__all__ = [
    "CompiledDatabricksBundle",
    "build_databricks_algorithm_a5_sha256_sql",
    "build_databricks_length_prefixed_token_sql",
    "compile_canonical_ir_to_databricks",
    "compile_databricks_hub_ddl_and_zero_keys",
    "compile_databricks_link_ddl",
    "compile_databricks_satellite_ddl",
    "compile_databricks_zone_2_merge",
    "normalize_databricks_type",
    "transpile_bundle_to_databricks",
    "transpile_duckdb_to_databricks_sql",
]
