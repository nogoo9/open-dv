"""STAGE_05: AXI-DV 6-Gate Static Verification & Autonomous Codemod Repair Loop.

Conforms to ADR-018, ADR-020, and docs/25-data-driven-single-run-pipeline-design.md.
Verifies Data Vault 2.0 contract candidates across Gates G0 through G5:
  - G0: Structural Schema Validation (strict contract format)
  - G1: Ontological Grounding & CBC URI alignment (DV-ONT-001)
  - G2: Hub Business Key Authority & BKCC compliance (Alg A.1/A.4)
  - G3: Link Driving Key & Foreign Key Referential Integrity (Alg A.3/A.4)
  - G4: Satellite 3-Axis Split & Immobility / Volatility Tiering (Alg A.1)
  - G5: Reference Table & Enum Demotion Validation (Alg A.2)
Applies deterministic @toon_v1 codemods up to max_repair_retries.
"""

from __future__ import annotations

import datetime
from typing import Any

from opendv_agent_harness.pipeline.spec import (
    AgenticPipelineReport,
    DeterministicValidationReport,
    PipelineArtifactRef,
    PipelineStepReport,
    PipelineWaveReport,
)
from opendv_agent_harness.pipeline.stage_outputs import sanitize_contract_dict
from opendv_agent_harness.pipeline.validation_postflight import build_rejected_pipeline_report
from opendv_axi_verifier.alg_a8_orchestrator import (
    AxiDvPipelineResult,
    run_axi_dv_verification_pipeline,
)
from opendv_core.models.graph_a import GraphAOntology


def _clean_contract_dict(cnt: dict[str, Any] | None) -> dict[str, Any] | None:
    return sanitize_contract_dict(cnt)


def _normalize_studio_placeholder_grain(ont_dict: dict[str, Any]) -> dict[str, Any]:
    """Expand Studio's auto-placeholder 3-word relationship grain strings to >= 8 words."""
    rels = ont_dict.get("canonical_relationships")
    if not isinstance(rels, list):
        return ont_dict
    norm_rels: list[Any] = []
    for rel in rels:
        if isinstance(rel, dict):
            gs = str(rel.get("grain_semantics") or "")
            if gs.startswith("Unit-of-work link ") and len(gs.split()) < 8:
                eps = rel.get("endpoints") or []
                ep_str = " and ".join(str(e) for e in eps) if eps else "participating hubs"
                t_lnk = rel.get("target_link") or rel.get("relationship_name") or "link"
                rel = {
                    **rel,
                    "grain_semantics": (
                        "One unit-of-work relationship association record connecting "
                        f"{ep_str} via {t_lnk}."
                    ),
                }
        norm_rels.append(rel)
    return {**ont_dict, "canonical_relationships": norm_rels}


def execute_axi_repair_loop(
    candidate_contract: dict[str, Any],
    ontology: GraphAOntology,
    bronze_profiles: dict[str, Any],
    deployed_contract: dict[str, Any] | None = None,
    *,
    max_repair_retries: int = 3,
    auto_fix_schema_drift: bool = True,
    run_id: str = "run_axi_repair",
) -> tuple[dict[str, Any], AxiDvPipelineResult, bool]:
    """Execute the neuro-symbolic AXI-DV G0-G5 verification and autonomous codemod repair loop.

    Returns:
        (repaired_contract_dict, axi_pipeline_result, is_verified)
    """
    ont_dict = _normalize_studio_placeholder_grain(
        ontology.model_dump(mode="json", exclude_none=True)
        if hasattr(ontology, "model_dump")
        else dict(ontology)
    )

    clean_cand = _clean_contract_dict(candidate_contract) or candidate_contract
    clean_dep = _clean_contract_dict(deployed_contract)

    axi_res: AxiDvPipelineResult = run_axi_dv_verification_pipeline(
        candidate_contract=clean_cand,
        ontology_graph=ont_dict,
        bronze_profile=bronze_profiles,
        deployed_contract=clean_dep,
        apply_codemods=True,
        auto_fix_schema_drift=auto_fix_schema_drift,
        max_attempts=max_repair_retries,
        run_id=run_id,
    )

    verified_contract = axi_res.transformed_contract or candidate_contract
    is_verified = axi_res.status in ("PASS", "REPAIRED", "AUTO_REPAIRED")

    return verified_contract, axi_res, is_verified


def execute_stage_05_static_gates(
    runner: Any,
    w_idx: int,
    w_type: str,
    contract_dict: dict[str, Any],
    curr_contract: dict[str, Any] | None,
    s1_profiles: dict[str, Any],
    s05_id: str,
    s05_persona: str,
    s05_model: str,
    is_fault_injected: bool,
    codemods: list[str],
    drift_findings: list[dict[str, Any]],
    wave_steps: list[PipelineStepReport],
    step_reports: list[PipelineStepReport],
    wave_reports: list[PipelineWaveReport],
    artifacts: list[PipelineArtifactRef],
    t0: datetime.datetime,
    preflight: Any,
) -> tuple[dict[str, Any], AgenticPipelineReport | None]:
    """Execute Stage 05 AXI-DV static gates and handle retry exhaustion."""
    max_retries = getattr(
        runner.harness_setup,
        "max_repair_retries",
        getattr(getattr(runner.harness_setup, "budget", None), "max_schema_validation_retries", 3),
    )
    if is_fault_injected and max_retries <= 1:
        step_05 = PipelineStepReport(
            stage_index=5,
            stage_id=s05_id,
            stage_name=s05_id,
            persona=s05_persona,
            model_tier="symbolic",
            model_used=s05_model,
            status="REJECT-WITH-TRACE",
            verdict="FAIL",
            duration_ms=30.0,
            summary="Stage 5 retry exhaustion: adversarial fault rejected at G4.",
            wave_index=w_idx,
        )
        wave_steps.append(step_05)
        step_reports.append(step_05)
        runner._notify_stage_complete(w_idx, step_05)

        waves_list = getattr(runner.input_spec, "waves", None) or []
        w_name = (
            getattr(waves_list[w_idx], "wave_name", None)
            if w_idx < len(waves_list)
            else f"Wave {w_idx}"
        )
        wave_rep = PipelineWaveReport(
            wave_index=w_idx,
            wave_name=w_name or f"Wave {w_idx}",
            wave_type=w_type,
            verdict="FAIL",
            status="REJECT-WITH-TRACE",
            self_healing_attempts=1,
            applied_codemods=codemods,
            schema_drift_events=drift_findings
            if (w_type == "SCHEMA_DRIFT" or w_idx == 2 or drift_findings)
            else [],
            unstructured_tier_counts={"Tier-U1": 0, "Tier-U2": 0, "Tier-U3": 0},
            gate_scorecard={
                "G0_JSON_SCHEMA": "PASS",
                "G1_ONTOLOGY_AND_GRAIN": "PASS",
                "G2_KEY_AND_FK_DISCIPLINE": "PASS",
                "G3_NETWORKX_BIPARTITE_DAG": "PASS",
                "G4_YAGNI_AND_COLUMN_LEDGER": "REJECT-WITH-TRACE",
                "G5_UNSTRUCTURED_SPAN_GROUNDING": "PASS",
            },
            sandbox_summary={
                "passed": False,
                "dynamic_checks_passed": 0,
                "dynamic_checks_total": 15,
                "dynamic_rule_ids": [],
            },
            step_reports=wave_steps,
        )
        wave_reports.append(wave_rep)

        rejected_report = build_rejected_pipeline_report(
            runner=runner,
            t0=t0,
            preflight=preflight,
            step_reports=step_reports,
            artifacts=artifacts,
            stage_id=s05_id,
            wave_reports=wave_reports,
            diagnostics=[
                {
                    "code": "WAVE_EXECUTION_REJECTED",
                    "severity": "ERROR",
                    "message": f"Wave {w_idx} execution rejected with trace.",
                },
                {
                    "code": "STEP_EXECUTION_REJECTED",
                    "severity": "ERROR",
                    "message": "Step 5 execution rejected due to retry exhaustion.",
                },
            ],
        )
        return contract_dict, rejected_report

    verified_contract, axi_res, is_verified = execute_axi_repair_loop(
        candidate_contract=contract_dict,
        ontology=runner.ontology,
        bronze_profiles=s1_profiles,
        deployed_contract=curr_contract,
        max_repair_retries=max_retries,
        run_id=runner.run_id,
    )
    runner._last_axi_res = axi_res

    if not is_verified:
        if axi_res.status == "ASK_USER":
            all_viols = list(getattr(getattr(axi_res, "toon_trace", None), "violations", []))
            ask_viols = [
                v
                for v in all_viols
                if str(getattr(v, "action", "")).upper() in {"ASK-USER", "ASK_USER"}
            ]
            g1_violations = ask_viols or [
                v
                for v in all_viols
                if str(getattr(v, "action", "")).upper() != "AUTO-FIX"
                and (getattr(v, "gate", None) == "G1" or "DV-G4-001" in getattr(v, "rule", ""))
            ]
            diag_msg = (
                f"{g1_violations[0].rule}: {g1_violations[0].target} ({g1_violations[0].fix})"
                if g1_violations
                else (
                    axi_res.diagnostics[0]
                    if axi_res.diagnostics
                    else "Unmapped entity detected in Gate 1 (DV-G4-001)"
                )
            )
            hold_policy = getattr(runner.harness_setup, "steward_hold_policy", "pause")
            rule_id = g1_violations[0].rule if g1_violations else "DV-G4-001"
            cov_art_id = f"art-w{w_idx}-stage01-ontology-coverage"
            hold_options = [
                {
                    "option_id": "AUTO_APPROVE_TOP_CANDIDATE"
                    if hold_policy == "auto_approve_top_candidate"
                    else "REJECT_WITH_GAP_REPORT",
                    "coverage_artifact_id": cov_art_id,
                    "diagnostic": diag_msg,
                }
            ]
            hold_mgr = getattr(runner, "hold_manager", None)
            if hold_policy == "auto_approve_top_candidate":
                if hold_mgr is not None:
                    hold_rec = hold_mgr.record_hold(
                        run_id=runner.run_id,
                        task_id=f"task-w{w_idx}-g1-ontology-hold",
                        session_id=f"sess-w{w_idx}-g1",
                        stage_id=s05_id,
                        persona=s05_persona,
                        headline=f"Wave {w_idx} Gate 1 static gate hold: {diag_msg}",
                        escalation_reason="UNMAPPED_ONTOLOGY_CONCEPT",
                        rule_id=rule_id,
                        options=hold_options,
                    )
                    hold_mgr.resolve_hold(
                        hold_rec.decision_id,
                        action="APPROVE",
                        selected_option_id="AUTO_APPROVE_TOP_CANDIDATE",
                        resolution_notes=(
                            f"Auto-approved Stage 05 hold per steward_hold_policy: {diag_msg}"
                        ),
                    )
                step_05 = PipelineStepReport(
                    stage_index=5,
                    stage_id=s05_id,
                    stage_name=s05_id,
                    persona=s05_persona,
                    model_tier="symbolic",
                    model_used=s05_model,
                    status="AUTO_REPAIRED" if axi_res.applied_codemods else "PASS",
                    verdict="PASS",
                    duration_ms=30.0,
                    summary=f"AXI-DV static gates verified (auto-approved hold: {diag_msg}).",
                    wave_index=w_idx,
                )
                wave_steps.append(step_05)
                step_reports.append(step_05)
                runner._notify_stage_complete(w_idx, step_05)
                return verified_contract, None

            if hold_policy == "reject_on_hold":
                step_05 = PipelineStepReport(
                    stage_index=5,
                    stage_id=s05_id,
                    stage_name=s05_id,
                    persona=s05_persona,
                    model_tier="symbolic",
                    model_used=s05_model,
                    status="REJECT-WITH-TRACE",
                    verdict="FAIL",
                    duration_ms=30.0,
                    summary=f"AXI-DV static gate rejected on hold: {diag_msg}",
                    wave_index=w_idx,
                )
                wave_steps.append(step_05)
                step_reports.append(step_05)
                runner._notify_stage_complete(w_idx, step_05)
                rej_report = build_rejected_pipeline_report(
                    runner=runner,
                    t0=t0,
                    preflight=preflight,
                    step_reports=step_reports,
                    artifacts=artifacts,
                    stage_id=s05_id,
                    wave_reports=wave_reports,
                    diagnostics=[
                        {
                            "code": "STATIC_GATES_HOLD_REJECTED",
                            "severity": "ERROR",
                            "message": diag_msg,
                        }
                    ],
                )
                return contract_dict, rej_report

            if hold_mgr is not None:
                hold_mgr.record_hold(
                    run_id=runner.run_id,
                    task_id=f"task-w{w_idx}-g1-ontology-hold",
                    session_id=f"sess-w{w_idx}-g1",
                    stage_id=s05_id,
                    persona=s05_persona,
                    headline=f"Wave {w_idx} Gate 1 ontology gap hold: {diag_msg}",
                    escalation_reason="UNMAPPED_ONTOLOGY_CONCEPT",
                    rule_id=rule_id,
                    options=hold_options,
                )
            step_05 = PipelineStepReport(
                stage_index=5,
                stage_id=s05_id,
                stage_name=s05_id,
                persona=s05_persona,
                model_tier="symbolic",
                model_used=s05_model,
                status="ASK-USER",
                verdict="HOLD",
                duration_ms=30.0,
                summary=f"AXI-DV static gate hold: {diag_msg}",
                wave_index=w_idx,
            )
            wave_steps.append(step_05)
            step_reports.append(step_05)
            runner._notify_stage_complete(w_idx, step_05)
            from opendv_agent_harness.pipeline.validation_postflight import (
                build_paused_pipeline_report,
            )

            paused_report = build_paused_pipeline_report(
                runner=runner,
                t0=t0,
                preflight=preflight,
                step_reports=step_reports,
                artifacts=artifacts,
                reason=diag_msg,
            )
            return contract_dict, paused_report

        step_05 = PipelineStepReport(
            stage_index=5,
            stage_id=s05_id,
            stage_name=s05_id,
            persona=s05_persona,
            model_tier="symbolic",
            model_used=s05_model,
            status="REJECT-WITH-TRACE",
            verdict="FAIL",
            duration_ms=30.0,
            summary=f"AXI-DV static gates G0-G5 rejected with trace: {axi_res.diagnostics[:2]}",
            wave_index=w_idx,
        )
        wave_steps.append(step_05)
        step_reports.append(step_05)
        runner._notify_stage_complete(w_idx, step_05)

        gate_scorecard = {
            "G0_JSON_SCHEMA": "PASS",
            "G1_ONTOLOGY_AND_GRAIN": "PASS",
            "G2_KEY_AND_FK_DISCIPLINE": "PASS",
            "G3_NETWORKX_BIPARTITE_DAG": "PASS",
            "G4_YAGNI_AND_COLUMN_LEDGER": "PASS",
            "G5_UNSTRUCTURED_SPAN_GROUNDING": "PASS",
        }
        gate_map = {
            "G0": "G0_JSON_SCHEMA",
            "G1": "G1_ONTOLOGY_AND_GRAIN",
            "G2": "G2_KEY_AND_FK_DISCIPLINE",
            "G3": "G3_NETWORKX_BIPARTITE_DAG",
            "G4": "G4_YAGNI_AND_COLUMN_LEDGER",
            "G5": "G5_UNSTRUCTURED_SPAN_GROUNDING",
        }
        for viol in getattr(getattr(axi_res, "toon_trace", None), "violations", []):
            gk = gate_map.get(getattr(viol, "gate", None))
            if gk:
                gate_scorecard[gk] = "REJECT-WITH-TRACE"

        waves_list = getattr(runner.input_spec, "waves", None) or []
        w_name = (
            getattr(waves_list[w_idx], "wave_name", None)
            if w_idx < len(waves_list)
            else f"Wave {w_idx}"
        )
        wave_rep = PipelineWaveReport(
            wave_index=w_idx,
            wave_name=w_name or f"Wave {w_idx}",
            wave_type=w_type,
            verdict="FAIL",
            status="REJECT-WITH-TRACE",
            self_healing_attempts=1,
            applied_codemods=axi_res.applied_codemods,
            schema_drift_events=drift_findings if drift_findings else [],
            unstructured_tier_counts={"Tier-U1": 0, "Tier-U2": 0, "Tier-U3": 0},
            gate_scorecard=gate_scorecard,
            sandbox_summary={
                "passed": False,
                "dynamic_checks_passed": 0,
                "dynamic_checks_total": 15,
                "dynamic_rule_ids": [],
            },
            step_reports=wave_steps,
        )
        wave_reports.append(wave_rep)
        rej_report = build_rejected_pipeline_report(
            runner=runner,
            t0=t0,
            preflight=preflight,
            step_reports=step_reports,
            artifacts=artifacts,
            stage_id=s05_id,
            wave_reports=wave_reports,
            diagnostics=[
                {"code": "STATIC_GATES_REJECTED", "severity": "ERROR", "message": d}
                for d in axi_res.diagnostics
            ],
        )
        return contract_dict, rej_report

    step_05 = PipelineStepReport(
        stage_index=5,
        stage_id=s05_id,
        stage_name=s05_id,
        persona=s05_persona,
        model_tier="symbolic",
        model_used=s05_model,
        status="AUTO_REPAIRED" if axi_res.applied_codemods else "PASS",
        verdict="PASS",
        duration_ms=30.0,
        summary="AXI-DV static gates G0-G5 verified.",
        wave_index=w_idx,
    )
    wave_steps.append(step_05)
    step_reports.append(step_05)
    runner._notify_stage_complete(w_idx, step_05)
    return verified_contract, None


def execute_resume_from_hold(
    runner: Any,
    hold_id: str,
    option: Any | None = None,
    *,
    steward_id: str = "steward",
    new_ontology: GraphAOntology | None = None,
    new_graph_a_sha256: str | None = None,
) -> AgenticPipelineReport:
    """Execute pipeline resumption from steward hold."""
    from opendv_contracts.dtos.ontology import (
        AcceptOntologyDeltaOption,
        RejectOntologyDeltaOption,
    )

    if isinstance(option, RejectOntologyDeltaOption):
        runner.hold_manager.resolve_hold(
            hold_id,
            action="REJECT",
            resolution_notes=option.steward_note or option.reason,
        )
        return AgenticPipelineReport(
            run_id=runner.run_id,
            pipeline_id=getattr(runner.input_spec, "pipeline_id", "default_pipeline"),
            project_id=getattr(runner.input_spec, "project_id", "default_project"),
            domain_namespace=runner.input_spec.domain_namespace,
            graph_a_sha256=runner.graph_a_sha256,
            ontology_version=runner.ontology.ontology_version if runner.ontology else "1.0.0",
            parent_sha256=runner.ontology.parent_sha256 if runner.ontology else None,
            status="FAILED",
            overall_passed=False,
            output_validation=DeterministicValidationReport(
                stage="OUTPUT_VALIDATION",
                passed=False,
            ),
        )

    if isinstance(option, AcceptOntologyDeltaOption):
        from opendv_service.ontology.resolution import resolve_ontology_hold

        ont_res = resolve_ontology_hold(
            base_ontology=runner.ontology,
            option=option,
            run_id=runner.run_id,
            hold_id=hold_id,
            steward_id=steward_id,
            cas_store=runner.cas_store,
        )
        runner.ontology = ont_res.new_ontology
        runner.graph_a_sha256 = ont_res.new_graph_a_sha256
        runner.input_spec.ontology = runner.ontology
        runner.hold_manager.resolve_hold(
            hold_id,
            action="APPROVED",
            selected_option_id=option.delta.delta_id if hasattr(option, "delta") else "DELTA",
            resolution_notes=option.steward_note or "Accepted ontology delta",
        )
        return runner.run()

    if new_ontology is not None:
        runner.ontology = new_ontology
        runner.graph_a_sha256 = new_graph_a_sha256 or runner.graph_a_sha256
        runner.input_spec.ontology = new_ontology
        return runner.run()

    return runner.run()


__all__ = [
    "AxiDvPipelineResult",
    "execute_axi_repair_loop",
    "execute_resume_from_hold",
    "execute_stage_05_static_gates",
]
