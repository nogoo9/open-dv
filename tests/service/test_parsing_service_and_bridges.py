"""Deterministic tests for M3 Layer-4 ParsingService, legacy cutover, and bridges."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import json
import os
import random
import shutil
import stat
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from opendv.agents.ports import AgentTurnRequest, AgentTurnResult
from opendv.modeling.models import DVModelingRequest
from opendv.models.diagnostics import DocumentCitationRef
from opendv.models.document_ir import (
    DocumentBlock,
    DocumentOutlineNode,
    PageSlice,
    PageSpan,
    ParsedDocumentIR,
    ParseOptions,
    ParseProvenance,
    SourceBlob,
    compute_canonical_text_sha256,
)
from opendv.parsing.redaction import redact_ir
from opendv.models.errors import CliExitCode, ErrorCode, OpenDVError
from opendv.models.grounding import GroundingPolicy, OntologyGroundReq
from opendv.models.grounding_spi import TableRerankAssignment, TableRerankResult
from opendv.models.ontology import GraphAOntology
from opendv.models.pii_tokenizer import REDACTED_LEAK_RE, ensure_no_redacted_placeholder
from opendv.models.project import HarnessProvider
from opendv.models.sources import DocumentMap, ExternalMcpServerSpec, PageMapSpec
from opendv.models.vault_ir import (
    BusinessKeyPart,
    ColumnTriageEntry,
    DraftField,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
    LinkParticipantSpec,
)
from opendv.models.wire_dtos_discovery import ProjectInitReq, SourceAttachReq, SourceReattachReq
from opendv.models.wire_dtos_vault import ProfileBundle
from opendv.service import OpenDVService, ParsingService, resolve_local_source_blob
from opendv.service._ext_mcp_guards import (
    _fit_png_within_broker_budget,
    resolve_doc_page_handles,
    run_ocr_on_pdf_bytes,
)
from opendv.service._mcp_ports import OcrBox
from opendv.service._mcp_tools import (
    BrokerToolContext,
    scout_read_document_pages,
    scout_view_page_image,
)
from opendv.service._remediation import build_remediation
from opendv.service.bundles.zip_guard import WIN_REPARSE_ATTR
from opendv.service.workflows._discovery_run_ctx import build_source_and_doc_context
from opendv.service.workflows._modeling_stateless_helpers import merge_profile_bundle
from opendv.service.workflows._source_docs import load_doc_pages_for_record
from opendv.service.workflows._vault_helpers import load_verifier_inputs
from opendv.service.workflows.modeling_stateless import (
    profile_sources_stateless,
    run_model_stateless,
    verify_draft_stateless,
)
from opendv.verifier._v4_unstructured import verify_v4_unstructured
from opendv.verifier.l1_codemods import run_l1_codemods
from tests.helpers import DeterministicMockHarness, MockTurnScript

REPO_ROOT = Path(__file__).resolve().parents[2]
CUSTODY_PDF = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"
STEWARDSHIP_MD = REPO_ROOT / "data" / "public_am" / "am_stewardship_domain_notes.md"
IBOR_SQLITE = REPO_ROOT / "data" / "public_am" / "am_sec_treasury_ibor.sqlite"
SCANNED_PDF = REPO_ROOT / "examples" / "sample_pdfs" / "institutional_scanned_schedule_ocr_test.pdf"
ASPOSE_DOCX = REPO_ROOT / "tests" / "parsing" / "fixtures_public" / "aspose_merged_table.docx"
RFC_TXT = REPO_ROOT / "tests" / "parsing" / "fixtures_public" / "rfc4180_csv.txt"
CORE_BUNDLE_ZIP = REPO_ROOT / "tests" / "golden_v2" / "am_core_bundle.core.opendv.zip"


def _make_simple_pdf(path: Path, text: str) -> Path:
    """Write a minimal single-page valid PDF without pypdf dependency."""
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT\n/F1 12 Tf\n72 720 Td\n({safe}) Tj\nET\n".encode("latin-1")
    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
        ),
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        (
            f"5 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
            + stream
            + b"endstream\nendobj\n"
        ),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        (
            f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("ascii")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


def _build_citation_draft(
    *,
    doc_id: str,
    page_number: int,
    verbatim_quote: str,
    occurrence: int,
    char_start: int,
    char_end: int,
    content_sha256: str,
) -> DraftVaultIR:
    prov_fields = [
        DraftField(name="extractor_version", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        DraftField(name="prompt_or_rule_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        DraftField(
            name="confidence_score",
            logical_type={"kind": "DECIMAL", "precision": 5, "scale": 4},
            nullable=False,
            is_pii=False,
        ),
        DraftField(name="review_status", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        DraftField(name="citation_span", logical_type="JSON_DOCUMENT", nullable=False, is_pii=False),
        DraftField(name="source_doc_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
    ]
    cit = DocumentCitationRef(
        doc_id=doc_id,
        page_number=page_number,
        verbatim_quote=verbatim_quote,
        occurrence=occurrence,
        char_start=char_start,
        char_end=char_end,
        content_sha256=content_sha256,
    )
    binding = DraftSourceBinding(
        source_id=doc_id,
        table_id=f"{doc_id}:page_{page_number}",
        col_mappings={},
        doc_citations=[cit],
    )
    tid = f"{doc_id}:page_{page_number}"
    return DraftVaultIR(
        project_id="proj-m3",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_clause",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=tid,
                        col_name="clause_id",
                        source_id=doc_id,
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="DOC",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=doc_id,
                        table_id=tid,
                        col_mappings={"clause_id": "clause_id"},
                    )
                ],
                attributes=[],
            ),
            DraftHubSpec(
                hub_name="hub_policy",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=tid,
                        col_name="policy_id",
                        source_id=doc_id,
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="DOC",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=doc_id,
                        table_id=tid,
                        col_mappings={"policy_id": "policy_id"},
                    )
                ],
                attributes=[],
            ),
        ],
        links=[
            DraftLinkSpec(
                link_name="lnk_clause_policy",
                link_kind="UNSTRUCTURED_EXTRACTION_LINK",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_clause",
                        role_name="clause",
                        fk_col_name="hk_clause",
                        source_col_names=["clause_id"],
                    ),
                    LinkParticipantSpec(
                        hub_name="hub_policy",
                        role_name="policy",
                        fk_col_name="hk_policy",
                        source_col_names=["policy_id"],
                    ),
                ],
                source_bindings=[binding],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_clause_ai_prov",
                parent_name="lnk_clause_policy",
                sat_kind="AI_PROVENANCE_SATELLITE",
                attributes=prov_fields,
                source_bindings=[binding],
            )
        ],
        column_triage=[
            ColumnTriageEntry(
                table_id=tid,
                col_name="clause_id",
                disposition="MODELED",
                target_ref="hub_clause",
            ),
            ColumnTriageEntry(
                table_id=tid,
                col_name="policy_id",
                disposition="MODELED",
                target_ref="hub_policy",
            ),
            ColumnTriageEntry(
                table_id=tid,
                col_name="clause_text",
                disposition="MODELED",
                target_ref="lnk_clause_policy",
            ),
        ],
    )


def test_resolve_local_source_blob_security_and_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify containment, Windows drive paths, file URIs, symlinks, UNC, ADS, 404, and 413."""
    ws = tmp_path / "ws"
    ws.mkdir()
    sample = ws / "note.txt"
    sample.write_text("Hello custody world.", encoding="utf-8")

    # 1. Valid path & file:/// URI with query/fragment sanitized
    blob_path = resolve_local_source_blob(str(sample), allowed_roots=[ws])
    dirty_uri = f"{sample.resolve().as_uri()}?token=secret_q#frag"
    blob_uri = resolve_local_source_blob(dirty_uri, allowed_roots=[ws])
    assert blob_path.binary_sha256 == blob_uri.binary_sha256
    assert blob_path.is_spooled_temp is False
    assert "?" not in blob_uri.uri_sanitized and "#" not in blob_uri.uri_sanitized
    assert "secret_q" not in blob_uri.uri_sanitized

    # 2. 400 SYNTAX_INVALID_ARGUMENT cases
    for bad_arg in (
        "",
        "   ",
        str(ws / "bad\x00.txt"),
        "file::https://example.com/a.pdf",
        "https://example.com/a.pdf",
        "file://remotehost/share/a.pdf",
        str(ws / "CON.pdf"),
    ):
        with pytest.raises(OpenDVError) as exc_400:
            resolve_local_source_blob(bad_arg, allowed_roots=[ws])
        assert exc_400.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT
        assert exc_400.value.http_status == 400

    # 3. 422 ARCHIVE_SECURITY_VIOLATION cases (UNC, ADS, traversal, root escape)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    for sec_arg in (
        r"\\server\share\doc.pdf",
        "//server/share/doc.pdf",
        f"{sample}:ads_stream",
        str(ws / ".." / "outside.txt"),
        str(outside),
    ):
        with pytest.raises(OpenDVError) as exc_422:
            resolve_local_source_blob(sec_arg, allowed_roots=[ws])
        assert exc_422.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
        assert exc_422.value.http_status == 422

    # 4. Symlink / reparse point inside allowed root -> 422
    rep_dir = ws / "reparse_sub"
    rep_dir.mkdir()
    rep_file = rep_dir / "inner.txt"
    rep_file.write_text("inner", encoding="utf-8")
    orig_lstat = Path.lstat

    def _fake_lstat(p: Path, *args: Any, **kwargs: Any) -> Any:
        st = orig_lstat(p, *args, **kwargs)
        if os.path.normcase(str(p)) == os.path.normcase(str(rep_dir)):
            return types.SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o755,
                st_file_attributes=WIN_REPARSE_ATTR,
                st_size=st.st_size,
            )
        return st

    monkeypatch.setattr(Path, "lstat", _fake_lstat)
    with pytest.raises(OpenDVError) as exc_sym:
        resolve_local_source_blob(rep_file, allowed_roots=[ws])
    assert exc_sym.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    monkeypatch.undo()

    # 5. Missing file -> 404 RESOURCE_NOT_FOUND
    with pytest.raises(OpenDVError) as exc_404:
        resolve_local_source_blob(ws / "missing.pdf", allowed_roots=[ws])
    assert exc_404.value.error_code == ErrorCode.RESOURCE_NOT_FOUND
    assert exc_404.value.exit_code == CliExitCode.NOT_FOUND
    assert exc_404.value.http_status == 404

    # 6. Exceeds max_bytes -> 413 ARCHIVE_SIZE_LIMIT_EXCEEDED
    with pytest.raises(OpenDVError) as exc_413:
        resolve_local_source_blob(sample, allowed_roots=[ws], max_bytes=5)
    assert exc_413.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED
    assert exc_413.value.http_status == 413


def test_parsing_service_stateless_lifecycle_and_remediation(tmp_path: Path) -> None:
    """Verify stateless ParsingService lifecycle, zero .opendv/ writes, and OCR remediation."""
    ws = tmp_path / "stateless_ws"
    ws.mkdir()
    pii_pdf = _make_simple_pdf(ws / "pii.pdf", "Contact SSN 123-45-6789 today.")

    svc = ParsingService(workspace_root=ws)
    spec = svc.get_spec()
    caps = svc.get_capabilities()
    assert "ParsedDocumentIR" in spec.schemas
    assert "ChunkRecord" in spec.schemas
    assert set(spec.formats) == {"pdf", "docx", "markdown", "domain_note"}
    assert set(spec.projectors) == {"document_map", "markdown", "chunks"}
    assert caps.pillow_version is not None

    insp = svc.inspect_document(pii_pdf)
    assert insp.page_count == 1 and insp.renderable is True

    raw_ir = svc.parse_document(pii_pdf, sink_class="LOCAL_HUMAN_IO")
    assert "123-45-6789" in raw_ir.pages[0].canonical_text
    assert svc.validate_ir(raw_ir) == []

    egress_ir = svc.parse_document(pii_pdf, sink_class="MODEL_EGRESS")
    assert egress_ir.provenance.redacted is True
    assert "123-45-6789" not in egress_ir.pages[0].canonical_text
    assert svc.validate_ir(egress_ir) == []

    jsonl_lines = list(svc.stream_export(raw_ir, target="chunks", sink_class="MODEL_EGRESS"))
    assert jsonl_lines and all("123-45-6789" not in line for line in jsonl_lines)
    assert not (ws / ".opendv").exists()

    _, render_res = svc.render_document_page(
        pii_pdf,
        1,
        sink_class="MODEL_EGRESS",
    )
    assert render_res.redacted is True

    # Remediation branches: default Databricks vs extra_name="ocr"
    rem_db = build_remediation(ErrorCode.DRIVER_MISSING, provider=None)
    assert any("databricks" in (a.command or "") for a in rem_db.actions)

    with patch("opendv.service._remediation.importlib.util.find_spec", return_value=None):
        rem_ocr_pkg = build_remediation(ErrorCode.DRIVER_MISSING, extra_name="ocr")
    assert any(a.actor == "agent" and "uv sync --extra ocr" in (a.command or "") for a in rem_ocr_pkg.actions)

    rem_ocr_bin = build_remediation(ErrorCode.DRIVER_MISSING, extra_name="ocr", tesseract_missing=True)
    assert any(a.actor == "human" and "tesseract" in a.label.lower() for a in rem_ocr_bin.actions)


def test_step2_to_4_cutover_and_partial_window_cache(tmp_path: Path) -> None:
    """Verify OpenDVService.parsing, dict[int, str] parsed_pages, and partial-window pushdown."""
    ws = tmp_path / "proj_ws"
    ws.mkdir()
    svc = OpenDVService(ws, state_dir=tmp_path / "state")
    assert isinstance(svc.parsing, ParsingService)

    svc.init_project(ProjectInitReq(project_id="p_m3", domain_namespace="asset_management"))
    local_pdf = ws / "custody.pdf"
    local_pdf.write_bytes(CUSTODY_PDF.read_bytes())

    attached = svc.attach_source("p_m3", SourceAttachReq(kind="pdf", uri_or_filename=str(local_pdf)))
    uploaded = svc.upload_source(
        "p_m3", filename="notes.md", content=STEWARDSHIP_MD.read_bytes(), kind="markdown"
    )
    reattached = svc.reattach_source("p_m3", attached.source_id, SourceReattachReq())
    att_sha = svc.state_manager.get_source_record("p_m3", attached.source_id)["binary_sha256"]
    reatt_sha = svc.state_manager.get_source_record("p_m3", reattached.source_id)["binary_sha256"]
    assert reattached.source_id == attached.source_id
    assert reatt_sha == att_sha

    rec = svc.state_manager.get_source_record("p_m3", attached.source_id)
    dmap = DocumentMap.model_validate(rec["document_map"])
    page_dict = load_doc_pages_for_record(
        svc.state_manager, ws, "p_m3", attached.source_id, rec, dmap, parsing_service=svc.parsing
    )
    assert isinstance(page_dict, dict)
    assert set(page_dict.keys()) == {1, 2, 3, 4, 5, 6}

    _profs, ctx_pages, summaries = build_source_and_doc_context(
        svc.state_manager, "p_m3", ws, parsing_service=svc.parsing
    )
    assert isinstance(ctx_pages[attached.source_id], dict)
    assert ctx_pages[attached.source_id][4] == ""  # unapproved scanned page 4 gated
    assert "CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE" in ctx_pages[attached.source_id][5]
    assert len(summaries) == 2 and uploaded.source_id in ctx_pages

    # Partial-window cache fill on scout_read_document_pages
    seen_windows: list[list[int] | None] = []
    real_psvc = ParsingService(allowed_roots=[ws])
    orig_parse = real_psvc.parse_document

    def _spy_parse(source: Any, options: ParseOptions | None = None, **kwargs: Any) -> Any:
        seen_windows.append(list(options.pages) if options and options.pages else None)
        return orig_parse(source, options=options, **kwargs)

    spy_psvc = MagicMock(wraps=real_psvc)
    spy_psvc.parse_document.side_effect = _spy_parse
    broker_ctx = BrokerToolContext(
        project_id="p_m3",
        run_id="run-m3",
        state_store=svc.state_manager,
        parsing_service=spy_psvc,
    )

    v12 = scout_read_document_pages(broker_ctx, attached.source_id, 1, 2)
    assert [v.page_number for v in v12] == [1, 2]
    assert seen_windows == [[1, 2]]
    assert set(broker_ctx.parsed_pages[attached.source_id].keys()) == {1, 2}

    v24 = scout_read_document_pages(broker_ctx, attached.source_id, 2, 4)
    assert [v.page_number for v in v24] == [2, 3, 4]
    assert seen_windows == [[1, 2], [3, 4]]
    assert set(broker_ctx.parsed_pages[attached.source_id].keys()) == {1, 2, 3, 4}
    assert v24[2].is_scanned is True and v24[2].masked_text == ""


def test_external_mcp_ocr_fail_loud_and_allow_unredacted(tmp_path: Path) -> None:
    """Verify _ext_mcp_guards.run_ocr_on_pdf_bytes fails loud on OCR runtime errors & gates scans."""
    pdf_bytes = SCANNED_PDF.read_bytes()

    # 1. Without OCR driver/provider -> clean empty boxes without crash
    with patch("opendv.service._ext_mcp_guards.is_ocr_driver_available", return_value=False):
        boxes, ocr_txt, png_bytes = run_ocr_on_pdf_bytes(pdf_bytes, 2)
    assert boxes == [] and ocr_txt == "" and png_bytes.startswith(b"\x89PNG\r\n\x1a\n")

    # 2. When pytesseract IS installed/available and fails at runtime -> propagate loudly!
    fake_tess = MagicMock()
    fake_tess.Output.DICT = "dict"
    fake_tess.image_to_data.side_effect = RuntimeError("tesseract crashed loudly")
    orig_find_spec = importlib.util.find_spec
    with (
        patch("opendv.parsing.redaction.shutil.which", return_value="/usr/bin/tesseract"),
        patch(
            "opendv.parsing.redaction.importlib.util.find_spec",
            side_effect=lambda name, *a, **kw: (
                object() if name == "pytesseract" else orig_find_spec(name, *a, **kw)
            ),
        ),
        patch.dict(sys.modules, {"pytesseract": fake_tess}),
        pytest.raises(RuntimeError, match="tesseract crashed loudly"),
    ):
        run_ocr_on_pdf_bytes(pdf_bytes, 2)

    # 3. resolve_doc_page_handles respects allow_unredacted_external_ocr
    ws = tmp_path / "ext_ws"
    ws.mkdir()
    svc = OpenDVService(ws, state_dir=tmp_path / "state")
    svc.init_project(ProjectInitReq(project_id="p_ext", domain_namespace="asset_management"))
    local_scan = ws / "scan.pdf"
    local_scan.write_bytes(pdf_bytes)
    src = svc.attach_source("p_ext", SourceAttachReq(kind="pdf", uri_or_filename=str(local_scan)))
    srv = ExternalMcpServerSpec(
        server_id="ext_1",
        transport="stdio",
        command=["python", "-m", "noop"],
        allow_document_pages=True,
    )
    args = {"doc_page": {"$opendv_handle": "doc_page", "doc_id": src.source_id, "page_number": 2}}

    # Approved scan page without OCR boxes and without allow_unredacted_external_ocr -> 422
    scan_rec = svc.state_manager.get_source_record("p_ext", src.source_id)
    svc.state_manager.upsert_source(
        "p_ext",
        source_id=src.source_id,
        kind="pdf",
        uri_or_filename=str(local_scan),
        binary_sha256=scan_rec["binary_sha256"],
        connector_config=scan_rec["connector_config"],
        tables_index=scan_rec.get("tables_index", []),
        status="READY_OFFLINE",
        profile=scan_rec["profile"],
        document_map=scan_rec["document_map"],
        approved_scan_pages=[2],
    )
    with pytest.raises(OpenDVError) as exc_blocked:
        resolve_doc_page_handles(
            args, project_id="p_ext", server=srv, state_store=svc.state_manager
        )
    assert exc_blocked.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    # With injected ocr_provider -> succeeds with redacted_png_b64
    resolved, meta, _ids = resolve_doc_page_handles(
        args,
        project_id="p_ext",
        server=srv,
        state_store=svc.state_manager,
        ocr_provider=lambda _b, _p: ([OcrBox(10, 10, 80, 30, "123-45-6789")], "SSN 123-45-6789"),
    )
    assert meta == (src.source_id, 2)
    assert resolved["doc_page"]["redacted_png_b64"]
    assert "123-45-6789" not in resolved["doc_page"]["masked_text"]


def test_grounding_bridge_project_docs_and_offline_fallback(tmp_path: Path) -> None:
    """Verify DiscoveryService.ground_ontology injects PII-stripped notes and handles offline zip."""
    ws = tmp_path / "gnd_ws"
    ws.mkdir()
    captured_notes: list[list[str]] = []

    class _SpyHarness:
        provider_name: HarnessProvider = "claude"

        async def run_turn(
            self,
            request: AgentTurnRequest,
            *,
            event_sink: Any = None,
            scout_dispatcher: Any = None,
        ) -> AgentTurnResult:
            _ = event_sink, scout_dispatcher
            start = request.prompt.find("{")
            end = request.prompt.rfind("}")
            body = json.loads(request.prompt[start : end + 1])
            captured_notes.append(list(body.get("steward_notes", [])))
            out = TableRerankResult(
                assignments=[
                    TableRerankAssignment(
                        column_name=c["column_name"],
                        abstain=True,
                        selected_index=None,
                        confidence=0.5,
                        rationale="Clean.",
                        suggested_search_terms=[],
                    )
                    for c in body["columns"]
                ]
            )
            return AgentTurnResult(
                agent_id=request.agent_id,
                provider=self.provider_name,
                model_id="claude-sonnet-4-6",
                text_output=out.model_dump_json(),
                structured_output=out,
            )

    svc = OpenDVService(ws, state_dir=tmp_path / "state", harness=_SpyHarness())  # type: ignore[arg-type]
    svc.init_project(ProjectInitReq(project_id="p_gnd", domain_namespace="asset_management"))
    csv_p = ws / "tbl.csv"
    csv_p.write_text("ambig_code_x,metric_val_y\nA1,10\n", encoding="utf-8")
    pii_pdf = _make_simple_pdf(ws / "pii_gnd.pdf", "Account holder SSN 123-45-6789 [REDACTED_TAG].")
    svc.attach_source("p_gnd", SourceAttachReq(kind="csv", uri_or_filename=str(csv_p)))
    svc.attach_source("p_gnd", SourceAttachReq(kind="pdf", uri_or_filename=str(pii_pdf)))

    resp = asyncio.run(
        svc.discovery.ground_ontology(
            OntologyGroundReq(project_id="p_gnd", policy=GroundingPolicy(mode="agentic"))
        )
    )
    assert len(resp.tables) == 1
    assert captured_notes and 1 <= len(captured_notes[0]) <= 5
    for note in captured_notes[0]:
        assert len(note) <= 1500
        ensure_no_redacted_placeholder(note)
        assert REDACTED_LEAK_RE.search(note) is None
        assert "123-45-6789" not in note

    # Offline .core.opendv.zip DocumentMap fallback
    off_ws = tmp_path / "off_ws"
    off_ws.mkdir()
    off_svc = OpenDVService(off_ws, state_dir=tmp_path / "off_state")
    off_svc.import_bundle(CORE_BUNDLE_ZIP, conflict="fail", allowed_roots=[REPO_ROOT, off_ws])
    off_resp = asyncio.run(
        off_svc.discovery.ground_ontology(
            OntologyGroundReq(
                project_id="am-golden-w1",
                policy=GroundingPolicy(mode="deterministic_only", skip_reason="offline"),
            )
        )
    )
    assert len(off_resp.tables) >= 1


def test_modeling_bridge_profile_bundle_and_gate_v4(tmp_path: Path) -> None:
    """Verify profile_sources_stateless, merge_profile_bundle, run_model_stateless, and Mode-2 V4."""
    ws = tmp_path / "mod_ws"
    ws.mkdir()
    pii_pdf = _make_simple_pdf(ws / "pii_mod.pdf", "Custody SSN 123-45-6789 clause.")
    bundle = profile_sources_stateless(
        sources=[
            f"pdf:{CUSTODY_PDF}",
            f"pdf:{pii_pdf}",
            f"docx:{ASPOSE_DOCX}",
            f"markdown:{STEWARDSHIP_MD}",
            f"domain_note:{RFC_TXT}",
        ],
        allowed_roots=[ws, REPO_ROOT],
    )
    assert bundle.profiles == []
    assert len(bundle.document_maps) == 5
    pii_dm = bundle.document_maps[1]
    assert "123-45-6789" not in pii_dm.model_dump_json()

    # Raw canonical_text_sha256 is preserved on DocumentMap for Gate V4 even when page has PII
    raw_ir = ParsingService(allowed_roots=[ws]).parse_document(pii_pdf, sink_class="LOCAL_HUMAN_IO")
    assert pii_dm.pages[0].canonical_text_sha256 == raw_ir.pages[0].canonical_text_sha256

    # merge_profile_bundle preserves caller DocumentMap on doc_id collision
    caller_dm = DocumentMap(
        doc_id=pii_dm.doc_id,
        filename="caller_wins.pdf",
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
        {"verifier_ctx": {"document_maps": [caller_dm.model_dump(mode="json")]}},
        ProfileBundle(document_maps=[pii_dm, bundle.document_maps[0]]),
    )
    merged_dms = merged["verifier_ctx"]["document_maps"]
    assert [d["doc_id"] for d in merged_dms] == [pii_dm.doc_id, bundle.document_maps[0].doc_id]
    assert merged_dms[0]["filename"] == "caller_wins.pdf"

    # Mode-1 verify_draft_stateless and run_model_stateless DVModelingRequest branch Gate V4
    cust_dm = bundle.document_maps[0]
    quote = "CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE"
    cust_ir = ParsingService(allowed_roots=[REPO_ROOT]).parse_document(
        CUSTODY_PDF, sink_class="LOCAL_HUMAN_IO"
    )
    p5_txt = cust_ir.pages[4].canonical_text
    idx2 = p5_txt.find(quote, p5_txt.find(quote) + 1)
    draft = _build_citation_draft(
        doc_id=cust_dm.doc_id,
        page_number=5,
        verbatim_quote=quote,
        occurrence=2,
        char_start=idx2,
        char_end=idx2 + len(quote),
        content_sha256=cust_dm.pages[4].canonical_text_sha256,
    )
    ver_res = verify_draft_stateless(
        {"profiles": [], "policy": "symbolic_only", "real_row_proving": False},
        draft_data=draft,
        profile_bundle=ProfileBundle(document_maps=[cust_dm]),
    )
    assert ver_res.status == "CERTIFIED_FAST", ver_res.verifier_report.v1_v6_diagnostics

    req = DVModelingRequest(
        ontology=GraphAOntology(ontology_version="1.0.0", domain_namespace="ibor", concepts=[]),
        profiles=[],
        policy="symbolic_only",
        real_row_proving=False,
    )
    harness = DeterministicMockHarness(
        scripts_by_agent={"ship-vault": [MockTurnScript(structured_output=draft)]}
    )
    mod_res = asyncio.run(
        run_model_stateless(
            req,
            profile_bundle=ProfileBundle(document_maps=[cust_dm]),
            workspace_root=ws,
            state_dir=tmp_path / "mod_state",
            injected_harness=harness,
        )
    )
    assert mod_res.verifier_report is not None
    assert [d for d in mod_res.verifier_report.v1_v6_diagnostics if d.gate == "V4"] == []

    # Mode-2 load_verifier_inputs: 1-based PageSlice keys + missing-binary metadata fallback
    m2_ws = tmp_path / "m2_ws"
    m2_ws.mkdir()
    svc = OpenDVService(m2_ws, state_dir=tmp_path / "m2_state")
    svc.init_project(ProjectInitReq(project_id="p_m2", domain_namespace="asset_management"))
    ont_v1 = GraphAOntology(ontology_version="1.0.0", domain_namespace="asset_management", concepts=[])
    rev_id = "ont-1"
    from opendv.models.project import RevisionMetadata

    svc.state_manager.save_revision(
        "p_m2",
        revision_id=rev_id,
        rev_kind="ontology_version",
        rev_key=rev_id,
        parent_rev_id=None,
        payload=ont_v1,
        metadata=RevisionMetadata(actor="human", reason="init"),
    )
    svc.state_manager.update_project_pointers("p_m2", active_ontology_rev_id=rev_id)

    m2_pdf = m2_ws / "custody_m2.pdf"
    m2_pdf.write_bytes(CUSTODY_PDF.read_bytes())
    m2_db = m2_ws / "ibor.sqlite"
    m2_db.write_bytes(IBOR_SQLITE.read_bytes())
    svc.attach_source("p_m2", SourceAttachReq(kind="sqlite", uri_or_filename=str(m2_db)))
    pdf_src = svc.attach_source("p_m2", SourceAttachReq(kind="pdf", uri_or_filename=str(m2_pdf)))
    _, _, _, _, _, _, ctx_present = load_verifier_inputs(svc.state_manager, m2_ws, "p_m2")
    assert ctx_present.metadata_only_binary_absent is False
    assert set(ctx_present.parsed_doc_pages[pdf_src.source_id].keys()) == {1, 2, 3, 4, 5, 6}

    # Remove attached binary from disk without marking status -> falls back to meta_absent=True
    m2_pdf.unlink()
    _, _, _, _, _, _, ctx_missing = load_verifier_inputs(svc.state_manager, m2_ws, "p_m2")
    assert ctx_missing.metadata_only_binary_absent is True
    assert ctx_missing.parsed_doc_pages == {}
    dm_m2 = next(d for d in ctx_missing.document_maps if d.doc_id == pdf_src.source_id)
    cert_draft = _build_citation_draft(
        doc_id=dm_m2.doc_id,
        page_number=5,
        verbatim_quote=quote,
        occurrence=2,
        char_start=idx2,
        char_end=idx2 + len(quote),
        content_sha256=dm_m2.pages[4].canonical_text_sha256,
    )
    _, cert_ir, _ = run_l1_codemods(cert_draft)
    assert verify_v4_unstructured(cert_ir, ctx_missing) == []


def test_regression_dense_scanned_pdf_fits_broker_budget(tmp_path: Path) -> None:
    """Bug 1: _fit_png_within_broker_budget downscales dense noisy PNGs to fit 64 KiB broker."""
    rng = random.Random(42)
    raw_rgb = rng.randbytes(800 * 1000 * 3)
    noisy_img = Image.frombytes("RGB", (800, 1000), raw_rgb)
    pdf_buf = io.BytesIO()
    noisy_img.save(pdf_buf, format="PDF", resolution=72.0)
    noisy_pdf_bytes = pdf_buf.getvalue()

    seen_pre_budget_sizes: list[tuple[int, int]] = []

    def _ocr_spy(png_in: bytes, _page: int) -> tuple[list[OcrBox], str]:
        with Image.open(io.BytesIO(png_in)) as im:
            seen_pre_budget_sizes.append(im.size)
        return ([OcrBox(10, 10, 90, 30, "4111-1111-1111-1111")], "PAN 4111-1111-1111-1111")

    boxes, ocr_txt, png_out = run_ocr_on_pdf_bytes(noisy_pdf_bytes, 1, ocr_provider=_ocr_spy)
    assert len(boxes) == 1 and "4111-1111-1111-1111" in ocr_txt
    assert seen_pre_budget_sizes and seen_pre_budget_sizes[0][0] >= 800
    assert len(png_out) <= 45_000
    assert len(base64.b64encode(png_out)) <= 65_536

    # Also verify scout_view_page_image succeeds under BrokerBudgetTracker (65,536 bytes)
    ws = tmp_path / "noisy_ws"
    ws.mkdir()
    svc = OpenDVService(ws, state_dir=tmp_path / "noisy_state")
    svc.init_project(ProjectInitReq(project_id="p_noisy", domain_namespace="asset_management"))
    noisy_pdf = ws / "noisy.pdf"
    noisy_pdf.write_bytes(noisy_pdf_bytes)
    src = svc.attach_source("p_noisy", SourceAttachReq(kind="pdf", uri_or_filename=str(noisy_pdf)))
    rec = svc.state_manager.get_source_record("p_noisy", src.source_id)
    svc.state_manager.upsert_source(
        "p_noisy",
        source_id=src.source_id,
        kind="pdf",
        uri_or_filename=str(noisy_pdf),
        binary_sha256=rec["binary_sha256"],
        connector_config=rec["connector_config"],
        tables_index=rec.get("tables_index", []),
        status="READY_OFFLINE",
        profile=rec["profile"],
        document_map=rec["document_map"],
        approved_scan_pages=[1],
    )
    broker_ctx = BrokerToolContext(
        project_id="p_noisy",
        run_id="run-noisy",
        state_store=svc.state_manager,
        parsing_service=svc.parsing,
        ocr_provider=_ocr_spy,
    )
    img_res = scout_view_page_image(broker_ctx, src.source_id, 1)
    assert img_res.redaction_applied is True
    assert len(img_res.redacted_png_b64.encode("ascii")) <= 65_536

    # Floor guard raises ARCHIVE_SIZE_LIMIT_EXCEEDED when budget is impossibly small
    with pytest.raises(OpenDVError) as exc_floor:
        _fit_png_within_broker_budget(png_out, max_png_bytes=16)
    assert exc_floor.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED


def test_regression_file_uri_ads_and_pipe_rejected_with_422(tmp_path: Path) -> None:
    """Bug 2: file:///...doc.pdf:ads raises ARCHIVE_SECURITY_VIOLATION (422), never raw OSError."""
    ws = tmp_path / "ads_ws"
    ws.mkdir()
    doc = ws / "doc.pdf"
    _make_simple_pdf(doc, "Clean page")
    base_uri = doc.resolve().as_uri()

    for bad_uri in (
        f"{base_uri}:hidden",
        f"{base_uri}%3Ahidden",
        f"{base_uri}|pipe",
        "file:///C|/bad/doc.pdf",
    ):
        with pytest.raises(OpenDVError) as exc_sec:
            resolve_local_source_blob(bad_uri, allowed_roots=[ws])
        assert exc_sec.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
        assert exc_sec.value.http_status == 422


def test_regression_grounding_notes_mask_before_slice(tmp_path: Path) -> None:
    """Bug 3: build_grounding_summary_notes strips PII before slicing at 120/240/300 boundaries."""
    ws = tmp_path / "slice_ws"
    ws.mkdir()
    pan = "4111-1111-1111-1111"
    # Straddles 120 (heading_preview), 240 (block snippet), and 300 (page fallback)
    txt_240 = ws / "straddle_240.txt"
    txt_240.write_text(("A" * 226) + pan + " trailing", encoding="utf-8")
    txt_120 = ws / "straddle_120.txt"
    txt_120.write_text(("B" * 108) + pan + " heading", encoding="utf-8")

    psvc = ParsingService(workspace_root=ws, allowed_roots=[ws])
    dmap_fallback = DocumentMap(
        doc_id="dm_raw",
        filename="fallback.txt",
        format="domain_note",
        page_count=1,
        pages=[
            PageMapSpec(
                page_number=1,
                char_count=128,
                has_images=False,
                is_scanned_or_low_density=False,
                ocr_box_count=0,
                heading_preview=("C" * 108) + pan,
                table_headers=[f"col_{pan}"],
                canonical_text_sha256="b" * 64,
            )
        ],
    )
    notes = psvc.build_grounding_summary_notes([txt_240, txt_120, dmap_fallback], max_notes=5)
    assert len(notes) == 3
    for note in notes:
        assert "4111" not in note
        ensure_no_redacted_placeholder(note)
        assert REDACTED_LEAK_RE.search(note) is None


def test_regression_profile_sources_stateless_symlinks_and_file_uris(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug 4: profile_sources_stateless(allowed_roots=None) rejects symlinks and aligns file://."""
    monkeypatch.delenv("OPENDV_WORKSPACE_ROOTS", raising=False)
    ws = tmp_path / "stateless_ws"
    ws.mkdir()
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    outside_pdf = _make_simple_pdf(outside / "out.pdf", "Custody bond report.")

    # 1. Valid non-symlinked out-of-cwd plain path and file:// URI both succeed consistently
    b_plain = profile_sources_stateless(
        sources=[f"pdf:{outside_pdf}"], workspace_root=ws, allowed_roots=None
    )
    b_uri = profile_sources_stateless(
        sources=[f"pdf:{outside_pdf.as_uri()}"], workspace_root=ws, allowed_roots=None
    )
    assert len(b_plain.document_maps) == 1 and len(b_uri.document_maps) == 1

    # 2. Reparse/junction directory & leaf reparse cannot bypass containment when allowed_roots=None
    junc_dir = ws / "junc_out"
    junc_dir.mkdir()
    junc_pdf = _make_simple_pdf(junc_dir / "junc.pdf", "Junction leaked.")
    leaf_pdf = _make_simple_pdf(ws / "leaf_reparse.pdf", "Leaf reparse leaked.")
    orig_lstat = Path.lstat
    orig_realpath = os.path.realpath

    def _fake_lstat(p: Path, *args: Any, **kwargs: Any) -> Any:
        st = orig_lstat(p, *args, **kwargs)
        norm_p = os.path.normcase(str(p))
        if norm_p == os.path.normcase(str(junc_dir)):
            return types.SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o755,
                st_file_attributes=WIN_REPARSE_ATTR,
                st_size=st.st_size,
            )
        if norm_p == os.path.normcase(str(leaf_pdf)):
            return types.SimpleNamespace(
                st_mode=stat.S_IFREG | 0o644,
                st_file_attributes=WIN_REPARSE_ATTR,
                st_size=st.st_size,
            )
        return st

    def _fake_realpath(p: Any, *args: Any, **kwargs: Any) -> Any:
        raw_s = os.fspath(p)
        if os.path.normcase(raw_s) == os.path.normcase(str(junc_dir)):
            return str(outside)
        return orig_realpath(p, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", _fake_lstat)
    monkeypatch.setattr(os.path, "realpath", _fake_realpath)
    for bad_src in (
        f"pdf:{junc_pdf}",
        f"pdf:{junc_pdf.as_uri()}",
        f"pdf:{leaf_pdf}",
        f"pdf:{leaf_pdf.as_uri()}",
    ):
        with pytest.raises(OpenDVError) as exc_junc:
            profile_sources_stateless(sources=[bad_src], workspace_root=ws, allowed_roots=None)
        assert exc_junc.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    monkeypatch.undo()

    # 3. OPENDV_WORKSPACE_ROOTS is still enforced when allowed_roots=None
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOTS", str(ws))
    with pytest.raises(OpenDVError) as exc_env:
        profile_sources_stateless(
            sources=[f"pdf:{outside_pdf}"], workspace_root=ws, allowed_roots=None
        )
    assert exc_env.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION


def test_regression_double_encoded_pipe_illegal_chars_and_dir_paths(tmp_path: Path) -> None:
    """M3-Iter3 Defect 1: %257C, plain '|', Windows illegal chars, and dirs raise OpenDVError."""
    ws = tmp_path / "edge_ws"
    ws.mkdir()
    doc = _make_simple_pdf(ws / "doc.pdf", "Clean page.")
    base_uri = doc.resolve().as_uri()

    # 1. Double-encoded %257C, %253A, and plain '|' paths -> 422 ARCHIVE_SECURITY_VIOLATION
    for bad_sec in (
        f"{base_uri}%257Chidden",
        f"{base_uri}%253Ahidden",
        str(ws / "doc.pdf|hidden"),
        "doc.pdf|hidden",
    ):
        with pytest.raises(OpenDVError) as exc_422:
            resolve_local_source_blob(bad_sec, workspace_root=ws, allowed_roots=[ws])
        assert exc_422.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
        assert exc_422.value.http_status == 422

    # 2. profile_sources_stateless with file:///...%257Chidden -> 422 (never OSError)
    with pytest.raises(OpenDVError) as exc_prof:
        profile_sources_stateless(
            sources=[f"pdf:{base_uri}%257Chidden"],
            workspace_root=ws,
            allowed_roots=[ws],
        )
    assert exc_prof.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    assert exc_prof.value.http_status == 422

    # 3. Illegal Windows filename chars (?, <, >, *, ") -> 400 SYNTAX_INVALID_ARGUMENT on Windows
    expected_illegal_code = (
        ErrorCode.SYNTAX_INVALID_ARGUMENT if os.name == "nt" else ErrorCode.RESOURCE_NOT_FOUND
    )
    for bad_name in ("bad?.pdf", "bad<.pdf", "bad>.pdf", "bad*.pdf", 'bad".pdf'):
        with pytest.raises(OpenDVError) as exc_char:
            resolve_local_source_blob(ws / bad_name, workspace_root=ws, allowed_roots=[ws])
        assert exc_char.value.error_code == expected_illegal_code

    # 4. Directory path -> 400 SYNTAX_INVALID_ARGUMENT (never PermissionError / IsADirectoryError)
    sub_dir = ws / "subdir"
    sub_dir.mkdir()
    for dir_target in (ws, sub_dir):
        with pytest.raises(OpenDVError) as exc_dir:
            resolve_local_source_blob(dir_target, workspace_root=ws, allowed_roots=[ws])
        assert exc_dir.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT
        assert exc_dir.value.http_status == 400
        assert "directory" in exc_dir.value.detail.lower()


def test_regression_document_map_projector_masks_heading_before_slice(tmp_path: Path) -> None:
    """M3-Iter3 Defect 2: DocumentMapProjector masks headings before [:120] slice."""
    ws = tmp_path / "dmap_mask_ws"
    ws.mkdir()
    pan = "4111-1111-1111-1111"
    long_txt = ("B" * 108) + pan + " rest of line"
    long_md = "# " + ("B" * 108) + pan + " rest of heading\n\nBody paragraph."

    txt_path = ws / "head_120.txt"
    txt_path.write_text(long_txt, encoding="utf-8")
    md_path = ws / "head_120.md"
    md_path.write_text(long_md, encoding="utf-8")

    psvc = ParsingService(workspace_root=ws, allowed_roots=[ws])
    for kind, fpath in (("domain_note", txt_path), ("markdown", md_path)):
        raw_ir = psvc.parse_document(fpath, sink_class="LOCAL_HUMAN_IO")
        dmap, _ = psvc.build_document_map(fpath)
        assert "4111" not in dmap.pages[0].heading_preview
        assert "<MASKED_PII>" in dmap.pages[0].heading_preview
        assert dmap.outline
        assert "4111" not in dmap.outline[0].title
        assert "<MASKED_PII>" in dmap.outline[0].title
        assert "4111" not in dmap.model_dump_json()
        assert dmap.pages[0].canonical_text_sha256 == raw_ir.pages[0].canonical_text_sha256

        bundle = profile_sources_stateless(
            sources=[f"{kind}:{fpath}"],
            workspace_root=ws,
            allowed_roots=[ws],
        )
        assert len(bundle.document_maps) == 1
        prof_dm = bundle.document_maps[0]
        assert "4111" not in prof_dm.pages[0].heading_preview
        assert "<MASKED_PII>" in prof_dm.pages[0].heading_preview
        assert prof_dm.outline
        assert "4111" not in prof_dm.outline[0].title
        assert "<MASKED_PII>" in prof_dm.outline[0].title
        assert "4111" not in prof_dm.model_dump_json()
        assert prof_dm.pages[0].canonical_text_sha256 == raw_ir.pages[0].canonical_text_sha256

    # Also verify headings straddling the 240-char outline truncation boundary
    long_240_md = ws / "head_240.md"
    long_240_md.write_text("# " + ("B" * 226) + pan + " rest\n\nBody.", encoding="utf-8")
    dm_240, _ = psvc.build_document_map(long_240_md)
    assert dm_240.outline and "<MASKED_PII>" in dm_240.outline[0].title
    assert "4111" not in dm_240.model_dump_json()


def test_regression_redact_ir_masks_pdf_outline_before_slice(tmp_path: Path) -> None:
    """M3-Iter4 Defect 1: redact_ir masks PDF page/heading outline titles before 120/240 slicing."""
    ws = tmp_path / "pdf_outline_ws"
    ws.mkdir()
    pan = "4111-1111-1111-1111"
    pdf_p = _make_simple_pdf(ws / "outline_120.pdf", ("B" * 108) + f"{pan} rest")

    psvc = ParsingService(workspace_root=ws, allowed_roots=[ws])
    ir_egress = psvc.parse_document(pdf_p, sink_class="MODEL_EGRESS")
    assert ir_egress.provenance.redacted is True
    assert ir_egress.outline
    assert "4111" not in ir_egress.outline[0].title
    assert "<MASKED_PII>" in ir_egress.outline[0].title
    assert psvc.validate_ir(ir_egress) == []

    # Heading block straddling 240-char boundary passed through redact_ir directly
    heading_text = ("B" * 226) + f"{pan} rest"
    raw_ir = ParsedDocumentIR(
        provenance=ParseProvenance(
            parser_id="liteparse_pdf",
            parser_version="1.0",
            source_uri_sanitized=str(pdf_p),
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
                canonical_text=heading_text,
                canonical_text_sha256=compute_canonical_text_sha256(heading_text),
                raw_text_length=len(heading_text),
                non_ws_chars=len(heading_text.replace(" ", "")),
                image_block_count=0,
                needs_ocr=False,
                extracted_via="layout_text",
                width_pt=612.0,
                height_pt=792.0,
            )
        ],
        outline=[
            DocumentOutlineNode(
                node_id="out_0",
                title=heading_text[:240],
                level=1,
                start_page=1,
                end_page=1,
                block_id="p1_b0",
            )
        ],
        blocks=[
            DocumentBlock(
                block_id="p1_b0",
                kind="heading",
                reading_order=0,
                spans=[PageSpan(page_number=1, char_start=0, char_end=len(heading_text))],
                heading_level=1,
                text=heading_text,
            )
        ],
    )
    red_ir = redact_ir(raw_ir)
    assert "4111" not in red_ir.outline[0].title
    assert "<MASKED_PII>" in red_ir.outline[0].title
    assert psvc.validate_ir(red_ir) == []


def test_regression_virtual_slice_24k_mask_before_split(tmp_path: Path) -> None:
    """M3-Iter4 Defect 2: 24,000-char virtual slices mask PII before splitting across .txt/.docx."""
    import docx

    ws = tmp_path / "split24k_ws"
    ws.mkdir()
    psvc = ParsingService(workspace_root=ws, allowed_roots=[ws])

    txt_pan = ws / "split_pan.txt"
    txt_pan.write_text("Title line\n\n" + ("word " * 4797) + "4111 1111 1111 1111 after\n", encoding="utf-8")

    txt_aba = ws / "split_aba.txt"
    txt_aba.write_text("Header ok\n\n" + ("X" * 23987) + " routing 021000021 tail\n", encoding="utf-8")

    docx_pan = ws / "split_pan.docx"
    d_doc = docx.Document()
    d_doc.add_paragraph("Title line")
    d_doc.add_paragraph(("word " * 4797) + "4111 1111 1111 1111 after routing 021000021 tail")
    d_doc.save(docx_pan)

    for kind, fpath in (
        ("domain_note", txt_pan),
        ("domain_note", txt_aba),
        ("docx", docx_pan),
    ):
        ir_raw = psvc.parse_document(fpath, sink_class="LOCAL_HUMAN_IO", redaction="off")
        raw_sha_by_page = {pg.page_number: pg.canonical_text_sha256 for pg in ir_raw.pages}

        # 1. Direct export_ir(ir_raw, target="document_map", sink_class="MODEL_EGRESS")
        proj = psvc.export_ir(ir_raw, target="document_map", sink_class="MODEL_EGRESS")
        assert isinstance(proj, dict)
        dm_exp: DocumentMap = proj["document_map"]
        views_exp = proj["page_text_views"]
        assert "4111 1111" not in dm_exp.model_dump_json()
        assert "021000021" not in dm_exp.model_dump_json()
        for v in views_exp:
            assert "4111 1111" not in v.masked_text
            assert "021000021" not in v.masked_text

        # 2. ParsingService.build_document_map preserves raw SHA-256 and leaks zero PII
        dm_built, _ = psvc.build_document_map(fpath)
        assert "4111 1111" not in dm_built.model_dump_json()
        assert "021000021" not in dm_built.model_dump_json()
        for pm in dm_built.pages:
            assert pm.canonical_text_sha256 == raw_sha_by_page[pm.page_number]

        # 3. profile_sources_stateless leaks zero PII and preserves raw SHA-256
        bundle = profile_sources_stateless(
            sources=[f"{kind}:{fpath}"],
            workspace_root=ws,
            allowed_roots=[ws],
        )
        assert len(bundle.document_maps) == 1
        dm_prof = bundle.document_maps[0]
        assert "4111 1111" not in dm_prof.model_dump_json()
        assert "021000021" not in dm_prof.model_dump_json()
        for pm in dm_prof.pages:
            assert pm.canonical_text_sha256 == raw_sha_by_page[pm.page_number]

    # 4. Also verify single-page get_document_pages on attached 24k document masks before split
    svc = OpenDVService(ws, state_dir=tmp_path / "split_state")
    svc.init_project(ProjectInitReq(project_id="p_24k", domain_namespace="asset_management"))
    src = svc.attach_source("p_24k", SourceAttachReq(kind="domain_note", uri_or_filename=str(txt_pan)))
    views = svc.discovery.get_document_pages("p_24k", src.source_id, page_start=2, page_end=2)
    assert len(views) == 1
    assert "4111 1111" not in views[0].masked_text


def test_regression_table_headers_row_level_aba_context_masking(tmp_path: Path) -> None:
    """M3-Iter4 Defect 3: table_headers mask ABA routing numbers using row-level context."""
    import docx

    ws = tmp_path / "table_aba_ws"
    ws.mkdir()
    psvc = ParsingService(workspace_root=ws, allowed_roots=[ws])

    md_tbl = ws / "tbl_aba.md"
    md_tbl.write_text(
        "# Title\n\n| col_4111-1111-1111-1111 | Routing | 021000021 |\n| --- | --- | --- |\n| a | b | c |\n",
        encoding="utf-8",
    )
    txt_tbl = ws / "tbl_aba.txt"
    txt_tbl.write_text(
        "| Routing | 021000021 |\n| --- | --- |\n| a | b |\n",
        encoding="utf-8",
    )
    docx_tbl = ws / "tbl_aba.docx"
    d_doc = docx.Document()
    tbl = d_doc.add_table(rows=2, cols=3)
    tbl.rows[0].cells[0].text = "hdr_4111-1111-1111-1111"
    tbl.rows[0].cells[1].text = "Routing"
    tbl.rows[0].cells[2].text = "021000021"
    tbl.rows[1].cells[0].text = "a"
    tbl.rows[1].cells[1].text = "b"
    tbl.rows[1].cells[2].text = "c"
    d_doc.save(docx_tbl)

    for kind, fpath in (("markdown", md_tbl), ("domain_note", txt_tbl), ("docx", docx_tbl)):
        ir_raw = psvc.parse_document(fpath, sink_class="LOCAL_HUMAN_IO", redaction="off")
        proj = psvc.export_ir(ir_raw, target="document_map", sink_class="MODEL_EGRESS")
        assert isinstance(proj, dict)
        dm_exp: DocumentMap = proj["document_map"]
        assert "021000021" not in dm_exp.pages[0].table_headers
        assert "<MASKED_PII>" in dm_exp.pages[0].table_headers

        dm_built, _ = psvc.build_document_map(fpath)
        assert "021000021" not in dm_built.pages[0].table_headers
        assert "<MASKED_PII>" in dm_built.pages[0].table_headers

        bundle = profile_sources_stateless(
            sources=[f"{kind}:{fpath}"],
            workspace_root=ws,
            allowed_roots=[ws],
        )
        assert "021000021" not in bundle.document_maps[0].pages[0].table_headers
        assert "<MASKED_PII>" in bundle.document_maps[0].pages[0].table_headers

    # Also verify PDF table block through both DocumentMapProjector and redact_ir
    pdf_tbl_ir = ParsedDocumentIR(
        provenance=ParseProvenance(
            parser_id="liteparse_pdf",
            parser_version="1.0",
            source_uri_sanitized=str(ws / "tbl.pdf"),
            binary_sha256="c" * 64,
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
                canonical_text="| Routing | 021000021 |",
                canonical_text_sha256=compute_canonical_text_sha256("| Routing | 021000021 |"),
                raw_text_length=23,
                non_ws_chars=17,
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
                text="| Routing | 021000021 |",
                table_headers=["Routing", "021000021"],
                table_rows=[["021000021", "021000021"]],
            )
        ],
    )
    red_pdf_ir = redact_ir(pdf_tbl_ir)
    assert red_pdf_ir.blocks[0].table_headers == ["Routing", "<MASKED_PII>"]
    assert red_pdf_ir.blocks[0].table_rows == [["<MASKED_PII>", "<MASKED_PII>"]]
    pdf_proj = psvc.export_ir(pdf_tbl_ir, target="document_map", sink_class="MODEL_EGRESS")
    assert isinstance(pdf_proj, dict)
    assert pdf_proj["document_map"].pages[0].table_headers == ["Routing", "<MASKED_PII>"]


def test_regression_v4_raw_alignment_on_shrink_and_expand(tmp_path: Path) -> None:
    """M3-Iter5 Fix 1: build_document_map & get_document_pages align with ir_raw under V4."""
    from opendv.verifier.ports import VerifierContext

    ws = tmp_path / "v4_align_ws"
    ws.mkdir()
    psvc = ParsingService(workspace_root=ws, allowed_roots=[ws])
    hex64 = "deadbeefcafebabe" * 4

    # 1. 291-char .txt with 64-hex secret + trailing quote -> offline Gate V4 passes
    txt_291 = ws / "v4_shrink_291.txt"
    quote = "FINAL_CLAUSE_QUOTE"
    pad_len = 291 - len(hex64) - 2 - len(quote)
    raw_291 = f"{hex64} {'x' * pad_len} {quote}"
    assert len(raw_291) == 291
    txt_291.write_text(raw_291, encoding="utf-8")
    dm_291, pages_291 = psvc.build_document_map(txt_291, doc_id="doc_291")
    assert dm_291.page_count == 1 and dm_291.pages[0].char_count == 291
    assert hex64 not in dm_291.model_dump_json()
    q_start = pages_291[1].index(quote)
    draft_291 = _build_citation_draft(
        doc_id="doc_291",
        page_number=1,
        verbatim_quote=quote,
        occurrence=1,
        char_start=q_start,
        char_end=q_start + len(quote),
        content_sha256=dm_291.pages[0].canonical_text_sha256,
    )
    _, cert_291, _ = run_l1_codemods(draft_291)
    assert (
        verify_v4_unstructured(
            cert_291,
            VerifierContext(document_maps=[dm_291], metadata_only_binary_absent=True),
        )
        == []
    )

    # 2. hex_shrink_v4.txt (24,049 raw -> 2 raw pages even though masked < 24,000)
    hex_shrink = ws / "hex_shrink_v4.txt"
    hex_text = hex64 + " " + ("word " * 4785) + "quote123 " + ("tail " * 10)
    assert len(hex_text) == 24_049
    hex_shrink.write_text(hex_text, encoding="utf-8")
    dm_hex, raw_hex = psvc.build_document_map(hex_shrink, doc_id="doc_hex")
    assert dm_hex.page_count == len(dm_hex.pages) == len(raw_hex) == 2
    assert dm_hex.pages[0].char_count == len(raw_hex[1])
    assert dm_hex.pages[1].char_count == len(raw_hex[2])
    assert hex64 not in dm_hex.model_dump_json()

    p1_idx = raw_hex[1].index("quote123")
    p2_idx = raw_hex[2].index("tail")
    d_hex = _build_citation_draft(
        doc_id="doc_hex",
        page_number=1,
        verbatim_quote="quote123",
        occurrence=1,
        char_start=p1_idx,
        char_end=p1_idx + len("quote123"),
        content_sha256=dm_hex.pages[0].canonical_text_sha256,
    )
    d_hex.hubs[0].source_bindings[0].doc_citations.append(
        DocumentCitationRef(
            doc_id="doc_hex",
            page_number=2,
            verbatim_quote="tail",
            occurrence=1,
            char_start=p2_idx,
            char_end=p2_idx + len("tail"),
            content_sha256=dm_hex.pages[1].canonical_text_sha256,
        )
    )
    _, cert_hex, _ = run_l1_codemods(d_hex)
    assert (
        verify_v4_unstructured(
            cert_hex,
            VerifierContext(document_maps=[dm_hex], metadata_only_binary_absent=True),
        )
        == []
    )

    # 3. ein_expand.txt (23,990 raw with 10 EINs -> 1 raw page, raw SHA-256 preserved)
    ein_expand = ws / "ein_expand.txt"
    eins = " ".join(["12-3456789"] * 10)
    ein_text = eins + " " + ("A" * (23_990 - len(eins) - 1))
    assert len(ein_text) == 23_990
    ein_expand.write_text(ein_text, encoding="utf-8")
    dm_ein, raw_ein = psvc.build_document_map(ein_expand, doc_id="doc_ein")
    assert dm_ein.page_count == len(dm_ein.pages) == len(raw_ein) == 1
    assert dm_ein.pages[0].char_count == 23_990
    assert dm_ein.pages[0].canonical_text_sha256 == compute_canonical_text_sha256(raw_ein[1])
    assert "12-3456789" not in dm_ein.model_dump_json()

    # 4. get_document_pages page 2 succeeds when PII shrinks masked text below 24,000
    svc = OpenDVService(ws, state_dir=tmp_path / "v4_state")
    svc.init_project(ProjectInitReq(project_id="p_shrink", domain_namespace="asset_management"))
    src = svc.attach_source("p_shrink", SourceAttachReq(kind="domain_note", uri_or_filename=str(hex_shrink)))
    views = svc.discovery.get_document_pages("p_shrink", src.source_id, page_start=2, page_end=2)
    assert len(views) == 1 and views[0].page_number == 2
    assert "tail" in views[0].masked_text and hex64 not in views[0].masked_text


def test_regression_unified_row_aba_masking_and_split_pipe_header(tmp_path: Path) -> None:
    """M3-Iter5 Fix 2: Zero ABA leaks across redact_ir, Docx/Md parsers, projectors & 24k split."""
    import docx

    from opendv.parsing import ChunksProjector, MarkdownProjector

    ws = tmp_path / "aba_all_ws"
    ws.mkdir()
    psvc = ParsingService(workspace_root=ws, allowed_roots=[ws])

    # 1. redact_ir PDF tables (standard, wide, and non-pipe canonical_text)
    tbl_md = "| Routing | 021000021 |\n| --- | --- |\n| 021000021 | ok |"
    wide_md = (
        "| Routing | Bank_Name_Padding_Exceeding_32_Chars | Code |\n"
        "| --- | --- | --- |\n"
        "| Chase | Branch_Padding_Exceeding_32_Chars | 021000021 |"
    )
    non_pipe_page = (
        f"{tbl_md}\n\n"
        "Routing  Bank_Name_Padding_Exceeding_32_Chars  Code\n"
        "Chase  Branch_Padding_Exceeding_32_Chars  021000021"
    )
    syn_ir = ParsedDocumentIR(
        provenance=ParseProvenance(
            parser_id="liteparse_pdf",
            parser_version="1.0",
            source_uri_sanitized=str(ws / "aba.pdf"),
            binary_sha256="d" * 64,
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
                canonical_text=non_pipe_page,
                canonical_text_sha256=compute_canonical_text_sha256(non_pipe_page),
                raw_text_length=len(non_pipe_page),
                non_ws_chars=len(non_pipe_page.replace(" ", "")),
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
                text=tbl_md,
                table_headers=["Routing", "021000021"],
                table_rows=[["021000021", "ok"]],
            ),
            DocumentBlock(
                block_id="p1_b1",
                kind="table",
                reading_order=1,
                spans=[PageSpan(page_number=1)],
                text=wide_md,
                table_headers=["Routing", "Bank_Name_Padding_Exceeding_32_Chars", "Code"],
                table_rows=[["Chase", "Branch_Padding_Exceeding_32_Chars", "021000021"]],
            ),
        ],
    )
    red_ir = redact_ir(syn_ir)
    assert "021000021" not in red_ir.pages[0].canonical_text
    for blk in red_ir.blocks:
        assert "021000021" not in blk.text
        assert "021000021" not in json.dumps(blk.table_headers)
        assert "021000021" not in json.dumps(blk.table_rows)
    for src_ir in (syn_ir, red_ir):
        dm_res = psvc.export_ir(src_ir, target="document_map", sink_class="MODEL_EGRESS")
        assert isinstance(dm_res, dict)
        assert "021000021" not in dm_res["page_text_views"][0].masked_text
        assert "021000021" not in dm_res["document_map"].model_dump_json()
        chunks = ChunksProjector().project(src_ir, redaction="on")
        assert all("021000021" not in c.text and "021000021" not in (c.table_json or "") for c in chunks)
        md_proj = MarkdownProjector().project(src_ir, redaction="on")
        assert "021000021" not in md_proj

    # 2. DocxParser, MarkdownTextParser, MarkdownProjector, ChunksProjector on ABA vs id tables
    docx_aba = ws / "aba_rows.docx"
    d_doc = docx.Document()
    t1 = d_doc.add_table(rows=3, cols=2)
    t1.rows[0].cells[0].text = "Routing"
    t1.rows[0].cells[1].text = "021000021"
    t1.rows[1].cells[0].text = "021000021"
    t1.rows[1].cells[1].text = "Chase"
    t1.rows[2].cells[0].text = "111"
    t1.rows[2].cells[1].text = "021000021"
    t2 = d_doc.add_table(rows=2, cols=2)
    t2.rows[0].cells[0].text = "id"
    t2.rows[0].cells[1].text = "021000021"
    t2.rows[1].cells[0].text = "row"
    t2.rows[1].cells[1].text = "021000021"
    d_doc.save(docx_aba)

    md_aba = ws / "aba_wide.md"
    md_aba.write_text(
        f"{wide_md}\n\n| ColA | ColB |\n| --- | --- |\n| 021000021 | ABA |\n",
        encoding="utf-8",
    )
    md_id = ws / "id_only.md"
    md_id.write_text("| id | 021000021 |\n| --- | --- |\n| row | 021000021 |\n", encoding="utf-8")

    ir_docx_eg = psvc.parse_document(docx_aba, sink_class="MODEL_EGRESS")
    assert "021000021" not in ir_docx_eg.blocks[0].text
    assert "021000021" not in json.dumps(ir_docx_eg.blocks[0].table_headers)
    assert "021000021" not in json.dumps(ir_docx_eg.blocks[0].table_rows)
    assert ir_docx_eg.blocks[1].table_headers == ["id", "021000021"]
    assert ir_docx_eg.blocks[1].table_rows == [["row", "021000021"]]

    ir_md_eg = psvc.parse_document(md_aba, sink_class="MODEL_EGRESS")
    assert "021000021" not in ir_md_eg.pages[0].canonical_text
    assert all("021000021" not in b.text for b in ir_md_eg.blocks)

    ir_md_raw = psvc.parse_document(md_aba, sink_class="LOCAL_HUMAN_IO")
    assert "021000021" not in str(psvc.export_ir(ir_md_raw, target="markdown", sink_class="MODEL_EGRESS"))
    ch_md = psvc.export_ir(ir_md_raw, target="chunks", sink_class="MODEL_EGRESS")
    assert isinstance(ch_md, list)
    assert all("021000021" not in c.text and "021000021" not in (c.table_json or "") for c in ch_md)

    ir_id_raw = psvc.parse_document(md_id, sink_class="LOCAL_HUMAN_IO")
    id_proj = psvc.export_ir(ir_id_raw, target="document_map", sink_class="MODEL_EGRESS")
    assert isinstance(id_proj, dict)
    assert id_proj["document_map"].pages[0].table_headers == ["id", "021000021"]
    assert "021000021" in id_proj["page_text_views"][0].masked_text

    # 3. Split pipe-table header across 24,000-char boundary -> zero '1111' in pages[1].table_headers
    split_hdr = ws / "split_pipe_hdr.txt"
    split_hdr.write_text(
        ("word " * 4795) + "| col1 | 4111 1111 1111 1111 | col3 | col4 |\n| --- | --- | --- | --- |\n| a | b | c | d |\n",
        encoding="utf-8",
    )
    ir_split_raw = psvc.parse_document(split_hdr, sink_class="LOCAL_HUMAN_IO")
    split_proj = psvc.export_ir(ir_split_raw, target="document_map", sink_class="MODEL_EGRESS")
    assert isinstance(split_proj, dict)
    assert len(split_proj["document_map"].pages) == 2
    assert "1111" not in json.dumps(split_proj["document_map"].pages[1].table_headers)
    assert "1111" not in split_proj["page_text_views"][1].masked_text
