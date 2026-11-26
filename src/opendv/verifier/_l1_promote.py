"""Z0..Z4 promotion from post-L1 DraftVaultIR into CertifiedVaultIR.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from opendv.models.project import EvolutionLockSpec
from opendv.models.sources import ColumnProfile, SourceProfile
from opendv.models.vault_ir import DraftVaultIR, PredicateNode, hub_canonical_bk_cols
from opendv.models.vault_ir_certified import (
    CertifiedHubSpec,
    CertifiedLinkSpec,
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    HashRecipeSpec,
    LoadSemanticsSpec,
    PhysicalLayoutHints,
    RefTableSpec,
    SchemaWaveDelta,
    SeedRecordsSpec,
    SourceBindingSpec,
    ZoneId,
)
from opendv.verifier._l1_link_promote_helpers import (
    build_link_binding_hash_recipe,
    lower_draft_bridges,
    sanitize_link_col_mappings,
    should_emit_sal,
    synthesize_link_fk_recipe,
)
from opendv.verifier._l1_zone_builders import (
    FieldSpec,
    assign_ir_fields,
    build_hash_recipes_for_hub,
    build_z0_z1_specs,
    build_z3_z4_specs,
    collect_prior_field_ids,
    dedup_field_specs,
)


def promote_draft_to_certified(
    draft: DraftVaultIR,
    *,
    profiles: Sequence[SourceProfile] = (),
    locks: EvolutionLockSpec | None = None,
    prior_ir: CertifiedVaultIR | None = None,
    ref_tables: Sequence[RefTableSpec] = (),
    link_filters: Mapping[str, PredicateNode] | None = None,
    z1_lineage_overrides: Mapping[str, Mapping[str, str]] | None = None,
    dedup_order_cols_by_sat: Mapping[str, Sequence[str]] | None = None,
) -> CertifiedVaultIR:
    """Promote post-L1 DraftVaultIR into CertifiedVaultIR across Zones Z0..Z4."""
    hwm_map = locks.iceberg_high_water_marks if locks else {}
    prior_fids = collect_prior_field_ids(prior_ir)
    filters = link_filters or {}
    z1_ovr = z1_lineage_overrides or {}
    dedup_map = dedup_order_cols_by_sat or {}
    hub_recipes_by_table: dict[str, list[HashRecipeSpec]] = {}
    col_idx: dict[tuple[str, str], ColumnProfile] = {}
    profile_cols_by_table: dict[str, set[str]] = {}
    for prof in profiles:
        for tbl in prof.tables:
            profile_cols_by_table[tbl.table_id] = {c.col_name for c in tbl.columns}
            for col in tbl.columns:
                col_idx[(tbl.table_id, col.col_name)] = col

    hubs_cert: list[CertifiedHubSpec] = []
    for h in draft.hubs:
        if h.attributes:
            raise ValueError(
                f"INV-05 violation: DraftHubSpec '{h.hub_name}' must have empty attributes."
            )
        slug = h.hub_name.removeprefix("hub_")
        hk_col = f"hk_{slug}"
        hub_recipe, bindings_h = build_hash_recipes_for_hub(h)
        for bh in bindings_h:
            if bh.hash_recipe is not None:
                hub_recipes_by_table.setdefault(bh.table_id, []).append(bh.hash_recipe)
        canon_bk_cols = hub_canonical_bk_cols(h)
        col_specs_h: list[FieldSpec] = [
            (hk_col, "FIXED_BINARY_32", False, False, "HASH_KEY"),
            ("bkcc", "UTF8_STRING", False, False, "BKCC"),
            ("load_dts", "TIMESTAMP_UTC_US", False, False, "AUDIT_DTS"),
            ("rec_src", "UTF8_STRING", False, False, "AUDIT_SRC"),
            *((col, "UTF8_STRING", False, False, "BUSINESS_KEY") for col in canon_bk_cols),
        ]
        ir_cols = assign_ir_fields(
            h.hub_name,
            dedup_field_specs(col_specs_h),
            prior_field_ids=prior_fids,
            hwm_map=hwm_map,
        )
        hints = (
            None
            if h.is_planned_concept
            else PhysicalLayoutHints(cluster_by_cols=[hk_col], iceberg_tags={"zone": "Z2"})
        )
        hubs_cert.append(
            CertifiedHubSpec(
                hub_name=h.hub_name,
                zone_id="Z2",
                business_keys=list(h.business_keys),
                key_tier=h.key_tier,
                source_bindings=bindings_h,
                attributes=[],
                is_planned_concept=h.is_planned_concept,
                survivorship_priority=list(h.survivorship_priority),
                columns=ir_cols,
                hash_recipe=hub_recipe,
                load_semantics=(
                    LoadSemanticsSpec(source_table_id=bindings_h[0].table_id)
                    if bindings_h and not h.is_planned_concept
                    else None
                ),
                physical_hints=hints,
            )
        )

    hub_cert_by_name = {h.hub_name: h for h in hubs_cert}
    links_cert: list[CertifiedLinkSpec] = []
    skipped_sal_links: set[str] = set()

    for lnk in draft.links:
        if lnk.link_kind == "SAME_AS" and not should_emit_sal(lnk):
            skipped_sal_links.add(lnk.link_name)
            continue

        slug = lnk.link_name.removeprefix("lnk_").removeprefix("t_lnk_").removeprefix("sal_")
        hk_col = f"hk_{lnk.link_name}" if lnk.link_name.startswith("sal_") else f"hk_{slug}"
        zone: ZoneId = (
            "Z3" if lnk.link_kind == "SAME_AS" or lnk.link_name.startswith("sal_") else "Z2"
        )
        flt_ast = filters.get(lnk.link_name)
        req_review: Literal["APPROVED"] | None = (
            "APPROVED" if lnk.link_kind == "UNSTRUCTURED_EXTRACTION_LINK" else None
        )
        tbl0 = lnk.source_bindings[0].table_id if lnk.source_bindings else lnk.link_name
        load_sem = LoadSemanticsSpec(
            source_table_id=tbl0,
            filter_ast=flt_ast,
            require_review_status=req_review,
        )
        bindings_l: list[SourceBindingSpec] = []
        for bl in lnk.source_bindings:
            tbl_cols = profile_cols_by_table.get(bl.table_id, set())
            bl_recipe = build_link_binding_hash_recipe(
                lnk,
                bl,
                hk_col=hk_col,
                hub_cert_by_name=hub_cert_by_name,
                col_idx=col_idx,
                tbl_cols=tbl_cols,
            )
            table_recipes = hub_recipes_by_table.setdefault(bl.table_id, [])
            table_recipes.append(bl_recipe)
            sanitized_maps = sanitize_link_col_mappings(
                bl.col_mappings,
                hk_col=hk_col,
                participants=lnk.participants,
                table_recipes=table_recipes,
            )
            existing_targets = {r.target_col for r in table_recipes}
            p_null_by_role = {
                ps.role_name: (ps.is_nullable is True) for ps in bl_recipe.participant_specs
            }
            for p in lnk.participants:
                needed_col = sanitized_maps.get(p.fk_col_name, p.fk_col_name)
                if needed_col in existing_targets:
                    if p_null_by_role.get(p.role_name):
                        for idx_r, rec in enumerate(table_recipes):
                            if (
                                rec.target_col == needed_col
                                and rec.recipe_kind == "HUB_HK"
                                and not rec.is_optional_inapplicable
                            ):
                                table_recipes[idx_r] = rec.model_copy(
                                    update={"is_optional_inapplicable": True}
                                )
                    continue
                synth = synthesize_link_fk_recipe(
                    needed_col=needed_col,
                    participant=p,
                    link_binding=bl,
                    ref_hub=hub_cert_by_name.get(p.hub_name),
                    col_idx=col_idx,
                    tbl_cols=tbl_cols,
                )
                if synth is not None:
                    table_recipes.append(synth)
                    existing_targets.add(needed_col)
            bindings_l.append(
                SourceBindingSpec(
                    source_id=bl.source_id,
                    table_id=bl.table_id,
                    col_mappings=sanitized_maps,
                    hash_recipe=bl_recipe,
                    load_semantics=LoadSemanticsSpec(
                        source_table_id=bl.table_id,
                        filter_ast=flt_ast,
                        require_review_status=req_review,
                    ),
                    doc_citations=list(bl.doc_citations),
                    mcp_evidence=list(bl.mcp_evidence),
                )
            )
        lnk_recipe = (
            bindings_l[0].hash_recipe
            if bindings_l and bindings_l[0].hash_recipe is not None
            else build_link_binding_hash_recipe(
                lnk,
                None,
                hk_col=hk_col,
                hub_cert_by_name=hub_cert_by_name,
                col_idx=col_idx,
                tbl_cols=set(),
            )
        )
        col_specs_l: list[FieldSpec] = [
            (hk_col, "FIXED_BINARY_32", False, False, "HASH_KEY"),
            *(
                (p.fk_col_name, "FIXED_BINARY_32", False, False, "HASH_FK")
                for p in lnk.participants
            ),
            ("load_dts", "TIMESTAMP_UTC_US", False, False, "AUDIT_DTS"),
            ("rec_src", "UTF8_STRING", False, False, "AUDIT_SRC"),
            *((dk, "UTF8_STRING", False, False, "DEGENERATE_KEY") for dk in lnk.degenerate_keys),
            *(
                (a.name, a.logical_type, a.nullable, a.is_pii, a.classification_tag)
                for a in lnk.attributes
            ),
        ]
        links_cert.append(
            CertifiedLinkSpec(
                link_name=lnk.link_name,
                zone_id=zone,
                link_kind=lnk.link_kind,
                participants=list(lnk.participants),
                driving_hub=lnk.driving_hub,
                degenerate_keys=list(lnk.degenerate_keys),
                source_bindings=bindings_l,
                attributes=list(lnk.attributes),
                columns=assign_ir_fields(
                    lnk.link_name,
                    dedup_field_specs(col_specs_l),
                    prior_field_ids=prior_fids,
                    hwm_map=hwm_map,
                ),
                hash_recipe=lnk_recipe,
                load_semantics=load_sem,
                physical_hints=PhysicalLayoutHints(
                    cluster_by_cols=[hk_col], iceberg_tags={"zone": zone}
                ),
            )
        )

    hub_names = {h.hub_name for h in draft.hubs}
    link_kind_by_name = {lnk.link_name: lnk.link_kind for lnk in links_cert}
    sats_cert: list[CertifiedSatelliteSpec] = []
    for sat in draft.satellites:
        if sat.sat_kind == "ESAT" and sat.parent_name in skipped_sal_links:
            continue
        sb0 = sat.source_bindings[0] if sat.source_bindings else None
        src_id = sb0.source_id if sb0 else "unknown_source"
        tbl_id = sb0.table_id if sb0 else sat.sat_name
        parent_hk = (
            f"hk_{sat.parent_name.removeprefix('hub_')}"
            if sat.parent_name in hub_names
            else f"hk_{sat.parent_name.removeprefix('lnk_').removeprefix('t_lnk_')}"
        )
        bitemp_col = sat.applied_dts_col if (sat.is_bitemporal and sat.applied_dts_col) else None
        sat_attrs = (
            [a for a in sat.attributes if a.name != bitemp_col]
            if bitemp_col
            else list(sat.attributes)
        )
        sat_cmaps = sb0.col_mappings if sb0 is not None else {}
        payload_cols = sorted(sat_cmaps.get(a.name, a.name) for a in sat_attrs)
        sat_recipe = (
            HashRecipeSpec(
                target_col="hash_diff",
                recipe_kind="SAT_HASH_DIFF",
                source_columns=payload_cols,
                sort_alphabetically=True,
                key_columns=[parent_hk],
            )
            if sat.sat_kind != "ESAT"
            else None
        )
        if sat_recipe is not None and sb0 is not None:
            hub_recipes_by_table.setdefault(tbl_id, []).append(sat_recipe)
        is_sal_esat = sat.sat_kind == "ESAT" and (
            sat.parent_name.startswith("sal_")
            or link_kind_by_name.get(sat.parent_name) == "SAME_AS"
        )
        s_zone: ZoneId = "Z3" if is_sal_esat else "Z2"
        sat_dedup = list(dedup_map.get(sat.sat_name, ()))
        bindings_s = [
            SourceBindingSpec(
                source_id=bs.source_id,
                table_id=bs.table_id,
                col_mappings=dict(bs.col_mappings),
                hash_recipe=sat_recipe,
                load_semantics=LoadSemanticsSpec(
                    source_table_id=bs.table_id,
                    dedup_order_cols=sat_dedup,
                ),
                doc_citations=list(bs.doc_citations),
                mcp_evidence=list(bs.mcp_evidence),
            )
            for bs in sat.source_bindings
        ]
        col_specs_s: list[FieldSpec] = [
            (parent_hk, "FIXED_BINARY_32", False, False, "HASH_KEY"),
            ("load_dts", "TIMESTAMP_UTC_US", False, False, "AUDIT_DTS"),
            ("rec_src", "UTF8_STRING", False, False, "AUDIT_SRC"),
        ]
        if sat_recipe is not None:
            col_specs_s.append(("hash_diff", "FIXED_BINARY_32", False, False, "HASH_DIFF"))
        if sat.is_bitemporal and sat.applied_dts_col:
            col_specs_s.append(
                (sat.applied_dts_col, "TIMESTAMP_UTC_US", False, False, "APPLIED_DTS")
            )
        for a in sat_attrs:
            col_specs_s.append(
                (a.name, a.logical_type, a.nullable, a.is_pii, a.classification_tag)
            )
        sats_cert.append(
            CertifiedSatelliteSpec(
                sat_name=sat.sat_name,
                zone_id=s_zone,
                sat_kind=sat.sat_kind,
                parent_name=sat.parent_name,
                source_id=src_id,
                source_table_id=tbl_id,
                source_bindings=bindings_s,
                extends_sat_name=sat.extends_sat_name,
                companion_epoch=sat.companion_epoch,
                is_bitemporal=sat.is_bitemporal,
                applied_dts_col=sat.applied_dts_col,
                load_mode=sat.load_mode,
                child_dependent_keys=list(sat.child_dependent_keys),
                driving_keys=list(sat.driving_keys),
                immutable_payload_fields=list(sat.immutable_payload_fields),
                attributes=sat_attrs,
                columns=assign_ir_fields(
                    sat.sat_name,
                    dedup_field_specs(col_specs_s),
                    prior_field_ids=prior_fids,
                    hwm_map=hwm_map,
                ),
                hash_recipe=sat_recipe,
                load_semantics=LoadSemanticsSpec(
                    source_table_id=tbl_id,
                    dedup_order_cols=sat_dedup,
                ),
                physical_hints=PhysicalLayoutHints(
                    cluster_by_cols=[parent_hk, "load_dts"],
                    iceberg_tags={"zone": s_zone, "sat_kind": sat.sat_kind},
                ),
            )
        )

    bronze_tables, staging_views = build_z0_z1_specs(
        profiles,
        hub_recipes_by_table,
        z1_ovr,
        prior_fids=prior_fids,
        hwm_map=hwm_map,
        hubs=draft.hubs,
    )
    pit_bridges, marts = build_z3_z4_specs(
        hubs_cert,
        sats_cert,
        links_cert,
        prior_fids=prior_fids,
        hwm_map=hwm_map,
    )
    bridge_specs = lower_draft_bridges(draft.bridges, hubs_cert, links_cert)
    pit_bridges = [*pit_bridges, *bridge_specs]

    wave_delta: SchemaWaveDelta | None = None
    if draft.wave_number >= 2:
        from_w = (
            locks.wave_number
            if locks and locks.wave_number < draft.wave_number
            else draft.wave_number - 1
        )
        locked_ents = (
            sorted(
                set(locks.locked_hub_bks.keys())
                | set(locks.locked_links.keys())
                | set(locks.locked_sat_hash_diffs.keys())
            )
            if locks
            else []
        )
        ext_names = [cs.sat_name for cs in sats_cert if cs.extends_sat_name is not None]
        added_ents = [
            *(ch.hub_name for ch in hubs_cert if ch.hub_name not in locked_ents),
            *(lnk.link_name for lnk in links_cert if lnk.link_name not in locked_ents),
            *(
                cs.sat_name
                for cs in sats_cert
                if cs.sat_name not in locked_ents and cs.extends_sat_name is None
            ),
        ]
        wave_delta = SchemaWaveDelta(
            from_wave=from_w,
            to_wave=draft.wave_number,
            added_entities=sorted(added_ents),
            added_sat_extensions=sorted(ext_names),
            locked_entities=locked_ents,
        )

    return CertifiedVaultIR(
        project_id=draft.project_id,
        ontology_version=draft.ontology_version,
        wave_number=draft.wave_number,
        bronze_tables=bronze_tables,
        staging_views=staging_views,
        hubs=hubs_cert,
        links=links_cert,
        satellites=sats_cert,
        reference_tables=list(ref_tables),
        pit_bridges=pit_bridges,
        information_marts=marts,
        seed_records=SeedRecordsSpec(),
        column_triage=list(draft.column_triage),
        justifications=list(draft.justifications),
        wave_delta=wave_delta,
    )


__all__ = ["promote_draft_to_certified"]
