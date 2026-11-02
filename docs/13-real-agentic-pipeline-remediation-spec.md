# Open-DV Real Agentic Pipeline Remediation Specification (`docs/13`)

**Status:** Engineering Remediation Blueprint  
**Target Repository:** `open-dv` (`packages/*`, `apps/*`, `examples/*`)  
**Objective:** Convert `open-dv` from a deterministic/simulated agentic facade into a **100% genuine, discovery-first, closed-loop LLM-driven neuro-symbolic Data Vault 2.0 application**.

---

## 1. Executive Summary & Architectural Diagnosis

An exhaustive code audit of `open-dv` revealed a fundamental architectural split:
1. **What is genuinely implemented today:**
   - **Pydantic V2 Metamodels (`packages/opendv-core`)**: `NoMistakeDataVault2ContractV5`, `CanonicalIR`, `GraphAOntology`, `GraphBProfile`, `VaultObservabilityEvent`, and length-prefixed SHA-256 (`compute_dv2_length_prefixed_sha256`).
   - **Static Verification & AST Codemods (`packages/opendv-axi-verifier`)**: All 40 static rules (`DV-G1` through `DV-G7`), `@toon_v1` token-oriented failure traces, and Algorithms `A.1`–`A.9b`.
   - **Empirical SQL Profiling (`packages/opendv-duckdb-sandbox/src/opendv_duckdb_sandbox/graph_b_profiler.py`)**: Real DuckDB cardinality, null-ratio, Shannon entropy, and Jaccard FK inclusion profiling (`GraphBProfiler`).
   - **Zone 0 DDL & Schema Diffing (`packages/opendv-duckdb-sandbox`, `packages/opendv-agent-harness/src/opendv_agent_harness/pipeline.py`)**: Generic Hub/Link/Satellite DDL + Zero-Key/Ghost-Record generation and cumulative wave-over-wave `SchemaDeltaSummary` computation.

2. **What is currently simulated, mocked, hardcoded, or bypassed:**
   - **Orchestration Facade (`pipeline.py`)**: `AgenticVaultPipeline` calls `await self.harness.run_task(...)` at Stages `00`, `01`, `02`, `04`, and `05`, **completely discards the LLM's return value**, and instead builds the contract in deterministic Python (`_synthesize_cumulative_contract`) using pre-baked answers supplied in `pipeline_input.yaml`.
   - **Fake Fault Injection & Fake Self-Healing (`pipeline.py`)**: Stage `04` intentionally deletes the last entry of `column_triage_ledger` (`inject_adversarial_fault_on_attempt_1 = True`) so Attempt 1 of Stage `05` fails with `DV-G4-002`, and Stage `05` "repairs" it on Attempt 2 by restoring `copy.deepcopy(candidate_contract)` while ignoring the `@ship-vault` LLM call.
   - **Pre-Baked Semantic Answers in Input YAML (`pipeline.py` & `pipeline_input.yaml`)**: `PipelineSourceFeedInput` and `PipelineUnstructuredDocInput` require the user to manually provide `business_key_columns`, `surrogate_key_columns`, `pii_columns`, `foreign_key_mappings`, `cbc_mapping_hints`, and `extracted_candidates` with hardcoded character offsets and confidence scores before running the pipeline.
   - **Stubbed Domain Tool Catalog (`tools/catalog.py`)**: The 6 domain tools exposed to agents (`profile_source_feed`, `extract_pdf_spans`, `compile_canonical_ir`, `run_axi_dv_gates`, `apply_ast_codemod`, `execute_duckdb_sandbox`) do not invoke the real verifiers/compilers; they return hardcoded strings, YAML comments, or mock booleans.
   - **Silent Mock SDK Fallbacks (`deps.py` & `deterministic_adapter.py`)**: When `provider` is set to `"antigravity"` or `"claude"` and the external SDK is absent, the platform silently injects `build_deterministic_agy_sdk_shim()` or `build_deterministic_claude_sdk_shim()`, returning fake strings and fake token counts (`150` in / `75` out).
   - **Hardcoded Private Equity Specialization in Stage 06 Sandbox (`compiler_4zone.py`, `sandbox_engine.py`, `adversarial_batches.py`, `dynamic_checks.py`)**: Zones 1–4 SQL compilation, the 4 adversarial batches, and the 15 `DV-DYN-001..015` checks are hardcoded to the Private Equity tables (`raw_vault.hub_fund`, `raw_vault.hub_limited_partner`, `raw_vault.sat_lp_core`, etc.). In `pipeline.py:L2884`, Stage `06` runs the PE benchmark instead of verifying the wave's actual `CanonicalIR` and CSV data.

---

## 2. Complete Inventory of Simulated, Mocked & Hardcoded Behaviors

| ID | Component / File | Lines | Current Simulated / Mocked Behavior | Required Real Behavior |
| :--- | :--- | :--- | :--- | :--- |
| **SIM-01** | `packages/opendv-agent-harness/src/opendv_agent_harness/pipeline.py` | `L1761-L1794` | `STAGE_00_ORCHESTRATION` calls `vfm_res = await self.harness.run_task(vfm_spec)` and only reads `vfm_res.model_used`; wave planning is static. | `@vault-first-mate` inspects source files, dependencies, and ontology to emit a validated `WaveExecutionPlan` (`output_schema`). |
| **SIM-02** | `packages/opendv-agent-harness/src/opendv_agent_harness/pipeline.py` | `L2064-L2069` | `STAGE_01_SCOUT_SOURCE` runs `await self.harness.run_task(scout_src_spec)` after Python has already built the profile; return value is not even assigned. | `@scout-source` receives `GraphBProfiler` empirical metrics (`U`, `U_nn`, `H`, `N_null`, Jaccard FK scores) + sample rows + `GraphAOntology` and returns structured `SourceFeedSemanticAnnotation` (inferred BKs, SKs, FKs, PII classification, BKCC namespace, CBC concept mappings). |
| **SIM-03** | `packages/opendv-agent-harness/src/opendv_agent_harness/pipeline.py` | `L2189-L2318` | `STAGE_02_SCOUT_UNSTRUCTURED` reads pre-populated `doc.extracted_candidates` from YAML, then calls `await self.harness.run_task(scout_unstr_spec)` and discards the result. | `@scout-unstructured` reads the raw document text + `GraphAOntology` + active Hub business keys in DuckDB, extracts entity mentions, verbatim quotes, and attributes via `output_schema`, resolves exact `char_start`/`char_end` offsets against the raw document, and computes real Hub match confidence (`Tier-U1..U3`). |
| **SIM-04** | `packages/opendv-agent-harness/src/opendv_agent_harness/pipeline.py` | `L2391-L2511`, `L3291-L3777` | `STAGE_03_SHIP_VAULT_PASS1` & `STAGE_04_SHIP_VAULT_PASS2` synthesize the entire contract in Python (`_synthesize_cumulative_contract`), then call `await self.harness.run_task(ship_spec)` and discard the output. | `@ship-vault` performs real Pass 1 (`ColumnTriageLedger` structured output) and Pass 2 (`NoMistakeDataVault2ContractV5` structured output / incremental wave delta merge) grounded on Stage `01`/`02` discoveries and `GraphAOntology`. |
| **SIM-05** | `packages/opendv-agent-harness/src/opendv_agent_harness/pipeline.py` | `L1393`, `L2475-L2489`, `L2619-L2633` | `inject_adversarial_fault_on_attempt_1` deletes `column_triage_ledger[-1]` on Attempt 1 so `DV-G4-002` triggers; `STAGE_05` calls `run_task(repair_spec)`, discards the output, and restores `copy.deepcopy(candidate_contract)` on Attempt 2. | Disable artificial fault injection by default (`inject_adversarial_fault_on_attempt_1: bool = False`). When `run_axi_dv_verification_pipeline` returns `REJECT-WITH-TRACE`, pass the `@toon_v1` trace and failing contract to `@ship-vault` (with `apply_ast_codemod` and `run_axi_dv_gates` tools + `output_schema=NoMistakeDataVault2ContractV5`) and assign the **LLM's repaired contract** to `current_attempt_contract`. |
| **SIM-06** | `packages/opendv-agent-harness/src/opendv_agent_harness/pipeline.py` & `examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml` | `pipeline.py:L311-L358`, `pipeline_input.yaml:L15-L133` | `PipelineSourceFeedInput` requires pre-populated `business_key_columns`, `foreign_key_mappings`, `pii_columns`, etc. `PipelineUnstructuredDocInput` requires pre-populated `extracted_candidates`. | Make all semantic hint fields optional overrides (`default_factory=list` / `None`). Allow `pipeline_input.yaml` to specify only `feed_id`, `source_system`, `table_name`, `file_path`, `timestamp_column` for structured feeds, and `doc_id`, `doc_type`, `file_path` for unstructured docs. |
| **SIM-07** | `packages/opendv-agent-harness/src/opendv_agent_harness/tools/catalog.py` | `L62-L350` | All 6 domain tools (`_handle_profile_source_feed`, `_handle_extract_pdf_spans`, `_handle_compile_canonical_ir`, `_handle_run_axi_dv_gates`, `_handle_apply_ast_codemod`, `_handle_execute_duckdb_sandbox`) are mock stubs returning placeholder dicts/strings. | Wire each tool handler directly to `GraphBProfiler`, `alg_a9b_unstructured`, `lower_contract_to_canonical_ir`, `compile_canonical_ir_to_4zone_sql`, `run_axi_dv_verification_pipeline`, `alg_a1..a9a` codemods, and `DuckDbShadowSandbox`. |
| **SIM-08** | `apps/opendv-platform-api/src/opendv_platform_api/deps.py` & `packages/opendv-agent-harness/src/opendv_agent_harness/adapters/deterministic_adapter.py` | `deps.py:L1325-L1341`, `deterministic_adapter.py:L165-L431` | `_resolve_sdk_shim_if_needed()` silently falls back to `build_deterministic_agy_sdk_shim()` / `build_deterministic_claude_sdk_shim()` when SDKs are not installed. `DeterministicHarnessAdapter` calls `model_validate({})` and swallows validation errors. | Fail closed when a real LLM provider (`antigravity` or `claude`) is selected without valid SDK/API credentials (unless `allow_mock_sdk_fallback=True` is explicitly set in unit tests), and implement direct `google-genai` / `anthropic` SDK execution paths. |
| **SIM-09** | `packages/opendv-duckdb-sandbox/src/opendv_duckdb_sandbox/compiler_4zone.py` | `L1820-L1856` | Checks `is_pe_domain = {"hub_fund", "hub_limited_partner", "hub_portfolio_company"}.issubset(hub_names)`. For non-PE schemas, Zone 0 is compiled generically, but Zones 1, 2, 3, and 4 emit only comments or PE-specific SQL. | Implement generic AST/SQL compilers for Zones 1, 2, 3, and 4 driven solely by any `CanonicalIR` and source table/CSV bindings. |
| **SIM-10** | `packages/opendv-duckdb-sandbox/src/opendv_duckdb_sandbox/` (`sandbox_engine.py`, `adversarial_batches.py`, `dynamic_checks.py`) & `pipeline.py` | `pipeline.py:L2884`, `sandbox_engine.py:L144-L183`, `adversarial_batches.py:L79-L438`, `dynamic_checks.py:L16-L130` | `STAGE_06` calls `sandbox.run_full_verification()` with `source=None`, running the hardcoded PE IR and hardcoded `HUB_PK_MAP` / `LINK_PK_MAP` / `SATELLITE_PK_MAP` / `FK_EDGES` checks instead of the wave's `CanonicalIR` and CSV data. | Derive PK maps and FK edges dynamically from `ir: CanonicalIR`, load the wave's actual CSV/Parquet files into DuckDB `bronze.*` tables, and synthesize Batches 1–4 and `DV-DYN-001..015` dynamically against the wave's compiled tables. |
| **SIM-11** | `apps/opendv-platform-api/src/opendv_platform_api/deps.py` | `L1343-L1408`, `L1640-L1714` | `create_run()` falls back to `_init_run_artifacts()` which emits 9 canned PE demo events (`build_pe_canonical_events()`) when `pipeline_input_path` is omitted. | Construct a `PipelineInputSpec` dynamically from the project's registered `bronze_sources` and `graph_a_ontology_path` and execute `AgenticVaultPipeline` for every real run. |

---

## 3. Work Packages for Implementation

Execute the following 6 Work Packages (`WP-01` through `WP-06`) in order. Every package preserves backward compatibility with existing Pydantic schemas (`NoMistakeDataVault2ContractV5`, `CanonicalIR`, `VaultObservabilityEvent`) while replacing simulated logic with real execution.

---

### WP-01: Wire Real Neuro-Symbolic Engines into Domain Tool Catalog (`tools/catalog.py`)

**Target File:** `packages/opendv-agent-harness/src/opendv_agent_harness/tools/catalog.py`

#### 1.1 `_handle_profile_source_feed` (`L62-L121`)
- **Current Bug:** Only iterates over `args.sample_rows` in Python memory and ignores `args.table_or_path` when it points to a real CSV, Parquet, or DuckDB table.
- **Required Fix:**
  1. Check if `Path(args.table_or_path).exists()`. If so, load the CSV/Parquet into an ephemeral DuckDB `:memory:` connection (or query the table via `GraphBProfiler` from `opendv_duckdb_sandbox.graph_b_profiler`).
  2. Compute exact column profiles (`cardinality_ratio_u`, `non_null_uniqueness_u_nn`, `shannon_entropy_h`, `null_ratio_n_null`, inferred SQL type, and Jaccard inclusion against any candidate Hub tables) plus up to 5 sample values per column.
  3. Fall back to `args.sample_rows` only if no file/table path exists (for unit test isolation).

#### 1.2 `_handle_extract_pdf_spans` (`L124-L189`)
- **Current Bug:** Performs a naive `quote in args.document_text` check without resolving exact character spans, normalized fuzzy matches, or `alg_a9b_unstructured` confidence tiers.
- **Required Fix:**
  1. For each candidate span in `args.candidate_spans`, locate all exact or whitespace-normalized occurrences in `args.document_text` (`re.finditer`), recording verified `char_start`, `char_end`, and `byte_start`/`byte_end`.
  2. Invoke `verify_and_route_unstructured_extraction` (`opendv_axi_verifier.codemods.alg_a9b_unstructured`) to compute the deterministic `Tier-U1` ($\ge 0.90$), `Tier-U2` ($[0.75, 0.90)$), or `Tier-U3` ($< 0.75$) routing decision and return structured `verified_spans`.

#### 1.3 `_handle_compile_canonical_ir` (`L192-L239`)
- **Current Bug:** Writes a stub dictionary `{"ir_version": "1.0.0", "hubs": hubs, ...}` and a 4-line SQL string `"CREATE SCHEMA IF NOT EXISTS raw_vault;\nCREATE SCHEMA IF NOT EXISTS business_vault;\n"`.
- **Required Fix:**
  1. Validate `args.contract_payload` into `NoMistakeDataVault2ContractV5`.
  2. Call `canonical_ir = lower_contract_to_canonical_ir(contract)` (`opendv_duckdb_sandbox.ir_lowerer`).
  3. Call `bundle = compile_canonical_ir_to_4zone_sql(canonical_ir)` (`opendv_duckdb_sandbox.compiler_4zone`).
  4. Return `canonical_ir.model_dump(mode="json")` and `bundle.full_sql`.

#### 1.4 `_handle_run_axi_dv_gates` (`L242-L275`)
- **Current Bug:** Checks `args.contract_payload.get("force_violation", False)` and returns a hardcoded 4-line `@toon_v1` string referencing `hub_fund`.
- **Required Fix:**
  1. Call `res = run_axi_dv_verification_pipeline(args.contract_payload, graph_a=..., graph_b=...)` (`opendv_axi_verifier.pipeline`).
  2. Return `passed=res.passed`, `status=res.status`, `violation_count=len(res.violations)`, `toon_trace=res.toon_text`, and serialized `violations`.

#### 1.5 `_handle_apply_ast_codemod` (`L278-L313`)
- **Current Bug:** Appends `# Applied deterministic codemod {args.codemod_id} on {args.target_entity}` to the YAML string without mutating the contract AST.
- **Required Fix:**
  1. Parse `args.contract_yaml` via `yaml.safe_load`.
  2. Dispatch `args.codemod_id` to the real codemod implementations in `opendv_axi_verifier.codemods`:
     - `"A.1"` $\rightarrow$ `alg_a1_satellite_split.apply_satellite_split(...)`
     - `"A.2"` $\rightarrow$ `alg_a2_enum_demotion.apply_enum_to_attribute_demotion(...)`
     - `"A.3"` $\rightarrow$ `alg_a3_link_effectivity.apply_link_attribute_migration(...)`
     - `"A.4"` $\rightarrow$ `alg_a4_cold_start.apply_cold_start_bootstrap(...)`
     - `"A.5"` $\rightarrow$ `alg_a5_sqlglot_hash.rewrite_naive_hash_sql(...)`
     - `"A.9a"` $\rightarrow$ `alg_a9a_schema_drift.classify_and_apply_schema_drift(...)`
  3. Re-serialize the mutated contract dict to YAML and return both `patched_contract_yaml` and `patched_contract_dict`.

#### 1.6 `_handle_execute_duckdb_sandbox` (`L316-L350`)
- **Current Bug:** Runs `con.execute(args.sql_bundle)` and returns hardcoded `replay_delta_rows=0, orphan_count=0` without running Batches 1–4 or `DV-DYN-001..015`.
- **Required Fix:**
  1. Instantiate `DuckDbShadowSandbox` (`opendv_duckdb_sandbox.sandbox_engine`).
  2. Execute `report = sandbox.run_full_verification(source=args.canonical_ir_or_contract, emit_telemetry=False)` (updated in `WP-05` to support arbitrary schemas).
  3. Return real `passed=report.passed`, `replay_delta_rows`, `orphan_count`, `batches`, and `dynamic_checks`.

---

### WP-02: Make `PipelineInputSpec` Discovery-First (Remove Required Pre-Baked Answers)

**Target Files:**
- `packages/opendv-agent-harness/src/opendv_agent_harness/pipeline.py` (`L311-L358`)
- `examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml`

#### 2.1 Update `PipelineSourceFeedInput` (`pipeline.py:L311-L329`)
Make all semantic classification fields optional overrides (`default_factory=list` or `None`) so a user can pass raw tables/files without pre-labeling columns:
```python
class PipelineSourceFeedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feed_id: str | None = None
    source_system: str
    table_name: str
    data_path: str = ""
    file_path: str | None = None
    baseline_data_path: str | None = None
    baseline_file_path: str | None = None
    key_authority_tier: (
        Literal["TIER_1_GLOBAL_STANDARD", "TIER_2_ENTERPRISE_SOR", "TIER_3_LOCAL_SCOPED"] | None
    ) = None
    bkcc_namespace: str | None = None
    record_source_code: str | None = None
    target_hub_name: str | None = None
    business_key_columns: list[str] = Field(default_factory=list)
    surrogate_key_columns: list[str] = Field(default_factory=list)
    timestamp_column: str | None = None
    foreign_key_mappings: dict[str, str] = Field(default_factory=dict)
    cbc_mapping_hints: dict[str, str] = Field(default_factory=dict)
    pii_columns: list[str] = Field(default_factory=list)
```

#### 2.2 Update `PipelineUnstructuredDocInput` (`pipeline.py:L332-L358`)
Make `target_hub_name`, `link_name`, `satellite_name`, and `extracted_candidates` optional (`None` / `default_factory=list`) so `@scout-unstructured` discovers them from the raw PDF/Markdown file and `GraphAOntology`.

---

### WP-03: Replace Discarded `harness.run_task()` Calls with Real Agentic Structured Outputs & Closed-Loop Repair (`pipeline.py`)

**Target File:** `packages/opendv-agent-harness/src/opendv_agent_harness/pipeline.py`

#### 3.1 Define Strict Pydantic Output Schemas for Each Agent Stage
Add the following structured output models in `pipeline.py` and pass them via `AgentTaskSpec.output_schema`:
1. **`Stage00OrchestrationPlanOutput`** (`@vault-first-mate`):
   - `wave_execution_order: list[int]`
   - `risk_assessment: str`
   - `recommended_max_retries: int`
2. **`Stage01SourceDiscoveryOutput` & `Stage01FeedDiscoveryItem`** (`@scout-source`):
   - Per-feed discovered semantics (plus `discovered_feeds: list[Stage01FeedDiscoveryItem]`):
     - `feed_id: str | None`
     - `source_system: str | None`
     - `table_name: str | None`
     - `record_source_code: str | None`
     - `target_hub_name: str | None`
     - `business_key_columns: list[str]`
     - `surrogate_key_columns: list[str]`
     - `timestamp_column: str | None`
     - `pii_columns: list[str]`
     - `key_authority_tier: Literal["TIER_1_GLOBAL_STANDARD", "TIER_2_ENTERPRISE_SOR", "TIER_3_LOCAL_SCOPED"]`
     - `bkcc_namespace: str`
     - `foreign_key_mappings: dict[str, str]`
     - `cbc_mapping_hints: dict[str, str]`
3. **`Stage02UnstructuredDiscoveryOutput`** (`@scout-unstructured`):
   - Per-document discovered extractions (plus `documents: list[dict[str, Any]]`):
     - `doc_id: str | None`
     - `target_hub_name: str`
     - `link_name: str | None`
     - `satellite_name: str | None`
     - `extracted_candidates: list[PipelineUnstructuredCandidateInput]` (where the agent supplies `extracted_business_key`, `verbatim_quote`, `target_hub`, `attributes`, and `confidence_score`, and Python deterministic post-processing verifies `verbatim_quote` exists in `raw_text`, computes exact `char_start`/`char_end` offsets, and enforces `alg_a9b_unstructured` confidence tiers).
4. **`Stage03ColumnTriageOutput`** (`@ship-vault` Pass 1):
   - `column_triage_ledger: list[ColumnTriageEntry]` covering 100% of physical columns across all cumulative structured feeds.
   - `unmapped_columns: list[str]`
5. **`Stage04ContractSynthesisOutput`** (`@ship-vault` Pass 2):
   - `contract: NoMistakeDataVault2ContractV5`
   - `synthesis_rationale: str`

#### 3.2 Stage-by-Stage Execution Wiring in `AgenticVaultPipeline`
- **In `STAGE_01_SCOUT_SOURCE` (`L1850-L2075`)**:
  1. Run `GraphBProfiler` on the DuckDB `bronze.*` tables to obtain exact `U`, `U_nn`, `H`, `N_null`, and cross-table Jaccard column overlap scores.
  2. Construct `scout_src_spec` containing the empirical `GraphBProfile` JSON, 5 sample rows per table, and `GraphAOntology` concepts/synonyms, with `output_schema=Stage01SourceDiscoveryOutput`.
  3. Call `scout_res = await self.harness.run_task(scout_src_spec)`.
  4. Use `scout_res.structured_output` to populate the feed's semantic metadata (merging any explicit user overrides from `pipeline_input.yaml` if provided).
- **In `STAGE_02_SCOUT_UNSTRUCTURED` (`L2150-L2325`)**:
  1. Read the full text of each unstructured document (`u_path.read_text(encoding="utf-8")`) and query the active Hub business keys loaded in Stage `01`.
  2. Construct `scout_unstr_spec` containing the document text, `GraphAOntology`, and candidate Hub business keys from Stage `01`, with `output_schema=Stage02UnstructuredDiscoveryOutput`.
  3. Call `unstr_res = await self.harness.run_task(scout_unstr_spec)`.
  4. For each candidate returned in `unstr_res.structured_output`, verify the `verbatim_quote` substring in the raw document text, compute exact `char_start` and `char_end` offsets (`raw_text.index(quote)`), and route via `alg_a9b_unstructured` (`Tier-U1`, `Tier-U2`, `Tier-U3`).
- **In `STAGE_03_SHIP_VAULT_PASS1` & `STAGE_04_SHIP_VAULT_PASS2` (`L2350-L2530`)**:
  1. Pass the Stage `01`/`02` discoveries, `GraphAOntology`, `GraphBProfile`, and the prior wave's `NoMistakeDataVault2ContractV5` (if Wave $> 1$) to `@ship-vault`.
  2. Use `@ship-vault`'s `structured_output` (`NoMistakeDataVault2ContractV5`) as `candidate_contract` (keeping `_synthesize_cumulative_contract` **only** as the fallback inside `DeterministicHarnessAdapter` for offline unit tests).
  3. Set `HarnessSetupConfig.inject_adversarial_fault_on_attempt_1` default to `False` (`L1393`).
- **In `STAGE_05_AXI_DV_STATIC_GATES` (`L2555-L2660`)**:
  1. Run `axi_res = run_axi_dv_verification_pipeline(current_attempt_contract, graph_a=..., graph_b=...)`.
  2. If `not axi_res.passed` and `attempt_idx < max_attempts`:
     - Build `repair_spec` containing:
       - The failing contract YAML/JSON
       - The exact `axi_res.toon_text` (`@toon_v1` compact violation trace with `fix_hint` and `codemod_ref`)
       - `output_schema=NoMistakeDataVault2ContractV5`
       - Allowed tools: `["apply_ast_codemod", "run_axi_dv_gates"]`
     - Call `repair_res = await self.harness.run_task(repair_spec)`.
     - Extract the repaired contract from `repair_res.structured_output` (or tool output) and assign `current_attempt_contract = repair_res.structured_output.model_dump(mode="json")` so the next loop iteration validates the **agent's actual repair**.

---

### WP-04: Fail-Closed LLM Adapters & Real SDK Execution (`adapters/` & `deps.py`)

**Target Files:**
- `apps/opendv-platform-api/src/opendv_platform_api/deps.py` (`L1325-L1341`)
- `packages/opendv-agent-harness/src/opendv_agent_harness/adapters/antigravity_adapter.py`
- `packages/opendv-agent-harness/src/opendv_agent_harness/adapters/claude_adapter.py`
- `packages/opendv-agent-harness/src/opendv_agent_harness/adapters/deterministic_adapter.py`

#### 4.1 Remove Silent Mock Shim Injection in Production (`deps.py:L1325-L1341`)
- **Current Bug:** `_resolve_sdk_shim_if_needed(provider)` catches `ImportError` and silently returns `build_deterministic_agy_sdk_shim()` or `build_deterministic_claude_sdk_shim()`.
- **Required Fix:**
  1. Add an explicit environment/config flag `OPENDV_ALLOW_MOCK_SDK_SHIM=false` (default `false` in production/CLI/API; set to `true` only in mock unit tests).
  2. In `GoogleAntigravityAdapter` (`antigravity_adapter.py`), if `google.antigravity` is not installed, support falling back to the official `google-genai` SDK (`from google import genai`) using `GEMINI_API_KEY` / `GOOGLE_API_KEY` with structured outputs (`response_schema=spec.output_schema`) and function calling (`tools=...`).
  3. In `ClaudeAgentSdkAdapter` (`claude_adapter.py`), if `claude_agent_sdk` is not installed, support falling back to the official `anthropic` Python SDK (`import anthropic`) using `ANTHROPIC_API_KEY` with tool use and structured JSON schema output.
  4. If neither the agent SDK nor the provider API key is available when `provider in ("antigravity", "claude")` and `OPENDV_ALLOW_MOCK_SDK_SHIM` is false, raise a clear `RuntimeError` explaining which SDK/API key (`GEMINI_API_KEY` or `ANTHROPIC_API_KEY`) must be configured.

---

### WP-05: Generalize 4-Zone SQL Compiler & 4-Batch Adversarial DuckDB Sandbox Beyond Private Equity (`opendv-duckdb-sandbox`)

**Target Files:**
- `packages/opendv-duckdb-sandbox/src/opendv_duckdb_sandbox/compiler_4zone.py` (`L1820-L1856`)
- `packages/opendv-duckdb-sandbox/src/opendv_duckdb_sandbox/dynamic_checks.py` (`L16-L130`)
- `packages/opendv-duckdb-sandbox/src/opendv_duckdb_sandbox/adversarial_batches.py` (`L79-L438`)
- `packages/opendv-duckdb-sandbox/src/opendv_duckdb_sandbox/sandbox_engine.py` (`L131-L212`)
- `packages/opendv-agent-harness/src/opendv_agent_harness/pipeline.py` (`L2881-L2890`)

#### 5.1 Generic Zones 1–4 Compilation in `compiler_4zone.py` (`L1820-L1856`)
When `not is_pe_domain`:
1. **Zone 1 (`_compile_generic_zone_1_staging_views`)**:
   - For each source table / entity mapping in `CanonicalIR`, emit `CREATE OR REPLACE VIEW staging.stg_<source_table> AS SELECT ...` with deterministic length-prefixed SHA-256 expressions (`LOWER(SHA256(...))`) for every Hub `hk_*`, Link `hk_lnk_*`, and Satellite `hash_diff` using the exact column order declared in `CanonicalIR`.
   - Wrap optional nullable FK hash expressions with `COALESCE(..., REPEAT('0', 64))`.
2. **Zone 2 (`_compile_generic_zone_2_raw_vault_dml`)**:
   - For every `HubIR` in `ir.hubs`: emit idempotent `INSERT INTO raw_vault.<hub_name> (...) SELECT ... FROM staging.stg_... WHERE NOT EXISTS (SELECT 1 FROM raw_vault.<hub_name> h WHERE h.<hk> = src.<hk>)`.
   - For every `LinkIR` in `ir.links`: emit idempotent `INSERT INTO raw_vault.<link_name> (...) SELECT ... WHERE NOT EXISTS (...)`.
   - For every `SatelliteIR` in `ir.satellites`: emit `INSERT INTO <schema>.<sat_name> (...) SELECT ...` with latest-version `QUALIFY ROW_NUMBER() OVER (PARTITION BY <parent_hk> ORDER BY load_dts DESC) = 1` anti-join (`cur.hash_diff <> src.hash_diff`).
3. **Zone 3 (`_compile_generic_zone_3_business_vault_sql`)**:
   - For every `PitTableIR` in `ir.pit_tables`: emit `CREATE TABLE IF NOT EXISTS business_vault.<pit_name>` and populate snapshot rows joining the Hub with each tracked Satellite using `AS OF` temporal join semantics and Ghost Record fallback `COALESCE(s.<parent_hk>, REPEAT('0', 64))` / `COALESCE(s.load_dts, TIMESTAMP '1900-01-01 00:00:00')`.
4. **Zone 4 (`_compile_generic_zone_4_information_mart_views`)**:
   - For each `PitTableIR`, emit `CREATE OR REPLACE VIEW information_mart.vw_<pit_name>_current AS SELECT ...` performing pure `INNER JOIN`s from `business_vault.<pit_name>` to `raw_vault.<hub_name>` and each tracked `raw_vault.<sat_name>` on `(hk, load_dts)`.

#### 5.2 Dynamic Metadata Derivation in `dynamic_checks.py` & `adversarial_batches.py`
1. Add a helper `build_vault_topology_maps(ir: CanonicalIR | None = None)` in `dynamic_checks.py`:
   - If `ir is None`, return the default PE maps (`HUB_PK_MAP`, `LINK_PK_MAP`, `SATELLITE_PK_MAP`, `FK_EDGES`).
   - If `ir is not None`, construct `hub_pk_map`, `link_pk_map`, `satellite_pk_map`, and `fk_edges` directly from `ir.hubs`, `ir.links`, `ir.satellites`, and `ir.same_as_links`.
2. Thread `ir: CanonicalIR | None = None` through `execute_4batch_adversarial_suite(conn, bundle, ir=ir)` and `evaluate_all_dynamic_checks(conn, ..., ir=ir)`:
   - **Batch 1**: Execute `bundle.zones_0_to_3_sql` (after seeding `bronze.*` tables from the wave's CSV/Parquet files if provided) and verify `0` orphans, `2` Zero Keys in every Hub/Link in `hub_pk_map`/`link_pk_map`, and `1` Ghost Record in every Satellite in `satellite_pk_map`.
   - **Batch 2**: Re-execute `bundle.zone_2_sql` and `bundle.zone_3_sql` and verify `delta_hubs == 0`, `delta_links == 0`, `delta_sats == 0`.
   - **Batch 3**: Verify `'AB'||'C'` vs `'A'||'BC'` boundary-shift collision immunity and insert 1 mutated payload row into the first non-empty `raw_vault` Satellite in `ir.satellites`, verifying `satellite_delta_rows == 1` and `hub_delta_rows + link_delta_rows == 0`.
   - **Batch 4**: Execute bi-temporal / restatement verification on bi-temporal satellites (if present in `ir.satellites`) or cross-source `BKCC` collision check on the primary Hub.
3. **Update `pipeline.py:L2881-L2890` (`STAGE_06_AXI_DV_DYNAMIC_SANDBOX`)**:
   - Pass the wave's actual `canonical_ir` and loaded DuckDB `bronze.*` tables into `sandbox.run_full_verification(source=canonical_ir, bronze_tables=..., emit_telemetry=False, run_id=run_id)` instead of calling `run_full_verification()` with `source=None`.

---

### WP-06: De-Mock Platform API Run Execution (`apps/opendv-platform-api/src/opendv_platform_api/deps.py`)

**Target File:** `apps/opendv-platform-api/src/opendv_platform_api/deps.py` (`L1343-L1408`, `L1640-L1714`)

1. Keep the pre-seeded `proj-apex-horizon-pe` project strictly labeled as a reference benchmark (`is_demo_project: bool = True`).
2. In `PlatformStateContainer.create_run()` (`L1640-L1714`):
   - When a user triggers a run on any project that has `bronze_sources` or `pipeline_input_path`, dynamically construct a `PipelineInputSpec` from the project's registered `bronze_sources`, `unstructured_documents`, and `graph_a_ontology_path` if `pipeline_input_path` is not explicitly set.
   - Execute `AgenticVaultPipeline` against those sources so all 8 stages (`STAGE_00` through `STAGE_07`) run live and emit real `VaultObservabilityEvent` records to the WebSocket stream and SQLite ledger.

---

## 4. Verification & Acceptance Criteria Checklist

After implementing `WP-01` through `WP-06`, run the following verification suite to confirm the application is 100% agentic and free of mocked facades:

1. **Zero Pre-Baked Hints Test (`global_commerce_vault`)**:
   - Strip `business_key_columns`, `surrogate_key_columns`, `pii_columns`, `foreign_key_mappings`, `cbc_mapping_hints`, and `extracted_candidates` from `examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml`.
   - Run `opendv pipeline run --input examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml --output-dir /tmp/gcv_real_run --provider antigravity` (or `--provider claude`).
   - Verify that:
     - `@scout-source` infers `customer_code`, `order_number`, `invoice_number`, and `return_rma_code` as Business Keys, flags `email` and `tax_id_ssn` as `PII` (`sat_customer_pii_vault`), and maps FKs via `GraphBProfiler` + `GraphAOntology`.
     - `@scout-unstructured` reads `docs/vip_shipping_rebate_addendum.md` and `docs/order_1002_damage_claim_memo.md`, extracts the verbatim quotes via LLM, computes exact `char_start`/`char_end` offsets, and links them to `CUST-1001` and `ORD-5002`.
     - `@ship-vault` synthesizes a valid `NoMistakeDataVault2ContractV5` across Waves 1, 2, and 3 that passes all 40 `DV-G1..G7` static gates.
     - If a static gate fails in `STAGE_05`, `@ship-vault` receives the `@toon_v1` trace and repairs the contract without `copy.deepcopy(candidate_contract)`.
     - `STAGE_06` compiles generic Zones 0–4 SQL for `global_commerce_vault` (`hub_customer`, `hub_sales_order`, `hub_invoice`, `hub_return_authorization`, `lnk_*`, `sat_*`, `pit_*`) and executes all 4 adversarial batches + `DV-DYN-001..015` against the actual `global_commerce_vault` tables in DuckDB.

2. **Automated Test & Quality Gate Commands**:
   ```bash
   uv run --all-packages pytest
   uv run ruff check packages apps
   bun test
   bun x tsc -b --pretty
   ```
