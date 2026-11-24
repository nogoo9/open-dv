"""Zone Z0 and Z2/Z3 Hub, Link, and Seed DML builders + full DML orchestration.

Strictly Layer 2a: imports only opendv.models, dialects, _ddl_common, _dml_common, _sat_dml.
"""

from __future__ import annotations

from collections.abc import Mapping

from opendv.models.vault_ir import DecimalType
from opendv.models.vault_ir_certified import (
    CertifiedHubSpec,
    CertifiedLinkSpec,
    CertifiedVaultIR,
    RefTableSpec,
    SeedRecordsSpec,
    StagingViewSpec,
)
from opendv.storage.dialects import SqlDialectProfile, lower_logical_type
from opendv.storage.sql_compiler._ddl_common import planned_exclusion_sets, qualify_name
from opendv.storage.sql_compiler._dml_common import (
    build_pit_bridge_dml,
    build_sat_ghost_dml,
    ghost_col_default_sql,
    idempotent_insert_from_select,
    ts_literal_sql,
)
from opendv.storage.sql_compiler._sat_dml import build_sat_load_dml
from opendv.storage.sql_compiler._scalar_exprs import predicate_to_sqlglot
from opendv.storage.sql_compiler.dialect_hashing import zero_key_hex_sql


def build_ref_seed_dml(
    ref: RefTableSpec,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> list[str]:
    """Emit idempotent reference code inserts into Z0 ref_*."""
    if not ref.codes:
        return []
    ref_full = qualify_name("Z0", ref.ref_name, aliases)
    rows_sql = "\nUNION ALL\n".join(
        f"SELECT '{code.replace(chr(39), chr(39) * 2)}' AS {ref.code_col}, "
        f"'{label.replace(chr(39), chr(39) * 2)}' AS {ref.label_col}"
        for code, label in sorted(ref.codes.items())
    )
    return [
        idempotent_insert_from_select(
            ref_full, [ref.code_col, ref.label_col], [ref.code_col], rows_sql, profile
        )
    ]


def build_hub_seed_dml(
    hub: CertifiedHubSpec,
    seed: SeedRecordsSpec,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> list[str]:
    """Emit -1 (0x00..00) and -2 (0xff..ff) Zero-Key seed records for a Hub."""
    dialect = profile.sqlglot_dialect or "duckdb"
    hub_full = qualify_name(hub.zone_id, hub.hub_name, aliases)
    hk_col = hub.columns[0].name
    cols = [c.name for c in hub.columns]
    ghost_ts = ts_literal_sql(seed.ghost_load_dts, profile.target_id, dialect)

    stmts: list[str] = []
    for is_err, label, num_val in (
        (False, seed.default_string, seed.default_num),
        (True, "ERROR", -2),
    ):
        z_hk = zero_key_hex_sql(dialect, is_error=is_err)
        vals: list[str] = []
        for c in hub.columns:
            if c.name == hk_col:
                vals.append(f"{z_hk} AS {c.name}")
            elif c.name == "bkcc":
                vals.append(f"'GLOBAL' AS {c.name}")
            elif c.name == "load_dts":
                vals.append(f"{ghost_ts} AS {c.name}")
            elif c.name == "rec_src":
                vals.append(f"'{seed.ghost_rec_src}' AS {c.name}")
            elif c.logical_type in ("INT32", "INT64") or isinstance(c.logical_type, DecimalType):
                dt = lower_logical_type(c.logical_type, profile.target_id)
                vals.append(f"CAST({num_val} AS {dt}) AS {c.name}")
            elif c.logical_type == "UTF8_STRING":
                vals.append(f"'{label}' AS {c.name}")
            else:
                g = ghost_col_default_sql(
                    c.name, c.logical_type, c.nullable, seed, profile.target_id, dialect
                )
                vals.append(f"{g} AS {c.name}")
        stmts.append(
            idempotent_insert_from_select(
                hub_full, cols, [hk_col], f"SELECT {', '.join(vals)}", profile
            )
        )
    return stmts


def build_link_seed_dml(
    lnk: CertifiedLinkSpec,
    seed: SeedRecordsSpec,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> list[str]:
    """Emit -1 (0x00..00) and -2 (0xff..ff) Zero-Key seed records for a Link."""
    dialect = profile.sqlglot_dialect or "duckdb"
    lnk_full = qualify_name(lnk.zone_id, lnk.link_name, aliases)
    hk_col = lnk.columns[0].name
    fk_cols = {p.fk_col_name for p in lnk.participants}
    cols = [c.name for c in lnk.columns]
    ghost_ts = ts_literal_sql(seed.ghost_load_dts, profile.target_id, dialect)

    stmts: list[str] = []
    for is_err, label in ((False, seed.default_string), (True, "ERROR")):
        z_hk = zero_key_hex_sql(dialect, is_error=is_err)
        vals: list[str] = []
        for c in lnk.columns:
            if c.name == hk_col or c.name in fk_cols:
                vals.append(f"{z_hk} AS {c.name}")
            elif c.name == "load_dts":
                vals.append(f"{ghost_ts} AS {c.name}")
            elif c.name == "rec_src":
                vals.append(f"'{seed.ghost_rec_src}' AS {c.name}")
            elif c.logical_type == "UTF8_STRING":
                vals.append(f"'{label}' AS {c.name}")
            else:
                g = ghost_col_default_sql(
                    c.name, c.logical_type, c.nullable, seed, profile.target_id, dialect
                )
                vals.append(f"{g} AS {c.name}")
        stmts.append(
            idempotent_insert_from_select(
                lnk_full, cols, [hk_col], f"SELECT {', '.join(vals)}", profile
            )
        )
    return stmts


def build_hub_load_dml(
    hub: CertifiedHubSpec,
    ir: CertifiedVaultIR,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> list[str]:
    """Emit idempotent Hub load DML from all bound Z1 staging views."""
    dialect = profile.sqlglot_dialect or "duckdb"
    hub_full = qualify_name(hub.zone_id, hub.hub_name, aliases)
    hk_col = hub.columns[0].name
    cols = [c.name for c in hub.columns]
    stg_by_tbl = {s.source_table_id: s for s in ir.staging_views}
    zero_00 = zero_key_hex_sql(dialect, is_error=False)
    zero_ff = zero_key_hex_sql(dialect, is_error=True)

    stmts: list[str] = []
    for sb in hub.source_bindings:
        stg = stg_by_tbl.get(sb.table_id)
        if stg is None:
            continue
        stg_full = qualify_name("Z1", stg.view_name, aliases)
        recipe = sb.hash_recipe or hub.hash_recipe
        bkcc_lit = recipe.bkcc_literal if recipe and recipe.bkcc_literal else "GLOBAL"
        bkcc_col = recipe.bkcc_col if recipe else None
        bkcc_expr = (
            f"COALESCE(NULLIF(UPPER(TRIM(src.{bkcc_col})), ''), '{bkcc_lit}')"
            if bkcc_col
            else f"'{bkcc_lit}'"
        )

        grp_cols: list[str] = [f"src.{hk_col}", bkcc_expr]
        sel_cols: list[str] = []
        for c in cols:
            if c == hk_col:
                sel_cols.append(f"src.{hk_col} AS {hk_col}")
            elif c == "bkcc":
                sel_cols.append(f"{bkcc_expr} AS bkcc")
            elif c == "load_dts":
                sel_cols.append("MIN(src.load_dts) AS load_dts")
            elif c == "rec_src":
                sel_cols.append("MIN(src.rec_src) AS rec_src")
            else:
                src_col = sb.col_mappings.get(c, c)
                sel_cols.append(f"src.{src_col} AS {c}")
                grp_cols.append(f"src.{src_col}")

        where_parts = [f"src.{hk_col} <> {zero_00}", f"src.{hk_col} <> {zero_ff}"]
        flt = (sb.load_semantics.filter_ast if sb.load_semantics else None) or (
            hub.load_semantics.filter_ast if hub.load_semantics else None
        )
        if flt is not None:
            where_parts.append(predicate_to_sqlglot(flt, table_alias="src").sql(dialect=dialect))

        select_sql = (
            f"SELECT {', '.join(sel_cols)}\n"
            f"FROM {stg_full} AS src\n"
            f"WHERE {' AND '.join(where_parts)}\n"
            f"GROUP BY {', '.join(grp_cols)}"
        )
        stmts.append(idempotent_insert_from_select(hub_full, cols, [hk_col], select_sql, profile))
    return stmts


def build_link_load_dml(
    lnk: CertifiedLinkSpec,
    ir: CertifiedVaultIR,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
) -> list[str]:
    """Emit idempotent Link load DML honoring filter_ast, APPROVED gate, and sal_* rules."""
    dialect = profile.sqlglot_dialect or "duckdb"
    lnk_full = qualify_name(lnk.zone_id, lnk.link_name, aliases)
    hk_col = lnk.columns[0].name
    cols = [c.name for c in lnk.columns]
    fk_set = {p.fk_col_name for p in lnk.participants}
    deg_set = set(lnk.degenerate_keys)
    stg_by_tbl = {s.source_table_id: s for s in ir.staging_views}
    zero_00 = zero_key_hex_sql(dialect, is_error=False)
    zero_ff = zero_key_hex_sql(dialect, is_error=True)

    stmts: list[str] = []
    for sb in lnk.source_bindings:
        stg = stg_by_tbl.get(sb.table_id)
        if stg is None:
            continue
        stg_full = qualify_name("Z1", stg.view_name, aliases)

        sel_cols: list[str] = []
        grp_cols: list[str] = []
        for c in cols:
            if c == "load_dts":
                sel_cols.append("MIN(src.load_dts) AS load_dts")
            elif c == "rec_src":
                sel_cols.append("MIN(src.rec_src) AS rec_src")
            elif c == hk_col or c in fk_set or c in deg_set:
                src_c = sb.col_mappings.get(c, c)
                sel_cols.append(f"src.{src_c} AS {c}")
                grp_cols.append(f"src.{src_c}")
            else:
                src_c = sb.col_mappings.get(c, c)
                sel_cols.append(f"MIN(src.{src_c}) AS {c}")

        where_parts = [f"src.{hk_col} <> {zero_00}", f"src.{hk_col} <> {zero_ff}"]
        sem = sb.load_semantics or lnk.load_semantics
        if sem is not None and sem.filter_ast is not None:
            where_parts.append(
                predicate_to_sqlglot(sem.filter_ast, table_alias="src").sql(dialect=dialect)
            )
        if (sem and sem.require_review_status == "APPROVED") or (
            lnk.link_kind == "UNSTRUCTURED_EXTRACTION_LINK"
        ):
            where_parts.append("src.steward_gate_status = 'APPROVED'")

        extra_anti = ""
        if lnk.link_kind == "SAME_AS" and len(lnk.participants) == 2:
            fk0 = lnk.participants[0].fk_col_name
            fk1 = lnk.participants[1].fk_col_name
            src_fk0 = sb.col_mappings.get(fk0, fk0)
            src_fk1 = sb.col_mappings.get(fk1, fk1)
            where_parts.append(f"src.{src_fk0} <> src.{src_fk1}")
            extra_anti = f"ex.{fk0} = src_q.{fk1} AND ex.{fk1} = src_q.{fk0}"
            if dialect not in ("duckdb", "redshift", "clickhouse"):
                where_parts.append(
                    f"NOT EXISTS (SELECT 1 FROM {lnk_full} AS ex "
                    f"WHERE ex.{fk0} = src.{src_fk1} AND ex.{fk1} = src.{src_fk0})"
                )

        select_sql = (
            f"SELECT {', '.join(sel_cols)}\n"
            f"FROM {stg_full} AS src\n"
            f"WHERE {' AND '.join(where_parts)}\n"
            f"GROUP BY {', '.join(grp_cols)}"
        )
        stmts.append(
            idempotent_insert_from_select(
                lnk_full, cols, [hk_col], select_sql, profile, extra_not_exists=extra_anti
            )
        )
    return stmts


def compile_full_dml(
    ir: CertifiedVaultIR,
    profile: SqlDialectProfile,
    aliases: Mapping[str, str],
    hd_col_map: Mapping[tuple[str, str], str],
) -> list[str]:
    """Compile ordered Z0/Z2/Z3 seed inserts and idempotent vault load DML."""
    planned_hubs, excluded_links, excluded_sats = planned_exclusion_sets(ir)
    stg_by_tbl: dict[str, StagingViewSpec] = {s.source_table_id: s for s in ir.staging_views}
    stmts: list[str] = []

    for r in ir.reference_tables:
        stmts.extend(build_ref_seed_dml(r, profile, aliases))
    for h in ir.hubs:
        if h.hub_name not in planned_hubs:
            stmts.extend(build_hub_seed_dml(h, ir.seed_records, profile, aliases))
    for lnk in ir.links:
        if lnk.link_name not in excluded_links:
            stmts.extend(build_link_seed_dml(lnk, ir.seed_records, profile, aliases))
    for sat in ir.satellites:
        if sat.sat_name not in excluded_sats:
            stmts.append(build_sat_ghost_dml(sat, ir.seed_records, profile, aliases))

    for h in ir.hubs:
        if h.hub_name not in planned_hubs:
            stmts.extend(build_hub_load_dml(h, ir, profile, aliases))
    for lnk in ir.links:
        if lnk.link_name not in excluded_links:
            stmts.extend(build_link_load_dml(lnk, ir, profile, aliases))
    for sat in ir.satellites:
        if sat.sat_name not in excluded_sats:
            stg = stg_by_tbl.get(sat.source_table_id)
            if stg is not None:
                stmts.append(build_sat_load_dml(sat, stg, ir, profile, aliases, hd_col_map))

    for pb in ir.pit_bridges:
        if pb.root_hub not in planned_hubs:
            stmts.extend(build_pit_bridge_dml(pb, ir, ir.seed_records, profile, aliases))
    return stmts


__all__ = [
    "build_hub_load_dml",
    "build_hub_seed_dml",
    "build_link_load_dml",
    "build_link_seed_dml",
    "build_pit_bridge_dml",
    "build_ref_seed_dml",
    "compile_full_dml",
]
