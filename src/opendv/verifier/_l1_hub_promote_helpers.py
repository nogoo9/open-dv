"""Private helpers for PROMOTE_HUB_BK_TO_IMMUTABLE (locked SAL routing and Link grain sync).

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from opendv.models.diagnostics import HorizonQuestion
from opendv.models.project import EvolutionLockSpec
from opendv.models.vault_ir import (
    BusinessKeyPart,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSourceBinding,
    DraftVaultIR,
    LinkParticipantSpec,
    hub_canonical_bk_cols,
)
from opendv.models.wire_dtos_vault import BucketAOperatorCall


def build_brk01_escalation(
    op: BucketAOperatorCall,
    title: str,
    prompt: str,
) -> HorizonQuestion:
    """Build a BRK-01 HorizonQuestion when Hub BK promotion cannot safely proceed."""
    from opendv.verifier._l1_bucket_a_hub_ops import escalate_question

    return escalate_question(op, "BRK-01", title, prompt)


def apply_locked_promote_hub_bk(
    draft: DraftVaultIR,
    call: BucketAOperatorCall,
    *,
    new_bk: str,
    source_id: str,
    cols_by_table: dict[str, set[str]],
    locks: EvolutionLockSpec | None = None,
) -> tuple[DraftVaultIR, list[str], list[HorizonQuestion]]:
    """Reroute locked Hub BK promotion onto canonical Hub + sal_* after validating columns."""
    assert call.target_hub is not None and call.table_id is not None
    canonical_hub = call.params.get("new_hub_name") or f"{call.target_hub}_canonical"
    sal_name = call.params.get("sal_name") or f"sal_{call.target_hub.removeprefix('hub_')}"
    hubs = list(draft.hubs)
    links = list(draft.links)
    target_hub_spec = next((h for h in hubs if h.hub_name == call.target_hub), None)
    hub_bindings_by_tbl = (
        {sb.table_id: sb for sb in target_hub_spec.source_bindings}
        if target_hub_spec is not None
        else {}
    )
    old_bks: list[str] = []
    if target_hub_spec is not None:
        old_bks = list(
            dict.fromkeys(
                [
                    bk.col_name
                    for bk in target_hub_spec.business_keys
                    if bk.table_id == call.table_id
                ]
                or [bk.col_name for bk in target_hub_spec.business_keys]
            )
        )
    dup_src_cols = [call.source_column] if call.source_column else (old_bks or [new_bk])
    sal_source_id = source_id
    sal_table_id = call.table_id

    has_schema = bool(draft.column_triage) or call.table_id in hub_bindings_by_tbl
    if has_schema:
        call_cols = set(cols_by_table.get(call.table_id, set()))
        if call.table_id in hub_bindings_by_tbl:
            call_cols.update(hub_bindings_by_tbl[call.table_id].col_mappings.values())
        if new_bk not in call_cols:
            return (
                draft,
                [],
                [
                    build_brk01_escalation(
                        call,
                        f"Locked Hub {call.target_hub} promotion missing {new_bk} "
                        f"on {call.table_id}",
                        f"Cannot promote {call.target_hub} from {call.table_id}: "
                        f"column {new_bk} does not exist on {call.table_id}.",
                    )
                ],
            )
        missing_dup = [c for c in dup_src_cols if c not in call_cols]
        if missing_dup:
            bridge_sb = None
            bridge_bks: list[str] = []
            if call.source_column is None and target_hub_spec is not None:
                for sb in target_hub_spec.source_bindings:
                    sb_bks = [
                        bk.col_name
                        for bk in target_hub_spec.business_keys
                        if bk.table_id == sb.table_id
                    ]
                    sb_cols = set(cols_by_table.get(sb.table_id, set())) | set(
                        sb.col_mappings.values()
                    )
                    if new_bk in sb_cols and sb_bks and all(c in sb_cols for c in sb_bks):
                        bridge_sb = sb
                        bridge_bks = list(dict.fromkeys(sb_bks))
                        break
            if bridge_sb is not None:
                sal_source_id = bridge_sb.source_id
                sal_table_id = bridge_sb.table_id
                dup_src_cols = bridge_bks
            else:
                return (
                    draft,
                    [],
                    [
                        build_brk01_escalation(
                            call,
                            f"Locked Hub {call.target_hub} promotion missing duplicate BK "
                            f"on {call.table_id}",
                            f"Cannot build SAME_AS Link {sal_name} on {call.table_id}: "
                            f"missing column(s) {', '.join(missing_dup)} needed to link "
                            f"{new_bk} to locked {call.target_hub}.",
                        )
                    ],
                )

    if target_hub_spec is None:
        return (
            draft,
            [],
            [
                build_brk01_escalation(
                    call,
                    f"Locked Hub {call.target_hub} missing from draft",
                    f"Cannot build SAME_AS Link {sal_name}: "
                    f"locked Hub {call.target_hub} is absent.",
                )
            ],
        )

    if sal_table_id not in hub_bindings_by_tbl:
        target_bk_cols = list(dict.fromkeys(bk.col_name for bk in target_hub_spec.business_keys))
        sal_cols = cols_by_table.get(sal_table_id, set())
        has_sal_cols = (not draft.column_triage and sal_table_id not in cols_by_table) or all(
            c in sal_cols for c in target_bk_cols
        )
        if not target_bk_cols or dup_src_cols != target_bk_cols or not has_sal_cols:
            return (
                draft,
                [],
                [
                    build_brk01_escalation(
                        call,
                        f"Locked Hub {call.target_hub} unbound on {sal_table_id}",
                        f"Cannot bind locked {call.target_hub} to {sal_table_id} for {sal_name}: "
                        f"requires matching BK column(s) {', '.join(target_bk_cols) or new_bk}.",
                    )
                ],
            )
        ref_bks = {bk.col_name: bk for bk in target_hub_spec.business_keys}
        added_target_bks = [
            BusinessKeyPart(
                table_id=sal_table_id,
                col_name=c,
                source_id=sal_source_id,
                key_tier=ref_bks[c].key_tier,
                bkcc_value=ref_bks[c].bkcc_value,
                bkcc_col=ref_bks[c].bkcc_col,
            )
            for c in target_bk_cols
        ]
        added_target_sb = DraftSourceBinding(
            source_id=sal_source_id,
            table_id=sal_table_id,
            col_mappings={c: c for c in target_bk_cols},
        )
        updated_target = target_hub_spec.model_copy(
            update={
                "business_keys": [*target_hub_spec.business_keys, *added_target_bks],
                "source_bindings": [*target_hub_spec.source_bindings, added_target_sb],
            }
        )
        hubs = [updated_target if h.hub_name == call.target_hub else h for h in hubs]

    canon_bks = [
        BusinessKeyPart(
            table_id=call.table_id,
            col_name=new_bk,
            source_id=source_id,
            key_tier="TIER_1_GLOBAL_STANDARD",
            bkcc_value="GLOBAL",
        )
    ]
    canon_bindings = [
        DraftSourceBinding(
            source_id=source_id,
            table_id=call.table_id,
            col_mappings={new_bk: new_bk},
        )
    ]
    if sal_table_id != call.table_id:
        canon_bks.append(
            BusinessKeyPart(
                table_id=sal_table_id,
                col_name=new_bk,
                source_id=sal_source_id,
                key_tier="TIER_1_GLOBAL_STANDARD",
                bkcc_value="GLOBAL",
            )
        )
        canon_bindings.append(
            DraftSourceBinding(
                source_id=sal_source_id,
                table_id=sal_table_id,
                col_mappings={new_bk: new_bk},
            )
        )
    existing_canon = next((h for h in hubs if h.hub_name == canonical_hub), None)
    if existing_canon is None:
        hubs.append(
            DraftHubSpec(
                hub_name=canonical_hub,
                business_keys=canon_bks,
                key_tier="TIER_1_GLOBAL_STANDARD",
                source_bindings=canon_bindings,
            )
        )
    else:
        existing_canon_bks = hub_canonical_bk_cols(existing_canon)
        existing_bk_keys = {(bk.table_id, bk.col_name) for bk in existing_canon.business_keys}
        missing_bks = [bk for bk in canon_bks if (bk.table_id, bk.col_name) not in existing_bk_keys]
        existing_bind_tids = {sb.table_id for sb in existing_canon.source_bindings}
        missing_bindings = [
            sb for sb in canon_bindings if sb.table_id not in existing_bind_tids
        ]
        canon_locked_bad = (
            locks is not None
            and canonical_hub in locks.locked_hub_bks
            and (
                sorted(set(locks.locked_hub_bks[canonical_hub])) != [new_bk]
                or bool(missing_bks or missing_bindings)
            )
        )
        if (existing_canon_bks and existing_canon_bks != [new_bk]) or canon_locked_bad:
            return (
                draft,
                [],
                [
                    build_brk01_escalation(
                        call,
                        f"Canonical Hub {canonical_hub} cannot accept {new_bk}",
                        f"Existing canonical Hub {canonical_hub} is locked or uses incompatible "
                        f"business key(s) ({', '.join(existing_canon_bks) or 'locked'}).",
                    )
                ],
            )
        if missing_bks or missing_bindings:
            merged_canon = existing_canon.model_copy(
                update={
                    "business_keys": [*existing_canon.business_keys, *missing_bks],
                    "source_bindings": [*existing_canon.source_bindings, *missing_bindings],
                    "is_planned_concept": False,
                }
            )
            hubs = [merged_canon if h.hub_name == canonical_hub else h for h in hubs]
    if not any(lnk.link_name == sal_name for lnk in links):
        slug = call.target_hub.removeprefix("hub_")
        master_fk = f"hk_{slug}_master"
        dup_fk = f"hk_{slug}_duplicate"
        canonical_hk = f"hk_{canonical_hub.removeprefix('hub_')}"
        target_hk = f"hk_{slug}"
        sal_col_mappings = {
            master_fk: canonical_hk,
            dup_fk: target_hk,
            new_bk: new_bk,
            **{c: c for c in dup_src_cols},
        }
        links.append(
            DraftLinkSpec(
                link_name=sal_name,
                link_kind="SAME_AS",
                participants=[
                    LinkParticipantSpec(
                        hub_name=canonical_hub,
                        role_name="MASTER",
                        fk_col_name=master_fk,
                        source_col_names=[new_bk],
                    ),
                    LinkParticipantSpec(
                        hub_name=call.target_hub,
                        role_name="DUPLICATE",
                        fk_col_name=dup_fk,
                        source_col_names=dup_src_cols,
                    ),
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=sal_source_id,
                        table_id=sal_table_id,
                        col_mappings=sal_col_mappings,
                    )
                ],
            )
        )
    return draft.model_copy(update={"hubs": hubs, "links": links}), [canonical_hub, sal_name], []


def sync_unlocked_hub_links(
    links: list[DraftLinkSpec],
    call: BucketAOperatorCall,
    locks: EvolutionLockSpec | None,
    *,
    new_bk: str,
    cols_by_table: dict[str, set[str]],
    promoted_hub_bindings: dict[str, DraftSourceBinding],
) -> tuple[list[DraftLinkSpec], list[HorizonQuestion]]:
    """Update attached Link participants and col_mappings to match an unlocked Hub's new BK."""
    updated_links: list[DraftLinkSpec] = []
    for lnk in links:
        matching_parts = [p for p in lnk.participants if p.hub_name == call.target_hub]
        if not matching_parts:
            updated_links.append(lnk)
            continue
        lnk_tids = [sb.table_id for sb in lnk.source_bindings]
        should_update = not lnk_tids or all(
            tid in promoted_hub_bindings
            or (bool(cols_by_table.get(tid)) and new_bk in cols_by_table[tid])
            for tid in lnk_tids
        )
        if not should_update:
            updated_links.append(lnk)
            continue
        if locks is not None and lnk.link_name in locks.locked_links:
            return (
                links,
                [
                    build_brk01_escalation(
                        call,
                        f"Locked Link {lnk.link_name} prevents Hub {call.target_hub} promotion",
                        f"Cannot update Link {lnk.link_name} grain to {new_bk}: Link is locked.",
                    )
                ],
            )
        if len(matching_parts) > 1:
            return (
                links,
                [
                    build_brk01_escalation(
                        call,
                        f"Self-referencing Link {lnk.link_name} prevents Hub "
                        f"{call.target_hub} promotion",
                        f"Cannot collapse distinct role columns on self-referencing Link "
                        f"{lnk.link_name} to {new_bk}.",
                    )
                ],
            )
        old_part_cols = {col for p in matching_parts for col in p.source_col_names}
        kept_cols = (
            {
                col
                for p in lnk.participants
                if p.hub_name != call.target_hub
                for col in p.source_col_names
            }
            | set(lnk.degenerate_keys)
            | {a.name for a in lnk.attributes}
        )
        new_parts = [
            p.model_copy(update={"source_col_names": [new_bk]})
            if p.hub_name == call.target_hub
            else p
            for p in lnk.participants
        ]
        new_bindings: list[DraftSourceBinding] = []
        for sb in lnk.source_bindings:
            new_maps = {
                k: v
                for k, v in sb.col_mappings.items()
                if k not in old_part_cols or k in kept_cols
            }
            hub_sb = promoted_hub_bindings.get(sb.table_id)
            mapped_new = (
                hub_sb.col_mappings.get(new_bk, new_bk)
                if hub_sb is not None
                else sb.col_mappings.get(new_bk, new_bk)
            )
            new_maps[new_bk] = mapped_new
            new_bindings.append(sb.model_copy(update={"col_mappings": new_maps}))
        updated_links.append(
            lnk.model_copy(update={"participants": new_parts, "source_bindings": new_bindings})
        )
    return updated_links, []


__all__ = ["apply_locked_promote_hub_bk", "sync_unlocked_hub_links"]
