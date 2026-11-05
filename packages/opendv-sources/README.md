# opendv-sources

`opendv-sources` defines the pluggable Service Provider Interfaces (SPI), registries, data transfer models, configuration loader, and conformance test kit for the Open-DV data source and format adapter system.

## Architectural Invariants

1. **Decision D8 (Pluggable SPI & Zero Core Drivers)**:
   This package contains only abstract interfaces, specifications, models, registries, and conformance utilities. Concrete adapters (such as local filesystem, inline bytes, DuckDB attach, text, and PDF) are provided by independent plugin packages. When unextended, attempting to resolve an unregistered adapter or format fails loudly with `UNSUPPORTED_FORMAT`.

2. **Decision D1 (In-Memory Inline Processing)**:
   `SourceSpec` supports `inline_content` and `client_path` for zero-disk operations from remote API clients.

3. **Security & Workspace Containment**:
   All filesystem and path resolutions adhere strictly to workspace boundary containment (re-exporting canonical resolver from `opendv_core.paths`). Credentials must be symbolic references (`env:VAR` or `secret:NAME`), preventing raw credential leakage in descriptors or logs.

4. **Universal In-Memory Format (PyArrow)**:
   Format parsers convert raw byte streams into `pyarrow.Table` / `RecordBatch` streams for zero-copy registration into DuckDB.

## Package Layout

- `opendv_sources.exceptions`: Typed exception hierarchy (`SourceError`, `UnsupportedFormatError`, etc.).
- `opendv_sources.models`: Immutable specifications (`SourceSpec`, `FormatSpec`), descriptors (`SourceDescriptor`, `ColumnDescriptor`), streams (`StreamChunk`, `BatchRead`), and document DTOs (`ExtractedDocument`).
- `opendv_sources.spi`: Low-level I/O (`SourceAdapter`, `FormatParser`) and high-level domain ports (`TabularSourceAdapter`, `DocumentSourceAdapter`).
- `opendv_sources.registry`: Entry-point dynamic discovery (`opendv.source_adapters`, `opendv.format_parsers`) and registry dispatch.
- `opendv_sources.config`: Unified YAML/JSON structured document loader (`load_structured_document`).
- `opendv_sources.conformance`: Reusable compliance test suites (`TabularAdapterConformanceSuite`, `DocumentAdapterConformanceSuite`).
