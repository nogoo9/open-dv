"""Gate V6b Vault anti-pattern detectors (DV-AP-001..010, 012).

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from opendv.models.diagnostics import AntiPatternJustification, VerifierDiagnostic
from opendv.models.modeling_preferences import SURROGATE_BK_RE, ModelingPreferences
from opendv.models.ontology import GraphAOntology
from opendv.models.policy import CEGAR_POLICY_SPECS, CegarPolicy
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.verifier._v6b_ontology_ap import (
    FLOAT_SQL_RE,
    SEQ_COL_RE,
    SOURCE_PREFIX_RE,
    apply_v6b_governance,
    index_profiles,
    is_sensitive_col,
    waiver_id,
)

_ENUM_LOOKUP_RE = re.compile(r"(?i)(?:_code|_status|_type|_tier|_grade|_category)$")
_TIER1_ID_SET: frozenset[str] = frozenset(
    {"lei", "lei_code", "isin", "cusip", "figi", "iban", "swift_bic", "bic", "cik"}
)
_FORBIDDEN_Z1_EXPR_RE = re.compile(
    r"(?i)(?:\bJOIN\b|\bGROUP\s+BY\b|\bHAVING\b|\bWHERE\b|\bCASE\b[\s\S]*?\bWHEN\b|\bUNION\b)"
)
_BYTES_PER_MB = 1024 * 1024


def _dv_diag(
    rule_id: str,
    entity_name: str,
    message: str,
    *,
    severity: str = "WARN",
) -> VerifierDiagnostic:
    return VerifierDiagnostic(
        gate="V6b",
        rule_id=rule_id,
        severity=severity,  # type: ignore[arg-type]
        entity_name=entity_name,
        waivable=True,
        suggested_waiver_id=waiver_id("WAIV", rule_id, entity_name),
        message=message,
    )


def lint_vault_antipatterns(
    ir: CertifiedVaultIR,
    profiles: Sequence[SourceProfile] = (),
    ontology: GraphAOntology | None = None,
    *,
    policy: CegarPolicy = "balanced",
    cdc_volatility_by_table: Mapping[str, Mapping[str, float]] | None = None,
    fk_turnover_by_table: Mapping[str, Mapping[str, float]] | None = None,
    entity_count_overrides: Mapping[str, int] | None = None,
    modeling_preferences: ModelingPreferences | None = None,
    justifications: Sequence[AntiPatternJustification] = (),
) -> list[VerifierDiagnostic]:
    """Detect Vault DV-AP-001..010, 012 anti-patterns on CertifiedVaultIR."""
    diags: list[VerifierDiagnostic] = []
    prefs = modeling_preferences or ModelingPreferences()
    policy_spec = CEGAR_POLICY_SPECS[policy]
    col_idx, tbl_idx = index_profiles(profiles)
    cdc = cdc_volatility_by_table or {}
    turnover = fk_turnover_by_table or {}
    ent_counts = entity_count_overrides or {}

    sal_hubs = {
        p.hub_name for lnk in ir.links if lnk.link_kind == "SAME_AS" for p in lnk.participants
    }
    hub_sigs: list[tuple[str, set[str]]] = []
    table_to_hubs: dict[str, set[str]] = {}
    for h in ir.hubs:
        if h.is_planned_concept or h.hub_name.endswith("_canonical"):
            continue
        if SOURCE_PREFIX_RE.search(h.hub_name):
            prefix_sev = (
                "ADVISORY"
                if (
                    policy_spec.allow_preference_exemptions
                    and prefs.multi_source_hub_strategy == "companion_hub_with_sal"
                    and h.hub_name in sal_hubs
                )
                else "WARN"
            )
            diags.append(
                _dv_diag(
                    "DV-AP-001",
                    h.hub_name,
                    f"Hub {h.hub_name} carries source prefix.",
                    severity=prefix_sev,
                )
            )
        sig_h = {bk.col_name.lower() for bk in h.business_keys} | {
            a.name.lower() for a in h.attributes
        }
        hub_sigs.append((h.hub_name, sig_h))
        for sb in h.source_bindings:
            table_to_hubs.setdefault(sb.table_id, set()).add(h.hub_name)

    for idx, (h1, s1) in enumerate(hub_sigs):
        for h2, s2 in hub_sigs[idx + 1 :]:
            if len(s1) >= 2 and len(s2) >= 2:
                jac = len(s1 & s2) / len(s1 | s2)
                if jac >= 0.80:
                    diags.append(
                        _dv_diag(
                            "DV-AP-001",
                            h1,
                            f"Siloed Hubs {h1} and {h2} share {jac:.0%} Jaccard overlap.",
                        )
                    )

    if ontology is not None:
        tbl_concepts: dict[str, set[str]] = {}
        for c in ontology.concepts:
            for tb in c.table_bindings:
                tbl_concepts.setdefault(tb.table_id, set()).add(c.concept_uri)
        for tbl_id, tbl in tbl_idx.items():
            if (
                len(tbl.columns) >= 15
                and len(table_to_hubs.get(tbl_id, set())) == 1
                and len(tbl_concepts.get(tbl_id, set())) >= 2
            ):
                only_hub = next(iter(table_to_hubs[tbl_id]))
                diags.append(
                    _dv_diag(
                        "DV-AP-001",
                        only_hub,
                        f"Wide table {tbl_id} collapsed into 1 Hub {only_hub} "
                        "despite >=2 concepts.",
                    )
                )

    binary_by_table: dict[str, list[str]] = {}
    has_nary_by_table: set[str] = set()
    for lnk in ir.links:
        tbl_id = lnk.source_bindings[0].table_id if lnk.source_bindings else ""
        if len(lnk.participants) >= 3 and tbl_id:
            has_nary_by_table.add(tbl_id)
        elif len(lnk.participants) == 2 and tbl_id and lnk.link_kind == "STANDARD":
            binary_by_table.setdefault(tbl_id, []).append(lnk.link_name)

        if len(lnk.participants) >= 5 and tbl_id:
            has_sparse = False
            for part in lnk.participants:
                for sc in part.source_col_names:
                    cp = col_idx.get((tbl_id, sc))
                    if cp is not None and (1.0 - cp.null_ratio) < 0.95:
                        has_sparse = True
            if has_sparse:
                diags.append(
                    _dv_diag(
                        "DV-AP-002",
                        lnk.link_name,
                        f"Mega-Link {lnk.link_name} ({len(lnk.participants)} Hubs) has sparse FKs.",
                    )
                )

    for tbl_id, bin_links in binary_by_table.items():
        if len(bin_links) >= 3 and tbl_id not in has_nary_by_table:
            diags.append(
                _dv_diag(
                    "DV-AP-002",
                    bin_links[0],
                    f"Over-split binary chasm on {tbl_id}: {len(bin_links)} binary Links.",
                )
            )

    for h in ir.hubs:
        if h.is_planned_concept:
            continue
        tiers_present = {bk.key_tier for bk in h.business_keys}
        if (
            "TIER_1_GLOBAL_STANDARD" in tiers_present
            and "TIER_3_LOCAL_SCOPED" in tiers_present
            and h.hub_name not in sal_hubs
        ):
            diags.append(
                _dv_diag(
                    "DV-AP-003",
                    h.hub_name,
                    f"Hub {h.hub_name} mixes Tier-1 and Tier-3 BKs without sal_* crosswalk.",
                )
            )
        for bk in h.business_keys:
            cp = col_idx.get((bk.table_id, bk.col_name))
            tp = tbl_idx.get(bk.table_id)
            is_surrogate_bk = bool(SURROGATE_BK_RE.search(bk.col_name)) or (
                cp is not None and cp.surrogate_score >= 0.75
            )
            is_float_bk = cp is not None and bool(FLOAT_SQL_RE.match(cp.sql_type.strip()))
            if is_surrogate_bk or is_float_bk:
                bkcc_norm = bk.bkcc_value.strip().upper()
                unfixable_legacy_surrogate = (
                    is_surrogate_bk
                    and not is_float_bk
                    and bk.key_tier == "TIER_3_LOCAL_SCOPED"
                    and bkcc_norm != "GLOBAL"
                    and policy != "strict"
                    and tp is not None
                    and (
                        tp.best_non_surrogate_bk_uniqueness_ratio is None
                        or tp.best_non_surrogate_bk_uniqueness_ratio < 0.99
                    )
                )
                pref_allow_surrogate = (
                    is_surrogate_bk
                    and not is_float_bk
                    and policy_spec.allow_preference_exemptions
                    and prefs.surrogate_key_strategy == "allow_with_bkcc_note"
                )
                sev = (
                    "ADVISORY"
                    if (unfixable_legacy_surrogate or pref_allow_surrogate)
                    else "WARN"
                )
                kind_lbl = "float" if is_float_bk else "surrogate"
                diags.append(
                    _dv_diag(
                        "DV-AP-003",
                        h.hub_name,
                        f"Hub {h.hub_name} uses {kind_lbl} BK '{bk.col_name}'.",
                        severity=sev,
                    )
                )
            is_tier1_or_entity_id = bk.col_name.lower() in _TIER1_ID_SET or (
                bk.col_name.lower().endswith("_id") and not SURROGATE_BK_RE.search(bk.col_name)
            )
            if SEQ_COL_RE.search(bk.col_name) or (
                not is_tier1_or_entity_id
                and cp is not None
                and cp.distinct_count_est <= 50
                and not cp.is_pk
                and (
                    _ENUM_LOOKUP_RE.search(bk.col_name)
                    or (tp is not None and tp.row_count_est > cp.distinct_count_est)
                )
            ):
                diags.append(
                    _dv_diag(
                        "DV-AP-004",
                        h.hub_name,
                        f"Hub {h.hub_name} models lookup/sequence '{bk.col_name}' as Hub.",
                    )
                )

    micro_groups: dict[tuple[str, str, str], list[tuple[str, int, str]]] = {}
    for sat in ir.satellites:
        if sat.sat_kind != "PII":
            for attr in sat.attributes:
                cp = col_idx.get((sat.source_table_id, attr.name))
                if is_sensitive_col(attr.name, cp, attr.is_pii):
                    diags.append(
                        _dv_diag(
                            "DV-AP-009",
                            sat.sat_name,
                            f"Unisolated PII '{attr.name}' in {sat.sat_kind} {sat.sat_name}.",
                        )
                    )
                    break

        if sat.sat_kind in ("STANDARD", "PII", "MAS", "EXT"):
            tbl_cdc = cdc.get(sat.source_table_id, {})
            lams = [tbl_cdc.get(a.name, 0.01) for a in sat.attributes]
            tp = tbl_idx.get(sat.source_table_id)
            n_ent = max(ent_counts.get(sat.source_table_id, 1), tp.row_count_est if tp else 1)
            max_lam = max(lams, default=0.0)
            min_lam = min(lams, default=0.0)
            w_amp = n_ent * max_lam * (32.0 * sum(1 for v in lams if v <= 0.05))
            if len(sat.attributes) >= 25 or (
                max_lam >= 0.50 and min_lam <= 0.01 and w_amp >= 50 * _BYTES_PER_MB
            ):
                diags.append(
                    _dv_diag(
                        "DV-AP-005",
                        sat.sat_name,
                        f"God Satellite {sat.sat_name} ({len(sat.attributes)} cols).",
                    )
                )

        if sat.sat_kind == "STANDARD" and len(sat.attributes) <= 2 and ir.wave_number == 1:
            key = (sat.parent_name, sat.source_id, sat.source_table_id)
            micro_groups.setdefault(key, []).append(
                (sat.sat_name, len(sat.attributes), sat.source_table_id)
            )

    for (_, _, tbl_id), group in micro_groups.items():
        if len(group) >= 3:
            tp = tbl_idx.get(tbl_id)
            n_ent = max(ent_counts.get(tbl_id, 1), tp.row_count_est if tp else 1)
            max_lam = max(cdc.get(tbl_id, {}).values(), default=0.01)
            if n_ent * max_lam * (len(group) * 64.0) < 50 * _BYTES_PER_MB:
                diags.append(
                    _dv_diag(
                        "DV-AP-006",
                        group[0][0],
                        f"Micro-satellite sprawl on {tbl_id}: {len(group)} tiny Satellites.",
                    )
                )

    esat_parents = {s.parent_name for s in ir.satellites if s.sat_kind == "ESAT"}
    for lnk in ir.links:
        tbl_id = lnk.source_bindings[0].table_id if lnk.source_bindings else ""
        tp = tbl_idx.get(tbl_id)
        tbl_col_names = {c.col_name.lower() for c in tp.columns} if tp else set()
        if lnk.link_kind == "STANDARD" and (
            "event_dts" in tbl_col_names or "occurred_at" in tbl_col_names
        ):
            diags.append(
                _dv_diag(
                    "DV-AP-007",
                    lnk.link_name,
                    f"Immutable event {tbl_id} modeled as STANDARD {lnk.link_name}, not t_lnk_*.",
                )
            )
        if lnk.link_kind == "TRANSACTIONAL" and any(
            v > 0.0 for v in turnover.get(tbl_id, {}).values()
        ):
            diags.append(
                _dv_diag(
                    "DV-AP-007",
                    lnk.link_name,
                    f"{lnk.link_name} models changing relationship as TRANSACTIONAL.",
                )
            )
        if lnk.link_kind == "STANDARD" and lnk.link_name not in esat_parents:
            has_turnover = any(v > 0.0 for v in turnover.get(tbl_id, {}).values())
            has_eff_cols = bool({"effective_from", "effective_to", "applied_dts"} & tbl_col_names)
            if has_turnover or has_eff_cols:
                diags.append(
                    _dv_diag(
                        "DV-AP-008",
                        lnk.link_name,
                        f"Timeline Link {lnk.link_name} missing attached esat_*.",
                    )
                )

    z2_sigs: dict[tuple[str, str, tuple[str, ...]], str] = {
        (s.parent_name, s.source_table_id, tuple(sorted(a.name for a in s.attributes))): s.sat_name
        for s in ir.satellites
        if s.zone_id == "Z2" and s.sat_kind == "STANDARD"
    }
    for sat in ir.satellites:
        if sat.zone_id == "Z3" and sat.sat_kind == "STANDARD":
            sig_s = (
                sat.parent_name,
                sat.source_table_id,
                tuple(sorted(a.name for a in sat.attributes)),
            )
            if sig_s in z2_sigs:
                diags.append(
                    _dv_diag(
                        "DV-AP-010",
                        sat.sat_name,
                        f"Z3 Satellite {sat.sat_name} mirrors Z2 {z2_sigs[sig_s]} 1:1.",
                    )
                )
    for stg in ir.staging_views:
        for col, expr in stg.col_lineage_mappings.items():
            if _FORBIDDEN_Z1_EXPR_RE.search(expr):
                diags.append(
                    _dv_diag(
                        "DV-AP-010",
                        stg.view_name,
                        f"StagingViewSpec {stg.view_name}.{col} leaks soft SQL ({expr!r}).",
                    )
                )

    pit_hubs = {pb.root_hub for pb in ir.pit_bridges}
    for h in ir.hubs:
        if h.is_planned_concept:
            continue
        sat_count = sum(1 for s in ir.satellites if s.parent_name == h.hub_name)
        if sat_count >= 3 and h.hub_name not in pit_hubs:
            diags.append(
                _dv_diag(
                    "DV-AP-012",
                    h.hub_name,
                    f"Hub {h.hub_name} has {sat_count} Satellites without pit_* accelerator.",
                )
            )
    for mart in ir.information_marts:
        if len(mart.joined_entities) >= 4 and mart.root_entity not in pit_hubs:
            diags.append(
                _dv_diag(
                    "DV-AP-012",
                    mart.view_name,
                    f"Mart {mart.view_name} joins {len(mart.joined_entities)} entities w/o PIT.",
                )
            )

    merged_justs = [*ir.justifications, *justifications]
    return apply_v6b_governance(
        diags,
        policy=policy,
        modeling_preferences=prefs,
        justifications=merged_justs,
    )


__all__ = ["lint_vault_antipatterns"]
