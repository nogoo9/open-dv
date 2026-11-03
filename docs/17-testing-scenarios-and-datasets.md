# Testing Scenarios & Datasets (`docs/17-testing-scenarios-and-datasets.md`)

## Issue #42 — Scenario S9 (Multi-Tenant / Multi-Unit Collisions)

### 1. Scenario Overview & Governing Requirements
- **GitHub Issue**: [#42 — `feat(compiler_4zone): support dynamic bkcc_column_expr for multi-tenant table hashing`](https://github.com/nogoo9/open-dv/issues/42)
- **Audit ID**: `GAP-11` (`MEDIUM`)
- **Functional Requirements**: `FR-DOM-02` (Multi-Source & Multi-Tenant Integration), `FR-INT-01` (Canonical IR & 4-Zone SQL Compilation)
- **DV 2.0 Rules & Invariants**: `KEY-04` (3-Tier Business Key Authority & BKCC Namespace Isolation), `Algorithm A.5` (6-Stage Length-Prefixed Canonical SHA-256 with Dynamic Tenant Namespace), `KEY-02(b)` (Zero-Key Unknown Sentinel `0x00..00`), `DV-COL-012` (Gate 1 Tier-3 BKCC Enforcement), `DV-EVO-003` / `DV-EVO-007` (Gate 4 BKCC Evolution Lock)

In shared single-table multi-tenant architectures, multiple tenants or business units reside in the same physical Bronze table (`bronze_saas.tenant_accounts`, `bronze_saas.tenant_subscriptions`) partitioned by a row-level discriminator column (`tenant_id` or `org_code`). Local natural keys (`account_id = 'ACCT-1001'`) collide across tenants (`TENANT_US` vs. `TENANT_EU`). Declaring `bkcc_column_expr: "tenant_id"` on the Hub's `BusinessKeySource` lowers into `CanonicalIR` (`IRHub`, `IRHashExpression`, `IRHubSourceMapping`) and compiles Zone 1 staging views that prefix `_hk` with the dynamic length-prefixed column expression (`9:TENANT_US||9:ACCT-1001` vs. `9:TENANT_EU||9:ACCT-1001`) while preserving `KEY-02(b)` Zero-Key resolution (`REPEAT('0', 64)`) when `account_id IS NULL`.

### 2. Test Dataset
- **`tenant_accounts.csv` (`bronze_saas.tenant_accounts`)**:
  ```csv
  tenant_id,account_id,account_name,plan_tier
  TENANT_US,ACCT-1001,Acme US Holdings,ENTERPRISE
  TENANT_EU,ACCT-1001,Acme EU GmbH,GROWTH
  TENANT_US,ACCT-1002,Beta US Corp,STARTER
  ```
- **`tenant_subscriptions.csv` (`bronze_saas.tenant_subscriptions`)**:
  ```csv
  tenant_id,account_id,subscription_id
  TENANT_US,ACCT-1001,SUB-9001
  TENANT_EU,ACCT-1001,SUB-9002
  ```
- **Expected Hub Keys (`raw_vault.hub_tenant_account`)**:
  - `('TENANT_EU', 'ACCT-1001')` $\to$ `2530e2e2959ce91a2582cdcc0369df5367ba21659d14078ea37902e23af18c4b` (`SHA256('9:TENANT_EU||9:ACCT-1001')`)
  - `('TENANT_US', 'ACCT-1001')` $\to$ `05732713befe836172388e84bd17a65b7fc1cd080a775cb9f9b2aafb48f407a5` (`SHA256('9:TENANT_US||9:ACCT-1001')`)
  - `('TENANT_US', 'ACCT-1002')` $\to$ `aa878e0129a5d13e21718834b48473337925ac1db45214194155e0c275b14e8d` (`SHA256('9:TENANT_US||9:ACCT-1002')`)
  - `('TENANT_US', NULL)` $\to$ `0000000000000000000000000000000000000000000000000000000000000000` (`ZERO_KEY_UNKNOWN`)

### 3. Test Scenarios Covered
- **`test_contract_v5_and_canonical_ir_accept_bkcc_column_expr`** (`packages/opendv-core/tests/test_core_models_and_hashing.py`): Validates `bkcc_column_expr` and `bkcc_literal` across Pydantic v2 models (`BusinessKeySource`, `IRHashExpression`, `IRHubSourceMapping`, `IRHub`) and Draft 2020-12 JSON Schemas (`schemas/no-mistake-dv2-contract-v5.schema.json`, `schemas/dv2-canonical-ir.schema.json`).
- **`test_build_algorithm_a5_sha256_sql_dynamic_bkcc_column_expr_and_zero_key`** (`packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py`): Verifies `build_algorithm_a5_sha256_sql` and `build_canonical_sha256_hash_ast` in `:memory:` DuckDB for dynamic `bkcc_column_expr` prefixing, input column deduplication, single-column fallback, `UPPER(TRIM(...))` normalization, and `KEY-02(b)` Zero-Key resolution (`REPEAT('0', 64)`) when business key columns are `NULL` or whitespace.
- **`test_scenario_s9_multitenant_bkcc_column_expr_e2e_4zone_and_sandbox`** (`packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py`): End-to-end verification of `tenant_accounts.csv` and `tenant_subscriptions.csv` through `validate_contract_v5`, Gate 1 `verify_gate1_contract_and_grain` (including whitespace-only `bkcc_column_expr` rejection under `DV-COL-012`), Gate 4 `verify_evolution_lock_alg_a7` (`BKCC_MUTATED`), SQLGlot AST verification of `codemod_enforce_staging_hashes_sql`, `lower_contract_to_canonical_ir`, `compile_canonical_ir_to_4zone_sql`, and `DuckDbShadowSandbox.run_full_verification` (4 Adversarial Batches + `DV-DYN-001..015`).

### 4. Running the Unit and E2E Tests
```bash
# Run the Issue #42 / Scenario S9 unit & E2E tests in opendv-duckdb-sandbox and opendv-core
uv run --all-packages pytest \
  packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py \
  packages/opendv-core/tests/test_core_models_and_hashing.py \
  -k "bkcc_column_expr or scenario_s9" -v

# Run the full Python & TypeScript conformance suites
uv run --all-packages pytest -v
bun test
```
