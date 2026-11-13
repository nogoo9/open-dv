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
import posixpath
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
from opendv_sources.adapters.liteparse_adapter import (
    InvalidDocumentError,
    LiteParseDocumentAdapter,
)
from opendv_sources.exceptions import (
    ArchiveSecurityViolationError,
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
MAX_OFFICE_XML_ENTRY_BYTES_DEFAULT: int = 32 * 1024 * 1024
MAX_OFFICE_COMPRESSION_RATIO_DEFAULT: float = 100.0
MAX_OFFICE_ZIP_ENTRIES_DEFAULT: int = 10_000
MAX_NESTED_OFFICE_ZIP_DEPTH: int = 2
_ZIP_RATIO_MIN_UNCOMPRESSED_BYTES: int = 16_384

_XML_MANDATORY_OOXML_REL_SUFFIXES: tuple[str, ...] = (
    "/officedocument",
    "/main",
    "/styles",
    "/settings",
    "/websettings",
    "/fonttable",
    "/footnotes",
    "/endnotes",
    "/header",
    "/footer",
    "/comments",
    "/numbering",
    "/glossarydocument",
    "/worksheet",
    "/dialogsheet",
    "/chartsheet",
    "/sharedstrings",
    "/calcchain",
    "/slide",
    "/slidelayout",
    "/slidemaster",
    "/notesslide",
    "/handoutmaster",
    "/theme",
    "/vmldrawing",
    "/diagramdata",
    "/chart",
    "/customxml",
    "/connections",
    "/querytable",
    "/table",
    "/pivottable",
    "/pivotcachedefinition",
)
_SAFE_BINARY_REL_SUFFIXES: tuple[str, ...] = (
    "/image",
    "/hdphoto",
    "/font",
    "/thumbnail",
    "/audio",
    "/video",
    "/media",
    "/printersettings",
)

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
    "data:image/svg",
)

_SAFE_RASTER_DATA_IMAGE_PREFIXES: tuple[str, ...] = (
    "data:image/png;",
    "data:image/jpeg;",
    "data:image/jpg;",
    "data:image/gif;",
    "data:image/webp;",
    "data:image/bmp;",
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

_PASSIVE_HYPERLINK_TAGS: frozenset[str] = frozenset(
    {"a", "hyperlink", "hlinkclick", "hlinkhover", "hlinkmouse"}
)
_PASSIVE_HYPERLINK_SCHEME_PREFIXES: tuple[str, ...] = (
    "http://",
    "https://",
    "ftp://",
    "ftps://",
)

_WORD_UNSAFE_FIELD_RE = re.compile(
    r"\b(?:DDEAUTO|DDE|INCLUDEPICTURE|INCLUDETEXT|IMPORT|LINK)\b",
    re.IGNORECASE,
)
_CALC_UNSAFE_FORMULA_RE = re.compile(
    r"\b(?:WEBSERVICE|RTD|DDE)\s*\(",
    re.IGNORECASE,
)
_ASCII_LB = r"(?<![A-Za-z0-9_])"
_ASCII_RB = r"(?![A-Za-z0-9_])"

_DOCTYPE_EXT_KW_RE = re.compile(
    rf"{_ASCII_LB}(?:SYSTEM|PUBLIC){_ASCII_RB}",
    re.IGNORECASE,
)
_META_HTTP_EQUIV_REFRESH_RE = re.compile(
    rf"{_ASCII_LB}http-equiv\s*=\s*(?:[\"']\s*)?refresh{_ASCII_RB}",
    re.IGNORECASE,
)
_META_URL_RE = re.compile(r"url\s*=", re.IGNORECASE)
_HTML_TAG_HEAD_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_:\-.]*)([\s\S]*)$")
_HTML_HREF_SRC_ATTR_RE = re.compile(
    r"(?:^|[\s\"'/])(?:[A-Za-z0-9_\-.]+:)?(href|src)\s*=\s*"
    r"(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))",
    re.IGNORECASE,
)
_HTML_ONLOAD_ACTUATE_RE = re.compile(
    rf"(?:^|[\s\"'/])(?:[A-Za-z0-9_\-.]+:)?actuate\s*=\s*(?:[\"']\s*)?onload{_ASCII_RB}",
    re.IGNORECASE,
)
_HTML_ANY_UNSAFE_SCHEME_RE = re.compile(
    rf"{_ASCII_LB}(?:src|href)\s*=\s*(?:[\"']\s*)?"
    r"(?:file:|smb:|jar:|gopher:|ldap:|ldaps:|netdoc:|data:image/svg|//|\\\\)",
    re.IGNORECASE,
)
_HTML_CSS_EXT_URL_RE = re.compile(
    r"url\s*\(\s*(?:[\"']\s*)?"
    r"(?:https?://|ftps?://|file:|smb:|jar:|gopher:|ldap:|ldaps:|netdoc:|data:image/svg|//|\\\\)",
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
    r"|\\\\[A-Za-z0-9_.\-]{1,255}\\[A-Za-z0-9_$.\-]{1,255}"
    r"|\x01\x01@[A-Za-z0-9_.\-]{1,255}\x03[A-Za-z0-9_$.\-]{1,255}"
    rf"|\x13\s*(?:DDEAUTO|DDE|INCLUDEPICTURE|INCLUDETEXT|RD|IMPORT|LINK|AUTOTEXTLIST|DATABASE){_ASCII_RB}"
    rf"|{_ASCII_LB}DDE\s+(?:"
    r"[\"']"
    rf"|{_OLE_EXT_PATH_START}"
    rf"|[A-Za-z0-9_\-]{{1,128}}\.(?:exe|com|bat|cmd|ps1|vbs|js|wsf|msc|dll|scr){_ASCII_RB}"
    r")"
    rf"|{_ASCII_LB}(?:RD|IMPORT|LINK)\s+(?:"
    rf"[\"']\s*{_OLE_EXT_PATH_START}"
    rf"|{_OLE_EXT_PATH_START}"
    rf"|[A-Za-z0-9_]{{1,64}}\.[A-Za-z0-9_.]{{1,64}}\s+(?:[\"']\s*)?{_OLE_EXT_PATH_START}"
    r")"
    r")",
    re.IGNORECASE,
)
_OLE_HTTP_URI_RE = re.compile(r"(?:https?|ftps?)://", re.IGNORECASE)
_OLE_LINK_AUTOLOAD_INDICATOR_RE = re.compile(
    rf"(?:\x01[\x00-\x1f]{{0,8}}(?:https?|ftps?)://|Ole10Native|\x01Ole{_ASCII_RB}|LinkSource|LinkInfo|AutoLoad|TargetMode\s*=\s*[\"']?External)",
    re.IGNORECASE,
)

# LibreOffice officecfg/registry/schema/org/openoffice/Office/{Writer,Calc}.xcs:
# - Writer/Content/Update/UpdateMode: 1 = manual field update only (never automatic)
# - Writer/Content/Update/Link: 0 = Always, 1 = OnRequest, 2 = Never (SwFldUpdateFlags)
# - Calc/Content/Update/Link:   0 = Always, 1 = Never (LM_NEVER), 2 = On request (LM_ON_DEMAND)
# - Calc/Formula/Load/{OOXMLRecalcMode,ODFRecalcMode}: 1 = Never recalculate on load
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
    <prop oor:name="DisableActiveContent" oor:op="fuse">
      <value>true</value>
    </prop>
    <prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse">
      <value>true</value>
    </prop>
  </item>
  <item oor:path="/org.openoffice.Office.Writer/Content/Update">
    <prop oor:name="UpdateMode" oor:op="fuse">
      <value>1</value>
    </prop>
    <prop oor:name="Link" oor:op="fuse">
      <value>2</value>
    </prop>
  </item>
  <item oor:path="/org.openoffice.Office.Calc/Content/Update">
    <prop oor:name="Link" oor:op="fuse">
      <value>1</value>
    </prop>
  </item>
  <item oor:path="/org.openoffice.Office.Calc/Formula/Load">
    <prop oor:name="OOXMLRecalcMode" oor:op="fuse">
      <value>1</value>
    </prop>
    <prop oor:name="ODFRecalcMode" oor:op="fuse">
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


def _norm_zip_part_name(part_name: str) -> str:
    """Normalize an archive part name to a clean POSIX relative path without leading `/`."""
    cleaned = part_name.replace("\\", "/").lstrip("/")
    if not cleaned:
        return ""
    norm = posixpath.normpath(cleaned)
    return "" if norm == "." else norm


def _resolve_opc_rel_target(rels_entry_name: str, target: str) -> str | None:
    """Resolve an internal OPC `.rels` Target relative to the owning part directory."""
    clean_target = target.split("#", 1)[0].split("?", 1)[0].strip()
    if not clean_target:
        return ""
    if clean_target.startswith("\\") or (
        len(clean_target) >= 2 and clean_target[0].isalpha() and clean_target[1] == ":"
    ):
        return None
    clean_posix = clean_target.replace("\\", "/")
    if clean_posix.startswith("/"):
        resolved = posixpath.normpath(clean_posix.lstrip("/"))
    else:
        rels_norm = _norm_zip_part_name(rels_entry_name)
        rels_dir = posixpath.dirname(rels_norm)
        if rels_dir == "_rels":
            base_dir = ""
        elif rels_dir.endswith("/_rels"):
            base_dir = rels_dir[: -len("/_rels")]
        else:
            base_dir = rels_dir
        resolved = posixpath.normpath(
            posixpath.join(base_dir, clean_posix) if base_dir else clean_posix
        )
    if resolved in ("", "."):
        return ""
    if resolved == ".." or resolved.startswith(("../", "/")):
        return None
    return resolved


def _is_unsafe_local_archive_ref(ref_val: str, entry_name: str) -> bool:
    """Return True if a local `href`/`src` escapes the archive root or uses host OS paths."""
    clean_ref = ref_val.split("#", 1)[0].split("?", 1)[0].strip()
    if not clean_ref:
        return False
    ref_lower = clean_ref.lower()
    if ref_lower.startswith(_SAFE_RASTER_DATA_IMAGE_PREFIXES):
        return False
    if clean_ref.startswith(("/", "\\")):
        return True
    if ":" in clean_ref:
        return True
    entry_dir = posixpath.dirname(_norm_zip_part_name(entry_name))
    rel_posix = clean_ref.replace("\\", "/")
    resolved = posixpath.normpath(posixpath.join(entry_dir, rel_posix) if entry_dir else rel_posix)
    return resolved == ".." or resolved.startswith(("../", "/"))


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


def _inspect_html_entry_security(
    entry_name: str,
    html_bytes: bytes,
    filename: str,
    *,
    max_entry_bytes: int = MAX_OFFICE_XML_ENTRY_BYTES_DEFAULT,
) -> None:
    """Linear O(L) scan of .html/.htm archive entries across Latin-1 & UTF-16LE/BE."""
    if len(html_bytes) > max_entry_bytes:
        raise DocumentPayloadTooLargeError(
            f"HTML entry '{entry_name}' in Office archive '{filename}' exceeds maximum allowed "
            f"size ({len(html_bytes)} > {max_entry_bytes} bytes)"
        )

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
        if _HTML_ANY_UNSAFE_SCHEME_RE.search(view) or _HTML_CSS_EXT_URL_RE.search(view):
            raise ArchiveSecurityViolationError(
                f"Office archive '{filename}' contains forbidden external HTML resource link in "
                f"'{entry_name}' ({enc_label})"
            )

        # Split once on `<`: disjoint slices guarantee sum(len(tag_body)) <= len(view) even when
        # no `>` exists anywhere in the entry, eliminating quadratic/cubic `[^>]*` backtracking.
        for seg in view.split("<")[1:]:
            if not seg:
                continue
            gt_idx = seg.find(">")
            tag_body = seg[: min(gt_idx, 8192)] if gt_idx != -1 else seg[:4096]
            if tag_body.startswith("!"):
                decl = tag_body[1:].lstrip()
                decl_upper = decl[:16].upper()
                if decl_upper.startswith("ENTITY") and (
                    len(decl) == 6 or not (decl[6].isalnum() or decl[6] == "_")
                ):
                    raise ArchiveSecurityViolationError(
                        f"Office archive '{filename}' contains forbidden DTD/ENTITY declaration in "
                        f"'{entry_name}' ({enc_label})"
                    )
                if (
                    decl_upper.startswith("DOCTYPE")
                    and (len(decl) == 7 or not (decl[7].isalnum() or decl[7] == "_"))
                    and ("[" in decl or _DOCTYPE_EXT_KW_RE.search(decl))
                ):
                    raise ArchiveSecurityViolationError(
                        f"Office archive '{filename}' contains forbidden DTD/ENTITY "
                        f"declaration in '{entry_name}' ({enc_label})"
                    )
                continue

            tag_stripped = tag_body.lstrip()
            head_match = _HTML_TAG_HEAD_RE.match(tag_stripped)
            if head_match is None:
                continue
            tag_raw, attrs_blob = head_match.group(1), head_match.group(2)
            local_tag_lower = tag_raw.rsplit(":", 1)[-1].lower()
            if (
                local_tag_lower == "meta"
                and _META_HTTP_EQUIV_REFRESH_RE.search(attrs_blob)
                and _META_URL_RE.search(attrs_blob)
            ):
                raise ArchiveSecurityViolationError(
                    f"Office archive '{filename}' contains forbidden external HTML resource link "
                    f"in '{entry_name}' ({enc_label})"
                )

            has_onload_actuate = bool(_HTML_ONLOAD_ACTUATE_RE.search(attrs_blob))
            for attr_match in _HTML_HREF_SRC_ATTR_RE.finditer(attrs_blob):
                attr_kind = attr_match.group(1).lower()
                raw_val = (
                    attr_match.group(2)
                    if attr_match.group(2) is not None
                    else attr_match.group(3)
                    if attr_match.group(3) is not None
                    else (attr_match.group(4) or "")
                )
                val_stripped = raw_val.strip()
                val_lower = val_stripped.lower()
                if val_lower.startswith(_UNSAFE_TARGET_PREFIXES) or val_lower.startswith("//"):
                    raise ArchiveSecurityViolationError(
                        f"Office archive '{filename}' contains forbidden {attr_kind} "
                        f"'{val_stripped}' in '{entry_name}' ({enc_label})"
                    )
                is_passive_anchor = (
                    attr_kind == "href" and local_tag_lower == "a" and not has_onload_actuate
                )
                if val_lower.startswith(_EXTERNAL_URI_PREFIXES):
                    if not (
                        is_passive_anchor
                        and val_lower.startswith(_PASSIVE_HYPERLINK_SCHEME_PREFIXES)
                    ):
                        raise ArchiveSecurityViolationError(
                            f"Office archive '{filename}' contains forbidden external HTML "
                            f"resource link '<{tag_raw} {attr_kind}={val_stripped!r}>' in "
                            f"'{entry_name}' ({enc_label})"
                        )
                elif not is_passive_anchor and _is_unsafe_local_archive_ref(
                    val_stripped, entry_name
                ):
                    raise ArchiveSecurityViolationError(
                        f"Office archive '{filename}' contains local path escape {attr_kind} "
                        f"'{val_stripped}' in '{entry_name}' ({enc_label})"
                    )


def _inspect_xml_entry_security(
    entry_name: str,
    xml_bytes: bytes,
    filename: str,
    *,
    force_rels: bool = False,
    max_entry_bytes: int = MAX_OFFICE_XML_ENTRY_BYTES_DEFAULT,
) -> list[tuple[str, str]]:
    """Reject XXE declarations, external OOXML/ODF resources, DDE, and WEBSERVICE in O(N) time."""
    if len(xml_bytes) > max_entry_bytes:
        raise DocumentPayloadTooLargeError(
            f"XML entry '{entry_name}' in Office archive '{filename}' exceeds maximum allowed "
            f"size ({len(xml_bytes)} > {max_entry_bytes} bytes)"
        )

    _inspect_expat_dtd_and_entities(xml_bytes, entry_name, filename)

    is_rels = force_rels or entry_name.lower().endswith(".rels")
    internal_rel_targets: list[tuple[str, str]] = []
    instr_chunks: list[str] = []

    def _flush_instr_chunks() -> None:
        if not instr_chunks:
            return
        combined_instr = "".join(instr_chunks)
        instr_chunks.clear()
        if _WORD_UNSAFE_FIELD_RE.search(combined_instr):
            raise ArchiveSecurityViolationError(
                f"Office archive '{filename}' contains split forbidden field instruction in "
                f"'{entry_name}'"
            )

    try:
        for event, elem in ET.iterparse(io.BytesIO(xml_bytes), events=("start", "end")):
            local_tag = _xml_local_name(elem.tag)
            local_tag_lower = local_tag.lower()

            if event == "start":
                if local_tag_lower == "relationships":
                    is_rels = True
                elif local_tag_lower == "fldchar":
                    for attr_k, attr_v in elem.attrib.items():
                        if _xml_local_name(
                            attr_k
                        ).lower() == "fldchartype" and attr_v.strip().lower() in (
                            "begin",
                            "end",
                        ):
                            _flush_instr_chunks()
                continue

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
                if target_lower.startswith(_UNSAFE_TARGET_PREFIXES) or target_lower.startswith(
                    "//"
                ):
                    raise ArchiveSecurityViolationError(
                        f"Office archive '{filename}' contains forbidden external target "
                        f"'{target}' in '{entry_name}'"
                    )
                if target_mode.lower() == "external":
                    if rel_type_lower.endswith(
                        _UNSAFE_OOXML_REL_TYPE_SUFFIXES
                    ) or not rel_type_lower.endswith("/hyperlink"):
                        raise ArchiveSecurityViolationError(
                            f"Office archive '{filename}' contains forbidden external relationship "
                            f"type '{rel_type}' -> '{target}' in '{entry_name}'"
                        )
                else:
                    if target_lower.startswith(_EXTERNAL_URI_PREFIXES):
                        raise ArchiveSecurityViolationError(
                            f"Office archive '{filename}' contains external URI '{target}' without "
                            f"TargetMode='External' in '{entry_name}'"
                        )
                    resolved_internal = _resolve_opc_rel_target(entry_name, target)
                    if resolved_internal is None:
                        raise ArchiveSecurityViolationError(
                            f"Office archive '{filename}' contains path-escaping relationship "
                            f"target '{target}' in '{entry_name}'"
                        )
                    if resolved_internal:
                        internal_rel_targets.append((rel_type_lower, resolved_internal))

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
                            f"Office archive '{filename}' contains forbidden simple field "
                            f"instruction in '{entry_name}'"
                        )

            if local_tag_lower == "f" and elem.text and _CALC_UNSAFE_FORMULA_RE.search(elem.text):
                raise ArchiveSecurityViolationError(
                    f"Office archive '{filename}' contains forbidden external formula in "
                    f"'{entry_name}'"
                )

            if local_tag_lower == "style" and elem.text and _HTML_CSS_EXT_URL_RE.search(elem.text):
                raise ArchiveSecurityViolationError(
                    f"Office archive '{filename}' contains forbidden external CSS url() in "
                    f"'{entry_name}'"
                )

            has_onload_actuate = any(
                _xml_local_name(k).lower() == "actuate" and v.strip().lower() == "onload"
                for k, v in elem.attrib.items()
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
                    if val_lower.startswith(_UNSAFE_TARGET_PREFIXES) or val_lower.startswith("//"):
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
                    is_passive_hyperlink = (
                        ak_lower == "href"
                        and local_tag_lower in _PASSIVE_HYPERLINK_TAGS
                        and not has_onload_actuate
                    )
                    if val_lower.startswith(_EXTERNAL_URI_PREFIXES):
                        if not (
                            is_passive_hyperlink
                            and val_lower.startswith(_PASSIVE_HYPERLINK_SCHEME_PREFIXES)
                        ):
                            raise ArchiveSecurityViolationError(
                                f"Office archive '{filename}' contains forbidden external resource "
                                f"link '<{local_tag} {ak_lower}={val_stripped!r}>' "
                                f"in '{entry_name}'"
                            )
                    elif not is_passive_hyperlink and _is_unsafe_local_archive_ref(
                        val_stripped, entry_name
                    ):
                        raise ArchiveSecurityViolationError(
                            f"Office archive '{filename}' contains local path escape "
                            f"'<{local_tag} {ak_lower}={val_stripped!r}>' in '{entry_name}'"
                        )

            # Release child subtrees in O(depth) RSS once inspected while keeping elem.tail intact.
            del elem[:]
    except ArchiveSecurityViolationError:
        raise
    except (ET.ParseError, RecursionError, MemoryError, ValueError) as exc:
        raise ArchiveSecurityViolationError(
            f"Malformed XML entry '{entry_name}' inside Office archive '{filename}': {exc}"
        ) from exc

    _flush_instr_chunks()
    return internal_rel_targets


_UTF32LE_WS_UNITS: frozenset[bytes] = frozenset(
    {b" \x00\x00\x00", b"\t\x00\x00\x00", b"\r\x00\x00\x00", b"\n\x00\x00\x00", b"\xff\xfe\x00\x00"}
)
_UTF32BE_WS_UNITS: frozenset[bytes] = frozenset(
    {b"\x00\x00\x00 ", b"\x00\x00\x00\t", b"\x00\x00\x00\r", b"\x00\x00\x00\n", b"\x00\x00\xfe\xff"}
)
_UTF16LE_WS_UNITS: frozenset[bytes] = frozenset(
    {b" \x00", b"\t\x00", b"\r\x00", b"\n\x00", b"\xff\xfe"}
)
_UTF16BE_WS_UNITS: frozenset[bytes] = frozenset(
    {b"\x00 ", b"\x00\t", b"\x00\r", b"\x00\n", b"\xfe\xff"}
)


def _strip_code_unit_ws(raw: bytes, ws_units: frozenset[bytes], unit_size: int) -> bytes:
    """Strip leading encoded whitespace/BOM code units while preserving multi-byte alignment."""
    idx = 0
    limit = len(raw) - (len(raw) % unit_size)
    while idx < limit and raw[idx : idx + unit_size] in ws_units:
        idx += unit_size
    return raw[idx:]


def _trim_chunk_leading_ws(lead_bom: bytes, chunk: bytes) -> bytes:
    """Return `chunk` stripped of leading whitespace according to `lead_bom` (or multi-encoding)."""
    if lead_bom == b"\xff\xfe\x00\x00":
        return _strip_code_unit_ws(chunk, _UTF32LE_WS_UNITS, 4)
    if lead_bom == b"\x00\x00\xfe\xff":
        return _strip_code_unit_ws(chunk, _UTF32BE_WS_UNITS, 4)
    if lead_bom == b"\xff\xfe":
        return _strip_code_unit_ws(chunk, _UTF16LE_WS_UNITS, 2)
    if lead_bom == b"\xfe\xff":
        return _strip_code_unit_ws(chunk, _UTF16BE_WS_UNITS, 2)
    if lead_bom == b"\xef\xbb\xbf":
        base = chunk[3:] if chunk.startswith(b"\xef\xbb\xbf") else chunk
        return base.lstrip(b" \t\r\n")

    # No BOM seen: if chunk is pure whitespace in ASCII, UTF-16LE/BE, or UTF-32LE/BE, skip it
    ascii_rem = chunk.lstrip(b" \t\r\n")
    if not ascii_rem:
        return b""
    for unit_sz, ws_set in (
        (4, _UTF32LE_WS_UNITS),
        (4, _UTF32BE_WS_UNITS),
        (2, _UTF16LE_WS_UNITS),
        (2, _UTF16BE_WS_UNITS),
    ):
        if len(chunk) >= unit_sz:
            rem_u = _strip_code_unit_ws(chunk, ws_set, unit_sz)
            if len(rem_u) < len(chunk):
                return rem_u
    return ascii_rem


def _finalize_entry_sniff(
    *,
    filename: str,
    entry_filename: str,
    lead_bom: bytes,
    sniff_accum: bytearray,
    should_buffer: bool,
    skipped_ws_chunks: bool,
    pending_chunks: list[bytes],
    entry_chunks: list[bytes],
) -> tuple[str | None, bool]:
    """Classify accumulated leading entry bytes and flush buffered chunks when needed."""
    if not sniff_accum and not lead_bom:
        pending_chunks.clear()
        return None, should_buffer
    sniffed_kind = _sniff_markup_or_container_kind(lead_bom + bytes(sniff_accum))
    if sniffed_kind == "rtf":
        raise ArchiveSecurityViolationError(
            f"Office archive '{filename}' contains forbidden embedded RTF part '{entry_filename}'"
        )
    if not should_buffer and sniffed_kind in ("xml", "html", "ole", "zip"):
        should_buffer = True
        if lead_bom and (
            skipped_ws_chunks or not pending_chunks or not pending_chunks[0].startswith(lead_bom)
        ):
            entry_chunks.append(lead_bom)
        entry_chunks.extend(pending_chunks)
    pending_chunks.clear()
    return sniffed_kind, should_buffer


def _sniff_markup_or_container_kind(head: bytes) -> str | None:
    """Classify an entry's leading bytes (`xml`, `html`, `ole`, `zip`, `rtf`, or None)."""
    if not head:
        return None
    if head.startswith(_OLE_CFB_MAGIC):
        return "ole"
    if head.startswith(_ZIP_LOCAL_MAGIC_PREFIXES):
        return "zip"

    lead_trim = head[3:] if head.startswith(b"\xef\xbb\xbf") else head
    lead_lstrip = lead_trim.lstrip(b" \t\r\n")
    if lead_lstrip[:5].lower() == b"{\\rtf":
        return "rtf"

    decoded = ""
    if head.startswith(b"\xef\xbb\xbf"):
        decoded = lead_lstrip[:512].decode("latin-1")
    elif head.startswith(b"\xff\xfe\x00\x00"):
        rem = _strip_code_unit_ws(head[4:], _UTF32LE_WS_UNITS, 4)
        decoded = rem[: min(2048, len(rem) - (len(rem) % 4))].decode("utf-32-le", errors="ignore")
    elif head.startswith(b"\x00\x00\xfe\xff"):
        rem = _strip_code_unit_ws(head[4:], _UTF32BE_WS_UNITS, 4)
        decoded = rem[: min(2048, len(rem) - (len(rem) % 4))].decode("utf-32-be", errors="ignore")
    elif head.startswith(b"\xff\xfe"):
        rem = _strip_code_unit_ws(head[2:], _UTF16LE_WS_UNITS, 2)
        decoded = rem[: min(2048, len(rem) - (len(rem) % 2))].decode("utf-16-le", errors="ignore")
    elif head.startswith(b"\xfe\xff"):
        rem = _strip_code_unit_ws(head[2:], _UTF16BE_WS_UNITS, 2)
        decoded = rem[: min(2048, len(rem) - (len(rem) % 2))].decode("utf-16-be", errors="ignore")
    else:
        rem16le = _strip_code_unit_ws(head, _UTF16LE_WS_UNITS, 2)
        rem16be = _strip_code_unit_ws(head, _UTF16BE_WS_UNITS, 2)
        if (
            len(rem16le) >= 4
            and rem16le[1] == 0
            and rem16le[3] == 0
            and rem16le[0] in (ord("<"), ord("{"))
        ):
            decoded = rem16le[: min(2048, len(rem16le) - (len(rem16le) % 2))].decode(
                "utf-16-le", errors="ignore"
            )
        elif (
            len(rem16be) >= 4
            and rem16be[0] == 0
            and rem16be[2] == 0
            and rem16be[1] in (ord("<"), ord("{"))
        ):
            decoded = rem16be[: min(2048, len(rem16be) - (len(rem16be) % 2))].decode(
                "utf-16-be", errors="ignore"
            )
        else:
            sample = lead_lstrip[:64]
            if (
                sample
                and b"\x00" not in sample
                and all(b >= 32 or b in (9, 10, 13) for b in sample)
            ):
                decoded = lead_lstrip[:512].decode("latin-1")

    stripped = decoded.lstrip(" \t\r\n\ufeff")
    if stripped[:5].lower() == "{\\rtf":
        return "rtf"
    if (
        len(stripped) >= 2
        and stripped[0] == "<"
        and (stripped[1].isalpha() or stripped[1] in ("?", "!", "/"))
    ):
        low = stripped[:256].lower()
        if (low.startswith("<!doctype html") or low.startswith("<html")) and "xmlns" not in low:
            return "html"
        return "xml"
    return None


def _classify_content_type(ct: str) -> str | None:
    """Classify an OPC `[Content_Types].xml` or ODF manifest media type (`xml` or `html`)."""
    ct_low = ct.split(";", 1)[0].strip().lower()
    if not ct_low:
        return None
    if ct_low in ("text/html", "message/rfc822", "application/x-mimearchive"):
        return "html"
    if ct_low.endswith("+xml") or ct_low in ("application/xml", "text/xml") or "/xml" in ct_low:
        return "xml"
    return None


def _parse_opc_content_types(xml_bytes: bytes) -> tuple[dict[str, str], dict[str, str], set[str]]:
    """Extract Default extension map, Override part map, and `.rels` parts from Content_Types."""
    default_ext: dict[str, str] = {}
    override_part: dict[str, str] = {}
    rels_parts: set[str] = set()
    root = ET.fromstring(xml_bytes)
    for elem in root.iter():
        ltag = _xml_local_name(elem.tag).lower()
        if ltag == "default":
            ext_val = ""
            ct_val = ""
            for k, v in elem.attrib.items():
                ak = _xml_local_name(k).lower()
                if ak == "extension":
                    ext_val = v.strip().lstrip(".").lower()
                elif ak == "contenttype":
                    ct_val = v.strip().lower()
            if ext_val and ct_val:
                default_ext[ext_val] = ct_val
        elif ltag == "override":
            part_val = ""
            ct_val = ""
            for k, v in elem.attrib.items():
                ak = _xml_local_name(k).lower()
                if ak == "partname":
                    part_val = _norm_zip_part_name(v.strip())
                elif ak == "contenttype":
                    ct_val = v.strip().lower()
            if part_val and ct_val:
                part_low = part_val.lower()
                override_part[part_low] = ct_val
                if "relationships+xml" in ct_val:
                    rels_parts.add(part_low)
    return default_ext, override_part, rels_parts


def _parse_odf_manifest_types(xml_bytes: bytes) -> dict[str, str]:
    """Extract lowercase part-path -> media-type mappings from ODF `META-INF/manifest.xml`."""
    manifest_map: dict[str, str] = {}
    root = ET.fromstring(xml_bytes)
    for elem in root.iter():
        if _xml_local_name(elem.tag).lower() != "file-entry":
            continue
        full_path = ""
        media_type = ""
        for k, v in elem.attrib.items():
            ak = _xml_local_name(k).lower()
            if ak == "full-path":
                full_path = _norm_zip_part_name(v.strip()).lower()
            elif ak == "media-type":
                media_type = v.strip().lower()
        if full_path and media_type:
            manifest_map[full_path] = media_type
    return manifest_map


def _inspect_office_zip_container(
    data: bytes,
    filename: str,
    *,
    max_uncompressed_bytes: int,
    max_compression_ratio: float,
    max_entries: int = MAX_OFFICE_ZIP_ENTRIES_DEFAULT,
    _depth: int = 0,
) -> None:
    """Validate ZIP-based Office archives (.docx/.xlsx/.pptx/.odt/...) against bombs & SSRF."""
    if _depth > MAX_NESTED_OFFICE_ZIP_DEPTH:
        raise ArchiveSecurityViolationError(
            f"Office archive '{filename}' exceeds maximum nested package depth "
            f"({MAX_NESTED_OFFICE_ZIP_DEPTH})"
        )
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

    max_xml_entry_bytes = min(max_uncompressed_bytes, MAX_OFFICE_XML_ENTRY_BYTES_DEFAULT)

    with zf:
        infos = zf.infolist()
        if len(infos) > max_entries:
            raise ArchiveSecurityViolationError(
                f"Office archive '{filename}' contains {len(infos)} entries, exceeding maximum "
                f"allowed entry count ({max_entries})"
            )

        declared_uncompressed = 0
        info_by_norm: dict[str, zipfile.ZipInfo] = {}
        info_by_lower: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            entry_name = info.filename
            norm_parts = entry_name.replace("\\", "/").split("/")
            if (
                entry_name.startswith(("/", "\\"))
                or ".." in norm_parts
                or (len(entry_name) >= 2 and entry_name[0].isalpha() and entry_name[1] == ":")
            ):
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
            if not info.is_dir():
                norm_key = _norm_zip_part_name(entry_name)
                if norm_key:
                    norm_low = norm_key.lower()
                    if norm_low in info_by_lower:
                        raise ArchiveSecurityViolationError(
                            f"Office archive '{filename}' contains duplicate or case-colliding "
                            f"entry '{entry_name}'"
                        )
                    info_by_norm[norm_key] = info
                    info_by_lower[norm_low] = info

        if (
            declared_uncompressed >= _ZIP_RATIO_MIN_UNCOMPRESSED_BYTES
            and (declared_uncompressed / max(len(data), 1)) > max_compression_ratio
        ):
            ratio = declared_uncompressed / max(len(data), 1)
            raise ArchiveSecurityViolationError(
                f"Office archive '{filename}' rejected as potential ZIP bomb: compression ratio "
                f"{ratio:.1f}:1 exceeds maximum {max_compression_ratio:.1f}:1"
            )

        def _read_bounded_entry_bytes(target_info: zipfile.ZipInfo, cap_bytes: int) -> bytes:
            chunks: list[bytes] = []
            total = 0
            try:
                with zf.open(target_info, mode="r") as member:
                    while True:
                        blk = member.read(65_536)
                        if not blk:
                            break
                        total += len(blk)
                        if total > cap_bytes:
                            raise DocumentPayloadTooLargeError(
                                f"Entry '{target_info.filename}' in Office archive '{filename}' "
                                f"exceeds maximum allowed size ({total} > {cap_bytes} bytes)"
                            )
                        if (
                            total >= _ZIP_RATIO_MIN_UNCOMPRESSED_BYTES
                            and (total / max(len(data), 1)) > max_compression_ratio
                        ):
                            ratio = total / max(len(data), 1)
                            raise ArchiveSecurityViolationError(
                                f"Office archive '{filename}' rejected as potential ZIP bomb: "
                                f"stream compression ratio {ratio:.1f}:1 exceeds maximum "
                                f"{max_compression_ratio:.1f}:1"
                            )
                        chunks.append(blk)
            except (zipfile.BadZipFile, OSError, EOFError, RuntimeError) as exc:
                raise ArchiveSecurityViolationError(
                    f"Failed to read entry '{target_info.filename}' in Office archive "
                    f"'{filename}': {exc}"
                ) from exc
            return b"".join(chunks)

        # Pre-parse [Content_Types].xml and META-INF/manifest.xml so non-.xml parts are mapped
        ct_default_ext: dict[str, str] = {}
        ct_override_part: dict[str, str] = {}
        ct_rels_parts: set[str] = set()
        odf_manifest_part: dict[str, str] = {}
        inspected_xml_entries: set[str] = set()
        inspected_any_entries: set[str] = set()

        ct_info = info_by_lower.get("[content_types].xml")
        if ct_info is not None:
            ct_bytes = _read_bounded_entry_bytes(ct_info, max_xml_entry_bytes)
            _inspect_xml_entry_security(
                ct_info.filename, ct_bytes, filename, max_entry_bytes=max_xml_entry_bytes
            )
            ct_default_ext, ct_override_part, ct_rels_parts = _parse_opc_content_types(ct_bytes)
            ct_norm = _norm_zip_part_name(ct_info.filename)
            inspected_xml_entries.add(ct_norm)
            inspected_any_entries.add(ct_norm)

        odf_manifest_info = info_by_lower.get("meta-inf/manifest.xml")
        if odf_manifest_info is not None:
            mf_bytes = _read_bounded_entry_bytes(odf_manifest_info, max_xml_entry_bytes)
            _inspect_xml_entry_security(
                odf_manifest_info.filename, mf_bytes, filename, max_entry_bytes=max_xml_entry_bytes
            )
            odf_manifest_part = _parse_odf_manifest_types(mf_bytes)
            mf_norm = _norm_zip_part_name(odf_manifest_info.filename)
            inspected_xml_entries.add(mf_norm)
            inspected_any_entries.add(mf_norm)

        actual_uncompressed = 0
        sniffed_kind_by_norm: dict[str, str | None] = {}
        internal_rel_targets: list[tuple[str, str, str]] = []

        for info in infos:
            if info.is_dir():
                continue
            norm_name = _norm_zip_part_name(info.filename)
            lower_name = norm_name.lower()
            ext_no_dot = lower_name.rsplit(".", 1)[-1] if "." in lower_name else ""

            mapped_ct = (
                ct_override_part.get(lower_name)
                or odf_manifest_part.get(lower_name)
                or ct_default_ext.get(ext_no_dot, "")
            )
            ct_kind = _classify_content_type(mapped_ct)
            is_rels_part = (
                lower_name.endswith(".rels")
                or lower_name in ct_rels_parts
                or "relationships+xml" in mapped_ct
            )
            is_ext_xml = lower_name.endswith(
                (".xml", ".rels", ".vml", ".svg", ".xhtml", ".fodt", ".fods", ".fodp")
            )
            is_ext_html = lower_name.endswith((".html", ".htm", ".mht", ".mhtml"))

            known_text = bool(
                is_ext_xml or is_ext_html or is_rels_part or ct_kind in ("xml", "html")
            )
            entry_chunks: list[bytes] = []
            entry_bytes_len = 0
            sniffed_kind: str | None = None
            should_buffer = known_text

            try:
                with zf.open(info, mode="r") as member:
                    awaiting_sniff = True
                    first_raw_chunk = True
                    lead_bom = b""
                    skipped_ws_chunks = False
                    sniff_accum = bytearray()
                    pending_chunks: list[bytes] = []

                    while True:
                        chunk = member.read(65_536)
                        if not chunk:
                            if awaiting_sniff:
                                awaiting_sniff = False
                                sniffed_kind, should_buffer = _finalize_entry_sniff(
                                    filename=filename,
                                    entry_filename=info.filename,
                                    lead_bom=lead_bom,
                                    sniff_accum=sniff_accum,
                                    should_buffer=should_buffer,
                                    skipped_ws_chunks=skipped_ws_chunks,
                                    pending_chunks=pending_chunks,
                                    entry_chunks=entry_chunks,
                                )
                            break
                        actual_uncompressed += len(chunk)
                        entry_bytes_len += len(chunk)
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
                        if awaiting_sniff:
                            if first_raw_chunk:
                                first_raw_chunk = False
                                if chunk.startswith(_OLE_CFB_MAGIC) or chunk.startswith(
                                    _ZIP_LOCAL_MAGIC_PREFIXES
                                ):
                                    sniff_accum.extend(chunk[:64])
                                    if not known_text:
                                        pending_chunks.append(chunk)
                                    awaiting_sniff = False
                                    sniffed_kind, should_buffer = _finalize_entry_sniff(
                                        filename=filename,
                                        entry_filename=info.filename,
                                        lead_bom=lead_bom,
                                        sniff_accum=sniff_accum,
                                        should_buffer=should_buffer,
                                        skipped_ws_chunks=skipped_ws_chunks,
                                        pending_chunks=pending_chunks,
                                        entry_chunks=entry_chunks,
                                    )
                                    if known_text:
                                        entry_chunks.append(chunk)
                                    continue
                                for bom_sig in (
                                    b"\xef\xbb\xbf",
                                    b"\xff\xfe\x00\x00",
                                    b"\x00\x00\xfe\xff",
                                    b"\xff\xfe",
                                    b"\xfe\xff",
                                ):
                                    if chunk.startswith(bom_sig):
                                        lead_bom = bom_sig
                                        break
                            if not sniff_accum:
                                non_ws = _trim_chunk_leading_ws(lead_bom, chunk)
                                if not non_ws:
                                    skipped_ws_chunks = True
                                    if should_buffer:
                                        if entry_bytes_len > max_xml_entry_bytes:
                                            raise DocumentPayloadTooLargeError(
                                                f"Markup entry '{info.filename}' in Office archive "
                                                f"'{filename}' exceeds maximum allowed entry size "
                                                f"({entry_bytes_len} > {max_xml_entry_bytes} bytes)"
                                            )
                                        entry_chunks.append(chunk)
                                    continue
                                sniff_accum.extend(non_ws[:2048])
                            else:
                                needed = max(0, 2048 - len(sniff_accum))
                                if needed:
                                    sniff_accum.extend(chunk[:needed])
                            if not should_buffer:
                                pending_chunks.append(chunk)
                            if len(sniff_accum) >= 512:
                                awaiting_sniff = False
                                sniffed_kind, should_buffer = _finalize_entry_sniff(
                                    filename=filename,
                                    entry_filename=info.filename,
                                    lead_bom=lead_bom,
                                    sniff_accum=sniff_accum,
                                    should_buffer=should_buffer,
                                    skipped_ws_chunks=skipped_ws_chunks,
                                    pending_chunks=pending_chunks,
                                    entry_chunks=entry_chunks,
                                )
                                if not should_buffer:
                                    continue
                                if not known_text:
                                    is_markup_buf_init = sniffed_kind in ("xml", "html")
                                    if is_markup_buf_init and entry_bytes_len > max_xml_entry_bytes:
                                        raise DocumentPayloadTooLargeError(
                                            f"Markup entry '{info.filename}' in Office archive "
                                            f"'{filename}' exceeds maximum allowed entry size "
                                            f"({entry_bytes_len} > {max_xml_entry_bytes} bytes)"
                                        )
                                    continue
                            elif not should_buffer:
                                continue
                        if should_buffer:
                            is_markup_buf = bool(known_text or sniffed_kind in ("xml", "html"))
                            if is_markup_buf and entry_bytes_len > max_xml_entry_bytes:
                                raise DocumentPayloadTooLargeError(
                                    f"Markup entry '{info.filename}' in Office archive "
                                    f"'{filename}' exceeds maximum allowed entry size "
                                    f"({entry_bytes_len} > {max_xml_entry_bytes} bytes)"
                                )
                            entry_chunks.append(chunk)
            except (zipfile.BadZipFile, OSError, EOFError, RuntimeError) as exc:
                raise ArchiveSecurityViolationError(
                    f"Failed to read entry '{info.filename}' in Office archive '{filename}': {exc}"
                ) from exc

            sniffed_kind_by_norm[norm_name] = sniffed_kind
            if norm_name in inspected_xml_entries or not should_buffer or not entry_chunks:
                continue

            payload_bytes = b"".join(entry_chunks)
            if (
                is_ext_html
                or ct_kind == "html"
                or (sniffed_kind == "html" and not is_ext_xml and ct_kind != "xml")
            ):
                _inspect_html_entry_security(
                    info.filename,
                    payload_bytes,
                    filename,
                    max_entry_bytes=max_xml_entry_bytes,
                )
                inspected_any_entries.add(norm_name)
            elif is_ext_xml or is_rels_part or ct_kind == "xml" or sniffed_kind == "xml":
                rels_found = _inspect_xml_entry_security(
                    info.filename,
                    payload_bytes,
                    filename,
                    force_rels=is_rels_part,
                    max_entry_bytes=max_xml_entry_bytes,
                )
                inspected_xml_entries.add(norm_name)
                inspected_any_entries.add(norm_name)
                for r_type, r_target in rels_found:
                    internal_rel_targets.append((info.filename, r_type, r_target))
            elif sniffed_kind == "ole":
                _inspect_ole_cfb_security(
                    payload_bytes, ".doc", filename=f"{filename}:{info.filename}"
                )
                inspected_any_entries.add(norm_name)
            elif sniffed_kind == "zip":
                _inspect_office_zip_container(
                    payload_bytes,
                    f"{filename}:{info.filename}",
                    max_uncompressed_bytes=max_uncompressed_bytes,
                    max_compression_ratio=max_compression_ratio,
                    max_entries=max_entries,
                    _depth=_depth + 1,
                )
                inspected_any_entries.add(norm_name)

        # Verify every internal OPC `.rels` relationship target was inspected (P04a::MAJ-02)
        idx_rel = 0
        while idx_rel < len(internal_rel_targets):
            rels_src, rel_type_low, target_norm = internal_rel_targets[idx_rel]
            idx_rel += 1
            target_info = info_by_norm.get(target_norm) or info_by_lower.get(target_norm.lower())
            if target_info is None:
                if rel_type_low.endswith("/officedocument"):
                    raise ArchiveSecurityViolationError(
                        f"Office archive '{filename}' officeDocument relationship in '{rels_src}' "
                        f"targets missing part '{target_norm}'"
                    )
                continue

            resolved_norm = _norm_zip_part_name(target_info.filename)
            if rel_type_low.endswith("/afchunk"):
                if resolved_norm not in inspected_any_entries:
                    raw_af = _read_bounded_entry_bytes(target_info, max_xml_entry_bytes)
                    af_kind = _sniff_markup_or_container_kind(raw_af)
                    if af_kind == "rtf":
                        raise ArchiveSecurityViolationError(
                            f"Office archive '{filename}' contains forbidden RTF altChunk "
                            f"'{target_info.filename}'"
                        )
                    if af_kind == "xml":
                        more_rels = _inspect_xml_entry_security(
                            target_info.filename,
                            raw_af,
                            filename,
                            max_entry_bytes=max_xml_entry_bytes,
                        )
                        inspected_xml_entries.add(resolved_norm)
                        for r_type, r_target in more_rels:
                            internal_rel_targets.append((target_info.filename, r_type, r_target))
                    _inspect_html_entry_security(
                        target_info.filename,
                        raw_af,
                        filename,
                        max_entry_bytes=max_xml_entry_bytes,
                    )
                    inspected_any_entries.add(resolved_norm)
                continue

            if rel_type_low.endswith(_XML_MANDATORY_OOXML_REL_SUFFIXES):
                if resolved_norm not in inspected_xml_entries:
                    raw_xml = _read_bounded_entry_bytes(target_info, max_xml_entry_bytes)
                    more_rels = _inspect_xml_entry_security(
                        target_info.filename,
                        raw_xml,
                        filename,
                        max_entry_bytes=max_xml_entry_bytes,
                    )
                    inspected_xml_entries.add(resolved_norm)
                    inspected_any_entries.add(resolved_norm)
                    for r_type, r_target in more_rels:
                        internal_rel_targets.append((target_info.filename, r_type, r_target))
                continue

            if resolved_norm not in inspected_any_entries:
                if (
                    rel_type_low.endswith(_SAFE_BINARY_REL_SUFFIXES)
                    and sniffed_kind_by_norm.get(resolved_norm) is None
                ):
                    continue
                raise ArchiveSecurityViolationError(
                    f"Office archive '{filename}' relationship '{rel_type_low}' in '{rels_src}' "
                    f"targets uninspected part '{target_info.filename}'"
                )


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
        if path_obj.is_file() and (os.access(path_obj, os.X_OK) or os.name == "nt"):
            return str(path_obj.resolve())
        return None

    # Check environment variables (do not silently fall back when explicitly configured)
    env_soffice = os.environ.get("SOFFICE_PATH") or os.environ.get("LIBREOFFICE_PATH")
    if env_soffice:
        env_path = Path(env_soffice)
        if env_path.is_file() and (os.access(env_path, os.X_OK) or os.name == "nt"):
            return str(env_path.resolve())
        return None

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
        if caller_sha256 and str(caller_sha256).strip().lower() != actual_sha256:
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
        try:
            taint = TaintClassification(taint_raw) if taint_raw else TaintClassification.INTERNAL
        except ValueError as exc:
            raise InvalidDocumentError(
                f"Invalid taint classification '{taint_raw}' for '{filename}': {exc}"
            ) from exc

        # Execute headless conversion in an isolated temporary profile; retry cleanup explicitly
        # and log a warning if OS file handles prevent immediate deletion (MAJ-08, P04a::MIN-10).
        tmp_dir = Path(tempfile.mkdtemp(prefix="opendv_soffice_"))
        try:
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
                with contextlib.suppress(OSError):
                    input_path.unlink(missing_ok=True)
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
            taint_rank = {
                TaintClassification.PUBLIC: 0,
                TaintClassification.INTERNAL: 1,
                TaintClassification.CONFIDENTIAL: 2,
                TaintClassification.RESTRICTED: 3,
            }

            def _stronger_taint(
                a: TaintClassification, b: TaintClassification
            ) -> TaintClassification:
                return a if taint_rank.get(a, 0) >= taint_rank.get(b, 0) else b

            eff_doc_taint = _stronger_taint(taint, pdf_result.taint)
            regrounded_chunks = [
                chunk.model_copy(
                    update={
                        "document_id": doc_id,
                        "source_sha256": actual_sha256,
                        "taint": _stronger_taint(eff_doc_taint, chunk.taint),
                    }
                )
                for chunk in pdf_result.chunks
            ]
            regrounded_tables = [
                tbl.model_copy(update={"taint": _stronger_taint(eff_doc_taint, tbl.taint)})
                if _stronger_taint(eff_doc_taint, tbl.taint) != tbl.taint
                else tbl
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
                taint=eff_doc_taint,
                source_sha256=actual_sha256,
                normalized_text=pdf_result.normalized_text,
                metadata=metadata,
            )
        finally:
            try:
                shutil.rmtree(tmp_dir)
            except OSError as cleanup_err:
                logger.warning(
                    "Deferred cleanup warning for LibreOffice temp dir '%s': %s",
                    tmp_dir,
                    cleanup_err,
                )
                shutil.rmtree(tmp_dir, ignore_errors=True)


__all__ = [
    "ArchiveSecurityViolationError",
    "LibreOfficeDocumentAdapter",
    "_inspect_expat_dtd_and_entities",
    "_inspect_ole_cfb_security",
    "find_soffice_binary",
]
