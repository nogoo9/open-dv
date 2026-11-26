"""Zone Z3 (PIT/Bridge) and Z4 (Information Mart) DDL builders.

Strictly Layer 2a: imports only opendv.models, dialects, sqlglot, _ddl_common, dialect_hashing.
"""

from __future__ import annotations

from collections.abc import Mapping

import sqlglot
from sqlglot import exp

from opendv.models.vault_ir import DecimalType, PortableLogicalType, hub_canonical_bk_cols
from opendv.models.vault_ir_certified import CertifiedVaultIR, MartViewSpec, PitBridgeSpec
from opendv.storage.dialects import (
    SqlDialectProfile,
    lower_logical_type,
    truncate_identifier_utf8,
)
from opendv.storage.sql_compiler._ddl_common import (
    assert_parsed_clean,
    planned_exclusion_sets,
    qualify_name,
    resolve_bridge_hop_columns,
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
    nn = "" if dialect == "clickhouse" else " NOT NULL"

    if pb.object_kind == "PIT":
        _, root_hk, targets = resolve_pit_bridge_columns(pb, ir, profile.max_ident_bytes)
        cols: list[str] = [f"{root_hk} {t_bin}{nn}", f"snapshot_dts {t_ts}{nn}"]
        for _, hk_col, ldts_col, app_col, app_ltype in targets:
            cols.append(f"{hk_col} {t_bin}{nn}")
            cols.append(f"{ldts_col} {t_ts}{nn}")
            if app_col is not None:
                t_app = lower_logical_type(app_ltype, profile.target_id)
                cols.append(f"{app_col} {t_app}{nn}")
        pk_cols = [root_hk, "snapshot_dts"]
    else:
        _, root_hk, hop_specs = resolve_bridge_hop_columns(pb, ir, profile.max_ident_bytes)
        cols = [f"{root_hk} {t_bin}{nn}"]
        for hop in hop_specs:
            cols.append(f"{hop.brg_link_hk_col} {t_bin}{nn}")
            if hop.brg_to_hub_hk_col is not None:
                cols.append(f"{hop.brg_to_hub_hk_col} {t_bin}{nn}")
        cols.append(f"load_dts {t_ts}{nn}")
        pk_cols = [root_hk, *(hop.brg_link_hk_col for hop in hop_specs)]

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


def _portable_type_from_cast_node(
    dt_node: exp.Expr | None,
    fallback: PortableLogicalType,
) -> PortableLogicalType:
    if dt_node is None:
        return fallback
    upper = dt_node.sql(dialect="duckdb").strip().upper()
    if upper.startswith(("DECIMAL", "NUMERIC")):
        if "(" in upper:
            inner = upper.split("(", 1)[1].rstrip(")")
            parts = [p.strip() for p in inner.split(",")]
            prec = min(38, max(1, int(parts[0]))) if parts and parts[0].isdigit() else 38
            scale = min(prec, max(0, int(parts[1]))) if len(parts) > 1 and parts[1].isdigit() else 0
            return DecimalType(precision=prec, scale=scale)
        return DecimalType(precision=38, scale=10)
    if "BOOL" in upper or upper == "BIT":
        return "BOOLEAN"
    if upper in ("INT", "INT4", "INTEGER", "SMALLINT", "TINYINT"):
        return "INT32"
    if any(tok in upper for tok in ("BIGINT", "INT8", "INT64", "HUGEINT")):
        return "INT64"
    if upper in ("DOUBLE", "FLOAT", "REAL", "FLOAT8", "FLOAT4"):
        return DecimalType(precision=38, scale=10)
    if upper == "DATE":
        return "DATE32"
    if "TIMESTAMP" in upper or "DATETIME" in upper:
        return "TIMESTAMP_UTC_US"
    if "JSON" in upper:
        return "JSON_DOCUMENT"
    if "BLOB" in upper or "BINARY" in upper or "BYTEA" in upper:
        return "FIXED_BINARY_32"
    if any(tok in upper for tok in ("VARCHAR", "TEXT", "STRING", "CHAR")):
        return "UTF8_STRING"
    return fallback


def build_esat_intervals_view_ddl(
    mart: MartViewSpec,
    ir: CertifiedVaultIR,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> list[str]:
    """Build Z3 ESAT interval projection view DDL (effective_from, effective_to, is_current)."""
    dialect = profile.sqlglot_dialect or "duckdb"
    mart_full = qualify_name(mart.zone_id, mart.view_name, aliases)
    _, excluded_links, excluded_sats = planned_exclusion_sets(ir)
    sat = next((s for s in ir.satellites if s.sat_name == mart.root_entity), None)
    if sat is None or sat.sat_name in excluded_sats:
        return []
    parent_lnk = next((lk for lk in ir.links if lk.link_name == sat.parent_name), None)
    if parent_lnk is None and mart.joined_entities:
        parent_lnk = next(
            (lk for lk in ir.links if lk.link_name == mart.joined_entities[0]), None
        )
    if parent_lnk is None or parent_lnk.link_name in excluded_links:
        return []

    sat_full = qualify_name(sat.zone_id, sat.sat_name, aliases)
    lnk_full = qualify_name(parent_lnk.zone_id, parent_lnk.link_name, aliases)
    lnk_hk = sat.columns[0].name if sat.columns else parent_lnk.columns[0].name
    p_lnk_hk = parent_lnk.columns[0].name
    sat_col_names = {c.name for c in sat.columns}

    dks = list(sat.driving_keys) or (
        [parent_lnk.participants[0].fk_col_name] if parent_lnk.participants else [lnk_hk]
    )
    dk_exprs = [f"sat.{dk}" if dk in sat_col_names else f"lnk.{dk}" for dk in dks]
    driving_expr = dk_exprs[0]

    out_hk = mart.columns[0].name if len(mart.columns) >= 1 else lnk_hk
    out_dk = mart.columns[1].name if len(mart.columns) >= 2 else dks[0]
    out_eff_from = mart.columns[2].name if len(mart.columns) >= 3 else "effective_from"
    out_eff_to = mart.columns[3].name if len(mart.columns) >= 4 else "effective_to"
    out_is_cur = mart.columns[4].name if len(mart.columns) >= 5 else "is_current"

    app_col = sat.applied_dts_col if (sat.is_bitemporal and sat.applied_dts_col) else None
    if app_col is not None:
        app_f = next((c for c in sat.columns if c.name == app_col), None)
        t_ts = lower_logical_type("TIMESTAMP_UTC_US", profile.target_id)
        app_expr = (
            f"CAST(sat.{app_col} AS {t_ts})"
            if (app_f is not None and app_f.logical_type != "TIMESTAMP_UTC_US")
            else f"sat.{app_col}"
        )
        eff_expr = f"COALESCE({app_expr}, sat.load_dts)"
        order_sql = f"COALESCE({app_expr}, sat.load_dts) ASC, sat.load_dts ASC"
    else:
        eff_expr = "sat.load_dts"
        order_sql = "sat.load_dts ASC"

    lead_arg = f"toNullable({eff_expr})" if dialect == "clickhouse" else eff_expr
    next_eff = f"LEAD({lead_arg}) OVER (PARTITION BY {', '.join(dk_exprs)} ORDER BY {order_sql})"
    is_cur_expr = (
        f"CASE WHEN {next_eff} IS NULL THEN CAST(1 AS BIT) ELSE CAST(0 AS BIT) END"
        if dialect == "tsql"
        else f"CASE WHEN {next_eff} IS NULL THEN TRUE ELSE FALSE END"
    )

    zero_00 = zero_key_hex_sql(dialect, is_error=False)
    zero_ff = zero_key_hex_sql(dialect, is_error=True)
    where_parts = [
        f"sat.{lnk_hk} <> {zero_00}",
        f"sat.{lnk_hk} <> {zero_ff}",
        f"lnk.{p_lnk_hk} <> {zero_00}",
        f"lnk.{p_lnk_hk} <> {zero_ff}",
        *(f"{dk_e} <> {zero_00} AND {dk_e} <> {zero_ff}" for dk_e in dk_exprs),
    ]
    select_sql = (
        f"SELECT\n"
        f"    sat.{lnk_hk} AS {out_hk},\n"
        f"    {driving_expr} AS {out_dk},\n"
        f"    {eff_expr} AS {out_eff_from},\n"
        f"    {next_eff} AS {out_eff_to},\n"
        f"    {is_cur_expr} AS {out_is_cur}\n"
        f"FROM {sat_full} AS sat\n"
        f"INNER JOIN {lnk_full} AS lnk ON lnk.{p_lnk_hk} = sat.{lnk_hk}\n"
        f"WHERE {' AND '.join(where_parts)}"
    )
    return wrap_replace_view(mart_full, select_sql, profile)


def build_mart_view_ddl(
    mart: MartViewSpec,
    ir: CertifiedVaultIR,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> list[str]:
    """Build Z3/Z4 Information Mart or ESAT interval view DDL excluding Zero/Ghost sentinel rows."""
    if mart.view_kind == "ESAT_INTERVALS":
        return build_esat_intervals_view_ddl(mart, ir, profile, aliases)

    dialect = profile.sqlglot_dialect or "duckdb"
    mart_full = qualify_name(mart.zone_id, mart.view_name, aliases)
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
        expr_ast = sqlglot.parse_one(raw_expr, read="duckdb")
        for cnode in expr_ast.find_all(exp.Column):
            if cnode.table and cnode.table in alias_rewrite:
                cnode.set("table", exp.to_identifier(alias_rewrite[cnode.table], quoted=False))
        for cast_node in expr_ast.find_all(exp.Cast, exp.TryCast):
            ltype = _portable_type_from_cast_node(cast_node.args.get("to"), col.logical_type)
            dt_sql = lower_logical_type(ltype, profile.target_id, include_tsql_collation=False)
            cast_node.set("to", exp.DataType.build(dt_sql, dialect=dialect))
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
        canon_bks = hub_canonical_bk_cols(hub) if hub is not None else []
        sel_cols = [f"{root_alias}.{src_root_hk}", *(f"{root_alias}.{bk}" for bk in canon_bks)]

    cte_prefix = f"WITH\n{',\n'.join(ctes)}\n" if ctes else ""
    join_block = ("\n" + "\n".join(joins)) if joins else ""
    select_sql = (
        f"{cte_prefix}SELECT\n    {',\n    '.join(sel_cols)}\n"
        f"FROM {root_full} AS {root_alias}{join_block}\n"
        f"WHERE {root_alias}.{src_root_hk} <> {zero_00} "
        f"AND {root_alias}.{src_root_hk} <> {zero_ff}"
    )
    return wrap_replace_view(mart_full, select_sql, profile)


__all__ = ["build_esat_intervals_view_ddl", "build_mart_view_ddl", "build_pit_bridge_ddl"]
