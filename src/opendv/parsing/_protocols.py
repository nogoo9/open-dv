"""Layer-2 static SPI protocols and internal render buffer contracts."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from opendv.models.document_ir import (
    ChunkRecord,
    InspectResult,
    NormalizedBBox,
    ParsedDocumentIR,
    ParseOptions,
    RedactionMode,
    SourceBlob,
    TargetProjection,
)


@dataclass(frozen=True, slots=True)
class NormalizedWordBox:
    """Normalized [0.0, 1.0] word bounding box used for Layer-2 pixel PII blackout."""

    bbox: NormalizedBBox
    text: str


@dataclass(frozen=True, slots=True)
class RenderedPageBuffer:
    """Internal Layer-2 rendered PNG buffer and optional normalized word boxes."""

    png_bytes: bytes
    width_px: int
    height_px: int
    dpi: int
    word_boxes: tuple[NormalizedWordBox, ...] = ()


@runtime_checkable
class FormatParser(Protocol):
    """Protocol implemented by deterministic local format adapters."""

    parser_id: str
    parser_version: str
    supports_render: bool

    def inspect(self, blob: SourceBlob, opts: ParseOptions) -> InspectResult:
        """Inspect page count and digital-vs-scanned status without full block extraction."""
        ...

    def parse(self, blob: SourceBlob, opts: ParseOptions) -> ParsedDocumentIR:
        """Parse a validated SourceBlob into canonical ParsedDocumentIR."""
        ...

    def render_page(
        self,
        blob: SourceBlob,
        page_number: int,
        opts: ParseOptions,
    ) -> RenderedPageBuffer:
        """Render a 1-based page to PNG bytes and optional normalized word boxes."""
        ...


@runtime_checkable
class IRProjector(Protocol):
    """Protocol implemented by deterministic ParsedDocumentIR downstream projectors."""

    target_id: TargetProjection

    def project(
        self,
        ir: ParsedDocumentIR,
        *,
        redaction: RedactionMode = "off",
        doc_id: str | None = None,
        filename: str | None = None,
        approved_scan_pages: Sequence[int] = (),
    ) -> dict[str, Any] | list[ChunkRecord] | str:
        """Project ParsedDocumentIR into the target structure."""
        ...

    def stream_jsonl(
        self,
        ir: ParsedDocumentIR,
        *,
        redaction: RedactionMode = "off",
    ) -> Iterator[str]:
        """Stream newline-delimited JSON records for the target projection."""
        ...


__all__ = [
    "FormatParser",
    "IRProjector",
    "NormalizedWordBox",
    "RenderedPageBuffer",
]
