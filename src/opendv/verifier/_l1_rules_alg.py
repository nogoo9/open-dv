"""Pure L1 deterministic codemods: Alg A.2 (ref_*), Alg A.3 (esat/mas), and Alg A.4 (BKCC/FK).

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from opendv.models.diagnostics import KeyTierEnum
from opendv.models.ir_kernel import resolve_bkcc
from opendv.models.project import EvolutionLockSpec
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
from opendv.models.vault_ir import (
    BusinessKeyPart,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
    LinkParticipantSpec,
    PredicateBranch,
    PredicateLeaf,
    PredicateNode,
)
from opendv.verifier._l1_alg_a1 import L1CodemodOptions, build_profile_indexes
from opendv.verifier._l1_alg_a2 import apply_alg_a2_ref_demotion

_TIER_RANK: dict[KeyTierEnum, int] = {
    "TIER_1_GLOBAL_STANDARD": 1,
    "TIER_2_ENTERPRISE_SOR": 2,
    "TIER_3_LOCAL_SCOPED": 3,
}
_TIER1_HINTS: frozenset[str] = frozenset(
    {"lei", "lei_code", "isin", "cusip", "figi", "iban", "swift_bic", "bic"}
)
_TEMPORAL_PRIORITY: tuple[str, ...] = (
    "applied_dts",
    "effective_from",
    "valid_from",
    "eff_from",
    "effective_date",
    "as_of_dts",
    "as_of_date",
    "updated_at",
    "modified_at",
    "changed_at",
)
_EXCLUDED_TEMP: frozenset[str] = frozenset(
    {
        "load_dts",
        "etl_snapshot_ts",
        "ingested_at",
        "created_at",
        "created_dts",
        "effective_to",
        "valid_to",
        "eff_to",
        "end_date",
        "end_dts",
    }
)


def _is_not_null_ast(cols: Sequence[str]) -> PredicateNode | None:
    clean = [c for c in cols if c]
    if not clean:
        return None
    leaves: list[PredicateNode] = [PredicateLeaf(op="IS_NOT_NULL", column=c) for c in clean]
    return leaves[0] if len(leaves) == 1 else PredicateBranch(op="AND", children=leaves)


def _normalize_sal(parts: list[LinkParticipantSpec]) -> list[LinkParticipantSpec]:
    if len(parts) != 2:
        return parts
    p0, p1 = parts[0], parts[1]
    r0 = p0.role_name.strip().upper() or "MASTER"
    r1 = p1.role_name.strip().upper() or "DUPLICATE"
    if r0 == "DUPLICATE" and r1 == "MASTER":
        p0, p1, r0, r1 = p1, p0, r1, r0
    if r0 == r1:
        r0, r1 = "MASTER", "DUPLICATE"
    fk0, fk1 = p0.fk_col_name or "hk_master", p1.fk_col_name or "hk_duplicate"
    if fk0 == fk1:
        fk0, fk1 = f"{fk0}_master", f"{fk1}_duplicate"
    return [
        p0.model_copy(update={"role_name": r0, "fk_col_name": fk0}),
        p1.model_copy(update={"role_name": r1, "fk_col_name": fk1}),
    ]


def _align_canonical_bks(bks: list[BusinessKeyPart], strategy: str) -> list[BusinessKeyPart]:
    if strategy != "unified_canonical_when_same_arity" or len(bks) < 2:
        return bks
    by_tbl: dict[str, list[BusinessKeyPart]] = {}
    for bk in bks:
        by_tbl.setdefault(bk.table_id, []).append(bk)
    if len(by_tbl) < 2 or len({len(v) for v in by_tbl.values()}) != 1:
        return bks
    tiers = {bk.key_tier for bk in bks}
    if ("TIER_1_GLOBAL_STANDARD" in tiers and "TIER_3_LOCAL_SCOPED" in tiers) or len(
        {bk.col_name for bk in bks}
    ) <= 1:
        return bks
    slots = [bk.canonical_col_name or bk.col_name for bk in next(iter(by_tbl.values()))]
    out: list[BusinessKeyPart] = []
    pos: dict[str, int] = {}
    for bk in bks:
        i = pos.get(bk.table_id, 0)
        pos[bk.table_id] = i + 1
        if bk.canonical_col_name is None and i < len(slots) and slots[i] != bk.col_name:
            out.append(bk.model_copy(update={"canonical_col_name": slots[i]}))
        else:
            out.append(bk)
    return out


def _part_nullability(
    part: LinkParticipantSpec,
    tbl_ids: Sequence[str],
    col_maps: Mapping[str, str],
    col_idx: Mapping[tuple[str, str], ColumnProfile],
) -> tuple[bool, float, list[str]]:
    cols = list(part.source_col_names)
    if not cols and part.fk_col_name in col_maps:
        cols = [col_maps[part.fk_col_name]]
    max_n, min_nn = 0.0, 1.0
    for tid in tbl_ids:
        for sc in cols:
            if (cp := col_idx.get((tid, sc))) is not None:
                max_n = max(max_n, cp.null_ratio)
                min_nn = min(min_nn, 1.0 - cp.null_ratio)
    if part.is_nullable is False:
        return False, min_nn, cols
    if part.is_nullable is True:
        return True, min_nn, cols
    return max_n > 0.0, min_nn, cols


def apply_alg_a4_bkcc_and_sparse_fks(
    draft: DraftVaultIR,
    profiles: Sequence[SourceProfile],
    locks: EvolutionLockSpec | None = None,
    *,
    options: L1CodemodOptions | None = None,
) -> tuple[DraftVaultIR, dict[str, PredicateNode]]:
    """Alg A.4: normalize 3-tier BKCC, canonicalize sal_*, and preserve nullable STANDARD Links."""
    opts = options or L1CodemodOptions()
    col_idx, _ = build_profile_indexes(profiles)
    locked_hubs = locks.locked_hub_bks if locks else {}
    locked_links = locks.locked_links if locks else {}

    updated_hubs: list[DraftHubSpec] = []
    for hub in draft.hubs:
        if hub.is_planned_concept:
            updated_hubs.append(hub)
            continue
        is_locked = hub.hub_name in locked_hubs
        new_bks: list[BusinessKeyPart] = []
        for bk in hub.business_keys:
            if is_locked:
                new_bks.append(bk)
                continue
            tier: KeyTierEnum = (
                "TIER_1_GLOBAL_STANDARD" if bk.col_name.lower() in _TIER1_HINTS else bk.key_tier
            )
            if tier == "TIER_1_GLOBAL_STANDARD":
                resolved, bkcc_col = "GLOBAL", None
            elif tier == "TIER_2_ENTERPRISE_SOR":
                resolved = resolve_bkcc(tier, bk.bkcc_value or bk.source_id)
                bkcc_col = bk.bkcc_col
            else:
                cand = bk.bkcc_value.strip() or bk.source_id.strip() or bk.table_id.strip()
                resolved, bkcc_col = resolve_bkcc(tier, cand or "LOCAL"), bk.bkcc_col
            new_bks.append(
                bk.model_copy(
                    update={"key_tier": tier, "bkcc_value": resolved, "bkcc_col": bkcc_col}
                )
            )
        if not is_locked:
            new_bks = _align_canonical_bks(new_bks, opts.multi_source_hub_strategy)
        best_tier = (
            min((b.key_tier for b in new_bks), key=lambda t: _TIER_RANK[t])
            if new_bks
            else hub.key_tier
        )
        updated_hubs.append(
            hub.model_copy(update={"business_keys": new_bks, "key_tier": best_tier})
        )

    link_filters: dict[str, PredicateNode] = {}
    updated_links: list[DraftLinkSpec] = []
    seen_names: set[str] = {lnk.link_name for lnk in draft.links}

    for lnk in draft.links:
        if lnk.link_kind == "SAME_AS":
            updated_links.append(
                lnk.model_copy(update={"participants": _normalize_sal(lnk.participants)})
            )
            continue
        if lnk.link_kind not in ("STANDARD", "TRANSACTIONAL"):
            updated_links.append(lnk)
            continue
        if (
            lnk.link_kind == "TRANSACTIONAL"
            and not opts.allow_transactional_links
            and not lnk.attributes
            and lnk.link_name not in locked_links
            and lnk.link_name not in opts.waived_ap007_entities
        ):
            lnk = lnk.model_copy(update={"link_kind": "STANDARD"})
        if lnk.link_kind == "STANDARD" and any(p.hub_name.strip() for p in lnk.participants):
            updated_links.append(lnk)
            continue

        tids = [b.table_id for b in lnk.source_bindings]
        cmaps = lnk.source_bindings[0].col_mappings if lnk.source_bindings else {}
        mandatory, sparse = [], []
        for p in lnk.participants:
            r_null, min_nn, sc = _part_nullability(p, tids, cmaps, col_idx)
            if p.is_nullable is None and min_nn < opts.mandatory_fk_non_null_ratio and r_null:
                sparse.append((p, sc))
            else:
                mandatory.append(p)
        if not sparse:
            updated_links.append(lnk)
            continue
        if len(lnk.participants) <= 2 or lnk.link_name in locked_links or len(mandatory) < 2:
            if (node := _is_not_null_ast([c for _, cols in sparse for c in cols])) is not None:
                link_filters[lnk.link_name] = node
            updated_links.append(lnk)
            continue
        updated_links.append(lnk.model_copy(update={"participants": mandatory}))
        anchor = next((p for p in mandatory if p.hub_name == lnk.driving_hub), mandatory[0])
        for sp_part, sp_cols in sparse:
            slug = (sp_part.role_name or sp_part.hub_name.removeprefix("hub_")).lower()
            base_n, s_name, idx = f"{lnk.link_name}_{slug}", f"{lnk.link_name}_{slug}", 2
            while s_name in seen_names:
                s_name, idx = f"{base_n}_{idx}", idx + 1
            seen_names.add(s_name)
            updated_links.append(
                DraftLinkSpec(
                    link_name=s_name,
                    link_kind=lnk.link_kind,
                    participants=[anchor, sp_part],
                    driving_hub=anchor.hub_name,
                    degenerate_keys=[],
                    source_bindings=list(lnk.source_bindings),
                    attributes=[],
                )
            )
            if (node := _is_not_null_ast(sp_cols)) is not None:
                link_filters[s_name] = node

    return draft.model_copy(update={"hubs": updated_hubs, "links": updated_links}), link_filters


def _detect_applied_dts(tbl: TableProfile | None, p_cols: set[str]) -> str | None:
    if tbl is None:
        return None
    low_map = {c.col_name.lower(): c for c in tbl.columns}
    for cand in _TEMPORAL_PRIORITY:
        if (cp := low_map.get(cand)) is not None and cp.col_name not in p_cols:
            return cp.col_name
    for cp in tbl.columns:
        low = cp.col_name.lower()
        if (
            cp.col_name in p_cols
            or low in _EXCLUDED_TEMP
            or low.startswith("created_")
            or low.endswith(("_to", "_end", "_end_dts"))
        ):
            continue
        up = cp.sql_type.upper()
        if cp.format_shape in ("ISO_TIMESTAMP", "ISO_DATE") or any(
            k in up for k in ("TIMESTAMP", "DATETIME", "DATE")
        ):
            return cp.col_name
    return None


def _resolve_driving_part(
    lnk: DraftLinkSpec,
    sb: DraftSourceBinding,
    tmap: Mapping[str, float],
    col_idx: Mapping[tuple[str, str], ColumnProfile],
) -> tuple[LinkParticipantSpec | None, float, int]:
    pk_idxs: list[int] = []
    non_pk_idxs: list[int] = []
    max_rate, max_idx = 0.0, -1
    for idx, p in enumerate(lnk.participants):
        raw = list(p.source_col_names) or (
            [sb.col_mappings[p.fk_col_name]] if p.fk_col_name in sb.col_mappings else []
        )
        cols = [sb.col_mappings.get(c, c) for c in raw] or [p.fk_col_name]
        is_pk = any((cp := col_idx.get((sb.table_id, c))) is not None and cp.is_pk for c in cols)
        (pk_idxs if is_pk else non_pk_idxs).append(idx)
        rate = max((float(tmap.get(c, 0.0)) for c in (*raw, *cols)), default=0.0)
        if rate > max_rate:
            max_rate, max_idx = rate, idx
    if lnk.driving_hub and (
        exp := next((p for p in lnk.participants if p.hub_name == lnk.driving_hub), None)
    ) is not None:
        return exp, max_rate, len(pk_idxs)
    if len(pk_idxs) == 1 and non_pk_idxs:
        return lnk.participants[pk_idxs[0]], max_rate, len(pk_idxs)
    if max_rate > 0.0 and max_idx >= 0:
        other = next((i for i in range(len(lnk.participants)) if i != max_idx), max_idx)
        return lnk.participants[other], max_rate, len(pk_idxs)
    return (lnk.participants[0] if len(lnk.participants) >= 2 else None), max_rate, len(pk_idxs)


def apply_alg_a3_esat_and_mas(
    draft: DraftVaultIR,
    fk_turnover_by_table: Mapping[str, Mapping[str, float]] | None = None,
    *,
    profiles: Sequence[SourceProfile] = (),
    options: L1CodemodOptions | None = None,
) -> DraftVaultIR:
    """Alg A.3: emit Z2 single-source esat_* across mutable Link bindings and normalize MAS."""
    opts = options or L1CodemodOptions()
    turnover = fk_turnover_by_table or {}
    col_idx, tbl_idx = build_profile_indexes(profiles)
    existing_keys = {
        (s.parent_name, sb.source_id, sb.table_id)
        for s in draft.satellites
        if s.sat_kind == "ESAT"
        for sb in s.source_bindings
    }
    existing_parents = {s.parent_name for s in draft.satellites if s.sat_kind == "ESAT"}
    used_names = {s.sat_name for s in draft.satellites}
    updated_links: list[DraftLinkSpec] = []
    added_sats: list[DraftSatelliteSpec] = []

    for lnk in draft.links:
        if (
            lnk.link_kind == "TRANSACTIONAL"
            and not opts.allow_transactional_links
            and not lnk.attributes
            and lnk.link_name not in opts.waived_ap007_entities
        ):
            lnk = lnk.model_copy(update={"link_kind": "STANDARD"})
        if opts.esat_mode == "off" or lnk.link_kind != "STANDARD" or not lnk.source_bindings:
            updated_links.append(lnk)
            continue

        src_cnts: dict[str, int] = {}
        for sb in lnk.source_bindings:
            src_cnts[sb.source_id] = src_cnts.get(sb.source_id, 0) + 1
        driving_hub = lnk.driving_hub

        for sb in lnk.source_bindings:
            if (lnk.link_name, sb.source_id, sb.table_id) in existing_keys or (
                not sb.source_id and lnk.link_name in existing_parents
            ):
                continue
            tbl_prof = tbl_idx.get(sb.table_id)
            d_part, max_rate, pk_cnt = _resolve_driving_part(
                lnk, sb, turnover.get(sb.table_id, {}), col_idx
            )
            p_cols = {
                sb.col_mappings.get(c, c)
                for p in lnk.participants
                for c in (p.source_col_names or [p.fk_col_name])
            }
            app_col = _detect_applied_dts(tbl_prof, p_cols)
            attach = max_rate > opts.esat_turnover_threshold
            if not attach and opts.esat_mode == "always_on_driving_hub":
                attach = lnk.driving_hub is not None or (
                    tbl_prof is not None
                    and len(lnk.participants) >= 2
                    and (pk_cnt == 1 or (app_col is not None and pk_cnt == 0))
                )
            if not attach or d_part is None:
                continue
            driving_hub = driving_hub or d_part.hub_name
            slug = lnk.link_name.removeprefix("lnk_").removeprefix("t_lnk_")
            t_slug = sb.table_id.split(":", 1)[-1].split(".")[-1]
            if sb.source_id:
                base = (
                    f"esat_{slug}_{sb.source_id}_{t_slug}"
                    if src_cnts.get(sb.source_id, 0) > 1
                    else f"esat_{slug}_{sb.source_id}"
                )
            else:
                base = f"esat_{slug}_{t_slug}" if len(lnk.source_bindings) > 1 else f"esat_{slug}"
            sat_name, idx = base, 2
            while sat_name in used_names:
                sat_name, idx = f"{base}_{idx}", idx + 1
            used_names.add(sat_name)
            added_sats.append(
                DraftSatelliteSpec(
                    sat_name=sat_name,
                    sat_kind="ESAT",
                    parent_name=lnk.link_name,
                    source_bindings=[
                        DraftSourceBinding(
                            source_id=sb.source_id,
                            table_id=sb.table_id,
                            col_mappings={app_col: app_col} if app_col else {},
                        )
                    ],
                    is_bitemporal=app_col is not None,
                    applied_dts_col=app_col,
                    load_mode="APPEND_WITH_EFFECTIVITY",
                    driving_keys=[d_part.fk_col_name],
                    attributes=[],
                )
            )
            existing_keys.add((lnk.link_name, sb.source_id, sb.table_id))
            existing_parents.add(lnk.link_name)

        updated_links.append(
            lnk.model_copy(update={"driving_hub": driving_hub})
            if driving_hub != lnk.driving_hub
            else lnk
        )

    norm_sats = [
        sat.model_copy(
            update={
                "attributes": [
                    f.model_copy(update={"nullable": False})
                    if f.name in set(sat.child_dependent_keys)
                    else f
                    for f in sat.attributes
                ]
            }
        )
        if (sat.sat_kind == "MAS" or bool(sat.child_dependent_keys)) and sat.child_dependent_keys
        else sat
        for sat in draft.satellites
    ]
    return draft.model_copy(
        update={"links": updated_links, "satellites": [*norm_sats, *added_sats]}
    )


apply_alg_a3_effectivity_sats = apply_alg_a3_esat_and_mas

__all__ = [
    "apply_alg_a2_ref_demotion",
    "apply_alg_a3_effectivity_sats",
    "apply_alg_a3_esat_and_mas",
    "apply_alg_a4_bkcc_and_sparse_fks",
]
