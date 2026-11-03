# 17. Testing Scenarios & Datasets Catalog

## Issue #31 — Scenario S5 (Nullable Foreign Key / 0..1 Relationship)

### 1. Scenario Overview & Governing Requirements
- **GitHub Issue**: [`#31`](https://github.com/nogoo9/open-dv/issues/31) (`BUG-02`, **HIGH**)
- **Governing Functional Requirement**: `FR-DOM-01` (§5.1.5 — Nullable-FK Link Splitting & Sparse Binary Link Filtering)
- **DV 2.0 Rules & Invariants**: `Algorithm A.4` (`codemod_enforce_cold_start_extensibility`), `DV-G2-001` / `DV-G2-001b` ($U_{\text{nn}} < 0.95$ Sparse FK Guard), `KEY-05` (Zero-Key Sentinel Discipline)
- **Architectural Property Verified**:
  When a binary Link ($\text{arity} = 2$) references an optional foreign key with empirical non-null ratio $U_{\text{nn}} < 0.95$ (or $\text{null\_rate} > 0.05$), `open-dv` must:
  1. Reject unfiltered binary Links in Gate 2 (`verify_gate1_contract_and_grain`) with `SEMANTIC_ERROR [Gate 2 - DV-G2-001]` (`DV-G2-001b`) when codemods are disabled.
  2. Automatically attach `where_filter_condition = "<fk_col> IS NOT NULL"` in-place on the binary Link and promote its `column_triage_ledger` entry from `FOREIGN_KEY_REF` to `OPTIONAL_FOREIGN_KEY_REF` in `Algorithm A.4` (`codemod_enforce_cold_start_extensibility`), preventing phantom `ZERO_KEY_UNKNOWN` (`'0'*64`) rows from clustering in the Raw Vault Link table.

### 2. Test Dataset & Fixture Layout
Located under `examples/pipeline_samples/global_commerce_vault/scenarios/s5_sparse_binary_link/`:
- **`crm_customers.csv`**: 2 enterprise customers (`TAX-US-900101`, `TAX-DE-800202`) with $U_{\text{nn}} = 1.0$, $\text{null\_rate} = 0.0$.
- **`erp_orders_optional_customer.csv`**: 4 sales orders (`ORD-2026-1001`..`1004`) where `customer_tax_id` is populated on 2 confirmed/shipped orders and `NULL` on 2 draft/quote orders ($\text{null\_rate} = 0.50$, effective non-null ratio $1.0 - \text{null\_rate} = 0.50 < 0.95$).
- **`s5_pipeline_input.yaml`**: Single-wave (`COLD_START`) pipeline input onboarding `crm_salesforce.crm_customers` (`hub_customer`) and `erp_netsuite.erp_orders` (`hub_order`) with binary relationship `lnk_order_customer`.

### 3. Running the Unit & End-to-End Tests

```bash
# 1. Run the non-mocked unit test (GraphBProfiler + Gate 2 DV-G2-001b + Algorithm A.4 + DuckDB SQL)
uv run pytest packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py \
  -k test_issue_31_alg_a4_and_gate2_sparse_binary_link -vv

# 2. Run the end-to-end 8-stage pipeline test for Scenario S5 optional binary links
uv run pytest packages/opendv-agent-harness/tests/test_agentic_pipeline.py \
  -k test_optional_only_foreign_key_link_synthesis -vv

# 3. Validate the Scenario S5 pipeline input specification via CLI
uv run opendv pipeline validate \
  --input examples/pipeline_samples/global_commerce_vault/scenarios/s5_sparse_binary_link/s5_pipeline_input.yaml \
  --harness-config examples/pipeline_samples/global_commerce_vault/harness_config_agy.yaml \
  --json
```
