"""Zone Z0, Z1, Z3, Z4 IR builders and monotonic IRField allocation helpers.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from opendv.models.ir_kernel import resolve_bkcc
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir import DraftHubSpec, PortableLogicalType
from opendv.models.vault_ir_certified import (
    BronzeTableSpec,
    CertifiedHubSpec,
    CertifiedLinkSpec,
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    HashRecipeSpec,
    IRField,
    LoadSemanticsSpec,
    MartViewSpec,
    PitBridgeSpec,
    SourceBindingSpec,
    StagingViewSpec,
)
from opendv.verifier._l1_drift_and_bucket_a import map_sql_to_portable_type

FieldSpec = tuple[str, PortableLogicalType, bool, bool, str | None]


def dedup_field_specs(specs: Sequence[FieldSpec]) -> list[FieldSpec]:
    """Deduplicate field specs by column name keeping first occurrence."""
    seen: set[str] = set()
    out: list[FieldSpec] = []
    for sp in specs:
        if sp[0] not in seen:
            seen.add(sp[0])
            out.append(sp)
    return out


def assign_ir_fields(
    table_key: str,
    specs: Sequence[FieldSpec],
    *,
    prior_field_ids: Mapping[str, Mapping[str, int]],
    hwm_map: Mapping[str, int],
) -> list[IRField]:
    """Build IRField list with strictly monotonically increasing field_id respecting HWMs."""
    existing_ids = prior_field_ids.get(table_key, {})
    hwm = hwm_map.get(table_key, 0)
    next_id = max([hwm, *existing_ids.values()], default=0) + 1
    out: list[IRField] = []
    assigned_existing: list[tuple[int, str, PortableLogicalType, bool, bool, str | None]] = []
    new_items: list[FieldSpec] = []

    for name, ltype, nullable, is_pii, tag in specs:
        if name in existing_ids:
            assigned_existing.append((existing_ids[name], name, ltype, nullable, is_pii, tag))
        else:
            new_items.append((name, ltype, nullable, is_pii, tag))

    assigned_existing.sort(key=lambda item: item[0])
    last_id = 0
    for fid, name, ltype, nullable, is_pii, tag in assigned_existing:
        actual_id = fid if fid > last_id else last_id + 1
        last_id = actual_id
        out.append(
            IRField(
                field_id=actual_id,
                name=name,
                logical_type=ltype,
                nullable=nullable,
                is_pii=is_pii,
                classification_tag=tag,
            )
        )
    next_id = max(next_id, last_id + 1)
    for name, ltype, nullable, is_pii, tag in new_items:
        out.append(
            IRField(
                field_id=next_id,
                name=name,
                logical_type=ltype,
                nullable=nullable,
                is_pii=is_pii,
                classification_tag=tag,
            )
        )
        next_id += 1
    return out


def collect_prior_field_ids(prior_ir: CertifiedVaultIR | None) -> dict[str, dict[str, int]]:
    """Index existing IRField.field_id values by entity name from prior wave CertifiedVaultIR."""
    if prior_ir is None:
        return {}
    mapping: dict[str, dict[str, int]] = {}
    for b in prior_ir.bronze_tables:
        mapping[b.table_name] = {f.name: f.field_id for f in b.columns}
    for stg in prior_ir.staging_views:
        mapping[stg.view_name] = {f.name: f.field_id for f in stg.projected_fields}
    for h in prior_ir.hubs:
        mapping[h.hub_name] = {f.name: f.field_id for f in h.columns}
    for lnk in prior_ir.links:
        mapping[lnk.link_name] = {f.name: f.field_id for f in lnk.columns}
    for sat in prior_ir.satellites:
        mapping[sat.sat_name] = {f.name: f.field_id for f in sat.columns}
    for m in prior_ir.information_marts:
        mapping[m.view_name] = {f.name: f.field_id for f in m.columns}
    return mapping


def build_hash_recipes_for_hub(
    hub: DraftHubSpec,
) -> tuple[HashRecipeSpec | None, list[SourceBindingSpec]]:
    """Construct HUB_HK HashRecipeSpec and SourceBindingSpec list for a DraftHubSpec."""
    if hub.is_planned_concept:
        return None, [
            SourceBindingSpec(
                source_id=b.source_id,
                table_id=b.table_id,
                col_mappings=dict(b.col_mappings),
                doc_citations=list(b.doc_citations),
                mcp_evidence=list(b.mcp_evidence),
            )
            for b in hub.source_bindings
        ]
    slug = hub.hub_name.removeprefix("hub_")
    hk_col = f"hk_{slug}"
    bk0 = hub.business_keys[0] if hub.business_keys else None
    bkcc_lit = (
        resolve_bkcc(hub.key_tier, bk0.bkcc_value if bk0 else "GLOBAL")
        if hub.key_tier != "TIER_3_LOCAL_SCOPED" or (bk0 and bk0.bkcc_value)
        else "LOCAL"
    )
    key_cols = list(dict.fromkeys(bk.col_name for bk in hub.business_keys))
    hub_recipe = HashRecipeSpec(
        target_col=hk_col,
        recipe_kind="HUB_HK",
        bkcc_literal=bkcc_lit,
        bkcc_col=bk0.bkcc_col if bk0 else None,
        source_columns=key_cols,
        sort_alphabetically=False,
        key_columns=key_cols,
    )
    bindings: list[SourceBindingSpec] = []
    for b in hub.source_bindings:
        tbl_bks = [bk for bk in hub.business_keys if bk.table_id == b.table_id] or list(
            hub.business_keys
        )
        b_bk0 = tbl_bks[0] if tbl_bks else bk0
        b_tier = b_bk0.key_tier if b_bk0 else hub.key_tier
        b_lit = (
            resolve_bkcc(b_tier, b_bk0.bkcc_value)
            if b_bk0 and (b_tier != "TIER_3_LOCAL_SCOPED" or b_bk0.bkcc_value)
            else bkcc_lit
        )
        b_cols = list(dict.fromkeys(bk.col_name for bk in tbl_bks))
        b_recipe = HashRecipeSpec(
            target_col=hk_col,
            recipe_kind="HUB_HK",
            bkcc_literal=b_lit,
            bkcc_col=b_bk0.bkcc_col if b_bk0 else None,
            source_columns=b_cols,
            sort_alphabetically=False,
            key_columns=b_cols,
        )
        bindings.append(
            SourceBindingSpec(
                source_id=b.source_id,
                table_id=b.table_id,
                col_mappings=dict(b.col_mappings),
                hash_recipe=b_recipe,
                load_semantics=LoadSemanticsSpec(source_table_id=b.table_id),
                doc_citations=list(b.doc_citations),
                mcp_evidence=list(b.mcp_evidence),
            )
        )
    return hub_recipe, bindings


def build_z0_z1_specs(
    profiles: Sequence[SourceProfile],
    hub_recipes_by_table: Mapping[str, list[HashRecipeSpec]],
    z1_ovr: Mapping[str, Mapping[str, str]],
    *,
    prior_fids: Mapping[str, Mapping[str, int]],
    hwm_map: Mapping[str, int],
) -> tuple[list[BronzeTableSpec], list[StagingViewSpec]]:
    """Build Z0 BronzeTableSpec and Z1 StagingViewSpec objects for all profiled tables."""
    bronze_tables: list[BronzeTableSpec] = []
    staging_views: list[StagingViewSpec] = []
    for prof in profiles:
        for tbl in prof.tables:
            b_name = f"bronze_{tbl.table_name}"
            b_specs: list[FieldSpec] = [
                (
                    c.col_name,
                    map_sql_to_portable_type(c.sql_type),
                    c.nullable,
                    bool(c.pii_tags),
                    None,
                )
                for c in tbl.columns
            ]
            bronze_tables.append(
                BronzeTableSpec(
                    table_name=b_name,
                    source_id=prof.source_id,
                    source_table_id=tbl.table_id,
                    zone_id="Z0",
                    columns=assign_ir_fields(
                        b_name, b_specs, prior_field_ids=prior_fids, hwm_map=hwm_map
                    ),
                )
            )
            stg_name = f"stg_{tbl.table_name}"
            lineage = {c.col_name: c.col_name for c in tbl.columns}
            lineage.update(z1_ovr.get(tbl.table_id, {}))
            staging_views.append(
                StagingViewSpec(
                    view_name=stg_name,
                    source_id=prof.source_id,
                    source_table_id=tbl.table_id,
                    zone_id="Z1",
                    col_lineage_mappings=lineage,
                    hash_recipes=list(hub_recipes_by_table.get(tbl.table_id, [])),
                    projected_fields=assign_ir_fields(
                        stg_name, b_specs, prior_field_ids=prior_fids, hwm_map=hwm_map
                    ),
                )
            )
    return bronze_tables, staging_views


def build_z3_z4_specs(
    hubs_cert: Sequence[CertifiedHubSpec],
    sats_cert: Sequence[CertifiedSatelliteSpec],
    links_cert: Sequence[CertifiedLinkSpec],
    *,
    prior_fids: Mapping[str, Mapping[str, int]],
    hwm_map: Mapping[str, int],
) -> tuple[list[PitBridgeSpec], list[MartViewSpec]]:
    """Build Z3 PitBridgeSpec and Z4 information_mart.vw_* MartViewSpec objects."""
    _ = links_cert
    pit_bridges: list[PitBridgeSpec] = []
    marts: list[MartViewSpec] = []
    for ch in hubs_cert:
        if ch.is_planned_concept:
            continue
        slug = ch.hub_name.removeprefix("hub_")
        attached_sats = [cs for cs in sats_cert if cs.parent_name == ch.hub_name]
        if len(attached_sats) >= 2:
            pit_bridges.append(
                PitBridgeSpec(
                    object_name=f"pit_{slug}",
                    object_kind="PIT",
                    zone_id="Z3",
                    root_hub=ch.hub_name,
                    target_satellites_or_links=sorted(cs.sat_name for cs in attached_sats),
                    grain_cols=[f"hk_{slug}", "snapshot_dts"],
                )
            )

        view_name = f"information_mart.vw_{slug}_current"
        col_specs_m: list[FieldSpec] = [
            (f"hk_{slug}", "FIXED_BINARY_32", False, False, "HASH_KEY"),
            *(
                (bk.col_name, "UTF8_STRING", False, False, "BUSINESS_KEY")
                for bk in ch.business_keys
            ),
        ]
        select_exprs: dict[str, str] = {
            f"hk_{slug}": f"{ch.hub_name}.hk_{slug}",
            **{bk.col_name: f"{ch.hub_name}.{bk.col_name}" for bk in ch.business_keys},
        }
        seen_mc = {sp[0] for sp in col_specs_m}
        for cs in attached_sats:
            for a in cs.attributes:
                if a.name not in seen_mc:
                    seen_mc.add(a.name)
                    col_specs_m.append(
                        (a.name, a.logical_type, a.nullable, a.is_pii, a.classification_tag)
                    )
                    select_exprs[a.name] = f"{cs.sat_name}.{a.name}"
        marts.append(
            MartViewSpec(
                view_name=view_name,
                zone_id="Z4",
                root_entity=ch.hub_name,
                joined_entities=[cs.sat_name for cs in attached_sats],
                columns=assign_ir_fields(
                    view_name, col_specs_m, prior_field_ids=prior_fids, hwm_map=hwm_map
                ),
                select_exprs=select_exprs,
            )
        )
    return pit_bridges, marts


__all__ = [
    "FieldSpec",
    "assign_ir_fields",
    "build_hash_recipes_for_hub",
    "build_z0_z1_specs",
    "build_z3_z4_specs",
    "collect_prior_field_ids",
    "dedup_field_specs",
]
