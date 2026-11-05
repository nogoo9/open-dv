"""Explorer artifact builders and visualizer payloads.

Conforms to User Directive R9 (file size <500 LOC / <20 KB) and Decision D3.
"""

from __future__ import annotations

import json

from opendv_contracts.dtos.explorer import (
    ArtifactDTO,
)
from opendv_service.use_cases.explorer_artifact_inputs import (
    _art,
    build_input_artifacts,
)
from opendv_service.use_cases.explorer_canonical import (
    build_canonical_doc_viz,
    build_canonical_dv_viz,
)


def build_bundle_artifacts(
    run_id: str,
    project_id: str,
    is_hold: bool,
    events_summary: list[tuple[str, str, str]],
) -> dict[str, ArtifactDTO]:
    """Construct all 24 canonical Explorer artifacts for the 8-stage pipeline."""
    dv_viz = build_canonical_dv_viz()
    doc_viz = build_canonical_doc_viz(is_hold=is_hold)
    all_w = ["W0", "W1", "W2", "W3"]

    draft_raw = (
        "version: '5.0'\nmodel_name: draft_commerce_vault\nhubs:\n"
        "  - hub_customer: { business_key: customer_tax_id }\n"
        "  - hub_order:    { business_key: order_id }"
    )
    final_contract_raw = (
        "version: '5.0'\nmodel_name: global_commerce_vault\nhubs:\n"
        "  - hub_customer: { business_key: customer_tax_id }\n"
        "  - hub_order:    { business_key: order_id }\n"
        "links:\n  - lnk_order_customer: { source: erp_orders }\nsatellites:\n"
        "  - sat_customer_core: { split: CORE }\n  - sat_customer_pii:  { split: RESTRICTED_PII }"
    )
    toon_raw = (
        "@toon_v1 trace:\n  stage: STAGE_05_AXI_DV_STATIC_GATES\n  attempt: 1\n"
        "  rule: DV-COL-012\n  target: stripe_customer_code\n"
        "  diagnosis: BKCC unscoped collision\n  repair_action: patch bkcc_prefix to STRIPE_EU"
    )
    sql_raw = (
        "-- 4-Zone DuckDB Data Vault 2.0 DDL\n"
        "CREATE SCHEMA IF NOT EXISTS bronze;\nCREATE SCHEMA IF NOT EXISTS staging;\n"
        "CREATE SCHEMA IF NOT EXISTS raw_vault;\nCREATE SCHEMA IF NOT EXISTS business_vault;\n\n"
        "CREATE TABLE raw_vault.hub_customer (\n"
        "    hk_customer VARCHAR(64) PRIMARY KEY,\n"
        "    customer_tax_id VARCHAR(64) NOT NULL,\n"
        "    load_dts TIMESTAMP WITH TIME ZONE NOT NULL,\n"
        "    rec_src VARCHAR(64) NOT NULL\n);"
    )

    artifacts = build_input_artifacts(project_id=project_id, is_hold=is_hold)

    stage_outputs: dict[str, ArtifactDTO] = {
        "grounding": _art(
            "grounding",
            "concept_grounding_ledger.json",
            "100% tables grounded",
            "Stage output",
            "ConceptGroundingLedger",
            "JSON",
            "table",
            "Bijection between physical source feeds and Graph A concepts.",
            json.dumps([{"source": "crm_customers", "concept": "CBC_CUSTOMER"}], indent=2),
            all_w,
            0,
            cols=["source_table", "canonical_concept", "archetype", "mapped_keys"],
            rows=[
                ["crm_customers", "CBC_CUSTOMER", "WHO_PARTY", "customer_id"],
                ["erp_orders", "CBC_ORDER", "WHAT_EVENT_ANCHOR", "order_id"],
                ["stripe_eu_invoices", "CBC_INVOICE", "WHAT_FINANCIAL", "invoice_id"],
            ],
        ),
        "profile": _art(
            "profile",
            "graph_b_profile.json",
            "24 columns profiled",
            "Stage output",
            "RelationalProfileReport",
            "JSON",
            "table",
            "Information-theoretic column profiles and surrogate key quarantine.",
            json.dumps({"tables": 4, "columns": 24, "surrogates": ["row_seq_id"]}, indent=2),
            all_w,
            0,
            cols=["table", "column", "data_type", "uniqueness", "null_ratio"],
            rows=[
                ["crm_customers", "customer_id", "VARCHAR", "1.00", "0.00"],
                ["crm_customers", "tax_identifier", "VARCHAR", "1.00", "0.00"],
                ["erp_orders", "order_id", "VARCHAR", "1.00", "0.00"],
            ],
        ),
        "drift": _art(
            "drift",
            "schema_drift_report.json",
            "3 drift changes classified",
            "Stage output",
            "SchemaDriftReport",
            "JSON",
            "table",
            "Algorithm A.9a intra-source schema evolution classification report.",
            json.dumps([{"change": "ADDITIVE", "column": "loyalty_tier_2026"}], indent=2),
            ["W2"],
            2,
            cols=["source", "change_kind", "column", "algorithm", "verdict"],
            rows=[
                ["crm_customers", "ADDITIVE", "loyalty_tier_2026", "ALG-A9A", "PASS"],
                ["crm_customers", "DEPRECATED", "fax_number", "ALG-A9A", "PASS"],
            ],
        ),
        "extract": _art(
            "extract",
            "unstructured_extractions.json",
            "2 spans · 1 U1 match, 1 hold",
            "Stage output",
            "UnstructuredExtractionLedger",
            "JSON",
            "doc",
            "Algorithm A.9b verbatim span extractions and steward hold resolution.",
            json.dumps(doc_viz.model_dump(mode="json"), indent=2),
            ["W3"],
            3,
            doc=doc_viz,
        ),
        "triage": _art(
            "triage",
            "column_triage_ledger.json",
            "24/24 columns assigned",
            "Stage output",
            "ColumnTriageLedger",
            "JSON",
            "table",
            "Exhaustive column accounting ledger matching Gate 1 bijection.",
            json.dumps([{"column": "customer_id", "assigned_role": "BUSINESS_KEY"}], indent=2),
            all_w,
            0,
            cols=["table", "column", "assigned_role", "gate1_status"],
            rows=[
                ["crm_customers", "customer_id", "BUSINESS_KEY", "PASS"],
                ["crm_customers", "tax_identifier", "BUSINESS_KEY", "PASS"],
                ["erp_orders", "order_id", "BUSINESS_KEY", "PASS"],
            ],
        ),
        "draft_contract": _art(
            "draft_contract",
            "draft_contract.yaml",
            "5 Hubs, 4 Links, 7 Sats",
            "Stage output",
            "DataVaultModelContract",
            "YAML",
            "dv",
            "Initial declarative Data Vault 2.0 contract draft before symbolic gates.",
            draft_raw,
            all_w,
            0,
            dv=dv_viz,
        ),
        "toon": _art(
            "toon",
            "axi_diagnostic_trace.toon",
            "44 tokens · closed repair",
            "Stage output",
            "AxiVerifierDiagnosticTrace",
            "TOON",
            "code",
            "Machine-readable symbolic verifier diagnostic trace for closed-loop repair.",
            toon_raw,
            all_w,
            0,
            outline=[("Gate 1", "Schema grain check"), ("Diagnostic", "@toon_v1 repair trace")],
        ),
        "gate_static": _art(
            "gate_static",
            "static_gates_scorecard.json",
            "99/99 checks PASS",
            "Stage output",
            "StaticGatesScorecard",
            "JSON",
            "checks",
            "Formal static verification scorecard across Gates 0 through 4.",
            json.dumps({"G0": "PASS", "G1": "PASS", "G2": "PASS", "G3": "PASS", "G4": "PASS"}),
            all_w,
            0,
            checks=[
                ("G0", "Profile & Drift", "PASS"),
                ("G1", "Grain & BKCC", "AUTO_REPAIRED" if is_hold else "PASS"),
                ("G2", "SQLGlot AST", "PASS"),
                ("G3", "NetworkX DAG", "PASS"),
                ("G4", "YAGNI Guard", "PASS"),
            ],
        ),
        "final_contract": _art(
            "final_contract",
            "final_contract.yaml",
            "Verified DV 2.0 contract",
            "Final output",
            "DataVaultModelContract",
            "YAML",
            "dv",
            "Verified, production-ready declarative Data Vault 2.0 contract.",
            final_contract_raw,
            all_w,
            0,
            dv=dv_viz,
        ),
        "gate_sandbox": _art(
            "gate_sandbox",
            "sandbox_execution_scorecard.json",
            "4 zones compiled · PASS",
            "Stage output",
            "SandboxExecutionScorecard",
            "JSON",
            "checks",
            "Gate 5 DuckDB analytical sandbox execution verification scorecard.",
            json.dumps({"G5": "PASS", "batches_ingested": 4}, indent=2),
            all_w,
            0,
            checks=[("G5", "DuckDB Analytical Sandbox", "PASS")],
        ),
        "sandbox_report": _art(
            "sandbox_report",
            "sandbox_report.json",
            "15 dynamic assertions PASS",
            "Stage output",
            "SandboxVerificationReport",
            "JSON",
            "checks",
            "DV-DYN-001..015 dynamic point-in-time and referential integrity report.",
            json.dumps({"assertions_tested": 15, "all_passed": True}, indent=2),
            all_w,
            0,
            checks=[
                ("DV-DYN-001", "Hub primary key uniqueness", "PASS"),
                ("DV-DYN-002", "Link foreign key referential integrity", "PASS"),
                ("DV-DYN-003", "Satellite hash_diff delta tracking", "PASS"),
            ],
        ),
        "final_ir": _art(
            "final_ir",
            "final_canonical_ir.json",
            "Draft 2020-12 IR",
            "Final output",
            "CanonicalDataVaultIR",
            "JSON",
            "code",
            "Deterministic Canonical Intermediate Representation.",
            json.dumps({"hubs": ["hub_customer", "hub_order"]}, indent=2),
            all_w,
            0,
            outline=[
                ("Hubs", "hub_customer, hub_order"),
                ("Links", "lnk_order_customer"),
                ("Satellites", "sat_customer_core, sat_customer_pii"),
            ],
        ),
        "final_sql": _art(
            "final_sql",
            "final_four_zone.sql",
            "Zones 0-3 DDL + DML",
            "Final output",
            "FourZoneDuckDBSQL",
            "SQL",
            "code",
            "Complete 4-zone DuckDB Data Vault 2.0 executable SQL pipeline.",
            sql_raw,
            all_w,
            0,
            outline=[
                ("Zone 0", "Raw Bronze view staging"),
                ("Zone 1", "Staging Views with hash keys"),
                ("Zone 2", "Raw Data Vault tables"),
                ("Zone 3", "Business Vault PIT tables"),
            ],
        ),
        "report": _art(
            "report",
            "pipeline_report.json",
            "8 stages · sealed execution",
            "Final output",
            "AgenticPipelineReport",
            "JSON",
            "checks",
            "Complete end-to-end execution audit report.",
            json.dumps({"run_id": run_id, "project_id": project_id, "status": "COMPLETED"}),
            all_w,
            0,
            checks=[
                ("Stage 00", "Orchestration & Grounding", "PASS"),
                ("Stage 05", "Static Verification", "PASS"),
                ("Stage 06", "DuckDB Sandbox", "PASS"),
                ("Stage 07", "Deterministic Compiler", "PASS"),
            ],
        ),
        "ledger": _art(
            "ledger",
            "vault-ledger.jsonl",
            "Observability event log",
            "Final output",
            "VaultObservabilityLedger",
            "JSONL",
            "events",
            "Canonical JSONL stream of all observability events emitted during run.",
            '{"event_id": "evt-001", "stage": "STAGE_00", "status": "PASS"}\n'
            '{"event_id": "evt-002", "stage": "STAGE_05", "status": "PASS"}',
            all_w,
            0,
            events=events_summary,
        ),
    }

    artifacts.update(stage_outputs)
    return artifacts
