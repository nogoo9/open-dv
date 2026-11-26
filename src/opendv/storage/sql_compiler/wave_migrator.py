"""Incremental WaveMigrator (`V001..V00N` additive DDL, EVO-04 widening, 0 Z2 drops).

Strictly Layer 2a: imports only opendv.models, dialects, sqlglot, ddl_builders, and stdlib.
"""

from __future__ import annotations

from collections.abc import Mapping

import sqlglot
from sqlglot import exp

from opendv.models.project import DbtSqlDialect, EvolutionLockSpec
from opendv.models.vault_ir_certified import (
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    IRField,
    PitBridgeSpec,
)
from opendv.storage.dialects import (
    SqlDialectProfile,
    assign_physical_aliases,
    get_dialect_profile,
    lower_logical_type,
)
from opendv.storage.sql_compiler.ddl_builders import (
    build_bronze_table_ddl,
    build_hub_ddl,
    build_link_ddl,
    build_mart_view_ddl,
    build_pit_bridge_ddl,
    build_ref_table_ddl,
    build_sat_ddl,
    build_schema_ddl,
    build_staging_view_ddl,
    compile_full_ddl,
    is_pit_bridge_excluded,
    planned_exclusion_sets,
    qualify_name,
    resolve_bridge_hop_columns,
    resolve_pit_bridge_columns,
    resolve_staging_hd_col_map,
)


def _alter_widen_column_sql(
    full_table: str,
    col: IRField,
    profile: SqlDialectProfile,
    dialect: DbtSqlDialect,
) -> str:
    dtype_str = lower_logical_type(
        col.logical_type,
        profile.target_id,
        include_tsql_collation=(dialect == "tsql"),
    )
    if dialect == "tsql":
        # sqlglot DataType.build treats VARCHAR(MAX) safely when parsed directly
        dtype_node = exp.DataType.build(dtype_str, dialect=dialect)
    else:
        dtype_node = exp.DataType.build(dtype_str, dialect=dialect)
    alter_ast = exp.Alter(
        this=exp.to_table(full_table, dialect=dialect),
        kind="TABLE",
        actions=[
            exp.AlterColumn(
                this=exp.to_identifier(col.name, quoted=False),
                dtype=dtype_node,
                allow_null=(col.nullable if dialect == "tsql" else None),
            )
        ],
    )
    sql = alter_ast.sql(dialect=dialect)
    parsed = sqlglot.parse(sql, read=dialect)
    for node in parsed:
        if node is not None and any(isinstance(sub, exp.Command) for sub in node.walk()):
            raise ValueError(f"exp.Command generated in EVO-04 alter for {dialect}: {sql}")
    return sql


def _alter_add_column_sql(
    full_table: str,
    col: IRField,
    profile: SqlDialectProfile,
    dialect: DbtSqlDialect,
) -> str:
    dtype_str = lower_logical_type(
        col.logical_type,
        profile.target_id,
        include_tsql_collation=(dialect == "tsql"),
    )
    if (
        dialect == "clickhouse"
        and col.nullable
        and not dtype_str.startswith(("Array(", "Map(", "JSON"))
    ):
        dtype_str = f"Nullable({dtype_str})"
    col_def = exp.ColumnDef(
        this=exp.to_identifier(col.name, quoted=False),
        kind=exp.DataType.build(dtype_str, dialect=dialect),
    )
    alter_ast = exp.Alter(
        this=exp.to_table(full_table, dialect=dialect),
        kind="TABLE",
        actions=[col_def],
    )
    sql = alter_ast.sql(dialect=dialect)
    parsed = sqlglot.parse(sql, read=dialect)
    for node in parsed:
        if node is not None and any(isinstance(sub, exp.Command) for sub in node.walk()):
            raise ValueError(f"exp.Command generated in ADD COLUMN for {dialect}: {sql}")
    return sql


def _pit_bridge_schema_sig(
    pb: PitBridgeSpec,
    ir: CertifiedVaultIR,
    max_ident_bytes: int,
) -> tuple[object, ...]:
    if pb.object_kind == "PIT":
        _, root_hk, targets = resolve_pit_bridge_columns(pb, ir, max_ident_bytes)
        return ("PIT", root_hk, tuple(targets))
    _, root_hk, hops = resolve_bridge_hop_columns(pb, ir, max_ident_bytes)
    return ("BRIDGE", root_hk, tuple((h.brg_link_hk_col, h.brg_to_hub_hk_col) for h in hops))


class WaveMigrator:
    """Compile additive wave-to-wave DDL migrations without touching Z2 Raw Vault history."""

    def compile_migration(
        self,
        prior_ir: CertifiedVaultIR | None,
        current_ir: CertifiedVaultIR,
        target_or_dialect: str,
        *,
        locks: EvolutionLockSpec | None = None,
        aliases: Mapping[str, str] | None = None,
    ) -> list[str]:
        """Return ordered DDL statements migrating prior_ir -> current_ir."""
        profile = get_dialect_profile(target_or_dialect)
        if profile.sqlglot_dialect is None:
            raise ValueError(f"WaveMigrator requires a SQL target, got {profile.target_id}")
        dialect = profile.sqlglot_dialect
        resolved_aliases = (
            dict(aliases)
            if aliases is not None
            else assign_physical_aliases(current_ir, profile.target_id, locks=locks)
        )
        hd_col_map = resolve_staging_hd_col_map(
            current_ir, max_ident_bytes=profile.max_ident_bytes
        )

        if prior_ir is None:
            return compile_full_ddl(current_ir, profile, resolved_aliases, hd_col_map)

        planned_hubs, excluded_links, excluded_sats = planned_exclusion_sets(current_ir)
        stmts: list[str] = list(build_schema_ddl(profile))

        prior_bronze = {b.table_name: b for b in prior_ir.bronze_tables}
        prior_refs = {r.ref_name for r in prior_ir.reference_tables}
        prior_hubs = {h.hub_name: h for h in prior_ir.hubs if not h.is_planned_concept}
        prior_links = {lk.link_name: lk for lk in prior_ir.links}
        prior_sats = {s.sat_name: s for s in prior_ir.satellites}

        # 1. Z0 Bronze & Reference tables (new tables or additive new columns)
        for b in current_ir.bronze_tables:
            if b.table_name not in prior_bronze:
                stmts.append(build_bronze_table_ddl(b, profile, resolved_aliases))
            else:
                old_cols = {c.name: c for c in prior_bronze[b.table_name].columns}
                full_b = qualify_name("Z0", b.table_name, resolved_aliases)
                for col in b.columns:
                    if col.name not in old_cols:
                        stmts.append(_alter_add_column_sql(full_b, col, profile, dialect))
                    elif (
                        col.logical_type != old_cols[col.name].logical_type
                        and profile.supports_alter_widen
                    ):
                        stmts.append(_alter_widen_column_sql(full_b, col, profile, dialect))

        for r in current_ir.reference_tables:
            if r.ref_name not in prior_refs:
                stmts.append(build_ref_table_ddl(r, profile, resolved_aliases))

        # 2. Z2/Z3 Hubs, Links, Satellites (new entities + EVO-04 widenings)
        for h in current_ir.hubs:
            if h.hub_name in planned_hubs:
                continue
            if h.hub_name not in prior_hubs:
                stmts.append(build_hub_ddl(h, profile, resolved_aliases))

        for lnk in current_ir.links:
            if lnk.link_name in excluded_links:
                continue
            if lnk.link_name not in prior_links:
                stmts.append(build_link_ddl(lnk, profile, resolved_aliases))

        existing_sat_names = {
            s.sat_name for s in current_ir.satellites if s.sat_name not in excluded_sats
        }
        for sat in current_ir.satellites:
            if sat.sat_name in excluded_sats:
                continue
            if sat.sat_name not in prior_sats:
                stmts.append(build_sat_ddl(sat, profile, resolved_aliases))
                continue

            old_sat = prior_sats[sat.sat_name]
            old_cols = {c.name: c for c in old_sat.columns}
            full_s = qualify_name(sat.zone_id, sat.sat_name, resolved_aliases)
            widened_cols: list[IRField] = []
            for col in sat.columns:
                if col.name not in old_cols:
                    # Only emit ADD COLUMN if not superseded by sat_*_ext_vN
                    stmts.append(_alter_add_column_sql(full_s, col, profile, dialect))
                elif col.logical_type != old_cols[col.name].logical_type:
                    widened_cols.append(col)

            if widened_cols:
                if profile.supports_alter_widen:
                    for col in widened_cols:
                        stmts.append(_alter_widen_column_sql(full_s, col, profile, dialect))
                else:
                    # ClickHouse: route EVO-04 widening to companion sat_*_ext_vN
                    ext_name = f"{sat.sat_name}_ext_v{current_ir.wave_number}"
                    if ext_name not in existing_sat_names:
                        parent_hk = sat.columns[0]
                        load_dts_f = next(c for c in sat.columns if c.name == "load_dts")
                        rec_src_f = next(c for c in sat.columns if c.name == "rec_src")
                        hd_f = next((c for c in sat.columns if c.name == "hash_diff"), None)
                        ext_cols = [parent_hk, load_dts_f, rec_src_f]
                        if hd_f is not None:
                            ext_cols.append(hd_f)
                        next_fid = max(c.field_id for c in ext_cols) + 1
                        for wcol in widened_cols:
                            ext_cols.append(
                                IRField(
                                    field_id=next_fid,
                                    name=wcol.name,
                                    logical_type=wcol.logical_type,
                                    nullable=wcol.nullable,
                                    is_pii=wcol.is_pii,
                                    classification_tag=wcol.classification_tag,
                                )
                            )
                            next_fid += 1
                        ext_spec = CertifiedSatelliteSpec(
                            sat_name=ext_name,
                            zone_id=sat.zone_id,
                            sat_kind="EXT",
                            parent_name=sat.parent_name,
                            source_id=sat.source_id,
                            source_table_id=sat.source_table_id,
                            extends_sat_name=sat.sat_name,
                            companion_epoch=current_ir.wave_number,
                            columns=ext_cols,
                            physical_hints=sat.physical_hints,
                        )
                        stmts.append(build_sat_ddl(ext_spec, profile, resolved_aliases))

        # 3. Z1 Staging Views (rebuilt via CREATE OR REPLACE / DROP+CREATE / CREATE OR ALTER)
        for stg in current_ir.staging_views:
            stmts.extend(
                build_staging_view_ddl(stg, current_ir, profile, resolved_aliases, hd_col_map)
            )

        # 4. Z3 PIT / Bridge DDL schema evolution (new or schema-changed tables)
        prior_pbs = {p.object_name: p for p in prior_ir.pit_bridges}
        for pb in current_ir.pit_bridges:
            if is_pit_bridge_excluded(pb, planned_hubs, excluded_links):
                continue
            prev_pb = prior_pbs.get(pb.object_name)
            if prev_pb is None or (
                _pit_bridge_schema_sig(pb, current_ir, profile.max_ident_bytes)
                != _pit_bridge_schema_sig(prev_pb, prior_ir, profile.max_ident_bytes)
            ):
                stmts.extend(build_pit_bridge_ddl(pb, current_ir, profile, resolved_aliases))

        # 5. Z3/Z4 Information Mart & ESAT Interval Views
        for mart in current_ir.information_marts:
            if (
                mart.root_entity not in planned_hubs
                and mart.root_entity not in excluded_sats
                and mart.root_entity not in excluded_links
            ):
                stmts.extend(build_mart_view_ddl(mart, current_ir, profile, resolved_aliases))

        return stmts


__all__ = ["WaveMigrator"]
