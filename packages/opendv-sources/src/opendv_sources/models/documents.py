"""Models for unstructured document references and normalized extractions."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel


class DocumentRef(BaseContractModel):
    """Reference to an individual document located within a source."""

    doc_id: str = Field(..., description="Unique document identifier")
    uri: str = Field(..., description="Location URI or sub-path within source")
    media_type: str = Field(
        ..., description="MIME media type (e.g. 'text/plain', 'application/pdf')"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary document metadata"
    )


class PageMapEntry(BaseContractModel):
    """Mapping of character spans to physical page numbers."""

    page_number: int = Field(ge=1, description="1-based page number")
    start_char: int = Field(ge=0, description="Inclusive start character offset in normalized text")
    end_char: int = Field(ge=0, description="Exclusive end character offset in normalized text")


class ExtractedDocument(BaseContractModel):
    """Normalized document extraction result with provenance and page map."""

    doc_id: str = Field(..., description="Unique document identifier matching DocumentRef")
    normalized_text: str = Field(..., description="Normalized text with uniform LF newlines")
    page_map: list[PageMapEntry] = Field(
        default_factory=list, description="Character offsets mapped to page numbers"
    )
    raw_sha256: str = Field(..., description="SHA-256 hex digest of raw binary document bytes")
    media_type: str = Field(..., description="Extracted document media type")
    char_count: int = Field(ge=0, description="Total characters in normalized text")


__all__ = [
    "DocumentRef",
    "ExtractedDocument",
    "PageMapEntry",
]
