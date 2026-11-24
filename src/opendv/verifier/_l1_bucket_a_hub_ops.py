"""BucketAOperatorCall Hub promotion operators (PROMOTE_COLUMN_TO_HUB, PROMOTE_HUB_BK_TO_IMMUTABLE).

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from opendv.models.diagnostics import HorizonOptionSpec, HorizonQuestion, KeyTierEnum
from opendv.models.project import EvolutionLockSpec
from opendv.models.vault_ir import (
    BusinessKeyPart,
    DraftField,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSourceBinding,
    DraftVaultIR,
    LinkParticipantSpec,
)
from opendv.models.wire_dtos_vault import BucketAOperatorCall
from opendv.verifier._l1_hub_promote_helpers import (
    apply_locked_promote_hub_bk,
    sync_unlocked_hub_links,
)


def escalate_question(
    op: BucketAOperatorCall,
    brk_code: str,
    title: str,
    prompt: str,
) -> HorizonQuestion:
    """Create a HorizonQuestion escalation when a locked entity cannot be rerouted."""
    return HorizonQuestion(
        question_id=f"HZN-{op.operator}-{op.target_hub or op.sat_name or 'IR'}",
        brk_code=brk_code,
        title=title,
        prompt=prompt,
        options=[
            HorizonOptionSpec(
                option_id="OPT_COMPANION",
                label="Create additive companion or new canonical entity",
                description="Preserve deployed locked keys/hash_diffs and extend additively.",
                implications="Zero re-hash; downstream Z4 views union/join companion.",
            ),
            HorizonOptionSpec(
                option_id="OPT_DEFER",
                label="Keep existing locked schema unchanged for this wave",
                description="Defer structural change to a future breaking wave.",
                implications="Maintains Wave N backward compatibility.",
            ),
        ],
        impact_summary=f"Locked entity prevented in-place {op.operator}.",
    )


def _parse_key_tier(raw_tier: str) -> KeyTierEnum:
    if raw_tier == "TIER_1_GLOBAL_STANDARD":
        return "TIER_1_GLOBAL_STANDARD"
    if raw_tier == "TIER_3_LOCAL_SCOPED":
        return "TIER_3_LOCAL_SCOPED"
    return "TIER_2_ENTERPRISE_SOR"


def apply_promote_col_to_hub(
    draft: DraftVaultIR,
    call: BucketAOperatorCall,
    locks: EvolutionLockSpec | None,
) -> tuple[DraftVaultIR, list[str], list[HorizonQuestion]]:
    """Promote a Satellite attribute into a new Hub + Link."""
    new_hub = call.params.get("new_hub_name", "").strip()
    new_link = call.params.get("new_link_name", "").strip()
    key_tier = _parse_key_tier(call.params.get("key_tier", "TIER_2_ENTERPRISE_SOR"))
    if (
        not call.sat_name
        or not call.table_id
        or not call.source_column
        or not new_hub
        or not new_link
    ):
        raise ValueError("PROMOTE_COLUMN_TO_HUB missing required locators/params")

    is_locked = locks is not None and call.sat_name in locks.locked_sat_hash_diffs
    if is_locked and call.params.get("remove_from_sat") == "true":
        return (
            draft,
            [],
            [
                escalate_question(
                    call,
                    "BRK-04",
                    f"Locked Satellite column removal on {call.sat_name}",
                    f"Cannot remove {call.source_column} from locked {call.sat_name}.",
                )
            ],
        )

    src_sat = next((s for s in draft.satellites if s.sat_name == call.sat_name), None)
    parent_hub = src_sat.parent_name if src_sat else (call.target_hub or "")
    source_id = (
        src_sat.source_bindings[0].source_id
        if (src_sat and src_sat.source_bindings)
        else "src"
    )
    bk_part = BusinessKeyPart(
        table_id=call.table_id,
        col_name=call.source_column,
        source_id=source_id,
        key_tier=key_tier,
        bkcc_value="GLOBAL" if key_tier == "TIER_1_GLOBAL_STANDARD" else source_id.upper(),
    )
    hubs = list(draft.hubs)
    if not any(h.hub_name == new_hub for h in hubs):
        hubs.append(
            DraftHubSpec(
                hub_name=new_hub,
                business_keys=[bk_part],
                key_tier=key_tier,
                source_bindings=[
                    DraftSourceBinding(
                        source_id=source_id,
                        table_id=call.table_id,
                        col_mappings={call.source_column: call.source_column},
                    )
                ],
            )
        )
    links = list(draft.links)
    if parent_hub and not any(lnk.link_name == new_link for lnk in links):
        parent_hub_spec = next((h for h in hubs if h.hub_name == parent_hub), None)
        parent_src_cols: list[str] = []
        if parent_hub_spec is not None:
            parent_src_cols = list(
                dict.fromkeys(
                    bk.col_name
                    for bk in parent_hub_spec.business_keys
                    if bk.table_id == call.table_id
                )
            )
            if not parent_src_cols:
                parent_src_cols = list(
                    dict.fromkeys(bk.col_name for bk in parent_hub_spec.business_keys)
                )
        link_col_maps = {c: c for c in [*parent_src_cols, call.source_column]}
        links.append(
            DraftLinkSpec(
                link_name=new_link,
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(
                        hub_name=parent_hub,
                        role_name="PARENT",
                        fk_col_name=f"hk_{parent_hub.removeprefix('hub_')}",
                        source_col_names=parent_src_cols,
                    ),
                    LinkParticipantSpec(
                        hub_name=new_hub,
                        role_name="PROMOTED",
                        fk_col_name=f"hk_{new_hub.removeprefix('hub_')}",
                        source_col_names=[call.source_column],
                    ),
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=source_id,
                        table_id=call.table_id,
                        col_mappings=link_col_maps,
                    )
                ],
            )
        )
    sats = list(draft.satellites)
    if not is_locked:
        sats = [
            s.model_copy(
                update={"attributes": [f for f in s.attributes if f.name != call.source_column]}
            )
            if s.sat_name == call.sat_name
            else s
            for s in sats
        ]
    return draft.model_copy(update={"hubs": hubs, "links": links, "satellites": sats}), [], []


def apply_promote_hub_bk_immutable(
    draft: DraftVaultIR,
    call: BucketAOperatorCall,
    locks: EvolutionLockSpec | None,
) -> tuple[DraftVaultIR, list[str], list[HorizonQuestion]]:
    """Promote mutable Hub BK to immutable, rerouting to canonical Hub + sal_* when locked."""
    new_bk = call.params.get("new_bk_col", "").strip()
    source_id = call.params.get("source_id", "").strip()
    demote_old = call.params.get("demote_old_bk_to_sat", "false").lower() == "true"
    if not call.target_hub or not call.table_id or not new_bk or not source_id:
        raise ValueError("PROMOTE_HUB_BK_TO_IMMUTABLE missing required locators/params")

    is_locked = locks is not None and call.target_hub in locks.locked_hub_bks
    rerouted: list[str] = []
    hubs = list(draft.hubs)
    links = list(draft.links)
    sats = list(draft.satellites)
    cols_by_table: dict[str, set[str]] = {}
    for entry in draft.column_triage:
        cols_by_table.setdefault(entry.table_id, set()).add(entry.col_name)

    if is_locked:
        return apply_locked_promote_hub_bk(
            draft,
            call,
            new_bk=new_bk,
            source_id=source_id,
            cols_by_table=cols_by_table,
            locks=locks,
        )

    ext_source_by_tbl = {
        sb.table_id: sb.source_id
        for sat in sats
        if sat.parent_name == call.target_hub
        for sb in sat.source_bindings
    }
    ext_source_by_tbl.update(
        {
            sb.table_id: sb.source_id
            for lnk in links
            if any(p.hub_name == call.target_hub for p in lnk.participants)
            for sb in lnk.source_bindings
        }
    )
    link_tables = [
        sb.table_id
        for lnk in links
        if any(p.hub_name == call.target_hub for p in lnk.participants)
        for sb in lnk.source_bindings
    ]
    sat_tables = [
        sb.table_id
        for sat in sats
        if sat.parent_name == call.target_hub
        for sb in sat.source_bindings
    ]

    updated_hubs: list[DraftHubSpec] = []
    demoted_bks: list[BusinessKeyPart] = []
    promoted_hub_bindings: dict[str, DraftSourceBinding] = {}
    for h in hubs:
        if h.hub_name != call.target_hub:
            updated_hubs.append(h)
            continue
        bound_tables = list(dict.fromkeys(sb.table_id for sb in h.source_bindings))
        all_tables = list(
            dict.fromkeys(
                [
                    *bound_tables,
                    *link_tables,
                    *sat_tables,
                    *([call.table_id] if draft.column_triage else []),
                ]
            )
        )
        is_multi_table = len(bound_tables) > 1 or (
            len(all_tables) > 1 and bool(draft.column_triage)
        )
        bindings_by_tbl = {sb.table_id: sb for sb in h.source_bindings}
        if is_multi_table:
            covers_all = all(
                new_bk in cols_by_table.get(tid, set())
                or (tid in bindings_by_tbl and new_bk in bindings_by_tbl[tid].col_mappings)
                for tid in all_tables
            )
            if not covers_all:
                return (
                    draft,
                    [],
                    [
                        escalate_question(
                            call,
                            "BRK-01",
                            f"Multi-table Hub {call.target_hub} promotion requires {new_bk}",
                            f"Cannot replace BusinessKey on multi-table Hub {call.target_hub}: "
                            f"{new_bk} does not cover all bound tables/Links "
                            f"({', '.join(all_tables)}).",
                        )
                    ],
                )
            demoted_bks.extend(bk for bk in h.business_keys if bk.col_name != new_bk)
            multi_bks: list[BusinessKeyPart] = []
            multi_bindings: list[DraftSourceBinding] = []
            seen_tids: set[str] = set()
            for sb in h.source_bindings:
                if sb.table_id in seen_tids:
                    continue
                seen_tids.add(sb.table_id)
                mapped_src = sb.col_mappings.get(new_bk, new_bk)
                multi_bks.append(
                    BusinessKeyPart(
                        table_id=sb.table_id,
                        col_name=new_bk,
                        source_id=sb.source_id,
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                )
                multi_bindings.append(
                    DraftSourceBinding(
                        source_id=sb.source_id,
                        table_id=sb.table_id,
                        col_mappings={new_bk: mapped_src},
                        doc_citations=list(sb.doc_citations),
                        mcp_evidence=list(sb.mcp_evidence),
                    )
                )
            if call.table_id not in seen_tids:
                seen_tids.add(call.table_id)
                multi_bks.append(
                    BusinessKeyPart(
                        table_id=call.table_id,
                        col_name=new_bk,
                        source_id=source_id,
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                )
                multi_bindings.append(
                    DraftSourceBinding(
                        source_id=source_id,
                        table_id=call.table_id,
                        col_mappings={new_bk: new_bk},
                    )
                )
            for tid in (*link_tables, *sat_tables):
                if tid in seen_tids:
                    continue
                seen_tids.add(tid)
                tid_src = ext_source_by_tbl.get(tid, source_id)
                multi_bks.append(
                    BusinessKeyPart(
                        table_id=tid,
                        col_name=new_bk,
                        source_id=tid_src,
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                )
                multi_bindings.append(
                    DraftSourceBinding(
                        source_id=tid_src,
                        table_id=tid,
                        col_mappings={new_bk: new_bk},
                    )
                )
            promoted_hub_bindings = {sb.table_id: sb for sb in multi_bindings}
            updated_hubs.append(
                h.model_copy(
                    update={
                        "key_tier": "TIER_1_GLOBAL_STANDARD",
                        "business_keys": multi_bks,
                        "source_bindings": multi_bindings,
                    }
                )
            )
            continue
        if draft.column_triage and (
            new_bk not in cols_by_table.get(call.table_id, set())
            and not (
                call.table_id in bindings_by_tbl
                and new_bk in bindings_by_tbl[call.table_id].col_mappings
            )
        ):
            return (
                draft,
                [],
                [
                    escalate_question(
                        call,
                        "BRK-01",
                        f"Hub {call.target_hub} promotion missing {new_bk} on {call.table_id}",
                        f"Cannot replace BusinessKey on {call.target_hub}: "
                        f"{new_bk} does not exist on {call.table_id}.",
                    )
                ],
            )
        demoted_bks.extend(bk for bk in h.business_keys if bk.col_name != new_bk)
        single_binding = DraftSourceBinding(
            source_id=source_id,
            table_id=call.table_id,
            col_mappings={
                new_bk: (
                    bindings_by_tbl[call.table_id].col_mappings.get(new_bk, new_bk)
                    if call.table_id in bindings_by_tbl
                    else new_bk
                )
            },
        )
        promoted_hub_bindings = {call.table_id: single_binding}
        updated_hubs.append(
            h.model_copy(
                update={
                    "key_tier": "TIER_1_GLOBAL_STANDARD",
                    "business_keys": [
                        BusinessKeyPart(
                            table_id=call.table_id,
                            col_name=new_bk,
                            source_id=source_id,
                            key_tier="TIER_1_GLOBAL_STANDARD",
                            bkcc_value="GLOBAL",
                        )
                    ],
                    "source_bindings": [single_binding],
                }
            )
        )

    updated_links, link_escs = sync_unlocked_hub_links(
        links,
        call,
        locks,
        new_bk=new_bk,
        cols_by_table=cols_by_table,
        promoted_hub_bindings=promoted_hub_bindings,
    )
    if link_escs:
        return draft, [], link_escs
    if demote_old and demoted_bks:
        demoted_cols = list(dict.fromkeys(bk.col_name for bk in demoted_bks))
        bk_tables_by_col: dict[str, set[str]] = {}
        for bk in demoted_bks:
            bk_tables_by_col.setdefault(bk.col_name, set()).add(bk.table_id)
        updated_sats = []
        for s in sats:
            if s.parent_name != call.target_hub:
                updated_sats.append(s)
                continue
            sat_tids = [sb.table_id for sb in s.source_bindings]
            applicable = (
                [
                    c
                    for c in demoted_cols
                    if all(
                        (c in cols_by_table[tid])
                        if cols_by_table.get(tid)
                        else (tid in bk_tables_by_col.get(c, set()))
                        for tid in sat_tids
                    )
                ]
                if sat_tids
                else demoted_cols
            )
            existing = {f.name for f in s.attributes}
            extra = [
                DraftField(name=c, logical_type="UTF8_STRING", nullable=True, is_pii=False)
                for c in applicable
                if c not in existing
            ]
            updated_sats.append(
                s.model_copy(update={"attributes": [*s.attributes, *extra]}) if extra else s
            )
        sats = updated_sats
    return (
        draft.model_copy(update={"hubs": updated_hubs, "links": updated_links, "satellites": sats}),
        rerouted,
        [],
    )


__all__ = [
    "apply_promote_col_to_hub",
    "apply_promote_hub_bk_immutable",
    "escalate_question",
]
