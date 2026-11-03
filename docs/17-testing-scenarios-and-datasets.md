# Open-DV Testing Scenarios & Datasets (`docs/17-testing-scenarios-and-datasets.md`)

## Issue #40 — Scenario S1 (Cold Start), Scenario S2 (Multi-Source Convergence)

### 1. Scenario Overview & Governing Requirements
- **GitHub Issue**: [#40 — `feat(axi-verifier,core): add Invariant DV-COL-013 to standardize Tier-1 global key prefixing`](https://github.com/nogoo9/open-dv/issues/40)
- **Audit Gap ID**: `GAP-09` (`MEDIUM`)
- **Governing Functional Requirements**: `FR-DOM-01` (Cold-Start $N=1$ Single-Source Onboarding), `FR-DOM-02` (Periodic Multi-Source Onboarding $N \to N+1$), `FR-VER-01` (Deterministic 6-Gate Symbolic Verification & 4-Batch Adversarial DuckDB Sandbox)
- **DV 2.0 Rules & Invariants**: `KEY-04` (3-Tier Business Key Authority), `DV-COL-013` (Tier-1 Global BKCC Invariant), `EVO-01` & `EVO-03` (Grain & Hash Key Immutability Across Waves)
- **Mapped Scenarios**:
  - **Scenario S1 (`Cold Start`, $N=1$):** Onboards single-source Tier-1 Global Standard feeds (`bronze_crm.accounts` with ACRA `company_uen` and `bronze_erp.invoices` with `invoice_global_id`). Verifies that Gate 1 (`gate1_contract_grain.py`) and Gate 3 (`gate3_networkx_dag.py`) reject any `TIER_1_GLOBAL_STANDARD` source lacking `require_src_qualifier=false` and `bkcc_prefix='GLOBAL'` with `DV-COL-013`, that `Algorithm A.4` (`alg_a4_cold_start.py`) normalizes Tier-1 sources to `bkcc_prefix='GLOBAL'`, and that `CanonicalIR` and 4-Zone DuckDB SQL hash `'202601234K'` as `'6:GLOBAL||10:202601234K'` (`452e5f90e20e894cf6fb9ab6dd799752b60915669d7ddba0ed2ff283774992e8`).
  - **Scenario S2 (`Multi-Source Convergence`, $N \to N+1$):** Expands `hub_customer` with a second Tier-1 source (`bronze_erp.invoices.tax_reg_no`, `bkcc_prefix='GLOBAL'`) and a colliding Tier-3 local-scoped source (`bronze_legacy.tickets.legacy_client_no`, `bkcc_prefix='LEGACY_TICKET_DB'`). Verifies that Tier-1 customer `'202601234K'` passively converges on the exact same Hub hash key (`452e5f90...`) with zero re-hashing storm (`EVO-01`), while Tier-3 key `'1001'` is isolated under `'16:LEGACY_TICKET_DB||4:1001'` (`28f087bb678034b531ff6991fdd613f6793d0a66ef6b5ece88bd5945d5f8488f`).

### 2. Test Dataset Specification
The E2E test suite (`tests/test_issue40_tier1_global_bkcc_e2e.py`) materializes three real CSV feeds and profiles them via `GraphBProfiler`:

| Wave | Qualified Bronze Table | Record Source | Authority Tier | BK Column | Sample Values | Expected Canonical Pre-Hash (`Algorithm A.5`) | Expected SHA-256 Hub Hash Key (`hk_*`) |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Wave 0 (`S1`)** | `bronze_crm.accounts` | `SFDC_CRM` | `TIER_1_GLOBAL_STANDARD` | `company_uen` | `202601234K` | `6:GLOBAL\|\|10:202601234K` | `452e5f90e20e894cf6fb9ab6dd799752b60915669d7ddba0ed2ff283774992e8` |
| **Wave 0 (`S1`)** | `bronze_erp.invoices` | `SAP_ERP` | `TIER_1_GLOBAL_STANDARD` | `invoice_global_id` | `INV-SG-2026-0001` | `6:GLOBAL\|\|16:INV-SG-2026-0001` | `c509665f95f979e5194689416288687266586e1389566e226253c769269395d8` |
| **Wave 1 (`S2`)** | `bronze_erp.invoices` | `SAP_ERP` | `TIER_1_GLOBAL_STANDARD` | `tax_reg_no` | `202601234K` | `6:GLOBAL\|\|10:202601234K` | `452e5f90e20e894cf6fb9ab6dd799752b60915669d7ddba0ed2ff283774992e8` *(passive convergence)* |
| **Wave 1 (`S2`)** | `bronze_legacy.tickets` | `LEGACY_TICKET_DB` | `TIER_3_LOCAL_SCOPED` | `legacy_client_no` | `1001` | `16:LEGACY_TICKET_DB\|\|4:1001` | `28f087bb678034b531ff6991fdd613f6793d0a66ef6b5ece88bd5945d5f8488f` *(namespace isolated)* |

### 3. Running the Unit and End-to-End Tests
Execute the following commands from the repository root (zero LLM credentials or external database services required):

```bash
# 1. Run the dedicated Issue #40 End-to-End (S1 -> S2) test suite
uv run pytest tests/test_issue40_tier1_global_bkcc_e2e.py -v

# 2. Run the unit regression tests across opendv-core, opendv-axi-verifier, and opendv-duckdb-sandbox
uv run pytest \
  packages/opendv-core/tests/test_core_models_and_hashing.py \
  packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py \
  packages/opendv-duckdb-sandbox/tests/test_duckdb_sandbox_suite.py \
  -k "tier1 or dv_col_013" -v

# 3. Run the full repository conformance test suite
uv run pytest -v
```
