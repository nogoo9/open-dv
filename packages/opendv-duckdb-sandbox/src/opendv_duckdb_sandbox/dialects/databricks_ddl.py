"""Databricks Delta Lake DDL & SQL identifier helpers."""

from __future__ import annotations

import re

from opendv_core.constants import (
    GHOST_DTS,
    ZERO_KEY_ERROR,
    ZERO_KEY_REC_SRC,
    ZERO_KEY_UNKNOWN,
)
from opendv_core.models.canonical_ir import IRHub, IRLink, IRSatellite

_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DEFAULT_UNITY_CATALOG = "opendv_catalog"


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


def compile_databricks_hub_ddl_and_zero_keys(
    hub: IRHub, *, catalog: str = _DEFAULT_UNITY_CATALOG
) -> str:
    cat = _validate_ident(catalog)
    tbl = _validate_ident(hub.name)
    hk = _validate_ident(hub.hk_column.name)
    bk_defs = [f"    {_validate_ident(bk.name)} STRING NOT NULL," for bk in hub.business_keys]

    ddl = "\n".join(
        [
            f"CREATE TABLE IF NOT EXISTS {cat}.raw_vault.{tbl} (",
            f"    {hk} STRING NOT NULL,",
            *bk_defs,
            "    load_dts TIMESTAMP NOT NULL,",
            "    rec_src STRING NOT NULL",
            ") USING DELTA",
            f"CLUSTER BY ({hk}, load_dts)",
            "TBLPROPERTIES ('delta.appendOnly' = 'true');",
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
        f"MERGE INTO {cat}.raw_vault.{tbl} AS target\n"
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


def compile_databricks_link_ddl(link: IRLink, *, catalog: str = _DEFAULT_UNITY_CATALOG) -> str:
    cat = _validate_ident(catalog)
    tbl = _validate_ident(link.name)
    hk = _validate_ident(link.hk_column.name)
    fk_defs = [
        f"    {_validate_ident(ref.fk_column_name)} STRING NOT NULL," for ref in link.hub_references
    ]
    return "\n".join(
        [
            f"CREATE TABLE IF NOT EXISTS {cat}.raw_vault.{tbl} (",
            f"    {hk} STRING NOT NULL,",
            *fk_defs,
            "    load_dts TIMESTAMP NOT NULL,",
            "    rec_src STRING NOT NULL",
            ") USING DELTA",
            f"CLUSTER BY ({hk}, load_dts)",
            "TBLPROPERTIES ('delta.appendOnly' = 'true');",
        ]
    )


def compile_databricks_satellite_ddl(
    sat: IRSatellite, *, catalog: str = _DEFAULT_UNITY_CATALOG
) -> str:
    cat = _validate_ident(catalog)
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
            f"CREATE TABLE IF NOT EXISTS {cat}.raw_vault.{tbl} (",
            f"    {parent_hk} STRING NOT NULL,",
            "    load_dts TIMESTAMP NOT NULL,",
            f"    {diff_col} STRING NOT NULL,",
            *data_col_defs,
            "    rec_src STRING NOT NULL",
            ") USING DELTA",
            f"CLUSTER BY ({parent_hk}, load_dts)",
            "TBLPROPERTIES ('delta.appendOnly' = 'true');",
        ]
    )
