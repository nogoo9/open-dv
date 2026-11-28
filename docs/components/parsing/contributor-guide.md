# Open-DV Unstructured Parsing Contributor Guide (`src/opendv/parsing/`)

> **User Guide:** [`user-guide.md`](user-guide.md) · **Architecture Design:** [`unstructured-parsing-engine.md`](../../designs/unstructured-parsing-engine.md) · **Repo Constitution:** [`AGENTS.md`](../../../AGENTS.md)

This guide is for contributors modifying or extending **`src/opendv/parsing/`** (Layer 2) and its Layer-1 (`models`), Layer-4 (`service`), and Layer-5 (`cli`) integration points.

## Table of Contents

1. [Architecture Position & Layer-2 Import Contract](#1-architecture-position--layer-2-import-contract)
2. [Module-by-Module Map](#2-module-by-module-map)
3. [Key Design Principles & Non-Negotiable Invariants](#3-key-design-principles--non-negotiable-invariants)
4. [Common Extension Recipes](#4-common-extension-recipes)
5. [Local Verification & PR Checklist](#5-local-verification--pr-checklist)

---

## 1. Architecture Position & Layer-2 Import Contract

`open-dv` enforces an acyclic **5-layer package DAG** checked statically by [`tests/architecture/test_import_contracts.py`](../../../tests/architecture/test_import_contracts.py):

```text
Layer 5: cli / api          (Typer CLI opendv parse & mcp serve; NEVER imports opendv.parsing directly)
   │
Layer 4: service            (ParsingService, resolve_local_source_blob, _mcp_parse_tools, Steps 2-6 bridges)
   │
Layer 3: agents | modeling  (Siblings; NEVER import opendv.parsing directly)
   │
Layer 2: parsing            <── THIS COMPONENT: pure local parsers, redaction, validation & projectors
         │  peers: sources | grounding | verifier | storage (ZERO cross-imports with parsing)
Layer 1: models             (document_ir.py, pii_tokenizer.py, errors.py, sources.py)
```

### Exact Import Rules (`V2_LAYER_BANS` & `test_source_adapters_contract.py`)

- **Allowed internal imports inside `src/opendv/parsing/`:** Python stdlib, `pydantic`, `opendv.models.*` (Layer 1), `liteparse`, `docx` (`python-docx`), `PIL` (`Pillow`), and optional `pytesseract`.
- **Banned imports inside `src/opendv/parsing/` (`V2_LAYER_BANS["src/opendv/parsing"]`):**
  `opendv.sources`, `opendv.grounding`, `opendv.verifier`, `opendv.storage`, `opendv.agents`, `opendv.modeling`, `opendv.service`, `opendv.api`, `opendv.cli`, `typer`, `fastapi`, `starlette`, `mcp`, `httpx`, `s3fs`, `fsspec`, `keyring`, `duckdb`, `sqlglot`, `pyarrow`, `openpyxl`, `rdflib`, `anthropic`, `google`, `subprocess`, `yaml`, `pypdf`, `pypdfium2`, `opendv_*`.
- **Reverse bans across all other V2 packages:**
  `"opendv.parsing"`, `"pypdf"`, `"pypdfium2"`, and `"liteparse"` are banned across `models`, `grounding`, `sources`, `verifier`, `storage`, `agents`, `modeling`, `service`, `api`, and `cli` (only `opendv.service` may import `opendv.parsing`).
- **Source adapter AST guard (`tests/architecture/test_source_adapters_contract.py`):**
  `src/opendv/parsing/` is explicitly exempted alongside `src/opendv/sources/` for format extension checks; Layer-4/5 callers must route by `DocumentFormat` / `DOC_SOURCE_KINDS` rather than comparing against `".pdf"`.
- **Zero `entry_points` (`C7`):** Callers compose `ParsingRegistry` explicitly via [`_protocols.py`](../../../src/opendv/parsing/_protocols.py) and [`_registry.py`](../../../src/opendv/parsing/_registry.py).

---

## 2. Module-by-Module Map

### Inside `src/opendv/parsing/` (`16` Modules)

Every `.py` module below stays strictly under `5,000` `cl100k_base` tokens (`D16`):

| Module | Primary Responsibility | Key Entry Points & Symbols |
| :--- | :--- | :--- |
| [`__init__.py`](../../../src/opendv/parsing/__init__.py) | Public Layer-2 exports | Re-exports `ParsingEngine`, `ParsingRegistry`, protocols, parsers, projectors, redaction, `validate_ir`, `parse_page_range_spec` |
| [`_protocols.py`](../../../src/opendv/parsing/_protocols.py) | Pure SPI protocols & internal render buffer | `FormatParser`, `IRProjector`, `NormalizedWordBox`, `RenderedPageBuffer` |
| [`_registry.py`](../../../src/opendv/parsing/_registry.py) | Explicit format & projector registry (zero `entry_points`) | `ParsingRegistry`, `default_registry` |
| [`_engine.py`](../../../src/opendv/parsing/_engine.py) | Layer-2 orchestration, sink redaction, OCR & validation | `ParsingEngine`, `capabilities_snapshot`, `parse_page_range_spec` |
| [`redaction.py`](../../../src/opendv/parsing/redaction.py) | Span-to-word-box PII alignment, `Pillow` blackout & optional OCR | `find_pii_spans`, `align_pii_spans_to_boxes`, `redact_png_boxes`, `redact_ir`, `run_pytesseract_ocr` |
| [`validate.py`](../../../src/opendv/parsing/validate.py) | Structural integrity & canonical digest validation | `validate_ir` |
| [`parsers/__init__.py`](../../../src/opendv/parsing/parsers/__init__.py) | Built-in parser exports | Re-exports `BoundedZipReader`, `LiteParsePdfParser`, `DocxParser`, `MarkdownTextParser` |
| [`parsers/_helpers.py`](../../../src/opendv/parsing/parsers/_helpers.py) | Shared slice splitting & PDF rotation helpers | `VIRTUAL_SLICE_MAX_CHARS`, `split_oversized_text`, `build_virtual_slices` |
| [`parsers/_zip_guard.py`](../../../src/opendv/parsing/parsers/_zip_guard.py) | Pure Layer-2 streaming `.docx` zip-bomb & XML guard | `BoundedZipReader` (`500` entries, `250 MiB`, `100.0` ratio > `1 MiB`, `DOCTYPE`/`ENTITY` check) |
| [`parsers/liteparse_pdf.py`](../../../src/opendv/parsing/parsers/liteparse_pdf.py) | PDF parser & page renderer (`liteparse`) | `LiteParsePdfParser`, `MAX_RENDER_DPI`, `MAX_RENDER_PIXELS` |
| [`parsers/docx_parser.py`](../../../src/opendv/parsing/parsers/docx_parser.py) | DOM-order OOXML `.docx` parser (`gridSpan`/`vMerge`) | `DocxParser` |
| [`parsers/text_parsers.py`](../../../src/opendv/parsing/parsers/text_parsers.py) | Stdlib `.md`/`.markdown` and `.txt` parser | `MarkdownTextParser` |
| [`projectors/__init__.py`](../../../src/opendv/parsing/projectors/__init__.py) | Built-in projector exports | Re-exports `DocumentMapProjector`, `MarkdownProjector`, `ChunksProjector` |
| [`projectors/doc_map_projector.py`](../../../src/opendv/parsing/projectors/doc_map_projector.py) | `DocumentMap` + `PageTextView` projector | `DocumentMapProjector` |
| [`projectors/markdown_projector.py`](../../../src/opendv/parsing/projectors/markdown_projector.py) | Reading-order GFM Markdown projector | `MarkdownProjector` |
| [`projectors/chunks_projector.py`](../../../src/opendv/parsing/projectors/chunks_projector.py) | Fixed-schema `ChunkRecord` JSON / JSONL projector | `ChunksProjector`, `MAX_CHUNK_CHARS` |

### Related Cross-Layer Touchpoints

| Layer | File(s) | Role Relative to `opendv.parsing` |
| :--- | :--- | :--- |
| **Layer 1 (`models`)** | `document_ir.py`, `pii_tokenizer.py`, `wire_dtos_vault.py`, `__init__.py` | `SourceBlob`, `ParsedDocumentIR`, `ChunkRecord`, `OcrBox`, `OcrProviderFn`, `ProfileBundle.document_maps`, consolidated `REDACTED_LEAK_RE` / `strip_pii_spans`. |
| **Layer 2 (`verifier`)** | `verifier/_v4_unstructured.py` | Gate `V4` checks consuming Layer-1 normalization and `VerifierContext`. |
| **Layer 4 (`service`)** | `parsing_svc.py`, `_mcp_parse_tools.py`, `_ext_mcp_guards.py`, `_mcp_ports.py`, `_remediation.py`, `citations.py`, `mcp_broker.py`, `_mcp_tools.py`, `_mcp_scout_tools.py`, `workflows/{discovery_svc,_discovery_*,_source_docs,_vault_helpers,modeling_stateless,_modeling_stateless_helpers}.py` | Workspace containment (`resolve_local_source_blob`), `ParsingService`, MCP parsing tools & `SpilloverRef`, `parsed_pages: dict[str, dict[int, str]]`, and grounding/modeling bridges. |
| **Layer 5 (`cli`)** | `_commands_parse.py`, `_commands_grounding.py`, `commands_core.py`, `commands_model.py`, `mcp_stdio.py`, `main.py`, `export_contracts.py` | Stateless `opendv parse`, `--input --doc`, `model profile` plain summary, unified `opendv mcp serve --profile project\|parsing\|all`, and `schemas/v2/` exports. |

---

## 3. Key Design Principles & Non-Negotiable Invariants

1. **Modular Token Budget (`D16` `< 5,000` tokens/module):**
   Every `.py` module under `src/opendv/` must stay strictly below `5,000` `cl100k_base` tokens ([`tests/architecture/test_module_token_budget.py`](../../../tests/architecture/test_module_token_budget.py)).
2. **Hardening & Literal Guards (`T14`, `test_no_hardcoded_literals.py`, `test_clone_detection.py`):**
   - Zero `/tmp`, `expanduser`, `.home()`, `examples/`, `docs/`, `127.0.0.1`, `localhost`, or non-exempt `http://` literals outside allowlisted modules (including CLI docstrings and `help=` text).
   - Zero module-level ALL-CAPS constant names containing `THRESHOLD` or `MAGIC` outside `EXACT_CONFIG_MODULE_ALLOWLIST`.
   - Zero duplicate code blocks `>= 8` lines across `cli`, `api`, `service`, `sources`, `parsing`, `grounding`, and `agents`.
3. **Immutable Internal IR vs. Non-Destructive Redaction:**
   Keep internal raw `ParsedDocumentIR` unmodified so Gate `V4` (`citations.py`) exact-quote offsets remain intact. Apply `redact_ir` only when `redaction="on"` is requested (`MODEL_EGRESS`), recomputing `canonical_text_sha256` and `raw_text_length` so redacted IRs validate cleanly.
4. **Validation vs. Leak Separation:**
   `parsing/validate.py` never calls `ensure_no_redacted_placeholder`; placeholder rejection lives exclusively at raw downstream boundaries (`citations.py`, Gate `V4`, grounding rerankers).
5. **Uniform `ChunkRecord` JSONL Schema:**
   `ChunksProjector.stream_jsonl()` always emits full `ChunkRecord` keys without `exclude_none=True` and serializes `table_json` / `bbox_json` via RFC 8785 `dumps_jcs` so DuckDB `read_ndjson` never encounters polymorphic column types.

---

## 4. Common Extension Recipes

### Adding a New `FormatParser`
1. Implement `FormatParser` (`parser_id`, `parser_version`, `supports_render`, `inspect`, `parse`, `render_page`) in a new module under `src/opendv/parsing/parsers/`.
2. Extend `DocumentFormat` and `DOCUMENT_FORMAT_BY_EXTENSION` in `src/opendv/models/document_ir.py`, and register the parser in `default_registry()` (`src/opendv/parsing/_registry.py`) or pass an explicit `ParsingRegistry`.
3. Ensure mask-before-split behavior on virtual slices and verify `tests/architecture/test_import_contracts.py` and `test_module_token_budget.py`.

### Adding a New `IRProjector`
1. Implement `IRProjector` (`target_id`, `project`, `stream_jsonl`) in `src/opendv/parsing/projectors/`.
2. Register the target in `TargetProjection` (`models/document_ir.py`), `TargetProjectionOpt` (`cli/_commands_parse.py`), and `default_registry()`.
3. Enforce `mask_pii_text()` across all text fields when `redaction == "on"`.

### Adding a CLI Option or MCP Tool Parameter
1. Update `src/opendv/cli/_commands_parse.py` or `src/opendv/service/_mcp_parse_tools.py` / `cli/commands_core.py`.
2. Regenerate `schemas/v2/cli-spec.json` (`uv run python -m opendv.cli.export_contracts`) and `docs/16-cli-reference.md` (`uv run python scripts/generate_cli_reference.py`), verifying `git diff schemas/v2/openapi.json` stays empty.
3. Update `user-guide.md` and `.agents/skills/opendv-parsing/SKILL.md` so `tests/docs/` passes.

---

## 5. Local Verification & PR Checklist

```bash
# 1. Parsing unit, containment, redaction, E2E & CLI/MCP tests (non-live)
uv run pytest tests/parsing/ tests/cli/ tests/parity/ -m "not live_provider" -o addopts="" -rs -q

# 2. Architecture DAG, D16 (<5k tokens), clone detection, T14 & doc sync gates
uv run pytest tests/architecture/ tests/docs/ -m "not live_provider" -o addopts="" -rs -q

# 3. CLI/OpenAPI contract parity & closed ErrorCode AST check
uv run python -m opendv.cli.export_contracts --check
uv run python scripts/check_error_codes.py --catalog schemas/v2/error-codes.json --baselines schemas/v2/baseline-digests.json --check-ast

# 4. Lint & strict typecheck
uv run ruff check src tests scripts
uv run mypy src tests/helpers
```
