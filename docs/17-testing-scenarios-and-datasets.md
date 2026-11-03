# Open-DV Testing Scenarios & Datasets (`docs/17-testing-scenarios-and-datasets.md`)

This document catalogs the normative test scenarios, multi-wave datasets, and exact verification commands for `open-dv` (`S1`–`S10`, `UF-1`–`UF-5`).

---

## Issue #38 — Scenario S4 (Upstream Schema Drift), Unmatched Case UF-1

### 1. Scenario Overview & Architectural Traceability
- **GitHub Issue**: [#38 — `feat(compiler_4zone): generate zone_0_migration_sql with idempotent ALTER TABLE ADD COLUMN statements`](https://github.com/nogoo9/open-dv/issues/38)
- **Audit Finding**: `BUG-07` (`MEDIUM`)
- **Governing Functional Requirements**: `FR-DOM-03` (Intra-Source Schema Drift $S_k(t) \to S_k(t+1)$), `FR-INT-01` (4-Zone Physical SQL Compilation)
- **Mapped Scenario & Case**: Scenario `S4` (Upstream Schema Drift), Unmatched Case `UF-1` (Source field not in Graph A ontology, added in-place to an existing Satellite)
- **DV 2.0 Invariants & Rules**: `INV-01` (Insert-Only Raw Vault History), `EVO-01` / `EVO-01a` (Additive Satellite Evolution), `DV-EVO-006` (`SAT_HASHDIFF_INVALIDATED`), `DV-DYN-014` (Additive Schema Evolution Non-Destructiveness), `Algorithm A.6` (`DV-AST-001`), `Algorithm A.7` (Evolution Lock)

### 2. Test Dataset & Multi-Wave Evolution Setup
This scenario exercises an existing Raw Vault Satellite (`raw_vault.sat_customer_core` attached to `raw_vault.hub_customer`, sourced from `crm_salesforce.crm_customers`) evolving across two waves in the same physical DuckDB database:

1. **Wave 0 (`COLD_START_N1` at `load_dts = '2026-10-01 06:00:00'`)**:
   - **Source Feed**: `examples/pipeline_samples/global_commerce_vault/sources/wave0_cold_start/crm_customers.csv`
   - **Business Key**: `customer_tax_id` (`TIER_1_GLOBAL_STANDARD`, `GLOBAL` BKCC)
   - **Initial `sat_customer_core` Payload Columns**: `legal_name`, `country_iso2`, `account_segment`, `credit_limit_usd`
   - **Sample Seed Rows**:
     - `('TAX-US-900101', 'Apex Industrial Holdings LLC', 'US', 'ENTERPRISE', 1250000.00)`
     - `('TAX-DE-800202', 'Rheinland Precision GmbH', 'DE', 'ENTERPRISE', 980000.00)`

2. **Wave 2 (`MULTI_SOURCE_EXPANSION` / `SCHEMA_DRIFT` at `load_dts = '2026-11-01 06:00:00'`)**:
   - **Source Feed**: `examples/pipeline_samples/global_commerce_vault/sources/wave2_schema_drift/crm_customers_v2.csv`
   - **In-Place Added Columns (`UF-1`)**: `loyalty_tier_code` (`VARCHAR`), `annual_spend_band` (`VARCHAR`)
   - **Migration Policy**: `hash_diff_migration_policy: "NULL_EQUIVALENT_PADDING"`
   - **Sample Drifted Rows**:
     - `('TAX-US-900101', 'Apex Industrial Holdings LLC', 'US', 'ENTERPRISE', 1250000.00, 'PLATINUM', 'BAND_1M_5M')`
     - `('TAX-DE-800202', 'Rheinland Precision GmbH', 'DE', 'ENTERPRISE', 980000.00, 'GOLD', 'BAND_500K_1M')`

3. **Compiled `zone_0_migration_sql` Output**:
   ```sql
   -- =============================================================================
   -- ZONE 0 MIGRATION: IDEMPOTENT ADDITIVE SATELLITE SCHEMA EVOLUTION (EVO-01)
   -- =============================================================================
   ALTER TABLE raw_vault.sat_customer_core ADD COLUMN IF NOT EXISTS loyalty_tier_code VARCHAR;
   ALTER TABLE raw_vault.sat_customer_core ADD COLUMN IF NOT EXISTS annual_spend_band VARCHAR;
   ```

### 3. Running the Unit and End-to-End (E2E) Tests

#### 3.1 Run the Dedicated Unit & Integration Tests (No Mocks)
```bash
uv run pytest packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py \
  -k "test_issue_38" -v
```

#### 3.2 Run the Standalone E2E Multi-Wave DuckDB Verification
```bash
uv run python -c '
import copy
import duckdb
from opendv_axi_verifier import verify_additive_evolution_invariants, verify_physical_dv2_sql_ast
from opendv_duckdb_sandbox import (
    check_dv_dyn_014_additive_schema_evolution,
    compile_canonical_ir_to_4zone_sql,
    lower_contract_to_canonical_ir,
)

wave0_contract = {
    "contract_version": "5.0",
    "domain_namespace": "global_commerce",
    "lifecycle_stage": "COLD_START_N1",
    "column_triage_ledger": [
        {"bronze_table": "crm_salesforce.crm_customers", "column_name": "customer_tax_id", "elm_classification": "BUSINESS_KEY", "target_dv_entity": "hub_customer"},
        {"bronze_table": "crm_salesforce.crm_customers", "column_name": "legal_name", "elm_classification": "DESCRIPTIVE_PAYLOAD", "target_dv_entity": "sat_customer_core"},
        {"bronze_table": "crm_salesforce.crm_customers", "column_name": "country_iso2", "elm_classification": "DESCRIPTIVE_PAYLOAD", "target_dv_entity": "sat_customer_core"},
        {"bronze_table": "crm_salesforce.crm_customers", "column_name": "account_segment", "elm_classification": "DESCRIPTIVE_PAYLOAD", "target_dv_entity": "sat_customer_core"},
        {"bronze_table": "crm_salesforce.crm_customers", "column_name": "credit_limit_usd", "elm_classification": "DESCRIPTIVE_PAYLOAD", "target_dv_entity": "sat_customer_core"},
    ],
    "hubs": [{"name": "hub_customer", "ontology_concept_uri": "urn:ontology:entity:customer", "business_key_sources": [{"bronze_table": "crm_salesforce.crm_customers", "key_columns": ["customer_tax_id"], "record_source_code": "CRM_SALESFORCE", "key_authority_tier": "TIER_1_GLOBAL_STANDARD", "require_src_qualifier": False}]}],
    "links": [],
    "satellites": [{"name": "sat_customer_core", "satellite_type": "STANDARD_SATELLITE", "parent_entity": "hub_customer", "source_bronze_table": "crm_salesforce.crm_customers", "volatility_tier": "STANDARD", "security_classification": "INTERNAL", "payload_columns": ["legal_name", "country_iso2", "account_segment", "credit_limit_usd"]}],
    "reference_tables": [],
    "unmapped_escalations": [],
}
wave0_rows = [
    {"customer_tax_id": "TAX-US-900101", "legal_name": "Apex Industrial Holdings LLC", "country_iso2": "US", "account_segment": "ENTERPRISE", "credit_limit_usd": 1250000.00, "load_dts": "2026-10-01 06:00:00"},
    {"customer_tax_id": "TAX-DE-800202", "legal_name": "Rheinland Precision GmbH", "country_iso2": "DE", "account_segment": "ENTERPRISE", "credit_limit_usd": 980000.00, "load_dts": "2026-10-01 06:00:00"},
]
ir_w0 = lower_contract_to_canonical_ir(wave0_contract)
bundle_w0 = compile_canonical_ir_to_4zone_sql(ir_w0, bronze_tables={"crm_salesforce.crm_customers": wave0_rows})

conn = duckdb.connect(":memory:")
conn.execute(bundle_w0.zones_0_to_3_sql)

wave2_contract = copy.deepcopy(wave0_contract)
wave2_contract["lifecycle_stage"] = "MULTI_SOURCE_EXPANSION"
wave2_contract["column_triage_ledger"].extend([
    {"bronze_table": "crm_salesforce.crm_customers", "column_name": "loyalty_tier_code", "elm_classification": "DESCRIPTIVE_PAYLOAD", "target_dv_entity": "sat_customer_core"},
    {"bronze_table": "crm_salesforce.crm_customers", "column_name": "annual_spend_band", "elm_classification": "DESCRIPTIVE_PAYLOAD", "target_dv_entity": "sat_customer_core"},
])
wave2_contract["satellites"][0]["hash_diff_migration_policy"] = "NULL_EQUIVALENT_PADDING"
wave2_contract["satellites"][0]["payload_columns"].extend(["loyalty_tier_code", "annual_spend_band"])
ir_w2 = lower_contract_to_canonical_ir(wave2_contract)
wave2_rows = [
    {"customer_tax_id": "TAX-US-900101", "legal_name": "Apex Industrial Holdings LLC", "country_iso2": "US", "account_segment": "ENTERPRISE", "credit_limit_usd": 1250000.00, "loyalty_tier_code": "PLATINUM", "annual_spend_band": "BAND_1M_5M", "load_dts": "2026-11-01 06:00:00"},
    {"customer_tax_id": "TAX-DE-800202", "legal_name": "Rheinland Precision GmbH", "country_iso2": "DE", "account_segment": "ENTERPRISE", "credit_limit_usd": 980000.00, "loyalty_tier_code": "GOLD", "annual_spend_band": "BAND_500K_1M", "load_dts": "2026-11-01 06:00:00"},
]
bundle_w2 = compile_canonical_ir_to_4zone_sql(ir_w2, bronze_tables={"crm_salesforce.crm_customers": wave2_rows}, baseline_ir=ir_w0)
conn.execute(bundle_w2.zones_0_to_3_sql)
assert check_dv_dyn_014_additive_schema_evolution(conn, historical_hash_mutations=0, ir=ir_w2).passed is True
print("Wave 0 + Wave 2 rows:", conn.execute("SELECT COUNT(*) FROM raw_vault.sat_customer_core WHERE rec_src <> '\''SYSTEM_GHOST'\''").fetchone()[0])
'
```
