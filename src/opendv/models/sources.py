"""Layer-1 Pydantic v2 models for data sources, Graph B profiles, DocumentMap, and MCP."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from opendv.models.diagnostics import StrictModel
from opendv.models.modeling_preferences import (
    ColumnFormatShape,
    PkContainmentOverlap,
    ValueFreqItem,
)

CanonicalSentinel = Literal[
    "",
    "N/A",
    "NA",
    "NONE",
    "NULL",
    "UNKNOWN",
    "TBD",
    "-1",
    "-2",
]

SurrogateKind = Literal[
    "none",
    "monotonic_int",
    "random_uuid_v4",
    "prefixed_operational_id",
]

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


class ColumnShapeStats(StrictModel):
    """Pre-masking column value shape, pattern, and sentinel summary."""

    profiled_non_null_count: int = Field(ge=0)
    distinct_ratio: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    length_min: int | None = Field(default=None, ge=0)
    length_max: int | None = Field(default=None, ge=0)
    numeric_min: float | None = Field(default=None, allow_inf_nan=False)
    numeric_max: float | None = Field(default=None, allow_inf_nan=False)
    looks_like_enum: bool = False
    observed_enum_values: list[str] = Field(default_factory=list)
    surrogate_kind: SurrogateKind = "none"
    structural_patterns: dict[str, float] = Field(default_factory=dict)
    detected_sentinels: list[CanonicalSentinel] = Field(default_factory=list)
    pii_suppressed: bool = False

    @model_validator(mode="after")
    def _validate_pii_suppression(self) -> ColumnShapeStats:
        if self.pii_suppressed and (
            self.length_min is not None
            or self.length_max is not None
            or self.numeric_min is not None
            or self.numeric_max is not None
            or self.observed_enum_values
        ):
            raise ValueError(
                "pii_suppressed=True requires length/numeric bounds to be None "
                "and observed_enum_values to be empty"
            )
        if (
            self.length_min is not None
            and self.length_max is not None
            and self.length_min > self.length_max
        ):
            raise ValueError(
                f"length_min ({self.length_min}) must be <= length_max ({self.length_max})"
            )
        if (
            self.numeric_min is not None
            and self.numeric_max is not None
            and self.numeric_min > self.numeric_max
        ):
            raise ValueError(
                f"numeric_min ({self.numeric_min}) must be <= numeric_max ({self.numeric_max})"
            )
        for pat_key, pat_val in self.structural_patterns.items():
            if not math.isfinite(pat_val) or pat_val < 0.0 or pat_val > 1.0:
                raise ValueError(
                    f"structural_patterns['{pat_key}'] must be finite in [0.0, 1.0], got {pat_val}"
                )
        return self


class ColumnProfile(StrictModel):
    """Physical column profile in Graph B (up to 5 masked sample values)."""

    col_name: str = Field(description="Physical column name.")
    sql_type: str = Field(description="Source SQL data type string.")
    nullable: bool = Field(description="Whether the physical column permits NULL values.")
    is_pk: bool = Field(description="True if declared as part of the source primary key.")
    pk_ord: int | None = Field(default=None, description="1-based primary key ordinal position.")
    fk_ref: str | None = Field(
        default=None,
        description="Declared foreign key target in 'table.column' form.",
    )
    null_ratio: float = Field(
        ge=0.0,
        le=1.0,
        description="Observed fraction of NULL values across profiled rows.",
    )
    distinct_count_est: int = Field(
        ge=0,
        description="Estimated count of distinct non-null values.",
    )
    pii_tags: list[str] = Field(
        default_factory=list,
        description="Detected PII or secret classification tags.",
    )
    sample_values: list[str | None] = Field(
        default_factory=list,
        max_length=5,
        description="Up to 5 masked sample values for column inspection.",
    )
    format_shape: ColumnFormatShape = Field(
        default="MIXED",
        description="Deterministic column literal shape classification.",
    )
    surrogate_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Surrogate identifier likelihood in [0.0, 1.0].",
    )
    top_value_frequencies: list[ValueFreqItem] = Field(
        default_factory=list,
        description="PII-redacted top frequent values when distinct <= 20.",
    )
    shape_stats: ColumnShapeStats | None = Field(
        default=None,
        description="Pre-masking column value shape, pattern, and sentinel summary.",
    )


class TableProfile(StrictModel):
    """Physical table profile inside SourceProfile."""

    table_id: str = Field(description="Canonical table identifier.")
    table_name: str = Field(description="Physical table name in the source.")
    row_count_est: int = Field(ge=0, description="Estimated physical row count.")
    columns: list[ColumnProfile] = Field(description="Ordered physical column profiles.")
    pk_uniqueness_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Primary-key uniqueness ratio over sampled/total rows.",
    )
    best_non_surrogate_bk_candidate_col: str | None = Field(
        default=None,
        description="Highest-scoring non-surrogate natural BK candidate column.",
    )
    best_non_surrogate_bk_uniqueness_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Uniqueness ratio of best non-surrogate BK candidate column.",
    )
    cluster_id: str | None = Field(
        default=None,
        description="Assigned deterministic cluster identifier.",
    )


class SourceProfile(StrictModel):
    """Graph B physical schema profile for a structured source."""

    source_id: str = Field(description="Attached source identifier.")
    schema_fingerprint: str = Field(description="Canonical source schema fingerprint.")
    table_count: int = Field(ge=0, description="Number of profiled tables in this source.")
    tables: list[TableProfile] = Field(description="Profiled table descriptors.")
    pk_containment_overlaps: list[PkContainmentOverlap] = Field(
        default_factory=list,
        description="Asymmetric FK-to-PK local containment overlaps across sources.",
    )


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
    "CanonicalSentinel",
    "ColumnProfile",
    "ColumnShapeStats",
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
    "SurrogateKind",
    "TableIndexEntry",
    "TablePreviewPage",
    "TableProfile",
]
