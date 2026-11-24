"""Gate V6b: Anti-pattern linter orchestration, waiver matching, and |V6b_fail| accounting.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from opendv.models.diagnostics import AntiPatternJustification, VerifierDiagnostic
from opendv.models.ontology import GraphAOntology
from opendv.models.policy import CegarPolicy
from opendv.models.project import OntologyWaiverSpec
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.verifier._v6b_ontology_ap import lint_ontology_antipatterns
from opendv.verifier._v6b_vault_ap import lint_vault_antipatterns


def lint_v6b_antipatterns(
    ir: CertifiedVaultIR,
    *,
    profiles: Sequence[SourceProfile] = (),
    ontology: GraphAOntology | None = None,
    policy: CegarPolicy = "balanced",
    ontology_waivers: Sequence[OntologyWaiverSpec] = (),
    cdc_volatility_by_table: Mapping[str, Mapping[str, float]] | None = None,
    fk_turnover_by_table: Mapping[str, Mapping[str, float]] | None = None,
    entity_count_overrides: Mapping[str, int] | None = None,
) -> list[VerifierDiagnostic]:
    """Run full V6b anti-pattern linter across ONT-AP-001..005 and DV-AP-001..010, 012."""
    _ = ontology_waivers
    diags: list[VerifierDiagnostic] = []
    if ontology is not None:
        diags.extend(
            lint_ontology_antipatterns(
                ontology,
                profiles,
                cdc_volatility_by_table=cdc_volatility_by_table,
            )
        )
    diags.extend(
        lint_vault_antipatterns(
            ir,
            profiles,
            ontology,
            policy=policy,
            cdc_volatility_by_table=cdc_volatility_by_table,
            fk_turnover_by_table=fk_turnover_by_table,
            entity_count_overrides=entity_count_overrides,
        )
    )
    return diags


def is_diagnostic_waived(
    diag: VerifierDiagnostic,
    *,
    justifications: Sequence[AntiPatternJustification] = (),
    ontology_waivers: Sequence[OntologyWaiverSpec] = (),
) -> bool:
    """Return True if a waivable V6b diagnostic has an accepted human/V7 or ontology waiver."""
    if not diag.waivable:
        return False
    for just in justifications:
        if (just.actor == "human" or just.reviewed_by_v7) and (
            (just.rule_id == diag.rule_id and just.entity_name == diag.entity_name)
            or (diag.suggested_waiver_id is not None and just.waiver_id == diag.suggested_waiver_id)
        ):
            return True
    if diag.rule_id.startswith("ONT-AP-"):
        for ow in ontology_waivers:
            if ow.rule_id == diag.rule_id and (
                (diag.concept_uri is not None and ow.concept_uri == diag.concept_uri)
                or (
                    diag.suggested_waiver_id is not None
                    and ow.waiver_id == diag.suggested_waiver_id
                )
            ):
                return True
    return False


def evaluate_v6b_accounting(
    diagnostics: Sequence[VerifierDiagnostic],
    *,
    justifications: Sequence[AntiPatternJustification] = (),
    ontology_waivers: Sequence[OntologyWaiverSpec] = (),
) -> tuple[int, int, int]:
    """Return (|V6b_fail|, waived_count, advisory_count) excluding ADVISORY & accepted waivers."""
    failing_keys: set[tuple[str, str]] = set()
    waived_keys: set[tuple[str, str]] = set()
    advisory_keys: set[tuple[str, str]] = set()

    for diag in diagnostics:
        key = (diag.rule_id, diag.entity_name)
        if diag.severity == "ADVISORY":
            advisory_keys.add(key)
            continue
        if is_diagnostic_waived(
            diag,
            justifications=justifications,
            ontology_waivers=ontology_waivers,
        ):
            waived_keys.add(key)
            continue
        failing_keys.add(key)

    return len(failing_keys), len(waived_keys), len(advisory_keys)


__all__ = [
    "evaluate_v6b_accounting",
    "is_diagnostic_waived",
    "lint_ontology_antipatterns",
    "lint_v6b_antipatterns",
    "lint_vault_antipatterns",
]
