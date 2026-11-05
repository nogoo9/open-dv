"""Data stream models for byte streaming and batch processing."""

from __future__ import annotations

from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel


class StreamChunk(BaseContractModel):
    """A sequential chunk of raw bytes from a streaming source."""

    chunk_index: int = Field(ge=0, description="0-based sequence index")
    data: bytes = Field(..., description="Raw byte payload")
    is_last: bool = Field(default=False, description="True if final chunk in stream")
    total_bytes: int | None = Field(default=None, description="Total byte size if known")


class SourceMetadata(BaseContractModel):
    """Low-level metadata reported by a source adapter."""

    byte_size: int | None = Field(default=None, description="Total size in bytes if known")
    content_type: str | None = Field(default=None, description="Reported MIME content type")
    modified_at: str | None = Field(default=None, description="ISO timestamp of last modification")
    etag: str | None = Field(default=None, description="Entity tag or remote version token")
    headers: dict[str, str] = Field(
        default_factory=dict, description="Supplementary headers or attributes"
    )


class BatchRead(BaseContractModel):
    """Metadata for a tabular batch read."""

    batch_index: int = Field(ge=0, description="0-based batch index")
    row_count: int = Field(ge=0, description="Number of rows in batch")


__all__ = [
    "BatchRead",
    "SourceMetadata",
    "StreamChunk",
]
