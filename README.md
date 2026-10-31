# `open-dv` — Open Agentic Data Vault 2.0 Modeller (`v6.0` Unified Edition: `dv-firstmate` + `no-mistakes` + `axi-dv`)

**`open-dv`** is an open neuro-symbolic multi-agent architecture, formal contract specification, and deterministic verification suite for autonomous **Data Vault 2.0 (DV 2.0)** enterprise data warehouse modelling, pragmatic $N=1 \to N+1$ schema evolution, and unstructured/multimodal vault integration.

Data Vault 2.0 achieves 100% historical auditability, decoupled parallel ingestion, and resilience to upstream operational schema drift by decomposing enterprise data into **Hubs** (identity), **Links** (associations and transactions), and **Satellites** (insert-only temporal state), joined via deterministic cryptographic hash keys (`_hk`) and change digests (`hash_diff`). However, when autonomous LLM agents design or evolve Data Vault schemas without formal guardrails, five fatal traps—**Source-Driven Hub Proliferation**, **Surrogate Key Leakage**, **Silent Hash Collisions**, **Soft Rule Contamination**, and **Destructive Satellite `UPDATE`s**—can silently corrupt historical lineage.

`open-dv` solves this through a **Neuro-Symbolic Verification Architecture** (`dv-firstmate` + `no-mistakes` + `axi-dv`):
1. **Dual-Graph Grounding & Constrained Contract Output (Zero Direct SQL Generation by LLMs):** Semantic modelling agents receive **Graph A** (Enterprise Core Business Concept Ontology) and **Graph B** (Bronze Empirical Profile: uniqueness $U(C)$, Shannon entropy $H(C)$, null rate $N_{\text{null}}(C)$, Jaccard inclusion $I(A.x \subseteq B.y)$, and change frequency $\lambda(c)$) and emit **only** a declarative YAML/JSON contract validated against [`schemas/no-mistake-dv2-contract-v5.schema.json`](schemas/no-mistake-dv2-contract-v5.schema.json) and [`schemas/dv2-canonical-ir.schema.json`](schemas/dv2-canonical-ir.schema.json).
2. **Pragmatic Cold-Start ($N=1$) & Additive Multi-Source ($N \to N+1$) Evolution:** Locks irreversible structural anchors on Day 1 (CBC Hub naming, 3-Tier `BKCC` Authority, Nullable-FK Link Splitter $U_{nn} \ge 0.95$, Insert-Only Raw Vault) while enforcing a **Gate 4 YAGNI Write-Amplification Cost Guard** ($W_{\text{amp}} \ge 50\text{ MB/day}$, $\ge 10$ cols, $\Delta\log_{10}(\lambda) \ge 2.0$) to prevent premature micro-satellite sprawl.
3. **The 6-Gate `axi-dv` & TOON Verification Pipeline (`Gates 0–5`):** Combines deterministic cold-start codemods (`Algorithms A.1`–`A.4`, including the Gate 4 write-amplification cost guard in `A.1`), length-prefixed hash compilation (`Algorithm A.5`), `sqlglot` AST verification (`Algorithm A.6`), the $N \to N+1$ additive-evolution lock (`Algorithm A.7`), the `axi-dv` orchestrator (`Algorithm A.8`), a `networkx` bipartite DAG verifier, and a **4-Batch Adversarial DuckDB Shadow Sandbox** (the last two specified in `docs/03` without reference implementations) with 3-way triage (`auto-fix`, token-dense `reject-with-trace` in TOON format, and `ask-user`).
4. **`dv-firstmate` Multi-Agent Swarm & Unstructured DV 2.0 (`Rules U1–U4`):** Coordinates a read-only `Vault First Mate` supervisor, `scout-source` (Bronze profiler & $S_k(t) \to S_k(t+1)$ drift detector), `scout-unstructured` (span-grounded NER + 3-Tier entity resolution `Tier-U1..U3` into `hub_document` and `mas_document_<cbc>_extraction`), and isolated `ship-vault` worktree workers with guarded rebase (`ci.revalidate_repairs`).

---

## Quick-Navigation Index

| Document / Artifact | Path | Description |
| :--- | :--- | :--- |
| **Master Agent Reference Guide (`v6.0`)** | [`docs/DV2_AGENT_REFERENCE_GUIDE.md`](docs/DV2_AGENT_REFERENCE_GUIDE.md) | End-to-end unified reference guide combining layer boundaries, entity DDL/DML templates, 6-stage canonical hashing, the 5 Fatal Traps, Cold-Start ($N=1$) pragmatism, `dv-firstmate` swarm, 6-Gate `axi-dv` pipeline, Unstructured DV 2.0 (`Rules U1–U4`), ELM calculus, and reference algorithms. |
| **Complete HTML Design & Implementation Guide** | [`docs/references/No-Mistake-Agentic-Data-Vault-2.0-Complete-Design-and-Implementation-Guide.html`](docs/references/No-Mistake-Agentic-Data-Vault-2.0-Complete-Design-and-Implementation-Guide.html) | Complete standalone HTML reference guide (*No-Mistake Agentic Data Vault 2.0: Complete Design & Implementation Guide*, `v6.0` Master Edition) with full mathematical proofs, visual callouts, schemas, and reference algorithms. |
| **01 — Core Modelling Specification** | [`docs/01-core-modelling-spec.md`](docs/01-core-modelling-spec.md) | Staging, Raw Vault, Business Vault, and Information Mart boundaries; Hub, Link (`lnk_`, `t_lnk_`, `sal_`, `hal_`, `x_lnk_`), and Satellite (`sat_`, `mas_`, `esat_`, `sts_`, `rts_`, `ref_`) specifications; PIT & Bridge tables; Zero Keys & Ghost Records; 6-stage canonicalization; the 5 Fatal Traps; and Node-Level SQLGlot AST Signatures. |
| **02 — Schema Stability, Cold-Start ($N=1$) & Evolution** | [`docs/02-schema-stability-and-evolution.md`](docs/02-schema-stability-and-evolution.md) | Fundamental stability invariants (`INV-01`–`INV-06`), source schema drift playbooks (`EVO-01`–`EVO-08`), key & bi-temporal rules (`KEY-01`–`KEY-05`, `TMP-01`–`TMP-04`), 15 anti-patterns (`AP-01`–`AP-15`), Asymmetric Reversibility Matrix, 3-Tier Business Key Authority, Nullable-FK Link Splitter, Gate 4 YAGNI Cost Guard, and 6-Scenario $N \to N+1$ / $S_k(t) \to S_k(t+1)$ evolution. |
| **03 — 6-Gate Verification & `dv-firstmate` Architecture** | [`docs/03-verification-and-agentic-architecture.md`](docs/03-verification-and-agentic-architecture.md) | Mathematical bipartite graph formulation, static rules (`DV-STR-*`, `DV-COL-*`, `DV-EVO-*`), runtime SQL templates (`DV-DYN-001`–`DV-DYN-015`), the 4 Adversarial Shadow Sandbox Batches, `dv-firstmate` multi-agent swarm (`Vault First Mate`, `scout-source`, `scout-unstructured`, `ship-vault`), and the 6-Gate `axi-dv` & TOON verification matrix. |
| **04 — Unstructured & Multi-Modal DV 2.0** | [`docs/04-unstructured-and-multimodal-dv2.md`](docs/04-unstructured-and-multimodal-dv2.md) | Architectural rules `U1`–`U4` for unstructured PDFs/audio/text, `hub_document` + `sat_document_blob_meta` + `lnk_document_<cbc>` + `mas_document_<cbc>_extraction` topology, Business Vault vector separation (`bsat_document_chunk_vector`), 3-Tier Entity Resolution (`Tier-U1..U3`), Algorithm `A.9b`, and Multimodal PIT/GraphRAG SQL (`Query D.10`). |
| **05 — ELM Calculus, 3-Pass CoT & Reference Algorithms** | [`docs/05-elm-calculus-and-reference-algorithms.md`](docs/05-elm-calculus-and-reference-algorithms.md) | Formal Graph B mathematical definitions ($U(C), H(C), N_{\text{null}}(C), I(A.x \subseteq B.y), \lambda(c), W_{\text{amp}}$), Ensemble Logical Modeling (ELM) calculus, 3-Pass CoT State Machine & Production System Prompt, and complete Python/SQL implementations for **Algorithms `A.1`–`A.9` + `D.10`** (the Graph B profiler, `networkx` topology verifier, 4-Batch sandbox, and Ghost Record seeder are specified in `docs/03` without reference implementations). |
| **06 — Explainability, Observability & Visualization Architecture (`WB-00..WB-11`)** | [`docs/06-explainability-and-observability-spec.md`](docs/06-explainability-and-observability-spec.md) | Unified `VaultObservabilityEvent` JSONL telemetry architecture (`state/vault-ledger.jsonl`), 3-Layer Progressive Disclosure Standard, Semantic Hash Pills (`[HK: prefix..suffix \| BKCC:Label]`), the 12-View Explainability & Observability Workbench (`WB-00`–`WB-11`), and detailed designs for all 9 pipeline step views (`VIZ-PIPE-00`, `VIZ-STEP-01`–`08`) and 8 verification gate views (`VIZ-GATE-01`–`08`). |
| **07 — End-to-End Private Equity Modelling & Verification Demo** | [`docs/07-end-to-end-private-equity-demo.md`](docs/07-end-to-end-private-equity-demo.md) | Self-contained 7-Scene Private Equity Asset Management walkthrough (*Apex Horizon Capital*) across `INVESTRAN_FUND_ADMIN`, `DEALCLOUD_PE_CRM`, `LEGACY_GP_ADMIN`, and `OBJECT_STORE_S3` PDFs (`calpers_side_letter_fund_iv_2026.pdf`, `ic_q3_valuation_nordic_cloud.pdf`), tracing Dual-Graph grounding, 3-Tier ER, surrogate quarantine, `BKCC` collision isolation, TOON self-healing, Day-3 schema drift (`Alg A.9a`), 4-Batch DuckDB sandbox verification, and 4-Zone SQL + `Query D.10` compilation. |
| **08 — Platform MVP Monorepo (`proto` + `moon` + `uv` + `bun`) & Headless API Spec** | [`docs/08-platform-mvp-monorepo-and-api-spec.md`](docs/08-platform-mvp-monorepo-and-api-spec.md) | Complete Moonrepo (`proto` + `moon`) polyglot setup with `uv` (Python 3.12+) and `bun` (TypeScript), 9-package workspace topology (`packages/*` & `apps/*`), 5-tier RBAC data redaction, Content-Addressable Store (CAS), and the OpenAPI 3.1 REST + Server-Sent Events (SSE) NDJSON streaming contract (`/api/v1/projects`, `/runs`, `/events`, `/gates`, `/sandbox`, `/decisions`, `/artifacts`). |
| **09 — Agnostic Agent Harness & Dual-SDK Adapter Spec (`packages/opendv-agent-harness`)** | [`docs/09-agnostic-agent-harness-and-sdk-spec.md`](docs/09-agnostic-agent-harness-and-sdk-spec.md) | Vendor-neutral `VaultAgentHarness` Protocol, `AgentTaskSpec`, `AgentToolDefinition`, `SubagentSpec`, `argon`/`flash` model-tier routing, automatic dual-SDK tool bridges, concrete `AntigravityHarnessAdapter` (`google.antigravity`) & `ClaudeAgentHarnessAdapter` (`claude_agent_sdk`) reference implementations, and `VaultObservabilityEvent` stream translation. |
| **10 — Decoupled UI Workbench, Protocol SDK & Theme Engine Spec (`WB-00..WB-11`)** | [`docs/10-decoupled-ui-workbench-and-theme-spec.md`](docs/10-decoupled-ui-workbench-and-theme-spec.md) | Zero-dependency `@opendv/protocol-ts` SDK (REST, SSE, offline `.jsonl` replay, Web Crypto `Alg A.5` Hash Pills), React 19 + Vite 6 + Tailwind CSS v4 + Zustand workbench on `bun`, CSS Custom Properties Theme Engine (`Obsidian Dark`, `Daylight Paper`, `High-Contrast Audit`), Responsive 3-Layer Progressive Disclosure Shell, `WorkbenchViewPlugin` API, and all 12 Workbench Views (`WB-00`–`WB-11`). |
| **11 — Master Agent Worker Implementation & Dispatch Plan (`WP-01..WP-12`)** | [`docs/11-agent-worker-implementation-plan.md`](docs/11-agent-worker-implementation-plan.md) | Turnkey, dependency-ordered Work Breakdown Structure (`WP-01` through `WP-12`) across 7 dispatch waves specifying target branch, agent tier (`argon` vs `flash`), `blocked-by` prerequisites, exact owned files, normative spec references (`docs/01`–`docs/10`), acceptance criteria, and `moon run` / `pytest` / `bun test` verification commands. |
| **No-Mistake DV 2.0 Contract V5 Schema** | [`schemas/no-mistake-dv2-contract-v5.schema.json`](schemas/no-mistake-dv2-contract-v5.schema.json) | JSON Schema (Draft 2020-12) for `NoMistakeDataVault2ContractV5`: column triage ledger, Hubs with 3-Tier BK Authority (`key_authority_tier`, `bkcc_prefix`), Links (including `where_filter_condition` for Nullable-FK split links and `UNSTRUCTURED_EXTRACTION_LINK`), Satellites (security classification and volatility tier), Reference Tables, and `unmapped_escalations`. Entity prefixes match the Canonical IR schema. |
| **Canonical IR JSON Schema** | [`schemas/dv2-canonical-ir.schema.json`](schemas/dv2-canonical-ir.schema.json) | JSON Schema (Draft 2020-12) defining the low-level Canonical Intermediate Representation (IR) contract between semantic modelling agents and the deterministic compiler. |
| **Observability Telemetry Event JSON Schema** | [`schemas/vault-observability-event.schema.json`](schemas/vault-observability-event.schema.json) | JSON Schema (Draft 2020-12) for `VaultObservabilityEvent` records emitted over `state/vault-ledger.jsonl` to drive the 12-View Explainability & Observability Workbench (`WB-00`–`WB-11`). |
| **Annotated Model Contract Example (`YAML`)** | [`examples/dv_model_contract.yaml`](examples/dv_model_contract.yaml) | Multi-source (`SFDC_CRM`, `SAP_ERP`, `LEGACY_TICKET_DB`) declarative Data Vault 2.0 model contract for a single `hub_customer`, demonstrating Tier-1 global keys, a `BKCC`-scoped Tier-3 legacy key, the column triage ledger (including a dropped system surrogate), and source-aligned Satellites whose `PII_HIGH` column is split into `sat_customer_crm_pii` by `Algorithm A.1`. |
| **Private Equity Reference Package (`YAML` / `JSON`)** | [`examples/private_equity/dv_pe_model_contract.yaml`](examples/private_equity/dv_pe_model_contract.yaml) | Complete Private Equity Asset Management reference package: validated `NoMistakeDataVault2ContractV5` YAML ([`dv_pe_model_contract.yaml`](examples/private_equity/dv_pe_model_contract.yaml)), Canonical IR JSON ([`pe_canonical_ir.json`](examples/private_equity/pe_canonical_ir.json)), Enterprise CBC Ontology `Graph A` ([`pe_graph_a_ontology.yaml`](examples/private_equity/pe_graph_a_ontology.yaml)), and Empirical Bronze & PDF Span Profile `Graph B` ([`pe_graph_b_profile.json`](examples/private_equity/pe_graph_b_profile.json)). |
| **Executable `pytest` Specification Suite (`tests/`)** | [`tests/`](tests/) | Comprehensive, deterministic `pytest` suite (`200+` test cases) verifying Draft 2020-12 schemas, Gate 1–6 static/topological/AST verifiers, length-prefixed SHA-256 test vectors, deterministic AST codemods (`A.1`–`A.9b`), 5-tier observability redaction & PII leak detection, and 4-Zone `:memory:` DuckDB execution across all 4 adversarial shadow sandbox batches and bi-temporal queries (`D.10` & `D.10b`). |

---

## `dv-firstmate` + `axi-dv` 6-Gate Neuro-Symbolic Architecture

```mermaid
flowchart TB
    User(["Data Architect / Captain"])
    FM["Vault First Mate (Read-Only Supervisor)\n- Owns state/vault-ledger.jsonl & Graph A Ontology\n- Never writes SQL or YAML models directly"]
    Watch["Zero-Token Bash Daemons\n- bin/fm-watch.sh (Fleet Monitor)\n- bin/fm-schema-watch.sh (Drift Detector)"]

    subgraph Scouts ["1. Read-Only Discovery Scouts"]
        SS["scout-source\n- Graph B Profiler (05 §1)\n- Algorithm A.9a (Intra-Source Drift)"]
        SU["scout-unstructured\n- Span-Grounded NER (Rules U1-U4)\n- 3-Tier ER (Tier-U1..U3) & Alg A.9b"]
    end

    subgraph Worktrees ["2. Isolated Treehouse Worktree Workers"]
        SW["ship-vault\n- 3-Pass CoT State Machine\n- Emits dv_model_contract.yaml\n- Guarded Rebase (ci.revalidate_repairs)"]
    end

    subgraph AxiDV ["3. Deterministic axi-dv Compiler & 6-Gate Verifier"]
        G0["Gate 0: Pre-Flight Profile & Drift (Graph B / Alg A.9a)"]
        G1["Gate 1: JSON Schema, 3-Tier BK & Grain (v5.0 / Alg A.4)"]
        G2["Gate 2: Deterministic Hash & SQLGlot AST (Alg A.5 / A.6)"]
        G3["Gate 3: NetworkX Bipartite Topology DAG (spec only) & Evolution Lock (Alg A.7)"]
        G4["Gate 4: YAGNI Write-Amp Cost Guard >= 50 MB/day (Alg A.1)"]
        G5["Gate 5: 4-Batch DuckDB Adversarial Sandbox (spec only) + Unstructured Verifier (Alg A.9b)"]
        G0 --> G1 --> G2 --> G3 --> G4 --> G5
    end

    User -- "--intent / ask-user decisions" --> FM
    Watch -- "State change / schema drift wakeup" --> FM
    FM -- "Dispatches" --> SS & SU
    SS & SU -- "Graph B JSON + Extraction Contracts" --> FM
    FM -- "Spawns isolated worktree" --> SW
    SW <-->|"auto-fix / TOON reject-with-trace"| AxiDV
    G5 -- "All 6 Gates PASS -> PR Ready" --> FM
```

---

## Summary of Invariant, Verification, & Algorithm Taxonomy

| Namespace | ID Range | Enforcement Layer | Scope & Purpose |
| :--- | :--- | :--- | :--- |
| **Fundamental Stability Invariants** | `INV-01` – `INV-06` | Architectural Contract | Insert-only Raw Vault immutability, virtualized `LEAD(load_dts)` end-dating, Hub semantic grain invariance, Link Unit-of-Work grain invariance, strict Hub/Link/Satellite separation of concerns, and idempotent `hash_diff` delta suppression. |
| **Source Schema Drift Playbooks** | `EVO-01` – `EVO-08` & `Alg A.9a` | Schema Evolution (`fm-schema-watch.sh`) | Additive evolution patterns for `ADD_COLUMN` (`sat_*_ext_v2`), `DROP_COLUMN` (Staging `NULL` tombstone), `RENAME_COLUMN` (Staging alias), `TYPE_WIDENING`, `SEMANTIC_TYPE_CHANGE`, velocity splits, PII isolation, and $N \to N+1$ multi-source onboarding. |
| **Key Canonicalization & Authority** | `KEY-01` – `KEY-05` & `TIER_1`..`TIER_3` | Gate 1 & Gate 2 (`Alg A.4` / `Alg A.5`) | 6-stage pre-hash canonicalization, length-prefixed (`<len>:<val>`) injective encoding, `TIER_1_GLOBAL_STANDARD` / `TIER_2_ENTERPRISE_SOR` (`BKCC='GLOBAL'`) passive integration, and `TIER_3_LOCAL_SCOPED` (`BKCC='<SOURCE_SYS>'`) collision isolation. |
| **Bi-Temporal & Multi-Timeline Rules** | `TMP-01` – `TMP-04` | Ingestion & Querying | Separation of `load_dts` (assertion time), `applied_dts` (business validity time), and `event_dts`; non-spoofing of `load_dts`; early-arriving fact Hub stubbing; and bi-temporal PIT queries (`Query D.10`). |
| **Unstructured & Multimodal Rules** | `Rule U1` – `Rule U4` & `Tier-U1..U3` | `scout-unstructured` & `Alg A.9b` | Content-addressable `hub_document` (`doc_sha256`), separation of Raw Vault claims (`mas_document_<cbc>_extraction`) from Business Vault vectors (`bsat_document_chunk_vector`), character-offset provenance (`span_start`, `span_end`), and 3-Tier Entity Resolution. |
| **Anti-Pattern Catalog** | `AP-01` – `AP-15` | Pre-Deploy Lint Gates | Catalog of 15 critical Data Vault failure modes (including physical `load_end_dts` updates, Sat-to-Sat chaining, missing Effectivity Satellite driving keys, `CONCAT_WS` NULL-skipping collisions, and source-system-specific Hubs). |
| **Static Graph, Column & AST Rules** | `DV-STR-001`–`014`, `DV-COL-001`–`012`, `DV-AST-001`–`006` | Gates 1–3 (`StaticVerifier` / `Alg A.6` / `Alg A.7`) | Bipartite Hub-Link topology ($\text{outdeg}(h)=0$, Link arity $\ge 2$, single-parent Satellites), Nullable-FK Link Splitter ($U_{nn} \ge 0.95$), alphabetical `hash_diff` sorting, and SQLGlot AST immutability enforcement. |
| **YAGNI Write-Amplification Guard** | `Gate 4` (`Alg A.1`) | Compile-Time Cost Guard | Permits single-source rate-of-change Satellite splits iff $\text{cols} \ge 10$, $\Delta\log_{10}(\lambda) \ge 2.0$, and $W_{\text{amp}} \ge 50\text{ MB/day}$ (or `PII`); otherwise triggers `auto-fix: merge_satellites`. |
| **Dynamic Runtime & Adversarial Batches** | `DV-DYN-001`–`015` & `Batches 1–4` (`DV-DYN-*` templates) | Gate 5 Shadow Sandbox (`DynamicVerifier`) | Executable SQL checks and 4 Adversarial DuckDB Sandbox Batches verifying Ghost Record `INNER JOIN` completeness (Batch 1), Replay Idempotency $\Delta\text{rows}=0$ (Batch 2), Single-Attribute Delta + Boundary-Shift Collision (Batch 3), and $N=1 \to N=2$ Cross-Source Collision (Batch 4). |

---

## Running the Normative `pytest` Suite & Validating Schemas

The [CI workflow](.github/workflows/ci.yml) runs on every push and pull request. It validates all JSON Schemas against Draft 2020-12, checks example contracts and Canonical IR documents, compiles every `python` code fence in `docs/`, executes the 4-Zone DuckDB SQL bundle, and runs the full normative `pytest` test suite under [`tests/`](tests/):

| Test Module | Coverage Scope |
| :--- | :--- |
| [`tests/conftest.py`](tests/conftest.py) | Shared session/function fixtures for repository paths, JSON Schemas, YAML contracts, Canonical IR, Graph A/B profiles, extracted `docs/05`–`docs/07` Python algorithms, and ephemeral `:memory:` DuckDB connections. |
| [`tests/test_schemas_and_contracts.py`](tests/test_schemas_and_contracts.py) | Draft 2020-12 meta-validation of `schemas/*.json`, positive validation of `dv_model_contract.yaml`, `dv_pe_model_contract.yaml`, and `pe_canonical_ir.json`, parametrized negative/adversarial mutations (`DV-G1-001`, `DV-G3-002`, `DV-G3-003`, naming regexes, hash functions, and zero-key/ghost record policies), plus `docs/08`–`docs/11` platform MVP monorepo configs, RBAC redaction, SSE wire vectors, dual-SDK agent harness adapters, hash pill parity, and `WP-01`–`WP-12` dispatch wave verification. |
| [`tests/test_hashing_and_keys.py`](tests/test_hashing_and_keys.py) | Reference `compute_dv2_length_prefixed_sha256` verification, all 12 normative Private Equity SHA-256 test vectors (`docs/06` Table 2.4), delimiter-collision immunity (`['AB','C'] != ['A','BC']`), asymmetric case/whitespace normalization, `-1` null vs. `-2` empty-string sentinels, and Zero Key constants (`0x00..00` and `0xff..ff`). |
| [`tests/test_verification_gates_and_codemods.py`](tests/test_verification_gates_and_codemods.py) | Gate 2 `Algorithm A.1` ELM-to-DV2 folding & Link grain checks (Traps 1 & 2), Gate 3 `networkx.MultiDiGraph` topology verifier (`DV-G3-001`..`005`), Gate 4 `Algorithm A.4` YAGNI & 67/67 Graph B column triage completeness (Trap 3 `SPLIT_REQUIRED`), Gate 6 `Algorithm A.6` `sqlglot` AST SQL linter (`DV-G6-001`..`005`), and deterministic AST codemods (`A.1`–`A.8`) with idempotence and post-codemod gate convergence. |
| [`tests/test_observability_and_redaction.py`](tests/test_observability_and_redaction.py) | `VaultObservabilityEvent` positive & negative validation across all 8 pipeline phases (`P1_INGESTION`..`P8_OPERATIONS`) and conditional `allOf` rules, 5-tier data classification redaction (`PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `MNPI`, `RESTRICTED_PII`) with adversarial PII leak detection, `format_semantic_hash_pill`, 3-layer progressive disclosure invariants, and 12-view workbench (`WB-00`–`WB-11`) plus step/gate visualization (`VIZ-PIPE-00`, `VIZ-STEP-01`..`08`, `VIZ-GATE-01`..`08`) schema validation. |
| [`tests/test_multimodal_and_duckdb_e2e.py`](tests/test_multimodal_and_duckdb_e2e.py) | `Algorithm A.9b` 17/17 PDF character-offset grounding spans + adversarial span mutations (`DV-AUD-001`..`005`), cross-modal numerical discrepancy detection (25 bps & 50 bps fee deltas without mutating `raw_vault.sat_fund_investran`), `Algorithm A.9a` intra-source schema drift codemod, 4-Zone DuckDB warehouse execution, Zero Keys & Ghost Records, 0 orphans across 23 FK relationships, all 15 `DV-DYN-001`..`015` checks, all 4 Adversarial Shadow Sandbox Batches, and Bi-Temporal PIT Audit Queries `D.10` & `D.10b` at `2026-10-15 12:00:00` and `2026-11-20 23:59:59`. |

To install dependencies and run the test suite locally:

```bash
python3 -m pip install pyyaml jsonschema duckdb pydantic pytest networkx sqlglot
pytest -v
```

For a quick standalone check that the JSON Schemas parse cleanly:

```bash
python3 -m json.tool schemas/dv2-canonical-ir.schema.json > /dev/null
python3 -m json.tool schemas/no-mistake-dv2-contract-v5.schema.json > /dev/null
python3 -m json.tool schemas/vault-observability-event.schema.json > /dev/null
```

---

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
