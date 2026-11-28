"""Tier 4 E2E suite: 8 real-world end-to-end application scenarios across F01..F12."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import stat
import sys
import zipfile
from pathlib import Path
from typing import Any

import docx
import pytest
from PIL import Image

from opendv.models.errors import CliExitCode, ErrorCode, OpenDVError
from tests.parsing.e2e.conftest import (
    PUBLIC_FIXTURES_DIR,
    REPO_ROOT,
    VIRTUAL_PAGE_SPLIT_CHARS,
    call_mcp_tool_json,
    make_boundary_pii_text_file,
    make_minimal_pdf,
    make_pii_multi_token_pdf,
    make_scanned_image_pdf,
    make_xxe_doctype_docx,
    make_zip_bomb_entry_count_docx,
    make_zip_bomb_ratio_docx,
    open_mcp_session,
    query_ndjson_with_duckdb,
)

GOLDEN_BUNDLE_PATH = REPO_ROOT / "tests" / "golden_v2" / "am_core_bundle.core.opendv.zip"
PUBLIC_AM_DIR = REPO_ROOT / "data" / "public_am"
SAMPLE_SCANNED_PDF = (
    REPO_ROOT / "examples" / "sample_pdfs" / "institutional_scanned_schedule_ocr_test.pdf"
)
CORRUPT_REPO_PDF = REPO_ROOT / "examples" / "scenarios" / "src_matrix" / "corrupt_document.pdf"


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65_536):
            hasher.update(chunk)
    return hasher.hexdigest()


def _count_black_pixels(png_bytes: bytes) -> int:
    with Image.open(io.BytesIO(png_bytes)) as img:
        rgb = img.convert("RGB")
        raw = rgb.tobytes()
        return sum(1 for i in range(0, len(raw), 3) if raw[i : i + 3] == b"\x00\x00\x00")


def _make_symlink_member_docx(path: Path) -> Path:
    doc = docx.Document()
    doc.add_paragraph("Symlink zip member trap.")
    buf = io.BytesIO()
    doc.save(buf)
    with (
        zipfile.ZipFile(io.BytesIO(buf.getvalue()), "r") as src,
        zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as dst,
    ):
        for info in src.infolist():
            dst.writestr(info, src.read(info.filename))
        sym_info = zipfile.ZipInfo("word/evil_link.xml")
        sym_info.create_system = 3
        sym_info.external_attr = ((stat.S_IFLNK | 0o777) << 16) | 0x0400
        dst.writestr(sym_info, b"/etc/passwd")
    return path


# ---------------------------------------------------------------------------
# Tier 4 — 8 Real-World Application Scenarios (TEST_INFRA.md #1–#8)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_t4_01_financial_regulatory_filing_cli_pipeline(
    cli_runner: Any,
    e2e_workspace: Any,
    public_fixture: Any,
) -> None:
    """Scenario 1: Regulatory PDF CLI pipeline (irs_f1120 + rotated fed_g19) -> DuckDB SQL."""
    spec = cli_runner(["parse", "spec"])
    assert spec.returncode == int(CliExitCode.OK) and spec.envelope is not None
    assert spec.envelope.ok is True
    assert set(spec.envelope.data["formats"]) == {"pdf", "markdown", "docx", "domain_note"}

    f1120 = public_fixture("irs_f1120.pdf")
    g19 = public_fixture("fed_g19_consumer_credit.pdf")

    for name, pdf_path, expected_pages in (("f1120", f1120, 6), ("g19", g19, 3)):
        ins = cli_runner(["parse", "inspect", str(pdf_path)], timeout=120.0)
        assert ins.returncode == 0 and ins.envelope is not None
        assert ins.envelope.data["page_count"] == expected_pages

        ir_path = e2e_workspace.workspace / f"{name}_red.ir.json"
        ext = cli_runner(
            ["parse", "extract", str(pdf_path), "--redact", "--out", str(ir_path)],
            timeout=120.0,
        )
        assert ext.returncode == 0, ext.stdout
        ir_data = json.loads(ir_path.read_text(encoding="utf-8"))
        assert ir_data["provenance"]["redacted"] is True
        if name == "g19":
            assert all(p["width_pt"] > p["height_pt"] for p in ir_data["pages"])

        val = cli_runner(["parse", "validate", str(ir_path)], timeout=120.0)
        assert val.returncode == 0 and val.envelope is not None and val.envelope.ok is True

        chunks_path = e2e_workspace.workspace / f"{name}_chunks.jsonl"
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
        assert exp.returncode == 0, exp.stdout
        agg = query_ndjson_with_duckdb(
            chunks_path,
            (
                "SELECT page_number, COUNT(*) AS cnt, BOOL_AND(redacted) AS all_redacted "
                "FROM chunks GROUP BY page_number ORDER BY page_number"
            ),
        )
        assert len(agg) >= 1
        assert all(row["cnt"] >= 1 and row["all_redacted"] is True for row in agg)

    e2e_workspace.assert_no_workspace_opendv_dir()


@pytest.mark.slow
def test_t4_02_asset_management_multi_format_corpus_cli(
    cli_runner: Any,
    e2e_workspace: Any,
    public_fixture: Any,
) -> None:
    """Scenario 2: AM PDF + MD + IRS SOI DOCX -> CLI extract/validate/document_map/markdown."""
    am_pdf = PUBLIC_AM_DIR / "am_custody_and_voting_guide.pdf"
    am_md = PUBLIC_AM_DIR / "am_stewardship_domain_notes.md"
    soi_docx = public_fixture("irs_soi_21incddocguide.docx")

    outputs: dict[str, dict[str, Any]] = {}
    for label, src_path in (("am_pdf", am_pdf), ("am_md", am_md), ("soi_docx", soi_docx)):
        ir_path = e2e_workspace.workspace / f"{label}.ir.json"
        dm_path = e2e_workspace.workspace / f"{label}.dm.json"
        md_path = e2e_workspace.workspace / f"{label}.md"

        assert cli_runner(["parse", "extract", str(src_path), "--out", str(ir_path)]).returncode == 0
        val = cli_runner(["parse", "validate", str(ir_path)])
        assert val.returncode == 0, f"validate failed on {label}: {val.stdout}"
        assert (
            cli_runner(
                ["parse", "export", str(ir_path), "--target", "document_map", "--out", str(dm_path)]
            ).returncode
            == 0
        )
        assert (
            cli_runner(
                ["parse", "export", str(ir_path), "--target", "markdown", "--out", str(md_path)]
            ).returncode
            == 0
        )
        outputs[label] = {
            "ir": json.loads(ir_path.read_text(encoding="utf-8")),
            "dm": json.loads(dm_path.read_text(encoding="utf-8"))["document_map"],
            "md": md_path.read_text(encoding="utf-8"),
        }

    am_dm = outputs["am_pdf"]["dm"]
    assert am_dm["page_count"] == 6
    assert am_dm["pages"][2]["table_headers"] == ["ballot_id", "issuer_cusip", "vote_choice"]
    assert am_dm["pages"][3]["is_scanned_or_low_density"] is True
    assert outputs["am_pdf"]["md"].count("CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE") == 2

    for non_pdf in ("am_md", "soi_docx"):
        pages = outputs[non_pdf]["ir"]["pages"]
        assert all(
            p["slice_kind"] == "virtual_section_page" and p["renderable"] is False for p in pages
        )

    e2e_workspace.assert_no_workspace_opendv_dir()


def test_t4_03_mcp_parsing_profile_persistent_stdio_session(
    e2e_workspace: Any,
    public_fixture: Any,
) -> None:
    """Scenario 3: Persistent MCP parsing session (spec -> inspect -> extract -> render -> export)."""
    docs_dir = e2e_workspace.workspace / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    fw9_copy = docs_dir / "irs_fw9.pdf"
    fw9_copy.write_bytes(public_fixture("irs_fw9.pdf").read_bytes())
    pii_pdf = make_pii_multi_token_pdf(docs_dir / "pii_statement.pdf")
    outside_pdf = make_minimal_pdf(e2e_workspace.workspace / "outside" / "secret.pdf")
    spill_dir = e2e_workspace.workspace / "spill"

    async def _run() -> None:
        async with open_mcp_session(
            "parsing",
            workspace=e2e_workspace,
            allow_roots=[docs_dir],
            max_inline_bytes=4_096,
            spill_dir=spill_dir,
        ) as (session, tools):
            assert len(tools) == 6
            is_err, spec, _ = await call_mcp_tool_json(session, "parse_spec", {})
            assert not is_err and "chunks" in spec["projectors"]

            is_err, ins, _ = await call_mcp_tool_json(
                session, "parse_inspect", {"uri": str(fw9_copy)}
            )
            assert not is_err and ins["page_count"] == 6

            is_err, ext_pii, _ = await call_mcp_tool_json(
                session, "parse_extract", {"uri": str(pii_pdf), "redact": True}
            )
            assert not is_err
            if ext_pii.get("spilled") is True:
                ir_payload = json.loads(Path(ext_pii["spill_path"]).read_text(encoding="utf-8"))
                validate_args: dict[str, Any] = {"ir_path": ext_pii["spill_path"]}
                export_args: dict[str, Any] = {
                    "target": "chunks",
                    "ir_path": ext_pii["spill_path"],
                    "format": "jsonl",
                }
            else:
                ir_payload = ext_pii
                validate_args = {"ir": ext_pii}
                export_args = {"target": "chunks", "ir": ext_pii, "format": "jsonl"}

            assert ir_payload["provenance"]["redacted"] is True
            assert "<MASKED_PII>" in ir_payload["pages"][0]["canonical_text"]
            assert "123-45-6789" not in ir_payload["pages"][0]["canonical_text"]

            is_err, rnd, _ = await call_mcp_tool_json(
                session, "parse_render_page", {"uri": str(fw9_copy), "page_number": 1}
            )
            assert not is_err
            spill_png = rnd["spillover"]
            assert spill_png is not None and spill_png["spilled"] is True
            png_path = Path(spill_png["spill_path"])
            assert png_path.parent == spill_dir and _sha256_file(png_path) == spill_png["sha256"]
            with Image.open(png_path) as img:
                assert img.size[0] > 0 and img.size[1] > 0

            is_err, val_res, _ = await call_mcp_tool_json(
                session, "parse_validate_ir", validate_args
            )
            assert not is_err and (val_res == [] or val_res.get("valid") is True)

            is_err, chunks_res, _ = await call_mcp_tool_json(
                session, "parse_export_ir", export_args
            )
            assert not is_err
            assert chunks_res is not None

            is_err, _, prob = await call_mcp_tool_json(
                session, "parse_inspect", {"uri": str(outside_pdf)}
            )
            assert is_err is True and prob is not None
            assert prob.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

            # Persistent session survives error cleanly
            is_err, spec_after, _ = await call_mcp_tool_json(session, "parse_spec", {})
            assert not is_err and "pdf" in spec_after["formats"]

    asyncio.run(_run())
    e2e_workspace.assert_no_workspace_opendv_dir()


@pytest.mark.slow
def test_t4_04_stateless_grounding_and_modeling_offline_pipeline(
    cli_runner: Any,
    e2e_workspace: Any,
    public_fixture: Any,
) -> None:
    """Scenario 4: Stateless ontology ground --doc + model profile + model verify + run."""
    from opendv.service.workflows.modeling_stateless import run_model_stateless
    from tests.cli._cli_test_helpers import build_account_draft_ir, build_account_ontology

    csv_path = e2e_workspace.workspace / "accounts.csv"
    csv_path.write_text(
        "account_id,account_name,status\nACC1,Alpha,OPEN\nACC2,Beta,CLOSED\n",
        encoding="utf-8",
    )
    pdf_doc = make_minimal_pdf(e2e_workspace.workspace / "acct_guide.pdf", "Account domain guide.")
    docx_doc = public_fixture("aspose_formatted_elements.docx")
    md_doc = public_fixture("datavault_automate_dv_readme.md")
    txt_doc = public_fixture("rfc4180_csv.txt")

    ground_req = e2e_workspace.workspace / "ground_req.json"
    ground_req.write_text(
        json.dumps(
            {
                "tables": [
                    {
                        "table_id": "tbl_accounts",
                        "table_name": "accounts",
                        "columns": [
                            {"column_name": "account_id", "data_type": "VARCHAR", "is_pk": True},
                            {"column_name": "account_name", "data_type": "VARCHAR"},
                            {"column_name": "status", "data_type": "VARCHAR"},
                        ],
                    }
                ],
                "global_steward_notes": [],
                "policy": {"mode": "deterministic_only", "skip_reason": "t4_04_offline"},
            }
        ),
        encoding="utf-8",
    )
    gnd = cli_runner(
        [
            "ontology",
            "ground",
            "--input",
            str(ground_req),
            "--doc",
            str(pdf_doc),
            "--doc",
            str(docx_doc),
            "--doc",
            str(md_doc),
            "--doc",
            str(txt_doc),
        ]
    )
    assert gnd.returncode == int(CliExitCode.OK), gnd.stderr or gnd.stdout
    assert gnd.envelope is not None and len(gnd.envelope.data["tables"]) >= 1
    assert sum(len(t["columns"]) for t in gnd.envelope.data["tables"]) >= 1

    bundle_path = e2e_workspace.workspace / "bundle.json"
    prof_args = [
        "model",
        "profile",
        "--source",
        f"csv:{csv_path}",
        "--source",
        f"pdf:{pdf_doc}",
        "--source",
        f"docx:{docx_doc}",
        "--source",
        f"markdown:{md_doc}",
        "--source",
        f"domain_note:{txt_doc}",
    ]
    plain_prof = cli_runner(prof_args, expect_json=False)
    assert plain_prof.returncode == 0 and "document_map" in plain_prof.stdout.lower()

    json_prof = cli_runner([*prof_args, "--out", str(bundle_path)])
    assert json_prof.returncode == 0, json_prof.stdout
    bundle_data = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert len(bundle_data["profiles"]) == 1
    assert len(bundle_data["document_maps"]) == 4

    sid = bundle_data["profiles"][0]["source_id"]
    tid = bundle_data["profiles"][0]["tables"][0]["table_id"]
    draft = build_account_draft_ir("p_t4_04", sid, tid)
    draft_path = e2e_workspace.workspace / "draft.json"
    draft_path.write_text(draft.model_dump_json(indent=2), encoding="utf-8")
    verify_req = e2e_workspace.workspace / "verify_req.json"
    verify_req.write_text(json.dumps({"policy": "balanced"}), encoding="utf-8")

    ver = cli_runner(
        [
            "model",
            "verify",
            "--input",
            str(verify_req),
            "--draft-ir",
            str(draft_path),
            "--profile",
            str(bundle_path),
        ]
    )
    assert ver.returncode == int(CliExitCode.OK), ver.stdout
    assert ver.envelope is not None and ver.envelope.data["status"] == "CERTIFIED_FAST"

    dry_run = asyncio.run(
        run_model_stateless(
            {
                "project_id": "p_t4_04",
                "policy": "balanced",
                "ontology": build_account_ontology().model_dump(mode="json"),
            },
            profile_bundle=bundle_path,
            dry_run=True,
            workspace_root=e2e_workspace.workspace,
            state_dir=e2e_workspace.state_dir,
        )
    )
    assert dry_run["dry_run"] is True and dry_run["valid"] is True
    e2e_workspace.assert_no_workspace_opendv_dir()


def _draft_from_certified(cert_ir: Any) -> Any:
    from opendv.models.vault_ir import (
        DraftHubSpec,
        DraftLinkSpec,
        DraftSatelliteSpec,
        DraftSourceBinding,
        DraftVaultIR,
    )

    def _bindings(bindings: Any) -> list[DraftSourceBinding]:
        return [
            DraftSourceBinding(
                source_id=b.source_id,
                table_id=b.table_id,
                col_mappings=dict(b.col_mappings),
                doc_citations=list(b.doc_citations),
                mcp_evidence=list(b.mcp_evidence),
            )
            for b in bindings
        ]

    return DraftVaultIR(
        project_id=cert_ir.project_id,
        ontology_version=cert_ir.ontology_version,
        wave_number=cert_ir.wave_number,
        hubs=[
            DraftHubSpec(
                hub_name=h.hub_name,
                business_keys=list(h.business_keys),
                key_tier=h.key_tier,
                source_bindings=_bindings(h.source_bindings),
                attributes=list(h.attributes),
                is_planned_concept=h.is_planned_concept,
                survivorship_priority=list(h.survivorship_priority),
            )
            for h in cert_ir.hubs
        ],
        links=[
            DraftLinkSpec(
                link_name=lnk.link_name,
                link_kind=lnk.link_kind,
                participants=list(lnk.participants),
                driving_hub=lnk.driving_hub,
                degenerate_keys=list(lnk.degenerate_keys),
                source_bindings=_bindings(lnk.source_bindings),
                attributes=list(lnk.attributes),
            )
            for lnk in cert_ir.links
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name=s.sat_name,
                sat_kind=s.sat_kind,
                parent_name=s.parent_name,
                source_bindings=_bindings(s.source_bindings),
                extends_sat_name=s.extends_sat_name,
                companion_epoch=s.companion_epoch,
                is_bitemporal=s.is_bitemporal,
                applied_dts_col=s.applied_dts_col,
                load_mode=s.load_mode,
                child_dependent_keys=list(s.child_dependent_keys),
                driving_keys=list(s.driving_keys),
                immutable_payload_fields=list(s.immutable_payload_fields),
                attributes=list(s.attributes),
            )
            for s in cert_ir.satellites
        ],
        column_triage=list(cert_ir.column_triage),
        justifications=list(cert_ir.justifications),
    )


def test_t4_05_metadata_only_core_bundle_offline_continuity(e2e_workspace: Any) -> None:
    """Scenario 5: .core.opendv.zip SOURCE_BINARY_ABSENT vs offline grounding & Gate V4."""
    from opendv.modeling.models import VerifyDraftRequest
    from opendv.modeling.verify_mode import verify_draft_vault
    from opendv.models.diagnostics import DocumentCitationRef
    from opendv.models.grounding import GroundingPolicy, OntologyGroundReq
    from opendv.models.vault_ir import DraftSourceBinding
    from opendv.service import OpenDVService
    from opendv.service.workflows._vault_helpers import load_verifier_inputs
    from opendv.storage.prover.duckdb_prover import DuckDbProverAdapter

    svc = OpenDVService(e2e_workspace.workspace, state_dir=e2e_workspace.state_dir)
    rep = svc.import_bundle(
        GOLDEN_BUNDLE_PATH,
        conflict="fail",
        enforce_workspace_containment=False,
    )
    assert "METADATA_ONLY_BINARY_ABSENT" in rep.readiness_flags

    with pytest.raises(OpenDVError) as exc_info:
        svc.get_document_pages("am-golden-w1", "src-7c0663dfb9")
    assert exc_info.value.error_code == ErrorCode.SOURCE_BINARY_ABSENT

    gnd_resp = asyncio.run(
        svc.discovery.ground_ontology(
            OntologyGroundReq(
                project_id="am-golden-w1",
                policy=GroundingPolicy(mode="deterministic_only", skip_reason="t4_05_offline"),
            )
        )
    )
    assert len(gnd_resp.tables) >= 1
    assert sum(len(t.columns) for t in gnd_resp.tables) >= 1

    ont, profs, _cl, _lock, cert_ir, _waivers, vctx = load_verifier_inputs(
        svc.state_manager,
        e2e_workspace.workspace,
        "am-golden-w1",
    )
    assert vctx.metadata_only_binary_absent is True
    assert len(vctx.document_maps) == 2
    assert cert_ir is not None

    pdf_map = next(dm for dm in vctx.document_maps if dm.doc_id == "src-7c0663dfb9")
    p5 = pdf_map.pages[4]
    quote = "CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE"
    base_draft = _draft_from_certified(cert_ir)
    valid_hub = base_draft.hubs[0].model_copy(
        update={
            "source_bindings": [
                DraftSourceBinding(
                    source_id=base_draft.hubs[0].source_bindings[0].source_id,
                    table_id=base_draft.hubs[0].source_bindings[0].table_id,
                    col_mappings=base_draft.hubs[0].source_bindings[0].col_mappings,
                    doc_citations=[
                        DocumentCitationRef(
                            doc_id="src-7c0663dfb9",
                            page_number=5,
                            verbatim_quote=quote,
                            occurrence=1,
                            char_start=0,
                            char_end=len(quote),
                            content_sha256=p5.canonical_text_sha256,
                        )
                    ],
                )
            ]
        }
    )
    prover = DuckDbProverAdapter()
    ok_res = verify_draft_vault(
        VerifyDraftRequest(
            draft_ir=base_draft.model_copy(update={"hubs": [valid_hub, *base_draft.hubs[1:]]}),
            profiles=profs,
            ontology=ont,
            verifier_ctx=vctx,
        ),
        prover=prover,
    )
    assert not any(
        d.rule_id in {"CITATION_SPAN_UNRESOLVED", "SPAN_HALLUCINATION"}
        for d in ok_res.verifier_report.v1_v6_diagnostics
    )

    tampered_hub = valid_hub.model_copy(
        update={
            "source_bindings": [
                valid_hub.source_bindings[0].model_copy(
                    update={
                        "doc_citations": [
                            valid_hub.source_bindings[0].doc_citations[0].model_copy(
                                update={"content_sha256": "deadbeef" * 8}
                            )
                        ]
                    }
                )
            ]
        }
    )
    bad_res = verify_draft_vault(
        VerifyDraftRequest(
            draft_ir=base_draft.model_copy(update={"hubs": [tampered_hub, *base_draft.hubs[1:]]}),
            profiles=profs,
            ontology=ont,
            verifier_ctx=vctx,
        ),
        prover=prover,
    )
    assert any(
        d.rule_id == "CITATION_SPAN_UNRESOLVED" for d in bad_res.verifier_report.v1_v6_diagnostics
    )


@pytest.mark.hardening
def test_t4_06_scanned_and_pii_sensitive_exhibit_workflow(
    cli_runner: Any,
    e2e_workspace: Any,
) -> None:
    """Scenario 6: Unapproved scan blocked -> approve_scan + OcrProviderFn -> MODEL_EGRESS."""
    from opendv.models.document_ir import OcrBox
    from opendv.service.parsing_svc import ParsingService

    blocked = cli_runner(["parse", "render-page", str(SAMPLE_SCANNED_PDF), "--page", "2"])
    assert blocked.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert blocked.envelope is not None
    assert blocked.envelope.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    approved_png = e2e_workspace.workspace / "p2_approved.png"
    approved = cli_runner(
        [
            "parse",
            "render-page",
            str(SAMPLE_SCANNED_PDF),
            "--page",
            "2",
            "--approve-scan",
            "--out",
            str(approved_png),
        ]
    )
    assert approved.returncode == int(CliExitCode.OK) and approved_png.is_file()

    scan_img_pdf = make_scanned_image_pdf(e2e_workspace.workspace / "scan_exhibit.pdf")

    def _ocr_provider(_png: bytes, _page: int) -> tuple[list[OcrBox], str]:
        return (
            [OcrBox(x0=10, y0=10, x1=150, y1=45, text="123-45-6789")],
            "123-45-6789",
        )

    svc = ParsingService(workspace_root=e2e_workspace.workspace, ocr_provider=_ocr_provider)
    red_scan_png, red_meta = svc.render_document_page(
        scan_img_pdf,
        1,
        sink_class="MODEL_EGRESS",
        redaction="on",
    )
    assert red_meta.ocr_used is True and red_meta.redacted is True
    assert _count_black_pixels(red_scan_png) > 0

    pii_pdf = make_pii_multi_token_pdf(e2e_workspace.workspace / "pii_exhibit.pdf")
    unred_bytes, _ = svc.render_document_page(
        pii_pdf, 1, sink_class="LOCAL_HUMAN_IO", redaction="off"
    )
    base_black = _count_black_pixels(unred_bytes)

    # In MCP parsing profile (MODEL_EGRESS), approve_scan=True still redacts PII & blocks OCR-less scans
    py_dir = str(Path(sys.executable).parent)

    async def _run_mcp() -> None:
        async with open_mcp_session(
            "parsing",
            workspace=e2e_workspace,
            allow_roots=[REPO_ROOT, e2e_workspace.workspace],
            env_extra={"PATH": py_dir},
        ) as (session, _tools):
            is_err, pii_render, _ = await call_mcp_tool_json(
                session,
                "parse_render_page",
                {"uri": str(pii_pdf), "page_number": 1, "approve_scan": True},
            )
            assert not is_err and pii_render["redacted"] is True
            rendered_png = base64.b64decode(pii_render["png_b64"])
            assert _count_black_pixels(rendered_png) > base_black

            is_err, _, prob = await call_mcp_tool_json(
                session,
                "parse_render_page",
                {"uri": str(SAMPLE_SCANNED_PDF), "page_number": 2, "approve_scan": True},
            )
            assert is_err is True and prob is not None
            assert prob.error_code in {ErrorCode.UNREDACTED_SCAN_BLOCKED, ErrorCode.DRIVER_MISSING}

    asyncio.run(_run_mcp())


@pytest.mark.slow
def test_t4_07_large_multi_section_public_standards_splitting(
    e2e_workspace: Any,
    public_fixture: Any,
) -> None:
    """Scenario 7: >24k MD/TXT + merged DOCX + PII mask-before-split -> Markdown & DuckDB."""
    from opendv.service.parsing_svc import ParsingService

    svc = ParsingService(
        workspace_root=e2e_workspace.workspace,
        allowed_roots=[PUBLIC_FIXTURES_DIR, e2e_workspace.workspace],
    )
    stripe_md = public_fixture("fivetran_dbt_stripe_readme.md")
    rfc8259 = public_fixture("rfc8259_json.txt")
    rfc6749 = public_fixture("rfc6749_oauth2.txt")
    merged_docx = public_fixture("aspose_merged_table.docx")
    pii_txt = make_boundary_pii_text_file(
        e2e_workspace.workspace / "boundary_std.txt",
        split_offset=VIRTUAL_PAGE_SPLIT_CHARS,
    )

    merged_ir = svc.parse_document(merged_docx, sink_class="MODEL_EGRESS", redaction="on")
    assert svc.validate_ir(merged_ir) == []
    merged_md = svc.export_ir(merged_ir, target="markdown", sink_class="MODEL_EGRESS")
    pipe_lines = [ln for ln in merged_md.splitlines() if ln.strip().startswith("|")]
    assert len(pipe_lines) >= 12
    assert all(ln.count("|") == pipe_lines[0].count("|") for ln in pipe_lines)

    for idx, doc_path in enumerate((stripe_md, rfc8259, rfc6749, merged_docx, pii_txt)):
        ir = svc.parse_document(doc_path, sink_class="MODEL_EGRESS", redaction="on")
        assert svc.validate_ir(ir) == [], f"validate_ir failed for {doc_path.name}"
        assert all(len(p.canonical_text) <= VIRTUAL_PAGE_SPLIT_CHARS for p in ir.pages)
        if doc_path == pii_txt:
            assert all("123-45-6789" not in p.canonical_text for p in ir.pages)

        chunks_path = e2e_workspace.workspace / f"std_{idx}.jsonl"
        chunks_path.write_text(
            "".join(svc.stream_export(ir, target="chunks", sink_class="MODEL_EGRESS")),
            encoding="utf-8",
        )
        rows = query_ndjson_with_duckdb(
            chunks_path,
            "SELECT COUNT(*) AS cnt, MAX(LENGTH(text)) AS max_len, BOOL_AND(redacted) AS r FROM chunks",
        )
        assert rows[0]["cnt"] >= 1
        assert rows[0]["max_len"] <= 4_000
        assert rows[0]["r"] is True


@pytest.mark.hardening
def test_t4_08_adversarial_security_boundary_audit_session(
    cli_runner: Any,
    e2e_workspace: Any,
    public_fixture: Any,
) -> None:
    """Scenario 8: Adversarial containment/URI/archive audit via CLI & MCP --profile all."""
    from opendv.models.wire_dtos_discovery import ProjectInitReq, SourceAttachReq
    from opendv.service import OpenDVService

    allowed = e2e_workspace.workspace / "allowed"
    outside = e2e_workspace.workspace / "outside"
    allowed.mkdir(parents=True, exist_ok=True)
    outside.mkdir(parents=True, exist_ok=True)

    valid_pdf = make_minimal_pdf(allowed / "valid.pdf", "Allowed PDF inside root.")
    outside_pdf = make_minimal_pdf(outside / "secret.pdf", "Outside root PDF.")
    bomb_cnt = make_zip_bomb_entry_count_docx(allowed / "bomb_cnt.docx")
    bomb_ratio = make_zip_bomb_ratio_docx(allowed / "bomb_ratio.docx")
    xxe_docx = make_xxe_doctype_docx(allowed / "xxe.docx")
    sym_docx = _make_symlink_member_docx(allowed / "sym_member.docx")
    enc_docx = allowed / "enc.docx"
    enc_docx.write_bytes(public_fixture("aspose_encrypted_ole2.docx").read_bytes())
    bad_pdf = allowed / "corrupt.pdf"
    bad_pdf.write_bytes(CORRUPT_REPO_PDF.read_bytes())

    # Valid local path & file:/// URI inside --allow-root succeed
    for target_arg in (str(valid_pdf), valid_pdf.resolve().as_uri()):
        ok = cli_runner(["parse", "inspect", target_arg, "--allow-root", str(allowed)])
        assert ok.returncode == int(CliExitCode.OK), ok.stdout

    # Adversarial cases mapped to exact ErrorCodes
    adversarial: list[tuple[str, ErrorCode, int]] = [
        (str(outside_pdf), ErrorCode.ARCHIVE_SECURITY_VIOLATION, int(CliExitCode.VALIDATION_ERROR)),
        (r"\\evil-server\share\doc.pdf", ErrorCode.ARCHIVE_SECURITY_VIOLATION, int(CliExitCode.VALIDATION_ERROR)),
        (f"{valid_pdf}:secret_ads", ErrorCode.ARCHIVE_SECURITY_VIOLATION, int(CliExitCode.VALIDATION_ERROR)),
        ("file::https://evil.test/x.pdf", ErrorCode.SYNTAX_INVALID_ARGUMENT, int(CliExitCode.VALIDATION_ERROR)),
        ("file://evilhost/a.pdf", ErrorCode.SYNTAX_INVALID_ARGUMENT, int(CliExitCode.VALIDATION_ERROR)),
        (str(allowed / "CON.pdf"), ErrorCode.SYNTAX_INVALID_ARGUMENT, int(CliExitCode.VALIDATION_ERROR)),
        (str(allowed / "does_not_exist.pdf"), ErrorCode.RESOURCE_NOT_FOUND, int(CliExitCode.NOT_FOUND)),
        (str(sym_docx), ErrorCode.ARCHIVE_SECURITY_VIOLATION, int(CliExitCode.VALIDATION_ERROR)),
        (str(xxe_docx), ErrorCode.ARCHIVE_SECURITY_VIOLATION, int(CliExitCode.VALIDATION_ERROR)),
        (str(bomb_cnt), ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED, int(CliExitCode.VALIDATION_ERROR)),
        (str(bomb_ratio), ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED, int(CliExitCode.VALIDATION_ERROR)),
        (str(enc_docx), ErrorCode.VERIFICATION_GATE_FAILED, int(CliExitCode.VALIDATION_ERROR)),
        (str(bad_pdf), ErrorCode.VERIFICATION_GATE_FAILED, int(CliExitCode.VALIDATION_ERROR)),
    ]

    # Test real OS symlink when privilege permits, else covered by sym_member.docx above
    real_sym = allowed / "real_link.pdf"
    try:
        real_sym.symlink_to(outside_pdf)
    except OSError:
        pass
    else:
        adversarial.append(
            (str(real_sym), ErrorCode.ARCHIVE_SECURITY_VIOLATION, int(CliExitCode.VALIDATION_ERROR))
        )

    for arg, expected_code, expected_rc in adversarial:
        res = cli_runner(["parse", "inspect", arg, "--allow-root", str(allowed)])
        assert res.returncode == expected_rc, f"Failed for {arg}: {res.stdout}"
        assert res.envelope is not None and res.envelope.error_code == expected_code

    # Initialize project and audit via MCP --profile all (23 tools)
    svc = OpenDVService(e2e_workspace.workspace, state_dir=e2e_workspace.state_dir)
    svc.init_project(ProjectInitReq(project_id="audit_p", domain_namespace="ibor"))
    svc.attach_source("audit_p", SourceAttachReq(kind="pdf", uri_or_filename=str(valid_pdf)))

    async def _run_mcp_all() -> None:
        async with open_mcp_session(
            "all",
            workspace=e2e_workspace,
            project="audit_p",
            allow_roots=[allowed],
        ) as (session, tools):
            assert len(tools) == 23
            for bad_uri, exp_code in (
                (str(outside_pdf), ErrorCode.ARCHIVE_SECURITY_VIOLATION),
                (str(xxe_docx), ErrorCode.ARCHIVE_SECURITY_VIOLATION),
                (str(enc_docx), ErrorCode.VERIFICATION_GATE_FAILED),
            ):
                is_err, _, prob = await call_mcp_tool_json(
                    session, "parse_inspect", {"uri": bad_uri}
                )
                assert is_err is True and prob is not None and prob.error_code == exp_code

            is_err, proj_data, _ = await call_mcp_tool_json(session, "project_get", {})
            assert not is_err and proj_data["project_id"] == "audit_p"
            is_err, spec_data, _ = await call_mcp_tool_json(session, "parse_spec", {})
            assert not is_err and "pdf" in spec_data["formats"]

    asyncio.run(_run_mcp_all())
