# 17. Testing Scenarios & Datasets (`open-dv`)

> **Normative E2E & Regression Testing Reference**  
> This document catalogs the verification scenarios (`S1`–`S10`), real CSV/YAML/JSON datasets, unit tests, and end-to-end (E2E) execution commands used to validate `open-dv` against its functional requirements ([`docs/00-mission-vision-and-functional-requirements.md`](00-mission-vision-and-functional-requirements.md)) and consistency audit ([`docs/14-end-to-end-consistency-and-scenarios-audit.md`](14-end-to-end-consistency-and-scenarios-audit.md)).

---

## 1. Repository Testing Scenarios & Datasets Catalog

| Scenario / Suite | Dataset & Fixture Path | Governing Requirements & Invariants | Primary Test Suite & CLI Entrypoint |
| :--- | :--- | :--- | :--- |
| **S1: Day-1 Cold Start ($N=1$)** | [`examples/pipeline_samples/global_commerce_vault/wave0_cold_start.yaml`](../examples/pipeline_samples/global_commerce_vault/wave0_cold_start.yaml) (`sources/crm_customers_w0.csv`, `sources/erp_orders_w0.csv`) | `FR-DOM-01`, `INV-01..06`, `KEY-01..05`, `DV-G4-002` (100% Column Triage), `Algorithms A.1`, `A.4`, `A.5` | `uv run opendv pipeline validate --input examples/pipeline_samples/global_commerce_vault/wave0_cold_start.yaml --harness-config examples/pipeline_samples/global_commerce_vault/harness_config_agy.yaml --json` |
| **S2: Multi-Source Entity Convergence & Composite-Feed Scoped BKCC (Issue [#39](https://github.com/nogoo9/open-dv/issues/39))** | [`examples/pipeline_samples/scenario_s2_issue39_bkcc/`](../examples/pipeline_samples/scenario_s2_issue39_bkcc/) (`src_composite_deals.csv`, `src_global_issuers.csv`, `src_local_companies.csv`, `src_company_convergence.csv`, `dv_s2_issue39_contract.yaml`) & [`examples/pipeline_samples/global_commerce_vault/wave1_incremental_billing.yaml`](../examples/pipeline_samples/global_commerce_vault/wave1_incremental_billing.yaml) | `FR-DOM-02`, `FR-INT-01`, `KEY-04`, `ANTI_SILO_VIOLATION`, `EVO-08`, `Algorithm A.4` & `A.5` (`BUG-08`) | `uv run pytest packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py -k "test_issue_39" -v` (see [§2](#2-issue-39--scenario-s2-multi-source-entity-convergence--scoped-bkcc) below) |
| **S3: Unmapped Feed / Absent Ontology Concept** | `packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py` & `packages/opendv-agent-harness/tests/test_hold_manager_and_pipeline.py` | `FR-DOM-04`, `DV-G4-001` (Ontology Firewall), `ASK-USER` / `STEWARD_HOLD` | `uv run pytest packages/opendv-axi-verifier/tests/ packages/opendv-agent-harness/tests/ -v` |
| **S4: Upstream Schema Drift ($S_k(t) \to S_k(t+1)$)** | [`examples/pipeline_samples/global_commerce_vault/wave2_schema_drift.yaml`](../examples/pipeline_samples/global_commerce_vault/wave2_schema_drift.yaml) (`sources/crm_customers_w2_drifted.csv`) | `FR-DOM-03`, `EVO-01..08`, `DV-EVO-001..008`, `Algorithms A.7`, `A.9a` | `uv run pytest packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py -k "drift or evolution" -v` |
| **S5: Nullable Foreign Key / $0..1$ Relationship ($U_{\text{nn}} < 0.95$)** | [`examples/private_equity/`](../examples/private_equity/) (`lnk_capital_call_co_invest`) & `packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py` | `FR-DOM-01`, `DV-G2-001`, `Algorithm A.4` (Sparse FK Companion Link Split) | `uv run pytest packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py -k "a4 or gate2" -v` |
| **S6: Retroactive / Out-of-Order Bi-Temporal Arrival** | [`examples/private_equity/dv_pe_model_contract.yaml`](../examples/private_equity/dv_pe_model_contract.yaml) & [`examples/private_equity/pe_canonical_ir.json`](../examples/private_equity/pe_canonical_ir.json) (Adversarial Batch 4 Q3 valuation restatement) | `FR-DOM-03`, `FR-VER-02`, `TMP-01..04`, `DV-DYN-009`, `Queries D.10` & `D.10b` | `uv run opendv sandbox --local --contract examples/private_equity/dv_pe_model_contract.yaml --canonical-ir examples/private_equity/pe_canonical_ir.json --query-type ADVERSARIAL_BATCHES_1_TO_4` |
| **S7: Malformed / Adversarial Raw Data & Hash Parity** | `packages/opendv-core/tests/test_core_models_and_hashing.py` & `packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py` (Batches 1–3) | `FR-VER-01`, `Algorithm A.5` (6-Stage Length-Prefixed SHA-256), `Algorithm A.6` (`DV-AST-001..006`) | `uv run pytest packages/opendv-core/tests/ packages/opendv-duckdb-sandbox/tests/ -v` |
| **S8: Unstructured Document Ingestion & Span Grounding** | [`examples/pipeline_samples/global_commerce_vault/wave3_unstructured_msa.yaml`](../examples/pipeline_samples/global_commerce_vault/wave3_unstructured_msa.yaml) (`sources/enterprise_msa_addendum_2026.txt`) | `FR-DOM-05`, `Rules U1–U4`, `Tier-U1..U3`, `Algorithm A.9b` (`SPAN_HALLUCINATION`) | `uv run pytest packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py -k "unstructured or a9b" -v` |
| **S9: Multi-Tenant / Multi-Unit Keyspace Collisions** | [`examples/dv_model_contract.yaml`](../examples/dv_model_contract.yaml), [`examples/private_equity/`](../examples/private_equity/) (`GLOBAL` vs `LEGACY_GP_ADMIN`), & [`examples/pipeline_samples/scenario_s2_issue39_bkcc/`](../examples/pipeline_samples/scenario_s2_issue39_bkcc/) | `FR-DOM-02`, `KEY-04` (3-Tier `BKCC` Namespace Isolation), `Algorithm A.4` & `A.5` | `uv run opendv verify --local --contract examples/private_equity/dv_pe_model_contract.yaml --graph-a examples/private_equity/pe_graph_a_ontology.yaml --graph-b examples/private_equity/pe_graph_b_profile.json` |
| **S10: Ghost Record & Dual Zero-Key Completeness** | [`examples/private_equity/`](../examples/private_equity/) & [`examples/pipeline_samples/scenario_s2_issue39_bkcc/`](../examples/pipeline_samples/scenario_s2_issue39_bkcc/) (Adversarial Batch 1 & Batch 2) | `FR-DOM-01`, `FR-VER-02`, `KEY-02b`, `DV-DYN-003` (Replay Idempotency), `DV-DYN-007` (Zero/Ghost Seed) | `uv run pytest packages/opendv-duckdb-sandbox/tests/test_duckdb_sandbox_suite.py -v` |

---

## 2. Issue #39 — Scenario S2 (Multi-Source Entity Convergence & Scoped BKCC)

- **GitHub Issue**: [#39 — `fix(compiler_4zone): scope staging BKCC mappings per (table, column, target_hub) tuple`](https://github.com/nogoo9/open-dv/issues/39)
- **Audit ID & Severity**: `BUG-08` (`MEDIUM`)
- **Governing Functional Requirements**: `FR-DOM-02` (Multi-Source Entity Convergence & Cross-Source Integration), `FR-INT-01` (Deterministic Canonical IR & 4-Zone SQL Compilation)
- **DV 2.0 Invariants & Algorithms**: `KEY-04` (Cross-System Collision Prevention), `Algorithm A.4` (3-Tier BKCC Authority), `Algorithm A.5` (6-Stage Length-Prefixed Canonical Pre-Hash Serialization), `Algorithm A.6` (`DV-AST-001`–`006` SQLGlot AST Linter)

### 2.1 Scenario & Architectural Context

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

### 2.2 Test Dataset Layout (`examples/pipeline_samples/scenario_s2_issue39_bkcc/`)

| File Path | Role | Key Columns & Authority Tiers |
| :--- | :--- | :--- |
| `data/src_composite_deals.csv` | Composite DealCloud feed (2 rows) | `lei_code` (`TIER_1_GLOBAL_STANDARD` $\to$ `hub_company`), `internal_deal_id` (`TIER_3_LOCAL_SCOPED`, `DEALCLOUD_LOCAL` $\to$ `hub_deal`), `company_legal_name`, `deal_stage` |
| `data/src_global_issuers.csv` | Bloomberg global issuer feed (2 rows) | `lei_code` (`TIER_1_GLOBAL_STANDARD` $\to$ `hub_company`), `credit_rating_code` |
| `data/src_local_companies.csv` | Legacy CRM local company feed (2 rows) | `legacy_co_num` (`TIER_3_LOCAL_SCOPED`, `LEGACY_CRM_LOCAL` $\to$ `hub_company`), `local_company_name` |
| `data/src_company_convergence.csv` | MDM Same-As crosswalk feed (2 rows) | `master_lei_code` (`GLOBAL` $\to$ `sal_company.hk_company_master`), `duplicate_legacy_co_num` (`LEGACY_CRM_LOCAL` $\to$ `sal_company.hk_company_duplicate`), `confidence_score` |
| `graph_a_ontology.yaml` | Domain Ontology (`Graph A`) | `urn:ontology:entity:company` (`hub_company`), `urn:ontology:entity:deal` (`hub_deal`), `lnk_deal_company`, `sal_company` |
| `graph_b_profile.json` | Empirical Source Profile (`Graph B`) | Column uniqueness, non-null, Shannon entropy, and candidate role metrics for all 4 tables |
| `dv_s2_issue39_contract.yaml` | `NoMistakeDataVault2ContractV5` Contract | 2 Hubs, 2 Links (`STANDARD_LINK` + `SAME_AS_LINK`), 5 Satellites, 11-entry `column_triage_ledger` |

### 2.3 Running the Unit Tests

Run the dedicated non-mocked unit tests in `packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py`:

```bash
uv run pytest packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py \
  -k "test_issue_39" -v
```

Covered unit test functions:
- `test_issue_39_composite_feed_scopes_bkcc_per_table_column_target_hub`: Verifies `build_staging_bkcc_registry`, `resolve_staging_bkcc_literal`, Algorithm A.6 SQLGlot AST verification (`verify_physical_dv2_sql_ast`), and `:memory:` DuckDB execution of `staging.stg_dealcloud_composite_deals`.
- `test_issue_39_scenario_s2_multi_source_convergence_and_4batch_sandbox`: Verifies multi-source convergence on `hub_company`, `hub_deal`, `lnk_deal_company`, and `sal_company` across all 4 Adversarial Batches (`DV-DYN-001`–`015`) using the real CSV files, plus same-table multi-column `hub_company` BKCC scoping.
- `test_issue_39_synthesize_cumulative_contract_per_column_bkcc_tier`: Verifies that `AgenticVaultPipeline.synthesize_cumulative_contract` scopes foreign-key Hub authority tiers and `SOURCE_SYSTEM_PREFIX_REQUIRED` sentinels per `(source_table, fk_column, target_hub)` tuple.

### 2.4 Running the End-to-End (E2E) Verification & Sandbox Suite

```bash
# 1. Run 6-Gate static + dynamic verification via the opendv CLI
uv run opendv verify --local \
  --contract examples/pipeline_samples/scenario_s2_issue39_bkcc/dv_s2_issue39_contract.yaml \
  --graph-a examples/pipeline_samples/scenario_s2_issue39_bkcc/graph_a_ontology.yaml \
  --graph-b examples/pipeline_samples/scenario_s2_issue39_bkcc/graph_b_profile.json

# 2. Compile to Canonical IR & 4-Zone SQL and run the 4-Batch Adversarial DuckDB Sandbox
uv run opendv compile --local \
  --contract examples/pipeline_samples/scenario_s2_issue39_bkcc/dv_s2_issue39_contract.yaml

uv run opendv sandbox --local \
  --contract examples/pipeline_samples/scenario_s2_issue39_bkcc/dv_s2_issue39_contract.yaml \
  --query-type ADVERSARIAL_BATCHES_1_TO_4 \
  --cutoff "2026-11-15 23:59:59"
```

---

## 3. Running Full Repository Conformance Suites

For complete repository-wide verification (unit tests, 6-gate static verifier, 4-Batch DuckDB sandbox, E2E pipeline specs, and TypeScript SDK/Workbench tests):

```bash
# Python normative test suite (all packages + E2E tests)
uv run pytest -v

# TypeScript protocol SDK & Web Workbench test suite
bun test
```

For multi-wave agentic pipeline execution (`wave0`–`wave3`) and Steward Hold workflows, see [`docs/16-human-and-agent-usage-guide.md`](16-human-and-agent-usage-guide.md).
