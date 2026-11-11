"""Layout Complexity Evaluator and Tesseract OCR Routing (Task 4B.3 / Issue #203).

Provides automated evaluation of document page layout complexity to distinguish
clean vector text from scanned, image-heavy, or unmapped glyph pages, and routes
extraction to native parsing or Tesseract OCR according to configured strategy.

Specification (docs/19 §3.4, docs/21 §Task 4B.3, ADR-010):
- Low character density (< 50 chars per page).
- Raster image area ratio (> 0.40 of total page area) computed via affine
  transformation matrix tracking without pixel decoding to prevent memory bloat.
- Unmapped glyphs (CIDs like '(cid:123)', replacement character '\\ufffd', PUA).
- OCR Routing modes: 'off', 'auto', 'force':
  * 'auto': if Tesseract binary is absent or fails, log warning, record
    ocr_skipped=True in PageMapEntry, and gracefully fall back to native parsing.
  * 'force': if Tesseract binary is absent, fail loudly by raising typed
    OCRUnavailableError("OCR_UNAVAILABLE") (HTTP 501 / exit code 1).
  * 'off': skip OCR unconditionally, setting ocr_skipped=True.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from opendv_sources.exceptions import OCRUnavailableError

logger = logging.getLogger(__name__)

# Normative threshold constants (ADR-010, docs/19 §3.4)
DEFAULT_SPARSE_TEXT_THRESHOLD: int = 50
DEFAULT_RASTER_RATIO_THRESHOLD: float = 0.40
VALID_OCR_MODES: frozenset[str] = frozenset({"auto", "force", "off"})

# Regex pattern for PDF unmapped character CID notations e.g. (cid:123)
UNMAPPED_CID_PATTERN: re.Pattern[str] = re.compile(r"\(cid:\d+\)")


@dataclass(frozen=True)
class LayoutComplexityResult:
    """Structured decision output from page layout complexity evaluation.

    Attributes:
        is_complex: Whether the page requires OCR or specialized handling.
        raster_ratio: Fraction of the page area covered by raster images [0.0, 1.0].
        char_count: Printable character count on the page.
        unmapped_glyphs: Number of unmapped font glyphs or replacement characters.
        reasons: Granular list of criteria triggering the complexity flag.
    """

    is_complex: bool
    raster_ratio: float
    char_count: int
    unmapped_glyphs: int
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Convert result to a plain dictionary representation."""
        return asdict(self)


@dataclass(frozen=True)
class OCRRouteDecision:
    """Routing directive produced by TesseractOCRRouter for an individual page.

    Attributes:
        should_ocr: Whether OCR extraction should be executed for this page.
        ocr_skipped: Whether OCR was skipped (disabled or unavailable).
        action: Concrete routing action ('native', 'ocr', 'fallback', 'skipped').
        confidence: Extraction confidence score (1.0 for native/ocr, 0.8 for fallback).
        reason: Human-readable diagnostic description of the decision.
        complexity: Underlying LayoutComplexityResult that informed the decision.
    """

    should_ocr: bool
    ocr_skipped: bool
    action: str
    confidence: float
    reason: str
    complexity: LayoutComplexityResult

    def to_dict(self) -> dict[str, Any]:
        """Convert decision to a plain dictionary representation."""
        return asdict(self)


def _multiply_matrix(m2: Sequence[float], m1: Sequence[float]) -> list[float]:
    """Multiply two 2D affine transformation matrices in PDF convention.

    Computes M = M2 x M1 where each matrix is [a, b, c, d, e, f] corresponding to:
        [ a  b  0 ]
        [ c  d  0 ]
        [ e  f  1 ]
    """
    a2, b2, c2, d2, e2, f2 = m2
    a1, b1, c1, d1, e1, f1 = m1
    return [
        a2 * a1 + b2 * c1,
        a2 * b1 + b2 * d1,
        c2 * a1 + d2 * c1,
        c2 * b1 + d2 * d1,
        e2 * a1 + f2 * c1 + e1,
        e2 * b1 + f2 * d1 + f1,
    ]


def count_unmapped_glyphs(text: str | None) -> int:
    """Count unmapped glyph indicators in extracted text without raster decoding.

    Checks:
    1. '(cid:N)' tokens extracted when PDF lacks a /ToUnicode CMap.
    2. Unicode replacement characters (U+FFFD).
    3. Private Use Area (PUA) codepoints (U+E000 to U+F8FF).

    Args:
        text: Extracted text string to inspect.

    Returns:
        Integer count of unmapped glyph occurrences.
    """
    if not text:
        return 0
    cid_count = len(UNMAPPED_CID_PATTERN.findall(text))
    replacement_count = text.count("\ufffd")
    pua_count = sum(1 for ch in text if 0xE000 <= ord(ch) <= 0xF8FF)
    return cid_count + replacement_count + pua_count


def calculate_page_raster_ratio(
    page: Any,
    page_width: float | None = None,
    page_height: float | None = None,
) -> float:
    """Calculate the fraction of page area covered by raster images without decoding pixels.

    Inspects PDF content stream affine transformations (q, Q, cm, Do) and
    /Resources /XObject dictionaries. Avoids calling image decoding routines to
    prevent memory exhaustion on multi-gigabyte or high-DPI scanned documents.

    Args:
        page: A pypdf PageObject or object exposing mediabox/cropbox and resources.
        page_width: Optional explicit page width override.
        page_height: Optional explicit page height override.

    Returns:
        Float ratio in the clamped range [0.0, 1.0].
    """
    if page is None:
        return 0.0

    # 1. Determine page physical dimensions
    width = 0.0
    height = 0.0
    if page_width is not None and page_width > 0:
        width = float(page_width)
    if page_height is not None and page_height > 0:
        height = float(page_height)

    if width <= 0.0 or height <= 0.0:
        with contextlib.suppress(Exception):
            mb = getattr(page, "mediabox", None) or getattr(page, "cropbox", None)
            if mb is not None:
                width = float(abs(float(mb.right) - float(mb.left)))
                height = float(abs(float(mb.top) - float(mb.bottom)))

    if width <= 0.0 or height <= 0.0:
        return 0.0

    page_area = width * height

    # 2. Discover image XObject identifiers in resources
    image_names: set[str] = set()
    form_xobjects: dict[str, Any] = {}

    resources = getattr(page, "get", lambda k: None)("/Resources")
    if resources is None and hasattr(page, "resources"):
        resources = page.resources

    if resources is not None and (isinstance(resources, dict) or hasattr(resources, "get")):
        xobjs = resources.get("/XObject")
        if xobjs and (isinstance(xobjs, dict) or hasattr(xobjs, "items")):
            for name, xobj in xobjs.items():
                resolved = xobj.get_object() if hasattr(xobj, "get_object") else xobj
                if not (isinstance(resolved, dict) or hasattr(resolved, "get")):
                    continue
                subtype = resolved.get("/Subtype")
                str_name = str(name)
                if subtype == "/Image":
                    image_names.add(str_name)
                    if str_name.startswith("/"):
                        image_names.add(str_name[1:])
                    else:
                        image_names.add(f"/{str_name}")
                elif subtype == "/Form":
                    form_xobjects[str_name] = resolved
                    if str_name.startswith("/"):
                        form_xobjects[str_name[1:]] = resolved
                    else:
                        form_xobjects[f"/{str_name}"] = resolved

    # 3. Parse content streams to trace displayed image area via CTM tracking
    total_image_area = 0.0
    contents = getattr(page, "get_contents", lambda: None)()
    if contents is not None and image_names:
        with contextlib.suppress(Exception):
            from pypdf.generic import ContentStream

            cs = ContentStream(contents, getattr(page, "pdf", None))
            operations = getattr(cs, "operations", [])

            stack: list[list[float]] = []
            ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]

            for operands, operator in operations:
                op_name = (
                    operator.decode("latin-1") if isinstance(operator, bytes) else str(operator)
                )
                if op_name == "q":
                    stack.append(list(ctm))
                elif op_name == "Q":
                    if stack:
                        ctm = stack.pop()
                elif op_name == "cm":
                    if len(operands) == 6:
                        with contextlib.suppress(ValueError, TypeError):
                            cm_vals = [float(x) for x in operands]
                            ctm = _multiply_matrix(cm_vals, ctm)
                elif op_name == "Do":
                    if operands:
                        target = str(operands[0])
                        if target in image_names:
                            # Rendered area is determinant of the 2x2 linear portion of CTM
                            area = abs(ctm[0] * ctm[3] - ctm[1] * ctm[2])
                            total_image_area += area
                        elif target in form_xobjects:
                            # Form XObject may contain images; estimate from CTM determinant
                            area = abs(ctm[0] * ctm[3] - ctm[1] * ctm[2])
                            total_image_area += area
                elif op_name in ("BI", "ID"):
                    # Inline image rendered under current CTM
                    area = abs(ctm[0] * ctm[3] - ctm[1] * ctm[2])
                    total_image_area += area

    # Fallback heuristic: if image XObjects exist but no content stream Do parsed,
    # estimate from image XObject dimensions without decoding pixel raster
    if total_image_area <= 0.0 and image_names and resources is not None:
        with contextlib.suppress(Exception):
            xobjs = resources.get("/XObject")
            if xobjs:
                for img_name in image_names:
                    xobj = xobjs.get(img_name)
                    if xobj:
                        res = xobj.get_object() if hasattr(xobj, "get_object") else xobj
                        if isinstance(res, dict) or hasattr(res, "get"):
                            w = float(res.get("/Width", 0))
                            h = float(res.get("/Height", 0))
                            total_image_area += w * h

    ratio = total_image_area / page_area
    return min(1.0, max(0.0, float(ratio)))


def evaluate_page_complexity(
    page: Any = None,
    *,
    text: str | None = None,
    char_count: int | None = None,
    raster_ratio: float | None = None,
    unmapped_glyphs: int | None = None,
    page_width: float | None = None,
    page_height: float | None = None,
    sparse_threshold: int = DEFAULT_SPARSE_TEXT_THRESHOLD,
    raster_threshold: float = DEFAULT_RASTER_RATIO_THRESHOLD,
) -> LayoutComplexityResult:
    """Evaluate document page layout complexity to trigger OCR routing.

    Examines:
    1. Character density: sparse pages with < 50 characters.
    2. Raster image area ratio: pages with > 0.40 area covered by raster images.
    3. Unmapped glyphs: unmapped CID references or replacement characters.

    Args:
        page: Optional page object (pypdf PageObject, dict, or object with .text).
        text: Optional explicit page text string.
        char_count: Optional explicit character count override.
        raster_ratio: Optional explicit raster area ratio override [0.0, 1.0].
        unmapped_glyphs: Optional explicit unmapped glyph count override.
        page_width: Optional page physical width in points.
        page_height: Optional page physical height in points.
        sparse_threshold: Minimum character count below which page is sparse (default 50).
        raster_threshold: Maximum raster ratio above which page is complex (default 0.40).

    Returns:
        Structured LayoutComplexityResult with boolean is_complex and detailed reasons.
    """
    resolved_text: str = ""
    if text is not None:
        resolved_text = text
    elif isinstance(page, dict):
        resolved_text = str(page.get("text") or "")
    elif page is not None:
        if hasattr(page, "extract_text"):
            with contextlib.suppress(Exception):
                resolved_text = page.extract_text() or ""
        elif hasattr(page, "text"):
            resolved_text = str(getattr(page, "text", "") or "")

    # Character count calculation
    if char_count is not None:
        actual_char_count = max(0, int(char_count))
    elif isinstance(page, dict) and "char_count" in page:
        actual_char_count = max(0, int(page["char_count"]))
    else:
        actual_char_count = len(resolved_text.strip())

    # Unmapped glyph count calculation
    if unmapped_glyphs is not None:
        actual_unmapped = max(0, int(unmapped_glyphs))
    elif isinstance(page, dict) and "unmapped_glyphs" in page:
        actual_unmapped = max(0, int(page["unmapped_glyphs"]))
    else:
        actual_unmapped = count_unmapped_glyphs(resolved_text)

    # Raster ratio calculation
    if raster_ratio is not None:
        actual_raster_ratio = min(1.0, max(0.0, float(raster_ratio)))
    elif isinstance(page, dict) and ("raster_ratio" in page or "raster_image_area_ratio" in page):
        val = page.get("raster_ratio", page.get("raster_image_area_ratio", 0.0))
        actual_raster_ratio = min(1.0, max(0.0, float(val)))
    elif page is not None:
        actual_raster_ratio = calculate_page_raster_ratio(
            page, page_width=page_width, page_height=page_height
        )
    else:
        actual_raster_ratio = 0.0

    # Complexity determination
    reasons: list[str] = []
    if actual_char_count < sparse_threshold:
        reasons.append(f"low_character_density ({actual_char_count} < {sparse_threshold})")
    if actual_raster_ratio > raster_threshold:
        reasons.append(f"high_raster_ratio ({actual_raster_ratio:.4f} > {raster_threshold:.2f})")
    if actual_unmapped > 0:
        reasons.append(f"unmapped_glyphs ({actual_unmapped})")

    is_complex = len(reasons) > 0

    return LayoutComplexityResult(
        is_complex=is_complex,
        raster_ratio=actual_raster_ratio,
        char_count=actual_char_count,
        unmapped_glyphs=actual_unmapped,
        reasons=reasons,
    )


def is_tesseract_available(tesseract_cmd: str | None = None) -> bool:
    """Check whether Tesseract OCR binary or engine is available on the system.

    Safe inspection: does not execute arbitrary shell commands.

    Args:
        tesseract_cmd: Optional explicit path to tesseract binary.

    Returns:
        True if tesseract binary is located on PATH or valid executable path.
    """
    cmd = (
        tesseract_cmd
        or os.environ.get("OPENDV_TESSERACT_CMD")
        or os.environ.get("TESSERACT_CMD")
        or "tesseract"
    )
    if shutil.which(cmd) is not None:
        return True

    # Check pytesseract discovery if installed
    with contextlib.suppress(Exception):
        import pytesseract  # type: ignore[import-untyped]

        pyt_cmd = getattr(pytesseract, "tesseract_cmd", "tesseract")
        if shutil.which(pyt_cmd) is not None:
            return True

    return False


class TesseractOCRRouter:
    """Orchestrates OCR routing decisions based on page complexity and configuration.

    Supports strategies:
    - 'off': Disable OCR unconditionally. Marks complex pages as ocr_skipped=True.
    - 'auto': If page is complex, attempt OCR if available. If missing or fails,
      log warning, mark ocr_skipped=True in PageMapEntry, and fall back to native parsing.
    - 'force': If page is complex, require OCR. If missing, fail loudly by raising
      typed OCRUnavailableError("OCR_UNAVAILABLE") (HTTP 501 / exit code 1).
    """

    def __init__(
        self,
        ocr_mode: str = "auto",
        *,
        tesseract_cmd: str | None = None,
        sparse_threshold: int = DEFAULT_SPARSE_TEXT_THRESHOLD,
        raster_threshold: float = DEFAULT_RASTER_RATIO_THRESHOLD,
    ) -> None:
        """Initialize TesseractOCRRouter.

        Args:
            ocr_mode: OCR strategy ('auto', 'force', 'off').
            tesseract_cmd: Optional path to tesseract executable.
            sparse_threshold: Character threshold for sparse page detection.
            raster_threshold: Raster area ratio threshold for image-heavy detection.

        Raises:
            ValueError: If ocr_mode is not one of ('auto', 'force', 'off').
        """
        if ocr_mode not in VALID_OCR_MODES:
            raise ValueError(
                f"Invalid ocr_mode '{ocr_mode}'; must be one of {sorted(VALID_OCR_MODES)}"
            )
        self.ocr_mode = ocr_mode
        self.tesseract_cmd = tesseract_cmd
        self.sparse_threshold = sparse_threshold
        self.raster_threshold = raster_threshold
        self._availability_override: bool | None = None

    def set_availability_override(self, available: bool | None) -> None:
        """Override OCR availability detection for testing without environment modification."""
        self._availability_override = available

    def is_available(self) -> bool:
        """Return whether Tesseract OCR is currently available."""
        if self._availability_override is not None:
            return self._availability_override
        return is_tesseract_available(self.tesseract_cmd)

    def route_page(
        self,
        complexity: LayoutComplexityResult,
        page_number: int = 1,
    ) -> OCRRouteDecision:
        """Determine OCR routing action for an individual evaluated page.

        Args:
            complexity: LayoutComplexityResult from evaluate_page_complexity.
            page_number: 1-indexed document page number.

        Returns:
            OCRRouteDecision indicating whether to OCR, skip, or fall back.

        Raises:
            OCRUnavailableError: If page is complex, ocr_mode='force', and OCR is missing.
        """
        # Non-complex pages always use clean native vector text extraction
        if not complexity.is_complex:
            return OCRRouteDecision(
                should_ocr=False,
                ocr_skipped=False,
                action="native",
                confidence=1.0,
                reason="page is clean vector text; OCR not required",
                complexity=complexity,
            )

        # Complex page with OCR explicitly disabled
        if self.ocr_mode == "off":
            return OCRRouteDecision(
                should_ocr=False,
                ocr_skipped=True,
                action="skipped",
                confidence=0.8,
                reason="page is complex but ocr_mode is 'off'; OCR skipped",
                complexity=complexity,
            )

        # Complex page with forced OCR: fail loudly if engine is missing
        if self.ocr_mode == "force":
            if not self.is_available():
                logger.error(
                    "Tesseract OCR required by ocr_mode='force' but engine is unavailable "
                    "for complex page %d (%s)",
                    page_number,
                    ", ".join(complexity.reasons),
                )
                raise OCRUnavailableError(
                    "OCR_UNAVAILABLE",
                    details={
                        "page_number": page_number,
                        "reasons": complexity.reasons,
                        "ocr_mode": self.ocr_mode,
                    },
                )
            return OCRRouteDecision(
                should_ocr=True,
                ocr_skipped=False,
                action="ocr",
                confidence=1.0,
                reason="page is complex and ocr_mode is 'force'; routing to OCR",
                complexity=complexity,
            )

        # Complex page with auto OCR: degrade gracefully if engine is missing
        if not self.is_available():
            logger.warning(
                "Tesseract OCR unavailable for complex page %d (%s); "
                "skipping OCR and degrading to native parsing",
                page_number,
                ", ".join(complexity.reasons),
            )
            return OCRRouteDecision(
                should_ocr=False,
                ocr_skipped=True,
                action="fallback",
                confidence=0.8,
                reason="page is complex but OCR engine unavailable; graceful native fallback",
                complexity=complexity,
            )

        return OCRRouteDecision(
            should_ocr=True,
            ocr_skipped=False,
            action="ocr",
            confidence=1.0,
            reason="page is complex and OCR engine available; routing to OCR",
            complexity=complexity,
        )

    def evaluate_and_route(
        self,
        page: Any = None,
        *,
        page_number: int = 1,
        **kwargs: Any,
    ) -> tuple[LayoutComplexityResult, OCRRouteDecision]:
        """Convenience method evaluating complexity and producing a routing decision.

        Args:
            page: Page object, dictionary, or content.
            page_number: 1-indexed document page number.
            **kwargs: Extra parameters passed to evaluate_page_complexity.

        Returns:
            Tuple of (LayoutComplexityResult, OCRRouteDecision).
        """
        complexity = evaluate_page_complexity(
            page,
            sparse_threshold=self.sparse_threshold,
            raster_threshold=self.raster_threshold,
            **kwargs,
        )
        decision = self.route_page(complexity, page_number=page_number)
        return complexity, decision

    def execute_ocr(
        self,
        image_data: bytes | Any,
        page_number: int = 1,
    ) -> str:
        """Execute OCR extraction on raw image bytes or PIL Image object.

        Args:
            image_data: Raw image payload bytes or PIL.Image instance.
            page_number: 1-indexed document page number.

        Returns:
            Extracted text string.

        Raises:
            OCRUnavailableError: If OCR fails or is unavailable in 'force' mode.
        """
        if not self.is_available():
            if self.ocr_mode == "force":
                raise OCRUnavailableError("OCR_UNAVAILABLE")
            logger.warning("OCR engine unavailable for page %d; returning empty text", page_number)
            return ""

        try:
            import pytesseract  # type: ignore[import-untyped]

            try:
                from PIL import Image as PilImage  # type: ignore[import-untyped]
            except ImportError:
                PilImage = None  # noqa: N806

            if isinstance(image_data, (bytes, bytearray)):
                if PilImage is not None:
                    import io

                    img = PilImage.open(io.BytesIO(image_data))
                else:
                    img = image_data
            else:
                img = image_data

            return str(pytesseract.image_to_string(img) or "")

        except Exception as exc:
            if self.ocr_mode == "force":
                raise OCRUnavailableError(
                    f"OCR_UNAVAILABLE: {exc}",
                    details={"page_number": page_number, "error": str(exc)},
                ) from exc
            logger.warning(
                "OCR extraction failed on page %d (%s); falling back to native text",
                page_number,
                exc,
                exc_info=True,
            )
            return ""


__all__ = [
    "DEFAULT_RASTER_RATIO_THRESHOLD",
    "DEFAULT_SPARSE_TEXT_THRESHOLD",
    "VALID_OCR_MODES",
    "LayoutComplexityResult",
    "OCRRouteDecision",
    "OCRUnavailableError",
    "TesseractOCRRouter",
    "calculate_page_raster_ratio",
    "count_unmapped_glyphs",
    "evaluate_page_complexity",
    "is_tesseract_available",
]
