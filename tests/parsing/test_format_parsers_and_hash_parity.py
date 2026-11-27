"""Critical-path tests for format parsers (.pdf/.docx/.md/.txt), BoundedZipReader, and hash parity."""

from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path
from typing import Any

import docx
import pytest
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from opendv.models.document_ir import (
    ParseOptions,
    SourceBlob,
    is_page_scanned_or_low_density,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.pii_tokenizer import MASKED_PII_TOKEN
from opendv.parsing import (
    BoundedZipReader,
    MarkdownTextParser,
    ParsingEngine,
    validate_ir,
)
from opendv.parsing.parsers._zip_guard import (
    DOCX_MAX_ARCHIVE_ENTRIES,
    DOCX_MAX_INFLATED_BYTES,
    DOCX_MAX_INFLATION_RATIO,
    DOCX_RATIO_MIN_ENTRY_BYTES,
)
from opendv.parsing.parsers.liteparse_pdf import MAX_RENDER_DPI, MAX_RENDER_PIXELS

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_AM = REPO_ROOT / "data" / "public_am"
SAMPLE_PDFS = REPO_ROOT / "examples" / "sample_pdfs"
SRC_MATRIX = REPO_ROOT / "examples" / "scenarios" / "src_matrix"
PUBLIC_FIXTURES = REPO_ROOT / "tests" / "parsing" / "fixtures_public"

# Pinned cross-OS canonical_text_sha256 digests over embedded-font PDF and text/markdown fixtures.
PINNED_CANONICAL_SHA256: dict[Path, list[str]] = {
    PUBLIC_AM / "am_custody_and_voting_guide.pdf": [
        "04e6ea1ef560dbf418656e3467ac1b193e219bcc5d02af785629fb4080a18599",
        "7270388cfed226437a680f462f5bd4232c5dc6e016d453a86ed19423e3074a26",
        "eb4c8141c70909fe630fd8eb82cf3c2266e3a933f29568a86124d8ec0de69268",
        "64aacc0fc72985d8892f01ac4c2d818163015f81ebd86b717e288b72af3b508d",
        "0201d151dfaccdf3ef7a54a48b2ac649fa486691f0ebaae42d7a90a36435a52b",
        "aeb178d5c6b74bd5d9b8c8eeac8266c3ff30853fcd274f45e73bdbbf919fd70b",
    ],
    PUBLIC_AM / "am_stewardship_domain_notes.md": [
        "ca5f096a54bb4f8cb3504fefc02ca05cee9c374aaaeb4dd47a17217a9de4dbb6",
    ],
    SRC_MATRIX / "enterprise_msa_addendum_2026.txt": [
        "d4f558b9fd9f52be999308e2be03a5ed82a42e65c97590dfd523c32f798e21e0",
    ],
    SRC_MATRIX / "enterprise_msa_addendum_2026_crlf.txt": [
        "d4f558b9fd9f52be999308e2be03a5ed82a42e65c97590dfd523c32f798e21e0",
    ],
    PUBLIC_FIXTURES / "fed_h15_interest_rates.pdf": [
        "b098590e4e486b3a5a43e24fc7dc010cd755c1a4d8d63cc59826beefbfa7c2ab",
        "bb6da5d2bc63c057c899e862c6302eab16600f9de8c69100d69473cde1acd93e",
        "63542249384ce58c6acd49be1f1752bdd017f3abc5cd9e6bdf5a13bfdf763e36",
    ],
    PUBLIC_FIXTURES / "irs_f1120.pdf": [
        "eb70b364c136f8eb44ffaa2c0b62f0cd49e5619c0cdf2ae0a67535826f1cacf1",
        "5c17c8b80ff92143ce8f53107beed09c89a97f4af3b62e1d6596a4b4ae91915c",
        "0d24a24aaba46cfe6fa61290f92fbe8bba10891fc7599a3864e83f29c10c6240",
        "7675bf71ee3705d16b2967a07e1a7482c0dd9adebeaa525d873810ee65a85d96",
        "cdefca2e5c9652706ba7f43fd848b4848c9d9d724b07aa1072edece66233d766",
        "934b017627edff271794eade265f1fe42d475c29ca5ce28b2f63aed619ecda95",
    ],
}


def _make_pdf(
    path: Path,
    lines: list[str],
    *,
    width: float = 612,
    height: float = 792,
    rotate: int = 0,
    password: str | None = None,
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
    ops = ["BT", "/F1 12 Tf", "72 720 Td", "16 TL"]
    for idx, line in enumerate(lines):
        esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"({esc}) Tj" if idx == 0 else f"T* ({esc}) Tj")
    ops.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(ops).encode("latin-1"))
    page[NameObject("/Contents")] = ArrayObject([writer._add_object(stream)])
    if rotate:
        page[NameObject("/Rotate")] = NumberObject(rotate)
    if password:
        writer.encrypt(password)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        writer.write(fh)
    return path


def test_all_four_formats_and_pinned_cross_os_hash_parity(tmp_path: Path) -> None:
    """Verify all 4 formats (.pdf/.docx/.md/.txt) and pinned cross-OS SHA-256 digests."""
    engine = ParsingEngine()

    # 1. Pinned cross-OS canonical_text_sha256 parity across embedded-font PDF & text fixtures
    for fixture_path, expected_hashes in PINNED_CANONICAL_SHA256.items():
        with SourceBlob.from_path(fixture_path) as blob:
            ir = engine.parse(blob)
            assert [p.canonical_text_sha256 for p in ir.pages] == expected_hashes, fixture_path.name
            assert validate_ir(ir) == []

    # 2. All 4 formats parsed cleanly with exact format_id, slice_kind, and renderable flags
    docx_sample = tmp_path / "four_fmt.docx"
    d = docx.Document()
    d.add_heading("Summary", level=1)
    d.add_paragraph("All four formats active.")
    d.save(docx_sample)

    format_samples = [
        (PUBLIC_AM / "am_custody_and_voting_guide.pdf", "pdf", "physical_page", True),
        (docx_sample, "docx", "virtual_section_page", False),
        (PUBLIC_AM / "am_stewardship_domain_notes.md", "markdown", "virtual_section_page", False),
        (SRC_MATRIX / "enterprise_msa_addendum_2026.txt", "domain_note", "virtual_section_page", False),
    ]
    for path, exp_fmt, exp_slice_kind, exp_renderable in format_samples:
        with SourceBlob.from_path(path) as blob:
            insp = engine.inspect(blob)
            ir = engine.parse(blob)
            assert insp.format_id == exp_fmt
            assert insp.renderable is exp_renderable
            assert ir.provenance.format_id == exp_fmt
            assert all(p.slice_kind == exp_slice_kind for p in ir.pages)
            assert all(p.renderable is exp_renderable for p in ir.pages)
            assert validate_ir(ir) == []


def test_text_and_markdown_empty_bom_line1_and_24k_mask_before_split(tmp_path: Path) -> None:
    """Verify 0-byte, BOM-only, BOM-prefixed line-1 Heading/table, rfc9110 BOM, and 24k masking."""
    engine = ParsingEngine()

    # 1. 0-byte and BOM-only .md/.txt emit 1 empty PageSlice
    for name, raw_bytes in (
        ("empty.md", b""),
        ("empty.txt", b""),
        ("bom_only.md", b"\xef\xbb\xbf"),
        ("bom_only.txt", b"\xef\xbb\xbf"),
    ):
        p = tmp_path / name
        p.write_bytes(raw_bytes)
        with SourceBlob.from_path(p) as blob:
            insp = engine.inspect(blob)
            ir = engine.parse(blob)
            assert insp.page_count == 1
            assert len(ir.pages) == 1
            pg = ir.pages[0]
            assert pg.extracted_via == "empty"
            assert pg.canonical_text == ""
            assert pg.raw_text_length == 0
            assert pg.non_ws_chars == 0
            assert pg.renderable is False
            dmap_proj = engine.project(ir, target="document_map")
            assert isinstance(dmap_proj, dict)
            assert dmap_proj["document_map"].page_count == 1
            assert validate_ir(ir) == []

    # 2. BOM-prefixed Markdown with # Heading on line 1
    bom_heading = tmp_path / "bom_heading.md"
    bom_heading.write_bytes(b"\xef\xbb\xbf# Custody Heading\n\nBody text line.\n")
    with SourceBlob.from_path(bom_heading) as blob:
        ir_hd = engine.parse(blob)
        assert not ir_hd.pages[0].canonical_text.startswith("\ufeff")
        assert ir_hd.blocks[0].kind == "heading"
        assert ir_hd.blocks[0].heading_level == 1
        assert ir_hd.blocks[0].text == "Custody Heading"
        assert ir_hd.outline and ir_hd.outline[0].title == "Custody Heading"
        assert validate_ir(ir_hd) == []

    # 3. BOM-prefixed pipe table on line 1 (including ABA routing masking under MODEL_EGRESS)
    bom_table = tmp_path / "bom_table.md"
    bom_table.write_bytes(
        b"\xef\xbb\xbf| account | aba routing |\n| --- | --- |\n| acct_1 | 021000021 |\n"
    )
    with SourceBlob.from_path(bom_table) as blob:
        ir_tbl_raw = engine.parse(blob)
        assert not ir_tbl_raw.pages[0].canonical_text.startswith("\ufeff")
        assert ir_tbl_raw.blocks[0].kind == "table"
        assert ir_tbl_raw.blocks[0].table_headers == ["account", "aba routing"]

        ir_tbl_red = engine.parse(blob, sink_class="MODEL_EGRESS", redaction="on")
        assert "021000021" not in ir_tbl_red.pages[0].canonical_text
        assert MASKED_PII_TOKEN in ir_tbl_red.pages[0].canonical_text
        assert ir_tbl_red.blocks[0].table_rows == [["acct_1", MASKED_PII_TOKEN]]
        assert validate_ir(ir_tbl_red) == []

    # 4. Real BOM-prefixed public corpus document: rfc9110_http_semantics.txt
    with SourceBlob.from_path(PUBLIC_FIXTURES / "rfc9110_http_semantics.txt") as blob:
        ir_rfc = engine.parse(blob)
        assert len(ir_rfc.pages) >= 15
        assert not ir_rfc.pages[0].canonical_text.startswith("\ufeff")
        assert "\ufeff" not in ir_rfc.pages[0].canonical_text
        assert all(len(p.canonical_text) <= 24_000 for p in ir_rfc.pages)
        assert validate_ir(ir_rfc) == []

    # 5. Mask-before-24,000-char split on text/markdown
    split_md = tmp_path / "split_pii.md"
    split_md.write_text(("A" * 23_994) + " 123-45-6789 " + ("B" * 400), encoding="utf-8")
    with SourceBlob.from_path(split_md) as blob:
        ir_split = engine.parse(blob, sink_class="MODEL_EGRESS", redaction="on")
        assert len(ir_split.pages) >= 2
        assert all(len(p.canonical_text) <= 24_000 for p in ir_split.pages)
        joined = "\n".join(p.canonical_text for p in ir_split.pages)
        assert "123-45-6789" not in joined
        assert MASKED_PII_TOKEN in joined
        for page in ir_split.pages:
            scrubbed = page.canonical_text.replace(MASKED_PII_TOKEN, "")
            assert "<MASK" not in scrubbed and "PII>" not in scrubbed
        assert validate_ir(ir_split) == []


def test_pdf_rotate_90_scanned_predicate_encrypted_corrupt_oob_and_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify /Rotate 90 bounds, scanned density predicate, locked/corrupt PDF, OOB, and caps."""
    assert MAX_RENDER_DPI == 300
    assert MAX_RENDER_PIXELS == 40_000_000

    # 1. Authoritative scanned-page predicate boundaries
    assert is_page_scanned_or_low_density(49, 1) is True
    assert is_page_scanned_or_low_density(50, 1) is False
    assert is_page_scanned_or_low_density(14, 0) is True
    assert is_page_scanned_or_low_density(15, 0) is False
    assert is_page_scanned_or_low_density(0, 0) is True
    assert is_page_scanned_or_low_density(20, 1) is True

    engine = ParsingEngine()

    # 2. /Rotate 90 synthetic + real Fed G19 rotated PDF
    rot_pdf = _make_pdf(tmp_path / "rot90.pdf", ["Rotated Custody Schedule"], rotate=90)
    with SourceBlob.from_path(rot_pdf) as blob:
        ir_rot = engine.parse(blob)
        pg = ir_rot.pages[0]
        assert pg.width_pt is not None and pg.height_pt is not None
        assert pg.width_pt > pg.height_pt
        for blk in ir_rot.blocks:
            for sp in blk.spans:
                if sp.bbox is not None:
                    assert 0.0 <= sp.bbox.x0 <= sp.bbox.x1 <= 1.0
                    assert 0.0 <= sp.bbox.y0 <= sp.bbox.y1 <= 1.0
        png_bytes, r_res = engine.render_page(blob, 1, ParseOptions(dpi=100))
        assert r_res.width > r_res.height
        with Image.open(io.BytesIO(png_bytes)) as img:
            assert img.size == (r_res.width, r_res.height)

    with SourceBlob.from_path(PUBLIC_FIXTURES / "fed_g19_consumer_credit.pdf") as blob:
        ir_g19 = engine.parse(blob)
        assert len(ir_g19.pages) == 3
        assert all((p.width_pt or 0) > (p.height_pt or 0) for p in ir_g19.pages)
        assert validate_ir(ir_g19) == []

    # 3. Encrypted & corrupt PDFs -> VERIFICATION_GATE_FAILED
    locked_pdf = _make_pdf(tmp_path / "locked.pdf", ["Secret"], password="pw123")
    with SourceBlob.from_path(locked_pdf) as blob:
        with pytest.raises(OpenDVError) as exc_locked:
            engine.parse(blob)
        assert exc_locked.value.error_code == ErrorCode.VERIFICATION_GATE_FAILED

    for bad_pdf in (SRC_MATRIX / "corrupt_document.pdf",):
        with SourceBlob.from_path(bad_pdf) as blob:
            with pytest.raises(OpenDVError) as exc_corrupt:
                engine.parse(blob)
            assert exc_corrupt.value.error_code == ErrorCode.VERIFICATION_GATE_FAILED

    # 4. --pages OOB, unconstrained inspect, max_pages, timeout_ms, dpi, and 40MP preflight
    fdic_pdf = SAMPLE_PDFS / "fdic_section3_2_loans_credit_risk.pdf"
    with SourceBlob.from_path(fdic_pdf) as blob:
        insp = engine.inspect(blob, ParseOptions(max_pages=2))
        assert insp.page_count == 88

        with pytest.raises(OpenDVError) as exc_max:
            engine.parse(blob, ParseOptions(max_pages=2))
        assert exc_max.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED

        with pytest.raises(OpenDVError) as exc_oob:
            engine.parse(blob, ParseOptions(pages=[999]))
        assert exc_oob.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

        with pytest.raises(OpenDVError) as exc_render_oob:
            engine.render_page(blob, 999)
        assert exc_render_oob.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

        with pytest.raises(OpenDVError) as exc_dpi:
            engine.render_page(blob, 1, ParseOptions(dpi=301))
        assert exc_dpi.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

        with pytest.raises(OpenDVError) as exc_timeout:
            engine.parse(blob, ParseOptions(timeout_ms=1))
        assert exc_timeout.value.error_code == ErrorCode.INTERNAL_SERVER_ERROR

    # 5. Pre-render 40,000,000 px preflight aborts BEFORE screenshot extraction
    huge_pdf = _make_pdf(tmp_path / "huge_40mp.pdf", ["Large canvas"], width=2500, height=2500)
    screenshot_calls: list[bool] = []
    pdf_parser = engine.registry.get_parser("pdf")
    orig_run = pdf_parser._run_liteparse  # type: ignore[attr-defined]

    def _spy_run(*args: Any, **kwargs: Any) -> Any:
        screenshot_calls.append(bool(kwargs.get("extract_screenshots", False)))
        return orig_run(*args, **kwargs)

    monkeypatch.setattr(pdf_parser, "_run_liteparse", _spy_run)
    with SourceBlob.from_path(huge_pdf) as blob:
        with pytest.raises(OpenDVError) as exc_px:
            engine.render_page(blob, 1, ParseOptions(dpi=300))
        assert exc_px.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED
    assert screenshot_calls == [False]


def test_docx_dom_order_merged_cells_mask_before_24k_and_bounded_zip(tmp_path: Path) -> None:
    """Verify DOCX DOM order, gridSpan/vMerge, OLE2, 24k masking, XXE, and BoundedZipReader caps."""
    assert DOCX_MAX_ARCHIVE_ENTRIES == 500
    assert DOCX_MAX_INFLATED_BYTES == 262_144_000
    assert DOCX_MAX_INFLATION_RATIO == 100.0
    assert DOCX_RATIO_MIN_ENTRY_BYTES == 1_048_576

    engine = ParsingEngine()

    # 1. OLE2 encrypted DOCX -> VERIFICATION_GATE_FAILED
    with SourceBlob.from_path(PUBLIC_FIXTURES / "aspose_encrypted_ole2.docx") as blob:
        with pytest.raises(OpenDVError) as exc_ole2:
            engine.parse(blob)
        assert exc_ole2.value.error_code == ErrorCode.VERIFICATION_GATE_FAILED

    # 2. DOM order (heading -> merged table with gridSpan/vMerge & nested table -> SDT -> paragraph)
    dom_docx = tmp_path / "dom_merged.docx"
    doc = docx.Document()
    doc.add_heading("1. DOM Intro", level=1)
    tbl = doc.add_table(rows=3, cols=3)
    tbl.cell(0, 0).text = "Region"
    tbl.cell(0, 1).text = "Metric"
    tbl.cell(0, 2).text = "Value"
    tbl.cell(0, 0).merge(tbl.cell(0, 1))
    tbl.cell(1, 0).text = "North"
    tbl.cell(1, 1).text = "Revenue"
    tbl.cell(1, 2).text = "100"
    tbl.cell(2, 0).text = "North"
    tbl.cell(2, 1).text = "Cost"
    tbl.cell(2, 2).text = "40"
    tbl.cell(1, 2).merge(tbl.cell(2, 2))
    nested = tbl.cell(1, 1).add_table(rows=2, cols=2)
    nested.cell(0, 0).text = "NK"
    nested.cell(0, 1).text = "NV"
    nested.cell(1, 0).text = "k1"
    nested.cell(1, 1).text = "v1"
    sdt_xml = (
        f'<w:sdt {nsdecls("w")}><w:sdtContent>'
        "<w:p><w:r><w:t>SDT Controlled Body</w:t></w:r></w:p>"
        "</w:sdtContent></w:sdt>"
    )
    tbl._tbl.addnext(parse_xml(sdt_xml))
    doc.add_paragraph("2. Trailing Paragraph")
    doc.save(dom_docx)

    with SourceBlob.from_path(dom_docx) as blob:
        ir_dom = engine.parse(blob)
        assert validate_ir(ir_dom) == []
        texts = [b.text for b in ir_dom.blocks]
        i_intro = next(i for i, t in enumerate(texts) if "1. DOM Intro" in t)
        i_tbl = next(i for i, b in enumerate(ir_dom.blocks) if b.kind == "table")
        i_nested = next(i for i, t in enumerate(texts) if "NK" in t)
        i_sdt = next(i for i, t in enumerate(texts) if "SDT Controlled Body" in t)
        i_post = next(i for i, t in enumerate(texts) if "2. Trailing Paragraph" in t)
        assert i_intro < i_tbl < i_nested < i_sdt < i_post
        main_tbl = ir_dom.blocks[i_tbl]
        assert main_tbl.table_headers is not None and len(main_tbl.table_headers) == 3
        assert main_tbl.table_rows is not None and all(len(r) == 3 for r in main_tbl.table_rows)

        # Out-of-bounds --pages on DOCX -> SYNTAX_INVALID_ARGUMENT
        with pytest.raises(OpenDVError) as exc_docx_oob:
            engine.parse(blob, ParseOptions(pages=[99]))
        assert exc_docx_oob.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # 3. Real merged table fixture + mask-before-24k split on DOCX
    with SourceBlob.from_path(PUBLIC_FIXTURES / "aspose_merged_table.docx") as blob:
        ir_real_tbl = engine.parse(blob)
        t_blocks = [b for b in ir_real_tbl.blocks if b.kind == "table"]
        assert t_blocks and t_blocks[0].table_headers is not None and len(t_blocks[0].table_headers) == 4
        assert all(len(r) == 4 for r in (t_blocks[0].table_rows or []))
        assert validate_ir(ir_real_tbl) == []

    pii_docx = tmp_path / "pii_24k.docx"
    d_long = docx.Document()
    d_long.add_paragraph(("Z" * 23_994) + " 123-45-6789 " + ("W" * 400))
    d_long.save(pii_docx)
    with SourceBlob.from_path(pii_docx) as blob:
        ir_pii = engine.parse(blob, sink_class="MODEL_EGRESS", redaction="on")
        assert len(ir_pii.pages) >= 2
        assert all(len(p.canonical_text) <= 24_000 for p in ir_pii.pages)
        joined = "\n".join(p.canonical_text for p in ir_pii.pages)
        assert "123-45-6789" not in joined
        assert MASKED_PII_TOKEN in joined
        assert validate_ir(ir_pii) == []

    # 4. BoundedZipReader: valid info.is_dir() succeeds in DocxParser
    base_docx = tmp_path / "base_valid.docx"
    d_min = docx.Document()
    d_min.add_paragraph("Directory entry OK.")
    d_min.save(base_docx)
    dir_docx = tmp_path / "with_dir_entry.docx"
    with zipfile.ZipFile(base_docx, "r") as src, zipfile.ZipFile(dir_docx, "w") as dst:
        dir_info = zipfile.ZipInfo("word/")
        dir_info.external_attr = 0x10
        dir_info.compress_type = zipfile.ZIP_STORED
        dst.writestr(dir_info, b"")
        for info in src.infolist():
            if info.filename != "word/":
                dst.writestr(info, src.read(info.filename))
    with SourceBlob.from_path(dir_docx) as blob:
        ir_dir = engine.parse(blob)
        assert "Directory entry OK." in ir_dir.pages[0].canonical_text

    # 5. BoundedZipReader default 500-entry cap, >1 MiB 100.0 ratio bomb, and inflated byte cap
    over_500 = tmp_path / "over_500.docx"
    with zipfile.ZipFile(over_500, "w") as zf:
        for i in range(501):
            zf.writestr(f"customXml/item_{i:04d}.xml", b"<a/>")
    with pytest.raises(OpenDVError) as exc_500:
        BoundedZipReader().read_validated(over_500)
    assert exc_500.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED

    ratio_bomb = tmp_path / "ratio_bomb.docx"
    with zipfile.ZipFile(ratio_bomb, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr("word/document.xml", b"\x00" * 1_200_000)
    with pytest.raises(OpenDVError) as exc_ratio:
        BoundedZipReader().read_validated(ratio_bomb)
    assert exc_ratio.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED

    cap_zip = tmp_path / "inflated_cap.docx"
    with zipfile.ZipFile(cap_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", b"X" * 200_000)
    with pytest.raises(OpenDVError) as exc_cap:
        BoundedZipReader(max_uncompressed_bytes=100_000).read_validated(cap_zip)
    assert exc_cap.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED

    # 6. XML DOCTYPE / ENTITY UTF-8 and UTF-16 rejection -> ARCHIVE_SECURITY_VIOLATION
    xxe_payloads = [
        b'<?xml version="1.0"?><!DOCTYPE foo><w:document/>',
        b'<?xml version="1.0"?><!ENTITY xxe SYSTEM "file:///x"><w:document/>',
        '<?xml version="1.0"?><!DOCTYPE root><a/>'.encode("utf-16"),
        "<!DOCTYPE root><a/>".encode("utf-16-le"),
        "<!ENTITY e SYSTEM 'x'><a/>".encode("utf-16-be"),
    ]
    for idx, payload in enumerate(xxe_payloads):
        z_xxe = tmp_path / f"xxe_{idx}.docx"
        with zipfile.ZipFile(z_xxe, "w") as zf:
            zf.writestr("word/document.xml", payload)
        with pytest.raises(OpenDVError) as exc_xxe:
            BoundedZipReader().read_validated(z_xxe)
        assert exc_xxe.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

    # 7. Symlink entry in ZIP -> ARCHIVE_SECURITY_VIOLATION
    sym_zip = tmp_path / "sym_entry.docx"
    with zipfile.ZipFile(sym_zip, "w") as zf:
        info = zipfile.ZipInfo("word/sym.xml")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, b"target")
    with pytest.raises(OpenDVError) as exc_sym:
        BoundedZipReader().read_validated(sym_zip)
    assert exc_sym.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION


def test_markdown_mime_parameters_and_x_markdown_resolution() -> None:
    """Gap 4b: `_resolve_text_format` normalizes MIME params and maps text/x-markdown."""
    engine = ParsingEngine()
    parser = MarkdownTextParser()
    for ctype in ("text/markdown; charset=utf-8", "TEXT/X-MARKDOWN; charset=UTF-8"):
        with SourceBlob.from_bytes(b"# Hi\n\nBody text.\n", content_type=ctype) as blob:
            assert engine.inspect(blob).format_id == "markdown"
            assert parser.inspect(blob, ParseOptions()).format_id == "markdown"
            ir = engine.parse(blob)
            assert ir.provenance.format_id == "markdown"
            assert ir.outline and ir.outline[0].title == "Hi"
            assert validate_ir(ir) == []
            ir_direct = parser.parse(blob, ParseOptions())
            assert ir_direct.provenance.format_id == "markdown"
