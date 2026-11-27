"""Tier 1 & Tier 2 E2E suite for F12: real-data public internet corpus and repo UAT fixtures."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from opendv.models.errors import CliExitCode, ErrorCode, OpenDVError
from tests.parsing.e2e.conftest import (
    PUBLIC_FIXTURES_DIR,
    REPO_ROOT,
    VIRTUAL_PAGE_SPLIT_CHARS,
    call_mcp_tool_json,
    open_mcp_session,
    public_fixture_path,
    query_ndjson_with_duckdb,
)

GOLDEN_BUNDLE_PATH = REPO_ROOT / "tests" / "golden_v2" / "am_core_bundle.core.opendv.zip"
PUBLIC_AM_DIR = REPO_ROOT / "data" / "public_am"
SAMPLE_PDFS_DIR = REPO_ROOT / "examples" / "sample_pdfs"
SRC_MATRIX_DIR = REPO_ROOT / "examples" / "scenarios" / "src_matrix"

PUBLIC_PDF_FIXTURES: tuple[str, ...] = (
    "fed_g19_consumer_credit.pdf",
    "fed_h15_interest_rates.pdf",
    "irs_fw9.pdf",
    "irs_f1120.pdf",
    "acl_2024_findings_eacl_34.pdf",
)

PUBLIC_VALID_DOCX_FIXTURES: tuple[str, ...] = (
    "irs_soi_21incddocguide.docx",
    "irs_soi_23zpdoc.docx",
    "aspose_merged_table.docx",
    "aspose_formatted_elements.docx",
)

PUBLIC_MARKDOWN_FIXTURES: tuple[str, ...] = (
    "fivetran_dbt_stripe_readme.md",
    "fivetran_dbt_quickbooks_readme.md",
    "fivetran_dbt_log_readme.md",
    "datavault_automate_dv_readme.md",
)

PUBLIC_TEXT_FIXTURES: tuple[str, ...] = (
    "rfc4180_csv.txt",
    "rfc8259_json.txt",
    "rfc6749_oauth2.txt",
    "rfc9110_http_semantics.txt",
)

NON_ENCRYPTED_PUBLIC_FIXTURES: tuple[str, ...] = (
    *PUBLIC_PDF_FIXTURES,
    *PUBLIC_VALID_DOCX_FIXTURES,
    *PUBLIC_MARKDOWN_FIXTURES,
    *PUBLIC_TEXT_FIXTURES,
)


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65_536):
            hasher.update(chunk)
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# Tier 1 — Happy-Path Public Internet Corpus & Repo UAT Coverage (F12)
# ---------------------------------------------------------------------------


def test_t1_public_manifest_all_18_entries_integrity(
    public_fixtures_manifest: dict[str, dict[str, Any]],
) -> None:
    """All 18 MANIFEST.json entries exist on disk, are non-empty, and match SHA-256."""
    assert len(public_fixtures_manifest) == 18
    expected_names = {*NON_ENCRYPTED_PUBLIC_FIXTURES, "aspose_encrypted_ole2.docx"}
    assert set(public_fixtures_manifest) == expected_names

    for name, entry in public_fixtures_manifest.items():
        fixture_path = public_fixture_path(name)
        raw_bytes = fixture_path.read_bytes()
        assert len(raw_bytes) > 0, f"Public fixture {name} must not be empty"
        assert len(raw_bytes) == entry["byte_size"], f"Byte size mismatch for {name}"
        assert hashlib.sha256(raw_bytes).hexdigest() == entry["sha256"], (
            f"SHA-256 mismatch for {name}"
        )


@pytest.mark.slow
@pytest.mark.parametrize("fixture_name", NON_ENCRYPTED_PUBLIC_FIXTURES)
def test_t1_all_17_non_encrypted_public_fixtures_cli_pipeline(
    fixture_name: str,
    cli_runner: Any,
    e2e_workspace: Any,
    public_fixtures_manifest: dict[str, dict[str, Any]],
) -> None:
    """Every non-encrypted public fixture succeeds across inspect/extract/validate/export."""
    fixture_path = public_fixture_path(fixture_name)
    meta = public_fixtures_manifest[fixture_name]
    expected_format = meta["format_id"]
    stem = fixture_path.stem

    ins = cli_runner(["parse", "inspect", str(fixture_path)], timeout=120.0)
    assert ins.returncode == int(CliExitCode.OK), ins.stderr or ins.stdout
    assert ins.envelope is not None and ins.envelope.ok is True
    assert isinstance(ins.envelope.data, dict)
    assert ins.envelope.data["format_id"] == expected_format
    assert ins.envelope.data["page_count"] >= 1
    assert ins.envelope.data["binary_sha256"] == meta["sha256"]

    ir_out = e2e_workspace.workspace / f"{stem}_ir.json"
    ext = cli_runner(
        ["parse", "extract", str(fixture_path), "--out", str(ir_out)],
        timeout=120.0,
    )
    assert ext.returncode == int(CliExitCode.OK), ext.stderr or ext.stdout
    assert ext.envelope is not None and ext.envelope.ok is True
    assert ir_out.is_file()

    val = cli_runner(["parse", "validate", str(ir_out)], timeout=120.0)
    assert val.returncode == int(CliExitCode.OK), (
        f"parse validate failed for {fixture_name}: {val.stdout}"
    )
    assert val.envelope is not None and val.envelope.ok is True

    dmap_out = e2e_workspace.workspace / f"{stem}_document_map.json"
    exp_dm = cli_runner(
        ["parse", "export", str(ir_out), "--target", "document_map", "--out", str(dmap_out)],
        timeout=120.0,
    )
    assert exp_dm.returncode == int(CliExitCode.OK), exp_dm.stderr or exp_dm.stdout
    dm_payload = json.loads(dmap_out.read_text(encoding="utf-8"))
    assert "document_map" in dm_payload
    assert dm_payload["document_map"]["format"] == expected_format
    assert dm_payload["document_map"]["page_count"] == ins.envelope.data["page_count"]

    md_out = e2e_workspace.workspace / f"{stem}.md"
    exp_md = cli_runner(
        ["parse", "export", str(ir_out), "--target", "markdown", "--out", str(md_out)],
        timeout=120.0,
    )
    assert exp_md.returncode == int(CliExitCode.OK), exp_md.stderr or exp_md.stdout
    assert len(md_out.read_text(encoding="utf-8").strip()) > 0

    chunks_out = e2e_workspace.workspace / f"{stem}_chunks.jsonl"
    exp_chunks = cli_runner(
        [
            "parse",
            "export",
            str(ir_out),
            "--target",
            "chunks",
            "--format",
            "jsonl",
            "--out",
            str(chunks_out),
        ],
        timeout=120.0,
    )
    assert exp_chunks.returncode == int(CliExitCode.OK), exp_chunks.stderr or exp_chunks.stdout
    rows = query_ndjson_with_duckdb(chunks_out, "SELECT COUNT(*) AS cnt FROM chunks")
    assert rows[0]["cnt"] >= 1

    e2e_workspace.assert_no_workspace_opendv_dir()


@pytest.mark.slow
def test_t1_public_pdfs_render_rotation_and_objstm(
    cli_runner: Any,
    e2e_workspace: Any,
    public_fixture: Any,
) -> None:
    """Render page 1 across all 5 public PDFs, verifying /Rotate 90 landscape & ObjStm PDFs."""
    expected_pages = {
        "fed_g19_consumer_credit.pdf": 3,
        "fed_h15_interest_rates.pdf": 3,
        "irs_fw9.pdf": 6,
        "irs_f1120.pdf": 6,
        "acl_2024_findings_eacl_34.pdf": 13,
    }
    for name, page_count in expected_pages.items():
        pdf_path = public_fixture(name)
        ir_out = e2e_workspace.workspace / f"{pdf_path.stem}.ir.json"
        ext = cli_runner(["parse", "extract", str(pdf_path), "--out", str(ir_out)], timeout=120.0)
        assert ext.returncode == int(CliExitCode.OK), ext.stdout
        ir_data = json.loads(ir_out.read_text(encoding="utf-8"))
        assert len(ir_data["pages"]) == page_count

        if name == "fed_g19_consumer_credit.pdf":
            for page in ir_data["pages"]:
                assert page["width_pt"] > page["height_pt"], (
                    "Expected landscape /Rotate 90 viewport width_pt > height_pt"
                )
            assert "G.19" in ir_data["pages"][0]["canonical_text"]
        elif name == "acl_2024_findings_eacl_34.pdf":
            assert "Findings of the Association" in ir_data["pages"][0]["canonical_text"]
            assert len(ir_data["blocks"]) >= 100
        elif name == "irs_f1120.pdf":
            table_blocks = [b for b in ir_data["blocks"] if b["kind"] == "table"]
            assert len(table_blocks) >= 10

        png_out = e2e_workspace.workspace / f"{pdf_path.stem}_p1.png"
        rnd = cli_runner(
            ["parse", "render-page", str(pdf_path), "--page", "1", "--out", str(png_out)],
            timeout=120.0,
        )
        assert rnd.returncode == int(CliExitCode.OK), f"render-page failed on {name}: {rnd.stdout}"
        assert png_out.is_file()
        with Image.open(png_out) as img:
            width, height = img.size
            assert width > 0 and height > 0
            if name == "fed_g19_consumer_credit.pdf":
                assert width > height, (
                    f"Expected rendered landscape PNG for /Rotate 90, got {width}x{height}"
                )

    e2e_workspace.assert_no_workspace_opendv_dir()


@pytest.mark.slow
def test_t1_public_docx_merged_tables_and_headings(
    cli_runner: Any,
    e2e_workspace: Any,
    public_fixture: Any,
) -> None:
    """Verify rectangular gridSpan/vMerge tables, 175-row IRS table, and DOCX headings."""
    aspose_merged = public_fixture("aspose_merged_table.docx")
    merged_ir_path = e2e_workspace.workspace / "merged.ir.json"
    res = cli_runner(["parse", "extract", str(aspose_merged), "--out", str(merged_ir_path)])
    assert res.returncode == int(CliExitCode.OK), res.stdout
    merged_ir = json.loads(merged_ir_path.read_text(encoding="utf-8"))
    merged_tables = [b for b in merged_ir["blocks"] if b["kind"] == "table"]
    assert len(merged_tables) == 1
    assert len(merged_tables[0]["table_headers"]) == 4
    assert len(merged_tables[0]["table_rows"]) == 11
    for row in merged_tables[0]["table_rows"]:
        assert len(row) == 4, f"Merged table row lost rectangular width: {row}"

    soi_21 = public_fixture("irs_soi_21incddocguide.docx")
    soi_chunks_path = e2e_workspace.workspace / "soi21_chunks.jsonl"
    soi_ir_path = e2e_workspace.workspace / "soi21.ir.json"
    assert cli_runner(["parse", "extract", str(soi_21), "--out", str(soi_ir_path)]).returncode == 0
    soi_ir = json.loads(soi_ir_path.read_text(encoding="utf-8"))
    soi_tables = [b for b in soi_ir["blocks"] if b["kind"] == "table"]
    assert len(soi_tables) == 1
    assert soi_tables[0]["table_headers"] == [
        "VARIABLE NAME",
        "DESCRIPTION",
        "VALUE/LINE REFERENCE",
        "TYPE",
    ]
    assert len(soi_tables[0]["table_rows"]) == 174
    assert (
        cli_runner(
            [
                "parse",
                "export",
                str(soi_ir_path),
                "--target",
                "chunks",
                "--format",
                "jsonl",
                "--out",
                str(soi_chunks_path),
            ]
        ).returncode
        == 0
    )
    hits = query_ndjson_with_duckdb(
        soi_chunks_path,
        "SELECT COUNT(*) AS cnt FROM chunks WHERE text LIKE '%STATEFIPS%'",
    )
    assert hits[0]["cnt"] >= 1

    fmt_docx = public_fixture("aspose_formatted_elements.docx")
    fmt_ir_path = e2e_workspace.workspace / "fmt.ir.json"
    assert cli_runner(["parse", "extract", str(fmt_docx), "--out", str(fmt_ir_path)]).returncode == 0
    fmt_ir = json.loads(fmt_ir_path.read_text(encoding="utf-8"))
    assert len(fmt_ir["outline"]) >= 3
    fmt_tables = [b for b in fmt_ir["blocks"] if b["kind"] == "table"]
    assert fmt_tables[0]["table_headers"] == ["#", "Name 1", "Name 2", "Name 3"]


@pytest.mark.slow
def test_t1_public_markdown_and_text_24k_splitting_and_bom(
    cli_runner: Any,
    e2e_workspace: Any,
    public_fixture: Any,
) -> None:
    """Verify >24k virtual splitting, RFC slice counts, and UTF-8 BOM stripping."""
    expected_slices = {
        "datavault_automate_dv_readme.md": 1,
        "fivetran_dbt_log_readme.md": 1,
        "fivetran_dbt_stripe_readme.md": 2,
        "fivetran_dbt_quickbooks_readme.md": 2,
        "rfc4180_csv.txt": 1,
        "rfc8259_json.txt": 2,
        "rfc6749_oauth2.txt": 6,
        "rfc9110_http_semantics.txt": 20,
    }
    for name, expected_count in expected_slices.items():
        path = public_fixture(name)
        ir_path = e2e_workspace.workspace / f"{path.stem}.ir.json"
        ext = cli_runner(["parse", "extract", str(path), "--out", str(ir_path)], timeout=120.0)
        assert ext.returncode == int(CliExitCode.OK), ext.stdout
        ir_data = json.loads(ir_path.read_text(encoding="utf-8"))
        pages = ir_data["pages"]
        assert len(pages) == expected_count, (
            f"{name}: expected {expected_count} virtual slices, got {len(pages)}"
        )
        for page in pages:
            assert page["slice_kind"] == "virtual_section_page"
            assert page["renderable"] is False
            assert len(page["canonical_text"]) <= VIRTUAL_PAGE_SPLIT_CHARS
        if name == "rfc9110_http_semantics.txt":
            assert not pages[0]["canonical_text"].startswith("\ufeff"), (
                "UTF-8 BOM must be stripped from canonical_text on first slice"
            )
            chunks_path = e2e_workspace.workspace / "rfc9110_chunks.jsonl"
            exp = cli_runner(
                [
                    "parse",
                    "export",
                    str(ir_path),
                    "--target",
                    "chunks",
                    "--format",
                    "jsonl",
                    "--out",
                    str(chunks_path),
                ],
                timeout=120.0,
            )
            assert exp.returncode == int(CliExitCode.OK), exp.stdout
            rows = query_ndjson_with_duckdb(
                chunks_path,
                "SELECT COUNT(*) AS cnt, MAX(LENGTH(text)) AS max_len FROM chunks",
            )
            assert rows[0]["cnt"] >= 2_000
            assert rows[0]["max_len"] <= 4_000


@pytest.mark.slow
def test_t1_repo_am_sample_pdfs_src_matrix_and_golden_bundle(
    cli_runner: Any,
    e2e_workspace: Any,
) -> None:
    """Verify repo UAT fixtures (public_am, sample_pdfs, src_matrix, golden bundle)."""
    from opendv.models.grounding import GroundingPolicy, OntologyGroundReq
    from opendv.service import OpenDVService
    from opendv.service.workflows._vault_helpers import load_verifier_inputs

    # 1. data/public_am/
    am_pdf = PUBLIC_AM_DIR / "am_custody_and_voting_guide.pdf"
    am_ins = cli_runner(["parse", "inspect", str(am_pdf)])
    assert am_ins.returncode == 0 and am_ins.envelope is not None
    assert am_ins.envelope.data["page_count"] == 6
    assert 4 in am_ins.envelope.data["scanned_pages"]

    am_ir_path = e2e_workspace.workspace / "am_guide.ir.json"
    assert cli_runner(["parse", "extract", str(am_pdf), "--out", str(am_ir_path)]).returncode == 0
    am_ir = json.loads(am_ir_path.read_text(encoding="utf-8"))
    assert len(am_ir["outline"]) == 6
    assert am_ir["pages"][4]["canonical_text"].count("CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE") == 2

    am_md = PUBLIC_AM_DIR / "am_stewardship_domain_notes.md"
    am_md_ir_path = e2e_workspace.workspace / "am_notes.ir.json"
    assert cli_runner(["parse", "extract", str(am_md), "--out", str(am_md_ir_path)]).returncode == 0
    am_md_ir = json.loads(am_md_ir_path.read_text(encoding="utf-8"))
    assert am_md_ir["pages"][0]["canonical_text_sha256"] == (
        "ca5f096a54bb4f8cb3504fefc02ca05cee9c374aaaeb4dd47a17217a9de4dbb6"
    )
    assert "StewardshipEngagementCase" in am_md_ir["pages"][0]["canonical_text"]

    # 2. examples/sample_pdfs/
    scan_pdf = SAMPLE_PDFS_DIR / "institutional_scanned_schedule_ocr_test.pdf"
    scan_ins = cli_runner(["parse", "inspect", str(scan_pdf)])
    assert scan_ins.returncode == 0 and scan_ins.envelope is not None
    assert scan_ins.envelope.data["page_count"] == 2
    assert scan_ins.envelope.data["digital_pages"] == [1]
    assert scan_ins.envelope.data["scanned_pages"] == [2]
    scan_ir_path = e2e_workspace.workspace / "scan_sched.ir.json"
    assert cli_runner(["parse", "extract", str(scan_pdf), "--out", str(scan_ir_path)]).returncode == 0
    scan_ir = json.loads(scan_ir_path.read_text(encoding="utf-8"))
    assert "US Treasury Bills" in scan_ir["pages"][0]["canonical_text"]
    assert scan_ir["pages"][1]["needs_ocr"] is True

    large_samples = (
        ("nyfed_sr506_repo_infrastructure.pdf", 55, "1-5", "Tri-Party Repo"),
        ("fdic_section3_2_loans_credit_risk.pdf", 88, "1-3", "RMS Manual of Examination Policies"),
        ("occ_comptrollers_handbook_rating_credit_risk.pdf", 69, "1-3", "Rating Credit Risk"),
    )
    for fname, total_pages, window, marker in large_samples:
        p = SAMPLE_PDFS_DIR / fname
        ins_res = cli_runner(["parse", "inspect", str(p)], timeout=120.0)
        assert ins_res.returncode == 0 and ins_res.envelope is not None
        assert ins_res.envelope.data["page_count"] == total_pages
        w_ir_path = e2e_workspace.workspace / f"{p.stem}.ir.json"
        w_ext = cli_runner(
            ["parse", "extract", str(p), "--pages", window, "--out", str(w_ir_path)],
            timeout=120.0,
        )
        assert w_ext.returncode == 0, w_ext.stdout
        w_ir = json.loads(w_ir_path.read_text(encoding="utf-8"))
        combined = "\n".join(pg["canonical_text"] for pg in w_ir["pages"])
        assert marker in combined

    # 3. examples/scenarios/src_matrix/ (PDF + CRLF/LF canonical SHA-256 parity)
    msa_pdf = SRC_MATRIX_DIR / "enterprise_msa_addendum_2026.pdf"
    assert cli_runner(["parse", "inspect", str(msa_pdf)]).returncode == 0
    lf_ir_path = e2e_workspace.workspace / "msa_lf.ir.json"
    crlf_ir_path = e2e_workspace.workspace / "msa_crlf.ir.json"
    assert (
        cli_runner(
            [
                "parse",
                "extract",
                str(SRC_MATRIX_DIR / "enterprise_msa_addendum_2026.txt"),
                "--out",
                str(lf_ir_path),
            ]
        ).returncode
        == 0
    )
    assert (
        cli_runner(
            [
                "parse",
                "extract",
                str(SRC_MATRIX_DIR / "enterprise_msa_addendum_2026_crlf.txt"),
                "--out",
                str(crlf_ir_path),
            ]
        ).returncode
        == 0
    )
    lf_sha = json.loads(lf_ir_path.read_text(encoding="utf-8"))["pages"][0]["canonical_text_sha256"]
    crlf_sha = json.loads(crlf_ir_path.read_text(encoding="utf-8"))["pages"][0][
        "canonical_text_sha256"
    ]
    assert lf_sha == crlf_sha

    e2e_workspace.assert_no_workspace_opendv_dir()

    # 4. Golden .core.opendv.zip offline DocumentMap continuity
    svc = OpenDVService(e2e_workspace.workspace, state_dir=e2e_workspace.state_dir)
    rep = svc.import_bundle(
        GOLDEN_BUNDLE_PATH,
        conflict="fail",
        enforce_workspace_containment=False,
    )
    assert {"READY_OFFLINE", "METADATA_ONLY_BINARY_ABSENT"}.issubset(set(rep.readiness_flags))
    ground_resp = asyncio.run(
        svc.discovery.ground_ontology(
            OntologyGroundReq(
                project_id="am-golden-w1",
                policy=GroundingPolicy(mode="deterministic_only", skip_reason="f12_offline_e2e"),
            )
        )
    )
    assert len(ground_resp.tables) >= 1
    assert sum(len(t.columns) for t in ground_resp.tables) >= 1
    _ont, _profs, _cl, _lock, _cert, _waivers, vctx = load_verifier_inputs(
        svc.state_manager,
        e2e_workspace.workspace,
        "am-golden-w1",
    )
    assert vctx.metadata_only_binary_absent is True
    assert {dm.doc_id for dm in vctx.document_maps} == {"src-ded3719416", "src-7c0663dfb9"}


# ---------------------------------------------------------------------------
# Tier 2 — Negative Paths, Boundary Limits & MCP/Redaction on Public Corpus
# ---------------------------------------------------------------------------


def test_t2_encrypted_ole2_docx_and_corrupt_pdf_fail_loud(
    cli_runner: Any,
    e2e_workspace: Any,
    public_fixture: Any,
) -> None:
    """Encrypted OLE2 .docx and corrupt repo PDF fail with VERIFICATION_GATE_FAILED (422)."""
    bad_inputs = (
        public_fixture("aspose_encrypted_ole2.docx"),
        SRC_MATRIX_DIR / "corrupt_document.pdf",
    )
    for bad_path in bad_inputs:
        for subcmd in ("inspect", "extract"):
            res = cli_runner(["parse", subcmd, str(bad_path)])
            assert res.returncode == int(CliExitCode.VALIDATION_ERROR)
            assert res.envelope is not None and res.envelope.ok is False
            assert res.envelope.error_code == ErrorCode.VERIFICATION_GATE_FAILED
            assert res.envelope.problem is not None
            assert res.envelope.problem.status == 422
            assert len(res.envelope.problem.detail.strip()) > 0
    e2e_workspace.assert_no_workspace_opendv_dir()


@pytest.mark.parametrize(
    "fixture_name",
    ("aspose_formatted_elements.docx", "datavault_automate_dv_readme.md", "rfc4180_csv.txt"),
)
def test_t2_render_page_rejected_on_non_renderable_public_docs(
    fixture_name: str,
    cli_runner: Any,
    e2e_workspace: Any,
) -> None:
    """render-page rejects non-renderable public .docx, .md, and .txt with exit 2."""
    path = public_fixture_path(fixture_name)
    res = cli_runner(["parse", "render-page", str(path), "--page", "1"])
    assert res.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert res.envelope is not None and res.envelope.ok is False
    assert res.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT
    e2e_workspace.assert_no_workspace_opendv_dir()


def test_t2_public_corpus_oob_pages_vs_max_pages_bounds(
    cli_runner: Any,
    e2e_workspace: Any,
    public_fixture: Any,
) -> None:
    """Out-of-bounds --pages raises SYNTAX_INVALID_ARGUMENT; >max_pages extract raises 413."""
    g19 = public_fixture("fed_g19_consumer_credit.pdf")
    oob_single = cli_runner(["parse", "extract", str(g19), "--pages", "4"])
    assert oob_single.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert oob_single.envelope is not None
    assert oob_single.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    acl = public_fixture("acl_2024_findings_eacl_34.pdf")
    oob_range = cli_runner(["parse", "extract", str(acl), "--pages", "1-20"])
    assert oob_range.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert oob_range.envelope is not None
    assert oob_range.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    too_many = cli_runner(["parse", "extract", str(acl), "--max-pages", "5"])
    assert too_many.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert too_many.envelope is not None
    assert too_many.envelope.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED
    assert too_many.envelope.problem is not None and too_many.envelope.problem.status == 413

    ins_ok = cli_runner(["parse", "inspect", str(acl), "--max-pages", "5"])
    assert ins_ok.returncode == int(CliExitCode.OK)
    assert ins_ok.envelope is not None and ins_ok.envelope.data is not None
    assert ins_ok.envelope.data["page_count"] == 13
    e2e_workspace.assert_no_workspace_opendv_dir()


def test_t2_unapproved_scanned_pages_blocked_then_approve_scan_succeeds(
    cli_runner: Any,
    e2e_workspace: Any,
) -> None:
    """Unapproved scanned pages fail with UNREDACTED_SCAN_BLOCKED; --approve-scan succeeds."""
    targets = (
        (PUBLIC_AM_DIR / "am_custody_and_voting_guide.pdf", 4),
        (SAMPLE_PDFS_DIR / "institutional_scanned_schedule_ocr_test.pdf", 2),
    )
    for pdf_path, page_num in targets:
        blocked = cli_runner(["parse", "render-page", str(pdf_path), "--page", str(page_num)])
        assert blocked.returncode == int(CliExitCode.VALIDATION_ERROR)
        assert blocked.envelope is not None and blocked.envelope.ok is False
        assert blocked.envelope.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

        out_png = e2e_workspace.workspace / f"{pdf_path.stem}_p{page_num}.png"
        approved = cli_runner(
            [
                "parse",
                "render-page",
                str(pdf_path),
                "--page",
                str(page_num),
                "--approve-scan",
                "--out",
                str(out_png),
            ]
        )
        assert approved.returncode == int(CliExitCode.OK), approved.stdout
        assert out_png.is_file()
        with Image.open(out_png) as img:
            assert img.size[0] > 0 and img.size[1] > 0
    e2e_workspace.assert_no_workspace_opendv_dir()


def test_t2_public_corpus_mcp_parsing_profile_spillover_and_error(
    e2e_workspace: Any,
    public_fixture: Any,
) -> None:
    """MCP --profile parsing handles spillover, redaction, and encrypted DOCX errors cleanly."""
    spill_dir = e2e_workspace.workspace / "mcp_spill"

    async def _run() -> None:
        async with open_mcp_session(
            "parsing",
            workspace=e2e_workspace,
            allow_roots=[PUBLIC_FIXTURES_DIR],
            max_inline_bytes=16_384,
            spill_dir=spill_dir,
        ) as (session, tools):
            assert len(tools) == 6
            fw9 = public_fixture("irs_fw9.pdf")
            is_err, ins_data, _ = await call_mcp_tool_json(
                session, "parse_inspect", {"uri": str(fw9)}
            )
            assert not is_err and ins_data["page_count"] == 6

            stripe_md = public_fixture("fivetran_dbt_stripe_readme.md")
            is_err, ext_md, _ = await call_mcp_tool_json(
                session, "parse_extract", {"uri": str(stripe_md), "redact": True}
            )
            assert not is_err
            if isinstance(ext_md, dict) and ext_md.get("spilled") is True:
                md_spill = Path(ext_md["spill_path"])
                assert md_spill.is_file()
                assert md_spill.parent == spill_dir
                assert _sha256_file(md_spill) == ext_md["sha256"]
                ext_md_payload = json.loads(md_spill.read_text(encoding="utf-8"))
            else:
                ext_md_payload = ext_md
            assert ext_md_payload["provenance"]["redacted"] is True

            rfc9110 = public_fixture("rfc9110_http_semantics.txt")
            is_err, ext_rfc, _ = await call_mcp_tool_json(
                session, "parse_extract", {"uri": str(rfc9110), "redact": True}
            )
            if isinstance(ext_rfc, dict) and ext_rfc.get("spilled") is True:
                ir_spill = Path(ext_rfc["spill_path"])
                assert ir_spill.is_file()
                is_err, exp_ref, _ = await call_mcp_tool_json(
                    session,
                    "parse_export_ir",
                    {
                        "target": "chunks",
                        "ir_path": str(ir_spill),
                        "format": "jsonl",
                        "redact": True,
                    },
                )
            else:
                is_err, exp_ref, _ = await call_mcp_tool_json(
                    session,
                    "parse_export_ir",
                    {"target": "chunks", "ir": ext_rfc, "format": "jsonl", "redact": True},
                )
            assert not is_err
            assert exp_ref["spilled"] is True
            assert exp_ref["format"] == "jsonl"
            spill_file = Path(exp_ref["spill_path"])
            assert spill_file.parent == spill_dir
            assert _sha256_file(spill_file) == exp_ref["sha256"]
            rows = query_ndjson_with_duckdb(spill_file, "SELECT COUNT(*) AS cnt FROM chunks")
            assert rows[0]["cnt"] >= 2_000

            enc_docx = public_fixture("aspose_encrypted_ole2.docx")
            is_err, _, prob = await call_mcp_tool_json(
                session, "parse_extract", {"uri": str(enc_docx)}
            )
            assert is_err is True and prob is not None
            assert prob.error_code == ErrorCode.VERIFICATION_GATE_FAILED

            # Session remains healthy after tool error
            is_err, spec_data, _ = await call_mcp_tool_json(session, "parse_spec", {})
            assert not is_err and "pdf" in spec_data["formats"]

    asyncio.run(_run())
    e2e_workspace.assert_no_workspace_opendv_dir()


def test_t2_golden_core_bundle_raw_read_raises_source_binary_absent(
    e2e_workspace: Any,
) -> None:
    """Imported .core.opendv.zip raises SOURCE_BINARY_ABSENT (409) on raw page read."""
    from opendv.service import OpenDVService

    svc = OpenDVService(e2e_workspace.workspace, state_dir=e2e_workspace.state_dir)
    svc.import_bundle(
        GOLDEN_BUNDLE_PATH,
        conflict="fail",
        enforce_workspace_containment=False,
    )
    dmaps = svc.list_document_maps("am-golden-w1")
    assert len(dmaps) == 2

    with pytest.raises(OpenDVError) as exc_info:
        svc.get_document_pages("am-golden-w1", "src-7c0663dfb9")
    assert exc_info.value.error_code == ErrorCode.SOURCE_BINARY_ABSENT


def test_t2_public_corpus_cross_format_grounding_and_project_doc_rejection(
    cli_runner: Any,
    e2e_workspace: Any,
    public_fixture: Any,
) -> None:
    """ontology ground --input --doc works across all 4 formats; --project --doc rejects."""
    req_path = e2e_workspace.workspace / "ground_req.json"
    req_path.write_text(
        json.dumps(
            {
                "tables": [
                    {
                        "table_id": "tbl_custody",
                        "table_name": "custody_holdings",
                        "columns": [
                            {"column_name": "cusip", "data_type": "VARCHAR", "is_pk": True},
                            {"column_name": "settlement_date", "data_type": "DATE"},
                        ],
                    }
                ],
                "global_steward_notes": ["Steward note baseline"],
                "policy": {"mode": "deterministic_only", "skip_reason": "f12_cross_format"},
            }
        ),
        encoding="utf-8",
    )
    res = cli_runner(
        [
            "ontology",
            "ground",
            "--input",
            str(req_path),
            "--doc",
            str(public_fixture("fed_g19_consumer_credit.pdf")),
            "--doc",
            str(public_fixture("aspose_formatted_elements.docx")),
            "--doc",
            str(PUBLIC_AM_DIR / "am_stewardship_domain_notes.md"),
            "--doc",
            str(public_fixture("rfc4180_csv.txt")),
        ]
    )
    assert res.returncode == int(CliExitCode.OK), res.stderr or res.stdout
    assert res.envelope is not None and res.envelope.ok is True
    assert len(res.envelope.data["tables"]) >= 1
    assert sum(len(t["columns"]) for t in res.envelope.data["tables"]) >= 1
    assert "<MASKED_PII>" not in res.stdout

    rej = cli_runner(
        [
            "ontology",
            "ground",
            "--project",
            "some-proj",
            "--doc",
            str(public_fixture("rfc4180_csv.txt")),
        ]
    )
    assert rej.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert rej.envelope is not None
    assert rej.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT
    e2e_workspace.assert_no_workspace_opendv_dir()
