# Open-DV Unstructured Parsing Component (`src/opendv/parsing/`)

The **Unstructured Parsing Component** (`src/opendv/parsing/`, Layer 2 + Layer-4 `src/opendv/service/parsing_svc.py`) extracts deterministic, span-grounded layout IR (`ParsedDocumentIR`), visual page PNGs with fail-closed PII blackout, and DuckDB-ready `ChunkRecord` JSONL from local `.pdf`, `.docx`, `.md`/`.markdown`, and `.txt` documents—without runtime `pypdf` or `pypdfium2` dependencies.

- **Pure Layer-2 SPI (`FormatParser` & `IRProjector`):** `LiteParsePdfParser` (`liteparse`), `DocxParser` (`BoundedZipReader` + DOM-ordered `python-docx`), and `MarkdownTextParser` (stdlib) produce canonical `ParsedDocumentIR` projected by `DocumentMapProjector`, `MarkdownProjector`, and `ChunksProjector`.
- **Sink-Class Isolation (`LOCAL_HUMAN_IO` vs. `MODEL_EGRESS`):** Keeps raw normalized `PageSlice` text immutable for Gate `V4` (`citations.py`), while producing redacted IR copies, masked projections, and span-aligned `Pillow` blackouts (plus optional `[ocr]` `pytesseract`) for outbound/agent flows.
- **Stateless CLI & Unified Inbound MCP:** Exposes `opendv [--json] parse {spec, inspect, extract, render-page, validate, export}` and `opendv mcp serve --profile project|parsing|all` (`17`/`6`/`23` tools with atomic `SpilloverRef` output) with zero `.opendv/` project writes.
- **Steps 2–4 & Cross-Component Bridges:** Powers project source attachment/preview, `DiscoveryService.ground_ontology()` (`--project` and `--input --doc`), and `ProfileBundle.document_maps` + Mode-1/Mode-2 Gate `V4` verification in `opendv.modeling`.

## Component Documentation Suite (`REQ-DOC-01`)

| Document | Audience | Contents |
| :--- | :--- | :--- |
| **Component README** ([`docs/components/parsing/README.md`](README.md)) | All | Overview, component surfaces, and navigation index (this file). |
| **User Guide** ([`user-guide.md`](user-guide.md)) | Operators & External Agents | 5-layer placement, CLI/MCP/Python workflows, full parameter reference, worked examples, exact PII regex & redaction limits, and troubleshooting. |
| **Architecture Design** ([`unstructured-parsing-engine.md`](../../designs/unstructured-parsing-engine.md)) | Architects & Reviewers | End-to-end architecture, DAG boundaries, Layer-1/2/4/5 contracts, alternatives considered, security controls, and risk mitigations. |
| **Contributor Guide** ([`contributor-guide.md`](contributor-guide.md)) | Maintainers & Developers | Layer-2 `V2_LAYER_BANS` contract, 16-module map, `< 5,000`-token `D16` & `T14` invariants, extension recipes, and verification gates. |
| **External Agent Skill** ([`SKILL.md`](../../../.agents/skills/opendv-parsing/SKILL.md)) | External Coding Agents | Step-by-step protocol for `opendv parse` and `opendv mcp serve --profile parsing|all`. |
