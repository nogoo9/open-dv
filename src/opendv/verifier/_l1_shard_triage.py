"""Deterministic DraftShardIR merge (>40 tables) and S6-03 UNRESOLVED triage injection.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from collections.abc import Sequence

from opendv.models.diagnostics import ClarificationQuestion, KeyTierEnum
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


def merge_draft_shards(
    shards: Sequence[DraftShardIR],
    *,
    project_id: str,
    ontology_version: str,
    wave_number: int,
) -> DraftVaultIR:
    """Deterministically merge cluster-sharded DraftShardIR objects into one DraftVaultIR."""
    hub_map: dict[str, DraftHubSpec] = {}
    link_map: dict[str, DraftLinkSpec] = {}
    sat_map: dict[str, DraftSatelliteSpec] = {}
    triage_map: dict[tuple[str, str], ColumnTriageEntry] = {}
    clar_map: dict[str, ClarificationQuestion] = {}

    for shard in sorted(shards, key=lambda s: s.cluster_id):
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
                hub_map[h.hub_name] = prev.model_copy(
                    update={
                        "business_keys": merged_bks,
                        "key_tier": best_tier,
                        "source_bindings": merged_bindings,
                        "attributes": _merge_fields([*prev.attributes, *h.attributes]),
                        "is_planned_concept": prev.is_planned_concept and h.is_planned_concept,
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
                        merged_cols = list(
                            dict.fromkeys([*part_seen[pkey].source_col_names, *p.source_col_names])
                        )
                        part_seen[pkey] = part_seen[pkey].model_copy(
                            update={"source_col_names": merged_cols}
                        )
                link_map[lnk.link_name] = prev_l.model_copy(
                    update={
                        "participants": [part_seen[k] for k in sorted(part_seen.keys())],
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
    "inject_unresolved_triage",
    "merge_draft_shards",
]
