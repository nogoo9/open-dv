"""E2E Tiers 1 & 2 tests for F08 (CLI), F09 (MCP), F10 (Grounding), and F11 (Modeling & Gate V4)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from opendv.models.errors import CliExitCode, ErrorCode
from tests.parsing.e2e.conftest import (
    REPO_ROOT,
    E2EWorkspace,
    call_mcp_tool_json,
    make_boundary_pii_text_file,
    make_high_pixel_pdf,
    make_minimal_pdf,
    make_pii_multi_token_pdf,
    make_scanned_image_pdf,
    open_mcp_session,
    query_ndjson_with_duckdb,
)


def _init_project_with_baseline(
    ws: E2EWorkspace,
    project_id: str,
) -> Any:
    """Initialize an isolated project with a minimal valid baseline ontology."""
    from opendv.models.ontology import (
        CanonicalBusinessConcept,
        ConceptAttribute,
        GraphAOntology,
    )
    from opendv.models.wire_dtos_discovery import ProjectInitReq
    from opendv.service import OpenDVService

    ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="ibor",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ibor:Account",
                concept_name="Account",
                description="Client investment account.",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                attributes=[
                    ConceptAttribute(
                        name="account_id",
                        logical_type="UTF8_STRING",
                        sql_type_hint="VARCHAR",
                        is_business_key=True,
                        is_pii=False,
                        nullable=False,
                    )
                ],
            )
        ],
    )
    svc = OpenDVService(ws.workspace, state_dir=ws.state_dir)
    svc.init_project(
        ProjectInitReq(
            project_id=project_id,
            domain_namespace="ibor",
            baseline_content=ont.model_dump_json(),
            baseline_format="json",
        )
    )
    return svc


def _build_v4_citation_draft(
    *,
    doc_id: str,
    page_number: int,
    verbatim_quote: str,
    occurrence: int,
    char_start: int,
    char_end: int,
    content_sha256: str,
    profiles: list[Any] | None = None,
) -> Any:
    """Construct a minimal DraftVaultIR exercising Gate V4 unstructured citation rules."""
    from opendv.models.diagnostics import DocumentCitationRef
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
        DraftField(
            name="extractor_version",
            logical_type="UTF8_STRING",
            nullable=False,
            is_pii=False,
        ),
        DraftField(
            name="prompt_or_rule_id",
            logical_type="UTF8_STRING",
            nullable=False,
            is_pii=False,
        ),
        DraftField(
            name="confidence_score",
            logical_type=DecimalType(precision=5, scale=4),
            nullable=False,
            is_pii=False,
        ),
        DraftField(
            name="review_status",
            logical_type="UTF8_STRING",
            nullable=False,
            is_pii=False,
        ),
        DraftField(
            name="citation_span",
            logical_type="JSON_DOCUMENT",
            nullable=False,
            is_pii=False,
        ),
        DraftField(
            name="source_doc_id",
            logical_type="UTF8_STRING",
            nullable=False,
            is_pii=False,
        ),
    ]
    triage = [
        ColumnTriageEntry(
            table_id=tbl.table_id,
            col_name=col.col_name,
            disposition="OMITTED_INTENTIONALLY",
            rationale="Unstructured Gate V4 verification test",
        )
        for prof in (profiles or [])
        for tbl in prof.tables
        for col in tbl.columns
    ]
    return DraftVaultIR(
        project_id="v4_e2e",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_document",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id="doc",
                        col_name="doc_id",
                        source_id=doc_id,
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=doc_id,
                        table_id="doc",
                        col_mappings={"doc_id": "doc_id"},
                    )
                ],
            ),
            DraftHubSpec(
                hub_name="hub_clause",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id="doc",
                        col_name="clause_id",
                        source_id=doc_id,
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="STEWARDSHIP",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=doc_id,
                        table_id="doc",
                        col_mappings={"clause_id": "clause_id"},
                    )
                ],
            ),
        ],
        links=[
            DraftLinkSpec(
                link_name="lnk_doc_clause",
                link_kind="UNSTRUCTURED_EXTRACTION_LINK",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_document",
                        role_name="DOC",
                        fk_col_name="hk_document",
                        source_col_names=["doc_id"],
                    ),
                    LinkParticipantSpec(
                        hub_name="hub_clause",
                        role_name="CLAUSE",
                        fk_col_name="hk_clause",
                        source_col_names=["clause_id"],
                    ),
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=doc_id,
                        table_id="doc",
                        col_mappings={"doc_id": "doc_id"},
                        doc_citations=[cit],
                    )
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_doc_clause_prov",
                sat_kind="AI_PROVENANCE_SATELLITE",
                parent_name="lnk_doc_clause",
                source_bindings=[
                    DraftSourceBinding(
                        source_id=doc_id,
                        table_id="doc",
                        col_mappings={"review_status": "review_status"},
                    )
                ],
                attributes=prov_attrs,
            )
        ],
        column_triage=triage,
    )


# ---------------------------------------------------------------------------
# F08: Stateless CLI (`opendv [--json] parse ...`)
# ---------------------------------------------------------------------------


def test_f08_t1_cli_root_help_and_spec_register_parse_group_13_entries_67_subcommands(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
) -> None:
    """Top-level `opendv --help` and `build_cli_spec()` expose 13 entries / 67 subcommands."""
    from opendv.cli.export_contracts import build_cli_spec

    res = cli_runner(["--help"], expect_json=False)
    assert res.returncode == 0
    assert "parse" in res.stdout

    spec = build_cli_spec()
    subcommands = spec["subcommands"]
    top_entries = {cmd["path"][0] for cmd in subcommands}
    assert len(top_entries) == 13
    assert len(subcommands) == 67
    assert "parse" in top_entries

    by_path = {" ".join(cmd["path"]): cmd for cmd in subcommands}
    for sub in ("spec", "inspect", "extract", "render-page", "validate", "export"):
        entry = by_path[f"parse {sub}"]
        assert entry["http_method"] is None
        assert entry["http_route"] is None

    e2e_workspace.assert_no_workspace_opendv_dir()


def test_f08_t1_cli_parse_spec_returns_valid_envelope_and_capabilities(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
) -> None:
    """`opendv --json parse spec` emits CliEnvelope with schemas, 4 formats, and 3 projectors."""
    res = cli_runner(["parse", "spec"])
    assert res.returncode == 0
    assert res.envelope is not None and res.envelope.ok is True
    data = res.envelope.data
    assert isinstance(data, dict)
    assert set(data["formats"]) == {"pdf", "docx", "markdown", "domain_note"}
    assert set(data["projectors"]) == {"document_map", "markdown", "chunks"}
    assert "capabilities" in data and "schemas" in data
    e2e_workspace.assert_no_workspace_opendv_dir()


def test_f08_t1_cli_parse_inspect_returns_digital_and_scanned_pages(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
) -> None:
    """`opendv --json parse inspect` classifies digital vs. scanned pages without .opendv writes."""
    pdf = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"
    res = cli_runner(["parse", "inspect", str(pdf)])
    assert res.returncode == 0
    assert res.envelope is not None and res.envelope.ok is True
    data = res.envelope.data
    assert isinstance(data, dict)
    assert data["format_id"] == "pdf"
    assert data["page_count"] == 6
    assert 4 in data["scanned_pages"]
    assert 1 in data["digital_pages"]
    assert data["renderable"] is True
    e2e_workspace.assert_no_workspace_opendv_dir()


def test_f08_t1_cli_parse_extract_pages_and_out_atomic_write(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
) -> None:
    """`parse extract --pages 1-2,5 --out` writes valid ParsedDocumentIR and emits summary."""
    from opendv.models.document_ir import ParsedDocumentIR

    pdf = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"
    out_ir = e2e_workspace.workspace / "extracted_ir.json"

    res = cli_runner(["parse", "extract", str(pdf), "--pages", "1-2,5", "--out", str(out_ir)])
    assert res.returncode == 0
    assert res.envelope is not None and res.envelope.ok is True
    assert out_ir.is_file()

    ir = ParsedDocumentIR.model_validate_json(out_ir.read_text(encoding="utf-8"))
    assert [p.page_number for p in ir.pages] == [1, 2, 5]
    assert ir.provenance.redacted is False
    assert isinstance(res.envelope.data, dict)
    e2e_workspace.assert_no_workspace_opendv_dir()


def test_f08_t1_cli_parse_render_page_out_png_and_inline_b64(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
) -> None:
    """`parse render-page` supports --out PNG file output as well as inline png_b64."""
    pdf = make_minimal_pdf(e2e_workspace.workspace / "render_cli.pdf")
    out_png = e2e_workspace.workspace / "page1.png"

    res_file = cli_runner(
        ["parse", "render-page", str(pdf), "--page", "1", "--dpi", "120", "--out", str(out_png)]
    )
    assert res_file.returncode == 0
    assert res_file.envelope is not None and isinstance(res_file.envelope.data, dict)
    assert res_file.envelope.data["output_path"] is not None
    assert res_file.envelope.data["png_b64"] is None
    assert out_png.is_file()
    with Image.open(out_png) as img_file:
        assert img_file.width > 0 and img_file.height > 0

    res_inline = cli_runner(["parse", "render-page", str(pdf), "--page", "1", "--dpi", "120"])
    assert res_inline.returncode == 0
    assert res_inline.envelope is not None and isinstance(res_inline.envelope.data, dict)
    b64_val = res_inline.envelope.data["png_b64"]
    assert isinstance(b64_val, str) and len(b64_val) > 0
    assert res_inline.envelope.data["output_path"] is None
    with Image.open(io.BytesIO(base64.b64decode(b64_val))) as img_b64:
        assert img_b64.width > 0 and img_b64.height > 0
    e2e_workspace.assert_no_workspace_opendv_dir()


def test_f08_t1_cli_parse_validate_valid_ir_returns_exit_0(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
) -> None:
    """`parse validate` returns exit 0 and writes empty diagnostics list to --out on valid IR."""
    pdf = make_minimal_pdf(e2e_workspace.workspace / "val_ok.pdf")
    ir_json = e2e_workspace.workspace / "ok_ir.json"
    val_json = e2e_workspace.workspace / "val_out.json"

    assert cli_runner(["parse", "extract", str(pdf), "--out", str(ir_json)]).returncode == 0
    res = cli_runner(["parse", "validate", str(ir_json), "--out", str(val_json)])
    assert res.returncode == 0
    assert res.envelope is not None and res.envelope.ok is True
    assert val_json.is_file()
    assert json.loads(val_json.read_text(encoding="utf-8")) == []
    e2e_workspace.assert_no_workspace_opendv_dir()


def test_f08_t1_cli_parse_export_chunks_jsonl_duckdb_read_ndjson(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """`parse export --target chunks --format jsonl --out` produces uniform DuckDB-readable NDJSON."""
    docx_path = public_fixture("aspose_merged_table.docx")
    ir_json = e2e_workspace.workspace / "docx_ir.json"
    chunks_jsonl = e2e_workspace.workspace / "chunks.jsonl"

    assert cli_runner(["parse", "extract", str(docx_path), "--out", str(ir_json)]).returncode == 0
    res = cli_runner(
        [
            "parse",
            "export",
            str(ir_json),
            "--target",
            "chunks",
            "--format",
            "jsonl",
            "--out",
            str(chunks_jsonl),
        ]
    )
    assert res.returncode == 0
    assert chunks_jsonl.is_file()

    rows = query_ndjson_with_duckdb(chunks_jsonl)
    assert len(rows) >= 1
    expected_cols = {"chunk_id", "doc_sha256", "format_id", "page_number", "table_json", "bbox_json"}
    assert expected_cols.issubset(rows[0].keys())
    e2e_workspace.assert_no_workspace_opendv_dir()


def test_f08_t2_cli_parse_rejects_api_url_and_env_api_url(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
) -> None:
    """`parse` rejects --api-url and OPENDV_API_URL with SYNTAX_INVALID_ARGUMENT (exit 2)."""
    res_flag = cli_runner(["--api-url", "http://127.0.0.1:8420", "parse", "spec"])
    assert res_flag.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert res_flag.envelope is not None
    assert res_flag.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    res_env = cli_runner(
        ["parse", "spec"],
        env_extra={"OPENDV_API_URL": "http://127.0.0.1:8420"},
    )
    assert res_env.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert res_env.envelope is not None
    assert res_env.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT
    e2e_workspace.assert_no_workspace_opendv_dir()


def test_f08_t2_cli_parse_validate_tampered_ir_returns_exit_2_verification_gate_failed(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
) -> None:
    """`parse validate` on tampered canonical_text exits 2 with VERIFICATION_GATE_FAILED."""
    pdf = make_minimal_pdf(e2e_workspace.workspace / "tamper.pdf")
    ir_json = e2e_workspace.workspace / "tamper_ir.json"
    assert cli_runner(["parse", "extract", str(pdf), "--out", str(ir_json)]).returncode == 0

    raw = json.loads(ir_json.read_text(encoding="utf-8"))
    raw["pages"][0]["canonical_text"] = "Tampered page body."
    ir_json.write_text(json.dumps(raw), encoding="utf-8")

    res = cli_runner(["parse", "validate", str(ir_json)])
    assert res.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert res.envelope is not None and res.envelope.ok is False
    assert res.envelope.error_code == ErrorCode.VERIFICATION_GATE_FAILED
    assert isinstance(res.envelope.data, list) and len(res.envelope.data) >= 1


def test_f08_t2_cli_parse_export_json_jsonl_without_out_rejected(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
) -> None:
    """`opendv --json parse export --format jsonl` without `--out` fails with exit 2."""
    pdf = make_minimal_pdf(e2e_workspace.workspace / "no_out.pdf")
    ir_json = e2e_workspace.workspace / "no_out_ir.json"
    assert cli_runner(["parse", "extract", str(pdf), "--out", str(ir_json)]).returncode == 0

    res = cli_runner(["parse", "export", str(ir_json), "--target", "chunks", "--format", "jsonl"])
    assert res.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert res.envelope is not None
    assert res.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


def test_f08_t2_cli_parse_render_page_rejects_non_renderable_and_oob_page(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """`parse render-page` rejects non-renderable DOCX and out-of-range page numbers."""
    docx_path = public_fixture("aspose_merged_table.docx")
    res_docx = cli_runner(["parse", "render-page", str(docx_path), "--page", "1"])
    assert res_docx.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert res_docx.envelope is not None
    assert res_docx.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    pdf = make_minimal_pdf(e2e_workspace.workspace / "one_page.pdf")
    res_oob = cli_runner(["parse", "render-page", str(pdf), "--page", "99"])
    assert res_oob.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert res_oob.envelope is not None
    assert res_oob.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


def test_f08_t2_cli_parse_render_page_pixel_budget_413_and_dpi_cap_400(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
) -> None:
    """`parse render-page` enforces dpi<=300 (400) and <=40M pixel preflight cap (413)."""
    pdf = make_minimal_pdf(e2e_workspace.workspace / "dpi.pdf")
    res_dpi = cli_runner(["parse", "render-page", str(pdf), "--page", "1", "--dpi", "301"])
    assert res_dpi.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert res_dpi.envelope is not None
    assert res_dpi.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    huge_pdf = make_high_pixel_pdf(e2e_workspace.workspace / "huge_px.pdf")
    res_px = cli_runner(["parse", "render-page", str(huge_pdf), "--page", "1", "--dpi", "150"])
    assert res_px.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert res_px.envelope is not None
    assert res_px.envelope.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED


def test_f08_t2_cli_parse_inspect_allows_gt_max_pages_while_extract_enforces_413(
    cli_runner: Callable[..., Any],
    public_fixture: Callable[[str], Path],
) -> None:
    """`parse inspect --max-pages 2` succeeds on 6p PDF, while `parse extract --max-pages 2` fails 413."""
    pdf = public_fixture("irs_f1120.pdf")

    res_inspect = cli_runner(["parse", "inspect", str(pdf), "--max-pages", "2"])
    assert res_inspect.returncode == 0
    assert res_inspect.envelope is not None and isinstance(res_inspect.envelope.data, dict)
    assert res_inspect.envelope.data["page_count"] == 6

    res_extract = cli_runner(["parse", "extract", str(pdf), "--max-pages", "2"])
    assert res_extract.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert res_extract.envelope is not None
    assert res_extract.envelope.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED


# ---------------------------------------------------------------------------
# F09: Unified Inbound MCP (`opendv mcp serve --profile project|parsing|all`)
# ---------------------------------------------------------------------------


def test_f09_t1_mcp_profile_parsing_exposes_exact_6_tools_without_project(
    e2e_workspace: E2EWorkspace,
) -> None:
    """`--profile parsing` serves exactly 6 zero-LLM tools without creating `.opendv/`."""
    expected = {
        "parse_spec",
        "parse_inspect",
        "parse_extract",
        "parse_render_page",
        "parse_validate_ir",
        "parse_export_ir",
    }

    async def _run() -> None:
        async with open_mcp_session(
            "parsing",
            workspace=e2e_workspace,
            allow_roots=[e2e_workspace.workspace],
        ) as (session, tools):
            assert set(tools.keys()) == expected
            is_err, payload, prob = await call_mcp_tool_json(session, "parse_spec", {})
            assert is_err is False and prob is None
            assert set(payload["formats"]) == {"pdf", "docx", "markdown", "domain_note"}

    asyncio.run(_run())
    e2e_workspace.assert_no_workspace_opendv_dir()


def test_f09_t1_mcp_profile_project_default_preserves_17_tools(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Default and explicit `--profile project` expose exactly the 17 lifecycle tools."""
    from opendv.cli.mcp_stdio import MCP_TOOL_NAMES

    _init_project_with_baseline(e2e_workspace, "p_mcp_17")

    async def _run() -> None:
        for explicit in (False, True):
            async with open_mcp_session(
                "project",
                workspace=e2e_workspace,
                project="p_mcp_17",
                explicit_profile_flag=explicit,
            ) as (_session, tools):
                assert len(tools) == 17
                assert set(tools.keys()) == set(MCP_TOOL_NAMES)
                assert not any(name.startswith("parse_") for name in tools)

    asyncio.run(_run())


def test_f09_t1_mcp_profile_all_exposes_23_tools(
    e2e_workspace: E2EWorkspace,
) -> None:
    """`--profile all` serves all 23 tools (17 project + 6 parsing) concurrently."""
    _init_project_with_baseline(e2e_workspace, "p_mcp_all")

    async def _run() -> None:
        async with open_mcp_session(
            "all",
            workspace=e2e_workspace,
            project="p_mcp_all",
            allow_roots=[e2e_workspace.workspace],
        ) as (session, tools):
            assert len(tools) == 23
            err_proj, proj_data, _ = await call_mcp_tool_json(session, "project_get", {})
            assert err_proj is False and proj_data["project_id"] == "p_mcp_all"
            err_spec, spec_data, _ = await call_mcp_tool_json(session, "parse_spec", {})
            assert err_spec is False and "schemas" in spec_data

    asyncio.run(_run())


def test_f09_t1_mcp_parse_extract_defaults_to_model_egress_redaction(
    e2e_workspace: E2EWorkspace,
) -> None:
    """`parse_extract` over MCP defaults to MODEL_EGRESS redaction (`redact=True`)."""
    from opendv.models.pii_tokenizer import MASKED_PII_TOKEN

    pdf_path = make_pii_multi_token_pdf(e2e_workspace.workspace / "mcp_pii.pdf")

    async def _run() -> None:
        async with open_mcp_session(
            "parsing",
            workspace=e2e_workspace,
            allow_roots=[e2e_workspace.workspace],
        ) as (session, _tools):
            is_err, payload, _ = await call_mcp_tool_json(
                session,
                "parse_extract",
                {"uri": str(pdf_path)},
            )
            assert is_err is False
            assert payload["provenance"]["redacted"] is True
            assert payload["provenance"]["sink_class"] == "MODEL_EGRESS"
            text = payload["pages"][0]["canonical_text"]
            assert MASKED_PII_TOKEN in text
            for secret in ("(415) 555-0199", "4111 1111 1111 1111", "021000021", "123-45-6789"):
                assert secret not in text

    asyncio.run(_run())


def test_f09_t1_mcp_parse_validate_and_export_ir_roundtrip(
    e2e_workspace: E2EWorkspace,
) -> None:
    """`parse_extract` -> `parse_validate_ir` -> `parse_export_ir(target='markdown')` roundtrips."""
    from opendv.models.pii_tokenizer import MASKED_PII_TOKEN

    pdf_path = make_pii_multi_token_pdf(e2e_workspace.workspace / "mcp_roundtrip.pdf")

    async def _run() -> None:
        async with open_mcp_session(
            "parsing",
            workspace=e2e_workspace,
            allow_roots=[e2e_workspace.workspace],
        ) as (session, _tools):
            is_err, ir_payload, _ = await call_mcp_tool_json(
                session,
                "parse_extract",
                {"uri": str(pdf_path)},
            )
            assert is_err is False

            val_err, val_payload, _ = await call_mcp_tool_json(
                session,
                "parse_validate_ir",
                {"ir": ir_payload},
            )
            assert val_err is False
            assert val_payload == []

            exp_err, exp_payload, _ = await call_mcp_tool_json(
                session,
                "parse_export_ir",
                {"target": "markdown", "ir": ir_payload, "redact": True},
            )
            assert exp_err is False
            assert isinstance(exp_payload, str)
            assert MASKED_PII_TOKEN in exp_payload
            assert "123-45-6789" not in exp_payload

    asyncio.run(_run())


def test_f09_t1_mcp_oversized_render_triggers_spillover_ref_in_spill_dir(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Exceeding `--max-inline-bytes` on `parse_render_page` writes SpilloverRef to `--spill-dir`."""
    pdf_path = make_minimal_pdf(e2e_workspace.workspace / "mcp_spill.pdf")
    spill_dir = e2e_workspace.root / "custom_spill"

    async def _run() -> None:
        async with open_mcp_session(
            "parsing",
            workspace=e2e_workspace,
            allow_roots=[e2e_workspace.workspace],
            max_inline_bytes=128,
            spill_dir=spill_dir,
        ) as (session, _tools):
            is_err, payload, _ = await call_mcp_tool_json(
                session,
                "parse_render_page",
                {"uri": str(pdf_path), "page_number": 1},
            )
            assert is_err is False
            assert payload["png_b64"] is None
            spill = payload["spillover"]
            assert spill is not None
            assert spill["spilled"] is True
            assert spill["format"] == "png"
            spill_path = Path(spill["spill_path"])
            assert spill_path.is_file()
            assert spill_path.resolve().is_relative_to(spill_dir.resolve())
            assert not spill_path.resolve().is_relative_to(e2e_workspace.workspace.resolve())
            raw_bytes = spill_path.read_bytes()
            assert spill["byte_size"] == len(raw_bytes)
            assert spill["sha256"] == hashlib.sha256(raw_bytes).hexdigest()

    asyncio.run(_run())


def test_f09_t2_mcp_parsing_profile_rejects_project_flag_including_blank(
    cli_runner: Callable[..., Any],
) -> None:
    """`mcp serve --profile parsing` rejects `--project` (both non-empty and blank '')."""
    for proj_val in ("p1", ""):
        res = cli_runner(["mcp", "serve", "--profile", "parsing", "--project", proj_val])
        assert res.returncode == int(CliExitCode.VALIDATION_ERROR)
        assert res.envelope is not None
        assert res.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


def test_f09_t2_mcp_project_profile_rejects_parsing_flags(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
) -> None:
    """`mcp serve --profile project` rejects --allow-root, --allow-unredacted, --max-inline-bytes, --spill-dir."""
    flag_sets = [
        ["--allow-root", str(e2e_workspace.workspace)],
        ["--allow-unredacted"],
        ["--max-inline-bytes", "4096"],
        ["--spill-dir", str(e2e_workspace.root / "spill")],
    ]
    for extra_flags in flag_sets:
        res = cli_runner(
            ["mcp", "serve", "--profile", "project", "--project", "p1", *extra_flags]
        )
        assert res.returncode == int(CliExitCode.VALIDATION_ERROR)
        assert res.envelope is not None
        assert res.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


def test_f09_t2_mcp_all_profile_requires_non_empty_project(
    cli_runner: Callable[..., Any],
) -> None:
    """`mcp serve --profile all` rejects omitted or whitespace-only `--project`."""
    for extra in ([], ["--project", "   "]):
        res = cli_runner(["mcp", "serve", "--profile", "all", *extra])
        assert res.returncode == int(CliExitCode.VALIDATION_ERROR)
        assert res.envelope is not None
        assert res.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


def test_f09_t2_mcp_allow_root_containment_blocks_escape_without_crashing_session(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Containment error in MCP returns ProblemDetails is_error=True while keeping session alive."""
    outside_pdf = make_minimal_pdf(e2e_workspace.root / "outside_mcp" / "secret.pdf")

    async def _run() -> None:
        async with open_mcp_session(
            "parsing",
            workspace=e2e_workspace,
            allow_roots=[e2e_workspace.workspace],
        ) as (session, _tools):
            is_err, _payload, problem = await call_mcp_tool_json(
                session,
                "parse_inspect",
                {"uri": str(outside_pdf)},
            )
            assert is_err is True
            assert problem is not None
            assert problem.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

            ok_err, spec_payload, _ = await call_mcp_tool_json(session, "parse_spec", {})
            assert ok_err is False and "formats" in spec_payload

    asyncio.run(_run())


def test_f09_t2_mcp_scanned_page_approve_scan_blocked_without_allow_unredacted(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Under MCP, approve_scan=True still fails closed unless --allow-unredacted is opted in."""
    scan_pdf = make_scanned_image_pdf(e2e_workspace.workspace / "mcp_scan.pdf")

    async def _run() -> None:
        async with open_mcp_session(
            "parsing",
            workspace=e2e_workspace,
            allow_roots=[e2e_workspace.workspace],
            allow_unredacted=False,
        ) as (session, _tools):
            is_err, _payload, problem = await call_mcp_tool_json(
                session,
                "parse_render_page",
                {"uri": str(scan_pdf), "page_number": 1, "approve_scan": True},
            )
            assert is_err is True
            assert problem is not None
            assert problem.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

        async with open_mcp_session(
            "parsing",
            workspace=e2e_workspace,
            allow_roots=[e2e_workspace.workspace],
            allow_unredacted=True,
        ) as (session_unred, _tools):
            is_err, payload, _ = await call_mcp_tool_json(
                session_unred,
                "parse_render_page",
                {"uri": str(scan_pdf), "page_number": 1, "approve_scan": True},
            )
            assert is_err is False
            assert payload["redacted"] is False

    asyncio.run(_run())


def test_f09_t2_mcp_oversized_export_jsonl_spills_atomically(
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """`parse_export_ir(..., format='jsonl')` exceeding max_inline_bytes spills DuckDB NDJSON."""
    md_path = public_fixture("fivetran_dbt_stripe_readme.md")
    spill_dir = e2e_workspace.root / "jsonl_spill"

    async def _run() -> None:
        async with open_mcp_session(
            "parsing",
            workspace=e2e_workspace,
            allow_roots=[e2e_workspace.workspace, REPO_ROOT],
            max_inline_bytes=256,
            spill_dir=spill_dir,
        ) as (session, _tools):
            ext_err, ir_payload, _ = await call_mcp_tool_json(
                session,
                "parse_extract",
                {"uri": str(md_path)},
            )
            # parse_extract itself may spill when >256 bytes; handle inline or spilled IR path.
            if ext_err is False and isinstance(ir_payload, dict) and ir_payload.get("spilled"):
                exp_args: dict[str, Any] = {
                    "target": "chunks",
                    "ir_path": ir_payload["spill_path"],
                    "format": "jsonl",
                }
            else:
                assert ext_err is False
                exp_args = {
                    "target": "chunks",
                    "ir": ir_payload,
                    "format": "jsonl",
                }

            exp_err, exp_payload, _ = await call_mcp_tool_json(
                session,
                "parse_export_ir",
                exp_args,
            )
            assert exp_err is False
            assert exp_payload["spilled"] is True
            assert exp_payload["format"] == "jsonl"
            spill_file = Path(exp_payload["spill_path"])
            assert spill_file.is_file()
            assert spill_file.resolve().is_relative_to(spill_dir.resolve())
            rows = query_ndjson_with_duckdb(spill_file)
            assert len(rows) >= 1

    asyncio.run(_run())


def test_f09_t2_mcp_default_spill_dir_uses_user_state_outside_allow_root(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Omitting `--spill-dir` defaults to `<user_state>/parse_spill` outside `--allow-root`."""
    pdf_path = make_minimal_pdf(e2e_workspace.workspace / "mcp_default_spill.pdf")

    async def _run() -> None:
        async with open_mcp_session(
            "parsing",
            workspace=e2e_workspace,
            allow_roots=[e2e_workspace.workspace],
            max_inline_bytes=64,
        ) as (session, _tools):
            is_err, payload, _ = await call_mcp_tool_json(
                session,
                "parse_render_page",
                {"uri": str(pdf_path), "page_number": 1},
            )
            assert is_err is False
            spill = payload["spillover"]
            assert spill is not None and spill["spilled"] is True
            spill_path = Path(spill["spill_path"]).resolve()
            expected_parent = (e2e_workspace.state_dir / "parse_spill").resolve()
            assert spill_path.is_relative_to(expected_parent)
            assert not spill_path.is_relative_to(e2e_workspace.workspace.resolve())

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# F10: Cross-Component Grounding (`DiscoveryService` & `ontology ground`)
# ---------------------------------------------------------------------------


def test_f10_t1_project_ground_ontology_injects_sanitized_doc_notes(
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """`DiscoveryService.ground_ontology` attaches DOC_SOURCE_KINDS notes into reranker prompts."""
    from opendv.agents.ports import AgentTurnRequest, AgentTurnResult
    from opendv.models.grounding import GroundingPolicy, OntologyGroundReq
    from opendv.models.grounding_spi import TableRerankAssignment, TableRerankResult
    from opendv.models.project import HarnessProvider
    from opendv.models.wire_dtos_discovery import SourceAttachReq
    from opendv.service import OpenDVService

    captured_steward_notes: list[list[str]] = []

    class _RecordingRerankHarness:
        provider_name: HarnessProvider = "claude"

        async def run_turn(
            self,
            request: AgentTurnRequest,
            *,
            event_sink: Any = None,
            scout_dispatcher: Any = None,
        ) -> AgentTurnResult:
            start = request.prompt.find("{")
            end = request.prompt.rfind("}")
            body = json.loads(request.prompt[start : end + 1])
            captured_steward_notes.append(list(body.get("steward_notes", [])))
            assignments = [
                TableRerankAssignment(
                    column_name=col["column_name"],
                    abstain=True,
                    selected_index=None,
                    confidence=0.5,
                    rationale="Recorded steward notes.",
                    suggested_search_terms=[],
                )
                for col in body["columns"]
            ]
            out = TableRerankResult(assignments=assignments)
            return AgentTurnResult(
                agent_id=request.agent_id,
                provider=self.provider_name,
                model_id="claude-sonnet-4-6",
                text_output=out.model_dump_json(),
                structured_output=out,
                turns_used=1,
            )

    csv_path = e2e_workspace.workspace / "ambiguous_holdings.csv"
    csv_path.write_text("holding_ref_x,alloc_amt_z\nH1,100\nH2,200\n", encoding="utf-8")

    _init_project_with_baseline(e2e_workspace, "p_gnd_docs")
    svc_with_harness = OpenDVService(
        e2e_workspace.workspace,
        state_dir=e2e_workspace.state_dir,
        harness=_RecordingRerankHarness(),  # type: ignore[arg-type]
    )
    svc_with_harness.attach_source(
        "p_gnd_docs", SourceAttachReq(kind="csv", uri_or_filename=str(csv_path))
    )
    pdf_src = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"
    docx_src = public_fixture("aspose_merged_table.docx")
    svc_with_harness.attach_source(
        "p_gnd_docs", SourceAttachReq(kind="pdf", uri_or_filename=str(pdf_src))
    )
    svc_with_harness.attach_source(
        "p_gnd_docs", SourceAttachReq(kind="docx", uri_or_filename=str(docx_src))
    )

    resp = asyncio.run(
        svc_with_harness.discovery.ground_ontology(
            OntologyGroundReq(
                project_id="p_gnd_docs",
                policy=GroundingPolicy(mode="agentic"),
            )
        )
    )
    assert len(resp.tables) == 1
    assert captured_steward_notes, "Expected agentic reranker to receive steward notes"
    notes = captured_steward_notes[0]
    assert 1 <= len(notes) <= 5
    assert all(len(n) <= 1500 for n in notes)
    assert all("<MASKED_PII>" not in n and "[REDACTED_" not in n for n in notes)


def test_f10_t1_build_grounding_summary_notes_strips_pii_without_placeholders(
    e2e_workspace: E2EWorkspace,
) -> None:
    """`ParsingService.build_grounding_summary_notes` strips PII without emitting <MASKED_PII>."""
    from opendv.models.pii_tokenizer import ensure_no_redacted_placeholder
    from opendv.service.parsing_svc import ParsingService

    pii_pdf = make_pii_multi_token_pdf(e2e_workspace.workspace / "gnd_pii.pdf")
    txt_file = make_boundary_pii_text_file(e2e_workspace.workspace / "gnd_notes.txt")
    svc = ParsingService(allowed_roots=[e2e_workspace.workspace])

    notes = svc.build_grounding_summary_notes([pii_pdf, txt_file], max_notes=5, max_chars_per_note=1500)
    assert 1 <= len(notes) <= 5
    for note in notes:
        assert len(note) <= 1500
        ensure_no_redacted_placeholder(note)
        assert "123-45-6789" not in note
        assert "4111 1111 1111 1111" not in note
        assert "(415) 555-0199" not in note
        assert "021000021" not in note


def test_f10_t1_cli_ontology_ground_input_with_repeatable_doc_succeeds(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """`opendv --json ontology ground --input --doc ...` succeeds across .pdf and .docx."""
    from opendv.models.grounding import (
        ColumnEvidence,
        GroundingPolicy,
        GroundingRequest,
        TableEvidence,
    )

    req = GroundingRequest(
        tables=[
            TableEvidence(
                table_id="tbl_holdings",
                table_name="holdings",
                columns=[
                    ColumnEvidence(column_name="cusip", data_type="VARCHAR", is_pk=True),
                    ColumnEvidence(column_name="maturity_date", data_type="DATE"),
                ],
            )
        ],
        policy=GroundingPolicy(mode="deterministic_only", skip_reason="e2e_doc_ground"),
    )
    req_path = e2e_workspace.workspace / "ground_req.json"
    req_path.write_text(req.model_dump_json(), encoding="utf-8")

    pdf_doc = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"
    docx_doc = public_fixture("aspose_merged_table.docx")
    res = cli_runner(
        [
            "ontology",
            "ground",
            "--input",
            str(req_path),
            "--doc",
            str(pdf_doc),
            "--doc",
            str(docx_doc),
        ]
    )
    assert res.returncode == 0
    assert res.envelope is not None and res.envelope.ok is True
    data = res.envelope.data
    assert isinstance(data, dict)
    assert len(data["tables"]) == 1
    assert [c["column_name"] for c in data["tables"][0]["columns"]] == ["cusip", "maturity_date"]
    e2e_workspace.assert_no_workspace_opendv_dir()


def test_f10_t1_ground_ontology_offline_core_bundle_falls_back_to_document_map(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Offline `am_core_bundle.core.opendv.zip` falls back to persisted DocumentMap cleanly."""
    from opendv.models.grounding import GroundingPolicy, OntologyGroundReq
    from opendv.service import OpenDVService

    bundle_zip = REPO_ROOT / "tests" / "golden_v2" / "am_core_bundle.core.opendv.zip"
    svc = OpenDVService(e2e_workspace.workspace, state_dir=e2e_workspace.state_dir)
    svc.import_bundle(
        bundle_zip,
        conflict="fail",
        allowed_roots=[REPO_ROOT, e2e_workspace.workspace],
    )

    resp = asyncio.run(
        svc.discovery.ground_ontology(
            OntologyGroundReq(
                project_id="am-golden-w1",
                policy=GroundingPolicy(mode="deterministic_only", skip_reason="offline_bundle"),
            )
        )
    )
    assert len(resp.tables) >= 1


def test_f10_t1_cli_ontology_ground_input_with_md_and_txt_docs(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """`opendv --json ontology ground --input --doc` works with .md and .txt files."""
    from opendv.models.grounding import (
        ColumnEvidence,
        GroundingPolicy,
        GroundingRequest,
        TableEvidence,
    )

    req = GroundingRequest(
        tables=[
            TableEvidence(
                table_id="tbl_sec",
                table_name="securities",
                columns=[ColumnEvidence(column_name="cusip", data_type="VARCHAR", is_pk=True)],
            )
        ],
        policy=GroundingPolicy(mode="deterministic_only", skip_reason="e2e_md_txt"),
    )
    req_path = e2e_workspace.workspace / "req_md_txt.json"
    req_path.write_text(req.model_dump_json(), encoding="utf-8")

    md_doc = REPO_ROOT / "data" / "public_am" / "am_stewardship_domain_notes.md"
    txt_doc = public_fixture("rfc4180_csv.txt")
    res = cli_runner(
        [
            "ontology",
            "ground",
            "--input",
            str(req_path),
            "--doc",
            str(md_doc),
            "--doc",
            str(txt_doc),
        ]
    )
    assert res.returncode == 0
    assert res.envelope is not None and res.envelope.ok is True


def test_f10_t2_cli_ontology_ground_rejects_project_plus_doc(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
) -> None:
    """`ontology ground --project <id> --doc <path>` is rejected with SYNTAX_INVALID_ARGUMENT."""
    pdf = make_minimal_pdf(e2e_workspace.workspace / "proj_doc.pdf")
    res = cli_runner(["ontology", "ground", "--project", "p1", "--doc", str(pdf)])
    assert res.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert res.envelope is not None
    assert res.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


def test_f10_t2_cli_ontology_ground_rejects_standalone_doc_without_input(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
) -> None:
    """Standalone `ontology ground --doc <path>` without `--input` is rejected with exit 2."""
    pdf = make_minimal_pdf(e2e_workspace.workspace / "solo_doc.pdf")
    res = cli_runner(["ontology", "ground", "--doc", str(pdf)])
    assert res.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert res.envelope is not None
    assert res.envelope.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


def test_f10_t2_ground_ontology_respects_20_note_cap_with_preexisting_notes(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
) -> None:
    """Appending --doc notes respects GroundingRequest.global_steward_notes max_length=20 headroom."""
    from opendv.models.grounding import (
        ColumnEvidence,
        GroundingPolicy,
        GroundingRequest,
        TableEvidence,
    )

    req = GroundingRequest(
        tables=[
            TableEvidence(
                table_id="tbl_cap",
                table_name="holdings",
                columns=[ColumnEvidence(column_name="cusip", data_type="VARCHAR", is_pk=True)],
            )
        ],
        global_steward_notes=[f"Existing steward note {idx}" for idx in range(18)],
        policy=GroundingPolicy(mode="deterministic_only", skip_reason="cap_test"),
    )
    req_path = e2e_workspace.workspace / "req_18.json"
    req_path.write_text(req.model_dump_json(), encoding="utf-8")

    docs = [
        make_minimal_pdf(e2e_workspace.workspace / f"cap_{i}.pdf", text=f"Doc note {i} context.")
        for i in range(4)
    ]
    args = ["ontology", "ground", "--input", str(req_path)]
    for d in docs:
        args.extend(["--doc", str(d)])

    res = cli_runner(args)
    assert res.returncode == 0
    assert res.envelope is not None and res.envelope.ok is True


def test_f10_t2_ground_ontology_doc_with_raw_pii_and_redacted_literal_never_leaks_to_reranker(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Attached PII + literal `[REDACTED_TEXT]` docs never leak PII or placeholders to reranker."""
    from opendv.agents.ports import AgentTurnRequest, AgentTurnResult
    from opendv.models.grounding import GroundingPolicy, OntologyGroundReq
    from opendv.models.grounding_spi import TableRerankAssignment, TableRerankResult
    from opendv.models.pii_tokenizer import REDACTED_LEAK_RE
    from opendv.models.project import HarnessProvider
    from opendv.models.wire_dtos_discovery import SourceAttachReq
    from opendv.service import OpenDVService

    observed_prompts: list[str] = []

    class _SpyHarness:
        provider_name: HarnessProvider = "claude"

        async def run_turn(
            self,
            request: AgentTurnRequest,
            *,
            event_sink: Any = None,
            scout_dispatcher: Any = None,
        ) -> AgentTurnResult:
            observed_prompts.append(request.prompt)
            start = request.prompt.find("{")
            end = request.prompt.rfind("}")
            body = json.loads(request.prompt[start : end + 1])
            out = TableRerankResult(
                assignments=[
                    TableRerankAssignment(
                        column_name=c["column_name"],
                        abstain=True,
                        selected_index=None,
                        confidence=0.5,
                        rationale="Clean prompt verified.",
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

    csv_path = e2e_workspace.workspace / "rerank_check.csv"
    csv_path.write_text("ambig_code_k,metric_val_m\nA1,10\nA2,20\n", encoding="utf-8")
    pii_pdf = make_pii_multi_token_pdf(e2e_workspace.workspace / "leak_check.pdf")
    lit_note = e2e_workspace.workspace / "lit_redacted.txt"
    lit_note.write_text("Court filing [REDACTED_TEXT] corporate governance memo.", encoding="utf-8")

    _init_project_with_baseline(e2e_workspace, "p_noleak")
    svc = OpenDVService(
        e2e_workspace.workspace,
        state_dir=e2e_workspace.state_dir,
        harness=_SpyHarness(),  # type: ignore[arg-type]
    )
    svc.attach_source("p_noleak", SourceAttachReq(kind="csv", uri_or_filename=str(csv_path)))
    svc.attach_source("p_noleak", SourceAttachReq(kind="pdf", uri_or_filename=str(pii_pdf)))
    svc.attach_source(
        "p_noleak", SourceAttachReq(kind="domain_note", uri_or_filename=str(lit_note))
    )

    resp = asyncio.run(
        svc.discovery.ground_ontology(
            OntologyGroundReq(project_id="p_noleak", policy=GroundingPolicy(mode="agentic"))
        )
    )
    assert len(resp.tables) == 1
    assert observed_prompts
    for prompt in observed_prompts:
        assert REDACTED_LEAK_RE.search(prompt) is None
        for secret in ("123-45-6789", "4111 1111 1111 1111", "(415) 555-0199", "021000021"):
            assert secret not in prompt


def test_f10_t2_cli_ontology_ground_doc_containment_escape_rejected(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
) -> None:
    """`ontology ground --input --doc <outside>` fails with ARCHIVE_SECURITY_VIOLATION."""
    from opendv.models.grounding import (
        ColumnEvidence,
        GroundingPolicy,
        GroundingRequest,
        TableEvidence,
    )

    req = GroundingRequest(
        tables=[
            TableEvidence(
                table_id="tbl_esc",
                table_name="holdings",
                columns=[ColumnEvidence(column_name="cusip", data_type="VARCHAR", is_pk=True)],
            )
        ],
        policy=GroundingPolicy(mode="deterministic_only", skip_reason="escape_test"),
    )
    req_path = e2e_workspace.workspace / "req_esc.json"
    req_path.write_text(req.model_dump_json(), encoding="utf-8")
    outside_pdf = make_minimal_pdf(e2e_workspace.root / "outside_gnd" / "secret.pdf")

    res = cli_runner(
        ["ontology", "ground", "--input", str(req_path), "--doc", str(outside_pdf)],
        env=e2e_workspace.subprocess_env(workspace_roots=[e2e_workspace.workspace]),
    )
    assert res.returncode == int(CliExitCode.VALIDATION_ERROR)
    assert res.envelope is not None
    assert res.envelope.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION


def test_f10_t2_cli_ontology_ground_missing_doc_returns_404(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
) -> None:
    """`ontology ground --input --doc <missing>` exits 3 with RESOURCE_NOT_FOUND."""
    from opendv.models.grounding import (
        ColumnEvidence,
        GroundingPolicy,
        GroundingRequest,
        TableEvidence,
    )

    req = GroundingRequest(
        tables=[
            TableEvidence(
                table_id="tbl_404",
                table_name="holdings",
                columns=[ColumnEvidence(column_name="cusip", data_type="VARCHAR", is_pk=True)],
            )
        ],
        policy=GroundingPolicy(mode="deterministic_only", skip_reason="missing_doc"),
    )
    req_path = e2e_workspace.workspace / "req_404.json"
    req_path.write_text(req.model_dump_json(), encoding="utf-8")

    res = cli_runner(
        [
            "ontology",
            "ground",
            "--input",
            str(req_path),
            "--doc",
            str(e2e_workspace.workspace / "no_such.pdf"),
        ]
    )
    assert res.returncode == int(CliExitCode.NOT_FOUND)
    assert res.envelope is not None
    assert res.envelope.error_code == ErrorCode.RESOURCE_NOT_FOUND


# ---------------------------------------------------------------------------
# F11: Cross-Component Modeling & Gate V4
# ---------------------------------------------------------------------------


def test_f11_t1_profile_sources_stateless_unblocks_all_4_doc_kinds(
    e2e_workspace: E2EWorkspace,
    public_fixture: Callable[[str], Path],
) -> None:
    """`profile_sources_stateless` routes pdf/docx/markdown/domain_note into document_maps."""
    from opendv.service.workflows.modeling_stateless import profile_sources_stateless

    pdf_p = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"
    docx_p = public_fixture("aspose_merged_table.docx")
    md_p = REPO_ROOT / "data" / "public_am" / "am_stewardship_domain_notes.md"
    txt_p = public_fixture("rfc4180_csv.txt")

    bundle = profile_sources_stateless(
        sources=[
            f"pdf:{pdf_p}",
            f"docx:{docx_p}",
            f"markdown:{md_p}",
            f"domain_note:{txt_p}",
        ],
        allowed_roots=[e2e_workspace.workspace, REPO_ROOT],
    )
    assert bundle.profiles == []
    assert len(bundle.document_maps) == 4
    assert {dm.format for dm in bundle.document_maps} == {"pdf", "docx", "markdown", "domain_note"}

    pdf_dm = next(dm for dm in bundle.document_maps if dm.format == "pdf")
    assert pdf_dm.page_count == 6
    assert pdf_dm.pages[3].is_scanned_or_low_density is True


def test_f11_t1_cli_model_profile_mixed_sqlite_and_docs_plain_and_json(
    cli_runner: Callable[..., Any],
    e2e_workspace: E2EWorkspace,
) -> None:
    """`opendv model profile` prints document_maps in plain mode and emits ProfileBundle JSON."""
    from opendv.models.wire_dtos_vault import ProfileBundle

    sqlite_p = REPO_ROOT / "data" / "public_am" / "am_sec_treasury_ibor.sqlite"
    pdf_p = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"
    bundle_out = e2e_workspace.workspace / "bundle.json"

    res_json = cli_runner(
        [
            "model",
            "profile",
            "--source",
            f"sqlite:{sqlite_p}",
            "--source",
            f"pdf:{pdf_p}",
            "--out",
            str(bundle_out),
        ]
    )
    assert res_json.returncode == 0
    assert res_json.envelope is not None and isinstance(res_json.envelope.data, dict)
    assert len(res_json.envelope.data["profiles"]) == 1
    assert len(res_json.envelope.data["document_maps"]) == 1

    loaded = ProfileBundle.model_validate_json(bundle_out.read_text(encoding="utf-8"))
    assert len(loaded.document_maps) == 1

    res_plain = cli_runner(
        ["model", "profile", "--source", f"sqlite:{sqlite_p}", "--source", f"pdf:{pdf_p}"],
        expect_json=False,
    )
    assert res_plain.returncode == 0
    plain_lower = res_plain.stdout.lower()
    assert re.search(r"document[_ ]maps?", plain_lower)
    assert re.search(
        r'(?:table[s_a-z]*"?\s*[:=]\s*6\b|\b6\s+(?:\w+\s+)?tables?\b)',
        plain_lower,
    )
    e2e_workspace.assert_no_workspace_opendv_dir()


def test_f11_t1_verify_draft_stateless_runs_gate_v4_via_profile_bundle_document_maps(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Mode-1 `verify_draft_stateless` passes Gate V4 using bundle.document_maps stored digest."""
    from opendv.service.parsing_svc import ParsingService
    from opendv.service.workflows.modeling_stateless import (
        profile_sources_stateless,
        verify_draft_stateless,
    )

    pdf_p = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"
    bundle = profile_sources_stateless(
        sources=[f"pdf:{pdf_p}"],
        allowed_roots=[e2e_workspace.workspace, REPO_ROOT],
    )
    dm = bundle.document_maps[0]
    raw_ir = ParsingService(allowed_roots=[REPO_ROOT]).parse_document(
        pdf_p, sink_class="LOCAL_HUMAN_IO"
    )
    quote = "CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE"
    p5_text = raw_ir.pages[4].canonical_text
    first_idx = p5_text.find(quote)
    second_idx = p5_text.find(quote, first_idx + 1)
    assert second_idx >= 0

    draft = _build_v4_citation_draft(
        doc_id=dm.doc_id,
        page_number=5,
        verbatim_quote=quote,
        occurrence=2,
        char_start=second_idx,
        char_end=second_idx + len(quote),
        content_sha256=dm.pages[4].canonical_text_sha256,
    )
    result = verify_draft_stateless(
        {"profiles": [], "policy": "symbolic_only", "real_row_proving": False},
        draft_data=draft,
        profile_bundle=bundle,
    )
    assert result.status == "CERTIFIED_FAST", result.verifier_report.v1_v6_diagnostics


def test_f11_t1_merge_profile_bundle_and_run_model_stateless_preserve_caller_doc_maps(
    e2e_workspace: E2EWorkspace,
) -> None:
    """`merge_profile_bundle` and `run_model_stateless` dict branch preserve caller DocumentMaps."""
    from opendv.models.events import ExternalMcpReceipt, OcrPageReceipt
    from opendv.models.ontology import GraphAOntology
    from opendv.models.sources import DocumentMap, PageMapSpec
    from opendv.models.wire_dtos_vault import ProfileBundle
    from opendv.service.workflows._modeling_stateless_helpers import merge_profile_bundle
    from opendv.service.workflows.modeling_stateless import run_model_stateless
    from tests.helpers import DeterministicMockHarness, MockTurnScript

    caller_dm = DocumentMap(
        doc_id="doc_shared",
        filename="caller_override.pdf",
        format="pdf",
        page_count=1,
        pages=[
            PageMapSpec(
                page_number=1,
                char_count=12,
                has_images=False,
                is_scanned_or_low_density=False,
                ocr_box_count=0,
                canonical_text_sha256="a" * 64,
            )
        ],
    )
    colliding_dm = caller_dm.model_copy(update={"filename": "bundle_should_not_win.pdf"})
    new_dm = caller_dm.model_copy(update={"doc_id": "doc_new", "filename": "new.pdf"})
    ocr_rec = OcrPageReceipt(
        receipt_id="ocr-1",
        doc_id="doc_shared",
        page_number=1,
        ocr_text_masked="ok",
        content_sha256="b" * 64,
        ocr_box_count=1,
    )
    mcp_rec = ExternalMcpReceipt(
        receipt_id="mcp-1",
        server_id="sec-edgar",
        tool_name="fetch",
        doc_id="doc_shared",
        page_number=1,
        args_sha256="c" * 64,
        content_sha256="d" * 64,
        masked_excerpt="excerpt",
    )

    bundle = ProfileBundle(document_maps=[colliding_dm, new_dm])
    raw_req = {
        "verifier_ctx": {
            "document_maps": [caller_dm.model_dump(mode="json")],
            "ocr_receipts": [ocr_rec.model_dump(mode="json")],
            "mcp_receipts": [mcp_rec.model_dump(mode="json")],
            "parsed_doc_pages": {"doc_shared": {1: "raw page text"}},
        }
    }
    merged = merge_profile_bundle(raw_req, bundle)
    vctx = merged["verifier_ctx"]
    assert [d["doc_id"] for d in vctx["document_maps"]] == ["doc_shared", "doc_new"]
    assert vctx["document_maps"][0]["filename"] == "caller_override.pdf"
    assert len(vctx["ocr_receipts"]) == 1
    assert len(vctx["mcp_receipts"]) == 1
    assert vctx["parsed_doc_pages"] == {"doc_shared": {1: "raw page text"}}

    # Also verify run_model_stateless dict branch propagates merged document_maps to Gate V4.
    draft = _build_v4_citation_draft(
        doc_id="doc_new",
        page_number=1,
        verbatim_quote="Hello world.",
        occurrence=1,
        char_start=0,
        char_end=12,
        content_sha256="a" * 64,
    )
    dict_req: dict[str, Any] = {
        "ontology": GraphAOntology(
            ontology_version="1.0.0", domain_namespace="ibor", concepts=[]
        ).model_dump(mode="json"),
        "profiles": [],
        "policy": "symbolic_only",
        "real_row_proving": False,
        "verifier_ctx": vctx,
    }
    harness = DeterministicMockHarness(
        scripts_by_agent={"ship-vault": [MockTurnScript(structured_output=draft)]}
    )
    res = asyncio.run(
        run_model_stateless(
            dict_req,
            profile_bundle=bundle,
            workspace_root=e2e_workspace.workspace,
            state_dir=e2e_workspace.state_dir,
            injected_harness=harness,
        )
    )
    assert res.verifier_report is not None
    assert [d for d in res.verifier_report.v1_v6_diagnostics if d.gate == "V4"] == []


def test_f11_t1_mode2_load_verifier_inputs_uses_1based_page_keys_and_passes_v4(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Mode-2 `_vault_helpers.load_verifier_inputs` keys parsed_doc_pages by 1-based page_number."""
    from opendv.models.wire_dtos_discovery import SourceAttachReq
    from opendv.service.workflows._vault_helpers import load_verifier_inputs
    from opendv.verifier._v4_unstructured import verify_v4_unstructured
    from opendv.verifier.l1_codemods import run_l1_codemods

    pdf_p = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"
    sqlite_p = REPO_ROOT / "data" / "public_am" / "am_sec_treasury_ibor.sqlite"
    svc = _init_project_with_baseline(e2e_workspace, "p_m2_v4")
    svc.attach_source("p_m2_v4", SourceAttachReq(kind="sqlite", uri_or_filename=str(sqlite_p)))
    pdf_src = svc.attach_source("p_m2_v4", SourceAttachReq(kind="pdf", uri_or_filename=str(pdf_p)))

    _, _, _, _, _, _, ctx = load_verifier_inputs(
        svc.state_manager,
        e2e_workspace.workspace,
        "p_m2_v4",
    )
    assert ctx.metadata_only_binary_absent is False
    assert set(ctx.parsed_doc_pages[pdf_src.source_id].keys()) == {1, 2, 3, 4, 5, 6}

    dm = next(d for d in ctx.document_maps if d.doc_id == pdf_src.source_id)
    quote = "CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE"
    p5 = ctx.parsed_doc_pages[pdf_src.source_id][5]
    idx1 = p5.find(quote)
    idx2 = p5.find(quote, idx1 + 1)
    assert idx2 >= 0

    draft = _build_v4_citation_draft(
        doc_id=dm.doc_id,
        page_number=5,
        verbatim_quote=quote,
        occurrence=2,
        char_start=idx2,
        char_end=idx2 + len(quote),
        content_sha256=dm.pages[4].canonical_text_sha256,
    )
    _, cert, _ = run_l1_codemods(draft)
    assert verify_v4_unstructured(cert, ctx) == []


def test_f11_t2_verify_draft_stateless_detects_v4_digest_and_bounds_mismatch(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Mode-1 Gate V4 fails closed on wrong content_sha256 or out-of-bounds char_end."""
    from opendv.service.workflows.modeling_stateless import (
        profile_sources_stateless,
        verify_draft_stateless,
    )

    pdf_p = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"
    bundle = profile_sources_stateless(
        sources=[f"pdf:{pdf_p}"],
        allowed_roots=[e2e_workspace.workspace, REPO_ROOT],
    )
    dm = bundle.document_maps[0]

    bad_sha_draft = _build_v4_citation_draft(
        doc_id=dm.doc_id,
        page_number=5,
        verbatim_quote="CONFIDENTIAL",
        occurrence=1,
        char_start=0,
        char_end=12,
        content_sha256="0" * 64,
    )
    res_sha = verify_draft_stateless(
        {"profiles": [], "policy": "symbolic_only", "real_row_proving": False},
        draft_data=bad_sha_draft,
        profile_bundle=bundle,
    )
    assert res_sha.status == "UNCERTIFIED_DRAFT"
    assert any(
        d.gate == "V4" and d.rule_id == "CITATION_SPAN_UNRESOLVED"
        for d in res_sha.verifier_report.v1_v6_diagnostics
    )

    bad_bounds_draft = _build_v4_citation_draft(
        doc_id=dm.doc_id,
        page_number=5,
        verbatim_quote="CONFIDENTIAL",
        occurrence=1,
        char_start=dm.pages[4].char_count,
        char_end=dm.pages[4].char_count + 12,
        content_sha256=dm.pages[4].canonical_text_sha256,
    )
    res_bounds = verify_draft_stateless(
        {"profiles": [], "policy": "symbolic_only", "real_row_proving": False},
        draft_data=bad_bounds_draft,
        profile_bundle=bundle,
    )
    assert res_bounds.status == "UNCERTIFIED_DRAFT"
    assert any(
        d.gate == "V4" and d.rule_id == "SPAN_HALLUCINATION"
        for d in res_bounds.verifier_report.v1_v6_diagnostics
    )


def test_f11_t2_verify_draft_stateless_blocks_redacted_placeholder_leak_in_citation(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Gate V4 rejects DocumentCitationRef verbatim_quote containing <MASKED_PII>."""
    from opendv.service.workflows.modeling_stateless import (
        profile_sources_stateless,
        verify_draft_stateless,
    )

    pdf_p = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"
    bundle = profile_sources_stateless(
        sources=[f"pdf:{pdf_p}"],
        allowed_roots=[e2e_workspace.workspace, REPO_ROOT],
    )
    dm = bundle.document_maps[0]

    leak_draft = _build_v4_citation_draft(
        doc_id=dm.doc_id,
        page_number=5,
        verbatim_quote="<MASKED_PII>",
        occurrence=1,
        char_start=0,
        char_end=12,
        content_sha256=dm.pages[4].canonical_text_sha256,
    )
    res = verify_draft_stateless(
        {"profiles": [], "policy": "symbolic_only", "real_row_proving": False},
        draft_data=leak_draft,
        profile_bundle=bundle,
    )
    assert res.status == "UNCERTIFIED_DRAFT"
    assert any(
        d.gate == "V4" and d.rule_id == "REDACTED_PLACEHOLDER_LEAK"
        for d in res.verifier_report.v1_v6_diagnostics
    )


def test_f11_t2_profile_sources_stateless_masks_pii_in_document_maps_and_honors_allowed_roots(
    e2e_workspace: E2EWorkspace,
) -> None:
    """`profile_sources_stateless` masks PII in DocumentMap metadata and enforces allowed_roots."""
    from opendv.models.errors import OpenDVError
    from opendv.service.workflows.modeling_stateless import profile_sources_stateless

    pii_pdf = make_pii_multi_token_pdf(e2e_workspace.workspace / "prof_pii.pdf")
    bundle = profile_sources_stateless(
        sources=[f"pdf:{pii_pdf}"],
        allowed_roots=[e2e_workspace.workspace],
    )
    dm_json = bundle.document_maps[0].model_dump_json()
    for secret in ("(415) 555-0199", "4111 1111 1111 1111", "021000021", "123-45-6789"):
        assert secret not in dm_json

    outside_pdf = make_minimal_pdf(e2e_workspace.root / "outside_prof" / "sec.pdf")
    with pytest.raises(OpenDVError) as exc_info:
        profile_sources_stateless(
            sources=[f"pdf:{outside_pdf}"],
            allowed_roots=[e2e_workspace.workspace],
        )
    assert exc_info.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION


def test_f11_t2_mode2_load_verifier_inputs_offline_core_bundle_sets_meta_absent_and_verifies_v4(
    e2e_workspace: E2EWorkspace,
) -> None:
    """Offline `am_core_bundle.core.opendv.zip` sets metadata_only_binary_absent and passes V4."""
    from opendv.service import OpenDVService
    from opendv.service.workflows._vault_helpers import load_verifier_inputs
    from opendv.verifier._v4_unstructured import verify_v4_unstructured
    from opendv.verifier.l1_codemods import run_l1_codemods

    bundle_zip = REPO_ROOT / "tests" / "golden_v2" / "am_core_bundle.core.opendv.zip"
    svc = OpenDVService(e2e_workspace.workspace, state_dir=e2e_workspace.state_dir)
    svc.import_bundle(
        bundle_zip,
        conflict="fail",
        allowed_roots=[REPO_ROOT, e2e_workspace.workspace],
    )

    _, _, _, _, _, _, ctx = load_verifier_inputs(
        svc.state_manager,
        e2e_workspace.workspace,
        "am-golden-w1",
    )
    assert ctx.metadata_only_binary_absent is True
    assert ctx.parsed_doc_pages == {}
    doc_ids = {d.doc_id for d in ctx.document_maps}
    assert {"src-7c0663dfb9", "src-ded3719416"}.issubset(doc_ids)
    assert any(r.receipt_id == "ocr-p4-golden" for r in ctx.ocr_receipts)

    pdf_dm = next(d for d in ctx.document_maps if d.doc_id == "src-7c0663dfb9")
    p5_spec = pdf_dm.pages[4]
    quote = "CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE"
    draft = _build_v4_citation_draft(
        doc_id="src-7c0663dfb9",
        page_number=5,
        verbatim_quote=quote,
        occurrence=2,
        char_start=p5_spec.char_count - len(quote),
        char_end=p5_spec.char_count,
        content_sha256=p5_spec.canonical_text_sha256,
    )
    _, cert, _ = run_l1_codemods(draft)
    assert verify_v4_unstructured(cert, ctx) == []

    # Verify OCR receipt offline verification for page 4 also passes.
    ocr_rec = next(r for r in ctx.ocr_receipts if r.receipt_id == "ocr-p4-golden")
    ocr_quote = "Custody fee schedule table extracted cleanly."
    ocr_draft = _build_v4_citation_draft(
        doc_id="src-7c0663dfb9",
        page_number=4,
        verbatim_quote=ocr_quote,
        occurrence=1,
        char_start=0,
        char_end=len(ocr_quote),
        content_sha256=ocr_rec.content_sha256,
    )
    _, ocr_cert, _ = run_l1_codemods(ocr_draft)
    assert verify_v4_unstructured(ocr_cert, ctx) == []


def test_f11_t2_profile_bundle_omits_raw_pages_and_business_context_hints_unpolluted(
    e2e_workspace: E2EWorkspace,
) -> None:
    """ProfileBundle excludes raw page text and leaves business_context_hints untouched."""
    from opendv.modeling.models import DVModelingRequest
    from opendv.models.ontology import GraphAOntology
    from opendv.service.workflows.modeling_stateless import (
        profile_sources_stateless,
        run_model_stateless,
    )

    pdf_p = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"
    bundle = profile_sources_stateless(
        sources=[f"pdf:{pdf_p}"],
        allowed_roots=[e2e_workspace.workspace, REPO_ROOT],
    )
    dumped = bundle.model_dump()
    for forbidden in ("parsed_doc_pages", "page_text_views", "document_excerpts"):
        assert forbidden not in dumped

    req = DVModelingRequest(
        ontology=GraphAOntology(ontology_version="1.0.0", domain_namespace="ibor", concepts=[]),
        profiles=[],
        policy="symbolic_only",
        real_row_proving=False,
    )
    dry_res = asyncio.run(
        run_model_stateless(
            req,
            profile_bundle=bundle,
            dry_run=True,
            workspace_root=e2e_workspace.workspace,
            state_dir=e2e_workspace.state_dir,
        )
    )
    assert dry_res["dry_run"] is True
    assert req.business_context_hints is None


def test_f11_t2_run_model_stateless_dv_modeling_request_branch_propagates_document_maps_to_v4(
    e2e_workspace: E2EWorkspace,
) -> None:
    """`run_model_stateless` merges bundle.document_maps in the DVModelingRequest branch for V4."""
    from opendv.modeling.models import DVModelingRequest
    from opendv.models.ontology import GraphAOntology
    from opendv.service.parsing_svc import ParsingService
    from opendv.service.workflows.modeling_stateless import (
        profile_sources_stateless,
        run_model_stateless,
    )
    from tests.helpers import DeterministicMockHarness, MockTurnScript

    pdf_p = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"
    bundle = profile_sources_stateless(
        sources=[f"pdf:{pdf_p}"],
        allowed_roots=[e2e_workspace.workspace, REPO_ROOT],
    )
    dm = bundle.document_maps[0]
    raw_ir = ParsingService(allowed_roots=[REPO_ROOT]).parse_document(
        pdf_p, sink_class="LOCAL_HUMAN_IO"
    )
    quote = "CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE"
    p5_text = raw_ir.pages[4].canonical_text
    first_idx = p5_text.find(quote)
    second_idx = p5_text.find(quote, first_idx + 1)
    assert second_idx >= 0

    draft = _build_v4_citation_draft(
        doc_id=dm.doc_id,
        page_number=5,
        verbatim_quote=quote,
        occurrence=2,
        char_start=second_idx,
        char_end=second_idx + len(quote),
        content_sha256=dm.pages[4].canonical_text_sha256,
    )
    harness = DeterministicMockHarness(
        scripts_by_agent={"ship-vault": [MockTurnScript(structured_output=draft)]}
    )
    req = DVModelingRequest(
        ontology=GraphAOntology(ontology_version="1.0.0", domain_namespace="ibor", concepts=[]),
        profiles=[],
        policy="symbolic_only",
        real_row_proving=False,
    )
    res = asyncio.run(
        run_model_stateless(
            req,
            profile_bundle=bundle,
            workspace_root=e2e_workspace.workspace,
            state_dir=e2e_workspace.state_dir,
            injected_harness=harness,
        )
    )
    assert res.verifier_report is not None
    v4_diags = [d for d in res.verifier_report.v1_v6_diagnostics if d.gate == "V4"]
    assert v4_diags == []
