"""Shared DML helpers, seed/ghost builders, and Z3 PIT/Bridge population DML.

Strictly Layer 2a: imports only opendv.models, dialects, sqlglot, _ddl_common, dialect_hashing.
"""

from __future__ import annotations

from collections.abc import Mapping

import sqlglot
from sqlglot import exp

from opendv.models.project import DbtSqlDialect
from opendv.models.vault_ir import ArrayType, DecimalType, MapType, PortableLogicalType
from opendv.models.vault_ir_certified import (
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    PitBridgeSpec,
    SeedRecordsSpec,
)
from opendv.storage.dialects import SqlDialectProfile, lower_logical_type
from opendv.storage.sql_compiler._ddl_common import qualify_name, resolve_pit_bridge_columns
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
        sql = (
            f"INSERT INTO {target_full} ({cols_csv})\n{select_sql}\n"
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
            f"LEFT ANTI JOIN {target_full} AS ex ON {on_cond}{inv_join}"
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
        elif sat.is_bitemporal and c.name == sat.applied_dts_col:
            vals.append(ghost_ts)
        else:
            vals.append(
                ghost_col_default_sql(
                    c.name, c.logical_type, c.nullable, seed, profile.target_id, dialect
                )
            )

    col_list = ", ".join(cols)
    val_list = ", ".join(vals)
    cond = f"tgt.{parent_hk} = {zero_00} AND tgt.load_dts = {ghost_ts}"

    if dialect == "postgres":
        pk_cols = [parent_hk, "load_dts"]
        if sat.is_bitemporal and sat.applied_dts_col:
            pk_cols.append(sat.applied_dts_col)
        if sat.sat_kind == "MAS" and sat.child_dependent_keys:
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


def build_pit_bridge_dml(
    pb: PitBridgeSpec,
    ir: CertifiedVaultIR,
    seed: SeedRecordsSpec,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> list[str]:
    """Emit Z3 PIT or Bridge population DML with Ghost fallback and bitemporal applied_dts."""
    dialect = profile.sqlglot_dialect or "duckdb"
    pb_full = qualify_name("Z3", pb.object_name, aliases)
    hub_full = qualify_name("Z2", pb.root_hub, aliases)
    src_root_hk, root_hk, targets = resolve_pit_bridge_columns(pb, ir, profile.max_ident_bytes)
    zero_00 = zero_key_hex_sql(dialect, is_error=False)
    zero_ff = zero_key_hex_sql(dialect, is_error=True)
    ghost_ts = ts_literal_sql(seed.ghost_load_dts, profile.target_id, dialect)
    t_ts = lower_logical_type("TIMESTAMP_UTC_US", profile.target_id)

    if pb.object_kind == "PIT":
        sats_by_name = {s.sat_name: s for s in ir.satellites}
        if not any(tgt in sats_by_name for tgt, *_ in targets):
            return []
        snap_parts: list[str] = [
            f"SELECT {src_root_hk} AS _hk, load_dts AS _snap FROM {hub_full} "
            f"WHERE {src_root_hk} <> {zero_00} AND {src_root_hk} <> {zero_ff}"
        ]
        for tgt, _, _, app_col, app_ltype in targets:
            s = sats_by_name.get(tgt)
            if s is None:
                continue
            s_full = qualify_name(s.zone_id, s.sat_name, aliases)
            p_hk = s.columns[0].name
            eff_col = s.applied_dts_col if (s.is_bitemporal and s.applied_dts_col) else "load_dts"
            snap_expr = (
                f"CAST({eff_col} AS {t_ts})" if (app_col and app_ltype == "DATE32") else eff_col
            )
            snap_parts.append(
                f"SELECT {p_hk} AS _hk, {snap_expr} AS _snap FROM {s_full} "
                f"WHERE {p_hk} <> {zero_00} AND {p_hk} <> {zero_ff}"
            )
        union_kw = (
            "\n    UNION DISTINCT\n    "
            if dialect in ("bigquery", "clickhouse")
            else "\n    UNION\n    "
        )
        snap_union = union_kw.join(snap_parts)

        cols = [root_hk, "snapshot_dts"]
        sel_exprs = [f"pts._hk AS {root_hk}", "pts._snap AS snapshot_dts"]
        joins: list[str] = []
        for idx, (tgt, hk_col, ldts_col, app_col, app_ltype) in enumerate(targets):
            s = sats_by_name.get(tgt)
            cols.extend([hk_col, ldts_col])
            if app_col is not None:
                cols.append(app_col)
            if s is None:
                sel_exprs.extend([f"{zero_00} AS {hk_col}", f"{ghost_ts} AS {ldts_col}"])
                if app_col is not None:
                    sel_exprs.append(f"{ghost_ts} AS {app_col}")
                continue
            s_full = qualify_name(s.zone_id, s.sat_name, aliases)
            p_hk = s.columns[0].name
            eff_col = s.applied_dts_col if (s.is_bitemporal and s.applied_dts_col) else "load_dts"
            alias_s = f"s{idx}"
            sel_exprs.append(f"COALESCE({alias_s}._s_hk, {zero_00}) AS {hk_col}")
            sel_exprs.append(f"COALESCE({alias_s}._s_dts, {ghost_ts}) AS {ldts_col}")
            extra_sel = ""
            if app_col is not None and s.applied_dts_col:
                ghost_app = ghost_col_default_sql(
                    s.applied_dts_col, app_ltype, False, seed, profile.target_id, dialect
                )
                sel_exprs.append(f"COALESCE({alias_s}._s_app, {ghost_app}) AS {app_col}")
                extra_sel = f", sat.{s.applied_dts_col} AS _s_app"
            eff_cmp = (
                f"CAST(sat.{eff_col} AS {t_ts})"
                if (app_col and app_ltype == "DATE32")
                else f"sat.{eff_col}"
            )
            joins.append(
                f"LEFT JOIN (\n"
                f"    SELECT p._hk AS _p_hk, p._snap AS _p_snap, "
                f"sat.{p_hk} AS _s_hk, sat.load_dts AS _s_dts{extra_sel},\n"
                f"           ROW_NUMBER() OVER (PARTITION BY p._hk, p._snap "
                f"ORDER BY sat.{eff_col} DESC, sat.load_dts DESC) AS _rn\n"
                f"    FROM (\n    {snap_union}\n    ) AS p\n"
                f"    INNER JOIN {s_full} AS sat ON sat.{p_hk} = p._hk "
                f"AND {eff_cmp} <= p._snap AND sat.{p_hk} <> {zero_00}\n"
                f") AS {alias_s} ON {alias_s}._p_hk = pts._hk "
                f"AND {alias_s}._p_snap = pts._snap AND {alias_s}._rn = 1"
            )
        select_sql = (
            f"SELECT {', '.join(sel_exprs)}\n"
            f"FROM (\n    {snap_union}\n) AS pts\n" + "\n".join(joins)
        )
        return [
            idempotent_insert_from_select(
                pb_full, cols, [root_hk, "snapshot_dts"], select_sql, profile
            )
        ]

    cols = [root_hk]
    sel_exprs = [f"h.{src_root_hk} AS {root_hk}"]
    joins = []
    links_by_name = {lk.link_name: lk for lk in ir.links}
    for idx, (tgt, fk_col, _, _, _) in enumerate(targets):
        lnk = links_by_name.get(tgt)
        cols.append(fk_col)
        if lnk is None:
            sel_exprs.append(f"{zero_00} AS {fk_col}")
            continue
        l_full = qualify_name(lnk.zone_id, lnk.link_name, aliases)
        raw_fk = lnk.columns[0].name
        part_fk = next(
            (p.fk_col_name for p in lnk.participants if p.hub_name == pb.root_hub),
            lnk.participants[0].fk_col_name,
        )
        la = f"l{idx}"
        sel_exprs.append(f"COALESCE({la}.{raw_fk}, {zero_00}) AS {fk_col}")
        joins.append(
            f"LEFT JOIN {l_full} AS {la} ON {la}.{part_fk} = h.{src_root_hk} "
            f"AND {la}.{raw_fk} <> {zero_00} AND {la}.{raw_fk} <> {zero_ff}"
        )
    cols.append("load_dts")
    sel_exprs.append("h.load_dts AS load_dts")
    select_sql = (
        f"SELECT {', '.join(sel_exprs)}\nFROM {hub_full} AS h\n"
        + ("\n".join(joins) + "\n" if joins else "")
        + f"WHERE h.{src_root_hk} <> {zero_00} AND h.{src_root_hk} <> {zero_ff}"
    )
    return [idempotent_insert_from_select(pb_full, cols, [root_hk], select_sql, profile)]


__all__ = [
    "assert_clean_dml",
    "build_pit_bridge_dml",
    "build_sat_ghost_dml",
    "ghost_col_default_sql",
    "idempotent_insert_from_select",
    "ts_literal_sql",
]
