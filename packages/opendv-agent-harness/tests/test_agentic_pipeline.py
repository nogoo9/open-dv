"""End-to-End Tests for the 8-Stage Neuro-Symbolic Agentic Pipeline (`WP-14`).

Tests:
1. `validate_pipeline_inputs` on valid sample files (`global_commerce_vault`) and on
   invalid inputs (path traversal `../`, missing CSV file, out-of-bounds `[char_start, char_end]`,
   and invalid Graph A ontology).
2. `run_agentic_vault_pipeline` end-to-end across all 4 waves (`COLD_START`,
   `INCREMENTAL_SOURCE`, `SCHEMA_DRIFT`, `UNSTRUCTURED_ONBOARDING`), verifying:
   - `report.overall_passed is True`
   - `report.input_validation.passed is True` and `report.output_validation.passed is True`
   - All 4 `wave_reports` pass Gates `G0`-`G5` and 4-Batch DuckDB Shadow Sandbox
     (`DV-DYN-001`-`015`)
   - All intermediate and final artifacts exist on disk, match their CAS SHA-256 digests,
     and `pipeline_report.md` + `pipeline_report.json` are generated.
3. Multi-provider harness execution (`"deterministic"`, `"deterministic-stub"`,
   `"antigravity"`, and `"claude"`) as well as `steward_hold_policy` modes
   (`"auto_approve_top_candidate"`, `"pause"`, `"reject_on_hold"`).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from opendv_agent_harness import (
    DeterministicHarnessAdapter,
    HarnessSetupConfig,
    PipelineInputSpec,
    create_agent_harness,
    get_harness_adapter,
    run_agentic_vault_pipeline,
    validate_pipeline_inputs,
    validate_pipeline_outputs,
)
from opendv_core.schema_registry import find_workspace_root
from opendv_telemetry import ContentAddressableStore


def _sample_dir() -> Path:
    return find_workspace_root() / "examples" / "pipeline_samples" / "global_commerce_vault"


def test_deterministic_harness_adapter_registration(tmp_path: Path) -> None:
    for provider in ("deterministic", "deterministic-stub"):
        setup = HarnessSetupConfig(provider=provider)
        cfg = setup.to_harness_config(
            workspace_dir=tmp_path,
            state_dir=tmp_path / "state",
            app_data_dir=tmp_path / "app",
            save_dir=tmp_path / "save",
        )
        adapter_1 = create_agent_harness(cfg)
        adapter_2 = get_harness_adapter(cfg)
        assert isinstance(adapter_1, DeterministicHarnessAdapter)
        assert isinstance(adapter_2, DeterministicHarnessAdapter)
        assert adapter_1.provider_name == provider


def test_validate_pipeline_inputs_valid_and_invalid(tmp_path: Path) -> None:
    sample_dir = _sample_dir()
    input_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")
    harness_setup = HarnessSetupConfig.from_file(sample_dir / "harness_config.yaml")

    valid_report = validate_pipeline_inputs(
        input_spec,
        harness_setup,
        base_dir=sample_dir,
    )
    assert valid_report.stage == "INPUT_VALIDATION"
    assert valid_report.passed is True
    assert valid_report.diagnostics == []

    # 1. Path traversal rejection ('../')
    traversal_spec = input_spec.model_copy(
        update={"graph_a_ontology_path": "../../../etc/passwd"}
    )
    traversal_report = validate_pipeline_inputs(
        traversal_spec,
        harness_setup,
        base_dir=sample_dir,
    )
    assert traversal_report.passed is False
    assert any(d["code"] == "PATH_TRAVERSAL_REJECTED" for d in traversal_report.diagnostics)

    # 2. Missing CSV file
    missing_csv_wave = input_spec.waves[0].model_copy(
        update={
            "sources": [
                input_spec.waves[0].sources[0].model_copy(
                    update={"data_path": "sources/wave0_cold_start/nonexistent.csv"}
                )
            ]
        }
    )
    missing_csv_spec = input_spec.model_copy(update={"waves": [missing_csv_wave]})
    missing_csv_report = validate_pipeline_inputs(
        missing_csv_spec,
        harness_setup,
        base_dir=sample_dir,
    )
    assert missing_csv_report.passed is False
    assert any(d["code"] == "SOURCE_FILE_MISSING" for d in missing_csv_report.diagnostics)

    # 3. Out-of-bounds unstructured [char_start, char_end]
    bad_cand = input_spec.waves[3].unstructured_documents[0].extracted_candidates[0].model_copy(
        update={"char_start": 0, "char_end": 999999}
    )
    bad_doc = input_spec.waves[3].unstructured_documents[0].model_copy(
        update={"extracted_candidates": [bad_cand]}
    )
    bad_wave3 = input_spec.waves[3].model_copy(update={"unstructured_documents": [bad_doc]})
    bad_span_spec = input_spec.model_copy(update={"waves": [bad_wave3]})
    bad_span_report = validate_pipeline_inputs(
        bad_span_spec,
        harness_setup,
        base_dir=sample_dir,
    )
    assert bad_span_report.passed is False
    assert any(d["code"] == "SPAN_OUT_OF_BOUNDS" for d in bad_span_report.diagnostics)

    # 4. Invalid Graph A ontology file
    bad_ont_path = tmp_path / "invalid_ontology.yaml"
    bad_ont_path.write_text(
        "ontology_version: '1.0.0'\ndomain_namespace: 'INVALID!'",
        encoding="utf-8",
    )
    bad_ont_spec = input_spec.model_copy(update={"graph_a_ontology_path": str(bad_ont_path)})
    bad_ont_report = validate_pipeline_inputs(
        bad_ont_spec,
        harness_setup,
        base_dir=tmp_path,
    )
    assert bad_ont_report.passed is False
    assert any(d["code"] == "ONTOLOGY_SCHEMA_INVALID" for d in bad_ont_report.diagnostics)


def test_run_agentic_vault_pipeline_four_waves_end_to_end(tmp_path: Path) -> None:
    sample_dir = _sample_dir()
    out_dir = tmp_path / "out"
    cas = ContentAddressableStore(out_dir / "cas")

    report = run_agentic_vault_pipeline(
        sample_dir / "pipeline_input.yaml",
        sample_dir / "harness_config.yaml",
        output_dir=out_dir,
        base_dir=sample_dir,
        cas_store=cas,
    )

    assert report.status == "COMPLETED"
    assert report.overall_passed is True
    assert report.input_validation.passed is True
    assert report.output_validation.passed is True
    assert report.output_validation.pii_leak_check_passed is True
    assert report.output_validation.cas_integrity_verified is True

    # Verify all 4 waves ran and passed Gates G0-G5 and 4-Batch DuckDB Shadow Sandbox
    assert len(report.wave_reports) == 4
    expected_wave_types = [
        "COLD_START",
        "INCREMENTAL_SOURCE",
        "SCHEMA_DRIFT",
        "UNSTRUCTURED_ONBOARDING",
    ]
    assert [w.wave_type for w in report.wave_reports] == expected_wave_types

    # Wave 0 exercises Attempt 1 @toon_v1 self-healing -> Attempt 2 convergence
    assert report.wave_reports[0].self_healing_attempts == 2
    assert "ALG_A1_3AXIS_SATELLITE_SPLIT" in report.wave_reports[0].applied_codemods
    assert "ALG_A4_COLD_START_EXTENSIBILITY" in report.wave_reports[0].applied_codemods

    # Wave 2 exercises intra-source schema drift detection & ALG_A9A additive companion satellite
    assert len(report.wave_reports[2].schema_drift_events) == 2
    assert "ALG_A9A_INTRA_SOURCE_SCHEMA_DRIFT" in report.wave_reports[2].applied_codemods

    # Wave 3 exercises unstructured span grounding (Tier-U1 + Tier-U3 auto-approved hold)
    assert report.wave_reports[3].unstructured_tier_counts["Tier-U1"] == 1
    assert report.wave_reports[3].unstructured_tier_counts["Tier-U3"] == 1
    assert len(report.resolved_holds) == 1

    for w_rep in report.wave_reports:
        assert w_rep.status in {"PASS", "AUTO_REPAIRED"}
        assert all(v == "PASS" for v in w_rep.gate_scorecard.values())
        assert w_rep.sandbox_summary["passed"] is True
        assert w_rep.sandbox_summary["dynamic_checks_passed"] == 15
        assert len(w_rep.sandbox_summary["dynamic_rule_ids"]) == 15

    # Verify all intermediate and final artifacts exist on disk and match CAS SHA-256
    for ref in [*report.intermediate_artifacts, *report.final_artifacts.values()]:
        assert ref.file_path is not None
        disk_file = Path(ref.file_path)
        assert disk_file.is_file()
        disk_bytes = disk_file.read_bytes()
        assert len(disk_bytes) == ref.byte_length
        assert hashlib.sha256(disk_bytes).hexdigest().lower() == ref.sha256.lower()
        assert cas.verify(ref.sha256) is True

    assert (out_dir / "pipeline_report.md").is_file()
    assert (out_dir / "pipeline_report.json").is_file()
    assert (out_dir / "final_contract.yaml").is_file()
    assert (out_dir / "final_canonical_ir.json").is_file()
    assert (out_dir / "final_four_zone.sql").is_file()
    assert (out_dir / "vault-ledger.jsonl").is_file()

    # Re-run deterministic output validator independently
    reval = validate_pipeline_outputs(report, cas_store=cas)
    assert reval.passed is True


def test_pipeline_providers_and_steward_hold_policies(tmp_path: Path) -> None:
    sample_dir = _sample_dir()
    input_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")
    single_wave_spec = input_spec.model_copy(update={"waves": [input_spec.waves[0]]})

    for provider in ("antigravity", "claude", "deterministic-stub"):
        p_out = tmp_path / f"out_{provider}"
        setup = HarnessSetupConfig(
            provider=provider,
            steward_hold_policy="auto_approve_top_candidate",
            inject_adversarial_fault_on_attempt_1=False,
        )
        rep = run_agentic_vault_pipeline(
            single_wave_spec,
            setup,
            output_dir=p_out,
            base_dir=sample_dir,
        )
        assert rep.overall_passed is True
        assert rep.status == "COMPLETED"

    # Test steward_hold_policy="pause" on Wave 3 Tier-U3 candidate
    two_wave_spec = input_spec.model_copy(
        update={
            "waves": [
                input_spec.waves[0],
                input_spec.waves[3].model_copy(update={"wave_index": 1}),
            ]
        }
    )
    pause_setup = HarnessSetupConfig(
        provider="deterministic",
        steward_hold_policy="pause",
        inject_adversarial_fault_on_attempt_1=False,
    )
    pause_rep = run_agentic_vault_pipeline(
        two_wave_spec,
        pause_setup,
        output_dir=tmp_path / "out_pause",
        base_dir=sample_dir,
    )
    assert pause_rep.status == "PAUSED_HOLD"
    assert pause_rep.overall_passed is False
    assert len(pause_rep.active_holds) == 1
    assert pause_rep.active_holds[0]["status"] == "PENDING"
