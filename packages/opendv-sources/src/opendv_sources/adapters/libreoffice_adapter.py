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
import xml.parsers.expat as expat
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


class ArchiveSecurityViolationError(InvalidDocumentError, ValueError):
    """Raised when an Office payload violates magic-byte, bomb, XXE, DDE, or link security."""

    error_code: ErrorCode = ErrorCode.ARCHIVE_SECURITY_VIOLATION
    exit_code: int = 4
    http_status: int = 422

    def __init__(
        self,
        message: str,
        *,
        error_code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            error_code=error_code or ErrorCode.ARCHIVE_SECURITY_VIOLATION,
            details=details,
        )


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
_OFFICE_MIME_EXTENSION_MAP: dict[str, str] = {
    mime: ext for ext, mime in _OFFICE_EXTENSION_MIME_MAP.items()
}
_GENERIC_BINARY_MIMES: frozenset[str] = frozenset(
    {"", "application/octet-stream", "binary/octet-stream"}
)
_ZIP_OFFICE_EXTENSIONS: frozenset[str] = frozenset(
    {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}
)
_OLE_OFFICE_EXTENSIONS: frozenset[str] = frozenset({".doc", ".xls", ".ppt"})
_ZIP_LOCAL_MAGIC_PREFIXES: tuple[bytes, ...] = (b"PK\x03\x04", b"PK\x05\x06")
_OLE_CFB_MAGIC: bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_OLE_CFB_MIN_BYTES: int = 512

_SOFFICE_INFILTER_MAP: dict[str, str] = {
    ".doc": "MS Word 97",
    ".docx": "MS Word 2007 XML",
    ".xls": "MS Excel 97",
    ".xlsx": "Calc MS Excel 2007 XML",
    ".ppt": "MS PowerPoint 97",
    ".pptx": "Impress MS PowerPoint 2007 XML",
    ".odt": "writer8",
    ".ods": "calc8",
    ".odp": "impress8",
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
_ASCII_LB = r"(?<![A-Za-z0-9_])"
_ASCII_RB = r"(?![A-Za-z0-9_])"

_TEXT_DOCTYPE_ENTITY_RE = re.compile(
    rf"<!\s*(?:ENTITY{_ASCII_RB}|DOCTYPE{_ASCII_RB}[^>]*(?:{_ASCII_LB}(?:SYSTEM|PUBLIC){_ASCII_RB}|\[))",
    re.IGNORECASE,
)
_HTML_META_REFRESH_RE = re.compile(
    rf"<\s*meta{_ASCII_RB}[^>]*{_ASCII_LB}http-equiv\s*=\s*[\"']?\s*refresh{_ASCII_RB}[^>]*url\s*=",
    re.IGNORECASE,
)
_HTML_ACTIVE_EXT_ATTR_RE = re.compile(
    r"<\s*(?:img|image|imagedata|iframe|frame|embed|object|applet|script|link|base|video|audio"
    rf"|source|feimage|use|fill|stroke|body|table|td|th){_ASCII_RB}[^>]*"
    rf"{_ASCII_LB}(?:src|href)\s*=\s*[\"']?\s*"
    r"(?:https?://|ftps?://|file:|smb:|jar:|gopher:|ldap:|ldaps:|netdoc:|//|\\\\)",
    re.IGNORECASE,
)
_HTML_ANY_UNSAFE_SCHEME_RE = re.compile(
    rf"{_ASCII_LB}(?:src|href)\s*=\s*[\"']?\s*(?:file:|smb:|jar:|gopher:|ldap:|ldaps:|netdoc:|\\\\)",
    re.IGNORECASE,
)
_HTML_CSS_EXT_URL_RE = re.compile(
    r"url\s*\(\s*[\"']?\s*(?:https?://|ftps?://|file:|smb:|jar:|gopher:|ldap:|ldaps:|netdoc:|//|\\\\)",
    re.IGNORECASE,
)

_OLE_EXT_PATH_START = r"(?:(?:https?|ftps?|file|smb)://|\\\\|[A-Za-z]:[/\\]|/[A-Za-z0-9_.\-]+)"

_OLE_UNSAFE_MARKER_RE = re.compile(
    r"(?:"
    rf"{_ASCII_LB}(?:DDEAUTO|INCLUDEPICTURE|INCLUDETEXT|WEBSERVICE){_ASCII_RB}"
    rf"|{_ASCII_LB}(?:RTD|DDE)\s*\("
    rf"|{_ASCII_LB}(?:table-source|section-source|cell-range-source|dde-source|dde-connection"
    rf"|stdolelink|olelink|externallink|externalbook|subdoc|htmlpubsaveas|attachedtemplate){_ASCII_RB}"
    r"|<!\s*(?:DOCTYPE|ENTITY)(?![A-Za-z0-9_])"
    r"|(?:file|ftp|ftps|smb|jar|gopher|ldap|ldaps|netdoc)://"
    rf"|{_ASCII_LB}file:///"
    r"|\\\\[A-Za-z0-9_.\-]+\\[A-Za-z0-9_$.\-]+"
    rf"|\x13\s*(?:DDEAUTO|DDE|INCLUDEPICTURE|INCLUDETEXT|RD|IMPORT|LINK|AUTOTEXTLIST|DATABASE){_ASCII_RB}"
    rf"|{_ASCII_LB}DDE\s+(?:"
    r"[\"']"
    rf"|{_OLE_EXT_PATH_START}"
    rf"|[A-Za-z0-9_\-]+\.(?:exe|com|bat|cmd|ps1|vbs|js|wsf|msc|dll|scr){_ASCII_RB}"
    r")"
    rf"|{_ASCII_LB}(?:RD|IMPORT|LINK)\s+(?:"
    rf"[\"']\s*{_OLE_EXT_PATH_START}"
    rf"|{_OLE_EXT_PATH_START}"
    rf"|[A-Za-z0-9_]+\.[A-Za-z0-9_.]+\s+[\"']?\s*{_OLE_EXT_PATH_START}"
    r")"
    r")",
    re.IGNORECASE,
)
_OLE_HTTP_URI_RE = re.compile(r"(?:https?|ftps?)://", re.IGNORECASE)
_OLE_LINK_AUTOLOAD_INDICATOR_RE = re.compile(
    rf"(?:\x01https?://|Ole10Native|\x01Ole{_ASCII_RB}|LinkSource|LinkInfo|AutoLoad|TargetMode\s*=\s*[\"']?External)",
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


def _inspect_expat_dtd_and_entities(xml_bytes: bytes, entry_name: str, filename: str = "") -> None:
    """Use Expat to reject external/internal DTDs and <!ENTITY> across UTF-8/UTF-16LE/BE."""
    archive_label = filename or entry_name
    parser = expat.ParserCreate()
    with contextlib.suppress(AttributeError, expat.ExpatError):
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    def _on_start_doctype(
        _doctype_name: str,
        sysid: str | None,
        pubid: str | None,
        has_internal_subset: int | bool,
    ) -> None:
        if sysid or pubid or bool(has_internal_subset):
            raise ArchiveSecurityViolationError(
                f"Office archive '{archive_label}' contains forbidden DTD declaration in "
                f"'{entry_name}'"
            )

    def _on_entity_decl(*_args: Any) -> None:
        raise ArchiveSecurityViolationError(
            f"Office archive '{archive_label}' contains forbidden ENTITY declaration in "
            f"'{entry_name}'"
        )

    parser.StartDoctypeDeclHandler = _on_start_doctype
    parser.EntityDeclHandler = _on_entity_decl

    try:
        parser.Parse(xml_bytes, True)
    except ArchiveSecurityViolationError:
        raise
    except (expat.ExpatError, RecursionError, MemoryError, UnicodeError, ValueError) as exc:
        raise ArchiveSecurityViolationError(
            f"Malformed XML entry '{entry_name}' inside Office archive '{archive_label}': {exc}"
        ) from exc


def _inspect_html_entry_security(entry_name: str, html_bytes: bytes, filename: str) -> None:
    """Scan .html/.htm archive entries across Latin-1 and UTF-16LE/BE for DTD/ENTITY & SSRF."""
    even_len = len(html_bytes) - (len(html_bytes) % 2)
    odd_raw = html_bytes[1:]
    odd_len = len(odd_raw) - (len(odd_raw) % 2)

    views: tuple[tuple[str, str], ...] = (
        ("latin1", html_bytes.decode("latin-1")),
        ("utf16le_even", html_bytes[:even_len].decode("utf-16-le", errors="ignore")),
        ("utf16le_odd", odd_raw[:odd_len].decode("utf-16-le", errors="ignore")),
        ("utf16be_even", html_bytes[:even_len].decode("utf-16-be", errors="ignore")),
        ("utf16be_odd", odd_raw[:odd_len].decode("utf-16-be", errors="ignore")),
    )
    for enc_label, view in views:
        if not view:
            continue
        if _TEXT_DOCTYPE_ENTITY_RE.search(view):
            raise ArchiveSecurityViolationError(
                f"Office archive '{filename}' contains forbidden DTD/ENTITY declaration in "
                f"'{entry_name}' ({enc_label})"
            )
        if (
            _HTML_META_REFRESH_RE.search(view)
            or _HTML_ACTIVE_EXT_ATTR_RE.search(view)
            or _HTML_ANY_UNSAFE_SCHEME_RE.search(view)
            or _HTML_CSS_EXT_URL_RE.search(view)
        ):
            raise ArchiveSecurityViolationError(
                f"Office archive '{filename}' contains forbidden external HTML resource link in "
                f"'{entry_name}' ({enc_label})"
            )


def _inspect_xml_entry_security(entry_name: str, xml_bytes: bytes, filename: str) -> None:
    """Reject XXE declarations, external OOXML/ODF subdocuments/images/OLE, DDE, and WEBSERVICE."""
    if _XML_DOCTYPE_ENTITY_RE.search(xml_bytes):
        raise ArchiveSecurityViolationError(
            f"Office archive '{filename}' contains forbidden DTD/ENTITY declaration in "
            f"'{entry_name}'"
        )
    _inspect_expat_dtd_and_entities(xml_bytes, entry_name, filename)

    try:
        root = ET.fromstring(xml_bytes)
    except ArchiveSecurityViolationError:
        raise
    except (ET.ParseError, RecursionError, MemoryError, ValueError) as exc:
        raise ArchiveSecurityViolationError(
            f"Malformed XML entry '{entry_name}' inside Office archive '{filename}': {exc}"
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
                raise ArchiveSecurityViolationError(
                    f"Office archive '{filename}' contains forbidden external target '{target}' "
                    f"in '{entry_name}'"
                )
            if target_mode.lower() == "external" and (
                rel_type_lower.endswith(_UNSAFE_OOXML_REL_TYPE_SUFFIXES)
                or not rel_type_lower.endswith("/hyperlink")
            ):
                raise ArchiveSecurityViolationError(
                    f"Office archive '{filename}' contains forbidden external relationship "
                    f"type '{rel_type}' -> '{target}' in '{entry_name}'"
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
            raise ArchiveSecurityViolationError(
                f"Office archive '{filename}' contains forbidden external link element "
                f"'<{local_tag}>' in '{entry_name}'"
            )

        if local_tag_lower == "meta":
            http_equiv = ""
            meta_content = ""
            for attr_k, attr_v in elem.attrib.items():
                ak = _xml_local_name(attr_k).lower()
                if ak == "http-equiv":
                    http_equiv = attr_v.strip().lower()
                elif ak == "content":
                    meta_content = attr_v.strip().lower()
            if http_equiv == "refresh" and "url=" in meta_content:
                raise ArchiveSecurityViolationError(
                    f"Office archive '{filename}' contains forbidden meta-refresh redirect in "
                    f"'{entry_name}'"
                )

        if local_tag_lower == "instrtext" and elem.text:
            instr_chunks.append(elem.text)
            if _WORD_UNSAFE_FIELD_RE.search(elem.text):
                raise ArchiveSecurityViolationError(
                    f"Office archive '{filename}' contains forbidden field instruction in "
                    f"'{entry_name}'"
                )

        if local_tag_lower == "fldsimple":
            for attr_k, attr_v in elem.attrib.items():
                if _xml_local_name(attr_k).lower() == "instr" and _WORD_UNSAFE_FIELD_RE.search(
                    attr_v
                ):
                    raise ArchiveSecurityViolationError(
                        f"Office archive '{filename}' contains forbidden simple field instruction "
                        f"in '{entry_name}'"
                    )

        if local_tag_lower == "f" and elem.text and _CALC_UNSAFE_FORMULA_RE.search(elem.text):
            raise ArchiveSecurityViolationError(
                f"Office archive '{filename}' contains forbidden external formula in '{entry_name}'"
            )

        for attr_k, attr_v in elem.attrib.items():
            ak_lower = _xml_local_name(attr_k).lower()
            val_stripped = attr_v.strip()
            val_lower = val_stripped.lower()
            if ak_lower == "formula" and _CALC_UNSAFE_FORMULA_RE.search(val_stripped):
                raise ArchiveSecurityViolationError(
                    f"Office archive '{filename}' contains forbidden spreadsheet formula in "
                    f"'{entry_name}'"
                )
            if ak_lower == "style" and _HTML_CSS_EXT_URL_RE.search(val_stripped):
                raise ArchiveSecurityViolationError(
                    f"Office archive '{filename}' contains forbidden external CSS url() in "
                    f"'{entry_name}'"
                )
            if ak_lower in {"href", "src"}:
                if val_lower.startswith(_UNSAFE_TARGET_PREFIXES):
                    raise ArchiveSecurityViolationError(
                        f"Office archive '{filename}' contains forbidden {ak_lower} "
                        f"'{val_stripped}' in '{entry_name}'"
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
                    raise ArchiveSecurityViolationError(
                        f"Office archive '{filename}' contains forbidden linked ODF resource "
                        f"'<{local_tag} {ak_lower}={val_stripped!r}>' in '{entry_name}'"
                    )
                if local_tag_lower in {
                    "image",
                    "img",
                    "object",
                    "object-ole",
                    "oleobject",
                    "imagedata",
                    "iframe",
                    "frame",
                    "embed",
                    "script",
                    "link",
                    "base",
                    "video",
                    "audio",
                    "source",
                    "feimage",
                    "use",
                    "fill",
                    "stroke",
                } and val_lower.startswith(_EXTERNAL_URI_PREFIXES):
                    raise ArchiveSecurityViolationError(
                        f"Office archive '{filename}' contains forbidden external media link "
                        f"'<{local_tag} {ak_lower}={val_stripped!r}>' in '{entry_name}'"
                    )

    if instr_chunks:
        combined_instr = "".join(instr_chunks)
        if _WORD_UNSAFE_FIELD_RE.search(combined_instr):
            raise ArchiveSecurityViolationError(
                f"Office archive '{filename}' contains split forbidden field instruction in "
                f"'{entry_name}'"
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
    if not data.startswith(_ZIP_LOCAL_MAGIC_PREFIXES):
        raise ArchiveSecurityViolationError(
            f"Office archive '{filename}' lacks required PKZIP local file header signature"
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(data), mode="r")
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise ArchiveSecurityViolationError(
            f"Corrupt Office ZIP container '{filename}': {exc}"
        ) from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > max_entries:
            raise ArchiveSecurityViolationError(
                f"Office archive '{filename}' contains {len(infos)} entries, exceeding maximum "
                f"allowed entry count ({max_entries})"
            )

        declared_uncompressed = 0
        for info in infos:
            entry_name = info.filename
            norm_parts = entry_name.replace("\\", "/").split("/")
            if entry_name.startswith(("/", "\\")) or ".." in norm_parts:
                raise ArchiveSecurityViolationError(
                    f"Office archive '{filename}' contains unsafe path traversal entry "
                    f"'{entry_name}'"
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
            raise ArchiveSecurityViolationError(
                f"Office archive '{filename}' rejected as potential ZIP bomb: compression ratio "
                f"{ratio:.1f}:1 exceeds maximum {max_compression_ratio:.1f}:1"
            )

        actual_uncompressed = 0
        for info in infos:
            if info.is_dir():
                continue
            lower_name = info.filename.lower()
            is_xml_like = lower_name.endswith(
                (".xml", ".rels", ".vml", ".svg", ".xhtml", ".fodt", ".fods", ".fodp")
            )
            is_html_like = lower_name.endswith((".html", ".htm"))
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
                            raise ArchiveSecurityViolationError(
                                f"Office archive '{filename}' rejected as potential ZIP bomb: "
                                f"stream compression ratio {ratio:.1f}:1 exceeds maximum "
                                f"{max_compression_ratio:.1f}:1"
                            )
                        if is_xml_like or is_html_like:
                            xml_chunks.append(chunk)
            except (zipfile.BadZipFile, OSError, EOFError, RuntimeError) as exc:
                raise ArchiveSecurityViolationError(
                    f"Failed to read entry '{info.filename}' in Office archive '{filename}': {exc}"
                ) from exc

            if is_xml_like and xml_chunks:
                _inspect_xml_entry_security(info.filename, b"".join(xml_chunks), filename)
            elif is_html_like and xml_chunks:
                _inspect_html_entry_security(info.filename, b"".join(xml_chunks), filename)


def _inspect_ole_cfb_security(data: bytes, ext: str, filename: str = "") -> None:
    """Validate legacy binary OLE2 CFB Office payloads (.doc/.xls/.ppt) against DDE & SSRF."""
    label = filename or ext
    ext_norm = ext.lower()
    if ext_norm not in _OLE_OFFICE_EXTENSIONS:
        raise ArchiveSecurityViolationError(
            f"Unsupported legacy OLE2 Office extension '{ext}' for '{label}'"
        )
    if not data.startswith(_OLE_CFB_MAGIC):
        raise ArchiveSecurityViolationError(
            f"Legacy Office document '{label}' lacks required OLE2 CFB header signature"
        )
    if len(data) < _OLE_CFB_MIN_BYTES:
        raise ArchiveSecurityViolationError(
            f"Legacy Office document '{label}' has malformed OLE2 CFB header "
            f"({len(data)} < {_OLE_CFB_MIN_BYTES} bytes)"
        )

    payload = data[len(_OLE_CFB_MAGIC) :]
    even_len = len(payload) - (len(payload) % 2)
    odd_raw = payload[1:]
    odd_len = len(odd_raw) - (len(odd_raw) % 2)

    text_views: tuple[tuple[str, str], ...] = (
        ("latin1", payload.decode("latin-1")),
        ("utf16le_even", payload[:even_len].decode("utf-16-le", errors="ignore")),
        ("utf16le_odd", odd_raw[:odd_len].decode("utf-16-le", errors="ignore")),
    )

    for encoding_label, view in text_views:
        if not view:
            continue
        match = _OLE_UNSAFE_MARKER_RE.search(view)
        if match is not None:
            matched_token = match.group(0).strip()
            raise ArchiveSecurityViolationError(
                f"Legacy Office document '{label}' contains forbidden external/DDE/formula "
                f"marker {matched_token!r} ({encoding_label})"
            )
        if _OLE_HTTP_URI_RE.search(view) and _OLE_LINK_AUTOLOAD_INDICATOR_RE.search(view):
            raise ArchiveSecurityViolationError(
                f"Legacy Office document '{label}' contains forbidden external OLE auto-load "
                f"URI link ({encoding_label})"
            )


def _resolve_and_validate_office_extension(filename: str, mime_type: str | None) -> tuple[str, str]:
    """Resolve normalized Office extension + MIME type and enforce extension/MIME agreement."""
    ext = Path(filename).suffix.lower()
    clean_mime = (mime_type or "").split(";", 1)[0].strip().lower()

    if not ext and clean_mime in _OFFICE_MIME_EXTENSION_MAP:
        ext = _OFFICE_MIME_EXTENSION_MAP[clean_mime]

    if ext not in _SOFFICE_INFILTER_MAP:
        raise ArchiveSecurityViolationError(
            f"Unsupported or unrecognized Office extension '{ext or filename}' for '{filename}'"
        )

    if clean_mime and clean_mime not in _GENERIC_BINARY_MIMES:
        expected_ext = _OFFICE_MIME_EXTENSION_MAP.get(clean_mime)
        if expected_ext != ext:
            raise ArchiveSecurityViolationError(
                f"Office MIME type '{mime_type}' disagrees with file extension '{ext}' "
                f"for '{filename}'"
            )

    resolved_mime = (
        clean_mime
        if clean_mime and clean_mime not in _GENERIC_BINARY_MIMES
        else _OFFICE_EXTENSION_MIME_MAP[ext]
    )
    return ext, resolved_mime


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

        ext, resolved_mime = _resolve_and_validate_office_extension(filename, mime_type)
        if ext in _ZIP_OFFICE_EXTENSIONS:
            if not data.startswith(_ZIP_LOCAL_MAGIC_PREFIXES):
                raise ArchiveSecurityViolationError(
                    f"Office document '{filename}' with extension '{ext}' lacks required "
                    f"PKZIP header signature"
                )
            _inspect_office_zip_container(
                data,
                filename,
                max_uncompressed_bytes=self.max_uncompressed_bytes,
                max_compression_ratio=self.max_compression_ratio,
            )
        elif ext in _OLE_OFFICE_EXTENSIONS:
            if not data.startswith(_OLE_CFB_MAGIC):
                raise ArchiveSecurityViolationError(
                    f"Legacy Office document '{filename}' with extension '{ext}' lacks required "
                    f"OLE2 CFB header signature"
                )
            _inspect_ole_cfb_security(data, ext, filename=filename)
        else:
            raise ArchiveSecurityViolationError(
                f"Unsupported Office extension '{ext}' for '{filename}'"
            )

        soffice_bin = find_soffice_binary(self.soffice_path)
        if not soffice_bin:
            raise OfficeConversionUnavailableError(
                "OFFICE_CONVERSION_UNAVAILABLE",
                details={"filename": filename, "requested": mime_type or filename},
            )

        infilter = _SOFFICE_INFILTER_MAP[ext]
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
                f"--infilter={infilter}",
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
    "ArchiveSecurityViolationError",
    "LibreOfficeDocumentAdapter",
    "_inspect_expat_dtd_and_entities",
    "_inspect_ole_cfb_security",
    "find_soffice_binary",
]
