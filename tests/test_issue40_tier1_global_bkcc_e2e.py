"""End-to-End Verification Suite for GitHub Issue #40 (GAP-09 / DV-COL-013).

Validates Scenario S1 (Cold Start) and Scenario S2 (Multi-Source Convergence)
without mocks using real CSV datasets, GraphBProfiler, 6-Gate static verification,
CanonicalIR lowering, 4-Zone DuckDB SQL compilation, and 4-Batch Adversarial
Shadow Sandbox execution.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import duckdb

from opendv_axi_verifier import (
    run_axi_dv_verification_pipeline,
    verify_additive_evolution_invariants,
    verify_column_invariants,
    verify_gate1_contract_and_grain,
)
from opendv_core import (
    GLOBAL_BKCC,
    compute_dv2_length_prefixed_sha256,
    compute_dv2_prehash_string,
    dv2_canonical_hash,
    resolve_fk_hash_key_with_zero_keys,
    validate_canonical_ir,
    validate_contract_v5,
)
from opendv_duckdb_sandbox.compiler_4zone import compile_canonical_ir_to_4zone_sql
from opendv_duckdb_sandbox.graph_b_profiler import GraphBProfiler
from opendv_duckdb_sandbox.ir_lowerer import lower_contract_to_canonical_ir
from opendv_duckdb_sandbox.sandbox_engine import DuckDbShadowSandbox


def _build_issue40_ontology() -> dict[str, Any]:
    """Build the Enterprise Party & Order Ontology (Graph A) for S1 -> S2."""
    return {
        "ontology_id": "enterprise_commercial_ontology_v1",
        "version": "1.0.0",
        "canonical_business_concepts": [
            {
                "concept_id": "CBC-CUSTOMER",
                "concept_uri": "urn:ontology:entity:customer",
                "label": "Customer Legal Entity",
                "description": (
                    "Corporate customer identified globally by ACRA UEN "
                    "or locally by ticket sequence."
                ),
                "taxonomy_class": "CORE_BUSINESS_ENTITY",
                "authoritative_sor": ["SFDC_CRM", "SAP_ERP"],
                "key_authority_tiers": {
                    "tier_1_global_standard": {
                        "authoritative_sor": ["SFDC_CRM", "SAP_ERP"],
                        "column_patterns": ["company_uen", "tax_reg_no"],
                        "bkcc_policy": "GLOBAL",
                    },
                    "tier_3_local_scoped": {
                        "authoritative_sor": ["LEGACY_TICKET_DB"],
                        "column_patterns": ["legacy_client_no"],
                        "bkcc_policy": "LEGACY_TICKET_DB",
                    },
                },
            },
            {
                "concept_id": "CBC-INVOICE",
                "concept_uri": "urn:ontology:entity:invoice",
                "label": "Commercial Invoice",
                "description": "Commercial billing invoice issued to a customer legal entity.",
                "taxonomy_class": "CORE_BUSINESS_ENTITY",
                "authoritative_sor": ["SAP_ERP"],
                "key_authority_tiers": {
                    "tier_1_global_standard": {
                        "authoritative_sor": ["SAP_ERP"],
                        "column_patterns": ["invoice_global_id"],
                        "bkcc_policy": "GLOBAL",
                    }
                },
            },
        ],
        "canonical_relationships": [
            {
                "relationship_id": "REL-CUSTOMER-INVOICE",
                "relationship_uri": "urn:ontology:relationship:customer_invoice",
                "label": "Customer Billed on Invoice",
                "endpoints": ["hub_customer", "hub_invoice"],
            }
        ],
    }


def _write_issue40_csv_datasets(base_dir: Path) -> dict[str, Path]:
    """Write real S1 (Wave 0 Cold Start) and S2 (Wave 1 Multi-Source) CSV feeds."""
    w0_dir = base_dir / "wave0_cold_start"
    w1_dir = base_dir / "wave1_multi_source"
    w0_dir.mkdir(parents=True, exist_ok=True)
    w1_dir.mkdir(parents=True, exist_ok=True)

    crm_csv = w0_dir / "crm_accounts.csv"
    crm_csv.write_text(
        "company_uen,legal_name,credit_Tier\n"
        "202601234K,Acme Global Holdings Pte Ltd,PLATINUM\n"
        "202609876M,Meridian Maritime Corp,GOLD\n",
        encoding="utf-8",
    )

    erp_csv = w0_dir / "erp_invoices.csv"
    erp_csv.write_text(
        "invoice_global_id,tax_reg_no,invoice_amount_sgd\n"
        "INV-SG-2026-0001,202601234K,125000.00\n"
        "INV-SG-2026-0002,202609876M,84500.00\n",
        encoding="utf-8",
    )

    legacy_csv = w1_dir / "legacy_tickets.csv"
    legacy_csv.write_text(
        "legacy_client_no,support_sla_tier\n"
        "1001,24X7_MISSION_CRITICAL\n"
        "1002,STANDARD_BUSINESS_HOURS\n",
        encoding="utf-8",
    )

    return {
        "bronze_crm.accounts": crm_csv,
        "bronze_erp.invoices": erp_csv,
        "bronze_legacy.tickets": legacy_csv,
    }


def test_issue40_e2e_scenario_s1_and_s2_tier1_global_bkcc_invariants(tmp_path: Path) -> None:
    """End-to-End verification of Issue #40 across Scenario S1 (Cold Start) and
    Scenario S2 (Multi-Source Convergence).
    """
    ontology = _build_issue40_ontology()
    csv_paths = _write_issue40_csv_datasets(tmp_path)

    # Profile real CSV files with DuckDB GraphBProfiler
    with GraphBProfiler() as profiler:
        crm_metrics = profiler.profile_csv(csv_paths["bronze_crm.accounts"])
        erp_metrics = profiler.profile_csv(csv_paths["bronze_erp.invoices"])
        leg_metrics = profiler.profile_csv(csv_paths["bronze_legacy.tickets"])
    assert len(crm_metrics) == 3
    assert len(erp_metrics) == 3
    assert len(leg_metrics) == 2

    # -------------------------------------------------------------------------
    # Phase 1: Gate 1 & Gate 3 must reject malformed TIER_1_GLOBAL_STANDARD
    #          sources with DV-COL-013, and Algorithm A.4 must repair them.
    # -------------------------------------------------------------------------
    wave0_corrupted_contract: dict[str, Any] = {
        "contract_version": "5.0",
        "domain_namespace": "global_commercial_banking",
        "lifecycle_stage": "COLD_START_N1",
        "column_triage_ledger": [
            {
                "bronze_table": "bronze_crm.accounts",
                "column_name": "company_uen",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_customer",
            },
            {
                "bronze_table": "bronze_crm.accounts",
                "column_name": "legal_name",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_customer_crm",
            },
            {
                "bronze_table": "bronze_crm.accounts",
                "column_name": "credit_Tier",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_customer_crm",
            },
            {
                "bronze_table": "bronze_erp.invoices",
                "column_name": "invoice_global_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_invoice",
            },
            {
                "bronze_table": "bronze_erp.invoices",
                "column_name": "tax_reg_no",
                "elm_classification": "FOREIGN_KEY_REF",
                "target_dv_entity": "lnk_customer_invoice",
            },
            {
                "bronze_table": "bronze_erp.invoices",
                "column_name": "invoice_amount_sgd",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_invoice_erp",
            },
        ],
        "hubs": [
            {
                "name": "hub_customer",
                "ontology_concept_uri": "urn:ontology:entity:customer",
                "business_key_sources": [
                    {
                        "bronze_table": "bronze_crm.accounts",
                        "key_columns": ["company_uen"],
                        "record_source_code": "SFDC_CRM",
                        "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                        # Intentional violation 1: local silo prefix + qualifier on Tier-1 key
                        "require_src_qualifier": True,
                        "bkcc_prefix": "SFDC_LOCAL_SILO",
                    }
                ],
            },
            {
                "name": "hub_invoice",
                "ontology_concept_uri": "urn:ontology:entity:invoice",
                "business_key_sources": [
                    {
                        "bronze_table": "bronze_erp.invoices",
                        "key_columns": ["invoice_global_id"],
                        "record_source_code": "SAP_ERP",
                        "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                        # Intentional violation 2: null bkcc_prefix on Tier-1 key
                        "require_src_qualifier": False,
                        "bkcc_prefix": None,
                    }
                ],
            },
        ],
        "links": [
            {
                "name": "lnk_customer_invoice",
                "link_type": "STANDARD_LINK",
                "ontology_concept_uri": "urn:ontology:relationship:customer_invoice",
                "source_bronze_table": "bronze_erp.invoices",
                "hub_references": [
                    {
                        "target_hub": "hub_customer",
                        "role_alias": "customer",
                        "source_fk_columns": ["tax_reg_no"],
                    },
                    {
                        "target_hub": "hub_invoice",
                        "role_alias": "invoice",
                        "source_fk_columns": ["invoice_global_id"],
                    },
                ],
            }
        ],
        "satellites": [
            {
                "name": "sat_customer_crm",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_customer",
                "source_bronze_table": "bronze_crm.accounts",
                "security_classification": "INTERNAL",
                "volatility_tier": "STANDARD",
                "payload_columns": ["credit_Tier", "legal_name"],
            },
            {
                "name": "sat_invoice_erp",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_invoice",
                "source_bronze_table": "bronze_erp.invoices",
                "security_classification": "INTERNAL",
                "volatility_tier": "STANDARD",
                "payload_columns": ["invoice_amount_sgd"],
            },
        ],
        "reference_tables": [],
        "unmapped_escalations": [],
    }

    gate1_diags = verify_gate1_contract_and_grain(
        wave0_corrupted_contract,
        ontology_graph=ontology,
        bronze_profile={},
        check_full_ledger_coverage=False,
    )
    gate3_col_diags = verify_column_invariants(wave0_corrupted_contract)
    assert any("DV-COL-013" in d and "bronze_crm.accounts" in d for d in gate1_diags)
    assert any("DV-COL-013" in d and "bronze_erp.invoices" in d for d in gate1_diags)
    assert any("DV-COL-013" in d and "bronze_crm.accounts" in d for d in gate3_col_diags)

    # Repair Wave 0 (Scenario S1 Cold Start) via Algorithm A.4 / A.8 pipeline
    s1_res = run_axi_dv_verification_pipeline(
        candidate_contract=wave0_corrupted_contract,
        ontology_graph=ontology,
        bronze_profile={},
        strict_schema=True,
    )
    assert s1_res.status == "PASS", f"S1 pipeline failed: {s1_res.diagnostics}"
    wave0_contract = s1_res.transformed_contract
    for hub in wave0_contract["hubs"]:
        for src in hub["business_key_sources"]:
            assert src["key_authority_tier"] == "TIER_1_GLOBAL_STANDARD"
            assert src["require_src_qualifier"] is False
            assert src["bkcc_prefix"] == GLOBAL_BKCC

    # -------------------------------------------------------------------------
    # Phase 2: Scenario S1 (Cold Start) Lowering, 4-Zone SQL & 4-Batch Sandbox
    # -------------------------------------------------------------------------
    s1_contract_model = validate_contract_v5(wave0_contract)
    s1_ir = lower_contract_to_canonical_ir(s1_contract_model)
    validate_canonical_ir(s1_ir.model_dump(mode="json", exclude_none=True))

    s1_hub_cust = next(h for h in s1_ir.hubs if h.name == "hub_customer")
    assert s1_hub_cust.verified_global_master_key is True
    assert [bk.name for bk in s1_hub_cust.business_keys] == ["bkcc", "company_uen"]
    assert s1_hub_cust.business_keys[0].is_bkcc is True
    assert s1_hub_cust.hk_column.hash_expression.input_columns == ["bkcc", "company_uen"]
    assert s1_hub_cust.source_mappings[0].bkcc_prefix == "GLOBAL"

    s1_bronze = {
        "bronze_crm.accounts": csv_paths["bronze_crm.accounts"],
        "bronze_erp.invoices": csv_paths["bronze_erp.invoices"],
    }
    s1_bundle = compile_canonical_ir_to_4zone_sql(s1_ir, bronze_tables=s1_bronze)

    expected_acme_hk = compute_dv2_length_prefixed_sha256(
        ["202601234K"], key_authority_tier="TIER_1_GLOBAL_STANDARD"
    ).lower()
    assert (
        compute_dv2_prehash_string(["202601234K"], key_authority_tier="TIER_1_GLOBAL_STANDARD")
        == "6:GLOBAL||10:202601234K"
    )
    assert dv2_canonical_hash(["202601234K"]).lower() == expected_acme_hk
    assert resolve_fk_hash_key_with_zero_keys(["202601234K"]).lower() == expected_acme_hk

    with duckdb.connect(":memory:") as con_s1:
        con_s1.execute(s1_bundle.full_sql)
        s1_row = con_s1.execute(
            "SELECT hk_customer, bkcc, company_uen FROM raw_vault.hub_customer "
            "WHERE company_uen = '202601234K'"
        ).fetchone()
        assert s1_row == (expected_acme_hk, "GLOBAL", "202601234K")

    with DuckDbShadowSandbox() as sbx_s1:
        rep_s1 = sbx_s1.run_full_verification(source=s1_ir, bronze_tables=s1_bronze)
        assert rep_s1.passed is True
        assert rep_s1.batch_report.all_passed is True
        assert rep_s1.dynamic_check_report.all_passed is True

    # -------------------------------------------------------------------------
    # Phase 3: Scenario S2 (Multi-Source Convergence: Add ERP Tier-1 + Legacy Tier-3)
    # -------------------------------------------------------------------------
    wave1_contract = copy.deepcopy(wave0_contract)
    wave1_contract["lifecycle_stage"] = "MULTI_SOURCE_EXPANSION"
    wave1_contract["column_triage_ledger"].extend(
        [
            {
                "bronze_table": "bronze_legacy.tickets",
                "column_name": "legacy_client_no",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_customer",
            },
            {
                "bronze_table": "bronze_legacy.tickets",
                "column_name": "support_sla_tier",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_customer_legacy",
            },
        ]
    )
    wave1_contract["hubs"][0]["business_key_sources"].extend(
        [
            {
                "bronze_table": "bronze_erp.invoices",
                "key_columns": ["tax_reg_no"],
                "record_source_code": "SAP_ERP",
                "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                "require_src_qualifier": False,
                "bkcc_prefix": "GLOBAL",
            },
            {
                "bronze_table": "bronze_legacy.tickets",
                "key_columns": ["legacy_client_no"],
                "record_source_code": "LEGACY_TICKET_DB",
                "key_authority_tier": "TIER_3_LOCAL_SCOPED",
                "require_src_qualifier": True,
                "bkcc_prefix": "LEGACY_TICKET_DB",
                "system_scoped_bk_justification": (
                    "Legacy ticket sequence numbers collide across systems; "
                    "isolated via LEGACY_TICKET_DB BKCC."
                ),
            },
        ]
    )
    wave1_contract["satellites"].append(
        {
            "name": "sat_customer_legacy",
            "satellite_type": "STANDARD_SATELLITE",
            "parent_entity": "hub_customer",
            "source_bronze_table": "bronze_legacy.tickets",
            "security_classification": "INTERNAL",
            "volatility_tier": "STANDARD",
            "payload_columns": ["support_sla_tier"],
        }
    )

    assert verify_additive_evolution_invariants(wave0_contract, wave1_contract) == []
    s2_res = run_axi_dv_verification_pipeline(
        candidate_contract=wave1_contract,
        ontology_graph=ontology,
        bronze_profile={},
        deployed_contract=wave0_contract,
        strict_schema=True,
    )
    assert s2_res.status == "PASS", f"S2 pipeline failed: {s2_res.diagnostics}"

    s2_ir = lower_contract_to_canonical_ir(validate_contract_v5(s2_res.transformed_contract))
    s2_hub_cust = next(h for h in s2_ir.hubs if h.name == "hub_customer")
    assert s2_hub_cust.verified_global_master_key is False
    # Hub business_keys and hash_expression.input_columns remain 100% invariant between S1 and S2!
    assert [bk.name for bk in s2_hub_cust.business_keys] == [
        bk.name for bk in s1_hub_cust.business_keys
    ]
    assert (
        s2_hub_cust.hk_column.hash_expression.input_columns
        == s1_hub_cust.hk_column.hash_expression.input_columns
    )
    assert [sm.bkcc_prefix for sm in s2_hub_cust.source_mappings] == [
        "GLOBAL",
        "GLOBAL",
        "LEGACY_TICKET_DB",
    ]

    s2_bundle = compile_canonical_ir_to_4zone_sql(s2_ir, bronze_tables=csv_paths)
    expected_legacy_hk = compute_dv2_length_prefixed_sha256(
        ["1001"],
        bkcc_prefix="LEGACY_TICKET_DB",
        key_authority_tier="TIER_3_LOCAL_SCOPED",
    ).lower()

    with duckdb.connect(":memory:") as con_s2:
        con_s2.execute(s2_bundle.full_sql)
        s2_acme_row = con_s2.execute(
            "SELECT hk_customer, bkcc, company_uen FROM raw_vault.hub_customer "
            "WHERE company_uen = '202601234K'"
        ).fetchone()
        s2_legacy_row = con_s2.execute(
            "SELECT hk_customer, bkcc, company_uen FROM raw_vault.hub_customer "
            "WHERE company_uen = '1001'"
        ).fetchone()
        # Proof 1: S1 -> S2 hash key stability for Tier-1 '202601234K' (zero re-hashing)
        assert s2_acme_row == s1_row == (expected_acme_hk, "GLOBAL", "202601234K")
        # Proof 2: Tier-3 '1001' isolated under 'LEGACY_TICKET_DB' in the same Hub
        assert s2_legacy_row == (expected_legacy_hk, "LEGACY_TICKET_DB", "1001")

    with DuckDbShadowSandbox() as sbx_s2:
        rep_s2 = sbx_s2.run_full_verification(source=s2_ir, bronze_tables=csv_paths)
        assert rep_s2.passed is True
        assert rep_s2.batch_report.all_passed is True
        assert rep_s2.dynamic_check_report.all_passed is True
