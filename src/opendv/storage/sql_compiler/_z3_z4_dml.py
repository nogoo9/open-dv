"""Zone Z3 PIT and Multi-Hop Bridge SELECT & DML builders.

Strictly Layer 2a: imports only opendv.models, dialects, _ddl_common, _dml_common, dialect_hashing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from opendv.models.vault_ir_certified import (
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    PitBridgeSpec,
    SeedRecordsSpec,
)
from opendv.storage.dialects import SqlDialectProfile, lower_logical_type
from opendv.storage.sql_compiler._ddl_common import (
    qualify_name,
    resolve_bridge_hop_columns,
    resolve_pit_bridge_columns,
)
from opendv.storage.sql_compiler._dml_common import (
    assert_clean_dml,
    ghost_col_default_sql,
    ts_literal_sql,
)
from opendv.storage.sql_compiler._scalar_exprs import predicate_to_sqlglot
from opendv.storage.sql_compiler.dialect_hashing import zero_key_hex_sql


def build_pit_bridge_select_sql(
    pb: PitBridgeSpec,
    ir: CertifiedVaultIR,
    seed: SeedRecordsSpec,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> tuple[list[str], str] | None:
    """Build column list and SELECT query for Z3 PIT or multi-hop Bridge tables."""
    dialect = profile.sqlglot_dialect or "duckdb"
    hub_full = qualify_name("Z2", pb.root_hub, aliases)
    zero_00 = zero_key_hex_sql(dialect, is_error=False)
    zero_ff = zero_key_hex_sql(dialect, is_error=True)
    ghost_ts = ts_literal_sql(seed.ghost_load_dts, profile.target_id, dialect)
    t_ts = lower_logical_type("TIMESTAMP_UTC_US", profile.target_id)

    if pb.object_kind == "PIT":
        src_root_hk, root_hk, targets = resolve_pit_bridge_columns(
            pb, ir, profile.max_ident_bytes
        )
        sats_by_name = {s.sat_name: s for s in ir.satellites}
        if not any(tgt in sats_by_name for tgt, *_ in targets):
            return None
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
        return cols, select_sql

    src_root_hk, root_hk, hop_specs = resolve_bridge_hop_columns(
        pb, ir, profile.max_ident_bytes
    )
    cols = [root_hk]
    sel_exprs = [f"h.{src_root_hk} AS {root_hk}"]
    joins = []
    links_by_name = {lk.link_name: lk for lk in ir.links}
    active_hops: set[int] = set()

    for idx, hop in enumerate(hop_specs):
        cols.append(hop.brg_link_hk_col)
        if hop.brg_to_hub_hk_col is not None:
            cols.append(hop.brg_to_hub_hk_col)
        lnk = links_by_name.get(hop.link_name)
        if lnk is None or (hop.join_parent_idx >= 0 and hop.join_parent_idx not in active_hops):
            sel_exprs.append(f"{zero_00} AS {hop.brg_link_hk_col}")
            if hop.brg_to_hub_hk_col is not None:
                sel_exprs.append(f"{zero_00} AS {hop.brg_to_hub_hk_col}")
            continue
        active_hops.add(idx)
        l_full = qualify_name(lnk.zone_id, lnk.link_name, aliases)
        la = f"l{idx}"
        parent_alias = "h" if hop.join_parent_idx < 0 else f"l{hop.join_parent_idx}"
        sel_exprs.append(
            f"COALESCE({la}.{hop.src_link_hk_col}, {zero_00}) AS {hop.brg_link_hk_col}"
        )
        if hop.brg_to_hub_hk_col is not None and hop.to_fk_col is not None:
            sel_exprs.append(
                f"COALESCE({la}.{hop.to_fk_col}, {zero_00}) AS {hop.brg_to_hub_hk_col}"
            )
        joins.append(
            f"LEFT JOIN {l_full} AS {la} "
            f"ON {la}.{hop.from_fk_col} = {parent_alias}.{hop.join_parent_col} "
            f"AND {la}.{hop.src_link_hk_col} <> {zero_00} "
            f"AND {la}.{hop.src_link_hk_col} <> {zero_ff}"
        )

    cols.append("load_dts")
    sel_exprs.append("h.load_dts AS load_dts")
    select_sql = (
        f"SELECT {', '.join(sel_exprs)}\nFROM {hub_full} AS h\n"
        + ("\n".join(joins) + "\n" if joins else "")
        + f"WHERE h.{src_root_hk} <> {zero_00} AND h.{src_root_hk} <> {zero_ff}"
    )
    return cols, select_sql


def build_pit_bridge_dml(
    pb: PitBridgeSpec,
    ir: CertifiedVaultIR,
    seed: SeedRecordsSpec,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> list[str]:
    """Emit steady-state TRUNCATE TABLE + INSERT batch refresh DML for Z3 PIT/Bridge."""
    built = build_pit_bridge_select_sql(pb, ir, seed, profile, aliases)
    if built is None:
        return []
    cols, select_sql = built
    dialect = profile.sqlglot_dialect or "duckdb"
    pb_full = qualify_name("Z3", pb.object_name, aliases)
    settings = "\nSETTINGS join_use_nulls = 1" if dialect == "clickhouse" else ""
    trunc_stmt = assert_clean_dml(f"TRUNCATE TABLE {pb_full}", dialect)
    ins_stmt = assert_clean_dml(
        f"INSERT INTO {pb_full} ({', '.join(cols)})\n{select_sql}{settings}",
        dialect,
    )
    return [trunc_stmt, ins_stmt]


def rewrite_dbt_vault_refs(
    sql: str,
    ir: CertifiedVaultIR,
    aliases: Mapping[str, str],
) -> str:
    """Rewrite qualified Z2/Z3 vault table references into dbt `{{ ref(...) }}` calls."""
    ref_map: dict[str, str] = {}
    for h in ir.hubs:
        if not h.is_planned_concept:
            ref_map[qualify_name(h.zone_id, h.hub_name, aliases)] = h.hub_name
    for lk in ir.links:
        ref_map[qualify_name(lk.zone_id, lk.link_name, aliases)] = lk.link_name
    for s in ir.satellites:
        ref_map[qualify_name(s.zone_id, s.sat_name, aliases)] = s.sat_name
    out = sql
    for q_name, logical_name in sorted(ref_map.items(), key=lambda kv: len(kv[0]), reverse=True):
        out = re.sub(rf"\b{re.escape(q_name)}\b", f"{{{{ ref('{logical_name}') }}}}", out)
    return out


def build_dbt_esat_sql(
    sat: CertifiedSatelliteSpec,
    src_model: str,
    ir: CertifiedVaultIR,
    profile: SqlDialectProfile,
    *,
    is_z3_view: bool,
) -> str:
    """Build native incremental Z2 esat_* or Z3 esat_sal_* view SQL for dbt."""
    dialect = profile.sqlglot_dialect or "duckdb"
    parent_hk = sat.columns[0].name if sat.columns else "parent_hk"
    applied_col = sat.applied_dts_col if (sat.is_bitemporal and sat.applied_dts_col) else None
    zero_00 = zero_key_hex_sql(dialect, is_error=False)
    zero_ff = zero_key_hex_sql(dialect, is_error=True)

    sat_col_names = [c.name for c in sat.columns]
    dks = list(sat.driving_keys) or [parent_hk]
    missing_dks = [dk for dk in dks if dk not in sat_col_names]

    stg_obj = next((s for s in ir.staging_views if s.source_table_id == sat.source_table_id), None)
    sat_sb = next(
        (b for b in sat.source_bindings if b.table_id == sat.source_table_id),
        sat.source_bindings[0] if sat.source_bindings else None,
    )
    sat_col_maps = sat_sb.col_mappings if sat_sb else {}
    parent_lnk = next((lk for lk in ir.links if lk.link_name == sat.parent_name), None)
    lnk_sb = (
        next(
            (b for b in parent_lnk.source_bindings if b.table_id == sat.source_table_id),
            parent_lnk.source_bindings[0] if parent_lnk.source_bindings else None,
        )
        if parent_lnk is not None
        else None
    )
    lnk_col_maps = lnk_sb.col_mappings if lnk_sb else {}
    stg_proj = (
        {f.name for f in stg_obj.projected_fields}
        | set(stg_obj.col_lineage_mappings.keys())
        | {r.target_col for r in stg_obj.hash_recipes}
    ) if stg_obj is not None else set()

    src_cols_list: list[str] = []
    for c in sat_col_names:
        if c in stg_proj or c in (parent_hk, "load_dts", "rec_src", applied_col):
            src_cols_list.append(f"src.{c}")
        else:
            mc = sat_col_maps.get(c, c)
            src_cols_list.append(f"src.{mc} AS {c}" if mc != c else f"src.{c}")

    dk_src_map: dict[str, str] = {}
    for dk in dks:
        if dk in stg_proj or dk in (parent_hk, "load_dts", "rec_src", applied_col):
            dk_src_map[dk] = dk
        else:
            dk_src_map[dk] = sat_col_maps.get(dk) or lnk_col_maps.get(dk) or dk
    for dk in missing_dks:
        mdk = dk_src_map[dk]
        src_cols_list.append(f"src.{mdk} AS {dk}" if mdk != dk else f"src.{dk}")

    where_parts = [f"src.{parent_hk} <> {zero_00}", f"src.{parent_hk} <> {zero_ff}"]
    sem = (sat_sb.load_semantics if sat_sb else None) or sat.load_semantics
    if sem and sem.filter_ast is not None:
        pred_sql = predicate_to_sqlglot(sem.filter_ast, table_alias="src").sql(dialect=dialect)
        where_parts.append(pred_sql)
    if sem and sem.require_review_status == "APPROVED":
        where_parts.append("src.steward_gate_status = 'APPROVED'")
    where_sql = " AND ".join(where_parts)

    if sem and sem.dedup_order_cols:
        d_cols = [sat_col_maps.get(c, c) for c in sem.dedup_order_cols]
        dedup_order = ", ".join([*(f"src.{c} DESC" for c in d_cols), "src.load_dts ASC"])
    else:
        base_o = (
            [f"src.{applied_col} ASC", "src.load_dts ASC"] if applied_col else ["src.load_dts ASC"]
        )
        dedup_order = ", ".join([*base_o, f"src.{parent_hk} DESC", "src.rec_src ASC"])

    part_dedup = [
        *(f"src.{dk_src_map[dk]}" for dk in dks),
        "src.load_dts",
        *([f"src.{applied_col}"] if applied_col else []),
    ]
    all_cols_csv = ", ".join(sat_col_names)
    part_by = ", ".join(dks)
    win_base = f"{applied_col} ASC, load_dts ASC" if applied_col else "load_dts ASC"
    rn_expr = (
        f"ROW_NUMBER() OVER (PARTITION BY {', '.join(part_dedup)} "
        f"ORDER BY {dedup_order}) AS _rn"
    )

    if is_z3_view:
        lag_expr = f"LAG({parent_hk}) OVER (PARTITION BY {part_by} ORDER BY {win_base}) AS _prev_hk"
        return (
            "{{ config(materialized='view') }}\n\n"
            f"WITH stg AS (\n"
            f"    SELECT {', '.join(src_cols_list)},\n"
            f"           {rn_expr}\n"
            f"    FROM {{{{ ref('{src_model}') }}}} AS src\n"
            f"    WHERE {where_sql}\n"
            f"),\n"
            f"win AS (\n"
            f"    SELECT {all_cols_csv},\n"
            f"           {lag_expr}\n"
            f"    FROM stg\n"
            f"    WHERE _rn = 1\n"
            f")\n"
            f"SELECT {all_cols_csv}\n"
            f"FROM win\n"
            f"WHERE _prev_hk IS NULL OR _prev_hk <> {parent_hk}\n"
        )

    existing_join = ""
    if missing_dks and parent_lnk is not None:
        lnk_hk = parent_lnk.columns[0].name
        existing_join = (
            f" INNER JOIN {{{{ ref('{parent_lnk.link_name}') }}}} AS plnk "
            f"ON plnk.{lnk_hk} = t.{parent_hk}"
        )
        tgt_dk_cols = [f"plnk.{dk}" for dk in missing_dks]
    elif missing_dks:
        tgt_dk_cols = [f"NULL AS {dk}" for dk in missing_dks]
    else:
        tgt_dk_cols = []

    tgt_cols = ", ".join([f"t.{c}" for c in sat_col_names] + tgt_dk_cols)
    new_cols = ", ".join(f"d.{c}" for c in sat_col_names + missing_dks)
    cmb_cols = ", ".join(sat_col_names + missing_dks)
    pk_cols = [parent_hk, "load_dts", *([applied_col] if applied_col else [])]
    pk_not_exists = " AND ".join(f"ex.{k} = d.{k}" for k in pk_cols)
    lag_inc = (
        f"LAG({parent_hk}) OVER (PARTITION BY {part_by} "
        f"ORDER BY {win_base}, _is_new ASC) AS _prev_hk"
    )

    return (
        "{{ config(materialized='incremental') }}\n\n"
        f"WITH stg AS (\n"
        f"    SELECT {', '.join(src_cols_list)},\n"
        f"           {rn_expr}\n"
        f"    FROM {{{{ ref('{src_model}') }}}} AS src\n"
        f"    WHERE {where_sql}\n"
        f"),\n"
        "{% if is_incremental() %}\n"
        f"existing AS (\n"
        f"    SELECT {tgt_cols}, 0 AS _is_new\n"
        f"    FROM {{{{ this }}}} AS t{existing_join}\n"
        f"    WHERE t.{parent_hk} <> {zero_00} AND t.{parent_hk} <> {zero_ff}\n"
        f"),\n"
        f"incoming AS (\n"
        f"    SELECT {new_cols}, 1 AS _is_new\n"
        f"    FROM stg AS d\n"
        f"    WHERE d._rn = 1 AND NOT EXISTS (\n"
        f"        SELECT 1 FROM {{{{ this }}}} AS ex WHERE {pk_not_exists}\n"
        f"    )\n"
        f"),\n"
        f"cmb AS (\n"
        f"    SELECT {cmb_cols}, _is_new FROM existing\n"
        f"    UNION ALL\n"
        f"    SELECT {cmb_cols}, _is_new FROM incoming\n"
        f"),\n"
        "{% else %}\n"
        f"cmb AS (\n"
        f"    SELECT {new_cols}, 1 AS _is_new\n"
        f"    FROM stg AS d\n"
        f"    WHERE d._rn = 1\n"
        f"),\n"
        "{% endif %}\n"
        f"win AS (\n"
        f"    SELECT {all_cols_csv}, _is_new,\n"
        f"           {lag_inc}\n"
        f"    FROM cmb\n"
        f")\n"
        f"SELECT {all_cols_csv}\n"
        f"FROM win\n"
        f"WHERE _is_new = 1 AND (_prev_hk IS NULL OR _prev_hk <> {parent_hk})\n"
    )


__all__ = [
    "build_dbt_esat_sql",
    "build_pit_bridge_dml",
    "build_pit_bridge_select_sql",
    "rewrite_dbt_vault_refs",
]
