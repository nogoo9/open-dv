# `open-dv` — Root Agent & Contributor Constitution (`AGENTS.md`)

> **Mandatory Orientation for Autonomous AI Agents, Multi-Agent Swarms & Human Contributors**  
> Read this file first before planning, modifying, verifying, or extending any package in the `open-dv` monorepo.

---

## 1. Mission Statement

**`open-dv` is an open-source, neuro-symbolic Data Vault 2.0 (DV 2.0) modelling, evolution, and verification platform.** Its mission is to transform heterogeneous operational schemas ($N=1 \to N+1$), evolving upstream tables ($S_k(t) \to S_k(t+1)$), and unstructured enterprise documents into mathematically verified, audit-proof Data Vault 2.0 architectures (**Hubs**, **Links**, **Satellites**, **PITs**, and **Bridges**)—**without ever trusting an LLM to author raw SQL DDL or DML directly.**

---

## 2. Core Architectural Separation: Target-Agnostic Models vs. Verification SQL

Every agent or contributor working on `open-dv` must maintain a strict mental and architectural boundary between **what `open-dv` produces as its primary downstream product** and **how `open-dv` dynamically verifies and demonstrates those models**:

### 2.1 Primary Downstream Deliverables (Target-Agnostic DV 2.0 Specifications)
The primary outputs of `open-dv` for downstream enterprise data platforms are **vendor-neutral, target-agnostic declarative model specifications**:
1. **`final_contract.yaml` (`NoMistakeDataVault2ContractV5`)** — Validated against [`schemas/no-mistake-dv2-contract-v5.schema.json`](schemas/no-mistake-dv2-contract-v5.schema.json). Captures 100% column triage accountability (`column_triage_ledger`), ontology-grounded `hubs`, $M:N$ `links` (`lnk_`, `t_lnk_`, `sal_`, `hal_`, `x_lnk_`), 3-axis split `satellites` (`sat_`, `mas_`, `esat_`, `sts_`, `rts_`), `reference_tables`, and `unmapped_escalations`.
2. **`final_canonical_ir.json` (`CanonicalIR`)** — Validated against [`schemas/dv2-canonical-ir.schema.json`](schemas/dv2-canonical-ir.schema.json). The lowered, deterministic intermediate representation ready for consumption by any downstream warehouse compiler, metadata catalog, or orchestration engine (e.g., **dbt / AutomateDV / datavault4dbt**, **Snowflake**, **BigQuery**, **Databricks**, **SQLMesh**, or custom enterprise DDL/DML generators).

### 2.2 Role of the 4-Zone DuckDB SQL Bundle (`final_four_zone.sql`)
`open-dv` includes a deterministic 4-Zone DuckDB SQL compiler (`packages/opendv-duckdb-sandbox/src/opendv_duckdb_sandbox/compiler_4zone.py`) that emits `final_four_zone.sql` (Zones 0–4). This SQL bundle serves two specific roles:
1. **Internal Dynamic Verification Harness (`STAGE_06_AXI_DV_DYNAMIC_SANDBOX` / Gate 5):** Executes the compiled model inside an ephemeral `:memory:` DuckDB instance across **4 Adversarial Shadow Sandbox Batches** and **15 runtime invariants (`DV-DYN-001`–`015`)** to mathematically prove grain uniqueness, zero hash collisions, orphan-free referential integrity, true-delta Satellite historization, bi-temporal PIT accuracy, and replay idempotency ($\Delta \text{rows} = 0$) *before* a contract is sealed.
2. **Golden Reference Implementation:** Provides an immediately executable reference deployment for local sandboxing, demos, and regression testing.
3. **NOT a Mandatory Production Warehouse Dependency:** Downstream adopters are **never** required to run DuckDB in production. They may consume `final_contract.yaml` and `final_canonical_ir.json` directly or plug in target-specific SQL/dbt compilers.

---

## 3. "Clear on What, Non-Prescriptive on How" Engineering Guardrails

To preserve architectural integrity while encouraging rapid innovation, all contributions and agent actions are governed by **Immutable Outcomes (The "What")** paired with **Implementation Freedom (The "How")**.

### 3.1 Immutable Outcomes (The "What" — Strictly Non-Negotiable)
No PR, codemod, prompt change, or feature extension may weaken or bypass these seven guarantees (defined normatively in [`docs/00-mission-vision-and-functional-requirements.md`](docs/00-mission-vision-and-functional-requirements.md)):
1. **Zero Unverified LLM SQL Generation (`OBJ-01`):** LLM agents (`@vault-first-mate`, `@scout-source`, `@scout-unstructured`, `@ship-vault`) emit and repair **only** declarative YAML/JSON contracts (`NoMistakeDataVault2ContractV5` / `CanonicalIR`). Physical SQL is emitted exclusively by deterministic symbolic compilers.
2. **100% Column Triage Accountability (`FR-DOM-01` / `DV-G4-002`):** Every physical source column in every wave must be accounted for in `column_triage_ledger` (`BUSINESS_KEY`, `FOREIGN_KEY_REF`, `DESCRIPTIVE_PAYLOAD`, `SYSTEM_SURROGATE_DROPPED`, or `OMITTED_INTENTIONALLY` with a $\ge 15$-character rationale). Silent column dropping is a hard gate failure.
3. **Deterministic, Injective, Collision-Free Hashing (`FR-VER-01` / `Algorithm A.5`):** All Hub/Link hash keys (`_hk`) and Satellite change digests (`hash_diff`) must use length-prefixed (`<octet_len>:<val>`) or delimiter-escaped canonical serialization with explicit null-token substitution (`'-1:^^'`), 3-Tier `BKCC` namespace isolation (`TIER_1_GLOBAL_STANDARD`, `TIER_2_ENTERPRISE_SOR`, `TIER_3_LOCAL_SCOPED`), and Dual Zero-Key sentinels (`0x00..00` for `-1` Unknown, `0xFF..FF` for `-2` Inapplicable/Error).
4. **Insert-Only Raw Vault History (`FR-VER-01` / `INV-01..06`):** Raw Data Vault tables (`hub_*`, `lnk_*`, `sat_*`, `mas_*`) are strictly `INSERT`-only. Physical `UPDATE`, `DELETE`, `DROP COLUMN`, or mutable `load_end_dts` columns on base Satellites are forbidden; effective intervals are derived virtually via `LEAD(load_dts)` windowing or PIT tables.
5. **Additive-Only Schema Evolution Across Waves (`FR-DOM-02..04` / `Algorithm A.7` & `EVO-01..08`):** Across $N \to N+1$ source onboarding and $S_k(t) \to S_k(t+1)$ schema drift waves, previously deployed Hub/Link grains and historical Satellite `hash_diff` digests are immutable. Added columns route to additive companion satellites (`sat_<base>_ext_*`), dropped source columns project `CAST(NULL AS <type>)`, and sparse optional FKs ($U_{\text{nn}} < 0.95$) split into filtered companion links (`Algorithm A.4`).
6. **Span-Grounded Unstructured Extraction & Steward Gating (`FR-DOM-05` / `Rules U1–U4`, `Algorithm A.9b`):** Raw blobs and embedding vectors never enter relational Satellites (`Rule U1`). Extracted claims require verbatim `[char_start, char_end]` document span provenance (`Rule U2/U3`), and low-confidence/ambiguous entity matches (`Tier-U3`) or unmapped ontology concepts (`DV-G4-001`) must pause for human Data Steward approval (`STEWARD_HOLD` / `ASK-USER`).
7. **5-Tier RBAC & Zero PII/Hash Leakage (`FR-INT-02`):** All telemetry (`state/vault-ledger.jsonl`), reports, and UI payloads must respect 5-Tier RBAC redaction and Semantic Hash Pill formatting (`validate_no_pii_leaks`).

### 3.2 Implementation Freedom (The "How" — Extensible by Design)
Provided all Immutable Outcomes and verification gates pass, contributors have full freedom to evolve and extend:
- **Agent Harnesses & Foundation Models (`packages/opendv-agent-harness`):** Add new LLM providers, CLI/SDK adapters beyond `agy`, `claude-cli`, `antigravity`, and `claude`, or refine prompt decomposition and `@toon_v1` repair loops.
- **Target Dialect Compilers & Exporters:** Add downstream compilers lowering `CanonicalIR` / `NoMistakeDataVault2ContractV5` to **dbt (`AutomateDV` / `datavault4dbt`)**, **Snowflake**, **BigQuery**, **Databricks Delta Live Tables**, **SQLMesh**, or **Apache Iceberg**.
- **Profiling & Graph Discovery Engines:** Enhance `GraphBProfiler`, functional dependency discovery, semantic similarity matchers, and multimodal OCR/PDF chunking strategies.
- **UI & API Transports (`apps/opendv-platform-api`, `apps/opendv-web-workbench`, `packages/opendv-protocol-ts`):** Evolve workbench visualizations (`WB-00`–`WB-11`), state persistence backends, and enterprise catalog integrations.

---

## 4. Navigation & Documentation Map

| Priority | Document | Purpose |
| :---: | :--- | :--- |
| **P0** | [`docs/00-mission-vision-and-functional-requirements.md`](docs/00-mission-vision-and-functional-requirements.md) | **Mission, Vision, Functional Requirements (`FR-DOM-*`, `FR-VER-*`, `FR-INT-*`), 6-Point Conformance Rubric & Traceability Matrix (`S1`–`S10`, Issues `#30`–`#44`).** |
| **P0** | [`docs/16-human-and-agent-usage-guide.md`](docs/16-human-and-agent-usage-guide.md) | **Operational Playbook:** Operating modes (`Mode A/B/C`), wave-by-wave periodic onboarding, Steward Hold resolution, and downstream `CanonicalIR` consumption (`dbt` / Snowflake / Databricks). |
| **P0** | [`README.md`](README.md) | Quick start, monorepo package layout, CLI command summary, and 4-wave sample dataset guide. |
| **P1** | [`docs/DV2_AGENT_REFERENCE_GUIDE.md`](docs/DV2_AGENT_REFERENCE_GUIDE.md) | Single-file technical reference for DV 2.0 layers, 6-stage hashing, `INV-01..06`, `EVO-01..08`, `AP-01..15`, and `Algorithms A.1–A.9b`. |
| **P1** | [`docs/12-agentic-pipeline-inputs-and-harness-guide.md`](docs/12-agentic-pipeline-inputs-and-harness-guide.md) | Complete specification for `pipeline_input.yaml`, `harness_config.yaml`, `model_overrides`, `agent_models`, and auth modes. |
| **P1** | [`docs/14-end-to-end-consistency-and-scenarios-audit.md`](docs/14-end-to-end-consistency-and-scenarios-audit.md) | 10-scenario architectural consistency audit, unmatched ontology field routing matrix, and tracked remediation issues (`#30`–`#34`, `#37`–`#44`). |
| **P2** | [`docs/01-core-modelling-spec.md`](docs/01-core-modelling-spec.md) – [`docs/15-end-to-end-pipeline-execution-and-interactive-spec.md`](docs/15-end-to-end-pipeline-execution-and-interactive-spec.md) | Deep-dive modular specifications covering ELM calculus, 6-Gate `axi-dv` verification, unstructured DV 2.0, observability (`WB-00..11`), and API/SDK contracts. |

---

## 5. Monorepo Package Ownership Boundaries

| Package / App Path | Responsibility | Allowed Dependencies |
| :--- | :--- | :--- |
| `packages/opendv-core` | Pydantic v2 models (`NoMistakeDataVault2ContractV5`, `CanonicalIR`), `Algorithm A.5` hashing, ELM math, constants. | Pure Python + Pydantic (zero LLM or DB dependencies). |
| `packages/opendv-telemetry` | Append-only JSONL ledger, Content-Addressable Store (CAS), 5-Tier RBAC redactor, PII detector, Semantic Hash Pills. | `opendv-core`. |
| `packages/opendv-axi-verifier` | 6-Gate static/evolution verifier (`Gates 0–4`), AST linter (`Algorithm A.6`), codemods (`A.1..A.9b`), `@toon_v1` codec. | `opendv-core`, `opendv-telemetry`, `sqlglot`, `networkx`. |
| `packages/opendv-duckdb-sandbox` | `GraphBProfiler`, IR lowerer, 4-Zone DuckDB SQL compiler (`compiler_4zone.py`), Gate 5 4-Batch Adversarial Shadow Sandbox (`DV-DYN-001..015`), PIT engine. | `opendv-core`, `opendv-telemetry`, `opendv-axi-verifier`, `duckdb`. |
| `packages/opendv-agent-harness` | 8-stage `AgenticVaultPipeline` (`STAGE_00`–`STAGE_07`), 4 agent personas, 8 neuro-symbolic tools, `StewardHoldManager`, and `agy` / `claude-cli` / `antigravity` / `claude` adapters. | All Python packages above. |
| `packages/opendv-protocol-ts` | Zero-dependency TypeScript SDK (`@opendv/protocol-ts`), SSE client, Web Crypto Hash Pills. | Pure TypeScript / Web APIs. |
| `apps/opendv-cli` | Unified `opendv` & `axi-dv` CLI (`--local` in-process and `--api-url` remote modes). | All Python packages above. |
| `apps/opendv-platform-api` | FastAPI REST + W3C SSE headless server (`:8420`) and OpenAPI 3.1 exporter. | All Python packages above + `fastapi`. |
| `apps/opendv-web-workbench` | 12-View React 19 + Bun Explainability & Steward Workbench (`WB-00`–`WB-11`, `:5173`). | `@opendv/protocol-ts`. |

---

## 6. Essential Conformance, Verification & Testing Commands

Before submitting any change or concluding an agentic task, verify conformance using these commands from the repository root:

```bash
# 1. Run the full normative Python test suite (unit + 6-gate verifier + DuckDB sandbox + E2E pipeline)
uv run pytest -v

# 2. Validate pipeline input specifications, ontology, sources, and harness configuration (deterministic pre-flight)
uv run opendv pipeline validate \
  --input examples/pipeline_samples/global_commerce_vault/wave0_cold_start.yaml \
  --harness-config examples/pipeline_samples/global_commerce_vault/harness_config_agy.yaml \
  --json

# 3. Run standalone 6-Gate symbolic verification (zero LLM credentials required)
uv run opendv verify --local \
  --contract examples/private_equity/dv_pe_model_contract.yaml \
  --graph-a examples/private_equity/pe_graph_a_ontology.yaml \
  --graph-b examples/private_equity/pe_graph_b_profile.json

# 4. Compile a verified contract to Canonical IR & 4-Zone SQL and run the 4-Batch DuckDB Shadow Sandbox
uv run opendv compile --local \
  --contract examples/private_equity/dv_pe_model_contract.yaml \
  --canonical-ir examples/private_equity/pe_canonical_ir.json

uv run opendv sandbox --local \
  --contract examples/private_equity/dv_pe_model_contract.yaml \
  --canonical-ir examples/private_equity/pe_canonical_ir.json \
  --query-type ADVERSARIAL_BATCHES_1_TO_4 \
  --cutoff "2026-11-15 23:59:59"

# 5. Run TypeScript SDK & Web Workbench typecheck and tests
bun test
```

> [!IMPORTANT]
> **Working on an Open GitHub Issue (`#30`–`#34`, `#37`–`#44`)?**  
> Always consult the **Requirements-to-Scenarios & Open Issues Traceability Matrix** in [`docs/00-mission-vision-and-functional-requirements.md`](docs/00-mission-vision-and-functional-requirements.md) (§7) and [`docs/14-end-to-end-consistency-and-scenarios-audit.md`](docs/14-end-to-end-consistency-and-scenarios-audit.md) to verify which Functional Requirement (`FR-*`), Scenario (`S1`–`S10`), and DV 2.0 Invariant (`INV-*`, `EVO-*`, `DV-*`) your change must satisfy and test.
