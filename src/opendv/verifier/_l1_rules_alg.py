"""Pure L1 deterministic codemods: Alg A.2 (ref_*), Alg A.3 (esat/mas), and Alg A.4 (BKCC/FK).

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from opendv.models.diagnostics import KeyTierEnum
from opendv.models.ir_kernel import resolve_bkcc
from opendv.models.project import EvolutionLockSpec
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir import (
    BusinessKeyPart,
    DraftField,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
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


def _build_is_not_null_ast(cols: Sequence[str]) -> PredicateNode | None:
    clean = [c for c in cols if c]
    if not clean:
        return None
    leaves: list[PredicateNode] = [PredicateLeaf(op="IS_NOT_NULL", column=c) for c in clean]
    if len(leaves) == 1:
        return leaves[0]
    return PredicateBranch(op="AND", children=leaves)


def _normalize_sal_participants(parts: list[LinkParticipantSpec]) -> list[LinkParticipantSpec]:
    if len(parts) != 2:
        return parts
    p0, p1 = parts[0], parts[1]
    role0 = p0.role_name.strip().upper() or "MASTER"
    role1 = p1.role_name.strip().upper() or "DUPLICATE"
    if role0 == "DUPLICATE" and role1 == "MASTER":
        p0, p1 = p1, p0
        role0, role1 = role1, role0
    if role0 == role1:
        role0, role1 = "MASTER", "DUPLICATE"
    fk0 = p0.fk_col_name or "hk_master"
    fk1 = p1.fk_col_name or "hk_duplicate"
    if fk0 == fk1:
        fk0, fk1 = f"{fk0}_master", f"{fk1}_duplicate"
    return [
        p0.model_copy(update={"role_name": role0, "fk_col_name": fk0}),
        p1.model_copy(update={"role_name": role1, "fk_col_name": fk1}),
    ]


def apply_alg_a4_bkcc_and_sparse_fks(
    draft: DraftVaultIR,
    profiles: Sequence[SourceProfile],
    locks: EvolutionLockSpec | None = None,
    *,
    options: L1CodemodOptions | None = None,
) -> tuple[DraftVaultIR, dict[str, PredicateNode]]:
    """Alg A.4: normalize 3-tier BKCC, canonicalize sal_*, and split/filter sparse FKs."""
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
            tier: KeyTierEnum = bk.key_tier
            if bk.col_name.lower() in _TIER1_HINTS and tier != "TIER_1_GLOBAL_STANDARD":
                tier = "TIER_1_GLOBAL_STANDARD"
            if tier == "TIER_1_GLOBAL_STANDARD":
                resolved = "GLOBAL"
                bkcc_col: str | None = None
            elif tier == "TIER_2_ENTERPRISE_SOR":
                resolved = resolve_bkcc(tier, bk.bkcc_value or bk.source_id)
                bkcc_col = bk.bkcc_col
            else:
                candidate = bk.bkcc_value.strip() or bk.source_id.strip() or bk.table_id.strip()
                resolved = resolve_bkcc(tier, candidate or "LOCAL")
                bkcc_col = bk.bkcc_col
            new_bks.append(
                bk.model_copy(
                    update={"key_tier": tier, "bkcc_value": resolved, "bkcc_col": bkcc_col}
                )
            )
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
    seen_link_names: set[str] = {lnk.link_name for lnk in draft.links}

    for lnk in draft.links:
        if lnk.link_kind == "SAME_AS":
            norm_parts = _normalize_sal_participants(lnk.participants)
            updated_links.append(lnk.model_copy(update={"participants": norm_parts}))
            continue
        if lnk.link_kind not in ("STANDARD", "TRANSACTIONAL"):
            updated_links.append(lnk)
            continue

        binding_tables = [b.table_id for b in lnk.source_bindings]
        mandatory: list[LinkParticipantSpec] = []
        sparse: list[tuple[LinkParticipantSpec, list[str]]] = []

        for part in lnk.participants:
            src_cols = list(part.source_col_names)
            if not src_cols and lnk.source_bindings:
                mapped = lnk.source_bindings[0].col_mappings.get(part.fk_col_name)
                if mapped:
                    src_cols = [mapped]
            min_nn = 1.0
            for tbl_id in binding_tables:
                for sc in src_cols:
                    prof = col_idx.get((tbl_id, sc))
                    if prof is not None:
                        min_nn = min(min_nn, 1.0 - prof.null_ratio)
            if min_nn < opts.mandatory_fk_non_null_ratio:
                sparse.append((part, src_cols))
            else:
                mandatory.append(part)

        if not sparse:
            updated_links.append(lnk)
            continue

        # BUG-02: binary Links with sparse FKs get IS_NOT_NULL filter on LoadSemanticsSpec
        if len(lnk.participants) <= 2 or lnk.link_name in locked_links or len(mandatory) < 2:
            filter_cols: list[str] = []
            for _, cols in sparse:
                filter_cols.extend(cols)
            ast_node = _build_is_not_null_ast(filter_cols)
            if ast_node is not None:
                link_filters[lnk.link_name] = ast_node
            updated_links.append(lnk)
            continue

        # N-ary Link (>= 3) with >= 2 mandatory + >= 1 sparse: split sparse into binary Links
        updated_links.append(lnk.model_copy(update={"participants": mandatory}))
        anchor = next(
            (p for p in mandatory if p.hub_name == lnk.driving_hub),
            mandatory[0],
        )
        for sp_part, sp_cols in sparse:
            slug = (sp_part.role_name or sp_part.hub_name.removeprefix("hub_")).lower()
            base_split = f"{lnk.link_name}_{slug}"
            split_name = base_split
            suffix_num = 2
            while split_name in seen_link_names:
                split_name = f"{base_split}_{suffix_num}"
                suffix_num += 1
            seen_link_names.add(split_name)
            updated_links.append(
                DraftLinkSpec(
                    link_name=split_name,
                    link_kind=lnk.link_kind,
                    participants=[anchor, sp_part],
                    driving_hub=anchor.hub_name,
                    degenerate_keys=[],
                    source_bindings=list(lnk.source_bindings),
                    attributes=[],
                )
            )
            ast_node = _build_is_not_null_ast(sp_cols)
            if ast_node is not None:
                link_filters[split_name] = ast_node

    return draft.model_copy(update={"hubs": updated_hubs, "links": updated_links}), link_filters


def apply_alg_a3_esat_and_mas(
    draft: DraftVaultIR,
    fk_turnover_by_table: Mapping[str, Mapping[str, float]] | None = None,
) -> DraftVaultIR:
    """Alg A.3: inject esat_* driving keys on turning STANDARD Links and normalize mas_*."""
    turnover = fk_turnover_by_table or {}
    existing_esat_parents = {
        s.parent_name for s in draft.satellites if s.sat_kind == "ESAT"
    }
    updated_links: list[DraftLinkSpec] = []
    added_sats: list[DraftSatelliteSpec] = []

    for lnk in draft.links:
        if lnk.link_kind != "STANDARD" or not lnk.source_bindings:
            updated_links.append(lnk)
            continue
        tbl_id = lnk.source_bindings[0].table_id
        tmap = turnover.get(tbl_id, {})
        best_hub: str | None = None
        best_fk: str | None = None
        best_rate = 0.0
        for part in lnk.participants:
            for sc in part.source_col_names or [part.fk_col_name]:
                rate = float(tmap.get(sc, 0.0))
                if rate > best_rate:
                    best_rate = rate
                    best_hub = part.hub_name
                    best_fk = part.fk_col_name
        if best_rate > 0.0 and best_hub is not None and best_fk is not None:
            updated_links.append(lnk.model_copy(update={"driving_hub": best_hub}))
            if lnk.link_name not in existing_esat_parents:
                slug = lnk.link_name.removeprefix("lnk_")
                added_sats.append(
                    DraftSatelliteSpec(
                        sat_name=f"esat_{slug}",
                        sat_kind="ESAT",
                        parent_name=lnk.link_name,
                        source_bindings=list(lnk.source_bindings[:1]),
                        load_mode="APPEND_WITH_EFFECTIVITY",
                        driving_keys=[best_fk],
                        attributes=[
                            DraftField(
                                name="effective_from",
                                logical_type="TIMESTAMP_UTC_US",
                                nullable=False,
                                is_pii=False,
                            ),
                            DraftField(
                                name="effective_to",
                                logical_type="TIMESTAMP_UTC_US",
                                nullable=True,
                                is_pii=False,
                            ),
                        ],
                    )
                )
                existing_esat_parents.add(lnk.link_name)
        else:
            updated_links.append(lnk)

    normalized_sats: list[DraftSatelliteSpec] = []
    for sat in draft.satellites:
        if sat.sat_kind == "MAS" and sat.child_dependent_keys:
            cdk_set = set(sat.child_dependent_keys)
            attrs = [
                f.model_copy(update={"nullable": False}) if f.name in cdk_set else f
                for f in sat.attributes
            ]
            normalized_sats.append(sat.model_copy(update={"attributes": attrs}))
        else:
            normalized_sats.append(sat)

    return draft.model_copy(
        update={
            "links": updated_links,
            "satellites": [*normalized_sats, *added_sats],
        }
    )


__all__ = [
    "apply_alg_a2_ref_demotion",
    "apply_alg_a3_esat_and_mas",
    "apply_alg_a4_bkcc_and_sparse_fks",
]
