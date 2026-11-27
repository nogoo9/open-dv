---
name: opendv-parsing
description: >-
  Stateless local unstructured document parsing workflow for external coding
  agents: inspect capabilities and IR schemas via opendv parse spec, classify
  digital vs. scanned pages via opendv parse inspect, extract layout IR
  (ParsedDocumentIR) via opendv parse extract, render visual pages with
  fail-closed PII blackout via opendv parse render-page, validate integrity via
  opendv parse validate, stream DuckDB-ready ChunkRecord JSONL or Markdown via
  opendv parse export, or launch the 6 zero-LLM MCP parsing tools via opendv
  mcp serve --profile parsing.
---

# Open-DV Unstructured Parsing Skill (`opendv parse` & MCP `--profile parsing`)

Use this workflow to inspect, parse, visually render, validate, and project local `.pdf`, `.docx`, `.md`/`.markdown`, and `.txt` documents into canonical `ParsedDocumentIR` and DuckDB-ready `ChunkRecord` JSONL without creating `.opendv/` project state or calling internal LLMs.

> **CLI Flag Contract:** Use only flags declared in `schemas/v2/cli-spec.json` (`--json` is a global flag placed before `parse` or `mcp`). `opendv parse` is strictly local/stateless, performs zero `.opendv/` disk writes, and rejects `--api-url` / `OPENDV_API_URL` (`exit 2`, `SYNTAX_INVALID_ARGUMENT`).

---

## Step 1: Inspect Schemas, Formats & Tier-1 Capabilities (`parse spec`)

Retrieve the live JSON Schemas (`ParsedDocumentIR`, `ChunkRecord`, `InspectResult`, `RenderPageResult`, `ValidationDiagnostic`, `ParseOptions`, `SpilloverRef`), supported formats (`pdf`, `docx`, `markdown`, `domain_note`), projectors (`document_map`, `markdown`, `chunks`), and installed driver capabilities (`liteparse`, `python-docx`, `Pillow`, optional `pytesseract` / `tesseract`):

```bash
uv run opendv --json parse spec
```

---

## Step 2: Inspect Page Counts & Scan Density (`parse inspect`)

Classify digital vs. scanned/low-density pages before full extraction. Notice `inspect` scans metadata across all pages without blocking on `--max-pages`:

```bash
uv run opendv --json parse inspect report.pdf --allow-root workspace_docs
```

Key fields in `InspectResult`:
- `format_id`: `"pdf" | "docx" | "markdown" | "domain_note"`.
- `page_count`, `digital_pages`, `scanned_pages`: 1-based page lists using `is_page_scanned_or_low_density(non_ws_chars, image_xobjects)`.
- `renderable`: `true` for PDFs, `false` for `.docx`/`.md`/`.txt`.

---

## Step 3: Extract Layout IR (`parse extract`)

Extract raw `ParsedDocumentIR` under `LOCAL_HUMAN_IO` (default) or apply `MODEL_EGRESS` PII masking with `--redact`. Use `--pages` to push page ranges down to `LiteParse`:

```bash
# Local unredacted inspection (pushes pages 1-2 and 5 down to LiteParse)
uv run opendv --json parse extract report.pdf --pages 1-2,5 --out base_ir.json

# MODEL_EGRESS redacted IR copy (recomputes canonical_text_sha256 on masked slices)
uv run opendv --json parse extract report.pdf --pages 1-2,5 --redact --out redacted_ir.json

# Enable optional OCR ([ocr] + system tesseract) on scanned pages
uv run opendv --json parse extract report.pdf --ocr --out ocr_ir.json
```

When `--out FILE` is supplied, `opendv parse extract` writes `ParsedDocumentIR` atomically with owner-only ACLs and prints summary metadata (`output_path`, `byte_size`, `sha256`, `format_id`, `page_count`, `block_count`, `redacted`) to stdout.

---

## Step 4: Render Page PNGs & Enforce Scan/Redaction Gates (`parse render-page`)

Render a 1-based physical PDF page to PNG (written to `--out FILE` or returned inline as `png_b64`):

```bash
# Standard digital page rendering to disk
uv run opendv --json parse render-page report.pdf --page 1 --dpi 150 --out page1.png

# MODEL_EGRESS rendering with span-aligned Pillow PII blackout
uv run opendv --json parse render-page report.pdf --page 1 --redact --out page1_redacted.png

# Explicit LOCAL_HUMAN_IO viewing of a scanned page (without --redact)
uv run opendv --json parse render-page report.pdf --page 4 --approve-scan --out page4.png
```

Gate behavior:
- `--redact` (`MODEL_EGRESS`) blackouts PII word boxes and **never** lets `--approve-scan` bypass OCR/redaction on scanned pages (fails closed with `UNREDACTED_SCAN_BLOCKED` or `DRIVER_MISSING`).
- Non-renderable formats (`.docx`, `.md`, `.txt`), out-of-range `--page`, and `--dpi > 300` fail with `SYNTAX_INVALID_ARGUMENT` (`exit 2`). Pre-render pixel budgets exceeding `40_000_000` pixels fail with `ARCHIVE_SIZE_LIMIT_EXCEEDED` (`exit 2`).

---

## Step 5: Validate `ParsedDocumentIR` Integrity (`parse validate`)

Verify `provenance.page_count`, `PageSlice.canonical_text_sha256`, `raw_text_length`, monotonic `reading_order`, `PageSpan` bounds, and `DocumentOutlineNode` consistency:

```bash
uv run opendv --json parse validate base_ir.json --out validate_diags.json
```

Exit codes: `0` (`ok: true`) when zero error diagnostics exist; `2` (`VERIFICATION_GATE_FAILED`) with `data` containing `ValidationDiagnostic[]` when integrity checks fail.

---

## Step 6: Project to `chunks`, `markdown`, or `document_map` (`parse export`)

Stream uniform-schema `ChunkRecord` JSONL (ready for DuckDB `read_ndjson`) or emit Markdown / `DocumentMap` JSON:

```bash
# Stream uniform ChunkRecord JSONL to disk (required when --json is set)
uv run opendv --json parse export base_ir.json --target chunks --format jsonl --out chunks.jsonl

# Project redacted GFM Markdown
uv run opendv --json parse export base_ir.json --target markdown --redact --out doc.md

# Project DocumentMap + PageTextView JSON
uv run opendv --json parse export base_ir.json --target document_map --redact --out doc_map.json
```

> **Note:** `opendv --json parse export --format jsonl` requires `--out FILE` (`exit 2` `SYNTAX_INVALID_ARGUMENT` if omitted).

---

## MCP Server Profile (`opendv mcp serve --profile parsing | all`)

Launch a zero-project, zero-LLM MCP stdio server exposing the `6` parsing tools (`parse_spec`, `parse_inspect`, `parse_extract`, `parse_render_page`, `parse_validate_ir`, `parse_export_ir`):

```bash
uv run opendv mcp serve --transport stdio --profile parsing --allow-root workspace_docs
```

Or expose all `23` tools (`17` project lifecycle + `6` parsing) concurrently:

```bash
uv run opendv mcp serve --transport stdio --profile all --project demo_proj --allow-root workspace_docs
```

MCP defaults & spillover behavior:
- **Fail-closed `MODEL_EGRESS` defaults:** `parse_extract` and `parse_export_ir` default to `redact=True`, and `parse_render_page` defaults to `MODEL_EGRESS` unless the operator passes `--allow-unredacted` (with `--no-allow-unredacted` as the explicit negative flag).
- **`SpilloverRef`:** Payloads exceeding `--max-inline-bytes` (default `65536`) spill atomically with owner-only ACLs into `--spill-dir` (default `<OS_USER_STATE>/opendv/parse_spill`) and return `{"spilled": true, "spill_path": ..., "byte_size": ..., "sha256": ..., "format": ...}`. Pass `spill_path` back as `ir_path` to `parse_validate_ir` or `parse_export_ir`.
- **Flag hygiene:** `--profile project` (default) rejects `--allow-root`, `--allow-unredacted`, `--max-inline-bytes`, and `--spill-dir`; `--profile parsing` rejects `--project`.

---

## Error Codes & Remediation Summary

| `ErrorCode` | Exit | Cause & Agent Action |
| :--- | :---: | :--- |
| `SYNTAX_INVALID_ARGUMENT` | `2` | Bad `--pages`, out-of-range `--page`, `--dpi > 300`, `--api-url` on `parse`, `--json --format jsonl` without `--out`, or invalid MCP `--profile` flag combinations. |
| `UNSUPPORTED_MEDIA_TYPE` | `2` | File extension/MIME outside `.pdf`, `.docx`, `.md`/`.markdown`, `.txt`. |
| `DRIVER_MISSING` | `2` | `--ocr` requested without `pytesseract` (`uv sync --extra ocr`) or system `tesseract` on `PATH` (`actor="human"` action). |
| `UNREDACTED_SCAN_BLOCKED` | `2` | Scanned page blocked under `MODEL_EGRESS` without OCR, under `LOCAL_HUMAN_IO` without `--approve-scan`, or unaligned PII word boxes. |
| `VERIFICATION_GATE_FAILED` | `2` | Tampered/invalid IR in `parse validate`, password-locked/corrupt PDF, or OLE2 encrypted `.docx`. |
| `ARCHIVE_SECURITY_VIOLATION` | `2` | Traversal, symlink, UNC, Windows ADS `:`, `--allow-root` escape, or XML `DOCTYPE`/`ENTITY` in `.docx`. |
| `ARCHIVE_SIZE_LIMIT_EXCEEDED` | `2` | Input `> 250 MiB`, `> max_pages`, `> 40_000_000` render pixels, or `.docx` zip-bomb limit (`500` entries / `250 MiB` / `100.0` ratio above `1 MiB`). |
| `RESOURCE_NOT_FOUND` | `3` | Missing local input file or `ir.json` path. |
