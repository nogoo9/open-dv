"""Zero-trust archive security guards for portable .opendv.zip bundles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.project import WINDOWS_RESERVED_NAMES, BundleManifest

MAX_BUNDLE_UNCOMPRESSED_BYTES: int = 250 * 1024 * 1024  # 262,144,000 bytes
MAX_BUNDLE_ENTRIES: int = 500
MAX_COMPRESSION_RATIO: float = 100.0
RATIO_CHECK_THRESHOLD_BYTES: int = 1024 * 1024
CHUNK_SIZE: int = 64 * 1024
WIN_REPARSE_ATTR: int = 0x0400

_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")
_SHORT_8_3_RE = re.compile(r"~\d+(?:\.|$)")
_WIN_ILLEGAL_CHARS_RE = re.compile(r'[<>:"|?*]')
_FORBIDDEN_INTERNAL_NAMES: frozenset[str] = frozenset(
    {
        "secrets.json",
        "project.sqlite",
        "project.sqlite-wal",
        "project.sqlite-shm",
    }
)


@dataclass(frozen=True, slots=True)
class VerifiedBundle:
    """In-memory verified contents of an .opendv.zip archive before any disk extraction."""

    manifest: BundleManifest
    json_entries: dict[str, bytes]
    binary_entries: tuple[str, ...]
    total_uncompressed_bytes: int


def _sec_error(msg: str) -> OpenDVError:
    return OpenDVError(msg, error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION)


def _is_reparse_or_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        st = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    win_attrs = getattr(st, "st_file_attributes", 0)
    return bool(win_attrs & WIN_REPARSE_ATTR)


def reject_symlinks_on_path_and_ancestors(
    target: Path,
    *,
    stop_roots: Sequence[Path] = (),
) -> None:
    """Reject '..' segments and symlinks/reparse points on target and its ancestors."""
    if ".." in target.parts:
        raise _sec_error(f"Path traversal '..' rejected in {target}")
    stop_norms = {os.path.normcase(os.path.abspath(str(r))) for r in stop_roots}
    cur = Path(os.path.abspath(str(target)))
    while True:
        cur_norm = os.path.normcase(str(cur))
        if cur_norm in stop_norms:
            break
        if _is_reparse_or_symlink(cur):
            raise _sec_error(f"Symlink or reparse point rejected at {cur}")
        parent = cur.parent
        if parent == cur:
            break
        cur = parent


def validate_containment(base_dir: Path, target: Path) -> Path:
    """Ensure resolved target stays strictly inside resolved base_dir."""
    base_real = os.path.normcase(os.path.realpath(base_dir))
    target_real = os.path.normcase(os.path.realpath(target))
    if target_real != base_real and not target_real.startswith(base_real + os.sep):
        raise _sec_error(f"Path '{target}' escapes containment root '{base_dir}'")
    return Path(target_real)


def _upper_win_drive(path_str: str) -> str:
    if os.name == "nt" and len(path_str) >= 2 and path_str[1] == ":":
        return path_str[0].upper() + path_str[1:]
    return path_str


def safe_open_no_follow(
    path: Path,
    flags: int,
    mode: int = 0o600,
    *,
    stop_roots: Sequence[Path] = (),
) -> int:
    """Open a file with O_NOFOLLOW/O_BINARY after checking parent has no symlinks."""
    if ".." in path.parts:
        raise _sec_error(f"Path traversal '..' rejected in {path}")
    path_str = os.path.normpath(_upper_win_drive(os.path.abspath(str(path))))
    if stop_roots:
        allowed = False
        for root in stop_roots:
            root_str = os.path.normpath(_upper_win_drive(os.path.abspath(str(root))))
            prefix = root_str if root_str.endswith(os.sep) else root_str + os.sep
            if path_str == root_str or path_str.startswith(prefix):
                allowed = True
                break
        if not allowed:
            raise _sec_error(f"Path '{path}' escapes allowed containment roots")
    safe_path = Path(path_str)
    reject_symlinks_on_path_and_ancestors(safe_path.parent, stop_roots=stop_roots)
    if _is_reparse_or_symlink(safe_path):
        raise _sec_error(f"Refusing to open symlink or reparse target: {path}")
    open_flags = flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path_str, open_flags, mode)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise _sec_error(f"Non-regular file rejected: {path}")
    except Exception:
        os.close(fd)
        raise
    return fd


def validate_archive_member(
    info: zipfile.ZipInfo,
    seen_normalized: set[str] | None = None,
) -> PurePosixPath:
    """Validate a single ZipInfo member against traversal, symlink, reparse, ADS, and OS hazards."""
    raw_name = info.filename
    orig_name = getattr(info, "orig_filename", raw_name) or raw_name
    if not raw_name:
        raise _sec_error("Empty archive member name rejected.")
    if "\\" in raw_name or "\\" in orig_name:
        raise _sec_error(f"Backslash rejected in archive member '{orig_name}'.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in orig_name) or any(
        ord(ch) < 32 or ord(ch) == 127 for ch in raw_name
    ):
        raise _sec_error(f"Control or NUL character rejected in archive member '{orig_name}'.")
    if orig_name != raw_name:
        raise _sec_error(
            f"Archive member name divergence rejected: '{orig_name}' vs '{raw_name}'."
        )
    if raw_name.startswith(("/", "//")):
        raise _sec_error(f"Absolute or UNC path rejected in archive member '{raw_name}'.")
    if _DRIVE_PREFIX_RE.match(raw_name):
        raise _sec_error(f"Windows drive prefix rejected in archive member '{raw_name}'.")
    if ":" in raw_name:
        raise _sec_error(f"Alternate Data Stream ':' rejected in archive member '{raw_name}'.")
    if info.is_dir() or raw_name.endswith("/"):
        raise _sec_error(f"Directory entry rejected in bundle archive: '{raw_name}'.")

    # Unconditional NTFS reparse-point bit check (works regardless of create_system)
    if info.external_attr & WIN_REPARSE_ATTR:
        raise _sec_error(f"NTFS reparse point attribute rejected on '{raw_name}'.")

    unix_mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_ISLNK(unix_mode):
        raise _sec_error(f"Symlink entry rejected in archive: '{raw_name}'.")
    if any(
        fn(unix_mode)
        for fn in (stat.S_ISCHR, stat.S_ISBLK, stat.S_ISFIFO, stat.S_ISSOCK)
    ):
        raise _sec_error(f"Special device/socket/FIFO entry rejected: '{raw_name}'.")

    parts = raw_name.split("/")
    for seg in parts:
        if seg in ("", ".", ".."):
            raise _sec_error(f"Unsafe path segment '{seg}' in '{raw_name}'.")
        if seg.endswith((".", " ")):
            raise _sec_error(f"Trailing dot or space rejected in '{raw_name}'.")
        if _SHORT_8_3_RE.search(seg):
            raise _sec_error(f"Windows 8.3 short-name segment rejected in '{raw_name}'.")
        if _WIN_ILLEGAL_CHARS_RE.search(seg):
            raise _sec_error(f"Illegal filename character rejected in '{raw_name}'.")
        lowered = seg.lower()
        stem = lowered.split(".", 1)[0]
        if lowered in WINDOWS_RESERVED_NAMES or stem in WINDOWS_RESERVED_NAMES:
            raise _sec_error(f"Windows reserved device name rejected in '{raw_name}'.")
        if lowered in _FORBIDDEN_INTERNAL_NAMES or lowered.startswith(
            (".lock", ".staging", ".tmp")
        ):
            raise _sec_error(f"Forbidden internal state entry rejected: '{raw_name}'.")

    clean_path = PurePosixPath(*parts)
    if seen_normalized is not None:
        norm_key = unicodedata.normalize("NFC", clean_path.as_posix()).casefold()
        if norm_key in seen_normalized:
            raise _sec_error(
                f"NFC/case-folded path collision detected for '{clean_path.as_posix()}'."
            )
        seen_normalized.add(norm_key)
    return clean_path


def _is_binary_member(rel_posix: str) -> bool:
    return rel_posix.startswith(("sources/", "targets/"))


def inspect_and_verify_archive(zip_path: Path) -> VerifiedBundle:
    """Verify all ZipInfo headers, limits, ratios, and manifest SHA-256 strictly in memory."""
    if not zip_path.exists():
        raise OpenDVError(
            f"Bundle archive not found: {zip_path}",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    reject_symlinks_on_path_and_ancestors(zip_path)
    zip_disk_size = zip_path.stat().st_size

    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise _sec_error(f"Invalid .opendv.zip archive '{zip_path.name}': {exc}") from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_BUNDLE_ENTRIES:
            raise _sec_error(
                f"Archive entry count {len(infos)} exceeds limit of {MAX_BUNDLE_ENTRIES}."
            )

        seen_normalized: set[str] = set()
        clean_infos: list[tuple[str, zipfile.ZipInfo]] = []
        for info in infos:
            posix_path = validate_archive_member(info, seen_normalized)
            clean_infos.append((posix_path.as_posix(), info))

        total_uncompressed = 0
        actual_digests: dict[str, tuple[str, int]] = {}
        json_entries: dict[str, bytes] = {}
        binary_entries: list[str] = []

        for rel_posix, info in clean_infos:
            compressed_size = max(min(info.compress_size, max(zip_disk_size, 1)), 1)
            entry_bytes = 0
            hasher = hashlib.sha256()
            keep_in_memory = not _is_binary_member(rel_posix)
            buf = bytearray()

            try:
                with zf.open(info, "r") as stream:
                    while True:
                        chunk = stream.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        entry_bytes += len(chunk)
                        total_uncompressed += len(chunk)
                        if total_uncompressed > MAX_BUNDLE_UNCOMPRESSED_BYTES:
                            raise OpenDVError(
                                "Bundle uncompressed size exceeds 250 MB limit.",
                                error_code=ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
                            )
                        if (
                            entry_bytes > RATIO_CHECK_THRESHOLD_BYTES
                            and (entry_bytes / compressed_size) > MAX_COMPRESSION_RATIO
                        ):
                            raise _sec_error(
                                f"Archive compression ratio bomb detected in '{rel_posix}'."
                            )
                        hasher.update(chunk)
                        if keep_in_memory:
                            buf.extend(chunk)
            except (zipfile.BadZipFile, RuntimeError, EOFError, OSError) as exc:
                raise _sec_error(
                    f"Corrupt archive stream for '{rel_posix}': {exc}"
                ) from exc

            actual_digests[rel_posix] = (hasher.hexdigest(), entry_bytes)
            if keep_in_memory:
                json_entries[rel_posix] = bytes(buf)
            else:
                binary_entries.append(rel_posix)

        manifest_raw = json_entries.get("manifest.json")
        if manifest_raw is None:
            raise _sec_error("Archive is missing required manifest.json.")
        try:
            manifest_obj = json.loads(manifest_raw.decode("utf-8"))
            manifest = BundleManifest.model_validate(manifest_obj)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
            raise _sec_error(f"Invalid bundle manifest.json: {exc}") from exc

        expected_names = set(manifest.entries)
        actual_names = set(actual_digests) - {"manifest.json"}
        if expected_names != actual_names:
            raise OpenDVError(
                "Bundle manifest entry list does not match archive contents.",
                error_code=ErrorCode.BUNDLE_HASH_MISMATCH,
            )

        for entry_name, expected_digest in manifest.entries.items():
            actual_sha, actual_size = actual_digests[entry_name]
            if (
                actual_sha != expected_digest.sha256
                or actual_size != expected_digest.uncompressed_bytes
            ):
                raise OpenDVError(
                    f"SHA-256 or size mismatch for archive entry '{entry_name}'.",
                    error_code=ErrorCode.BUNDLE_HASH_MISMATCH,
                )

    return VerifiedBundle(
        manifest=manifest,
        json_entries=json_entries,
        binary_entries=tuple(binary_entries),
        total_uncompressed_bytes=total_uncompressed,
    )


def safe_extract_binary_entries(
    zip_path: Path,
    binary_entries: Sequence[str],
    staging_root: Path,
    *,
    stop_roots: Sequence[Path] = (),
) -> list[Path]:
    """Safely extract already-verified binary entries into staging_root via O_NOFOLLOW."""
    if not binary_entries:
        return []
    reject_symlinks_on_path_and_ancestors(zip_path, stop_roots=stop_roots)
    reject_symlinks_on_path_and_ancestors(staging_root, stop_roots=stop_roots)
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for rel_posix in binary_entries:
            dest = staging_root / Path(*PurePosixPath(rel_posix).parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            validate_containment(staging_root, dest)
            fd = safe_open_no_follow(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as out_f, zf.open(rel_posix, "r") as in_f:
                    while True:
                        chunk = in_f.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        out_f.write(chunk)
                    out_f.flush()
            except Exception:
                dest.unlink(missing_ok=True)
                raise
            extracted.append(dest)
    return extracted


__all__ = [
    "CHUNK_SIZE",
    "MAX_BUNDLE_ENTRIES",
    "MAX_BUNDLE_UNCOMPRESSED_BYTES",
    "MAX_COMPRESSION_RATIO",
    "RATIO_CHECK_THRESHOLD_BYTES",
    "WIN_REPARSE_ATTR",
    "VerifiedBundle",
    "inspect_and_verify_archive",
    "reject_symlinks_on_path_and_ancestors",
    "safe_extract_binary_entries",
    "safe_open_no_follow",
    "validate_archive_member",
    "validate_containment",
]
