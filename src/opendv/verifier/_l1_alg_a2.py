"""Pure L1 deterministic codemod: Alg A.2 reference enumeration demotion (ref_* <= 50).

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from opendv.models.ontology import GraphAOntology
from opendv.models.project import EvolutionLockSpec
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
from opendv.models.vault_ir import (
    DraftField,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
)
from opendv.models.vault_ir_certified import RefTableSpec
from opendv.verifier._l1_alg_a1 import build_profile_indexes

_ENUM_SUFFIX_RE = re.compile(r"(?i)(?:_code|_status|_type|_tier|_grade|_category)$")
_TIER1_REF_EXEMPT: frozenset[str] = frozenset(
    {"lei", "lei_code", "isin", "cusip", "figi", "iban", "swift_bic", "bic"}
)


def _is_enum_hub_candidate(
    hub: DraftHubSpec,
    ontology: GraphAOntology | None,
    col_idx: Mapping[tuple[str, str], ColumnProfile],
    tbl_idx: Mapping[str, TableProfile],
    max_enum_cardinality: int,
) -> tuple[bool, str, dict[str, str]]:
    if not hub.business_keys:
        return False, "", {}
    slug = hub.hub_name.removeprefix("hub_")
    expected_ref = f"ref_{slug}"
    if ontology is not None:
        for enum_spec in ontology.enumerations:
            if (
                enum_spec.target_reference_table == expected_ref
                or enum_spec.enumeration_name.lower() == slug.lower()
            ):
                return True, enum_spec.enum_uri, dict(enum_spec.codes)

    all_enum_shaped = True
    codes: dict[str, str] = {}
    for bk in hub.business_keys:
        col = col_idx.get((bk.table_id, bk.col_name))
        _ = tbl_idx.get(bk.table_id)
        if col is None or col.is_pk or bk.col_name.lower() in _TIER1_REF_EXEMPT:
            all_enum_shaped = False
            break
        if col.distinct_count_est <= 0 or col.distinct_count_est > max_enum_cardinality:
            all_enum_shaped = False
            break
        if not _ENUM_SUFFIX_RE.search(col.col_name):
            all_enum_shaped = False
            break
        for val in col.sample_values:
            if val is not None and val != "<MASKED_PII>":
                codes[val] = val
    if all_enum_shaped and len(hub.attributes) <= 2:
        return True, f"urn:opendv:enum:{slug}", codes
    return False, "", {}


def apply_alg_a2_ref_demotion(
    draft: DraftVaultIR,
    profiles: Sequence[SourceProfile],
    ontology: GraphAOntology | None = None,
    locks: EvolutionLockSpec | None = None,
    *,
    max_enum_cardinality: int = 50,
) -> tuple[DraftVaultIR, list[RefTableSpec]]:
    """Alg A.2: demote static <= 50-value enumeration Hubs to Zone Z0 ref_* tables."""
    col_idx, tbl_idx = build_profile_indexes(profiles)
    locked_hubs = locks.locked_hub_bks if locks else {}

    demoted: dict[str, str] = {}
    ref_tables: list[RefTableSpec] = []
    retained_hubs: list[DraftHubSpec] = []

    for hub in draft.hubs:
        if hub.is_planned_concept or hub.hub_name in locked_hubs or not hub.business_keys:
            retained_hubs.append(hub)
            continue
        is_enum, enum_uri, codes = _is_enum_hub_candidate(
            hub, ontology, col_idx, tbl_idx, max_enum_cardinality
        )
        if not is_enum:
            retained_hubs.append(hub)
            continue
        ref_name = f"ref_{hub.hub_name.removeprefix('hub_')}"
        demoted[hub.hub_name] = ref_name
        bk0 = hub.business_keys[0]
        label_col = hub.attributes[0].name if hub.attributes else f"{bk0.col_name}_label"
        ref_tables.append(
            RefTableSpec(
                ref_name=ref_name,
                enum_uri=enum_uri or f"urn:opendv:enum:{ref_name}",
                zone_id="Z0",
                code_col=bk0.col_name,
                label_col=label_col,
                codes=codes,
            )
        )

    if not demoted:
        return draft, []

    deleted_sat_remap = {
        s.sat_name: demoted[s.parent_name] for s in draft.satellites if s.parent_name in demoted
    }
    retained_sats = [s for s in draft.satellites if s.parent_name not in demoted]
    retained_links: list[DraftLinkSpec] = []
    dissolved_entries: list[tuple[str, str, str, str, list[str]]] = []
    link_remap: dict[str, str] = {}

    for lnk in draft.links:
        real_parts = [p for p in lnk.participants if p.hub_name not in demoted]
        dem_parts = [p for p in lnk.participants if p.hub_name in demoted]
        if not dem_parts:
            retained_links.append(lnk)
            continue
        if len(real_parts) >= 2:
            degen = sorted(
                set(lnk.degenerate_keys).union(*(p.source_col_names for p in dem_parts))
            )
            new_driving = None if lnk.driving_hub in demoted else lnk.driving_hub
            retained_links.append(
                lnk.model_copy(
                    update={
                        "participants": real_parts,
                        "degenerate_keys": degen,
                        "driving_hub": new_driving,
                    }
                )
            )
        elif len(real_parts) == 1 and lnk.source_bindings:
            real_hub = real_parts[0].hub_name
            sb0 = lnk.source_bindings[0]
            fk_cols = [c for p in dem_parts for c in p.source_col_names]
            dissolved_entries.append(
                (lnk.link_name, real_hub, sb0.table_id, sb0.source_id, fk_cols)
            )
            link_remap[lnk.link_name] = real_hub
        else:
            link_remap[lnk.link_name] = (
                real_parts[0].hub_name if real_parts else demoted[dem_parts[0].hub_name]
            )

    col_remap: dict[tuple[str, str], str] = {}
    if dissolved_entries:
        used_sat_names = {s.sat_name for s in retained_sats}
        updated_sats = list(retained_sats)
        for lnk_name, r_hub, t_id, src_id, fk_cols in dissolved_entries:
            if not fk_cols:
                continue
            target_idx = next(
                (
                    i
                    for i, s in enumerate(updated_sats)
                    if s.parent_name == r_hub
                    and s.sat_kind == "STANDARD"
                    and any(b.table_id == t_id for b in s.source_bindings)
                ),
                None,
            )
            if target_idx is not None:
                sat = updated_sats[target_idx]
                existing_names = {f.name for f in sat.attributes}
                extra_fields = [
                    DraftField(
                        name=col,
                        logical_type="UTF8_STRING",
                        nullable=True,
                        is_pii=False,
                    )
                    for col in fk_cols
                    if col not in existing_names
                ]
                new_bindings = [
                    b.model_copy(
                        update={
                            "col_mappings": {**b.col_mappings, **{c: c for c in fk_cols}}
                        }
                    )
                    if b.table_id == t_id
                    else b
                    for b in sat.source_bindings
                ]
                updated_sats[target_idx] = sat.model_copy(
                    update={
                        "attributes": [*sat.attributes, *extra_fields],
                        "source_bindings": new_bindings,
                    }
                )
                target_sat_name = sat.sat_name
            else:
                tbl_slug = t_id.split(":", 1)[-1].split(".")[-1]
                base_syn = f"sat_{r_hub.removeprefix('hub_')}_{tbl_slug}"
                syn_name = base_syn
                idx = 2
                while syn_name in used_sat_names:
                    syn_name = f"{base_syn}_{idx}"
                    idx += 1
                used_sat_names.add(syn_name)
                updated_sats.append(
                    DraftSatelliteSpec(
                        sat_name=syn_name,
                        sat_kind="STANDARD",
                        parent_name=r_hub,
                        source_bindings=[
                            DraftSourceBinding(
                                source_id=src_id,
                                table_id=t_id,
                                col_mappings={c: c for c in fk_cols},
                            )
                        ],
                        attributes=[
                            DraftField(
                                name=col,
                                logical_type="UTF8_STRING",
                                nullable=True,
                                is_pii=False,
                            )
                            for col in fk_cols
                        ],
                    )
                )
                target_sat_name = syn_name
            link_remap[lnk_name] = target_sat_name
            for col in fk_cols:
                col_remap[(t_id, col)] = target_sat_name
        retained_sats = updated_sats

    updated_triage = []
    for entry in draft.column_triage:
        ckey = (entry.table_id, entry.col_name)
        if ckey in col_remap and entry.disposition == "MODELED":
            updated_triage.append(entry.model_copy(update={"target_ref": col_remap[ckey]}))
        elif entry.target_ref in demoted:
            updated_triage.append(
                entry.model_copy(update={"target_ref": demoted[entry.target_ref]})
            )
        elif entry.target_ref in link_remap:
            updated_triage.append(
                entry.model_copy(update={"target_ref": link_remap[entry.target_ref]})
            )
        elif entry.target_ref in deleted_sat_remap:
            updated_triage.append(
                entry.model_copy(update={"target_ref": deleted_sat_remap[entry.target_ref]})
            )
        else:
            updated_triage.append(entry)

    return (
        draft.model_copy(
            update={
                "hubs": retained_hubs,
                "links": retained_links,
                "satellites": retained_sats,
                "column_triage": updated_triage,
            }
        ),
        ref_tables,
    )


__all__ = ["apply_alg_a2_ref_demotion"]
