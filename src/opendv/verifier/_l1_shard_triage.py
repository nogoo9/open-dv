"""Deterministic DraftShardIR merge (>40 tables), provenance map, and triage injection.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from opendv.models.diagnostics import (
    AntiPatternJustification,
    ClarificationQuestion,
    KeyTierEnum,
)
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir import (
    ColumnTriageEntry,
    DraftField,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
    DraftShardIR,
    DraftSourceBinding,
    DraftVaultIR,
    LinkParticipantSpec,
    SatKind,
)

_TIER_RANK: dict[KeyTierEnum, int] = {
    "TIER_1_GLOBAL_STANDARD": 1,
    "TIER_2_ENTERPRISE_SOR": 2,
    "TIER_3_LOCAL_SCOPED": 3,
}
_SAT_KIND_PRIORITY: dict[SatKind, int] = {
    "MAS": 1,
    "ESAT": 1,
    "PII": 1,
    "EXT": 1,
    "AI_PROVENANCE_SATELLITE": 1,
    "STANDARD": 2,
}
_DISPOSITION_RANK: dict[str, int] = {
    "MODELED": 1,
    "SYSTEM_METADATA": 2,
    "SYSTEM_SURROGATE_DROPPED": 3,
    "OMITTED_INTENTIONALLY": 4,
    "UNRESOLVED": 99,
}


def _merge_bindings(bindings: Sequence[DraftSourceBinding]) -> list[DraftSourceBinding]:
    by_key: dict[tuple[str, str], DraftSourceBinding] = {}
    for b in bindings:
        key = (b.source_id, b.table_id)
        if key not in by_key:
            by_key[key] = b.model_copy(deep=True)
            continue
        prev = by_key[key]
        merged_maps = {**prev.col_mappings, **b.col_mappings}
        cit_seen = {
            (c.doc_id, c.page_number, c.verbatim_quote, c.occurrence) for c in prev.doc_citations
        }
        merged_cits = list(prev.doc_citations)
        for c in b.doc_citations:
            ckey = (c.doc_id, c.page_number, c.verbatim_quote, c.occurrence)
            if ckey not in cit_seen:
                cit_seen.add(ckey)
                merged_cits.append(c)
        mcp_seen = {m.receipt_id for m in prev.mcp_evidence}
        merged_mcp = list(prev.mcp_evidence)
        for m in b.mcp_evidence:
            if m.receipt_id not in mcp_seen:
                mcp_seen.add(m.receipt_id)
                merged_mcp.append(m)
        by_key[key] = prev.model_copy(
            update={
                "col_mappings": merged_maps,
                "doc_citations": merged_cits,
                "mcp_evidence": merged_mcp,
            }
        )
    return [by_key[k] for k in sorted(by_key.keys())]


def _merge_fields(fields: Sequence[DraftField]) -> list[DraftField]:
    by_name: dict[str, DraftField] = {}
    for f in fields:
        if f.name not in by_name:
            by_name[f.name] = f
            continue
        prev = by_name[f.name]
        by_name[f.name] = prev.model_copy(
            update={
                "is_pii": prev.is_pii or f.is_pii,
                "nullable": prev.nullable or f.nullable,
                "classification_tag": prev.classification_tag or f.classification_tag,
            }
        )
    return [by_name[k] for k in sorted(by_name.keys())]


def _filter_shard_halos(
    shard: DraftShardIR,
    *,
    cluster_tables_by_id: Mapping[str, Sequence[str]] | None,
    boundary_neighbors_by_shard: Mapping[str, Sequence[str]] | None,
) -> DraftShardIR:
    owned = (
        set(cluster_tables_by_id.get(shard.cluster_id, ()))
        if cluster_tables_by_id is not None and shard.cluster_id in cluster_tables_by_id
        else set()
    )
    halo = (
        set(boundary_neighbors_by_shard.get(shard.cluster_id, ())) - owned
        if boundary_neighbors_by_shard is not None
        else set()
    )
    if not owned and not halo:
        return shard

    def _is_owned(tid: str) -> bool:
        if tid in halo:
            return False
        if owned:
            return tid in owned
        return True

    kept_hubs: list[DraftHubSpec] = []
    for h in shard.hubs:
        kb = [sb for sb in h.source_bindings if _is_owned(sb.table_id)]
        kbks = [bk for bk in h.business_keys if _is_owned(bk.table_id)]
        if h.source_bindings and not kb:
            continue
        if h.business_keys and not kbks and not h.is_planned_concept:
            continue
        kept_hubs.append(h.model_copy(update={"source_bindings": kb, "business_keys": kbks}))

    kept_links: list[DraftLinkSpec] = []
    for lnk in shard.links:
        kb = [sb for sb in lnk.source_bindings if _is_owned(sb.table_id)]
        if lnk.source_bindings and not kb:
            continue
        kept_links.append(lnk.model_copy(update={"source_bindings": kb}))

    kept_sats: list[DraftSatelliteSpec] = []
    for sat in shard.satellites:
        kb = [sb for sb in sat.source_bindings if _is_owned(sb.table_id)]
        if sat.source_bindings and not kb:
            continue
        kept_sats.append(sat.model_copy(update={"source_bindings": kb}))

    kept_triage = [e for e in shard.column_triage if _is_owned(e.table_id)]
    return shard.model_copy(
        update={
            "hubs": kept_hubs,
            "links": kept_links,
            "satellites": kept_sats,
            "column_triage": kept_triage,
        }
    )


def _merge_participant(prev: LinkParticipantSpec, nxt: LinkParticipantSpec) -> LinkParticipantSpec:
    merged_cols = list(dict.fromkeys([*prev.source_col_names, *nxt.source_col_names]))
    merged_null: bool | None = prev.is_nullable
    if merged_null is None:
        merged_null = nxt.is_nullable
    elif nxt.is_nullable is not None:
        merged_null = prev.is_nullable or nxt.is_nullable
    return prev.model_copy(
        update={
            "source_col_names": merged_cols,
            "target_hub_binding_table_id": (
                prev.target_hub_binding_table_id or nxt.target_hub_binding_table_id
            ),
            "is_nullable": merged_null,
            "key_tier": prev.key_tier or nxt.key_tier,
            "bkcc_value": prev.bkcc_value or nxt.bkcc_value,
            "bkcc_col": prev.bkcc_col or nxt.bkcc_col,
            "unknown_predicate": prev.unknown_predicate or nxt.unknown_predicate,
            "error_predicate": prev.error_predicate or nxt.error_predicate,
        }
    )


def build_entity_shard_provenance_map(
    shards: Sequence[DraftShardIR],
    *,
    cluster_tables_by_id: Mapping[str, Sequence[str]] | None = None,
    boundary_neighbors_by_shard: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, list[str]]:
    """Map each merged Hub, Link, and Satellite name to sorted owning cluster_id(s)."""
    prov: dict[str, set[str]] = {}
    for raw_shard in sorted(shards, key=lambda s: s.cluster_id):
        shard = _filter_shard_halos(
            raw_shard,
            cluster_tables_by_id=cluster_tables_by_id,
            boundary_neighbors_by_shard=boundary_neighbors_by_shard,
        )
        for h in shard.hubs:
            prov.setdefault(h.hub_name, set()).add(shard.cluster_id)
        for lnk in shard.links:
            prov.setdefault(lnk.link_name, set()).add(shard.cluster_id)
        for sat in shard.satellites:
            prov.setdefault(sat.sat_name, set()).add(shard.cluster_id)
    return {name: sorted(cids) for name, cids in sorted(prov.items())}


def validate_frozen_shared_hub_keys(
    prior_draft: DraftVaultIR,
    shard: DraftShardIR,
    provenance_map: Mapping[str, Sequence[str]],
) -> list[str]:
    """Return shared-Hub names whose business keys diverged from prior_draft."""
    prior_hubs = {h.hub_name: h for h in prior_draft.hubs}
    diverged: list[str] = []
    for h in shard.hubs:
        owners = provenance_map.get(h.hub_name, ())
        prior_h = prior_hubs.get(h.hub_name)
        if len(owners) <= 1 or prior_h is None:
            continue
        prior_sig = sorted(
            (bk.table_id, bk.col_name, bk.canonical_col_name, bk.key_tier)
            for bk in prior_h.business_keys
        )
        cur_sig = sorted(
            (bk.table_id, bk.col_name, bk.canonical_col_name, bk.key_tier)
            for bk in h.business_keys
        )
        if cur_sig != prior_sig:
            diverged.append(h.hub_name)
    return sorted(set(diverged))


def merge_draft_shards(
    shards: Sequence[DraftShardIR],
    *,
    project_id: str,
    ontology_version: str,
    wave_number: int,
    cluster_tables_by_id: Mapping[str, Sequence[str]] | None = None,
    boundary_neighbors_by_shard: Mapping[str, Sequence[str]] | None = None,
) -> DraftVaultIR:
    """Deterministically merge cluster-sharded DraftShardIR objects into one DraftVaultIR."""
    hub_map: dict[str, DraftHubSpec] = {}
    link_map: dict[str, DraftLinkSpec] = {}
    sat_map: dict[str, DraftSatelliteSpec] = {}
    triage_map: dict[tuple[str, str], ColumnTriageEntry] = {}
    just_map: dict[tuple[str, str, str], AntiPatternJustification] = {}
    clar_map: dict[str, ClarificationQuestion] = {}

    for raw_shard in sorted(shards, key=lambda s: s.cluster_id):
        shard = _filter_shard_halos(
            raw_shard,
            cluster_tables_by_id=cluster_tables_by_id,
            boundary_neighbors_by_shard=boundary_neighbors_by_shard,
        )
        for h in shard.hubs:
            if h.hub_name not in hub_map:
                hub_map[h.hub_name] = h.model_copy(deep=True)
            else:
                prev = hub_map[h.hub_name]
                bk_seen = {(b.table_id, b.col_name, b.source_id): b for b in prev.business_keys}
                for b in h.business_keys:
                    bk_seen.setdefault((b.table_id, b.col_name, b.source_id), b)
                merged_bks = [bk_seen[k] for k in sorted(bk_seen.keys())]
                best_tier = min(prev.key_tier, h.key_tier, key=lambda t: _TIER_RANK[t])
                merged_bindings = _merge_bindings([*prev.source_bindings, *h.source_bindings])
                merged_prio = list(
                    dict.fromkeys([*prev.survivorship_priority, *h.survivorship_priority])
                )
                hub_map[h.hub_name] = prev.model_copy(
                    update={
                        "business_keys": merged_bks,
                        "key_tier": best_tier,
                        "source_bindings": merged_bindings,
                        "attributes": _merge_fields([*prev.attributes, *h.attributes]),
                        "is_planned_concept": prev.is_planned_concept and h.is_planned_concept,
                        "survivorship_priority": merged_prio,
                    }
                )

        for lnk in shard.links:
            if lnk.link_name not in link_map:
                link_map[lnk.link_name] = lnk.model_copy(deep=True)
            else:
                prev_l = link_map[lnk.link_name]
                part_seen: dict[tuple[str, str, str], LinkParticipantSpec] = {
                    (p.hub_name, p.role_name, p.fk_col_name): p for p in prev_l.participants
                }
                for p in lnk.participants:
                    pkey = (p.hub_name, p.role_name, p.fk_col_name)
                    if pkey not in part_seen:
                        part_seen[pkey] = p
                    else:
                        part_seen[pkey] = _merge_participant(part_seen[pkey], p)
                link_map[lnk.link_name] = prev_l.model_copy(
                    update={
                        "participants": list(part_seen.values()),
                        "driving_hub": prev_l.driving_hub or lnk.driving_hub,
                        "degenerate_keys": sorted(
                            set(prev_l.degenerate_keys) | set(lnk.degenerate_keys)
                        ),
                        "source_bindings": _merge_bindings(
                            [*prev_l.source_bindings, *lnk.source_bindings]
                        ),
                        "attributes": _merge_fields([*prev_l.attributes, *lnk.attributes]),
                    }
                )

        for sat in shard.satellites:
            if sat.sat_name not in sat_map:
                sat_map[sat.sat_name] = sat.model_copy(deep=True)
            else:
                prev_s = sat_map[sat.sat_name]
                merged_kind = min(
                    prev_s.sat_kind, sat.sat_kind, key=lambda k: _SAT_KIND_PRIORITY[k]
                )
                merged_epoch = max(
                    (e for e in (prev_s.companion_epoch, sat.companion_epoch) if e is not None),
                    default=None,
                )
                merged_load = (
                    sat.load_mode
                    if prev_s.load_mode == "INSERT_ONLY_DELTA"
                    else prev_s.load_mode
                )
                sat_map[sat.sat_name] = prev_s.model_copy(
                    update={
                        "sat_kind": merged_kind,
                        "extends_sat_name": prev_s.extends_sat_name or sat.extends_sat_name,
                        "companion_epoch": merged_epoch,
                        "is_bitemporal": prev_s.is_bitemporal or sat.is_bitemporal,
                        "applied_dts_col": prev_s.applied_dts_col or sat.applied_dts_col,
                        "load_mode": merged_load,
                        "child_dependent_keys": list(
                            dict.fromkeys([*prev_s.child_dependent_keys, *sat.child_dependent_keys])
                        ),
                        "driving_keys": list(
                            dict.fromkeys([*prev_s.driving_keys, *sat.driving_keys])
                        ),
                        "immutable_payload_fields": list(
                            dict.fromkeys(
                                [*prev_s.immutable_payload_fields, *sat.immutable_payload_fields]
                            )
                        ),
                        "source_bindings": _merge_bindings(
                            [*prev_s.source_bindings, *sat.source_bindings]
                        ),
                        "attributes": _merge_fields([*prev_s.attributes, *sat.attributes]),
                    }
                )

        for entry in shard.column_triage:
            tkey = (entry.table_id, entry.col_name)
            if tkey not in triage_map:
                triage_map[tkey] = entry
            else:
                old_e = triage_map[tkey]
                if _DISPOSITION_RANK[entry.disposition] < _DISPOSITION_RANK[old_e.disposition]:
                    triage_map[tkey] = entry

        for just in shard.justifications:
            jkey = (just.rule_id, just.entity_name, just.waiver_id)
            if (
                jkey not in just_map
                or just.actor == "human"
                or (just.reviewed_by_v7 and not just_map[jkey].reviewed_by_v7)
            ):
                just_map[jkey] = just

        for q in shard.pending_clarifications:
            clar_map.setdefault(q.question_id, q)

    return DraftVaultIR(
        project_id=project_id,
        ontology_version=ontology_version,
        wave_number=wave_number,
        hubs=[hub_map[k] for k in sorted(hub_map.keys())],
        links=[link_map[k] for k in sorted(link_map.keys())],
        satellites=[sat_map[k] for k in sorted(sat_map.keys())],
        column_triage=[triage_map[k] for k in sorted(triage_map.keys())],
        justifications=[just_map[k] for k in sorted(just_map.keys())],
        pending_clarifications=list(clar_map.values()),
    )


def inject_unresolved_triage(
    draft: DraftVaultIR,
    profiles: Sequence[SourceProfile],
) -> DraftVaultIR:
    """S6-03: inject UNRESOLVED ColumnTriageEntry for any omitted Graph B (table_id, col_name)."""
    dedup: dict[tuple[str, str], ColumnTriageEntry] = {}
    for entry in draft.column_triage:
        key = (entry.table_id, entry.col_name)
        prev = dedup.get(key)
        if prev is None or (
            _DISPOSITION_RANK[entry.disposition] < _DISPOSITION_RANK[prev.disposition]
        ):
            dedup[key] = entry
    for prof in profiles:
        for tbl in prof.tables:
            for col in tbl.columns:
                key = (tbl.table_id, col.col_name)
                if key not in dedup:
                    dedup[key] = ColumnTriageEntry(
                        table_id=tbl.table_id,
                        col_name=col.col_name,
                        disposition="UNRESOLVED",
                        target_ref=None,
                        rationale="",
                    )
    return draft.model_copy(update={"column_triage": list(dedup.values())})


__all__ = [
    "build_entity_shard_provenance_map",
    "inject_unresolved_triage",
    "merge_draft_shards",
    "validate_frozen_shared_hub_keys",
]
