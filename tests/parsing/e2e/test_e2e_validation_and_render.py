"""Opaque-box E2E tests (Tiers 1 & 2) for F04 (IR Validation/Projectors) and F05 (Render/Caps)."""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from opendv.models.errors import CliExitCode, ErrorCode, OpenDVError
from tests.parsing.e2e.conftest import (
    REPO_ROOT,
    CliResult,
    E2EWorkspace,
    make_dom_order_merged_docx,
    make_high_pixel_pdf,
    make_minimal_pdf,
    make_pii_multi_token_pdf,
    make_scanned_image_pdf,
    query_ndjson_with_duckdb,
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


def _extract_to_json(
    cli_runner: Callable[..., CliResult],
    source_path: Path,
    out_path: Path,
    *,
    redact: bool = False,
) -> dict[str, Any]:
    args = ["parse", "extract", str(source_path), "--out", str(out_path)]
    if redact:
        args.append("--redact")
    res = cli_runner(args)
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert out_path.is_file()
    return json.loads(out_path.read_text(encoding="utf-8"))


def _diag_codes_from_validate_envelope(res: CliResult) -> set[str]:
    assert res.envelope is not None
    data = res.envelope.data
    if isinstance(data, list):
        return {str(item["code"]) for item in data if isinstance(item, dict) and "code" in item}
    if isinstance(data, dict) and isinstance(data.get("diagnostics"), list):
        return {
            str(item["code"])
            for item in data["diagnostics"]
            if isinstance(item, dict) and "code" in item
        }
    return set()


# =============================================================================
# F04: IR Validation & Projectors (Tier 1: 6 tests, Tier 2: 6 tests)
# =============================================================================


def test_f04_t1_cli_validate_clean_pdf_docx_md_txt_irs(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """F04-T1-01: parse validate returns exit 0 on cleanly extracted PDF, DOCX, MD, and TXT IRs."""
    samples = (
        ("pdf_ir.json", public_fixture("fed_h15_interest_rates.pdf")),
        ("docx_ir.json", public_fixture("aspose_merged_table.docx")),
        ("md_ir.json", public_fixture("fivetran_dbt_stripe_readme.md")),
        ("txt_ir.json", public_fixture("rfc4180_csv.txt")),
    )
    for out_name, src_path in samples:
        ir_path = e2e_workspace.workspace / out_name
        _extract_to_json(cli_runner, src_path, ir_path)
        val_res = cli_runner(["parse", "validate", str(ir_path)])
        assert val_res.returncode == 0, f"{src_path.name}: {val_res.stdout}\n{val_res.stderr}"
        assert val_res.envelope is not None and val_res.envelope.ok is True


def test_f04_t1_cli_validate_raw_redacted_placeholder_non_regression(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F04-T1-02: parse validate allows literal [REDACTED_*] and <MASKED_PII> without failing."""
    pdf_path = make_minimal_pdf(
        e2e_workspace.workspace / "literal_redacted.pdf",
        text="Literal [REDACTED_TOKEN] and <MASKED_PII> inside document body.",
    )
    for idx, redact in enumerate((False, True)):
        ir_path = e2e_workspace.workspace / f"red_nonreg_{idx}.json"
        _extract_to_json(cli_runner, pdf_path, ir_path, redact=redact)
        val_res = cli_runner(["parse", "validate", str(ir_path)])
        assert val_res.returncode == 0, val_res.stdout + "\n" + val_res.stderr
        assert val_res.envelope is not None and val_res.envelope.ok is True


def test_f04_t1_cli_export_document_map_off_vs_on_redaction(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F04-T1-03: document_map export masks outbound views while keeping raw canonical SHA-256."""
    from opendv.models.document_ir import ParsedDocumentIR
    from opendv.models.sources import DocumentMap, PageTextView

    pii_pdf = make_pii_multi_token_pdf(e2e_workspace.workspace / "pii_dmap.pdf")
    ir_path = e2e_workspace.workspace / "pii_dmap_ir.json"
    ir_raw_dict = _extract_to_json(cli_runner, pii_pdf, ir_path)
    ir_raw = ParsedDocumentIR.model_validate(ir_raw_dict)

    off_res = cli_runner(["parse", "export", str(ir_path), "--target", "document_map"])
    assert off_res.returncode == 0 and off_res.envelope is not None
    assert isinstance(off_res.envelope.data, dict)
    dmap_off = DocumentMap.model_validate(off_res.envelope.data["document_map"])
    views_off = [PageTextView.model_validate(v) for v in off_res.envelope.data["page_text_views"]]
    assert dmap_off.format == "pdf"
    assert "123-45-6789" in views_off[0].masked_text

    on_res = cli_runner(["parse", "export", str(ir_path), "--target", "document_map", "--redact"])
    assert on_res.returncode == 0 and on_res.envelope is not None
    assert isinstance(on_res.envelope.data, dict)
    dmap_on = DocumentMap.model_validate(on_res.envelope.data["document_map"])
    views_on = [PageTextView.model_validate(v) for v in on_res.envelope.data["page_text_views"]]
    assert "123-45-6789" not in views_on[0].masked_text
    assert "<MASKED_PII>" in views_on[0].masked_text
    assert dmap_on.pages[0].canonical_text_sha256 == ir_raw.pages[0].canonical_text_sha256


def test_f04_t1_cli_export_markdown_gfm_tables_and_dom_order(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F04-T1-04: markdown projection renders headings, GFM pipe tables, and SDTs in DOM order."""
    from opendv.models.document_ir import ParsedDocumentIR
    from opendv.service.parsing_svc import ParsingService

    dom_docx = make_dom_order_merged_docx(
        e2e_workspace.workspace / "dom_md.docx",
        include_boundary_pii=False,
    )
    ir_path = e2e_workspace.workspace / "dom_md_ir.json"
    _extract_to_json(cli_runner, dom_docx, ir_path)

    md_out = e2e_workspace.workspace / "projected.md"
    res = cli_runner(
        ["parse", "export", str(ir_path), "--target", "markdown", "--out", str(md_out)]
    )
    assert res.returncode == 0
    assert md_out.is_file()
    md_text = md_out.read_text(encoding="utf-8")
    assert "# 1. DOM Order Intro" in md_text
    assert "| Region Metric |  | Value |" in md_text
    assert "| --- |" in md_text
    assert md_text.index("1. DOM Order Intro") < md_text.index("SDT Controlled Section Paragraph")
    assert md_text.index("SDT Controlled Section Paragraph") < md_text.index(
        "2. Post-Table Paragraph"
    )

    pii_docx = make_dom_order_merged_docx(
        e2e_workspace.workspace / "dom_md_pii.docx",
        include_boundary_pii=True,
    )
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])
    ir_pii: ParsedDocumentIR = svc.parse_document(pii_docx)
    md_red = svc.export_ir(ir_pii, target="markdown", sink_class="MODEL_EGRESS", redaction="on")
    assert isinstance(md_red, str)
    assert "123-45-6789" not in md_red
    assert "<MASKED_PII>" in md_red


def test_f04_t1_service_export_chunks_4000_char_bound_and_heading_path(
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """F04-T1-05: chunks projector enforces <=4,000-char chunks, heading_path, and table_json."""
    from opendv.service.parsing_svc import ParsingService

    svc = ParsingService(allowed_roots=[e2e_workspace.workspace, REPO_ROOT])
    long_md = e2e_workspace.workspace / "oversized_block.md"
    long_para = " ".join(["token"] * 1800)  # > 10,000 chars in one paragraph
    long_md.write_text(
        f"# Top Heading\n\n## Sub Heading\n\n{long_para}\n\n"
        "| col_a | col_b |\n| --- | --- |\n| x | y |\n",
        encoding="utf-8",
    )

    for src in (long_md, public_fixture("fivetran_dbt_stripe_readme.md")):
        ir = svc.parse_document(src)
        chunks = svc.export_ir(ir, target="chunks")
        assert isinstance(chunks, list) and len(chunks) > 0
        for ch in chunks:
            assert len(ch.text) <= 4_000

    ir_long = svc.parse_document(long_md)
    chunks_long = svc.export_ir(ir_long, target="chunks")
    assert isinstance(chunks_long, list)
    para_chunks = [c for c in chunks_long if c.block_kind == "paragraph"]
    assert len(para_chunks) >= 3
    assert all(c.heading_path == ["Top Heading", "Sub Heading"] for c in para_chunks)
    table_chunks = [c for c in chunks_long if c.block_kind == "table"]
    assert len(table_chunks) == 1
    assert table_chunks[0].table_json is not None
    parsed_tbl = json.loads(table_chunks[0].table_json)
    assert parsed_tbl["headers"] == ["col_a", "col_b"]
    assert parsed_tbl["rows"] == [["x", "y"]]


def test_f04_t1_cli_export_chunks_jsonl_duckdb_read_ndjson_uniform_schema(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """F04-T1-06: streaming JSONL chunks have uniform 16-key schema and query cleanly in DuckDB."""
    ir_path = e2e_workspace.workspace / "h15_ir.json"
    _extract_to_json(cli_runner, public_fixture("fed_h15_interest_rates.pdf"), ir_path)

    jsonl_path = e2e_workspace.workspace / "chunks.jsonl"
    res = cli_runner(
        [
            "parse",
            "export",
            str(ir_path),
            "--target",
            "chunks",
            "--format",
            "jsonl",
            "--out",
            str(jsonl_path),
        ]
    )
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert jsonl_path.is_file()

    raw_lines = [ln for ln in jsonl_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(raw_lines) > 0
    parsed_lines = [json.loads(ln) for ln in raw_lines]
    first_keys = set(parsed_lines[0].keys())
    assert len(first_keys) == 16
    assert all(set(item.keys()) == first_keys for item in parsed_lines)
    assert all(len(item["text"]) <= 4_000 for item in parsed_lines)

    rows = query_ndjson_with_duckdb(
        jsonl_path,
        "SELECT COUNT(*) AS cnt, COUNT(DISTINCT block_kind) AS kinds, "
        "MIN(page_number) AS min_p, MAX(LENGTH(text)) AS max_len FROM chunks",
    )
    assert len(rows) == 1
    assert rows[0]["cnt"] == len(parsed_lines)
    assert rows[0]["kinds"] >= 1
    assert rows[0]["min_p"] == 1
    assert rows[0]["max_len"] <= 4_000


def test_f04_t2_cli_validate_tampered_canonical_sha256_fails_gate(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F04-T2-01: tampered canonical_text_sha256 and raw_text_length fail VERIFICATION_GATE_FAILED."""
    min_pdf = make_minimal_pdf(e2e_workspace.workspace / "tamper.pdf")
    ir_path = e2e_workspace.workspace / "tamper_ir.json"
    ir_dict = _extract_to_json(cli_runner, min_pdf, ir_path)
    ir_dict["pages"][0]["canonical_text_sha256"] = "0" * 64
    ir_dict["pages"][0]["raw_text_length"] = 99999

    bad_ir_path = e2e_workspace.workspace / "tampered_ir.json"
    bad_ir_path.write_text(json.dumps(ir_dict), encoding="utf-8")
    diag_out = e2e_workspace.workspace / "tamper_diags.json"

    res = cli_runner(["parse", "validate", str(bad_ir_path), "--out", str(diag_out)])
    _assert_cli_error(res, ErrorCode.VERIFICATION_GATE_FAILED, CliExitCode.VALIDATION_ERROR, 422)
    codes = _diag_codes_from_validate_envelope(res)
    assert "CANONICAL_HASH_MISMATCH" in codes
    assert "PAGE_LENGTH_MISMATCH" in codes
    assert diag_out.is_file()


def test_f04_t2_cli_validate_invalid_page_span_half_null_and_inverted_bounds(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F04-T2-02: invalid PageSpan (char_start=0, char_end=None or char_start>char_end) fails exit 2."""
    from pydantic import ValidationError

    from opendv.models.document_ir import PageSpan

    with pytest.raises(ValidationError):
        PageSpan(page_number=1, char_start=0, char_end=None)
    with pytest.raises(ValidationError):
        PageSpan(page_number=1, char_start=10, char_end=5)

    min_pdf = make_minimal_pdf(e2e_workspace.workspace / "span_dto.pdf")
    ir_path = e2e_workspace.workspace / "span_dto_ir.json"
    base_ir = _extract_to_json(cli_runner, min_pdf, ir_path)

    for label, span_patch in (
        ("half_null", {"page_number": 1, "char_start": 0, "char_end": None, "bbox": None}),
        ("inverted", {"page_number": 1, "char_start": 10, "char_end": 5, "bbox": None}),
    ):
        mutated = json.loads(json.dumps(base_ir))
        mutated["blocks"][0]["spans"] = [span_patch]
        bad_path = e2e_workspace.workspace / f"bad_span_{label}.json"
        bad_path.write_text(json.dumps(mutated), encoding="utf-8")
        res = cli_runner(["parse", "validate", str(bad_path)])
        assert res.returncode == int(CliExitCode.VALIDATION_ERROR)
        assert res.envelope is not None
        assert res.envelope.ok is False
        assert res.envelope.error_code in {
            ErrorCode.VERIFICATION_GATE_FAILED,
            ErrorCode.SYNTAX_INVALID_ARGUMENT,
        }


def test_f04_t2_cli_validate_span_bounds_exceeds_page_and_oob_page(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F04-T2-03: span bounds beyond page length or missing page_number trigger 422 diagnostics."""
    min_pdf = make_minimal_pdf(e2e_workspace.workspace / "span_oob.pdf")
    ir_path = e2e_workspace.workspace / "span_oob_ir.json"
    ir_dict = _extract_to_json(cli_runner, min_pdf, ir_path)

    ir_dict["blocks"][0]["spans"] = [
        {"page_number": 1, "char_start": 0, "char_end": 999999, "bbox": None},
        {"page_number": 99, "char_start": None, "char_end": None, "bbox": None},
    ]
    bad_path = e2e_workspace.workspace / "span_oob_bad.json"
    bad_path.write_text(json.dumps(ir_dict), encoding="utf-8")

    res = cli_runner(["parse", "validate", str(bad_path)])
    _assert_cli_error(res, ErrorCode.VERIFICATION_GATE_FAILED, CliExitCode.VALIDATION_ERROR, 422)
    codes = _diag_codes_from_validate_envelope(res)
    assert "SPAN_BOUNDS_INVALID" in codes
    assert "SPAN_PAGE_OUT_OF_RANGE" in codes


def test_f04_t2_cli_validate_outline_and_block_integrity_errors(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F04-T2-04: duplicate block_id, non-monotonic reading_order, outline/page mismatches fail."""
    md_path = e2e_workspace.workspace / "integrity.md"
    md_path.write_text("# Heading One\n\nParagraph A.\n\nParagraph B.\n", encoding="utf-8")
    ir_path = e2e_workspace.workspace / "integrity_ir.json"
    ir_dict = _extract_to_json(cli_runner, md_path, ir_path)

    dup_blk = json.loads(json.dumps(ir_dict["blocks"][0]))
    ir_dict["blocks"][1]["block_id"] = dup_blk["block_id"]
    ir_dict["blocks"][1]["reading_order"] = 5
    ir_dict["blocks"][2]["reading_order"] = 1
    ir_dict["provenance"]["page_count"] = 99
    ir_dict["pages"].append(json.loads(json.dumps(ir_dict["pages"][0])))
    ir_dict["outline"].append(
        {
            "node_id": "bad_out",
            "title": "Ghost Section",
            "level": 1,
            "start_page": 99,
            "end_page": 99,
            "block_id": "nonexistent_block_id",
            "children": [],
        }
    )
    bad_path = e2e_workspace.workspace / "integrity_bad.json"
    bad_path.write_text(json.dumps(ir_dict), encoding="utf-8")

    res = cli_runner(["parse", "validate", str(bad_path)])
    _assert_cli_error(res, ErrorCode.VERIFICATION_GATE_FAILED, CliExitCode.VALIDATION_ERROR, 422)
    codes = _diag_codes_from_validate_envelope(res)
    assert "DUPLICATE_BLOCK_ID" in codes
    assert "READING_ORDER_NON_MONOTONIC" in codes
    assert "PROVENANCE_PAGE_COUNT_MISMATCH" in codes
    assert "DUPLICATE_PAGE_NUMBER" in codes
    assert "OUTLINE_PAGE_OUT_OF_RANGE" in codes
    assert "OUTLINE_BLOCK_MISSING" in codes


def test_f04_t2_cli_export_json_jsonl_without_out_rejected(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F04-T2-05: opendv --json parse export --format jsonl without --out fails with 400."""
    min_pdf = make_minimal_pdf(e2e_workspace.workspace / "jsonl_no_out.pdf")
    ir_path = e2e_workspace.workspace / "jsonl_no_out_ir.json"
    _extract_to_json(cli_runner, min_pdf, ir_path)

    res = cli_runner(["parse", "export", str(ir_path), "--target", "chunks", "--format", "jsonl"])
    _assert_cli_error(res, ErrorCode.SYNTAX_INVALID_ARGUMENT, CliExitCode.VALIDATION_ERROR, 400)


def test_f04_t2_cli_export_unknown_target_rejected(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F04-T2-06: unknown projection target fails with SYNTAX_INVALID_ARGUMENT (400)."""
    from opendv.models.document_ir import ParsedDocumentIR
    from opendv.service.parsing_svc import ParsingService

    min_pdf = make_minimal_pdf(e2e_workspace.workspace / "bad_target.pdf")
    ir_path = e2e_workspace.workspace / "bad_target_ir.json"
    ir_dict = _extract_to_json(cli_runner, min_pdf, ir_path)

    res = cli_runner(["parse", "export", str(ir_path), "--target", "bogus_target"])
    _assert_cli_error(res, ErrorCode.SYNTAX_INVALID_ARGUMENT, CliExitCode.VALIDATION_ERROR, 400)

    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])
    ir = ParsedDocumentIR.model_validate(ir_dict)
    with pytest.raises(OpenDVError) as exc_info:
        svc.export_ir(ir, target="bogus_target")  # type: ignore[arg-type]
    assert exc_info.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


# =============================================================================
# F05: Visual Render & Caps (Tier 1: 6 tests, Tier 2: 6 tests)
# =============================================================================


def test_f05_t1_cli_render_page_out_file_png_and_dimensions(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """F05-T1-01: render-page --out FILE.png writes valid PNG matching RenderPageResult metadata."""
    from opendv.models.document_ir import RenderPageResult

    h15_pdf = public_fixture("fed_h15_interest_rates.pdf")
    png_out = e2e_workspace.workspace / "page1.png"
    res = cli_runner(
        ["parse", "render-page", str(h15_pdf), "--page", "1", "--dpi", "150", "--out", str(png_out)]
    )
    assert res.returncode == 0 and res.envelope is not None and res.envelope.ok is True
    meta = RenderPageResult.model_validate(res.envelope.data)
    assert meta.page_number == 1
    assert meta.dpi == 150
    assert meta.redacted is False
    assert meta.ocr_used is False
    assert meta.output_path is not None
    assert Path(meta.output_path).resolve() == png_out.resolve()
    assert png_out.is_file()
    raw_png = png_out.read_bytes()
    assert raw_png[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(io.BytesIO(raw_png)) as img:
        assert img.size == (meta.width, meta.height)


def test_f05_t1_cli_render_page_inline_png_b64_without_out(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F05-T1-02: render-page without --out returns valid base64 PNG in RenderPageResult.png_b64."""
    from opendv.models.document_ir import RenderPageResult

    min_pdf = make_minimal_pdf(e2e_workspace.workspace / "inline_render.pdf")
    res = cli_runner(["parse", "render-page", str(min_pdf), "--page", "1"])
    assert res.returncode == 0 and res.envelope is not None and res.envelope.ok is True
    meta = RenderPageResult.model_validate(res.envelope.data)
    assert meta.output_path is None
    assert meta.png_b64 is not None and len(meta.png_b64) > 0
    decoded = base64.b64decode(meta.png_b64, validate=True)
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(io.BytesIO(decoded)) as img:
        assert img.size == (meta.width, meta.height)


def test_f05_t1_cli_render_page_dpi_300_ceiling_scales_dimensions(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F05-T1-03: render-page accepts dpi <= 300 and scales pixel dimensions proportionally."""
    from opendv.models.document_ir import RenderPageResult

    min_pdf = make_minimal_pdf(e2e_workspace.workspace / "dpi_scale.pdf")
    r72 = cli_runner(["parse", "render-page", str(min_pdf), "--page", "1", "--dpi", "72"])
    r300 = cli_runner(["parse", "render-page", str(min_pdf), "--page", "1", "--dpi", "300"])
    assert r72.returncode == 0 and r300.returncode == 0
    assert r72.envelope is not None and r300.envelope is not None
    m72 = RenderPageResult.model_validate(r72.envelope.data)
    m300 = RenderPageResult.model_validate(r300.envelope.data)
    assert m72.dpi == 72 and m300.dpi == 300
    assert m300.width >= m72.width * 3
    assert m300.height >= m72.height * 3


def test_f05_t1_cli_render_rotated_90_pdf_with_redact(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """F05-T1-04: /Rotate 90 PDF renders landscape PNG cleanly under --redact."""
    from opendv.models.document_ir import RenderPageResult

    g19_pdf = public_fixture("fed_g19_consumer_credit.pdf")
    out_png = e2e_workspace.workspace / "g19_rot90.png"
    res = cli_runner(
        ["parse", "render-page", str(g19_pdf), "--page", "1", "--redact", "--out", str(out_png)]
    )
    assert res.returncode == 0 and res.envelope is not None
    meta = RenderPageResult.model_validate(res.envelope.data)
    assert meta.redacted is True
    assert meta.width > meta.height
    assert out_png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_f05_t1_cli_inspect_scanned_page_image_block_count_and_approve_scan(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F05-T1-05: scanned pages track image_block_count and render under --approve-scan."""
    from opendv.models.document_ir import InspectResult, ParsedDocumentIR, RenderPageResult

    inst_pdf = (
        REPO_ROOT / "examples" / "sample_pdfs" / "institutional_scanned_schedule_ocr_test.pdf"
    )
    ins_res = cli_runner(["parse", "inspect", str(inst_pdf)])
    assert ins_res.returncode == 0 and ins_res.envelope is not None
    ins = InspectResult.model_validate(ins_res.envelope.data)
    assert ins.scanned_pages == [2]
    assert ins.digital_pages == [1]

    ext_res = cli_runner(["parse", "extract", str(inst_pdf)])
    assert ext_res.returncode == 0 and ext_res.envelope is not None
    ir = ParsedDocumentIR.model_validate(ext_res.envelope.data)
    assert ir.pages[1].needs_ocr is True
    assert ir.pages[1].image_block_count >= 1

    out_png = e2e_workspace.workspace / "approved_scan_p2.png"
    ren_res = cli_runner(
        [
            "parse",
            "render-page",
            str(inst_pdf),
            "--page",
            "2",
            "--approve-scan",
            "--out",
            str(out_png),
        ]
    )
    assert ren_res.returncode == 0 and ren_res.envelope is not None
    meta = RenderPageResult.model_validate(ren_res.envelope.data)
    assert meta.redacted is False
    assert out_png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_f05_t1_service_render_and_inspect_unconstrained_by_max_pages(
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """F05-T1-06: inspect ignores max_pages while extract enforces it; RenderPageResult has no bytes."""
    from opendv.models.document_ir import ParseOptions
    from opendv.service.parsing_svc import ParsingService

    svc = ParsingService(allowed_roots=[e2e_workspace.workspace, REPO_ROOT])
    h15_pdf = public_fixture("fed_h15_interest_rates.pdf")

    ins = svc.inspect_document(h15_pdf, options=ParseOptions(max_pages=2))
    assert ins.page_count == 3

    with pytest.raises(OpenDVError) as exc_info:
        svc.parse_document(h15_pdf, options=ParseOptions(max_pages=2))
    assert exc_info.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED

    png_bytes, result = svc.render_document_page(h15_pdf, 1, options=ParseOptions(dpi=100))
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    dumped = result.model_dump(mode="json")
    assert all(not isinstance(val, bytes) for val in dumped.values())
    assert result.spillover is None


def test_f05_t2_cli_render_page_dpi_above_300_rejected(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F05-T2-01: render-page with dpi=301 fails with SYNTAX_INVALID_ARGUMENT (400)."""
    min_pdf = make_minimal_pdf(e2e_workspace.workspace / "dpi301.pdf")
    res = cli_runner(["parse", "render-page", str(min_pdf), "--page", "1", "--dpi", "301"])
    _assert_cli_error(res, ErrorCode.SYNTAX_INVALID_ARGUMENT, CliExitCode.VALIDATION_ERROR, 400)


def test_f05_t2_cli_render_page_pixel_preflight_exceeds_40mp_rejected(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F05-T2-02: pre-render pixel budget >40,000,000 px aborts with ARCHIVE_SIZE_LIMIT_EXCEEDED."""
    huge_pdf = make_high_pixel_pdf(
        e2e_workspace.workspace / "huge_40mp.pdf",
        width_pt=4000.0,
        height_pt=4000.0,
    )
    res = cli_runner(["parse", "render-page", str(huge_pdf), "--page", "1", "--dpi", "150"])
    _assert_cli_error(res, ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED, CliExitCode.VALIDATION_ERROR, 413)


def test_f05_t2_cli_render_page_non_renderable_formats_rejected(
    cli_runner: Callable[..., CliResult],
    public_fixture: Callable[[str], Path],
) -> None:
    """F05-T2-03: render-page rejects .docx, .md, and .txt with SYNTAX_INVALID_ARGUMENT (400)."""
    for fname in ("aspose_merged_table.docx", "datavault_automate_dv_readme.md", "rfc4180_csv.txt"):
        res = cli_runner(["parse", "render-page", str(public_fixture(fname)), "--page", "1"])
        _assert_cli_error(res, ErrorCode.SYNTAX_INVALID_ARGUMENT, CliExitCode.VALIDATION_ERROR, 400)


def test_f05_t2_cli_render_page_zero_or_out_of_range_rejected(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F05-T2-04: render-page with --page 0 or --page > total_pages fails with 400."""
    min_pdf = make_minimal_pdf(e2e_workspace.workspace / "oob_page.pdf")
    for bad_page in ("0", "2"):
        res = cli_runner(["parse", "render-page", str(min_pdf), "--page", bad_page])
        _assert_cli_error(res, ErrorCode.SYNTAX_INVALID_ARGUMENT, CliExitCode.VALIDATION_ERROR, 400)


def test_f05_t2_service_render_page_timeout_ms_enforced(
    e2e_workspace: E2EWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F05-T2-05: hard timeout_ms during render-page maps to INTERNAL_SERVER_ERROR (500)."""
    from liteparse.types import ParseTimeoutError

    from opendv.models.document_ir import ParseOptions
    from opendv.service.parsing_svc import ParsingService

    min_pdf = make_minimal_pdf(e2e_workspace.workspace / "render_timeout.pdf")
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])

    def _raise_timeout(self: Any, *_args: Any, **_kwargs: Any) -> Any:
        raise ParseTimeoutError(
            "simulated 1ms timeout expiry",
            source=str(min_pdf),
            timeout=0.001,
        )

    monkeypatch.setattr("liteparse.LiteParse.parse", _raise_timeout)
    with pytest.raises(OpenDVError) as exc_info:
        svc.render_document_page(min_pdf, 1, options=ParseOptions(timeout_ms=1))
    assert exc_info.value.error_code == ErrorCode.INTERNAL_SERVER_ERROR
    assert exc_info.value.http_status == 500


def test_f05_t2_cli_render_unapproved_scanned_page_and_model_egress_blocked(
    cli_runner: Callable[..., CliResult],
    e2e_workspace: E2EWorkspace,
) -> None:
    """F05-T2-06: unapproved scan and MODEL_EGRESS scan without OCR fail UNREDACTED_SCAN_BLOCKED."""
    from opendv.models.document_ir import ParseOptions
    from opendv.service.parsing_svc import ParsingService

    scanned_pdf = make_scanned_image_pdf(e2e_workspace.workspace / "scanned_block.pdf")
    res = cli_runner(["parse", "render-page", str(scanned_pdf), "--page", "1"])
    _assert_cli_error(res, ErrorCode.UNREDACTED_SCAN_BLOCKED, CliExitCode.VALIDATION_ERROR, 422)

    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])
    with pytest.raises(OpenDVError) as exc_info:
        svc.render_document_page(
            scanned_pdf,
            1,
            sink_class="MODEL_EGRESS",
            redaction="on",
            approve_scan=True,
            options=ParseOptions(ocr=False),
        )
    assert exc_info.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED
