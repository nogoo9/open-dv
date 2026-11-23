---
name: open-dv-workflow
description: >-
  Guides users and runs end-to-end Open-DV workflows across the local Web
  Workbench (:8420), opendv CLI, and opendvctl: starting loopback services,
  configuring LLM agent harnesses, ingesting structured/unstructured sources,
  building/editing/committing Graph A ontologies (including Ask FIBO & citations),
  continuously generating and evolving 5-Zone Data Vault 2.0 schemas onto picked
  or auto-bound deployed_contract baselines, inspecting the 4-view DV Schema
  Explorer & live SSE logs, and answering questions grounded in Open-DV specs.
---

# Open-DV End-to-End Workflow & Q&A Guide

## Overview

`open-dv-workflow` covers the complete **Open-DV** lifecycle across the Claude `DESIGN.md` **Web Workbench (`http://127.0.0.1:8420`)**, **`uv run opendv`**, and **`opendvctl`**:

1. **Setup & Harness Config**: Start/probe loopback `opendv serve`, verify real LLM agents (`agy`, `claude-cli`, etc.) and `HarnessBudget`.
2. **Source Ingestion & Previews (Steps 1–3)**: Start blank/starter-pack/imported sessions, attach SQLite/DuckDB/Databricks UC/CSV/Parquet connectors, upload PDFs (`ocr_mode` `auto|force|off`) or inline notes (`do_not_send_to_agent`), parse, and inspect bounded tables/chunks.
3. **Ontology Discovery, Editing & Commit (Steps 4–5)**: Run single-pass real-agent discovery (`@term-scout -> @domain-clarifier -> @ontology-architect`), exercise **Ask FIBO & Sources** + `CitationInspectorDrawer`, answer live-cascading `Q1..Qn`, cherry-pick with explicit `MAJOR · Opt-In` isolation, optionally edit manually via `VisualEditorDrawer`, export `.yaml`/`.json`/`.ttl`, or commit atomically via OCC `If-Match`.
4. **Initial & Continuous Multi-Wave DV 2.0 Generation (Step 6)**: Pick **any prior certified `run_id`**, an **external `final_contract.yaml` (`deployed_contract`)**, the **auto-bound latest session run**, or **Fresh Cold-Start Vault** to feed new waves/schemas non-destructively into DuckDB & Databricks Delta (`INV-03/04`, `EVO-01..08`, `↺ Sync Drift to Step 4`, `StewardHold`).
5. **Explore, Monitor & Export**: Inspect the 4-view **DV Schema Explorer** (`/dv-schema`), run safe read-only `SELECT`/`UNION` queries, stream per-stage/per-step SSE logs (`00..06` / `STAGE_00..07`), and export `.odvstudio.zip` session bundles.
6. **Grounded Open-DV Q&A**: Answer product, SQL/hashing, CLI, and troubleshooting questions strictly from verified in-repo specifications.

## Dependencies

- **`uv`**: Ensure `uv` is available on `PATH`. Execute every Python script and `opendv` CLI call via `uv run` (never bare `python` or `python3`).
- **`credentials`**: Use when configuring optional SDK API keys (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`) or `DATABRICKS_HOST` Unity Catalog credentials so secrets are never echoed or persisted in plain JSON reports.
- **`chrome-devtools`** *(plus built-in `browser` subagent)*: Use when the user asks to open, verify, or drive live screens in the `:8420` Web Workbench or DV Schema Explorer.

See [UI & Dual-CLI Playbook](./references/ui_and_dual_cli_playbook.md), [Multi-Wave Contract Continuation](./references/multi_wave_contract_continuation.md), and [Q&A Knowledge Index](./references/opendv_qa_knowledge_index.md).

## Quick Start

Start the loopback service and verify health + agent harness readiness:

```powershell
# Terminal 1 (from open-dv root) — loopback open access (zero login tokens)
uv run opendv serve --host 127.0.0.1 --port 8420 --open

# Terminal 2 — run helper doctor writing JSON output to a file
uv run .agents/skills/open-dv-workflow/scripts/opendv_workflow.py doctor --output scratch/doctor.json
```

Discover continuable contracts and feed a new CSV wave onto a picked prior `run_id`:

```powershell
# 1. List Wizard sessions & prior certified runs (contracts)
uv run .agents/skills/open-dv-workflow/scripts/opendv_workflow.py list-contracts --limit 20 --output scratch/contracts.json

# 2. Add new wave data into the session & parse
uv run .agents/skills/open-dv-workflow/scripts/opendv_workflow.py ingest-parse --session-id <sid> --file path/to/wave2.csv --parse --limit 20 --output scratch/ingest.json

# 3. Continue DV 2.0 generation against the explicitly picked contract baseline
uv run .agents/skills/open-dv-workflow/scripts/opendv_workflow.py evolve-vault --session-id <sid> --baseline-mode run-id --baseline-run-id <base_run_id> --dialects duckdb,databricks --output scratch/wave2_run.json
```

## Utility Scripts

All programmatic operations live in [scripts/opendv_workflow.py](./scripts/opendv_workflow.py) (using [scripts/opendv_api_client.py](./scripts/opendv_api_client.py)). Every subcommand writes structured JSON via `--output <file>` (never dumping huge payloads to stdout) and enforces explicit `--limit` where collections are queried:

- `doctor --output <file>`: Checks `/api/v1/health`, `/api/v1/platform/capabilities`, `/api/v1/platform/config`, active agent readiness, Web UI URLs, and CLI binaries.
- `configure-harness [--provider ...] [--fast-model ...] [--reasoning-model ...] [--max-cost-usd ...] [--dry-run] --output <file>`: Validates and updates LLM provider + `HarnessBudget` (`Settings` / Step 4a parity).
- `list-contracts --limit <n> [--session-id <id>] --output <file>`: Lists Wizard sessions (`D25`) and all certified `COMPLETED` runs whose `final_contract.yaml` can be continued.
- `ingest-parse [--session-id <id> | --domain-namespace <ns> | --import-ontology <file>] [--pack-ids ...] [--connector ...] [--file ...] [--note ...] [--ocr-mode auto|force|off] [--do-not-send] [--parse] --limit <n> --output <file>`: Runs Steps 1–3 (`POST /sessions`, starter packs, connectors, uploads, inline notes, `parse`, `evidence/tables`, `evidence/chunks`).
- `discover-commit --session-id <id> [--steering ...] [--fibo-question ...] [--answer-json <file>] [--accept-proposal-id <pid>] [--include-breaking] [--commit-rationale ...] [--export-format yaml|json|ttl] --output <file>`: Runs Steps 4–5 with `mode="agent"`, optional Ask FIBO Q&A, clarification answers, `MAJOR` opt-in guard, OCC `If-Match` commit, and standalone ontology export.
- `evolve-vault --baseline-mode {latest-session|run-id|contract-file|cold-start} [--session-id <id>] [--baseline-run-id <rid>] [--deployed-contract-file <path>] [--dialects duckdb,databricks] [--run-id <rid> --hold-id <hid> --hold-action APPROVE|REJECT|ANSWER] --output <file>`: Continues DV 2.0 generation on a chosen contract (`D12`) or resolves a `PAUSED_HOLD` (`D14`).
- `explore-schema --run-id <rid> [--compare-run-id <base_rid>] [--table <schema.table>] [--sql "SELECT ..."] --limit <n> --output <file>`: Fetches 4-view DV Schema Explorer projections, wave diffs, table rows, and read-only SQL query results (`D11`).
- `export-logs-bundle --session-id <id> [--wizard-step <1..6>] [--stage-id <00..06|STAGE_00..07>] [--persona ...] --limit <n> [--export-logs-jsonl <path>] [--export-bundle-zip <path>] --output <file>`: Queries stage/step logs and exports `.jsonl` or `.odvstudio.zip`.
- `search-docs --query "<terms>" --scope {all|docs|cli|api|design} --limit <n> --output <file>`: Searches in-repo docs and specs so every Open-DV answer cites exact files and lines.

## Workflow

1. **Check Service & Harness First**: Run `doctor` (or inspect `http://127.0.0.1:8420/settings`). If the server is unreachable, ask or start `uv run opendv serve --host 127.0.0.1 --port 8420`. Never bind `--host 0.0.0.0` unless the user explicitly accepts unauthenticated remote exposure (`--allow-remote-unauthenticated`). If `active_provider_available` is `false`, guide configuration via `configure-harness` or `<AgentHarnessConfigCard />`—never substitute mock or deterministic fallbacks in UI workflows (`D28`).
2. **Choose Baseline & Feed Data (Steps 1–3)**: Start a new session, apply Asset Management starter packs, import an existing `.yaml`/`.json`/`.ttl` ontology, or resume an existing Wizard session. Attach connectors/files/notes via `ingest-parse` or the Step 2 UI, keeping sensitive data tagged `do_not_send_to_agent` (`D6`).
3. **Evolve Ontology or Fast-Forward (Steps 4–5 or `D8`)**: For new concepts/columns, run `discover-commit` (or Step 4 UI), resolve clarifications & Ask FIBO citations, inspect breaking candidates (`MAJOR · Opt-In` stays unchecked unless explicitly approved `D7`), optionally tweak in `VisualEditorDrawer`, and commit SemVer changes cleanly. For already-certified ontologies, click **`Fast-Forward to Step 6 →`**.
4. **Pick Contract Baseline & Generate/Update DV 2.0 Schema (Step 6)**: Run `list-contracts` to inspect prior certified runs. Invoke `evolve-vault` (or Step 6 UI **Deployed Contract Baseline (D12)** picker) choosing `latest-session`, `run-id`, `contract-file`, or `cold-start`. If the run hits `PAUSED_HOLD` (`StewardHold`), budget hold (`D21`), or unmodeled drift (`UF-1..5`), surface the hold/drift details to the user before resolving or syncing back to Step 4 (`D10`).
5. **Inspect Schema, Wave Diffs & Logs**: Open `/dv-schema?run=<id>&compare=<base_id>` or call `explore-schema` to verify bipartite topology, `100%` column triage, zero `hash_diff` / `DROP` violations (`DV-DYN-014`), populated row previews, safe `SELECT` queries, and DuckDB + Databricks SQL. Stream live logs via `SessionLogsInspector`, `export-logs-bundle`, or `uv run opendv ontology studio logs --session <id> --follow`.
6. **Answer Open-DV Questions**: Run `search-docs` first across `DESIGN.md`, `docs/16`–`28`, `schemas/v1/cli-spec.json`, and `schemas/v1/openapi.json`, then cite exact file paths, flags, and error codes.

## Rate Limiting

`scripts/opendv_api_client.py` coordinates all REST calls through a cross-process OS file lock (`msvcrt.locking` on Windows / `fcntl.flock` on POSIX) paired with `time.monotonic()`:
- **Loopback (`127.0.0.1` / `localhost` / `::1`)**: `5.0 req/s` default across concurrent subagents.
- **Non-loopback `--api-url`**: `1.0 req/s` default (override with `--rps`).
- **`HTTP 429` & `5xx`**: `5xx` retries with exponential backoff (`1s`, `2s`, `4s`) logged to `stderr`; persistent `429` raises `RateLimitError` naming URL and RPS limit; all `4xx` responses include full JSON/RFC 9457 Problem Details bodies.
- **Concurrency (`D27`)**: Open-DV allows only **1 active job/run per session** (`HTTP 409 SESSION_JOB_ACTIVE`). Poll active jobs to completion or cancel explicitly rather than firing concurrent jobs in the same session.

## Common Mistakes

1. **Expecting deterministic mode in the Web UI**: The Wizard/Settings UI is strictly Agent-only (`422 AGENT_NOT_CONFIGURED` if no real provider is configured, `D28`). Configure `agy` or `claude-cli` instead of looking for a UI deterministic toggle.
2. **Evolving a second wave without binding `deployed_contract`**: Passing cold-start instead of `latest-session`, `--baseline-run-id`, or `--deployed-contract-file` skips `INV-03/04` and `EVO-01..08` evolution guarantees.
3. **Stale `head_sha256` on commits (`HTTP 412`)**: Always re-read `GET /sessions/{id}/status` right before passing `If-Match` / `base_sha256` to `construct`, `delta`, or `proposals/{id}/accept`.
4. **Running mutating SQL or binding `0.0.0.0` carelessly**: `DV Schema Explorer` blocks `DROP`/`INSERT`/`UPDATE`/file scans fail-closed (`D11`), and `opendv serve --host 0.0.0.0` exits `40` unless `--allow-remote-unauthenticated` is passed (`D18`).
