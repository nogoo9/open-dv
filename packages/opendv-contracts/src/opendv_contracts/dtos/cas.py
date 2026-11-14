"""Data transfer objects for Content-Addressable Storage (CAS) source text ingestion."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from opendv_contracts.dtos.common import BaseContractModel
from opendv_contracts.dtos.ingestion import TaintClassification


class TextSourceUploadRequest(BaseContractModel):
    """Payload for ingesting free-form raw text or markdown into session CAS."""

    text: str = Field(
        ...,
        min_length=1,
        max_length=10_000_000,
        description="Raw text or markdown document content",
    )
    source_name: str | None = Field(
        default=None, max_length=256, description="Descriptive document filename or label"
    )
    format: Literal["text", "markdown"] = Field(
        default="text", description="Text format encoding: 'text' or 'markdown'"
    )
    taint: TaintClassification = Field(
        default=TaintClassification.INTERNAL,
        description="Data governance security taint level",
    )
    do_not_send_to_agent: bool = Field(
        default=False,
        description="Confidentiality taint flag (blocks cleartext dispatch to external agents)",
    )
    description: str | None = Field(
        default=None, max_length=1000, description="Optional business context or description"
    )


class AddTextSourceRequest(BaseContractModel):
    """CLI request model supporting inline text or file path."""

    text: str | None = Field(default=None, description="Inline text content to ingest")
    file: str | None = Field(default=None, description="Path to text or markdown file to ingest")
    source_name: str = Field(
        default="domain_notes.txt", description="Logical source document name in manifest"
    )
    format: Literal["text", "markdown"] = Field(default="text", description="Text format encoding")
    do_not_send_to_agent: bool = Field(default=False, description="Confidentiality taint flag")
    description: str | None = Field(default=None, description="Optional description")


class SourceUploadResponse(BaseContractModel):
    """Response returned upon persisting raw source to session CAS."""

    source_id: str = Field(..., description="Unique generated source identifier")
    source_sha256: str = Field(
        ..., pattern=r"^[a-f0-9]{64}$", description="Canonical SHA-256 digest of ingested bytes"
    )
    sha256: str | None = Field(default=None, description="Alias for source_sha256")
    file_name: str = Field(..., description="Sanitized filename")
    filename: str = Field(..., description="Backwards-compatibility alias for file_name")
    size_bytes: int = Field(..., ge=0, description="Size in bytes of ingested content")
    byte_count: int = Field(..., ge=0, description="Backwards-compatibility alias for size_bytes")
    mime_type: str = Field(default="text/plain", description="MIME media type")
    do_not_send_to_agent: bool = Field(default=False, description="Active confidentiality taint")
    provenance: str = Field(default="cas:text", description="Storage provenance URI")
    created_at: str | None = Field(default=None, description="ISO-8601 UTC creation timestamp")

    @model_validator(mode="after")
    def _populate_aliases(self) -> SourceUploadResponse:
        if self.sha256 is None:
            object.__setattr__(self, "sha256", self.source_sha256)
        return self


TextSourceUploadResponse = SourceUploadResponse

__all__ = [
    "AddTextSourceRequest",
    "SourceUploadResponse",
    "TextSourceUploadRequest",
    "TextSourceUploadResponse",
]
