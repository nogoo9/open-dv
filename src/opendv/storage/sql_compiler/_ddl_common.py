"""Shared DDL helpers (zone schemas, aliases, planned exclusions, view wrapping).

Strictly Layer 2a: imports only opendv.models, dialects, sqlglot, and stdlib.
"""

from __future__ import annotations

from collections.abc import Mapping

import sqlglot
from sqlglot import exp

from opendv.models.project import DbtSqlDialect
from opendv.models.vault_ir import PortableLogicalType
from opendv.models.vault_ir_certified import (
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    IRField,
    PitBridgeSpec,
)
from opendv.storage.dialects import (
    SqlDialectProfile,
    lower_logical_type,
    truncate_identifier_utf8,
)

ZONE_SCHEMAS: dict[str, str] = {
    "Z0": "bronze",
    "Z1": "staging",
    "Z2": "raw_vault",
    "Z3": "business_vault",
    "Z4": "information_mart",
}


def alias_of(name: str, aliases: Mapping[str, str]) -> str:
    """Resolve physical identifier alias or return original name."""
    return aliases.get(name, name)


def qualify_name(zone_id: str, logical_name: str, aliases: Mapping[str, str]) -> str:
    """Return `<schema>.<physical_name>` honoring pre-qualified MartViewSpec names."""
    schema = ZONE_SCHEMAS[zone_id]
    if "." in logical_name:
        raw_schema, raw_obj = logical_name.split(".", 1)
        return f"{alias_of(raw_schema, aliases)}.{alias_of(raw_obj, aliases)}"
    return f"{schema}.{alias_of(logical_name, aliases)}"


def planned_exclusion_sets(
    ir: CertifiedVaultIR,
) -> tuple[set[str], set[str], set[str]]:
    """Return (planned_hubs, excluded_links, excluded_sats) for is_planned_concept=True."""
    planned_hubs = {h.hub_name for h in ir.hubs if h.is_planned_concept}
    excluded_links = {
        lnk.link_name
        for lnk in ir.links
        if any(p.hub_name in planned_hubs for p in lnk.participants)
    }
    excluded_sats = {
        sat.sat_name
        for sat in ir.satellites
        if sat.parent_name in planned_hubs or sat.parent_name in excluded_links
    }
    return planned_hubs, excluded_links, excluded_sats


def resolve_staging_hd_col_map(
    ir: CertifiedVaultIR,
    max_ident_bytes: int = 255,
) -> dict[tuple[str, str], str]:
    """Disambiguate and bound SAT_HASH_DIFF target columns per (stg.view_name, sat.sat_name)."""
    _, _, excluded_sats = planned_exclusion_sets(ir)
    sats_by_table: dict[str, list[CertifiedSatelliteSpec]] = {}
    for sat in ir.satellites:
        if sat.sat_name in excluded_sats or sat.hash_recipe is None:
            continue
        sats_by_table.setdefault(sat.source_table_id, []).append(sat)

    mapping: dict[tuple[str, str], str] = {}
    for stg in ir.staging_views:
        sats = sats_by_table.get(stg.source_table_id, [])
        used_hd_cols: set[str] = (
            {f.name for f in stg.projected_fields}
            | set(stg.col_lineage_mappings.keys())
            | {r.target_col for r in stg.hash_recipes if r.recipe_kind != "SAT_HASH_DIFF"}
            | {"load_dts", "rec_src"}
        )
        if len(sats) == 1:
            recipe = sats[0].hash_recipe
            raw_col = recipe.target_col if recipe else "hash_diff"
            mapping[(stg.view_name, sats[0].sat_name)] = truncate_identifier_utf8(
                raw_col, max_ident_bytes, used_hd_cols
            )
        else:
            for sat in sats:
                mapping[(stg.view_name, sat.sat_name)] = truncate_identifier_utf8(
                    f"hash_diff__{sat.sat_name}", max_ident_bytes, used_hd_cols
                )
    return mapping


def col_def_sql(field: IRField, target_id: str, dialect: DbtSqlDialect) -> str:
    """Format a column definition for CREATE TABLE DDL."""
    dtype = lower_logical_type(
        field.logical_type,
        target_id,
        include_tsql_collation=(dialect == "tsql"),
    )
    if dialect == "clickhouse":
        if field.nullable and not str(dtype).startswith(("Array(", "Map(", "JSON")):
            return f"{field.name} Nullable({dtype})"
        return f"{field.name} {dtype}"
    null_kw = "" if field.nullable else " NOT NULL"
    return f"{field.name} {dtype}{null_kw}"


def assert_parsed_clean(sql: str, dialect: DbtSqlDialect) -> str:
    """Assert SQL parses in target dialect with zero exp.Command nodes."""
    nodes = sqlglot.parse(sql, read=dialect)
    for node in nodes:
        if node is not None and any(isinstance(sub, exp.Command) for sub in node.walk()):
            raise ValueError(f"exp.Command emitted in {dialect} DDL: {sql}")
    return sql


def wrap_replace_view(
    full_view: str,
    select_sql: str,
    profile: SqlDialectProfile,
) -> list[str]:
    """Wrap a SELECT query into dialect-specific view replacement DDL."""
    dialect = profile.sqlglot_dialect or "duckdb"
    if profile.view_replace_mode == "DROP_AND_CREATE":
        drop_stmt = exp.Drop(
            kind="VIEW",
            exists=True,
            tables=[exp.to_table(full_view, dialect=dialect)],
        ).sql(dialect=dialect)
        create_stmt = f"CREATE VIEW {full_view} AS\n{select_sql}"
        return [
            assert_parsed_clean(drop_stmt, dialect),
            assert_parsed_clean(create_stmt, dialect),
        ]
    if profile.view_replace_mode == "CREATE_OR_ALTER":
        stmt = f"CREATE OR ALTER VIEW {full_view} AS\n{select_sql}"
        return [assert_parsed_clean(stmt, dialect)]
    stmt = f"CREATE OR REPLACE VIEW {full_view} AS\n{select_sql}"
    return [assert_parsed_clean(stmt, dialect)]


def resolve_pit_bridge_columns(
    pb: PitBridgeSpec,
    ir: CertifiedVaultIR,
    max_ident_bytes: int,
) -> tuple[str, str, list[tuple[str, str, str, str | None, PortableLogicalType]]]:
    """Return (src_root_hk, pit_root_hk, [(tgt, hk_or_fk_col, ldts_col, app_col, app_ltype)])."""
    used: set[str] = set()
    hub = next((h for h in ir.hubs if h.hub_name == pb.root_hub), None)
    src_root_hk = (
        hub.columns[0].name if (hub and hub.columns) else f"hk_{pb.root_hub.removeprefix('hub_')}"
    )
    pit_root_hk = truncate_identifier_utf8(src_root_hk, max_ident_bytes, used)
    out: list[tuple[str, str, str, str | None, PortableLogicalType]] = []
    if pb.object_kind == "PIT":
        used.add("snapshot_dts")
        sats_by_name = {s.sat_name: s for s in ir.satellites}
        for tgt in pb.target_satellites_or_links:
            hk_col = truncate_identifier_utf8(f"{tgt}_hk", max_ident_bytes, used)
            ldts_col = truncate_identifier_utf8(f"{tgt}_load_dts", max_ident_bytes, used)
            sat = sats_by_name.get(tgt)
            if sat is not None and sat.is_bitemporal and sat.applied_dts_col:
                app_col: str | None = truncate_identifier_utf8(
                    f"{tgt}_applied_dts", max_ident_bytes, used
                )
                app_ltype: PortableLogicalType = next(
                    (c.logical_type for c in sat.columns if c.name == sat.applied_dts_col),
                    "TIMESTAMP_UTC_US",
                )
            else:
                app_col, app_ltype = None, "TIMESTAMP_UTC_US"
            out.append((tgt, hk_col, ldts_col, app_col, app_ltype))
    else:
        used.add("load_dts")
        links_by_name = {lk.link_name: lk for lk in ir.links}
        for tgt in pb.target_satellites_or_links:
            lnk = links_by_name.get(tgt)
            raw_fk = lnk.columns[0].name if (lnk and lnk.columns) else f"hk_{tgt}"
            fk_col = truncate_identifier_utf8(raw_fk, max_ident_bytes, used)
            out.append((tgt, fk_col, "load_dts", None, "TIMESTAMP_UTC_US"))
    return src_root_hk, pit_root_hk, out


__all__ = [
    "ZONE_SCHEMAS",
    "alias_of",
    "assert_parsed_clean",
    "col_def_sql",
    "planned_exclusion_sets",
    "qualify_name",
    "resolve_pit_bridge_columns",
    "resolve_staging_hd_col_map",
    "wrap_replace_view",
]
