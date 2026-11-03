"""Gate 3 Verifier: NetworkX Bipartite DAG Structural Verifier (`DV-STR-001`-`015`
and `DV-G3-001`-`005`) & Column-Level Invariant Verifier (`DV-COL-001`-`012`).

Constructs a directed multigraph `G = (V_H u V_L u V_S, E)` using `networkx.MultiDiGraph`
and verifies all 15 Data Vault 2.0 structural topology invariants:
  - `DV-STR-001` (`DV-G3-001`): Hub independence (`outdeg(h) == 0`, no Hub-to-Hub edges)
  - `DV-STR-002` (`DV-G3-002`): Link minimum Hub arity (`len(hub_references) >= 2`)
  - `DV-STR-003` (`DV-G3-004`): Link target Hub existence (`target_hub in V_H`, no Link-to-Link)
  - `DV-STR-004`: Self-referencing Link (`SAME_AS` / `HIERARCHICAL`) role & FK disambiguation
  - `DV-STR-005` (`DV-G3-003`): Satellite single-parent invariant (`outdeg(s) == 1`)
  - `DV-STR-006` (`DV-G3-003`): No Satellite-to-Satellite chaining (`parent not in V_S`)
  - `DV-STR-007`: Effectivity Satellite (`esat_*`) parent Link & driving key strict subset validity
  - `DV-STR-008`: Multi-Active Satellite (`MAS`) requires non-empty `sub_sequence_column`
  - `DV-STR-009`: Non-Historized Link (`NHL`) immutability & no attached `SAT` / `MAS`
  - `DV-STR-010`: Duplicate Link signature rejection
  - `DV-STR-011`: Chasm & Fan trap prevention on N-ary Links and Bridges
  - `DV-STR-012`: Raw Satellite single-source split rule (`len(source_mappings) == 1`)
  - `DV-STR-013`: Connected component / no isolated Hub islands (`indeg(h) >= 1`)
  - `DV-STR-014` (`DV-G3-005`): Bipartite DAG acyclicity (`nx.is_directed_acyclic_graph(G)`)
    and `HIERARCHICAL` Link recursion termination guarantee
  - `DV-STR-015`: Canonical Same-As Link (`sal_*`) edge ordering (`hk_master != hk_duplicate`,
    master-before-duplicate role/authority ordering, and rejection of `(A, A)` self-loops and
    duplicate inverted `(B, A)` pairs)
"""

from __future__ import annotations

from typing import Any

import networkx as nx  # type: ignore[import-untyped]

_SAL_MASTER_TOKENS: tuple[str, ...] = (
    "MASTER",
    "PRIMARY",
    "CANONICAL",
    "GOLDEN",
    "SURVIVING",
    "AUTHORITATIVE",
)
_SAL_DUPLICATE_TOKENS: tuple[str, ...] = (
    "DUPLICATE",
    "DUP",
    "ALIAS",
    "SECONDARY",
    "MERGED",
    "LEGACY",
    "SCOPED",
    "SYNONYM",
)
_AUTHORITY_TIER_RANK: dict[str, int] = {
    "TIER_1_GLOBAL_STANDARD": 1,
    "TIER_2_ENTERPRISE_SOR": 2,
    "TIER_3_LOCAL_SCOPED": 3,
}


def _entity_id(entity: dict[str, Any]) -> str:
    return str(entity.get("name") or entity.get("entity_id") or "")


def _ref_target_hub(ref: dict[str, Any]) -> str:
    return str(ref.get("target_hub") or ref.get("hub_id") or "")


def _ref_role_name(ref: dict[str, Any]) -> str:
    return str(ref.get("role_alias") or ref.get("role_name") or "").strip()


def _ref_fk_cols(ref: dict[str, Any]) -> list[str]:
    if "source_fk_columns" in ref and isinstance(ref["source_fk_columns"], list):
        return [str(c) for c in ref["source_fk_columns"] if str(c).strip()]
    fk = str(ref.get("fk_column_name") or "").strip()
    return [fk] if fk else []


def _resolve_sal_ref_tier_rank(
    ref: dict[str, Any],
    hub_obj: dict[str, Any] | None,
) -> int | None:
    """Resolve the 3-Tier BKCC authority rank (1=Tier-1, 2=Tier-2, 3=Tier-3) for a SAL reference."""
    direct_tier = str(ref.get("key_authority_tier") or ref.get("bk_authority_tier") or "").upper()
    if direct_tier in _AUTHORITY_TIER_RANK:
        return _AUTHORITY_TIER_RANK[direct_tier]
    if not hub_obj or not isinstance(hub_obj, dict):
        return None

    ref_cols = [c.lower() for c in _ref_fk_cols(ref)]
    if not ref_cols:
        return None

    bk_sources = [s for s in hub_obj.get("business_key_sources", []) if isinstance(s, dict)]
    for src in bk_sources:
        tier = str(src.get("key_authority_tier") or src.get("bk_authority_tier") or "").upper()
        rank = _AUTHORITY_TIER_RANK.get(tier)
        if rank is None:
            continue
        src_cols = [str(c).lower() for c in src.get("key_columns", []) if str(c).strip()]
        if src_cols and ref_cols == src_cols:
            return rank

    for src in bk_sources:
        tier = str(src.get("key_authority_tier") or src.get("bk_authority_tier") or "").upper()
        rank = _AUTHORITY_TIER_RANK.get(tier)
        if rank is None:
            continue
        src_cols = [str(c).lower() for c in src.get("key_columns", []) if str(c).strip()]
        if len(ref_cols) == 1 and len(src_cols) == 1 and ref_cols[0].endswith(f"_{src_cols[0]}"):
            return rank

    return None


def build_dv2_networkx_digraph(contract: dict[str, Any]) -> nx.MultiDiGraph:
    """Build the directed Data Vault 2.0 topology graph `G = (V_H u V_L u V_S, E)`."""
    g = nx.MultiDiGraph()

    for hub in contract.get("hubs", []):
        h_id = _entity_id(hub)
        if h_id:
            g.add_node(h_id, archetype="HUB", data=hub)

    for lnk in contract.get("links", []):
        l_id = _entity_id(lnk)
        if l_id:
            g.add_node(l_id, archetype="LINK", data=lnk)

    for sat in contract.get("satellites", []):
        s_id = _entity_id(sat)
        if s_id:
            g.add_node(s_id, archetype="SATELLITE", data=sat)

    # Hub illegal outgoing edges (DV-STR-001)
    for hub in contract.get("hubs", []):
        h_id = _entity_id(hub)
        for fk in hub.get("foreign_keys", []):
            target = fk.get("target_hub") if isinstance(fk, dict) else str(fk)
            if target:
                g.add_edge(h_id, str(target), edge_type="ILLEGAL_HUB_FK")
        if hub.get("parent_hub_id"):
            g.add_edge(h_id, str(hub["parent_hub_id"]), edge_type="ILLEGAL_HUB_FK")

    # Link outgoing edges to referenced Hubs
    for lnk in contract.get("links", []):
        l_id = _entity_id(lnk)
        for idx, ref in enumerate(lnk.get("hub_references", [])):
            target = _ref_target_hub(ref)
            if target:
                g.add_edge(
                    l_id,
                    target,
                    key=idx,
                    edge_type="LINK_TO_HUB",
                    role=_ref_role_name(ref),
                )

    # Satellite outgoing edge to parent entity
    for sat in contract.get("satellites", []):
        s_id = _entity_id(sat)
        parent = str(sat.get("parent_entity") or sat.get("parent_entity_id") or "")
        if parent:
            g.add_edge(s_id, parent, edge_type="SAT_TO_PARENT")
        for extra_parent in sat.get("additional_parents", []):
            g.add_edge(s_id, str(extra_parent), edge_type="ILLEGAL_MULTI_PARENT")

    return g


def verify_structural_invariants(
    contract: dict[str, Any],
    allow_link_to_link: bool = False,
    *,
    check_isolated_hubs: bool = True,
) -> list[str]:
    """Gate 3 Verifier: Enforce `DV-STR-001` through `DV-STR-015` (`DV-G3-001`-`005`)
    using a `networkx.MultiDiGraph` bipartite DAG model.
    """
    violations: list[str] = []
    g = build_dv2_networkx_digraph(contract)

    hubs = {_entity_id(h): h for h in contract.get("hubs", []) if _entity_id(h)}
    links = {_entity_id(lnk): lnk for lnk in contract.get("links", []) if _entity_id(lnk)}
    sats = {_entity_id(s): s for s in contract.get("satellites", []) if _entity_id(s)}

    v_h = set(hubs.keys())
    v_l = set(links.keys())
    v_s = set(sats.keys())

    # 1. DV-STR-001 / DV-G3-001: Hub Independence (outdeg(h) == 0, no Hub-to-Hub edges)
    for h_name, h_obj in hubs.items():
        if h_obj.get("foreign_keys") or h_obj.get("parent_hub_id") or g.out_degree(h_name) > 0:
            violations.append(
                f"[DV-STR-001 / DV-G3-001] Hub '{h_name}' has outgoing foreign-key edge(s) "
                f"(outdeg={g.out_degree(h_name)}); direct Hub-to-Hub edges are forbidden."
            )

    # 2. Link Invariants (DV-STR-002..004, DV-STR-010, DV-STR-011, DV-STR-014, DV-STR-015)
    seen_link_signatures: dict[tuple[Any, ...], str] = {}
    seen_sal_endpoint_pairs: dict[tuple[str, frozenset[str]], tuple[str, tuple[str, str]]] = {}
    link_fk_cols_map: dict[str, set[str]] = {}

    for l_name, l_obj in links.items():
        refs = list(l_obj.get("hub_references", []))
        l_type = str(l_obj.get("link_type") or l_obj.get("link_subtype") or "STANDARD_LINK").upper()

        # DV-STR-002 / DV-G3-002: Link Minimum Arity >= 2
        if len(refs) < 2:
            violations.append(
                f"[DV-STR-002 / DV-G3-002] Link '{l_name}' has Hub arity {len(refs)} < 2. "
                f"Every Link must connect at least 2 Hub references."
            )

        fk_cols_for_link: set[str] = set()
        target_hub_list: list[str] = []
        roles: list[str] = []
        fk_first_cols: list[str] = []

        for ref in refs:
            t_hub = _ref_target_hub(ref)
            target_hub_list.append(t_hub)
            roles.append(_ref_role_name(ref))
            ref_fks = _ref_fk_cols(ref)
            fk_cols_for_link.update(ref_fks)
            if ref_fks:
                fk_first_cols.append(ref_fks[0])

            # DV-STR-003 / DV-G3-004: Link Target Hub Existence & Link-to-Link Prohibition
            if t_hub in v_l and not allow_link_to_link:
                violations.append(
                    f"[DV-STR-003 / DV-G3-004] Link '{l_name}' references another Link "
                    f"'{t_hub}'. Link-to-Link references are forbidden."
                )
            elif t_hub not in v_h and not (allow_link_to_link and t_hub in v_l):
                violations.append(
                    f"[DV-STR-003] Link '{l_name}' references non-existent Hub '{t_hub}'."
                )

        link_fk_cols_map[l_name] = fk_cols_for_link

        # DV-STR-004: Self-Referencing Link Role & FK Column Disambiguation
        is_self_ref = l_type in {
            "SAME_AS",
            "SAME_AS_LINK",
            "HIERARCHICAL",
            "HIERARCHICAL_LINK",
        } or len(set(target_hub_list)) < len(target_hub_list)
        if (
            is_self_ref
            and len(refs) >= 2
            and (
                any(r == "" for r in roles)
                or len(set(roles)) != len(refs)
                or len(set(fk_first_cols)) != len(refs)
            )
        ):
            violations.append(
                f"[DV-STR-004] Self-referencing / {l_type} Link '{l_name}' must "
                f"declare distinct non-empty role names and distinct FK columns."
            )

        # DV-STR-015: Canonical Same-As Link (sal_*) Edge Ordering & Self-Loop / Inverted-Pair Guard
        is_sal = l_type in {"SAME_AS", "SAME_AS_LINK"} or l_name.lower().startswith("sal_")
        if is_sal:
            if len(refs) != 2 or len(set(target_hub_list)) != 1:
                violations.append(
                    f"[DV-STR-015] Same-As Link '{l_name}' must connect exactly 2 references "
                    f"to the same target Hub (found {target_hub_list})."
                )
            else:
                t_hub = target_hub_list[0]
                ref0_cols = tuple(_ref_fk_cols(refs[0]))
                ref1_cols = tuple(_ref_fk_cols(refs[1]))
                fk0 = str(
                    refs[0].get("fk_column_name") or (ref0_cols[0] if ref0_cols else "")
                ).strip()
                fk1 = str(
                    refs[1].get("fk_column_name") or (ref1_cols[0] if ref1_cols else "")
                ).strip()
                ep0 = ref0_cols[0] if ref0_cols else fk0
                ep1 = ref1_cols[0] if ref1_cols else fk1

                where_cond_u = str(l_obj.get("where_filter_condition") or "").upper()
                has_reflexive_where = (
                    bool(where_cond_u)
                    and "=" in where_cond_u
                    and "!=" not in where_cond_u
                    and "<>" not in where_cond_u
                    and "NOT " not in where_cond_u
                    and (
                        (
                            bool(ep0)
                            and bool(ep1)
                            and ep0.upper() in where_cond_u
                            and ep1.upper() in where_cond_u
                        )
                        or (
                            "HK_" in where_cond_u
                            and "MASTER" in where_cond_u
                            and "DUPLICATE" in where_cond_u
                        )
                    )
                )

                raw_pairs = (
                    l_obj.get("edge_pairs")
                    or l_obj.get("sample_pairs")
                    or l_obj.get("candidate_pairs")
                    or []
                )
                has_pair_self_loop = False
                has_pair_inverted_dup = False
                seen_directed_pairs: set[tuple[str, str]] = set()
                if isinstance(raw_pairs, list):
                    for pair in raw_pairs:
                        if isinstance(pair, (list, tuple)) and len(pair) == 2:
                            p_a, p_b = str(pair[0]).strip(), str(pair[1]).strip()
                        elif isinstance(pair, dict):
                            p_a = str(pair.get("hk_master") or pair.get("master") or "").strip()
                            p_b = str(
                                pair.get("hk_duplicate") or pair.get("duplicate") or ""
                            ).strip()
                        else:
                            continue
                        if p_a and p_b:
                            if p_a == p_b:
                                has_pair_self_loop = True
                            elif (p_b, p_a) in seen_directed_pairs:
                                has_pair_inverted_dup = True
                            seen_directed_pairs.add((p_a, p_b))

                is_self_loop = (
                    (bool(roles[0]) and roles[0].upper() == roles[1].upper())
                    or (bool(ref0_cols) and ref0_cols == ref1_cols)
                    or (bool(fk0) and bool(fk1) and fk0 == fk1)
                    or l_obj.get("asymmetry_predicate") is False
                    or l_obj.get("allow_self_loops") is True
                    or has_reflexive_where
                    or has_pair_self_loop
                )
                if is_self_loop:
                    violations.append(
                        f"[DV-STR-015] Same-As Link '{l_name}' violates asymmetry "
                        f"(hk_master != hk_duplicate): reflexive self-loop (A, A) detected "
                        f"between endpoints ({ep0 or roles[0]} vs {ep1 or roles[1]})."
                    )

                r0_u = roles[0].upper()
                r1_u = roles[1].upper()
                fk0_u = f"{' '.join(ref0_cols)} {fk0}".upper()
                fk1_u = f"{' '.join(ref1_cols)} {fk1}".upper()

                r0_has_m = any(tok in r0_u for tok in _SAL_MASTER_TOKENS)
                r0_has_d = any(tok in r0_u for tok in _SAL_DUPLICATE_TOKENS)
                r1_has_m = any(tok in r1_u for tok in _SAL_MASTER_TOKENS)
                r1_has_d = any(tok in r1_u for tok in _SAL_DUPLICATE_TOKENS)

                if r0_has_m or r0_has_d or r1_has_m or r1_has_d:
                    canonical_role_order = r0_has_m and not r0_has_d and r1_has_d and not r1_has_m
                else:
                    fk0_has_m = any(tok in fk0_u for tok in _SAL_MASTER_TOKENS)
                    fk0_has_d = any(tok in fk0_u for tok in _SAL_DUPLICATE_TOKENS)
                    fk1_has_m = any(tok in fk1_u for tok in _SAL_MASTER_TOKENS)
                    fk1_has_d = any(tok in fk1_u for tok in _SAL_DUPLICATE_TOKENS)
                    canonical_role_order = (
                        fk0_has_m and not fk0_has_d and fk1_has_d and not fk1_has_m
                    )

                if not canonical_role_order:
                    violations.append(
                        f"[DV-STR-015] Same-As Link '{l_name}' violates canonical "
                        f"(hk_master, hk_duplicate) edge ordering: endpoint 0 "
                        f"('{roles[0] or ep0}') must be MASTER/PRIMARY and endpoint 1 "
                        f"('{roles[1] or ep1}') must be DUPLICATE/ALIAS."
                    )

                hub_obj = hubs.get(t_hub)
                rank0 = _resolve_sal_ref_tier_rank(refs[0], hub_obj)
                rank1 = _resolve_sal_ref_tier_rank(refs[1], hub_obj)
                if rank0 is not None and rank1 is not None and rank0 > rank1:
                    violations.append(
                        f"[DV-STR-015] Same-As Link '{l_name}' violates canonical authority "
                        f"hierarchy on Hub '{t_hub}': master endpoint '{ep0 or roles[0]}' "
                        f"(Tier-{rank0}) has lower authority than duplicate endpoint "
                        f"'{ep1 or roles[1]}' (Tier-{rank1})."
                    )

                if has_pair_inverted_dup:
                    violations.append(
                        f"[DV-STR-015] Same-As Link '{l_name}' contains duplicate inverted "
                        f"(B, A) edge pairs conflicting with canonical (A, B) ordering."
                    )

                if ep0 and ep1 and ep0 != ep1:
                    sal_pair_key = (t_hub, frozenset({ep0, ep1}))
                    if sal_pair_key in seen_sal_endpoint_pairs:
                        prev_sal, prev_eps = seen_sal_endpoint_pairs[sal_pair_key]
                        violations.append(
                            f"[DV-STR-015] Same-As Link '{l_name}' defines duplicate or "
                            f"inverted (B, A) endpoint pair ({ep0}, {ep1}) on Hub '{t_hub}', "
                            f"conflicting with '{prev_sal}' {prev_eps}."
                        )
                    else:
                        seen_sal_endpoint_pairs[sal_pair_key] = (l_name, (ep0, ep1))

        # DV-STR-014: Hierarchical Link Recursion Termination Guarantee
        if l_type in {"HIERARCHICAL", "HIERARCHICAL_LINK"} and (
            "max_recursion_depth" in l_obj or "cycle_prevention_predicate" in l_obj
        ):
            max_depth = l_obj.get("max_recursion_depth")
            cycle_pred = l_obj.get("cycle_prevention_predicate")
            if max_depth is None or not (1 <= int(max_depth) <= 100) or cycle_pred is not True:
                violations.append(
                    f"[DV-STR-014 / DV-G3-005] Hierarchical Link '{l_name}' lacks bounded "
                    f"max_recursion_depth in [1, 100] or cycle_prevention_predicate=True."
                )

        # DV-STR-010: Duplicate Link Signature Rejection
        degen = tuple(
            sorted(
                str(c)
                for c in (l_obj.get("degenerate_columns") or l_obj.get("degenerate_Columns") or [])
            )
        )
        sem_role = str(
            l_obj.get("semantic_role")
            or l_obj.get("where_filter_condition")
            or l_obj.get("ontology_concept_uri")
            or ""
        )
        sig = (
            tuple(sorted(target_hub_list)),
            degen,
            sem_role,
            str(l_obj.get("where_filter_condition") or ""),
        )
        if sig in seen_link_signatures:
            other_lnk = seen_link_signatures[sig]
            violations.append(
                f"[DV-STR-010] Duplicate Link signature between '{other_lnk}' and '{l_name}'."
            )
        else:
            seen_link_signatures[sig] = l_name

        # DV-STR-011: N-ary Link Chasm/Fan Trap Check
        if len(refs) >= 3 and (
            "fan_trap_risk" in l_obj
            or ("grain_statement" in l_obj and not l_obj.get("grain_statement"))
        ):
            grain_stmt = str(l_obj.get("grain_statement") or "").strip()
            if not grain_stmt or l_obj.get("fan_trap_risk") is True:
                violations.append(
                    f"[DV-STR-011] N-ary Link '{l_name}' exhibits chasm/fan trap risk "
                    f"or lacks an explicit atomic grain_statement."
                )

    # Check Bridges for DV-STR-011
    for bridge in contract.get("bridges", []):
        b_id = _entity_id(bridge)
        if int(bridge.get("independent_one_to_many_branch_count", 0)) >= 2 and not bridge.get(
            "pre_aggregated_branches", False
        ):
            violations.append(
                f"[DV-STR-011] Bridge '{b_id}' joins multiple 1:M Links without pre-aggregation."
            )

    # 3. Satellite Invariants (DV-STR-005..009, DV-STR-012)
    for s_name, s_obj in sats.items():
        parent = str(s_obj.get("parent_entity") or s_obj.get("parent_entity_id") or "")
        s_type = str(
            s_obj.get("satellite_type") or s_obj.get("satellite_subtype") or "STANDARD_SATELLITE"
        ).upper()
        extra_parents = list(s_obj.get("additional_parents", []))

        # DV-STR-006 / DV-G3-003: No Satellite-to-Satellite Chaining
        if parent in v_s:
            violations.append(
                f"[DV-STR-006 / DV-G3-003] Satellite '{s_name}' attaches to another "
                f"Satellite '{parent}'. Satellite-to-Satellite chaining is forbidden."
            )
        # DV-STR-005 / DV-G3-003: Single Parent in V_H u V_L
        elif extra_parents or parent not in (v_h | v_l):
            violations.append(
                f"[DV-STR-005 / DV-G3-003] Satellite '{s_name}' violates single-parent "
                f"invariant (parent='{parent}', additional_parents={extra_parents})."
            )

        # DV-STR-007: Effectivity Satellite Driving Key Subset Validity
        if s_type in {"EFFECTIVITY", "EFFECTIVITY_SATELLITE"}:
            if parent not in v_l:
                violations.append(
                    f"[DV-STR-007] Effectivity Satellite '{s_name}' must attach to a Link "
                    f"(found parent '{parent}')."
                )
            else:
                drv_cols: set[str] = set()
                if s_obj.get("driving_key_column"):
                    drv_cols.add(str(s_obj["driving_key_column"]))
                for dc in s_obj.get("driving_key_columns", []):
                    if str(dc).strip():
                        drv_cols.add(str(dc))
                parent_fks = link_fk_cols_map.get(parent, set())
                if not drv_cols or not (drv_cols < parent_fks):
                    violations.append(
                        f"[DV-STR-007] Effectivity Satellite '{s_name}' driving key "
                        f"{sorted(drv_cols)} is not a non-empty strict subset of parent "
                        f"Link '{parent}' FKs {sorted(parent_fks)}."
                    )

        # DV-STR-008: Multi-Active Satellite Sub-Sequence / Child Dependent Key Requirement
        if s_type in {"MAS", "MULTI_ACTIVE_SATELLITE"}:
            sub_seq = str(s_obj.get("sub_sequence_column") or "").strip()
            cdk = [str(c) for c in s_obj.get("child_dependent_keys", []) if str(c).strip()]
            if not sub_seq and not cdk:
                violations.append(
                    f"[DV-STR-008] Multi-Active Satellite '{s_name}' requires a non-empty "
                    f"sub_sequence_column or child_dependent_keys."
                )

        # DV-STR-009: Non-Historized Link Immutability (no attached SAT / MAS)
        if parent in v_l:
            parent_l_type = str(
                links[parent].get("link_type")
                or links[parent].get("link_subtype")
                or "STANDARD_LINK"
            ).upper()
            if parent_l_type in {"NON_HISTORIZED", "NON_HISTORIZED_LINK"} and s_type in {
                "SAT",
                "STANDARD_SATELLITE",
                "MAS",
                "MULTI_ACTIVE_SATELLITE",
            }:
                violations.append(
                    f"[DV-STR-009] Non-Historized Link '{parent}' cannot have mutable "
                    f"descriptive Satellite '{s_name}' ({s_type}) attached."
                )

        # DV-STR-012: Raw Satellite Single-Source Split Rule
        if not s_obj.get("is_business_vault", False):
            if "source_mappings" in s_obj and isinstance(s_obj["source_mappings"], list):
                if len(s_obj["source_mappings"]) != 1:
                    violations.append(
                        f"[DV-STR-012] Raw Satellite '{s_name}' has "
                        f"{len(s_obj['source_mappings'])} source_mappings (expected 1)."
                    )
            elif not str(s_obj.get("source_bronze_table") or "").strip():
                violations.append(
                    f"[DV-STR-012] Raw Satellite '{s_name}' is missing source_bronze_table."
                )

    # 4. DV-STR-013: Isolated Hub Island Check (indeg(h) >= 1)
    if check_isolated_hubs and (v_l or v_s):
        for h_name in v_h:
            if g.in_degree(h_name) < 1:
                violations.append(
                    f"[DV-STR-013] Hub '{h_name}' is an isolated island with zero incoming "
                    f"Link or Satellite edges (indeg=0)."
                )

    # 5. DV-STR-014 / DV-G3-005: Global Bipartite DAG Acyclicity
    if not nx.is_directed_acyclic_graph(g):
        cycles = list(nx.simple_cycles(g))
        violations.append(
            f"[DV-STR-014 / DV-G3-005] Directed cycle(s) detected in Vault topology: {cycles}"
        )

    return violations


def verify_column_invariants(ir: dict[str, Any]) -> list[str]:
    """Verify column-level invariants `DV-COL-001` through `DV-COL-012`."""
    violations: list[str] = []
    expected_digest_bits = 256
    expected_null_token = "-1:^^"

    for h in ir.get("hubs", []):
        h_id = _entity_id(h)
        if "hk_digest_bits" in h and h.get("hk_digest_bits") != expected_digest_bits:
            violations.append(f"DV-COL-001: Hub {h_id} invalid HK digest width.")

        bks = list(h.get("business_key_columns", []))
        bk_sources = list(h.get("business_key_sources", []))
        if not bks and not bk_sources:
            violations.append(f"DV-COL-003: Hub {h_id} has empty BK list.")
        for bk in bks:
            if not bk.get("is_nullable_allowed", False) and bk.get("nullable", False):
                violations.append(f"DV-COL-003: Hub {h_id} BK {bk.get('column_name')} is nullable.")

        if "load_dts_column" in h and (
            not h.get("load_dts_column") or h.get("load_dts_nullable", True)
        ):
            violations.append(f"DV-COL-005: Hub {h_id} invalid load_dts.")
        if "rec_src_column" in h and (
            not h.get("rec_src_column") or h.get("rec_src_nullable", True)
        ):
            violations.append(f"DV-COL-006: Hub {h_id} invalid rec_src.")

        if h.get("payload_columns"):
            violations.append(f"DV-COL-007: Hub {h_id} contains descriptive payload columns.")

        recipe = h.get("hash_recipe", {})
        if recipe and (
            not recipe.get("use_length_prefix", False)
            or recipe.get("null_token") != expected_null_token
            or not recipe.get("trim_whitespace", False)
            or not recipe.get("uppercase_bk", False)
        ):
            violations.append(
                f"DV-COL-010/011: Hub {h_id} violates canonical hash standardization."
            )

        if h.get("has_cross_source_bk_collision", False) and not h.get("bkcc_column"):
            violations.append(f"DV-COL-012: Hub {h_id} has cross-source BK collision without BKCC.")

    for lnk in ir.get("links", []):
        l_id = _entity_id(lnk)
        if "hk_digest_bits" in lnk and lnk.get("hk_digest_bits") != expected_digest_bits:
            violations.append(f"DV-COL-002: Link {l_id} invalid LHK digest width.")

        for ref in lnk.get("hub_references", []):
            if ref.get("fk_nullable", False):
                violations.append(
                    f"DV-COL-004: Link {l_id} FK {ref.get('fk_column_name')} is nullable."
                )

        if "load_dts_column" in lnk and (
            not lnk.get("load_dts_column") or lnk.get("load_dts_nullable", True)
        ):
            violations.append(f"DV-COL-005: Link {l_id} invalid load_dts.")
        if "rec_src_column" in lnk and (
            not lnk.get("rec_src_column") or lnk.get("rec_src_nullable", True)
        ):
            violations.append(f"DV-COL-006: Link {l_id} invalid rec_src.")

        l_sub = str(lnk.get("link_type") or lnk.get("link_subtype") or "STANDARD")
        if l_sub not in {"NON_HISTORIZED", "NON_HISTORIZED_LINK"} and lnk.get(
            "mutable_payload_columns"
        ):
            violations.append(f"DV-COL-008: Link {l_id} contains mutable descriptive payload.")

        recipe = lnk.get("hash_recipe", {})
        if recipe and (
            not recipe.get("use_length_prefix", False)
            or recipe.get("null_token") != expected_null_token
        ):
            violations.append(
                f"DV-COL-010: Link {l_id} violates canonical length-prefixed hash recipe."
            )

    for s in ir.get("satellites", []):
        s_id = _entity_id(s)
        s_sub = str(s.get("satellite_type") or s.get("satellite_subtype") or "SAT")
        if "parent_hk_column" in s and (
            not s.get("parent_hk_column") or s.get("parent_hk_nullable", True)
        ):
            violations.append(f"DV-COL-004: Satellite {s_id} invalid parent_hk.")
        if "load_dts_column" in s and (
            not s.get("load_dts_column") or s.get("load_dts_nullable", True)
        ):
            violations.append(f"DV-COL-005: Satellite {s_id} invalid load_dts.")
        if "rec_src_column" in s and (
            not s.get("rec_src_column") or s.get("rec_src_nullable", True)
        ):
            violations.append(f"DV-COL-006: Satellite {s_id} invalid rec_src.")

        if s_sub in {"SAT", "STANDARD_SATELLITE", "MAS", "MULTI_ACTIVE_SATELLITE"}:
            if "hash_diff_column" in s and not s.get("hash_diff_column"):
                violations.append(f"DV-COL-009: Satellite {s_id} missing hash_diff.")
            if not s.get("payload_columns"):
                violations.append(f"DV-COL-009: Satellite {s_id} has empty payload_columns.")
            hd_recipe = s.get("hash_diff_recipe", {})
            if hd_recipe and (
                not hd_recipe.get("sorted_alphabetically", False)
                or not hd_recipe.get("use_length_prefix", False)
                or hd_recipe.get("null_token") != expected_null_token
            ):
                violations.append(f"DV-COL-009/010: Satellite {s_id} invalid hash_diff recipe.")

    return violations
