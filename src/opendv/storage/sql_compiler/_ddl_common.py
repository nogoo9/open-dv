"""Shared DDL helpers (zone schemas, aliases, planned exclusions, view wrapping).

Strictly Layer 2a: imports only opendv.models, dialects, sqlglot, and stdlib.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from opendv.models.project import DbtSqlDialect
from opendv.models.vault_ir import BridgeHopSpec, PortableLogicalType
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
from opendv.storage.sql_compiler._collection_exprs import TSQL_COLLATION

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


def col_def_sql(
    field: IRField,
    target_id: str,
    dialect: DbtSqlDialect,
    *,
    is_key_col: bool = False,
) -> str:
    """Format a column definition for CREATE TABLE DDL."""
    if is_key_col and dialect == "tsql" and field.logical_type == "UTF8_STRING":
        return f"{field.name} VARCHAR(255) COLLATE {TSQL_COLLATION} NOT NULL"
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


@dataclass(frozen=True, slots=True)
class BridgeHopColumnSpec:
    """Resolved column and join metadata for a single Bridge hop."""

    link_name: str
    brg_link_hk_col: str
    src_link_hk_col: str
    from_fk_col: str
    to_fk_col: str | None
    join_parent_idx: int
    join_parent_col: str
    to_hub_name: str | None
    brg_to_hub_hk_col: str | None


def is_pit_bridge_excluded(
    pb: PitBridgeSpec,
    planned_hubs: set[str],
    excluded_links: set[str],
) -> bool:
    """Return True when a PIT/Bridge references a planned Hub or excluded Link."""
    if pb.root_hub in planned_hubs:
        return True
    if pb.object_kind == "BRIDGE":
        hop_links = (
            [h.link_name for h in pb.hops] if pb.hops else list(pb.target_satellites_or_links)
        )
        return any(lk in excluded_links for lk in hop_links)
    return False


def resolve_bridge_hop_columns(
    pb: PitBridgeSpec,
    ir: CertifiedVaultIR,
    max_ident_bytes: int,
) -> tuple[str, str, list[BridgeHopColumnSpec]]:
    """Return (src_root_hk, brg_root_hk, hop_specs) across pb.hops or target links."""
    used: set[str] = set()
    hub = next((h for h in ir.hubs if h.hub_name == pb.root_hub), None)
    src_root_hk = (
        hub.columns[0].name if (hub and hub.columns) else f"hk_{pb.root_hub.removeprefix('hub_')}"
    )
    brg_root_hk = truncate_identifier_utf8(src_root_hk, max_ident_bytes, used)
    used.add("load_dts")

    hops = (
        list(pb.hops)
        if pb.hops
        else [BridgeHopSpec(link_name=t) for t in pb.target_satellites_or_links]
    )
    links_by_name = {lk.link_name: lk for lk in ir.links}
    trav_offset = (
        1
        if len(pb.traversed_hk_cols) == len(hops) + 1 and pb.traversed_hk_cols[0] == src_root_hk
        else 0
    )

    hop_specs: list[BridgeHopColumnSpec] = []
    cur_hub = pb.root_hub
    prev_idx = -1
    prev_col = src_root_hk

    for idx, hop in enumerate(hops):
        lnk = links_by_name.get(hop.link_name)
        src_lnk_hk = lnk.columns[0].name if (lnk and lnk.columns) else f"hk_{hop.link_name}"
        brg_link_hk = truncate_identifier_utf8(src_lnk_hk, max_ident_bytes, used)
        if lnk is None:
            hop_specs.append(
                BridgeHopColumnSpec(
                    link_name=hop.link_name,
                    brg_link_hk_col=brg_link_hk,
                    src_link_hk_col=src_lnk_hk,
                    from_fk_col=hop.from_fk_col or src_root_hk,
                    to_fk_col=hop.to_fk_col,
                    join_parent_idx=prev_idx,
                    join_parent_col=prev_col,
                    to_hub_name=None,
                    brg_to_hub_hk_col=None,
                )
            )
            continue

        join_idx, join_col, anchor_hub = prev_idx, prev_col, cur_hub
        if (
            not pb.hops
            and idx > 0
            and not any(p.hub_name == cur_hub for p in lnk.participants)
            and any(p.hub_name == pb.root_hub for p in lnk.participants)
        ):
            join_idx, join_col, anchor_hub = -1, src_root_hk, pb.root_hub

        from_fk = hop.from_fk_col
        if from_fk is None:
            p_from = next((p for p in lnk.participants if p.hub_name == anchor_hub), None)
            if p_from is None and lnk.participants:
                p_from = lnk.participants[0]
            from_fk = p_from.fk_col_name if p_from is not None else src_root_hk

        to_fk = hop.to_fk_col
        to_hub: str | None = None
        if to_fk is not None:
            p_to = next((p for p in lnk.participants if p.fk_col_name == to_fk), None)
            to_hub = p_to.hub_name if p_to is not None else None
        else:
            p_to = next(
                (
                    p
                    for p in lnk.participants
                    if p.fk_col_name != from_fk and p.hub_name != anchor_hub
                ),
                None,
            )
            if p_to is None:
                p_to = next((p for p in lnk.participants if p.fk_col_name != from_fk), None)
            if p_to is not None:
                to_fk = p_to.fk_col_name
                to_hub = p_to.hub_name

        brg_to_hk: str | None = None
        if to_fk is not None:
            trav_idx = idx + trav_offset
            raw_trav = (
                pb.traversed_hk_cols[trav_idx]
                if trav_idx < len(pb.traversed_hk_cols)
                else to_fk
            )
            brg_to_hk = truncate_identifier_utf8(raw_trav, max_ident_bytes, used)

        hop_specs.append(
            BridgeHopColumnSpec(
                link_name=hop.link_name,
                brg_link_hk_col=brg_link_hk,
                src_link_hk_col=src_lnk_hk,
                from_fk_col=from_fk,
                to_fk_col=to_fk,
                join_parent_idx=join_idx,
                join_parent_col=join_col,
                to_hub_name=to_hub,
                brg_to_hub_hk_col=brg_to_hk,
            )
        )
        if to_hub is not None and to_fk is not None:
            cur_hub = to_hub
            prev_idx = idx
            prev_col = to_fk

    return src_root_hk, brg_root_hk, hop_specs


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
        src_root_hk, pit_root_hk, hop_specs = resolve_bridge_hop_columns(pb, ir, max_ident_bytes)
        for hop in hop_specs:
            out.append((hop.link_name, hop.brg_link_hk_col, "load_dts", None, "TIMESTAMP_UTC_US"))
    return src_root_hk, pit_root_hk, out


__all__ = [
    "ZONE_SCHEMAS",
    "BridgeHopColumnSpec",
    "alias_of",
    "assert_parsed_clean",
    "col_def_sql",
    "is_pit_bridge_excluded",
    "planned_exclusion_sets",
    "qualify_name",
    "resolve_bridge_hop_columns",
    "resolve_pit_bridge_columns",
    "resolve_staging_hd_col_map",
    "wrap_replace_view",
]
