"""Zone Z0 and Z2/Z3 Hub, Link, and Seed DML builders + full DML orchestration.

Strictly Layer 2a: imports only opendv.models, dialects, _ddl_common, _dml_common, _sat_dml.
"""

from __future__ import annotations

from collections.abc import Mapping

from opendv.models.vault_ir import DecimalType, hub_canonical_bk_cols
from opendv.models.vault_ir_certified import (
    CertifiedHubSpec,
    CertifiedLinkSpec,
    CertifiedVaultIR,
    RefTableSpec,
    SeedRecordsSpec,
    StagingViewSpec,
)
from opendv.storage.dialects import SqlDialectProfile, lower_logical_type
from opendv.storage.sql_compiler._ddl_common import (
    is_pit_bridge_excluded,
    planned_exclusion_sets,
    qualify_name,
)
from opendv.storage.sql_compiler._dml_common import (
    build_sat_ghost_dml,
    ghost_col_default_sql,
    idempotent_insert_from_select,
    ts_literal_sql,
)
from opendv.storage.sql_compiler._sat_dml import build_sat_load_dml
from opendv.storage.sql_compiler._scalar_exprs import (
    TSQL_COLLATION,
    format_scalar_sql,
    predicate_to_sqlglot,
)
from opendv.storage.sql_compiler._z3_z4_dml import build_pit_bridge_dml
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
    canon_bks = set(hub_canonical_bk_cols(hub))
    sys_cols = {hk_col, "bkcc", "load_dts", "rec_src"}
    cols = [
        c.name
        for c in hub.columns
        if (not canon_bks) or c.name in sys_cols or c.name in canon_bks
    ]
    bk_by_tbl_canon = {
        (bk.table_id, bk.canonical_col_name or bk.col_name): bk.col_name
        for bk in hub.business_keys
    }
    stg_by_tbl = {s.source_table_id: s for s in ir.staging_views}
    zero_00 = zero_key_hex_sql(dialect, is_error=False)
    zero_ff = zero_key_hex_sql(dialect, is_error=True)

    stmts: list[str] = []
    for sb in hub.source_bindings:
        stg = stg_by_tbl.get(sb.table_id)
        if stg is None:
            continue
        stg_full = qualify_name("Z1", stg.view_name, aliases)
        stg_proj = (
            {f.name for f in stg.projected_fields}
            | set(stg.col_lineage_mappings.keys())
            | {r.target_col for r in stg.hash_recipes}
        )
        eff_tier = sb.key_tier or hub.key_tier
        recipe = sb.hash_recipe or hub.hash_recipe
        if eff_tier == "TIER_1_GLOBAL_STANDARD":
            bkcc_expr = "'GLOBAL'"
        else:
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
                mapped = sb.col_mappings.get(c)
                if mapped is not None and mapped in stg_proj:
                    src_col = mapped
                elif c in stg_proj:
                    src_col = c
                elif (raw_bk := bk_by_tbl_canon.get((sb.table_id, c))) is not None and (
                    raw_bk in stg_proj
                ):
                    src_col = raw_bk
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
    col_types = {c.name: c.logical_type for c in lnk.columns}
    fk_set = {p.fk_col_name for p in lnk.participants}
    deg_set = set(lnk.degenerate_keys)
    stg_by_tbl = {s.source_table_id: s for s in ir.staging_views}
    hub_tiers = {h.hub_name: h.key_tier for h in ir.hubs}
    hub_bind_tiers = {
        (h.hub_name, b.table_id): (b.key_tier or h.key_tier)
        for h in ir.hubs
        for b in h.source_bindings
    }
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
            elif c in deg_set:
                src_c = sb.col_mappings.get(c, c)
                ltype = col_types.get(c, "UTF8_STRING")
                if ltype == "UTF8_STRING":
                    norm_d = format_scalar_sql(
                        f"src.{src_c}", ltype, dialect, is_business_key=False
                    )
                    if dialect == "tsql":
                        deg_expr = f"COALESCE({norm_d}, 'UNKNOWN' COLLATE {TSQL_COLLATION})"
                    elif dialect == "clickhouse":
                        deg_expr = f"if(isNull({norm_d}), 'UNKNOWN', assumeNotNull({norm_d}))"
                    else:
                        deg_expr = f"COALESCE({norm_d}, 'UNKNOWN')"
                else:
                    deg_expr = f"src.{src_c}"
                sel_cols.append(f"{deg_expr} AS {c}")
                grp_cols.append(deg_expr)
            elif c == hk_col or c in fk_set:
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
        is_symmetric_sal = False
        if lnk.link_kind == "SAME_AS" and len(lnk.participants) == 2:
            p0, p1 = lnk.participants[0], lnk.participants[1]
            t0 = (
                p0.key_tier
                or hub_bind_tiers.get((p0.hub_name, p0.target_hub_binding_table_id or sb.table_id))
                or hub_tiers.get(p0.hub_name)
            )
            t1 = (
                p1.key_tier
                or hub_bind_tiers.get((p1.hub_name, p1.target_hub_binding_table_id or sb.table_id))
                or hub_tiers.get(p1.hub_name)
            )
            is_symmetric_sal = (p0.hub_name == p1.hub_name) and (t0 == t1)
            if is_symmetric_sal:
                fk0, fk1 = p0.fk_col_name, p1.fk_col_name
                src_fk0 = sb.col_mappings.get(fk0, fk0)
                src_fk1 = sb.col_mappings.get(fk1, fk1)
                where_parts.append(f"src.{src_fk0} <> src.{src_fk1}")
                extra_anti = f"ex.{fk0} = src_q.{fk1} AND ex.{fk1} = src_q.{fk0}"
                if dialect not in ("duckdb", "redshift", "clickhouse", "postgres"):
                    where_parts.append(
                        f"NOT EXISTS (SELECT 1 FROM {lnk_full} AS ex "
                        f"WHERE ex.{fk0} = src.{src_fk1} AND ex.{fk1} = src.{src_fk0})"
                    )

        base_select = (
            f"SELECT {', '.join(sel_cols)}\n"
            f"FROM {stg_full} AS src\n"
            f"WHERE {' AND '.join(where_parts)}\n"
            f"GROUP BY {', '.join(grp_cols)}"
        )
        if is_symmetric_sal:
            fk0, fk1 = lnk.participants[0].fk_col_name, lnk.participants[1].fk_col_name
            c0 = f"CASE WHEN _sal_g.{fk0} <= _sal_g.{fk1} THEN _sal_g.{fk0} ELSE _sal_g.{fk1} END"
            c1 = f"CASE WHEN _sal_g.{fk0} <= _sal_g.{fk1} THEN _sal_g.{fk1} ELSE _sal_g.{fk0} END"
            select_sql = (
                f"SELECT {', '.join(f'_sal_w.{c}' for c in cols)}\n"
                f"FROM (\n"
                f"    SELECT {', '.join(f'_sal_g.{c}' for c in cols)},\n"
                f"           ROW_NUMBER() OVER (PARTITION BY {c0}, {c1} "
                f"ORDER BY _sal_g.load_dts ASC, _sal_g.{hk_col} ASC) AS _sal_rn\n"
                f"    FROM (\n{base_select}\n    ) AS _sal_g\n"
                f") AS _sal_w\n"
                f"WHERE _sal_w._sal_rn = 1"
            )
        else:
            select_sql = base_select

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
        if not is_pit_bridge_excluded(pb, planned_hubs, excluded_links):
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
