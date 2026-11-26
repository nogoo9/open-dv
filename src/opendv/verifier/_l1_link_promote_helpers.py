"""Link participant_specs, staging HUB_HK FK, SAL gating, and Bridge lowering helpers.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from opendv.models.diagnostics import KeyTierEnum
from opendv.models.sources import ColumnProfile
from opendv.models.vault_ir import (
    DraftBridgeSpec,
    DraftLinkSpec,
    DraftSourceBinding,
    LinkParticipantSpec,
    PredicateBranch,
    PredicateLeaf,
    PredicateNode,
)
from opendv.models.vault_ir_certified import (
    CertifiedHubSpec,
    CertifiedLinkSpec,
    HashRecipeSpec,
    LinkParticipantHashSpec,
    PitBridgeSpec,
    SourceBindingSpec,
)


def sanitize_link_col_mappings(
    raw_maps: Mapping[str, str],
    *,
    hk_col: str,
    participants: Sequence[LinkParticipantSpec],
    table_recipes: Sequence[HashRecipeSpec],
) -> dict[str, str]:
    """Ensure Link HK/FK col_mappings reference hashed staging targets, never raw columns."""
    hash_targets = {r.target_col for r in table_recipes}
    hub_recipes = [r for r in table_recipes if r.recipe_kind == "HUB_HK"]
    part_by_fk = {p.fk_col_name: p for p in participants}
    link_hash_cols = {hk_col, *part_by_fk}
    out: dict[str, str] = {}
    for k, v in raw_maps.items():
        if k not in link_hash_cols or v in hash_targets:
            out[k] = v
            continue
        if k == hk_col:
            continue
        part = part_by_fk[k]
        cand_cols = set(part.source_col_names) | {v}
        matched = next((r for r in hub_recipes if cand_cols & set(r.source_columns)), None)
        if matched is None:
            hub_hk = f"hk_{part.hub_name.removeprefix('hub_')}"
            matched = next((r for r in hub_recipes if r.target_col == hub_hk), None)
        if matched is not None and matched.target_col != k:
            out[k] = matched.target_col
    return out


def resolve_hub_binding_for_participant(
    participant: LinkParticipantSpec,
    link_binding: DraftSourceBinding | None,
    ref_hub: CertifiedHubSpec | None,
) -> SourceBindingSpec | None:
    """Deterministically match a Link participant to its target Hub SourceBindingSpec."""
    if ref_hub is None or ref_hub.is_planned_concept or not ref_hub.source_bindings:
        return None
    if participant.target_hub_binding_table_id:
        return next(
            (
                hb
                for hb in ref_hub.source_bindings
                if hb.table_id == participant.target_hub_binding_table_id
            ),
            None,
        )
    if link_binding is not None:
        by_tbl = next(
            (hb for hb in ref_hub.source_bindings if hb.table_id == link_binding.table_id),
            None,
        )
        if by_tbl is not None:
            return by_tbl
        by_src = [hb for hb in ref_hub.source_bindings if hb.source_id == link_binding.source_id]
        if len(by_src) == 1:
            return by_src[0]
    if len(ref_hub.source_bindings) == 1:
        return ref_hub.source_bindings[0]
    return None


def _collect_predicate_cols(node: PredicateNode | None) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, PredicateLeaf):
        return {node.column}
    if isinstance(node, PredicateBranch):
        cols: set[str] = set()
        for child in node.children:
            cols.update(_collect_predicate_cols(child))
        return cols
    return set()


def _remap_predicate_node(node: PredicateNode, col_map: Mapping[str, str]) -> PredicateNode:
    if isinstance(node, PredicateLeaf):
        return node.model_copy(update={"column": col_map[node.column]})
    return node.model_copy(
        update={"children": [_remap_predicate_node(c, col_map) for c in node.children]}
    )


def _remap_hub_predicate(
    pred: PredicateNode | None,
    ref_hub: CertifiedHubSpec | None,
    matched_hb: SourceBindingSpec | None,
    part_src_cols: Sequence[str],
) -> PredicateNode | None:
    if pred is None or ref_hub is None:
        return None
    hub_bks = (
        [bk.col_name for bk in ref_hub.business_keys if bk.table_id == matched_hb.table_id]
        if matched_hb is not None
        else []
    )
    if not hub_bks:
        hub_bks = list(dict.fromkeys(bk.col_name for bk in ref_hub.business_keys))
    if not hub_bks or len(hub_bks) != len(part_src_cols):
        return None
    col_map = dict(zip(hub_bks, part_src_cols, strict=True))
    pred_cols = _collect_predicate_cols(pred)
    if not pred_cols or not pred_cols.issubset(col_map.keys()):
        return None
    return _remap_predicate_node(pred, col_map)


def resolve_participant_src_cols(
    part: LinkParticipantSpec,
    link_binding: DraftSourceBinding | None,
    tbl_cols: set[str],
) -> list[str]:
    """Resolve source columns on a Link binding for a participant."""
    raw = list(part.source_col_names)
    if not raw and link_binding is not None:
        mapped = link_binding.col_mappings.get(part.fk_col_name)
        if mapped:
            raw = [mapped]
    if link_binding is None:
        return raw
    resolved: list[str] = []
    for col in raw:
        mapped = link_binding.col_mappings.get(col)
        if tbl_cols:
            if col in tbl_cols:
                resolved.append(col)
            elif mapped and mapped in tbl_cols:
                resolved.append(mapped)
            else:
                resolved.append(col)
        elif mapped and not mapped.startswith("hk_"):
            resolved.append(mapped)
        else:
            resolved.append(col)
    return resolved


def resolve_participant_nullable(
    part: LinkParticipantSpec,
    tbl_id: str,
    src_cols: Sequence[str],
    col_idx: Mapping[tuple[str, str], ColumnProfile],
) -> bool:
    """Resolve tri-state is_nullable (False mandatory, True nullable, None -> null_ratio > 0)."""
    if part.is_nullable is False:
        return False
    if part.is_nullable is True:
        return True
    if not tbl_id:
        return False
    return any(
        (cp := col_idx.get((tbl_id, sc))) is not None and cp.null_ratio > 0.0 for sc in src_cols
    )


def build_link_binding_hash_recipe(
    lnk: DraftLinkSpec,
    link_binding: DraftSourceBinding | None,
    *,
    hk_col: str,
    hub_cert_by_name: Mapping[str, CertifiedHubSpec],
    col_idx: Mapping[tuple[str, str], ColumnProfile],
    tbl_cols: set[str],
) -> HashRecipeSpec:
    """Build per-Link-binding HashRecipeSpec with ordered LinkParticipantHashSpecs."""
    tbl_id = link_binding.table_id if link_binding is not None else ""
    src_id = link_binding.source_id if link_binding is not None else ""
    part_specs: list[LinkParticipantHashSpec] = []

    for part in lnk.participants:
        src_cols = resolve_participant_src_cols(part, link_binding, tbl_cols)
        if not src_cols:
            src_cols = [part.fk_col_name]
        ref_hub = hub_cert_by_name.get(part.hub_name)
        matched_hb = resolve_hub_binding_for_participant(part, link_binding, ref_hub)
        hb_recipe = (
            matched_hb.hash_recipe
            if matched_hb is not None and matched_hb.hash_recipe is not None
            else (ref_hub.hash_recipe if ref_hub is not None else None)
        )
        eff_tier: KeyTierEnum = (
            part.key_tier
            or (matched_hb.key_tier if matched_hb is not None else None)
            or (ref_hub.key_tier if ref_hub is not None else "TIER_2_ENTERPRISE_SOR")
        )
        if eff_tier == "TIER_1_GLOBAL_STANDARD":
            bkcc_lit: str | None = "GLOBAL"
            bkcc_col: str | None = None
        else:
            raw_hb_bkcc_col = (
                hb_recipe.bkcc_col
                if hb_recipe is not None
                and hb_recipe.bkcc_col
                and (not tbl_cols or hb_recipe.bkcc_col in tbl_cols)
                else None
            )
            bkcc_col = part.bkcc_col if part.bkcc_col is not None else raw_hb_bkcc_col
            if part.bkcc_value and part.bkcc_value.strip():
                bkcc_lit = part.bkcc_value.strip().upper()
            elif hb_recipe is not None and hb_recipe.bkcc_literal:
                bkcc_lit = hb_recipe.bkcc_literal
            elif eff_tier == "TIER_3_LOCAL_SCOPED":
                bkcc_lit = src_id.strip().upper() or tbl_id.strip().upper() or "LOCAL"
            else:
                bkcc_lit = "GLOBAL"

        unk_pred = part.unknown_predicate
        if unk_pred is None and hb_recipe is not None:
            unk_pred = _remap_hub_predicate(
                hb_recipe.unknown_predicate, ref_hub, matched_hb, src_cols
            )
        err_pred = part.error_predicate
        if err_pred is None and hb_recipe is not None:
            err_pred = _remap_hub_predicate(
                hb_recipe.error_predicate, ref_hub, matched_hb, src_cols
            )

        resolved_null = resolve_participant_nullable(part, tbl_id, src_cols, col_idx)
        part_specs.append(
            LinkParticipantHashSpec(
                hub_name=part.hub_name,
                role_name=part.role_name,
                target_hub_binding_table_id=(
                    matched_hb.table_id
                    if matched_hb is not None
                    else part.target_hub_binding_table_id
                ),
                bkcc_literal=bkcc_lit,
                bkcc_col=bkcc_col,
                key_tier=eff_tier,
                source_columns=src_cols,
                unknown_predicate=unk_pred,
                error_predicate=err_pred,
                is_degenerate=False,
                is_nullable=resolved_null,
            )
        )

    for dk in lnk.degenerate_keys:
        mapped_dk = link_binding.col_mappings.get(dk, dk) if link_binding is not None else dk
        dk_col = mapped_dk if not mapped_dk.startswith("hk_") else dk
        part_specs.append(
            LinkParticipantHashSpec(
                hub_name="",
                role_name=dk,
                bkcc_literal=None,
                bkcc_col=None,
                key_tier="TIER_2_ENTERPRISE_SOR",
                source_columns=[dk_col],
                is_degenerate=True,
                is_nullable=None,
            )
        )

    all_cols = list(dict.fromkeys(c for ps in part_specs for c in ps.source_columns))
    non_degen = [ps for ps in part_specs if not ps.is_degenerate]
    all_opt = bool(non_degen) and all(ps.is_nullable is True for ps in non_degen)
    return HashRecipeSpec(
        target_col=hk_col,
        recipe_kind="LINK_HK",
        bkcc_literal=None if part_specs else "GLOBAL",
        source_columns=all_cols,
        sort_alphabetically=False,
        key_columns=all_cols,
        participant_specs=part_specs,
        is_optional_inapplicable=all_opt,
    )


def synthesize_link_fk_recipe(
    *,
    needed_col: str,
    participant: LinkParticipantSpec,
    link_binding: DraftSourceBinding,
    ref_hub: CertifiedHubSpec | None,
    col_idx: Mapping[tuple[str, str], ColumnProfile],
    tbl_cols: set[str],
) -> HashRecipeSpec | None:
    """Synthesize a missing Link participant HUB_HK recipe on a staging table."""

    def _cols_ok(cols: Sequence[str]) -> bool:
        return bool(cols) and (not tbl_cols or all(c in tbl_cols for c in cols))

    fk_src_cols: list[str] = []
    cand_src = resolve_participant_src_cols(participant, link_binding, tbl_cols)
    if _cols_ok(cand_src):
        fk_src_cols = cand_src
    elif (mapped := link_binding.col_mappings.get(participant.fk_col_name)) and _cols_ok([mapped]):
        fk_src_cols = [mapped]
    elif ref_hub is not None and _cols_ok([bk.col_name for bk in ref_hub.business_keys]):
        fk_src_cols = [bk.col_name for bk in ref_hub.business_keys]
    if not fk_src_cols:
        return None

    matched_hb = resolve_hub_binding_for_participant(participant, link_binding, ref_hub)
    hub_recipe = (
        matched_hb.hash_recipe
        if matched_hb is not None and matched_hb.hash_recipe is not None
        else (ref_hub.hash_recipe if ref_hub is not None else None)
    )
    eff_tier: KeyTierEnum = (
        participant.key_tier
        or (matched_hb.key_tier if matched_hb is not None else None)
        or (ref_hub.key_tier if ref_hub is not None else "TIER_2_ENTERPRISE_SOR")
    )
    if eff_tier == "TIER_1_GLOBAL_STANDARD":
        bkcc_col: str | None = None
        bkcc_lit: str | None = "GLOBAL"
    else:
        raw_hb_col = (
            hub_recipe.bkcc_col
            if hub_recipe is not None
            and hub_recipe.bkcc_col
            and (not tbl_cols or hub_recipe.bkcc_col in tbl_cols)
            else None
        )
        bkcc_col = participant.bkcc_col if participant.bkcc_col is not None else raw_hb_col
        if participant.bkcc_value and participant.bkcc_value.strip():
            bkcc_lit = participant.bkcc_value.strip().upper()
        elif hub_recipe is not None and hub_recipe.bkcc_literal:
            bkcc_lit = hub_recipe.bkcc_literal
        else:
            bkcc_lit = None if bkcc_col is not None else "GLOBAL"

    resolved_null = resolve_participant_nullable(
        participant, link_binding.table_id, fk_src_cols, col_idx
    )
    return HashRecipeSpec(
        target_col=needed_col,
        recipe_kind="HUB_HK",
        bkcc_literal=bkcc_lit,
        bkcc_col=bkcc_col,
        source_columns=fk_src_cols,
        sort_alphabetically=False,
        key_columns=fk_src_cols,
        is_optional_inapplicable=resolved_null,
    )


def should_emit_sal(lnk: DraftLinkSpec) -> bool:
    """Gate SAME_AS (sal_*) emission on verified crosswalk or multi-key bindings."""
    if lnk.link_kind != "SAME_AS" or not lnk.source_bindings or len(lnk.participants) != 2:
        return False
    p0, p1 = lnk.participants
    if p0.hub_name != p1.hub_name:
        return True
    if p0.source_col_names != p1.source_col_names:
        return True
    return any(
        sb.col_mappings.get(p0.fk_col_name) != sb.col_mappings.get(p1.fk_col_name)
        for sb in lnk.source_bindings
    )


def lower_draft_bridges(
    draft_bridges: Sequence[DraftBridgeSpec],
    hubs_cert: Sequence[CertifiedHubSpec],
    links_cert: Sequence[CertifiedLinkSpec],
) -> list[PitBridgeSpec]:
    """Lower DraftBridgeSpec entries into Z3 PitBridgeSpec(object_kind='BRIDGE')."""
    planned_hubs = {h.hub_name for h in hubs_cert if h.is_planned_concept}
    active_hubs = {h.hub_name: h for h in hubs_cert if not h.is_planned_concept}
    excluded_links = {
        lnk.link_name
        for lnk in links_cert
        if any(p.hub_name in planned_hubs for p in lnk.participants)
    }
    active_links = {
        lnk.link_name: lnk for lnk in links_cert if lnk.link_name not in excluded_links
    }

    bridges: list[PitBridgeSpec] = []
    for brg in draft_bridges:
        if brg.root_hub in planned_hubs or any(hop.link_name in excluded_links for hop in brg.hops):
            continue

        root_hub_spec = active_hubs.get(brg.root_hub)
        root_hk = (
            root_hub_spec.columns[0].name
            if (root_hub_spec is not None and root_hub_spec.columns)
            else f"hk_{brg.root_hub.removeprefix('hub_')}"
        )
        cur_hub = brg.root_hub
        traversed_hks: list[str] = []
        link_hks: list[str] = []

        for hop in brg.hops:
            lnk = active_links.get(hop.link_name)
            lnk_slug = hop.link_name.removeprefix("lnk_").removeprefix("t_lnk_").removeprefix(
                "sal_"
            )
            if lnk is None:
                link_hks.append(f"hk_{lnk_slug}")
                continue
            l_hk = lnk.columns[0].name if lnk.columns else f"hk_{lnk_slug}"
            link_hks.append(l_hk)
            p_in = (
                next((p for p in lnk.participants if p.fk_col_name == hop.from_fk_col), None)
                if hop.from_fk_col
                else next((p for p in lnk.participants if p.hub_name == cur_hub), None)
            )
            p_out = (
                next((p for p in lnk.participants if p.fk_col_name == hop.to_fk_col), None)
                if hop.to_fk_col
                else next((p for p in lnk.participants if p is not p_in), None)
            )
            if p_in is not None and p_out is not None:
                traversed_hks.append(p_out.fk_col_name)
                cur_hub = p_out.hub_name

        bridges.append(
            PitBridgeSpec(
                object_name=brg.bridge_name,
                object_kind="BRIDGE",
                zone_id="Z3",
                root_hub=brg.root_hub,
                target_satellites_or_links=[hop.link_name for hop in brg.hops],
                grain_cols=[root_hk, *link_hks],
                hops=list(brg.hops),
                traversed_hk_cols=traversed_hks,
            )
        )
    return bridges


__all__ = [
    "build_link_binding_hash_recipe",
    "lower_draft_bridges",
    "resolve_hub_binding_for_participant",
    "resolve_participant_nullable",
    "resolve_participant_src_cols",
    "sanitize_link_col_mappings",
    "should_emit_sal",
    "synthesize_link_fk_recipe",
]
