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
3. Multi-provider harness execution (`"agy"`, `"antigravity"`, and `"claude"`,
   plus negative rejection of `"deterministic"` / `"deterministic-stub"`) as well as
   `steward_hold_policy` modes (`"auto_approve_top_candidate"`, `"pause"`, `"reject_on_hold"`).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import pytest
import yaml

from opendv_agent_harness import (
    AgenticPipelineReport,
    AntigravityHarnessAdapter,
    ClaudeAgentHarnessAdapter,
    DeterministicHarnessAdapter,
    HarnessAuthConfig,
    HarnessBudget,
    HarnessSetupConfig,
    ObservabilityVerdict,
    PipelineInputSpec,
    create_agent_harness,
    get_harness_adapter,
    run_agentic_vault_pipeline,
    validate_pipeline_inputs,
    validate_pipeline_outputs,
)
from opendv_agent_harness.adapters.deterministic_adapter import (
    _synthesize_minimal_schema_instance,
    build_deterministic_agy_sdk_shim,
    build_deterministic_claude_sdk_shim,
)
from opendv_agent_harness.pipeline import (
    PipelineUnstructuredCandidateInput,
    Stage00OrchestrationPlanOutput,
    Stage01FeedDiscoveryItem,
    Stage01SourceDiscoveryOutput,
    Stage02UnstructuredDiscoveryOutput,
    Stage03ColumnTriageOutput,
    Stage04ContractSynthesisOutput,
)
from opendv_core.schema_registry import find_workspace_root
from opendv_telemetry import ContentAddressableStore


def _sample_dir() -> Path:
    return find_workspace_root() / "examples" / "pipeline_samples" / "global_commerce_vault"


def test_deterministic_harness_adapter_registration(tmp_path: Path) -> None:
    from pydantic import ValidationError

    for invalid_provider in ("deterministic", "deterministic-stub"):
        with pytest.raises(ValidationError):
            HarnessSetupConfig(provider=invalid_provider)  # type: ignore[arg-type]

    sample_dir = _sample_dir()
    input_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")
    invalid_override_report = validate_pipeline_inputs(
        input_spec,
        {"provider": "deterministic"},
        base_dir=sample_dir,
    )
    assert invalid_override_report.passed is False
    assert any(d["code"] == "HARNESS_SETUP_INVALID" for d in invalid_override_report.diagnostics)

    setup = HarnessSetupConfig(provider="agy")
    cfg = setup.to_harness_config(
        workspace_dir=tmp_path,
        state_dir=tmp_path / "state",
        app_data_dir=tmp_path / "app",
        save_dir=tmp_path / "save",
    )
    unregistered_cfg = cfg.model_copy(update={"provider": "deterministic"})
    with pytest.raises(ValueError, match="Unsupported harness provider 'deterministic'"):
        create_agent_harness(unregistered_cfg)
    with pytest.raises(ValueError, match="Unsupported harness provider 'deterministic'"):
        get_harness_adapter(unregistered_cfg)

    direct_adapter = DeterministicHarnessAdapter(cfg)
    assert isinstance(direct_adapter, DeterministicHarnessAdapter)
    assert direct_adapter.provider_name == "deterministic"


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
    traversal_spec = input_spec.model_copy(update={"graph_a_ontology_path": "../../../etc/passwd"})
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
                input_spec.waves[0]
                .sources[0]
                .model_copy(update={"data_path": "sources/wave0_cold_start/nonexistent.csv"})
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
    bad_cand = PipelineUnstructuredCandidateInput(
        candidate_id="cand-bad-span",
        target_hub="hub_customer",
        extracted_business_key="TAX-US-99100",
        verbatim_quote="Tax ID: TAX-US-99100",
        char_start=0,
        char_end=999999,
    )
    bad_doc = (
        input_spec.waves[3]
        .unstructured_documents[0]
        .model_copy(update={"extracted_candidates": [bad_cand]})
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

    # 5. Output directory path traversal rejection before directory creation
    bad_out_spec = input_spec.model_copy(update={"output_dir": "../../escaped_out"})
    bad_out_report = validate_pipeline_inputs(
        bad_out_spec,
        harness_setup,
        base_dir=sample_dir,
    )
    assert bad_out_report.passed is False
    assert any(d["code"] == "PATH_TRAVERSAL_REJECTED" for d in bad_out_report.diagnostics)

    failed_pipeline_rep = run_agentic_vault_pipeline(
        bad_out_spec,
        harness_setup,
        base_dir=sample_dir,
    )
    assert failed_pipeline_rep.status == "FAILED"
    assert failed_pipeline_rep.overall_passed is False
    assert failed_pipeline_rep.input_validation.passed is False

    # 6. Discovery-first allows empty business_key_columns=[], but rejects blank column strings
    empty_bk_src = input_spec.waves[0].sources[0].model_copy(update={"business_key_columns": []})
    empty_bk_wave = input_spec.waves[0].model_copy(update={"sources": [empty_bk_src]})
    empty_bk_spec = input_spec.model_copy(update={"waves": [empty_bk_wave]})
    empty_bk_report = validate_pipeline_inputs(
        empty_bk_spec,
        harness_setup,
        base_dir=sample_dir,
    )
    assert empty_bk_report.passed is True

    blank_bk_src = (
        input_spec.waves[0].sources[0].model_copy(update={"business_key_columns": ["   "]})
    )
    blank_bk_wave = input_spec.waves[0].model_copy(update={"sources": [blank_bk_src]})
    blank_bk_spec = input_spec.model_copy(update={"waves": [blank_bk_wave]})
    blank_bk_report = validate_pipeline_inputs(
        blank_bk_spec,
        harness_setup,
        base_dir=sample_dir,
    )
    assert blank_bk_report.passed is False
    assert any(d["code"] == "MISSING_BUSINESS_KEY_COLUMNS" for d in blank_bk_report.diagnostics)

    # 7. Unstructured-only wave without prior relational source rejection
    unstructured_first_wave = input_spec.waves[3].model_copy(update={"wave_index": 0})
    unstructured_only_spec = input_spec.model_copy(update={"waves": [unstructured_first_wave]})
    unstructured_only_report = validate_pipeline_inputs(
        unstructured_only_spec,
        harness_setup,
        base_dir=sample_dir,
    )
    assert unstructured_only_report.passed is False
    assert any(
        d["code"] == "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE"
        for d in unstructured_only_report.diagnostics
    )

    # 8. Declared column missing from CSV and unstructured verbatim quote mismatch
    missing_col_src = (
        input_spec.waves[0].sources[0].model_copy(update={"pii_columns": ["nonexistent_pii_col"]})
    )
    bad_quote_cand = PipelineUnstructuredCandidateInput(
        candidate_id="cand-bad-quote",
        target_hub="hub_customer",
        extracted_business_key="TAX-US-99100",
        verbatim_quote="TAMPERED QUOTE NOT IN DOCUMENT",
        char_start=0,
        char_end=15,
    )
    bad_quote_doc = (
        input_spec.waves[3]
        .unstructured_documents[0]
        .model_copy(update={"extracted_candidates": [bad_quote_cand]})
    )
    combined_bad_wave = input_spec.waves[0].model_copy(
        update={
            "sources": [missing_col_src],
            "unstructured_documents": [bad_quote_doc],
        }
    )
    combined_bad_spec = input_spec.model_copy(update={"waves": [combined_bad_wave]})
    combined_bad_report = validate_pipeline_inputs(
        combined_bad_spec,
        harness_setup,
        base_dir=sample_dir,
    )
    assert combined_bad_report.passed is False
    bad_codes = {d["code"] for d in combined_bad_report.diagnostics}
    assert "DECLARED_COLUMNS_MISSING" in bad_codes
    assert "SPAN_QUOTE_MISMATCH" in bad_codes


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
        sdk_module=build_deterministic_agy_sdk_shim(),
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

    final_contract_data = yaml.safe_load(
        (out_dir / "final_contract.yaml").read_text(encoding="utf-8")
    )
    final_sat_names = {s["name"] for s in final_contract_data.get("satellites", [])}
    assert "sat_customer_core_ext_v2" in final_sat_names
    assert "sat_document_customer_ai_provenance" in final_sat_names

    wave3_graph_b = json.loads(
        (out_dir / "waves" / "wave_3" / "wave_3_graph_b_profile.json").read_text(encoding="utf-8")
    )
    wave3_unstructured_docs = [
        doc
        for sys_obj in wave3_graph_b.get("source_systems", [])
        for doc in sys_obj.get("unstructured_documents", [])
    ]
    assert any(doc["doc_uri"] == "DOC-MSA-2026-089" for doc in wave3_unstructured_docs)

    # Re-run deterministic output validator independently (in-memory and reloaded from disk)
    reval = validate_pipeline_outputs(report, cas_store=cas)
    assert reval.passed is True
    loaded_from_disk = AgenticPipelineReport.model_validate_json(
        (out_dir / "pipeline_report.json").read_text(encoding="utf-8")
    )
    reval_disk = validate_pipeline_outputs(loaded_from_disk, cas_store=cas)
    assert reval_disk.passed is True
    assert reval_disk.cas_integrity_verified is True

    # Adversarial output tamper & missing artifact checks
    fc_path = out_dir / "final_contract.yaml"
    original_fc_bytes = fc_path.read_bytes()
    fc_path.write_bytes(original_fc_bytes + b"\n# tampered\n")
    tampered_val = validate_pipeline_outputs(report, cas_store=cas)
    assert tampered_val.passed is False
    assert tampered_val.cas_integrity_verified is False
    assert any(d["code"] == "ARTIFACT_SHA256_MISMATCH" for d in tampered_val.diagnostics)
    fc_path.write_bytes(original_fc_bytes)

    fc_path.unlink()
    missing_val = validate_pipeline_outputs(report, cas_store=cas)
    assert missing_val.passed is False
    assert missing_val.cas_integrity_verified is False
    assert any(d["code"] == "ARTIFACT_FILE_MISSING" for d in missing_val.diagnostics)
    fc_path.write_bytes(original_fc_bytes)


def test_pipeline_providers_and_steward_hold_policies(tmp_path: Path) -> None:
    sample_dir = _sample_dir()
    input_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")
    single_wave_spec = input_spec.model_copy(update={"waves": [input_spec.waves[0]]})

    agy_cfg = HarnessSetupConfig(provider="antigravity").to_harness_config(
        workspace_dir=tmp_path,
        state_dir=tmp_path / "state_agy",
        app_data_dir=tmp_path / "app_agy",
        save_dir=tmp_path / "save_agy",
    )
    claude_cfg = HarnessSetupConfig(provider="claude").to_harness_config(
        workspace_dir=tmp_path,
        state_dir=tmp_path / "state_claude",
        app_data_dir=tmp_path / "app_claude",
        save_dir=tmp_path / "save_claude",
    )
    with pytest.raises(ImportError):
        AntigravityHarnessAdapter(agy_cfg)._load_sdk()
    with pytest.raises(ImportError):
        ClaudeAgentHarnessAdapter(claude_cfg)._load_sdk()

    sdk_shims: dict[
        Literal["agy", "antigravity", "claude", "claude-cli", "claude-code"],
        Any,
    ] = {
        "agy": build_deterministic_agy_sdk_shim(),
        "antigravity": build_deterministic_agy_sdk_shim(),
        "claude": build_deterministic_claude_sdk_shim(),
        "claude-cli": build_deterministic_claude_sdk_shim(),
        "claude-code": build_deterministic_claude_sdk_shim(),
    }
    for provider, sdk_shim in sdk_shims.items():
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
            sdk_module=sdk_shim,
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
        provider="agy",
        steward_hold_policy="pause",
        inject_adversarial_fault_on_attempt_1=False,
    )
    pause_out = tmp_path / "out_pause"
    pause_rep = run_agentic_vault_pipeline(
        two_wave_spec,
        pause_setup,
        output_dir=pause_out,
        base_dir=sample_dir,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )
    assert pause_rep.status == "PAUSED_HOLD"
    assert pause_rep.overall_passed is False
    assert pause_rep.output_validation.passed is True
    assert len(pause_rep.active_holds) == 1
    assert pause_rep.active_holds[0]["status"] == "PENDING"
    pause_final_contract = yaml.safe_load(
        (pause_out / "final_contract.yaml").read_text(encoding="utf-8")
    )
    pause_hub_names = {h["name"] for h in pause_final_contract.get("hubs", [])}
    pause_sat_names = {s["name"] for s in pause_final_contract.get("satellites", [])}
    assert "hub_document" not in pause_hub_names
    assert "sat_document_customer_ai_provenance" not in pause_sat_names

    # Test steward_hold_policy="reject_on_hold" on Wave 3 Tier-U3 candidate
    reject_setup = HarnessSetupConfig(
        provider="agy",
        steward_hold_policy="reject_on_hold",
        inject_adversarial_fault_on_attempt_1=False,
    )
    reject_out = tmp_path / "out_reject"
    reject_rep = run_agentic_vault_pipeline(
        two_wave_spec,
        reject_setup,
        output_dir=reject_out,
        base_dir=sample_dir,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )
    assert reject_rep.status == "FAILED"
    assert reject_rep.overall_passed is False
    assert reject_rep.output_validation.passed is False
    assert any(
        d["code"] == "WAVE_EXECUTION_REJECTED" for d in reject_rep.output_validation.diagnostics
    )
    wb03_events = [
        ev for ev in reject_rep.observability_events if ev["workbench_view_id"] == "WB-03"
    ]
    assert len(wb03_events) == 2
    assert wb03_events[-1]["status"] == ObservabilityVerdict.REJECT_WITH_TRACE.value
    reject_final_contract = yaml.safe_load(
        (reject_out / "final_contract.yaml").read_text(encoding="utf-8")
    )
    reject_hub_names = {h["name"] for h in reject_final_contract.get("hubs", [])}
    reject_sat_names = {s["name"] for s in reject_final_contract.get("satellites", [])}
    assert "hub_document" not in reject_hub_names
    assert "sat_document_customer_ai_provenance" not in reject_sat_names


def test_optional_only_foreign_key_link_synthesis(tmp_path: Path) -> None:
    sample_dir = _sample_dir()
    input_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")
    harness_setup = HarnessSetupConfig(
        provider="agy",
        inject_adversarial_fault_on_attempt_1=False,
    )

    cust_csv = tmp_path / "customers.csv"
    cust_csv.write_text(
        "customer_tax_id,legal_name,updated_at\n"
        "TAX-US-01,Acme Corp,2026-01-01 00:00:00\n"
        "TAX-US-02,Globex Inc,2026-01-02 00:00:00\n",
        encoding="utf-8",
    )
    orders_opt_fk_csv = tmp_path / "orders_opt_fk.csv"
    orders_opt_fk_csv.write_text(
        "order_id,customer_tax_id,order_status,updated_at\n"
        "ORD-01,TAX-US-01,CONFIRMED,2026-01-01 01:00:00\n"
        "ORD-02,,DRAFT,2026-01-02 01:00:00\n",
        encoding="utf-8",
    )

    cust_src = (
        input_spec.waves[0]
        .sources[0]
        .model_copy(
            update={
                "data_path": str(cust_csv),
                "business_key_columns": ["customer_tax_id"],
                "surrogate_key_columns": [],
                "foreign_key_mappings": {},
                "pii_columns": [],
            }
        )
    )
    order_src = (
        input_spec.waves[0]
        .sources[1]
        .model_copy(
            update={
                "data_path": str(orders_opt_fk_csv),
                "business_key_columns": ["order_id"],
                "surrogate_key_columns": [],
                "foreign_key_mappings": {"customer_tax_id": "hub_customer"},
                "pii_columns": [],
            }
        )
    )
    opt_wave = input_spec.waves[0].model_copy(update={"sources": [cust_src, order_src]})
    opt_spec = input_spec.model_copy(
        update={
            "graph_a_ontology_path": str(
                sample_dir / "ontology" / "commerce_graph_a_ontology.yaml"
            ),
            "waves": [opt_wave],
        }
    )
    out_dir = tmp_path / "out_opt_fk"
    rep = run_agentic_vault_pipeline(
        opt_spec,
        harness_setup,
        output_dir=out_dir,
        base_dir=tmp_path,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )
    assert rep.status == "COMPLETED"
    assert rep.overall_passed is True
    final_contract = yaml.safe_load((out_dir / "final_contract.yaml").read_text(encoding="utf-8"))
    link_names = {lnk["name"] for lnk in final_contract.get("links", [])}
    assert "lnk_order_customer" in link_names
    fk_ledger_entries = [
        entry
        for entry in final_contract.get("column_triage_ledger", [])
        if entry["bronze_table"] == "erp_netsuite.erp_orders"
        and entry["column_name"] == "customer_tax_id"
    ]
    assert len(fk_ledger_entries) == 1
    assert fk_ledger_entries[0]["target_dv_entity"] == "lnk_order_customer"
    assert fk_ledger_entries[0]["elm_classification"] == "OPTIONAL_FOREIGN_KEY_REF"

    # Verify Stage 5 retry exhaustion propagates REJECT-WITH-TRACE and FAILED status
    exhausted_setup = HarnessSetupConfig(
        provider="agy",
        budget=HarnessBudget(max_schema_validation_retries=1),
        inject_adversarial_fault_on_attempt_1=True,
    )
    exhausted_out = tmp_path / "out_exhausted_s5"
    exhausted_rep = run_agentic_vault_pipeline(
        opt_spec,
        exhausted_setup,
        output_dir=exhausted_out,
        base_dir=tmp_path,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )
    assert exhausted_rep.status == "FAILED"
    assert exhausted_rep.overall_passed is False
    assert exhausted_rep.output_validation.passed is False
    assert exhausted_rep.wave_reports[0].status == "REJECT-WITH-TRACE"
    assert exhausted_rep.wave_reports[0].self_healing_attempts == 1
    assert (
        exhausted_rep.wave_reports[0].gate_scorecard["G4_YAGNI_AND_COLUMN_LEDGER"]
        == "REJECT-WITH-TRACE"
    )
    exhausted_codes = {d["code"] for d in exhausted_rep.output_validation.diagnostics}
    assert "WAVE_EXECUTION_REJECTED" in exhausted_codes
    assert "STEP_EXECUTION_REJECTED" in exhausted_codes


def test_harness_auth_config_resolution_and_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample_dir = _sample_dir()
    input_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")
    single_wave_spec = input_spec.model_copy(update={"waves": [input_spec.waves[0]]})

    # 1. Load sample harness_config_antigravity.yaml and harness_config_claude.yaml
    agy_sample = HarnessSetupConfig.from_file(sample_dir / "harness_config_antigravity.yaml")
    assert agy_sample.provider == "antigravity"
    assert agy_sample.model_overrides in (
        {"reasoning": "gemini-2.5-pro", "fast": "gemini-2.5-flash"},
        {"reasoning": "gemini-4-argon-high", "fast": "gemini-3.8-flash-high"},
    )
    assert agy_sample.auth.auth_mode == "api_key_env"
    assert agy_sample.auth.api_key_env_var == "GEMINI_API_KEY"
    assert agy_sample.auth.require_credentials_present is False

    claude_sample = HarnessSetupConfig.from_file(sample_dir / "harness_config_claude.yaml")
    assert claude_sample.provider == "claude"
    assert claude_sample.model_overrides == {
        "reasoning": "claude-opus-4-6",
        "fast": "claude-haiku-4-5",
    }
    assert claude_sample.auth.auth_mode == "api_key_env"
    assert claude_sample.auth.api_key_env_var == "ANTHROPIC_API_KEY"
    assert claude_sample.auth.require_credentials_present is False

    assert validate_pipeline_inputs(single_wave_spec, agy_sample, base_dir=sample_dir).passed
    assert validate_pipeline_inputs(single_wave_spec, claude_sample, base_dir=sample_dir).passed

    # 2. api_key_env resolution for Antigravity and Claude (plus api_base_url)
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-secret-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-secret-key")

    agy_api_setup = agy_sample.model_copy(
        update={
            "auth": agy_sample.auth.model_copy(
                update={
                    "api_base_url": "https://gateway.example.com/gemini",
                    "require_credentials_present": True,
                }
            ),
            "extra_provider_options": {"region_hint": "eu"},
        }
    )
    agy_rt_cfg = agy_api_setup.to_harness_config(workspace_dir=tmp_path)
    assert agy_rt_cfg.env["GEMINI_API_KEY"] == "test-gemini-secret-key"
    assert agy_rt_cfg.env["GOOGLE_API_KEY"] == "test-gemini-secret-key"
    assert agy_rt_cfg.env["GOOGLE_GEMINI_BASE_URL"] == "https://gateway.example.com/gemini"
    assert agy_rt_cfg.extra_provider_options == {"region_hint": "eu"}
    assert validate_pipeline_inputs(single_wave_spec, agy_api_setup, base_dir=sample_dir).passed

    claude_api_setup = claude_sample.model_copy(
        update={
            "auth": claude_sample.auth.model_copy(
                update={
                    "api_base_url": "https://gateway.example.com/anthropic",
                    "require_credentials_present": True,
                }
            )
        }
    )
    claude_rt_cfg = claude_api_setup.to_harness_config(workspace_dir=tmp_path)
    assert claude_rt_cfg.env["ANTHROPIC_API_KEY"] == "test-anthropic-secret-key"
    assert claude_rt_cfg.env["ANTHROPIC_BASE_URL"] == "https://gateway.example.com/anthropic"
    assert validate_pipeline_inputs(single_wave_spec, claude_api_setup, base_dir=sample_dir).passed

    # 3. Missing api_key_env when require_credentials_present=True emits HARNESS_AUTH_MISSING
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    missing_key_val = validate_pipeline_inputs(
        single_wave_spec,
        agy_api_setup,
        base_dir=sample_dir,
    )
    assert missing_key_val.passed is False
    assert any(d["code"] == "HARNESS_AUTH_MISSING" for d in missing_key_val.diagnostics)

    # 4. vertex_adc resolution for Antigravity and Claude + missing gcp_project check
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)

    agy_vertex_missing = HarnessSetupConfig(
        provider="antigravity",
        auth=HarnessAuthConfig(
            auth_mode="vertex_adc",
            require_credentials_present=True,
        ),
    )
    val_vtx_missing = validate_pipeline_inputs(
        single_wave_spec,
        agy_vertex_missing,
        base_dir=sample_dir,
    )
    assert val_vtx_missing.passed is False
    assert any(d["code"] == "HARNESS_AUTH_MISSING" for d in val_vtx_missing.diagnostics)

    agy_vertex_ok = HarnessSetupConfig(
        provider="antigravity",
        auth=HarnessAuthConfig(
            auth_mode="vertex_adc",
            gcp_project="proj-agy-vertex-01",
            gcp_location="us-central1",
            gcp_credentials_path="/tmp/sa-agy.json",
            require_credentials_present=True,
        ),
    )
    agy_vtx_cfg = agy_vertex_ok.to_harness_config(workspace_dir=tmp_path)
    assert agy_vtx_cfg.env["GOOGLE_GENAI_USE_VERTEXAI"] == "true"
    assert agy_vtx_cfg.env["GOOGLE_CLOUD_PROJECT"] == "proj-agy-vertex-01"
    assert agy_vtx_cfg.env["GOOGLE_CLOUD_LOCATION"] == "us-central1"
    assert agy_vtx_cfg.env["GOOGLE_APPLICATION_CREDENTIALS"] == "/tmp/sa-agy.json"
    assert validate_pipeline_inputs(single_wave_spec, agy_vertex_ok, base_dir=sample_dir).passed

    claude_vertex_ok = HarnessSetupConfig(
        provider="claude",
        auth=HarnessAuthConfig(
            auth_mode="vertex_adc",
            gcp_project="proj-claude-vertex-01",
            gcp_location="us-east5",
            gcp_credentials_path="/tmp/sa-claude.json",
            require_credentials_present=True,
        ),
    )
    claude_vtx_cfg = claude_vertex_ok.to_harness_config(workspace_dir=tmp_path)
    assert claude_vtx_cfg.env["CLAUDE_CODE_USE_VERTEX"] == "1"
    assert claude_vtx_cfg.env["ANTHROPIC_VERTEX_PROJECT_ID"] == "proj-claude-vertex-01"
    assert claude_vtx_cfg.env["CLOUD_ML_REGION"] == "us-east5"
    assert claude_vtx_cfg.env["GOOGLE_APPLICATION_CREDENTIALS"] == "/tmp/sa-claude.json"
    assert validate_pipeline_inputs(single_wave_spec, claude_vertex_ok, base_dir=sample_dir).passed

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-from-os-env")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "europe-west4")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa-from-os.json")
    claude_vertex_env_fallback = HarnessSetupConfig(
        provider="claude",
        auth=HarnessAuthConfig(
            auth_mode="vertex_adc",
            require_credentials_present=True,
        ),
    )
    claude_vtx_fallback_cfg = claude_vertex_env_fallback.to_harness_config(workspace_dir=tmp_path)
    assert claude_vtx_fallback_cfg.env["CLAUDE_CODE_USE_VERTEX"] == "1"
    assert claude_vtx_fallback_cfg.env["ANTHROPIC_VERTEX_PROJECT_ID"] == "proj-from-os-env"
    assert claude_vtx_fallback_cfg.env["CLOUD_ML_REGION"] == "europe-west4"
    assert claude_vtx_fallback_cfg.env["GOOGLE_APPLICATION_CREDENTIALS"] == "/tmp/sa-from-os.json"
    assert validate_pipeline_inputs(
        single_wave_spec,
        claude_vertex_env_fallback,
        base_dir=sample_dir,
    ).passed
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    # 5. aws_bedrock resolution for Claude + incompatible rejection on Antigravity
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    agy_bedrock_bad = HarnessSetupConfig(
        provider="antigravity",
        auth=HarnessAuthConfig(
            auth_mode="aws_bedrock",
            aws_region="us-east-1",
        ),
    )
    val_bedrock_incompat = validate_pipeline_inputs(
        single_wave_spec,
        agy_bedrock_bad,
        base_dir=sample_dir,
    )
    assert val_bedrock_incompat.passed is False
    assert any(d["code"] == "HARNESS_AUTH_INCOMPATIBLE" for d in val_bedrock_incompat.diagnostics)

    claude_bedrock_missing = HarnessSetupConfig(
        provider="claude",
        auth=HarnessAuthConfig(
            auth_mode="aws_bedrock",
            require_credentials_present=True,
        ),
    )
    val_bedrock_missing = validate_pipeline_inputs(
        single_wave_spec,
        claude_bedrock_missing,
        base_dir=sample_dir,
    )
    assert val_bedrock_missing.passed is False
    assert any(d["code"] == "HARNESS_AUTH_MISSING" for d in val_bedrock_missing.diagnostics)

    claude_bedrock_ok = HarnessSetupConfig(
        provider="claude",
        auth=HarnessAuthConfig(
            auth_mode="aws_bedrock",
            aws_region="us-east-1",
            aws_profile="dv-bedrock-sso",
            require_credentials_present=True,
        ),
    )
    claude_bdr_cfg = claude_bedrock_ok.to_harness_config(workspace_dir=tmp_path)
    assert claude_bdr_cfg.env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert claude_bdr_cfg.env["AWS_REGION"] == "us-east-1"
    assert claude_bdr_cfg.env["AWS_PROFILE"] == "dv-bedrock-sso"
    assert validate_pipeline_inputs(single_wave_spec, claude_bedrock_ok, base_dir=sample_dir).passed

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    monkeypatch.setenv("AWS_PROFILE", "os-bedrock-profile")
    claude_bedrock_env_fallback = HarnessSetupConfig(
        provider="claude",
        auth=HarnessAuthConfig(
            auth_mode="aws_bedrock",
            require_credentials_present=True,
        ),
    )
    claude_bdr_fallback_cfg = claude_bedrock_env_fallback.to_harness_config(workspace_dir=tmp_path)
    assert claude_bdr_fallback_cfg.env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert claude_bdr_fallback_cfg.env["AWS_REGION"] == "us-west-2"
    assert claude_bdr_fallback_cfg.env["AWS_PROFILE"] == "os-bedrock-profile"
    assert validate_pipeline_inputs(
        single_wave_spec,
        claude_bedrock_env_fallback,
        base_dir=sample_dir,
    ).passed
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    # 6. Verify report.harness_setup includes auth_mode & api_key_env_var and redacts env secrets
    secret_setup = HarnessSetupConfig(
        provider="antigravity",
        auth=HarnessAuthConfig(
            auth_mode="api_key_env",
            api_key_env_var="CUSTOM_AGY_SECRET_VAR",
            require_credentials_present=True,
        ),
        env={"CUSTOM_AGY_SECRET_VAR": "super-secret-value-do-not-leak"},
        inject_adversarial_fault_on_attempt_1=False,
    )
    rep = run_agentic_vault_pipeline(
        single_wave_spec,
        secret_setup,
        output_dir=tmp_path / "out_secret_check",
        base_dir=sample_dir,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )
    assert rep.overall_passed is True
    assert rep.harness_setup["auth_mode"] == "api_key_env"
    assert rep.harness_setup["api_key_env_var"] == "CUSTOM_AGY_SECRET_VAR"
    assert rep.harness_setup["env"] == {"CUSTOM_AGY_SECRET_VAR": "[REDACTED]"}
    assert "super-secret-value-do-not-leak" not in rep.model_dump_json()


def test_discovery_first_pipeline_without_prebaked_hints(tmp_path: Path) -> None:
    """Verify WP-02 discovery-first PipelineInputSpec without pre-baked hints."""
    assert HarnessSetupConfig().inject_adversarial_fault_on_attempt_1 is False
    for stage_schema in (
        Stage00OrchestrationPlanOutput,
        Stage01SourceDiscoveryOutput,
        Stage02UnstructuredDiscoveryOutput,
        Stage03ColumnTriageOutput,
        Stage04ContractSynthesisOutput,
    ):
        synthesized = _synthesize_minimal_schema_instance(stage_schema)
        assert isinstance(synthesized, stage_schema)

    sample_dir = _sample_dir()
    input_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")
    for wave_obj in input_spec.waves:
        for src_obj in wave_obj.sources:
            assert src_obj.business_key_columns == []
            assert src_obj.surrogate_key_columns == []
            assert src_obj.pii_columns == []
            assert src_obj.foreign_key_mappings == {}
            assert src_obj.cbc_mapping_hints == {}
        for doc_obj in wave_obj.unstructured_documents:
            assert doc_obj.extracted_candidates == []

    ambient_cli_setup = HarnessSetupConfig(
        provider="antigravity",
        auth=HarnessAuthConfig(auth_mode="ambient_cli", require_credentials_present=True),
    )
    ambient_val = validate_pipeline_inputs(input_spec, ambient_cli_setup, base_dir=sample_dir)
    assert ambient_val.passed is False
    assert any(d["code"] == "HARNESS_AUTH_MISSING" for d in ambient_val.diagnostics)

    setup = HarnessSetupConfig(
        provider="agy",
        steward_hold_policy="auto_approve_top_candidate",
    )
    out_dir = tmp_path / "discovery_first_out"
    rep = run_agentic_vault_pipeline(
        input_spec,
        setup,
        output_dir=out_dir,
        base_dir=sample_dir,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )
    assert rep.status == "COMPLETED"
    assert rep.overall_passed is True
    assert rep.wave_reports[0].self_healing_attempts == 1
    assert rep.wave_reports[3].unstructured_tier_counts["Tier-U1"] == 1
    assert rep.wave_reports[3].unstructured_tier_counts["Tier-U3"] == 1

    final_contract = yaml.safe_load((out_dir / "final_contract.yaml").read_text(encoding="utf-8"))
    hub_names = {h["name"] for h in final_contract.get("hubs", [])}
    assert {"hub_customer", "hub_order", "hub_document"}.issubset(hub_names)
    sat_names = {s["name"] for s in final_contract.get("satellites", [])}
    assert "sat_customer_pii" in sat_names
    assert "sat_customer_core_ext_v2" in sat_names
    assert "sat_document_customer_ai_provenance" in sat_names


def test_real_llm_structured_output_consumption(tmp_path: Path) -> None:
    """Verify WP-03 structured_output consumption across STAGE_00..STAGE_04."""
    sample_dir = _sample_dir()
    input_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")
    wave0_and_3_spec = input_spec.model_copy(
        update={
            "waves": [
                input_spec.waves[0],
                input_spec.waves[3].model_copy(update={"wave_index": 1}),
            ]
        }
    )
    doc_path = (
        sample_dir / "sources" / "wave3_unstructured_msa" / "enterprise_msa_addendum_2026.txt"
    )
    doc_text = doc_path.read_text(encoding="utf-8")
    quote_u1 = "Apex Industrial Holdings LLC (Tax ID: TAX-US-900101)"
    start_u1 = doc_text.index(quote_u1)
    end_u1 = start_u1 + len(quote_u1)

    def _custom_structured_output_factory(spec: Any) -> Any:
        stage_val = spec.stage_id.value
        if stage_val == "STAGE_00_ORCHESTRATION":
            return Stage00OrchestrationPlanOutput(
                wave_execution_order=[0, 1],
                risk_assessment="LLM-grounded ontology plan for commerce vault.",
                recommended_max_retries=3,
            )
        if stage_val == "STAGE_01_SCOUT_SOURCE":
            return Stage01SourceDiscoveryOutput(
                discovered_feeds=[
                    Stage01FeedDiscoveryItem(
                        source_system="crm_salesforce",
                        table_name="crm_customers",
                        record_source_code="CRM_SALESFORCE",
                        target_hub_name="hub_customer",
                        business_key_columns=["customer_tax_id"],
                        surrogate_key_columns=["customer_row_seq"],
                        timestamp_column=None,
                        pii_columns=["contact_email", "tax_ssn_tin"],
                        key_authority_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_namespace="GLOBAL",
                        cbc_mapping_hints={"crm_customers": "CBC_CUSTOMER"},
                    ),
                    Stage01FeedDiscoveryItem(
                        source_system="erp_netsuite",
                        table_name="erp_orders",
                        record_source_code="ERP_NETSUITE",
                        target_hub_name="hub_order",
                        business_key_columns=["order_id"],
                        surrogate_key_columns=[],
                        timestamp_column="order_date",
                        pii_columns=[],
                        key_authority_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_namespace="GLOBAL",
                        foreign_key_mappings={
                            "customer_tax_id": "hub_customer",
                            "promo_campaign_code": "hub_promotion",
                        },
                        cbc_mapping_hints={"erp_orders": "CBC_ORDER"},
                    ),
                ]
            )
        if stage_val == "STAGE_02_SCOUT_UNSTRUCTURED":
            return Stage02UnstructuredDiscoveryOutput(
                doc_id="DOC-MSA-2026-089",
                target_hub_name="hub_document",
                link_name="lnk_document_customer",
                satellite_name="sat_document_customer_ai_provenance",
                extracted_candidates=[
                    PipelineUnstructuredCandidateInput(
                        candidate_id="cand-llm-u1",
                        target_hub="hub_customer",
                        extracted_business_key="TAX-US-900101",
                        verbatim_quote=quote_u1,
                        char_start=start_u1,
                        char_end=end_u1,
                        confidence_score=0.99,
                        er_tier="TIER_U1_DETERMINISTIC",
                        top2_margin=0.95,
                    )
                ],
            )
        if stage_val == "STAGE_03_SHIP_VAULT_PASS1":
            return Stage03ColumnTriageOutput.model_validate(
                {
                    "column_triage_ledger": [
                        {
                            "bronze_table": "crm_salesforce.crm_customers",
                            "column_name": "customer_tax_id",
                            "elm_classification": "BUSINESS_KEY",
                            "target_dv_entity": "hub_customer",
                        }
                    ]
                }
            )
        return None

    setup = HarnessSetupConfig(
        provider="agy",
        steward_hold_policy="auto_approve_top_candidate",
        extra_provider_options={"structured_output_factory": _custom_structured_output_factory},
    )
    out_dir = tmp_path / "llm_structured_out"
    injected_harness = DeterministicHarnessAdapter(
        setup.to_harness_config(
            workspace_dir=tmp_path,
            state_dir=out_dir / "state",
            app_data_dir=out_dir / "app_data",
            save_dir=out_dir / "save",
        ),
        structured_output_factory=_custom_structured_output_factory,
    )
    rep = run_agentic_vault_pipeline(
        wave0_and_3_spec,
        setup,
        output_dir=out_dir,
        base_dir=sample_dir,
        harness=injected_harness,
    )
    assert rep.status == "COMPLETED"
    assert rep.overall_passed is True

    s00_step = next(s for s in rep.step_reports if s.stage_id == "STAGE_00_ORCHESTRATION")
    assert s00_step.metrics.get("llm_structured_plan_used") is True
    s01_step = next(s for s in rep.step_reports if s.stage_id == "STAGE_01_SCOUT_SOURCE")
    assert s01_step.metrics.get("llm_structured_discovery_used") is True
    s02_steps = [
        s
        for s in rep.step_reports
        if s.stage_id == "STAGE_02_SCOUT_UNSTRUCTURED" and s.wave_index == 1
    ]
    assert len(s02_steps) == 1
    assert s02_steps[0].metrics.get("llm_structured_extraction_used") is True
    s03_step = next(s for s in rep.step_reports if s.stage_id == "STAGE_03_SHIP_VAULT_PASS1")
    assert s03_step.metrics.get("llm_structured_triage_used") is True

    final_contract = yaml.safe_load((out_dir / "final_contract.yaml").read_text(encoding="utf-8"))
    triage_targets = {
        (e["bronze_table"], e["column_name"], e["target_dv_entity"])
        for e in final_contract.get("column_triage_ledger", [])
    }
    assert ("crm_salesforce.crm_customers", "customer_tax_id", "hub_customer") in triage_targets


def test_incremental_multi_call_pipeline_evolution(tmp_path: Path) -> None:
    """Verify WP-07 standalone multi-call incremental pipeline evolution across 4 waves."""
    sample_dir = _sample_dir()
    base_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")
    setup = HarnessSetupConfig.from_file(sample_dir / "harness_config.yaml")

    # -----------------------------------------------------------------------
    # Call 1: Wave 0 Cold Start (CRM Customers + ERP Orders)
    # -----------------------------------------------------------------------
    out_w0 = tmp_path / "call_0_wave0"
    spec_w0 = base_spec.model_copy(
        update={
            "initial_contract_path": None,
            "waves": [base_spec.waves[0].model_copy(update={"wave_index": 0})],
        }
    )
    rep_w0 = run_agentic_vault_pipeline(
        spec_w0,
        setup,
        output_dir=out_w0,
        base_dir=sample_dir,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )
    assert rep_w0.status == "COMPLETED"
    assert rep_w0.overall_passed is True
    assert len(rep_w0.wave_reports) == 1
    assert all(v == "PASS" for v in rep_w0.wave_reports[0].gate_scorecard.values())
    assert rep_w0.wave_reports[0].sandbox_summary["passed"] is True
    assert rep_w0.wave_reports[0].sandbox_summary["dynamic_checks_passed"] == 15

    contract_w0_path = out_w0 / "final_contract.yaml"
    contract_w0 = yaml.safe_load(contract_w0_path.read_text(encoding="utf-8"))
    assert contract_w0["lifecycle_stage"] == "COLD_START_N1"
    assert {h["name"] for h in contract_w0["hubs"]} == {
        "hub_customer",
        "hub_order",
        "hub_promotion",
    }
    assert {lnk["name"] for lnk in contract_w0["links"]} == {
        "lnk_order_customer",
        "lnk_order_customer_promotion",
    }
    assert {s["name"] for s in contract_w0["satellites"]} == {
        "sat_customer_core",
        "sat_customer_pii",
        "sat_order_core",
        "sat_promotion_ref",
    }
    assert len(contract_w0["column_triage_ledger"]) == 16

    # -----------------------------------------------------------------------
    # Call 2: Wave 1 Incremental Source (Stripe Invoices delta only)
    # -----------------------------------------------------------------------
    out_w1 = tmp_path / "call_1_wave1"
    spec_w1 = base_spec.model_copy(
        update={
            "initial_contract_path": str(contract_w0_path),
            "waves": [base_spec.waves[1].model_copy(update={"wave_index": 0})],
        }
    )
    rep_w1 = run_agentic_vault_pipeline(
        spec_w1,
        setup,
        output_dir=out_w1,
        base_dir=sample_dir,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )
    assert rep_w1.status == "COMPLETED"
    assert rep_w1.overall_passed is True
    assert len(rep_w1.wave_reports) == 1
    assert all(v == "PASS" for v in rep_w1.wave_reports[0].gate_scorecard.values())
    assert rep_w1.wave_reports[0].sandbox_summary["passed"] is True
    assert rep_w1.wave_reports[0].sandbox_summary["dynamic_checks_passed"] == 15

    contract_w1_path = out_w1 / "final_contract.yaml"
    contract_w1 = yaml.safe_load(contract_w1_path.read_text(encoding="utf-8"))
    assert contract_w1["lifecycle_stage"] == "MULTI_SOURCE_EXPANSION"
    assert {h["name"] for h in contract_w1["hubs"]} == {
        "hub_customer",
        "hub_order",
        "hub_promotion",
        "hub_invoice",
    }
    assert {lnk["name"] for lnk in contract_w1["links"]} == {
        "lnk_order_customer",
        "lnk_order_customer_promotion",
        "lnk_invoice_customer_order",
    }
    assert {s["name"] for s in contract_w1["satellites"]} == {
        "sat_customer_core",
        "sat_customer_pii",
        "sat_order_core",
        "sat_promotion_ref",
        "sat_invoice_core",
    }
    assert len(contract_w1["column_triage_ledger"]) == 21

    # -----------------------------------------------------------------------
    # Call 3: Wave 2 Intra-Source Schema Drift (CRM Customers v2 delta only,
    #         even when baseline_data_path is omitted and inferred from contract)
    # -----------------------------------------------------------------------
    out_w2 = tmp_path / "call_2_wave2"
    w2_src_no_baseline = (
        base_spec.waves[2].sources[0].model_copy(update={"baseline_data_path": None})
    )
    spec_w2 = base_spec.model_copy(
        update={
            "initial_contract_path": str(contract_w1_path),
            "waves": [
                base_spec.waves[2].model_copy(
                    update={"wave_index": 0, "sources": [w2_src_no_baseline]}
                )
            ],
        }
    )
    rep_w2 = run_agentic_vault_pipeline(
        spec_w2,
        setup,
        output_dir=out_w2,
        base_dir=sample_dir,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )
    assert rep_w2.status == "COMPLETED"
    assert rep_w2.overall_passed is True
    assert len(rep_w2.wave_reports) == 1
    assert len(rep_w2.wave_reports[0].schema_drift_events) == 2
    assert "ALG_A9A_INTRA_SOURCE_SCHEMA_DRIFT" in rep_w2.wave_reports[0].applied_codemods
    assert all(v == "PASS" for v in rep_w2.wave_reports[0].gate_scorecard.values())
    assert rep_w2.wave_reports[0].sandbox_summary["passed"] is True
    assert rep_w2.wave_reports[0].sandbox_summary["dynamic_checks_passed"] == 15

    contract_w2_path = out_w2 / "final_contract.yaml"
    contract_w2 = yaml.safe_load(contract_w2_path.read_text(encoding="utf-8"))
    assert contract_w2["lifecycle_stage"] == "MULTI_SOURCE_EXPANSION"
    assert {h["name"] for h in contract_w2["hubs"]} == {
        "hub_customer",
        "hub_order",
        "hub_promotion",
        "hub_invoice",
    }
    assert {lnk["name"] for lnk in contract_w2["links"]} == {
        "lnk_order_customer",
        "lnk_order_customer_promotion",
        "lnk_invoice_customer_order",
    }
    assert {s["name"] for s in contract_w2["satellites"]} == {
        "sat_customer_core",
        "sat_customer_core_ext_v2",
        "sat_customer_pii",
        "sat_order_core",
        "sat_promotion_ref",
        "sat_invoice_core",
    }
    assert len(contract_w2["column_triage_ledger"]) == 23

    # -----------------------------------------------------------------------
    # Call 4: Wave 3 Unstructured Document Onboarding (MSA Addendum only,
    #         zero relational sources in wave input)
    # -----------------------------------------------------------------------
    out_w3 = tmp_path / "call_3_wave3"
    spec_w3 = base_spec.model_copy(
        update={
            "initial_contract_path": str(contract_w2_path),
            "waves": [base_spec.waves[3].model_copy(update={"wave_index": 0})],
        }
    )
    assert validate_pipeline_inputs(spec_w3, setup, base_dir=sample_dir).passed is True
    rep_w3 = run_agentic_vault_pipeline(
        spec_w3,
        setup,
        output_dir=out_w3,
        base_dir=sample_dir,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )
    assert rep_w3.status == "COMPLETED"
    assert rep_w3.overall_passed is True
    assert len(rep_w3.wave_reports) == 1
    assert rep_w3.wave_reports[0].unstructured_tier_counts["Tier-U1"] == 1
    assert rep_w3.wave_reports[0].unstructured_tier_counts["Tier-U3"] == 1
    assert len(rep_w3.resolved_holds) == 1
    assert all(v == "PASS" for v in rep_w3.wave_reports[0].gate_scorecard.values())
    assert rep_w3.wave_reports[0].sandbox_summary["passed"] is True
    assert rep_w3.wave_reports[0].sandbox_summary["dynamic_checks_passed"] == 15

    contract_w3_path = out_w3 / "final_contract.yaml"
    contract_w3 = yaml.safe_load(contract_w3_path.read_text(encoding="utf-8"))
    assert contract_w3["lifecycle_stage"] == "MULTI_SOURCE_EXPANSION"
    assert {h["name"] for h in contract_w3["hubs"]} == {
        "hub_customer",
        "hub_order",
        "hub_promotion",
        "hub_invoice",
        "hub_document",
    }
    assert {lnk["name"] for lnk in contract_w3["links"]} == {
        "lnk_order_customer",
        "lnk_order_customer_promotion",
        "lnk_invoice_customer_order",
        "lnk_document_customer",
    }
    assert {s["name"] for s in contract_w3["satellites"]} == {
        "sat_customer_core",
        "sat_customer_core_ext_v2",
        "sat_customer_pii",
        "sat_order_core",
        "sat_promotion_ref",
        "sat_invoice_core",
        "sat_document_customer_ai_provenance",
        "mas_document_extractions",
    }
    assert len(contract_w3["column_triage_ledger"]) == 24


def test_wp20_semantic_model_tiers_agent_models_and_schema_validation(tmp_path: Path) -> None:
    """Verify WP-20 semantic model_overrides, agent_models, and strict YAML validation."""
    sample_dir = _sample_dir()
    input_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")
    wave0_and_3_spec = input_spec.model_copy(
        update={
            "waves": [
                input_spec.waves[0],
                input_spec.waves[3].model_copy(update={"wave_index": 1}),
            ]
        }
    )

    # 1. Legacy "argon"/"flash" and invalid keys fail with HARNESS_SETUP_INVALID
    legacy_argon_yaml = tmp_path / "bad_legacy_argon.yaml"
    legacy_argon_yaml.write_text(
        "provider: antigravity\n"
        "model_overrides:\n"
        "  argon: gemini-4-argon-high\n"
        "  flash: gemini-3.8-flash-high\n",
        encoding="utf-8",
    )
    legacy_val = validate_pipeline_inputs(
        wave0_and_3_spec,
        legacy_argon_yaml,
        base_dir=sample_dir,
    )
    assert legacy_val.passed is False
    assert any(d["code"] == "HARNESS_SETUP_INVALID" for d in legacy_val.diagnostics)

    bad_tier_yaml = tmp_path / "bad_tier_key.yaml"
    bad_tier_yaml.write_text(
        "provider: antigravity\nmodel_overrides:\n  ultra_heavy: gemini-4-argon-high\n",
        encoding="utf-8",
    )
    bad_tier_val = validate_pipeline_inputs(
        wave0_and_3_spec,
        bad_tier_yaml,
        base_dir=sample_dir,
    )
    assert bad_tier_val.passed is False
    assert any(d["code"] == "HARNESS_SETUP_INVALID" for d in bad_tier_val.diagnostics)

    bad_agent_yaml = tmp_path / "bad_agent_key.yaml"
    bad_agent_yaml.write_text(
        "provider: claude\n"
        "model_overrides:\n"
        "  reasoning: claude-opus-4-6\n"
        "  fast: claude-haiku-4-5\n"
        "agent_models:\n"
        "  rogue-agent: claude-sonnet-4-6\n",
        encoding="utf-8",
    )
    bad_agent_val = validate_pipeline_inputs(
        wave0_and_3_spec,
        bad_agent_yaml,
        base_dir=sample_dir,
    )
    assert bad_agent_val.passed is False
    assert any(d["code"] == "HARNESS_SETUP_INVALID" for d in bad_agent_val.diagnostics)

    # 2. Verify model_overrides routes vault-first-mate & ship-vault to reasoning and scouts to fast
    tier_setup = HarnessSetupConfig(
        provider="antigravity",
        model_overrides={
            "reasoning": "tier-reasoning-v2",
            "fast": "tier-fast-v2",
        },
        steward_hold_policy="auto_approve_top_candidate",
    )
    tier_rep = run_agentic_vault_pipeline(
        wave0_and_3_spec,
        tier_setup,
        output_dir=tmp_path / "out_tier_only",
        base_dir=sample_dir,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )
    assert tier_rep.overall_passed is True
    for step in tier_rep.step_reports:
        if step.persona in {"vault-first-mate", "ship-vault"}:
            assert step.model_tier == "reasoning"
            assert step.model_used == "tier-reasoning-v2"
        elif step.persona in {"scout-source", "scout-unstructured"}:
            assert step.model_tier == "fast"
            assert step.model_used == "tier-fast-v2"

    # 3. Verify agent_models takes precedence over model_overrides in PipelineStepReport.model_used
    agent_setup = HarnessSetupConfig(
        provider="claude",
        model_overrides={
            "reasoning": "tier-reasoning-v2",
            "fast": "tier-fast-v2",
        },
        agent_models={
            "scout-unstructured": "custom-unstructured-v1",
            "ship-vault": "custom-ship-v1",
        },
        steward_hold_policy="auto_approve_top_candidate",
    )
    agent_rep = run_agentic_vault_pipeline(
        wave0_and_3_spec,
        agent_setup,
        output_dir=tmp_path / "out_agent_models",
        base_dir=sample_dir,
        sdk_module=build_deterministic_claude_sdk_shim(),
    )
    assert agent_rep.overall_passed is True
    assert agent_rep.harness_setup["model_overrides"] == {
        "reasoning": "tier-reasoning-v2",
        "fast": "tier-fast-v2",
    }
    assert agent_rep.harness_setup["agent_models"] == {
        "scout-unstructured": "custom-unstructured-v1",
        "ship-vault": "custom-ship-v1",
    }

    for step in agent_rep.step_reports:
        if step.persona == "vault-first-mate":
            assert step.model_tier == "reasoning"
            assert step.model_used == "tier-reasoning-v2"
        elif step.persona == "scout-source":
            assert step.model_tier == "fast"
            assert step.model_used == "tier-fast-v2"
        elif step.persona == "scout-unstructured":
            assert step.model_tier == "fast"
            assert step.model_used == "custom-unstructured-v1"
        elif step.persona == "ship-vault":
            assert step.model_tier == "reasoning"
            assert step.model_used == "custom-ship-v1"


def test_wp23_claude_cli_harness_config_validation_and_four_waves_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify WP-23 claude-cli sample config, auth preflight/mode validation, and 4-wave run."""
    import shutil

    sample_dir = _sample_dir()
    input_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")
    single_wave_spec = input_spec.model_copy(update={"waves": [input_spec.waves[0]]})

    # 1. Load sample harness_config_claude_cli.yaml and verify defaults
    claude_cli_sample = HarnessSetupConfig.from_file(sample_dir / "harness_config_claude_cli.yaml")
    assert claude_cli_sample.provider == "claude-cli"
    assert claude_cli_sample.auth.mode == "cli_session"
    assert claude_cli_sample.auth.auth_mode == "cli_session"
    assert claude_cli_sample.auth.require_preflight is False
    assert claude_cli_sample.model_overrides == {
        "reasoning": "claude-opus-4-6",
        "fast": "claude-haiku-4-5",
    }
    assert (
        validate_pipeline_inputs(
            single_wave_spec,
            claude_cli_sample,
            base_dir=sample_dir,
        ).passed
        is True
    )

    # 2. Invalid auth.mode (such as "adc") on claude-cli emits HARNESS_AUTH_INVALID
    bad_adc_setup = HarnessSetupConfig.model_validate(
        {
            "provider": "claude-cli",
            "auth": {
                "mode": "adc",
                "require_preflight": False,
            },
        }
    )
    bad_adc_val = validate_pipeline_inputs(
        single_wave_spec,
        bad_adc_setup,
        base_dir=sample_dir,
    )
    assert bad_adc_val.passed is False
    assert any(d["code"] == "HARNESS_AUTH_INVALID" for d in bad_adc_val.diagnostics)

    # 3. require_preflight=True when claude binary is missing emits HARNESS_PROVIDER_UNAVAILABLE
    monkeypatch.delenv("OPENDV_CLAUDE_CLI_BIN", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    preflight_missing_setup = HarnessSetupConfig.model_validate(
        {
            "provider": "claude-cli",
            "auth": {
                "mode": "cli_session",
                "require_preflight": True,
            },
        }
    )
    preflight_missing_val = validate_pipeline_inputs(
        single_wave_spec,
        preflight_missing_setup,
        base_dir=sample_dir,
    )
    assert preflight_missing_val.passed is False
    assert any(
        d["code"] == "HARNESS_PROVIDER_UNAVAILABLE" for d in preflight_missing_val.diagnostics
    )

    # When claude binary is resolvable via extra_provider_options["claude_binary"], preflight passes
    fake_claude_bin = tmp_path / "claude"
    fake_claude_bin.write_text("#!/bin/sh\necho '{}'\n", encoding="utf-8")
    preflight_ok_setup = preflight_missing_setup.model_copy(
        update={"extra_provider_options": {"claude_binary": str(fake_claude_bin)}}
    )
    assert (
        validate_pipeline_inputs(
            single_wave_spec,
            preflight_ok_setup,
            base_dir=sample_dir,
        ).passed
        is True
    )

    # 4. Run full 4-wave pipeline with harness_config_claude_cli.yaml and agent_models override
    custom_claude_cli_setup = claude_cli_sample.model_copy(
        update={
            "agent_models": {
                "scout-unstructured": "claude-sonnet-4-6",
                "ship-vault": "claude-opus-4-6-custom",
            }
        }
    )
    out_dir = tmp_path / "out_claude_cli_4waves"
    rep = run_agentic_vault_pipeline(
        input_spec,
        custom_claude_cli_setup,
        output_dir=out_dir,
        base_dir=sample_dir,
        sdk_module=build_deterministic_claude_sdk_shim(),
    )
    assert rep.status == "COMPLETED"
    assert rep.overall_passed is True
    assert len(rep.wave_reports) == 4
    assert rep.harness_setup["provider"] == "claude-cli"
    for step in rep.step_reports:
        if step.persona == "vault-first-mate":
            assert step.model_used == "claude-opus-4-6"
        elif step.persona == "scout-source":
            assert step.model_used == "claude-haiku-4-5"
        elif step.persona == "scout-unstructured":
            assert step.model_used == "claude-sonnet-4-6"
        elif step.persona == "ship-vault":
            assert step.model_used == "claude-opus-4-6-custom"


def test_wp24_pipeline_stage_and_stream_callbacks_order_and_metadata(tmp_path: Path) -> None:
    """Verify WP-24 on_stage_start, on_stage_complete, and on_stream_event callbacks."""
    from opendv_agent_harness.models import HarnessEventKind, HarnessStreamEvent
    from opendv_agent_harness.pipeline import PipelineStepReport

    sample_dir = _sample_dir()
    input_spec = PipelineInputSpec.from_file(sample_dir / "wave0_cold_start.yaml")
    setup = HarnessSetupConfig(
        provider="antigravity",
        model_overrides={
            "reasoning": "gemini-4-argon-high",
            "fast": "gemini-3.8-flash-high",
        },
        steward_hold_policy="auto_approve_top_candidate",
    )

    started_stages: list[tuple[int, str, str, str]] = []
    completed_stages: list[tuple[int, PipelineStepReport]] = []
    stream_events: list[HarnessStreamEvent] = []

    rep = run_agentic_vault_pipeline(
        input_spec,
        setup,
        output_dir=tmp_path / "wp24_callbacks_out",
        base_dir=sample_dir,
        sdk_module=build_deterministic_agy_sdk_shim(),
        on_stage_start=lambda w, s, p, m: started_stages.append((w, s, p, m)),
        on_stage_complete=lambda w, sr: completed_stages.append((w, sr)),
        on_stream_event=lambda ev: stream_events.append(ev),
    )
    assert rep.overall_passed is True

    expected_stage_ids = [
        "STAGE_00_ORCHESTRATION",
        "STAGE_01_SCOUT_SOURCE",
        "STAGE_02_SCOUT_UNSTRUCTURED",
        "STAGE_03_SHIP_VAULT_PASS1",
        "STAGE_04_SHIP_VAULT_PASS2",
        "STAGE_05_AXI_DV_STATIC_GATES",
        "STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
        "STAGE_07_DETERMINISTIC_COMPILER",
    ]
    assert [s[1] for s in started_stages] == expected_stage_ids
    assert [c[1].stage_id for c in completed_stages] == expected_stage_ids
    for wave_idx, step_report in completed_stages:
        assert wave_idx == 0
        assert step_report.agent_persona == step_report.persona
        assert step_report.verdict == step_report.status
        assert step_report.verdict in {"PASS", "AUTO_REPAIRED"}
        assert step_report.duration_ms >= 0.0
        assert len(step_report.summary) > 0

    text_deltas = [
        ev.delta_text
        for ev in stream_events
        if ev.event_kind == HarnessEventKind.TEXT_DELTA and ev.delta_text
    ]
    assert len(text_deltas) > 0
