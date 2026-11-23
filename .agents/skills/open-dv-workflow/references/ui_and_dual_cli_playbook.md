# Open-DV Web Workbench, `opendv`, and `opendvctl` Parity Playbook

Use this guide whenever a user works interactively in the **Claude `DESIGN.md` 4-tab Web Workbench** (`http://127.0.0.1:8420`), the local **`uv run opendv`** CLI, or the zero-token **`opendvctl`** client (`apps\opendvctl\dist\release\opendvctl-windows-x64.exe`).

---

## 1. Server Launch & Loopback Security Guard (`D18`)

| Mode | Command | Behavior |
| :--- | :--- | :--- |
| **Loopback Open Access (Default)** | `uv run opendv serve --host 127.0.0.1 --port 8420 --open` | Zero login/token/CSRF on loopback; `Host`/`Origin` anti-rebinding checks active |
| **Fail-Closed Remote Guard** | `uv run opendv serve --host 0.0.0.0 --port 8420` | Exits with code **`40`** unless explicitly overridden |
| **Explicit Unauthenticated Remote** | `uv run opendv serve --host 0.0.0.0 --port 8420 --allow-remote-unauthenticated` | Loud CLI warning + coral `RemoteUnauthenticatedBanner` in Web UI |

Web routes:
- `http://127.0.0.1:8420/` — **Ontology & Pipeline** (Unified 6-Step Wizard; `/ontology` and `/builder` alias here)
- `http://127.0.0.1:8420/dv-schema` — **DV Schema Explorer** (4 interactive views)
- `http://127.0.0.1:8420/explorer` — **Run Explorer** (`STAGE_00..07` artifacts)
- `http://127.0.0.1:8420/settings` — **Settings** (`<AgentHarnessConfigCard />` & `HarnessBudget`)

---

## 2. Configuring the Agent Harness (Step 4a / `Settings` / CLI)

- **Web Workbench**: Open **Settings** or trigger Step 4 (**Configure Agent Harness Before Discovery** on HTTP `422 AGENT_NOT_CONFIGURED`). Pick provider (`agy`, `agy-cli`, `claude-cli`, `claude-code`, `antigravity`, `claude`), set `fast`/`reasoning` models and `HarnessBudget` (`max_turns`, `max_cost_usd`, `timeout_seconds`), then click **Verify & Save** until topbar shows `● Agent Ready`.
- **Dual CLI**:
  ```powershell
  uv run opendv config show
  uv run opendv config set --provider agy --fast-model gemini-3.8-flash-high --reasoning-model gemini-4-argon-high --timeout-seconds 600
  uv run opendv config validate --provider agy
  ```
- **Rule**: The UI is strictly Agent-only (`D28`). `--mode deterministic` exists only in CLI/API for offline CI regression suites and is tagged `mode=deterministic` in logs.

---

## 3. Step-by-Step Mapping Across All 6 Wizard Steps + Explorer + Logs

| Stage / Feature | Web Workbench (`:8420`) | `uv run opendv` CLI | `opendvctl` Remote Binary (`--api-url http://127.0.0.1:8420`) |
| :--- | :--- | :--- | :--- |
| **Step 1 — Scope & Baseline** | **Option A · Start New Baseline** (`Blank · v0.0.0`, `Workspace Seed`, `Starter Packs`) ➔ **Initialize Baseline Session**, or **Option B · Evolve Existing / Import** (`.yaml`/`.json`/`.ttl`) | `uv run opendv ontology studio create --session <id> --domain-namespace <ns>` · `starter-pack apply --session <id> --pack <id>` | `opendvctl ontology studio create ...` / `opendvctl ontology studio list --api-url http://127.0.0.1:8420` |
| **Step 2 — Sources & Privacy** | Connectors (**SQLite**, **Databricks UC**, **DuckDB**, **Parquet·CSV·XLSX·JSON**), structured upload, PDF/Doc upload (`OCR Mode`: `auto`/`force`/`off`), **`do_not_send_to_agent`** toggle, **Attach Inline Note**, **`Parse Sources`** | `ontology studio attach-source --session <id> --adapter sqlite --uri <path> --table <tbl>` · `upload --session <id> --file <path>` · `text add --session <id> --text "..."` · `parse --session <id> --ocr-mode auto --wait` | Exact same subcommands + `--api-url http://127.0.0.1:8420` |
| **Step 3 — Previews & Fast-Forward** | **3a · Structured Tables** (`<= 50` rows, null%/entropy/FK hints) & **3b · Parsed Chunks** (character spans, `table_data`). Certified baselines can click **`Fast-Forward to Step 6 →`** (`D8`) | `opendv source inspect --uri <path> --adapter <type>` · `ontology studio status --session <id> --json` | `opendvctl source inspect ... --api-url http://127.0.0.1:8420` |
| **Step 4 — Agent Draft, Q&A & Citations** | **`Run Agent Discovery`** (`@term-scout -> @domain-clarifier -> @ontology-architect`), live cascading `Q1..Qn`, **Ask FIBO & Sources**, citation drawer (`CitationInspectorDrawer`), cascading checkboxes (`D5`), unchecked **`MAJOR · Opt-In`** (`D7`) | `ontology studio construct --session <id> --base-sha256 <sha> --mode agent --wait` · `fibo-qa --session <id> --question "..."` · `clarify` · `answer` · `proposal list` | Exact same subcommands + `--api-url http://127.0.0.1:8420` |
| **Step 5 — Diff, Graph & Commit** | Review diff badges & dot-grid DAG; optional **`+ Add / Edit Concept, Attribute or Link`** (`VisualEditorDrawer`, `D15`); **`Export / Promote Ontology Only`** (`.yaml`/`.json`/`.ttl`, `D9`); or commit next SemVer snapshot | `ontology studio proposal accept --session <id> --proposal-id <pid> --base-sha256 <sha>` · `export --session <id> --export-format yaml --out ont.yaml` | `opendvctl ontology studio proposal accept ...` / `export ...` |
| **Step 6 — DV 2.0 Pipeline Build** | Select **DuckDB** / **Databricks Delta**, verify **Deployed Contract Baseline (D12)**, click **`Run 5-Zone Vault Pipeline`**, inspect `00..06` clickable pills, handle **`PAUSED_HOLD`** / **`BudgetHold`** or **`↺ Sync Drift to Step 4`** (`D10`) | `pipeline run --blueprint <bp> --dialect databricks --initial-contract <contract.yaml> --stream` · `hold decide --run-id <rid> --action APPROVE` | `opendvctl run create ...` / `opendvctl hold decide ...` |
| **DV Schema Explorer** | Click **`Explore Generated DV 2.0 Schema ➔`** or **`DV Schema Explorer`** top tab: **Interactive DV 2.0 Graph**, **100% Triage & Wave Diff**, **Vault Rows & Safe SQL** (`SELECT`/`UNION` only), **5-Zone SQL (Z0–Z4) & IR** | `uv run opendv run dv-schema --run-id <rid> [--compare <base_rid>] [--table <tbl> --limit 50] [--sql "SELECT ..."]` | `opendvctl run dv-schema --run-id <rid> --api-url http://127.0.0.1:8420` |
| **Live Session & Stage Logs** | Click **View Stage Logs ↗** (Steps 2, 4, 5, 6) or any Step 6 pill (`00..06`) to open **`SessionLogsInspector`** (live `EventSource` SSE tail, stage/step/persona filters, cost meter, masked prompts, `@toon_v1`, `.jsonl` download) | `uv run opendv ontology studio logs --session <id> [--stage STAGE_04] [--step 4] [--follow]` · `bundle export --session <id> --out s.odvstudio.zip` | `opendvctl ontology studio logs ...` / `bundle export ...` |
