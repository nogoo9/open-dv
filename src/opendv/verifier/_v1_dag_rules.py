"""Gate V1 DAG rules (DV-STR-001..015) using networkx.MultiDiGraph.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

import networkx as nx  # type: ignore[import-untyped]

from opendv.models.diagnostics import KeyTierEnum, VerifierDiagnostic
from opendv.models.vault_ir import PredicateBranch, PredicateLeaf, PredicateNode
from opendv.models.vault_ir_certified import CertifiedLinkSpec, CertifiedVaultIR

_ZONE_ORDER: dict[str, int] = {"Z0": 0, "Z1": 1, "Z2": 2, "Z3": 3, "Z4": 4}
_TIER_RANK: dict[KeyTierEnum, int] = {
    "TIER_1_GLOBAL_STANDARD": 1,
    "TIER_2_ENTERPRISE_SOR": 2,
    "TIER_3_LOCAL_SCOPED": 3,
}


def v1_diag(
    rule_id: str,
    severity: str,
    entity_name: str,
    message: str,
    *,
    concept_uri: str | None = None,
) -> VerifierDiagnostic:
    """Create a Gate V1 VerifierDiagnostic."""
    sev = severity if severity in ("BLOCKER", "ERROR", "WARN", "ADVISORY") else "ERROR"
    return VerifierDiagnostic(
        gate="V1",
        rule_id=rule_id,
        severity=sev,  # type: ignore[arg-type]
        entity_name=entity_name,
        concept_uri=concept_uri,
        waivable=False,
        message=message,
    )


def _canonical_pred(node: PredicateNode | None) -> str:
    if node is None:
        return ""
    if isinstance(node, PredicateLeaf):
        return f"{node.op}:{node.column}={node.literal!r}"
    if isinstance(node, PredicateBranch):
        children = ",".join(sorted(_canonical_pred(c) for c in node.children))
        return f"{node.op}({children})"
    return ""


def _check_sal_ordering(
    lnk: CertifiedLinkSpec,
    hub_tiers: dict[str, KeyTierEnum],
    diags: list[VerifierDiagnostic],
) -> None:
    if len(lnk.participants) != 2:
        diags.append(
            v1_diag(
                "DV-STR-015",
                "BLOCKER",
                lnk.link_name,
                f"SAME_AS link {lnk.link_name} must have exactly 2 participants.",
            )
        )
        return
    p0, p1 = lnk.participants
    r0 = p0.role_name.strip().upper()
    r1 = p1.role_name.strip().upper()
    if r0 == r1 or not r0 or not r1:
        diags.append(
            v1_diag(
                "DV-STR-015",
                "BLOCKER",
                lnk.link_name,
                f"SAME_AS link {lnk.link_name} requires distinct non-empty roles.",
            )
        )
    if r0 in ("DUPLICATE", "ALIAS") and r1 in ("MASTER", "PRIMARY"):
        diags.append(
            v1_diag(
                "DV-STR-015",
                "BLOCKER",
                lnk.link_name,
                f"SAME_AS link {lnk.link_name} has inverted (DUPLICATE, MASTER) ordering.",
            )
        )
    if p0.fk_col_name == p1.fk_col_name:
        diags.append(
            v1_diag(
                "DV-STR-015",
                "BLOCKER",
                lnk.link_name,
                f"SAME_AS link {lnk.link_name} cannot use identical FK column names.",
            )
        )
    if p0.hub_name == p1.hub_name and p0.source_col_names == p1.source_col_names:
        diags.append(
            v1_diag(
                "DV-STR-015",
                "BLOCKER",
                lnk.link_name,
                f"SAME_AS link {lnk.link_name} contains a reflexive self-loop.",
            )
        )
    t0 = hub_tiers.get(p0.hub_name)
    t1 = hub_tiers.get(p1.hub_name)
    if t0 and t1 and _TIER_RANK[t0] > _TIER_RANK[t1]:
        diags.append(
            v1_diag(
                "DV-STR-015",
                "BLOCKER",
                lnk.link_name,
                f"SAME_AS link {lnk.link_name} places lower-tier Hub before higher-tier Hub.",
            )
        )


def check_v1_dag_rules(ir: CertifiedVaultIR) -> list[VerifierDiagnostic]:
    """Run DV-STR-001..015 topological and structural rules on CertifiedVaultIR."""
    diags: list[VerifierDiagnostic] = []
    hubs_by_name = {h.hub_name: h for h in ir.hubs}
    links_by_name = {lnk.link_name: lnk for lnk in ir.links}
    sats_by_name = {s.sat_name: s for s in ir.satellites}
    hub_tiers = {h.hub_name: h.key_tier for h in ir.hubs}

    ref_graph: nx.MultiDiGraph = nx.MultiDiGraph()
    zone_graph: nx.MultiDiGraph = nx.MultiDiGraph()
    node_zones: dict[str, str] = {}

    for b in ir.bronze_tables:
        zone_graph.add_node(b.table_name, zone="Z0")
        node_zones[b.table_name] = "Z0"
    for r in ir.reference_tables:
        zone_graph.add_node(r.ref_name, zone="Z0")
        node_zones[r.ref_name] = "Z0"
    for stg in ir.staging_views:
        zone_graph.add_node(stg.view_name, zone="Z1")
        node_zones[stg.view_name] = "Z1"
        for b in ir.bronze_tables:
            if b.source_table_id == stg.source_table_id:
                zone_graph.add_edge(b.table_name, stg.view_name)

    hub_hk_owners = {
        f"hk_{oh.hub_name.removeprefix('hub_')}": oh.hub_name for oh in ir.hubs
    }
    link_hk_owners = {
        (
            "hk_"
            f"{ol.link_name.removeprefix('lnk_').removeprefix('t_lnk_').removeprefix('sal_')}"
        ): ol.link_name
        for ol in ir.links
    }
    for h in ir.hubs:
        ref_graph.add_node(h.hub_name, kind="HUB")
        zone_graph.add_node(h.hub_name, zone=h.zone_id)
        node_zones[h.hub_name] = h.zone_id
        own_hk = f"hk_{h.hub_name.removeprefix('hub_')}"
        for cname in [c.name for c in h.columns] + [a.name for a in h.attributes]:
            if cname.startswith("hk_") and cname != own_hk:
                target = hub_hk_owners.get(cname) or link_hk_owners.get(cname) or cname
                ref_graph.add_edge(h.hub_name, target)
        for sb in h.source_bindings:
            for stg in ir.staging_views:
                if stg.source_table_id == sb.table_id:
                    zone_graph.add_edge(stg.view_name, h.hub_name)

    link_sigs: dict[tuple[tuple[str, ...], tuple[str, ...], str, str], list[str]] = {}
    binary_links_per_table: dict[tuple[str, str], list[str]] = {}
    hal_graph: nx.DiGraph = nx.DiGraph()

    for lnk in ir.links:
        ref_graph.add_node(lnk.link_name, kind="LINK")
        zone_graph.add_node(lnk.link_name, zone=lnk.zone_id)
        node_zones[lnk.link_name] = lnk.zone_id

        if len(lnk.participants) < 2:
            diags.append(
                v1_diag(
                    "DV-STR-002",
                    "BLOCKER",
                    lnk.link_name,
                    f"Link {lnk.link_name} has fewer than 2 participants.",
                )
            )

        seen_hubs: set[str] = set()
        seen_roles: set[str] = set()
        seen_fks: set[str] = set()
        is_self_ref = lnk.link_kind in ("SAME_AS", "HIERARCHICAL")

        for part in lnk.participants:
            if part.hub_name not in hubs_by_name:
                kind_hint = "Link" if part.hub_name in links_by_name else "unknown entity"
                diags.append(
                    v1_diag(
                        "DV-STR-003",
                        "BLOCKER",
                        lnk.link_name,
                        f"Link {lnk.link_name} references {kind_hint} '{part.hub_name}'.",
                    )
                )
            else:
                ref_graph.add_edge(lnk.link_name, part.hub_name)
                zone_graph.add_edge(part.hub_name, lnk.link_name)

            if part.hub_name in seen_hubs:
                is_self_ref = True
            seen_hubs.add(part.hub_name)
            if part.role_name in seen_roles and part.role_name:
                seen_roles.add(f"__DUP__:{part.role_name}")
            seen_roles.add(part.role_name)
            seen_fks.add(part.fk_col_name)

        if is_self_ref and (
            len(seen_fks) < len(lnk.participants)
            or any(not p.role_name.strip() for p in lnk.participants)
            or any(r.startswith("__DUP__:") for r in seen_roles)
        ):
            diags.append(
                v1_diag(
                    "DV-STR-004",
                    "BLOCKER",
                    lnk.link_name,
                    f"Self-ref Link {lnk.link_name} needs distinct role_name and fk_col_name.",
                )
            )

        if lnk.link_kind == "SAME_AS":
            _check_sal_ordering(lnk, hub_tiers, diags)
        elif lnk.link_kind == "HIERARCHICAL" and len(lnk.participants) >= 2:
            for i in range(len(lnk.participants)):
                for j in range(i + 1, len(lnk.participants)):
                    pi, pj = lnk.participants[i], lnk.participants[j]
                    if pi.hub_name == pj.hub_name and pi.source_col_names == pj.source_col_names:
                        diags.append(
                            v1_diag(
                                "DV-STR-014",
                                "ERROR",
                                lnk.link_name,
                                f"HIERARCHICAL Link {lnk.link_name} contains reflexive loop "
                                f"on {pi.hub_name}.",
                            )
                        )
            p0, p1 = lnk.participants[0], lnk.participants[1]
            if p0.hub_name != p1.hub_name:
                r0 = p0.role_name.strip().upper()
                r1 = p1.role_name.strip().upper()
                if r0 in ("CHILD", "SUB", "DUPLICATE") and r1 in ("PARENT", "SUPER", "MASTER"):
                    hal_graph.add_edge(p1.hub_name, p0.hub_name)
                else:
                    hal_graph.add_edge(p0.hub_name, p1.hub_name)

        tbl_id = lnk.source_bindings[0].table_id if lnk.source_bindings else ""
        flt = _canonical_pred(lnk.load_semantics.filter_ast if lnk.load_semantics else None)
        sig = (
            tuple(sorted(p.hub_name for p in lnk.participants)),
            tuple(sorted(lnk.degenerate_keys)),
            flt,
            tbl_id,
        )
        link_sigs.setdefault(sig, []).append(lnk.link_name)

        if len(lnk.participants) == 2 and tbl_id and lnk.link_kind == "STANDARD":
            for p in lnk.participants:
                binary_links_per_table.setdefault((tbl_id, p.hub_name), []).append(lnk.link_name)

    for lnk_names in link_sigs.values():
        if len(lnk_names) > 1:
            diags.append(
                v1_diag(
                    "DV-STR-010",
                    "WARN",
                    lnk_names[0],
                    f"Duplicate Link signature across {', '.join(lnk_names)}.",
                )
            )

    for (tbl_id, anchor_hub), l_names in binary_links_per_table.items():
        if len(l_names) >= 3:
            diags.append(
                v1_diag(
                    "DV-STR-011",
                    "WARN",
                    l_names[0],
                    f"Chasm risk: {len(l_names)} binary Links from {tbl_id} on {anchor_hub}.",
                )
            )

    ext_graph: nx.DiGraph = nx.DiGraph()
    for sat in ir.satellites:
        ref_graph.add_node(sat.sat_name, kind="SAT")
        zone_graph.add_node(sat.sat_name, zone=sat.zone_id)
        node_zones[sat.sat_name] = sat.zone_id

        if sat.parent_name in sats_by_name:
            diags.append(
                v1_diag(
                    "DV-STR-006",
                    "BLOCKER",
                    sat.sat_name,
                    f"Satellite {sat.sat_name} chains to Satellite {sat.parent_name}.",
                )
            )
        elif sat.parent_name not in hubs_by_name and sat.parent_name not in links_by_name:
            diags.append(
                v1_diag(
                    "DV-STR-005",
                    "BLOCKER",
                    sat.sat_name,
                    f"Satellite {sat.sat_name} references unknown parent {sat.parent_name}.",
                )
            )
        else:
            ref_graph.add_edge(sat.sat_name, sat.parent_name)
            zone_graph.add_edge(sat.parent_name, sat.sat_name)

        if sat.extends_sat_name:
            if sat.extends_sat_name == sat.sat_name or sat.extends_sat_name not in sats_by_name:
                diags.append(
                    v1_diag(
                        "DV-STR-006",
                        "BLOCKER",
                        sat.sat_name,
                        f"Satellite {sat.sat_name} invalid extends_sat_name "
                        f"'{sat.extends_sat_name}'.",
                    )
                )
            else:
                ext_graph.add_edge(sat.sat_name, sat.extends_sat_name)

        if sat.sat_kind == "ESAT":
            parent_lnk = links_by_name.get(sat.parent_name)
            if parent_lnk is None:
                diags.append(
                    v1_diag(
                        "DV-STR-007",
                        "BLOCKER",
                        sat.sat_name,
                        f"ESAT {sat.sat_name} must attach to a Link, not {sat.parent_name}.",
                    )
                )
            else:
                parent_fks = {p.fk_col_name for p in parent_lnk.participants}
                dk_set = set(sat.driving_keys)
                if not dk_set or not (dk_set < parent_fks):
                    diags.append(
                        v1_diag(
                            "DV-STR-007",
                            "BLOCKER",
                            sat.sat_name,
                            f"ESAT {sat.sat_name} driving_keys must be strict subset of Link FKs.",
                        )
                    )

        if sat.sat_kind == "MAS":
            if not sat.child_dependent_keys:
                diags.append(
                    v1_diag(
                        "DV-STR-008",
                        "BLOCKER",
                        sat.sat_name,
                        f"MAS {sat.sat_name} requires non-empty child_dependent_keys.",
                    )
                )
            else:
                col_nulls = {c.name: c.nullable for c in sat.columns}
                attr_nulls = {a.name: a.nullable for a in sat.attributes}
                for cdk in sat.child_dependent_keys:
                    if col_nulls.get(cdk, attr_nulls.get(cdk, True)):
                        diags.append(
                            v1_diag(
                                "DV-STR-008",
                                "BLOCKER",
                                sat.sat_name,
                                f"MAS {sat.sat_name} CDK '{cdk}' must be nullable=False.",
                            )
                        )

        if sat.parent_name in links_by_name:
            parent_lnk = links_by_name[sat.parent_name]
            if parent_lnk.link_kind == "TRANSACTIONAL" and sat.sat_kind not in (
                "ESAT",
                "AI_PROVENANCE_SATELLITE",
            ):
                diags.append(
                    v1_diag(
                        "DV-STR-009",
                        "ERROR",
                        sat.sat_name,
                        f"TRANSACTIONAL Link {parent_lnk.link_name} forbids {sat.sat_kind} Sat.",
                    )
                )

        if len(sat.source_bindings) > 1 or any(
            sb.source_id != sat.source_id or sb.table_id != sat.source_table_id
            for sb in sat.source_bindings
        ):
            diags.append(
                v1_diag(
                    "DV-STR-012",
                    "ERROR",
                    sat.sat_name,
                    f"Satellite {sat.sat_name} violates single-source split rule.",
                )
            )

    if ext_graph.number_of_edges() > 0 and not nx.is_directed_acyclic_graph(ext_graph):
        for cyc in sorted(nx.simple_cycles(ext_graph)):
            diags.append(
                v1_diag("DV-STR-006", "BLOCKER", cyc[0], f"Cycle in extends_sat_name: {cyc}.")
            )

    if hal_graph.number_of_edges() > 0 and not nx.is_directed_acyclic_graph(hal_graph):
        diags.append(
            v1_diag("DV-STR-014", "ERROR", "CertifiedVaultIR", "HIERARCHICAL Hub cycle.")
        )

    for pb in ir.pit_bridges:
        zone_graph.add_node(pb.object_name, zone=pb.zone_id)
        node_zones[pb.object_name] = pb.zone_id
        if pb.root_hub in node_zones:
            zone_graph.add_edge(pb.root_hub, pb.object_name)
        for tgt in pb.target_satellites_or_links:
            if tgt in node_zones:
                zone_graph.add_edge(tgt, pb.object_name)

    for mart in ir.information_marts:
        zone_graph.add_node(mart.view_name, zone=mart.zone_id)
        node_zones[mart.view_name] = mart.zone_id
        if mart.root_entity in node_zones:
            zone_graph.add_edge(mart.root_entity, mart.view_name)
        for je in mart.joined_entities:
            if je in node_zones:
                zone_graph.add_edge(je, mart.view_name)

    for h in ir.hubs:
        if ref_graph.out_degree(h.hub_name) > 0:
            diags.append(
                v1_diag(
                    "DV-STR-001",
                    "BLOCKER",
                    h.hub_name,
                    f"Hub {h.hub_name} has outgoing FK/structural edges.",
                )
            )
        if not h.is_planned_concept and ref_graph.in_degree(h.hub_name) == 0:
            diags.append(
                v1_diag(
                    "DV-STR-013",
                    "WARN",
                    h.hub_name,
                    f"Hub {h.hub_name} is isolated (0 attached Links or Satellites).",
                )
            )

    if not nx.is_directed_acyclic_graph(zone_graph):
        diags.append(
            v1_diag(
                "DV-STR-014",
                "ERROR",
                "CertifiedVaultIR",
                "Vault zone-flow graph contains a cycle.",
            )
        )
    for u, v in zone_graph.edges():
        zu = node_zones.get(str(u), "Z2")
        zv = node_zones.get(str(v), "Z2")
        if _ZONE_ORDER[zu] > _ZONE_ORDER[zv]:
            diags.append(
                v1_diag(
                    "DV-STR-014",
                    "ERROR",
                    str(v),
                    f"Backward zone dependency {u}({zu}) -> {v}({zv}) is forbidden.",
                )
            )

    return diags


__all__ = ["check_v1_dag_rules", "v1_diag"]
