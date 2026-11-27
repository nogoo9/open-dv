"""Gates V2-V5 dispatchers, EvolutionLockSpec builder, and unified V1-V6b orchestration.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from collections.abc import Sequence

from opendv.models.diagnostics import (
    AntiPatternJustification,
    IRProverReport,
    VerifierDiagnostic,
)
from opendv.models.policy import (
    CEGAR_POLICY_SPECS,
    compute_cegar_energy,
    compute_deterministic_energy,
)
from opendv.models.project import EvolutionLockSpec
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.verifier._v2_v3_checks import (
    verify_v2_hash_and_bkcc,
    verify_v3_sentinels_and_temporal,
)
from opendv.verifier._v4_unstructured import verify_v4_unstructured
from opendv.verifier._v5_evolution import verify_v5_evolution
from opendv.verifier.ports import IRRuntimeProver, VerifierContext, VerifierReport
from opendv.verifier.v1_structure import verify_v1_structure
from opendv.verifier.v6b_antipatterns import (
    apply_v6b_governance,
    evaluate_v6b_accounting,
    is_diagnostic_waived,
    lint_v6b_antipatterns,
    resolve_effective_v6b_blocking_rules,
)


def _merge_justifications(
    ir_justifications: Sequence[AntiPatternJustification],
    persisted_waivers: Sequence[AntiPatternJustification],
) -> list[AntiPatternJustification]:
    merged: list[AntiPatternJustification] = []
    seen: set[tuple[str, str, str]] = set()
    for just in (*ir_justifications, *persisted_waivers):
        key = (just.waiver_id, just.rule_id, just.entity_name)
        if key not in seen:
            seen.add(key)
            merged.append(just)
    return merged


def build_evolution_lock_from_certified(ir: CertifiedVaultIR) -> EvolutionLockSpec:
    """Extract an EvolutionLockSpec from a sealed CertifiedVaultIR for wave N -> N+1."""
    locked_hubs = {
        h.hub_name: sorted({bk.col_name for bk in h.business_keys})
        for h in ir.hubs
        if not h.is_planned_concept
    }
    locked_links = {
        lnk.link_name: sorted({p.hub_name for p in lnk.participants}) for lnk in ir.links
    }
    locked_source_cols: dict[str, list[str]] = {}
    for b in ir.bronze_tables:
        locked_source_cols[b.source_table_id] = [c.name for c in b.columns]

    locked_sat_hd: dict[str, list[str]] = {}
    sat_epochs: dict[str, int] = {}
    for sat in ir.satellites:
        if sat.sat_kind in ("STANDARD", "PII", "MAS", "EXT"):
            locked_sat_hd[sat.sat_name] = sorted(a.name for a in sat.attributes)
        if sat.extends_sat_name and sat.companion_epoch is not None:
            prev = sat_epochs.get(sat.extends_sat_name, 1)
            sat_epochs[sat.extends_sat_name] = max(prev, sat.companion_epoch)
        elif sat.sat_kind in ("STANDARD", "PII", "MAS"):
            sat_epochs.setdefault(sat.sat_name, 1)

    hwms: dict[str, int] = {}
    for h in ir.hubs:
        if h.columns:
            hwms[h.hub_name] = max(c.field_id for c in h.columns)
    for lnk in ir.links:
        if lnk.columns:
            hwms[lnk.link_name] = max(c.field_id for c in lnk.columns)
    for sat in ir.satellites:
        if sat.columns:
            hwms[sat.sat_name] = max(c.field_id for c in sat.columns)

    return EvolutionLockSpec(
        wave_number=ir.wave_number,
        locked_hub_bks=locked_hubs,
        locked_links=locked_links,
        locked_source_columns=locked_source_cols,
        locked_sat_hash_diffs=locked_sat_hd,
        iceberg_high_water_marks=hwms,
        sat_epochs=sat_epochs,
    )


def run_v1_to_v6_gates(
    ir: CertifiedVaultIR,
    ctx: VerifierContext | None = None,
    *,
    prover: IRRuntimeProver | None = None,
    include_v6b: bool = True,
) -> VerifierReport:
    """Run symbolic Gates V1-V5, optional V6 IRRuntimeProver port, and V6b linter."""
    context = ctx or VerifierContext()
    effective_blocking_rules = resolve_effective_v6b_blocking_rules(
        context.policy, context.modeling_preferences
    )
    merged_justifications = _merge_justifications(
        ir.justifications, context.persisted_waivers
    )

    v1 = verify_v1_structure(ir, profiles=context.profiles, ontology=context.ontology)
    v2 = verify_v2_hash_and_bkcc(ir)
    v3 = verify_v3_sentinels_and_temporal(ir)
    v4 = verify_v4_unstructured(ir, context)
    v5 = verify_v5_evolution(
        ir,
        locks=context.evolution_locks,
        prior_ir=context.prior_certified_ir,
        cold_start_requested=context.cold_start_requested,
        policy=context.policy,
        blocking_v6b_rules=effective_blocking_rules,
    )

    v1_v6_diags: list[VerifierDiagnostic] = [*v1, *v2, *v3, *v4, *v5]
    v6_empirical: list[VerifierDiagnostic] = []
    prover_report: IRProverReport | None = None

    if prover is not None:
        prover_report = prover.prove(
            ir,
            context.profiles,
            policy=context.policy,
            source_rows_by_table=context.source_rows_by_table,
            table_sample_flags=context.table_sample_flags,
            real_row_proving=context.real_row_proving,
        )
        for rule_id, passed_flag in sorted(prover_report.dyn_results.items()):
            if not passed_flag:
                v1_v6_diags.append(
                    VerifierDiagnostic(
                        gate="V6",
                        rule_id=rule_id,
                        severity="BLOCKER",
                        entity_name="CertifiedVaultIR",
                        waivable=False,
                        message=f"Runtime prover invariant {rule_id} failed.",
                        counterexample_rows=prover_report.counterexamples[:5],
                    )
                )
        v1_v6_diags.extend(prover_report.ast_violations)
        v6_empirical.extend(
            apply_v6b_governance(
                prover_report.empirical_smells,
                policy=context.policy,
                modeling_preferences=context.modeling_preferences,
                justifications=merged_justifications,
                ontology_waivers=context.ontology_waivers,
            )
        )

    v6b_diags: list[VerifierDiagnostic] = []
    if include_v6b:
        v6b_diags = lint_v6b_antipatterns(
            ir,
            profiles=context.profiles,
            ontology=context.ontology,
            policy=context.policy,
            ontology_waivers=context.ontology_waivers,
            cdc_volatility_by_table=context.cdc_volatility_by_table,
            fk_turnover_by_table=context.fk_turnover_by_table,
            entity_count_overrides=context.entity_count_overrides,
            modeling_preferences=context.modeling_preferences,
            justifications=merged_justifications,
        )
    v6b_all = [*v6b_diags, *v6_empirical]

    v1_v6_fail = len(
        {(d.rule_id, d.entity_name) for d in v1_v6_diags if d.severity != "ADVISORY"}
    )
    v6b_fail, waived_cnt, advisory_cnt = evaluate_v6b_accounting(
        v6b_all,
        justifications=merged_justifications,
        ontology_waivers=context.ontology_waivers,
    )
    policy_spec = CEGAR_POLICY_SPECS[context.policy]
    blocking_v6b_fail = len(
        {
            (d.rule_id, d.entity_name)
            for d in v6b_all
            if (d.rule_id in effective_blocking_rules or policy_spec.require_zero_unwaived_v6b)
            and d.severity != "ADVISORY"
            and not is_diagnostic_waived(
                d,
                justifications=merged_justifications,
                ontology_waivers=context.ontology_waivers,
            )
        }
    )

    energy = compute_cegar_energy(v1_v6_fail, v6b_fail, policy=context.policy)
    deterministic_energy = compute_deterministic_energy(v1_v6_fail, blocking_v6b_fail)
    passed = deterministic_energy == 0

    return VerifierReport(
        passed=passed,
        v1_v6_diagnostics=v1_v6_diags,
        v6b_diagnostics=v6b_all,
        all_diagnostics=[*v1_v6_diags, *v6b_all],
        v1_v6_fail_count=v1_v6_fail,
        v6b_fail_count=v6b_fail,
        waived_v6b_count=waived_cnt,
        advisory_count=advisory_cnt,
        prover_report=prover_report,
        energy_e=energy,
        deterministic_energy=deterministic_energy,
        schema_proof=(
            dict(prover_report.schema_proof_results) if prover_report is not None else {}
        ),
        real_data_proof=(
            dict(prover_report.real_data_proof_results) if prover_report is not None else {}
        ),
    )


__all__ = [
    "build_evolution_lock_from_certified",
    "run_v1_to_v6_gates",
    "verify_v2_hash_and_bkcc",
    "verify_v3_sentinels_and_temporal",
    "verify_v4_unstructured",
    "verify_v5_evolution",
]
