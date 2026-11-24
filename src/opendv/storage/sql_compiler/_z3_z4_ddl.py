"""Zone Z3 (PIT/Bridge) and Z4 (Information Mart) DDL builders.

Strictly Layer 2a: imports only opendv.models, dialects, sqlglot, _ddl_common, dialect_hashing.
"""

from __future__ import annotations

from collections.abc import Mapping

import sqlglot
from sqlglot import exp

from opendv.models.vault_ir_certified import CertifiedVaultIR, MartViewSpec, PitBridgeSpec
from opendv.storage.dialects import (
    SqlDialectProfile,
    lower_logical_type,
    truncate_identifier_utf8,
)
from opendv.storage.sql_compiler._ddl_common import (
    assert_parsed_clean,
    qualify_name,
    resolve_pit_bridge_columns,
    wrap_replace_view,
)
from opendv.storage.sql_compiler.dialect_hashing import zero_key_hex_sql


def build_pit_bridge_ddl(
    pb: PitBridgeSpec,
    ir: CertifiedVaultIR,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> list[str]:
    """Build Z3 PIT or Bridge rebuild DDL."""
    dialect = profile.sqlglot_dialect or "duckdb"
    full_name = qualify_name("Z3", pb.object_name, aliases)
    t_bin = lower_logical_type("FIXED_BINARY_32", profile.target_id)
    t_ts = lower_logical_type("TIMESTAMP_UTC_US", profile.target_id)
    _, root_hk, targets = resolve_pit_bridge_columns(pb, ir, profile.max_ident_bytes)
    nn = "" if dialect == "clickhouse" else " NOT NULL"

    cols: list[str] = [f"{root_hk} {t_bin}{nn}"]
    if pb.object_kind == "PIT":
        cols.append(f"snapshot_dts {t_ts}{nn}")
        for _, hk_col, ldts_col, app_col, app_ltype in targets:
            cols.append(f"{hk_col} {t_bin}{nn}")
            cols.append(f"{ldts_col} {t_ts}{nn}")
            if app_col is not None:
                t_app = lower_logical_type(app_ltype, profile.target_id)
                cols.append(f"{app_col} {t_app}{nn}")
        pk_cols = [root_hk, "snapshot_dts"]
    else:
        for _, fk_col, _, _, _ in targets:
            cols.append(f"{fk_col} {t_bin}{nn}")
        cols.append(f"load_dts {t_ts}{nn}")
        pk_cols = [root_hk]

    all_defs = (
        [*cols, f"PRIMARY KEY ({', '.join(pk_cols)})"]
        if dialect not in ("clickhouse", "bigquery", "databricks")
        else cols
    )
    body = ",\n    ".join(all_defs)
    if dialect == "clickhouse":
        stmt = (
            f"CREATE OR REPLACE TABLE {full_name} (\n    {body}\n) "
            f"ENGINE = MergeTree() ORDER BY ({', '.join(pk_cols)})"
        )
        return [assert_parsed_clean(stmt, dialect)]
    if profile.z3_rebuild_mode == "DROP_AND_CREATE":
        drop_stmt = exp.Drop(
            kind="TABLE",
            exists=True,
            tables=[exp.to_table(full_name, dialect=dialect)],
        ).sql(dialect=dialect)
        create_stmt = f"CREATE TABLE {full_name} (\n    {body}\n)"
        return [
            assert_parsed_clean(drop_stmt, dialect),
            assert_parsed_clean(create_stmt, dialect),
        ]
    stmt = f"CREATE OR REPLACE TABLE {full_name} (\n    {body}\n)"
    return [assert_parsed_clean(stmt, dialect)]


def build_mart_view_ddl(
    mart: MartViewSpec,
    ir: CertifiedVaultIR,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> list[str]:
    """Build Z4 Information Mart view DDL excluding Zero/Ghost sentinel rows."""
    dialect = profile.sqlglot_dialect or "duckdb"
    mart_full = qualify_name("Z4", mart.view_name, aliases)
    hub = next((h for h in ir.hubs if h.hub_name == mart.root_entity), None)
    if hub is not None and hub.is_planned_concept:
        return []

    root_full = qualify_name("Z2", mart.root_entity, aliases)
    src_root_hk = (
        hub.columns[0].name
        if (hub and hub.columns)
        else f"hk_{mart.root_entity.removeprefix('hub_')}"
    )
    used_aliases: set[str] = set()
    raw_root_alias = aliases.get(mart.root_entity, mart.root_entity)
    root_alias = (
        raw_root_alias
        if len(raw_root_alias.encode("utf-8")) <= profile.max_ident_bytes
        else truncate_identifier_utf8(mart.root_entity, profile.max_ident_bytes, used_aliases)
    )
    used_aliases.add(root_alias)
    alias_rewrite: dict[str, str] = {mart.root_entity: root_alias}
    zero_00 = zero_key_hex_sql(dialect, is_error=False)
    zero_ff = zero_key_hex_sql(dialect, is_error=True)

    sats_by_name = {s.sat_name: s for s in ir.satellites}
    ctes: list[str] = []
    joins: list[str] = []
    for ent in mart.joined_entities:
        sat = sats_by_name.get(ent)
        if sat is None:
            continue
        sat_full = qualify_name(sat.zone_id, sat.sat_name, aliases)
        parent_hk = sat.columns[0].name if sat.columns else src_root_hk
        order_parts = []
        if sat.is_bitemporal and sat.applied_dts_col:
            order_parts.append(f"{sat.applied_dts_col} DESC")
        order_parts.append("load_dts DESC")
        order_sql = ", ".join(order_parts)
        raw_ent_alias = aliases.get(ent, ent)
        ent_alias = (
            raw_ent_alias
            if len(raw_ent_alias.encode("utf-8")) <= profile.max_ident_bytes
            and raw_ent_alias not in used_aliases
            else truncate_identifier_utf8(ent, profile.max_ident_bytes, used_aliases)
        )
        used_aliases.add(ent_alias)
        alias_rewrite[ent] = ent_alias
        cte_name = truncate_identifier_utf8(f"cur_{ent}", profile.max_ident_bytes, used_aliases)
        sat_cols_csv = ", ".join(c.name for c in sat.columns)
        ctes.append(
            f"{cte_name} AS (\n"
            f"    SELECT {sat_cols_csv}, "
            f"ROW_NUMBER() OVER (PARTITION BY {parent_hk} ORDER BY {order_sql}) AS _rn\n"
            f"    FROM {sat_full}\n"
            f"    WHERE {parent_hk} <> {zero_00} AND {parent_hk} <> {zero_ff}\n"
            f")"
        )
        joins.append(
            f"LEFT JOIN {cte_name} AS {ent_alias} "
            f"ON {ent_alias}.{parent_hk} = {root_alias}.{src_root_hk} AND {ent_alias}._rn = 1"
        )

    sel_cols: list[str] = []
    used_cols: set[str] = set()
    for col in mart.columns:
        raw_expr = mart.select_exprs.get(col.name, f"{mart.root_entity}.{col.name}")
        expr_ast = sqlglot.parse_one(raw_expr, read=dialect)
        for cnode in expr_ast.find_all(exp.Column):
            if cnode.table and cnode.table in alias_rewrite:
                cnode.set("table", exp.to_identifier(alias_rewrite[cnode.table], quoted=False))
        expr_sql = expr_ast.sql(dialect=dialect)
        col_out = (
            col.name
            if (
                len(col.name.encode("utf-8")) <= profile.max_ident_bytes
                and col.name not in used_cols
            )
            else truncate_identifier_utf8(col.name, profile.max_ident_bytes, used_cols)
        )
        used_cols.add(col_out)
        if isinstance(expr_ast, exp.Column) and expr_ast.name == col_out:
            sel_cols.append(expr_sql)
        else:
            sel_cols.append(f"{expr_sql} AS {col_out}")
    if not sel_cols:
        sel_cols = [f"{root_alias}.{src_root_hk}"]

    cte_prefix = f"WITH\n{',\n'.join(ctes)}\n" if ctes else ""
    join_block = ("\n" + "\n".join(joins)) if joins else ""
    select_sql = (
        f"{cte_prefix}SELECT\n    {',\n    '.join(sel_cols)}\n"
        f"FROM {root_full} AS {root_alias}{join_block}\n"
        f"WHERE {root_alias}.{src_root_hk} <> {zero_00} "
        f"AND {root_alias}.{src_root_hk} <> {zero_ff}"
    )
    return wrap_replace_view(mart_full, select_sql, profile)


__all__ = ["build_mart_view_ddl", "build_pit_bridge_ddl"]
