"""Headless LibreOffice Conversion Document Adapter (Task 4B.4 / Issue #204).

Converts Microsoft Office and OpenDocument formats (.docx, .pptx, .odt, .xlsx, .doc, .ppt, .xls)
to PDF using headless LibreOffice (`soffice`) in an isolated user installation profile,
then delegates to LiteParseDocumentAdapter for high-performance layout and text extraction.
When `soffice` is missing from the host system, raises typed OfficeConversionUnavailableError
(HTTP 501 / exit 1).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opendv_contracts.dtos.ingestion import (
    IngestionResult,
    TaintClassification,
)
from opendv_contracts.errors import ErrorCode
from opendv_sources.adapters.liteparse_adapter import (
    InvalidDocumentError,
    LiteParseDocumentAdapter,
)
from opendv_sources.exceptions import (
    DocumentExtractionError,
    DocumentPayloadTooLargeError,
    OfficeConversionUnavailableError,
)
from opendv_sources.ingestion.adapter import BaseDocumentAdapter

logger = logging.getLogger(__name__)

_OFFICE_EXTENSION_MIME_MAP: dict[str, str] = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".doc": "application/msword",
    ".ppt": "application/vnd.ms-powerpoint",
    ".xls": "application/vnd.ms-excel",
}

MAX_OFFICE_INPUT_BYTES_DEFAULT: int = 50 * 1024 * 1024
MAX_OFFICE_UNCOMPRESSED_BYTES_DEFAULT: int = 200 * 1024 * 1024
MAX_OFFICE_COMPRESSION_RATIO_DEFAULT: float = 100.0
MAX_OFFICE_ZIP_ENTRIES_DEFAULT: int = 10_000
_ZIP_RATIO_MIN_UNCOMPRESSED_BYTES: int = 16_384

_UNSAFE_OOXML_REL_TYPE_SUFFIXES: tuple[str, ...] = (
    "/image",
    "/oleobject",
    "/package",
    "/frame",
    "/subdocument",
    "/attachedtemplate",
    "/externallink",
    "/externallinkpath",
    "/connections",
    "/querytable",
    "/slideupdateinfo",
    "/movie",
    "/video",
    "/audio",
    "/htmlpubsaveas",
    "/mailmergeheader",
    "/headersource",
    "/recipientdata",
)

_UNSAFE_TARGET_PREFIXES: tuple[str, ...] = (
    "file:",
    "smb:",
    "\\\\",
    "jar:",
    "gopher:",
    "ldap:",
    "ldaps:",
    "netdoc:",
)

_EXTERNAL_URI_PREFIXES: tuple[str, ...] = (
    "http://",
    "https://",
    "ftp://",
    "ftps://",
    "file:",
    "smb:",
    "//",
    "\\\\",
)

_WORD_UNSAFE_FIELD_RE = re.compile(
    r"\b(?:DDEAUTO|DDE|INCLUDEPICTURE|INCLUDETEXT|IMPORT|LINK)\b",
    re.IGNORECASE,
)
_CALC_UNSAFE_FORMULA_RE = re.compile(
    r"\b(?:WEBSERVICE|RTD|DDE)\s*\(",
    re.IGNORECASE,
)
_XML_DOCTYPE_ENTITY_RE = re.compile(
    rb"<!\s*(?:ENTITY\b|DOCTYPE\b[^>]*\b(?:SYSTEM|PUBLIC)\b)",
    re.IGNORECASE,
)

_HARDENED_REGISTRYMODIFICATIONS_XCU: str = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry"
           xmlns:xs="http://www.w3.org/2001/XMLSchema"
           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
    <prop oor:name="MacroSecurityLevel" oor:op="fuse">
      <value>3</value>
    </prop>
    <prop oor:name="DisableMacrosExecution" oor:op="fuse">
      <value>true</value>
    </prop>
  </item>
  <item oor:path="/org.openoffice.Office.Writer/Content/Update">
    <prop oor:name="UpdateMode" oor:op="fuse">
      <value>1</value>
    </prop>
    <prop oor:name="Link" oor:op="fuse">
      <value>1</value>
    </prop>
  </item>
  <item oor:path="/org.openoffice.Office.Calc/Content/Update">
    <prop oor:name="Link" oor:op="fuse">
      <value>1</value>
    </prop>
  </item>
</oor:items>
"""


def _xml_local_name(tag: str) -> str:
    """Strip XML `{namespace}` prefix from an ElementTree tag or attribute name."""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _write_hardened_libreoffice_profile(profile_dir: Path) -> Path:
    """Write hardened `user/registrymodifications.xcu` disabling macros and auto link updates."""
    user_dir = profile_dir / "user"
    user_dir.mkdir(parents=True, exist_ok=True)
    xcu_path = user_dir / "registrymodifications.xcu"
    xcu_path.write_text(_HARDENED_REGISTRYMODIFICATIONS_XCU, encoding="utf-8")
    return xcu_path


def _inspect_xml_entry_security(entry_name: str, xml_bytes: bytes, filename: str) -> None:
    """Reject XXE declarations, external OOXML/ODF subdocuments/images/OLE, DDE, and WEBSERVICE."""
    if _XML_DOCTYPE_ENTITY_RE.search(xml_bytes):
        raise InvalidDocumentError(
            f"Office archive '{filename}' contains forbidden DTD/ENTITY declaration in "
            f"'{entry_name}'",
            error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        )

    try:
        root = ET.fromstring(xml_bytes)
    except (ET.ParseError, RecursionError, MemoryError, ValueError) as exc:
        raise InvalidDocumentError(
            f"Malformed XML entry '{entry_name}' inside Office archive '{filename}': {exc}",
            error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        ) from exc

    is_rels = entry_name.lower().endswith(".rels")
    instr_chunks: list[str] = []

    for elem in root.iter():
        local_tag = _xml_local_name(elem.tag)
        local_tag_lower = local_tag.lower()

        if is_rels and local_tag_lower == "relationship":
            rel_type = ""
            target = ""
            target_mode = ""
            for attr_k, attr_v in elem.attrib.items():
                ak = _xml_local_name(attr_k).lower()
                if ak == "type":
                    rel_type = attr_v.strip()
                elif ak == "target":
                    target = attr_v.strip()
                elif ak == "targetmode":
                    target_mode = attr_v.strip()

            target_lower = target.lower()
            rel_type_lower = rel_type.lower()
            if target_lower.startswith(_UNSAFE_TARGET_PREFIXES):
                raise InvalidDocumentError(
                    f"Office archive '{filename}' contains forbidden external target '{target}' "
                    f"in '{entry_name}'",
                    error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
                )
            if target_mode.lower() == "external" and (
                rel_type_lower.endswith(_UNSAFE_OOXML_REL_TYPE_SUFFIXES)
                or not rel_type_lower.endswith("/hyperlink")
            ):
                raise InvalidDocumentError(
                    f"Office archive '{filename}' contains forbidden external relationship "
                    f"type '{rel_type}' -> '{target}' in '{entry_name}'",
                    error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
                )

        if local_tag_lower in {
            "subdoc",
            "dde-source",
            "dde-connection-decl",
            "dde-connection",
            "externallink",
            "externalbook",
            "olelink",
        }:
            raise InvalidDocumentError(
                f"Office archive '{filename}' contains forbidden external link element "
                f"'<{local_tag}>' in '{entry_name}'",
                error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
            )

        if local_tag_lower == "instrtext" and elem.text:
            instr_chunks.append(elem.text)
            if _WORD_UNSAFE_FIELD_RE.search(elem.text):
                raise InvalidDocumentError(
                    f"Office archive '{filename}' contains forbidden field instruction in "
                    f"'{entry_name}'",
                    error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
                )

        if local_tag_lower == "fldsimple":
            for attr_k, attr_v in elem.attrib.items():
                if _xml_local_name(attr_k).lower() == "instr" and _WORD_UNSAFE_FIELD_RE.search(
                    attr_v
                ):
                    raise InvalidDocumentError(
                        f"Office archive '{filename}' contains forbidden simple field instruction "
                        f"in '{entry_name}'",
                        error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
                    )

        if local_tag_lower == "f" and elem.text and _CALC_UNSAFE_FORMULA_RE.search(elem.text):
            raise InvalidDocumentError(
                f"Office archive '{filename}' contains forbidden external formula in "
                f"'{entry_name}'",
                error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
            )

        for attr_k, attr_v in elem.attrib.items():
            ak_lower = _xml_local_name(attr_k).lower()
            val_stripped = attr_v.strip()
            val_lower = val_stripped.lower()
            if ak_lower == "formula" and _CALC_UNSAFE_FORMULA_RE.search(val_stripped):
                raise InvalidDocumentError(
                    f"Office archive '{filename}' contains forbidden spreadsheet formula in "
                    f"'{entry_name}'",
                    error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
                )
            if ak_lower == "href":
                if val_lower.startswith(_UNSAFE_TARGET_PREFIXES):
                    raise InvalidDocumentError(
                        f"Office archive '{filename}' contains forbidden href '{val_stripped}' "
                        f"in '{entry_name}'",
                        error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
                    )
                if (
                    local_tag_lower
                    in {
                        "section-source",
                        "table-source",
                        "cell-range-source",
                        "floating-frame",
                        "plugin",
                        "applet",
                    }
                    and val_stripped
                    and not val_stripped.startswith("#")
                ):
                    raise InvalidDocumentError(
                        f"Office archive '{filename}' contains forbidden linked ODF resource "
                        f"'<{local_tag} href={val_stripped!r}>' in '{entry_name}'",
                        error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
                    )
                if local_tag_lower in {
                    "image",
                    "object",
                    "object-ole",
                    "imagedata",
                } and val_lower.startswith(_EXTERNAL_URI_PREFIXES):
                    raise InvalidDocumentError(
                        f"Office archive '{filename}' contains forbidden external media link "
                        f"'<{local_tag} href={val_stripped!r}>' in '{entry_name}'",
                        error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
                    )

    if instr_chunks:
        combined_instr = "".join(instr_chunks)
        if _WORD_UNSAFE_FIELD_RE.search(combined_instr):
            raise InvalidDocumentError(
                f"Office archive '{filename}' contains split forbidden field instruction in "
                f"'{entry_name}'",
                error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
            )


def _inspect_office_zip_container(
    data: bytes,
    filename: str,
    *,
    max_uncompressed_bytes: int,
    max_compression_ratio: float,
    max_entries: int = MAX_OFFICE_ZIP_ENTRIES_DEFAULT,
) -> None:
    """Validate ZIP-based Office archives (.docx/.xlsx/.pptx/.odt/...) against bombs & SSRF."""
    has_pk_magic = data.startswith(b"PK")
    if not has_pk_magic and not zipfile.is_zipfile(io.BytesIO(data)):
        return

    try:
        zf = zipfile.ZipFile(io.BytesIO(data), mode="r")
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise InvalidDocumentError(
            f"Corrupt Office ZIP container '{filename}': {exc}",
            error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        ) from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > max_entries:
            raise InvalidDocumentError(
                f"Office archive '{filename}' contains {len(infos)} entries, exceeding maximum "
                f"allowed entry count ({max_entries})",
                error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
            )

        declared_uncompressed = 0
        for info in infos:
            entry_name = info.filename
            norm_parts = entry_name.replace("\\", "/").split("/")
            if entry_name.startswith(("/", "\\")) or ".." in norm_parts:
                raise InvalidDocumentError(
                    f"Office archive '{filename}' contains unsafe path traversal entry "
                    f"'{entry_name}'",
                    error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
                )
            declared_uncompressed += max(0, int(info.file_size))
            if declared_uncompressed > max_uncompressed_bytes:
                raise DocumentPayloadTooLargeError(
                    f"Office archive '{filename}' declared uncompressed size exceeds maximum "
                    f"allowed limit ({declared_uncompressed} > {max_uncompressed_bytes} bytes)"
                )

        if (
            declared_uncompressed >= _ZIP_RATIO_MIN_UNCOMPRESSED_BYTES
            and (declared_uncompressed / max(len(data), 1)) > max_compression_ratio
        ):
            ratio = declared_uncompressed / max(len(data), 1)
            raise InvalidDocumentError(
                f"Office archive '{filename}' rejected as potential ZIP bomb: compression ratio "
                f"{ratio:.1f}:1 exceeds maximum {max_compression_ratio:.1f}:1",
                error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
            )

        actual_uncompressed = 0
        for info in infos:
            if info.is_dir():
                continue
            lower_name = info.filename.lower()
            is_xml_like = lower_name.endswith((".xml", ".rels"))
            xml_chunks: list[bytes] = []
            try:
                with zf.open(info, mode="r") as member:
                    while True:
                        chunk = member.read(65_536)
                        if not chunk:
                            break
                        actual_uncompressed += len(chunk)
                        if actual_uncompressed > max_uncompressed_bytes:
                            raise DocumentPayloadTooLargeError(
                                f"Office archive '{filename}' stream-decompressed size exceeds "
                                f"maximum allowed limit ({max_uncompressed_bytes} bytes)"
                            )
                        if (
                            actual_uncompressed >= _ZIP_RATIO_MIN_UNCOMPRESSED_BYTES
                            and (actual_uncompressed / max(len(data), 1)) > max_compression_ratio
                        ):
                            ratio = actual_uncompressed / max(len(data), 1)
                            raise InvalidDocumentError(
                                f"Office archive '{filename}' rejected as potential ZIP bomb: "
                                f"stream compression ratio {ratio:.1f}:1 exceeds maximum "
                                f"{max_compression_ratio:.1f}:1",
                                error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
                            )
                        if is_xml_like:
                            xml_chunks.append(chunk)
            except (zipfile.BadZipFile, OSError, EOFError, RuntimeError) as exc:
                raise InvalidDocumentError(
                    f"Failed to read entry '{info.filename}' in Office archive '{filename}': {exc}",
                    error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
                ) from exc

            if is_xml_like and xml_chunks:
                _inspect_xml_entry_security(info.filename, b"".join(xml_chunks), filename)


_ALLOWED_SOFFICE_ENV_KEYS: frozenset[str] = frozenset(
    {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "COMMONPROGRAMFILES",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "DISPLAY",
        "FONTCONFIG_PATH",
        "FONTCONFIG_FILE",
        "LD_LIBRARY_PATH",
    }
)


def _build_sanitized_soffice_env(tmp_dir: Path) -> dict[str, str]:
    """Build a minimal allowlisted environment for headless LibreOffice without server secrets."""
    sanitized: dict[str, str] = {}
    for key, val in os.environ.items():
        if key.upper() in _ALLOWED_SOFFICE_ENV_KEYS:
            sanitized[key] = val
    tmp_str = str(tmp_dir)
    sanitized["TEMP"] = tmp_str
    sanitized["TMP"] = tmp_str
    sanitized["TMPDIR"] = tmp_str
    return sanitized


def _terminate_process_tree(proc: subprocess.Popen[bytes]) -> None:
    """Best-effort termination of soffice launcher and child soffice.bin process tree."""
    pid = getattr(proc, "pid", None)
    if pid is not None:
        if os.name == "nt":
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    check=False,
                    capture_output=True,
                    timeout=5,
                )
        else:
            killpg = getattr(os, "killpg", None)
            sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
            if killpg is not None:
                with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                    killpg(pid, sigkill)
    with contextlib.suppress(Exception):
        proc.kill()
    with contextlib.suppress(Exception):
        proc.communicate(timeout=2)


def find_soffice_binary(custom_path: str | None = None) -> str | None:
    """Locate the LibreOffice `soffice` binary on PATH or standard install directories.

    Args:
        custom_path: Optional explicit binary path provided by the caller or configuration.

    Returns:
        Full path to executable if found and executable, else None.
    """
    if custom_path:
        path_obj = Path(custom_path)
        if path_obj.is_file() and os.access(path_obj, os.X_OK):
            return str(path_obj.resolve())
        # On Windows, is_file is sufficient even if X_OK isn't strictly POSIX
        if path_obj.is_file() and os.name == "nt":
            return str(path_obj.resolve())

    # Check environment variables
    env_soffice = os.environ.get("SOFFICE_PATH") or os.environ.get("LIBREOFFICE_PATH")
    if env_soffice:
        env_path = Path(env_soffice)
        if env_path.is_file():
            return str(env_path.resolve())

    # Check PATH via shutil.which
    for bin_name in ("soffice", "libreoffice", "soffice.exe", "libreoffice.exe"):
        which_path = shutil.which(bin_name)
        if which_path:
            return which_path

    return None


class LibreOfficeDocumentAdapter(BaseDocumentAdapter):
    """Adapter converting Office documents to PDF via headless LibreOffice,
    then parsing via LiteParseDocumentAdapter.
    """

    supported_mime_types: Sequence[str] = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.presentation",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/msword",
        "application/vnd.ms-powerpoint",
        "application/vnd.ms-excel",
    )
    supported_extensions: Sequence[str] = (
        ".docx",
        ".pptx",
        ".odt",
        ".xlsx",
        ".doc",
        ".ppt",
        ".xls",
        ".odp",
        ".ods",
    )

    def __init__(
        self,
        *,
        soffice_path: str | None = None,
        timeout_seconds: float = 60.0,
        pdf_adapter: LiteParseDocumentAdapter | None = None,
        max_input_bytes: int | None = MAX_OFFICE_INPUT_BYTES_DEFAULT,
        max_uncompressed_bytes: int = MAX_OFFICE_UNCOMPRESSED_BYTES_DEFAULT,
        max_compression_ratio: float = MAX_OFFICE_COMPRESSION_RATIO_DEFAULT,
        **liteparse_kwargs: Any,
    ) -> None:
        """Initialize LibreOfficeDocumentAdapter.

        Args:
            soffice_path: Optional explicit path to the `soffice` executable.
            timeout_seconds: Subprocess timeout in seconds for conversion.
            pdf_adapter: Optional pre-configured LiteParseDocumentAdapter instance.
            max_input_bytes: Maximum allowed raw input byte size (50 MiB default).
            max_uncompressed_bytes: Maximum allowed uncompressed archive bytes (200 MiB default).
            max_compression_ratio: Maximum allowed ZIP compression ratio (100:1 default).
            **liteparse_kwargs: Keyword arguments passed to fallback LiteParseDocumentAdapter.
        """
        self.soffice_path = soffice_path
        self.timeout_seconds = timeout_seconds
        self.max_input_bytes = max_input_bytes
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.max_compression_ratio = max_compression_ratio
        self.pdf_adapter = pdf_adapter or LiteParseDocumentAdapter(**liteparse_kwargs)

    @property
    def adapter_name(self) -> str:
        return "libreoffice"

    def is_available(self) -> bool:
        """Check if LibreOffice binary is available on the system."""
        return find_soffice_binary(self.soffice_path) is not None

    def parse_bytes(
        self,
        data: bytes,
        filename: str,
        mime_type: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Convert raw office document bytes to PDF and parse via LiteParseDocumentAdapter.

        Args:
            data: Raw office file bytes (.docx, .pptx, .odt, etc.).
            filename: Original document filename.
            mime_type: Optional explicit MIME media type.
            **kwargs: Extra parameters including source_sha256, document_id, taint.

        Returns:
            Structured IngestionResult from intermediate PDF parsing.

        Raises:
            InvalidDocumentError: If document bytes are empty.
            OfficeConversionUnavailableError: If LibreOffice `soffice` binary is missing (HTTP 501).
            DocumentExtractionError: If conversion fails or outputs corrupted intermediate PDF.
        """
        if not data:
            raise InvalidDocumentError(f"Office document '{filename}' is empty (0 bytes)")

        if self.max_input_bytes is not None and len(data) > self.max_input_bytes:
            raise DocumentPayloadTooLargeError(
                f"Office document '{filename}' exceeds maximum allowed input size "
                f"({len(data)} > {self.max_input_bytes} bytes)"
            )

        actual_sha256 = hashlib.sha256(data).hexdigest()
        caller_sha256 = kwargs.pop("source_sha256", None)
        if caller_sha256 and caller_sha256 != actual_sha256:
            raise DocumentExtractionError(
                f"Supplied source_sha256 '{caller_sha256}' does not match computed "
                f"digest '{actual_sha256}' for '{filename}'"
            )

        _inspect_office_zip_container(
            data,
            filename,
            max_uncompressed_bytes=self.max_uncompressed_bytes,
            max_compression_ratio=self.max_compression_ratio,
        )

        soffice_bin = find_soffice_binary(self.soffice_path)
        if not soffice_bin:
            raise OfficeConversionUnavailableError(
                "OFFICE_CONVERSION_UNAVAILABLE",
                details={"filename": filename, "requested": mime_type or filename},
            )

        ext = Path(filename).suffix.lower()
        default_mime = _OFFICE_EXTENSION_MIME_MAP.get(ext, "application/octet-stream")
        resolved_mime = mime_type or default_mime

        doc_id = str(kwargs.get("document_id") or f"doc_{actual_sha256[:16]}")
        taint_raw = kwargs.get("taint", TaintClassification.INTERNAL)
        taint = TaintClassification(taint_raw) if taint_raw else TaintClassification.INTERNAL

        # Execute headless conversion in an isolated temporary profile; ignore_cleanup_errors
        # ensures lingering OS file handles on timeout cannot mask DocumentExtractionError (MAJ-08)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            profile_dir = tmp_dir / "profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            _write_hardened_libreoffice_profile(profile_dir)
            profile_uri = profile_dir.as_uri()

            out_dir = tmp_dir / "out"
            out_dir.mkdir(parents=True, exist_ok=True)

            safe_stem = re.sub(r"[^\w\-.]", "_", Path(filename).stem) or "document"
            safe_filename = f"{safe_stem}_{secrets.token_hex(4)}{ext}"
            input_path = tmp_dir / safe_filename
            input_path.write_bytes(data)

            cmd = [
                soffice_bin,
                "--headless",
                "--norestore",
                "--nolockcheck",
                "--nodefault",
                "--nofirststartwizard",
                f"-env:UserInstallation={profile_uri}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(input_path),
            ]

            sanitized_env = _build_sanitized_soffice_env(tmp_dir)
            popen_kwargs: dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "env": sanitized_env,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                popen_kwargs["start_new_session"] = True

            try:
                proc = subprocess.Popen(cmd, **popen_kwargs)
            except (FileNotFoundError, PermissionError) as exc:
                raise OfficeConversionUnavailableError(
                    "OFFICE_CONVERSION_UNAVAILABLE",
                    details={"error": str(exc), "binary": soffice_bin},
                ) from exc

            try:
                _, stderr_bytes = proc.communicate(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                _terminate_process_tree(proc)
                raise DocumentExtractionError(
                    f"LibreOffice conversion timed out after {self.timeout_seconds}s for "
                    f"'{filename}'"
                ) from exc

            if proc.returncode != 0:
                stderr_msg = (stderr_bytes or b"").decode("utf-8", errors="replace").strip()
                raise DocumentExtractionError(
                    f"LibreOffice conversion failed on '{filename}' with exit "
                    f"code {proc.returncode}: {stderr_msg}"
                )

            # Locate the generated PDF
            expected_pdf = out_dir / f"{input_path.stem}.pdf"
            if not expected_pdf.is_file():
                # Search for any PDF generated in out_dir
                generated_pdfs = list(out_dir.glob("*.pdf"))
                if generated_pdfs:
                    expected_pdf = generated_pdfs[0]
                else:
                    raise DocumentExtractionError(
                        f"LibreOffice conversion completed successfully but output PDF was not "
                        f"found in '{out_dir}'"
                    )

            pdf_bytes = expected_pdf.read_bytes()
            if not pdf_bytes:
                raise DocumentExtractionError(
                    f"LibreOffice produced an empty PDF (0 bytes) for '{filename}'"
                )

            # Delegate to LiteParseDocumentAdapter
            # Pass kwargs without source_sha256 so LiteParse parses the intermediate PDF bytes
            liteparse_kwargs = dict(kwargs)
            liteparse_kwargs["document_id"] = doc_id
            liteparse_kwargs["taint"] = taint

            pdf_result = self.pdf_adapter.parse_bytes(
                pdf_bytes,
                filename=f"{Path(filename).stem}.pdf",
                mime_type="application/pdf",
                **liteparse_kwargs,
            )

            # Re-ground chunks, tables, and result to original office provenance (P04::MAJ-01)
            regrounded_chunks = [
                chunk.model_copy(
                    update={
                        "document_id": doc_id,
                        "source_sha256": actual_sha256,
                        "taint": taint,
                    }
                )
                for chunk in pdf_result.chunks
            ]
            regrounded_tables = [
                tbl.model_copy(update={"taint": taint}) if tbl.taint != taint else tbl
                for tbl in pdf_result.tables
            ]

            metadata = dict(pdf_result.metadata)
            metadata.update(
                {
                    "office_converted": True,
                    "conversion_engine": "libreoffice",
                    "original_filename": filename,
                    "original_mime_type": resolved_mime,
                    "intermediate_pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                }
            )

            return IngestionResult(
                document_id=doc_id,
                filename=filename,
                mime_type=resolved_mime,
                total_pages=pdf_result.total_pages,
                total_characters=pdf_result.total_characters,
                chunks=regrounded_chunks,
                page_map=pdf_result.page_map,
                tables=regrounded_tables,
                taint=taint,
                source_sha256=actual_sha256,
                normalized_text=pdf_result.normalized_text,
                metadata=metadata,
            )


__all__ = [
    "LibreOfficeDocumentAdapter",
    "find_soffice_binary",
]
