"""Tests for CUJ 1 CLI (`opendv parse`), CUJ 2 MCP (`--profile`), and CUJ 3 SPI injection."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import json
import os
import sys
import unittest.mock
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import duckdb
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError
from PIL import Image

from opendv.cli.main import main as cli_main
from opendv.cli.mcp_stdio import MCP_TOOL_NAMES, PARSING_MCP_TOOL_NAMES, create_mcp_stdio_server
from opendv.models import (
    CliEnvelope,
    CliExitCode,
    ErrorCode,
    OpenDVError,
    ProblemDetails,
    ProjectInitReq,
)
from opendv.models.document_ir import (
    ChunkRecord,
    DocumentBlock,
    DocumentOutlineNode,
    InspectResult,
    NormalizedBBox,
    PageSlice,
    PageSpan,
    ParsedDocumentIR,
    ParseOptions,
    ParseProvenance,
    RedactionMode,
    SourceBlob,
    TargetProjection,
    compute_canonical_text_sha256,
    normalize_page_text,
)
from opendv.models.pii_tokenizer import MASKED_PII_TOKEN, mask_pii_text
from opendv.parsing import (
    FormatParser,
    IRProjector,
    NormalizedWordBox,
    ParsingEngine,
    ParsingRegistry,
    RenderedPageBuffer,
)
from opendv.service import OpenDVService, ParsingService

REPO_ROOT = Path(__file__).resolve().parents[2]
CUSTODY_PDF = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"
ASPOSE_DOCX = REPO_ROOT / "tests" / "parsing" / "fixtures_public" / "aspose_merged_table.docx"
STRIPE_MD = REPO_ROOT / "tests" / "parsing" / "fixtures_public" / "fivetran_dbt_stripe_readme.md"


def _make_text_pdf(path: Path, text: str) -> Path:
    """Create a deterministic single-page PDF with embedded Helvetica text."""
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


def _setup_cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, dict[str, str]]:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    user_state = tmp_path / "user_state"
    state_dir = user_state / "opendv"
    roaming = user_state / "Roaming"
    state_dir.mkdir(parents=True, exist_ok=True)
    roaming.mkdir(parents=True, exist_ok=True)

    src_path = str(REPO_ROOT / "src")
    old_pp = os.environ.get("PYTHONPATH", "")
    py_path = f"{src_path}{os.pathsep}{old_pp}" if old_pp else src_path
    env_map = {
        "OPENDV_HOME": str(ws / ".opendv"),
        "OPENDV_WORKSPACE_ROOTS": f"{ws}{os.pathsep}{tmp_path}{os.pathsep}{REPO_ROOT}",
        "LOCALAPPDATA": str(user_state),
        "APPDATA": str(roaming),
        "USERPROFILE": str(user_state),
        "HOME": str(user_state),
        "XDG_STATE_HOME": str(user_state),
        "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        "PYTHONPATH": py_path,
    }
    for k, v in env_map.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("OPENDV_API_URL", raising=False)
    monkeypatch.chdir(ws)

    child_env = {**os.environ, **env_map}
    child_env.pop("OPENDV_API_URL", None)
    return ws, state_dir, child_env


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
    assert raw, f"Expected JSON envelope on stdout for {args}; stderr={stderr_buf.getvalue()}"
    return code, CliEnvelope.model_validate_json(raw)


def _query_duckdb_ndjson(jsonl_path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    conn = duckdb.connect(":memory:")
    try:
        rel = conn.execute(
            "SELECT * FROM read_ndjson(?, auto_detect=true)",
            [str(jsonl_path)],
        )
        cols = [desc[0] for desc in (rel.description or ())]
        rows = [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
        return cols, rows
    finally:
        conn.close()


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


# ---------------------------------------------------------------------------
# CUJ 1: Stateless CLI (`opendv --json parse ...`)
# ---------------------------------------------------------------------------


def test_cuj1_cli_parse_lifecycle_zero_opendv_writes_and_duckdb_ndjson(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CUJ 1: `parse {spec, inspect, extract, render-page, validate, export}` + DuckDB read_ndjson."""
    ws, _state_dir, _ = _setup_cli_env(tmp_path, monkeypatch)

    # 1. parse spec
    code_spec, env_spec = _run_json_cli(["parse", "spec"])
    assert code_spec == 0 and env_spec.ok is True
    assert isinstance(env_spec.data, dict)
    assert set(env_spec.data["formats"]) == {"pdf", "docx", "markdown", "domain_note"}
    assert set(env_spec.data["projectors"]) == {"document_map", "markdown", "chunks"}
    assert "ParsedDocumentIR" in env_spec.data["schemas"]
    assert "ChunkRecord" in env_spec.data["schemas"]
    assert not (ws / ".opendv").exists()

    # 2. parse inspect (unconstrained by --max-pages; extract enforces --max-pages)
    code_insp, env_insp = _run_json_cli(["parse", "inspect", str(CUSTODY_PDF), "--max-pages", "2"])
    assert code_insp == 0 and isinstance(env_insp.data, dict)
    assert env_insp.data["page_count"] == 6
    assert 4 in env_insp.data["scanned_pages"]
    assert 1 in env_insp.data["digital_pages"]

    code_cap, env_cap = _run_json_cli(["parse", "extract", str(CUSTODY_PDF), "--max-pages", "2"])
    assert code_cap == int(CliExitCode.VALIDATION_ERROR)
    assert env_cap.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED

    # 3. parse extract (--pages pushdown + --redact copy)
    ir_out = ws / "custody_pages.json"
    code_ext, env_ext = _run_json_cli(
        ["parse", "extract", str(CUSTODY_PDF), "--pages", "1-2,5", "--out", str(ir_out)]
    )
    assert code_ext == 0 and ir_out.is_file()
    assert isinstance(env_ext.data, dict) and env_ext.data["page_count"] == 3
    ir = ParsedDocumentIR.model_validate_json(ir_out.read_text(encoding="utf-8"))
    assert [p.page_number for p in ir.pages] == [1, 2, 5]
    assert ir.provenance.redacted is False

    pii_pdf = _make_text_pdf(ws / "cli_pii.pdf", "Holder SSN 123-45-6789 review.")
    red_ir_out = ws / "pii_redacted_ir.json"
    code_red, _ = _run_json_cli(["parse", "extract", str(pii_pdf), "--redact", "--out", str(red_ir_out)])
    assert code_red == 0
    red_ir = ParsedDocumentIR.model_validate_json(red_ir_out.read_text(encoding="utf-8"))
    assert red_ir.provenance.redacted is True
    assert red_ir.provenance.sink_class == "MODEL_EGRESS"
    assert MASKED_PII_TOKEN in red_ir.pages[0].canonical_text
    assert "123-45-6789" not in red_ir.pages[0].canonical_text

    # 4. parse render-page (digital --out vs inline, scanned gate with --approve-scan vs --redact)
    png_out = ws / "page1.png"
    code_png, env_png = _run_json_cli(
        ["parse", "render-page", str(CUSTODY_PDF), "--page", "1", "--dpi", "120", "--out", str(png_out)]
    )
    assert code_png == 0 and isinstance(env_png.data, dict)
    assert env_png.data["png_b64"] is None and env_png.data["output_path"] is not None
    with Image.open(png_out) as img:
        assert img.width > 0 and img.height > 0

    code_scan_blk, env_scan_blk = _run_json_cli(
        ["parse", "render-page", str(CUSTODY_PDF), "--page", "4"]
    )
    assert code_scan_blk == int(CliExitCode.VALIDATION_ERROR)
    assert env_scan_blk.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    scan_png = ws / "page4_approved.png"
    code_scan_ok, env_scan_ok = _run_json_cli(
        [
            "parse",
            "render-page",
            str(CUSTODY_PDF),
            "--page",
            "4",
            "--approve-scan",
            "--out",
            str(scan_png),
        ]
    )
    assert code_scan_ok == 0 and scan_png.is_file()
    assert isinstance(env_scan_ok.data, dict) and env_scan_ok.data["redacted"] is False

    code_scan_egress, env_scan_egress = _run_json_cli(
        ["parse", "render-page", str(CUSTODY_PDF), "--page", "4", "--approve-scan", "--redact"]
    )
    assert code_scan_egress == int(CliExitCode.VALIDATION_ERROR)
    assert env_scan_egress.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    # 5. parse validate (valid exit 0 vs tampered exit 2 VERIFICATION_GATE_FAILED)
    val_out = ws / "val_diags.json"
    code_val, _ = _run_json_cli(["parse", "validate", str(red_ir_out), "--out", str(val_out)])
    assert code_val == 0 and json.loads(val_out.read_text(encoding="utf-8")) == []

    tampered = json.loads(ir_out.read_text(encoding="utf-8"))
    tampered["pages"][0]["canonical_text"] = "Tampered page contents."
    tampered_path = ws / "tampered_ir.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    code_tamper, env_tamper = _run_json_cli(["parse", "validate", str(tampered_path)])
    assert code_tamper == int(CliExitCode.VALIDATION_ERROR)
    assert env_tamper.error_code == ErrorCode.VERIFICATION_GATE_FAILED
    assert isinstance(env_tamper.data, list) and len(env_tamper.data) >= 1

    # 6. parse export --target chunks --format jsonl --out + DuckDB read_ndjson
    docx_ir_path = ws / "aspose_ir.json"
    code_docx, _ = _run_json_cli(["parse", "extract", str(ASPOSE_DOCX), "--out", str(docx_ir_path)])
    assert code_docx == 0

    chunks_jsonl = ws / "chunks.jsonl"
    code_exp, env_exp = _run_json_cli(
        [
            "parse",
            "export",
            str(docx_ir_path),
            "--target",
            "chunks",
            "--format",
            "jsonl",
            "--out",
            str(chunks_jsonl),
        ]
    )
    assert code_exp == 0 and chunks_jsonl.is_file()
    assert isinstance(env_exp.data, dict) and env_exp.data["line_count"] >= 2

    raw_lines = [json.loads(line) for line in chunks_jsonl.read_text(encoding="utf-8").splitlines() if line]
    expected_keys = set(ChunkRecord.model_fields.keys())
    assert all(set(item.keys()) == expected_keys for item in raw_lines)
    assert any(item["table_json"] is not None for item in raw_lines)
    assert any(item["table_json"] is None for item in raw_lines)

    cols, rows = _query_duckdb_ndjson(chunks_jsonl)
    assert len(rows) == len(raw_lines)
    assert expected_keys.issubset(set(cols))

    # --json --format jsonl without --out is rejected
    code_no_out, env_no_out = _run_json_cli(
        ["parse", "export", str(docx_ir_path), "--target", "chunks", "--format", "jsonl"]
    )
    assert code_no_out == int(CliExitCode.VALIDATION_ERROR)
    assert env_no_out.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    assert not (ws / ".opendv").exists()


def test_cuj1_cli_parse_rejects_api_url_flag_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CUJ 1: `opendv parse` rejects `--api-url` and `OPENDV_API_URL` with exit 2."""
    ws, _state_dir, _ = _setup_cli_env(tmp_path, monkeypatch)

    code_flag, env_flag = _run_json_cli(["--api-url", "http://127.0.0.1:8420", "parse", "spec"])
    assert code_flag == int(CliExitCode.VALIDATION_ERROR)
    assert env_flag.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    code_env, env_env = _run_json_cli(
        ["parse", "spec"],
        env_overrides={"OPENDV_API_URL": "http://127.0.0.1:8420"},
    )
    assert code_env == int(CliExitCode.VALIDATION_ERROR)
    assert env_env.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT
    assert not (ws / ".opendv").exists()


# ---------------------------------------------------------------------------
# CUJ 2: MCP (`--profile project|parsing|all`, containment, redaction, spillover)
# ---------------------------------------------------------------------------


def test_cuj2_mcp_profiles_17_6_23_and_flag_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CUJ 2: Verify `17`/`6`/`23` tools across profiles and CLI/server flag rejections."""
    ws, state_dir, child_env = _setup_cli_env(tmp_path, monkeypatch)

    # Rejection checks via CLI
    for bad_proj in ("p1", ""):
        code_p, env_p = _run_json_cli(["mcp", "serve", "--profile", "parsing", "--project", bad_proj])
        assert code_p == int(CliExitCode.VALIDATION_ERROR)
        assert env_p.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    for extra_flag in (
        ["--allow-root", str(ws)],
        ["--allow-unredacted"],
        ["--max-inline-bytes", "1024"],
        ["--spill-dir", str(tmp_path / "spill")],
    ):
        code_pr, env_pr = _run_json_cli(
            ["mcp", "serve", "--profile", "project", "--project", "p1", *extra_flag]
        )
        assert code_pr == int(CliExitCode.VALIDATION_ERROR)
        assert env_pr.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    code_all_miss, env_all_miss = _run_json_cli(["mcp", "serve", "--profile", "all"])
    assert code_all_miss == int(CliExitCode.VALIDATION_ERROR)
    assert env_all_miss.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # --profile parsing exposes exact 6 tools without creating .opendv/
    async def _check_parsing_stdio() -> None:
        params = StdioServerParameters(
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
                str(ws),
            ],
            env=child_env,
        )
        async with stdio_client(params) as (r_s, w_s), ClientSession(r_s, w_s) as sess:
            await sess.initialize()
            listed = await sess.list_tools()
            names = {t.name for t in listed.tools}
            assert len(names) == 6
            assert names == set(PARSING_MCP_TOOL_NAMES)
            is_err, spec_data, _ = await _call_mcp_tool(sess, "parse_spec", {})
            assert is_err is False and set(spec_data["formats"]) == {
                "pdf",
                "docx",
                "markdown",
                "domain_note",
            }

    asyncio.run(_check_parsing_stdio())
    assert not (ws / ".opendv").exists()

    # Initialize project and verify --profile project (17) and --profile all (23)
    svc = OpenDVService(ws, state_dir=state_dir)
    svc.init_project(ProjectInitReq(project_id="mcp-p1", domain_namespace="asset_management"))

    async def _check_project_and_all() -> None:
        proj_srv = create_mcp_stdio_server(
            profile="project",
            project_id="mcp-p1",
            workspace_root=ws,
            state_dir=state_dir,
        )
        proj_tools = {t.name for t in await proj_srv.list_tools()}
        assert len(proj_tools) == 17
        assert proj_tools == set(MCP_TOOL_NAMES)

        all_params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "opendv.cli",
                "mcp",
                "serve",
                "--transport",
                "stdio",
                "--profile",
                "all",
                "--project",
                "mcp-p1",
                "--allow-root",
                str(ws),
            ],
            env=child_env,
        )
        async with stdio_client(all_params) as (r_s, w_s), ClientSession(r_s, w_s) as sess:
            await sess.initialize()
            listed = await sess.list_tools()
            all_names = {t.name for t in listed.tools}
            assert len(all_names) == 23
            assert all_names == set(MCP_TOOL_NAMES) | set(PARSING_MCP_TOOL_NAMES)
            err_proj, proj_data, _ = await _call_mcp_tool(sess, "project_get", {})
            assert err_proj is False and proj_data["project_id"] == "mcp-p1"

    asyncio.run(_check_project_and_all())


def test_cuj2_mcp_allow_root_containment_allow_unredacted_and_approve_scan_gating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CUJ 2: Verify `--allow-root`, `--allow-unredacted`, and `approve_scan` MCP fail-closed gates."""
    ws, _state_dir, child_env = _setup_cli_env(tmp_path, monkeypatch)
    local_custody = ws / "custody.pdf"
    local_custody.write_bytes(CUSTODY_PDF.read_bytes())
    local_pii = _make_text_pdf(ws / "pii.pdf", "Contact SSN 123-45-6789 immediately.")
    outside_pdf = _make_text_pdf(tmp_path / "outside" / "forbidden.pdf", "Outside root.")

    async def _run() -> None:
        # Strict default: allow_unredacted=False
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
                str(ws),
            ],
            env=child_env,
        )
        async with stdio_client(strict_params) as (r_s, w_s), ClientSession(r_s, w_s) as sess:
            await sess.initialize()

            # Outside --allow-root fails with ARCHIVE_SECURITY_VIOLATION while session stays alive
            err_out, _, prob_out = await _call_mcp_tool(
                sess, "parse_inspect", {"uri": str(outside_pdf)}
            )
            assert err_out is True and prob_out is not None
            assert prob_out.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

            # Unredacted extract blocked without --allow-unredacted
            err_unred, _, prob_unred = await _call_mcp_tool(
                sess, "parse_extract", {"uri": str(local_pii), "redact": False}
            )
            assert err_unred is True and prob_unred is not None
            assert prob_unred.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

            # Redacted extract succeeds with MODEL_EGRESS masking
            err_red, red_ir, _ = await _call_mcp_tool(
                sess, "parse_extract", {"uri": str(local_pii)}
            )
            assert err_red is False
            assert red_ir["provenance"]["redacted"] is True
            assert MASKED_PII_TOKEN in red_ir["pages"][0]["canonical_text"]
            assert "123-45-6789" not in red_ir["pages"][0]["canonical_text"]

            # approve_scan=True does NOT bypass MODEL_EGRESS on scanned page 4
            err_scan, _, prob_scan = await _call_mcp_tool(
                sess,
                "parse_render_page",
                {"uri": str(local_custody), "page_number": 4, "approve_scan": True},
            )
            assert err_scan is True and prob_scan is not None
            assert prob_scan.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

        # Opt-in --allow-unredacted permits approve_scan=True under LOCAL_HUMAN_IO
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
                str(ws),
                "--allow-unredacted",
            ],
            env=child_env,
        )
        async with stdio_client(unred_params) as (r_s, w_s), ClientSession(r_s, w_s) as sess:
            await sess.initialize()
            err_ok, ok_res, _ = await _call_mcp_tool(
                sess,
                "parse_render_page",
                {"uri": str(local_custody), "page_number": 4, "approve_scan": True},
            )
            assert err_ok is False and ok_res["redacted"] is False
            png_bytes = (
                base64.b64decode(ok_res["png_b64"])
                if ok_res["png_b64"]
                else Path(ok_res["spillover"]["spill_path"]).read_bytes()
            )
            with Image.open(io.BytesIO(png_bytes)) as im:
                assert im.width > 0 and im.height > 0

    asyncio.run(_run())


def test_cuj2_mcp_spill_dir_spillover_ref_and_ir_path_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CUJ 2: Oversized extract/export/render spill to `--spill-dir` and `ir_path` round-trips."""
    ws, _state_dir, child_env = _setup_cli_env(tmp_path, monkeypatch)
    local_md = ws / "stripe.md"
    local_md.write_bytes(STRIPE_MD.read_bytes())
    local_pdf = ws / "custody.pdf"
    local_pdf.write_bytes(CUSTODY_PDF.read_bytes())
    spill_dir = tmp_path / "custom_spill_dir"

    async def _run() -> None:
        params = StdioServerParameters(
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
                str(ws),
                "--max-inline-bytes",
                "512",
                "--spill-dir",
                str(spill_dir),
            ],
            env=child_env,
        )
        async with stdio_client(params) as (r_s, w_s), ClientSession(r_s, w_s) as sess:
            await sess.initialize()

            # 1. parse_extract spills IR JSON to spill_dir
            err_ext, ext_ref, _ = await _call_mcp_tool(
                sess, "parse_extract", {"uri": str(local_md)}
            )
            assert err_ext is False and ext_ref["spilled"] is True
            assert ext_ref["format"] == "json"
            ir_spill = Path(ext_ref["spill_path"]).resolve()
            assert ir_spill.is_relative_to(spill_dir.resolve())
            assert ir_spill.is_file()
            assert hashlib.sha256(ir_spill.read_bytes()).hexdigest() == ext_ref["sha256"]

            # 2. Pass ir_path back into parse_validate_ir
            err_val, diags, _ = await _call_mcp_tool(
                sess, "parse_validate_ir", {"ir_path": str(ir_spill)}
            )
            assert err_val is False and diags == []

            # 3. Pass ir_path back into parse_export_ir(format="jsonl") -> spills JSONL readable by DuckDB
            err_exp, exp_ref, _ = await _call_mcp_tool(
                sess,
                "parse_export_ir",
                {"target": "chunks", "ir_path": str(ir_spill), "format": "jsonl"},
            )
            assert err_exp is False and exp_ref["spilled"] is True
            assert exp_ref["format"] == "jsonl"
            jsonl_spill = Path(exp_ref["spill_path"]).resolve()
            assert jsonl_spill.is_relative_to(spill_dir.resolve())
            cols, rows = _query_duckdb_ndjson(jsonl_spill)
            assert len(rows) >= 1 and "chunk_id" in cols and "table_json" in cols

            # 4. parse_render_page exceeding 512 bytes spills PNG to spill_dir
            err_ren, ren_res, _ = await _call_mcp_tool(
                sess, "parse_render_page", {"uri": str(local_pdf), "page_number": 1}
            )
            assert err_ren is False
            assert ren_res["png_b64"] is None
            assert ren_res["spillover"]["spilled"] is True
            assert ren_res["spillover"]["format"] == "png"
            png_spill = Path(ren_res["spillover"]["spill_path"]).resolve()
            assert png_spill.is_relative_to(spill_dir.resolve())
            assert png_spill.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# CUJ 3: Custom FormatParser & IRProjector SPI injection
# ---------------------------------------------------------------------------


class _CustomKvNoteParser:
    """Custom Layer-2 FormatParser SPI implementation for `.txt` domain_note files."""

    parser_id = "custom_kv_note"
    parser_version = "0.1.0"
    supports_render = False

    def inspect(self, blob: SourceBlob, opts: ParseOptions) -> InspectResult:
        _ = opts
        raw = normalize_page_text(blob.path.read_text(encoding="utf-8"))
        return InspectResult(
            source_uri_sanitized=blob.uri_sanitized,
            binary_sha256=blob.binary_sha256,
            byte_size=blob.byte_size,
            format_id="domain_note",
            page_count=1,
            scanned_pages=[],
            digital_pages=[1],
            renderable=False,
        )

    def parse(self, blob: SourceBlob, opts: ParseOptions) -> ParsedDocumentIR:
        _ = opts
        raw = normalize_page_text(blob.path.read_text(encoding="utf-8"))
        sha = compute_canonical_text_sha256(raw)
        non_ws = sum(1 for ch in raw if not ch.isspace())
        return ParsedDocumentIR(
            provenance=ParseProvenance(
                parser_id=self.parser_id,
                parser_version=self.parser_version,
                source_uri_sanitized=blob.uri_sanitized,
                binary_sha256=blob.binary_sha256,
                detected_mime="text/plain",
                format_id="domain_note",
                page_count=1,
                redacted=False,
                sink_class="LOCAL_HUMAN_IO",
                elapsed_ms=1,
            ),
            pages=[
                PageSlice(
                    page_number=1,
                    slice_kind="virtual_section_page",
                    renderable=False,
                    canonical_text=raw,
                    canonical_text_sha256=sha,
                    raw_text_length=len(raw),
                    non_ws_chars=non_ws,
                    image_block_count=0,
                    needs_ocr=False,
                    extracted_via="native_text",
                )
            ],
            outline=[
                DocumentOutlineNode(
                    node_id="out_1",
                    title="Custom Note Root",
                    level=1,
                    start_page=1,
                    end_page=1,
                    block_id="blk_1",
                )
            ],
            blocks=[
                DocumentBlock(
                    block_id="blk_1",
                    kind="paragraph",
                    reading_order=0,
                    spans=[
                        PageSpan(
                            page_number=1,
                            char_start=0,
                            char_end=len(raw),
                            bbox=NormalizedBBox(x0=0.0, y0=0.0, x1=1.0, y1=0.2),
                        )
                    ],
                    text=raw,
                )
            ],
        )

    def render_page(
        self, blob: SourceBlob, page_number: int, opts: ParseOptions
    ) -> RenderedPageBuffer:
        _ = blob, page_number, opts
        raise NotImplementedError("Custom KV parser is text-only.")


class _CustomChunksProjector:
    """Custom IRProjector emitting ChunkRecord items and DuckDB-safe JSONL."""

    target_id: TargetProjection = "chunks"

    def project(
        self,
        ir: ParsedDocumentIR,
        *,
        redaction: RedactionMode = "off",
        doc_id: str | None = None,
        filename: str | None = None,
        approved_scan_pages: Sequence[int] = (),
    ) -> list[ChunkRecord]:
        _ = doc_id, filename, approved_scan_pages
        page_by_num = {pg.page_number: pg for pg in ir.pages}
        out: list[ChunkRecord] = []
        for blk in ir.blocks:
            pg = page_by_num[blk.spans[0].page_number]
            txt = mask_pii_text(blk.text) if redaction == "on" else blk.text
            out.append(
                ChunkRecord(
                    chunk_id=f"custom:{blk.block_id}",
                    doc_sha256=ir.provenance.binary_sha256,
                    format_id=ir.provenance.format_id,
                    page_number=pg.page_number,
                    slice_kind=pg.slice_kind,
                    canonical_text_sha256=pg.canonical_text_sha256,
                    heading_path=["Custom Note Root"],
                    block_id=blk.block_id,
                    block_kind=blk.kind,
                    reading_order=blk.reading_order,
                    text=txt,
                    char_start=blk.spans[0].char_start if redaction == "off" else None,
                    char_end=blk.spans[0].char_end if redaction == "off" else None,
                    bbox_json=None,
                    table_json=None,
                    redacted=(redaction == "on") or ir.provenance.redacted,
                )
            )
        return out

    def stream_jsonl(
        self, ir: ParsedDocumentIR, *, redaction: RedactionMode = "off"
    ) -> Iterator[str]:
        for rec in self.project(ir, redaction=redaction):
            yield rec.model_dump_json() + "\n"


def test_cuj3_custom_format_parser_and_ir_projector_spi_injection(tmp_path: Path) -> None:
    """CUJ 3: Custom FormatParser & IRProjector compose via ParsingRegistry/ParsingEngine/ParsingService."""
    ws = tmp_path / "spi_ws"
    ws.mkdir()
    note = ws / "policy_memo.txt"
    note.write_text("Custodian SSN 123-45-6789 approved limit 5000.", encoding="utf-8")

    custom_parser = _CustomKvNoteParser()
    custom_projector = _CustomChunksProjector()
    assert isinstance(custom_parser, FormatParser)
    assert isinstance(custom_projector, IRProjector)
    assert isinstance(NormalizedWordBox(bbox=NormalizedBBox(x0=0, y0=0, x1=0.1, y1=0.1), text="x"), NormalizedWordBox)

    registry = ParsingRegistry()
    registry.register_parser("domain_note", custom_parser)
    registry.register_projector(custom_projector)

    with pytest.raises(OpenDVError) as exc_fmt:
        registry.get_parser("pdf")
    assert exc_fmt.value.error_code == ErrorCode.UNSUPPORTED_MEDIA_TYPE

    with pytest.raises(OpenDVError) as exc_proj:
        registry.get_projector("markdown")
    assert exc_proj.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    engine = ParsingEngine(registry=registry)
    svc = ParsingService(workspace_root=ws, allowed_roots=[ws], registry=registry)
    spec = svc.get_spec()
    assert spec.formats == ["domain_note"]
    assert spec.projectors == ["chunks"]

    insp = svc.inspect_document(note)
    assert insp.format_id == "domain_note" and insp.renderable is False

    raw_ir = svc.parse_document(note, sink_class="LOCAL_HUMAN_IO")
    assert raw_ir.provenance.parser_id == "custom_kv_note"
    assert "123-45-6789" in raw_ir.pages[0].canonical_text
    assert svc.validate_ir(raw_ir) == []

    # Engine applies redact_ir automatically when custom parser does not implement parse_redacted
    egress_ir = engine.parse(SourceBlob.from_path(note), sink_class="MODEL_EGRESS")
    assert egress_ir.provenance.redacted is True
    assert MASKED_PII_TOKEN in egress_ir.pages[0].canonical_text
    assert "123-45-6789" not in egress_ir.pages[0].canonical_text
    assert engine.validate(egress_ir) == []

    # Non-renderable custom parser rejected cleanly on render_page
    with pytest.raises(OpenDVError) as exc_ren:
        svc.render_document_page(note, 1)
    assert exc_ren.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Custom IRProjector streams DuckDB-readable ChunkRecord JSONL
    jsonl_path = ws / "custom_chunks.jsonl"
    jsonl_path.write_text(
        "".join(svc.stream_export(raw_ir, target="chunks", sink_class="MODEL_EGRESS")),
        encoding="utf-8",
    )
    cols, rows = _query_duckdb_ndjson(jsonl_path)
    assert len(rows) == 1
    assert rows[0]["chunk_id"] == "custom:blk_1"
    assert rows[0]["redacted"] is True
    assert "123-45-6789" not in rows[0]["text"]
    assert "table_json" in cols
    assert not (ws / ".opendv").exists()


def test_mcp_parse_validate_ir_non_utf8_returns_syntax_invalid_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap 4a: Non-UTF-8 `ir_path` in `parse_validate_ir`/`parse_export_ir` returns SYNTAX_INVALID_ARGUMENT."""
    ws, state_dir, _ = _setup_cli_env(tmp_path, monkeypatch)
    bin_file = ws / "invalid_utf8.bin"
    bin_file.write_bytes(b"\xff\xfe\x00\x01binary-garbage")

    srv = create_mcp_stdio_server(
        profile="parsing",
        workspace_root=ws,
        state_dir=state_dir,
        allow_roots=[ws],
    )
    for tool_name, tool_args in (
        ("parse_validate_ir", {"ir_path": str(bin_file)}),
        ("parse_export_ir", {"target": "markdown", "ir_path": str(bin_file)}),
    ):
        with pytest.raises(ToolError) as exc_info:
            asyncio.run(srv.call_tool(tool_name, tool_args))
        raw_msg = str(exc_info.value)
        problem = ProblemDetails.model_validate_json(raw_msg[raw_msg.find("{") :])
        assert problem.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT
        assert problem.status == 400
