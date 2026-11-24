"""Synchronous DocumentMapBuilder (PDF, DOCX, Markdown, domain notes) with graceful OCR fallback."""

from __future__ import annotations

import contextlib
import hashlib
import io
import re
import shutil
from collections.abc import Sequence
from importlib import import_module
from pathlib import Path
from typing import Any, Literal

import pypdf
import pypdfium2 as pdfium  # type: ignore[import-untyped]

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.sources import DocumentMap, OutlineNode, PageMapSpec, PageTextView
from opendv.sources.pii import mask_pii_text

DocumentFormat = Literal["pdf", "markdown", "docx", "domain_note"]

_CR_OR_CRLF_RE = re.compile(r"\r\n|\r")
_HORIZ_WS_RE = re.compile(r"[ \t]+")
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_MD_TABLE_SEP_RE = re.compile(r"^\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?$")


def normalize_page_text(text: str) -> str:
    """Normalize line endings (\r\n|\r -> \n) and horizontal whitespace ([ \t]+ -> ' ')."""
    unified = _CR_OR_CRLF_RE.sub("\n", text)
    return _HORIZ_WS_RE.sub(" ", unified)


def compute_canonical_text_sha256(text: str) -> str:
    """Compute SHA-256 over whitespace-normalized canonical page text."""
    return hashlib.sha256(normalize_page_text(text).encode("utf-8")).hexdigest()


def is_page_scanned_or_low_density(non_ws_chars: int, image_xobjects: int) -> bool:
    """Apply authoritative scanned/low-density page rule."""
    return (non_ws_chars < 50 and image_xobjects >= 1) or (non_ws_chars < 15)


def _count_non_ws(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


def _resolve_obj(obj: Any) -> Any:
    return obj.get_object() if obj is not None and hasattr(obj, "get_object") else obj


def _count_page_image_xobjects(page: Any) -> int:
    resources = _resolve_obj(page.get("/Resources") if hasattr(page, "get") else None)
    if resources is None or not hasattr(resources, "get"):
        return 0

    xobjs = _resolve_obj(resources.get("/XObject"))

    image_names: set[str] = set()
    form_names: set[str] = set()
    if xobjs is not None and hasattr(xobjs, "items"):
        for name, xobj in xobjs.items():
            resolved = _resolve_obj(xobj)
            if resolved is None or not hasattr(resolved, "get"):
                continue
            subtype = resolved.get("/Subtype")
            s_name = str(name)
            variants = {s_name, s_name.lstrip("/"), f"/{s_name.lstrip('/')}"}
            if subtype == "/Image":
                image_names.update(variants)
            elif subtype == "/Form":
                form_res = _resolve_obj(resolved.get("/Resources"))
                raw_sub_x = (
                    form_res.get("/XObject")
                    if form_res is not None and hasattr(form_res, "get")
                    else None
                )
                sub_x = _resolve_obj(raw_sub_x)
                if sub_x is not None and hasattr(sub_x, "items"):
                    for _, sx in sub_x.items():
                        sr = _resolve_obj(sx)
                        if sr is not None and hasattr(sr, "get") and sr.get("/Subtype") == "/Image":
                            form_names.update(variants)
                            break

    drawn_count = 0
    contents = page.get_contents() if hasattr(page, "get_contents") else None
    if contents is not None:
        with contextlib.suppress(Exception):
            from pypdf.generic import ContentStream

            cs = ContentStream(contents, getattr(page, "pdf", None))
            for operands, operator in getattr(cs, "operations", ()):
                op_name = (
                    operator.decode("latin-1") if isinstance(operator, bytes) else str(operator)
                )
                if op_name == "Do" and operands:
                    target = str(operands[0])
                    if target in image_names or target in form_names:
                        drawn_count += 1
                elif op_name == "BI":
                    drawn_count += 1

    if drawn_count > 0:
        return drawn_count
    if image_names and contents is None:
        return len(image_names) // 2 or 1
    return 0


def _extract_table_headers_from_text(text: str) -> list[str]:
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if (
            "|" in stripped
            and idx + 1 < len(lines)
            and _MD_TABLE_SEP_RE.match(lines[idx + 1].strip())
        ):
            cells = [c.strip() for c in stripped.strip("|").split("|") if c.strip()]
            if len(cells) >= 2:
                return cells
        if "\t" in stripped:
            cells = [c.strip() for c in stripped.split("\t") if c.strip()]
            if len(cells) >= 2 and all(len(c) <= 48 for c in cells):
                return cells
        cols = [c.strip() for c in re.split(r"\s{2,}", stripped) if c.strip()]
        if len(cols) >= 2 and all("_" in c and " " not in c for c in cols):
            return cols
        ws_cols = stripped.split()
        if len(ws_cols) >= 2 and all(
            "_" in c and c.replace("_", "").isalnum() for c in ws_cols
        ):
            return ws_cols
    return []


def _extract_heading_preview(text: str) -> str:
    for line in text.splitlines():
        clean = line.strip().lstrip("#").strip()
        if clean:
            return clean[:120]
    return ""


def _extract_pdf_outline(reader: pypdf.PdfReader) -> list[OutlineNode]:
    nodes: list[OutlineNode] = []

    def _walk(items: Any, level: int) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, list):
                _walk(item, level + 1)
                continue
            title = getattr(item, "title", None)
            if not title and isinstance(item, dict):
                title = item.get("/Title")
            if not title:
                continue
            page_num = 1
            with contextlib.suppress(Exception):
                page_idx = reader.get_destination_page_number(item)
                if isinstance(page_idx, int) and page_idx >= 0:
                    page_num = page_idx + 1
            nodes.append(OutlineNode(title=str(title).strip(), page_start=page_num, level=level))

    with contextlib.suppress(Exception):
        raw_outline = reader.outline
        _walk(raw_outline, 1)
    return nodes


def _try_ocr_pdf_page(pdf_bytes: bytes, page_index: int) -> tuple[int, str]:
    """Run optional [ocr] pytesseract + pypdfium2; degrade gracefully to (0, '') if absent."""
    if shutil.which("tesseract") is None:
        return 0, ""
    try:
        pytesseract = import_module("pytesseract")
        import_module("PIL.Image")
    except ImportError:
        return 0, ""

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        page = doc[page_index]
        try:
            bitmap = page.render(scale=2.0)
            pil_image = bitmap.to_pil()
        finally:
            page.close()
    finally:
        doc.close()

    try:
        data = pytesseract.image_to_data(pil_image, output_type=pytesseract.Output.DICT)
    except (OSError, RuntimeError, ValueError, TypeError):
        return 0, ""

    texts = data.get("text", [])
    confs = data.get("conf", [])
    words: list[str] = []
    boxes = 0
    for txt, conf in zip(texts, confs, strict=False):
        clean = str(txt).strip()
        if not clean:
            continue
        try:
            conf_val = float(conf)
        except (TypeError, ValueError):
            conf_val = -1.0
        if conf_val >= 0:
            boxes += 1
            words.append(clean)
    return boxes, " ".join(words)


class DocumentMapBuilder:
    """Synchronous zero-LLM Flow B DocumentMap builder for PDF, DOCX, Markdown, and notes."""

    def build_from_bytes(
        self,
        *,
        source_id: str,
        filename: str,
        doc_format: DocumentFormat,
        content: bytes | str,
        approved_scan_pages: Sequence[int] = (),
        run_ocr: bool = True,
    ) -> tuple[DocumentMap, list[str]]:
        """Build a DocumentMap and parallel list of normalized page texts."""
        if doc_format == "pdf":
            raw_bytes = content.encode("latin-1") if isinstance(content, str) else bytes(content)
            return self._build_pdf(
                source_id=source_id,
                filename=filename,
                raw_bytes=raw_bytes,
                approved_scan_pages=approved_scan_pages,
                run_ocr=run_ocr,
            )
        if doc_format == "docx":
            raw_bytes = content.encode("utf-8") if isinstance(content, str) else bytes(content)
            return self._build_docx(
                source_id=source_id,
                filename=filename,
                raw_bytes=raw_bytes,
                approved_scan_pages=approved_scan_pages,
            )
        if doc_format in ("markdown", "domain_note"):
            if isinstance(content, bytes):
                try:
                    text = content.decode("utf-8")
                except UnicodeDecodeError as err:
                    raise OpenDVError(
                        f"Invalid UTF-8 text in '{filename}': {err}",
                        error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                    ) from err
            else:
                text = str(content)
            return self._build_text_doc(
                source_id=source_id,
                filename=filename,
                doc_format=doc_format,
                text=text,
                approved_scan_pages=approved_scan_pages,
            )
        raise OpenDVError(
            f"Unsupported document format '{doc_format}'",
            error_code=ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        )

    def build_from_path(
        self,
        *,
        source_id: str,
        path: str | Path,
        doc_format: DocumentFormat,
        approved_scan_pages: Sequence[int] = (),
        run_ocr: bool = True,
    ) -> tuple[DocumentMap, list[str]]:
        """Build a DocumentMap from a local file path."""
        resolved = Path(path).resolve()
        if not resolved.is_file():
            raise OpenDVError(
                f"Document binary not found: {resolved}",
                error_code=ErrorCode.SOURCE_BINARY_ABSENT,
            )
        return self.build_from_bytes(
            source_id=source_id,
            filename=resolved.name,
            doc_format=doc_format,
            content=resolved.read_bytes(),
            approved_scan_pages=approved_scan_pages,
            run_ocr=run_ocr,
        )

    def build_page_text_views(
        self,
        doc_map: DocumentMap,
        normalized_pages: Sequence[str],
        *,
        page_start: int = 1,
        page_end: int | None = None,
    ) -> list[PageTextView]:
        """Produce PII-masked PageTextView objects respecting approved_scan_pages."""
        end = page_end if page_end is not None else doc_map.page_count
        if page_start < 1 or end < page_start:
            raise OpenDVError(
                f"Invalid page range {page_start}..{end}",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        approved_set = set(doc_map.approved_scan_pages)
        views: list[PageTextView] = []
        for page_spec in doc_map.pages:
            if not (page_start <= page_spec.page_number <= end):
                continue
            idx = page_spec.page_number - 1
            raw_text = normalized_pages[idx] if idx < len(normalized_pages) else ""
            views.append(
                PageTextView(
                    doc_id=doc_map.doc_id,
                    page_number=page_spec.page_number,
                    masked_text=mask_pii_text(raw_text),
                    is_scanned=page_spec.is_scanned_or_low_density,
                    scan_approved=page_spec.page_number in approved_set,
                )
            )
        return views

    def _build_pdf(
        self,
        *,
        source_id: str,
        filename: str,
        raw_bytes: bytes,
        approved_scan_pages: Sequence[int],
        run_ocr: bool,
    ) -> tuple[DocumentMap, list[str]]:
        if not raw_bytes or b"%PDF-" not in raw_bytes[:1024]:
            raise OpenDVError(
                f"Invalid or empty PDF document '{filename}'",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        try:
            reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
            if reader.is_encrypted and not reader.decrypt("") and reader.is_encrypted:
                raise OpenDVError(
                    f"Encrypted PDF '{filename}' cannot be indexed",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
            if not reader.pages:
                raise OpenDVError(
                    f"PDF '{filename}' contains zero pages",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
        except OpenDVError:
            raise
        except Exception as err:
            raise OpenDVError(
                f"Corrupt PDF '{filename}': {err}",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            ) from err

        outline = _extract_pdf_outline(reader)
        pages: list[PageMapSpec] = []
        normalized_pages: list[str] = []

        for idx, page in enumerate(reader.pages):
            try:
                raw_text = page.extract_text() or ""
            except Exception as err:
                raise OpenDVError(
                    f"Failed extracting text from '{filename}' page {idx + 1}: {err}",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                ) from err

            norm_text = normalize_page_text(raw_text)
            non_ws = _count_non_ws(norm_text)
            img_count = _count_page_image_xobjects(page)
            scanned = is_page_scanned_or_low_density(non_ws, img_count)
            ocr_boxes = 0
            if scanned and run_ocr:
                ocr_boxes, ocr_text = _try_ocr_pdf_page(raw_bytes, idx)
                if ocr_text.strip():
                    norm_ocr = normalize_page_text(ocr_text)
                    norm_text = (
                        f"{norm_text}\n{norm_ocr}".strip()
                        if norm_text.strip()
                        else norm_ocr
                    )

            normalized_pages.append(norm_text)
            pages.append(
                PageMapSpec(
                    page_number=idx + 1,
                    char_count=len(norm_text),
                    has_images=img_count > 0,
                    is_scanned_or_low_density=scanned,
                    ocr_box_count=ocr_boxes,
                    table_headers=_extract_table_headers_from_text(raw_text)
                    or _extract_table_headers_from_text(norm_text),
                    heading_preview=_extract_heading_preview(norm_text),
                    canonical_text_sha256=compute_canonical_text_sha256(norm_text),
                )
            )

        if not outline:
            for p in pages:
                if p.heading_preview:
                    outline.append(
                        OutlineNode(title=p.heading_preview, page_start=p.page_number, level=1)
                    )

        doc_map = DocumentMap(
            doc_id=source_id,
            filename=filename,
            format="pdf",
            page_count=len(pages),
            approved_scan_pages=sorted(set(approved_scan_pages)),
            outline=outline,
            pages=pages,
        )
        return doc_map, normalized_pages

    def _build_docx(
        self,
        *,
        source_id: str,
        filename: str,
        raw_bytes: bytes,
        approved_scan_pages: Sequence[int],
    ) -> tuple[DocumentMap, list[str]]:
        if not raw_bytes:
            raise OpenDVError(
                f"DOCX document '{filename}' is empty",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        try:
            import docx

            doc = docx.Document(io.BytesIO(raw_bytes))
        except Exception as err:
            raise OpenDVError(
                f"Invalid DOCX document '{filename}': {err}",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            ) from err

        lines: list[str] = []
        outline: list[OutlineNode] = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            lines.append(text)
            style_name = getattr(getattr(para, "style", None), "name", "") or ""
            if style_name.lower().startswith("heading"):
                lvl_digits = "".join(ch for ch in style_name if ch.isdigit())
                level = max(1, int(lvl_digits)) if lvl_digits else 1
                outline.append(OutlineNode(title=text, page_start=1, level=level))

        table_headers: list[str] = []
        for table in doc.tables:
            for r_idx, row in enumerate(table.rows):
                row_vals = [cell.text.strip() for cell in row.cells]
                if r_idx == 0 and not table_headers:
                    table_headers = [v for v in row_vals if v]
                lines.append(" | ".join(row_vals))

        combined = "\n".join(lines)
        norm_text = normalize_page_text(combined)
        page = PageMapSpec(
            page_number=1,
            char_count=len(norm_text),
            has_images=False,
            is_scanned_or_low_density=False,
            ocr_box_count=0,
            table_headers=table_headers,
            heading_preview=_extract_heading_preview(norm_text),
            canonical_text_sha256=compute_canonical_text_sha256(norm_text),
        )
        doc_map = DocumentMap(
            doc_id=source_id,
            filename=filename,
            format="docx",
            page_count=1,
            approved_scan_pages=sorted(set(approved_scan_pages)),
            outline=outline,
            pages=[page],
        )
        return doc_map, [norm_text]

    def _build_text_doc(
        self,
        *,
        source_id: str,
        filename: str,
        doc_format: Literal["markdown", "domain_note"],
        text: str,
        approved_scan_pages: Sequence[int],
    ) -> tuple[DocumentMap, list[str]]:
        if not text.strip():
            raise OpenDVError(
                f"Document '{filename}' is empty",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        norm_text = normalize_page_text(text)
        outline: list[OutlineNode] = []
        for line in norm_text.splitlines():
            m = _MD_HEADING_RE.match(line.strip())
            if m:
                outline.append(OutlineNode(title=m.group(2), page_start=1, level=len(m.group(1))))

        page = PageMapSpec(
            page_number=1,
            char_count=len(norm_text),
            has_images=False,
            is_scanned_or_low_density=False,
            ocr_box_count=0,
            table_headers=_extract_table_headers_from_text(text)
            or _extract_table_headers_from_text(norm_text),
            heading_preview=_extract_heading_preview(norm_text),
            canonical_text_sha256=compute_canonical_text_sha256(norm_text),
        )
        doc_map = DocumentMap(
            doc_id=source_id,
            filename=filename,
            format=doc_format,
            page_count=1,
            approved_scan_pages=sorted(set(approved_scan_pages)),
            outline=outline,
            pages=[page],
        )
        return doc_map, [norm_text]


__all__ = [
    "DocumentFormat",
    "DocumentMapBuilder",
    "compute_canonical_text_sha256",
    "is_page_scanned_or_low_density",
    "normalize_page_text",
]
