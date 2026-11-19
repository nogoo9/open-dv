"""Phase 2 TDD Tests: Stage 02 Unmodeled Drift (`UF-1..UF-5`) Loopback (`FR-DVM-16`).

Covers:
1. Stage 02 detects unmodeled feeds and column drift (`UF-1..UF-5` taxonomy codes)
   and surfaces structured drift findings in `AgenticPipelineReport` for Step 4 sync (`D10`).
2. Each structured drift finding includes `code` in `{"UF-1", "UF-2", "UF-3", "UF-4", "UF-5"}`,
   `source_table`, `column_name`, `drift_type`, and `suggested_op`.
"""

from __future__ import annotations

from pathlib import Path

from opendv_agent_harness.pipeline.config import HarnessSetupConfig
from opendv_agent_harness.pipeline.runner import run_agentic_vault_pipeline
from opendv_agent_harness.pipeline.spec import PipelineInputSpec
from opendv_testkit import build_deterministic_agy_sdk_shim

REPO_ROOT = Path(__file__).resolve().parents[1]
S3_DIR = REPO_ROOT / "examples" / "scenarios" / "s3_unmapped_feed"
UF2_DIR = REPO_ROOT / "examples" / "scenarios" / "uf2_renamed_column"


def test_stage02_surfaces_uf1_to_uf5_structured_drift_findings_in_report(tmp_path: Path) -> None:
    """Stage 02 surfaces structured `UF-1..UF-5` drift findings in `AgenticPipelineReport`."""
    spec = PipelineInputSpec.model_validate(
        {
            "pipeline_id": "uf_drift_loopback_test",
            "project_id": "proj_uf_drift",
            "domain_namespace": "enterprise_crm",
            "graph_a_ontology_path": str(UF2_DIR / "graph_a_ontology.yaml"),
            "initial_contract_path": str(UF2_DIR / "contract_w0.yaml"),
            "sources": [
                {
                    "feed_id": "crm_customers_w1",
                    "source_system": "bronze_crm",
                    "table_name": "customers",
                    "data_path": str(UF2_DIR / "sources" / "wave1_renamed" / "crm_customers.csv"),
                    "record_source_code": "CRM_CORE",
                    "business_key_columns": ["customer_tax_id"],
                },
                {
                    "feed_id": "unmapped_supplier",
                    "source_system": "bronze_logistics",
                    "table_name": "feed_unmapped_supplier",
                    "data_path": str(S3_DIR / "sources" / "feed_unmapped_supplier.csv"),
                    "record_source_code": "LOGISTICS_FEED",
                    "business_key_columns": ["delivery_id"],
                },
            ],
        }
    )
    setup = HarnessSetupConfig(
        provider="agy",
        stage_output_policy="symbolic_completion",
        steward_hold_policy="auto_approve_top_candidate",
    )
    report = run_agentic_vault_pipeline(
        spec,
        setup,
        output_dir=tmp_path / "out_uf_drift",
        base_dir=REPO_ROOT,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )

    findings = list(getattr(report, "drift_findings", None) or [])
    if not findings:
        findings = [
            f
            for w in report.wave_reports
            for f in (getattr(w, "schema_drift_events", None) or [])
        ]
    assert findings, "Expected Stage 02 structured UF-1..UF-5 drift findings in AgenticPipelineReport"

    uf_codes = {str(f.get("code", "")) for f in findings if isinstance(f, dict)}
    assert uf_codes & {"UF-1", "UF-2", "UF-3", "UF-4", "UF-5"}, (
        f"Expected structured UF-1..UF-5 code in drift findings, got: {findings}"
    )
    for item in findings:
        if item.get("code") in {"UF-1", "UF-2", "UF-3", "UF-4", "UF-5"}:
            assert item.get("source_table")
            assert item.get("drift_type")
            assert item.get("suggested_op")
