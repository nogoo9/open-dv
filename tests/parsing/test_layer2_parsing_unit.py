"""Deterministic unit tests for Milestone M2: Pure Layer-2 Parsing Core (src/opendv/parsing)."""

from __future__ import annotations

import io
import json
import stat
import sys
import types
import zipfile
from pathlib import Path
from typing import Any

import docx
import duckdb
import pypdf
import pytest
from PIL import Image
from pypdf import PdfWriter

from opendv.models.document_ir import (
    ChunkRecord,
    DocumentBlock,
    DocumentFormat,
    DocumentOutlineNode,
    InspectResult,
    NormalizedBBox,
    OcrBox,
    PageSlice,
    PageSpan,
    ParsedDocumentIR,
    ParseOptions,
    ParseProvenance,
    RedactionMode,
    SourceBlob,
    TargetProjection,
    compute_canonical_text_sha256,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.parsing import (
    BoundedZipReader,
    ChunksProjector,
    DocumentMapProjector,
    DocxParser,
    FormatParser,
    IRProjector,
    LiteParsePdfParser,
    MarkdownProjector,
    MarkdownTextParser,
    NormalizedWordBox,
    ParsingEngine,
    ParsingRegistry,
    RenderedPageBuffer,
    align_pii_spans_to_boxes,
    capabilities_snapshot,
    default_registry,
    parse_page_range_spec,
    redact_png_boxes,
    run_pytesseract_ocr,
    validate_ir,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PUBLIC_AM = REPO_ROOT / "data" / "public_am"
SAMPLE_PDFS = REPO_ROOT / "examples" / "sample_pdfs"
SRC_MATRIX = REPO_ROOT / "examples" / "scenarios" / "src_matrix"
PUBLIC_FIXTURES = REPO_ROOT / "tests" / "parsing" / "fixtures_public"


def _make_full_page_scan_pdf(path: Path) -> None:
    """Create a 1-page PDF containing only a large raster image (no text)."""
    img = Image.new("RGB", (600, 800), color=(245, 245, 245))
    pdf_buf = io.BytesIO()
    img.save(pdf_buf, format="PDF")
    path.write_bytes(pdf_buf.getvalue())


def _make_text_pdf(
    path: Path,
    lines: list[str],
    *,
    rotate: int = 0,
    password: str | None = None,
) -> None:
    """Create a simple 1-page PDF with Helvetica text via Pillow + pypdf."""
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    font_dict = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font_dict)
    resources = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    page[NameObject("/Resources")] = resources

    ops: list[str] = ["BT", "/F1 12 Tf"]
    y = 740
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"1 0 0 1 50 {y} Tm ({escaped}) Tj")
        y -= 24
    ops.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(ops).encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    if rotate:
        page.rotate(rotate)
    if password:
        writer.encrypt(password)
    with path.open("wb") as fh:
        writer.write(fh)


def test_pdf_parser_public_am_and_scanned_fixtures() -> None:
    """Verify LiteParsePdfParser on real custody guide, scanned schedule, and f1120 PDFs."""
    engine = ParsingEngine()

    custody_pdf = PUBLIC_AM / "am_custody_and_voting_guide.pdf"
    with SourceBlob.from_path(custody_pdf) as blob:
        insp = engine.inspect(blob)
        assert insp.format_id == "pdf"
        assert insp.page_count == 6
        assert insp.renderable is True
        assert 4 in insp.scanned_pages
        assert 1 in insp.digital_pages

        ir = engine.parse(blob)
        assert len(ir.pages) == 6
        assert ir.provenance.ocr_pages_detected >= 1
        assert ir.pages[4].canonical_text.count("CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE") == 2
        assert validate_ir(ir) == []

        proj = engine.project(ir, target="document_map", doc_id="src_custody_pdf")
        assert isinstance(proj, dict)
        dmap = proj["document_map"]
        assert dmap.page_count == 6
        assert len(dmap.outline) == 6
        assert dmap.pages[2].table_headers == ["ballot_id", "issuer_cusip", "vote_choice"]
        assert dmap.pages[3].is_scanned_or_low_density is True
        assert dmap.pages[3].has_images is True

    scanned_pdf = SAMPLE_PDFS / "institutional_scanned_schedule_ocr_test.pdf"
    with SourceBlob.from_path(scanned_pdf) as blob:
        ir_scan = engine.parse(blob)
        assert len(ir_scan.pages) == 2
        assert ir_scan.pages[0].needs_ocr is False
        assert ir_scan.pages[1].needs_ocr is True
        assert ir_scan.pages[1].image_block_count >= 1

    f1120_pdf = PUBLIC_FIXTURES / "irs_f1120.pdf"
    with SourceBlob.from_path(f1120_pdf) as blob:
        ir_f1120 = engine.parse(blob, ParseOptions(pages=[1]))
        assert len(ir_f1120.pages) == 1
        assert len(ir_f1120.blocks) > 0
        assert validate_ir(ir_f1120) == []


def test_pdf_full_page_image_rotate_locked_corrupt_and_limits(tmp_path: Path) -> None:
    """Cover Trap #3 (full_page_image), /Rotate 90, locked/corrupt PDFs, bounds & timeouts."""
    engine = ParsingEngine()

    # 1. Full-page raster PDF sets image_block_count >= 1 and needs_ocr=True
    full_scan_pdf = tmp_path / "full_scan.pdf"
    _make_full_page_scan_pdf(full_scan_pdf)
    with SourceBlob.from_path(full_scan_pdf) as blob:
        ir_scan = engine.parse(blob)
        assert ir_scan.pages[0].image_block_count >= 1
        assert ir_scan.pages[0].needs_ocr is True

    # 2. /Rotate 90 auto-rotates viewport width/height and PNG dimensions
    rot_pdf = tmp_path / "rot90.pdf"
    _make_text_pdf(rot_pdf, ["Rotated Custody Header", "SSN 123-45-6789"], rotate=90)
    with SourceBlob.from_path(rot_pdf) as blob:
        ir_rot = engine.parse(blob)
        pg = ir_rot.pages[0]
        assert pg.width_pt is not None and pg.height_pt is not None
        assert pg.width_pt > pg.height_pt
        png_bytes, r_res = engine.render_page(blob, 1, ParseOptions(dpi=100))
        assert r_res.width > r_res.height
        with Image.open(io.BytesIO(png_bytes)) as img:
            assert img.size == (r_res.width, r_res.height)

    # 3. Password-locked & corrupt PDFs -> VERIFICATION_GATE_FAILED
    locked_pdf = tmp_path / "locked.pdf"
    _make_text_pdf(locked_pdf, ["Secret line"], password="secret")
    with SourceBlob.from_path(locked_pdf) as blob:
        with pytest.raises(OpenDVError) as exc_locked:
            engine.parse(blob)
        assert exc_locked.value.error_code == ErrorCode.VERIFICATION_GATE_FAILED

    with SourceBlob.from_path(SRC_MATRIX / "corrupt_document.pdf") as blob:
        with pytest.raises(OpenDVError) as exc_corrupt:
            engine.parse(blob)
        assert exc_corrupt.value.error_code == ErrorCode.VERIFICATION_GATE_FAILED

    # 4. Page windowing, unconstrained inspect, max_pages, dpi, pixel budget, and timeout
    fdic_pdf = SAMPLE_PDFS / "fdic_section3_2_loans_credit_risk.pdf"
    with SourceBlob.from_path(fdic_pdf) as blob:
        insp_fdic = engine.inspect(blob, ParseOptions(max_pages=2))
        assert insp_fdic.page_count == 88

        with pytest.raises(OpenDVError) as exc_max:
            engine.parse(blob, ParseOptions(max_pages=2))
        assert exc_max.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED

        ir_win = engine.parse(blob, ParseOptions(pages=[1, 3], max_pages=2))
        assert [p.page_number for p in ir_win.pages] == [1, 3]

        with pytest.raises(OpenDVError) as exc_oob:
            engine.parse(blob, ParseOptions(pages=[999]))
        assert exc_oob.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

        with pytest.raises(OpenDVError) as exc_dpi:
            engine.render_page(blob, 1, ParseOptions(dpi=301))
        assert exc_dpi.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

        with pytest.raises(OpenDVError) as exc_timeout:
            engine.parse(blob, ParseOptions(timeout_ms=1))
        assert exc_timeout.value.error_code == ErrorCode.INTERNAL_SERVER_ERROR

    # 5. Pixel budget preflight (> 40,000,000 px) -> ARCHIVE_SIZE_LIMIT_EXCEEDED
    huge_pdf = tmp_path / "huge.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=2500, height=2500)
    with huge_pdf.open("wb") as fh:
        writer.write(fh)
    with SourceBlob.from_path(huge_pdf) as blob:
        with pytest.raises(OpenDVError) as exc_px:
            engine.render_page(blob, 1, ParseOptions(dpi=300))
        assert exc_px.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED


def test_bounded_zip_reader_security_and_limits(tmp_path: Path) -> None:
    """Verify BoundedZipReader allows valid dirs and blocks bombs/traversal/XML hazards."""
    # Valid zip with info.is_dir() succeeds
    valid_zip = tmp_path / "valid_dirs.zip"
    with zipfile.ZipFile(valid_zip, "w") as zf:
        dir_info = zipfile.ZipInfo("word/")
        dir_info.external_attr = 0x10
        zf.writestr(dir_info, b"")
        zf.writestr("word/document.xml", b"<w:document/>")
    entries = BoundedZipReader().read_validated(valid_zip)
    assert entries == {"word/document.xml": b"<w:document/>"}

    # Entry count cap
    many_zip = tmp_path / "many.zip"
    with zipfile.ZipFile(many_zip, "w") as zf:
        for i in range(4):
            zf.writestr(f"f{i}.xml", b"<x/>")
    with pytest.raises(OpenDVError) as exc_cnt:
        BoundedZipReader(max_entries=3).read_validated(many_zip)
    assert exc_cnt.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED

    # Inflated byte cap
    big_zip = tmp_path / "big.zip"
    with zipfile.ZipFile(big_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", b"A" * 200_000)
    with pytest.raises(OpenDVError) as exc_size:
        BoundedZipReader(max_uncompressed_bytes=100_000).read_validated(big_zip)
    assert exc_size.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED

    # Ratio bomb
    bomb_zip = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", b"Z" * 1_200_000)
    with pytest.raises(OpenDVError) as exc_ratio:
        BoundedZipReader(max_ratio=10.0, ratio_min_entry_bytes=100_000).read_validated(bomb_zip)
    assert exc_ratio.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED

    # XML DOCTYPE / ENTITY -> ARCHIVE_SECURITY_VIOLATION
    for payload in (b"<!DOCTYPE foo><a/>", b"<!ENTITY xxe SYSTEM 'x'><a/>"):
        xml_zip = tmp_path / "xxe.zip"
        with zipfile.ZipFile(xml_zip, "w") as zf:
            zf.writestr("word/document.xml", payload)
        with pytest.raises(OpenDVError) as exc_xxe:
            BoundedZipReader().read_validated(xml_zip)
        assert exc_xxe.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

    # Traversal, ADS, reserved device, symlink, reparse, encrypted flag
    bad_names = ["../evil.xml", "/abs.xml", "C:drive.xml", "word/foo:ads.xml", "word/con.xml", "a\\b.xml"]
    for bad in bad_names:
        z_bad = tmp_path / "bad_member.zip"
        with zipfile.ZipFile(z_bad, "w") as zf:
            info = zipfile.ZipInfo("placeholder.xml")
            info.filename = bad
            zf.writestr(info, b"<x/>")
        with pytest.raises(OpenDVError) as exc_sec:
            BoundedZipReader().read_validated(z_bad)
        assert exc_sec.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

    # Symlink / reparse point / encrypted flag / corrupt zip
    sym_zip = tmp_path / "sym.zip"
    with zipfile.ZipFile(sym_zip, "w") as zf:
        info = zipfile.ZipInfo("word/link.xml")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, b"target")
    with pytest.raises(OpenDVError) as exc_sym:
        BoundedZipReader().read_validated(sym_zip)
    assert exc_sym.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

    rep_zip = tmp_path / "reparse.zip"
    with zipfile.ZipFile(rep_zip, "w") as zf:
        info = zipfile.ZipInfo("word/reparse.xml")
        info.external_attr = 0x0400
        zf.writestr(info, b"target")
    with pytest.raises(OpenDVError) as exc_rep:
        BoundedZipReader().read_validated(rep_zip)
    assert exc_rep.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

    enc_info = zipfile.ZipInfo("word/doc.xml")
    enc_info.flag_bits = 0x1
    with pytest.raises(OpenDVError) as exc_enc:
        BoundedZipReader().validate_entry_info(enc_info)
    assert exc_enc.value.error_code == ErrorCode.VERIFICATION_GATE_FAILED

    corrupt_zip = tmp_path / "corrupt.docx"
    corrupt_zip.write_bytes(b"not-a-valid-zip-payload")
    with pytest.raises(OpenDVError) as exc_bad_zip:
        BoundedZipReader().read_validated(corrupt_zip)
    assert exc_bad_zip.value.error_code == ErrorCode.VERIFICATION_GATE_FAILED


def test_docx_parser_dom_merged_tables_ole2_and_mask_before_split(tmp_path: Path) -> None:
    """Verify DocxParser on OLE2, formatted SDT, merged gridSpan/vMerge, and 24k mask-before-split."""
    engine = ParsingEngine()

    # 1. OLE2 encrypted docx -> VERIFICATION_GATE_FAILED
    with SourceBlob.from_path(PUBLIC_FIXTURES / "aspose_encrypted_ole2.docx") as blob:
        with pytest.raises(OpenDVError) as exc_ole:
            engine.parse(blob)
        assert exc_ole.value.error_code == ErrorCode.VERIFICATION_GATE_FAILED

    # 2. Inline <w:sdt> + DOM order (real fixture + inline <w:p><w:sdt>)
    with SourceBlob.from_path(PUBLIC_FIXTURES / "aspose_formatted_elements.docx") as blob:
        ir_fmt = engine.parse(blob)
        assert len(ir_fmt.pages) >= 1
        assert len(ir_fmt.blocks) >= 5
        assert validate_ir(ir_fmt) == []

    sdt_docx = tmp_path / "inline_sdt.docx"
    d_sdt = docx.Document()
    p_sdt = d_sdt.add_paragraph("Hello ")
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    sdt = OxmlElement("w:sdt")
    sdt_c = OxmlElement("w:sdtContent")
    r_el = OxmlElement("w:r")
    t_el = OxmlElement("w:t")
    t_el.text = "SDT_VALUE"
    r_el.append(t_el)
    sdt_c.append(r_el)
    sdt.append(sdt_c)
    p_sdt._p.append(sdt)
    d_sdt.save(sdt_docx)
    with SourceBlob.from_path(sdt_docx) as blob:
        ir_sdt = engine.parse(blob)
        assert any("Hello SDT_VALUE" in b.text for b in ir_sdt.blocks)

    # 3. Rectangular merged cells across gridSpan and vMerge
    with SourceBlob.from_path(PUBLIC_FIXTURES / "aspose_merged_table.docx") as blob:
        ir_tbl = engine.parse(blob)
        table_blocks = [b for b in ir_tbl.blocks if b.kind == "table"]
        assert len(table_blocks) >= 1
        tb = table_blocks[0]
        assert tb.table_headers is not None
        assert tb.table_rows is not None
        expected_cols = len(tb.table_headers)
        assert expected_cols == 4
        assert all(len(r) == expected_cols for r in tb.table_rows)
        assert validate_ir(ir_tbl) == []

    # 4. Mask-before-24,000-char split on single oversized paragraph with PII straddling 24,000
    long_docx = tmp_path / "long_pii.docx"
    d = docx.Document()
    d.add_heading("Custody Agreement", level=1)
    prefix = "A" * 23_994
    suffix = "B" * 500
    d.add_paragraph(f"{prefix} 123-45-6789 steward.lead@fund-example.com {suffix}")
    d.save(long_docx)

    with SourceBlob.from_path(long_docx) as blob:
        ir_redacted = engine.parse(blob, sink_class="MODEL_EGRESS", redaction="on")
        assert ir_redacted.provenance.redacted is True
        assert len(ir_redacted.pages) >= 2
        assert all(len(p.canonical_text) <= 24_000 for p in ir_redacted.pages)
        joined = "\n".join(p.canonical_text for p in ir_redacted.pages)
        assert "123-45-6789" not in joined
        assert "steward.lead@fund-example.com" not in joined
        assert "<MASKED_PII>" in joined
        assert validate_ir(ir_redacted) == []

        with pytest.raises(OpenDVError) as exc_render:
            engine.render_page(blob, 1)
        assert exc_render.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


def test_markdown_and_text_parser_empty_crlf_utf8_and_mask_before_split(tmp_path: Path) -> None:
    """Verify MarkdownTextParser on 0-byte, invalid UTF-8, CRLF parity, and 24k mask-before-split."""
    engine = ParsingEngine()

    # 1. 0-byte .md and .txt -> 1 empty PageSlice
    for ext in (".md", ".txt"):
        empty_file = tmp_path / f"empty{ext}"
        empty_file.write_bytes(b"")
        with SourceBlob.from_path(empty_file) as blob:
            ir_empty = engine.parse(blob)
            assert len(ir_empty.pages) == 1
            assert ir_empty.pages[0].extracted_via == "empty"
            assert ir_empty.pages[0].renderable is False
            assert ir_empty.pages[0].canonical_text == ""
            proj = engine.project(ir_empty, target="document_map")
            assert isinstance(proj, dict)
            assert proj["document_map"].page_count == 1
            assert validate_ir(ir_empty) == []

    # 2. Invalid UTF-8 -> SYNTAX_INVALID_ARGUMENT
    bad_utf8 = tmp_path / "bad.md"
    bad_utf8.write_bytes(b"\xff\xfe\xfd")
    with SourceBlob.from_path(bad_utf8) as blob:
        with pytest.raises(OpenDVError) as exc_utf:
            engine.parse(blob)
        assert exc_utf.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # 3. LF vs CRLF SHA-256 parity
    with (
        SourceBlob.from_path(SRC_MATRIX / "enterprise_msa_addendum_2026.txt") as b_lf,
        SourceBlob.from_path(SRC_MATRIX / "enterprise_msa_addendum_2026_crlf.txt") as b_crlf,
    ):
        ir_lf = engine.parse(b_lf)
        ir_crlf = engine.parse(b_crlf)
        assert ir_lf.pages[0].canonical_text_sha256 == ir_crlf.pages[0].canonical_text_sha256

    # 4. Mask-before-24,000-char split with ABA routing straddling boundary
    long_txt = tmp_path / "long_note.txt"
    lead = "X" * 23_985
    long_txt.write_text(f"{lead} routing 021000021 end\nNext line", encoding="utf-8")
    with SourceBlob.from_path(long_txt) as blob:
        ir_masked = engine.parse(blob, sink_class="MODEL_EGRESS", redaction="on")
        assert len(ir_masked.pages) >= 2
        assert all(len(p.canonical_text) <= 24_000 for p in ir_masked.pages)
        all_txt = "\n".join(p.canonical_text for p in ir_masked.pages)
        assert "021000021" not in all_txt
        assert "<MASKED_PII>" in all_txt
        assert validate_ir(ir_masked) == []


def test_visual_redaction_word_box_alignment_and_ocr_gating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify multi-token PII blackout, fail-closed miss, OCR provider, and DRIVER_MISSING."""
    engine = ParsingEngine()

    # 1. Native PDF with SSN, multi-word phone, Luhn CC PAN, ABA routing, email
    pii_pdf = tmp_path / "pii_native.pdf"
    _make_text_pdf(
        pii_pdf,
        [
            "SSN 123-45-6789",
            "Phone (555) 123-4567",
            "Card 4532 0151 1283 0366",
            "ABA routing 021000021",
            "Mail steward.lead@fund-example.com;",
        ],
    )
    with SourceBlob.from_path(pii_pdf) as blob:
        png_bytes, r_res = engine.render_page(
            blob, 1, ParseOptions(dpi=150), sink_class="MODEL_EGRESS", redaction="on"
        )
        assert r_res.redacted is True
        assert r_res.ocr_boxes_applied >= 5
        with Image.open(io.BytesIO(png_bytes)) as img:
            rgba = img.convert("RGBA")
            # At least one pure black pixel (0, 0, 0, 255) exists from blackout
            colors = rgba.getcolors(maxcolors=rgba.width * rgba.height) or []
            black_pixels = sum(cnt for cnt, px in colors if px == (0, 0, 0, 255))
            assert black_pixels > 50

    # 2. Fail-closed align_pii_spans_to_boxes
    with pytest.raises(OpenDVError) as exc_empty_boxes:
        align_pii_spans_to_boxes("SSN 123-45-6789", [])
    assert exc_empty_boxes.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    dummy_box = NormalizedWordBox(
        bbox=NormalizedBBox(x0=0.1, y0=0.1, x1=0.2, y1=0.2), text="harmless"
    )
    with pytest.raises(OpenDVError) as exc_miss:
        align_pii_spans_to_boxes("SSN 123-45-6789", [dummy_box])
    assert exc_miss.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    # 3. Scanned PDF gating: LOCAL_HUMAN_IO vs approve_scan vs MODEL_EGRESS vs DRIVER_MISSING
    scanned_pdf = SAMPLE_PDFS / "institutional_scanned_schedule_ocr_test.pdf"
    with SourceBlob.from_path(scanned_pdf) as blob:
        with pytest.raises(OpenDVError) as exc_unapproved:
            engine.render_page(blob, 2, redaction="off", approve_scan=False)
        assert exc_unapproved.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

        _, res_ok = engine.render_page(blob, 2, redaction="off", approve_scan=True)
        assert res_ok.redacted is False

        # approve_scan=True NEVER bypasses MODEL_EGRESS redaction
        with pytest.raises(OpenDVError) as exc_egress:
            engine.render_page(blob, 2, redaction="on", approve_scan=True)
        assert exc_egress.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

        # opts.ocr=True without [ocr] -> DRIVER_MISSING (agent uv sync --extra ocr)
        monkeypatch.setattr("importlib.util.find_spec", lambda name: None if name == "pytesseract" else object())
        with pytest.raises(OpenDVError) as exc_drv_pkg:
            engine.render_page(blob, 2, ParseOptions(ocr=True), redaction="on")
        assert exc_drv_pkg.value.error_code == ErrorCode.DRIVER_MISSING
        assert exc_drv_pkg.value.remediation is not None
        assert any(a.command == "uv sync --extra ocr" for a in exc_drv_pkg.value.remediation.actions)

        # pytesseract present without tesseract binary -> DRIVER_MISSING (actor="human")
        monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
        monkeypatch.setattr("shutil.which", lambda _cmd: None)
        with pytest.raises(OpenDVError) as exc_drv_bin:
            engine.render_page(blob, 2, ParseOptions(ocr=True), redaction="on")
        assert exc_drv_bin.value.error_code == ErrorCode.DRIVER_MISSING
        assert exc_drv_bin.value.remediation is not None
        assert any(a.actor == "human" for a in exc_drv_bin.value.remediation.actions)

        # Injected OcrProviderFn succeeds and blacks out OCR box
        def _fake_ocr(_png: bytes, _page: int) -> tuple[list[OcrBox], str]:
            return [OcrBox(20, 30, 140, 60, "123-45-6789")], "Borrower 123-45-6789"

        red_png, res_ocr = engine.render_page(
            blob, 2, redaction="on", approve_scan=True, ocr_provider=_fake_ocr
        )
        assert res_ocr.redacted is True
        assert res_ocr.ocr_used is True
        assert res_ocr.ocr_boxes_applied == 1
        with Image.open(io.BytesIO(red_png)) as img:
            assert img.convert("RGBA").getpixel((50, 45)) == (0, 0, 0, 255)

    # 4. Mocked pytesseract.image_to_data + redact_png_boxes pixel check
    fake_tess = types.SimpleNamespace(
        Output=types.SimpleNamespace(DICT="dict"),
        image_to_data=lambda _img, output_type=None: {
            "text": ["Borrower", "123-45-6789"],
            "conf": ["95", "91"],
            "left": [10, 50],
            "top": [10, 10],
            "width": [40, 80],
            "height": [12, 12],
        },
    )
    base_img = Image.new("RGBA", (200, 100), color=(255, 255, 255, 255))
    base_buf = io.BytesIO()
    base_img.save(base_buf, format="PNG")
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: object())
    monkeypatch.setattr("shutil.which", lambda _cmd: "tesseract")
    monkeypatch.setitem(sys.modules, "pytesseract", fake_tess)
    boxes, ocr_txt = run_pytesseract_ocr(base_buf.getvalue())
    assert len(boxes) == 2
    assert ocr_txt == "Borrower 123-45-6789"
    out_img_bytes = redact_png_boxes(base_buf.getvalue(), [boxes[1]], padding_px=2)
    with Image.open(io.BytesIO(out_img_bytes)) as img:
        assert img.convert("RGBA").getpixel((60, 15)) == (0, 0, 0, 255)
        assert img.convert("RGBA").getpixel((5, 5)) == (255, 255, 255, 255)


def test_validate_projectors_duckdb_and_custom_spi(tmp_path: Path) -> None:
    """Verify validate_ir (no placeholder leak), Markdown/Chunks projectors, DuckDB, and custom SPI."""
    # 1. validate_ir allows <MASKED_PII> and [REDACTED_ text, and flags tampered digest/bounds
    sample_md = tmp_path / "sample.md"
    sample_md.write_text(
        "# Section One\n\nContact alice@example.com for details.\n\n"
        "| col_a | col_b |\n| --- | --- |\n| v1 | alice@example.com |\n",
        encoding="utf-8",
    )
    engine = ParsingEngine()
    with SourceBlob.from_path(sample_md) as blob:
        ir_raw = engine.parse(blob)
        ir_red = engine.parse(blob, sink_class="MODEL_EGRESS", redaction="on")
        assert engine.validate(ir_raw) == []
        assert engine.validate(ir_red) == []

        # Explicitly confirm validate_ir does NOT reject [REDACTED_ placeholders
        red_placeholder_page = ir_red.pages[0].model_copy(
            update={
                "canonical_text": "[REDACTED_PII] note",
                "canonical_text_sha256": compute_canonical_text_sha256("[REDACTED_PII] note"),
                "raw_text_length": len("[REDACTED_PII] note"),
            }
        )
        ir_with_placeholder = ir_red.model_copy(update={"pages": [red_placeholder_page], "blocks": [], "outline": []})
        assert engine.validate(ir_with_placeholder) == []

        # Tamper SHA-256 -> CANONICAL_HASH_MISMATCH
        bad_page = ir_raw.pages[0].model_copy(update={"canonical_text_sha256": "0" * 64})
        diags = engine.validate(ir_raw.model_copy(update={"pages": [bad_page]}))
        assert any(d.code == "CANONICAL_HASH_MISMATCH" for d in diags)

        # DocumentMapProjector preserves raw SHA-256 while masking view/title/headers
        dmap_res = engine.project(ir_raw, target="document_map", redaction="on")
        assert isinstance(dmap_res, dict)
        assert dmap_res["document_map"].pages[0].canonical_text_sha256 == ir_raw.pages[0].canonical_text_sha256
        assert "alice@example.com" not in dmap_res["page_text_views"][0].masked_text
        assert "<MASKED_PII>" in dmap_res["page_text_views"][0].masked_text

        # MarkdownProjector masks PII on redaction="on"
        md_out = engine.project(ir_raw, target="markdown", redaction="on")
        assert isinstance(md_out, str)
        assert "# Section One" in md_out
        assert "alice@example.com" not in md_out
        assert "<MASKED_PII>" in md_out

        # ChunksProjector uniform-key JSONL -> DuckDB read_ndjson
        chunks_jsonl = "".join(engine.stream_jsonl(ir_raw, target="chunks", redaction="on"))
        lines = [json.loads(ln) for ln in chunks_jsonl.strip().splitlines()]
        assert len(lines) >= 3
        first_keys = set(lines[0].keys())
        assert len(first_keys) == 16
        assert all(set(item.keys()) == first_keys for item in lines)
        assert all(len(item["text"]) <= 4_000 for item in lines)

        ndjson_path = tmp_path / "chunks.jsonl"
        ndjson_path.write_text(chunks_jsonl, encoding="utf-8")
        con = duckdb.connect(":memory:")
        try:
            rows = con.execute(
                f"SELECT block_kind, heading_path, redacted FROM read_ndjson('{ndjson_path.as_posix()}')"
            ).fetchall()
            assert len(rows) == len(lines)
            assert all(r[2] is True for r in rows)
        finally:
            con.close()

    # 2. Custom FormatParser + IRProjector registration + parse_page_range_spec + capabilities
    class _CustomParser:
        parser_id = "custom_note"
        parser_version = "0.1"
        supports_render = False

        def inspect(self, blob: SourceBlob, opts: ParseOptions) -> InspectResult:
            _ = opts
            return InspectResult(
                source_uri_sanitized=blob.uri_sanitized,
                binary_sha256=blob.binary_sha256,
                byte_size=blob.byte_size,
                format_id="domain_note",
                page_count=1,
                scanned_pages=[],
                digital_pages=[1],
                renderable=False,
            )

        def parse(self, blob: SourceBlob, opts: ParseOptions) -> ParsedDocumentIR:
            _ = opts
            txt = "Email test@example.com"
            return ParsedDocumentIR(
                provenance=ParseProvenance(
                    parser_id=self.parser_id,
                    parser_version=self.parser_version,
                    source_uri_sanitized=blob.uri_sanitized,
                    binary_sha256=blob.binary_sha256,
                    format_id="domain_note",
                    page_count=1,
                ),
                pages=[
                    PageSlice(
                        page_number=1,
                        slice_kind="virtual_section_page",
                        renderable=False,
                        canonical_text=txt,
                        canonical_text_sha256=compute_canonical_text_sha256(txt),
                        raw_text_length=len(txt),
                        non_ws_chars=21,
                        image_block_count=0,
                        needs_ocr=False,
                        extracted_via="native_text",
                    )
                ],
                outline=[DocumentOutlineNode(node_id="n0", title="test@example.com", level=1, start_page=1, end_page=1)],
                blocks=[
                    DocumentBlock(
                        block_id="b0",
                        kind="paragraph",
                        reading_order=0,
                        spans=[PageSpan(page_number=1, char_start=0, char_end=len(txt))],
                        text=txt,
                    )
                ],
            )

        def render_page(self, blob: SourceBlob, page_number: int, opts: ParseOptions) -> RenderedPageBuffer:
            _ = blob, page_number, opts
            raise OpenDVError("No render", error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT)

    custom_reg = ParsingRegistry()
    custom_reg.register_parser("domain_note", _CustomParser())
    assert isinstance(custom_reg.get_parser("domain_note"), FormatParser)
    assert isinstance(DocumentMapProjector(), IRProjector)
    assert isinstance(MarkdownProjector(), IRProjector)
    assert isinstance(ChunksProjector(), IRProjector)

    custom_engine = ParsingEngine(registry=custom_reg)
    note_path = tmp_path / "custom.txt"
    note_path.write_text("Email test@example.com", encoding="utf-8")
    with SourceBlob.from_path(note_path) as blob:
        red_custom = custom_engine.parse(blob, sink_class="MODEL_EGRESS", redaction="on")
        assert red_custom.provenance.redacted is True
        assert red_custom.pages[0].canonical_text == "Email <MASKED_PII>"
        assert red_custom.outline[0].title == "<MASKED_PII>"
        assert custom_engine.validate(red_custom) == []

    # Registry error paths
    with pytest.raises(OpenDVError) as exc_fmt:
        custom_reg.get_parser("pdf")
    assert exc_fmt.value.error_code == ErrorCode.UNSUPPORTED_MEDIA_TYPE

    with pytest.raises(OpenDVError) as exc_proj:
        default_registry().get_projector("unknown")  # type: ignore[arg-type]
    assert exc_proj.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Page range parsing + capabilities
    assert parse_page_range_spec("1-3, 5, 2-4") == [1, 2, 3, 4, 5]
    for bad_spec in ("", "0", "3-1", "1,,2", "abc"):
        with pytest.raises(OpenDVError) as exc_rng:
            parse_page_range_spec(bad_spec)
        assert exc_rng.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    caps = capabilities_snapshot()
    assert caps.liteparse_version is not None
    assert caps.docx_available is True
    assert caps.pillow_version is not None


def test_docx_merged_empty_cells_and_trailing_paragraphs_span_parity(tmp_path: Path) -> None:
    """Verify DocxParser PageSpan offsets match canonical_text on empty/merged cells."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    merged_docx = tmp_path / "merged_empty.docx"
    doc = docx.Document()
    doc.add_heading("Custody Table Section", level=1)
    tbl = doc.add_table(rows=3, cols=3)
    tbl.cell(0, 0).text = ""
    tbl.cell(0, 1).text = "Account"
    tbl.cell(0, 2).text = "Owner"
    tbl.cell(1, 0).text = "Row1"
    tbl.cell(1, 1).text = "123-45-6789"
    tbl.cell(1, 2).text = ""
    tbl.cell(2, 0).text = "Row2"
    tbl.cell(2, 1).text = ""
    tbl.cell(2, 2).text = "alice@example.com"

    # Add gridSpan=2 on row 1 col 1, vMerge restart/continuation on col 0, and gridBefore on row 2
    row0_pr = OxmlElement("w:trPr")
    grid_before = OxmlElement("w:gridBefore")
    grid_before.set(qn("w:val"), "1")
    row0_pr.append(grid_before)
    tbl.rows[2]._tr.insert(0, row0_pr)

    tc_span_pr = tbl.rows[1].cells[1]._tc.get_or_add_tcPr()
    gspan = OxmlElement("w:gridSpan")
    gspan.set(qn("w:val"), "2")
    tc_span_pr.append(gspan)

    vm_start = tbl.rows[1].cells[0]._tc.get_or_add_tcPr()
    vm1 = OxmlElement("w:vMerge")
    vm1.set(qn("w:val"), "restart")
    vm_start.append(vm1)
    vm_cont = tbl.rows[2].cells[0]._tc.get_or_add_tcPr()
    vm_cont.append(OxmlElement("w:vMerge"))

    doc.add_paragraph("Trailing paragraph after merged table with steward@example.org.")
    doc.save(merged_docx)

    engine = ParsingEngine()
    with SourceBlob.from_path(merged_docx) as blob:
        for mode in ("off", "on"):
            red_mode: RedactionMode = mode  # type: ignore[assignment]
            ir = engine.parse(blob, redaction=red_mode)
            assert validate_ir(ir) == []
            assert len(ir.pages) == 1
            page_text = ir.pages[0].canonical_text
            assert len(ir.blocks) == 3
            for blk in ir.blocks:
                assert len(blk.spans) == 1
                sp = blk.spans[0]
                assert sp.char_start is not None and sp.char_end is not None
                assert page_text[sp.char_start : sp.char_end] == blk.text


def test_parsing_engine_model_egress_enforcement(tmp_path: Path) -> None:
    """Verify MODEL_EGRESS enforces redaction='on' by default and rejects redaction='off'."""
    engine = ParsingEngine()

    note = tmp_path / "egress_note.txt"
    note.write_text("Contact SSN 123-45-6789 immediately.", encoding="utf-8")
    with SourceBlob.from_path(note) as blob:
        ir_egress = engine.parse(blob, sink_class="MODEL_EGRESS")
        assert ir_egress.provenance.sink_class == "MODEL_EGRESS"
        assert ir_egress.provenance.redacted is True
        assert "123-45-6789" not in ir_egress.pages[0].canonical_text
        assert "<MASKED_PII>" in ir_egress.pages[0].canonical_text

        ir_local_redact = engine.parse(blob, sink_class="LOCAL_HUMAN_IO", redaction="on")
        assert ir_local_redact.provenance.sink_class == "LOCAL_HUMAN_IO"
        assert ir_local_redact.provenance.redacted is True
        assert "<MASKED_PII>" in ir_local_redact.pages[0].canonical_text

        with pytest.raises(OpenDVError) as exc_parse:
            engine.parse(blob, sink_class="MODEL_EGRESS", redaction="off")
        assert exc_parse.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    pii_pdf = tmp_path / "egress_pii.pdf"
    _make_text_pdf(pii_pdf, ["SSN 123-45-6789", "Safe line"])
    with SourceBlob.from_path(pii_pdf) as blob:
        png_bytes, r_res = engine.render_page(
            blob, 1, ParseOptions(dpi=150), sink_class="MODEL_EGRESS"
        )
        assert r_res.redacted is True
        assert r_res.ocr_boxes_applied >= 1
        with Image.open(io.BytesIO(png_bytes)) as img:
            colors = img.convert("RGBA").getcolors(maxcolors=img.width * img.height) or []
            assert sum(cnt for cnt, px in colors if px == (0, 0, 0, 255)) > 20

        _, r_local = engine.render_page(
            blob, 1, ParseOptions(dpi=150), sink_class="LOCAL_HUMAN_IO", redaction="on"
        )
        assert r_local.redacted is True

        with pytest.raises(OpenDVError) as exc_render:
            engine.render_page(blob, 1, sink_class="MODEL_EGRESS", redaction="off")
        assert exc_render.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    scan_pdf = tmp_path / "egress_scan.pdf"
    _make_full_page_scan_pdf(scan_pdf)
    with SourceBlob.from_path(scan_pdf) as blob:
        with pytest.raises(OpenDVError) as exc_scan:
            engine.render_page(blob, 1, sink_class="MODEL_EGRESS", approve_scan=True)
        assert exc_scan.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED


def test_bounded_zip_reader_rejects_utf16_xml_prolog_hazards(tmp_path: Path) -> None:
    """Verify BoundedZipReader blocks UTF-16 BOM and BOM-less DOCTYPE/ENTITY declarations."""
    payloads = [
        ("word/document.xml", '<?xml version="1.0"?><!DOCTYPE root><a/>'.encode("utf-16")),
        ("word/document.xml", "<!DOCTYPE root><a/>".encode("utf-16-le")),
        ("word/document.xml", "<!ENTITY xxe SYSTEM 'file:///x'><a/>".encode("utf-16-be")),
        ("word/_rels/document.xml.rels", "<!doctype r><x/>".encode("utf-16-le")),
        ("word/document.xml", "<!entity e 'v'><a/>".encode("utf-16-be")),
    ]
    for idx, (member, raw_bytes) in enumerate(payloads):
        z_path = tmp_path / f"utf16_xxe_{idx}.docx"
        with zipfile.ZipFile(z_path, "w") as zf:
            zf.writestr(member, raw_bytes)
        with pytest.raises(OpenDVError) as exc_sec:
            BoundedZipReader().read_validated(z_path)
        assert exc_sec.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION


def test_split_oversized_text_preserves_masked_pii_across_boundaries(tmp_path: Path) -> None:
    """Verify unbroken PII straddling 4k/24k limits never fragments <MASKED_PII> tokens."""
    from opendv.models.pii_tokenizer import MASKED_PII_TOKEN
    from opendv.parsing.parsers._helpers import split_oversized_text

    def _assert_no_masked_fragments(texts: list[str], *, expect_token: bool = True) -> None:
        if expect_token:
            assert any(MASKED_PII_TOKEN in t for t in texts)
        for text in texts:
            scrubbed = text.replace(MASKED_PII_TOKEN, "")
            assert "<MASK" not in scrubbed
            assert "PII>" not in scrubbed

    # 1. Direct split_oversized_text across straddling offsets and token_start == pos guard
    for offset in (3_989, 3_995, 3_999):
        raw_4k = ("a" * offset) + MASKED_PII_TOKEN + ("b" * 50)
        pieces_4k = split_oversized_text(raw_4k, 4_000)
        assert "".join(pieces_4k) == raw_4k
        assert all(len(p) <= 4_000 for p in pieces_4k)
        _assert_no_masked_fragments(pieces_4k)

    small_limit_pieces = split_oversized_text(f"{MASKED_PII_TOKEN}tail", limit=5)
    assert small_limit_pieces == [MASKED_PII_TOKEN, "tail"]

    engine = ParsingEngine()

    # 2. ChunksProjector (4,000-char chunk boundary)
    unbroken_4k = tmp_path / "unbroken_4k.txt"
    unbroken_4k.write_text(("a" * 3_993) + ",123-45-6789," + ("b" * 100), encoding="utf-8")
    with SourceBlob.from_path(unbroken_4k) as blob:
        ir_4k = engine.parse(blob, sink_class="MODEL_EGRESS")
        assert validate_ir(ir_4k) == []
        chunks = ChunksProjector().project(ir_4k, redaction="on")
        assert len(chunks) >= 2
        assert all(len(c.text) <= 4_000 for c in chunks)
        chunk_texts = [c.text for c in chunks]
        assert all("123-45-6789" not in t for t in chunk_texts)
        _assert_no_masked_fragments(chunk_texts)

    # 3. MarkdownTextParser (24,000-char virtual slice boundary)
    unbroken_md = tmp_path / "unbroken_24k.md"
    unbroken_md.write_text(("a" * 23_993) + ",123-45-6789," + ("b" * 500), encoding="utf-8")
    with SourceBlob.from_path(unbroken_md) as blob:
        ir_md = engine.parse(blob, sink_class="MODEL_EGRESS", redaction="on")
        assert validate_ir(ir_md) == []
        assert len(ir_md.pages) >= 2
        assert all(len(p.canonical_text) <= 24_000 for p in ir_md.pages)
        md_pages = [p.canonical_text for p in ir_md.pages]
        assert all("123-45-6789" not in t for t in md_pages)
        _assert_no_masked_fragments(md_pages)

    # 4. DocxParser (24,000-char virtual slice boundary)
    unbroken_docx = tmp_path / "unbroken_24k.docx"
    doc = docx.Document()
    doc.add_paragraph(("x" * 23_993) + ",123-45-6789," + ("y" * 500))
    doc.save(unbroken_docx)
    with SourceBlob.from_path(unbroken_docx) as blob:
        ir_docx = engine.parse(blob, sink_class="MODEL_EGRESS", redaction="on")
        assert validate_ir(ir_docx) == []
        assert len(ir_docx.pages) >= 2
        assert all(len(p.canonical_text) <= 24_000 for p in ir_docx.pages)
        docx_pages = [p.canonical_text for p in ir_docx.pages]
        assert all("123-45-6789" not in t for t in docx_pages)
        _assert_no_masked_fragments(docx_pages)
