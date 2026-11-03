# `open-dv` Testing Scenarios & Datasets Reference (`docs/17-testing-scenarios-and-datasets.md`)

> **Normative Testing & Scenario Verification Guide**
> Documents end-to-end testing scenarios (`S1`–`S10`), datasets, and verification commands across the `open-dv` monorepo.

---

## Issue #43 — Scenario S10 (Ghost Record & Zero Key Handling)

### 1. Overview & Governing Requirements
- **GitHub Issue**: [`#43`](https://github.com/nogoo9/open-dv/issues/43) (`GAP-12`, Severity: `MEDIUM`)
- **Functional Requirements**: `FR-DOM-01` (Automated Relational Source Discovery, Column Triage & Raw Vault Synthesis), `FR-VER-02` (Dynamic DuckDB Adversarial Shadow Sandbox & PIT Audit)
- **DV 2.0 Rules & Invariants**: `KEY-02b` (Pre-Hash Null & Sentinel Substitution), `DV-DYN-002` (Parent-Child Referential Integrity), `DV-DYN-007` (Ghost & Dual Zero-Key `INNER JOIN` Completeness)

### 2. Scenario & Test Dataset Specification
Scenario `S10` verifies that the 4-Zone DuckDB SQL compiler (`compiler_4zone.py`) implements a deterministic **3-way conditional hash branch** in `build_algorithm_a5_sha256_sql` and all Zone 1 staging views (`staging.stg_*`):
1. **Error / Inapplicable Zero Key (`ZERO_KEY_ERROR = 'f' * 64`)**: Triggered when a business key or foreign key token equals `'-2'` (`ZERO_KEY_ERROR_TOKEN`) or matches an explicit `error_predicate`.
2. **Unknown / Missing Zero Key (`ZERO_KEY_UNKNOWN = '0' * 64`)**: Triggered when a business key or foreign key token is `NULL`, empty/whitespace (`''`), equals `'-1'` (`ZERO_KEY_UNKNOWN_TOKEN`), or matches an explicit `unknown_predicate` (excluding constant `bkcc` namespace columns).
3. **Valid Business Key Digest**: Computed via Algorithm A.5 length-prefixed SHA-256 (`<octet_len>:<val>` with `||` delimiter and `BKCC` prefix).

#### Test Datasets Exercised
- **Private Equity Reference Vault (`dealcloud.src_portfolio_companies`)**:
  - `deal_id = 'PC-DEAL-01'`, `lei_code = '529900T8BM49AURSDO55'` $\to$ valid SHA-256 `hk_portco`
  - `deal_id = 'PC-NULL-FK'`, `lei_code = NULL` $\to$ `hk_portco = REPEAT('0', 64)` (`ZERO_KEY_UNKNOWN`)
  - `deal_id = 'PC-ERR-FK'`, `lei_code = '-2'` $\to$ `hk_portco = REPEAT('f', 64)` (`ZERO_KEY_ERROR`)
- **Generic Canonical IR Vault (`global_commerce_vault` / `bronze.src_hub_customer` & `bronze.src_lnk_customer_order`)**:
  - Hub & Satellite rows with `('GLOBAL', 'CUST-S10-101')`, `('GLOBAL', NULL)`, `('GLOBAL', '-1')`, and `('GLOBAL', '-2')`
  - Link rows with valid FK, `NULL` FK, `'-1'` FK, and `'-2'` FK

### 3. How to Run the Unit & End-to-End Tests

```bash
# 1. Run the unit tests for 3-way Zero-Key SQL branching in build_algorithm_a5_sha256_sql
uv run pytest packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py \
  -k "test_algorithm_a5_sql_helper" -v

# 2. Run the Scenario S10 end-to-end DuckDB sandbox test (non-mocked)
uv run pytest packages/opendv-duckdb-sandbox/tests/test_duckdb_sandbox_suite.py \
  -k "test_scenario_s10_dual_zero_key_3way_branching_e2e" -v

# 3. Run the CLI end-to-end 4-Batch Adversarial DuckDB Shadow Sandbox verification
uv run opendv sandbox --local \
  --contract examples/private_equity/dv_pe_model_contract.yaml \
  --canonical-ir examples/private_equity/pe_canonical_ir.json \
  --query-type ADVERSARIAL_BATCHES_1_TO_4 \
  --cutoff "2026-11-15 23:59:59" \
  --json
```
