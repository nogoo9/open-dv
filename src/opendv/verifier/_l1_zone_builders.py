"""Zone Z0, Z1, Z3, Z4 IR builders and monotonic IRField allocation helpers.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from opendv.models.diagnostics import KeyTierEnum
from opendv.models.ir_kernel import resolve_bkcc
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir import (
    DecimalType,
    DraftField,
    DraftHubSpec,
    PortableLogicalType,
    hub_canonical_bk_cols,
)
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
from opendv.verifier._l1_drift_and_bucket_a import (
    _portable_to_sql_hint,
    is_lossless_type_widening,
    map_sql_to_portable_type,
)

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


def _make_field(
    fid: int, name: str, ltype: PortableLogicalType, nullable: bool, is_pii: bool, tag: str | None
) -> IRField:
    return IRField(
        field_id=fid, name=name, logical_type=ltype,
        nullable=nullable, is_pii=is_pii, classification_tag=tag,
    )


def assign_ir_fields(
    table_key: str,
    specs: Sequence[FieldSpec],
    *,
    prior_field_ids: Mapping[str, Mapping[str, int]],
    hwm_map: Mapping[str, int],
) -> list[IRField]:
    """Build IRField list with strictly monotonically increasing field_id respecting HWMs."""
    existing_ids = prior_field_ids.get(table_key, {})
    next_id = max([hwm_map.get(table_key, 0), *existing_ids.values()], default=0) + 1
    out: list[IRField] = []
    assigned = sorted(
        [(existing_ids[n], n, lt, nu, pii, t) for n, lt, nu, pii, t in specs if n in existing_ids],
        key=lambda item: item[0],
    )
    last_id = 0
    for fid, name, ltype, nullable, is_pii, tag in assigned:
        last_id = fid if fid > last_id else last_id + 1
        out.append(_make_field(last_id, name, ltype, nullable, is_pii, tag))
    next_id = max(next_id, last_id + 1)
    for name, ltype, nullable, is_pii, tag in specs:
        if name not in existing_ids:
            out.append(_make_field(next_id, name, ltype, nullable, is_pii, tag))
            next_id += 1
    return out


def collect_prior_field_ids(prior_ir: CertifiedVaultIR | None) -> dict[str, dict[str, int]]:
    """Index existing IRField.field_id values by entity name from prior CertifiedVaultIR."""
    if prior_ir is None:
        return {}
    srcs = [
        *((b.table_name, b.columns) for b in prior_ir.bronze_tables),
        *((v.view_name, v.projected_fields) for v in prior_ir.staging_views),
        *((h.hub_name, h.columns) for h in prior_ir.hubs),
        *((lnk.link_name, lnk.columns) for lnk in prior_ir.links),
        *((s.sat_name, s.columns) for s in prior_ir.satellites),
        *((m.view_name, m.columns) for m in prior_ir.information_marts),
    ]
    return {name: {f.name: f.field_id for f in fields} for name, fields in srcs}


def _bkcc_pair(
    tier: KeyTierEnum, bk0_val: str | None, bk0_col: str | None, fallback: str
) -> tuple[str, str | None]:
    if tier == "TIER_1_GLOBAL_STANDARD":
        return "GLOBAL", None
    lit = resolve_bkcc(tier, bk0_val) if (tier != "TIER_3_LOCAL_SCOPED" or bk0_val) else fallback
    return lit, bk0_col


def build_hash_recipes_for_hub(
    hub: DraftHubSpec,
) -> tuple[HashRecipeSpec | None, list[SourceBindingSpec]]:
    """Construct HUB_HK HashRecipeSpec and SourceBindingSpec list for a DraftHubSpec."""
    if hub.is_planned_concept:
        return None, [
            SourceBindingSpec(
                source_id=b.source_id, table_id=b.table_id, col_mappings=dict(b.col_mappings),
                doc_citations=list(b.doc_citations), mcp_evidence=list(b.mcp_evidence),
            )
            for b in hub.source_bindings
        ]
    hk_col = f"hk_{hub.hub_name.removeprefix('hub_')}"
    bk0 = hub.business_keys[0] if hub.business_keys else None
    bkcc_lit, hub_bkcc_col = _bkcc_pair(
        hub.key_tier, bk0.bkcc_value if bk0 else "GLOBAL", bk0.bkcc_col if bk0 else None, "LOCAL"
    )
    key_cols = hub_canonical_bk_cols(hub)
    hub_recipe = HashRecipeSpec(
        target_col=hk_col, recipe_kind="HUB_HK", bkcc_literal=bkcc_lit, bkcc_col=hub_bkcc_col,
        source_columns=key_cols, sort_alphabetically=False, key_columns=key_cols,
    )
    bindings: list[SourceBindingSpec] = []
    for b in hub.source_bindings:
        bks = list(hub.business_keys)
        tbl_bks = [bk for bk in bks if bk.table_id == b.table_id] or bks
        b_bk0 = tbl_bks[0] if tbl_bks else bk0
        b_tier = b_bk0.key_tier if b_bk0 else hub.key_tier
        b_lit, b_bkcc_col = _bkcc_pair(
            b_tier, b_bk0.bkcc_value if b_bk0 else None, b_bk0.bkcc_col if b_bk0 else None, bkcc_lit
        )
        b_cols = list(dict.fromkeys(bk.col_name for bk in tbl_bks))
        bindings.append(
            SourceBindingSpec(
                source_id=b.source_id, table_id=b.table_id, col_mappings=dict(b.col_mappings),
                key_tier=b_tier,
                hash_recipe=HashRecipeSpec(
                    target_col=hk_col, recipe_kind="HUB_HK", bkcc_literal=b_lit,
                    bkcc_col=b_bkcc_col, source_columns=b_cols,
                    sort_alphabetically=False, key_columns=b_cols,
                ),
                load_semantics=LoadSemanticsSpec(source_table_id=b.table_id),
                doc_citations=list(b.doc_citations), mcp_evidence=list(b.mcp_evidence),
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
    hubs: Sequence[DraftHubSpec] = (),
) -> tuple[list[BronzeTableSpec], list[StagingViewSpec]]:
    """Build Z0 BronzeTableSpec and Z1 StagingViewSpec objects for all profiled tables."""
    aliases_by_tbl: dict[str, list[tuple[str, str]]] = {}
    for h in hubs:
        if not h.is_planned_concept:
            for bk in h.business_keys:
                if bk.canonical_col_name and bk.canonical_col_name != bk.col_name:
                    aliases_by_tbl.setdefault(bk.table_id, []).append(
                        (bk.canonical_col_name, bk.col_name)
                    )

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
            stg_specs = list(b_specs)
            spec_map = {sp[0]: sp for sp in b_specs}
            for canon_col, raw_col in aliases_by_tbl.get(tbl.table_id, ()):
                lineage.setdefault(canon_col, raw_col)
                if canon_col not in spec_map and raw_col in spec_map:
                    _, lt, nu, pii, _ = spec_map[raw_col]
                    stg_specs.append((canon_col, lt, nu, pii, "BUSINESS_KEY"))
                    spec_map[canon_col] = stg_specs[-1]
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
                        stg_name,
                        dedup_field_specs(stg_specs),
                        prior_field_ids=prior_fids,
                        hwm_map=hwm_map,
                    ),
                )
            )
    return bronze_tables, staging_views


def _order_sats_by_priority(
    sats: Sequence[CertifiedSatelliteSpec],
    priority: Sequence[str],
) -> list[CertifiedSatelliteSpec]:
    prio_idx = {key: idx for idx, key in enumerate(priority)}

    def _key(sat: CertifiedSatelliteSpec) -> tuple[int, int, int, str]:
        st_key = f"{sat.source_id}:{sat.source_table_id}"
        ep = -(sat.companion_epoch or 1)
        if st_key in prio_idx:
            return (0, prio_idx[st_key], ep, sat.sat_name)
        if sat.source_id in prio_idx:
            return (0, prio_idx[sat.source_id], ep, sat.sat_name)
        return (1, 0, ep, sat.sat_name)

    return sorted(sats, key=_key)


def _common_widened_type(types: Sequence[PortableLogicalType]) -> PortableLogicalType | None:
    if not types:
        return None
    cur: PortableLogicalType = types[0]
    for nxt in types[1:]:
        if cur == nxt or is_lossless_type_widening(nxt, cur):
            continue
        if is_lossless_type_widening(cur, nxt):
            cur = nxt
        elif isinstance(cur, DecimalType) and isinstance(nxt, DecimalType):
            max_i = max(cur.precision - cur.scale, nxt.precision - nxt.scale)
            max_s = max(cur.scale, nxt.scale)
            cur = DecimalType(precision=min(38, max_i + max_s), scale=max_s)
        elif (
            (isinstance(cur, DecimalType) and nxt in ("INT32", "INT64"))
            or (isinstance(nxt, DecimalType) and cur in ("INT32", "INT64"))
        ):
            dec = cur if isinstance(cur, DecimalType) else nxt
            iv = nxt if isinstance(cur, DecimalType) else cur
            assert isinstance(dec, DecimalType)
            req = 10 if iv == "INT32" else 19
            prec = min(38, max(req, dec.precision - dec.scale) + dec.scale)
            cur = DecimalType(precision=prec, scale=dec.scale)
        else:
            return None
    return cur


def _superseded_sat_cols(ordered_sats: Sequence[CertifiedSatelliteSpec]) -> set[tuple[str, str]]:
    by_name = {s.sat_name: s for s in ordered_sats}
    attrs_map = {s.sat_name: {a.name: a for a in s.attributes} for s in ordered_sats}
    superseded: set[tuple[str, str]] = set()
    for ext in ordered_sats:
        base_name, seen = ext.extends_sat_name, {ext.sat_name}
        while base_name and base_name not in seen and base_name in by_name:
            seen.add(base_name)
            b_attrs = attrs_map[base_name]
            for attr in ext.attributes:
                old = b_attrs.get(attr.name)
                if old is not None and not is_lossless_type_widening(
                    old.logical_type, attr.logical_type
                ):
                    superseded.add((base_name, attr.name))
            base_name = by_name[base_name].extends_sat_name
    return superseded


def _build_esat_interval_mart(
    sat: CertifiedSatelliteSpec,
    lnk: CertifiedLinkSpec,
    *,
    prior_fids: Mapping[str, Mapping[str, int]],
    hwm_map: Mapping[str, int],
) -> MartViewSpec:
    hk_lnk = (
        sat.columns[0].name
        if sat.columns
        else (lnk.columns[0].name if lnk.columns else f"hk_{sat.parent_name}")
    )
    driving_hk = (
        sat.driving_keys[0]
        if sat.driving_keys
        else (f"hk_{lnk.driving_hub.removeprefix('hub_')}" if lnk.driving_hub else "driving_hk")
    )
    eff_ts = (
        f"{sat.sat_name}.{sat.applied_dts_col}"
        if (sat.is_bitemporal and sat.applied_dts_col)
        else f"{sat.sat_name}.load_dts"
    )
    lead_expr = (
        f"LEAD({eff_ts}) OVER (PARTITION BY {sat.parent_name}.{driving_hk} "
        f"ORDER BY {eff_ts}, {sat.sat_name}.load_dts)"
    )
    view_name = f"business_vault.vw_{sat.sat_name}_intervals"
    specs: list[FieldSpec] = [
        (hk_lnk, "FIXED_BINARY_32", False, False, "HASH_KEY"),
        (driving_hk, "FIXED_BINARY_32", False, False, "HASH_FK"),
        ("effective_from", "TIMESTAMP_UTC_US", False, False, "EFFECTIVE_FROM"),
        ("effective_to", "TIMESTAMP_UTC_US", True, False, "EFFECTIVE_TO"),
        ("is_current", "BOOLEAN", False, False, "IS_CURRENT"),
    ]
    return MartViewSpec(
        view_name=view_name,
        zone_id="Z3",
        view_kind="ESAT_INTERVALS",
        root_entity=sat.sat_name,
        joined_entities=[sat.parent_name],
        columns=assign_ir_fields(
            view_name, dedup_field_specs(specs), prior_field_ids=prior_fids, hwm_map=hwm_map
        ),
        select_exprs={
            hk_lnk: f"{sat.sat_name}.{hk_lnk}",
            driving_hk: f"{sat.parent_name}.{driving_hk}",
            "effective_from": eff_ts,
            "effective_to": lead_expr,
            "is_current": f"({lead_expr} IS NULL)",
        },
    )


def build_z3_z4_specs(
    hubs_cert: Sequence[CertifiedHubSpec],
    sats_cert: Sequence[CertifiedSatelliteSpec],
    links_cert: Sequence[CertifiedLinkSpec],
    *,
    prior_fids: Mapping[str, Mapping[str, int]],
    hwm_map: Mapping[str, int],
) -> tuple[list[PitBridgeSpec], list[MartViewSpec]]:
    """Build Z3 PIT / ESAT_INTERVALS and Z4 information_mart.vw_* MartViewSpec objects."""
    pit_bridges: list[PitBridgeSpec] = []
    marts: list[MartViewSpec] = []
    planned_hubs = {h.hub_name for h in hubs_cert if h.is_planned_concept}
    links_by_name = {lnk.link_name: lnk for lnk in links_cert}

    for ch in hubs_cert:
        if ch.is_planned_concept:
            continue
        slug = ch.hub_name.removeprefix("hub_")
        canon_bks = hub_canonical_bk_cols(ch)
        pit_sats = [
            cs
            for cs in sats_cert
            if cs.parent_name == ch.hub_name
            and not (cs.sat_kind == "MAS" or bool(cs.child_dependent_keys))
        ]
        if len(pit_sats) >= 2:
            pit_bridges.append(
                PitBridgeSpec(
                    object_name=f"pit_{slug}",
                    object_kind="PIT",
                    zone_id="Z3",
                    root_hub=ch.hub_name,
                    target_satellites_or_links=sorted(cs.sat_name for cs in pit_sats),
                    grain_cols=[f"hk_{slug}", "snapshot_dts"],
                )
            )
        vw_sats = _order_sats_by_priority(
            [cs for cs in pit_sats if cs.sat_kind != "PII"], ch.survivorship_priority
        )
        superseded = _superseded_sat_cols(vw_sats)
        view_name = f"information_mart.vw_{slug}_current"
        col_specs_m: list[FieldSpec] = [
            (f"hk_{slug}", "FIXED_BINARY_32", False, False, "HASH_KEY"),
            *((col, "UTF8_STRING", False, False, "BUSINESS_KEY") for col in canon_bks),
        ]
        select_exprs: dict[str, str] = {
            f"hk_{slug}": f"{ch.hub_name}.hk_{slug}",
            **{col: f"{ch.hub_name}.{col}" for col in canon_bks},
        }
        reserved = {sp[0] for sp in col_specs_m}
        arms_by_col: dict[str, list[tuple[CertifiedSatelliteSpec, DraftField]]] = {}
        joined_sats: list[str] = []
        for cs in vw_sats:
            used = False
            for a in cs.attributes:
                if a.name not in reserved and (cs.sat_name, a.name) not in superseded:
                    arms_by_col.setdefault(a.name, []).append((cs, a))
                    used = True
            if used:
                joined_sats.append(cs.sat_name)

        for col_name, arms in arms_by_col.items():
            _, a0 = arms[0]
            if len(arms) == 1:
                col_specs_m.append(
                    (col_name, a0.logical_type, a0.nullable, a0.is_pii, a0.classification_tag)
                )
                select_exprs[col_name] = f"{arms[0][0].sat_name}.{a0.name}"
                continue
            tgt_t = _common_widened_type([a.logical_type for _, a in arms]) or a0.logical_type
            exprs: list[str] = []
            for cs, a in arms:
                ref = f"{cs.sat_name}.{a.name}"
                ex = f"NULLIF(TRIM({ref}), '')" if a.logical_type == "UTF8_STRING" else ref
                if a.logical_type != tgt_t:
                    ex = f"CAST({ex} AS {_portable_to_sql_hint(tgt_t)})"
                exprs.append(ex)
            select_exprs[col_name] = f"COALESCE({', '.join(exprs)})"
            col_specs_m.append((
                col_name, tgt_t, all(a.nullable for _, a in arms),
                any(a.is_pii for _, a in arms), a0.classification_tag,
            ))

        marts.append(
            MartViewSpec(
                view_name=view_name, zone_id="Z4", view_kind="CURRENT",
                root_entity=ch.hub_name, joined_entities=joined_sats,
                columns=assign_ir_fields(
                    view_name, col_specs_m, prior_field_ids=prior_fids, hwm_map=hwm_map
                ),
                select_exprs=select_exprs,
            )
        )

    for sat in sats_cert:
        if sat.sat_kind == "ESAT" and sat.zone_id == "Z2":
            lnk = links_by_name.get(sat.parent_name)
            if lnk is not None and not any(p.hub_name in planned_hubs for p in lnk.participants):
                marts.append(
                    _build_esat_interval_mart(sat, lnk, prior_fids=prior_fids, hwm_map=hwm_map)
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
