"""Algorithm A.8: 6-Gate `axi-dv` Verification & Codemod Orchestrator.

Coordinates deterministic contract/AST codemods (`A.1`-`A.5`, `A.9a`), static
verification gates (`Gate 1`-`Gate 6`, `A.6`, `A.7`, `A.9b`), `@toon_v1` diagnostic
trace serialization, and `opendv-telemetry` observability ledger emission.
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from opendv_axi_verifier.codemods.alg_a1_satellite_split import (
    codemod_split_satellites_3axis,
)
from opendv_axi_verifier.codemods.alg_a2_enum_demotion import (
    codemod_demote_enum_hubs_to_ref,
)
from opendv_axi_verifier.codemods.alg_a3_link_effectivity import (
    codemod_inject_link_effectivity,
)
from opendv_axi_verifier.codemods.alg_a4_cold_start import (
    codemod_enforce_cold_start_extensibility,
)
from opendv_axi_verifier.codemods.alg_a9a_schema_drift import (
    codemod_intra_source_schema_drift,
)
from opendv_axi_verifier.toon_codec import (
    ToonTrace,
    ToonViolation,
    build_toon_trace_from_diagnostics,
    serialize_toon_v1,
)
from opendv_axi_verifier.verifiers.alg_a6_sqlglot_ast import (
    verify_sql_models_batch,
)
from opendv_axi_verifier.verifiers.alg_a7_evolution_lock import (
    verify_additive_evolution_invariants,
)
from opendv_axi_verifier.verifiers.alg_a9b_unstructured import (
    evaluate_unstructured_er_routing,
    verify_gate5_unstructured_invariants,
)
from opendv_axi_verifier.verifiers.gate1_contract_grain import (
    verify_gate1_contract_and_grain,
)
from opendv_axi_verifier.verifiers.gate3_networkx_dag import (
    verify_column_invariants,
    verify_structural_invariants,
)
from opendv_telemetry import VaultLedgerEmitter


@dataclass
class AxiDvPipelineResult:
    """Result container for `run_axi_dv_verification_pipeline`.

    Supports both 3-tuple unpacking (`status, transformed_contract, diagnostics = res`)
    and attribute access (`res.toon_trace`, `res.toon_text`, `res.applied_codemods`,
    `res.emitted_events`, `res.er_routing_decisions`).
    """

    status: str
    transformed_contract: dict[str, Any]
    diagnostics: list[str]
    toon_trace: ToonTrace
    toon_text: str
    applied_codemods: list[str] = field(default_factory=list)
    emitted_events: list[dict[str, Any]] = field(default_factory=list)
    er_routing_decisions: list[dict[str, Any]] = field(default_factory=list)

    def __iter__(self) -> Iterator[Any]:
        yield self.status
        yield self.transformed_contract
        yield self.diagnostics

    def __len__(self) -> int:
        return 3

    def __getitem__(self, index: int) -> Any:
        if index == 0:
            return self.status
        if index == 1:
            return self.transformed_contract
        if index == 2:
            return self.diagnostics
        raise IndexError(index)


def run_axi_dv_verification_pipeline(
    candidate_contract: dict[str, Any],
    ontology_graph: dict[str, Any],
    bronze_profile: dict[str, Any],
    bronze_link_cdc_profile: dict[str, dict[str, float]] | None = None,
    deployed_contract: dict[str, Any] | None = None,
    *,
    parsed_doc_samples: dict[str, str] | None = None,
    compiled_sql_models: dict[str, str] | None = None,
    apply_codemods: bool = True,
    auto_fix_schema_drift: bool = False,
    strict_schema: bool = False,
    require_grain_statements: bool = False,
    check_full_ledger_coverage: bool = False,
    check_isolated_hubs: bool = False,
    enforce_er_holds_as_diagnostics: bool = False,
    run_id: str = "run-axi-dv-default",
    attempt: int = 1,
    max_attempts: int = 3,
    ledger_emitter: VaultLedgerEmitter | None = None,
) -> AxiDvPipelineResult:
    """Algorithm A.8: End-to-End 6-Gate `axi-dv` Verification & Codemod Orchestrator.

    Returns an `AxiDvPipelineResult` which unpacks directly as
    `(status, transformed_contract, diagnostics)` while also exposing
    `.toon_trace`, `.toon_text`, `.applied_codemods`, `.emitted_events`,
    and `.er_routing_decisions`.
    """
    t0 = time.perf_counter()
    pre_contract = copy.deepcopy(candidate_contract)
    contract = copy.deepcopy(candidate_contract)
    diagnostics: list[str] = []
    applied_codemods: list[str] = []
    auto_fix_violations: list[ToonViolation] = []

    # 1. Deterministic AST & Contract Codemods (Algorithms A.4, A.2, A.3, A.1, A.9a)
    if apply_codemods:
        if deployed_contract is not None and auto_fix_schema_drift:
            before_drift = copy.deepcopy(contract)
            contract = codemod_intra_source_schema_drift(deployed_contract, contract)
            if contract != before_drift:
                applied_codemods.append("ALG_A9A_INTRA_SOURCE_SCHEMA_DRIFT")

        before_a4 = copy.deepcopy(contract)
        contract = codemod_enforce_cold_start_extensibility(
            contract, ontology_graph, bronze_profile
        )
        if contract != before_a4:
            applied_codemods.append("ALG_A4_COLD_START_EXTENSIBILITY")
            auto_fix_violations.append(
                ToonViolation(
                    gate="G2",
                    rule="ALG-A4",
                    action="AUTO-FIX",
                    target="hubs_and_links",
                    fix=(
                        "Enforced 3-Tier BKCC authority and split sparse optional FKs (U_nn < 0.95)"
                    ),
                )
            )

        before_a2 = copy.deepcopy(contract)
        contract = codemod_demote_enum_hubs_to_ref(contract, ontology_graph, bronze_profile)
        if contract != before_a2:
            applied_codemods.append("ALG_A2_ENUM_DEMOTION")
            auto_fix_violations.append(
                ToonViolation(
                    gate="G1",
                    rule="ALG-A2",
                    action="AUTO-FIX",
                    target="reference_tables",
                    fix="Demoted static low-cardinality enum Hub(s) to ref_* tables",
                )
            )

        before_a3 = copy.deepcopy(contract)
        contract = codemod_inject_link_effectivity(
            contract, bronze_link_cdc_profile or bronze_profile
        )
        if contract != before_a3:
            applied_codemods.append("ALG_A3_LINK_EFFECTIVITY")
            auto_fix_violations.append(
                ToonViolation(
                    gate="G3",
                    rule="ALG-A3",
                    action="AUTO-FIX",
                    target="links",
                    fix="Injected esat_* Effectivity Satellite for Link with positive FK swap rate",
                )
            )

        before_a1 = copy.deepcopy(contract)
        contract = codemod_split_satellites_3axis(contract, bronze_profile)
        if contract != before_a1:
            applied_codemods.append("ALG_A1_3AXIS_SATELLITE_SPLIT")
            auto_fix_violations.append(
                ToonViolation(
                    gate="G4",
                    rule="ALG-A1",
                    action="AUTO-FIX",
                    target="satellites",
                    fix=(
                        "Applied 3-axis Satellite split & Gate 4 YAGNI cost "
                        "guard (W_amp >= 50.0 MB/d)"
                    ),
                )
            )

    # 2. Check explicit human architect escalations in contract.unmapped_escalations
    for esc in contract.get("unmapped_escalations", []):
        if isinstance(esc, dict):
            reason = esc.get("escalation_reason", "MISSING_ONTOLOGY_CONCEPT")
            b_tbl = esc.get("bronze_table", "unknown")
            cols = esc.get("columns", [])
            question = esc.get("architect_question", "Requires human architect confirmation.")
            diagnostics.append(f"ASK_USER [Gate 1 - {reason}] on '{b_tbl}' ({cols}): {question}")

    # 3. Gate 0 / Gate 3 Assertion: Additive Evolution Lock (if upgrading V_N -> V_{N+1})
    if deployed_contract is not None:
        diagnostics.extend(verify_additive_evolution_invariants(deployed_contract, contract))

    # 4. Gate 1 & Gate 2 Assertions: Contract Schema, Grain Words, Anti-Silo, Surrogate Keys,
    #    and Column-Level Invariants (DV-COL-001..012)
    diagnostics.extend(
        verify_gate1_contract_and_grain(
            contract,
            ontology_graph=ontology_graph,
            bronze_profile=bronze_profile,
            strict_schema=strict_schema,
            require_grain_statements=require_grain_statements,
            check_full_ledger_coverage=check_full_ledger_coverage,
        )
    )
    diagnostics.extend(verify_column_invariants(contract))

    # 5. Gate 3 Assertion: NetworkX Bipartite DAG Structural Invariants (DV-STR-001..015)
    diagnostics.extend(
        verify_structural_invariants(
            contract,
            check_isolated_hubs=check_isolated_hubs,
        )
    )

    # 6. Gate 5 Assertion: Unstructured Grounding, Span & 3-Tier ER Routing (Algorithm A.9b)
    if parsed_doc_samples is not None or any(
        lnk.get("link_type") == "UNSTRUCTURED_EXTRACTION_LINK" for lnk in contract.get("links", [])
    ):
        diagnostics.extend(
            verify_gate5_unstructured_invariants(
                contract,
                parsed_doc_samples=parsed_doc_samples,
                bronze_profile=bronze_profile,
                enforce_er_holds_as_diagnostics=enforce_er_holds_as_diagnostics,
            )
        )

    er_decisions = evaluate_unstructured_er_routing(bronze_profile, parsed_doc_samples)

    # 7. Gate 6 Assertion: SQLGlot Physical SQL AST Linter (Algorithm A.6)
    if compiled_sql_models:
        unstructured_links = {
            str(lnk.get("name"))
            for lnk in contract.get("links", [])
            if isinstance(lnk, dict) and lnk.get("link_type") == "UNSTRUCTURED_EXTRACTION_LINK"
        }
        diagnostics.extend(
            verify_sql_models_batch(
                compiled_sql_models,
                unstructured_link_names=unstructured_links,
            )
        )

    # 8. Determine overall status & build @toon_v1 trace
    if diagnostics:
        has_reject = any(not d.startswith("ASK_USER") for d in diagnostics)
        status = "REJECT_WITH_TRACE" if has_reject else "ASK_USER"
    else:
        status = "PASS"
        diagnostics = ["ALL_GATES_GREEN: Contract verified and cold-start codemods applied."]

    toon_trace = build_toon_trace_from_diagnostics(
        diagnostics,
        run_id=run_id,
        attempt=attempt,
        max_attempts=max_attempts,
        status=status,
        extra_violations=auto_fix_violations,
    )
    toon_text = serialize_toon_v1(toon_trace)

    # 9. Optional Observability Ledger Emission via opendv-telemetry
    emitted_events: list[dict[str, Any]] = []
    if ledger_emitter is not None:
        elapsed_ms = max(0.01, (time.perf_counter() - t0) * 1000.0)
        pre_hash = ledger_emitter.cas_store.put(json.dumps(pre_contract, sort_keys=True))
        post_hash = ledger_emitter.cas_store.put(json.dumps(contract, sort_keys=True))
        toon_hash = ledger_emitter.cas_store.put(toon_text)

        primary_codemod = applied_codemods[0] if applied_codemods else "NONE"
        if status == "PASS":
            event_status = "AUTO_REPAIRED" if applied_codemods else "PASS"
        elif status == "ASK_USER":
            event_status = "ASK-USER"
        else:
            event_status = "REJECT-WITH-TRACE"
        remediation_obj = (
            {
                "codemod_type": primary_codemod,
                "target_entity": str(contract.get("contract_id", "dv_contract")),
                "patch_diff": f"Applied codemods: {', '.join(applied_codemods)}",
                "action_description": "Automated cold-start & YAGNI codemod repair",
            }
            if event_status == "AUTO_REPAIRED"
            else None
        )
        event_payload: dict[str, Any] = {
            "event_id": f"evt-{run_id}-g3-{attempt}",
            "run_id": run_id,
            "stage_id": "STAGE_05_AXI_DV_STATIC_GATES",
            "phase_id": "P6_VERIFICATION",
            "gate_id": "G3",
            "rule_code": (toon_trace.violations[0].rule if toon_trace.violations else "DV-STR-001"),
            "contract_version": "5.0",
            "agent_persona": "axi-dv",
            "status": event_status,
            "context_refs": {
                "contract_yaml_pre_sha256": pre_hash,
                "contract_yaml_post_sha256": post_hash,
                "toon_trace_sha256": toon_hash,
            },
            "data_classification": "INTERNAL",
            "layer1_story_banner": {
                "headline": f"6-Gate AXI Verifier completed with status {event_status}",
                "business_impact": (
                    "Verified Data Vault 2.0 structural topology, grain, and hash invariants."
                ),
                "verdict_pill": event_status,
            },
            "layer2_visual_payload": {
                "semantic_hash_pills": [],
                "summary_metrics": {
                    "execution_duration_ms": round(elapsed_ms, 3),
                    "applied_codemod_count": len(applied_codemods),
                    "violation_count": len(toon_trace.violations),
                },
            },
            "layer3_formal_proof": {
                "algorithm_ref": "Algorithm A.8 (6-Gate Orchestrator)",
                "toon_diagnostic_payload": toon_text,
            },
        }
        if remediation_obj is not None:
            event_payload["remediation"] = remediation_obj
        evt = ledger_emitter.emit(event_payload)
        emitted_events.append(evt)

    return AxiDvPipelineResult(
        status=status,
        transformed_contract=contract,
        diagnostics=diagnostics,
        toon_trace=toon_trace,
        toon_text=toon_text,
        applied_codemods=applied_codemods,
        emitted_events=emitted_events,
        er_routing_decisions=er_decisions,
    )
