# Open-DV V2 Q&A Knowledge Index & Authoritative Grounding Map

When answering questions about **Open-DV V2**, run `opendv_workflow.py search-docs --query "<terms>" --limit 8 --output <out.json>` first and cite the exact files, schemas, or line numbers rather than answering from memory.

---

## 1. Quick Topic-to-Source Index (in `open-dv`)

| Question Domain | Authoritative Repo Source(s) |
| :--- | :--- |
| **Clean-Sheet V2 Architecture, 4-Layer DAG, `project.sqlite`, `ErrorCode`s** | `docs/designs/v2-clean-sheet-architecture.md`, `src/opendv/`, `schemas/v2/error-codes.json` |
| **Visual Design Tokens, WCAG 2.1 AA Contrast, 4-Tab Chrome, 7-Step UI** | `DESIGN.md`, `docs/designs/v2-workbench-ui-design.md`, `web/src/styles/tokens.css`, `apps/opendv-website/src/styles/tokens.css` |
| **Unified `opendv` CLI (`53` subcommands across `11` groups) & REST/SSE OpenAPI Contract** | `docs/16-cli-reference.md`, `schemas/v2/cli-spec.json`, `schemas/v2/openapi.json` |
| **Discovery & Grounding Scenarios (`SCEN-01..09`) & PII Isolation** | `docs/29-discovery-grounding-and-evolution-scenarios.md`, `.agents/rules/discovery-and-grounding-scenarios.md` |
| **Enterprise DV 2.0, 3-Loop CEGAR, `VaultSchemaIR v2.0`, Horizon Critic** | `docs/30-enterprise-dv2-modeling-and-loop-engineering.md`, `docs/31-v2-e2e-requirements-gaps-and-uat-spec.md` |
| **Steps 1–7 User Walkthrough & 20-Scenario Playbook** | `docs/32-v2-user-experience-and-scenario-guide.md`, `examples/v2_asset_management/README.md` |
| **DV 2.0 Math, Length-Prefixed Hashing (`Alg A.5`), `INV/EVO/AP` Rules** | `docs/DV2_AGENT_REFERENCE_GUIDE.md`, `docs/01-core-modelling-spec.md`, `docs/02-schema-stability-and-evolution.md`, `docs/05-elm-calculus-and-reference-algorithms.md` |
| **Hardening Invariants (JCS, SQLite WAL, ACLs, Zip Guard, SSE, Token Budget)** | `AGENTS.md`, `CLAUDE.md`, `.agents/rules/ontology-studio-learnings.md` |
| **EGM Spec Index & Historical V1 Supersession Ledger** | `docs/designs/SITEMAP.md` |

---

## 2. Core Open-DV V2 Architectural Facts & Frequently Asked Answers

- **7-Step Workbench Track (`Project Workflow` -> `/projects/:id/step/1..7`):**
  1. **Step 1 (`Workspace`)**: Create/seed/import project state (`.opendv/projects/<id>/project.sqlite`, `starter_packs`, `.opendv.zip`).
  2. **Step 2 (`Sources`)**: Attach/reattach/upload SQLite, DuckDB, Postgres, CSV, Parquet, PDF, Markdown, or inline notes (`ConnectorAdapter` SPI + SSRF/PII checks + Flow B `document_map.json`).
  3. **Step 3 (`Ontology`)**: Run agentic ontology discovery, Ask FIBO Q&A (`fibo-core-pack.json` `2,206` concepts, `>= 0.65` Path A exact/close match vs. Path B1 empirical/doc span), edit/validate/commit CAS `OntologyDelta`s (`34` ops).
  4. **Step 4 (`Vault Build`)**: Execute the 3-Loop CEGAR `VaultSchemaIR v2.0` builder with live **`Budget Meter`** (`Turns · Tokens · Active Seconds`) and **`Guided ⇄ Observatory`** toggle.
  5. **Step 5 (`Verify & Review`)**: Run symbolic verification gates + DuckDB prover checks, inspect `ProblemDetails` remediations, and inspect Horizon Critic risk findings.
  6. **Step 6 (`Decisions`)**: Resolve Steward Holds, submit `AntiPattern`/quarantine waivers with explicit rationale, and seal the readiness brief.
  7. **Step 7 (`Storage & Query`)**: Compile/migrate/regenerate across `11` targets (`duckdb_local`, `databricks_uc`, `snowflake`, `postgres`, `bigquery`, `sqlserver_fabric`, `redshift`, `clickhouse`, `iceberg`, `parquet`, `dbt`), execute guarded read-only queries, and export `.opendv.zip`.
- **Top-Level Navigation (4 Tabs):** `Project Workflow`, `DV Schema Explorer` (`/projects/:id/explorer` — `5-Zone Z0–Z4` + `information_mart`), `Run Explorer` (`/projects/:id/runs` — attempt diffs, budget controls, SSE logs), and `Settings` (`/settings` — Tier-1/Tier-2 harness readiness [`readiness.json`, `opendv doctor [--probe]`], `CegarPolicy`, user-global `KeyringSecretStore` [`service_name="opendv"`, `secret_keys.json`], workspace trust, MCP servers).
- **Zero-SQL `VaultSchemaIR v2.0` (`OBJ-01`):** LLM agents never author SQL DDL/DML; physical SQL and orchestrator artifacts emit deterministically in `src/opendv/storage/emitters/`.
- **Canonical Hashing (`Algorithm A.5`) & Dual Zero Keys:** Hubs seed Unknown (`'-1'` -> `'0'*64`) and Not-Applicable (`'-2'` -> `'f'*64`), Satellites seed Ghost Records (`'0'*64 @ 1900-01-01T00:00:00Z`), and multi-part keys concatenate as `<octet_len>:<val>` with null token `'-1:^^'`.
- **Common Error Codes (`schemas/v2/error-codes.json`, `34` closed codes):**
  - Exit **`40`** on `opendv serve`: Loopback-only security guard (`D18`) — pass `--host 127.0.0.1` or explicit `--allow-remote-unauthenticated`.
  - HTTP **`422 AGENT_NOT_CONFIGURED`**: No real LLM harness (`agy`, `claude-cli`, `claude`, `gemini`) configured/authenticated (`D28` — zero UI deterministic mock fallback); synchronous errors, `ProviderReadinessRecord`, and `DoctorResponse` attach structured `remediation` (`actor: "agent"` vs `actor: "human"`, `requires_tty: true`).
  - HTTP **`409 PROJECT_JOB_ACTIVE`**: Another active ontology/vault run holds the project lock.
  - HTTP **`409 REVISION_CONFLICT`**: Stale `base_revision_id` OCC commit; inspect server ontology before rebasing.
  - HTTP **`202 UNCERTIFIED_DRAFT`** (CLI exit **`5`** `HOLD_OR_UNCERTIFIED`): Async run stopped at `HarnessBudget` or `K_max` ceiling with an uncertified draft; inspect in `Run Explorer` or raise limits via `opendv ontology control --action raise-budget` / `opendv vault control --action raise-budget`.
