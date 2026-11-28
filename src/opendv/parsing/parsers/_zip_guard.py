"""Bounded streaming ZIP reader and XML entity/doctype guard for OOXML (.docx) inputs."""

from __future__ import annotations

import re
import stat
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.project import WINDOWS_RESERVED_NAMES

DOCX_MAX_ARCHIVE_ENTRIES: Final[int] = 500
DOCX_MAX_INFLATED_BYTES: Final[int] = 262_144_000
DOCX_MAX_INFLATION_RATIO: Final[float] = 100.0
DOCX_RATIO_MIN_ENTRY_BYTES: Final[int] = 1_048_576
DOCX_STREAM_CHUNK_BYTES: Final[int] = 65_536

_REPARSE_ATTR_BIT: Final[int] = 0x0400
_SPECIAL_UNIX_MODES: Final[tuple[int, ...]] = (
    stat.S_IFLNK,
    stat.S_IFCHR,
    stat.S_IFBLK,
    stat.S_IFIFO,
    stat.S_IFSOCK,
)
_WIN_DRIVE_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z]:")
_SHORT_DOS_NAME_RE: Final[re.Pattern[str]] = re.compile(r"~\d+(?:\.|$)")
_DISALLOWED_CHARS_RE: Final[re.Pattern[str]] = re.compile(r'[<>:"|?*]')
_XML_PROLOG_HAZARD_RE: Final[re.Pattern[bytes]] = re.compile(
    rb"<!(?:DOCTYPE|ENTITY)\b",
    re.IGNORECASE,
)


def _archive_violation(reason: str) -> OpenDVError:
    return OpenDVError(reason, error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION)


def _size_limit_exceeded(reason: str) -> OpenDVError:
    return OpenDVError(reason, error_code=ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED)


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _has_xml_prolog_hazard(payload: bytes) -> bool:
    if _XML_PROLOG_HAZARD_RE.search(payload):
        return True
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        decoded = payload.decode("utf-16", errors="ignore").encode("utf-8", errors="ignore")
        if _XML_PROLOG_HAZARD_RE.search(decoded):
            return True
    if b"\x00" in payload:
        for encoding in ("utf-16-le", "utf-16-be"):
            decoded = payload.decode(encoding, errors="ignore").encode("utf-8", errors="ignore")
            if _XML_PROLOG_HAZARD_RE.search(decoded):
                return True
        if _XML_PROLOG_HAZARD_RE.search(payload.replace(b"\x00", b"")):
            return True
    return False


@dataclass(frozen=True, slots=True)
class BoundedZipReader:
    """Validate ZIP member metadata and stream inflated bytes under strict bounds."""

    max_entries: int = DOCX_MAX_ARCHIVE_ENTRIES
    max_uncompressed_bytes: int = DOCX_MAX_INFLATED_BYTES
    max_ratio: float = DOCX_MAX_INFLATION_RATIO
    ratio_min_entry_bytes: int = DOCX_RATIO_MIN_ENTRY_BYTES

    def validate_entry_info(
        self,
        info: zipfile.ZipInfo,
        seen_keys: set[str] | None = None,
    ) -> tuple[PurePosixPath, bool]:
        """Validate one ZipInfo header while permitting valid directory entries."""
        member = info.filename
        original = getattr(info, "orig_filename", member) or member
        if not member or "\\" in member or "\\" in original:
            raise _archive_violation(f"Invalid archive member name '{original}'.")
        if _has_control_chars(member) or _has_control_chars(original) or original != member:
            raise _archive_violation(f"Unsafe or mismatched archive member '{original}'.")
        if member.startswith(("/", "//")) or _WIN_DRIVE_RE.match(member) or ":" in member:
            raise _archive_violation(f"Absolute, drive, or ADS path rejected: '{member}'.")
        if info.flag_bits & 0x1:
            raise OpenDVError(
                f"Encrypted archive member '{member}' cannot be processed.",
                error_code=ErrorCode.VERIFICATION_GATE_FAILED,
            )
        if info.external_attr & _REPARSE_ATTR_BIT:
            raise _archive_violation(f"Reparse point attribute rejected on '{member}'.")

        file_type = stat.S_IFMT((info.external_attr >> 16) & 0xFFFF)
        if file_type in _SPECIAL_UNIX_MODES:
            raise _archive_violation(f"Symlink or special device entry rejected: '{member}'.")

        is_dir = info.is_dir() or member.endswith("/")
        trimmed = member[:-1] if is_dir and member.endswith("/") else member
        if not trimmed or trimmed.endswith("/"):
            raise _archive_violation(f"Malformed directory member path '{member}'.")

        segments = trimmed.split("/")
        for part in segments:
            if part in ("", ".", "..") or part.endswith((".", " ")):
                raise _archive_violation(f"Unsafe path segment '{part}' in '{member}'.")
            if _SHORT_DOS_NAME_RE.search(part) or _DISALLOWED_CHARS_RE.search(part):
                raise _archive_violation(f"Disallowed filename pattern in '{member}'.")
            low = part.lower()
            base = low.split(".", 1)[0]
            if low in WINDOWS_RESERVED_NAMES or base in WINDOWS_RESERVED_NAMES:
                raise _archive_violation(f"Reserved OS device name '{part}' in '{member}'.")

        rel_path = PurePosixPath(*segments)
        if seen_keys is not None:
            folded = unicodedata.normalize("NFC", rel_path.as_posix()).casefold()
            if folded in seen_keys:
                raise _archive_violation(f"Normalized archive path collision: '{member}'.")
            seen_keys.add(folded)
        return rel_path, is_dir

    def read_validated(self, archive_path: Path) -> dict[str, bytes]:
        """Read and validate all archive file payloads in bounded streaming chunks."""
        try:
            disk_bytes = archive_path.stat().st_size
            zf = zipfile.ZipFile(archive_path, "r")
        except (zipfile.BadZipFile, OSError) as exc:
            raise OpenDVError(
                f"Corrupt or encrypted archive '{archive_path.name}': {exc}",
                error_code=ErrorCode.VERIFICATION_GATE_FAILED,
            ) from exc

        entries: dict[str, bytes] = {}
        with zf:
            infos = zf.infolist()
            if len(infos) > self.max_entries:
                raise _size_limit_exceeded(
                    f"Archive entry count {len(infos)} exceeds limit {self.max_entries}."
                )

            seen_keys: set[str] = set()
            total_inflated = 0
            for info in infos:
                rel_path, is_dir = self.validate_entry_info(info, seen_keys)
                if is_dir:
                    continue
                rel_name = rel_path.as_posix()
                denom = max(min(info.compress_size, max(disk_bytes, 1)), 1)
                entry_bytes = 0
                buf = bytearray()
                try:
                    with zf.open(info, "r") as stream:
                        while chunk := stream.read(DOCX_STREAM_CHUNK_BYTES):
                            entry_bytes += len(chunk)
                            total_inflated += len(chunk)
                            if total_inflated > self.max_uncompressed_bytes:
                                raise _size_limit_exceeded(
                                    "Archive inflated size exceeds "
                                    f"{self.max_uncompressed_bytes} bytes."
                                )
                            if (
                                entry_bytes > self.ratio_min_entry_bytes
                                and (entry_bytes / denom) > self.max_ratio
                            ):
                                raise _size_limit_exceeded(
                                    f"Archive compression ratio exceeded in '{rel_name}'."
                                )
                            buf.extend(chunk)
                except (zipfile.BadZipFile, RuntimeError, EOFError, OSError, zlib.error) as exc:
                    raise OpenDVError(
                        f"Unreadable or encrypted archive stream '{rel_name}': {exc}",
                        error_code=ErrorCode.VERIFICATION_GATE_FAILED,
                    ) from exc

                payload = bytes(buf)
                if rel_name.lower().endswith((".xml", ".rels")) and _has_xml_prolog_hazard(
                    payload
                ):
                    raise _archive_violation(
                        f"Disallowed XML DOCTYPE/ENTITY declaration in '{rel_name}'."
                    )
                entries[rel_name] = payload
        return entries


__all__ = [
    "DOCX_MAX_ARCHIVE_ENTRIES",
    "DOCX_MAX_INFLATED_BYTES",
    "DOCX_MAX_INFLATION_RATIO",
    "DOCX_RATIO_MIN_ENTRY_BYTES",
    "BoundedZipReader",
]
