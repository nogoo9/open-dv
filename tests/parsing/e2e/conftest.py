"""Opaque-box E2E test harness, environment isolation, and synthetic generators."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
import zlib
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import docx
import duckdb
import pypdf
import pytest
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from opendv.models import CliEnvelope, ProblemDetails

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = REPO_ROOT / "src"
PUBLIC_FIXTURES_DIR = REPO_ROOT / "tests" / "parsing" / "fixtures_public"
PUBLIC_MANIFEST_PATH = PUBLIC_FIXTURES_DIR / "MANIFEST.json"
VIRTUAL_PAGE_SPLIT_CHARS = 24_000


def _snapshot_dir_mtimes(directory: Path | None) -> dict[str, float]:
    """Capture relative file modification times inside ``directory``."""
    if directory is None or not directory.exists():
        return {}
    return {
        str(p.relative_to(directory)): p.stat().st_mtime
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def _build_pythonpath(existing: str | None) -> str:
    """Return a PYTHONPATH value with ``REPO_ROOT / 'src'`` prepended."""
    src_str = str(SRC_DIR)
    if not existing:
        return src_str
    parts = [p for p in existing.split(os.pathsep) if p]
    if src_str in parts:
        return existing
    return f"{src_str}{os.pathsep}{existing}"


@dataclass(frozen=True, slots=True)
class E2EWorkspace:
    """Isolated per-test filesystem and environment boundary for CLI/MCP E2E runs.

    By default, ``OPENDV_WORKSPACE_ROOTS`` includes ``self.workspace``, ``self.root``
    (``tmp_path``), and ``REPO_ROOT`` so tests can access public fixtures; pass
    ``workspace_roots`` to :meth:`subprocess_env` to narrow roots for containment tests.
    """

    root: Path
    workspace: Path
    opendv_home: Path
    user_state: Path
    state_dir: Path
    roaming_dir: Path
    host_home_opendv: Path
    host_localappdata_opendv: Path | None
    initial_home_mtimes: dict[str, float]
    initial_localappdata_mtimes: dict[str, float]

    def subprocess_env(
        self,
        extra: Mapping[str, str] | None = None,
        workspace_roots: Sequence[Path] | None = None,
    ) -> dict[str, str]:
        """Return subprocess environment isolated from host user state and keychains."""
        roots = (
            (self.workspace, self.root, REPO_ROOT)
            if workspace_roots is None
            else tuple(workspace_roots)
        )
        env = dict(os.environ)
        env.update(
            {
                "OPENDV_HOME": str(self.opendv_home),
                "LOCALAPPDATA": str(self.user_state),
                "APPDATA": str(self.roaming_dir),
                "USERPROFILE": str(self.user_state),
                "HOME": str(self.user_state),
                "XDG_STATE_HOME": str(self.user_state),
                "OPENDV_WORKSPACE_ROOTS": os.pathsep.join(str(p) for p in roots),
                "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
                "PYTHONPATH": _build_pythonpath(env.get("PYTHONPATH")),
            }
        )
        if extra:
            env.update(extra)
        return env

    def assert_host_opendv_unmodified(self) -> None:
        """Assert host ~/.opendv and LOCALAPPDATA/opendv were never mutated."""
        assert _snapshot_dir_mtimes(self.host_home_opendv) == self.initial_home_mtimes, (
            f"Host {self.host_home_opendv} was mutated during isolated E2E test"
        )
        assert (
            _snapshot_dir_mtimes(self.host_localappdata_opendv)
            == self.initial_localappdata_mtimes
        ), f"Host {self.host_localappdata_opendv} was mutated during isolated E2E test"

    def assert_no_workspace_opendv_dir(self) -> None:
        """Assert stateless commands did not create a ``.opendv`` directory in workspace."""
        ws_opendv = self.workspace / ".opendv"
        assert not ws_opendv.exists(), f"Unexpected workspace state dir created: {ws_opendv}"


@pytest.fixture
def e2e_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[E2EWorkspace]:
    """Create an isolated E2E workspace and assert host ~/.opendv remains untouched."""
    host_home_opendv = Path.home() / ".opendv"
    raw_localappdata = os.environ.get("LOCALAPPDATA")
    host_localappdata_opendv = (
        Path(raw_localappdata) / "opendv" if raw_localappdata else None
    )
    initial_home_mtimes = _snapshot_dir_mtimes(host_home_opendv)
    initial_localappdata_mtimes = _snapshot_dir_mtimes(host_localappdata_opendv)

    workspace = tmp_path / "ws"
    opendv_home = workspace / ".opendv"
    user_state = tmp_path / "user_state"
    state_dir = user_state / "opendv"
    roaming_dir = user_state / "Roaming"
    for folder in (workspace, state_dir, roaming_dir):
        folder.mkdir(parents=True, exist_ok=True)

    ws_roots = f"{workspace}{os.pathsep}{tmp_path}{os.pathsep}{REPO_ROOT}"
    monkeypatch.setenv("OPENDV_HOME", str(opendv_home))
    monkeypatch.setenv("LOCALAPPDATA", str(user_state))
    monkeypatch.setenv("APPDATA", str(roaming_dir))
    monkeypatch.setenv("USERPROFILE", str(user_state))
    monkeypatch.setenv("HOME", str(user_state))
    monkeypatch.setenv("XDG_STATE_HOME", str(user_state))
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOTS", ws_roots)
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")

    isolated = E2EWorkspace(
        root=tmp_path,
        workspace=workspace,
        opendv_home=opendv_home,
        user_state=user_state,
        state_dir=state_dir,
        roaming_dir=roaming_dir,
        host_home_opendv=host_home_opendv,
        host_localappdata_opendv=host_localappdata_opendv,
        initial_home_mtimes=initial_home_mtimes,
        initial_localappdata_mtimes=initial_localappdata_mtimes,
    )
    yield isolated
    isolated.assert_host_opendv_unmodified()


@dataclass(frozen=True, slots=True)
class CliResult:
    """Captured result of executing the ``opendv`` CLI as a subprocess."""

    returncode: int
    stdout: str
    stderr: str
    envelope: CliEnvelope | None


def run_cli(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    env_extra: Mapping[str, str] | None = None,
    expect_json: bool = True,
    workspace: E2EWorkspace | None = None,
    timeout: float = 60.0,
) -> CliResult:
    """Execute ``python -m opendv.cli`` and optionally validate ``CliEnvelope`` JSON."""
    cmd_args = list(args)
    if expect_json and "--json" not in cmd_args:
        cmd_args = ["--json", *cmd_args]

    if env is not None:
        child_env = dict(env)
        child_env["PYTHONPATH"] = _build_pythonpath(child_env.get("PYTHONPATH"))
    elif workspace is not None:
        child_env = workspace.subprocess_env(extra=env_extra)
    else:
        child_env = dict(os.environ)
        child_env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
        child_env["PYTHONPATH"] = _build_pythonpath(child_env.get("PYTHONPATH"))
        if env_extra:
            child_env.update(env_extra)

    if env is not None and env_extra:
        child_env.update(env_extra)

    run_cwd = cwd if cwd is not None else (workspace.workspace if workspace is not None else REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-m", "opendv.cli", *cmd_args],
        cwd=str(run_cwd),
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )
    envelope = CliEnvelope.model_validate_json(proc.stdout) if expect_json else None
    return CliResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        envelope=envelope,
    )


@pytest.fixture
def cli_runner(e2e_workspace: E2EWorkspace) -> Callable[..., CliResult]:
    """Return a CLI runner bound to the test's isolated ``E2EWorkspace``."""

    def _run(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        env_extra: Mapping[str, str] | None = None,
        expect_json: bool = True,
        timeout: float = 60.0,
    ) -> CliResult:
        return run_cli(
            args,
            cwd=cwd,
            env=env,
            env_extra=env_extra,
            expect_json=expect_json,
            workspace=e2e_workspace,
            timeout=timeout,
        )

    return _run


@asynccontextmanager
async def open_mcp_session(
    profile: str = "parsing",
    *,
    workspace: E2EWorkspace | None = None,
    project: str | None = None,
    allow_roots: Sequence[Path] | None = None,
    allow_unredacted: bool | None = None,
    max_inline_bytes: int | None = None,
    spill_dir: Path | None = None,
    env_extra: Mapping[str, str] | None = None,
    explicit_profile_flag: bool = False,
) -> AsyncIterator[tuple[ClientSession, dict[str, Any]]]:
    """Launch ``opendv mcp serve`` over stdio and yield ``(session, tools_by_name)``."""
    args = ["-m", "opendv.cli", "mcp", "serve", "--transport", "stdio"]
    if profile != "project" or explicit_profile_flag:
        args.extend(["--profile", profile])
    if project is not None:
        args.extend(["--project", project])
    if allow_roots is not None:
        for root in allow_roots:
            args.extend(["--allow-root", str(root)])
    if allow_unredacted is True:
        args.append("--allow-unredacted")
    elif allow_unredacted is False:
        args.append("--no-allow-unredacted")
    if max_inline_bytes is not None:
        args.extend(["--max-inline-bytes", str(max_inline_bytes)])
    if spill_dir is not None:
        args.extend(["--spill-dir", str(spill_dir)])

    if workspace is not None:
        child_env = workspace.subprocess_env(extra=env_extra)
        cwd = str(workspace.workspace)
    else:
        child_env = dict(os.environ)
        child_env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
        child_env["PYTHONPATH"] = _build_pythonpath(child_env.get("PYTHONPATH"))
        if env_extra:
            child_env.update(env_extra)
        cwd = str(REPO_ROOT)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=args,
        env=child_env,
        cwd=cwd,
    )
    async with (
        stdio_client(server_params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        listed = await session.list_tools()
        tools_by_name = {tool.name: tool for tool in listed.tools}
        yield session, tools_by_name


async def call_mcp_tool_json(
    session: ClientSession,
    name: str,
    arguments: Mapping[str, Any] | None = None,
) -> tuple[bool, Any, ProblemDetails | None]:
    """Call an MCP tool and decode either its JSON result or ``ProblemDetails`` error."""
    res = await session.call_tool(name, dict(arguments) if arguments is not None else {})
    is_error = bool(getattr(res, "is_error", False))
    text = ""
    if res.content:
        first = res.content[0]
        if hasattr(first, "text"):
            text = str(first.text)

    if is_error:
        json_idx = text.find("{")
        if json_idx != -1:
            raw_json = text[json_idx:]
            problem = ProblemDetails.model_validate_json(raw_json)
            return True, json.loads(raw_json), problem
        if res.structured_content is not None:
            problem = ProblemDetails.model_validate(res.structured_content)
            return True, dict(res.structured_content), problem
        return True, {"raw_error": text}, None

    if res.structured_content is not None:
        payload = res.structured_content.get("result", res.structured_content)
        return False, payload, None
    return False, (json.loads(text) if text else None), None


def load_public_fixtures_manifest() -> dict[str, dict[str, Any]]:
    """Load the verified public corpus manifest from ``tests/parsing/fixtures_public/MANIFEST.json``."""
    raw = json.loads(PUBLIC_MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("Expected MANIFEST.json to be a JSON object mapping filename -> metadata")
    return raw


@pytest.fixture(scope="session")
def public_fixtures_manifest() -> dict[str, dict[str, Any]]:
    """Session-scoped manifest dictionary for all verified public corpus fixtures."""
    return load_public_fixtures_manifest()


def public_fixture_path(name: str) -> Path:
    """Return the path to a named fixture in ``tests/parsing/fixtures_public/``."""
    path = PUBLIC_FIXTURES_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Public fixture not found: {path}")
    return path


@pytest.fixture(scope="session")
def public_fixture() -> Callable[[str], Path]:
    """Session-scoped factory resolving public fixture filenames to ``Path`` objects."""
    return public_fixture_path


def query_ndjson_with_duckdb(
    jsonl_path: Path,
    sql: str = "SELECT * FROM chunks",
) -> list[dict[str, Any]]:
    """Query an NDJSON file using DuckDB ``read_ndjson(..., auto_detect=true)``."""
    posix_path = jsonl_path.resolve().as_posix()
    escaped_posix = posix_path.replace("'", "''")
    con = duckdb.connect(":memory:")
    try:
        con.execute(
            f"CREATE TEMP VIEW chunks AS SELECT * FROM read_ndjson('{escaped_posix}', auto_detect=true)"
        )
        if "?" in sql:
            cursor = con.execute(sql, [posix_path])
        elif "{path}" in sql:
            cursor = con.execute(sql.format(path=escaped_posix))
        else:
            cursor = con.execute(sql)
        cols = [desc[0] for desc in (cursor.description or ())]
        return [dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]
    finally:
        con.close()


# ---- Deterministic Synthetic Boundary Generators ----


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _make_pdf_font_resources() -> DictionaryObject:
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    return DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})


def _make_pdf_text_stream(lines: Sequence[str]) -> DecodedStreamObject:
    ops = ["BT", "/F1 12 Tf", "72 720 Td", "16 TL"]
    for idx, line in enumerate(lines):
        escaped = _pdf_escape(line)
        ops.append(f"({escaped}) Tj" if idx == 0 else f"T* ({escaped}) Tj")
    ops.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(ops).encode("latin-1"))
    return stream


def make_empty_text_file(path: Path) -> Path:
    """Write a 0-byte empty text/markdown file at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def make_minimal_pdf(path: Path, text: str = "Open-DV minimal PDF fixture.") -> Path:
    """Create a minimal single-page digital PDF containing ``text``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = _make_pdf_font_resources()
    stream = _make_pdf_text_stream(text.splitlines() or [text])
    page[NameObject("/Contents")] = ArrayObject([writer._add_object(stream)])
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def make_rotated_pdf(
    path: Path,
    rotate_deg: int = 90,
    text: str = "Rotated page financial summary.",
) -> Path:
    """Create a single-page PDF with explicit ``/Rotate`` page dictionary entry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Rotate")] = NumberObject(rotate_deg)
    page[NameObject("/Resources")] = _make_pdf_font_resources()
    stream = _make_pdf_text_stream(text.splitlines() or [text])
    page[NameObject("/Contents")] = ArrayObject([writer._add_object(stream)])
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def make_locked_pdf(
    path: Path,
    password: str = "secret",
    text: str = "Confidential password-protected PDF.",
) -> Path:
    """Create an encrypted PDF requiring ``password``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = _make_pdf_font_resources()
    stream = _make_pdf_text_stream([text])
    page[NameObject("/Contents")] = ArrayObject([writer._add_object(stream)])
    writer.encrypt(password)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def make_corrupt_pdf(path: Path) -> Path:
    """Write a malformed/truncated PDF file that fails parsing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n%%corrupt-garbage-truncated\n1 0 obj\n<< /Type /Catalog\n")
    return path


def make_high_pixel_pdf(
    path: Path,
    width_pt: float = 4000.0,
    height_pt: float = 4000.0,
) -> Path:
    """Create a PDF whose rendered pixel count exceeds 40,000,000 pixels at 150 DPI."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=width_pt, height=height_pt)
    page[NameObject("/Resources")] = _make_pdf_font_resources()
    stream = _make_pdf_text_stream(["High pixel preflight boundary page"])
    page[NameObject("/Contents")] = ArrayObject([writer._add_object(stream)])
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def make_pii_multi_token_pdf(path: Path) -> Path:
    """Create a digital PDF containing multi-token PII values with word boxes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "Customer Verification Statement",
        "Phone: (415) 555-0199",
        "Card: 4111 1111 1111 1111",
        "Routing: ABA 021000021",
        "Tax ID: 123-45-6789",
    ]
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = _make_pdf_font_resources()
    stream = _make_pdf_text_stream(lines)
    page[NameObject("/Contents")] = ArrayObject([writer._add_object(stream)])
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def make_scanned_image_pdf(path: Path) -> Path:
    """Create a single-page image-only PDF with an embedded ``/Im0`` raster and zero text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (64, 64), color=(240, 240, 240))
    raw_rgb = img.tobytes()
    compressed = zlib.compress(raw_rgb)

    img_xobj = DecodedStreamObject()
    img_xobj.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(64),
            NameObject("/Height"): NumberObject(64),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
            NameObject("/Filter"): NameObject("/FlateDecode"),
            NameObject("/Length"): NumberObject(len(compressed)),
        }
    )
    img_xobj._data = compressed

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    img_ref = writer._add_object(img_xobj)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): img_ref})}
    )
    content = DecodedStreamObject()
    content.set_data(b"q 400 0 0 300 100 400 cm /Im0 Do Q")
    page[NameObject("/Contents")] = ArrayObject([writer._add_object(content)])
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def _build_minimal_docx_bytes(paragraph_text: str = "Directory entry test document.") -> bytes:
    doc = docx.Document()
    doc.add_paragraph(paragraph_text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_dir_entry_docx(path: Path) -> Path:
    """Create a valid ``.docx`` containing an explicit ``info.is_dir()`` ``'word/'`` entry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _build_minimal_docx_bytes()
    with (
        zipfile.ZipFile(io.BytesIO(raw), "r") as src,
        zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as dst,
    ):
        dir_info = zipfile.ZipInfo("word/")
        dir_info.external_attr = 0x10
        dir_info.compress_type = zipfile.ZIP_STORED
        dst.writestr(dir_info, b"")
        for info in src.infolist():
            if info.filename != "word/":
                dst.writestr(info, src.read(info.filename))
    return path


def make_zip_bomb_entry_count_docx(path: Path, entry_count: int = 505) -> Path:
    """Create a ``.docx`` archive exceeding the 500-entry archive limit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _build_minimal_docx_bytes("Zip bomb entry count fixture.")
    with (
        zipfile.ZipFile(io.BytesIO(raw), "r") as src,
        zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as dst,
    ):
        for info in src.infolist():
            dst.writestr(info, src.read(info.filename))
        for idx in range(entry_count):
            dst.writestr(f"customXml/item_{idx:04d}.xml", b"<item/>")
    return path


def make_zip_bomb_ratio_docx(
    path: Path,
    uncompressed_bytes: int = 2 * 1024 * 1024,
) -> Path:
    """Create a ``.docx`` containing a >1 MiB entry with compression ratio > 100:1."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _build_minimal_docx_bytes("Zip bomb compression ratio fixture.")
    payload = b"\x00" * uncompressed_bytes
    with (
        zipfile.ZipFile(io.BytesIO(raw), "r") as src,
        zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as dst,
    ):
        for info in src.infolist():
            dst.writestr(info, src.read(info.filename))
        dst.writestr("customXml/bomb_payload.bin", payload)
    return path


def make_xxe_doctype_docx(path: Path) -> Path:
    """Create a ``.docx`` whose ``word/document.xml`` embeds a ``<!DOCTYPE`` / ``<!ENTITY``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _build_minimal_docx_bytes("XXE placeholder")
    xxe_doc = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        b'<!DOCTYPE foo [<!ENTITY xxe "injected">]>\n'
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b"<w:body><w:p><w:r><w:t>&xxe;</w:t></w:r></w:p></w:body></w:document>"
    )
    with (
        zipfile.ZipFile(io.BytesIO(raw), "r") as src,
        zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as dst,
    ):
        for info in src.infolist():
            if info.filename == "word/document.xml":
                dst.writestr("word/document.xml", xxe_doc)
            else:
                dst.writestr(info, src.read(info.filename))
    return path


def make_ole2_header_docx(path: Path) -> Path:
    """Write a file with ``.docx`` extension starting with OLE2 encrypted-office magic bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ole2_magic = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    path.write_bytes(ole2_magic + b"\x00" * 504)
    return path


def _spaced_filler(length: int) -> str:
    """Build deterministic space-delimited ASCII filler of exact character ``length``."""
    if length <= 0:
        return ""
    unit = "data "
    reps = (length // len(unit)) + 2
    buf = (unit * reps)[:length]
    if buf and not buf.endswith(" "):
        buf = buf[:-1] + " "
    return buf


def _dom_preceding_text_length(body: Any) -> int:
    """Compute cumulative ``'\\n'.join`` DOM text length across paragraphs, tables, and SDTs."""
    parts: list[str] = []
    for child in body:
        tag = child.tag.split("}")[-1]
        if tag == "p":
            text = "".join(el.text or "" for el in child.iter(qn("w:t")))
            if text:
                parts.append(text)
        elif tag in ("tbl", "sdt"):
            for para in child.iter(qn("w:p")):
                text = "".join(el.text or "" for el in para.iter(qn("w:t")))
                if text:
                    parts.append(text)
    return len("\n".join(parts))


def make_dom_order_merged_docx(
    path: Path,
    *,
    include_boundary_pii: bool = True,
) -> Path:
    """Create a ``.docx`` exercising DOM order, SDT, nested tables, merges, and 24k PII span."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = docx.Document()
    doc.add_heading("1. DOM Order Intro", level=1)

    tbl = doc.add_table(rows=3, cols=3)
    tbl.cell(0, 0).text = "Region"
    tbl.cell(0, 1).text = "Metric"
    tbl.cell(0, 2).text = "Value"
    tbl.cell(0, 0).merge(tbl.cell(0, 1))
    tbl.cell(1, 0).text = "North"
    tbl.cell(1, 1).text = "Revenue"
    tbl.cell(1, 2).text = "100"
    tbl.cell(2, 0).text = "North"
    tbl.cell(2, 1).text = "Cost"
    tbl.cell(2, 2).text = "40"
    tbl.cell(1, 2).merge(tbl.cell(2, 2))

    # Nested table inside cell (1, 1)
    nested = tbl.cell(1, 1).add_table(rows=2, cols=2)
    nested.cell(0, 0).text = "N_K"
    nested.cell(0, 1).text = "N_V"
    nested.cell(1, 0).text = "sub"
    nested.cell(1, 1).text = "99"

    # Explicit <w:sdt>/<w:sdtContent> content control immediately after the table
    sdt_xml = (
        f'<w:sdt {nsdecls("w")}>'
        "<w:sdtContent>"
        "<w:p><w:r><w:t>SDT Controlled Section Paragraph</w:t></w:r></w:p>"
        "</w:sdtContent>"
        "</w:sdt>"
    )
    tbl._tbl.addnext(parse_xml(sdt_xml))

    doc.add_paragraph("2. Post-Table Paragraph")

    if include_boundary_pii:
        # Size filler prefix from exact cumulative DOM text so "123-45-6789" straddles 24,000.
        preceding_len = _dom_preceding_text_length(doc.element.body)
        separator = 1 if preceding_len > 0 else 0
        prefix_len = max(0, VIRTUAL_PAGE_SPLIT_CHARS - 5 - (preceding_len + separator))
        prefix = _spaced_filler(prefix_len)
        suffix = _spaced_filler(600)
        doc.add_paragraph(f"{prefix}123-45-6789 {suffix}")

    doc.save(path)
    return path


def make_boundary_pii_docx(
    path: Path,
    *,
    split_offset: int = VIRTUAL_PAGE_SPLIT_CHARS,
) -> Path:
    """Create a single-paragraph ``.docx`` with word-bounded PII at ``23995..24006``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    token = "123-45-6789"
    prefix_len = max(0, split_offset - 5)
    prefix = _spaced_filler(prefix_len)
    suffix = _spaced_filler(400)
    doc = docx.Document()
    doc.add_paragraph(f"{prefix}{token} {suffix}")
    doc.save(path)
    return path


def make_boundary_pii_text_file(
    path: Path,
    *,
    split_offset: int = VIRTUAL_PAGE_SPLIT_CHARS,
) -> Path:
    """Create a ``.md`` or ``.txt`` file with word-bounded PII straddling ``split_offset``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    token = "123-45-6789"
    # Position token so index ``split_offset`` falls cleanly inside the SSN token.
    prefix_len = max(0, split_offset - 5)
    prefix = _spaced_filler(prefix_len)
    suffix = _spaced_filler(400)
    content = f"{prefix}{token} {suffix}"
    path.write_text(content, encoding="utf-8")
    return path
