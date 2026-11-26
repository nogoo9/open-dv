# Open-DV V2 Web Workbench & Unified `opendv` CLI Playbook

Use this guide whenever driving the **4-tab Claude WCAG 2.1 AA Web Workbench** (`http://127.0.0.1:8420`, `web/`), the unified **`uv run opendv`** CLI (`53` subcommands across `11` groups — running in-process or remotely via `--api-url http://127.0.0.1:8420`), or **`uv run opendv mcp serve`**.

---

## 1. Server Launch & Loopback Security Guard (`D18`)

| Mode | Command | Behavior |
| :--- | :--- | :--- |
| **Loopback Open Access (Default)** | `uv run opendv serve --host 127.0.0.1 --port 8420` | Zero login/token/CSRF prompts on loopback; `Host`/`Origin` anti-rebinding checks active. |
| **Fail-Closed Remote Guard** | `uv run opendv serve --host 0.0.0.0 --port 8420` | Fails closed with exit code **`40`** unless explicitly overridden. |
| **Explicit Unauthenticated Remote** | `uv run opendv serve --host 0.0.0.0 --port 8420 --allow-remote-unauthenticated` | Loud CLI warning + visible remote unauthenticated indicator in the Web UI. |

Web routes:
- `http://127.0.0.1:8420/projects/:id/step/1..7` — **Project Workflow** (7-Step track, `Guided ⇄ Observatory`, `Readiness`/`Revision` pills, **`Budget Meter`**)
- `http://127.0.0.1:8420/projects/:id/explorer` — **DV Schema Explorer** (4 interactive views across **5 Zones `Z0–Z4`** + `information_mart`)
- `http://127.0.0.1:8420/projects/:id/runs` — **Run Explorer** (attempt diffs, SSE logs, budget & Horizon Critic controls)
- `http://127.0.0.1:8420/settings` — **Settings** (harness provider, `CegarPolicy`, OS keyring secrets, trusted workspaces, MCP servers)

---

## 2. Configuring Harnesses, Policies & Secrets (`Settings` / CLI)

- **Web Workbench:** Open **Settings** (`/settings`) to review per-provider Tier-1/Tier-2 readiness cards and run **Test** probes, pick `harness_provider` (`agy`, `claude-cli`, `claude`, `gemini`), configure `<datalist>` model overrides, `cegar_policy`, `v7_critic_tier`, `dbt_sql_dialect`, add/revoke trusted workspace roots, manage connection profiles, and store API keys securely in user-global OS keyring (`KeyringSecretStore`).
- **Unified CLI:**
  ```powershell
  uv run opendv --json doctor --check-agents --check-mcp
  uv run opendv --json doctor --probe
  uv run opendv --json settings show
  uv run opendv settings set --harness-provider agy --cegar-policy balanced --v7-critic-tier FRONTIER_CRITIC
  uv run opendv settings models --provider claude-cli
  uv run opendv settings profile list
  uv run opendv project settings show --project <id>
  uv run opendv project settings set --project <id> --cegar-policy strict
  uv run opendv settings trust-workspace --path C:\path\to\workspace
  uv run opendv secrets set ANTHROPIC_API_KEY --prompt
  ```
- **Rule:** Both UI and production CLI paths are strictly Agent-only (`422 AGENT_NOT_CONFIGURED` if unconfigured); deterministic execution applies only to schema profiling, symbolic `V1–V6` verifiers, and Step 7 dialect emitters.
- **Remediation Actions:** Read `remediation.summary` and `remediation.actions` from `ProblemDetails` or `DoctorResponse`; execute `actor: "agent"` (`requires_tty: false`) commands directly and surface `actor: "human"` (`requires_tty: true`) commands verbatim for the human operator (never faking interactive logins or passing raw secrets on CLI args).

---

## 3. Step-by-Step Cross-Surface Parity (`Step 1..7` + Explorers + Export)

| Stage / Tab | Web Workbench (`:8420`) | Unified `uv run opendv` CLI *(append `--api-url http://127.0.0.1:8420` for server mode)* |
| :--- | :--- | :--- |
| **Step 1 — Scope & Seeds** | Create project (`namespace`, starter pack `core_spine` / `multi_asset_ibor` / ..., workspace seed, baseline) or import `.opendv.zip` | `uv run opendv project init --project <id> --namespace <ns> --starter-pack <pack>` · `project list` · `project import <zip>` |
| **Step 2 — Sources & Secrets** | Attach/reattach/upload SQLite, DuckDB, Postgres, CSV, Parquet, PDF, or inline Markdown notes (`approve-scan` on scanned pages) and configure keyring bindings | `uv run opendv source attach --project <id> --kind csv --uri <path>` · `source note --project <id> --path <md>` · `source approve-scan` · `secrets set` |
| **Step 3 — Preview & Clusters** | Inspect lazy deterministic table profiles, schema term cloud (`concept_memory`), and Flow B PDF document outlines / scanned page status | Use Workbench UI lazy preview or `POST /api/v1/sources/inspect` (headless evidence endpoints via `:8420`) |
| **Step 4 — Discovery** | Run agentic discovery, inspect FIBO matches & page spans, run Ask FIBO Q&A, resolve clarification holds | `uv run opendv ontology construct --project <id> --discovery-mode incremental_new --wait` · `ontology fibo-qa` · `ontology answer` |
| **Step 5 — Ontology Explorer** | Explore bipartite concept graph, inspect key tiers/enums/governance/anti-patterns, edit YAML/graph, validate & commit CAS `OntologyDelta`s | `uv run opendv ontology validate --project <id> --yaml <file>` · `ontology commit` · `ontology export` |
| **Step 6 — Vault Synthesis** | Launch 3-Loop CEGAR build (`fast`, `balanced`, `strict`, `symbolic_only`), switch `Guided ⇄ Observatory`, inspect `VaultSchemaIR v2.0` gates & DuckDB counterexamples, resolve Steward Holds, waivers & Horizon Critic assumptions, watch **`Budget Meter`** (`Turns · Tokens · Active Seconds`) | `uv run opendv vault build --project <id> --policy balanced --wait` · `vault verify` · `vault hold` · `vault waive` · `vault horizon` · `vault control` |
| **Step 7 — Storage & Targets** | Compile/migrate/regenerate across `11` targets (`duckdb_local`, `databricks_uc`, `snowflake`, `postgres`, `bigquery`, `sqlserver_fabric`, `redshift`, `clickhouse`, `iceberg`, `parquet`, `dbt`), run read-only SQL, export `.opendv.zip` | `uv run opendv storage compile --project <id> --target duckdb_local --verify-runtime` · `storage migrate` · `storage query --project <id> --sql "SELECT ..."` · `project export --project <id> --out bundle.opendv.zip` |
| **DV Schema Explorer & Run Explorer** | Switch topbar tabs **DV Schema Explorer** (`5-Zone Z0–Z4` `@xyflow/react` graph, catalog, DDL, matrix) and **Run Explorer** (run attempts, SSE logs, budget boost) | `uv run opendv vault logs --project <id> --run <rid> --since-seq 0 --limit 200` · `opendv mcp serve --transport stdio --project <id>` |
