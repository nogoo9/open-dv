"""Pure Layer-2 unstructured document parsing core (`opendv.parsing`)."""

from __future__ import annotations

from opendv.parsing._engine import ParsingEngine, capabilities_snapshot, parse_page_range_spec
from opendv.parsing._protocols import (
    FormatParser,
    IRProjector,
    NormalizedWordBox,
    RenderedPageBuffer,
)
from opendv.parsing._registry import ParsingRegistry, default_registry
from opendv.parsing.parsers import (
    BoundedZipReader,
    DocxParser,
    LiteParsePdfParser,
    MarkdownTextParser,
)
from opendv.parsing.projectors import (
    ChunksProjector,
    DocumentMapProjector,
    MarkdownProjector,
)
from opendv.parsing.redaction import (
    align_pii_spans_to_boxes,
    ensure_ocr_driver_available,
    find_pii_spans,
    is_ocr_driver_available,
    redact_ir,
    redact_png_boxes,
    run_pytesseract_ocr,
)
from opendv.parsing.validate import validate_ir

__all__ = [
    "BoundedZipReader",
    "ChunksProjector",
    "DocumentMapProjector",
    "DocxParser",
    "FormatParser",
    "IRProjector",
    "LiteParsePdfParser",
    "MarkdownProjector",
    "MarkdownTextParser",
    "NormalizedWordBox",
    "ParsingEngine",
    "ParsingRegistry",
    "RenderedPageBuffer",
    "align_pii_spans_to_boxes",
    "capabilities_snapshot",
    "default_registry",
    "ensure_ocr_driver_available",
    "find_pii_spans",
    "is_ocr_driver_available",
    "parse_page_range_spec",
    "redact_ir",
    "redact_png_boxes",
    "run_pytesseract_ocr",
    "validate_ir",
]
