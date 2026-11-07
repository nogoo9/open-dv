"""Vault DDL, Dual Zero-Keys, Ghost Records, and Additive Schema Migration."""

from __future__ import annotations

from typing import Any

from opendv_core.constants import (
    GHOST_DTS,
    GHOST_REC_SRC,
    ZERO_KEY_ERROR,
    ZERO_KEY_REC_SRC,
    ZERO_KEY_UNKNOWN,
)
from opendv_core.models.canonical_ir import (
    CanonicalIR,
    IRHub,
    IRLink,
    IRReferenceTable,
    IRSatellite,
)
from opendv_duckdb_sandbox.compiler_4zone.hashing import _normalize_duckdb_type, _validate_ident
from opendv_duckdb_sandbox.compiler_4zone.schema_utils import (
    _ghost_literal_for_sat_column,
    _link_schema,
    _satellite_all_data_columns,
    _satellite_child_key_names,
    _satellite_schema,
)


def _compile_generic_hub_ddl_and_zero_keys(hub: IRHub) -> str:
    """Compile DDL and Dual Zero-Key (`'0'*64` and `'f'*64`) inserts for an `IRHub`."""
    tbl = _validate_ident(hub.name)
    hk = _validate_ident(hub.hk_column.name)
    bk_defs = [f"    {_validate_ident(bk.name)} VARCHAR NOT NULL," for bk in hub.business_keys]
    ddl = "\n".join(
        [
            f"CREATE TABLE IF NOT EXISTS raw_vault.{tbl} (",
            f"    {hk} CHAR(64) NOT NULL PRIMARY KEY,",
            *bk_defs,
            "    load_dts TIMESTAMP NOT NULL,",
            "    rec_src VARCHAR NOT NULL",
            ");",
        ]
    )
    unknown_bks = ", ".join("'SYSTEM'" if bk.is_bkcc else "'-1'" for bk in hub.business_keys)
    error_bks = ", ".join("'SYSTEM'" if bk.is_bkcc else "'-2'" for bk in hub.business_keys)
    zero_insert = (
        f"INSERT OR IGNORE INTO raw_vault.{tbl} VALUES\n"
        f"    ('{ZERO_KEY_UNKNOWN}', {unknown_bks}, "
        f"TIMESTAMP '{GHOST_DTS}', '{ZERO_KEY_REC_SRC}'),\n"
        f"    ('{ZERO_KEY_ERROR}', {error_bks}, "
        f"TIMESTAMP '{GHOST_DTS}', '{ZERO_KEY_REC_SRC}');"
    )
    return f"{ddl}\n{zero_insert}"


def _compile_generic_reference_ddl_and_seed(ref: IRReferenceTable) -> str:
    """Compile DDL and baseline seed rows for an `IRReferenceTable`."""
    tbl = _validate_ident(ref.name)
    code_col = _validate_ident(ref.natural_code_column)
    return (
        f"CREATE TABLE IF NOT EXISTS raw_vault.{tbl} (\n"
        f"    {code_col} VARCHAR NOT NULL PRIMARY KEY,\n"
        f"    load_dts TIMESTAMP NOT NULL,\n"
        f"    rec_src VARCHAR NOT NULL\n"
        f");\n"
        f"INSERT OR IGNORE INTO raw_vault.{tbl} VALUES\n"
        f"    ('REF-001', TIMESTAMP '2026-10-01 06:00:00', 'SYSTEM_SEED'),\n"
        f"    ('REF-002', TIMESTAMP '2026-10-01 06:00:00', 'SYSTEM_SEED');"
    )


def _compile_generic_link_ddl_and_zero_keys(link: IRLink) -> str:
    """Compile DDL and Dual Zero-Key (`'0'*64` and `'f'*64`) inserts for an `IRLink`."""
    schema = _link_schema(link)
    tbl = _validate_ident(link.name)
    hk = _validate_ident(link.hk_column.name)
    fk_defs = [
        f"    {_validate_ident(ref.fk_column_name)} CHAR(64) NOT NULL,"
        for ref in link.hub_references
    ]
    ddl = "\n".join(
        [
            f"CREATE TABLE IF NOT EXISTS {schema}.{tbl} (",
            f"    {hk} CHAR(64) NOT NULL PRIMARY KEY,",
            *fk_defs,
            "    load_dts TIMESTAMP NOT NULL,",
            "    rec_src VARCHAR NOT NULL",
            ");",
        ]
    )
    unknown_fks = ", ".join(f"'{ZERO_KEY_UNKNOWN}'" for _ in link.hub_references)
    error_fks = ", ".join(f"'{ZERO_KEY_ERROR}'" for _ in link.hub_references)
    zero_insert = (
        f"INSERT OR IGNORE INTO {schema}.{tbl} VALUES\n"
        f"    ('{ZERO_KEY_UNKNOWN}', {unknown_fks}, "
        f"TIMESTAMP '{GHOST_DTS}', '{ZERO_KEY_REC_SRC}'),\n"
        f"    ('{ZERO_KEY_ERROR}', {error_fks}, "
        f"TIMESTAMP '{GHOST_DTS}', '{ZERO_KEY_REC_SRC}');"
    )
    return f"{ddl}\n{zero_insert}"


def _compile_generic_satellite_ddl_and_ghost(sat: IRSatellite) -> str:
    """Compile DDL and Ghost Record (`'1900-01-01 00:00:00'`) insert for an `IRSatellite`."""
    schema = _satellite_schema(sat)
    tbl = _validate_ident(sat.name)
    parent_hk = _validate_ident(sat.parent_hk_column.name)
    has_applied = sat.applied_dts_column is not None
    data_cols = _satellite_all_data_columns(sat)
    sub_seq_names = set(_satellite_child_key_names(sat))

    col_defs: list[str] = [
        f"    {parent_hk} CHAR(64) NOT NULL,",
        "    load_dts TIMESTAMP NOT NULL,",
    ]
    if has_applied:
        col_defs.append("    applied_dts TIMESTAMP NOT NULL,")
    col_defs.extend(
        [
            "    rec_src VARCHAR NOT NULL,",
            "    hash_diff CHAR(64) NOT NULL,",
        ]
    )
    for c in data_cols:
        c_name = _validate_ident(c.name)
        c_type = _normalize_duckdb_type(c.data_type)
        nullability = " NOT NULL" if c.name in sub_seq_names else ""
        col_defs.append(f"    {c_name} {c_type}{nullability},")

    sub_seq_pk_tail = ", ".join(_validate_ident(c) for c in sorted(sub_seq_names))
    pk_cols = [parent_hk, "load_dts"]
    if sub_seq_pk_tail:
        pk_cols.append(sub_seq_pk_tail)
    col_defs.append(f"    PRIMARY KEY ({', '.join(pk_cols)})")

    ddl = "\n".join(
        [
            f"CREATE TABLE IF NOT EXISTS {schema}.{tbl} (",
            *col_defs,
            ");",
        ]
    )

    ghost_cols = [parent_hk, "load_dts"]
    ghost_vals = [f"'{ZERO_KEY_UNKNOWN}'", f"TIMESTAMP '{GHOST_DTS}'"]
    if has_applied:
        ghost_cols.append("applied_dts")
        ghost_vals.append(f"TIMESTAMP '{GHOST_DTS}'")
    ghost_cols.extend(["rec_src", "hash_diff"])
    ghost_vals.extend([f"'{GHOST_REC_SRC}'", f"'{ZERO_KEY_UNKNOWN}'"])
    for col in data_cols:
        if col.name in sub_seq_names:
            ghost_cols.append(_validate_ident(col.name))
            ghost_vals.append(_ghost_literal_for_sat_column(col, sub_seq_names))
    ghost_insert = (
        f"INSERT OR IGNORE INTO {schema}.{tbl} ({', '.join(ghost_cols)}) VALUES\n"
        f"    ({', '.join(ghost_vals)});"
    )
    return f"{ddl}\n{ghost_insert}"


def compile_zone_0_migration_sql(
    ir: CanonicalIR | dict[str, Any],
    *,
    baseline_ir: CanonicalIR | dict[str, Any] | None = None,
) -> str:
    """Compile idempotent `ALTER TABLE <schema>.<sat> ADD COLUMN IF NOT EXISTS` statements."""
    ir_obj = CanonicalIR.model_validate(ir) if isinstance(ir, dict) else ir
    base_obj = (
        CanonicalIR.model_validate(baseline_ir) if isinstance(baseline_ir, dict) else baseline_ir
    )
    prev_sats = {s.name: s for s in base_obj.satellites} if base_obj is not None else None

    stmts: list[str] = []
    for sat in ir_obj.satellites:
        schema = _satellite_schema(sat)
        tbl = _validate_ident(sat.name)
        child_keys = set(_satellite_child_key_names(sat))
        data_cols = [c for c in _satellite_all_data_columns(sat) if c.name not in child_keys]
        if prev_sats is not None:
            if sat.name not in prev_sats:
                continue
            prev_col_names = {
                c.name.lower() for c in _satellite_all_data_columns(prev_sats[sat.name])
            }
            target_cols = [c for c in data_cols if c.name.lower() not in prev_col_names]
        else:
            target_cols = data_cols

        for col in target_cols:
            c_name = _validate_ident(col.name)
            c_type = _normalize_duckdb_type(col.data_type)
            stmts.append(f"ALTER TABLE {schema}.{tbl} ADD COLUMN IF NOT EXISTS {c_name} {c_type};")

    if not stmts:
        return ""
    return "\n".join(
        [
            "-- =============================================================================",
            "-- ZONE 0 MIGRATION: IDEMPOTENT ADDITIVE SATELLITE SCHEMA EVOLUTION (EVO-01)",
            "-- =============================================================================",
            *stmts,
        ]
    )


__all__ = [
    "_compile_generic_hub_ddl_and_zero_keys",
    "_compile_generic_link_ddl_and_zero_keys",
    "_compile_generic_reference_ddl_and_seed",
    "_compile_generic_satellite_ddl_and_ghost",
    "compile_zone_0_migration_sql",
]
