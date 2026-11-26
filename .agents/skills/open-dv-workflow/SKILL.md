---
name: open-dv-workflow
description: >-
  Guides users and runs end-to-end Open-DV V2 workflows across the local 7-Step
  Web Workbench (:8420), unified opendv CLI, and opendv mcp serve: starting
  loopback services, configuring harnesses/keyring secrets, attaching structured
  and PDF/note sources, constructing and committing Graph A ontologies (FIBO Q&A
  and spans), running the 3-Loop CEGAR Vault build with Budget Meter and Run
  Explorer, emitting Step 7 SQL/dbt across 11 targets, and exporting .opendv.zip.
---

# Open-DV V2 End-to-End Workflow & Q&A Guide

## Overview

`open-dv-workflow` covers the unified **Open-DV V2** lifecycle across the Claude WCAG 2.1 AA **Web Workbench (`http://127.0.0.1:8420`, `web/`)**, **`uv run opendv`** (`52` subcommands, in-process or `--api-url http://127.0.0.1:8420`), and **`opendv mcp serve`** *(for stateless `opendv model {spec, profile, verify, run}` agent workflows with zero `.opendv/` writes, see [opendv-modeling](../opendv-modeling/SKILL.md))*:

1. **Setup, Health & Keyring Secrets (`Settings`)**: Start/probe loopback `opendv serve`, verify LLM providers (`agy`, `claude`, `gemini`) via `opendv --json doctor`, and configure OS keyring secrets (`opendv secrets`) + `CegarPolicy` presets (`opendv settings`).
2. **Scope, Sources & Evidence Previews (`1. Scope & Seeds`, `2. Sources & Secrets`, `3. Preview & Clusters`)**: Initialize project-scoped state (`.opendv/projects/<id>/project.sqlite`, starter packs or workspace seeds), attach SQLite/DuckDB/Postgres/CSV/Parquet/PDF/notes with automatic PII detection and SSRF/path-escape guards (Flow B synchronous `document_map.json`, zero chunk shredding), and inspect lazy deterministic table/page previews.
3. **Discovery & Ontology Explorer (`4. Discovery`, `5. Ontology Explorer`)**: Run agentic ontology construction (`opendv ontology construct --discovery-mode incremental_new`), exercise Ask FIBO Q&A + span citation resolution (`opendv ontology fibo-qa`), resolve clarification holds (`opendv ontology answer`), explore/edit YAML & graph, and validate/commit CAS `OntologyDelta`s (`opendv ontology commit`).
4. **3-Loop CEGAR Vault Synthesis & Decisions (`6. Vault Synthesis`)**: Build `VaultSchemaIR v2.0` (`opendv vault build --policy balanced|fast|strict|symbolic_only --wait`), monitor the **`Budget Meter`** (`Turns · Tokens · Active Seconds`), inspect runs in **`Run Explorer`**, resolve Steward Holds (`opendv vault hold`), waivers (`opendv vault waive`), and Horizon Critic reviews (`opendv vault horizon`).
5. **Storage, Query & Export (`7. Storage & Targets`, `DV Schema Explorer`)**: Compile & execute deterministic `5-Zone (Z0–Z4)` DDL/DML across `11` targets (`duckdb_local`, `databricks_uc`, `snowflake`, `postgres`, `bigquery`, `sqlserver_fabric`, `redshift`, `clickhouse`, `iceberg`, `parquet`, `dbt`), run read-only `opendv storage query`, and export/import portable `.opendv.zip` (`core_project_state` default).
6. **Grounded Open-DV Q&A**: Answer architecture, DV 2.0 modeling/hashing, CLI, and troubleshooting questions strictly from verified in-repo specifications.

## Dependencies

- **`uv`**: Ensure `uv` is available on `PATH`. Execute every Python script and `opendv` CLI call via `uv run` (never bare `python` or `python3`).
- **`KeyringSecretStore` / env**: Configure provider API keys or catalog credentials via `uv run opendv secrets set <NAME> --prompt` (secrets are never written to `project.sqlite` or `.opendv.zip`).
- **`chrome-devtools`** *(plus built-in `browser` subagent)*: Use when verifying screens in `:8420` (`Project Workflow`, `DV Schema Explorer`, `Run Explorer`, `Settings`).

See [Web Workbench & Unified CLI Playbook](./references/ui_and_dual_cli_playbook.md), [Multi-Wave Evolution Continuation](./references/multi_wave_contract_continuation.md), and [Q&A Knowledge Index](./references/opendv_qa_knowledge_index.md).

## Quick Start

Start the loopback service and verify health + agent readiness:

```powershell
# Terminal 1 (from open-dv root) — loopback open access (zero login tokens)
uv run opendv serve --host 127.0.0.1 --port 8420

# Terminal 2 — run helper doctor writing JSON output to a file
uv run .agents/skills/open-dv-workflow/scripts/opendv_workflow.py doctor --output scratch/doctor.json
```

Initialize a project, attach a new wave source, and evolve `VaultSchemaIR v2.0`:

```powershell
# 1. List projects & latest Vault IR readiness
uv run .agents/skills/open-dv-workflow/scripts/opendv_workflow.py list-contracts --limit 20 --output scratch/projects.json

# 2. Attach new wave data into the project & inspect profiles
uv run .agents/skills/open-dv-workflow/scripts/opendv_workflow.py ingest-parse --project-id am_demo --file data/public_am/sec_13f_holdings.csv --limit 20 --output scratch/ingest.json

# 3. Run CEGAR Vault build and compile Step 7 storage DDL
uv run .agents/skills/open-dv-workflow/scripts/opendv_workflow.py evolve-vault --project-id am_demo --policy balanced --targets duckdb_local,dbt --output scratch/vault_run.json
```

## Utility Scripts

Helper scripts live in [scripts/opendv_workflow.py](./scripts/opendv_workflow.py) (using [scripts/opendv_api_client.py](./scripts/opendv_api_client.py) and [scripts/opendv_stage_commands.py](./scripts/opendv_stage_commands.py)). Commands write JSON via `--output <file>` and support `--limit`:

- `doctor --output <file>`: Checks `/api/v1/health`, `/api/v1/settings`, `/api/v1/agents`, and `/api/v1/sources/adapters`.
- `configure-harness [--provider agy|claude|gemini] [--cegar-policy ...] [--v7-critic-tier ...] [--dbt-dialect ...] --output <file>`: Validates and updates `PUT /api/v1/settings`.
- `list-contracts --limit <n> [--project-id <id>] --output <file>`: Lists projects (`GET /api/v1/projects`) and run summaries (`GET /api/v1/projects/{id}/runs`).
- `ingest-parse [--project-id <id>] [--domain-namespace <ns>] [--starter-pack-id <id>] [--kind ...] [--uri ...] [--file ...] [--note ...] --limit <n> --output <file>`: Initializes/attaches sources (`POST /api/v1/projects`, `POST .../sources`, `POST .../sources/upload`) and reads `/evidence/tables/index` & `/evidence/documents/map`.
- `discover-commit --project-id <id> [--discovery-mode incremental_new|incremental_reverify|full_rebuild] [--hint ...] [--fibo-question ...] [--answer-json <file>] [--rationale ...] [--export-format yaml|json|ttl] --output <file>`: Runs `POST .../ontology/construct`, optional `fibo-qa` / `answers`, CAS commit, and ontology export.
- `evolve-vault --project-id <id> [--policy fast|balanced|strict|symbolic_only] [--targets duckdb_local,postgres,...] [--run-id <rid> --answers-json <file>] --output <file>`: Launches `POST .../runs`, polls run state, submits Steward decisions (`POST .../decisions`), and compiles `POST .../storage/compile`.
- `explore-schema --project-id <id> [--sql "SELECT ..."] [--limit <n>] --output <file>`: Reads `GET .../vault/ir`, `GET .../storage/targets`, and executes guarded read-only `POST .../storage/query`.
- `export-logs-bundle --project-id <id> [--run-id <rid>] [--since-seq <n>] --limit <n> [--export-bundle-zip <path>] [--full-demo] --output <file>`: Fetches run logs (`GET .../runs/{rid}/logs`) and downloads portable `.opendv.zip` (`core_project_state` default, `--full-demo` optional).
- `search-docs --query "<terms>" --scope {all|docs|cli|api|design} --limit <n> --output <file>`: Searches V2 docs and schemas so every answer cites exact files and lines.

## Workflow & Guardrails

1. **Service & Secrets First**: Ensure `:8420` is reachable (`doctor`) and real provider auth is active; never bind `0.0.0.0` without `--allow-remote-unauthenticated` (`D18`, exit code `40`), and never expect a `--mode deterministic` UI toggle (`422 AGENT_NOT_CONFIGURED`).
2. **One Mutating Job Per Project (`409 PROJECT_JOB_ACTIVE`)**: SQLite WAL serializes active ontology/vault runs per project; inspect `Run Explorer` (`GET .../runs/{rid}/logs`) or send `POST .../controls` (`pause`/`cancel`) before starting another run.
3. **Zero-SQL `VaultSchemaIR v2.0` vs. Step 7**: Keep modeling additive across waves (`INV-01..06`, `EVO-01..08`), verify via `POST .../vault/verify`, and materialize DDL/SQL only via Step 7 (`POST .../storage/compile|migrate|regenerate`).
4. **Grounded Q&A**: Run `search-docs` across `DESIGN.md`, `docs/designs/`, `docs/29..32`, `docs/16-cli-reference.md`, and `schemas/v2/` before answering technical questions.
