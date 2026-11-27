"""Critical-path tests for PII redaction, word-box alignment, OCR, and scan gating."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

from opendv.models import ProjectInitReq
from opendv.models.document_ir import (
    DocumentBlock,
    NormalizedBBox,
    OcrBox,
    PageSlice,
    PageSpan,
    ParsedDocumentIR,
    ParseOptions,
    ParseProvenance,
    SourceBlob,
    compute_canonical_text_sha256,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.pii_tokenizer import (
    MASKED_PII_TOKEN,
    REDACTED_LEAK_RE,
    mask_pipe_tables_in_text,
    truncate_preserving_masked_pii,
)
from opendv.models.sources import ExternalMcpServerSpec
from opendv.models.wire_dtos_discovery import SourceAttachReq
from opendv.parsing import (
    ChunksProjector,
    DocumentMapProjector,
    MarkdownProjector,
    NormalizedWordBox,
    ParsingEngine,
    align_pii_spans_to_boxes,
    redact_ir,
    validate_ir,
)
from opendv.parsing.redaction import collect_table_aba_tokens_by_page, find_pii_spans
from opendv.service import OpenDVService
from opendv.service._ext_mcp_guards import resolve_doc_page_handles
from opendv.service._mcp_scout_tools import scout_read_document_pages
from opendv.service._mcp_tools import BrokerToolContext
from opendv.service.parsing_svc import ParsingService
from tests.helpers import DeterministicMockHarness

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PDFS = REPO_ROOT / "examples" / "sample_pdfs"
PUBLIC_AM = REPO_ROOT / "data" / "public_am"


def _make_text_pdf(
    path: Path,
    lines: list[str],
    *,
    width: float = 612,
    height: float = 792,
) -> Path:
    writer = PdfWriter()
    page = writer.add_blank_page(width=width, height=height)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    ops = ["BT", "/F1 12 Tf", "72 720 Td", "18 TL"]
    for idx, line in enumerate(lines):
        esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"({esc}) Tj" if idx == 0 else f"T* ({esc}) Tj")
    ops.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(ops).encode("latin-1"))
    page[NameObject("/Contents")] = ArrayObject([writer._add_object(stream)])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        writer.write(fh)
    return path


def test_render_dpi_and_40mp_pixel_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify dpi <= 300 and <= 40,000,000 px preflight before screenshot rendering."""
    engine = ParsingEngine()
    pdf_path = _make_text_pdf(tmp_path / "normal.pdf", ["Preflight check page"])
    with SourceBlob.from_path(pdf_path) as blob:
        png_300, res_300 = engine.render_page(blob, 1, ParseOptions(dpi=300))
        assert res_300.dpi == 300
        assert png_300.startswith(b"\x89PNG\r\n\x1a\n")

        with pytest.raises(OpenDVError) as exc_dpi:
            engine.render_page(blob, 1, ParseOptions(dpi=301))
        assert exc_dpi.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    large_pdf = _make_text_pdf(
        tmp_path / "large_page.pdf",
        ["Large viewport digital preflight verification page with sufficient text density."],
        width=2500,
        height=2500,
    )
    with SourceBlob.from_path(large_pdf) as blob:
        # 2500*2500*(150/72)^2 ~= 27.1M <= 40,000,000 passes
        _, res_150 = engine.render_page(blob, 1, ParseOptions(dpi=150))
        assert res_150.dpi == 150

        # 2500*2500*(300/72)^2 ~= 108.5M > 40,000,000 fails in preflight before render
        pdf_parser = engine.registry.get_parser("pdf")
        seen_screenshots: list[bool] = []
        orig_run = pdf_parser._run_liteparse  # type: ignore[attr-defined]

        def _spy_run(*args: Any, **kwargs: Any) -> Any:
            seen_screenshots.append(bool(kwargs.get("extract_screenshots", False)))
            return orig_run(*args, **kwargs)

        monkeypatch.setattr(pdf_parser, "_run_liteparse", _spy_run)
        with pytest.raises(OpenDVError) as exc_px:
            engine.render_page(blob, 1, ParseOptions(dpi=300))
        assert exc_px.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED
        assert seen_screenshots == [False]


def test_model_egress_redacted_ir_copy_vs_raw_and_redacted_placeholder_non_regression(
    tmp_path: Path,
) -> None:
    """Verify MODEL_EGRESS produces a redacted IR copy while raw IR & [REDACTED_ PDF stay intact."""
    engine = ParsingEngine()
    pdf_path = _make_text_pdf(
        tmp_path / "redacted_raw.pdf",
        [
            "Exhibit [REDACTED_BY_COURT] stays readable in raw IR.",
            "Taxpayer SSN 123-45-6789 and email lead@fund-example.com",
        ],
    )
    with SourceBlob.from_path(pdf_path) as blob:
        raw_ir = engine.parse(blob, sink_class="LOCAL_HUMAN_IO", redaction="off")
        egress_ir = engine.parse(blob, sink_class="MODEL_EGRESS")

        # Raw internal IR keeps verbatim text, [REDACTED_BY_COURT], and original digest
        assert raw_ir.provenance.redacted is False
        assert raw_ir.provenance.sink_class == "LOCAL_HUMAN_IO"
        assert "[REDACTED_BY_COURT]" in raw_ir.pages[0].canonical_text
        assert "123-45-6789" in raw_ir.pages[0].canonical_text
        assert validate_ir(raw_ir) == []

        # MODEL_EGRESS returns a non-destructive redacted copy with recomputed SHA-256
        assert egress_ir.provenance.redacted is True
        assert egress_ir.provenance.sink_class == "MODEL_EGRESS"
        assert "123-45-6789" not in egress_ir.pages[0].canonical_text
        assert "lead@fund-example.com" not in egress_ir.pages[0].canonical_text
        assert MASKED_PII_TOKEN in egress_ir.pages[0].canonical_text
        assert egress_ir.pages[0].canonical_text_sha256 != raw_ir.pages[0].canonical_text_sha256
        assert validate_ir(egress_ir) == []

        # Original raw_ir was never mutated
        assert "123-45-6789" in raw_ir.pages[0].canonical_text

        # MODEL_EGRESS explicitly forbids redaction='off'
        with pytest.raises(OpenDVError) as exc_off:
            engine.parse(blob, sink_class="MODEL_EGRESS", redaction="off")
        assert exc_off.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


def test_span_based_multi_token_pii_alignment_and_fail_closed_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify multi-token RE_PHONE, RE_CC_PAN, RE_ABA_DIGITS box alignment & UNREDACTED_SCAN_BLOCKED."""
    def _wb(idx: int, text: str) -> NormalizedWordBox:
        x0 = min(0.9, 0.05 + idx * 0.1)
        return NormalizedWordBox(
            bbox=NormalizedBBox(x0=x0, y0=0.1, x1=x0 + 0.08, y1=0.15),
            text=text,
        )

    # 1. RE_PHONE multi-word alignment: "(415)" + "555-0199"
    phone_boxes = [_wb(0, "Call"), _wb(1, "(415)"), _wb(2, "555-0199"), _wb(3, "today")]
    aligned_phone = align_pii_spans_to_boxes("Call (415) 555-0199 today", phone_boxes)
    assert [b.text for b in aligned_phone] == ["(415)", "555-0199"]

    # 2. RE_CC_PAN multi-word Luhn alignment vs non-Luhn control
    cc_boxes = [_wb(0, "Card"), _wb(1, "4532"), _wb(2, "0151"), _wb(3, "1283"), _wb(4, "0366")]
    aligned_cc = align_pii_spans_to_boxes("Card 4532 0151 1283 0366", cc_boxes)
    assert [b.text for b in aligned_cc] == ["4532", "0151", "1283", "0366"]
    assert find_pii_spans("Card 4532 0151 1283 0365") == []  # fails Luhn

    # 3. RE_ABA_DIGITS context-gated alignment
    aba_boxes = [_wb(0, "ABA"), _wb(1, "routing"), _wb(2, "021000021")]
    aligned_aba = align_pii_spans_to_boxes("ABA routing 021000021", aba_boxes)
    assert [b.text for b in aligned_aba] == ["021000021"]
    assert find_pii_spans("Invoice 021000021") == []  # no ABA context

    # 4. Fail-closed UNREDACTED_SCAN_BLOCKED on missing/partial word boxes
    with pytest.raises(OpenDVError) as exc_zero:
        align_pii_spans_to_boxes("Phone (415) 555-0199", [])
    assert exc_zero.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    with pytest.raises(OpenDVError) as exc_partial:
        align_pii_spans_to_boxes(
            "SSN 123-45-6789 and 987-65-4321",
            [_wb(0, "SSN"), _wb(1, "123-45-6789")],
        )
    assert exc_partial.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    # 5. End-to-end PDF visual blackout across RE_PHONE, RE_CC_PAN, RE_ABA_DIGITS, RE_SSN
    multi_pdf = _make_text_pdf(
        tmp_path / "multi_pii.pdf",
        [
            "Phone: (415) 555-0199",
            "Card: 4532 0151 1283 0366",
            "Routing: ABA 021000021",
            "Tax: 123-45-6789",
        ],
    )
    engine = ParsingEngine()
    with SourceBlob.from_path(multi_pdf) as blob:
        png_bytes, r_res = engine.render_page(
            blob, 1, ParseOptions(dpi=150), sink_class="MODEL_EGRESS", redaction="on"
        )
        assert r_res.redacted is True
        assert r_res.ocr_boxes_applied >= 7
        with Image.open(io.BytesIO(png_bytes)) as img:
            rgba = img.convert("RGBA")
            colors = rgba.getcolors(maxcolors=rgba.width * rgba.height) or []
            assert sum(cnt for cnt, px in colors if px == (0, 0, 0, 255)) > 100

        # If parser returns empty word_boxes on a PII page -> fail closed UNREDACTED_SCAN_BLOCKED
        pdf_parser = engine.registry.get_parser("pdf")
        orig_render = pdf_parser.render_page

        def _strip_boxes(b: SourceBlob, pg: int, opts: ParseOptions) -> Any:
            buf = orig_render(b, pg, opts)
            return types.SimpleNamespace(
                png_bytes=buf.png_bytes,
                width_px=buf.width_px,
                height_px=buf.height_px,
                dpi=buf.dpi,
                word_boxes=(),
            )

        monkeypatch.setattr(pdf_parser, "render_page", _strip_boxes)
        with pytest.raises(OpenDVError) as exc_miss:
            engine.render_page(blob, 1, ParseOptions(dpi=150), sink_class="MODEL_EGRESS")
        assert exc_miss.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED


def test_ocr_provider_missing_extra_missing_binary_and_approve_scan_separation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify OcrProviderFn, DRIVER_MISSING branches, and approve_scan vs MODEL_EGRESS separation."""
    scanned_pdf = SAMPLE_PDFS / "institutional_scanned_schedule_ocr_test.pdf"
    custody_pdf = PUBLIC_AM / "am_custody_and_voting_guide.pdf"
    svc = ParsingService(allowed_roots=[REPO_ROOT, tmp_path])

    # 1. LOCAL_HUMAN_IO vs approve_scan vs MODEL_EGRESS separation (zero skips)
    with pytest.raises(OpenDVError) as exc_local_blocked:
        svc.render_document_page(scanned_pdf, 2, sink_class="LOCAL_HUMAN_IO", approve_scan=False)
    assert exc_local_blocked.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    raw_png, res_approved = svc.render_document_page(
        scanned_pdf, 2, sink_class="LOCAL_HUMAN_IO", approve_scan=True
    )
    assert raw_png.startswith(b"\x89PNG\r\n\x1a\n")
    assert res_approved.redacted is False

    # approve_scan=True MUST NOT bypass MODEL_EGRESS redaction when OCR is absent
    orig_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        "opendv.parsing.redaction.importlib.util.find_spec",
        lambda name, *a, **kw: None if name == "pytesseract" else orig_find_spec(name, *a, **kw),
    )
    monkeypatch.setattr("opendv.parsing.redaction.shutil.which", lambda _cmd: None)

    with pytest.raises(OpenDVError) as exc_egress_blocked:
        svc.render_document_page(
            custody_pdf, 4, sink_class="MODEL_EGRESS", approve_scan=True
        )
    assert exc_egress_blocked.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    # 2. Mocked missing [ocr] package -> DRIVER_MISSING(extra_name="ocr") with uv sync --extra ocr
    with pytest.raises(OpenDVError) as exc_missing_pkg:
        svc.render_document_page(
            scanned_pdf,
            2,
            sink_class="MODEL_EGRESS",
            options=ParseOptions(ocr=True),
        )
    assert exc_missing_pkg.value.error_code == ErrorCode.DRIVER_MISSING
    assert exc_missing_pkg.value.remediation is not None
    assert any(
        a.actor == "agent" and a.command == "uv sync --extra ocr"
        for a in exc_missing_pkg.value.remediation.actions
    )

    # 3. Mocked pytesseract present + missing system binary -> DRIVER_MISSING with actor="human"
    monkeypatch.setattr("opendv.parsing.redaction.importlib.util.find_spec", lambda _name: object())
    monkeypatch.setattr("opendv.parsing.redaction.shutil.which", lambda _cmd: None)
    with pytest.raises(OpenDVError) as exc_missing_bin:
        svc.render_document_page(
            scanned_pdf,
            2,
            sink_class="MODEL_EGRESS",
            options=ParseOptions(ocr=True),
        )
    assert exc_missing_bin.value.error_code == ErrorCode.DRIVER_MISSING
    assert exc_missing_bin.value.remediation is not None
    assert any(
        a.actor == "human" and "tesseract" in a.label.lower()
        for a in exc_missing_bin.value.remediation.actions
    )

    # 4. Injected OcrProviderFn enables MODEL_EGRESS blackout & OCR text extraction
    def _ocr_provider(_png: bytes, page_num: int) -> tuple[list[OcrBox], str]:
        if page_num == 2:
            return (
                [
                    OcrBox(25, 30, 120, 60, "Routing"),
                    OcrBox(130, 30, 260, 60, "123-45-6789"),
                ],
                "Routing 123-45-6789",
            )
        return ([], "")

    red_png, res_ocr = svc.render_document_page(
        scanned_pdf,
        2,
        sink_class="MODEL_EGRESS",
        approve_scan=True,
        ocr_provider=_ocr_provider,
    )
    assert res_ocr.redacted is True
    assert res_ocr.ocr_used is True
    assert res_ocr.ocr_boxes_applied == 1
    with Image.open(io.BytesIO(red_png)) as img:
        assert img.convert("RGBA").getpixel((190, 45)) == (0, 0, 0, 255)

    ocr_engine = ParsingEngine(ocr_provider=_ocr_provider)
    with SourceBlob.from_path(scanned_pdf) as blob:
        ir_ocr = ocr_engine.parse(
            blob, ParseOptions(pages=[2], ocr=True), sink_class="MODEL_EGRESS"
        )
        assert ir_ocr.pages[0].extracted_via == "ocr_text"
        assert "123-45-6789" not in ir_ocr.pages[0].canonical_text
        assert MASKED_PII_TOKEN in ir_ocr.pages[0].canonical_text

    # 5. Mocked pytesseract.image_to_data works end-to-end when [ocr] + tesseract are present
    fake_tess = types.SimpleNamespace(
        Output=types.SimpleNamespace(DICT="dict"),
        image_to_data=lambda _img, output_type=None: {
            "text": ["Phone", "(415)", "555-0199"],
            "conf": ["95", "92", "90"],
            "left": [10, 80, 140],
            "top": [20, 20, 20],
            "width": [60, 50, 90],
            "height": [20, 20, 20],
        },
    )
    monkeypatch.setattr("opendv.parsing.redaction.shutil.which", lambda _cmd: "tesseract")
    monkeypatch.setitem(sys.modules, "pytesseract", fake_tess)
    tess_png, res_tess = svc.render_document_page(
        scanned_pdf, 2, sink_class="MODEL_EGRESS", options=ParseOptions(ocr=True)
    )
    assert res_tess.redacted is True
    assert res_tess.ocr_used is True
    assert res_tess.ocr_boxes_applied == 2
    with Image.open(io.BytesIO(tess_png)) as img:
        assert img.convert("RGBA").getpixel((100, 30)) == (0, 0, 0, 255)
        assert img.convert("RGBA").getpixel((180, 30)) == (0, 0, 0, 255)


def test_outline_title_truncation_preserves_masked_pii(tmp_path: Path) -> None:
    """Gap 3: 240/120-char outline truncation never bisects <MASKED_PII> into partial '<MASK'."""
    assert truncate_preserving_masked_pii(("A" * 234) + " " + MASKED_PII_TOKEN, 240) == "A" * 234
    assert truncate_preserving_masked_pii(("A" * 227) + " " + MASKED_PII_TOKEN, 240) == (
        ("A" * 227) + " " + MASKED_PII_TOKEN
    )
    assert truncate_preserving_masked_pii(MASKED_PII_TOKEN + " suffix", 5) == ""

    md_path = tmp_path / "long_pii_heading.md"
    md_path.write_text(
        f"# {'A' * 234} 123-45-6789\n\n## {'B' * 114} 123-45-6789\n\nBody text.\n",
        encoding="utf-8",
    )
    engine = ParsingEngine()
    with SourceBlob.from_path(md_path) as blob:
        egress_ir = engine.parse(blob, sink_class="MODEL_EGRESS", redaction="on")
        raw_ir = engine.parse(blob, sink_class="LOCAL_HUMAN_IO", redaction="off")

    assert egress_ir.outline and "<MASK" not in egress_ir.outline[0].title
    assert len(egress_ir.outline[0].title) <= 240
    assert validate_ir(egress_ir) == []

    redacted_ir = redact_ir(raw_ir)
    assert redacted_ir.outline and "<MASK" not in redacted_ir.outline[0].title
    assert len(redacted_ir.outline[0].title) <= 240
    assert validate_ir(redacted_ir) == []

    projected = engine.project(raw_ir, target="document_map", redaction="on")
    assert isinstance(projected, dict)
    dmap = projected["document_map"]
    assert dmap.outline and "<MASK" not in dmap.outline[0].title
    assert "<MASK" not in dmap.pages[0].heading_preview
    assert len(dmap.pages[0].heading_preview) <= 120

    # Fallback outline & heading_preview without explicit headings
    fb_path = tmp_path / "fallback_120.txt"
    fb_path.write_text(("C" * 114) + " 123-45-6789\n", encoding="utf-8")
    with SourceBlob.from_path(fb_path) as fb_blob:
        fb_egress = engine.parse(fb_blob, sink_class="MODEL_EGRESS", redaction="on")
        fb_raw = engine.parse(fb_blob, sink_class="LOCAL_HUMAN_IO", redaction="off")
    assert fb_egress.outline and "<MASK" not in fb_egress.outline[0].title
    fb_red = redact_ir(fb_raw)
    assert fb_red.outline and "<MASK" not in fb_red.outline[0].title
    fb_proj = engine.project(fb_raw, target="document_map", redaction="on")
    assert isinstance(fb_proj, dict)
    assert "<MASK" not in fb_proj["document_map"].pages[0].heading_preview
    assert fb_proj["document_map"].outline and "<MASK" not in fb_proj["document_map"].outline[0].title


def _make_bordered_aba_table_pdf(path: Path) -> Path:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    ops = [
        "0.5 w",
        "40 640 360 40 re S",
        "400 640 170 40 re S",
        "40 680 360 40 re S",
        "400 680 170 40 re S",
        "BT /F1 9 Tf 1 0 0 1 48 695 Tm (Account Holder) Tj ET",
        "BT /F1 9 Tf 1 0 0 1 408 695 Tm (ABA Routing Number) Tj ET",
        f"BT /F1 8 Tf 1 0 0 1 48 655 Tm (Holder_{'X' * 38}) Tj ET",
        "BT /F1 9 Tf 1 0 0 1 408 655 Tm (021000021) Tj ET",
    ]
    stream = DecodedStreamObject()
    stream.set_data("\n".join(ops).encode("latin-1"))
    page[NameObject("/Contents")] = ArrayObject([writer._add_object(stream)])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        writer.write(fh)
    return path


def test_bordered_pdf_table_and_headerless_aba_redaction_across_all_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap 6: bordered PDF tables and headerless Table blocks mask ABA routing numbers everywhere."""
    ws = tmp_path / "ws"
    state_dir = tmp_path / "state"
    ws.mkdir(parents=True, exist_ok=True)
    bordered_pdf = _make_bordered_aba_table_pdf(ws / "bordered_aba_table.pdf")
    psvc = ParsingService(workspace_root=ws, allowed_roots=[ws])

    # 1. LiteParse promotes row 0 -> table_headers & emits GFM separator; MODEL_EGRESS masks ABA
    ir_raw = psvc.parse_document(bordered_pdf, sink_class="LOCAL_HUMAN_IO", redaction="off")
    tbl_raw = [b for b in ir_raw.blocks if b.kind == "table"]
    assert tbl_raw, "LiteParse must emit blk.kind == 'table' for bordered 2x2 PDF"
    assert tbl_raw[0].table_headers == ["Account Holder", "ABA Routing Number"]
    assert tbl_raw[0].table_rows == [[f"Holder_{'X' * 38}", "021000021"]]
    assert "| --- | --- |" in tbl_raw[0].text

    ir_egress = psvc.parse_document(bordered_pdf, sink_class="MODEL_EGRESS")
    assert validate_ir(ir_egress) == []
    assert "021000021" not in ir_egress.pages[0].canonical_text
    assert MASKED_PII_TOKEN in ir_egress.pages[0].canonical_text
    tbl_eg = [b for b in ir_egress.blocks if b.kind == "table"][0]
    assert "021000021" not in tbl_eg.text
    assert tbl_eg.table_rows == [[f"Holder_{'X' * 38}", MASKED_PII_TOKEN]]

    # 2. Grounding notes, markdown, and chunks projections mask 021000021
    notes = psvc.build_grounding_summary_notes([bordered_pdf], max_notes=2)
    assert notes and "021000021" not in notes[0]
    assert REDACTED_LEAK_RE.search(notes[0]) is None

    for src_ir in (ir_raw, ir_egress):
        md_out = str(psvc.export_ir(src_ir, target="markdown", sink_class="MODEL_EGRESS"))
        assert "021000021" not in md_out
        assert MASKED_PII_TOKEN in md_out
        chunks_out = psvc.export_ir(src_ir, target="chunks", sink_class="MODEL_EGRESS")
        assert isinstance(chunks_out, list) and chunks_out
        assert all(
            "021000021" not in c.text and "021000021" not in (c.table_json or "")
            for c in chunks_out
        )

    # 3. get_document_pages, scout_read_document_pages, and resolve_doc_page_handles
    svc = OpenDVService(ws, state_dir=state_dir, harness=DeterministicMockHarness())
    svc.init_project(ProjectInitReq(project_id="p_gap6", domain_namespace="asset_management"))
    pdf_src = svc.attach_source("p_gap6", SourceAttachReq(kind="pdf", uri_or_filename=str(bordered_pdf)))

    doc_pages = svc.get_document_pages("p_gap6", pdf_src.source_id, page_start=1, page_end=1)
    assert len(doc_pages) == 1
    assert "021000021" not in doc_pages[0].masked_text
    assert MASKED_PII_TOKEN in doc_pages[0].masked_text

    ctx = BrokerToolContext(
        project_id="p_gap6",
        run_id="run-gap6",
        state_store=svc.state_manager,
        parsing_service=psvc,
        parsed_pages={},
    )
    scout_views = scout_read_document_pages(ctx, pdf_src.source_id, 1, 1)
    assert len(scout_views) == 1
    assert "021000021" not in scout_views[0].masked_text
    assert MASKED_PII_TOKEN in scout_views[0].masked_text

    srv = ExternalMcpServerSpec(
        server_id="ext_gap6",
        transport="stdio",
        command=["python", "-m", "noop"],
        allow_document_pages=True,
    )
    resolved, _, _ = resolve_doc_page_handles(
        {"doc": {"$opendv_handle": "doc_page", "doc_id": pdf_src.source_id, "page_number": 1}},
        project_id="p_gap6",
        server=srv,
        state_store=svc.state_manager,
    )
    assert "021000021" not in resolved["doc"]["masked_text"]
    assert MASKED_PII_TOKEN in resolved["doc"]["masked_text"]

    # 4. render_document_page(MODEL_EGRESS) blacks out the 021000021 word bbox
    png_bytes, res_render = psvc.render_document_page(
        bordered_pdf, 1, sink_class="MODEL_EGRESS", options=ParseOptions(dpi=150)
    )
    assert res_render.redacted is True
    assert res_render.ocr_boxes_applied >= 1

    pdf_parser = ParsingEngine().registry.get_parser("pdf")
    with SourceBlob.from_path(bordered_pdf) as blob:
        raw_buf = pdf_parser.render_page(blob, 1, ParseOptions(dpi=150, emit_word_boxes=True))
    aba_box = next(wb for wb in raw_buf.word_boxes if "021000021" in wb.text)
    cx = int(((aba_box.bbox.x0 + aba_box.bbox.x1) / 2.0) * res_render.width)
    cy = int(((aba_box.bbox.y0 + aba_box.bbox.y1) / 2.0) * res_render.height)
    with Image.open(io.BytesIO(png_bytes)) as img:
        assert img.convert("RGBA").getpixel((cx, cy)) == (0, 0, 0, 255)

    # 5. Fail-closed UNREDACTED_SCAN_BLOCKED if table ABA token cannot align to a word box
    local_engine = ParsingEngine()
    local_pdf_parser = local_engine.registry.get_parser("pdf")
    orig_render = local_pdf_parser.render_page

    def _drop_aba_word_box(b: SourceBlob, pg: int, opts: ParseOptions) -> Any:
        buf = orig_render(b, pg, opts)
        filtered = tuple(wb for wb in buf.word_boxes if "021000021" not in wb.text)
        return types.SimpleNamespace(
            png_bytes=buf.png_bytes,
            width_px=buf.width_px,
            height_px=buf.height_px,
            dpi=buf.dpi,
            word_boxes=filtered,
        )

    monkeypatch.setattr(local_pdf_parser, "render_page", _drop_aba_word_box)
    with SourceBlob.from_path(bordered_pdf) as blob:
        with pytest.raises(OpenDVError) as exc_unaligned:
            local_engine.render_page(
                blob, 1, ParseOptions(dpi=150), sink_class="MODEL_EGRESS"
            )
        assert exc_unaligned.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    # 6. Headerless custom SPI DocumentBlock(table_headers=[], table_rows=[...])
    pipeless_page = f"Account Holder ABA Routing Number\n{'Z' * 45} 021000021"
    headerless_ir = ParsedDocumentIR(
        provenance=ParseProvenance(
            parser_id="custom_spi",
            parser_version="1.0",
            source_uri_sanitized="custom.pdf",
            binary_sha256="a" * 64,
            format_id="pdf",
            page_count=1,
            redacted=False,
            sink_class="LOCAL_HUMAN_IO",
        ),
        pages=[
            PageSlice(
                page_number=1,
                slice_kind="physical_page",
                renderable=True,
                canonical_text=pipeless_page,
                canonical_text_sha256=compute_canonical_text_sha256(pipeless_page),
                raw_text_length=len(pipeless_page),
                non_ws_chars=len(pipeless_page.replace(" ", "")),
                image_block_count=0,
                needs_ocr=False,
                extracted_via="layout_text",
                width_pt=612.0,
                height_pt=792.0,
            )
        ],
        outline=[],
        blocks=[
            DocumentBlock(
                block_id="p1_b0",
                kind="table",
                reading_order=0,
                spans=[PageSpan(page_number=1)],
                text=f"| Account Holder | ABA Routing Number |\n| {'Z' * 45} | 021000021 |",
                table_headers=[],
                table_rows=[
                    ["Account Holder", "ABA Routing Number"],
                    ["Z" * 45, "021000021"],
                ],
            )
        ],
    )
    aba_by_page = collect_table_aba_tokens_by_page(headerless_ir.blocks)
    assert aba_by_page == {1: {"021000021"}}
    red_hl = redact_ir(headerless_ir)
    assert "021000021" not in red_hl.pages[0].canonical_text
    assert "021000021" not in red_hl.blocks[0].text
    assert "021000021" not in json.dumps(red_hl.blocks[0].table_rows)

    hl_dm = DocumentMapProjector().project(headerless_ir, redaction="on")
    assert "021000021" not in hl_dm["page_text_views"][0].masked_text
    hl_chunks = ChunksProjector().project(headerless_ir, redaction="on")
    assert all(
        "021000021" not in c.text and "021000021" not in (c.table_json or "")
        for c in hl_chunks
    )
    hl_md = MarkdownProjector().project(headerless_ir, redaction="on")
    assert "021000021" not in hl_md

    with pytest.raises(OpenDVError) as exc_hl_miss:
        align_pii_spans_to_boxes(pipeless_page, [], aba_tokens={"021000021"})
    assert exc_hl_miss.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED


def test_resolve_doc_page_handles_digital_pdf_pixel_redaction_without_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap 7: digital PDF doc_page handles blackout native PII pixels even without [ocr]."""
    ws = tmp_path / "ws_gap7"
    state_dir = tmp_path / "state_gap7"
    ws.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    ops = [
        "0.5 w",
        "40 640 360 40 re S",
        "400 640 170 40 re S",
        "40 680 360 40 re S",
        "400 680 170 40 re S",
        "BT /F1 9 Tf 1 0 0 1 48 695 Tm (Account Holder) Tj ET",
        "BT /F1 9 Tf 1 0 0 1 408 695 Tm (ABA Routing Number) Tj ET",
        f"BT /F1 8 Tf 1 0 0 1 48 655 Tm (Holder_{'X' * 38}) Tj ET",
        "BT /F1 9 Tf 1 0 0 1 408 655 Tm (021000021) Tj ET",
        "BT /F1 10 Tf 1 0 0 1 48 590 Tm (Employee SSN 123-45-6789) Tj ET",
    ]
    stream = DecodedStreamObject()
    stream.set_data("\n".join(ops).encode("latin-1"))
    page[NameObject("/Contents")] = ArrayObject([writer._add_object(stream)])
    pdf_path = ws / "digital_pii.pdf"
    with pdf_path.open("wb") as fh:
        writer.write(fh)

    orig_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        "opendv.parsing.redaction.importlib.util.find_spec",
        lambda name, *a, **kw: None if name == "pytesseract" else orig_find_spec(name, *a, **kw),
    )
    monkeypatch.setattr("opendv.parsing.redaction.shutil.which", lambda _cmd: None)

    svc = OpenDVService(ws, state_dir=state_dir, harness=DeterministicMockHarness())
    svc.init_project(ProjectInitReq(project_id="p_gap7", domain_namespace="asset_management"))
    src = svc.attach_source("p_gap7", SourceAttachReq(kind="pdf", uri_or_filename=str(pdf_path)))
    srv = ExternalMcpServerSpec(
        server_id="ext_gap7",
        transport="stdio",
        command=["python", "-m", "noop"],
        allow_document_pages=True,
    )
    resolved, meta, _ = resolve_doc_page_handles(
        {"doc": {"$opendv_handle": "doc_page", "doc_id": src.source_id, "page_number": 1}},
        project_id="p_gap7",
        server=srv,
        state_store=svc.state_manager,
        ocr_provider=None,
    )
    assert meta == (src.source_id, 1)
    assert "021000021" not in resolved["doc"]["masked_text"]
    assert "123-45-6789" not in resolved["doc"]["masked_text"]
    assert MASKED_PII_TOKEN in resolved["doc"]["masked_text"]

    png_bytes = base64.b64decode(resolved["doc"]["redacted_png_b64"])
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")

    pdf_parser = ParsingEngine().registry.get_parser("pdf")
    with SourceBlob.from_path(pdf_path) as blob:
        raw_buf = pdf_parser.render_page(blob, 1, ParseOptions(dpi=108, emit_word_boxes=True))
    aba_box = next(wb for wb in raw_buf.word_boxes if "021000021" in wb.text)
    ssn_box = next(wb for wb in raw_buf.word_boxes if "123-45-6789" in wb.text)

    with Image.open(io.BytesIO(png_bytes)) as img:
        rgba = img.convert("RGBA")
        for wb in (aba_box, ssn_box):
            cx = int(((wb.bbox.x0 + wb.bbox.x1) / 2.0) * rgba.width)
            cy = int(((wb.bbox.y0 + wb.bbox.y1) / 2.0) * rgba.height)
            assert rgba.getpixel((cx, cy)) == (0, 0, 0, 255)


def test_blank_header_pipe_table_and_ocr_pipe_table_aba_redaction(tmp_path: Path) -> None:
    """Gaps 8 & 9: blank-header pipe tables (.md/.txt) and OCR pipe tables mask ABA digits."""
    ws = tmp_path / "ws_gap89"
    ws.mkdir(parents=True, exist_ok=True)
    blank_hdr_table = (
        "|  |  |\n"
        "| --- | --- |\n"
        "| Account Holder | ABA Routing Number |\n"
        f"| Holder_{'X' * 38} | 021000021 |\n"
    )
    masked_direct = mask_pipe_tables_in_text(blank_hdr_table)
    assert "021000021" not in masked_direct
    assert MASKED_PII_TOKEN in masked_direct

    non_aba_blank_hdr = (
        "|  |  |\n"
        "| --- | --- |\n"
        "| Account Holder | Invoice Reference |\n"
        f"| Holder_{'X' * 38} | 021000021 |\n"
    )
    assert "021000021" in mask_pipe_tables_in_text(non_aba_blank_hdr)

    psvc = ParsingService(workspace_root=ws, allowed_roots=[ws])
    for name in ("blank_hdr.md", "blank_hdr.txt"):
        doc_path = ws / name
        doc_path.write_text(blank_hdr_table, encoding="utf-8")
        ir_eg = psvc.parse_document(doc_path, sink_class="MODEL_EGRESS")
        assert validate_ir(ir_eg) == []
        assert "021000021" not in ir_eg.pages[0].canonical_text
        assert MASKED_PII_TOKEN in ir_eg.pages[0].canonical_text
        tbl_blks = [b for b in ir_eg.blocks if b.kind == "table"]
        assert len(tbl_blks) == 1
        assert "021000021" not in tbl_blks[0].text
        assert MASKED_PII_TOKEN in tbl_blks[0].text
        assert tbl_blks[0].table_rows is not None
        assert "021000021" not in json.dumps(tbl_blks[0].table_rows)

        notes = psvc.build_grounding_summary_notes([doc_path], max_notes=2)
        assert notes and "021000021" not in notes[0]
        assert REDACTED_LEAK_RE.search(notes[0]) is None

    # Gap 9: ParsingEngine._apply_ocr_to_ir masks OCR pipe-table ABA >32 chars from header
    scanned_pdf = SAMPLE_PDFS / "institutional_scanned_schedule_ocr_test.pdf"
    ocr_pipe_table = (
        "| Account Holder | ABA Routing Number |\n"
        "| --- | --- |\n"
        f"| Holder_{'X' * 38} | 021000021 |"
    )
    ocr_engine = ParsingEngine(
        ocr_provider=lambda _png, _pg: ([OcrBox(10, 10, 100, 30, "021000021")], ocr_pipe_table)
    )
    with SourceBlob.from_path(scanned_pdf) as blob:
        ir_ocr = ocr_engine.parse(blob, ParseOptions(pages=[2], ocr=True), sink_class="MODEL_EGRESS")
    assert ir_ocr.pages[0].extracted_via == "ocr_text"
    assert "021000021" not in ir_ocr.pages[0].canonical_text
    assert MASKED_PII_TOKEN in ir_ocr.pages[0].canonical_text
