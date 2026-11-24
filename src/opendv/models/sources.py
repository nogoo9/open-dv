"""Layer-1 Pydantic v2 models for data sources, Graph B profiles, DocumentMap, and MCP."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from opendv.models.diagnostics import StrictModel

SourceKind = Literal[
    "sqlite",
    "duckdb",
    "parquet",
    "csv",
    "xlsx",
    "databricks_uc",
    "pdf",
    "markdown",
    "docx",
    "domain_note",
]

SourceStatus = Literal[
    "READY_OFFLINE",
    "NEEDS_LOCAL_SECRET_BINDING",
    "METADATA_ONLY_BINARY_ABSENT",
]


class ConnectorFieldSpec(StrictModel):
    """Metadata descriptor for a single connector configuration form field."""

    key: str
    label: str
    input_kind: Literal["text", "secret", "path", "select"]
    required: bool
    secret_ref: bool
    options: list[str] = Field(default_factory=list)


class ConnectorAdapterSpec(StrictModel):
    """Static descriptor for one of the 6 structured connector adapters."""

    adapter_id: str
    display_name: str
    fields: list[ConnectorFieldSpec]


class ConnectorConfigPayload(StrictModel):
    """Persisted non-sensitive configuration for a connector profile or attached source."""

    adapter_id: str
    display_name: str
    uri_or_path: str = ""
    options: dict[str, str | int | bool] = Field(default_factory=dict)
    secret_refs: list[str] = Field(default_factory=list)
    uploaded_relpath: str | None = None
    selected_tables: list[str] | None = None
    profile_timeout_seconds: int = Field(default=30, ge=1)


class SourceSummary(StrictModel):
    """Summary metadata for an attached project source."""

    source_id: str
    kind: str
    uri_or_filename: str
    status: SourceStatus
    table_count: int = Field(ge=0)
    page_count: int | None = None
    attached_at: str
    locked_in_wave: str | None = None
    connector_config: ConnectorConfigPayload | None = None


class ColumnProfile(StrictModel):
    """Physical column profile in Graph B (up to 5 masked sample values)."""

    col_name: str
    sql_type: str
    nullable: bool
    is_pk: bool
    pk_ord: int | None = None
    fk_ref: str | None = None
    null_ratio: float = Field(ge=0.0, le=1.0)
    distinct_count_est: int = Field(ge=0)
    pii_tags: list[str] = Field(default_factory=list)
    sample_values: list[str | None] = Field(default_factory=list, max_length=5)


class TableProfile(StrictModel):
    """Physical table profile inside SourceProfile."""

    table_id: str
    table_name: str
    row_count_est: int = Field(ge=0)
    columns: list[ColumnProfile]


class SourceProfile(StrictModel):
    """Graph B physical schema profile for a structured source."""

    source_id: str
    schema_fingerprint: str
    table_count: int = Field(ge=0)
    tables: list[TableProfile]


class TableIndexEntry(StrictModel):
    """MinHash-clustered table index entry exposed in Step 3a."""

    table_id: str
    source_id: str
    table_name: str
    cluster_id: str
    col_count: int = Field(ge=0)
    pii_col_count: int = Field(ge=0)
    pk_cols: list[str] = Field(default_factory=list)
    fk_refs: list[str] = Field(default_factory=list)
    badge: Literal["NEW", "UPDATED", "UNCHANGED"]
    modeled_version: str | None = None


class TablePreviewPage(StrictModel):
    """Paged row preview for a profiled table."""

    table_id: str
    source_id: str
    columns: list[str]
    rows: list[list[str | None]]
    total_rows: int = Field(ge=0)


class OutlineNode(StrictModel):
    """Document outline heading node."""

    title: str
    page_start: int = Field(ge=1)
    level: int = Field(ge=1)


class PageMapSpec(StrictModel):
    """Per-page structural metadata and canonical text digest."""

    page_number: int = Field(ge=1)
    char_count: int = Field(ge=0)
    has_images: bool
    is_scanned_or_low_density: bool
    ocr_box_count: int = Field(ge=0)
    table_headers: list[str] = Field(default_factory=list)
    heading_preview: str = ""
    canonical_text_sha256: str


class DocumentMap(StrictModel):
    """Structured map of an attached PDF, Markdown, DOCX, or domain note."""

    doc_id: str
    filename: str
    format: Literal["pdf", "markdown", "docx", "domain_note"]
    page_count: int = Field(ge=1)
    approved_scan_pages: list[int] = Field(default_factory=list)
    outline: list[OutlineNode] = Field(default_factory=list)
    pages: list[PageMapSpec] = Field(default_factory=list)


class PageTextView(StrictModel):
    """PII-masked page text returned by Step 3b and Step 4 scouts."""

    doc_id: str
    page_number: int = Field(ge=1)
    masked_text: str
    is_scanned: bool
    scan_approved: bool


def _default_allowed_stages() -> list[Literal["step4_discovery"]]:
    return ["step4_discovery"]


class ExternalMcpServerSpec(StrictModel):
    """Configuration for a user-registered external MCP server."""

    server_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    transport: Literal["stdio", "streamable_http"]
    command: list[str] | None = None
    url: str | None = None
    secret_refs: list[str] = Field(default_factory=list)
    allowed_stages: list[Literal["step4_discovery"]] = Field(
        default_factory=_default_allowed_stages
    )
    allow_scout_calls: bool = False
    allow_document_pages: bool = False
    timeout_seconds: int = Field(default=30, ge=1)
    enabled: bool = True


__all__ = [
    "ColumnProfile",
    "ConnectorAdapterSpec",
    "ConnectorConfigPayload",
    "ConnectorFieldSpec",
    "DocumentMap",
    "ExternalMcpServerSpec",
    "OutlineNode",
    "PageMapSpec",
    "PageTextView",
    "SourceKind",
    "SourceProfile",
    "SourceStatus",
    "SourceSummary",
    "TableIndexEntry",
    "TablePreviewPage",
    "TableProfile",
]
