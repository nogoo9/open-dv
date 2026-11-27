# `open-dv` — Root Agent & Contributor Constitution (`AGENTS.md`)

> **Mandatory Orientation for Autonomous AI Agents, Multi-Agent Swarms & Human Contributors**  
> Read this file first before planning, modifying, verifying, or extending any module in `open-dv`.

> [!IMPORTANT]
> **Clean-Sheet V2 Architecture (`2.0.0`):** See [`docs/designs/SITEMAP.md`](docs/designs/SITEMAP.md) for the active-vs-historical index across all specifications. Open-DV V2 replaces the legacy V1 multi-package monorepo (`packages/*`, `apps/opendvctl`, `/sessions`, `--mode deterministic`, `CanonicalIR`, `6-step` wizard, and `.odvstudio.zip`) with a clean 4-layer DAG in [`src/opendv/`](src/opendv/), unified `opendv` CLI, React 19 [`web/`](web/) 7-step Workbench, SQLite WAL `project.sqlite` persistence, [`schemas/v2/`](schemas/v2/), `UniversalVaultSchemaIR v2.0`, 11 dialect emitters (`Step 7`), and portable `.opendv.zip` (`core_project_state` default) archives.

---

## 1. Mission Statement

**`open-dv` is an open-source, neuro-symbolic Data Vault 2.0 (DV 2.0) modelling, evolution, and verification platform.** Its mission is to transform heterogeneous operational schemas ($N=1 \to N+1$), evolving upstream tables ($S_k(t) \to S_k(t+1)$), and unstructured enterprise documents into mathematically verified, audit-proof Data Vault 2.0 architectures (**Hubs**, **Links**, **Satellites**, **PITs**, and **Bridges**)—**without ever trusting an LLM to author raw SQL DDL or DML directly.**

---

## 2. Core Architectural Separation: Target-Agnostic IR vs. Symbolic Verification & Dialects

Maintain a strict boundary between **what `open-dv` models**, **how `open-dv` dynamically proves those models**, and **how targets emit SQL**:

### 2.1 Primary Downstream Deliverables (Target-Agnostic DV 2.0 Models)
1. **`GraphAOntology` (`graph_a.json`)** — Business ontology mutated exclusively via typed `OntologyDelta` (`36` operations) under CAS optimistic concurrency (`If-Match` / `base_sha256`), grounded with FIBO Pack v2 `fibo-core-pack.json` (`2,240` FIBO classes, `767` properties, `942` individuals across `9` modules) and `8` Asset Management starter packs.
2. **`VaultSchemaIR v2.0` (`UniversalVaultSchemaIR`, Step 4)** — Zero-SQL canonical Data Vault 2.0 intermediate representation with 100% `column_triage`, `bkcc` 3-tier business key collision codes, `hubs`, `links` (`lnk_`, `sal_`, `hal_`, `t_lnk_`), `satellites` (`sat_`, `mas_`, `esat_`, `sat_*_pii`, `sat_*_ext_vN`, AI provenance), `pits`, `bridges`, and `reference_tables`.

### 2.2 Role of the Symbolic Verifier, DuckDB Prover & Step 7 Dialect Emitters (5-Zone `Z0–Z4`)
`src/opendv/verifier/` and `src/opendv/storage/` verify `VaultSchemaIR` across **Zones `Z0–Z4`** (`bronze`, `staging`, `raw_vault`, `business_vault`, `information_mart`):
1. **3-Loop CEGAR Prover (`src/opendv/verifier/` + `src/opendv/storage/prover/`):** Executes bounded in-memory DuckDB proof batches against generated IR to catch grain duplicates, hash collisions, orphan FKs, sparse FK collapses, non-additive drift, PII taint leaks, and idempotency failures (`Δrows = 0`), returning structured `ProblemDetails` remediation patches instead of allowing LLM-written SQL.
2. **Step 7 Multi-Dialect Emitters (`11` targets):** Pure deterministic AST/template emitters in `src/opendv/storage/emitters/` lower verified `VaultSchemaIR v2.0` to `duckdb_local`, `databricks_uc`, `snowflake`, `postgres`, `bigquery`, `sqlserver_fabric`, `redshift`, `clickhouse`, `iceberg`, `parquet`, and `dbt` without requiring DuckDB in downstream production.

---

## 3. "Clear on What, Non-Prescriptive on How" Engineering Guardrails & Rationale

All contributions are governed by **Immutable Outcomes (The "What")** paired with **Implementation Freedom (The "How")** and **Design Rationale (The "Why")** (see [`docs/00-mission-vision-and-functional-requirements.md` §3.1](docs/00-mission-vision-and-functional-requirements.md#31-normative-guarantee-what-vs-implementation-flexibility-how--design-rationale-why-matrix)):

### 3.1 Immutable Outcomes (The "What" — Strictly Non-Negotiable) & Design Rationale (The "Why")
No PR, codemod, prompt change, or extension may weaken these fifteen guarantees:
1. **Zero Unverified LLM SQL Generation (`OBJ-01`):** LLM agents emit/repair **only** declarative `OntologyDelta` / `VaultSchemaIR` JSON proposals; physical SQL DDL/DML comes solely from deterministic `src/opendv/storage/` emitters. *(Rationale: eliminates SQL/delimiter/`hash_diff` hallucination in insert-only vaults.)*
2. **100% Column Triage Accountability (`FR-DOM-01` / `DV-G4-002`):** Every discovered source column across waves must appear in `column_triage_ledger` (`BUSINESS_KEY`, `FOREIGN_KEY_REF`, `DESCRIPTIVE_PAYLOAD`, `SYSTEM_SURROGATE_DROPPED`, or `OMITTED_INTENTIONALLY` with rationale). *(Rationale: prevents silent dropping of drifted columns, preserving full regulatory lineage.)*
3. **Deterministic, Injective, Collision-Free Hashing (`FR-VER-01` / `Algorithm A.5`):** All `_hk` and `hash_diff` digests use length-prefixed serialization with null tokens `'-1:^^'` (missing/unknown) and `'-2:^^'` (error/inapplicable), 3-Tier `BKCC`, and Dual Zero-Keys (`0x00..00` for `-1`, `0xFF..FF` for `-2`). *(Rationale: prevents collisions, `CONCAT_WS` null drops, and UTF-8 byte/char divergence.)*
4. **Insert-Only Raw Vault History (`FR-VER-01` / `INV-01..06`):** Raw tables (`hub_*`, `lnk_*`, `sat_*`, `mas_*`) are strictly `INSERT`-only; `UPDATE`, `DELETE`, `DROP COLUMN`, and mutable `load_end_dts` are forbidden (derive intervals via `LEAD(load_dts)` or PITs). *(Rationale: preserves non-destructive historical auditability without SCD2 rewrite locks.)*
5. **Additive-Only Schema Evolution (`FR-DOM-02..04` / `Algorithm A.7` & `EVO-01..08`):** Across $N \to N+1$ and $S_k(t) \to S_k(t+1)$ waves, deployed grains and historical `hash_diff` digests are immutable; new columns route to `sat_<base>_ext_*`, dropped columns project `CAST(NULL AS <type>)`, and sparse FKs split into companion links (`Algorithm A.4`). *(Rationale: zero downtime, zero re-hashing storms, zero broken downstream queries.)*
6. **Span-Grounded Unstructured Extraction & Steward Gating (`FR-DOM-05` / `Rules U1–U4`):** Raw blobs/vectors never enter relational Satellites (`Rule U1`); extracted claims require `(doc_id, page_number, char_start, char_end)` spans grounded into `AI_PROVENANCE_SATELLITE`, and unresolved ambiguities pause for steward review. *(Rationale: blocks document hallucinations and governs ambiguous identities.)*
7. **Privacy Containment & Zero PII/Hash Leakage (`FR-INT-02`):** Unmasked PII (`SSN`, IBAN, email, high-entropy secrets) and pre-masked or PII-tainted columns fail closed before outbound agent calls (`ColumnShapeStats.pii_suppressed`, `mode="deterministic_only"`); reports/UI payloads redact sensitive fields and emit Semantic Hash Pills. *(Rationale: enforces GDPR/CCPA & air-gapped governance without exposing raw keys.)*
8. **Cross-Surface Contract Parity:** Unified `opendv` CLI, FastAPI REST/SSE (`/api/v1/...`), `opendv mcp serve`, and the `web/` Workbench enforce identical `StrictModel` (`extra="forbid"`) schemas, RFC 8785 JCS SHA-256 digests, `34` `ErrorCode`s, and `8` `CliExitCode`s (`schemas/v2/`). *(Rationale: deterministic parity across operators, automation, and browser clients.)*
9. **Baseline & Golden Discipline:** `schemas/v2/{cli-spec,error-codes,openapi,baseline-digests}.json` and `tests/golden_v2/` baselines change only via canonical exporters/regenerators (`python -m opendv.cli.export_contracts`) with verified round-trip tests; hand-edits are forbidden. *(Rationale: prevents silent contract drift.)*
10. **Layered DAG Core (`REQ-CORE-01`):** Domain logic lives in [`src/opendv/`](src/opendv/) enforcing strict downward imports: Layer 1 (`models`) -> Layer 2 (`grounding`, `sources`, `verifier`, `storage`) -> Layer 3 (`agents`) -> Layer 4 (`service`) -> Layer 5 (`api`, `cli`). `cli` never imports `api`, `api`/`cli` never import `grounding`, and `models` imports no higher layer. *(Rationale: guarantees modularity and zero circular dependencies.)*
11. **Hardening & Fail-Loud Guarantees (`REQ-CFG-01` / `REQ-MOCK-01` / `REQ-FAIL-01`):** Zero hardcoded user paths/ports outside explicit defaults, zero production mocks or silent synthetic fallbacks, and explicit typed `ProblemDetails` (`34` `ErrorCode`s, e.g. `422 AGENT_NOT_CONFIGURED`, `409 PROJECT_JOB_ACTIVE`, `429 BUDGET_EXCEEDED`). *(Rationale: air-gapped reliability without false-positive passes.)*
12. **Single Canonical CLI (`REQ-CLI-01`):** All commands derive from `schemas/v2/cli-spec.json` (`61` subcommands across `project`, `source`, `ontology`, `model`, `vault`, `storage`, `agents`, `mcp`, `secrets`, `settings`, `doctor`, `serve`) under unified `uv run opendv ...` (supports both in-process mode and `--api-url http://127.0.0.1:8420`). *(Rationale: single authoritative command surface.)*
13. **Pluggable Source Adapters (`REQ-SRC-01`):** Ingestion routes through `src/opendv/sources/` (`ConnectorAdapter` SPI + SSRF/path-escape guards) with unified schema profiling and PII taint checks. *(Rationale: decouples heterogeneous connectors from modeling logic.)*
14. **Decision-Gated Ontology & Active-Run Locking (`REQ-ONT-01`):** `Graph A` mutations require typed `OntologyDelta` (`36` ops) under CAS SHA-256 verification, and concurrent mutating jobs per project fail closed with `409 PROJECT_JOB_ACTIVE`. *(Rationale: protects domain definitions and SQLite WAL project integrity.)*
15. **Complete Component Documentation Suite (`REQ-DOC-01`):** Every first-class component must create and maintain four living documents: a **Component README** (`docs/components/<component>/README.md`), a **User Guide** (`docs/components/<component>/user-guide.md` covering flows, parameters, concrete inputs/outputs, and examples), an **Architecture Design Doc** (`docs/designs/<component>-*.md` indexed in [`docs/designs/SITEMAP.md`](docs/designs/SITEMAP.md)), and a **Contributor Guide** (`docs/components/<component>/contributor-guide.md` covering module layout, design principles, extension recipes, and verification gates), linked from root [`README.md`](README.md) and validated by `tests/docs/`. *(Rationale: guarantees operators, external agents, and contributors can use, understand, and safely extend every component without tribal knowledge or doc drift.)*

### 3.2 Implementation Freedom (The "How" — Extensible by Design) & Rationale
Provided all Immutable Outcomes and verification gates pass, contributors may freely extend:
- **Agent Harnesses & Models ([`src/opendv/agents/`](src/opendv/agents/)):** Add LLM adapters beyond `agy`, `claude-cli`, `claude`, and `gemini`, or refine bounded CEGAR repair loops with `Budget Meter` (`Turns · Tokens · Active Seconds`) caps *(avoids vendor lock-in)*.
- **Target Dialect Emitters ([`src/opendv/storage/emitters/`](src/opendv/storage/emitters/)):** Extend or add SQL/orchestrator emitters beyond the built-in 11 targets *(decouples modelling from warehouse dialect)*.
- **Connectors & Discovery Engines ([`src/opendv/sources/`](src/opendv/sources/), [`src/opendv/grounding/`](src/opendv/grounding/)):** Add read-only database/lakehouse connectors, OCR/PDF extractors, or `KnowledgeSource` SPI providers alongside calibrated FIBO Pack v2 and YAML glossaries *(scales from local SQLite/DuckDB files to cloud catalogs)*.
- **UI & API Transports ([`src/opendv/api/`](src/opendv/api/), [`web/`](web/)):** Evolve React 19 Workbench views, `@xyflow/react` visualizations, and SQLite persistence within `DESIGN.md` WCAG 2.1 AA tokens *(embeds cleanly into governance workflows)*.

---

## 4. Navigation & Documentation Map

| Priority | Document | Purpose |
| :---: | :--- | :--- |
| **P0** | [`docs/designs/SITEMAP.md`](docs/designs/SITEMAP.md) | Canonical EGM design sitemap indexing V2 active specs, historical docs, and `.dev/docs` scratch routing. |
| **P0** | [`docs/designs/v2-clean-sheet-architecture.md`](docs/designs/v2-clean-sheet-architecture.md) | Authoritative clean-sheet V2 specification: 4-layer DAG, `project.sqlite` 5-table schema, `34` `ErrorCode`s, REST/CLI/MCP contracts. |
| **P0** | [`docs/designs/v2-workbench-ui-design.md`](docs/designs/v2-workbench-ui-design.md) & [`DESIGN.md`](DESIGN.md) | Canonical Claude-aligned WCAG 2.1 AA design system (`#faf9f5`, `#cc785c`, `#57554f`, `#8f442d`, `5-Zone` `Z0–Z4`, 4-tab topbar, 7-step UI). |
| **P0** | [`docs/29-discovery-grounding-and-evolution-scenarios.md`](docs/29-discovery-grounding-and-evolution-scenarios.md) | Canonical 9-Scenario Discovery, Grounding & Multi-Wave Matrix (`SCEN-01..09`), Path A/B1/B2 rules, privacy containment & parity rules. |
| **P0** | [`docs/30-enterprise-dv2-modeling-and-loop-engineering.md`](docs/30-enterprise-dv2-modeling-and-loop-engineering.md) | V2 Strategy Blueprint: Enterprise DV 2.0 + `BKCC` + `sal_*`, 3-Loop CEGAR, `VaultSchemaIR v2.0`, Step 7 emitters, `Guided ⇄ Observatory` UX, `.opendv.zip`. |
| **P0** | [`docs/31-v2-e2e-requirements-gaps-and-uat-spec.md`](docs/31-v2-e2e-requirements-gaps-and-uat-spec.md) | Consolidated V2 E2E spec (`OBS-01..48`, `REQ-01..68`) and `89`-item UAT checklist. |
| **P0** | [`docs/32-v2-user-experience-and-scenario-guide.md`](docs/32-v2-user-experience-and-scenario-guide.md) | Steps 1–7 User Experience Guide (`Core Project State` default `.opendv.zip`, `Budget Meter`, `Run Explorer`) & `20`-Scenario Playbook. |
| **P0** | [`docs/33-v2-phased-implementation-and-real-data-uat-plan.md`](docs/33-v2-phased-implementation-and-real-data-uat-plan.md) | Master V2 Phased TDD Implementation & Real-Data UAT Plan (`Phases V2-1..V2-9`, `DS-CEGAR-12TRAP`, `data/public_am/`). |
| **P0** | [`docs/16-cli-reference.md`](docs/16-cli-reference.md) & [`docs/16-human-and-agent-usage-guide.md`](docs/16-human-and-agent-usage-guide.md) | Auto-generated V2 CLI reference from `schemas/v2/cli-spec.json` (`16-cli-reference`) and operational playbook (`16-human-and-agent`). |
| **P0** | [`docs/components/modeling/README.md`](docs/components/modeling/README.md) · [`user-guide.md`](docs/components/modeling/user-guide.md) · [`contributor-guide.md`](docs/components/modeling/contributor-guide.md) · [`docs/designs/dv2-modeling-engine-and-governance.md`](docs/designs/dv2-modeling-engine-and-governance.md) | Reference `REQ-DOC-01` 4-doc suite for `src/opendv/modeling/` (README, User Guide, Contributor Guide, Stage & Agent Architecture Design). |
| **P0** | [`README.md`](README.md) & [`examples/v2_asset_management/README.md`](examples/v2_asset_management/README.md) | Quick start, V2 package layout, CLI summary, and Asset Management `data/public_am/` walkthrough. |
| **P1** | [`docs/DV2_AGENT_REFERENCE_GUIDE.md`](docs/DV2_AGENT_REFERENCE_GUIDE.md) | DV 2.0 layers, length-prefixed hashing, `INV-01..06`, `EVO-01..08`, `AP-01..15`, and `Algorithms A.1–A.9b`. |
| **P1** | [`docs/00-mission-vision-and-functional-requirements.md`](docs/00-mission-vision-and-functional-requirements.md) – [`docs/28-unified-ontology-pipeline-design-and-plan.md`](docs/28-unified-ontology-pipeline-design-and-plan.md) | Foundational DV 2.0 theory, conformance rubric, and historical V1 architecture records (each marked with its V2 supersession notice). |

---

## 5. V2 Package & Application Ownership Boundaries

| Path | Layer / Role | Allowed Dependencies |
| :--- | :--- | :--- |
| `src/opendv/models/` | **Layer 1:** Pydantic v2 `StrictModel` DTOs (`GraphAOntology`, `36`-op `OntologyDelta`, `GroundingRequest`/`GroundingResponse`, `GroundingReceipt`, `VaultSchemaIR v2.0`, `ProjectState`), `34` `ErrorCode`s, `8` `CliExitCode`s, RFC 8785 `json_hasher`. | Stdlib + Pydantic. |
| `src/opendv/grounding/` | **Layer 2a:** Synchronous two-module grounding core (`spi.py`, `kernel/`, `structured/`, `providers/{fibo,glossary}/`), Pack v2 (`2,240` classes, `767` properties, `942` individuals), Logit-Normal calibration. | `opendv.models` (`yaml` in `providers/glossary/loader.py` only). |
| `src/opendv/sources/` | **Layer 2b:** Pluggable `ConnectorAdapter` SPI, SQLite/DuckDB/Postgres/CSV/Parquet/PDF/Markdown/Inline ingestion, SSRF/path-escape guards, pre-masking `ColumnShapeStats` profiler, PII re-exports, `grounding_adapter.py`. | `opendv.models`, `opendv.grounding`, `pypdf`, `pyarrow`, `duckdb`. |
| `src/opendv/verifier/` | **Layer 2:** Symbolic CEGAR 3-loop prover, static/evolution AST checks, `ProblemDetails` remediations, `VaultSchemaIR` lowering & hash rules. | `opendv.models`, `sqlglot`, `networkx`. |
| `src/opendv/storage/` | **Layer 2:** `:memory:` DuckDB prover sandbox, readonly SQL guard, AST dialect emitters (`11` targets for Step 7 `v7`). | `opendv.models`, `opendv.verifier`, `duckdb`, `sqlglot`. |
| `src/opendv/modeling/` | **Layer 3:** DV 2.0 modeling engine (Mode-1 `verify_draft_vault`, Mode-2 CEGAR `model_vault`, `V7`/`V8` critics, `S6-06` `STEER`/revert, deterministic sharding; acyclic peer to `agents`). | `opendv.models`, `opendv.verifier` (zero `opendv.agents`, `opendv.grounding`, `opendv.sources`, or `opendv.storage` imports). |
| `src/opendv/agents/` | **Layer 3:** Agent CLI/SDK harnesses (`agy`, `claude-cli`, `claude`, `gemini`), Tier-2 auth probes, `opendv mcp serve`, tool router, Step 4 budget tracking. | `opendv.models`, `opendv.verifier` (acyclic peer to `modeling`). |
| `src/opendv/service/` | **Layer 4:** `OpenDVService`, SQLite WAL `project.sqlite` store, `KeyringSecretStore`, `.opendv.zip` `core_project_state` bundles, `7`-step workflows, grounding orchestrator/projection, `fibo_pack_ops.py`, SSE event bus. | `opendv.models`, `opendv.grounding`, `sources`, `verifier`, `storage`, `modeling`, `agents`. |
| `src/opendv/api/` | **Layer 5 (Adapter):** FastAPI REST + W3C SSE server (`:8420`), loopback open-access security guard (`exit 40`), static Web UI host, OpenAPI 3.1 exporter. | `opendv.models`, `opendv.service`, `fastapi`. |
| `src/opendv/cli/` | **Layer 5 (Adapter):** Unified Typer CLI (`opendv`), `opendv mcp serve` (`stdio`), `schemas/v2/` contract exporter (`python -m opendv.cli.export_contracts`), table/JSON renderers. | `opendv.models`, `opendv.service` (never imports `opendv.api`, `opendv.agents`, `opendv.sources`, `opendv.verifier`, `opendv.storage`, or `opendv.modeling`). |
| `web/` | **Frontend Workbench:** React 19 + Vite + Bun 4-tab WCAG AA Workbench served on `:8420`: `Project Workflow` (`Step 1..7`, `Guided ⇄ Observatory`, `Budget Meter`), `DV Schema Explorer`, `Run Explorer`, `Settings`. | `/api/v1/...` REST + SSE (`schemas/v2/openapi.json`). |
| `apps/opendv-website/` | **Docs Microsite:** Astro static site documenting V2 architecture, 7-step UX, and live Asset Management walkthrough. | Self-contained Astro build (`DESIGN.md` tokens). |
| `schemas/v2/` | **Canonical Contracts:** `cli-spec.json`, `error-codes.json`, `openapi.json`, `baseline-digests.json` (+ `tests/golden_v2/`). | Exported deterministically from `src/opendv/`. |

---

## 6. Essential Conformance, Verification & Testing Commands

Run from `open-dv/` before concluding any task:

```bash
# 1. Verify environment & unified CLI contracts
uv run opendv --help
uv run opendv --json doctor
uv run python -m opendv.cli.export_contracts --check
uv run python scripts/check_error_codes.py --catalog schemas/v2/error-codes.json --baselines schemas/v2/baseline-digests.json --check-ast

# 2. Architectural boundary, clone, token-budget (<5,000), doc-sync & hardening guards
uv run pytest tests/architecture/ tests/docs/ tests/parity/test_public_datasets_manifest.py -v

# 3. Full V2 Python test suite (models, grounding, sources, verifier, storage, service, api, cli, parity)
uv run pytest tests/models/ tests/grounding/ tests/sources/ tests/verifier/ tests/storage/ tests/service/ tests/api/ tests/cli/ tests/parity/ -m "not live_provider" -v

# 4. Lint & strict typecheck
uv run ruff check src tests scripts
uv run mypy src

# 5. Web Workbench (web/) & marketing site (apps/opendv-website/)
bun run --cwd web typecheck && bun run --cwd web lint && bun run --cwd web build && bun run --cwd web test
bun run --cwd apps/opendv-website build
```

---

## 7. V2 Platform & Ontology Hardening Learnings

All work touching CAS/hashing, SQLite WAL persistence, async SSE jobs, CLI/API/Workbench contracts, governance (`ONT-EVO-001..003`), or docs must obey [`.agents/rules/ontology-studio-learnings.md`](.agents/rules/ontology-studio-learnings.md):

1. **RFC 8785 JCS & Digest Fidelity:** Never invent placeholder digests (V2 `0.0.0` genesis `GraphAOntology` = `0ae856bf9fdba74e83e793ea9f74106a0c14d21d73756e1e3a12abd426d0083a`; `starter_packs` & `fibo_core_pack` pinned in `schemas/v2/baseline-digests.json`). Always serialize canonical JCS bytes via `dumps_jcs_bytes`/`sha256_jcs` and enforce `If-Match` / `base_sha256` optimistic concurrency without auto-refreshing stale client edits.
2. **SQLite WAL Locking, Binary-SID ACLs & Zip Guard:** Persist all project state atomically inside `.opendv/projects/<id>/project.sqlite` (5 tables) with single-writer per-project lock semantics returning `409 PROJECT_JOB_ACTIVE` on conflicting active jobs; enforce restricted ACLs via Win32 `advapi32` binary SIDs (POSIX `0600`/`0700`); validate `.opendv.zip` imports via `zip_guard.py` (anti-zip-slip, anti-zip-bomb, SHA-256 manifest verification) and store API secrets only via `KeyringSecretStore`.
3. **Thread-Safe Async & Gapless SSE:** Dispatch from worker threads strictly via `loop.call_soon_threadsafe`, replay `RunStreamEvent` sequence IDs before subscribing live queues, and track explicit Step 4 `Budget Meter` metrics (`Turns · Tokens · Active Seconds`, failing cleanly with `429 BUDGET_EXCEEDED`).
4. **Cross-Surface `extra="forbid"`, Loopback Open Access (`D18`), `< 5,000`-Token Ceiling (`D16`), & Agent-Only UI (`D28`):** Keep `schemas/v2/{cli-spec,error-codes,openapi}.json`, `opendv`, FastAPI (`:8420`), `web/`, and docs aligned. Loopback requests run zero-auth without token/CSRF logins; non-loopback `--host` exits `40` unless `--allow-remote-unauthenticated` is passed. Keep every module in `src/opendv/` and `web/src/` strictly `< 5,000` `cl100k_base` tokens (`tests/architecture/test_module_token_budget.py`).
5. **Fail-Closed Governance & Privacy Containment:** Enforce Path A FIBO `skos:exactMatch`/`skos:closeMatch >= 0.6500` (`skos:broadMatch` excluded) backed by server-minted `GroundingReceipt`s, PII/secret detection + `(?<![A-Za-z0-9])` pseudonymization with `ColumnShapeStats.pii_suppressed`, `<MASKED_PII>` sample masking, `sat_*_pii` isolation, `mode="deterministic_only"`, SSRF/path-escape connector guards, and untrusted identifier sanitization across all Step 7 emitters.
6. **Multi-Agent Wave Hygiene:** Never commit scratch context/critique dumps or one-off scripts into `scripts/` or `reports/`.

---

## 8. Ponytail Engineering Discipline & Attribution (MIT)

All contributors and AI agents must also follow [`.agents/rules/ponytail.md`](.agents/rules/ponytail.md) and apply the vendored Ponytail skills (`.agents/skills/ponytail*/SKILL.md`: `/ponytail`, `/ponytail-review`, `/ponytail-audit`, `/ponytail-debt`, `/ponytail-gain`, `/ponytail-help`):
- **Before you write:** Read the task and touched code; list callers, tests, fixtures, config, and exports (`scope`), and check what could break.
- **The 6-Rung Simplicity Ladder:** (1) Does it need to exist? (2) Already in this codebase? (3) Stdlib/platform feature? (4) Installed dependency? (5) One readable line? (6) Minimum complete code that works.
- **Discipline & Checks:** Deletion beats addition; grep all callers on bug fixes; preserve error handling/validation; leave a focused test/assert for non-trivial logic; mark intentional bounded deferrals as `shortcut: <limit>, <upgrade trigger>`.
- **Never cut:** Validation at trust boundaries (`OBJ-01`, `FR-DOM-*`, `INV-01..06`, `EVO-01..08`, security/privacy/RBAC, accessibility, user-requested behavior).
- **Reply Footer:** End implementation replies with 1–2 lines stating what was skipped/not checked and any risk the user must know.

> **Attribution:** Ponytail rules and skills are vendored from [`@dietrichgebert/ponytail`](https://github.com/DietrichGebert/ponytail) v5.3.3 by **Dietrich Gebert**, licensed under the **MIT License**.

---

## 9. Elephant-Goldfish Model (EGM) Spec-Driven Workflow (Apache-2.0)

`open-dv` adopts Google's **Elephant-Goldfish Model (EGM)** (`/egm-brainstorm`, `/egm-draft`, `/egm-review`, `/egm-review-loop`, `/egm-bootstrap`, `/egm-drift-report`) with fresh-context Goldfish verification:

- **Context Loader & Sitemap:** Invoke the [`open-dv-elephant`](.agents/skills/open-dv-elephant/SKILL.md) skill and read [`docs/designs/SITEMAP.md`](docs/designs/SITEMAP.md) before drafting or reviewing new specs.
- **Scratch vs. Approved Artifacts:** Keep unapproved brainstorm briefs, WIP drafts, and review/drift reports out of Git under `.dev/docs` (gitignored); promote human-approved EGM specs into `docs/designs/` and index them in [`docs/designs/SITEMAP.md`](docs/designs/SITEMAP.md).
- **Bootstrap Convention:** When running `/egm-bootstrap` on packages or subtrees that already carry curated human `README.md` docs, pass `target_filename="EGM.md"` unless intentionally updating `README.md`.

EGM artifact directory: .dev/docs
