"""Zone Z0..Z2 DDL builders and full Z0..Z4 DDL orchestration.

Strictly Layer 2a: imports only opendv.models, dialects, _ddl_common, _z3_z4_ddl, dialect_hashing.
"""

from __future__ import annotations

from collections.abc import Mapping

from opendv.models.vault_ir import PortableLogicalType
from opendv.models.vault_ir_certified import (
    BronzeTableSpec,
    CertifiedHubSpec,
    CertifiedLinkSpec,
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    RefTableSpec,
    StagingViewSpec,
)
from opendv.storage.dialects import SqlDialectProfile, lower_logical_type
from opendv.storage.sql_compiler._ddl_common import (
    ZONE_SCHEMAS,
    alias_of,
    assert_parsed_clean,
    col_def_sql,
    planned_exclusion_sets,
    qualify_name,
    resolve_pit_bridge_columns,
    resolve_staging_hd_col_map,
    wrap_replace_view,
)
from opendv.storage.sql_compiler._z3_z4_ddl import build_mart_view_ddl, build_pit_bridge_ddl
from opendv.storage.sql_compiler.dialect_hashing import compile_hash_recipe_sql


def _wrap_create_table(
    full_table: str,
    cols_sql: list[str],
    pk_cols: list[str],
    profile: SqlDialectProfile,
    *,
    order_by_cols: list[str] | None = None,
) -> str:
    dialect = profile.sqlglot_dialect or "duckdb"
    all_defs = list(cols_sql)
    if pk_cols and dialect not in ("clickhouse", "bigquery", "databricks"):
        all_defs.append(f"PRIMARY KEY ({', '.join(pk_cols)})")
    body = ",\n    ".join(all_defs)

    if dialect == "clickhouse":
        ob = ", ".join(order_by_cols or pk_cols or ["tuple()"])
        raw = (
            f"CREATE TABLE IF NOT EXISTS {full_table} (\n    {body}\n) "
            f"ENGINE = MergeTree() ORDER BY ({ob})"
        )
        return assert_parsed_clean(raw, dialect)

    if dialect == "tsql":
        create_stmt = f"CREATE TABLE {full_table} (\n    {body}\n)"
        wrapped = f"IF OBJECT_ID('{full_table}', 'U') IS NULL\nBEGIN\n    {create_stmt};\nEND"
        return assert_parsed_clean(wrapped, dialect)

    raw = f"CREATE TABLE IF NOT EXISTS {full_table} (\n    {body}\n)"
    return assert_parsed_clean(raw, dialect)


def build_schema_ddl(profile: SqlDialectProfile) -> list[str]:
    """Emit schema/database creation DDL for Zones Z0..Z4."""
    dialect = profile.sqlglot_dialect or "duckdb"
    stmts: list[str] = []
    for schema in ZONE_SCHEMAS.values():
        if dialect == "clickhouse":
            sql = f"CREATE DATABASE IF NOT EXISTS {schema}"
        elif dialect == "tsql":
            sql = f"IF SCHEMA_ID('{schema}') IS NULL BEGIN CREATE SCHEMA {schema}; END"
        else:
            sql = f"CREATE SCHEMA IF NOT EXISTS {schema}"
        stmts.append(assert_parsed_clean(sql, dialect))
    return stmts


def build_bronze_table_ddl(
    bronze: BronzeTableSpec,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> str:
    """Build Z0 Bronze table DDL."""
    dialect = profile.sqlglot_dialect or "duckdb"
    full_name = qualify_name("Z0", bronze.table_name, aliases)
    cols = [col_def_sql(c, profile.target_id, dialect) for c in bronze.columns]
    ob = [bronze.columns[0].name] if bronze.columns else []
    return _wrap_create_table(full_name, cols, [], profile, order_by_cols=ob)


def build_ref_table_ddl(
    ref: RefTableSpec,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> str:
    """Build Z0 Reference enumeration table DDL."""
    dialect = profile.sqlglot_dialect or "duckdb"
    full_name = qualify_name("Z0", ref.ref_name, aliases)
    t_str = lower_logical_type(
        "UTF8_STRING", profile.target_id, include_tsql_collation=(dialect == "tsql")
    )
    nn = "" if dialect == "clickhouse" else " NOT NULL"
    cols = [f"{ref.code_col} {t_str}{nn}", f"{ref.label_col} {t_str}{nn}"]
    return _wrap_create_table(full_name, cols, [ref.code_col], profile)


def build_staging_view_ddl(
    stg: StagingViewSpec,
    ir: CertifiedVaultIR,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
    hd_col_map: Mapping[tuple[str, str], str],
) -> list[str]:
    """Build Z1 Staging view DDL with Alg A.5 hash expressions and disambiguated hash_diff."""
    dialect = profile.sqlglot_dialect or "duckdb"
    bronze = next((b for b in ir.bronze_tables if b.source_table_id == stg.source_table_id), None)
    if bronze is None:
        return []

    bronze_full = qualify_name("Z0", bronze.table_name, aliases)
    stg_full = qualify_name("Z1", stg.view_name, aliases)
    col_types: dict[str, PortableLogicalType] = {c.name: c.logical_type for c in bronze.columns}
    col_names = {c.name for c in bronze.columns}

    hub_tier_by_col: dict[str, str] = {}
    for h in ir.hubs:
        if h.is_planned_concept:
            continue
        for sb in h.source_bindings:
            if sb.table_id == stg.source_table_id and sb.hash_recipe is not None:
                hub_tier_by_col[sb.hash_recipe.target_col] = h.key_tier

    _, _, excluded_sats = planned_exclusion_sets(ir)
    sats_for_tbl = [
        s
        for s in ir.satellites
        if s.sat_name not in excluded_sats
        and s.source_table_id == stg.source_table_id
        and s.hash_recipe is not None
    ]
    sat_idx = 0

    projections: list[str] = []
    projected_names: set[str] = set()

    for target_col, src_col in stg.col_lineage_mappings.items():
        if src_col in col_names:
            proj = f"src.{src_col} AS {target_col}" if target_col != src_col else f"src.{src_col}"
            projections.append(proj)
            projected_names.add(target_col)

    for pf in stg.projected_fields:
        if pf.name not in projected_names and pf.name in col_names:
            projections.append(f"src.{pf.name}")
            projected_names.add(pf.name)

    if "load_dts" not in projected_names:
        ts_type = lower_logical_type("TIMESTAMP_UTC_US", profile.target_id)
        ts_src = "now64(6, 'UTC')" if dialect == "clickhouse" else "CURRENT_TIMESTAMP"
        projections.append(f"CAST({ts_src} AS {ts_type}) AS load_dts")
        projected_names.add("load_dts")
    if "rec_src" not in projected_names:
        projections.append(f"'{stg.source_id}' AS rec_src")
        projected_names.add("rec_src")

    seen_recipes: set[str] = set()
    for recipe in stg.hash_recipes:
        if recipe.recipe_kind == "SAT_HASH_DIFF":
            alias_col = recipe.target_col
            if sat_idx < len(sats_for_tbl):
                sat = sats_for_tbl[sat_idx]
                alias_col = hd_col_map.get((stg.view_name, sat.sat_name), recipe.target_col)
                sat_idx += 1
            elif alias_col in seen_recipes:
                alias_col = f"{recipe.target_col}__{sat_idx}"
                sat_idx += 1
        else:
            alias_col = recipe.target_col

        if alias_col in seen_recipes or alias_col in projected_names:
            continue
        seen_recipes.add(alias_col)
        tier = hub_tier_by_col.get(recipe.target_col, "TIER_2_ENTERPRISE_SOR")
        expr_sql = compile_hash_recipe_sql(
            recipe,
            dialect,
            key_tier=tier,  # type: ignore[arg-type]
            col_types=col_types,
            table_alias="src",
        )
        projections.append(f"{expr_sql} AS {alias_col}")

    select_sql = f"SELECT\n    {',\n    '.join(projections)}\nFROM {bronze_full} AS src"
    return wrap_replace_view(stg_full, select_sql, profile)


def build_hub_ddl(
    hub: CertifiedHubSpec,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> str:
    """Build Z2 Hub DDL."""
    dialect = profile.sqlglot_dialect or "duckdb"
    full_name = qualify_name(hub.zone_id, hub.hub_name, aliases)
    cols = [col_def_sql(c, profile.target_id, dialect) for c in hub.columns]
    default_hk = f"hk_{hub.hub_name.removeprefix('hub_')}"
    hk_col = next((c.name for c in hub.columns if c.name.startswith("hk_")), default_hk)
    return _wrap_create_table(full_name, cols, [hk_col], profile)


def build_link_ddl(
    lnk: CertifiedLinkSpec,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> str:
    """Build Z2/Z3 Link DDL."""
    dialect = profile.sqlglot_dialect or "duckdb"
    full_name = qualify_name(lnk.zone_id, lnk.link_name, aliases)
    cols = [col_def_sql(c, profile.target_id, dialect) for c in lnk.columns]
    hk_col = next((c.name for c in lnk.columns if c.name.startswith("hk_")), lnk.columns[0].name)
    return _wrap_create_table(full_name, cols, [hk_col], profile)


def build_sat_ddl(
    sat: CertifiedSatelliteSpec,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> str:
    """Build Z2/Z3 Satellite DDL (STANDARD, MAS, ESAT, PII, AI_PROVENANCE, EXT)."""
    dialect = profile.sqlglot_dialect or "duckdb"
    full_name = qualify_name(sat.zone_id, sat.sat_name, aliases)
    cols = [col_def_sql(c, profile.target_id, dialect) for c in sat.columns]
    parent_hk = sat.columns[0].name if sat.columns else "parent_hk"
    pk_cols = [parent_hk, "load_dts"]
    if sat.is_bitemporal and sat.applied_dts_col:
        pk_cols.append(sat.applied_dts_col)
    if sat.sat_kind == "MAS" and sat.child_dependent_keys:
        if dialect == "tsql":
            non_max = [
                cdk
                for cdk in sat.child_dependent_keys
                if any(
                    f.name == cdk
                    and f.logical_type in ("INT32", "INT64", "DATE32", "FIXED_BINARY_32")
                    for f in sat.columns
                )
            ]
            pk_cols.extend(non_max)
        else:
            pk_cols.extend(sat.child_dependent_keys)
    return _wrap_create_table(
        full_name, cols, pk_cols, profile, order_by_cols=[parent_hk, "load_dts"]
    )


def compile_full_ddl(
    ir: CertifiedVaultIR,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
    hd_col_map: Mapping[tuple[str, str], str],
) -> list[str]:
    """Compile ordered Z0 -> Z1 -> Z2 -> Z3 -> Z4 DDL statements."""
    planned_hubs, excluded_links, excluded_sats = planned_exclusion_sets(ir)
    stmts: list[str] = list(build_schema_ddl(profile))

    for b in ir.bronze_tables:
        stmts.append(build_bronze_table_ddl(b, profile, aliases))
    for r in ir.reference_tables:
        stmts.append(build_ref_table_ddl(r, profile, aliases))
    for stg in ir.staging_views:
        stmts.extend(build_staging_view_ddl(stg, ir, profile, aliases, hd_col_map))

    for h in ir.hubs:
        if h.hub_name not in planned_hubs:
            stmts.append(build_hub_ddl(h, profile, aliases))
    for lnk in ir.links:
        if lnk.link_name not in excluded_links:
            stmts.append(build_link_ddl(lnk, profile, aliases))
    for sat in ir.satellites:
        if sat.sat_name not in excluded_sats:
            stmts.append(build_sat_ddl(sat, profile, aliases))

    for pb in ir.pit_bridges:
        if pb.root_hub not in planned_hubs:
            stmts.extend(build_pit_bridge_ddl(pb, ir, profile, aliases))
    for mart in ir.information_marts:
        if mart.root_entity not in planned_hubs:
            stmts.extend(build_mart_view_ddl(mart, ir, profile, aliases))
    return stmts


__all__ = [
    "ZONE_SCHEMAS",
    "alias_of",
    "build_bronze_table_ddl",
    "build_hub_ddl",
    "build_link_ddl",
    "build_mart_view_ddl",
    "build_pit_bridge_ddl",
    "build_ref_table_ddl",
    "build_sat_ddl",
    "build_schema_ddl",
    "build_staging_view_ddl",
    "compile_full_ddl",
    "planned_exclusion_sets",
    "qualify_name",
    "resolve_pit_bridge_columns",
    "resolve_staging_hd_col_map",
]
