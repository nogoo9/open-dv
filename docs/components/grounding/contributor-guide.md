# Open-DV Ontology Grounding Contributor Guide (`src/opendv/grounding/`)

> **User Guide:** [`user-guide.md`](user-guide.md) · **Architecture Design:** [`ontology-grounding-engine-and-fibo-pack.md`](../../designs/ontology-grounding-engine-and-fibo-pack.md) · **Repo Constitution:** [`AGENTS.md`](../../../AGENTS.md)

This guide is for contributors modifying or extending **`src/opendv/grounding/`** (Layer 2a) and its integration points across Layer 1 (`models`), Layer 2b (`sources`), Layer 4 (`service`), Layer 5 (`api`, `cli`), and build-time `scripts/`.

## Table of Contents

1. [Architecture Position & Layer-2a Import Contract](#1-architecture-position--layer-2a-import-contract)
2. [Module-by-Module Map](#2-module-by-module-map)
3. [Key Design Principles & Non-Negotiable Invariants](#3-key-design-principles--non-negotiable-invariants)
4. [Common Extension Recipes](#4-common-extension-recipes)
5. [Local Verification & PR Checklist](#5-local-verification--pr-checklist)

---

## 1. Architecture Position & Layer-2a Import Contract

`open-dv` enforces an acyclic **5-layer package DAG** checked statically via AST scans in [`tests/architecture/test_import_contracts.py`](../../../tests/architecture/test_import_contracts.py):

```text
Layer 5: cli / api          (Typer CLI, FastAPI server; NEVER imports grounding directly)
   │
Layer 4: service            (Calls ColumnGroundingEngine, Orchestrator, MCP tool, Projection, FiboPackOps)
   │
Layer 3: agents | modeling  (Siblings; NEVER import grounding directly)
   │
Layer 2b: sources           (Calls grounding.structured.{validators, profiler} & grounding_adapter)
   │
Layer 2a: grounding         <── THIS COMPONENT: synchronous core + KnowledgeSource SPI & providers
   │
Layer 1: models             (StrictModel DTOs, ColumnShapeStats, GroundingReceipt, ErrorCodes)
```

### Exact Import Rules (`V2_LAYER_BANS` & `PER_FILE_IMPORT_EXCEPTIONS`)

- **Allowed internal imports inside `src/opendv/grounding/`:** Python stdlib and `opendv.models.*` (Layer 1) only (plus `yaml` solely inside [`src/opendv/grounding/providers/glossary/loader.py`](../../../src/opendv/grounding/providers/glossary/loader.py) via `PER_FILE_IMPORT_EXCEPTIONS`).
- **Banned imports across `src/opendv/grounding/`:**
  `opendv.sources`, `opendv.verifier`, `opendv.storage`, `opendv.agents`, `opendv.modeling`, `opendv.service`, `opendv.api`, `opendv.cli`, `duckdb`, `sqlglot`, `sqlite3`, `pyarrow`, `openpyxl`, `pypdf`, `pypdfium2`, `docx`, `pytesseract`, `PIL`, `databricks`, `urllib`, `http`, `socket`, `subprocess`, `httpx`, `requests`, `aiohttp`, `anthropic`, `google`, `fastapi`, `starlette`, `typer`, `mcp`, `rdflib`, `yaml` (except `glossary/loader.py`), `opendv_*`.
- **Subpackage layering inside `grounding/`:**
  - `grounding/kernel/` bans `opendv.grounding.providers` and `opendv.grounding.structured`.
  - `grounding/structured/` bans `opendv.grounding.providers` (consuming backends strictly through `KnowledgeSource` in `spi.py`).
- **Reverse bans (`opendv.grounding` forbidden):**
  `models`, `verifier`, `storage`, `agents`, `modeling`, `api`, and `cli` all ban `opendv.grounding`. Only Layer 2b (`sources`), Layer 4 (`service`), and `tests/` may import `opendv.grounding`.
- **Zero runtime `rdflib` (`scripts/` only):** `rdflib` is banned across the entire `src/opendv/` tree and confined exclusively to build-time compilation in [`scripts/vendor_fibo.py`](../../../scripts/vendor_fibo.py) and its helpers.

### SPI Decoupling via `spi.py`

All domain backends (built-in FIBO Pack v2, YAML glossaries, and wave-to-wave project overlays) implement the synchronous `KnowledgeSource` Protocol in [`src/opendv/grounding/spi.py`](../../../src/opendv/grounding/spi.py) (`metadata`, `calibration_params`, `expand_query_terms`, `recall_by_text`, `match_values_or_mentions`, `get_item`, `get_ancestors`, `evaluate_structured_candidates`, `score`). Keeping `CalibrationParams` and SPI DTOs in [`src/opendv/models/grounding_spi.py`](../../../src/opendv/models/grounding_spi.py) guarantees zero circular imports between `kernel/` and `providers/`.

---

## 2. Module-by-Module Map

### Inside `src/opendv/grounding/`

Every `.py` module below stays strictly under `5,000` `cl100k_base` tokens (`D16`):

| Module / Asset | Primary Responsibility | Key Entry Points & Symbols |
| :--- | :--- | :--- |
| [`__init__.py`](../../../src/opendv/grounding/__init__.py) | Public SPI re-export | Re-exports `KnowledgeSource` |
| [`spi.py`](../../../src/opendv/grounding/spi.py) | Synchronous Module-2 Protocol | `KnowledgeSource` |
| [`kernel/__init__.py`](../../../src/opendv/grounding/kernel/__init__.py) | Kernel public surface | Re-exports lexical, calibration, PII, ISO codes, and overlay primitives |
| [`kernel/lexical.py`](../../../src/opendv/grounding/kernel/lexical.py) | `Decimal(28)` deterministic BM25 index | `LexicalIndex`, `LexicalMatcherConfig`, `LexicalWeightsConfig`, `load_lexical_matcher_config`, `STOPWORDS` |
| [`kernel/calibration.py`](../../../src/opendv/grounding/kernel/calibration.py) | Modality-aware log-odds fusion & Logit-Normal CIs | `CandidateChannelInputs`, `ColumnCalibrationContext`, `CalibrationOutcome`, `select_and_calibrate`, `check_fast_path_eligible`, `compute_phi_*`, `compute_sigma_*`, `determine_suggested_predicate` |
| [`kernel/pii_core.py`](../../../src/opendv/grounding/kernel/pii_core.py) | Re-exported PII detectors + raw-PII span stripping | `REDACTED_LEAK_RE`, `strip_pii_spans`, `has_redacted_placeholder`, `ensure_no_redacted_placeholder`, plus re-exports from `models/pii_tokenizer.py` |
| [`kernel/_project_source.py`](../../../src/opendv/grounding/kernel/_project_source.py) | Wave-1/committed `GraphAOntology` & memory indexer | `ProjectGraphASource`, `dedupe_str_list` |
| [`kernel/overlay.py`](../../../src/opendv/grounding/kernel/overlay.py) | Wave-2+ `OverlayKnowledgeSource` composition | `OverlayKnowledgeSource`, re-exports `ProjectGraphASource` |
| [`kernel/iso_codes.json`](../../../src/opendv/grounding/kernel/iso_codes.json) | Bundled ISO 4217 / ISO 3166-1 reference table (wheel asset) | Loaded via `load_iso_codes` / `IsoCodesTable` in `kernel/__init__.py` |
| [`structured/__init__.py`](../../../src/opendv/grounding/structured/__init__.py) | Structured engine & profiler exports | Re-exports `ColumnGroundingEngine`, `SymbolicGroundingDraft`, `compute_column_shape_stats`, validators |
| [`structured/validators.py`](../../../src/opendv/grounding/structured/validators.py) | Domain-neutral structural & checksum validators | `PATTERN_VALIDATORS`, `STRUCTURAL_PATTERN_KEYS`, `compute_structural_patterns`, `is_valid_{lei,isin,cusip,sedol,iban,aba_routing,swift_bic,figi,iso_4217_ccy,iso_3166_country,uuid_v4,iso_date,iso_timestamp}` |
| [`structured/profiler.py`](../../../src/opendv/grounding/structured/profiler.py) | Pre-masking column shape statistics | `compute_column_shape_stats` |
| [`structured/steward_context.py`](../../../src/opendv/grounding/structured/steward_context.py) | Steward context, pre-screen, paired columns & channel prep | `StewardTableContext`, `check_etl_prescreen`, `detect_paired_columns`, `profile_column_evidence`, `build_candidate_channel_inputs`, `FOREIGN_ROLE_PREFIXES` |
| [`structured/engine.py`](../../../src/opendv/grounding/structured/engine.py) | Synchronous 3-stage Column Grounding Engine | `ColumnGroundingEngine`, `SymbolicGroundingDraft`, `ColumnSymbolicDraft`, `TableSymbolicDraft` |
| [`providers/fibo/__init__.py`](../../../src/opendv/grounding/providers/fibo/__init__.py) | FIBO provider public exports | Re-exports `FiboKnowledgeSource`, `FiboReferenceOntologyLoader`, `FiboPackQueryAdapter`, `SubclassClosureResolver`, constants |
| [`providers/fibo/loader.py`](../../../src/opendv/grounding/providers/fibo/loader.py) | Pure-JSON Pack v2 (`2.0.0`) loader & `fibo-qa` adapter | `FiboReferenceOntologyLoader`, `FiboPackQueryAdapter`, `get_bundled_fibo_pack_sha256`, `FIBO_V2_MODULES`, `MAX_*` caps |
| [`providers/fibo/closure.py`](../../../src/opendv/grounding/providers/fibo/closure.py) | Taxonomic closure & derived Pack v2 index builder | `PackDerivedIndexes`, `SubclassClosureResolver`, `build_pack_v2_derived_indexes`, `minimal_taxonomic_leaves` |
| [`providers/fibo/closure_rules.py`](../../../src/opendv/grounding/providers/fibo/closure_rules.py) | Archetype priority rules, 17-ancestor PII map & schema checks | `FIBO_ID_SPECS`, `FIBO_PII_MAP`, `CMNS_RANGE_ARCHETYPES`, `validate_pack_v2_data`, `verify_pack_against_baselines` |
| [`providers/fibo/acronyms.py`](../../../src/opendv/grounding/providers/fibo/acronyms.py) | Context-guarded financial acronyms & range scoring | `FINANCIAL_ACRONYMS`, `should_expand_sec`, `expand_financial_tokens`, `score_candidate_shape_range`, `check_arch_type_veto` |
| [`providers/fibo/knowledge_source.py`](../../../src/opendv/grounding/providers/fibo/knowledge_source.py) | FIBO `KnowledgeSource` backend implementation | `FiboKnowledgeSource` |
| [`providers/fibo/fibo-core-pack.json`](../../../src/opendv/grounding/providers/fibo/fibo-core-pack.json) | Bundled FIBO Pack v2 (`master_2026Q3`, wheel asset) | `2,240` classes, `767` properties, `942` individuals |
| [`providers/fibo/fibo_calibration.json`](../../../src/opendv/grounding/providers/fibo/fibo_calibration.json) | Calibrated Logit-Normal parameters & held-out report (wheel asset) | Loaded by `FiboKnowledgeSource.calibration_params` |
| [`providers/glossary/loader.py`](../../../src/opendv/grounding/providers/glossary/loader.py) | Alias-free YAML loader & ReDoS-safe regex validator | `GlossaryDocument`, `GlossaryTermSpec`, `LoadedGlossary`, `load_glossary`, `validate_safe_regex`, `get_example_glossary_path` |
| [`providers/glossary/knowledge_source.py`](../../../src/opendv/grounding/providers/glossary/knowledge_source.py) | Domain-neutral glossary `KnowledgeSource` backend | `GlossaryKnowledgeSource` |
| [`providers/glossary/example_glossary.yaml`](../../../src/opendv/grounding/providers/glossary/example_glossary.yaml) | Reference SaaS glossary fixture (wheel asset) | `customer`, `subscription_plan`, `billing_invoice`, identifiers, codes, relationship |

### Related Cross-Layer Touchpoints

| Layer | File(s) | Role Relative to `opendv.grounding` |
| :--- | :--- | :--- |
| **Layer 1 (`models`)** | `grounding.py`, `grounding_spi.py`, `events.py`, `sources.py`, `ontology.py`, `ontology_deltas.py`, `vault_ir.py`, `pii_tokenizer.py` | Public DTOs, internal SPI/rerank DTOs, `GroundingReceipt`, `ColumnShapeStats`, `36`-op `OntologyDelta`, canonical `map_sql_to_portable_type`, and shared PII primitives. |
| **Layer 2b (`sources`)** | `adapters.py`, `_extractors.py`, `_extractors_files.py`, `profiler.py`, `pii.py`, `grounding_adapter.py` | Captures `shape_samples` (`200` rows) before masking, computes `ColumnShapeStats` in `sources/profiler.py`, re-exports PII symbols cleanly (`test_clone_detection.py`), and converts `SourceProfile` to `GroundingRequest`. |
| **Layer 3 (`agents`)** | `sdk_runner.py`, `cli_subprocess_base.py`, `packs/ontology-first-mate/SKILL.md` | Enforces forced `emit_structured_output` (`tools == []`) & `--json-schema`, and instructs `@ontology-first-mate` on MCP `ground_columns`. |
| **Layer 4 (`service`)** | `fibo_pack_ops.py`, `_mcp_grounding_tool.py`, `_mcp_tools.py`, `_mcp_scout_tools.py`, `workflows/{_grounding_orchestrator.py, _grounding_reranker.py, _grounding_projection.py, _discovery_ontology.py, _ontology_deltas.py}`, `bundles/{exporter.py, importer.py}` | Pure-JSON pack installation/drift audit, `<= 64 KB` MCP `ground_columns`, Layer-4 fast-path + chunked rerank + 1-retry rescue, 4-step `GraphAOntology` projection, server receipt verification, and `.opendv.zip` `imported_unverified` isolation. |
| **Layer 5 (`api` / `cli`)** | `api/{routes_workflows.py, _route_helpers.py}`, `cli/{_commands_grounding.py, _client_grounding.py, export_contracts.py}` | `POST /api/v1/ontology/ground` (path containment + `safe_open_no_follow` + `Semaphore(4)`), `opendv ontology ground|fibo-sync|fibo-qa`, and `schemas/v2/` contract exports. |
| **Build-Time (`scripts/`)** | `vendor_fibo*.py`, `calibrate_fibo_grounding.py`, `generate_cli_reference.py` | Upstream `prod.ttl.zip` compilation (`rdflib`) + pack verification and 5-fold `GroupKFold` OOF calibration. |

---

## 3. Key Design Principles & Non-Negotiable Invariants

1. **Modular Token Budget (`D16` `< 5,000` tokens/module):**
   Every `.py` module in `src/opendv/` must remain strictly below `5,000` `cl100k_base` tokens ([`tests/architecture/test_module_token_budget.py`](../../../tests/architecture/test_module_token_budget.py)). Notice `src/opendv/models/grounding_spi.py` is intentionally omitted from `models/__init__.py` barrel exports, and Layer-4 workflows split across `_grounding_orchestrator.py`, `_grounding_reranker.py`, `_grounding_projection.py`, `fibo_pack_ops.py`, and `_mcp_grounding_tool.py`.
2. **Synchronous Layer-2a vs. Layer-4 Agentic Orchestrator:**
   Nothing in `src/opendv/grounding/` performs async I/O, network calls, database queries, subprocess calls, or LLM calls. Agentic fast-path skipping, batched LLM disambiguation, and bounded rescue run exclusively in Layer 4 (`_grounding_orchestrator.py`).
3. **Zero Clone Violations (`pylint --enable=duplicate-code --min-similarity-lines=8`):**
   Keep shared PII regex/tokenization in `models/pii_tokenizer.py` (`sources/pii.py` and `grounding/kernel/pii_core.py` re-export cleanly without duplicating `__all__` line blocks or implementation functions).
4. **Hardening (`T14`) & Wheel Packaging Parity:**
   Never use unallowlisted `.expanduser()`, `REPO_ROOT`, network calls, or broad `BLE001` catches in `src/opendv/`. Register any new data asset under `src/opendv/grounding/` inside `pyproject.toml`, `.github/workflows/ci.yml`, and [`tests/architecture/test_ci_and_wheel_packaging.py`](../../../tests/architecture/test_ci_and_wheel_packaging.py).
5. **Strict Fail-Closed Security:**
   - Prompts passed through `_grounding_reranker.py` never carry `sample_values`, `observed_enum_values`, or raw URIs, validate every field via `detect_pii_in_value()` and `ensure_no_redacted_placeholder()`, and escape untrusted metadata in nonce delimiters.
   - `GroundingReceipt`s are minted solely from server-stored `SourceProfile`s in `project.sqlite`; `.opendv.zip` imports always downgrade to `provenance="imported_unverified"`.
   - `enforce_grounding_asset_path` + `safe_open_no_follow(..., stop_roots=[...])` guard every file read on REST requests and `fibo-sync`.

---

## 4. Common Extension Recipes

### Adding a New `KnowledgeSource` Provider
1. Implement `KnowledgeSource` (`src/opendv/grounding/spi.py`) in a new submodule under `src/opendv/grounding/providers/<provider>/` without importing `opendv.sources`, `service`, `agents`, or network libraries.
2. Populate accurate `KnowledgeSourceMetadata.anchor_entry_kinds` (empty `frozenset()` if flat/entity-less so `calibration.py` adjusts $\rho_{\text{mod}}$ cleanly).
3. If your loader needs a library-specific dependency beyond stdlib + `opendv.models`, evaluate `V2_LAYER_BANS` carefully and prefer passing pre-read bytes from Layer 4 if filesystem confinement is needed.
4. Add focused unit tests under `tests/grounding/` and re-run `tests/architecture/test_import_contracts.py`.

### Adding a New Structural Pattern Validator
1. Add the pure-stdlib checksum/format validator in `src/opendv/grounding/structured/validators.py` and register its key in `STRUCTURAL_PATTERN_KEYS` and `PATTERN_VALIDATORS`.
2. Map the pattern key inside `FiboKnowledgeSource.match_values_or_mentions()` (or `GlossaryKnowledgeSource`), distinguishing checksum-backed patterns from checksum-less patterns in `check_fast_path_eligible()` if relevant.
3. Add test cases in `tests/grounding/test_validators_and_shapes.py` and re-check `scripts/calibrate_fibo_grounding.py --verify-heldout`.

### Updating Bundled FIBO Pack v2 & Calibration
1. Run build-time compilation: `uv run python scripts/vendor_fibo.py --update-repo` (verifies archive security, `starter_packs/` Path A $\ge 0.6500$, size/latency ceilings, and updates `schemas/v2/baseline-digests.json`).
2. Re-fit and verify calibration if Pack v2 scores changed materially: `uv run python scripts/calibrate_fibo_grounding.py` followed by `--verify-heldout`.
3. Verify `tests/grounding/test_fibo_pack_v2_and_sync.py`, `tests/grounding/test_calibration_and_selection.py`, and `tests/sources/test_fibo_and_starter_packs.py`.

### Adding or Modifying CLI Options (`opendv ontology ground|fibo-sync`)
1. Update Layer-1 request DTOs in `src/opendv/models/grounding.py` with `Field(description=...)` if adding request fields.
2. Wire flags in `src/opendv/cli/_commands_grounding.py` and client transport in `_client_grounding.py` (never importing `opendv.grounding` in `cli/`).
3. Regenerate contracts and docs: `uv run python -m opendv.cli.export_contracts`, `uv run python scripts/generate_cli_reference.py`, and `bun run --cwd web codegen`.

---

## 5. Local Verification & PR Checklist

Run these commands from the repository root after editing `src/opendv/grounding/` or its integrations:

```bash
# 1. Grounding unit, projection, calibration & CLI/REST/MCP tests (non-live)
uv run pytest tests/grounding/ -m "not live_provider" -o addopts="" -rs -q

# 2. Calibration held-out benchmark gate
uv run python scripts/calibrate_fibo_grounding.py --verify-heldout

# 3. Architecture DAG, D16 (<5k tokens), clone detection, T14 & wheel packaging gates
uv run pytest tests/architecture/ tests/docs/ -m "not live_provider" -o addopts="" -rs -q

# 4. Schema / OpenAPI / CLI contract parity & closed ErrorCode AST check
uv run python -m opendv.cli.export_contracts --check
uv run python scripts/check_error_codes.py --catalog schemas/v2/error-codes.json --baselines schemas/v2/baseline-digests.json --check-ast

# 5. Static lint & strict typecheck
uv run ruff check src tests scripts
uv run mypy src tests/helpers
```

For end-to-end live-agent validation against configured providers (`agy`, `claude-cli`), run the opt-in UAT suite explicitly:

```bash
uv run pytest tests/grounding/test_grounding_live_uat.py -m live_provider -v
```
