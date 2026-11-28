"""Opaque-box E2E tests (Tiers 1 & 2) for F01 (PDF), F02 (DOCX/Zip), and F03 (Markdown/Text)."""

from __future__ import annotations

import json
import stat
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from opendv.models.errors import CliExitCode, ErrorCode, OpenDVError
from tests.parsing.e2e.conftest import (
    REPO_ROOT,
    VIRTUAL_PAGE_SPLIT_CHARS,
    CliResult,
    E2EWorkspace,
    make_boundary_pii_docx,
    make_boundary_pii_text_file,
    make_corrupt_pdf,
    make_dir_entry_docx,
    make_dom_order_merged_docx,
    make_empty_text_file,
    make_locked_pdf,
    make_minimal_pdf,
    make_ole2_header_docx,
    make_pii_multi_token_pdf,
    make_rotated_pdf,
    make_xxe_doctype_docx,
    make_zip_bomb_entry_count_docx,
    make_zip_bomb_ratio_docx,
)


def _assert_cli_error(
    res: CliResult,
    expected_code: ErrorCode,
    expected_exit: CliExitCode,
    expected_status: int,
) -> None:
    assert res.returncode == int(expected_exit), res.stdout + "\n" + res.stderr
    assert res.envelope is not None
    assert res.envelope.ok is False
    assert res.envelope.exit_code == int(expected_exit)
    assert res.envelope.error_code == expected_code
    assert res.envelope.problem is not None
    assert res.envelope.problem.error_code == expected_code
    assert res.envelope.problem.status == expected_status


def _walk_outline_nodes(nodes: list[Any], total_pages: int, block_ids: set[str]) -> int:
    count = 0
    for node in nodes:
        count += 1
        assert 1 <= node.level <= 6
        assert 1 <= node.start_page <= node.end_page <= total_pages
        assert node.node_id
        assert node.title.strip()
        if node.block_id is not None:
            assert node.block_id in block_ids
        if node.children:
            count += _walk_outline_nodes(node.children, total_pages, block_ids)
    return count


# =============================================================================
# F01: PDF Parsing (Tier 1: 6 tests, Tier 2: 5 tests)
# =============================================================================


def test_f01_t1_cli_pdf_inspect_vs_extract_multipage(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """F01-T1-01: inspect vs extract on multi-page digital and mixed-scan PDFs."""
    from opendv.models.document_ir import InspectResult, ParsedDocumentIR

    h15_path = public_fixture("fed_h15_interest_rates.pdf")
    am_path = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"

    ins_h15_res = cli_runner(["parse", "inspect", str(h15_path)])
    assert ins_h15_res.returncode == 0
    assert ins_h15_res.envelope is not None and ins_h15_res.envelope.ok is True
    ins_h15 = InspectResult.model_validate(ins_h15_res.envelope.data)
    assert ins_h15.format_id == "pdf"
    assert ins_h15.page_count == 3
    assert ins_h15.scanned_pages == []
    assert ins_h15.digital_pages == [1, 2, 3]
    assert ins_h15.renderable is True

    ins_am_res = cli_runner(["parse", "inspect", str(am_path)])
    assert ins_am_res.returncode == 0
    assert ins_am_res.envelope is not None
    ins_am = InspectResult.model_validate(ins_am_res.envelope.data)
    assert ins_am.page_count == 6
    assert ins_am.scanned_pages == [4]
    assert ins_am.digital_pages == [1, 2, 3, 5, 6]

    ext_res = cli_runner(["parse", "extract", str(h15_path)])
    assert ext_res.returncode == 0
    assert ext_res.envelope is not None and ext_res.envelope.ok is True
    ir = ParsedDocumentIR.model_validate(ext_res.envelope.data)
    assert ir.provenance.format_id == "pdf"
    assert ir.provenance.page_count == 3
    assert len(ir.pages) == 3
    assert all(p.slice_kind == "physical_page" and p.renderable is True for p in ir.pages)
    assert all(p.extracted_via == "layout_text" for p in ir.pages)
    assert len(ir.blocks) > 0
    for blk in ir.blocks:
        for span in blk.spans:
            assert 1 <= span.page_number <= 3

    e2e_workspace.assert_no_workspace_opendv_dir()


def test_f01_t1_cli_pdf_pages_range_spec_dedup_and_order(
    cli_runner: Callable[..., CliResult],
    public_fixture: Callable[[str], Path],
) -> None:
    """F01-T1-02: --pages range spec deduplicates, sorts, and restricts extracted PDF pages."""
    from opendv.models.document_ir import ParsedDocumentIR

    acl_pdf = public_fixture("acl_2024_findings_eacl_34.pdf")
    res = cli_runner(["parse", "extract", str(acl_pdf), "--pages", "5,1-3,2"])
    assert res.returncode == 0
    assert res.envelope is not None and res.envelope.ok is True
    ir = ParsedDocumentIR.model_validate(res.envelope.data)
    assert ir.provenance.page_count == 4
    assert [p.page_number for p in ir.pages] == [1, 2, 3, 5]
    assert len(ir.blocks) > 0
    for blk in ir.blocks:
        for span in blk.spans:
            assert span.page_number in {1, 2, 3, 5}


def test_f01_t1_service_pdf_rotate_90_180_270_normalized_bbox_bounds(
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """F01-T1-03: /Rotate 90/180/270 PDFs preserve positive dims and [0.0, 1.0] NormalizedBBox."""
    from opendv.service.parsing_svc import ParsingService

    svc = ParsingService(allowed_roots=[e2e_workspace.root, REPO_ROOT])
    g19_ir = svc.parse_document(public_fixture("fed_g19_consumer_credit.pdf"))
    assert g19_ir.provenance.page_count == 3
    for pg in g19_ir.pages:
        assert pg.width_pt is not None and pg.width_pt > 0.0
        assert pg.height_pt is not None and pg.height_pt > 0.0
    for blk in g19_ir.blocks:
        for sp in blk.spans:
            if sp.bbox is not None:
                assert 0.0 <= sp.bbox.x0 <= sp.bbox.x1 <= 1.0
                assert 0.0 <= sp.bbox.y0 <= sp.bbox.y1 <= 1.0

    for deg in (90, 180, 270):
        rot_pdf = make_rotated_pdf(
            e2e_workspace.workspace / f"rot_{deg}.pdf",
            rotate_deg=deg,
        )
        ir = svc.parse_document(rot_pdf)
        assert len(ir.pages) == 1
        pg = ir.pages[0]
        if deg in (90, 270):
            assert pg.width_pt == 792.0
            assert pg.height_pt == 612.0
        else:
            assert pg.width_pt == 612.0
            assert pg.height_pt == 792.0
        # Linux PDFium /Helvetica substitution can insert intra-word spaces on rotated text.
        assert "rotatedpagefinancialsummary" in "".join(pg.canonical_text.lower().split())
        for blk in ir.blocks:
            for sp in blk.spans:
                if sp.bbox is not None:
                    assert 0.0 <= sp.bbox.x0 <= sp.bbox.x1 <= 1.0
                    assert 0.0 <= sp.bbox.y0 <= sp.bbox.y1 <= 1.0


def test_f01_t1_service_pdf_outline_hierarchy_multipage(
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """F01-T1-04: multi-page PDF produces valid DocumentOutlineNode hierarchy."""
    from opendv.service.parsing_svc import ParsingService

    svc = ParsingService(allowed_roots=[e2e_workspace.root, REPO_ROOT])
    ir = svc.parse_document(public_fixture("irs_f1120.pdf"))
    assert ir.provenance.page_count == 6
    assert len(ir.pages) == 6
    assert len(ir.outline) >= 1
    block_ids = {blk.block_id for blk in ir.blocks}
    total_nodes = _walk_outline_nodes(ir.outline, total_pages=6, block_ids=block_ids)
    assert total_nodes >= 1


def test_f01_t1_cli_and_service_pdf_canonical_sha256_parity_lf_crlf_ws(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
    public_fixtures_manifest: dict[str, dict[str, Any]],
) -> None:
    """F01-T1-05: canonical_text_sha256 is deterministic and invariant across LF/CRLF/spaces."""
    from opendv.models.document_ir import ParsedDocumentIR, compute_canonical_text_sha256
    from opendv.service.parsing_svc import ParsingService

    fw9_path = public_fixture("irs_fw9.pdf")
    r1 = cli_runner(["parse", "extract", str(fw9_path)])
    r2 = cli_runner(["parse", "extract", str(fw9_path)])
    assert r1.returncode == 0 and r2.returncode == 0
    assert r1.envelope is not None and r2.envelope is not None
    ir1 = ParsedDocumentIR.model_validate(r1.envelope.data)
    ir2 = ParsedDocumentIR.model_validate(r2.envelope.data)
    assert ir1.provenance.binary_sha256 == public_fixtures_manifest["irs_fw9.pdf"]["sha256"]
    assert [p.canonical_text_sha256 for p in ir1.pages] == [
        p.canonical_text_sha256 for p in ir2.pages
    ]
    for pg in ir1.pages:
        assert pg.canonical_text_sha256 == compute_canonical_text_sha256(pg.canonical_text)

    pdf_lf = make_minimal_pdf(
        e2e_workspace.workspace / "lf.pdf",
        text="Alpha line\nBeta value",
    )
    pdf_ws = make_minimal_pdf(
        e2e_workspace.workspace / "ws.pdf",
        text="Alpha   line\r\nBeta\t\tvalue",
    )
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])
    ir_lf = svc.parse_document(pdf_lf)
    ir_ws = svc.parse_document(pdf_ws)
    assert ir_lf.pages[0].canonical_text == ir_ws.pages[0].canonical_text
    assert ir_lf.pages[0].canonical_text_sha256 == ir_ws.pages[0].canonical_text_sha256


def test_f01_t1_cli_pdf_extract_out_file_and_redaction_toggle(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F01-T1-06: extract defaults unredacted; --redact --out writes consistent redacted IR."""
    from opendv.models.document_ir import ParsedDocumentIR, compute_canonical_text_sha256

    pii_pdf = make_pii_multi_token_pdf(e2e_workspace.workspace / "pii_stmt.pdf")
    raw_res = cli_runner(["parse", "extract", str(pii_pdf)])
    assert raw_res.returncode == 0 and raw_res.envelope is not None
    raw_ir = ParsedDocumentIR.model_validate(raw_res.envelope.data)
    assert raw_ir.provenance.redacted is False
    assert "123-45-6789" in raw_ir.pages[0].canonical_text

    out_json = e2e_workspace.workspace / "redacted_ir.json"
    red_res = cli_runner(["parse", "extract", str(pii_pdf), "--redact", "--out", str(out_json)])
    assert red_res.returncode == 0
    assert out_json.is_file()
    red_ir = ParsedDocumentIR.model_validate_json(out_json.read_text(encoding="utf-8"))
    assert red_ir.provenance.redacted is True
    assert "123-45-6789" not in red_ir.pages[0].canonical_text
    assert "<MASKED_PII>" in red_ir.pages[0].canonical_text
    assert red_ir.pages[0].canonical_text_sha256 == compute_canonical_text_sha256(
        red_ir.pages[0].canonical_text
    )


def test_f01_t2_cli_pdf_password_locked_fails_verification_gate(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F01-T2-01: password-locked PDF fails inspect and extract with VERIFICATION_GATE_FAILED."""
    locked = make_locked_pdf(e2e_workspace.workspace / "locked.pdf")
    for subcmd in ("inspect", "extract"):
        res = cli_runner(["parse", subcmd, str(locked)])
        _assert_cli_error(res, ErrorCode.VERIFICATION_GATE_FAILED, CliExitCode.VALIDATION_ERROR, 422)


def test_f01_t2_cli_pdf_corrupt_truncated_fails_verification_gate(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F01-T2-02: corrupt/truncated PDF fails with VERIFICATION_GATE_FAILED (never empty pages)."""
    from opendv.service.parsing_svc import ParsingService

    corrupt_syn = make_corrupt_pdf(e2e_workspace.workspace / "corrupt.pdf")
    corrupt_repo = REPO_ROOT / "examples" / "scenarios" / "src_matrix" / "corrupt_document.pdf"

    for target in (corrupt_syn, corrupt_repo):
        res = cli_runner(["parse", "extract", str(target)])
        _assert_cli_error(res, ErrorCode.VERIFICATION_GATE_FAILED, CliExitCode.VALIDATION_ERROR, 422)

    svc = ParsingService(allowed_roots=[e2e_workspace.workspace, REPO_ROOT])
    with pytest.raises(OpenDVError) as exc_info:
        svc.parse_document(corrupt_syn)
    assert exc_info.value.error_code == ErrorCode.VERIFICATION_GATE_FAILED


def test_f01_t2_cli_pdf_out_of_range_and_malformed_pages_syntax_error(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F01-T2-03: out-of-range --pages and invalid page specs raise SYNTAX_INVALID_ARGUMENT."""
    one_page = make_minimal_pdf(e2e_workspace.workspace / "single.pdf")
    for bad_spec in ("2", "0", "5-2", "1,,2", "abc", ""):
        res = cli_runner(["parse", "extract", str(one_page), "--pages", bad_spec])
        _assert_cli_error(res, ErrorCode.SYNTAX_INVALID_ARGUMENT, CliExitCode.VALIDATION_ERROR, 400)


def test_f01_t2_cli_pdf_max_pages_enforced_on_extract_only(
    cli_runner: Callable[..., CliResult],
    public_fixture: Callable[[str], Path],
) -> None:
    """F01-T2-04: inspect allows >max_pages while extract enforces max_pages with 413."""
    h15 = public_fixture("fed_h15_interest_rates.pdf")
    ins_res = cli_runner(["parse", "inspect", str(h15), "--max-pages", "2"])
    assert ins_res.returncode == 0
    assert ins_res.envelope is not None and ins_res.envelope.data is not None
    assert ins_res.envelope.data["page_count"] == 3

    ext_res = cli_runner(["parse", "extract", str(h15), "--max-pages", "2"])
    _assert_cli_error(ext_res, ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED, CliExitCode.VALIDATION_ERROR, 413)


def test_f01_t2_cli_pdf_timeout_ms_enforced_and_invalid_timeout_rejected(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F01-T2-05: hard timeout_ms expiry yields INTERNAL_SERVER_ERROR (500); timeout_ms=0 yields 400."""
    fdic_pdf = REPO_ROOT / "examples" / "sample_pdfs" / "fdic_section3_2_loans_credit_risk.pdf"
    timeout_res = cli_runner(["parse", "extract", str(fdic_pdf), "--timeout-ms", "1"])
    _assert_cli_error(timeout_res, ErrorCode.INTERNAL_SERVER_ERROR, CliExitCode.EXECUTION_ERROR, 500)

    min_pdf = make_minimal_pdf(e2e_workspace.workspace / "min_to.pdf")
    bad_to_res = cli_runner(["parse", "extract", str(min_pdf), "--timeout-ms", "0"])
    _assert_cli_error(bad_to_res, ErrorCode.SYNTAX_INVALID_ARGUMENT, CliExitCode.VALIDATION_ERROR, 400)


# =============================================================================
# F02: DOCX & BoundedZipReader (Tier 1: 5 tests, Tier 2: 6 tests)
# =============================================================================


def test_f02_t1_cli_docx_dom_order_sdt_and_nested_table(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F02-T1-01: DOCX preserves <w:p>, <w:tbl>, <w:sdt>, and nested table DOM order."""
    from opendv.models.document_ir import ParsedDocumentIR

    dom_docx = make_dom_order_merged_docx(
        e2e_workspace.workspace / "dom_order.docx",
        include_boundary_pii=False,
    )
    res = cli_runner(["parse", "extract", str(dom_docx)])
    assert res.returncode == 0 and res.envelope is not None
    ir = ParsedDocumentIR.model_validate(res.envelope.data)
    assert ir.provenance.format_id == "docx"
    assert all(p.slice_kind == "virtual_section_page" and p.renderable is False for p in ir.pages)

    ordered_texts = [blk.text for blk in sorted(ir.blocks, key=lambda b: b.reading_order)]
    joined = "\n".join(ordered_texts)
    idx_intro = joined.index("1. DOM Order Intro")
    idx_tbl = joined.index("Region")
    idx_sdt = joined.index("SDT Controlled Section Paragraph")
    idx_post = joined.index("2. Post-Table Paragraph")
    assert idx_intro < idx_tbl < idx_sdt < idx_post
    assert "N_K" in joined and "sub" in joined and "99" in joined


def test_f02_t1_service_docx_rectangular_merged_cells_gridspan_vmerge(
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """F02-T1-02: gridSpan and vMerge DOCX tables produce uniform rectangular column widths."""
    from opendv.service.parsing_svc import ParsingService

    svc = ParsingService(allowed_roots=[e2e_workspace.workspace, REPO_ROOT])
    dom_docx = make_dom_order_merged_docx(
        e2e_workspace.workspace / "merged.docx",
        include_boundary_pii=False,
    )

    for path, expected_cols in (
        (public_fixture("aspose_merged_table.docx"), 4),
        (dom_docx, 3),
    ):
        ir = svc.parse_document(path)
        tables = [b for b in ir.blocks if b.kind == "table"]
        assert len(tables) >= 1
        outer = tables[0]
        assert outer.table_headers is not None and len(outer.table_headers) == expected_cols
        assert outer.table_rows is not None and len(outer.table_rows) >= 1
        assert all(len(row) == expected_cols for row in outer.table_rows)

    irs_ir = svc.parse_document(public_fixture("irs_soi_21incddocguide.docx"))
    irs_tables = [b for b in irs_ir.blocks if b.kind == "table"]
    assert len(irs_tables) >= 1
    for tbl in irs_tables:
        assert tbl.table_headers is not None
        width = len(tbl.table_headers)
        assert width >= 1
        assert tbl.table_rows is not None
        assert all(len(row) == width for row in tbl.table_rows)


def test_f02_t1_cli_docx_public_irs_and_aspose_virtual_section_slices(
    cli_runner: Callable[..., CliResult],
    public_fixture: Callable[[str], Path],
) -> None:
    """F02-T1-03: public IRS and Aspose DOCX fixtures extract virtual_section_page IRs."""
    from opendv.models.document_ir import InspectResult, ParsedDocumentIR

    for fname in ("irs_soi_23zpdoc.docx", "aspose_formatted_elements.docx"):
        fpath = public_fixture(fname)
        ins_res = cli_runner(["parse", "inspect", str(fpath)])
        assert ins_res.returncode == 0 and ins_res.envelope is not None
        ins = InspectResult.model_validate(ins_res.envelope.data)
        assert ins.format_id == "docx"
        assert ins.renderable is False
        assert ins.scanned_pages == []

        ext_res = cli_runner(["parse", "extract", str(fpath)])
        assert ext_res.returncode == 0 and ext_res.envelope is not None
        ir = ParsedDocumentIR.model_validate(ext_res.envelope.data)
        assert all(p.slice_kind == "virtual_section_page" for p in ir.pages)
        assert any(b.kind == "table" for b in ir.blocks)
        assert any(b.kind == "list" for b in ir.blocks)


def test_f02_t1_cli_docx_dir_entry_zipinfo_is_dir_accepted(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F02-T1-04: valid .docx archives containing info.is_dir() entries parse cleanly."""
    from opendv.models.document_ir import ParsedDocumentIR

    dir_docx = make_dir_entry_docx(e2e_workspace.workspace / "dir_entry.docx")
    res = cli_runner(["parse", "extract", str(dir_docx)])
    assert res.returncode == 0 and res.envelope is not None
    ir = ParsedDocumentIR.model_validate(res.envelope.data)
    assert "Directory entry test document." in ir.pages[0].canonical_text


def test_f02_t1_cli_docx_pii_mask_before_24000_char_split(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F02-T1-05: DOCX masks unsplit text before 24,000-char virtual_section_page splitting."""
    from opendv.models.document_ir import ParsedDocumentIR

    for fname, builder in (
        ("boundary_pii.docx", lambda p: make_boundary_pii_docx(p)),
        ("dom_pii.docx", lambda p: make_dom_order_merged_docx(p, include_boundary_pii=True)),
    ):
        fpath = builder(e2e_workspace.workspace / fname)
        res = cli_runner(["parse", "extract", str(fpath), "--redact"])
        assert res.returncode == 0 and res.envelope is not None
        ir = ParsedDocumentIR.model_validate(res.envelope.data)
        assert ir.provenance.redacted is True
        assert len(ir.pages) >= 2
        for pg in ir.pages:
            assert len(pg.canonical_text) <= VIRTUAL_PAGE_SPLIT_CHARS
            assert "123-45-6789" not in pg.canonical_text
        joined = "".join(pg.canonical_text for pg in ir.pages)
        assert "123-45-6789" not in joined
        assert "<MASKED_PII>" in joined


def test_f02_t2_cli_docx_ole2_encrypted_fails_verification_gate(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """F02-T2-01: OLE2-encrypted .docx fails inspect and extract with VERIFICATION_GATE_FAILED."""
    ole2_pub = public_fixture("aspose_encrypted_ole2.docx")
    ole2_syn = make_ole2_header_docx(e2e_workspace.workspace / "ole2_hdr.docx")

    for path in (ole2_pub, ole2_syn):
        for subcmd in ("inspect", "extract"):
            res = cli_runner(["parse", subcmd, str(path)])
            _assert_cli_error(
                res,
                ErrorCode.VERIFICATION_GATE_FAILED,
                CliExitCode.VALIDATION_ERROR,
                422,
            )


def test_f02_t2_cli_docx_zip_bomb_entry_count_exceeds_500(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F02-T2-02: .docx with >500 ZIP entries fails with ARCHIVE_SIZE_LIMIT_EXCEEDED (413)."""
    bomb = make_zip_bomb_entry_count_docx(
        e2e_workspace.workspace / "many_entries.docx",
        entry_count=505,
    )
    res = cli_runner(["parse", "extract", str(bomb)])
    _assert_cli_error(res, ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED, CliExitCode.VALIDATION_ERROR, 413)


def test_f02_t2_cli_docx_zip_bomb_ratio_exceeds_100_above_1mib(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F02-T2-03: .docx with >100.0 ratio above 1 MiB aborts with ARCHIVE_SIZE_LIMIT_EXCEEDED."""
    bomb = make_zip_bomb_ratio_docx(
        e2e_workspace.workspace / "ratio_bomb.docx",
        uncompressed_bytes=2 * 1024 * 1024,
    )
    res = cli_runner(["parse", "extract", str(bomb)])
    _assert_cli_error(res, ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED, CliExitCode.VALIDATION_ERROR, 413)


def test_f02_t2_zip_guard_inflated_bytes_cap_and_defaults(
    e2e_workspace: E2EWorkspace,
) -> None:
    """F02-T2-04: BoundedZipReader enforces 250 MiB default and aborts mid-stream on inflated cap."""
    from opendv.parsing.parsers._zip_guard import (
        DOCX_MAX_ARCHIVE_ENTRIES,
        DOCX_MAX_INFLATED_BYTES,
        DOCX_MAX_INFLATION_RATIO,
        BoundedZipReader,
    )

    default_reader = BoundedZipReader()
    assert default_reader.max_entries == 500 == DOCX_MAX_ARCHIVE_ENTRIES
    assert default_reader.max_uncompressed_bytes == 262_144_000 == DOCX_MAX_INFLATED_BYTES
    assert default_reader.max_ratio == 100.0 == DOCX_MAX_INFLATION_RATIO

    valid_docx = make_dir_entry_docx(e2e_workspace.workspace / "valid_stream.docx")
    capped_reader = BoundedZipReader(max_uncompressed_bytes=512)
    with pytest.raises(OpenDVError) as exc_info:
        capped_reader.read_validated(valid_docx)
    assert exc_info.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED


def test_f02_t2_cli_docx_xml_doctype_entity_rejected(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F02-T2-05: XML <!DOCTYPE / <!ENTITY declarations in .docx fail with ARCHIVE_SECURITY_VIOLATION."""
    xxe_docx = make_xxe_doctype_docx(e2e_workspace.workspace / "xxe.docx")
    res = cli_runner(["parse", "extract", str(xxe_docx)])
    _assert_cli_error(res, ErrorCode.ARCHIVE_SECURITY_VIOLATION, CliExitCode.VALIDATION_ERROR, 422)


def test_f02_t2_zip_guard_traversal_ads_symlink_and_encrypted_member(
    e2e_workspace: E2EWorkspace,
) -> None:
    """F02-T2-06: BoundedZipReader blocks traversal, ADS, symlinks (422) and encrypted entries."""
    from opendv.parsing.parsers._zip_guard import BoundedZipReader

    reader = BoundedZipReader()

    traversal_zip = e2e_workspace.workspace / "traversal.docx"
    with zipfile.ZipFile(traversal_zip, "w") as zf:
        zf.writestr("../evil.xml", b"<x/>")
    with pytest.raises(OpenDVError) as exc_trav:
        reader.read_validated(traversal_zip)
    assert exc_trav.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

    ads_zip = e2e_workspace.workspace / "ads.docx"
    with zipfile.ZipFile(ads_zip, "w") as zf:
        zf.writestr("word/document.xml:secret", b"<x/>")
    with pytest.raises(OpenDVError) as exc_ads:
        reader.read_validated(ads_zip)
    assert exc_ads.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

    symlink_zip = e2e_workspace.workspace / "symlink.docx"
    with zipfile.ZipFile(symlink_zip, "w") as zf:
        info = zipfile.ZipInfo("word/link.xml")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, b"/etc/passwd")
    with pytest.raises(OpenDVError) as exc_sym:
        reader.read_validated(symlink_zip)
    assert exc_sym.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

    enc_zip = e2e_workspace.workspace / "enc_flag.docx"
    with zipfile.ZipFile(enc_zip, "w") as zf:
        zf.writestr("word/document.xml", b"encrypted")
    raw_zip = bytearray(enc_zip.read_bytes())
    lh_idx = raw_zip.index(b"PK\x03\x04")
    raw_zip[lh_idx + 6] |= 0x01
    cd_idx = raw_zip.index(b"PK\x01\x02")
    raw_zip[cd_idx + 8] |= 0x01
    enc_zip.write_bytes(bytes(raw_zip))
    with pytest.raises(OpenDVError) as exc_enc:
        reader.read_validated(enc_zip)
    assert exc_enc.value.error_code == ErrorCode.VERIFICATION_GATE_FAILED


# =============================================================================
# F03: Markdown & Text Parsing (Tier 1: 6 tests, Tier 2: 5 tests)
# =============================================================================


def test_f03_t1_cli_markdown_and_dot_markdown_extension_happy_paths(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """F03-T1-01: .md and .markdown files extract headings, lists, tables, and outline."""
    from opendv.models.document_ir import ParsedDocumentIR

    md_custom = e2e_workspace.workspace / "architecture.markdown"
    md_custom.write_text(
        "# Core Spec\n\n- Item A\n- Item B\n\n| key | val |\n| --- | --- |\n| k1 | v1 |\n",
        encoding="utf-8",
    )
    am_md = REPO_ROOT / "data" / "public_am" / "am_stewardship_domain_notes.md"
    dv_md = public_fixture("datavault_automate_dv_readme.md")

    for path in (md_custom, am_md, dv_md):
        res = cli_runner(["parse", "extract", str(path)])
        assert res.returncode == 0 and res.envelope is not None
        ir = ParsedDocumentIR.model_validate(res.envelope.data)
        assert ir.provenance.format_id == "markdown"
        assert all(
            p.slice_kind == "virtual_section_page" and p.renderable is False for p in ir.pages
        )
        assert any(b.kind == "heading" for b in ir.blocks)
        assert len(ir.outline) >= 1

    ext_custom = cli_runner(["parse", "extract", str(md_custom)])
    assert ext_custom.envelope is not None
    ir_custom = ParsedDocumentIR.model_validate(ext_custom.envelope.data)
    assert any(b.kind == "list" for b in ir_custom.blocks)
    assert any(b.kind == "table" for b in ir_custom.blocks)


def test_f03_t1_cli_txt_domain_notes_and_crlf_lf_sha256_parity(
    cli_runner: Callable[..., CliResult],
    public_fixture: Callable[[str], Path],
) -> None:
    """F03-T1-02: .txt extracts as domain_note with identical SHA-256 across LF and CRLF."""
    from opendv.models.document_ir import ParsedDocumentIR

    csv_rfc = public_fixture("rfc4180_csv.txt")
    res_rfc = cli_runner(["parse", "extract", str(csv_rfc)])
    assert res_rfc.returncode == 0 and res_rfc.envelope is not None
    ir_rfc = ParsedDocumentIR.model_validate(res_rfc.envelope.data)
    assert ir_rfc.provenance.format_id == "domain_note"
    assert ir_rfc.pages[0].renderable is False

    lf_txt = REPO_ROOT / "examples" / "scenarios" / "src_matrix" / "enterprise_msa_addendum_2026.txt"
    crlf_txt = (
        REPO_ROOT
        / "examples"
        / "scenarios"
        / "src_matrix"
        / "enterprise_msa_addendum_2026_crlf.txt"
    )
    r_lf = cli_runner(["parse", "extract", str(lf_txt)])
    r_crlf = cli_runner(["parse", "extract", str(crlf_txt)])
    assert r_lf.returncode == 0 and r_crlf.returncode == 0
    assert r_lf.envelope is not None and r_crlf.envelope is not None
    ir_lf = ParsedDocumentIR.model_validate(r_lf.envelope.data)
    ir_crlf = ParsedDocumentIR.model_validate(r_crlf.envelope.data)
    assert ir_lf.pages[0].canonical_text == ir_crlf.pages[0].canonical_text
    assert ir_lf.pages[0].canonical_text_sha256 == ir_crlf.pages[0].canonical_text_sha256


def test_f03_t1_cli_markdown_and_txt_gt_24000_virtual_section_page_splitting(
    cli_runner: Callable[..., CliResult],
    public_fixture: Callable[[str], Path],
) -> None:
    """F03-T1-03: >24,000-char .md and .txt split cleanly into <=24,000-char virtual slices."""
    from opendv.models.document_ir import ParsedDocumentIR

    for fname, expected_fmt in (
        ("fivetran_dbt_stripe_readme.md", "markdown"),
        ("rfc8259_json.txt", "domain_note"),
    ):
        res = cli_runner(["parse", "extract", str(public_fixture(fname))])
        assert res.returncode == 0 and res.envelope is not None
        ir = ParsedDocumentIR.model_validate(res.envelope.data)
        assert ir.provenance.format_id == expected_fmt
        assert len(ir.pages) >= 2
        assert [p.page_number for p in ir.pages] == list(range(1, len(ir.pages) + 1))
        for pg in ir.pages:
            assert pg.slice_kind == "virtual_section_page"
            assert pg.renderable is False
            assert len(pg.canonical_text) <= VIRTUAL_PAGE_SPLIT_CHARS


def test_f03_t1_cli_text_pii_mask_before_24000_char_split(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F03-T1-04: .md/.txt masks boundary-straddling PII BEFORE 24,000-char virtual slice split."""
    from opendv.models.document_ir import ParsedDocumentIR

    for ext in (".md", ".txt"):
        fpath = make_boundary_pii_text_file(e2e_workspace.workspace / f"pii_boundary{ext}")
        res = cli_runner(["parse", "extract", str(fpath), "--redact"])
        assert res.returncode == 0 and res.envelope is not None
        ir = ParsedDocumentIR.model_validate(res.envelope.data)
        assert ir.provenance.redacted is True
        assert len(ir.pages) >= 2
        for pg in ir.pages:
            assert len(pg.canonical_text) <= VIRTUAL_PAGE_SPLIT_CHARS
            assert "123-45-6789" not in pg.canonical_text
        joined = "".join(pg.canonical_text for pg in ir.pages)
        assert "123-45-6789" not in joined
        assert "<MASKED_PII>" in joined


def test_f03_t1_service_txt_utf8_bom_large_rfc_parsing(
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """F03-T1-05: UTF-8 BOM 500 KB RFC text parses cleanly into >15 bounded virtual slices."""
    from opendv.service.parsing_svc import ParsingService

    svc = ParsingService(allowed_roots=[e2e_workspace.workspace, REPO_ROOT])
    ir = svc.parse_document(public_fixture("rfc9110_http_semantics.txt"))
    assert ir.provenance.format_id == "domain_note"
    assert len(ir.pages) > 15
    assert ir.provenance.page_count == len(ir.pages)
    assert all(len(p.canonical_text) <= VIRTUAL_PAGE_SPLIT_CHARS for p in ir.pages)


def test_f03_t1_cli_text_zero_byte_empty_md_and_txt_single_empty_slice(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F03-T1-06: 0-byte .md and .txt emit single PageSlice(extracted_via='empty')."""
    from opendv.models.document_ir import InspectResult, ParsedDocumentIR

    for fname, expected_fmt in (("empty.md", "markdown"), ("empty.txt", "domain_note")):
        fpath = make_empty_text_file(e2e_workspace.workspace / fname)
        ins_res = cli_runner(["parse", "inspect", str(fpath)])
        assert ins_res.returncode == 0 and ins_res.envelope is not None
        ins = InspectResult.model_validate(ins_res.envelope.data)
        assert ins.format_id == expected_fmt
        assert ins.page_count == 1
        assert ins.renderable is False

        ext_res = cli_runner(["parse", "extract", str(fpath)])
        assert ext_res.returncode == 0 and ext_res.envelope is not None
        ir = ParsedDocumentIR.model_validate(ext_res.envelope.data)
        assert len(ir.pages) == 1
        pg = ir.pages[0]
        assert pg.page_number == 1
        assert pg.slice_kind == "virtual_section_page"
        assert pg.renderable is False
        assert pg.extracted_via == "empty"
        assert pg.canonical_text == ""
        assert pg.raw_text_length == 0
        assert pg.non_ws_chars == 0
        assert ir.blocks == []


def test_f03_t2_cli_unsupported_extension_fails_unsupported_media_type(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F03-T2-01: unsupported file extension fails with UNSUPPORTED_MEDIA_TYPE (415)."""
    unknown = e2e_workspace.workspace / "archive.xyz"
    unknown.write_bytes(b"opaque binary payload")
    for subcmd in ("inspect", "extract"):
        res = cli_runner(["parse", subcmd, str(unknown)])
        _assert_cli_error(res, ErrorCode.UNSUPPORTED_MEDIA_TYPE, CliExitCode.VALIDATION_ERROR, 415)


def test_f03_t2_cli_text_invalid_utf8_fails_syntax_invalid_argument(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F03-T2-02: invalid UTF-8 bytes in .md/.txt fail with SYNTAX_INVALID_ARGUMENT (400)."""
    for fname in ("bad.md", "bad.txt"):
        fpath = e2e_workspace.workspace / fname
        fpath.write_bytes(b"\xff\xfe\xfd\x80")
        for subcmd in ("inspect", "extract"):
            res = cli_runner(["parse", subcmd, str(fpath)])
            _assert_cli_error(
                res,
                ErrorCode.SYNTAX_INVALID_ARGUMENT,
                CliExitCode.VALIDATION_ERROR,
                400,
            )


def test_f03_t2_cli_text_page_window_and_oob_page_rejected(
    cli_runner: Callable[..., CliResult],
    public_fixture: Callable[[str], Path],
) -> None:
    """F03-T2-03: --pages selects virtual slices and rejects out-of-range virtual pages (400)."""
    from opendv.models.document_ir import ParsedDocumentIR

    stripe_md = public_fixture("fivetran_dbt_stripe_readme.md")
    ok_res = cli_runner(["parse", "extract", str(stripe_md), "--pages", "2"])
    assert ok_res.returncode == 0 and ok_res.envelope is not None
    ir = ParsedDocumentIR.model_validate(ok_res.envelope.data)
    assert [p.page_number for p in ir.pages] == [2]

    oob_res = cli_runner(["parse", "extract", str(stripe_md), "--pages", "99"])
    _assert_cli_error(oob_res, ErrorCode.SYNTAX_INVALID_ARGUMENT, CliExitCode.VALIDATION_ERROR, 400)


def test_f03_t2_cli_text_max_pages_enforced_on_extract_not_inspect(
    cli_runner: Callable[..., CliResult],
    public_fixture: Callable[[str], Path],
) -> None:
    """F03-T2-04: inspect allows >max_pages while extract blocks with 413 on multi-slice text."""
    oauth_txt = public_fixture("rfc6749_oauth2.txt")
    ins_res = cli_runner(["parse", "inspect", str(oauth_txt), "--max-pages", "2"])
    assert ins_res.returncode == 0 and ins_res.envelope is not None
    assert ins_res.envelope.data is not None
    assert ins_res.envelope.data["page_count"] > 2

    ext_res = cli_runner(["parse", "extract", str(oauth_txt), "--max-pages", "2"])
    _assert_cli_error(
        ext_res,
        ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
        CliExitCode.VALIDATION_ERROR,
        413,
    )


def test_f03_t2_service_empty_file_document_map_projection_page_count_ge_1(
    e2e_workspace: E2EWorkspace,
) -> None:
    """F03-T2-05: 0-byte Markdown projects to DocumentMap with page_count == 1."""
    from opendv.service.parsing_svc import ParsingService

    zero_md = make_empty_text_file(e2e_workspace.workspace / "zero.md")
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])
    ir = svc.parse_document(zero_md)
    assert svc.validate_ir(ir) == []
    proj = svc.export_ir(ir, target="document_map")
    assert isinstance(proj, dict)
    assert proj["document_map"].page_count == 1
    assert len(proj["document_map"].pages) == 1
    assert proj["document_map"].pages[0].char_count == 0
