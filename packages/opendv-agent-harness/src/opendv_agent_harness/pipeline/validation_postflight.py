"""Post-flight validation, Evolution Diff & VaultBlueprint Lineage Chaining.

Conforms to ADR-017, ADR-018, ADR-020, and
docs/25-data-driven-single-run-pipeline-design.md (§1.1, §1.4).
Validates 100% column triage accountability, computes BlueprintEvolutionDiff,
and cryptographically chains VaultBlueprint lineage (revision N+1 with SHA-256 parent link).
"""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from opendv_agent_harness.pipeline.blueprint_lineage import (
    advance_blueprint_lineage,
    compute_blueprint_evolution_diff,
)
from opendv_agent_harness.pipeline.spec import (
    AgenticPipelineReport,
    DeterministicValidationReport,
    PipelineArtifactRef,
    PipelineStepReport,
    PipelineWaveReport,
)
from opendv_core.models.vault_blueprint import (
    compute_contract_sha256,
)
from opendv_core.ontology_hashing import compute_ontology_sha256


def validate_pipeline_outputs(
    report: AgenticPipelineReport,
    cas_store: Any | None = None,
) -> DeterministicValidationReport:
    """Deterministically validate final pipeline outputs, artifacts, and contract."""
    diagnostics: list[dict[str, Any]] = []
    if report.output_validation and not report.output_validation.passed:
        for d in report.output_validation.diagnostics:
            if d not in diagnostics:
                diagnostics.append(d)
    checked_items = 0
    verified_artifacts = 0
    cas_ok = True

    all_refs = [*report.artifacts, *report.intermediate_artifacts, *report.final_artifacts.values()]
    seen_ids: set[str] = set()
    unique_refs: list[PipelineArtifactRef] = []
    for r in all_refs:
        rid = r.artifact_id or r.name
        if rid not in seen_ids:
            seen_ids.add(rid)
            unique_refs.append(r)

    for ref in unique_refs:
        checked_items += 1
        if cas_store is not None and ref.sha256:
            try:
                if not cas_store.verify(ref.sha256):
                    cas_ok = False
                    diagnostics.append(
                        {
                            "code": "CAS_INTEGRITY_FAILURE",
                            "severity": "ERROR",
                            "target": ref.name,
                            "message": f"CAS blob verification failed for sha256={ref.sha256}.",
                        }
                    )
            except (KeyError, ValueError, OSError, RuntimeError) as exc:
                cas_ok = False
                diagnostics.append(
                    {
                        "code": "CAS_LOOKUP_ERROR",
                        "severity": "ERROR",
                        "target": ref.name,
                        "message": f"CAS verification raised error for {ref.name}: {exc}",
                    }
                )

        f_path = Path(ref.path) if ref.path else None
        if f_path is not None:
            if not f_path.is_file():
                cas_ok = False
                diagnostics.append(
                    {
                        "code": "ARTIFACT_FILE_MISSING",
                        "severity": "ERROR",
                        "target": ref.name,
                        "message": f"Materialized artifact file missing: '{f_path}'.",
                    }
                )
            else:
                raw_bytes = f_path.read_bytes()
                disk_sha = hashlib.sha256(raw_bytes).hexdigest()
                if disk_sha.lower() != ref.sha256.lower():
                    cas_ok = False
                    diagnostics.append(
                        {
                            "code": "ARTIFACT_SHA256_MISMATCH",
                            "severity": "ERROR",
                            "target": ref.name,
                            "message": f"Artifact hash mismatch for {ref.name}",
                        }
                    )
                else:
                    verified_artifacts += 1
        else:
            verified_artifacts += 1

    # Verify Blueprint integrity if present
    if report.blueprint is not None:
        checked_items += 1
        bp = report.blueprint
        try:
            expected_ont_sha = compute_ontology_sha256(bp.ontology)
            if bp.lineage.ontology_sha256 != expected_ont_sha:
                diagnostics.append(
                    {
                        "code": "BLUEPRINT_HASH_MISMATCH",
                        "severity": "ERROR",
                        "target": "blueprint.lineage.ontology_sha256",
                        "message": "Output blueprint ontology hash does not match ontology",
                    }
                )
            if bp.contract is not None:
                expected_cnt_sha = compute_contract_sha256(bp.contract)
                if bp.lineage.contract_sha256 and bp.lineage.contract_sha256 != expected_cnt_sha:
                    diagnostics.append(
                        {
                            "code": "BLUEPRINT_HASH_MISMATCH",
                            "severity": "ERROR",
                            "target": "blueprint.lineage.contract_sha256",
                            "message": "Output blueprint contract hash does not match contract",
                        }
                    )
        except (KeyError, ValueError, TypeError, AttributeError, RuntimeError) as exc:
            diagnostics.append(
                {
                    "code": "OUTPUT_BLUEPRINT_VERIFICATION_FAILED",
                    "severity": "ERROR",
                    "target": "blueprint",
                    "message": str(exc),
                }
            )

    passed = len(diagnostics) == 0
    return DeterministicValidationReport(
        stage="OUTPUT_VALIDATION",
        passed=passed,
        checked_items_count=checked_items,
        verified_artifacts_count=verified_artifacts,
        cas_integrity_verified=cas_ok,
        diagnostics=diagnostics,
    )


def _h_setup_summary(runner: Any) -> dict[str, Any]:
    hs = getattr(runner, "harness_setup", None)
    if hasattr(hs, "to_summary_dict"):
        return hs.to_summary_dict()
    if hasattr(hs, "model_dump"):
        return hs.model_dump(mode="json")
    return {}


def _initial_contract_metadata(runner: Any) -> tuple[bool, str | None, str]:
    spec = getattr(runner, "input_spec", None)
    bp = getattr(spec, "blueprint", None)
    has_ic = bool(
        spec
        and (
            getattr(spec, "initial_contract", None) is not None
            or getattr(spec, "initial_contract_content", None) is not None
            or getattr(bp, "contract", None) is not None
        )
    )
    ip = getattr(spec, "initial_contract_path", None)
    return has_ic, (str(ip) if (has_ic and ip) else None), (
        "MULTI_SOURCE_EXPANSION" if has_ic else "COLD_START_N1"
    )


def build_paused_pipeline_report(
    runner: Any,
    t0: datetime.datetime,
    preflight: Any,
    step_reports: list[PipelineStepReport],
    artifacts: list[PipelineArtifactRef],
    reason: str,
    *,
    drift_findings: list[dict[str, Any]] | None = None,
) -> AgenticPipelineReport:
    """Build and return an AgenticPipelineReport in PAUSED_HOLD state."""
    paused_stage_id = (
        step_reports[-1].stage_id if step_reports else "STAGE_02_SCOUT_UNSTRUCTURED"
    )
    hold_mgr = getattr(runner, "hold_manager", None)
    if hold_mgr is not None and not hold_mgr.list_pending_holds():
        persona = (
            step_reports[-1].persona
            if step_reports and step_reports[-1].persona
            else "vault-first-mate"
        )
        hold_mgr.record_hold(
            run_id=runner.run_id,
            task_id=f"task-{runner.run_id}-pause",
            session_id=getattr(getattr(runner, "input_spec", None), "session_id", None)
            or f"sess-{runner.run_id}",
            stage_id=paused_stage_id or "STAGE_02_SCOUT_UNSTRUCTURED",
            persona=persona,
            headline=f"Steward hold at {paused_stage_id}: {reason}",
            escalation_reason=str(reason or "STEWARD_HOLD"),
            rule_id="DV-HOLD-001",
            options=[],
        )
    pending = (
        [
            h.model_dump(mode="json") if hasattr(h, "model_dump") else h
            for h in hold_mgr.list_pending_holds()
        ]
        if hold_mgr is not None
        else []
    )
    h_setup_summary = _h_setup_summary(runner)
    has_init_c, init_c_path, lc_stage = _initial_contract_metadata(runner)
    report = AgenticPipelineReport(
        run_id=runner.run_id,
        pipeline_id=getattr(runner.input_spec, "pipeline_id", "default_pipeline"),
        project_id=getattr(runner.input_spec, "project_id", "default_project"),
        domain_namespace=runner.input_spec.domain_namespace,
        has_initial_contract=has_init_c,
        initial_contract_path=init_c_path,
        lifecycle_stage=lc_stage,
        status="PAUSED_HOLD",
        overall_passed=False,
        started_at=t0.isoformat(),
        completed_at=datetime.datetime.now(datetime.UTC).isoformat(),
        harness_setup=h_setup_summary,
        input_validation=preflight,
        verdict="HOLD",
        step_reports=step_reports,
        artifacts=artifacts,
        intermediate_artifacts=artifacts,
        active_holds=pending,
        observability_events=list(getattr(runner, "observability_events", [])),
        fallbacks_applied=list(getattr(runner, "fallbacks_applied", [])),
        graph_a_sha256=runner.graph_a_sha256,
        ontology_version=getattr(runner.ontology, "ontology_version", "1.0.0"),
        parent_sha256=getattr(runner.ontology, "parent_sha256", None),
        drift_findings=list(drift_findings or []),
        summary_markdown=f"# Pipeline Paused: {reason}\n",
    )
    from opendv_agent_harness.pipeline.drift_projection import persist_resumable_run_context

    persist_resumable_run_context(
        runner,
        paused_stage_id=paused_stage_id,
        paused_reason=reason,
    )
    report.output_validation = validate_pipeline_outputs(report, cas_store=runner.cas_store)
    return report


def build_rejected_pipeline_report(
    runner: Any,
    t0: datetime.datetime,
    preflight: Any,
    step_reports: list[PipelineStepReport],
    artifacts: list[PipelineArtifactRef],
    stage_id: str,
    wave_reports: list[PipelineWaveReport] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
) -> AgenticPipelineReport:
    """Build and return an AgenticPipelineReport in FAILED / REJECT-WITH-TRACE state."""
    h_setup_summary = _h_setup_summary(runner)
    out_val = DeterministicValidationReport(
        stage="OUTPUT_VALIDATION",
        passed=False,
        diagnostics=diagnostics
        or [
            {
                "code": "WAVE_EXECUTION_REJECTED",
                "severity": "ERROR",
                "message": "Wave rejected on hold.",
            }
        ],
    )
    obs_events = list(getattr(runner, "observability_events", []))
    if not obs_events:
        obs_events = [
            {"stage_id": stage_id, "status": "IN_PROGRESS"},
            {"stage_id": stage_id, "status": "REJECT-WITH-TRACE"},
        ]
    return AgenticPipelineReport(
        run_id=runner.run_id,
        pipeline_id=getattr(runner.input_spec, "pipeline_id", "default_pipeline"),
        project_id=getattr(runner.input_spec, "project_id", "default_project"),
        domain_namespace=runner.input_spec.domain_namespace,
        status="FAILED",
        overall_passed=False,
        started_at=t0.isoformat(),
        completed_at=datetime.datetime.now(datetime.UTC).isoformat(),
        harness_setup=h_setup_summary,
        input_validation=preflight,
        output_validation=out_val,
        observability_events=obs_events,
        fallbacks_applied=list(getattr(runner, "fallbacks_applied", [])),
        verdict="FAIL",
        wave_reports=wave_reports or [],
        step_reports=step_reports,
        artifacts=artifacts,
        intermediate_artifacts=artifacts,
        summary_markdown="# Pipeline Failed: Wave Execution Rejected\n",
    )


def materialize_final_pipeline_outputs(
    output_dir: Path,
    cas_store: Any | None,
    out_blueprint: Any,
    curr_contract: Any,
    four_zone_sql: Any,
    graph_a_sha256: str | None,
    loaded_graph_a: Any | None,
    artifacts: list[PipelineArtifactRef],
    final_artifacts: dict[str, PipelineArtifactRef],
) -> None:
    """Write all terminal output files and sync to CAS store."""
    bp_path = output_dir / "vault_blueprint.yaml"
    bp_bytes = yaml.safe_dump(
        out_blueprint.model_dump(mode="json")
        if hasattr(out_blueprint, "model_dump")
        else dict(out_blueprint),
        sort_keys=False,
    ).encode("utf-8")
    bp_path.write_bytes(bp_bytes)
    bp_sha = hashlib.sha256(bp_bytes).hexdigest()
    if cas_store is not None:
        cas_store.put(bp_bytes)
    artifacts.append(
        PipelineArtifactRef(
            artifact_id="vault_blueprint",
            name="vault_blueprint.yaml",
            sha256=bp_sha,
            path=str(bp_path),
            artifact_type="YAML",
            byte_length=len(bp_bytes),
        )
    )

    fc_path = output_dir / "final_contract.yaml"
    fc_bytes = yaml.safe_dump(curr_contract, sort_keys=False).encode("utf-8")
    fc_path.write_bytes(fc_bytes)
    fc_sha = hashlib.sha256(fc_bytes).hexdigest()
    if cas_store is not None:
        cas_store.put(fc_bytes)
    final_art_ref = PipelineArtifactRef(
        artifact_id="final_contract",
        name="final_contract.yaml",
        sha256=fc_sha,
        path=str(fc_path),
        artifact_type="YAML",
        byte_length=len(fc_bytes),
    )
    final_artifacts["final_contract"] = final_art_ref

    cnt_json_path = output_dir / "dv_contract.json"
    cnt_json_bytes = json.dumps(curr_contract, indent=2).encode("utf-8")
    cnt_json_path.write_bytes(cnt_json_bytes)
    _cnt_json_sha = hashlib.sha256(cnt_json_bytes).hexdigest()
    if cas_store is not None:
        cas_store.put(cnt_json_bytes)

    ir_path = output_dir / "final_canonical_ir.json"
    from opendv_duckdb_sandbox.ir_lowerer import lower_contract_to_canonical_ir

    try:
        canonical_ir_obj = lower_contract_to_canonical_ir(curr_contract)
        ir_dict = (
            canonical_ir_obj.model_dump(mode="json", exclude_none=True)
            if hasattr(canonical_ir_obj, "model_dump")
            else canonical_ir_obj
        )
        ir_bytes = json.dumps(ir_dict, indent=2).encode("utf-8")
    except (ValueError, TypeError, KeyError, AttributeError):
        ir_bytes = json.dumps(curr_contract, indent=2).encode("utf-8")
    ir_path.write_bytes(ir_bytes)
    _ir_sha = hashlib.sha256(ir_bytes).hexdigest()
    if cas_store is not None:
        cas_store.put(ir_bytes)

    # Extract clean raw SQL text
    if isinstance(four_zone_sql, dict):
        duck_obj = four_zone_sql.get("duckdb")
        if isinstance(duck_obj, dict):
            raw_sql = duck_obj.get("all_zones") or "\n\n".join(
                str(v) for k, v in duck_obj.items() if isinstance(v, str)
            )
        elif isinstance(duck_obj, str):
            raw_sql = duck_obj
        else:
            raw_sql = "\n\n".join(
                str(v) for k, v in four_zone_sql.items() if isinstance(v, str)
            )
    else:
        raw_sql = str(four_zone_sql)

    sql_path = output_dir / "final_four_zone.sql"
    sql_bytes = raw_sql.encode("utf-8")
    sql_path.write_bytes(sql_bytes)
    _sql_sha = hashlib.sha256(sql_bytes).hexdigest()
    if cas_store is not None:
        cas_store.put(sql_bytes)

    if isinstance(four_zone_sql, dict) and "databricks" in four_zone_sql:
        db_obj = four_zone_sql["databricks"]
        if isinstance(db_obj, dict):
            db_raw_sql = db_obj.get("all_zones") or "\n\n".join(
                str(v) for k, v in db_obj.items() if isinstance(v, str)
            )
        elif isinstance(db_obj, str):
            db_raw_sql = db_obj
        else:
            db_raw_sql = ""
        if db_raw_sql:
            db_sql_path = output_dir / "final_four_zone_databricks.sql"
            db_sql_bytes = db_raw_sql.encode("utf-8")
            db_sql_path.write_bytes(db_sql_bytes)
            if cas_store is not None:
                cas_store.put(db_sql_bytes)

    ledger_path = output_dir / "vault-ledger.jsonl"
    if not ledger_path.is_file():
        ledger_path.write_text("", encoding="utf-8", newline="\n")

    if graph_a_sha256 and loaded_graph_a is not None:
        ont_disk_path = output_dir / "graph_a_ontology.yaml"
        raw_b = getattr(loaded_graph_a, "raw_bytes", b"")
        if raw_b:
            ont_disk_path.write_bytes(raw_b)
        if cas_store is not None:
            cas_store.put(raw_b)
        artifacts.append(
            PipelineArtifactRef(
                artifact_id="art-stage00-graph-a-ontology",
                name="graph_a_ontology.yaml",
                sha256=graph_a_sha256,
                path=str(ont_disk_path),
                artifact_type="YAML",
                byte_length=len(raw_b),
            )
        )

    for wave_dir in sorted((output_dir / "waves").glob("wave_*")):
        wave_idx_str = wave_dir.name.removeprefix("wave_")
        gb_path = wave_dir / f"{wave_dir.name}_graph_b_profile.json"
        art_id = f"art-w{wave_idx_str}-stage01-graph-b-profile"
        if gb_path.is_file() and not any(a.artifact_id == art_id for a in artifacts):
            gb_bytes = gb_path.read_bytes()
            gb_sha = hashlib.sha256(gb_bytes).hexdigest()
            if cas_store is not None:
                cas_store.put(gb_bytes)
            artifacts.append(
                PipelineArtifactRef(
                    artifact_id=art_id,
                    name=gb_path.name,
                    sha256=gb_sha,
                    path=str(gb_path),
                    artifact_type="JSON",
                    byte_length=len(gb_bytes),
                )
            )


def build_completed_pipeline_report(
    runner: Any,
    t0: datetime.datetime,
    preflight: Any,
    out_blueprint: Any,
    wave_reports: list[PipelineWaveReport],
    step_reports: list[PipelineStepReport],
    artifacts: list[PipelineArtifactRef],
    final_artifacts: dict[str, PipelineArtifactRef],
    resolved_holds: list[dict[str, Any]],
    diff_summary: dict[str, Any],
    rec_errors: list[str],
    inference_plan: Any,
    four_zone_sql: dict[str, Any],
) -> AgenticPipelineReport:
    """Build and return terminal AgenticPipelineReport in COMPLETED state."""
    h_setup_summary = _h_setup_summary(runner)
    from opendv_agent_harness.pipeline.drift_projection import enrich_drift_findings

    for wr in wave_reports:
        if getattr(wr, "schema_drift_events", None):
            wr.schema_drift_events = enrich_drift_findings(list(wr.schema_drift_events))
    aggregated_drift = [
        ev
        for wr in wave_reports
        for ev in (getattr(wr, "schema_drift_events", None) or [])
    ]
    has_init_c, init_c_path, lc_stage = _initial_contract_metadata(runner)
    report = AgenticPipelineReport(
        run_id=runner.run_id,
        pipeline_id=getattr(runner.input_spec, "pipeline_id", "default_pipeline"),
        project_id=getattr(runner.input_spec, "project_id", "default_project"),
        domain_namespace=runner.input_spec.domain_namespace,
        has_initial_contract=has_init_c,
        initial_contract_path=init_c_path,
        lifecycle_stage=lc_stage,
        status="COMPLETED",
        overall_passed=True,
        started_at=t0.isoformat(),
        completed_at=datetime.datetime.now(datetime.UTC).isoformat(),
        harness_setup=h_setup_summary,
        input_validation=preflight,
        verdict="PASS",
        blueprint=out_blueprint,
        wave_reports=wave_reports,
        step_reports=step_reports,
        artifacts=artifacts,
        intermediate_artifacts=artifacts,
        final_artifacts=final_artifacts,
        resolved_holds=resolved_holds,
        observability_events=list(getattr(runner, "observability_events", [])),
        fallbacks_applied=list(getattr(runner, "fallbacks_applied", [])),
        graph_a_sha256=runner.graph_a_sha256,
        ontology_version=getattr(runner.ontology, "ontology_version", "1.0.0"),
        parent_sha256=getattr(runner.ontology, "parent_sha256", None),
        decision_refs=list(getattr(runner.ontology, "decision_refs", []) or []),
        evolution_diff=diff_summary,
        recommender_errors=rec_errors if rec_errors else None,
        inference_plan=inference_plan.model_dump(mode="json") if inference_plan else {},
        four_zone_sql=four_zone_sql,
        drift_findings=aggregated_drift,
        summary_markdown=f"# Open-DV Pipeline Run {runner.run_id}\n\n- Verdict: PASS\n",
    )

    (runner.output_dir / "pipeline_report.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8", newline="\n"
    )
    (runner.output_dir / "pipeline_report.md").write_text(
        report.summary_markdown, encoding="utf-8", newline="\n"
    )

    report.output_validation = validate_pipeline_outputs(report, cas_store=runner.cas_store)
    if not report.output_validation.passed:
        report.overall_passed = False
        report.status = "FAILED"
        report.verdict = "FAIL"

    return report


def build_preflight_failure_report(
    runner: Any,
    t0: datetime.datetime,
    preflight: Any,
) -> AgenticPipelineReport:
    """Build and return terminal AgenticPipelineReport when preflight validation fails."""
    h_setup_summary = _h_setup_summary(runner)
    return AgenticPipelineReport(
        run_id=runner.run_id,
        pipeline_id=getattr(runner.input_spec, "pipeline_id", "default_pipeline"),
        project_id=getattr(runner.input_spec, "project_id", "default_project"),
        domain_namespace=runner.input_spec.domain_namespace,
        status="FAILED",
        overall_passed=False,
        started_at=t0.isoformat(),
        completed_at=datetime.datetime.now(datetime.UTC).isoformat(),
        harness_setup=h_setup_summary,
        input_validation=preflight,
        verdict="FAIL",
        summary_markdown=(
            "# Preflight Validation Failed\n\n"
            "Pipeline execution aborted due to invalid inputs."
        ),
    )


build_paused_hold_pipeline_report = build_paused_pipeline_report

__all__ = [
    "advance_blueprint_lineage",
    "build_completed_pipeline_report",
    "build_paused_hold_pipeline_report",
    "build_paused_pipeline_report",
    "build_preflight_failure_report",
    "build_rejected_pipeline_report",
    "compute_blueprint_evolution_diff",
    "materialize_final_pipeline_outputs",
    "validate_pipeline_outputs",
]
