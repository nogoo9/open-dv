# `open-dv` — Root Agent & Contributor Constitution (`AGENTS.md`)

> **Mandatory Orientation for Autonomous AI Agents, Multi-Agent Swarms & Human Contributors**  
> Read this file first before planning, modifying, verifying, or extending any package in the `open-dv` monorepo.

---

## 1. Mission Statement

**`open-dv` is an open-source, neuro-symbolic Data Vault 2.0 (DV 2.0) modelling, evolution, and verification platform.** Its mission is to transform heterogeneous operational schemas ($N=1 \to N+1$), evolving upstream tables ($S_k(t) \to S_k(t+1)$), and unstructured enterprise documents into mathematically verified, audit-proof Data Vault 2.0 architectures (**Hubs**, **Links**, **Satellites**, **PITs**, and **Bridges**)—**without ever trusting an LLM to author raw SQL DDL or DML directly.**

---

## 2. Core Architectural Separation: Target-Agnostic Models vs. Verification SQL

Maintain a strict boundary between **what `open-dv` produces downstream** and **how `open-dv` dynamically verifies those models**:

### 2.1 Primary Downstream Deliverables (Target-Agnostic DV 2.0 Specifications)
1. **`final_contract.yaml` (`NoMistakeDataVault2ContractV5`)** — Validated against [`schemas/no-mistake-dv2-contract-v5.schema.json`](schemas/no-mistake-dv2-contract-v5.schema.json). Captures 100% column triage (`column_triage_ledger`), ontology-grounded `hubs`, $M:N$ `links` (`lnk_`, `t_lnk_`, `sal_`, `hal_`, `x_lnk_`), 3-axis split `satellites` (`sat_`, `mas_`, `esat_`, `sts_`, `rts_`), `reference_tables`, and `unmapped_escalations`.
2. **`final_canonical_ir.json` (`CanonicalIR`)** — Validated against [`schemas/dv2-canonical-ir.schema.json`](schemas/dv2-canonical-ir.schema.json). Deterministic lowered IR consumed by downstream warehouse compilers, catalogs, or orchestrators (**dbt / AutomateDV / datavault4dbt**, **Snowflake**, **BigQuery**, **Databricks**, **SQLMesh**, or custom DDL/DML generators).

### 2.2 Role of the 4-Zone DuckDB SQL Bundle (`final_four_zone.sql`)
`packages/opendv-duckdb-sandbox/src/opendv_duckdb_sandbox/compiler_4zone.py` emits `final_four_zone.sql` (Zones 0–4) for two specific roles:
1. **Internal Dynamic Verification Harness (`STAGE_06_AXI_DV_DYNAMIC_SANDBOX` / Gate 5):** Runs inside `:memory:` DuckDB across **4 Adversarial Shadow Sandbox Batches** and **15 runtime invariants (`DV-DYN-001`–`015`)** to prove grain uniqueness, zero hash collisions, orphan-free referential integrity, true-delta historization, bi-temporal PIT accuracy, and replay idempotency ($\Delta \text{rows} = 0$) before sealing a contract.
2. **Golden Reference Implementation:** Executable local reference deployment for sandboxing, demos, and regression tests.
3. **NOT a Production Warehouse Dependency:** Downstream adopters consume `final_contract.yaml` and `final_canonical_ir.json` directly or via target-specific compilers without requiring DuckDB in production.

---

## 3. "Clear on What, Non-Prescriptive on How" Engineering Guardrails & Rationale

All contributions are governed by **Immutable Outcomes (The "What")** paired with **Implementation Freedom (The "How")** and **Design Rationale (The "Why")** (see [`docs/00-mission-vision-and-functional-requirements.md` §3.1](docs/00-mission-vision-and-functional-requirements.md#31-normative-guarantee-what-vs-implementation-flexibility-how--design-rationale-why-matrix)):

### 3.1 Immutable Outcomes (The "What" — Strictly Non-Negotiable) & Design Rationale (The "Why")
No PR, codemod, prompt change, or extension may weaken these fourteen guarantees:
1. **Zero Unverified LLM SQL Generation (`OBJ-01`):** Agents (`@vault-first-mate`, `@scout-source`, `@scout-unstructured`, `@ship-vault`) emit/repair **only** declarative YAML/JSON (`NoMistakeDataVault2ContractV5` / `CanonicalIR`); physical SQL comes solely from symbolic compilers. *(Rationale: eliminates SQL/delimiter/`hash_diff` hallucination in insert-only vaults.)*
2. **100% Column Triage Accountability (`FR-DOM-01` / `DV-G4-002`):** Every source column in every wave must appear in `column_triage_ledger` (`BUSINESS_KEY`, `FOREIGN_KEY_REF`, `DESCRIPTIVE_PAYLOAD`, `SYSTEM_SURROGATE_DROPPED`, or `OMITTED_INTENTIONALLY` with a $\ge 15$-char rationale). *(Rationale: prevents silent dropping of drifted columns, preserving full regulatory lineage.)*
3. **Deterministic, Injective, Collision-Free Hashing (`FR-VER-01` / `Algorithm A.5`):** All `_hk` and `hash_diff` digests use length-prefixed (`<octet_len>:<val>`) or escaped serialization with null token `'-1:^^'`, 3-Tier `BKCC` (`TIER_1_GLOBAL_STANDARD`, `TIER_2_ENTERPRISE_SOR`, `TIER_3_LOCAL_SCOPED`), and Dual Zero-Keys (`0x00..00` for `-1`, `0xFF..FF` for `-2`). *(Rationale: prevents collisions, `CONCAT_WS` null drops, and UTF-8 byte/char divergence.)*
4. **Insert-Only Raw Vault History (`FR-VER-01` / `INV-01..06`):** Raw tables (`hub_*`, `lnk_*`, `sat_*`, `mas_*`) are strictly `INSERT`-only; `UPDATE`, `DELETE`, `DROP COLUMN`, and mutable `load_end_dts` are forbidden (derive intervals via `LEAD(load_dts)` or PITs). *(Rationale: preserves non-destructive historical auditability without SCD2 rewrite locks.)*
5. **Additive-Only Schema Evolution (`FR-DOM-02..04` / `Algorithm A.7` & `EVO-01..08`):** Across $N \to N+1$ and $S_k(t) \to S_k(t+1)$ waves, deployed grains and historical `hash_diff` digests are immutable; new columns route to `sat_<base>_ext_*`, dropped columns project `CAST(NULL AS <type>)`, and sparse FKs ($U_{\text{nn}} < 0.95$) split into companion links (`Algorithm A.4`). *(Rationale: zero downtime, zero re-hashing storms, zero broken downstream queries.)*
6. **Span-Grounded Unstructured Extraction & Steward Gating (`FR-DOM-05` / `Rules U1–U4`, `Algorithm A.9b`):** Raw blobs/vectors never enter relational Satellites (`Rule U1`); claims require `[char_start, char_end]` spans (`Rule U2/U3`), and `Tier-U3` / `DV-G4-001` ambiguities pause on `STEWARD_HOLD` / `ASK-USER`. *(Rationale: blocks document hallucinations and governs ambiguous identities.)*
7. **5-Tier RBAC & Zero PII/Hash Leakage (`FR-INT-02`):** Telemetry (`state/vault-ledger.jsonl`), reports, and UI payloads enforce 5-Tier RBAC redaction and Semantic Hash Pills (`validate_no_pii_leaks`). *(Rationale: enables GDPR/CCPA crypto-shredding without exposing sensitive payloads or raw keys.)*
8. **Parity Mandate across Transports:** Standalone `opendv` (Python), client `opendvctl` (Bun), and REST/SSE API expose identical capabilities with normalized parity (RFC 8785 JCS, identical exit codes, and error details). *(Rationale: deterministic behavior across scripts, operators, and UI clients.)*
9. **Golden Rule for Output Baselines:** Goldens change only via `pytest -m golden --update-golden --golden-justification="[#ISSUE] reason"`; hand-edits are forbidden. *(Rationale: prevents silent contract drift and test weakening.)*
10. **Shared Application Core (`REQ-CORE-01`):** Application logic, pipelines, verification, profiling, compilation, and sandboxing live exclusively in `packages/opendv-service`; `opendv-cli` and `opendv-platform-api` are thin adapters (`opendv-cli` never imports API modules). *(Rationale: enforces layered monorepo DAG boundaries.)*
11. **Hardening & Fail-Loud Guarantees (`REQ-CFG-01` / `REQ-MOCK-01` / `REQ-FAIL-01`):** Zero hardcoded paths/ports, zero production mocks, and explicit typed error UX (no silent fallbacks/demo data). *(Rationale: container/air-gapped reliability without false-positive passes.)*
12. **Two Consistent CLIs (`REQ-CLI-01` / `REQ-CLI-02`):** `opendv` and `opendvctl` derive from shared `cli-spec.json` with identical flags, help texts, and canonical outputs. *(Rationale: single-binary remote portability plus in-process local execution.)*
13. **Pluggable Source Adapters (`REQ-SRC-01` / `REQ-SRC-02`):** Ingestion routes through `packages/opendv-sources` SPI (`file`, `inline`, `duckdb_attach`, `text`, `pdf`) via `source:`. *(Rationale: decouples storage/formats from modelling logic.)*
14. **Decision-Gated Ontology Seams (`REQ-ONT-01` / `REQ-ONT-02`):** `Graph A` mutations require typed `OntologyDelta` (`apply_ontology_delta`) under CAS lineage; recommenders emit proposals only. *(Rationale: protects domain definitions from unverified in-place mutations.)*

### 3.2 Implementation Freedom (The "How" — Extensible by Design) & Rationale
Provided all Immutable Outcomes and verification gates pass, contributors may freely extend:
- **Agent Harnesses & Models (`packages/opendv-agent-harness`):** Add LLM providers, CLI/SDK adapters beyond `agy`, `claude-cli`, `antigravity`, and `claude`, or refine `@toon_v1` repair loops *(avoids vendor lock-in)*.
- **Target Dialect Compilers & Exporters:** Lower `CanonicalIR` / `NoMistakeDataVault2ContractV5` to **dbt (`AutomateDV` / `datavault4dbt`)**, **Snowflake**, **BigQuery**, **Databricks**, **SQLMesh**, or **Apache Iceberg** *(decouples modelling from warehouse dialect)*.
- **Profiling & Discovery Engines:** Enhance `GraphBProfiler`, functional dependency discovery, similarity matchers, and OCR/PDF chunking *(scales from local files to cloud metadata push-down)*.
- **UI & API Transports (`apps/opendv-platform-api`, `apps/opendv-web-workbench`, `packages/opendv-protocol-ts`):** Evolve visualizations, persistence backends, and catalog integrations *(embeds cleanly into governance portals and CI/CD)*.

---

## 4. Navigation & Documentation Map

| Priority | Document | Purpose |
| :---: | :--- | :--- |
| **P0** | [`docs/00-mission-vision-and-functional-requirements.md`](docs/00-mission-vision-and-functional-requirements.md) | Mission, Functional Requirements (`FR-DOM-*`, `FR-VER-*`, `FR-INT-*`), Conformance Rubric & Traceability Matrix (`S1`–`S10`, `#30`–`#44`). |
| **P0** | [`docs/16-human-and-agent-usage-guide.md`](docs/16-human-and-agent-usage-guide.md) | Operational Playbook: `opendv` vs `opendvctl`, sources, `Mode A/B/C`, periodic waves, Steward Holds, and downstream `CanonicalIR` usage. |
| **P0** | [`docs/17-testing-scenarios-and-datasets.md`](docs/17-testing-scenarios-and-datasets.md) | Normative Test Ledger: datasets, scenarios (`S1`–`S10`), unmatched cases (`UF-1`–`UF-5`), 3-transport parity, and Goldens (`GT-01`..`GT-06`). |
| **P0** | [`docs/18-implementation-plan-platform-parity-and-architecture.md`](docs/18-implementation-plan-platform-parity-and-architecture.md) | Platform parity & architecture roadmap (`REQ-CORE/CFG/MOCK/FAIL/CLI/SRC/ONT-*`, Phases P1–P10, Issue `#63`). |
| **P0** | [`docs/19-ontology-studio-architecture.md`](docs/19-ontology-studio-architecture.md) – [`docs/24-ontology-studio-engineering-learnings.md`](docs/24-ontology-studio-engineering-learnings.md) | Ontology Studio architecture (`19`), normative spec (`20`), plan (`21`), user guide (`22`), extension guide (`23`), and hardening learnings (`24`). |
| **P0** | [`README.md`](README.md) | Quick start, monorepo layout, CLI summary, and 4-wave sample dataset guide. |
| **P1** | [`docs/DV2_AGENT_REFERENCE_GUIDE.md`](docs/DV2_AGENT_REFERENCE_GUIDE.md) | DV 2.0 layers, 6-stage hashing, `INV-01..06`, `EVO-01..08`, `AP-01..15`, and `Algorithms A.1–A.9b`. |
| **P1** | [`docs/12-agentic-pipeline-inputs-and-harness-guide.md`](docs/12-agentic-pipeline-inputs-and-harness-guide.md) | Specification for `pipeline_input.yaml`, `harness_config.yaml`, `model_overrides`, `agent_models`, and auth modes. |
| **P1** | [`docs/14-end-to-end-consistency-and-scenarios-audit.md`](docs/14-end-to-end-consistency-and-scenarios-audit.md) | 10-scenario consistency audit, unmatched ontology field routing matrix, and remediation issues (`#30`–`#44`). |
| **P2** | [`docs/01-core-modelling-spec.md`](docs/01-core-modelling-spec.md) – [`docs/15-end-to-end-pipeline-execution-and-interactive-spec.md`](docs/15-end-to-end-pipeline-execution-and-interactive-spec.md) | Modular specs covering ELM calculus, 6-Gate `axi-dv` verification, unstructured DV 2.0, observability, and API/SDK contracts. |

---

## 5. Monorepo Package Ownership Boundaries

| Package / App Path | Responsibility | Allowed Dependencies |
| :--- | :--- | :--- |
| `packages/opendv-contracts` | Language-neutral Pydantic v2 / TypeSpec DTOs, events, and error models. | Pure Python + Pydantic. |
| `packages/opendv-cli-common` | Shared CLI tree/spec loader, table rendering, TOON serialization, exit codes. | `opendv-contracts`. |
| `packages/opendv-service` | Headless application service, Ontology Studio & pipeline orchestrator (`OpenDVService`). | `opendv-core`, `opendv-axi-verifier`, `opendv-duckdb-sandbox`, `opendv-telemetry`, `opendv-agent-harness`, `opendv-contracts`, `opendv-sources`. |
| `packages/opendv-sources` | Pluggable source adapters (`file`, `inline`, `duckdb_attach`, `text`, `pdf`) and parsers. | `opendv-core`, `opendv-contracts`. |
| `packages/opendv-testkit` | Isolated test doubles, deterministic adapters, mock bundles (zero prod imports). | `opendv-service`, `opendv-core`, `opendv-contracts`. |
| `packages/opendv-core` | Pydantic v2 models (`NoMistakeDataVault2ContractV5`, `CanonicalIR`), `Algorithm A.5` hashing, ELM math. | `opendv-contracts`. |
| `packages/opendv-telemetry` | Append-only JSONL ledger, CAS, 5-Tier RBAC redactor, PII detector, Hash Pills. | `opendv-core`, `opendv-contracts`. |
| `packages/opendv-axi-verifier` | 6-Gate static/evolution verifier (`Gates 0–4`), AST linter (`A.6`), codemods (`A.1..A.9b`), `@toon_v1`. | `opendv-core`, `opendv-telemetry`, `opendv-contracts`, `sqlglot`, `networkx`. |
| `packages/opendv-duckdb-sandbox` | `GraphBProfiler`, IR lowerer, `compiler_4zone.py`, Gate 5 Shadow Sandbox (`DV-DYN-001..015`), PIT engine. | `opendv-core`, `opendv-telemetry`, `opendv-axi-verifier`, `opendv-contracts`, `opendv-sources`, `duckdb`. |
| `packages/opendv-agent-harness` | 8-stage `AgenticVaultPipeline` (`STAGE_00`–`STAGE_07`), 4 personas, 8 tools, `StewardHoldManager`, LLM adapters. | `opendv-core`, `opendv-telemetry`, `opendv-axi-verifier`, `opendv-duckdb-sandbox`, `opendv-contracts`, `opendv-sources`. |
| `packages/opendv-protocol-ts` | Zero-dependency TypeScript SDK (`@opendv/protocol-ts`), SSE client, Web Crypto Hash Pills. | Pure TypeScript / Web APIs. |
| `apps/opendv-cli` | Python standalone in-process engine CLI (`opendv`). | `opendv-service`, `opendv-cli-common`, `opendv-contracts`. |
| `apps/opendvctl` | Bun compiled platform client standalone binary (`opendvctl`). | `opendv-protocol-ts`. |
| `apps/opendv-platform-api` | FastAPI REST + W3C SSE headless server (`:8420`) and OpenAPI 3.1 exporter. | `opendv-service`, `opendv-contracts`, `fastapi`. |
| `apps/opendv-web-workbench` | React 19 + Bun Explorer, Steward & Ontology Studio Workbench (`:5173` / `/ontology`). | `opendv-protocol-ts`. |

---

## 6. Essential Conformance, Verification & Testing Commands

Run from the repository root before concluding any task:

```bash
# 1. Full normative Python suite (unit + 6-gate verifier + DuckDB sandbox + E2E)
uv run pytest -v

# 2. Cross-transport parity tests (opendv, opendvctl, REST API)
uv run pytest -m parity -v

# 3. Golden Test Suite (GT-01..GT-06)
uv run pytest -m golden -v

# 4. Architectural boundary, mock-isolation, and no-duplication guards (T13, T14)
uv run pytest tests/architecture/ -v

# 5. Deterministic pipeline input & harness pre-flight validation
uv run opendv pipeline validate \
  --input examples/pipeline_samples/global_commerce_vault/wave0_cold_start.yaml \
  --harness-config examples/pipeline_samples/global_commerce_vault/harness_config_agy.yaml \
  --json

# 6. Standalone 6-Gate symbolic verification (zero LLM credentials required)
uv run opendv verify \
  --contract examples/private_equity/dv_pe_model_contract.yaml \
  --graph-a examples/private_equity/pe_graph_a_ontology.yaml \
  --graph-b examples/private_equity/pe_graph_b_profile.json

# 7. Compile verified contract to Canonical IR & 4-Zone SQL + 4-Batch Shadow Sandbox
uv run opendv compile \
  --contract examples/private_equity/dv_pe_model_contract.yaml \
  --canonical-ir examples/private_equity/pe_canonical_ir.json

uv run opendv sandbox \
  --contract examples/private_equity/dv_pe_model_contract.yaml \
  --canonical-ir examples/private_equity/pe_canonical_ir.json \
  --query-type ADVERSARIAL_BATCHES_1_TO_4 \
  --cutoff "2026-11-15 23:59:59"

# 8. TypeScript SDK, Web Workbench, and opendvctl tests & typecheck
bun test
bun x tsc -b --pretty
```

> [!IMPORTANT]
> **Working on an Open GitHub Issue (`#30`–`#44`, `#63`–`#123`)?** Consult §7 of [`docs/00-mission-vision-and-functional-requirements.md`](docs/00-mission-vision-and-functional-requirements.md) and [`docs/17-testing-scenarios-and-datasets.md`](docs/17-testing-scenarios-and-datasets.md) to verify all target `FR-*`, `S1`–`S10`, `GT-01`..`GT-06`, and `INV-*/EVO-*/DV-*/ONT-EVO-*` invariants.

---

## 7. Ontology Studio & Platform Hardening Learnings

All work touching CAS/hashing, file locks, async SSE jobs, CLI/API/Workbench contracts, governance (`ONT-EVO-001..003`), or docs must obey [`.agents/rules/ontology-studio-learnings.md`](.agents/rules/ontology-studio-learnings.md) and [`docs/24-ontology-studio-engineering-learnings.md`](docs/24-ontology-studio-engineering-learnings.md):

1. **RFC 8785 JCS & Digest Fidelity:** Never invent placeholder digests (genesis `0.0.0` Graph A = `1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9`; PE golden = `1c100409...`). Persist exact canonical JCS bytes (`sha256(disk_bytes) == target_sha256`) and scope empty-field pruning strictly to post-baseline extensions without reordering legacy arrays (`attributes`).
2. **Windows/POSIX Locks & Binary-SID ACLs:** Use the single `StudioLock` (`L1 -> L5`) protocol without overlapping `msvcrt.locking` byte writes, key `ContextVar` lock reentrancy on `(thread_id, task_id, is_locked)` with `BaseException` (`CancelledError`) multi-lock rollback, place directory-lifecycle locks in the parent directory on Windows (never inside a directory targeted by `os.replace`), enforce Windows ACLs via Win32 `advapi32` binary SIDs (`S-1-5-18`, `S-1-5-32-544`, owner SID — never localized `icacls.exe` stdout), use staging + tombstone swaps on `force=True`, and kill full process trees on timeout.
3. **Thread-Safe Async & Gapless SSE:** Dispatch from OS worker threads exclusively via `loop.call_soon_threadsafe`, and subscribe live SSE queues *before* replaying `events.jsonl` (deduplicating by monotonic sequence ID).
4. **Cross-Surface `extra="forbid"` & OCC Parity:** Keep `cli-spec.json`, `error-codes.json`, `opendv`, `opendvctl`, FastAPI, Web Workbench (`studioFetch` with `credentials: "include"` + `X-OpenDV-Workbench: 1`), and tested doc snippets (`tests/docs/test_doc_code_snippets.py`) strictly aligned. Never auto-fetch current server `head_sha256` to populate `If-Match` over stale client deltas/canvas edits, and sanitize untrusted LLM/PDF identifiers and comments in all exporters.
5. **Fail-Closed Governance, Sticky Privacy & Portable Audits:** Enforce Path A `FIBO` `skos:exactMatch`/`skos:closeMatch >= 0.6500` (`skos:broadMatch` excluded) over `concept_name + synonyms` with mutation-triggered grandfather/waiver revocation, never derive trusted bootstrap overrides (`allow_bootstrap` / `allow_legacy_ip1_bootstrap`) from caller-controlled payload metadata, use `(?<![A-Za-z0-9])` boundaries in pseudonymizers with sticky `do_not_send_to_agent` taint, and maintain HMAC-signed `audit.jsonl` chains across bundle imports.
6. **Multi-Agent Wave Hygiene:** Never commit scratch context/critique dumps or one-off scripts into `scripts/` or `reports/`.
