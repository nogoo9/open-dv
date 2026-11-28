"""Tier 3 E2E suite: cross-feature pairwise combinations across F01..F12."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import shutil
import sys
import tempfile
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
    make_boundary_pii_docx,
    make_boundary_pii_text_file,
    make_dir_entry_docx,
    make_dom_order_merged_docx,
    make_empty_text_file,
    make_high_pixel_pdf,
    make_minimal_pdf,
    make_pii_multi_token_pdf,
    make_rotated_pdf,
    make_scanned_image_pdf,
    make_xxe_doctype_docx,
    make_zip_bomb_entry_count_docx,
    make_zip_bomb_ratio_docx,
    open_mcp_session,
    public_fixture_path,
    query_ndjson_with_duckdb,
)

GOLDEN_BUNDLE_PATH = REPO_ROOT / "tests" / "golden_v2" / "am_core_bundle.core.opendv.zip"
SAMPLE_SCANNED_PDF = (
    REPO_ROOT / "examples" / "sample_pdfs" / "institutional_scanned_schedule_ocr_test.pdf"
)
SRC_MATRIX_DIR = REPO_ROOT / "examples" / "scenarios" / "src_matrix"


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


# ---------------------------------------------------------------------------
# Tier 3 — 18 Pairwise Cross-Feature Combination Tests (F01..F12)
# ---------------------------------------------------------------------------


def test_t3_01_docx_dom_merged_pii_mask_before_24k_to_duckdb(e2e_workspace: Any) -> None:
    """F02 x F04 x F06: DOCX DOM order + merged cells + mask-before-24k -> DuckDB chunks."""
    from opendv.service.parsing_svc import ParsingService

    merged_path = make_dom_order_merged_docx(
        e2e_workspace.workspace / "dom_merged.docx",
        include_boundary_pii=False,
    )
    svc = ParsingService(workspace_root=e2e_workspace.workspace)
    ir_dom = svc.parse_document(merged_path, sink_class="LOCAL_HUMAN_IO", redaction="off")
    assert svc.validate_ir(ir_dom) == []
    texts = [b.text for b in ir_dom.blocks]
    intro_idx = next(i for i, t in enumerate(texts) if "1. DOM Order Intro" in t)
    tbl_idx = next(i for i, b in enumerate(ir_dom.blocks) if b.kind == "table")
    sdt_idx = next(i for i, t in enumerate(texts) if "SDT Controlled Section Paragraph" in t)
    post_idx = next(i for i, t in enumerate(texts) if "2. Post-Table Paragraph" in t)
    assert intro_idx < tbl_idx < sdt_idx < post_idx
    tbl_block = ir_dom.blocks[tbl_idx]
    assert tbl_block.table_headers is not None and len(tbl_block.table_headers) == 3
    assert tbl_block.table_rows is not None
    assert all(len(row) == 3 for row in tbl_block.table_rows)

    pii_docx = make_boundary_pii_docx(
        e2e_workspace.workspace / "boundary_pii.docx",
        split_offset=VIRTUAL_PAGE_SPLIT_CHARS,
    )
    raw_ir = svc.parse_document(pii_docx, sink_class="LOCAL_HUMAN_IO", redaction="off")
    assert len(raw_ir.pages) == 2
    assert "123-45-6789" not in raw_ir.pages[0].canonical_text
    assert "123-45-6789" in raw_ir.pages[1].canonical_text

    red_ir = svc.parse_document(pii_docx, sink_class="MODEL_EGRESS", redaction="on")
    assert red_ir.provenance.redacted is True
    for page in red_ir.pages:
        assert len(page.canonical_text) <= VIRTUAL_PAGE_SPLIT_CHARS
        assert "123-45-6789" not in page.canonical_text
    assert any("<MASKED_PII>" in page.canonical_text for page in red_ir.pages)

    chunks_path = e2e_workspace.workspace / "docx_chunks.jsonl"
    chunks_path.write_text(
        "".join(svc.stream_export(red_ir, target="chunks", sink_class="MODEL_EGRESS")),
        encoding="utf-8",
    )
    rows = query_ndjson_with_duckdb(
        chunks_path,
        "SELECT COUNT(*) AS cnt, BOOL_AND(redacted) AS all_redacted FROM chunks",
    )
    assert rows[0]["cnt"] >= 1
    assert rows[0]["all_redacted"] is True


@pytest.mark.hardening
def test_t3_02_rotated_pdf_multi_token_pii_render_and_extract_parity(
    cli_runner: Any,
    e2e_workspace: Any,
) -> None:
    """F01 x F05 x F06: Rotated PDF + multi-token PII word boxes -> render-page & extract."""
    rot_pdf = make_rotated_pdf(
        e2e_workspace.workspace / "rot_pii.pdf",
        rotate_deg=90,
        text="Card: 4111 1111 1111 1111\nPhone: (415) 555-0199\nTax ID: 123-45-6789",
    )
    ir_out = e2e_workspace.workspace / "rot_pii_ir.json"
    ext = cli_runner(["parse", "extract", str(rot_pdf), "--redact", "--out", str(ir_out)])
    assert ext.returncode == int(CliExitCode.OK), ext.stdout
    ir_data = json.loads(ir_out.read_text(encoding="utf-8"))
    assert ir_data["provenance"]["redacted"] is True
    page_text = ir_data["pages"][0]["canonical_text"]
    assert ir_data["pages"][0]["width_pt"] > ir_data["pages"][0]["height_pt"]
    assert "<MASKED_PII>" in page_text
    assert "4111 1111 1111 1111" not in page_text
    assert "123-45-6789" not in page_text

    unred_png = e2e_workspace.workspace / "rot_unred.png"
    red_png = e2e_workspace.workspace / "rot_red.png"
    assert (
        cli_runner(
            ["parse", "render-page", str(rot_pdf), "--page", "1", "--out", str(unred_png)]
        ).returncode
        == 0
    )
    assert (
        cli_runner(
            [
                "parse",
                "render-page",
                str(rot_pdf),
                "--page",
                "1",
                "--redact",
                "--out",
                str(red_png),
            ]
        ).returncode
        == 0
    )
    with Image.open(red_png) as img:
        w, h = img.size
        assert w > h, f"Expected rotated landscape PNG, got {w}x{h}"
    assert _count_black_pixels(red_png.read_bytes()) > _count_black_pixels(unred_png.read_bytes())
    e2e_workspace.assert_no_workspace_opendv_dir()


@pytest.mark.hardening
def test_t3_03_mcp_parsing_profile_spillover_export_and_render(
    e2e_workspace: Any,
    public_fixture: Any,
) -> None:
    """F05 x F09 x F12: MCP parsing profile + --spill-dir -> parse_export_ir & parse_render_page."""
    spill_dir = e2e_workspace.workspace / "spill"

    async def _run() -> None:
        async with open_mcp_session(
            "parsing",
            workspace=e2e_workspace,
            allow_roots=[PUBLIC_FIXTURES_DIR],
            max_inline_bytes=512,
            spill_dir=spill_dir,
        ) as (session, tools):
            assert len(tools) == 6
            f1120 = public_fixture("irs_f1120.pdf")

            is_err, render_res, _ = await call_mcp_tool_json(
                session, "parse_render_page", {"uri": str(f1120), "page_number": 1}
            )
            assert not is_err
            assert render_res["png_b64"] is None
            spill_png = render_res["spillover"]
            assert spill_png["spilled"] is True
            assert spill_png["format"] == "png"
            png_path = Path(spill_png["spill_path"])
            assert png_path.parent == spill_dir and png_path.is_file()
            assert _sha256_file(png_path) == spill_png["sha256"]
            with Image.open(png_path) as img:
                assert img.size[0] > 0

            is_err, ext_ref, _ = await call_mcp_tool_json(
                session, "parse_extract", {"uri": str(f1120), "pages": "1", "redact": True}
            )
            assert not is_err and ext_ref["spilled"] is True
            ir_spill = Path(ext_ref["spill_path"])
            assert _sha256_file(ir_spill) == ext_ref["sha256"]

            is_err, exp_ref, _ = await call_mcp_tool_json(
                session,
                "parse_export_ir",
                {"target": "markdown", "ir_path": str(ir_spill), "redact": True},
            )
            assert not is_err and exp_ref["spilled"] is True
            assert Path(exp_ref["spill_path"]).is_file()

    asyncio.run(_run())
    e2e_workspace.assert_no_workspace_opendv_dir()


@pytest.mark.hardening
def test_t3_04_windows_uri_allow_root_approve_scan_vs_model_egress(
    e2e_workspace: Any,
) -> None:
    """F06 x F07: file:/// URI + allowed_roots + approve_scan still redacts under MODEL_EGRESS."""
    from opendv.service.parsing_svc import ParsingService

    allowed = e2e_workspace.workspace / "allowed_docs"
    allowed.mkdir(parents=True, exist_ok=True)
    pii_pdf = make_pii_multi_token_pdf(allowed / "pii_multi.pdf")
    scan_pdf = make_scanned_image_pdf(allowed / "scan_only.pdf")

    svc = ParsingService(workspace_root=e2e_workspace.workspace, allowed_roots=[allowed])
    file_uri = pii_pdf.resolve().as_uri()
    assert file_uri.startswith("file:///")

    unred_bytes, unred_meta = svc.render_document_page(
        file_uri,
        1,
        sink_class="LOCAL_HUMAN_IO",
        redaction="off",
        approve_scan=True,
    )
    red_bytes, red_meta = svc.render_document_page(
        file_uri,
        1,
        sink_class="MODEL_EGRESS",
        redaction="on",
        approve_scan=True,
    )
    assert unred_meta.redacted is False
    assert red_meta.redacted is True
    assert _count_black_pixels(red_bytes) > _count_black_pixels(unred_bytes)

    # approve_scan=True NEVER bypasses MODEL_EGRESS scan/OCR gate
    with pytest.raises(OpenDVError) as exc_info:
        svc.render_document_page(
            scan_pdf.resolve().as_uri(),
            1,
            sink_class="MODEL_EGRESS",
            redaction="on",
            approve_scan=True,
        )
    assert exc_info.value.error_code in {ErrorCode.UNREDACTED_SCAN_BLOCKED, ErrorCode.DRIVER_MISSING}


@pytest.mark.hardening
def test_t3_05_discovery_ground_ontology_doc_kinds_and_20_slot_saturation(
    cli_runner: Any,
    e2e_workspace: Any,
) -> None:
    """F03 x F10: DiscoveryService.ground_ontology + DOC_SOURCE_KINDS + 20-slot saturation."""
    from opendv.models.document_ir import DOC_SOURCE_KINDS
    from opendv.models.grounding import GroundingPolicy, OntologyGroundReq
    from opendv.models.wire_dtos_discovery import ProjectInitReq, SourceAttachReq
    from opendv.service import OpenDVService

    assert frozenset({"pdf", "markdown", "docx", "domain_note"}) == DOC_SOURCE_KINDS

    svc = OpenDVService(e2e_workspace.workspace, state_dir=e2e_workspace.state_dir)
    svc.init_project(ProjectInitReq(project_id="gnd-doc-p1", domain_namespace="finance"))

    csv_path = e2e_workspace.workspace / "positions.csv"
    csv_path.write_text("cusip,market_value\n037833100,1000\n", encoding="utf-8")
    svc.attach_source("gnd-doc-p1", SourceAttachReq(kind="csv", uri_or_filename=str(csv_path)))

    md_path = e2e_workspace.workspace / "notes.md"
    md_path.write_text(
        "# Custody Notes\nContact alice@example.com or 123-45-6789 for CUSIP breaks.\n",
        encoding="utf-8",
    )
    txt_path = e2e_workspace.workspace / "policy.txt"
    txt_path.write_text("Settlement calendar T+1 rule for equity holdings.\n", encoding="utf-8")
    pdf_path = make_minimal_pdf(
        e2e_workspace.workspace / "brief.pdf",
        "CUSIP security identifier governance brief.",
    )
    svc.attach_source("gnd-doc-p1", SourceAttachReq(kind="markdown", uri_or_filename=str(md_path)))
    svc.attach_source("gnd-doc-p1", SourceAttachReq(kind="domain_note", uri_or_filename=str(txt_path)))
    svc.attach_source("gnd-doc-p1", SourceAttachReq(kind="pdf", uri_or_filename=str(pdf_path)))

    resp = asyncio.run(
        svc.discovery.ground_ontology(
            OntologyGroundReq(
                project_id="gnd-doc-p1",
                policy=GroundingPolicy(mode="deterministic_only", skip_reason="t3_05_project"),
            )
        )
    )
    assert len(resp.tables) >= 1
    assert sum(len(t.columns) for t in resp.tables) >= 1

    # Saturate global_steward_notes (18 pre-existing slots + 3 --doc files -> must stay <= 20)
    req_json = e2e_workspace.workspace / "sat_req.json"
    req_json.write_text(
        json.dumps(
            {
                "tables": [
                    {
                        "table_id": "t1",
                        "table_name": "positions",
                        "columns": [{"column_name": "cusip", "data_type": "VARCHAR", "is_pk": True}],
                    }
                ],
                "global_steward_notes": [f"Existing note {i}" for i in range(18)],
                "policy": {"mode": "deterministic_only", "skip_reason": "t3_05_sat"},
            }
        ),
        encoding="utf-8",
    )
    cli_res = cli_runner(
        [
            "ontology",
            "ground",
            "--input",
            str(req_json),
            "--doc",
            str(md_path),
            "--doc",
            str(txt_path),
            "--doc",
            str(pdf_path),
        ]
    )
    assert cli_res.returncode == int(CliExitCode.OK), cli_res.stderr or cli_res.stdout
    assert "<MASKED_PII>" not in cli_res.stdout
    assert "123-45-6789" not in cli_res.stdout


@pytest.mark.hardening
def test_t3_06_stateless_profile_merge_and_run_model_gate_v4(
    e2e_workspace: Any,
    public_fixture: Any,
) -> None:
    """F01 x F02 x F11: profile_sources_stateless + merge_profile_bundle + run_model_stateless."""
    from opendv.modeling.models import DVModelingRequest, VerifyDraftRequest
    from opendv.modeling.verify_mode import verify_draft_vault
    from opendv.models.sources import DocumentMap, PageMapSpec
    from opendv.service.workflows._modeling_stateless_helpers import merge_profile_bundle
    from opendv.service.workflows.modeling_stateless import (
        profile_sources_stateless,
        run_model_stateless,
    )
    from opendv.storage.prover.duckdb_prover import DuckDbProverAdapter
    from tests.cli._cli_test_helpers import build_account_draft_ir, build_account_ontology

    csv_path = e2e_workspace.workspace / "accounts.csv"
    csv_path.write_text(
        "account_id,account_name,status\nACC1,Alpha,OPEN\nACC2,Beta,CLOSED\n",
        encoding="utf-8",
    )
    am_pdf = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"
    merged_docx = public_fixture("aspose_merged_table.docx")

    bundle = profile_sources_stateless(
        [f"csv:{csv_path}", f"pdf:{am_pdf}", f"docx:{merged_docx}"],
        proving_sample_rows=5,
        allowed_roots=[e2e_workspace.workspace, REPO_ROOT],
    )
    assert len(bundle.profiles) == 1
    assert len(bundle.document_maps) == 2
    pdf_map = next(dm for dm in bundle.document_maps if dm.format == "pdf")
    assert pdf_map.page_count == 6
    assert pdf_map.pages[3].is_scanned_or_low_density is True

    # Collision precedence: caller-supplied DocumentMap wins on matching doc_id
    caller_override = DocumentMap(
        doc_id=pdf_map.doc_id,
        filename="caller_override.pdf",
        format="pdf",
        page_count=1,
        pages=[
            PageMapSpec(
                page_number=1,
                char_count=10,
                has_images=False,
                is_scanned_or_low_density=False,
                ocr_box_count=0,
                canonical_text_sha256="a" * 64,
            )
        ],
    )
    merged = merge_profile_bundle(
        {"verifier_ctx": {"document_maps": [caller_override.model_dump(mode="json")]}},
        bundle,
    )
    merged_maps = merged["verifier_ctx"]["document_maps"]
    assert len(merged_maps) == 2
    by_id = {m["doc_id"]: m for m in merged_maps}
    assert by_id[pdf_map.doc_id]["filename"] == "caller_override.pdf"

    sid = bundle.profiles[0].source_id
    tid = bundle.profiles[0].tables[0].table_id
    ont = build_account_ontology()
    draft = build_account_draft_ir("p_t3", sid, tid)
    verify_res = verify_draft_vault(
        VerifyDraftRequest(draft_ir=draft, profiles=bundle.profiles, ontology=ont),
        prover=DuckDbProverAdapter(),
    )
    assert verify_res.status == "CERTIFIED_FAST"

    run_req = DVModelingRequest(project_id="p_t3", ontology=ont, profiles=[])
    dry_res = asyncio.run(
        run_model_stateless(
            run_req,
            profile_bundle=bundle,
            dry_run=True,
            workspace_root=e2e_workspace.workspace,
            state_dir=e2e_workspace.state_dir,
        )
    )
    assert dry_res["dry_run"] is True and dry_res["valid"] is True


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


@pytest.mark.hardening
def test_t3_07_core_bundle_offline_grounding_vault_helpers_and_source_binary_absent(
    e2e_workspace: Any,
) -> None:
    """F07 x F10 x F11: .core.opendv.zip offline continuity vs SOURCE_BINARY_ABSENT."""
    from opendv.modeling.models import VerifyDraftRequest
    from opendv.modeling.verify_mode import verify_draft_vault
    from opendv.models.diagnostics import DocumentCitationRef
    from opendv.models.grounding import GroundingPolicy, OntologyGroundReq
    from opendv.models.vault_ir import DraftSourceBinding
    from opendv.service import OpenDVService
    from opendv.service.workflows._vault_helpers import load_verifier_inputs
    from opendv.storage.prover.duckdb_prover import DuckDbProverAdapter

    svc = OpenDVService(e2e_workspace.workspace, state_dir=e2e_workspace.state_dir)
    svc.import_bundle(GOLDEN_BUNDLE_PATH, conflict="fail", enforce_workspace_containment=False)

    with pytest.raises(OpenDVError) as exc_info:
        svc.get_document_pages("am-golden-w1", "src-7c0663dfb9")
    assert exc_info.value.error_code == ErrorCode.SOURCE_BINARY_ABSENT

    ground_resp = asyncio.run(
        svc.discovery.ground_ontology(
            OntologyGroundReq(
                project_id="am-golden-w1",
                policy=GroundingPolicy(mode="deterministic_only", skip_reason="t3_07_offline"),
            )
        )
    )
    assert len(ground_resp.tables) >= 1
    assert sum(len(t.columns) for t in ground_resp.tables) >= 1

    ont, profs, _cl, _lock, cert_ir, _waivers, vctx = load_verifier_inputs(
        svc.state_manager,
        e2e_workspace.workspace,
        "am-golden-w1",
    )
    assert vctx.metadata_only_binary_absent is True
    assert len(vctx.document_maps) == 2
    assert cert_ir is not None

    pdf_map = next(dm for dm in vctx.document_maps if dm.doc_id == "src-7c0663dfb9")
    p5_spec = pdf_map.pages[4]
    quote = "CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE"
    base_draft = _draft_from_certified(cert_ir)
    first_hub = base_draft.hubs[0].model_copy(
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
                            content_sha256=p5_spec.canonical_text_sha256,
                        )
                    ],
                )
            ]
        }
    )
    ok_draft = base_draft.model_copy(update={"hubs": [first_hub, *base_draft.hubs[1:]]})
    prover = DuckDbProverAdapter()
    ok_res = verify_draft_vault(
        VerifyDraftRequest(draft_ir=ok_draft, profiles=profs, ontology=ont, verifier_ctx=vctx),
        prover=prover,
    )
    v4_ok_codes = [
        d.rule_id
        for d in ok_res.verifier_report.v1_v6_diagnostics
        if d.rule_id in {"CITATION_SPAN_UNRESOLVED", "SPAN_HALLUCINATION"}
    ]
    assert v4_ok_codes == []

    bad_hub = first_hub.model_copy(
        update={
            "source_bindings": [
                first_hub.source_bindings[0].model_copy(
                    update={
                        "doc_citations": [
                            first_hub.source_bindings[0].doc_citations[0].model_copy(
                                update={"content_sha256": "0" * 64}
                            )
                        ]
                    }
                )
            ]
        }
    )
    bad_draft = base_draft.model_copy(update={"hubs": [bad_hub, *base_draft.hubs[1:]]})
    bad_res = verify_draft_vault(
        VerifyDraftRequest(draft_ir=bad_draft, profiles=profs, ontology=ont, verifier_ctx=vctx),
        prover=prover,
    )
    assert any(
        d.rule_id == "CITATION_SPAN_UNRESOLVED" for d in bad_res.verifier_report.v1_v6_diagnostics
    )


@pytest.mark.hardening
def test_t3_08_cli_validate_redacted_ir_and_raw_placeholder_to_markdown(
    cli_runner: Any,
    e2e_workspace: Any,
) -> None:
    """F04 x F06 x F08: CLI parse validate accepts redacted IR & raw [REDACTED_*] -> markdown."""
    raw_pdf = make_minimal_pdf(
        e2e_workspace.workspace / "legacy_marker.pdf",
        "Legacy marker [REDACTED_LEGACY] and SSN 123-45-6789",
    )
    raw_ir_path = e2e_workspace.workspace / "raw_ir.json"
    red_ir_path = e2e_workspace.workspace / "red_ir.json"
    assert cli_runner(["parse", "extract", str(raw_pdf), "--out", str(raw_ir_path)]).returncode == 0
    assert (
        cli_runner(
            ["parse", "extract", str(raw_pdf), "--redact", "--out", str(red_ir_path)]
        ).returncode
        == 0
    )

    assert cli_runner(["parse", "validate", str(raw_ir_path)]).returncode == 0
    assert cli_runner(["parse", "validate", str(red_ir_path)]).returncode == 0

    md_path = e2e_workspace.workspace / "red_export.md"
    exp = cli_runner(
        ["parse", "export", str(red_ir_path), "--target", "markdown", "--out", str(md_path)]
    )
    assert exp.returncode == 0, exp.stdout
    md_text = md_path.read_text(encoding="utf-8")
    assert "<MASKED_PII>" in md_text
    assert "123-45-6789" not in md_text
    e2e_workspace.assert_no_workspace_opendv_dir()


def test_t3_09_bounded_zip_dir_entry_merged_cells_to_document_map(
    cli_runner: Any,
    e2e_workspace: Any,
) -> None:
    """F02 x F04: BoundedZipReader permits info.is_dir() + merged cells -> document_map."""
    dir_docx = make_dir_entry_docx(e2e_workspace.workspace / "dir_entry.docx")
    assert cli_runner(["parse", "inspect", str(dir_docx)]).returncode == 0

    merged_docx = make_dom_order_merged_docx(
        e2e_workspace.workspace / "merged_map.docx",
        include_boundary_pii=False,
    )
    ir_path = e2e_workspace.workspace / "merged_map.ir.json"
    dm_path = e2e_workspace.workspace / "merged_map.dm.json"
    assert cli_runner(["parse", "extract", str(merged_docx), "--out", str(ir_path)]).returncode == 0
    assert (
        cli_runner(
            ["parse", "export", str(ir_path), "--target", "document_map", "--out", str(dm_path)]
        ).returncode
        == 0
    )
    ir_data = json.loads(ir_path.read_text(encoding="utf-8"))
    tbl_blocks = [b for b in ir_data["blocks"] if b["kind"] == "table"]
    assert len(tbl_blocks) >= 1
    assert len(tbl_blocks[0]["table_headers"]) == 3
    assert all(len(row) == 3 for row in tbl_blocks[0]["table_rows"])
    dm_data = json.loads(dm_path.read_text(encoding="utf-8"))
    assert dm_data["document_map"]["format"] == "docx"
    assert dm_data["document_map"]["page_count"] == 1
    assert dm_data["document_map"]["pages"][0]["table_headers"] == ["Region Metric", "Value"]


def test_t3_10_ocr_provider_scanned_fixture_blackbox_and_page_map_spec(
    e2e_workspace: Any,
) -> None:
    """F05 x F06 x F12: OcrProviderFn + scanned institutional PDF -> black-box PNG + PageMapSpec."""
    from opendv.models.document_ir import OcrBox
    from opendv.service.parsing_svc import ParsingService

    def _fake_ocr(_png_bytes: bytes, page_num: int) -> tuple[list[OcrBox], str]:
        if page_num == 2:
            return (
                [
                    OcrBox(x0=20, y0=20, x1=120, y1=50, text="Signatory"),
                    OcrBox(x0=130, y0=20, x1=260, y1=50, text="123-45-6789"),
                ],
                "Signatory 123-45-6789",
            )
        return ([], "")

    svc = ParsingService(
        workspace_root=e2e_workspace.workspace,
        allowed_roots=[REPO_ROOT, e2e_workspace.workspace],
        ocr_provider=_fake_ocr,
    )
    ir = svc.parse_document(SAMPLE_SCANNED_PDF, sink_class="LOCAL_HUMAN_IO", redaction="off")
    dm_proj = svc.export_ir(ir, target="document_map", sink_class="MODEL_EGRESS", redaction="on")
    dmap = dm_proj["document_map"]
    assert dmap.pages[0].is_scanned_or_low_density is False
    assert dmap.pages[1].is_scanned_or_low_density is True

    png_bytes, rmeta = svc.render_document_page(
        SAMPLE_SCANNED_PDF,
        2,
        sink_class="MODEL_EGRESS",
        redaction="on",
    )
    assert rmeta.ocr_used is True
    assert rmeta.redacted is True
    assert rmeta.ocr_boxes_applied >= 1
    assert _count_black_pixels(png_bytes) > 0


@pytest.mark.hardening
def test_t3_11_source_blob_from_bytes_parsing_svc_validate_and_cleanup(
    e2e_workspace: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F07 x F04: SourceBlob.from_bytes -> ParsingService -> validate_ir -> __exit__ & error cleanup."""
    from opendv.models.document_ir import DEFAULT_MAX_SOURCE_BYTES, SourceBlob
    from opendv.service.parsing_svc import ParsingService

    pdf_path = make_minimal_pdf(e2e_workspace.workspace / "raw_src.pdf", "Spooled bytes payload.")
    raw_bytes = pdf_path.read_bytes()

    with SourceBlob.from_bytes(raw_bytes, filename_hint="spooled.pdf") as blob:
        spool_path = blob.path
        assert spool_path.is_file()
        svc = ParsingService(
            workspace_root=e2e_workspace.workspace,
            allowed_roots=[spool_path.parent, e2e_workspace.workspace],
        )
        ir = svc.parse_document(spool_path, sink_class="LOCAL_HUMAN_IO", redaction="off")
        assert svc.validate_ir(ir) == []
    assert not spool_path.exists(), "SourceBlob.__exit__ must unlink spooled temp file"

    partial_target = e2e_workspace.workspace / "partial_fail.pdf"

    class _FailingSpool:
        def __init__(self, path: Path) -> None:
            self.name = str(path)
            path.write_bytes(b"%PDF")

        def write(self, _data: bytes) -> int:
            raise OSError("Simulated WinError 32 partial write failure")

        def flush(self) -> None:
            return None

        def __enter__(self) -> _FailingSpool:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        tempfile,
        "NamedTemporaryFile",
        lambda **_kwargs: _FailingSpool(partial_target),
    )
    with pytest.raises(OpenDVError) as exc_info:
        SourceBlob.from_bytes(b"hello", filename_hint="fail.pdf")
    assert exc_info.value.error_code == ErrorCode.INTERNAL_SERVER_ERROR
    assert not partial_target.exists(), "Partial spool must be unlinked on write OSError"

    with pytest.raises(OpenDVError) as cap_exc:
        SourceBlob.from_bytes(b"x", max_bytes=0)
    assert cap_exc.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED
    assert DEFAULT_MAX_SOURCE_BYTES == 262_144_000


@pytest.mark.hardening
def test_t3_12_cli_ontology_ground_multi_doc_pii_strip_and_model_profile_plain(
    cli_runner: Any,
    e2e_workspace: Any,
    public_fixture: Any,
) -> None:
    """F08 x F10 x F11: ontology ground --input --doc + model profile plain formatter."""
    pii_txt = e2e_workspace.workspace / "pii_notes.txt"
    pii_txt.write_text(
        "Account SSN 123-45-6789 and email analyst@example.com for custody ledger.\n",
        encoding="utf-8",
    )
    pdf_doc = make_minimal_pdf(e2e_workspace.workspace / "guide.pdf", "Custody ledger brief.")
    docx_doc = public_fixture("aspose_formatted_elements.docx")

    req_path = e2e_workspace.workspace / "gnd_req.json"
    req_path.write_text(
        json.dumps(
            {
                "tables": [
                    {
                        "table_id": "t_acc",
                        "table_name": "accounts",
                        "columns": [{"column_name": "account_id", "data_type": "VARCHAR", "is_pk": True}],
                    }
                ],
                "global_steward_notes": [],
                "policy": {"mode": "deterministic_only", "skip_reason": "t3_12"},
            }
        ),
        encoding="utf-8",
    )
    gnd = cli_runner(
        [
            "ontology",
            "ground",
            "--input",
            str(req_path),
            "--doc",
            str(pii_txt),
            "--doc",
            str(pdf_doc),
            "--doc",
            str(docx_doc),
        ]
    )
    assert gnd.returncode == int(CliExitCode.OK), gnd.stderr or gnd.stdout
    assert "123-45-6789" not in gnd.stdout
    assert "<MASKED_PII>" not in gnd.stdout

    csv_path = e2e_workspace.workspace / "accounts.csv"
    csv_path.write_text("account_id,name\nA1,Alpha\n", encoding="utf-8")
    plain_prof = cli_runner(
        [
            "model",
            "profile",
            "--source",
            f"csv:{csv_path}",
            "--source",
            f"pdf:{pdf_doc}",
            "--source",
            f"docx:{docx_doc}",
        ],
        expect_json=False,
    )
    assert plain_prof.returncode == int(CliExitCode.OK), plain_prof.stderr or plain_prof.stdout
    assert "document_map" in plain_prof.stdout.lower()
    e2e_workspace.assert_no_workspace_opendv_dir()


def test_t3_13_liteparse_inspect_max_pages_with_windowed_extract_and_render(
    e2e_workspace: Any,
) -> None:
    """F01 x F05: inspect unconstrained by max_pages paired with windowed extract & render caps."""
    from opendv.models.document_ir import ParseOptions
    from opendv.service.parsing_svc import ParsingService

    fdic_pdf = REPO_ROOT / "examples" / "sample_pdfs" / "fdic_section3_2_loans_credit_risk.pdf"
    svc = ParsingService(
        workspace_root=e2e_workspace.workspace,
        allowed_roots=[REPO_ROOT, e2e_workspace.workspace],
    )
    ins = svc.inspect_document(fdic_pdf, options=ParseOptions(max_pages=2))
    assert ins.page_count == 88

    with pytest.raises(OpenDVError) as max_exc:
        svc.parse_document(fdic_pdf, options=ParseOptions(max_pages=2))
    assert max_exc.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED

    window_ir = svc.parse_document(fdic_pdf, options=ParseOptions(pages=[1, 3], max_pages=2))
    assert [p.page_number for p in window_ir.pages] == [1, 3]

    high_px = make_high_pixel_pdf(e2e_workspace.workspace / "huge.pdf", 4000.0, 4000.0)
    with pytest.raises(OpenDVError) as px_exc:
        svc.render_document_page(high_px, 1, options=ParseOptions(dpi=150))
    assert px_exc.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED

    small_pdf = make_minimal_pdf(e2e_workspace.workspace / "small.pdf")
    with pytest.raises(OpenDVError) as dpi_exc:
        svc.render_document_page(small_pdf, 1, options=ParseOptions(dpi=301))
    assert dpi_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


def test_t3_14_crlf_vs_lf_canonical_sha256_parity_across_projections(
    cli_runner: Any,
    e2e_workspace: Any,
) -> None:
    """F03 x F04 x F12: CRLF vs LF produces identical canonical_text_sha256 through export."""
    lf_txt = SRC_MATRIX_DIR / "enterprise_msa_addendum_2026.txt"
    crlf_txt = SRC_MATRIX_DIR / "enterprise_msa_addendum_2026_crlf.txt"

    digests: list[str] = []
    chunk_counts: list[int] = []
    for label, path in (("lf", lf_txt), ("crlf", crlf_txt)):
        ir_path = e2e_workspace.workspace / f"{label}.ir.json"
        dm_path = e2e_workspace.workspace / f"{label}.dm.json"
        ck_path = e2e_workspace.workspace / f"{label}.chunks.jsonl"
        assert cli_runner(["parse", "extract", str(path), "--out", str(ir_path)]).returncode == 0
        assert (
            cli_runner(
                ["parse", "export", str(ir_path), "--target", "document_map", "--out", str(dm_path)]
            ).returncode
            == 0
        )
        assert (
            cli_runner(
                [
                    "parse",
                    "export",
                    str(ir_path),
                    "--target",
                    "chunks",
                    "--format",
                    "jsonl",
                    "--out",
                    str(ck_path),
                ]
            ).returncode
            == 0
        )
        dm = json.loads(dm_path.read_text(encoding="utf-8"))["document_map"]
        digests.append(dm["pages"][0]["canonical_text_sha256"])
        rows = query_ndjson_with_duckdb(
            ck_path, "SELECT DISTINCT canonical_text_sha256 AS sha FROM chunks"
        )
        chunk_counts.append(len(rows))
        assert rows[0]["sha"] == digests[-1]

    assert digests[0] == digests[1]
    assert chunk_counts == [1, 1]


def test_t3_15_mcp_profile_all_23_tools_source_list_and_parse_inspect(
    cli_runner: Any,
    e2e_workspace: Any,
) -> None:
    """F07 x F09: MCP --profile all (23 tools) + profile flag isolation across project/parsing."""
    from opendv.models.wire_dtos_discovery import ProjectInitReq, SourceAttachReq
    from opendv.service import OpenDVService

    svc = OpenDVService(e2e_workspace.workspace, state_dir=e2e_workspace.state_dir)
    svc.init_project(ProjectInitReq(project_id="mcp-all-p1", domain_namespace="ibor"))
    pdf_path = make_minimal_pdf(e2e_workspace.workspace / "attached.pdf", "MCP profile all doc.")
    svc.attach_source("mcp-all-p1", SourceAttachReq(kind="pdf", uri_or_filename=str(pdf_path)))

    async def _run() -> None:
        async with open_mcp_session(
            "all",
            workspace=e2e_workspace,
            project="mcp-all-p1",
            allow_roots=[e2e_workspace.workspace],
        ) as (session, tools):
            assert len(tools) == 23
            is_err, src_data, _ = await call_mcp_tool_json(session, "source_list", {})
            assert not is_err and len(src_data) == 1

            is_err, ins_data, _ = await call_mcp_tool_json(
                session, "parse_inspect", {"uri": str(pdf_path)}
            )
            assert not is_err and ins_data["page_count"] == 1

    asyncio.run(_run())

    bad_proj = cli_runner(["mcp", "serve", "--profile", "project", "--spill-dir", str(e2e_workspace.workspace)])
    assert bad_proj.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert bad_proj.envelope is not None
    assert bad_proj.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    bad_parse = cli_runner(["mcp", "serve", "--profile", "parsing", "--project", ""])
    assert bad_parse.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert bad_parse.envelope is not None
    assert bad_parse.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


def test_t3_16_text_mask_before_24k_and_zero_byte_empty_slice(e2e_workspace: Any) -> None:
    """F03 x F06: Markdown/TXT mask-before-24k split + 0-byte empty slice contract."""
    from opendv.service.parsing_svc import ParsingService

    svc = ParsingService(workspace_root=e2e_workspace.workspace)
    for ext in (".md", ".txt"):
        pii_file = make_boundary_pii_text_file(
            e2e_workspace.workspace / f"boundary{ext}",
            split_offset=VIRTUAL_PAGE_SPLIT_CHARS,
        )
        red_ir = svc.parse_document(pii_file, sink_class="MODEL_EGRESS", redaction="on")
        assert red_ir.provenance.redacted is True
        assert all(len(p.canonical_text) <= VIRTUAL_PAGE_SPLIT_CHARS for p in red_ir.pages)
        assert all("123-45-6789" not in p.canonical_text for p in red_ir.pages)
        assert any("<MASKED_PII>" in p.canonical_text for p in red_ir.pages)

        empty_file = make_empty_text_file(e2e_workspace.workspace / f"empty{ext}")
        empty_ir = svc.parse_document(empty_file, sink_class="LOCAL_HUMAN_IO", redaction="off")
        assert len(empty_ir.pages) == 1
        assert empty_ir.pages[0].extracted_via == "empty"
        assert empty_ir.pages[0].renderable is False
        assert empty_ir.pages[0].slice_kind == "virtual_section_page"
        dmap = svc.export_ir(empty_ir, target="document_map")["document_map"]
        assert dmap.page_count == 1


@pytest.mark.hardening
def test_t3_17_missing_ocr_driver_remediation_agent_vs_human(
    e2e_workspace: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F06 x F07: Missing pytesseract (agent) vs missing tesseract binary (human), zero skips."""
    import importlib.util

    from opendv.models.document_ir import ParseOptions
    from opendv.service.parsing_svc import ParsingService

    scan_pdf = make_scanned_image_pdf(e2e_workspace.workspace / "scan_ocr.pdf")
    svc = ParsingService(workspace_root=e2e_workspace.workspace)

    real_find_spec = importlib.util.find_spec
    real_which = shutil.which

    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, package=None: None if name == "pytesseract" else real_find_spec(name, package),
    )
    with pytest.raises(OpenDVError) as agent_exc:
        svc.render_document_page(
            scan_pdf,
            1,
            sink_class="MODEL_EGRESS",
            redaction="on",
            options=ParseOptions(ocr=True),
        )
    assert agent_exc.value.error_code == ErrorCode.DRIVER_MISSING
    assert agent_exc.value.remediation is not None
    assert any(
        a.actor == "agent" and a.command and "ocr" in a.command
        for a in agent_exc.value.remediation.actions
    )

    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, package=None: object() if name == "pytesseract" else real_find_spec(name, package),
    )
    monkeypatch.setattr(
        shutil,
        "which",
        lambda cmd, *args, **kwargs: None if cmd == "tesseract" else real_which(cmd, *args, **kwargs),
    )
    with pytest.raises(OpenDVError) as human_exc:
        svc.render_document_page(
            scan_pdf,
            1,
            sink_class="MODEL_EGRESS",
            redaction="on",
            options=ParseOptions(ocr=True),
        )
    assert human_exc.value.error_code == ErrorCode.DRIVER_MISSING
    assert human_exc.value.remediation is not None
    assert any(a.actor == "human" for a in human_exc.value.remediation.actions)


def test_t3_18_cli_parse_validate_tamper_jsonl_no_out_and_api_url_reject(
    cli_runner: Any,
    e2e_workspace: Any,
) -> None:
    """F04 x F08: CLI parse validate tamper + jsonl without --out + --api-url rejection."""
    pdf_path = make_minimal_pdf(e2e_workspace.workspace / "tamper.pdf", "Tamper test.")
    ir_path = e2e_workspace.workspace / "tamper.ir.json"
    assert cli_runner(["parse", "extract", str(pdf_path), "--out", str(ir_path)]).returncode == 0

    ir_data = json.loads(ir_path.read_text(encoding="utf-8"))
    ir_data["pages"][0]["canonical_text_sha256"] = "f" * 64
    tampered_path = e2e_workspace.workspace / "tampered.ir.json"
    tampered_path.write_text(json.dumps(ir_data), encoding="utf-8")

    val = cli_runner(["parse", "validate", str(tampered_path)])
    assert val.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert val.envelope is not None and val.envelope.ok is False
    assert val.envelope.error_code == ErrorCode.VERIFICATION_GATE_FAILED
    assert "CANONICAL_HASH_MISMATCH" in val.envelope.model_dump_json()

    no_out = cli_runner(["parse", "export", str(ir_path), "--target", "chunks", "--format", "jsonl"])
    assert no_out.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert no_out.envelope is not None
    assert no_out.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    bad_api = cli_runner(["--api-url", "http://127.0.0.1:9", "parse", "spec"])
    assert bad_api.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert bad_api.envelope is not None
    assert bad_api.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Zip bomb & XXE checks combined with CLI validation
    for bad_docx, expected_code in (
        (
            make_zip_bomb_entry_count_docx(e2e_workspace.workspace / "bomb_cnt.docx"),
            ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
        ),
        (
            make_zip_bomb_ratio_docx(e2e_workspace.workspace / "bomb_ratio.docx"),
            ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
        ),
        (
            make_xxe_doctype_docx(e2e_workspace.workspace / "xxe.docx"),
            ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        ),
    ):
        bad_res = cli_runner(["parse", "inspect", str(bad_docx)])
        assert bad_res.returncode == int(CliExitCode.VALIDATION_ERROR)
        assert bad_res.envelope is not None
        assert bad_res.envelope.error_code == expected_code
    e2e_workspace.assert_no_workspace_opendv_dir()
