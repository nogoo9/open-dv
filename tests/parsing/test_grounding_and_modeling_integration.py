"""Integration tests for `opendv.grounding` and `opendv.modeling` unstructured support."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import unittest.mock
from pathlib import Path
from typing import Any

import docx
import pytest

from opendv.agents.ports import AgentTurnRequest, AgentTurnResult
from opendv.cli._client_grounding import execute_ground_ontology
from opendv.cli.main import main as cli_main
from opendv.modeling.models import DVModelingRequest
from opendv.models import (
    CliEnvelope,
    CliExitCode,
    ErrorCode,
    HarnessProvider,
    OpenDVError,
    ProjectInitReq,
)
from opendv.models.diagnostics import DocumentCitationRef
from opendv.models.events import ExternalMcpReceipt, OcrPageReceipt
from opendv.models.grounding import (
    ColumnEvidence,
    GroundingPolicy,
    GroundingRequest,
    OntologyGroundReq,
    TableEvidence,
)
from opendv.models.grounding_spi import TableRerankAssignment, TableRerankResult
from opendv.models.ontology import GraphAOntology
from opendv.models.pii_tokenizer import (
    MASKED_PII_TOKEN,
    REDACTED_LEAK_RE,
    ensure_no_redacted_placeholder,
)
from opendv.models.sources import DocumentMap, ExternalMcpServerSpec, PageMapSpec
from opendv.models.vault_ir import (
    BusinessKeyPart,
    ColumnTriageEntry,
    DecimalType,
    DraftField,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
    LinkParticipantSpec,
)
from opendv.models.wire_dtos_discovery import SourceAttachReq
from opendv.models.wire_dtos_vault import ProfileBundle
from opendv.service import OpenDVService, ParsingService
from opendv.service._ext_mcp_guards import resolve_doc_page_handles
from opendv.service._mcp_scout_tools import scout_read_document_pages
from opendv.service._mcp_tools import BrokerToolContext
from opendv.service.secrets import KeyringSecretStore, scoped_env_first
from opendv.service.workflows._discovery_run_ctx import build_source_and_doc_context
from opendv.service.workflows._modeling_stateless_helpers import merge_profile_bundle
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
ASPOSE_DOCX = REPO_ROOT / "tests" / "parsing" / "fixtures_public" / "aspose_merged_table.docx"
RFC_TXT = REPO_ROOT / "tests" / "parsing" / "fixtures_public" / "rfc4180_csv.txt"
CORE_BUNDLE_ZIP = REPO_ROOT / "tests" / "golden_v2" / "am_core_bundle.core.opendv.zip"
EXCEPTION_QUOTE = "CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE"


def _make_text_pdf(path: Path, text: str) -> Path:
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


def _make_spec_docx(path: Path, title: str, body: str) -> Path:
    doc = docx.Document()
    doc.add_heading(title, level=1)
    doc.add_paragraph(body)
    tbl = doc.add_table(rows=2, cols=2)
    tbl.cell(0, 0).text = "Entity"
    tbl.cell(0, 1).text = "Key"
    tbl.cell(1, 0).text = "CustodyAccount"
    tbl.cell(1, 1).text = "account_code"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def _setup_isolated_cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    user_state = tmp_path / "user_state"
    state_dir = user_state / "opendv"
    roaming = user_state / "Roaming"
    state_dir.mkdir(parents=True, exist_ok=True)
    roaming.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_HOME", str(ws / ".opendv"))
    monkeypatch.setenv(
        "OPENDV_WORKSPACE_ROOTS", f"{ws}{os.pathsep}{tmp_path}{os.pathsep}{REPO_ROOT}"
    )
    monkeypatch.setenv("LOCALAPPDATA", str(user_state))
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("USERPROFILE", str(user_state))
    monkeypatch.setenv("HOME", str(user_state))
    monkeypatch.setenv("XDG_STATE_HOME", str(user_state))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    monkeypatch.delenv("OPENDV_API_URL", raising=False)
    monkeypatch.chdir(ws)
    return ws, state_dir


def _run_json_cli(
    args: list[str], *, env_overrides: dict[str, str] | None = None
) -> tuple[int, CliEnvelope]:
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    ctx = (
        unittest.mock.patch.dict(os.environ, env_overrides, clear=False)
        if env_overrides
        else contextlib.nullcontext()
    )
    with ctx, contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
        code = cli_main(["--json", *args])
    raw = stdout_buf.getvalue().strip()
    assert raw, f"Expected JSON envelope for {args}; stderr={stderr_buf.getvalue()}"
    return code, CliEnvelope.model_validate_json(raw)


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
    cit = DocumentCitationRef(
        doc_id=doc_id,
        page_number=page_number,
        verbatim_quote=verbatim_quote,
        occurrence=occurrence,
        char_start=char_start,
        char_end=char_end,
        content_sha256=content_sha256,
    )
    prov_attrs = [
        DraftField(name="extractor_version", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        DraftField(name="prompt_or_rule_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        DraftField(
            name="confidence_score",
            logical_type=DecimalType(precision=5, scale=4),
            nullable=False,
            is_pii=False,
        ),
        DraftField(name="review_status", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        DraftField(name="citation_span", logical_type="JSON_DOCUMENT", nullable=False, is_pii=False),
        DraftField(name="source_doc_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
    ]
    tid = f"{doc_id}:page_{page_number}"
    binding = DraftSourceBinding(
        source_id=doc_id,
        table_id=tid,
        col_mappings={},
        doc_citations=[cit],
    )
    return DraftVaultIR(
        project_id="proj-gnd-mod",
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
                        source_id=doc_id, table_id=tid, col_mappings={"clause_id": "clause_id"}
                    )
                ],
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
                        source_id=doc_id, table_id=tid, col_mappings={"policy_id": "policy_id"}
                    )
                ],
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
                attributes=prov_attrs,
                source_bindings=[binding],
            )
        ],
        column_triage=[
            ColumnTriageEntry(
                table_id=tid, col_name="clause_id", disposition="MODELED", target_ref="hub_clause"
            ),
            ColumnTriageEntry(
                table_id=tid, col_name="policy_id", disposition="MODELED", target_ref="hub_policy"
            ),
            ColumnTriageEntry(
                table_id=tid,
                col_name="clause_text",
                disposition="MODELED",
                target_ref="lnk_clause_policy",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# opendv.grounding Integration
# ---------------------------------------------------------------------------


def test_grounding_discovery_service_doc_source_kinds_and_offline_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DiscoveryService.ground_ontology injects sanitized summary notes and falls back offline."""
    ws, state_dir = _setup_isolated_cli_env(tmp_path, monkeypatch)
    captured_prompts: list[str] = []
    captured_notes: list[list[str]] = []

    class _RecordingReranker:
        provider_name: HarnessProvider = "claude"

        async def run_turn(
            self,
            request: AgentTurnRequest,
            *,
            event_sink: Any = None,
            scout_dispatcher: Any = None,
        ) -> AgentTurnResult:
            _ = event_sink, scout_dispatcher
            captured_prompts.append(request.prompt)
            body = json.loads(request.prompt[request.prompt.find("{") : request.prompt.rfind("}") + 1])
            captured_notes.append(list(body.get("steward_notes", [])))
            out = TableRerankResult(
                assignments=[
                    TableRerankAssignment(
                        column_name=col["column_name"],
                        abstain=True,
                        selected_index=None,
                        confidence=0.5,
                        rationale="Inspected document notes.",
                        suggested_search_terms=[],
                    )
                    for col in body["columns"]
                ]
            )
            return AgentTurnResult(
                agent_id=request.agent_id,
                provider=self.provider_name,
                model_id="claude-sonnet-4-6",
                text_output=out.model_dump_json(),
                structured_output=out,
            )

    svc = OpenDVService(ws, state_dir=state_dir, harness=_RecordingReranker())  # type: ignore[arg-type]
    svc.init_project(ProjectInitReq(project_id="p_gnd_int", domain_namespace="asset_management"))

    csv_p = ws / "ambiguous_positions.csv"
    csv_p.write_text("ambig_code_a,metric_val_b\nX1,100\n", encoding="utf-8")
    pii_pdf = _make_text_pdf(ws / "pii_memo.pdf", "Custody SSN 123-45-6789 [REDACTED_TEXT] memo.")
    spec_docx = _make_spec_docx(
        ws / "spec.docx", "Custody Rules", "Phone (415) 555-0199 proxy governance rules."
    )
    note_txt = ws / "domain.txt"
    note_txt.write_text("ABA 021000021 wire settlement guide [REDACTED_PII].", encoding="utf-8")

    svc.attach_source("p_gnd_int", SourceAttachReq(kind="csv", uri_or_filename=str(csv_p)))
    svc.attach_source("p_gnd_int", SourceAttachReq(kind="pdf", uri_or_filename=str(pii_pdf)))
    svc.attach_source("p_gnd_int", SourceAttachReq(kind="docx", uri_or_filename=str(spec_docx)))
    svc.attach_source("p_gnd_int", SourceAttachReq(kind="markdown", uri_or_filename=str(STEWARDSHIP_MD)))
    svc.attach_source("p_gnd_int", SourceAttachReq(kind="domain_note", uri_or_filename=str(note_txt)))

    resp = asyncio.run(
        svc.discovery.ground_ontology(
            OntologyGroundReq(project_id="p_gnd_int", policy=GroundingPolicy(mode="agentic"))
        )
    )
    assert len(resp.tables) == 1
    assert captured_notes and 1 <= len(captured_notes[0]) <= 5
    for note in captured_notes[0]:
        assert len(note) <= 1500
        ensure_no_redacted_placeholder(note)
        assert REDACTED_LEAK_RE.search(note) is None
    for prompt in captured_prompts:
        assert REDACTED_LEAK_RE.search(prompt) is None
        for secret in ("123-45-6789", "(415) 555-0199", "021000021"):
            assert secret not in prompt

    # Offline .core.opendv.zip DocumentMap fallback (METADATA_ONLY_BINARY_ABSENT)
    off_ws = tmp_path / "off_ws"
    off_ws.mkdir()
    off_svc = OpenDVService(off_ws, state_dir=tmp_path / "off_state")
    off_svc.import_bundle(CORE_BUNDLE_ZIP, conflict="fail", allowed_roots=[REPO_ROOT, off_ws])
    off_resp = asyncio.run(
        off_svc.discovery.ground_ontology(
            OntologyGroundReq(
                project_id="am-golden-w1",
                policy=GroundingPolicy(mode="deterministic_only", skip_reason="offline_fallback"),
            )
        )
    )
    assert len(off_resp.tables) >= 1

    # Direct ParsingService.build_grounding_summary_notes on persisted DocumentMap
    doc_maps = off_svc.discovery.list_document_maps("am-golden-w1")
    dm_notes = ParsingService().build_grounding_summary_notes(doc_maps)
    assert len(dm_notes) >= 1
    assert all(len(n) <= 1500 and REDACTED_LEAK_RE.search(n) is None for n in dm_notes)


def test_grounding_cli_ontology_ground_input_doc_and_rejections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ontology ground --input --doc` works statelessly; `--project --doc` & solo `--doc` fail."""
    ws, _ = _setup_isolated_cli_env(tmp_path, monkeypatch)

    req = GroundingRequest(
        tables=[
            TableEvidence(
                table_id="tbl_sec",
                table_name="securities",
                columns=[
                    ColumnEvidence(column_name="cusip", data_type="VARCHAR", is_pk=True),
                    ColumnEvidence(column_name="maturity_date", data_type="DATE"),
                ],
            )
        ],
        global_steward_notes=[f"Note {i}" for i in range(18)],
        policy=GroundingPolicy(mode="deterministic_only", skip_reason="cli_doc_test"),
    )
    req_path = ws / "ground_req.json"
    req_path.write_text(req.model_dump_json(), encoding="utf-8")

    # Repeatable --doc respects remaining headroom (20 - 18 = 2) and creates zero .opendv/
    code_ok, env_ok = _run_json_cli(
        [
            "ontology",
            "ground",
            "--input",
            str(req_path),
            "--doc",
            str(CUSTODY_PDF),
            "--doc",
            str(ASPOSE_DOCX),
            "--doc",
            str(STEWARDSHIP_MD),
            "--doc",
            str(RFC_TXT),
        ]
    )
    assert code_ok == 0 and env_ok.ok is True
    assert isinstance(env_ok.data, dict) and len(env_ok.data["tables"]) == 1
    assert not (ws / ".opendv" / "projects").exists()

    # --project --doc rejected (SYNTAX_INVALID_ARGUMENT / exit 2)
    code_proj_doc, env_proj_doc = _run_json_cli(
        ["ontology", "ground", "--project", "p1", "--doc", str(CUSTODY_PDF)]
    )
    assert code_proj_doc == int(CliExitCode.VALIDATION_ERROR)
    assert env_proj_doc.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Standalone --doc rejected (SYNTAX_INVALID_ARGUMENT / exit 2)
    code_solo, env_solo = _run_json_cli(["ontology", "ground", "--doc", str(CUSTODY_PDF)])
    assert code_solo == int(CliExitCode.VALIDATION_ERROR)
    assert env_solo.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Containment escape via --doc rejected
    outside_pdf = _make_text_pdf(tmp_path / "outside_gnd" / "secret.pdf", "Outside.")
    code_esc, env_esc = _run_json_cli(
        ["ontology", "ground", "--input", str(req_path), "--doc", str(outside_pdf)],
        env_overrides={"OPENDV_WORKSPACE_ROOTS": str(ws)},
    )
    assert code_esc == int(CliExitCode.VALIDATION_ERROR)
    assert env_esc.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION


# ---------------------------------------------------------------------------
# opendv.modeling Integration
# ---------------------------------------------------------------------------


def test_modeling_profile_sources_stateless_doc_maps_and_pii_containment(
    tmp_path: Path,
) -> None:
    """`profile_sources_stateless` routes DOC_SOURCE_KINDS to ProfileBundle.document_maps only."""
    ws = tmp_path / "mod_prof_ws"
    ws.mkdir()
    pii_pdf = _make_text_pdf(ws / "pii_prof.pdf", "Custody SSN 123-45-6789 agreement.")

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
    assert {dm.format for dm in bundle.document_maps} == {"pdf", "docx", "markdown", "domain_note"}

    # Scanned page 4 on custody PDF recorded cleanly without crashing
    cust_dm = bundle.document_maps[0]
    assert cust_dm.page_count == 6 and cust_dm.pages[3].is_scanned_or_low_density is True

    # PII masked in DocumentMap metadata while raw canonical_text_sha256 kept for Gate V4
    pii_dm = bundle.document_maps[1]
    assert "123-45-6789" not in pii_dm.model_dump_json()
    raw_ir = ParsingService(allowed_roots=[ws]).parse_document(pii_pdf, sink_class="LOCAL_HUMAN_IO")
    assert pii_dm.pages[0].canonical_text_sha256 == raw_ir.pages[0].canonical_text_sha256

    # ProfileBundle excludes raw page text fields
    dumped = bundle.model_dump()
    for forbidden in ("parsed_doc_pages", "page_text_views", "document_excerpts"):
        assert forbidden not in dumped

    # allowed_roots containment enforced
    outside_pdf = _make_text_pdf(tmp_path / "outside_mod" / "secret.pdf", "Secret.")
    with pytest.raises(OpenDVError) as exc_sec:
        profile_sources_stateless(sources=[f"pdf:{outside_pdf}"], allowed_roots=[ws])
    assert exc_sec.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION


def test_modeling_merge_profile_bundle_and_run_model_stateless_mode1_gate_v4(
    tmp_path: Path,
) -> None:
    """Verify merge_profile_bundle, Mode-1 verify_draft_stateless, and run_model_stateless Gate V4."""
    ws = tmp_path / "mod_v4_ws"
    ws.mkdir()
    state_dir = tmp_path / "mod_v4_state"

    bundle = profile_sources_stateless(
        sources=[f"pdf:{CUSTODY_PDF}"],
        allowed_roots=[ws, REPO_ROOT],
    )
    cust_dm = bundle.document_maps[0]

    # 1. merge_profile_bundle preserves caller DocumentMap on collision + ocr/mcp/parsed_doc_pages
    caller_dm = DocumentMap(
        doc_id=cust_dm.doc_id,
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
    extra_dm = caller_dm.model_copy(update={"doc_id": "doc_extra", "filename": "extra.pdf"})
    ocr_rec = OcrPageReceipt(
        receipt_id="ocr-1",
        doc_id=cust_dm.doc_id,
        page_number=4,
        ocr_text_masked="ok",
        content_sha256="b" * 64,
        ocr_box_count=1,
    )
    mcp_rec = ExternalMcpReceipt(
        receipt_id="mcp-1",
        server_id="sec",
        tool_name="fetch",
        doc_id=cust_dm.doc_id,
        page_number=1,
        args_sha256="c" * 64,
        content_sha256="d" * 64,
        masked_excerpt="ex",
    )
    merged = merge_profile_bundle(
        {
            "verifier_ctx": {
                "document_maps": [caller_dm.model_dump(mode="json")],
                "ocr_receipts": [ocr_rec.model_dump(mode="json")],
                "mcp_receipts": [mcp_rec.model_dump(mode="json")],
                "parsed_doc_pages": {cust_dm.doc_id: {1: "raw text"}},
            }
        },
        ProfileBundle(document_maps=[cust_dm, extra_dm]),
    )
    vctx = merged["verifier_ctx"]
    assert [d["doc_id"] for d in vctx["document_maps"]] == [cust_dm.doc_id, "doc_extra"]
    assert vctx["document_maps"][0]["filename"] == "caller_override.pdf"
    assert len(vctx["ocr_receipts"]) == 1 and len(vctx["mcp_receipts"]) == 1
    assert vctx["parsed_doc_pages"] == {cust_dm.doc_id: {1: "raw text"}}

    # 2. Mode-1 verify_draft_stateless passes Gate V4 offline and catches SHA/bounds/placeholder errors
    raw_ir = ParsingService(allowed_roots=[REPO_ROOT]).parse_document(
        CUSTODY_PDF, sink_class="LOCAL_HUMAN_IO"
    )
    p5_txt = raw_ir.pages[4].canonical_text
    idx2 = p5_txt.find(EXCEPTION_QUOTE, p5_txt.find(EXCEPTION_QUOTE) + 1)
    assert idx2 >= 0
    valid_draft = _build_citation_draft(
        doc_id=cust_dm.doc_id,
        page_number=5,
        verbatim_quote=EXCEPTION_QUOTE,
        occurrence=2,
        char_start=idx2,
        char_end=idx2 + len(EXCEPTION_QUOTE),
        content_sha256=cust_dm.pages[4].canonical_text_sha256,
    )
    ok_ver = verify_draft_stateless(
        {"profiles": [], "policy": "symbolic_only", "real_row_proving": False},
        draft_data=valid_draft,
        profile_bundle=bundle,
    )
    assert ok_ver.status == "CERTIFIED_FAST", ok_ver.verifier_report.v1_v6_diagnostics

    for bad_draft, expected_rule in (
        (
            _build_citation_draft(
                doc_id=cust_dm.doc_id,
                page_number=5,
                verbatim_quote=EXCEPTION_QUOTE,
                occurrence=2,
                char_start=idx2,
                char_end=idx2 + len(EXCEPTION_QUOTE),
                content_sha256="0" * 64,
            ),
            "CITATION_SPAN_UNRESOLVED",
        ),
        (
            _build_citation_draft(
                doc_id=cust_dm.doc_id,
                page_number=5,
                verbatim_quote=EXCEPTION_QUOTE,
                occurrence=1,
                char_start=cust_dm.pages[4].char_count,
                char_end=cust_dm.pages[4].char_count + len(EXCEPTION_QUOTE),
                content_sha256=cust_dm.pages[4].canonical_text_sha256,
            ),
            "SPAN_HALLUCINATION",
        ),
        (
            _build_citation_draft(
                doc_id=cust_dm.doc_id,
                page_number=5,
                verbatim_quote="<MASKED_PII>",
                occurrence=1,
                char_start=0,
                char_end=12,
                content_sha256=cust_dm.pages[4].canonical_text_sha256,
            ),
            "REDACTED_PLACEHOLDER_LEAK",
        ),
    ):
        bad_ver = verify_draft_stateless(
            {"profiles": [], "policy": "symbolic_only", "real_row_proving": False},
            draft_data=bad_draft,
            profile_bundle=bundle,
        )
        assert bad_ver.status == "UNCERTIFIED_DRAFT"
        assert any(
            d.gate == "V4" and d.rule_id == expected_rule
            for d in bad_ver.verifier_report.v1_v6_diagnostics
        )

    # 3. run_model_stateless DVModelingRequest branch propagates bundle.document_maps to Gate V4
    req = DVModelingRequest(
        ontology=GraphAOntology(ontology_version="1.0.0", domain_namespace="ibor", concepts=[]),
        profiles=[],
        policy="symbolic_only",
        real_row_proving=False,
    )
    harness = DeterministicMockHarness(
        scripts_by_agent={"ship-vault": [MockTurnScript(structured_output=valid_draft)]}
    )
    mod_res = asyncio.run(
        run_model_stateless(
            req,
            profile_bundle=bundle,
            workspace_root=ws,
            state_dir=state_dir,
            injected_harness=harness,
        )
    )
    assert mod_res.verifier_report is not None
    assert [d for d in mod_res.verifier_report.v1_v6_diagnostics if d.gate == "V4"] == []
    assert req.business_context_hints is None


def test_modeling_mode2_vault_helpers_page_keys_and_offline_core_bundle_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mode-2 `_vault_helpers.load_verifier_inputs` keys by PageSlice.page_number & falls back offline."""
    ws, state_dir = _setup_isolated_cli_env(tmp_path, monkeypatch)
    svc = OpenDVService(ws, state_dir=state_dir)
    svc.init_project(ProjectInitReq(project_id="p_m2_keys", domain_namespace="asset_management"))
    from opendv.models.project import RevisionMetadata

    ont = GraphAOntology(ontology_version="1.0.0", domain_namespace="asset_management", concepts=[])
    svc.state_manager.save_revision(
        "p_m2_keys",
        revision_id="ont-v1",
        rev_kind="ontology_version",
        rev_key="ont-v1",
        parent_rev_id=None,
        payload=ont,
        metadata=RevisionMetadata(actor="human", reason="init"),
    )
    svc.state_manager.update_project_pointers("p_m2_keys", active_ontology_rev_id="ont-v1")

    local_pdf = ws / "custody.pdf"
    local_pdf.write_bytes(CUSTODY_PDF.read_bytes())
    local_db = ws / "ibor.sqlite"
    local_db.write_bytes(IBOR_SQLITE.read_bytes())
    svc.attach_source("p_m2_keys", SourceAttachReq(kind="sqlite", uri_or_filename=str(local_db)))
    pdf_src = svc.attach_source("p_m2_keys", SourceAttachReq(kind="pdf", uri_or_filename=str(local_pdf)))

    _, _, _, _, _, _, ctx_live = load_verifier_inputs(svc.state_manager, ws, "p_m2_keys")
    assert ctx_live.metadata_only_binary_absent is False
    assert set(ctx_live.parsed_doc_pages[pdf_src.source_id].keys()) == {1, 2, 3, 4, 5, 6}
    p5 = ctx_live.parsed_doc_pages[pdf_src.source_id][5]
    idx2 = p5.find(EXCEPTION_QUOTE, p5.find(EXCEPTION_QUOTE) + 1)
    assert idx2 >= 0
    dm_live = next(d for d in ctx_live.document_maps if d.doc_id == pdf_src.source_id)
    draft_live = _build_citation_draft(
        doc_id=dm_live.doc_id,
        page_number=5,
        verbatim_quote=EXCEPTION_QUOTE,
        occurrence=2,
        char_start=idx2,
        char_end=idx2 + len(EXCEPTION_QUOTE),
        content_sha256=dm_live.pages[4].canonical_text_sha256,
    )
    _, cert_live, _ = run_l1_codemods(draft_live)
    assert verify_v4_unstructured(cert_live, ctx_live) == []

    # Offline golden .core.opendv.zip fallback
    off_ws = tmp_path / "off_m2_ws"
    off_ws.mkdir()
    off_svc = OpenDVService(off_ws, state_dir=tmp_path / "off_m2_state")
    off_svc.import_bundle(CORE_BUNDLE_ZIP, conflict="fail", allowed_roots=[REPO_ROOT, off_ws])
    _, _, _, _, _, _, ctx_off = load_verifier_inputs(
        off_svc.state_manager, off_ws, "am-golden-w1"
    )
    assert ctx_off.metadata_only_binary_absent is True
    assert ctx_off.parsed_doc_pages == {}
    pdf_off_dm = next(d for d in ctx_off.document_maps if d.doc_id == "src-7c0663dfb9")
    p5_spec = pdf_off_dm.pages[4]
    draft_off = _build_citation_draft(
        doc_id=pdf_off_dm.doc_id,
        page_number=5,
        verbatim_quote=EXCEPTION_QUOTE,
        occurrence=2,
        char_start=p5_spec.char_count - len(EXCEPTION_QUOTE),
        char_end=p5_spec.char_count,
        content_sha256=p5_spec.canonical_text_sha256,
    )
    _, cert_off, _ = run_l1_codemods(draft_off)
    assert verify_v4_unstructured(cert_off, ctx_off) == []


def test_adversarial_table_aba_and_cross_slice_redaction_across_grounding_scout_and_external_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gaps 1 & 2: Table-context ABA (>32 chars from header) and cross-24k-slice PII never leak."""
    ws, state_dir = _setup_isolated_cli_env(tmp_path, monkeypatch)
    captured_prompts: list[str] = []

    class _SpyReranker:
        provider_name: HarnessProvider = "claude"

        async def run_turn(
            self,
            request: AgentTurnRequest,
            *,
            event_sink: Any = None,
            scout_dispatcher: Any = None,
        ) -> AgentTurnResult:
            _ = event_sink, scout_dispatcher
            captured_prompts.append(request.prompt)
            body = json.loads(request.prompt[request.prompt.find("{") : request.prompt.rfind("}") + 1])
            out = TableRerankResult(
                assignments=[
                    TableRerankAssignment(
                        column_name=col["column_name"],
                        abstain=True,
                        selected_index=None,
                        confidence=0.5,
                        rationale="Checked table notes.",
                        suggested_search_terms=[],
                    )
                    for col in body["columns"]
                ]
            )
            return AgentTurnResult(
                agent_id=request.agent_id,
                provider=self.provider_name,
                model_id="claude-sonnet-4-6",
                text_output=out.model_dump_json(),
                structured_output=out,
            )

    svc = OpenDVService(ws, state_dir=state_dir, harness=_SpyReranker())  # type: ignore[arg-type]
    svc.init_project(ProjectInitReq(project_id="p_aba", domain_namespace="asset_management"))

    aba_md = ws / "aba_table.md"
    aba_md.write_text(
        "| Account Holder | ABA Routing Number |\n"
        "| --- | --- |\n"
        f"| {'X' * 45} | 021000021 |\n",
        encoding="utf-8",
    )

    aba_docx = ws / "aba_table.docx"
    d_aba = docx.Document()
    tbl = d_aba.add_table(rows=2, cols=2)
    tbl.cell(0, 0).text = "Account Holder"
    tbl.cell(0, 1).text = "ABA Routing Number"
    tbl.cell(1, 0).text = "X" * 45
    tbl.cell(1, 1).text = "021000021"
    d_aba.save(aba_docx)

    multi_md = ws / "multi_slice.md"
    multi_md.write_text(("A" * 23_994) + " 123-45-6789 " + ("B" * 400), encoding="utf-8")

    multi_docx = ws / "multi_slice.docx"
    d_multi = docx.Document()
    d_multi.add_paragraph(("Z" * 23_994) + " 123-45-6789 " + ("W" * 400))
    d_multi.save(multi_docx)

    aba_pdf = _make_text_pdf(
        ws / "aba_table.pdf",
        "| Account Holder | ABA Routing Number | " + ("X" * 45) + " | 021000021 |",
    )

    csv_p = ws / "ambig_tbl.csv"
    csv_p.write_text("ambig_code_x,metric_val_y\nA1,10\n", encoding="utf-8")

    # Gap 1: build_grounding_summary_notes + DiscoveryService.ground_ontology
    psvc = ParsingService(workspace_root=ws, allowed_roots=[ws])
    direct_notes = psvc.build_grounding_summary_notes([aba_md, aba_docx], max_notes=5)
    assert len(direct_notes) == 2
    for note in direct_notes:
        assert "021000021" not in note
        assert REDACTED_LEAK_RE.search(note) is None

    svc.attach_source("p_aba", SourceAttachReq(kind="csv", uri_or_filename=str(csv_p)))
    md_src = svc.attach_source("p_aba", SourceAttachReq(kind="markdown", uri_or_filename=str(aba_md)))
    docx_src = svc.attach_source("p_aba", SourceAttachReq(kind="docx", uri_or_filename=str(aba_docx)))
    multi_md_src = svc.attach_source(
        "p_aba", SourceAttachReq(kind="markdown", uri_or_filename=str(multi_md))
    )
    multi_docx_src = svc.attach_source(
        "p_aba", SourceAttachReq(kind="docx", uri_or_filename=str(multi_docx))
    )
    pdf_src = svc.attach_source("p_aba", SourceAttachReq(kind="pdf", uri_or_filename=str(aba_pdf)))

    asyncio.run(
        svc.discovery.ground_ontology(
            OntologyGroundReq(project_id="p_aba", policy=GroundingPolicy(mode="agentic"))
        )
    )
    assert captured_prompts
    for prompt in captured_prompts:
        assert "021000021" not in prompt
        assert "123-45-6789" not in prompt
        assert REDACTED_LEAK_RE.search(prompt) is None

    # Gap 2a: scout_read_document_pages (both empty cache and build_source_and_doc_context pre-fill)
    _, pre_pages, _ = build_source_and_doc_context(svc.state_manager, "p_aba", ws, parsing_service=psvc)
    for initial_pages in ({}, pre_pages):
        ctx = BrokerToolContext(
            project_id="p_aba",
            run_id="run-gap2",
            state_store=svc.state_manager,
            parsing_service=psvc,
            parsed_pages={k: dict(v) for k, v in initial_pages.items()},
        )
        for sid in (md_src.source_id, docx_src.source_id):
            views = scout_read_document_pages(ctx, sid, 1, 1)
            assert len(views) == 1
            assert "021000021" not in views[0].masked_text
            assert MASKED_PII_TOKEN in views[0].masked_text
            assert "021000021" in ctx.parsed_pages[sid][1]
        for sid in (multi_md_src.source_id, multi_docx_src.source_id):
            views = scout_read_document_pages(ctx, sid, 1, 2)
            assert len(views) == 2
            joined_masked = "\n".join(v.masked_text for v in views)
            assert "123-45-6789" not in joined_masked
            assert MASKED_PII_TOKEN in joined_masked

    # Gap 2b: resolve_doc_page_handles on .md, .docx, multi-slice .md/.docx, and .pdf
    srv = ExternalMcpServerSpec(
        server_id="ext_gap2",
        transport="stdio",
        command=["python", "-m", "noop"],
        allow_document_pages=True,
    )
    for sid in (md_src.source_id, docx_src.source_id, pdf_src.source_id):
        resolved, _, _ = resolve_doc_page_handles(
            {"doc": {"$opendv_handle": "doc_page", "doc_id": sid, "page_number": 1}},
            project_id="p_aba",
            server=srv,
            state_store=svc.state_manager,
        )
        assert "021000021" not in resolved["doc"]["masked_text"]
        assert MASKED_PII_TOKEN in resolved["doc"]["masked_text"]

    for sid in (multi_md_src.source_id, multi_docx_src.source_id):
        p1, _, _ = resolve_doc_page_handles(
            {"doc": {"$opendv_handle": "doc_page", "doc_id": sid, "page_number": 1}},
            project_id="p_aba",
            server=srv,
            state_store=svc.state_manager,
        )
        p2, _, _ = resolve_doc_page_handles(
            {"doc": {"$opendv_handle": "doc_page", "doc_id": sid, "page_number": 2}},
            project_id="p_aba",
            server=srv,
            state_store=svc.state_manager,
        )
        combined = p1["doc"]["masked_text"] + "\n" + p2["doc"]["masked_text"]
        assert "123-45-6789" not in combined
        assert MASKED_PII_TOKEN in combined


def test_execute_ground_ontology_preserves_scoped_env_first_inside_running_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap 5: `_run_awaitable` preserves `scoped_env_first(True)` when called inside a running loop."""
    ws, state_dir = _setup_isolated_cli_env(tmp_path, monkeypatch)
    seen_env_first: list[bool] = []

    class _AsyncStubService:
        async def ground_ontology(self, req: OntologyGroundReq) -> Any:
            _ = req
            store = KeyringSecretStore(ws, state_dir=state_dir)
            seen_env_first.append(store.is_env_first_override_active())
            from opendv.models import GroundingResponse

            return GroundingResponse(
                knowledge_source_id="fibo-pack-v2",
                knowledge_source_version="2.0.0",
                knowledge_source_sha256="a" * 64,
                engine_version="2.0.0",
                mode_used="deterministic_only",
                skip_reason="ctx_test",
                tables=[],
            )

    class _StubClient:
        is_remote = False
        _service = _AsyncStubService()

        def service(self) -> Any:
            return self._service

        def _http(self, method: str, path: str, *, json_body: Any = None) -> Any:
            _ = method, path, json_body
            raise AssertionError("Not remote")

    req = OntologyGroundReq(
        project_id="p_ctx",
        policy=GroundingPolicy(mode="deterministic_only", skip_reason="ctx_test"),
    )

    async def _invoke_inside_loop() -> None:
        with scoped_env_first(True):
            execute_ground_ontology(_StubClient(), req)

    asyncio.run(_invoke_inside_loop())
    assert seen_env_first == [True]
