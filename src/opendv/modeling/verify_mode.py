"""Layer-3 Mode-1 zero-LLM deterministic draft verification."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import ValidationError

from opendv.modeling.models import (
    DVModelingRequest,
    VerifyDraftRequest,
    VerifyDraftResult,
    attach_wave_delta,
    compute_preferences_sha256,
    resolve_next_action,
)
from opendv.modeling.prompts import scan_fragility_surface
from opendv.models.diagnostics import AntiPatternJustification, VerifierDiagnostic
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.modeling_preferences import RemediationHint
from opendv.models.pii_tokenizer import MASKED_PII_TOKEN, PII_TOKEN_RE
from opendv.models.policy import compute_cegar_energy, compute_deterministic_energy
from opendv.models.project import EvolutionLockSpec
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir import DraftVaultIR
from opendv.models.vault_ir_certified import CertifiedVaultIR, SeedRecordsSpec
from opendv.verifier.l1_codemods import run_l1_codemods
from opendv.verifier.ports import IRRuntimeProver, VerifierContext, VerifierReport
from opendv.verifier.v2_5_gates import build_evolution_lock_from_certified, run_v1_to_v6_gates


def validate_source_rows_pii_tokenized(
    profiles: Sequence[SourceProfile],
    source_rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]] | None,
) -> None:
    """Fail closed if any PII-tagged column in source_rows_by_table is untokenized."""
    if not source_rows_by_table:
        return
    for prof in profiles:
        for tbl in prof.tables:
            pii_cols = [c.col_name for c in tbl.columns if c.pii_tags]
            if not pii_cols:
                continue
            rows = source_rows_by_table.get(tbl.table_id)
            if rows is None:
                rows = source_rows_by_table.get(tbl.table_name)
            if not rows:
                continue
            for row_idx, row in enumerate(rows):
                for col_name in pii_cols:
                    val = row.get(col_name)
                    if val is None:
                        continue
                    if isinstance(val, str) and PII_TOKEN_RE.fullmatch(val):
                        continue
                    err_code = (
                        ErrorCode.REDACTED_PLACEHOLDER_LEAK
                        if val == MASKED_PII_TOKEN
                        else ErrorCode.SYNTAX_INVALID_ARGUMENT
                    )
                    raise OpenDVError(
                        f"Untokenized scalar in PII-tagged column '{tbl.table_id}.{col_name}' "
                        f"at row {row_idx}; expected <PII:16hex> token before proving.",
                        error_code=err_code,
                    )


def _merge_persisted_waivers(
    ir_justifications: Sequence[AntiPatternJustification],
    persisted_waivers: Sequence[AntiPatternJustification],
) -> list[AntiPatternJustification]:
    """Preserve and merge persisted Steward waivers onto IR justifications."""
    merged = list(ir_justifications)
    for pw in persisted_waivers:
        matched_idx: int | None = None
        for idx, existing in enumerate(merged):
            if existing.waiver_id == pw.waiver_id or (
                existing.rule_id == pw.rule_id and existing.entity_name == pw.entity_name
            ):
                matched_idx = idx
                break
        if matched_idx is None:
            merged.append(pw)
        else:
            prev = merged[matched_idx]
            merged[matched_idx] = pw.model_copy(
                update={"reviewed_by_v7": prev.reviewed_by_v7 or pw.reviewed_by_v7}
            )
    return merged


def _build_verifier_context(
    request: VerifyDraftRequest | DVModelingRequest,
) -> VerifierContext:
    base = request.verifier_ctx or VerifierContext()
    rows_copy = (
        {k: [dict(r) for r in v] for k, v in request.source_rows_by_table.items()}
        if request.source_rows_by_table is not None
        else base.source_rows_by_table
    )
    flags_copy = (
        dict(request.table_sample_flags)
        if request.table_sample_flags is not None
        else base.table_sample_flags
    )
    cdc_copy = (
        {k: dict(v) for k, v in request.cdc_volatility_by_table.items()}
        if request.cdc_volatility_by_table is not None
        else dict(base.cdc_volatility_by_table)
    )
    fk_copy = (
        {k: dict(v) for k, v in request.fk_turnover_by_table.items()}
        if request.fk_turnover_by_table is not None
        else dict(base.fk_turnover_by_table)
    )
    real_row = (
        bool(request.real_row_proving)
        if request.real_row_proving is not None
        else base.real_row_proving
    )
    return base.model_copy(
        update={
            "profiles": list(request.profiles),
            "ontology": request.ontology,
            "evolution_locks": request.wave.evolution_locks,
            "prior_certified_ir": request.wave.prior_certified_ir,
            "policy": request.policy,
            "modeling_preferences": request.modeling_preferences,
            "persisted_waivers": list(request.persisted_waivers),
            "source_rows_by_table": rows_copy,
            "table_sample_flags": flags_copy,
            "real_row_proving": real_row,
            "cdc_volatility_by_table": cdc_copy,
            "fk_turnover_by_table": fk_copy,
        }
    )


def _suggest_fix(diag: VerifierDiagnostic) -> str:
    if diag.rule_id == "INV-05":
        return "Remove descriptive attributes from Hub and place them in a Satellite."
    if diag.rule_id == "DV-COL-001":
        return (
            "Add missing ColumnTriageEntry or update UNRESOLVED columns to MODELED, "
            "SYSTEM_METADATA, SYSTEM_SURROGATE_DROPPED, or OMITTED_INTENTIONALLY (>= 15 chars)."
        )
    if diag.rule_id == "DV-COL-002":
        return "Ensure Hub business_keys reference valid non-empty source columns."
    if diag.rule_id == "DV-STR-009":
        return (
            f"Remove Satellite '{diag.entity_name}' from its TRANSACTIONAL Link: "
            "move non-historized event payload columns into DraftLinkSpec.attributes on the "
            "t_lnk_* Link, re-parent the Satellite to an owning Hub, or change "
            "link_kind='STANDARD'."
        )
    if diag.rule_id == "DV-STR-016":
        return "Repair BridgeHopSpec path so Link hops form a connected acyclic chain."
    if diag.rule_id == "DV-STR-017":
        return "Align survivorship_priority entries with valid (source_id or source_id:table_id)."
    if diag.rule_id == "DV-AP-009":
        return "Isolate PII columns into a sat_*_pii Satellite (sat_kind='PII') or waive."
    if diag.rule_id == "DV-AP-003":
        return "Replace surrogate/float BK with a natural key, or use TIER_3_LOCAL_SCOPED / waive."
    if diag.waivable:
        return (
            f"Remediate {diag.rule_id} on '{diag.entity_name}' ({diag.message}) "
            "or submit an audited V6b waiver / adjust ModelingPreferences."
        )
    return f"Repair {diag.rule_id} on '{diag.entity_name}': {diag.message}"


def _build_remediation_hints(report: VerifierReport) -> list[RemediationHint]:
    hints: list[RemediationHint] = list(report.remediation_hints)
    seen: set[tuple[str, str]] = {(h.rule_id, h.entity_name) for h in hints}
    if report.prover_report is not None:
        for ce in report.prover_report.counterexamples:
            if not isinstance(ce, dict) or not ce.get("remediation_hint"):
                continue
            rule_id = str(ce.get("rule_id") or "DV-DYN-004")
            entity_name = str(ce.get("entity_name") or "CertifiedVaultIR")
            key = (rule_id, entity_name)
            if key in seen:
                continue
            seen.add(key)
            hints.append(
                RemediationHint(
                    gate="V6",
                    rule_id=rule_id,
                    entity_name=entity_name,
                    suggested_fix=str(ce["remediation_hint"]),
                    waivable=False,
                )
            )
    for diag in report.all_diagnostics:
        if diag.severity == "ADVISORY":
            continue
        key = (diag.rule_id, diag.entity_name)
        if key in seen:
            continue
        seen.add(key)
        hints.append(
            RemediationHint(
                gate=diag.gate,
                rule_id=diag.rule_id,
                entity_name=diag.entity_name,
                suggested_fix=_suggest_fix(diag),
                waivable=diag.waivable,
            )
        )
    return hints


def _inject_extra_v1_diags(
    report: VerifierReport,
    extra_diags: Sequence[VerifierDiagnostic],
    *,
    request: VerifyDraftRequest | DVModelingRequest,
) -> VerifierReport:
    if not extra_diags:
        return report
    existing_keys = {(d.rule_id, d.entity_name) for d in report.v1_v6_diagnostics}
    added = [d for d in extra_diags if (d.rule_id, d.entity_name) not in existing_keys]
    if not added:
        return report
    v1_v6 = [*report.v1_v6_diagnostics, *added]
    all_diags = [*v1_v6, *report.v6b_diagnostics]
    v1_v6_fail = len({(d.rule_id, d.entity_name) for d in v1_v6 if d.severity != "ADVISORY"})
    blocking_v6b_approx = max(
        0, (report.deterministic_energy - 100 * report.v1_v6_fail_count) // 10
    )
    det_energy = compute_deterministic_energy(v1_v6_fail, blocking_v6b_approx)
    energy = compute_cegar_energy(v1_v6_fail, report.v6b_fail_count, policy=request.policy)
    return report.model_copy(
        update={
            "passed": False,
            "v1_v6_diagnostics": v1_v6,
            "all_diagnostics": all_diags,
            "v1_v6_fail_count": v1_v6_fail,
            "energy_e": energy,
            "deterministic_energy": det_energy,
        }
    )


def verify_draft_vault(
    request: VerifyDraftRequest | DVModelingRequest,
    draft_ir: DraftVaultIR | None = None,
    *,
    prover: IRRuntimeProver | None = None,
) -> VerifyDraftResult:
    """Execute zero-LLM Mode-1 deterministic verification (L1 + V1-V6 + tiered V6b)."""
    if isinstance(request, VerifyDraftRequest):
        effective_draft = draft_ir if draft_ir is not None else request.draft_ir
    elif isinstance(request, DVModelingRequest):
        if draft_ir is None:
            raise OpenDVError(
                "draft_ir is required when calling verify_draft_vault with DVModelingRequest.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        effective_draft = draft_ir
    else:
        raise OpenDVError(
            f"Unsupported request type {type(request).__name__}.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    if request.business_context_hints and request.business_context_hints.strip():
        raise OpenDVError(
            "business_context_hints is forbidden in Mode-1 verify_draft_vault; "
            "translate hints into DraftVaultIR before verification.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if request.guide_markdown and request.guide_markdown.strip():
        raise OpenDVError(
            "guide_markdown is forbidden in Mode-1 verify_draft_vault.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    effective_prover = prover if prover is not None else request.prover
    if effective_prover is None and request.policy != "symbolic_only":
        raise OpenDVError(
            f"IRRuntimeProver is required for policy='{request.policy}' in verify_draft_vault.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    validate_source_rows_pii_tokenized(request.profiles, request.source_rows_by_table)

    inv05_diags: list[VerifierDiagnostic] = [
        VerifierDiagnostic(
            gate="V1",
            rule_id="INV-05",
            severity="BLOCKER",
            entity_name=h.hub_name,
            waivable=False,
            message=f"Hub {h.hub_name} violates INV-05: Hub attributes must be empty.",
        )
        for h in effective_draft.hubs
        if h.attributes
    ]
    lowering_draft = (
        effective_draft.model_copy(
            update={
                "hubs": [
                    h.model_copy(update={"attributes": []}) if h.attributes else h
                    for h in effective_draft.hubs
                ]
            }
        )
        if inv05_diags
        else effective_draft
    )

    structural_diags: list[VerifierDiagnostic] = [*inv05_diags]
    try:
        rewritten_draft, cert_ir, _ = run_l1_codemods(
            lowering_draft,
            profiles=request.profiles,
            ontology=request.ontology,
            locks=request.wave.evolution_locks,
            prior_ir=request.wave.prior_certified_ir,
            modeling_preferences=request.modeling_preferences,
            fk_turnover_by_table=request.fk_turnover_by_table,
            cdc_volatility_by_table=request.cdc_volatility_by_table,
        )
    except (ValueError, ValidationError) as exc:
        rewritten_draft = lowering_draft
        cert_ir = CertifiedVaultIR(
            project_id=effective_draft.project_id,
            ontology_version=effective_draft.ontology_version,
            wave_number=effective_draft.wave_number,
            seed_records=SeedRecordsSpec(),
            column_triage=list(effective_draft.column_triage),
            justifications=list(effective_draft.justifications),
        )
        structural_diags.append(
            VerifierDiagnostic(
                gate="V1",
                rule_id="DV-STR-001",
                severity="BLOCKER",
                entity_name=effective_draft.project_id,
                waivable=False,
                message=f"L1 lowering rejected malformed DraftVaultIR: {exc}",
            )
        )

    for hub in rewritten_draft.hubs:
        if hub.attributes:
            structural_diags.append(
                VerifierDiagnostic(
                    gate="V1",
                    rule_id="INV-05",
                    severity="BLOCKER",
                    entity_name=hub.hub_name,
                    waivable=False,
                    message=f"Hub {hub.hub_name} still carries attributes after L1 lowering.",
                )
            )

    if request.persisted_waivers:
        cert_ir = cert_ir.model_copy(
            update={
                "justifications": _merge_persisted_waivers(
                    cert_ir.justifications, request.persisted_waivers
                )
            }
        )

    ctx = _build_verifier_context(request)
    report = run_v1_to_v6_gates(cert_ir, ctx, prover=effective_prover)
    report = _inject_extra_v1_diags(report, structural_diags, request=request)

    cert_ir = attach_wave_delta(
        cert_ir,
        prior_ir=request.wave.prior_certified_ir,
        locks=request.wave.evolution_locks,
    )
    fragility = scan_fragility_surface(cert_ir, request.profiles, request.wave.evolution_locks)

    is_certified = (
        report.passed
        and report.v1_v6_fail_count == 0
        and report.v6b_fail_count == 0
    )
    status: Literal["CERTIFIED_FAST", "UNCERTIFIED_DRAFT"] = (
        "CERTIFIED_FAST" if is_certified else "UNCERTIFIED_DRAFT"
    )
    evo_lock: EvolutionLockSpec | None = (
        build_evolution_lock_from_certified(cert_ir) if is_certified else None
    )
    hints = _build_remediation_hints(report)
    report = report.model_copy(update={"remediation_hints": hints})
    next_action = resolve_next_action(status=status, report=report)

    return VerifyDraftResult(
        status=status,
        next_action=next_action,
        draft_ir=rewritten_draft,
        certified_ir=cert_ir,
        evolution_lock=evo_lock,
        verifier_report=report,
        remediation_hints=hints,
        deterministic_energy=report.deterministic_energy,
        final_energy_e=report.energy_e,
        fragility_summary=fragility,
        preferences_sha256=compute_preferences_sha256(request.modeling_preferences),
    )


__all__ = [
    "validate_source_rows_pii_tokenized",
    "verify_draft_vault",
]
