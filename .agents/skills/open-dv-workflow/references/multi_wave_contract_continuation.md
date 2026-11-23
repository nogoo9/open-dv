# Multi-Wave Schema Evolution & `deployed_contract` Continuation Guide

Open-DV supports **continuous non-destructive evolution** of an existing Data Vault 2.0 model across Waves (`W1 -> W2 -> W3 -> ...`) by explicitly carrying forward any certified `final_contract.yaml` (`deployed_contract`) as the baseline for the next run (`D12`, `Algorithms A.7 & A.9a`).

---

## 1. Choosing Which Contract Baseline to Continue From

In **Step 6** (`Deployed Contract Baseline (D12)`) or via `opendv_workflow.py evolve-vault --baseline-mode ...`, you have **4 explicit baseline modes**:

| Mode | UI Control (`Step 6`) | CLI Helper (`evolve-vault`) | When to Use |
| :--- | :--- | :--- | :--- |
| **`latest-session`** *(Default in Wizard)* | Automatically selects `<run_id> (Latest Certified)` in **Deployed Contract Baseline (D12)** | `--baseline-mode latest-session` | Standard Wave 1 ➔ Wave 2 ➔ Wave 3 continuation inside the active Studio session (`INV-03/04` & `EVO-01..08` auto-enforced). |
| **`run-id`** *(Pick any prior run)* | Open the **Deployed Contract Baseline (D12)** dropdown and pick any certified `run_id` across the workspace | `list-contracts --limit 20` ➔ pick `run_id` ➔ `--baseline-mode run-id --baseline-run-id <run_id>` | Branching evolution from an earlier certified snapshot or continuing from another session's certified vault. |
| **`contract-file`** *(External YAML/JSON)* | Choose file under **Upload baseline deployed_contract.yaml** | `--baseline-mode contract-file --deployed-contract-file <path/to/final_contract.yaml>` | Feeding new sources/schemas against a contract exported from CI, Git, or another environment. |
| **`cold-start`** *(Reset)* | Select **`Fresh Cold-Start Vault`** in the dropdown | `--baseline-mode cold-start` | Intentionally generating a brand-new greenfield Data Vault without prior grain/hash locks. |

> [!IMPORTANT]
> When running `opendv pipeline run` directly without the helper script, pass `--initial-contract <path/to/final_contract.yaml>` (`initial_contract_content` in `POST /api/v1/pipelines/runs`) to bind the prior contract.

---

## 2. Continuous Wave Loop (Adding More Data & Schemas Safely)

1. **Attach New Wave Sources (Step 2)**: Return to **Step 2** in the same session (or create a session from your updated ontology export) and attach new tables/feeds, updated CSV schemas (with added/renamed/dropped columns), or unstructured policy PDFs/notes.
2. **Parse & Model Drift (`Steps 3–5`)**:
   - **Option A — Model First**: Parse sources, run **Agent Discovery** (`Step 4`) so `@ontology-architect` proposes additive `MINOR` concepts/attributes (while isolating destructive drops into unchecked `MAJOR · Opt-In`), review in **Step 5**, and commit `v1.1.0` / `v1.2.0`.
   - **Option B — Fast-Forward (`D8`) + Reactive Drift Sync (`D10`)**: If your baseline ontology already covers the concepts or you want Stage 02 (`Alg A.9a`) to inspect new columns automatically, jump to **Step 6** and run. If Stage 02 surfaces unmodeled columns/feeds (`UF-1..UF-5`), click **`↺ Sync Drift to Step 4`** to pre-populate Step 4 proposals cleanly.
3. **Run Step 6 Against the Picked Baseline (`evolve-vault`)**:
   - Verify **Deployed Contract Baseline** points to your intended `run_id` or uploaded `final_contract.yaml`.
   - Pick target dialects (**DuckDB** and/or **Databricks Delta**).
   - Monitor the 7 pills (`00..06`, projecting `STAGE_00..07`) and click any pill to stream stage-scoped logs live.
4. **Enforced Non-Destructive Invariants (`EVO-01..08`, `DV-DYN-014`)**:
   - **Zero `DROP COLUMN` / Zero Hub/Link Grain Mutation (`INV-03/04`)**: Existing Hub PKs, Link grains, and prior Satellite `hash_diff` column sets stay frozen.
   - **Added Columns (`EVO-01`)**: Lower into a companion drift Satellite (e.g., `sat_<entity>_ext_v2`).
   - **Renamed Columns (`EVO-03`)**: Resolved through Zone 1 `stg_*` projection aliases without mutating Zone 2 physical history.
   - **Deprecated Columns (`EVO-02`)**: Projected as `CAST(NULL AS <type>)` in Zone 1 with zero Zone 2 drops.
   - **PII Columns (`EVO-07`)**: Isolated into `sat_<entity>_pii` (`<MASKED_PII>` in agent prompts).
   - **Cross-System Aliases (`EVO-08`)**: Converged via Business Vault `sal_*` + `esat_sal_*`.
   - **Sparse Optional FKs (`Alg A.4`)**: Split into companion `lnk_*` with `WHERE <fk> IS NOT NULL`.
5. **Verify Diff in DV Schema Explorer (`/dv-schema?run=<new_id>&compare=<base_id>`)**:
   - Switch to **`100% Triage & Wave Diff`** (`View 2`) to certify `100%` column triage coverage and inspect `+Hub`, `+Sparse FK Link`, `+PII Sat`, `+Companion Drift Sat`, and `0 DROP / 0 hash_diff Mutation`.
   - Open **`Vault Rows & Safe SQL`** (`View 3`) to query populated Hub/Link/Satellite/PIT rows with read-only `SELECT` / `UNION`.

---

## 3. Handling Holds & Locks Mid-Evolution

- **`PAUSED_HOLD` (`StewardHold`, `D14`)**: Triggered by Ontology Firewall unmapped feeds (`S3`) or `Tier-U2` unstructured span verification. Review inline in Step 6 (`StewardHoldModal`), `Run Explorer`, `uv run opendv hold decide`, or `evolve-vault --run-id <rid> --hold-id <hid> --hold-action APPROVE|REJECT|ANSWER` (never bypass unverified holds silently).
- **`BudgetHold` (`D21`)**: If `max_turns`, `max_input_tokens`, `max_output_tokens`, `max_cost_usd`, or `timeout_seconds` trips, Step 6 / Step 4 surfaces **Raise budget & resume** vs **Abort**.
- **`409 SESSION_JOB_ACTIVE` (`D27`)**: Only one active job or pipeline run executes per session at a time; watch live SSE logs or explicitly click **Cancel** before starting another job in the same session.
