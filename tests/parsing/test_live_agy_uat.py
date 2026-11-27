"""Live `agy` UAT suite covering Step 4-7 project workflow and standalone Skill+CLI+MCP parsing."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import docx
import duckdb
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image
from pydantic import BaseModel, Field

from opendv.agents.cli_subprocess_base import (
    IsolatedTempCwd,
    build_isolated_child_env,
    extract_json_from_stdout,
    parse_ndjson_stream_line,
    stream_cli_subprocess_lines,
    validate_cli_structured_output,
)
from opendv.agents.ports import resolve_model_for_agent
from opendv.agents.subprocess_runner import build_agy_command, resolve_cli_binary
from opendv.cli.mcp_stdio import PARSING_MCP_TOOL_NAMES
from opendv.models import (
    CliEnvelope,
    CliExitCode,
    ErrorCode,
    OpenDVError,
    ProblemDetails,
    WorkspaceSettings,
)
from opendv.models.diagnostics import DocumentCitationRef
from opendv.models.document_ir import ChunkRecord, ParsedDocumentIR
from opendv.models.grounding import GroundingPolicy, OntologyGroundReq
from opendv.models.pii_tokenizer import REDACTED_LEAK_RE, ensure_no_redacted_placeholder
from opendv.models.sources import DocumentMap
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
from opendv.models.wire_dtos_discovery import (
    CitationResolveReq,
    HoldAnswersReq,
    OntologyCommitReq,
    OntologyConstructReq,
    ProjectInitReq,
    SourceAttachReq,
)
from opendv.models.wire_dtos_vault import VaultBuildReq
from opendv.service import OpenDVService
from opendv.service._mcp_scout_tools import scout_read_document_pages, scout_view_page_image
from opendv.service._mcp_tools import BrokerToolContext
from opendv.service.citations import resolve_citation
from opendv.service.workflows._source_docs import (
    load_doc_pages_for_record,
    resolve_readable_source_path,
)
from opendv.service.workflows._vault_helpers import load_verifier_inputs
from opendv.verifier._v4_unstructured import verify_v4_unstructured
from opendv.verifier.l1_codemods import run_l1_codemods
from tests.cli._cli_test_helpers import run_loopback_api_server
from tests.parity._v2_parity_helpers import (
    FIXTURE_TIMESTAMP,
    PDF_EXCEPTION_QUOTE,
    setup_isolated_env,
    snapshot_dir_mtimes,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_AM = REPO_ROOT / "data" / "public_am"
SAMPLE_PDFS = REPO_ROOT / "examples" / "sample_pdfs"
PUBLIC_FIXTURES = REPO_ROOT / "tests" / "parsing" / "fixtures_public"
PARSING_SKILL_PATH = REPO_ROOT / ".agents" / "skills" / "opendv-parsing" / "SKILL.md"


def _live_cli_env(
    ws: Path,
    opendv_home: Path,
    extra_root: Path,
    *,
    localappdata_override: Path | None = None,
) -> dict[str, str]:
    """Build subprocess env isolating OPENDV_HOME while preserving host agy auth."""
    env = dict(os.environ)
    env["OPENDV_HOME"] = str(opendv_home)
    env["OPENDV_WORKSPACE_ROOTS"] = f"{ws}{os.pathsep}{extra_root}{os.pathsep}{REPO_ROOT}"
    env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    src_path = str(REPO_ROOT / "src")
    old_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_path}{os.pathsep}{old_pp}" if old_pp else src_path
    if localappdata_override is not None:
        env["LOCALAPPDATA"] = str(localappdata_override)
        env["XDG_STATE_HOME"] = str(localappdata_override)
        env["HOME"] = str(localappdata_override)
        env["USERPROFILE"] = str(localappdata_override)
        env["APPDATA"] = str(localappdata_override / "Roaming")
    return env


def _run_cli_subprocess(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> tuple[int, CliEnvelope]:
    proc = subprocess.run(
        [sys.executable, "-m", "opendv.cli", "--json", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
        check=False,
    )
    assert proc.stdout.strip(), f"Missing JSON output for {args}; stderr={proc.stderr}"
    return proc.returncode, CliEnvelope.model_validate_json(proc.stdout)


async def _call_mcp_tool(
    session: ClientSession, name: str, arguments: dict[str, Any]
) -> tuple[bool, Any, ProblemDetails | None]:
    res = await session.call_tool(name, arguments)
    text = getattr(res.content[0], "text", "") if res.content else ""
    if res.is_error:
        idx = text.find("{")
        raw_json = text[idx:] if idx != -1 else text
        return True, None, ProblemDetails.model_validate_json(raw_json)
    if res.structured_content is not None:
        return False, res.structured_content.get("result", res.structured_content), None
    if not text.strip():
        return False, None, None
    try:
        return False, json.loads(text), None
    except json.JSONDecodeError:
        return False, text, None


def _generate_docx_spec(path: Path) -> Path:
    doc = docx.Document()
    doc.add_heading("Institutional Stewardship & Custody Specification", level=1)
    doc.add_paragraph(
        "InstitutionalFiler entities file Form 13F holdings and must log any "
        "stewardship exception under custody governance rules."
    )
    tbl = doc.add_table(rows=2, cols=2)
    tbl.cell(0, 0).text = "Entity"
    tbl.cell(0, 1).text = "BusinessKey"
    tbl.cell(1, 0).text = "InstitutionalFiler"
    tbl.cell(1, 1).text = "filer_cik"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


class _StandaloneParsingUatPlan(BaseModel):
    """Structured JSON schema verified against live `agy` in standalone Case 2."""

    detected_formats: list[str] = Field(min_length=4)
    scanned_pdf_filename: str
    scanned_page_numbers: list[int] = Field(min_length=1)
    recommended_cli_commands: list[str] = Field(min_length=3)


# ---------------------------------------------------------------------------
# Case 1: Live `agy` Step 4-7 Project Workflow
# ---------------------------------------------------------------------------


@pytest.mark.live_provider
@pytest.mark.asyncio
async def test_live_agy_uat_case1_step4_to_7_project_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 1: Live `agy` Steps 4-7 over custody PDF, generated DOCX, and IBOR SQLite."""
    binary = resolve_cli_binary("agy")
    assert binary
    env = setup_isolated_env(tmp_path / "case1", monkeypatch, preserve_live_auth=True)
    host_before = snapshot_dir_mtimes(env.host_local_opendv)
    home_opendv = Path.home() / ".opendv"
    home_before = snapshot_dir_mtimes(home_opendv)

    pub_dir = env.ws / "public_am"
    pub_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pub_dir / "am_custody_and_voting_guide.pdf"
    sqlite_path = pub_dir / "am_sec_treasury_ibor.sqlite"
    shutil.copy2(PUBLIC_AM / "am_custody_and_voting_guide.pdf", pdf_path)
    shutil.copy2(PUBLIC_AM / "am_sec_treasury_ibor.sqlite", sqlite_path)
    docx_path = _generate_docx_spec(pub_dir / "custody_stewardship_spec.docx")

    svc = env.make_service(harness=None)
    svc.update_settings(
        WorkspaceSettings(
            harness_provider="agy",
            secret_backend="user_state_file",
            cegar_policy="fast",
            timeout_seconds=180,
            updated_at=FIXTURE_TIMESTAMP,
        )
    )
    pid = "uat-agy-case1"
    proj = svc.init_project(ProjectInitReq(project_id=pid, domain_namespace="asset_management"))
    initial_rev = proj.active_ontology_rev_id
    assert initial_rev is not None

    pdf_src = svc.attach_source(pid, SourceAttachReq(kind="pdf", uri_or_filename=str(pdf_path)))
    docx_src = svc.attach_source(pid, SourceAttachReq(kind="docx", uri_or_filename=str(docx_path)))
    sql_src = svc.attach_source(
        pid,
        SourceAttachReq(
            kind="sqlite",
            uri_or_filename=str(sqlite_path),
            selected_tables=["institutional_filers"],
        ),
    )

    # 1. scout_read_document_pages + UNREDACTED_SCAN_BLOCKED on page 4 -> opendv source approve-scan
    broker_ctx = BrokerToolContext(
        project_id=pid,
        run_id="uat-scout-1",
        state_store=svc.state_manager,
        parsing_service=svc.parsing,
        source_path_resolver=lambda p_id, d_id: resolve_readable_source_path(
            svc.state_manager, env.ws, p_id, svc.state_manager.get_source_record(p_id, d_id)
        ),
    )
    views = scout_read_document_pages(broker_ctx, pdf_src.source_id, 1, 5)
    assert [v.page_number for v in views] == [1, 2, 3, 4, 5]
    assert views[0].masked_text.strip()
    assert views[3].is_scanned is True and views[3].scan_approved is False and views[3].masked_text == ""
    assert PDF_EXCEPTION_QUOTE in views[4].masked_text

    with pytest.raises(OpenDVError) as exc_scan:
        scout_view_page_image(broker_ctx, pdf_src.source_id, 4)
    assert exc_scan.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    cli_env = _live_cli_env(env.ws, env.opendv_home, tmp_path)
    with run_loopback_api_server(svc) as base_url:
        code_app, env_app = _run_cli_subprocess(
            [
                "--api-url",
                base_url,
                "source",
                "approve-scan",
                "--project",
                pid,
                "--doc",
                pdf_src.source_id,
                "--pages",
                "4",
                "--rationale",
                "Live agy UAT steward approved page 4 scan.",
            ],
            cwd=env.ws,
            env=cli_env,
        )
        assert code_app == 0 and env_app.ok is True
    assert svc.get_document_map(pid, pdf_src.source_id).approved_scan_pages == [4]
    p4_view = svc.get_document_pages(pid, pdf_src.source_id, page_start=4, page_end=4)[0]
    assert p4_view.scan_approved is True

    # 2. citations.resolve_citation on page 5 occurrence=2 and generated DOCX
    pdf_rec = svc.state_manager.get_source_record(pid, pdf_src.source_id)
    pdf_dmap = DocumentMap.model_validate(pdf_rec["document_map"])
    pdf_pages = load_doc_pages_for_record(
        svc.state_manager,
        env.ws,
        pid,
        pdf_src.source_id,
        pdf_rec,
        pdf_dmap,
        parsing_service=svc.parsing,
    )
    cit_pdf = resolve_citation(
        CitationResolveReq(
            doc_id=pdf_src.source_id,
            page_number=5,
            verbatim_quote=PDF_EXCEPTION_QUOTE,
            occurrence=2,
        ),
        parsed_pages=pdf_pages,
        document_map=pdf_dmap,
    )
    assert cit_pdf.matched is True and cit_pdf.source_kind == "canonical_text"
    assert cit_pdf.char_start is not None and cit_pdf.char_end is not None

    docx_quote = "InstitutionalFiler entities file Form 13F holdings"
    cit_docx = svc.resolve_citation_span(
        pid,
        CitationResolveReq(
            doc_id=docx_src.source_id,
            page_number=1,
            verbatim_quote=docx_quote,
            occurrence=1,
        ),
    )
    assert cit_docx.matched is True and cit_docx.char_start is not None

    # 3. Project-bound opendv.grounding with live agy
    gnd_resp = await svc.discovery.ground_ontology(
        OntologyGroundReq(project_id=pid, policy=GroundingPolicy(mode="agentic"))
    )
    assert len(gnd_resp.tables) == 1
    assert gnd_resp.tables[0].table_name == "institutional_filers"
    assert REDACTED_LEAK_RE.search(gnd_resp.model_dump_json()) is None

    # 4. Step 4 live agy discovery (Graph A remains uncommitted until steward commit)
    s4_run = await svc.start_ontology_construction(
        pid,
        OntologyConstructReq(
            discovery_mode="incremental_new",
            hint="Model InstitutionalFiler (institutional_filers.filer_cik) concisely.",
        ),
    )
    for _ in range(3):
        if s4_run.status != "HOLD_CHECKPOINTED":
            break
        answers = {
            q.question_id: (q.recommended_answer or "filer_cik")
            for q in s4_run.pending_questions
        }
        s4_run = await svc.answer_ontology_hold(pid, s4_run.run_id, HoldAnswersReq(answers=answers))
    assert s4_run.status == "COMPLETED"
    assert svc.get_project(pid).active_ontology_rev_id == initial_rev

    # 5. Steward commits ontology with page 5 occurrence=2 + DOCX citations -> Step 6 live CEGAR + Gate V4
    svc.commit_ontology(
        pid,
        OntologyCommitReq.model_validate(
            {
                "base_revision_id": initial_rev,
                "rationale": "Live agy Case 1 grounded InstitutionalFiler commit.",
                "low_confidence_grounding_waiver": True,
                "grounding_rationale": "Grounded against SQLite institutional_filers, PDF p5 occ2, and DOCX spec.",
                "waive_quarantine": True,
                "operations": [
                    {
                        "op_id": "op_live_filer",
                        "op_type": "AddConcept",
                        "concept_uri": "urn:asset_management:InstitutionalFiler",
                        "concept_name": "InstitutionalFiler",
                        "description": "SEC 13F institutional investment manager keyed by filer_cik.",
                        "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                        "table_bindings": [
                            {
                                "source_id": sql_src.source_id,
                                "table_id": f"{sql_src.source_id}:institutional_filers",
                                "col_name": "filer_cik",
                            }
                        ],
                        "source_citations": [
                            {
                                "doc_id": pdf_src.source_id,
                                "page_number": 5,
                                "verbatim_quote": PDF_EXCEPTION_QUOTE,
                                "occurrence": 2,
                                "char_start": cit_pdf.char_start,
                                "char_end": cit_pdf.char_end,
                                "content_sha256": cit_pdf.resolved_sha256,
                            },
                            {
                                "doc_id": docx_src.source_id,
                                "page_number": 1,
                                "verbatim_quote": docx_quote,
                                "occurrence": 1,
                                "char_start": cit_docx.char_start,
                                "char_end": cit_docx.char_end,
                                "content_sha256": cit_docx.resolved_sha256,
                            },
                        ],
                        "rationale": "Bind InstitutionalFiler to institutional_filers.filer_cik",
                    }
                ],
            }
        ),
    )

    s6_run = await svc.build_vault(pid, VaultBuildReq(policy="fast"))
    for _ in range(3):
        if s6_run.status != "HOLD_CHECKPOINTED":
            break
        answers6 = {
            q.question_id: (
                q.recommended_answer
                or (q.options[0].option_id if q.options else "filer_cik")
            )
            for q in s6_run.pending_questions
        }
        s6_run = await svc.submit_vault_decisions(pid, s6_run.run_id, HoldAnswersReq(answers=answers6))
    assert s6_run.status in {"COMPLETED", "CERTIFIED_FAST"}
    assert len(svc.get_vault_ir(pid).hubs) >= 1
    assert svc.verify_vault(pid).passed is True

    # Explicit Gate V4 check on hydrated VerifierContext with page 5 occurrence=2 + DOCX citations
    _, _, _, _, _, _, v4_ctx = load_verifier_inputs(svc.state_manager, env.ws, pid)
    assert set(v4_ctx.parsed_doc_pages[pdf_src.source_id].keys()) == {1, 2, 3, 4, 5, 6}
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
    pdf_binding = DraftSourceBinding(
        source_id=pdf_src.source_id,
        table_id=f"{pdf_src.source_id}:page_5",
        col_mappings={},
        doc_citations=[
            DocumentCitationRef(
                doc_id=pdf_src.source_id,
                page_number=5,
                verbatim_quote=PDF_EXCEPTION_QUOTE,
                occurrence=2,
                char_start=cit_pdf.char_start,
                char_end=cit_pdf.char_end,
                content_sha256=cit_pdf.resolved_sha256,
            ),
        ],
    )
    docx_binding = DraftSourceBinding(
        source_id=docx_src.source_id,
        table_id=f"{docx_src.source_id}:page_1",
        col_mappings={},
        doc_citations=[
            DocumentCitationRef(
                doc_id=docx_src.source_id,
                page_number=1,
                verbatim_quote=docx_quote,
                occurrence=1,
                char_start=cit_docx.char_start,
                char_end=cit_docx.char_end,
                content_sha256=cit_docx.resolved_sha256,
            ),
        ],
    )
    cit_draft = DraftVaultIR(
        project_id=pid,
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_filer",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id=f"{pdf_src.source_id}:page_5",
                        col_name="filer_cik",
                        source_id=pdf_src.source_id,
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="SEC_CIK",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=pdf_src.source_id,
                        table_id=f"{pdf_src.source_id}:page_5",
                        col_mappings={"filer_cik": "filer_cik"},
                    )
                ],
            ),
            DraftHubSpec(
                hub_name="hub_exception",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=f"{docx_src.source_id}:page_1",
                        col_name="exception_id",
                        source_id=docx_src.source_id,
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="DOC",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=docx_src.source_id,
                        table_id=f"{docx_src.source_id}:page_1",
                        col_mappings={"exception_id": "exception_id"},
                    )
                ],
            ),
        ],
        links=[
            DraftLinkSpec(
                link_name="lnk_filer_exception",
                link_kind="UNSTRUCTURED_EXTRACTION_LINK",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_filer",
                        role_name="filer",
                        fk_col_name="hk_filer",
                        source_col_names=["filer_cik"],
                    ),
                    LinkParticipantSpec(
                        hub_name="hub_exception",
                        role_name="exception",
                        fk_col_name="hk_exception",
                        source_col_names=["exception_id"],
                    ),
                ],
                source_bindings=[pdf_binding, docx_binding],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_filer_exception_prov",
                parent_name="lnk_filer_exception",
                sat_kind="AI_PROVENANCE_SATELLITE",
                attributes=prov_attrs,
                source_bindings=[pdf_binding, docx_binding],
            )
        ],
        column_triage=[
            ColumnTriageEntry(
                table_id=f"{pdf_src.source_id}:page_5",
                col_name="filer_cik",
                disposition="MODELED",
                target_ref="hub_filer",
            ),
            ColumnTriageEntry(
                table_id=f"{docx_src.source_id}:page_1",
                col_name="exception_id",
                disposition="MODELED",
                target_ref="hub_exception",
            ),
            ColumnTriageEntry(
                table_id=f"{pdf_src.source_id}:page_5",
                col_name="clause_text",
                disposition="MODELED",
                target_ref="lnk_filer_exception",
            ),
        ],
    )
    _, cert_v4, _ = run_l1_codemods(cit_draft)
    assert verify_v4_unstructured(cert_v4, v4_ctx) == []

    assert snapshot_dir_mtimes(env.host_local_opendv) == host_before
    assert snapshot_dir_mtimes(home_opendv) == home_before


# ---------------------------------------------------------------------------
# Case 2: Standalone `agy` + Skill + CLI + MCP `--profile parsing`
# ---------------------------------------------------------------------------


@pytest.mark.live_provider
@pytest.mark.asyncio
async def test_live_agy_uat_case2_standalone_agy_skill_cli_and_mcp_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 2: Standalone `agy` + Skill + `opendv parse` CLI + `mcp serve --profile parsing`."""
    binary = resolve_cli_binary("agy")
    assert binary
    env = setup_isolated_env(tmp_path / "case2", monkeypatch, preserve_live_auth=True)
    host_before = snapshot_dir_mtimes(env.host_local_opendv)
    home_opendv = Path.home() / ".opendv"
    home_before = snapshot_dir_mtimes(home_opendv)

    stateless_ws = tmp_path / "stateless_ws"
    stateless_ws.mkdir(parents=True, exist_ok=True)
    user_state = tmp_path / "case2_user_state"
    (user_state / "opendv").mkdir(parents=True, exist_ok=True)
    (user_state / "Roaming").mkdir(parents=True, exist_ok=True)
    spill_dir = tmp_path / "case2_spill"

    cli_env = _live_cli_env(
        stateless_ws,
        stateless_ws / ".opendv",
        tmp_path,
        localappdata_override=user_state,
    )

    fdic_pdf = SAMPLE_PDFS / "fdic_section3_2_loans_credit_risk.pdf"
    scan_pdf = SAMPLE_PDFS / "institutional_scanned_schedule_ocr_test.pdf"
    fed_g19 = PUBLIC_FIXTURES / "fed_g19_consumer_credit.pdf"
    irs_docx = PUBLIC_FIXTURES / "irs_soi_21incddocguide.docx"
    stripe_md = PUBLIC_FIXTURES / "fivetran_dbt_stripe_readme.md"
    rfc9110_txt = PUBLIC_FIXTURES / "rfc9110_http_semantics.txt"

    # 1. Stateless CLI (`opendv --json parse ...`) across sample_pdfs + public internet fixtures
    code_spec, env_spec = _run_cli_subprocess(["parse", "spec"], cwd=stateless_ws, env=cli_env)
    assert code_spec == 0 and env_spec.ok is True

    insp_summaries: list[dict[str, Any]] = []
    for doc_path in (fdic_pdf, scan_pdf, fed_g19, irs_docx, stripe_md, rfc9110_txt):
        c_insp, e_insp = _run_cli_subprocess(
            ["parse", "inspect", str(doc_path)], cwd=stateless_ws, env=cli_env
        )
        assert c_insp == 0 and isinstance(e_insp.data, dict)
        insp_summaries.append({"filename": doc_path.name, **e_insp.data})

    # FDIC extract -> validate -> export chunks JSONL -> DuckDB read_ndjson
    fdic_ir = stateless_ws / "fdic_ir.json"
    c_ext, _ = _run_cli_subprocess(
        ["parse", "extract", str(fdic_pdf), "--pages", "1-2", "--out", str(fdic_ir)],
        cwd=stateless_ws,
        env=cli_env,
    )
    assert c_ext == 0
    c_val, _ = _run_cli_subprocess(["parse", "validate", str(fdic_ir)], cwd=stateless_ws, env=cli_env)
    assert c_val == 0
    chunks_path = stateless_ws / "fdic_chunks.jsonl"
    c_exp, _ = _run_cli_subprocess(
        [
            "parse",
            "export",
            str(fdic_ir),
            "--target",
            "chunks",
            "--format",
            "jsonl",
            "--out",
            str(chunks_path),
        ],
        cwd=stateless_ws,
        env=cli_env,
    )
    assert c_exp == 0 and chunks_path.is_file()
    conn = duckdb.connect(":memory:")
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM read_ndjson(?, auto_detect=true)", [str(chunks_path)]
        ).fetchone()
        assert cnt is not None and cnt[0] >= 1
    finally:
        conn.close()
    assert len(ChunkRecord.model_fields) > 0

    # Extract & validate real internet-sourced docs across all 4 formats
    for extra_doc, extra_flags in (
        (fed_g19, ["--redact"]),
        (irs_docx, []),
        (stripe_md, []),
        (rfc9110_txt, ["--pages", "1-2"]),
    ):
        out_p = stateless_ws / f"{extra_doc.stem}_ir.json"
        ce, _ = _run_cli_subprocess(
            ["parse", "extract", str(extra_doc), *extra_flags, "--out", str(out_p)],
            cwd=stateless_ws,
            env=cli_env,
        )
        assert ce == 0
        cv, _ = _run_cli_subprocess(["parse", "validate", str(out_p)], cwd=stateless_ws, env=cli_env)
        assert cv == 0
        ParsedDocumentIR.model_validate_json(out_p.read_text(encoding="utf-8"))

    # Scanned page render-page gate: fails without --approve-scan, succeeds with --approve-scan
    c_blk, e_blk = _run_cli_subprocess(
        ["parse", "render-page", str(scan_pdf), "--page", "2"],
        cwd=stateless_ws,
        env=cli_env,
    )
    assert c_blk == int(CliExitCode.VALIDATION_ERROR)
    assert e_blk.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    scan_png = stateless_ws / "scan_p2.png"
    c_app, e_app = _run_cli_subprocess(
        ["parse", "render-page", str(scan_pdf), "--page", "2", "--approve-scan", "--out", str(scan_png)],
        cwd=stateless_ws,
        env=cli_env,
    )
    assert c_app == 0 and scan_png.is_file()
    assert isinstance(e_app.data, dict) and e_app.data["redacted"] is False
    with Image.open(scan_png) as im:
        assert im.width > 0 and im.height > 0

    # 2. MCP `--profile parsing` (fail-closed MODEL_EGRESS vs --allow-unredacted)
    strict_params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "opendv.cli",
            "mcp",
            "serve",
            "--transport",
            "stdio",
            "--profile",
            "parsing",
            "--allow-root",
            str(REPO_ROOT),
            "--allow-root",
            str(stateless_ws),
            "--spill-dir",
            str(spill_dir),
        ],
        env=cli_env,
    )
    async with stdio_client(strict_params) as (r_s, w_s), ClientSession(r_s, w_s) as sess:
        await sess.initialize()
        listed = await sess.list_tools()
        assert {t.name for t in listed.tools} == set(PARSING_MCP_TOOL_NAMES)

        err_g19, g19_ir, _ = await _call_mcp_tool(
            sess, "parse_extract", {"uri": str(fed_g19)}
        )
        assert err_g19 is False and g19_ir["provenance"]["redacted"] is True

        # approve_scan=True fails closed under MODEL_EGRESS when --allow-unredacted is False
        err_scan_blk, _, prob_scan_blk = await _call_mcp_tool(
            sess,
            "parse_render_page",
            {"uri": str(scan_pdf), "page_number": 2, "approve_scan": True},
        )
        assert err_scan_blk is True and prob_scan_blk is not None
        assert prob_scan_blk.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    unred_params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "opendv.cli",
            "mcp",
            "serve",
            "--transport",
            "stdio",
            "--profile",
            "parsing",
            "--allow-root",
            str(REPO_ROOT),
            "--allow-root",
            str(stateless_ws),
            "--allow-unredacted",
            "--spill-dir",
            str(spill_dir),
        ],
        env=cli_env,
    )
    async with stdio_client(unred_params) as (r_s, w_s), ClientSession(r_s, w_s) as sess:
        await sess.initialize()
        err_ok, ok_res, _ = await _call_mcp_tool(
            sess,
            "parse_render_page",
            {"uri": str(scan_pdf), "page_number": 2, "approve_scan": True},
        )
        assert err_ok is False and ok_res["redacted"] is False
        assert bool(ok_res["png_b64"]) or bool(ok_res["spillover"])

    # 3. Live `agy` CLI invocation with SKILL.md context via build_agy_command
    skill_text = PARSING_SKILL_PATH.read_text(encoding="utf-8")
    ensure_no_redacted_placeholder(skill_text)
    model_id = resolve_model_for_agent("agy", "STANDARD_WORKER", env=os.environ).model_id
    schema_json = json.dumps(_StandaloneParsingUatPlan.model_json_schema(), separators=(",", ":"))
    prompt = (
        f"You are operating the Open-DV unstructured parsing skill.\n"
        f"Skill reference:\n{skill_text[:3000]}\n\n"
        f"Inspect results from real documents:\n{json.dumps(insp_summaries, indent=2)}\n\n"
        "Return ONLY valid JSON matching the schema listing the 4 distinct format_id values "
        "observed, the scanned PDF filename (`institutional_scanned_schedule_ocr_test.pdf`) and its "
        "scanned_pages list, and at least 3 valid `uv run opendv --json parse ...` commands."
    )

    child_env = build_isolated_child_env(provider="agy", step="step4", parent_env=os.environ)
    run_state: dict[str, Any] = {"returncode": 0, "stdout": "", "stderr": ""}
    text_deltas: list[str] = []
    last_result: dict[str, Any] | None = None
    seen_fine = False

    with IsolatedTempCwd() as temp_cwd:
        schema_path = temp_cwd / "schema.json"
        schema_path.write_text(schema_json, encoding="utf-8")
        cmd, stdin_txt = build_agy_command(
            binary,
            model_id=model_id,
            prompt=prompt,
            json_schema_path=str(schema_path),
        )
        assert "--mcp-config" not in cmd and "--tools" not in cmd
        async for line in stream_cli_subprocess_lines(
            cmd,
            input_text=stdin_txt,
            env=child_env,
            cwd=temp_cwd,
            timeout_seconds=120.0,
            state_out=run_state,
            cli_label="agy CLI",
        ):
            _, c_chunks, res_obj, is_fine = parse_ndjson_stream_line(
                line, seen_fine_grained_deltas=seen_fine
            )
            if is_fine:
                seen_fine = True
            text_deltas.extend(c_chunks)
            if res_obj is not None:
                last_result = dict(res_obj)

    assert int(run_state["returncode"]) == 0, run_state["stderr"] or run_state["stdout"]
    payload = last_result or extract_json_from_stdout(
        str(run_state["stdout"]), cli_label="agy CLI", primary_text_key="response"
    )
    assert payload.get("is_error") is not True, payload
    raw_res = payload.get("response") if payload.get("response") is not None else payload.get("result")
    text_out = raw_res if isinstance(raw_res, str) else "".join(text_deltas)
    plan = validate_cli_structured_output(
        _StandaloneParsingUatPlan,
        raw_structured=payload.get("structured_output") or (raw_res if isinstance(raw_res, dict) else None),
        raw_response_text=text_out,
        cli_label="agy CLI",
    )
    assert isinstance(plan, _StandaloneParsingUatPlan)
    assert {"pdf", "docx", "markdown", "domain_note"}.issubset(set(plan.detected_formats))
    assert "institutional_scanned_schedule_ocr_test.pdf" in plan.scanned_pdf_filename
    assert 2 in plan.scanned_page_numbers

    assert not (stateless_ws / ".opendv").exists()
    assert snapshot_dir_mtimes(env.host_local_opendv) == host_before
    assert snapshot_dir_mtimes(home_opendv) == home_before
