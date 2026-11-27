"""E2E Tiers 1 & 2 tests for F06 (PII Redaction, OCR & approve_scan) and F07 (Local Source Security)."""

from __future__ import annotations

import io
import os
import shutil
import stat
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from opendv.models.errors import CliExitCode, ErrorCode, OpenDVError
from tests.parsing.e2e.conftest import (
    REPO_ROOT,
    E2EWorkspace,
    make_boundary_pii_docx,
    make_boundary_pii_text_file,
    make_minimal_pdf,
    make_pii_multi_token_pdf,
    make_scanned_image_pdf,
)


def _decode_png(png_bytes: bytes) -> Image.Image:
    with Image.open(io.BytesIO(png_bytes)) as img:
        return img.convert("RGB")


def _count_black_pixels(img: Image.Image) -> int:
    rgb = img.convert("RGB")
    return sum(1 for px in rgb.getdata() if px == (0, 0, 0))


def _box_has_black_pixels(img: Image.Image, x0: int, y0: int, x1: int, y1: int) -> bool:
    cx0 = max(0, x0)
    cy0 = max(0, y0)
    cx1 = min(img.width, x1)
    cy1 = min(img.height, y1)
    if cx1 <= cx0 or cy1 <= cy0:
        return False
    crop = img.crop((cx0, cy0, cx1, cy1)).convert("RGB")
    return any(px == (0, 0, 0) for px in crop.getdata())


# ---------------------------------------------------------------------------
# F06: PII Redaction, Multi-Token Word-Box Alignment, OCR & approve_scan
# ---------------------------------------------------------------------------


def test_f06_t1_local_human_io_preserves_raw_while_model_egress_redacts_ir(
    e2e_workspace: E2EWorkspace,
) -> None:
    """LOCAL_HUMAN_IO retains raw IR text; MODEL_EGRESS returns a redacted copy with updated SHA."""
    from opendv.models.document_ir import compute_canonical_text_sha256
    from opendv.models.pii_tokenizer import MASKED_PII_TOKEN
    from opendv.service.parsing_svc import ParsingService

    pdf_path = make_pii_multi_token_pdf(e2e_workspace.workspace / "pii_multi.pdf")
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])

    raw_ir = svc.parse_document(pdf_path, sink_class="LOCAL_HUMAN_IO")
    raw_text = raw_ir.pages[0].canonical_text
    assert raw_ir.provenance.redacted is False
    assert raw_ir.provenance.sink_class == "LOCAL_HUMAN_IO"
    assert "(415) 555-0199" in raw_text
    assert "4111 1111 1111 1111" in raw_text
    assert "021000021" in raw_text
    assert "123-45-6789" in raw_text

    egress_ir = svc.parse_document(pdf_path, sink_class="MODEL_EGRESS")
    egress_text = egress_ir.pages[0].canonical_text
    assert egress_ir.provenance.redacted is True
    assert egress_ir.provenance.sink_class == "MODEL_EGRESS"
    assert MASKED_PII_TOKEN in egress_text
    assert "(415) 555-0199" not in egress_text
    assert "4111 1111 1111 1111" not in egress_text
    assert "021000021" not in egress_text
    assert "123-45-6789" not in egress_text
    assert egress_ir.pages[0].canonical_text_sha256 == compute_canonical_text_sha256(egress_text)
    assert egress_ir.pages[0].canonical_text_sha256 != raw_ir.pages[0].canonical_text_sha256
    # Raw IR returned earlier remains unmutated.
    assert "123-45-6789" in raw_ir.pages[0].canonical_text


def test_f06_t1_multi_token_pii_word_box_blackout_on_rendered_png(
    e2e_workspace: E2EWorkspace,
) -> None:
    """MODEL_EGRESS page rendering blacks out aligned multi-token PII word boxes in PNG."""
    from opendv.service.parsing_svc import ParsingService

    pdf_path = make_pii_multi_token_pdf(e2e_workspace.workspace / "pii_render.pdf")
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])

    raw_png, raw_res = svc.render_document_page(pdf_path, page_number=1, sink_class="LOCAL_HUMAN_IO")
    red_png, red_res = svc.render_document_page(pdf_path, page_number=1, sink_class="MODEL_EGRESS")

    assert raw_res.redacted is False
    assert red_res.redacted is True
    assert raw_png != red_png

    raw_img = _decode_png(raw_png)
    red_img = _decode_png(red_png)
    assert (red_img.width, red_img.height) == (red_res.width, red_res.height)
    assert _count_black_pixels(red_img) > _count_black_pixels(raw_img)

    redacted_ir = svc.parse_document(pdf_path, sink_class="MODEL_EGRESS")
    assert svc.validate_ir(redacted_ir) == []


def test_f06_t1_projectors_mask_pii_under_model_egress(
    e2e_workspace: E2EWorkspace,
) -> None:
    """All 3 projectors mask PII under MODEL_EGRESS while keeping DocumentMap raw page digest."""
    from opendv.models.pii_tokenizer import MASKED_PII_TOKEN
    from opendv.service.parsing_svc import ParsingService

    pdf_path = make_pii_multi_token_pdf(e2e_workspace.workspace / "pii_proj.pdf")
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])
    raw_ir = svc.parse_document(pdf_path, sink_class="LOCAL_HUMAN_IO")

    dm_out = svc.export_ir(raw_ir, target="document_map", sink_class="MODEL_EGRESS")
    md_out = svc.export_ir(raw_ir, target="markdown", sink_class="MODEL_EGRESS")
    chunks_out = svc.export_ir(raw_ir, target="chunks", sink_class="MODEL_EGRESS")

    doc_map = dm_out["document_map"]
    views = dm_out["page_text_views"]
    assert doc_map.pages[0].canonical_text_sha256 == raw_ir.pages[0].canonical_text_sha256
    assert MASKED_PII_TOKEN in views[0].masked_text
    assert "123-45-6789" not in views[0].masked_text
    assert "4111 1111 1111 1111" not in views[0].masked_text

    assert isinstance(md_out, str)
    assert MASKED_PII_TOKEN in md_out
    for raw_secret in ("(415) 555-0199", "4111 1111 1111 1111", "021000021", "123-45-6789"):
        assert raw_secret not in md_out
        assert all(raw_secret not in chunk.text for chunk in chunks_out)
    assert any(MASKED_PII_TOKEN in chunk.text for chunk in chunks_out)


def test_f06_t1_injected_ocr_provider_redacts_scanned_page(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Injected OcrProviderFn enables deterministic OCR text + PNG blackout on scanned PDF."""
    from opendv.models.document_ir import OcrBox, ParseOptions
    from opendv.service.parsing_svc import ParsingService

    pdf_path = make_scanned_image_pdf(e2e_workspace.workspace / "scan_ocr.pdf")
    calls: list[tuple[int, int]] = []

    def _fake_ocr(png_bytes: bytes, page_num: int) -> tuple[list[OcrBox], str]:
        calls.append((len(png_bytes), page_num))
        return (
            [
                OcrBox(x0=10, y0=10, x1=55, y1=28, text="SSN:"),
                OcrBox(x0=60, y0=10, x1=180, y1=28, text="123-45-6789"),
            ],
            "SSN: 123-45-6789",
        )

    svc = ParsingService(allowed_roots=[e2e_workspace.workspace], ocr_provider=_fake_ocr)
    png_bytes, res = svc.render_document_page(
        pdf_path,
        page_number=1,
        sink_class="MODEL_EGRESS",
        options=ParseOptions(ocr=True),
    )

    assert len(calls) == 1 and calls[0][1] == 1 and calls[0][0] > 0
    assert res.ocr_used is True
    assert res.ocr_boxes_applied >= 1
    assert res.redacted is True
    img = _decode_png(png_bytes)
    assert _box_has_black_pixels(img, 60, 10, 180, 28)


def test_f06_t1_approve_scan_unblocks_local_human_io_only(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Scanned page under LOCAL_HUMAN_IO requires approve_scan=True and stays unredacted."""
    from opendv.service.parsing_svc import ParsingService

    pdf_path = make_scanned_image_pdf(e2e_workspace.workspace / "scan_local.pdf")
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])

    with pytest.raises(OpenDVError) as exc_blocked:
        svc.render_document_page(
            pdf_path,
            page_number=1,
            sink_class="LOCAL_HUMAN_IO",
            approve_scan=False,
        )
    assert exc_blocked.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED
    assert exc_blocked.value.http_status == 422

    png_bytes, res = svc.render_document_page(
        pdf_path,
        page_number=1,
        sink_class="LOCAL_HUMAN_IO",
        approve_scan=True,
    )
    assert res.redacted is False
    assert res.ocr_used is False
    img = _decode_png(png_bytes)
    assert img.width == res.width and img.height == res.height


def test_f06_t1_validate_ir_allows_redacted_ir_and_raw_literal_redacted_text(
    e2e_workspace: E2EWorkspace,
) -> None:
    """validate_ir never runs ensure_no_redacted_placeholder, which stays enforced downstream."""
    from opendv.models.pii_tokenizer import ensure_no_redacted_placeholder
    from opendv.service.parsing_svc import ParsingService

    court_pdf = make_minimal_pdf(
        e2e_workspace.workspace / "court_literal.pdf",
        text="Exhibit [REDACTED_BY_COURT] summary.",
    )
    pii_pdf = make_pii_multi_token_pdf(e2e_workspace.workspace / "pii_val.pdf")
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])

    court_ir = svc.parse_document(court_pdf, sink_class="LOCAL_HUMAN_IO")
    redacted_ir = svc.parse_document(pii_pdf, sink_class="MODEL_EGRESS")

    assert svc.validate_ir(court_ir) == []
    assert svc.validate_ir(redacted_ir) == []

    with pytest.raises(OpenDVError) as exc_leak:
        ensure_no_redacted_placeholder("Exhibit [REDACTED_BY_COURT] summary.")
    assert exc_leak.value.error_code == ErrorCode.REDACTED_PLACEHOLDER_LEAK


def test_f06_t2_approve_scan_does_not_bypass_model_egress_redaction(
    e2e_workspace: E2EWorkspace,
) -> None:
    """approve_scan=True never bypasses fail-closed MODEL_EGRESS scan redaction."""
    from opendv.service.parsing_svc import ParsingService

    pdf_path = make_scanned_image_pdf(e2e_workspace.workspace / "scan_egress.pdf")
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])

    with pytest.raises(OpenDVError) as exc_info:
        svc.render_document_page(
            pdf_path,
            page_number=1,
            sink_class="MODEL_EGRESS",
            approve_scan=True,
        )
    assert exc_info.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED
    assert exc_info.value.http_status == 422


def test_f06_t2_missing_ocr_extra_raises_driver_missing_with_remediation(
    e2e_workspace: E2EWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing pytesseract raises DRIVER_MISSING with 'uv sync --extra ocr' remediation (no skip)."""
    from opendv.models.document_ir import ParseOptions
    from opendv.service.parsing_svc import ParsingService

    pdf_path = make_scanned_image_pdf(e2e_workspace.workspace / "scan_missing_extra.pdf")
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)

    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])
    with pytest.raises(OpenDVError) as exc_info:
        svc.render_document_page(
            pdf_path,
            page_number=1,
            sink_class="MODEL_EGRESS",
            options=ParseOptions(ocr=True),
        )
    assert exc_info.value.error_code == ErrorCode.DRIVER_MISSING
    assert exc_info.value.http_status == 422
    assert exc_info.value.remediation is not None
    assert any(
        act.actor == "agent" and act.command and "uv sync --extra ocr" in act.command
        for act in exc_info.value.remediation.actions
    )


def test_f06_t2_pytesseract_present_without_tesseract_binary_emits_human_action(
    e2e_workspace: E2EWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installed pytesseract without tesseract binary emits actor='human' remediation action."""
    import importlib.machinery

    from opendv.models.document_ir import ParseOptions
    from opendv.service.parsing_svc import ParsingService

    pdf_path = make_scanned_image_pdf(e2e_workspace.workspace / "scan_no_bin.pdf")
    fake_pytesseract = types.ModuleType("pytesseract")
    fake_pytesseract.__spec__ = importlib.machinery.ModuleSpec("pytesseract", loader=None)
    fake_pytesseract.Output = types.SimpleNamespace(DICT="dict")  # type: ignore[attr-defined]
    fake_pytesseract.image_to_data = lambda *_a, **_kw: {}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pytesseract", fake_pytesseract)
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)

    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])
    with pytest.raises(OpenDVError) as exc_info:
        svc.render_document_page(
            pdf_path,
            page_number=1,
            sink_class="MODEL_EGRESS",
            options=ParseOptions(ocr=True),
        )
    assert exc_info.value.error_code == ErrorCode.DRIVER_MISSING
    assert exc_info.value.remediation is not None
    assert any(
        act.actor == "human" and "tesseract" in act.label.lower()
        for act in exc_info.value.remediation.actions
    )


def test_f06_t2_word_box_alignment_miss_fails_closed_unredacted_scan_blocked(
    e2e_workspace: E2EWorkspace,
) -> None:
    """If PII exists on page text but zero word boxes overlap, fail closed with UNREDACTED_SCAN_BLOCKED."""
    from opendv.models.document_ir import (
        DocumentBlock,
        InspectResult,
        PageSlice,
        PageSpan,
        ParsedDocumentIR,
        ParseOptions,
        ParseProvenance,
        SourceBlob,
        compute_canonical_text_sha256,
    )
    from opendv.parsing import ParsingEngine, ParsingRegistry, RenderedPageBuffer
    from opendv.service.parsing_svc import ParsingService

    pdf_path = make_minimal_pdf(e2e_workspace.workspace / "align_miss.pdf", text="Tax ID: 123-45-6789")

    class _NoBoxPdfParser:
        parser_id = "stub-nobox"
        parser_version = "1.0"
        supports_render = True

        def inspect(self, blob: SourceBlob, opts: ParseOptions) -> InspectResult:
            return InspectResult(
                source_uri_sanitized=blob.uri_sanitized,
                binary_sha256=blob.binary_sha256,
                byte_size=blob.byte_size,
                format_id="pdf",
                page_count=1,
                scanned_pages=[],
                digital_pages=[1],
                renderable=True,
                warnings=[],
            )

        def parse(self, blob: SourceBlob, opts: ParseOptions) -> ParsedDocumentIR:
            text = "Tax ID: 123-45-6789"
            sha = compute_canonical_text_sha256(text)
            return ParsedDocumentIR(
                provenance=ParseProvenance(
                    parser_id=self.parser_id,
                    parser_version=self.parser_version,
                    source_uri_sanitized=blob.uri_sanitized,
                    binary_sha256=blob.binary_sha256,
                    detected_mime="application/pdf",
                    format_id="pdf",
                    page_count=1,
                    ocr_pages_detected=0,
                    redacted=False,
                    sink_class="LOCAL_HUMAN_IO",
                    elapsed_ms=1,
                    warnings=[],
                ),
                pages=[
                    PageSlice(
                        page_number=1,
                        slice_kind="physical_page",
                        renderable=True,
                        canonical_text=text,
                        canonical_text_sha256=sha,
                        raw_text_length=len(text),
                        non_ws_chars=len(text.replace(" ", "")),
                        image_block_count=0,
                        needs_ocr=False,
                        extracted_via="native_text",
                        width_pt=612.0,
                        height_pt=792.0,
                        rotation_deg=0,
                    )
                ],
                outline=[],
                blocks=[
                    DocumentBlock(
                        block_id="b1",
                        kind="paragraph",
                        reading_order=0,
                        spans=[PageSpan(page_number=1, char_start=0, char_end=len(text), bbox=None)],
                        heading_level=None,
                        text=text,
                        list_ordered=None,
                        table_headers=None,
                        table_rows=None,
                        caption=None,
                    )
                ],
            )

        def render_page(
            self, blob: SourceBlob, page_number: int, opts: ParseOptions
        ) -> RenderedPageBuffer:
            buf = io.BytesIO()
            Image.new("RGB", (200, 100), color=(255, 255, 255)).save(buf, format="PNG")
            return RenderedPageBuffer(
                png_bytes=buf.getvalue(),
                width_px=200,
                height_px=100,
                dpi=opts.dpi,
                word_boxes=(),
            )

    registry = ParsingRegistry()
    registry.register_parser("pdf", _NoBoxPdfParser())
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace], registry=registry)
    engine = ParsingEngine(registry)

    with SourceBlob.from_path(pdf_path) as blob, pytest.raises(OpenDVError) as exc_engine:
        engine.render_page(blob, page_number=1, redaction="on")
    assert exc_engine.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    with pytest.raises(OpenDVError) as exc_info:
        svc.render_document_page(pdf_path, page_number=1, sink_class="MODEL_EGRESS")
    assert exc_info.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED


def test_f06_t2_boundary_pii_masked_before_24k_split_in_docx_and_text(
    e2e_workspace: E2EWorkspace,
) -> None:
    """PII straddling the 24,000-char slice boundary is masked BEFORE slicing across docx/md/txt."""
    from opendv.models.pii_tokenizer import MASKED_PII_TOKEN
    from opendv.service.parsing_svc import ParsingService

    docx_path = make_boundary_pii_docx(e2e_workspace.workspace / "boundary.docx")
    md_path = make_boundary_pii_text_file(e2e_workspace.workspace / "boundary.md")
    txt_path = make_boundary_pii_text_file(e2e_workspace.workspace / "boundary.txt")
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])

    for path in (docx_path, md_path, txt_path):
        ir = svc.parse_document(path, sink_class="MODEL_EGRESS")
        assert len(ir.pages) >= 2, f"Expected >=2 slices for {path.name}"
        joined = "".join(p.canonical_text for p in ir.pages)
        assert "123-45-6789" not in joined
        assert MASKED_PII_TOKEN in joined
        chunks = svc.export_ir(ir, target="chunks", sink_class="MODEL_EGRESS")
        assert all("123-45-6789" not in c.text for c in chunks)


def test_f06_t2_aba_context_gating_and_luhn_pan_precision(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Context-gated ABA and Luhn PAN mask true PII while leaving non-PII lookalikes intact."""
    from opendv.models.pii_tokenizer import MASKED_PII_TOKEN
    from opendv.service.parsing_svc import ParsingService

    pdf_path = make_minimal_pdf(
        e2e_workspace.workspace / "aba_luhn.pdf",
        text="Order 021000021\nInvalid 4111111111111112\nRouting: ABA 021000021",
    )
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])

    egress_ir = svc.parse_document(pdf_path, sink_class="MODEL_EGRESS")
    text = egress_ir.pages[0].canonical_text
    assert "Order 021000021" in text
    assert "Invalid 4111111111111112" in text
    assert f"ABA {MASKED_PII_TOKEN}" in text

    raw_png, _ = svc.render_document_page(pdf_path, page_number=1, sink_class="LOCAL_HUMAN_IO")
    red_png, red_res = svc.render_document_page(pdf_path, page_number=1, sink_class="MODEL_EGRESS")
    assert red_res.redacted is True
    assert _count_black_pixels(_decode_png(red_png)) > _count_black_pixels(_decode_png(raw_png))


def test_f06_t2_institutional_scanned_schedule_p2_blocked_until_ocr_or_local_approval(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Real repo scanned schedule PDF blocks page 2 until OcrProviderFn or LOCAL_HUMAN_IO approval."""
    from opendv.models.document_ir import OcrBox, ParseOptions
    from opendv.service.parsing_svc import ParsingService

    fixture_pdf = REPO_ROOT / "examples" / "sample_pdfs" / "institutional_scanned_schedule_ocr_test.pdf"
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace, REPO_ROOT])

    # Page 1 is digital and renders under MODEL_EGRESS.
    _, p1_res = svc.render_document_page(fixture_pdf, page_number=1, sink_class="MODEL_EGRESS")
    assert p1_res.page_number == 1

    # Page 2 is scanned and fails closed without OCR under MODEL_EGRESS.
    with pytest.raises(OpenDVError) as exc_blocked:
        svc.render_document_page(fixture_pdf, page_number=2, sink_class="MODEL_EGRESS")
    assert exc_blocked.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    # With injected OcrProviderFn + ocr=True, page 2 renders and redacts.
    ocr_svc = ParsingService(
        allowed_roots=[e2e_workspace.workspace, REPO_ROOT],
        ocr_provider=lambda _png, _p: (
            [OcrBox(x0=12, y0=12, x1=150, y1=32, text="123-45-6789")],
            "Account SSN 123-45-6789",
        ),
    )
    _, p2_ocr = ocr_svc.render_document_page(
        fixture_pdf,
        page_number=2,
        sink_class="MODEL_EGRESS",
        options=ParseOptions(ocr=True),
    )
    assert p2_ocr.ocr_used is True and p2_ocr.redacted is True

    # Under LOCAL_HUMAN_IO + approve_scan=True, page 2 renders unredacted.
    _, p2_local = svc.render_document_page(
        fixture_pdf,
        page_number=2,
        sink_class="LOCAL_HUMAN_IO",
        approve_scan=True,
    )
    assert p2_local.redacted is False


# ---------------------------------------------------------------------------
# F07: Local Source Security, URI Resolution & Containment
# ---------------------------------------------------------------------------


def test_f07_t1_windows_drive_path_and_file_uri_accepted_within_allowed_root(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Raw local paths (including Windows C:\\... before urlparse) and file:/// URIs resolve."""
    from opendv.service.parsing_svc import ParsingService

    pdf_path = make_minimal_pdf(e2e_workspace.workspace / "local_ok.pdf")
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])

    res_path = svc.inspect_document(str(pdf_path))
    res_uri = svc.inspect_document(pdf_path.as_uri())

    assert res_path.format_id == "pdf"
    assert res_uri.format_id == "pdf"
    assert res_path.binary_sha256 == res_uri.binary_sha256
    assert "?" not in res_uri.source_uri_sanitized


def test_f07_t1_sanitized_uri_strips_userinfo_and_query_params(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Query strings and fragments on file:/// URIs are stripped from source_uri_sanitized."""
    from opendv.service.parsing_svc import ParsingService

    pdf_path = make_minimal_pdf(e2e_workspace.workspace / "uri_clean.pdf")
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])

    dirty_uri = f"{pdf_path.as_uri()}?token=super_secret_token&sig=xyz#frag"
    inspected = svc.inspect_document(dirty_uri)
    ir = svc.parse_document(dirty_uri, sink_class="LOCAL_HUMAN_IO")

    for val in (inspected.source_uri_sanitized, ir.provenance.source_uri_sanitized):
        assert "super_secret_token" not in val
        assert "token=" not in val
        assert "?" not in val
        assert "#" not in val


def test_f07_t1_source_blob_from_path_preserves_caller_file_on_exit(
    e2e_workspace: E2EWorkspace,
) -> None:
    """SourceBlob.from_path sets is_spooled_temp=False and preserves caller file after __exit__."""
    import hashlib

    from opendv.models.document_ir import SourceBlob

    pdf_path = make_minimal_pdf(e2e_workspace.workspace / "keep_me.pdf")
    expected_bytes = pdf_path.read_bytes()
    expected_sha = hashlib.sha256(expected_bytes).hexdigest()

    with SourceBlob.from_path(pdf_path) as blob:
        assert blob.is_spooled_temp is False
        assert blob.binary_sha256 == expected_sha
        assert blob.byte_size == len(expected_bytes)
        assert blob.path == pdf_path

    assert pdf_path.is_file()


def test_f07_t1_source_blob_from_bytes_spools_and_cleans_on_exit(
    e2e_workspace: E2EWorkspace,
) -> None:
    """SourceBlob.from_bytes writes a temporary spool file, parses cleanly, and unlinks on __exit__."""
    from opendv.models.document_ir import SourceBlob
    from opendv.service.parsing_svc import ParsingService

    raw_pdf = make_minimal_pdf(e2e_workspace.workspace / "src.pdf").read_bytes()
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])
    spool_path: Path | None = None

    with SourceBlob.from_bytes(raw_pdf, filename_hint="in_mem.pdf") as blob:
        spool_path = blob.path
        assert blob.is_spooled_temp is True
        assert blob.path.is_file()
        ir = svc.parse_document(blob, sink_class="LOCAL_HUMAN_IO")
        assert ir.provenance.format_id == "pdf"
        assert ir.provenance.page_count == 1

    assert spool_path is not None
    assert not spool_path.exists()


def test_f07_t1_missing_local_file_maps_to_resource_not_found_404(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Non-existent local path maps FileNotFoundError -> RESOURCE_NOT_FOUND (exit 3, 404)."""
    from opendv.service.parsing_svc import ParsingService

    missing = e2e_workspace.workspace / "does_not_exist.pdf"
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])

    with pytest.raises(OpenDVError) as exc_info:
        svc.inspect_document(missing)
    assert exc_info.value.error_code == ErrorCode.RESOURCE_NOT_FOUND
    assert exc_info.value.exit_code == CliExitCode.NOT_FOUND
    assert exc_info.value.http_status == 404


def test_f07_t1_unsupported_extension_maps_to_unsupported_media_type_415(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Files outside .pdf/.docx/.md/.markdown/.txt raise UNSUPPORTED_MEDIA_TYPE (exit 2, 415)."""
    from opendv.service.parsing_svc import ParsingService

    bad_file = e2e_workspace.workspace / "payload.exe"
    bad_file.write_bytes(b"MZ\x90\x00not_a_document")
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])

    with pytest.raises(OpenDVError) as exc_info:
        svc.inspect_document(bad_file)
    assert exc_info.value.error_code == ErrorCode.UNSUPPORTED_MEDIA_TYPE
    assert exc_info.value.exit_code == CliExitCode.VALIDATION_ERROR
    assert exc_info.value.http_status == 415


@pytest.mark.parametrize(
    "bad_uri",
    [
        "file::https://example.com/a.pdf",
        "https://example.com/a.pdf",
        "file://remotehost/share/a.pdf",
    ],
)
def test_f07_t2_chained_uri_non_file_scheme_and_nonempty_netloc_return_400(
    e2e_workspace: E2EWorkspace,
    bad_uri: str,
) -> None:
    """Chained :: schemes, remote schemes, and non-empty file:// netloc fail with 400."""
    from opendv.service.parsing_svc import ParsingService

    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])
    with pytest.raises(OpenDVError) as exc_info:
        svc.inspect_document(bad_uri)
    assert exc_info.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT
    assert exc_info.value.exit_code == CliExitCode.VALIDATION_ERROR
    assert exc_info.value.http_status == 400


def test_f07_t2_windows_reserved_device_and_control_bytes_return_400(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Windows reserved device names and NUL/control bytes stay SYNTAX_INVALID_ARGUMENT (400)."""
    from opendv.service.parsing_svc import ParsingService

    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])
    for bad in (
        str(e2e_workspace.workspace / "CON.pdf"),
        str(e2e_workspace.workspace / "NUL.txt"),
        str(e2e_workspace.workspace / "bad\x00.pdf"),
    ):
        with pytest.raises(OpenDVError) as exc_info:
            svc.inspect_document(bad)
        assert exc_info.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT
        assert exc_info.value.http_status == 400


def test_f07_t2_unc_ntfs_ads_and_traversal_remap_to_security_violation_422(
    e2e_workspace: E2EWorkspace,
) -> None:
    """UNC shares, NTFS Alternate Data Streams, and .. traversal map to ARCHIVE_SECURITY_VIOLATION."""
    from opendv.service.parsing_svc import ParsingService

    pdf_path = make_minimal_pdf(e2e_workspace.workspace / "ads_host.pdf")
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])

    for bad in (
        r"\\server\share\a.pdf",
        f"{pdf_path}:secret_stream",
        str(e2e_workspace.workspace / ".." / "escape.pdf"),
    ):
        with pytest.raises(OpenDVError) as exc_info:
            svc.inspect_document(bad)
        assert exc_info.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
        assert exc_info.value.exit_code == CliExitCode.VALIDATION_ERROR
        assert exc_info.value.http_status == 422


def test_f07_t2_allow_root_containment_escape_remaps_to_422(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Files outside configured allowed_roots remap containment failure to 422."""
    from opendv.service.parsing_svc import ParsingService

    outside_pdf = make_minimal_pdf(e2e_workspace.root / "outside" / "secret.pdf")
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])

    with pytest.raises(OpenDVError) as exc_info:
        svc.inspect_document(outside_pdf)
    assert exc_info.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    assert exc_info.value.http_status == 422


def test_f07_t2_symlink_on_file_and_ancestor_dir_rejected_422(
    e2e_workspace: E2EWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Symlink or reparse point on target file or ancestor directory inside allowed root raises 422."""
    from opendv.service.bundles.zip_guard import WIN_REPARSE_ATTR
    from opendv.service.parsing_svc import ParsingService

    outside_dir = e2e_workspace.root / "outside_target"
    outside_pdf = make_minimal_pdf(outside_dir / "real.pdf")
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])

    file_link = e2e_workspace.workspace / "linked.pdf"
    dir_link = e2e_workspace.workspace / "linked_dir"
    created_real_symlinks = False
    try:
        os.symlink(outside_pdf, file_link)
        os.symlink(outside_dir, dir_link, target_is_directory=True)
        created_real_symlinks = True
    except OSError:
        created_real_symlinks = False

    if created_real_symlinks:
        for candidate in (file_link, dir_link / "real.pdf"):
            with pytest.raises(OpenDVError) as exc_info:
                svc.inspect_document(candidate)
            assert exc_info.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
            assert exc_info.value.http_status == 422
        return

    # Unprivileged Windows accounts without SeCreateSymbolicLinkPrivilege: test reparse-point path.
    nested_dir = e2e_workspace.workspace / "reparse_parent"
    nested_dir.mkdir(parents=True, exist_ok=True)
    nested_pdf = make_minimal_pdf(nested_dir / "doc.pdf")
    orig_lstat = Path.lstat

    def _mock_lstat(self_path: Path, *args: Any, **kwargs: Any) -> Any:
        real_st = orig_lstat(self_path, *args, **kwargs)
        if os.path.normcase(str(self_path)) == os.path.normcase(str(nested_dir)):
            return types.SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o755,
                st_file_attributes=WIN_REPARSE_ATTR,
                st_size=real_st.st_size,
            )
        return real_st

    monkeypatch.setattr(Path, "lstat", _mock_lstat)
    with pytest.raises(OpenDVError) as exc_info:
        svc.inspect_document(nested_pdf)
    assert exc_info.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    assert exc_info.value.http_status == 422


def test_f07_t2_file_exceeding_250_mib_rejected_413_without_full_ram_read(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Local file > 250 MiB (262,144,000 bytes) fails with ARCHIVE_SIZE_LIMIT_EXCEEDED (413)."""
    from opendv.service.parsing_svc import ParsingService

    huge_txt = e2e_workspace.workspace / "huge.txt"
    with huge_txt.open("wb") as fh:
        fh.seek(262_144_000)
        fh.write(b"\x00")

    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])
    with pytest.raises(OpenDVError) as exc_info:
        svc.inspect_document(huge_txt)
    assert exc_info.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED
    assert exc_info.value.exit_code == CliExitCode.VALIDATION_ERROR
    assert exc_info.value.http_status == 413


def test_f07_t2_source_blob_from_bytes_over_250_mib_rejected_413(
    e2e_workspace: E2EWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SourceBlob.from_bytes defaults max_bytes=262_144_000 and rejects oversize buffers with 413."""
    import inspect
    import tempfile

    from opendv.models.document_ir import SourceBlob

    sig = inspect.signature(SourceBlob.from_bytes)
    assert sig.parameters["max_bytes"].default == 262_144_000

    spool_dir = e2e_workspace.root / "isolated_tmp_413"
    spool_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tempfile, "tempdir", str(spool_dir))

    with pytest.raises(OpenDVError) as exc_info:
        SourceBlob.from_bytes(b"a" * 1025, filename_hint="oversize.txt", max_bytes=1024)
    assert exc_info.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED
    assert exc_info.value.http_status == 413
    assert list(spool_dir.iterdir()) == []


def test_f07_t2_source_blob_from_bytes_partial_write_cleanup_on_oserror(
    e2e_workspace: E2EWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Partial-write OSError during SourceBlob.from_bytes unlinks the temp spool file cleanly."""
    import tempfile

    from opendv.models.document_ir import SourceBlob

    spool_dir = e2e_workspace.root / "isolated_tmp_err"
    spool_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tempfile, "tempdir", str(spool_dir))

    real_ntf = tempfile.NamedTemporaryFile

    class _FailingWriteSpool:
        def __init__(self, real_file: Any) -> None:
            self._real = real_file
            self.name = real_file.name

        def write(self, data: bytes) -> int:
            half = max(1, len(data) // 2)
            self._real.write(data[:half])
            self._real.flush()
            raise OSError("Simulated disk full after partial write")

        def flush(self) -> None:
            self._real.flush()

        def close(self) -> None:
            self._real.close()

        def __enter__(self) -> _FailingWriteSpool:
            self._real.__enter__()
            return self

        def __exit__(self, *args: Any) -> None:
            self._real.__exit__(*args)

    def _wrapped_ntf(*args: Any, **kwargs: Any) -> _FailingWriteSpool:
        return _FailingWriteSpool(real_ntf(*args, **kwargs))

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", _wrapped_ntf)

    with pytest.raises(OpenDVError, match="Simulated disk full") as exc_info:
        SourceBlob.from_bytes(b"Hello partial spool payload", filename_hint="partial.txt")
    assert exc_info.value.error_code == ErrorCode.INTERNAL_SERVER_ERROR
    assert isinstance(exc_info.value.__cause__, OSError)

    assert list(spool_dir.iterdir()) == []
