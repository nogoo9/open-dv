"""Gate V1 Bridge, Survivorship, and Link Participant rules.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import networkx as nx  # type: ignore[import-untyped]

from opendv.models.diagnostics import VerifierDiagnostic
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir import (
    BridgeHopSpec,
    DraftBridgeSpec,
    DraftHubSpec,
    DraftLinkSpec,
    PortableLogicalType,
)
from opendv.models.vault_ir_certified import (
    CertifiedHubSpec,
    CertifiedLinkSpec,
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    SourceBindingSpec,
)
from opendv.verifier._l1_drift_and_bucket_a import is_lossless_type_widening
from opendv.verifier._v1_dag_rules import v1_diag


def source_cols_by_table(
    ir: CertifiedVaultIR,
    profiles: Sequence[SourceProfile],
) -> dict[str, set[str]]:
    """Index physical source columns by table_id from profiles or bronze_tables."""
    cols_by_tbl: dict[str, set[str]] = {}
    for prof in profiles:
        for tbl in prof.tables:
            cols_by_tbl[tbl.table_id] = {c.col_name for c in tbl.columns}
    for bronze in ir.bronze_tables:
        if bronze.source_table_id not in cols_by_tbl and bronze.columns:
            cols_by_tbl[bronze.source_table_id] = {c.name for c in bronze.columns}
    return cols_by_tbl


def _types_compatible(t1: PortableLogicalType, t2: PortableLogicalType) -> bool:
    return t1 == t2 or is_lossless_type_widening(t1, t2) or is_lossless_type_widening(t2, t1)


def _is_z4_excluded_sat(
    sat: CertifiedSatelliteSpec,
    sat_by_name: Mapping[str, CertifiedSatelliteSpec],
) -> bool:
    cur: CertifiedSatelliteSpec | None = sat
    seen: set[str] = set()
    while cur is not None:
        if cur.sat_kind in ("PII", "MAS", "ESAT") or bool(cur.child_dependent_keys):
            return True
        if not cur.extends_sat_name or cur.extends_sat_name in seen:
            break
        seen.add(cur.sat_name)
        cur = sat_by_name.get(cur.extends_sat_name)
    return False


def validate_survivorship_and_z4_types(ir: CertifiedVaultIR) -> list[VerifierDiagnostic]:
    """Validate survivorship_priority ownership and Z4 COALESCE type compatibility (DV-STR-017)."""
    diags: list[VerifierDiagnostic] = []
    sats_by_hub: dict[str, list[CertifiedSatelliteSpec]] = {}
    for sat in ir.satellites:
        sats_by_hub.setdefault(sat.parent_name, []).append(sat)

    for hub in ir.hubs:
        hub_sats = sats_by_hub.get(hub.hub_name, [])
        owned_sources = {sb.source_id for sb in hub.source_bindings} | {
            s.source_id for s in hub_sats
        }
        owned_pairs = {(sb.source_id, sb.table_id) for sb in hub.source_bindings} | {
            (s.source_id, s.source_table_id) for s in hub_sats
        }
        owned_pair_strs = {f"{src}:{tbl}" for src, tbl in owned_pairs}

        seen_prio: set[str] = set()
        for entry in hub.survivorship_priority:
            clean = entry.strip()
            if not clean:
                diags.append(
                    v1_diag(
                        "DV-STR-017",
                        "BLOCKER",
                        hub.hub_name,
                        f"Hub {hub.hub_name} survivorship_priority contains blank entry.",
                    )
                )
                continue
            if clean in seen_prio:
                diags.append(
                    v1_diag(
                        "DV-STR-017",
                        "BLOCKER",
                        hub.hub_name,
                        f"Hub {hub.hub_name} survivorship_priority duplicates '{clean}'.",
                    )
                )
                continue
            seen_prio.add(clean)
            is_owned = clean in owned_sources or clean in owned_pair_strs
            if not is_owned and ":" in clean:
                src_part, rest = clean.split(":", 1)
                is_owned = (src_part, rest) in owned_pairs or (src_part, clean) in owned_pairs
            if not is_owned:
                diags.append(
                    v1_diag(
                        "DV-STR-017",
                        "BLOCKER",
                        hub.hub_name,
                        f"Hub {hub.hub_name} survivorship_priority '{clean}' not owned by Hub.",
                    )
                )

        sat_by_name = {s.sat_name: s for s in hub_sats}
        z4_sats = [s for s in hub_sats if not _is_z4_excluded_sat(s, sat_by_name)]
        attrs_by_pair: dict[tuple[str, str], dict[str, PortableLogicalType]] = {}
        for sat in sorted(
            z4_sats,
            key=lambda s: (s.companion_epoch or 1, 1 if s.extends_sat_name else 0, s.sat_name),
        ):
            pair = (sat.source_id, sat.source_table_id)
            pair_map = attrs_by_pair.setdefault(pair, {})
            for attr in sat.attributes:
                pair_map[attr.name] = attr.logical_type

        if len(attrs_by_pair) < 2:
            continue
        pairs_by_attr: dict[str, list[tuple[tuple[str, str], PortableLogicalType]]] = {}
        for pair in sorted(attrs_by_pair):
            for attr_name, ltype in attrs_by_pair[pair].items():
                pairs_by_attr.setdefault(attr_name, []).append((pair, ltype))

        for attr_name, entries in sorted(pairs_by_attr.items()):
            if len(entries) < 2:
                continue
            first_pair, first_type = entries[0]
            for other_pair, other_type in entries[1:]:
                if not _types_compatible(first_type, other_type):
                    diags.append(
                        v1_diag(
                            "DV-STR-017",
                            "BLOCKER",
                            hub.hub_name,
                            f"Hub {hub.hub_name} Z4 column '{attr_name}' incompatible across "
                            f"{first_pair[0]}:{first_pair[1]} ({first_type}) and "
                            f"{other_pair[0]}:{other_pair[1]} ({other_type}).",
                        )
                    )
                    break

    return diags


def validate_bridge_hop_chain(
    bridge_name: str,
    root_hub: str,
    hops: Sequence[BridgeHopSpec],
    hubs_by_name: Mapping[str, CertifiedHubSpec | DraftHubSpec],
    links_by_name: Mapping[str, CertifiedLinkSpec | DraftLinkSpec],
    *,
    target_satellites_or_links: Sequence[str] = (),
) -> list[VerifierDiagnostic]:
    """Validate connected non-cyclic BridgeHopSpec chain (DV-STR-016)."""
    diags: list[VerifierDiagnostic] = []
    if root_hub not in hubs_by_name:
        diags.append(
            v1_diag(
                "DV-STR-016",
                "BLOCKER",
                bridge_name,
                f"Bridge {bridge_name} references unknown root_hub '{root_hub}'.",
            )
        )
        return diags

    if not hops:
        if not target_satellites_or_links:
            diags.append(
                v1_diag("DV-STR-016", "BLOCKER", bridge_name, f"Bridge {bridge_name} has 0 hops.")
            )
        else:
            for tgt in target_satellites_or_links:
                if tgt not in links_by_name:
                    diags.append(
                        v1_diag(
                            "DV-STR-016",
                            "BLOCKER",
                            bridge_name,
                            f"Bridge {bridge_name} references unknown Link '{tgt}'.",
                        )
                    )
        return diags

    current_hub = root_hub
    seen_edges: set[tuple[str, str, str]] = set()
    for idx, hop in enumerate(hops):
        lnk = links_by_name.get(hop.link_name)
        if lnk is None:
            diags.append(
                v1_diag(
                    "DV-STR-016",
                    "BLOCKER",
                    bridge_name,
                    f"Bridge {bridge_name} hop[{idx}] unknown Link '{hop.link_name}'.",
                )
            )
            break

        fk_map = {p.fk_col_name: p for p in lnk.participants}
        if hop.from_fk_col is not None:
            if hop.from_fk_col not in fk_map:
                diags.append(
                    v1_diag(
                        "DV-STR-016",
                        "BLOCKER",
                        bridge_name,
                        f"Bridge {bridge_name} hop[{idx}] missing from_fk_col "
                        f"'{hop.from_fk_col}' on '{lnk.link_name}'.",
                    )
                )
                break
            from_part = fk_map[hop.from_fk_col]
            if from_part.hub_name != current_hub:
                diags.append(
                    v1_diag(
                        "DV-STR-016",
                        "BLOCKER",
                        bridge_name,
                        f"Bridge {bridge_name} hop[{idx}] disconnected: '{hop.from_fk_col}' "
                        f"targets '{from_part.hub_name}', expected '{current_hub}'.",
                    )
                )
                break
        else:
            matching_from = [p for p in lnk.participants if p.hub_name == current_hub]
            if hop.to_fk_col is not None and len(matching_from) > 1:
                matching_from = [p for p in matching_from if p.fk_col_name != hop.to_fk_col]
            if len(matching_from) != 1:
                reason = "does not connect to" if not matching_from else "needs from_fk_col for"
                diags.append(
                    v1_diag(
                        "DV-STR-016",
                        "BLOCKER",
                        bridge_name,
                        f"Bridge {bridge_name} hop[{idx}] '{lnk.link_name}' {reason} "
                        f"'{current_hub}'.",
                    )
                )
                break
            from_part = matching_from[0]

        if hop.to_fk_col is not None:
            if hop.to_fk_col not in fk_map:
                diags.append(
                    v1_diag(
                        "DV-STR-016",
                        "BLOCKER",
                        bridge_name,
                        f"Bridge {bridge_name} hop[{idx}] missing to_fk_col "
                        f"'{hop.to_fk_col}' on '{lnk.link_name}'.",
                    )
                )
                break
            to_part = fk_map[hop.to_fk_col]
            if to_part.fk_col_name == from_part.fk_col_name:
                diags.append(
                    v1_diag(
                        "DV-STR-016",
                        "BLOCKER",
                        bridge_name,
                        f"Bridge {bridge_name} hop[{idx}] loops on FK '{from_part.fk_col_name}'.",
                    )
                )
                break
        else:
            candidates = [p for p in lnk.participants if p.fk_col_name != from_part.fk_col_name]
            if len(candidates) != 1:
                diags.append(
                    v1_diag(
                        "DV-STR-016",
                        "BLOCKER",
                        bridge_name,
                        f"Bridge {bridge_name} hop[{idx}] '{lnk.link_name}' needs to_fk_col.",
                    )
                )
                break
            to_part = candidates[0]

        edge = (lnk.link_name, from_part.fk_col_name, to_part.fk_col_name)
        rev_edge = (lnk.link_name, to_part.fk_col_name, from_part.fk_col_name)
        is_hal_self = (
            lnk.link_kind == "HIERARCHICAL" or lnk.link_name.startswith("hal_")
        ) and from_part.hub_name == to_part.hub_name
        if rev_edge in seen_edges or (edge in seen_edges and not is_hal_self):
            diags.append(
                v1_diag(
                    "DV-STR-016",
                    "BLOCKER",
                    bridge_name,
                    f"Bridge {bridge_name} hop[{idx}] directed edge cycle on {edge}.",
                )
            )
            break
        seen_edges.add(edge)
        current_hub = to_part.hub_name

    return diags


def validate_draft_bridge(
    bridge: DraftBridgeSpec,
    hubs_by_name: Mapping[str, DraftHubSpec],
    links_by_name: Mapping[str, DraftLinkSpec],
) -> list[VerifierDiagnostic]:
    """Validate a DraftBridgeSpec hop chain."""
    return validate_bridge_hop_chain(
        bridge.bridge_name, bridge.root_hub, bridge.hops, hubs_by_name, links_by_name
    )


def validate_pit_bridges(ir: CertifiedVaultIR) -> list[VerifierDiagnostic]:
    """Validate all BRIDGE entries in CertifiedVaultIR.pit_bridges."""
    hubs_by_name: dict[str, CertifiedHubSpec] = {h.hub_name: h for h in ir.hubs}
    links_by_name: dict[str, CertifiedLinkSpec] = {lnk.link_name: lnk for lnk in ir.links}
    diags: list[VerifierDiagnostic] = []
    for pb in ir.pit_bridges:
        if pb.object_kind == "BRIDGE":
            diags.extend(
                validate_bridge_hop_chain(
                    pb.object_name,
                    pb.root_hub,
                    pb.hops,
                    hubs_by_name,
                    links_by_name,
                    target_satellites_or_links=pb.target_satellites_or_links,
                )
            )
    return diags


def _check_hub_binding_match(
    link_name: str,
    role_name: str,
    ref_hub: CertifiedHubSpec,
    sb: SourceBindingSpec,
    target_tbl: str | None,
    diags: list[VerifierDiagnostic],
) -> None:
    if ref_hub.is_planned_concept:
        return
    hub_tbl_ids = {hsb.table_id for hsb in ref_hub.source_bindings}
    if target_tbl is not None and target_tbl not in hub_tbl_ids:
        diags.append(
            v1_diag(
                "DV-STR-003",
                "BLOCKER",
                link_name,
                f"Link {link_name} '{role_name}' target_hub_binding_table_id "
                f"'{target_tbl}' missing on {ref_hub.hub_name}.",
            )
        )
        return
    if not hub_tbl_ids:
        return
    hub_by_src: dict[str, list[str]] = {}
    for hsb in ref_hub.source_bindings:
        hub_by_src.setdefault(hsb.source_id, []).append(hsb.table_id)
    resolved = (
        target_tbl in hub_tbl_ids
        if target_tbl is not None
        else (
            sb.table_id in hub_tbl_ids
            or len(hub_by_src.get(sb.source_id, [])) == 1
            or len(ref_hub.source_bindings) == 1
        )
    )
    if not resolved:
        diags.append(
            v1_diag(
                "DV-STR-003",
                "BLOCKER",
                link_name,
                f"Link {link_name} '{role_name}' binding '{sb.table_id}' cannot resolve "
                f"Hub binding on {ref_hub.hub_name}.",
            )
        )


def _check_bkcc_col_exists(
    link_name: str,
    role_name: str,
    bkcc_col: str | None,
    key_tier: str,
    table_id: str,
    src_cols_by_tbl: Mapping[str, set[str]],
    diags: list[VerifierDiagnostic],
) -> None:
    if bkcc_col and key_tier != "TIER_1_GLOBAL_STANDARD":
        tbl_cols = src_cols_by_tbl.get(table_id)
        if tbl_cols is not None and bkcc_col not in tbl_cols:
            diags.append(
                v1_diag(
                    "DV-COL-012",
                    "BLOCKER",
                    link_name,
                    f"Link {link_name} '{role_name}' bkcc_col '{bkcc_col}' missing from "
                    f"'{table_id}'.",
                )
            )


def validate_link_participant_bindings(
    ir: CertifiedVaultIR,
    profiles: Sequence[SourceProfile],
) -> list[VerifierDiagnostic]:
    """Validate Link participant target_hub_binding_table_id, participant_specs, and bkcc_col."""
    diags: list[VerifierDiagnostic] = []
    hubs_by_name = {h.hub_name: h for h in ir.hubs}
    src_cols_by_tbl = source_cols_by_table(ir, profiles)

    for lnk in ir.links:
        for sb in lnk.source_bindings:
            for part in lnk.participants:
                ref_hub = hubs_by_name.get(part.hub_name)
                if ref_hub is None:
                    continue
                _check_hub_binding_match(
                    lnk.link_name,
                    part.role_name,
                    ref_hub,
                    sb,
                    part.target_hub_binding_table_id,
                    diags,
                )
                _check_bkcc_col_exists(
                    lnk.link_name,
                    part.role_name,
                    part.bkcc_col,
                    part.key_tier or ref_hub.key_tier,
                    sb.table_id,
                    src_cols_by_tbl,
                    diags,
                )
            if sb.hash_recipe is None:
                continue
            for ps in sb.hash_recipe.participant_specs:
                if not ps.is_degenerate and ps.hub_name and ps.hub_name in hubs_by_name:
                    _check_hub_binding_match(
                        lnk.link_name,
                        ps.role_name,
                        hubs_by_name[ps.hub_name],
                        sb,
                        ps.target_hub_binding_table_id,
                        diags,
                    )
                _check_bkcc_col_exists(
                    lnk.link_name,
                    ps.role_name,
                    ps.bkcc_col,
                    ps.key_tier,
                    sb.table_id,
                    src_cols_by_tbl,
                    diags,
                )
    return diags


def are_hubs_sal_connected(ir: CertifiedVaultIR, hub_names: Sequence[str]) -> bool:
    """Return True if all given Hubs belong to one connected component via sal_* Links."""
    non_canon = [hn for hn in hub_names if not hn.endswith("_canonical")]
    if len(non_canon) <= 1:
        return True
    sal_graph: nx.Graph = nx.Graph()
    for hn in non_canon:
        sal_graph.add_node(hn)
    for lnk in ir.links:
        if lnk.link_kind == "SAME_AS" or lnk.link_name.startswith("sal_"):
            p_hubs = [p.hub_name for p in lnk.participants if p.hub_name]
            for i in range(len(p_hubs)):
                for j in range(i + 1, len(p_hubs)):
                    sal_graph.add_edge(p_hubs[i], p_hubs[j])
    reachable = nx.node_connected_component(sal_graph, non_canon[0])
    return all(hn in reachable for hn in non_canon)


__all__ = [
    "are_hubs_sal_connected",
    "source_cols_by_table",
    "validate_bridge_hop_chain",
    "validate_draft_bridge",
    "validate_link_participant_bindings",
    "validate_pit_bridges",
    "validate_survivorship_and_z4_types",
]
