# 17. Testing Scenarios & Datasets (`open-dv`)

> **Normative E2E & Regression Testing Reference**  
> This document catalogs the verification scenarios, real CSV/YAML/JSON datasets, unit tests, and end-to-end (E2E) execution commands used to validate `open-dv` against its functional requirements (`docs/00-mission-vision-and-functional-requirements.md`) and consistency audit (`docs/14-end-to-end-consistency-and-scenarios-audit.md`).

---

## Issue #39 — Scenario S2 (Multi-Source Entity Convergence)

- **GitHub Issue**: [#39 — `fix(compiler_4zone): scope staging BKCC mappings per (table, column, target_hub) tuple`](https://github.com/nogoo9/open-dv/issues/39)
- **Audit ID & Severity**: `BUG-08` (`MEDIUM`)
- **Governing Functional Requirements**: `FR-DOM-02` (Multi-Source Entity Convergence & Cross-Source Integration), `FR-INT-01` (Deterministic Canonical IR & 4-Zone SQL Compilation)
- **DV 2.0 Invariants & Algorithms**: `KEY-04` (Cross-System Collision Prevention), `Algorithm A.4` (3-Tier BKCC Authority), `Algorithm A.5` (6-Stage Length-Prefixed Canonical Pre-Hash Serialization), `Algorithm A.6` (`DV-AST-001`–`006` SQLGlot AST Linter)

### 1. Scenario & Architectural Context

Scenario S2 exercises both **multi-source entity convergence ($N=1 \to N+1$)** and **composite upstream feeds containing mixed-authority business keys**:

1. **Composite Raw Feed (`dealcloud.src_composite_deals`)**:
   - `lei_code` maps to `hub_company` under **`TIER_1_GLOBAL_STANDARD`** (`bkcc_prefix: null`, effective BKCC `'GLOBAL'` when `hub_company` carries a `bkcc` column for multi-source convergence, or unprefixed `None` when `hub_company` has only `lei_code`).
   - `internal_deal_id` maps to `hub_deal` under **`TIER_3_LOCAL_SCOPED`** (`bkcc_prefix: 'DEALCLOUD_LOCAL'`, `require_src_qualifier: true`).
   - Both columns also participate in `lnk_deal_company` (`hk_deal`, `hk_company`) from the same composite feed.
2. **Passive Global Convergence (`bloomberg.src_global_issuers`)**:
   - `lei_code` maps to `hub_company` under **`TIER_1_GLOBAL_STANDARD`**, passively converging with `dealcloud.src_composite_deals.lei_code` onto identical `hk_company` hash keys without creating duplicate Hub entries.
3. **Isolated Local Convergence + Same-As Link (`legacy_crm.src_local_companies` + `mdm.src_company_convergence`)**:
   - `legacy_co_num` maps to `hub_company` under **`TIER_3_LOCAL_SCOPED`** (`bkcc_prefix: 'LEGACY_CRM_LOCAL'`), isolating local CRM numbers from global LEIs.
   - `mdm.src_company_convergence` feeds `sal_company` (`SAME_AS_LINK`), where `master_lei_code` (`role_alias: 'MASTER'`) resolves to `(mdm.src_company_convergence, master_lei_code, hub_company) -> 'GLOBAL'` and `duplicate_legacy_co_num` (`role_alias: 'DUPLICATE'`) resolves to `(mdm.src_company_convergence, duplicate_legacy_co_num, hub_company) -> 'LEGACY_CRM_LOCAL'`.

### 2. Test Dataset Layout (`examples/pipeline_samples/scenario_s2_issue39_bkcc/`)

| File Path | Role | Key Columns & Authority Tiers |
| :--- | :--- | :--- |
| `data/src_composite_deals.csv` | Composite DealCloud feed (2 rows) | `lei_code` (`TIER_1_GLOBAL_STANDARD` $\to$ `hub_company`), `internal_deal_id` (`TIER_3_LOCAL_SCOPED`, `DEALCLOUD_LOCAL` $\to$ `hub_deal`), `company_legal_name`, `deal_stage` |
| `data/src_global_issuers.csv` | Bloomberg global issuer feed (2 rows) | `lei_code` (`TIER_1_GLOBAL_STANDARD` $\to$ `hub_company`), `credit_rating_code` |
| `data/src_local_companies.csv` | Legacy CRM local company feed (2 rows) | `legacy_co_num` (`TIER_3_LOCAL_SCOPED`, `LEGACY_CRM_LOCAL` $\to$ `hub_company`), `local_company_name` |
| `data/src_company_convergence.csv` | MDM Same-As crosswalk feed (2 rows) | `master_lei_code` (`GLOBAL` $\to$ `sal_company.hk_company_master`), `duplicate_legacy_co_num` (`LEGACY_CRM_LOCAL` $\to$ `sal_company.hk_company_duplicate`), `confidence_score` |
| `graph_a_ontology.yaml` | Domain Ontology (`Graph A`) | `urn:ontology:entity:company` (`hub_company`), `urn:ontology:entity:deal` (`hub_deal`), `lnk_deal_company`, `sal_company` |
| `graph_b_profile.json` | Empirical Source Profile (`Graph B`) | Column uniqueness, non-null, Shannon entropy, and candidate role metrics for all 4 tables |
| `dv_s2_issue39_contract.yaml` | `NoMistakeDataVault2ContractV5` Contract | 2 Hubs, 2 Links (`STANDARD_LINK` + `SAME_AS_LINK`), 5 Satellites, 11-entry `column_triage_ledger` |

### 3. Running the Unit Tests

Run the dedicated non-mocked unit tests in `packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py`:

```bash
uv run pytest packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py \
  -k "test_issue_39" -v
```

Covered unit test functions:
- `test_issue_39_composite_feed_scopes_bkcc_per_table_column_target_hub`: Verifies `build_staging_bkcc_registry`, `resolve_staging_bkcc_literal`, Algorithm A.6 SQLGlot AST verification (`verify_physical_dv2_sql_ast`), and `:memory:` DuckDB execution of `staging.stg_dealcloud_composite_deals`.
- `test_issue_39_scenario_s2_multi_source_convergence_and_4batch_sandbox`: Verifies multi-source convergence on `hub_company`, `hub_deal`, `lnk_deal_company`, and `sal_company` across all 4 Adversarial Batches (`DV-DYN-001`–`015`).
- `test_issue_39_synthesize_cumulative_contract_per_column_bkcc_tier`: Verifies that `AgenticVaultPipeline.synthesize_cumulative_contract` scopes foreign-key Hub authority tiers per `(source_table, fk_column, target_hub)` tuple.

### 4. Running the End-to-End (E2E) Verification & Sandbox Suite

```bash
# 1. Run 6-Gate static + dynamic verification via the opendv CLI
uv run opendv verify --local \
  --contract examples/pipeline_samples/scenario_s2_issue39_bkcc/dv_s2_issue39_contract.yaml \
  --graph-a examples/pipeline_samples/scenario_s2_issue39_bkcc/graph_a_ontology.yaml \
  --graph-b examples/pipeline_samples/scenario_s2_issue39_bkcc/graph_b_profile.json

# 2. Compile to Canonical IR & 4-Zone SQL and run the 4-Batch Adversarial DuckDB Sandbox
uv run opendv compile --local \
  --contract examples/pipeline_samples/scenario_s2_issue39_bkcc/dv_s2_issue39_contract.yaml \
  --output-ir /tmp/s2_issue39_canonical_ir.json \
  --output-sql /tmp/s2_issue39_four_zone.sql

uv run opendv sandbox --local \
  --contract examples/pipeline_samples/scenario_s2_issue39_bkcc/dv_s2_issue39_contract.yaml \
  --canonical-ir /tmp/s2_issue39_canonical_ir.json \
  --query-type ADVERSARIAL_BATCHES_1_TO_4 \
  --cutoff "2026-11-15 23:59:59"
```
