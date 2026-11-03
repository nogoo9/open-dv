# Open-DV Testing Scenarios & Datasets

## Issue #41 — Scenario S2 (Multi-Source Entity Convergence)

- **GitHub Issue**: [`#41` — `feat(axi-verifier): add Invariant DV-STR-015 to enforce canonical edge ordering on Same-As Links`](https://github.com/nogoo9/open-dv/issues/41)
- **Audit Gap ID & Severity**: `GAP-10` (`MEDIUM`)
- **Governing Functional Requirements**: `FR-DOM-02` (Wave-by-Wave Multi-Source Entity Convergence), `FR-VER-01` (6-Gate Neuro-Symbolic Verification)
- **DV 2.0 Invariants Verified**: `DV-STR-004`, `DV-STR-015` (Canonical Same-As Link Ordering), `DV-DYN-006` (Multi-Source BK Convergence & SAL Resolution), `DV-DYN-007` (Ghost & Zero-Key Completeness)

### Scenario Overview & Dataset
Scenario S2 models multi-source entity convergence onto a shared Hub (`hub_customer` / `hub_limited_partner`) across sources with distinct 3-Tier `BKCC` authority levels:
1. **Enterprise System of Record (`erp.src_sap_customers` / `investran.src_lp_investors`)**: `TIER_2_ENTERPRISE_SOR` (`require_src_qualifier: false`, `bkcc_prefix: null`).
2. **Local Scoped Source (`crm.src_sfdc_accounts` / `legacy_gp.src_legacy_lp_roster`)**: `TIER_3_LOCAL_SCOPED` (`require_src_qualifier: true`, `bkcc_prefix: "CRM_SALESFORCE"` / `"LEGACY_GP_ADMIN"`).
3. **MDM Steward Crosswalk (`mdm.src_customer_steward_crosswalk` / `mdm.src_lp_steward_crosswalk`)**: Feeds `sal_customer` / `sal_limited_partner` (`link_type: "SAME_AS_LINK"`), mapping `MASTER` (`index 0`, Tier-2) $\to$ `DUPLICATE_ALIAS` (`index 1`, Tier-3).

Adversarial mutations tested against this scenario:
- **Static Contract Mutations (Gate 3 `DV-STR-015`)**:
  - Inverted role ordering (`[DUPLICATE_ALIAS_LP, MASTER_LP]`).
  - Inverted 3-Tier `BKCC` authority hierarchy (`TIER_3_LOCAL_SCOPED` mapped as `MASTER_LP` over `TIER_2_ENTERPRISE_SOR` as `DUPLICATE_ALIAS_LP`).
  - Reflexive self-loops `(A, A)` in `source_fk_columns`, `where_filter_condition`, or `edge_pairs`.
  - Duplicate inverted `(B, A)` pairs within `edge_pairs` or across a second `sal_*` link on the same Hub.
- **Dynamic Runtime Mutations (Zone 2 DML & Gate 5 `DV-DYN-006`)**:
  - Bronze crosswalk rows containing reflexive self-loops `('CUST-100', 'CUST-100')` and inverted duplicate pairs `('CUST-100', 'CUST-200')` + `('CUST-200', 'CUST-100')`.
  - Direct injection of bypassed `(A, A)` and `(B, A)` rows into `business_vault.sal_customer` to verify `check_dv_dyn_006_bkcc_collision_and_sal` fails with `failing_rows >= 2`.

### How to Run the Unit & End-to-End Tests

```bash
# 1. Run the Gate 3 static invariant unit test for DV-STR-015 (no mocks)
uv run pytest -v packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py \
  -k "test_gate3_dv_str_015_canonical_sal_edge_ordering"

# 2. Run the Scenario S2 end-to-end 4-Zone SQL compiler & DuckDB Shadow Sandbox test (no mocks)
uv run pytest -v packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py \
  -k "test_scenario_s2_sal_canonical_ordering_and_dv_dyn_006_enforcement"

# 3. Run standalone CLI 6-Gate verification and 4-Batch DuckDB Shadow Sandbox on the PE reference model
uv run opendv verify --local \
  --contract examples/private_equity/dv_pe_model_contract.yaml \
  --graph-a examples/private_equity/pe_graph_a_ontology.yaml \
  --graph-b examples/private_equity/pe_graph_b_profile.json

uv run opendv sandbox --local \
  --contract examples/private_equity/dv_pe_model_contract.yaml \
  --canonical-ir examples/private_equity/pe_canonical_ir.json \
  --query-type ADVERSARIAL_BATCHES_1_TO_4 \
  --cutoff "2026-11-15 23:59:59"
```
