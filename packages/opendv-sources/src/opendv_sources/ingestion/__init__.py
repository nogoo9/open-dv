"""Document Ingestion Framework and SPI (Task 4B.1 / Issue #201)."""

from typing import Any

from opendv_sources.exceptions import (
    OCRUnavailableError,
    OfficeConversionUnavailableError,
)
from opendv_sources.ingestion.adapter import (
    BaseDocumentAdapter,
    DocumentAdapter,
    PlainTextDocumentAdapter,
)
from opendv_sources.ingestion.layout_evaluator import (
    DEFAULT_RASTER_RATIO_THRESHOLD,
    DEFAULT_SPARSE_TEXT_THRESHOLD,
    LayoutComplexityResult,
    OCRRouteDecision,
    TesseractOCRRouter,
    calculate_page_raster_ratio,
    count_unmapped_glyphs,
    evaluate_page_complexity,
    is_tesseract_available,
)
from opendv_sources.ingestion.normalization import (
    build_normalization_offset_map,
    compute_utf16_code_units,
    compute_utf16_offset,
    compute_utf16_offset_map,
    compute_utf16_offsets_for_points,
    normalize_line_endings,
    normalize_text,
    normalize_unicode_nfc,
)
from opendv_sources.ingestion.service import (
    IngestionService,
    max_taint,
)

__all__ = [
    "DEFAULT_RASTER_RATIO_THRESHOLD",
    "DEFAULT_SPARSE_TEXT_THRESHOLD",
    "BaseDocumentAdapter",
    "DelimitedTabularAdapter",
    "DocumentAdapter",
    "IngestionService",
    "LayoutComplexityResult",
    "LibreOfficeDocumentAdapter",
    "MarkdownDocumentAdapter",
    "MarkupDocumentAdapter",
    "OCRRouteDecision",
    "OCRUnavailableError",
    "OfficeConversionUnavailableError",
    "PlainTextDocumentAdapter",
    "PyArrowTabularAdapter",
    "StructuredDataDocumentAdapter",
    "TesseractOCRRouter",
    "build_normalization_offset_map",
    "calculate_page_raster_ratio",
    "compute_utf16_code_units",
    "compute_utf16_offset",
    "compute_utf16_offset_map",
    "compute_utf16_offsets_for_points",
    "count_unmapped_glyphs",
    "evaluate_page_complexity",
    "find_soffice_binary",
    "is_tesseract_available",
    "max_taint",
    "normalize_line_endings",
    "normalize_text",
    "normalize_unicode_nfc",
]


def __getattr__(name: str) -> Any:
    """Lazy import of document adapters to prevent circular imports with opendv_sources.adapters."""
    if name in (
        "DelimitedTabularAdapter",
        "MarkdownDocumentAdapter",
        "MarkupDocumentAdapter",
        "PyArrowTabularAdapter",
        "StructuredDataDocumentAdapter",
    ):
        import opendv_sources.adapters.multiformat_adapter as _mfa

        return getattr(_mfa, name)
    if name in ("LibreOfficeDocumentAdapter", "find_soffice_binary"):
        import opendv_sources.adapters.libreoffice_adapter as _loa

        return getattr(_loa, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
