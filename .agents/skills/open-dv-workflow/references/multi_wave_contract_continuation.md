# Multi-Wave Schema Evolution & Project Continuation Guide (V2)

Open-DV V2 enforces **continuous, additive, non-destructive Data Vault 2.0 evolution** across waves (`Wave 1 -> Wave 2 -> ...`) scoped strictly to a project's SQLite WAL history (`.opendv/projects/<project_id>/project.sqlite`).

---

## 1. Per-Project Evolution Lock & Baseline Continuity

Unlike V1's global run registry, V2 isolates every ontology revision, evidence fingerprint, `VaultSchemaIR v2.0` (`head_ir`), and wave evolution lock strictly inside its **Project**:

| Workflow Stage | Web Workbench (`Step 1..7`) | Unified `uv run opendv` CLI | Evolution Guarantee |
| :--- | :--- | :--- | :--- |
| **1. Scope & Seeds** | Create new project, seed starter pack (`core_spine`, `multi_asset_ibor`, etc.), or import `.opendv.zip` | `uv run opendv project init --project <id> --namespace <ns> [--starter-pack <pack>]` | Initializes isolated SQLite WAL `project.sqlite` with zero cross-project leakage (`409 PROJECT_JOB_ACTIVE` lock). |
| **2. Sources & Secrets** | Attach/reattach/upload new CSV, Parquet, SQLite, DuckDB, Postgres, PDF, or Markdown notes and bind keyring credentials | `uv run opendv source attach --project <id> --kind <kind> --uri <path>` | Synchronous schema profiling, PII masking (`<MASKED_PII>`), and Flow B `document_map` indexing (`0s` LLM wait). |
| **3. Preview & Clusters** | Inspect lazy deterministic table profiles, schema term cloud, and Flow B page outlines (`✦ NEW` vs. `↻ UPDATED` vs. `✓ Modeled`) | *(Lazy Workbench evidence views / `source inspect`)* | Flags new and modified wave evidence deterministically against prior committed ontology snapshots (`0` LLM tokens). |
| **4. Discovery** | Step 4 **`Discovery`** selector: `incremental_new`, `incremental_reverify`, `full_rebuild` | `uv run opendv ontology construct --project <id> --discovery-mode incremental_new --wait` | Preserves existing committed hubs (`✦ NEW` vs. `⊕ EXTENDS`) and surfaces FIBO citations + clarification holds. |
| **5. Ontology Explorer** | Step 5 **`Ontology Explorer`**: inspect bipartite graph, anti-patterns, deployed locks, edit YAML, and commit | `uv run opendv ontology validate --project <id> --yaml <file>` · `ontology commit` | Enforces CAS `base_revision_id` OCC (`409 REVISION_CONFLICT`) and blocks breaking deletes against deployed vaults. |
| **6. Vault Synthesis** | Step 6 **`Vault Synthesis`** (`Guided ⇄ Observatory` + **`Budget Meter`**) + **`Run Explorer`** | `uv run opendv vault build --project <id> --policy balanced --wait` · `vault verify` · `vault hold` · `vault horizon` | Auto-binds prior project `VaultSchemaIR` locks; splits added fields to `sat_<base>_ext_*`, PII to `sat_*_pii`, sparse FKs to companion links, and aliases to `sal_*`; pauses cleanly for Steward decisions (`--answers-json`) or budget extension (`+turns` / `+tokens`). |
| **7. Storage & Targets** | Step 7 **`Storage & Targets`**: **Incremental Migration** vs. **Full Regeneration** across `11` targets | `uv run opendv storage compile --project <id> --target duckdb_local --verify-runtime` · `storage migrate` · `storage regenerate` | Incremental migrations leave deployed Raw Vault history untouched while Full Regeneration reproduces an identical catalog cleanly from `VaultSchemaIR v2.0`. |

---

## 2. Enforced Non-Destructive Invariants (`INV-01..06`, `EVO-01..08`)

- **Zero `DROP COLUMN` / Zero Historical Hash Mutation (`INV-03/04`):** Existing Hub business keys, Link grains, and Satellite `hash_diff` column sets remain immutable across waves.
- **Additive Evolution Routing:**
  - **Added Columns (`EVO-01`):** Emitted into companion extension Satellites (`sat_<entity>_ext_*`).
  - **Deprecated Columns (`EVO-02`):** Projected as `CAST(NULL AS <type>)` in staging views (`Z1`) without dropping physical Raw Vault columns (`Z2`).
  - **Renamed Columns (`EVO-03`):** Mapped cleanly in `Z1` staging views while preserving historical `_hk` / `hash_diff`.
  - **PII Columns (`EVO-07`):** Segregated into `sat_<entity>_pii` with `<MASKED_PII>` agent telemetry redaction.
  - **Cross-System Aliases (`EVO-08`):** Converged via Business Vault `sal_*` + `esat_sal_*`.
  - **Sparse Optional FKs (`Alg A.4`):** Isolated into companion links filtered cleanly by structured `PredicateAST` (`IS_NOT_NULL`).

---

## 3. Portable Bundles & Concurrency Locks

- **Default Bundle (`core_project_state`):** `uv run opendv project export --project <id> --out <id>.core.opendv.zip` exports project metadata, ontology revisions, `VaultSchemaIR v2.0`, and audit hashes without leaking OS keyring secrets. Pass `--full-demo` to embed runtime fixtures/DuckDB outputs (`ZIP_STORED`).
- **Active Run Guard (`409 PROJECT_JOB_ACTIVE`):** Only one mutating job (`ontology construct` or `vault build`) runs per project at a time. Inspect live progress in **Run Explorer** (`uv run opendv vault logs --project <id> --run <rid>`) or issue `vault control --action pause|raise-budget` before starting another run.
