"""Explicit Layer-2 parser and projector registry (zero entry_points discovery)."""

from __future__ import annotations

from collections.abc import Sequence

from opendv.models.document_ir import DocumentFormat, TargetProjection
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.parsing._protocols import FormatParser, IRProjector
from opendv.parsing.parsers import DocxParser, LiteParsePdfParser, MarkdownTextParser
from opendv.parsing.projectors import (
    ChunksProjector,
    DocumentMapProjector,
    MarkdownProjector,
)


class ParsingRegistry:
    """Explicit registry mapping DocumentFormat and TargetProjection identifiers."""

    def __init__(self) -> None:
        self._parsers: dict[DocumentFormat, FormatParser] = {}
        self._projectors: dict[TargetProjection, IRProjector] = {}

    def register_parser(
        self,
        formats: DocumentFormat | Sequence[DocumentFormat],
        parser: FormatParser,
    ) -> None:
        """Register a FormatParser for one or more DocumentFormat values."""
        fmt_list: Sequence[DocumentFormat] = (
            [formats] if isinstance(formats, str) else formats
        )
        for fmt in fmt_list:
            self._parsers[fmt] = parser

    def register_projector(self, projector: IRProjector) -> None:
        """Register an IRProjector keyed by its target_id."""
        self._projectors[projector.target_id] = projector

    def get_parser(self, fmt: DocumentFormat) -> FormatParser:
        """Return registered FormatParser or raise UNSUPPORTED_MEDIA_TYPE."""
        parser = self._parsers.get(fmt)
        if parser is None:
            raise OpenDVError(
                f"No parser registered for format '{fmt}'.",
                error_code=ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            )
        return parser

    def get_projector(self, target: TargetProjection) -> IRProjector:
        """Return registered IRProjector or raise SYNTAX_INVALID_ARGUMENT."""
        projector = self._projectors.get(target)
        if projector is None:
            raise OpenDVError(
                f"Unknown IR projection target '{target}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        return projector

    def supported_formats(self) -> list[DocumentFormat]:
        """Return sorted list of registered DocumentFormat identifiers."""
        return sorted(self._parsers)

    def supported_projectors(self) -> list[TargetProjection]:
        """Return sorted list of registered TargetProjection identifiers."""
        return sorted(self._projectors)


def default_registry() -> ParsingRegistry:
    """Create the default Stage-1 ParsingRegistry with all built-in adapters."""
    registry = ParsingRegistry()
    registry.register_parser("pdf", LiteParsePdfParser())
    registry.register_parser("docx", DocxParser())
    registry.register_parser(["markdown", "domain_note"], MarkdownTextParser())
    registry.register_projector(DocumentMapProjector())
    registry.register_projector(MarkdownProjector())
    registry.register_projector(ChunksProjector())
    return registry


__all__ = [
    "ParsingRegistry",
    "default_registry",
]
