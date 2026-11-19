"""Phase 2 TDD Tests: Sparse Nullable FK Companion Links (`FR-DVM-08`, Algorithm A.4).

Covers:
1. Binary sparse FK (`U_nn < 0.95` via `GraphBProfiler` on `s5_sparse_binary_link`) receives
   `where_filter_condition = '<fk> IS NOT NULL'`, `OPTIONAL_FOREIGN_KEY_REF` triage role,
   and filters NULL-FK rows out of `raw_vault.lnk_*` in DuckDB execution.
2. N-ary sparse FK (`U_nn < 0.95`) splits into a dedicated companion `lnk_*` with
   `WHERE <fk> IS NOT NULL` while keeping the core Link unfiltered.
3. Unsplit sparse FK fails Gate 2 (`DV-G2-001`).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import duckdb
import yaml

from opendv_axi_verifier import (
    codemod_enforce_cold_start_extensibility,
    verify_gate1_contract_and_grain,
)
from opendv_core.config import get_core_modeling_config
from opendv_core.schema_registry import validate_contract_v5
from opendv_duckdb_sandbox.compiler_4zone import compile_canonical_ir_to_4zone_sql
from opendv_duckdb_sandbox.graph_b_profiler import GraphBProfiler
from opendv_duckdb_sandbox.ir_lowerer import lower_contract_to_canonical_ir
from opendv_sources import SourceSpec, get_source_registry

REPO_ROOT = Path(__file__).resolve().parents[1]
S5_DIR = (
    REPO_ROOT
    / "examples"
    / "pipeline_samples"
    / "global_commerce_vault"
    / "scenarios"
    / "s5_sparse_binary_link"
)
PE_DIR = REPO_ROOT / "examples" / "private_equity"


def test_sparse_binary_fk_filtered_link_and_duckdb_execution() -> None:
    """Sparse binary FK (`U_nn < 0.95`) gets `WHERE ... IS NOT NULL` and skips NULL rows in DuckDB."""
    orders_csv = S5_DIR / "erp_orders_optional_customer.csv"
    cust_csv = S5_DIR / "crm_customers.csv"
    reg = get_source_registry()
    with GraphBProfiler() as profiler:
        reg.get_tabular_adapter("file").register(
            SourceSpec(adapter="file", uri=str(orders_csv)), profiler.conn, "v_orders"
        )
        metrics = profiler.profile_relation("v_orders")
    cust_fk_metric = next(m for m in metrics if m.column_name == "customer_tax_id")
    assert cust_fk_metric.non_null_ratio < get_core_modeling_config().sparse_fk_null_threshold

    bronze_profile = {
        "source_systems": [
            {
                "record_source_code": "ERP_NETSUITE",
                "tables": [
                    {
                        "bronze_table": "erp_netsuite.erp_orders",
                        "columns": [
                            {
                                "column_name": "customer_tax_id",
                                "non_null_ratio": cust_fk_metric.non_null_ratio,
                            }
                        ],
                    }
                ],
            }
        ]
    }
    raw_contract: dict[str, Any] = {
        "contract_version": "5.0",
        "domain_namespace": "global_commerce",
        "lifecycle_stage": "COLD_START_N1",
        "column_triage_ledger": [
            {
                "bronze_table": "crm_salesforce.crm_customers",
                "column_name": "customer_tax_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_customer",
            },
            {
                "bronze_table": "erp_netsuite.erp_orders",
                "column_name": "order_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_order",
            },
            {
                "bronze_table": "erp_netsuite.erp_orders",
                "column_name": "customer_tax_id",
                "elm_classification": "FOREIGN_KEY_REF",
                "target_dv_entity": "lnk_order_customer",
            },
        ],
        "hubs": [
            {
                "name": "hub_customer",
                "ontology_concept_uri": "urn:ontology:entity:customer",
                "business_key_sources": [
                    {
                        "bronze_table": "crm_salesforce.crm_customers",
                        "key_columns": ["customer_tax_id"],
                        "record_source_code": "CRM_SALESFORCE",
                        "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                        "require_src_qualifier": False,
                        "bkcc_prefix": "GLOBAL",
                    }
                ],
            },
            {
                "name": "hub_order",
                "ontology_concept_uri": "urn:ontology:entity:order",
                "business_key_sources": [
                    {
                        "bronze_table": "erp_netsuite.erp_orders",
                        "key_columns": ["order_id"],
                        "record_source_code": "ERP_NETSUITE",
                        "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                        "require_src_qualifier": False,
                        "bkcc_prefix": "GLOBAL",
                    }
                ],
            },
        ],
        "links": [
            {
                "name": "lnk_order_customer",
                "link_type": "STANDARD_LINK",
                "ontology_concept_uri": "urn:ontology:relationship:order_customer",
                "source_bronze_table": "erp_netsuite.erp_orders",
                "hub_references": [
                    {
                        "target_hub": "hub_order",
                        "role_alias": "order",
                        "source_fk_columns": ["order_id"],
                    },
                    {
                        "target_hub": "hub_customer",
                        "role_alias": "customer",
                        "source_fk_columns": ["customer_tax_id"],
                    },
                ],
            }
        ],
        "satellites": [],
        "reference_tables": [],
        "unmapped_escalations": [],
    }

    # Pre-codemod must fail DV-G2-001
    pre_diags = verify_gate1_contract_and_grain(raw_contract, bronze_profile=bronze_profile)
    assert any("DV-G2-001" in d for d in pre_diags)

    repaired = codemod_enforce_cold_start_extensibility(raw_contract, {}, bronze_profile)
    lnk = next(l for l in repaired["links"] if l["name"] == "lnk_order_customer")
    assert lnk.get("where_filter_condition") == "customer_tax_id IS NOT NULL"
    fk_entry = next(
        e for e in repaired["column_triage_ledger"] if e["column_name"] == "customer_tax_id" and e["bronze_table"] == "erp_netsuite.erp_orders"
    )
    assert fk_entry["elm_classification"] == "OPTIONAL_FOREIGN_KEY_REF"

    ir_model = lower_contract_to_canonical_ir(validate_contract_v5(repaired))
    bundle = compile_canonical_ir_to_4zone_sql(
        ir_model,
        bronze_tables={
            "crm_salesforce.crm_customers": cust_csv,
            "erp_netsuite.erp_orders": orders_csv,
        },
    )
    assert "customer_tax_id IS NOT NULL" in bundle.zone_1_sql or "customer_tax_id IS NOT NULL" in bundle.zone_2_sql

    with duckdb.connect(":memory:") as con:
        con.execute(bundle.full_sql)
        non_null_source = con.execute(
            f"SELECT COUNT(*) FROM read_csv_auto('{orders_csv.as_posix()}') WHERE customer_tax_id IS NOT NULL"
        ).fetchone()[0]
        lnk_rows = con.execute(
            "SELECT COUNT(*) FROM raw_vault.lnk_order_customer "
            "WHERE hk_lnk_order_customer NOT IN (REPEAT('0', 64), REPEAT('f', 64))"
        ).fetchone()[0]
        assert lnk_rows == non_null_source


def test_sparse_nary_fk_splits_into_companion_filtered_link() -> None:
    """Sparse FK on 3-way Link (`U_nn < 0.95`) splits into dedicated companion `lnk_*`."""
    ont = dict(yaml.safe_load((PE_DIR / "pe_graph_a_ontology.yaml").read_text(encoding="utf-8")))
    gb = dict(json.loads((PE_DIR / "pe_graph_b_profile.json").read_text(encoding="utf-8")))
    pe_contract = dict(yaml.safe_load((PE_DIR / "dv_pe_model_contract.yaml").read_text(encoding="utf-8")))

    # Reconstruct pre-split 3-way lnk_lp_commitment containing sparse co_invest_portco_lei
    unsplit = copy.deepcopy(pe_contract)
    co_invest_lnk = next(l for l in unsplit["links"] if l["name"] == "lnk_lp_commitment_co_invest")
    co_invest_ref = next(r for r in co_invest_lnk["hub_references"] if r["target_hub"] == "hub_portfolio_company")
    unsplit["links"] = [l for l in unsplit["links"] if l["name"] != "lnk_lp_commitment_co_invest"]
    main_lnk = next(l for l in unsplit["links"] if l["name"] == "lnk_lp_commitment")
    main_lnk["hub_references"].append(co_invest_ref)

    pre_diags = verify_gate1_contract_and_grain(unsplit, ontology_graph=ont, bronze_profile=gb)
    assert any("DV-G2-001" in d and "co_invest_portco_lei" in d for d in pre_diags)

    split_contract = codemod_enforce_cold_start_extensibility(unsplit, ont, gb)
    links_by_name = {l["name"]: l for l in split_contract["links"]}
    assert "lnk_lp_commitment" in links_by_name
    assert len(links_by_name["lnk_lp_commitment"]["hub_references"]) == 2
    assert not links_by_name["lnk_lp_commitment"].get("where_filter_condition")

    companion_links = [
        l for name, l in links_by_name.items() if name != "lnk_lp_commitment" and l.get("where_filter_condition")
    ]
    assert len(companion_links) == 1
    assert "co_invest_portco_lei IS NOT NULL" in companion_links[0]["where_filter_condition"]
    assert verify_gate1_contract_and_grain(split_contract, ontology_graph=ont, bronze_profile=gb) == []
