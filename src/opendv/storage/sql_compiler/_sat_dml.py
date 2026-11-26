"""Satellite (STANDARD, PII, EXT, AI_PROVENANCE, MAS, ESAT, Bitemporal) load DML builders.

Strictly Layer 2a: imports only opendv.models, dialects, _ddl_common, _dml_common, _scalar_exprs.
"""

from __future__ import annotations

from collections.abc import Mapping

from opendv.models.project import DbtSqlDialect
from opendv.models.vault_ir_certified import (
    CertifiedLinkSpec,
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    SourceBindingSpec,
    StagingViewSpec,
)
from opendv.storage.dialects import SqlDialectProfile
from opendv.storage.sql_compiler._ddl_common import qualify_name
from opendv.storage.sql_compiler._dml_common import assert_clean_dml
from opendv.storage.sql_compiler._scalar_exprs import predicate_to_sqlglot
from opendv.storage.sql_compiler.dialect_hashing import zero_key_hex_sql


def _sat_upsert_sql(
    sat_full: str,
    cols: list[str],
    pk_cols: list[str],
    delta_q: str,
    dialect: DbtSqlDialect,
) -> str:
    cols_csv = ", ".join(cols)
    if dialect == "postgres":
        sql = (
            f"INSERT INTO {sat_full} ({cols_csv})\n{delta_q}\n"
            f"ON CONFLICT ({', '.join(pk_cols)}) DO NOTHING"
        )
        return assert_clean_dml(sql, dialect)
    if dialect in ("databricks", "snowflake", "bigquery", "tsql"):
        hold = " WITH (HOLDLOCK)" if dialect == "tsql" else ""
        src_vals = ", ".join(f"s.{c}" for c in cols)
        on_cond = " AND ".join(f"tgt.{k} = s.{k}" for k in pk_cols)
        sql = (
            f"MERGE INTO {sat_full}{hold} AS tgt USING (\n{delta_q}\n) AS s "
            f"ON {on_cond} WHEN NOT MATCHED THEN INSERT ({cols_csv}) VALUES ({src_vals})"
        )
        if dialect == "tsql":
            sql += ";"
        return assert_clean_dml(sql, dialect)
    return assert_clean_dml(f"INSERT INTO {sat_full} ({cols_csv})\n{delta_q}", dialect)


def _sat_where_and_dedup(
    sat: CertifiedSatelliteSpec,
    sat_sb: SourceBindingSpec | None,
    sat_col_maps: Mapping[str, str],
    parent_hk: str,
    applied_col: str | None,
    dialect: DbtSqlDialect,
    hd_col: str | None = None,
) -> tuple[str, str]:
    zero_00 = zero_key_hex_sql(dialect, is_error=False)
    zero_ff = zero_key_hex_sql(dialect, is_error=True)
    where_parts = [f"src.{parent_hk} <> {zero_00}", f"src.{parent_hk} <> {zero_ff}"]
    sem = (sat_sb.load_semantics if sat_sb else None) or sat.load_semantics
    if sem and sem.filter_ast is not None:
        where_parts.append(
            predicate_to_sqlglot(sem.filter_ast, table_alias="src").sql(dialect=dialect)
        )
    if sem and sem.require_review_status == "APPROVED":
        where_parts.append("src.steward_gate_status = 'APPROVED'")
    dedup_cols = (
        [sat_col_maps.get(c, c) for c in sem.dedup_order_cols]
        if (sem and sem.dedup_order_cols)
        else []
    )
    if dedup_cols:
        dedup_order = ", ".join([*(f"src.{c} DESC" for c in dedup_cols), "src.load_dts ASC"])
    else:
        base = (
            [f"src.{applied_col} ASC", "src.load_dts ASC"] if applied_col else ["src.load_dts ASC"]
        )
        tie = [f"src.{hd_col} DESC" if hd_col else f"src.{parent_hk} DESC", "src.rec_src ASC"]
        dedup_order = ", ".join([*base, *tie])
    return " AND ".join(where_parts), dedup_order


def _sat_staging_select_and_pk(
    sat: CertifiedSatelliteSpec,
    stg: StagingViewSpec,
    hd_col_map: Mapping[tuple[str, str], str],
    dialect: DbtSqlDialect,
) -> tuple[str, list[str], str, list[str], str | None, str]:
    parent_hk = sat.columns[0].name if sat.columns else "parent_hk"
    has_hd = sat.sat_kind != "ESAT" and sat.hash_recipe is not None
    stg_hd_col = hd_col_map.get((stg.view_name, sat.sat_name), "hash_diff")
    applied_col = sat.applied_dts_col if (sat.is_bitemporal and sat.applied_dts_col) else None

    is_mas = sat.sat_kind == "MAS" or bool(sat.child_dependent_keys)
    part_cols = [parent_hk]
    if is_mas and sat.child_dependent_keys:
        part_cols.extend(sat.child_dependent_keys)

    pk_cols = [parent_hk, "load_dts"]
    if applied_col:
        pk_cols.append(applied_col)
    if is_mas and sat.child_dependent_keys:
        pk_cols.extend(sat.child_dependent_keys)

    sat_sb = next(
        (b for b in sat.source_bindings if b.table_id == stg.source_table_id),
        sat.source_bindings[0] if sat.source_bindings else None,
    )
    col_maps = sat_sb.col_mappings if sat_sb else {}
    stg_proj_names = (
        {f.name for f in stg.projected_fields}
        | set(stg.col_lineage_mappings.keys())
        | {r.target_col for r in stg.hash_recipes}
    )
    select_parts: list[str] = []
    for c in sat.columns:
        if c.name == "hash_diff" and has_hd:
            select_parts.append(f"src.{stg_hd_col} AS hash_diff")
        elif c.name in stg_proj_names or c.name in (parent_hk, "load_dts", "rec_src", applied_col):
            select_parts.append(f"src.{c.name}")
        else:
            mapped = col_maps.get(c.name, c.name)
            part = f"src.{mapped} AS {c.name}" if mapped != c.name else f"src.{c.name}"
            select_parts.append(part)

    where_sql, dedup_order = _sat_where_and_dedup(
        sat, sat_sb, col_maps, parent_hk, applied_col, dialect,
        hd_col=stg_hd_col if has_hd else None,
    )
    return ", ".join(select_parts), part_cols, where_sql, pk_cols, applied_col, dedup_order


def _build_esat_dml(
    sat: CertifiedSatelliteSpec,
    stg: StagingViewSpec,
    ir: CertifiedVaultIR,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> str:
    dialect = profile.sqlglot_dialect or "duckdb"
    sat_full = qualify_name(sat.zone_id, sat.sat_name, aliases)
    stg_full = qualify_name("Z1", stg.view_name, aliases)
    parent_hk = sat.columns[0].name if sat.columns else "parent_hk"
    applied_col = sat.applied_dts_col if (sat.is_bitemporal and sat.applied_dts_col) else None
    zero_00 = zero_key_hex_sql(dialect, is_error=False)
    zero_ff = zero_key_hex_sql(dialect, is_error=True)

    sat_col_names = [c.name for c in sat.columns]
    dks = list(sat.driving_keys) or [parent_hk]
    missing_dks = [dk for dk in dks if dk not in sat_col_names]

    sat_sb = next(
        (b for b in sat.source_bindings if b.table_id == stg.source_table_id),
        sat.source_bindings[0] if sat.source_bindings else None,
    )
    sat_col_maps = sat_sb.col_mappings if sat_sb else {}
    parent_lnk: CertifiedLinkSpec | None = next(
        (lk for lk in ir.links if lk.link_name == sat.parent_name), None
    )
    lnk_sb = (
        next(
            (b for b in parent_lnk.source_bindings if b.table_id == stg.source_table_id),
            parent_lnk.source_bindings[0] if parent_lnk.source_bindings else None,
        )
        if parent_lnk is not None
        else None
    )
    lnk_col_maps = lnk_sb.col_mappings if lnk_sb else {}
    stg_proj_names = (
        {f.name for f in stg.projected_fields}
        | set(stg.col_lineage_mappings.keys())
        | {r.target_col for r in stg.hash_recipes}
    )

    existing_join = ""
    if missing_dks and parent_lnk is not None:
        lnk_full = qualify_name(parent_lnk.zone_id, parent_lnk.link_name, aliases)
        lnk_hk = parent_lnk.columns[0].name
        existing_join = f" INNER JOIN {lnk_full} AS plnk ON plnk.{lnk_hk} = t.{parent_hk}"
        tgt_dk_cols = [f"plnk.{dk}" for dk in missing_dks]
    elif missing_dks:
        tgt_dk_cols = [f"NULL AS {dk}" for dk in missing_dks]
    else:
        tgt_dk_cols = []

    src_cols_list: list[str] = []
    for c in sat_col_names:
        if c in stg_proj_names or c in (parent_hk, "load_dts", "rec_src", applied_col):
            src_cols_list.append(f"src.{c}")
        else:
            mc = sat_col_maps.get(c, c)
            src_cols_list.append(f"src.{mc} AS {c}" if mc != c else f"src.{c}")

    dk_src_map: dict[str, str] = {}
    for dk in dks:
        if dk in stg_proj_names or dk in (parent_hk, "load_dts", "rec_src", applied_col):
            dk_src_map[dk] = dk
        else:
            dk_src_map[dk] = sat_col_maps.get(dk) or lnk_col_maps.get(dk) or dk
    for dk in missing_dks:
        mdk = dk_src_map[dk]
        src_cols_list.append(f"src.{mdk} AS {dk}" if mdk != dk else f"src.{dk}")

    dk_sel_src = [f"src.{dk_src_map[dk]}" for dk in dks]
    tgt_cols = ", ".join([f"t.{c}" for c in sat_col_names] + tgt_dk_cols)
    src_cols = ", ".join(src_cols_list)
    part_by = ", ".join(dks)
    all_cols_csv = ", ".join(sat_col_names)

    where_sql, dedup_order = _sat_where_and_dedup(
        sat, sat_sb, sat_col_maps, parent_hk, applied_col, dialect
    )
    pk_cols = [parent_hk, "load_dts", *([applied_col] if applied_col else [])]
    part_dedup = [*dk_sel_src, "src.load_dts", *([f"src.{applied_col}"] if applied_col else [])]

    dedup_src = (
        f"SELECT {src_cols}, ROW_NUMBER() OVER (PARTITION BY {', '.join(part_dedup)} "
        f"ORDER BY {dedup_order}) AS _rn\n"
        f"    FROM {stg_full} AS src\n"
        f"    WHERE {where_sql}"
    )
    new_cols = ", ".join(f"d.{c}" for c in sat_col_names + missing_dks)
    pk_not_exists = " AND ".join(f"ex.{k} = d.{k}" for k in pk_cols)
    combined = (
        f"SELECT {tgt_cols}, 0 AS _is_new FROM {sat_full} AS t{existing_join}\n"
        f"    WHERE t.{parent_hk} <> {zero_00} AND t.{parent_hk} <> {zero_ff}\n"
        f"    UNION ALL\n"
        f"    SELECT {new_cols}, 1 AS _is_new FROM (\n        {dedup_src}\n    ) AS d\n"
        f"    WHERE d._rn = 1 AND NOT EXISTS (\n"
        f"        SELECT 1 FROM {sat_full} AS ex WHERE {pk_not_exists}\n"
        f"    )"
    )
    win_order = (
        f"{applied_col} ASC, load_dts ASC, _is_new ASC"
        if applied_col
        else "load_dts ASC, _is_new ASC"
    )
    windowed = (
        f"SELECT {', '.join(sat_col_names)}, _is_new,\n"
        f"           LAG({parent_hk}) OVER (PARTITION BY {part_by} "
        f"ORDER BY {win_order}) AS _prev_hk\n"
        f"    FROM (\n        {combined}\n    ) AS cmb"
    )
    delta_q = (
        f"SELECT {all_cols_csv} FROM (\n    {windowed}\n) AS win\n"
        f"WHERE _is_new = 1 AND (_prev_hk IS NULL OR _prev_hk <> {parent_hk})"
    )
    return _sat_upsert_sql(sat_full, sat_col_names, pk_cols, delta_q, dialect)


def build_sat_load_dml(
    sat: CertifiedSatelliteSpec,
    stg: StagingViewSpec,
    ir: CertifiedVaultIR,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
    hd_col_map: Mapping[tuple[str, str], str],
) -> str:
    """Emit idempotent true-delta Satellite load DML across all 8 SQL dialects."""
    if sat.sat_kind == "ESAT":
        return _build_esat_dml(sat, stg, ir, profile, aliases)

    dialect = profile.sqlglot_dialect or "duckdb"
    sat_full = qualify_name(sat.zone_id, sat.sat_name, aliases)
    stg_full = qualify_name("Z1", stg.view_name, aliases)
    parent_hk = sat.columns[0].name if sat.columns else "parent_hk"
    zero_00 = zero_key_hex_sql(dialect, is_error=False)

    sel_list, part_cols, where_sql, pk_cols, applied_col, dedup_order = _sat_staging_select_and_pk(
        sat, stg, hd_col_map, dialect
    )
    all_cols = [c.name for c in sat.columns]
    cols_csv = ", ".join(all_cols)
    part_csv = ", ".join(part_cols)
    pk_join = " AND ".join(f"ex.{k} = d.{k}" for k in pk_cols)

    if dialect == "clickhouse" and not applied_col:
        part_d = ", ".join(f"d.{p}" for p in part_cols)
        part_src = ", ".join(f"src.{k}" for k in pk_cols)
        cur_on = " AND ".join(f"s.{p} = cur.{p}" for p in part_cols)
        anti_on = " AND ".join(f"s.{k} = ex.{k}" for k in pk_cols)
        sql = (
            f"INSERT INTO {sat_full} ({cols_csv})\n"
            f"SELECT {', '.join(f's.{c}' for c in all_cols)}\n"
            f"FROM (\n"
            f"    SELECT {', '.join(f'd.{c}' for c in all_cols)},\n"
            f"           LAG(d.hash_diff) OVER (PARTITION BY {part_d} "
            f"ORDER BY d.load_dts ASC) AS _prev_hd\n"
            f"    FROM (\n"
            f"        SELECT {sel_list}, ROW_NUMBER() OVER (PARTITION BY {part_src} "
            f"ORDER BY {dedup_order}) AS _rn\n"
            f"        FROM {stg_full} AS src WHERE {where_sql}\n"
            f"    ) AS d WHERE d._rn = 1\n"
            f") AS s\n"
            f"LEFT JOIN (\n"
            f"    SELECT {part_csv}, argMax(hash_diff, load_dts) AS latest_hd\n"
            f"    FROM {sat_full} WHERE {parent_hk} <> {zero_00} GROUP BY {part_csv}\n"
            f") AS cur ON {cur_on}\n"
            f"LEFT ANTI JOIN {sat_full} AS ex ON {anti_on}\n"
            f"WHERE (s._prev_hd IS NULL OR s._prev_hd <> s.hash_diff)\n"
            f"  AND (cur.{parent_hk} IS NULL OR s.hash_diff <> cur.latest_hd)\n"
            f"SETTINGS join_use_nulls = 1"
        )
        return assert_clean_dml(sql, dialect)

    order_expr = f"{applied_col} ASC, load_dts ASC" if applied_col else "load_dts ASC"
    part_src = ", ".join(f"src.{k}" for k in pk_cols)
    dedup_src = (
        f"SELECT {sel_list}, ROW_NUMBER() OVER (PARTITION BY {part_src} "
        f"ORDER BY {dedup_order}) AS _rn\n"
        f"    FROM {stg_full} AS src WHERE {where_sql}"
    )

    if applied_col:
        t_cols = ", ".join(f"t.{c}" for c in all_cols)
        d_cols = ", ".join(f"d.{c}" for c in all_cols)
        combined = (
            f"SELECT {t_cols}, 0 AS _is_new FROM {sat_full} AS t WHERE t.{parent_hk} <> {zero_00}\n"
            f"    UNION ALL\n"
            f"    SELECT {d_cols}, 1 AS _is_new FROM (\n        {dedup_src}\n    ) AS d\n"
            f"    WHERE d._rn = 1 AND NOT EXISTS (SELECT 1 FROM {sat_full} AS ex WHERE {pk_join})"
        )
        delta_q = (
            f"SELECT {cols_csv} FROM (\n"
            f"    SELECT {cols_csv}, _is_new,\n"
            f"           LAG(hash_diff) OVER (PARTITION BY {part_csv} "
            f"ORDER BY {order_expr}, _is_new ASC) AS _prev_hd\n"
            f"    FROM (\n        {combined}\n    ) AS cmb\n"
            f") AS win WHERE _is_new = 1 AND (_prev_hd IS NULL OR _prev_hd <> hash_diff)"
        )
    else:
        batch_delta = (
            f"SELECT {cols_csv},\n"
            f"       LAG(hash_diff) OVER (PARTITION BY {part_csv} "
            f"ORDER BY load_dts ASC) AS _prev_hd\n"
            f"FROM (\n    {dedup_src}\n) AS dd WHERE _rn = 1"
        )
        latest_tgt = (
            f"SELECT {part_csv}, hash_diff AS latest_hd, "
            f"ROW_NUMBER() OVER (PARTITION BY {part_csv} ORDER BY load_dts DESC) AS _lrn\n"
            f"FROM {sat_full} WHERE {parent_hk} <> {zero_00}"
        )
        cur_on = " AND ".join(f"b.{p} = cur.{p}" for p in part_cols)
        ex_on = " AND ".join(f"ex.{k} = b.{k}" for k in pk_cols)
        delta_q = (
            f"SELECT {', '.join(f'b.{c}' for c in all_cols)} FROM (\n    {batch_delta}\n) AS b\n"
            f"LEFT JOIN (\n    {latest_tgt}\n) AS cur ON {cur_on} AND cur._lrn = 1\n"
            f"WHERE (b._prev_hd IS NULL OR b._prev_hd <> b.hash_diff)\n"
            f"  AND (cur.{parent_hk} IS NULL OR b.hash_diff <> cur.latest_hd)\n"
            f"  AND NOT EXISTS (SELECT 1 FROM {sat_full} AS ex WHERE {ex_on})"
        )

    return _sat_upsert_sql(sat_full, all_cols, pk_cols, delta_q, dialect)


__all__ = ["build_sat_load_dml"]
