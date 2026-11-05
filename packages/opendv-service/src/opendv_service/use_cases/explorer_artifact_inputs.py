"""Input and setting artifact builders for the Explorer bundle.

Conforms to User Directive R9 (file size <500 LOC / <20 KB) and Decision D3.
"""

from __future__ import annotations

from typing import Any

from opendv_contracts.dtos.explorer import (
    ArtifactDTO,
    ArtifactGroup,
    VizKind,
)
from opendv_service.use_cases.explorer_canonical import (
    build_canonical_doc_viz,
    build_canonical_ontology_viz,
)


def _art(
    id: str,
    name: str,
    short: str,
    group: ArtifactGroup,
    type: str,
    fmt: str,
    viz: VizKind,
    desc: str,
    raw: str,
    waves: list[str],
    wave_index: int = 0,
    **kwargs: Any,
) -> ArtifactDTO:
    """Helper to construct ArtifactDTO concisely."""
    return ArtifactDTO(
        id=id,
        name=name,
        short=short,
        group=group,
        type=type,
        fmt=fmt,
        viz=viz,
        desc=desc,
        raw=raw,
        waves=waves,
        wave_index=wave_index,
        **kwargs,
    )


def build_input_artifacts(
    project_id: str,
    is_hold: bool,
) -> dict[str, ArtifactDTO]:
    """Construct settings and input artifacts."""
    ontology_viz = build_canonical_ontology_viz()
    doc_viz = build_canonical_doc_viz(is_hold=is_hold)
    all_w = ["W0", "W1", "W2", "W3"]

    harness_raw = (
        "version: '1.0'\ndefault_provider: antigravity\nmax_self_healing_retries: 3\n"
        "personas:\n"
        "  vault-first-mate:       { model_tier: pro,   temperature: 0.1 }\n"
        "  scout-source:           { model_tier: flash, temperature: 0.0 }\n"
        "  scout-unstructured:     { model_tier: flash, temperature: 0.0 }\n"
        "  ship-vault:             { model_tier: pro,   temperature: 0.1 }\n"
        "  axi-dv:                 { mode: deterministic }\n"
        "  duckdb-sandbox:         { mode: deterministic }\n"
        "  deterministic-compiler: { mode: deterministic }"
    )
    runopts_raw = (
        "--steward-hold-policy auto_approve_top_candidate\n"
        "--rbac-role INTERNAL_MODELER\n"
        "--target-dialect duckdb\n"
        "--pit-as-of 2026-01-16T00:00:00Z\n"
        "--seal-cas"
    )
    spec_raw = (
        f"project_id: {project_id}\n"
        "ontology_path: ontology/graph_a_ontology.yaml\n"
        "waves:\n"
        "  - wave_id: wave_0_cold_start\n"
        "    relational_sources: [sources/wave0_crm_customers.csv, sources/wave0_erp_orders.csv]\n"
        "  - wave_id: wave_1_incremental_stripe\n"
        "    relational_sources: [sources/wave1_stripe_eu_invoices.csv]\n"
        "  - wave_id: wave_2_crm_schema_drift\n"
        "    relational_sources: [sources/wave2_crm_customers_v2_drift.csv]\n"
        "  - wave_id: wave_3_unstructured_msa\n"
        "    unstructured_sources: [sources/wave3_enterprise_msa_addendum_2026.txt]"
    )
    ont_raw = (
        "domain: global_commerce\nversion: '1.0.0'\nconcepts:\n"
        "  - name: CBC_CUSTOMER\n    archetype: WHO_PARTY\n    keys: [customer_tax_id]\n"
        "  - name: CBC_ORDER\n    archetype: WHAT_EVENT_ANCHOR\n    keys: [order_id]\n"
        "  - name: CBC_INVOICE\n    archetype: WHAT_FINANCIAL_INSTRUMENT\n"
        "    keys: [invoice_number]\n"
        "  - name: CBC_PROMOTION\n    archetype: HOW_CONTRACTUAL_RULE\n"
        "    keys: [promo_campaign_code]\n"
        "  - name: CBC_CONTRACT_DOCUMENT\n    archetype: WHAT_UNSTRUCTURED_ARTIFACT\n"
        "    keys: [doc_id]"
    )

    return {
        "harness": _art(
            "harness",
            "harness_config.yaml",
            "agy · pro + flash · 3 retries",
            "Setting",
            "AgentHarnessConfig",
            "YAML",
            "kv",
            "LLM provider configuration, persona tiers, and self-healing retry caps.",
            harness_raw,
            all_w,
            0,
            kv=[
                ("default_provider", "antigravity (agy)"),
                ("@vault-first-mate", "pro · T=0.1 · STAGE_00"),
                ("@scout-source", "flash · T=0.0 · STAGE_01"),
                ("@scout-unstructured", "flash · T=0.0 · STAGE_02"),
                ("@ship-vault", "pro · T=0.1 · STAGE_03, STAGE_04"),
                ("@axi-dv", "deterministic (no LLM) · STAGE_05"),
                ("@duckdb-sandbox", "deterministic (no LLM) · STAGE_06"),
                ("@deterministic-compiler", "deterministic (no LLM) · STAGE_07"),
                ("max_self_healing_retries", "3 (Algorithm A.8)"),
            ],
        ),
        "runopts": _art(
            "runopts",
            "run options",
            "auto_approve_top · INTERNAL_MODELER",
            "Setting",
            "PipelineExecutionOptions",
            "Flags",
            "kv",
            "Runtime governance options, RBAC classification, and target dialect.",
            runopts_raw,
            all_w,
            0,
            kv=[
                ("steward_hold_policy", "auto_approve_top_candidate"),
                ("rbac_role", "INTERNAL_MODELER"),
                ("hash_algorithm", "SHA-256 length-prefixed (Algorithm A.5)"),
                ("target_dialect", "DuckDB 4-zone (Bronze / Staging / Raw / Business)"),
                ("cas_sealing", "enabled (SHA-256 content-addressable)"),
            ],
        ),
        "spec": _art(
            "spec",
            "pipeline_input.yaml",
            "4 waves · W0 cold → W3 MSA",
            "Input",
            "AgenticPipelineInputSpec",
            "YAML",
            "kv",
            "Master pipeline manifest declaring waves, sources, and ontology.",
            spec_raw,
            all_w,
            0,
            kv=[
                ("project_id", project_id),
                ("ontology_path", "ontology/graph_a_ontology.yaml"),
                ("initial_contract", "none (Wave 0 cold start)"),
                ("Wave 0 · cold_start", "crm_customers.csv, erp_orders.csv"),
                ("Wave 1 · incremental", "stripe_eu_invoices.csv (N=2 → N=3)"),
                ("Wave 2 · schema_drift", "crm_customers_v2_drift.csv (S1(t) → S1(t+1))"),
                ("Wave 3 · unstructured_msa", "enterprise_msa_addendum_2026.txt"),
            ],
        ),
        "ontology": _art(
            "ontology",
            "graph_a_ontology.yaml",
            "5 concepts · Customer, Order, Invoice…",
            "Input",
            "GraphAOntologySpec",
            "YAML",
            "ontology",
            "Canonical enterprise business ontology defining concepts and relationships.",
            ont_raw,
            all_w,
            0,
            ontology=ontology_viz,
        ),
        "crm": _art(
            "crm",
            "crm_customers.csv",
            "Wave 0 · 1,000 customers",
            "Input",
            "RelationalSourceTable",
            "CSV",
            "table",
            "Customer CRM master data for Wave 0 cold start onboarding.",
            (
                "customer_id,company_name,tax_identifier,created_date\n"
                "CUST-001,Acme Logistics,TAX-US-10001,2026-01-01\n"
                "CUST-002,Global Freight,TAX-US-10002,2026-01-02"
            ),
            ["W0"],
            0,
            cols=["customer_id", "company_name", "tax_identifier", "created_date"],
            rows=[
                ["CUST-001", "Acme Logistics", "TAX-US-10001", "2026-01-01"],
                ["CUST-002", "Global Freight", "TAX-US-10002", "2026-01-02"],
            ],
        ),
        "erp": _art(
            "erp",
            "erp_orders.csv",
            "Wave 0 · 5,000 orders",
            "Input",
            "RelationalSourceTable",
            "CSV",
            "table",
            "ERP sales order transaction ledger.",
            (
                "order_id,customer_id,order_date,total_amount\n"
                "ORD-1001,CUST-001,2026-01-02,1450.00\n"
                "ORD-1002,CUST-002,2026-01-03,890.50"
            ),
            ["W0"],
            0,
            cols=["order_id", "customer_id", "order_date", "total_amount"],
            rows=[
                ["ORD-1001", "CUST-001", "2026-01-02", "1450.00"],
                ["ORD-1002", "CUST-002", "2026-01-03", "890.50"],
            ],
        ),
        "stripe": _art(
            "stripe",
            "stripe_eu_invoices.csv",
            "Wave 1 · 3,200 invoices",
            "Input",
            "RelationalSourceTable",
            "CSV",
            "table",
            "Incremental Stripe European billing settlement records.",
            (
                "invoice_id,order_id,stripe_customer_code,amount_paid\n"
                "INV-8801,ORD-1001,cus_8892_eu,1450.00\n"
                "INV-8802,ORD-1002,cus_9912_eu,890.50"
            ),
            ["W1"],
            1,
            cols=["invoice_id", "order_id", "stripe_customer_code", "amount_paid"],
            rows=[
                ["INV-8801", "ORD-1001", "cus_8892_eu", "1450.00"],
                ["INV-8802", "ORD-1002", "cus_9912_eu", "890.50"],
            ],
        ),
        "crm2": _art(
            "crm2",
            "crm_customers_v2_drift.csv",
            "Wave 2 · Schema drift",
            "Input",
            "RelationalSourceTable",
            "CSV",
            "table",
            "CRM schema evolution source carrying additive loyalty tier column.",
            (
                "customer_id,company_name,tax_identifier,loyalty_tier_2026\n"
                "CUST-001,Acme Logistics,TAX-US-10001,GOLD\n"
                "CUST-002,Global Freight,TAX-US-10002,SILVER"
            ),
            ["W2"],
            2,
            cols=["customer_id", "company_name", "tax_identifier", "loyalty_tier_2026"],
            rows=[
                ["CUST-001", "Acme Logistics", "TAX-US-10001", "GOLD"],
                ["CUST-002", "Global Freight", "TAX-US-10002", "SILVER"],
            ],
        ),
        "msa": _art(
            "msa",
            "enterprise_msa_addendum_2026.txt",
            "Wave 3 · Unstructured MSA",
            "Input",
            "UnstructuredContractDocument",
            "TXT",
            "doc",
            "Unstructured legal enterprise master services agreement addendum.",
            doc_viz.source_text,
            ["W3"],
            3,
            doc=doc_viz,
        ),
    }
