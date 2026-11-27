# Open-DV Modeling Engine Contributor Guide (`src/opendv/modeling/`)

> **User Guide:** [`user-guide.md`](user-guide.md) · **Architecture & Governance Design:** [`dv2-modeling-engine-and-governance.md`](../../designs/dv2-modeling-engine-and-governance.md) · **Repo Constitution:** [`AGENTS.md`](../../../AGENTS.md)

This guide is for contributors modifying or extending **`src/opendv/modeling/`** (Layer 3) and its surrounding verifier, service, and CLI integration points.

## Table of Contents

1. [Architecture Position & Layer-3 Import Contract](#1-architecture-position--layer-3-import-contract)
2. [Module-by-Module Map](#2-module-by-module-map)
3. [Key Design Principles & Non-Negotiable Invariants](#3-key-design-principles--non-negotiable-invariants)
4. [Common Extension Recipes](#4-common-extension-recipes)
5. [Local Verification & PR Checklist](#5-local-verification--pr-checklist)

---

## 1. Architecture Position & Layer-3 Import Contract

`open-dv` enforces an acyclic **5-layer package DAG** checked statically by [`tests/architecture/test_import_contracts.py`](../../../tests/architecture/test_import_contracts.py):

```text
Layer 5: cli / api          (Typer CLI, FastAPI SSE server; NEVER imports modeling directly)
   │
Layer 4: service            (Calls model_vault/verify_draft_vault & injects concrete ports)
   │
Layer 3: modeling | agents  (Siblings: ZERO modeling -> agents or agents -> modeling imports)
   │
Layer 2: sources | verifier | storage   (modeling imports ONLY verifier from Layer 2)
   │
Layer 1: models             (StrictModel DTOs, VaultSchemaIR v2.0, policy specs, PII tokenizer)
```

### Exact Import Rules for `src/opendv/modeling/`

- **Allowed internal imports:** `opendv.models.*` (Layer 1) and `opendv.verifier.*` (Layer 2) only.
- **Banned imports inside `src/opendv/modeling/` (`V2_LAYER_BANS`):**
  `opendv.sources`, `opendv.storage`, `opendv.agents`, `opendv.service`, `opendv.api`, `opendv.cli`, `duckdb`, `sqlglot`, `fastapi`, `starlette`, `typer`, `mcp`, `rdflib`, `opendv_*`.
- **Reverse ban (must remain bidirectional):** `opendv.modeling` is banned inside `opendv.models`, `opendv.sources`, `opendv.verifier`, `opendv.storage`, sibling `opendv.agents`, `opendv.api`, and `opendv.cli`. Only Layer 4 (`opendv.service`) and tests invoke `opendv.modeling`.

### Dependency Injection via `ports.py`

Because `src/opendv/modeling/` cannot import `opendv.agents`, `opendv.storage`, or `opendv.service`, all runtime collaborators cross cleanly through Layer-2/3 `Protocol` ports injected by Layer 4 ([`_run_adapters.py`](../../../src/opendv/service/workflows/_run_adapters.py), [`modeling_stateless.py`](../../../src/opendv/service/workflows/modeling_stateless.py), and [`vault_svc.py`](../../../src/opendv/service/workflows/vault_svc.py)):

| Port (`Protocol` / Callable) | Defined In | Purpose | Injected Concrete Adapter |
| :--- | :--- | :--- | :--- |
| **`ModelingHarnessPort`** | `modeling/ports.py` | Executes single zero-tool turns (`@ship-vault`, `V7`, `V8`) | `wrap_modeling_harness(...)` (`_run_adapters.py`) |
| **`ModelingPackLoader`** | `modeling/ports.py` | Loads agent `SKILL.md` instructions & `dv2-modeling-guide.md` | `ServiceModelingPackLoader` (`_run_adapters.py`) |
| **`ModelingEventSink`** | `modeling/ports.py` | Emits monotonic Step-6 `RunStreamEvent` SSE frames | Managed run JSONL/DB sink or `None` in stateless CLI |
| **`ModelingControlReader`** | `modeling/ports.py` | Polls `pause`, `hint`, `accept-best`, `raise-budget`, `horizon-review`, `revert` signals | Managed `RunStore` signal reader |
| **`ModelingCheckpointStore`** | `modeling/ports.py` | Saves `RunCheckpointRecord` (`S6-02`/`S6-05`/`S6-06`) & `AttemptSnapshotSpec` | Managed `RunStore` checkpoint repository |
| **`ModelingScoutDispatcher`** | `modeling/ports.py` | Optional read-only scout interface on turn ports | Not used in Step-6 zero-tool prompts (`role_kind="none"`) |
| **`IRRuntimeProver`** | `verifier/ports.py` | Executes Gate `V6` DuckDB `schema_proof` & `real_data_proof` | `DuckDbProverAdapter` (`storage/prover/duckdb_prover.py`) |

---

## 2. Module-by-Module Map

### Inside `src/opendv/modeling/` (12 Modules)

Every module below is kept strictly under `5,000` `cl100k_base` tokens (`D16`):

| Module | Primary Responsibility | Key Entry Points & Symbols |
| :--- | :--- | :--- |
| [`__init__.py`](../../../src/opendv/modeling/__init__.py) | Public Layer-3 facade exports | Re-exports `model_vault`, `verify_draft_vault`, `run_v8_horizon_review`, DTOs, and port contracts |
| [`models.py`](../../../src/opendv/modeling/models.py) | Component request/result/resume DTOs & state helpers | `VerifyDraftRequest`, `VerifyDraftResult`, `DVModelingRequest`, `DVModelingResult`, `WaveEvolutionInput`, `ModelingResumeInput`, `CandidateState`, `is_better_candidate`, `resolve_next_action`, `attach_wave_delta`, `preserve_v7_waivers` |
| [`ports.py`](../../../src/opendv/modeling/ports.py) | Decoupled `Protocol` ports, turn DTOs & model routing | `ModelingHarnessPort`, `ModelingPackLoader`, `ModelingTurnRequest`, `ModelingTurnResult`, `MODEL_TIER_DEFAULTS`, `MODEL_ENV_OVERRIDES`, `resolve_model_for_agent`, `enforce_v7_tier_lock` |
| [`token_utils.py`](../../../src/opendv/modeling/token_utils.py) | Token counting & untrusted hint fencing | `DEFAULT_CONTEXT_SOFT_CEILING_TOKENS` (`40_000`), `count_tokens` (`cl100k_base`), `compact_json_dumps`, `format_untrusted_business_context` |
| [`prompts.py`](../../../src/opendv/modeling/prompts.py) | Prompt builders, guide validation, fragility & compaction | `MAX_MODELING_GUIDE_TOKENS` (`2_500`), `MAX_BUSINESS_CONTEXT_HINTS_TOKENS` (`1_500`), `scan_fragility_surface`, `build_compact_7tuple_summary`, `build_ship_vault_prompt`, `build_v7_prompt`, `build_v8_prompt`, `resolve_modeling_guide` |
| [`verify_mode.py`](../../../src/opendv/modeling/verify_mode.py) | Mode-1 zero-LLM deterministic verification pipeline | `verify_draft_vault`, `validate_source_rows_pii_tokenized`, `_merge_persisted_waivers`, `_build_remediation_hints`, `_suggest_fix` |
| [`_sharding.py`](../../../src/opendv/modeling/_sharding.py) | Deterministic wide-catalog clustering, parallel sharding & merge | `preallocate_canonical_hubs`, `resolve_cluster_assignments`, `scale_shard_budget`, `preserve_post_merge_draft`, `run_ship_vault_synthesis` |
| [`critics.py`](../../../src/opendv/modeling/critics.py) | Gate `V7` & Gate `V8` LLM critic orchestration + waiver auditing | `run_v7_semantic_critic`, `run_v8_horizon_critic`, `audit_v6b_justifications`, `evaluate_v7_and_v6b`, `score_v7_step` |
| [`horizon.py`](../../../src/opendv/modeling/horizon.py) | `S6-06` `V8` Horizon outer loop, `STEER`/`REVERT` & deduplication | `run_v8_outer_loop`, `resolve_s606_resume_action`, `record_horizon_assumptions_from_answers`, `deduplicate_horizon_questions`, `make_post_steer_exhausted_question` |
| [`engine.py`](../../../src/opendv/modeling/engine.py) | Mode-2 CEGAR loop (`S6-01`..`S6-07`) tying all stages together | `model_vault`, `_execute_revert`, `_seed_resumed`, `_finalize` |
| [`_engine_helpers.py`](../../../src/opendv/modeling/_engine_helpers.py) | Shared Mode-2 `RunCtx`, control polling, checkpoints & sealing | `RunCtx`, `init_run_ctx`, `poll_control_signals`, `save_checkpoint`, `checkpoint_result`, `seal_result` |
| [`review.py`](../../../src/opendv/modeling/review.py) | Standalone in-place Gate `V8` upgrade over certified waves | `run_v8_horizon_review` |

### Related Cross-Layer Touchpoints

| Layer | File(s) | Role Relative to `opendv.modeling` |
| :--- | :--- | :--- |
| **Layer 1 (`models`)** | `modeling_preferences.py`, `policy.py`, `vault_ir.py`, `vault_ir_certified.py`, `diagnostics.py`, `events.py`, `pii_tokenizer.py` | Owns `ModelingPreferences`, `CegarPolicySpec`, energy formulas, `DraftVaultIR` (`INV-05`, `OMITTED_INTENTIONALLY >= 15` chars), `CertifiedVaultIR`, `RealDataRuleResult`, `RunCheckpointRecord`, and shared HMAC `<PII:16hex>` utilities. |
| **Layer 2 (`verifier`)** | `hint_survey.py`, `l1_codemods.py`, `v2_5_gates.py`, `v6b_antipatterns.py` | Pure-Python deterministic algorithms: `survey_modeling_hints` (`P1–P4`), `Alg A.1–A.9b`, `V1–V5` static/evolution gates, `V6` orchestration, and policy-driven `V6b` anti-patterns. |
| **Layer 2 (`sources` / `storage`)** | `sources/profiler.py`, `sources/containment.py`, `storage/prover/duckdb_prover.py` | Bounded sample capture (`proving_sample_rows`), FK->PK hash overlaps, `:memory:` DuckDB `schema_proof` + `real_data_proof`, type-preserving PII value projector, and exception redaction. |
| **Layer 3 (`agents/packs`)** | `packs/dv2-modeling-guide.md`, `packs/{ship-vault,dv-semantic-critic,dv-horizon-critic}/SKILL.md` | Prompt packs & shared modeling guide loaded by `ModelingPackLoader` (never imported as Python). |
| **Layer 4 (`service`)** | `service/workflows/modeling_stateless.py`, `_modeling_stateless_helpers.py`, `_run_adapters.py`, `vault_svc.py` | Translates CLI/API inputs into `VerifyDraftRequest`/`DVModelingRequest`, enforces symlink/`O_NOFOLLOW` containment & guide trust, and wires `project.sqlite` persistence or zero-disk stateless flows. |
| **Layer 5 (`cli` / `api`)** | `cli/commands_model.py`, `cli/export_contracts.py` | Exposes `opendv model {spec, profile, verify, run}` through Layer 4 and exports `schemas/v2/` + `web/src/api/types.generated.ts`. |

---

## 3. Key Design Principles & Non-Negotiable Invariants

1. **Modular Token Budget (`D16` `< 5,000` tokens/module):**
   Every `.py` file under `src/opendv/` and `.ts`/`.tsx` file under `web/src/` (except `types.generated.ts`) must stay strictly under **`5,000` `cl100k_base` tokens** (enforced by [`tests/architecture/test_module_token_budget.py`](../../../tests/architecture/test_module_token_budget.py)). Split cohesive helpers into private sibling modules (such as `_engine_helpers.py` and `_sharding.py`) instead of growing monoliths.
2. **Strict Schema Contracts (`StrictModel` & Codegen Sync):**
   Use `StrictModel` (`extra="forbid"`) and add `Field(description=...)` on every public DTO field (`DVModelingRequest`, `VerifyDraftRequest`, `ModelingPreferences`, `DraftVaultIR`, `ModelSpecResp`). Any schema or CLI flag change requires regenerating `schemas/v2/` and frontend types via `uv run python -m opendv.cli.export_contracts`.
3. **Zero Unverified LLM SQL & Zero-Tool Mode-2 Turns:**
   Neither Mode 1 (`verify_draft_vault`) nor Mode 2 (`@ship-vault`, `V7`, `V8`) prompts ever emit SQL DDL/DML (`OBJ-01`). All internal Mode-2 turns execute with `allowed_tools: []`; all table stats, ambiguity ratios, `PkContainmentOverlap`, and counterexample diagnostics are pre-computed deterministically before the turn.
4. **Fail-Closed Security & PII Hygiene:**
   - Always route raw row samples through `pii_tokenizer.py` (`<PII:16hex>`) before entering `verify_draft_vault` or `model_vault`; `validate_source_rows_pii_tokenized` rejects untokenized values or stripped `<MASKED_PII>` strings.
   - Mode-2 `business_context_hints` (`<= 1,500` tokens) are sanitized via `format_untrusted_business_context` inside `<untrusted_business_context>` delimiters beneath `V1–V6 > ModelingPreferences > hints/guide`. Mode 1 (`verify_draft_vault`) rejects `business_context_hints` and `guide_markdown` outright.
   - Workspace `--guide` / `--hints-file` paths go through `reject_symlinks_on_path_and_ancestors`, `validate_workspace_containment`, and `safe_open_no_follow` (`O_NOFOLLOW`) in `commands_model.py`, plus SHA-256 `TrustedWorkspaceSpec` verification.
5. **Monotonic Convergence, Anti-Forgery & Safe Fallback:**
   - **Waiver integrity:** `@ship-vault` cannot forge human approvals or `V7` sign-off (`engine.py` resets `actor="agent"` and `reviewed_by_v7=False` on synthesis outputs; only `audit_v6b_justifications` flips `reviewed_by_v7=True` from `V7`'s `accepted_waiver_ids`).
   - **Candidate selection & cycle halt:** `is_better_candidate` prioritizes `(v1_v6_fail_count == 0, lowest energy_e)` while `seen_digests` halts oscillation immediately.
   - **`S6-06 STEER` & `revert` safety:** Entering `STEER` snapshots `pre_steer_fallback_*`, clears `best=None` (`skip_synthesis=False`), caps post-steer `V8` re-scoring to `<= 1` pass, ratchets on `Bucket A` regressions, and supports zero-synthesis `"revert"` restoration.
6. **Bounded Sharding & Compaction (`40,000`-token ceiling):**
   - `@ship-vault` shards deterministically when `> 40` tables, `> 150` columns, or `> 40,000` input tokens (`resolve_cluster_assignments`), pinning PKs/FKs/BK candidates on wide-table slices and charging `asyncio.gather` parallel shards as `1` logical turn.
   - `V7` and `V8` stay single-pass global and switch cleanly to `wide_catalog_summary` compaction above `40,000` tokens (`prompts.py`), retaining full `col_name:type` detail on flagged/cross-source/`>= 8`-col entities.
7. **Provider-Agnostic Model Routing:**
   Never hardcode LLM model IDs in business logic. `resolve_model_for_agent` resolves `(provider, ModelTier)` in precedence order: `agent_override > tier_override > env_override (OPENDV_*_MODEL) > MODEL_TIER_DEFAULTS`, while `enforce_v7_tier_lock` locks `V7` to `FRONTIER_CRITIC` under `strict`.

---

## 4. Common Extension Recipes

### Adding a New Tier-1 `ModelingPreferences` Switch
1. Add the typed `Literal` / field with default and `Field(description=...)` to `ModelingPreferences` in `src/opendv/models/modeling_preferences.py`.
2. Wire its deterministic effect into Layer 2 (`src/opendv/verifier/l1_codemods.py` / `v6b_antipatterns.py`), CLI parsing (`parse_modeling_pref_updates` in `commands_model.py` if special parsing beyond string/bool/int is needed), and Mode-2 packs (`dv2-modeling-guide.md`) if relevant.
3. Update `user-guide.md` and `tests/modeling/test_governance_preferences.py`, then regenerate `schemas/v2/` and `web/src/api/types.generated.ts` via `export_contracts`.

### Adding a Deterministic Rule or `RemediationHint`
1. Implement the rule in Layer 2 (`src/opendv/verifier/v2_5_gates.py`, `v6b_antipatterns.py`, or `duckdb_prover.py`) emitting a `VerifierDiagnostic` with the appropriate `GateId`, severity, and `waivable` flag.
2. Add an actionable remediation template in `_suggest_fix` (`src/opendv/modeling/verify_mode.py`) so Mode-1 agents and `--json` callers receive clear `REPAIR_DRAFT_IR` instructions.
3. Add a unit test under `tests/modeling/` or `tests/verifier/`.

### Editing Mode-2 Agent Prompts or Pack Instructions
1. Edit the pack (`src/opendv/agents/packs/{ship-vault,dv-semantic-critic,dv-horizon-critic}/SKILL.md` or `dv2-modeling-guide.md`) while keeping `allowed_tools: []` and `dv2-modeling-guide.md <= 2,500` tokens.
2. Edit prompt payload assembly in `src/opendv/modeling/prompts.py` (making sure `_finalize_critic` compaction stays `<= 40,000` tokens on wide catalogs).
3. Test with `tests/modeling/test_prompts_and_compaction.py` and verify token budgets.

### Adding a CLI Flag or Option to `opendv model`
1. Add the option in `src/opendv/cli/commands_model.py` and keep orchestration in Layer 4 (`src/opendv/service/workflows/modeling_stateless.py`)—never import `opendv.modeling` in CLI modules.
2. Regenerate CLI contracts (`uv run python -m opendv.cli.export_contracts`) and update `docs/components/modeling/user-guide.md` / `.agents/skills/opendv-modeling/SKILL.md` so `tests/docs/` passes.

---

## 5. Local Verification & PR Checklist

Run these checks from the repository root before opening or merging a PR touching `src/opendv/modeling/`:

```bash
# 1. Targeted modeling & verifier test suites
uv run pytest tests/modeling/ tests/verifier/ tests/cli/ -q

# 2. Architecture boundary, token ceiling (< 5,000 tok), mock, & LLM seam guards
uv run pytest tests/architecture/ -q

# 3. Doc-sync, relative markdown links, & CLI snippet validation
uv run pytest tests/docs/ -q

# 4. Schema / OpenAPI / CLI contract parity & closed ErrorCode AST check
uv run python -m opendv.cli.export_contracts --check
uv run python scripts/check_error_codes.py --catalog schemas/v2/error-codes.json --baselines schemas/v2/baseline-digests.json --check-ast

# 5. Static lint & type checking
uv run ruff check src tests scripts
uv run mypy src
```

> **Note on `test_mock_guards.py` & Live UAT:** Tests in `tests/architecture/test_mock_guards.py` enforce strict test-double boundaries—prefer real `:memory:` `DuckDbProverAdapter` execution over mocked provers whenever testing `verify_draft_vault` or `model_vault`. For end-to-end provider changes, run `pytest -m live_provider` against a real configured harness (`agy`, `claude-cli`, or `gemini`) in addition to the offline suite above.
