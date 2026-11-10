"""Evidence and Document Ingestion DTOs (Task 4B.1 / Issue #201).

Defines canonical data transfer objects for document ingestion, page offset mapping,
tabular structures, taint classification, and chunked evidence spans consumed across
Open-DV ingestion pipelines and the Ontology Construction Studio.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import AliasChoices, Field, model_validator

from opendv_contracts.dtos.common import BaseContractModel


class TaintClassification(StrEnum):
    """Data classification taint levels for ingested evidence and documents."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"

    @classmethod
    def _missing_(cls, value: object) -> TaintClassification | None:
        if isinstance(value, str):
            val_lower = value.lower().strip()
            for member in cls:
                if member.value == val_lower:
                    return member
        return None


class EvidenceChunk(BaseContractModel):
    """Atomic text span extracted from an ingested source document for grounding."""

    chunk_id: str = Field(..., description="Unique chunk UUID or identifier")
    document_id: str = Field(..., description="Source document identifier")
    text: str = Field(..., min_length=1, description="Normalized chunk text content")
    page_number: int | None = Field(
        default=None, ge=1, description="1-indexed physical document page number"
    )
    start_char: int | None = Field(
        default=None, ge=0, description="Unicode code point start offset (inclusive)"
    )
    end_char: int | None = Field(
        default=None, ge=0, description="Unicode code point end offset (exclusive)"
    )
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Extraction or recognition confidence"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary chunk metadata")
    bounding_box: tuple[float, float, float, float] | None = Field(
        default=None, description="Normalized bounding box coordinates (x0, y0, x1, y1)"
    )
    is_table: bool = Field(default=False, description="Whether this chunk represents tabular data")
    table_id: str | None = Field(
        default=None, description="Associated table identifier if is_table is True"
    )
    taint: TaintClassification = Field(
        default=TaintClassification.INTERNAL,
        validation_alias=AliasChoices("taint", "taint_classification"),
        description="Taint classification clearance level",
    )
    source_sha256: str | None = Field(
        default=None, description="SHA-256 digest of original raw document bytes"
    )
    utf16_start: int | None = Field(
        default=None, ge=0, description="Browser DOM UTF-16 code unit start offset (inclusive)"
    )
    utf16_end: int | None = Field(
        default=None, ge=0, description="Browser DOM UTF-16 code unit end offset (exclusive)"
    )

    @model_validator(mode="after")
    def _validate_offsets(self) -> EvidenceChunk:
        if (
            self.start_char is not None
            and self.end_char is not None
        ):
            if self.start_char > self.end_char:
                raise ValueError(
                    f"start_char ({self.start_char}) must be <= end_char ({self.end_char})"
                )
            expected_len = self.end_char - self.start_char
            if expected_len != len(self.text):
                raise ValueError(
                    f"Span length (end_char {self.end_char} - start_char {self.start_char} = "
                    f"{expected_len}) does not match text length ({len(self.text)})"
                )
        if (
            self.utf16_start is not None
            and self.utf16_end is not None
            and self.utf16_start > self.utf16_end
        ):
            raise ValueError(
                f"utf16_start ({self.utf16_start}) must be <= utf16_end ({self.utf16_end})"
            )
        return self

    @property
    def start_offset(self) -> int | None:
        """Alias for start_char."""
        return self.start_char

    @property
    def end_offset(self) -> int | None:
        """Alias for end_char."""
        return self.end_char

    @property
    def taint_classification(self) -> TaintClassification:
        """Alias for taint."""
        return self.taint


class PageMapEntry(BaseContractModel):
    """Character span offset mapping and metadata for an individual document page."""

    page_number: int = Field(..., ge=1, description="1-indexed document page number")
    text: str = Field(default="", description="Normalized text content on this page")
    start_offset: int = Field(
        ...,
        ge=0,
        validation_alias=AliasChoices("start_offset", "start_char"),
        description="Start Unicode character offset in full document text (inclusive)",
    )
    end_offset: int = Field(
        ...,
        ge=0,
        validation_alias=AliasChoices("end_offset", "end_char"),
        description="End Unicode character offset in full document text (exclusive)",
    )
    utf16_start: int | None = Field(
        default=None, ge=0, description="UTF-16 code unit start index for browser DOM selection"
    )
    utf16_end: int | None = Field(
        default=None, ge=0, description="UTF-16 code unit end index for browser DOM selection"
    )
    is_scanned: bool = Field(
        default=False, description="Whether page consists predominantly of raster/scanned image"
    )
    ocr_skipped: bool = Field(
        default=False, description="Whether OCR extraction was skipped due to missing engine"
    )
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Page extraction confidence score"
    )
    bounding_box: tuple[float, float, float, float] | None = Field(
        default=None, description="Normalized bounding box coordinates (x0, y0, x1, y1)"
    )
    has_tables: bool = Field(
        default=False, description="Whether page contains extracted structured tables"
    )

    @model_validator(mode="after")
    def _validate_offsets(self) -> PageMapEntry:
        if self.start_offset > self.end_offset:
            raise ValueError(
                f"start_offset ({self.start_offset}) must be <= end_offset ({self.end_offset})"
            )
        diff = self.end_offset - self.start_offset
        if self.text and diff != len(self.text):
            raise ValueError(
                f"Span length (end_offset {self.end_offset} - start_offset {self.start_offset} = "
                f"{diff}) does not match text length ({len(self.text)})"
            )
        return self

    @property
    def start_char(self) -> int:
        """Alias for start_offset."""
        return self.start_offset

    @property
    def end_char(self) -> int:
        """Alias for end_offset."""
        return self.end_offset

    @property
    def char_count(self) -> int:
        """Character count on this page."""
        if self.text:
            return len(self.text)
        return max(0, self.end_offset - self.start_offset)


class TableStructure(BaseContractModel):
    """Structured table extracted from a document page."""

    table_id: str = Field(..., description="Unique table identifier")
    page_number: int | None = Field(
        default=None, ge=1, description="1-indexed page number containing the table"
    )
    headers: list[str] = Field(default_factory=list, description="Column header labels")
    rows: list[list[str]] = Field(default_factory=list, description="Table body row cell values")
    caption: str | None = Field(default=None, description="Table title or caption text")
    raw_html_or_md: str | None = Field(
        default=None, description="Raw HTML or Markdown table representation"
    )
    bounding_box: tuple[float, float, float, float] | None = Field(
        default=None, description="Bounding box of table on page"
    )

    @property
    def row_count(self) -> int:
        """Number of rows in the table body."""
        return len(self.rows)

    @property
    def column_count(self) -> int:
        """Number of columns in the table."""
        if self.headers:
            return len(self.headers)
        if self.rows:
            return len(self.rows[0])
        return 0


class IngestionResult(BaseContractModel):
    """Normalized result of document ingestion with chunked evidence and page map."""

    document_id: str = Field(..., description="Unique document identifier")
    filename: str = Field(..., description="Original filename")
    mime_type: str = Field(..., description="MIME media type")
    total_pages: int = Field(default=0, ge=0, description="Total physical pages in document")
    total_characters: int = Field(
        default=0, ge=0, description="Total character count in normalized text"
    )
    chunks: list[EvidenceChunk] = Field(
        default_factory=list, description="Extracted evidence chunks"
    )
    page_map: list[PageMapEntry] = Field(
        default_factory=list, description="Per-page character and offset map"
    )
    tables: list[TableStructure] = Field(
        default_factory=list, description="Extracted structured tables"
    )
    taint: TaintClassification = Field(
        default=TaintClassification.INTERNAL,
        validation_alias=AliasChoices("taint", "taint_classification"),
        description="Taint classification level",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary ingestion metadata"
    )
    source_sha256: str | None = Field(
        default=None, description="SHA-256 digest of original raw document bytes"
    )
    normalized_text: str | None = Field(
        default=None, description="Complete normalized text content with LF line endings"
    )

    @property
    def char_count(self) -> int:
        """Total character count."""
        return self.total_characters

    @property
    def text(self) -> str:
        """Convenience accessor for normalized text."""
        return self.normalized_text or ""

    @property
    def doc_id(self) -> str:
        """Alias for document_id."""
        return self.document_id

    @property
    def media_type(self) -> str:
        """Alias for mime_type."""
        return self.mime_type


__all__ = [
    "EvidenceChunk",
    "IngestionResult",
    "PageMapEntry",
    "TableStructure",
    "TaintClassification",
]
