#!/usr/bin/env python3
"""Archive security, SSRF-hardened HTTPS zip download, and git acquisition for vendor_fibo.py."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final, Literal
from urllib.parse import urljoin, urlsplit

import httpx

from opendv.models.errors import OpenDVError
from opendv.models.project import WINDOWS_RESERVED_NAMES
from opendv.service.bundles.zip_guard import (
    reject_symlinks_on_path_and_ancestors,
    validate_archive_member,
)

GIT_BIN: str = shutil.which("git") or "git"

MAX_ARCHIVE_MEMBERS: int = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES: int = 200 * 1024 * 1024  # 200 MB
MAX_ARCHIVE_DOWNLOAD_BYTES: int = 50 * 1024 * 1024  # 50 MB
MAX_ARCHIVE_COMPRESSION_RATIO: float = 50.0
RATIO_CHECK_FLOOR_BYTES: int = 1024 * 1024  # 1 MB
FILE_ATTRIBUTE_REPARSE_POINT: int = 0x400
_ARCHIVE_STREAM_CHUNK_BYTES: int = 65_536
SCRIPT_CACHE_MARKER: str = ".opendv_cache_manifest.json"
DEFAULT_GIT_TIMEOUT_SECONDS: int = 120

DEFAULT_PROD_ZIP_URL_TEMPLATE: Final[str] = (
    "https://spec.edmcouncil.org/fibo/ontology/master/{tag}/prod.ttl.zip"
)
DEFAULT_DEV_ZIP_URL_TEMPLATE: Final[str] = (
    "https://spec.edmcouncil.org/fibo/ontology/master/{tag}/dev.ttl.zip"
)

_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")
_WIN_ILLEGAL_CHARS_RE = re.compile(r'[<>:"|?*]')
_XML_UNSAFE_DECL_RE = re.compile(rb"<!(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_UTF_BOM_PREFIXES: Final[tuple[bytes, ...]] = (
    b"\xff\xfe\x00\x00",
    b"\x00\x00\xfe\xff",
    b"\xff\xfe",
    b"\xfe\xff",
)

BLOCKED_SSRF_CIDRS: Final[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]] = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::ffff:0:0/96"),
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("100::/64"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
)


class ArchiveSecurityError(ValueError):
    """Raised when an archive, XML payload, or download violates security constraints."""


def _win_long_path(path: Path) -> Path:
    """Apply Windows extended-length path prefix when needed to bypass 260-char MAX_PATH."""
    if sys.platform != "win32":
        return path
    raw = os.path.abspath(os.fspath(path))
    if raw.startswith("\\\\?\\") or len(raw) < 240:
        return path
    if raw.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + raw.lstrip("\\"))
    return Path("\\\\?\\" + raw)


def hash_file_sha256(file_path: Path, chunk_size: int = 65536) -> str:
    """Compute SHA-256 digest of a file in chunks to limit memory footprint."""
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def _effective_uncompressed_limit() -> int:
    facade = sys.modules.get("scripts.vendor_fibo")
    if facade is not None and hasattr(facade, "MAX_ARCHIVE_UNCOMPRESSED_BYTES"):
        return int(facade.MAX_ARCHIVE_UNCOMPRESSED_BYTES)
    return MAX_ARCHIVE_UNCOMPRESSED_BYTES


def validate_xml_ontology_bytes(raw_bytes: bytes, source_name: str = "") -> None:
    """Reject UTF-16/32 BOMs, NUL bytes, and DOCTYPE/ENTITY declarations before XML parsing."""
    label = source_name or "ontology file"
    for bom in _UTF_BOM_PREFIXES:
        if raw_bytes.startswith(bom):
            raise ArchiveSecurityError(f"Unsupported UTF-16/32 BOM rejected in XML {label}")
    if b"\x00" in raw_bytes:
        raise ArchiveSecurityError(f"NUL byte rejected in XML {label}")
    if _XML_UNSAFE_DECL_RE.search(raw_bytes):
        raise ArchiveSecurityError(
            f"Forbidden DOCTYPE or ENTITY declaration rejected in XML {label}"
        )


def _is_blocked_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if (
        isinstance(ip_obj, ipaddress.IPv6Address)
        and ip_obj.ipv4_mapped is not None
        and _is_blocked_ip(ip_obj.ipv4_mapped)
    ):
        return True
    if any(ip_obj in net for net in BLOCKED_SSRF_CIDRS):
        return True
    return bool(
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    )


def validate_https_url_no_ssrf(url: str) -> str:
    """Require HTTPS URL and block private/loopback/link-local/reserved DNS resolutions."""
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ArchiveSecurityError(f"HTTPS-only URL required, got '{url}'")
    if parsed.username is not None or parsed.password is not None:
        raise ArchiveSecurityError(f"Credentials in URL rejected: '{url}'")
    hostname = (parsed.hostname or "").strip()
    if not hostname:
        raise ArchiveSecurityError(f"Missing hostname in URL '{url}'")
    port = parsed.port or 443

    try:
        ip_lit = ipaddress.ip_address(hostname)
    except ValueError:
        ip_lit = None

    if ip_lit is not None:
        if _is_blocked_ip(ip_lit):
            raise ArchiveSecurityError(f"SSRF blocked IP address '{ip_lit}' for '{url}'")
        return url.strip()

    try:
        addr_infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise RuntimeError(f"Failed to resolve host '{hostname}' for '{url}': {exc}") from exc

    if not addr_infos:
        raise RuntimeError(f"DNS resolution returned no addresses for '{hostname}'")

    for _fam, _socktype, _proto, _canon, sockaddr in addr_infos:
        ip_text = str(sockaddr[0]).split("%", 1)[0]
        ip_obj = ipaddress.ip_address(ip_text)
        if _is_blocked_ip(ip_obj):
            raise ArchiveSecurityError(
                f"SSRF blocked resolved IP '{ip_obj}' for host '{hostname}'"
            )
    return url.strip()


def download_fibo_archive(
    url: str,
    dest_path: Path,
    *,
    expected_sha256: str | None = None,
    max_bytes: int = MAX_ARCHIVE_DOWNLOAD_BYTES,
    max_redirects: int = 5,
    timeout: float = 120.0,
) -> str:
    """Download an archive over HTTPS with per-hop SSRF validation and SHA-256 verification."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    current_url = validate_https_url_no_ssrf(url)

    with httpx.Client(follow_redirects=False, timeout=timeout) as client:
        for _hop in range(max_redirects + 1):
            current_url = validate_https_url_no_ssrf(current_url)
            with client.stream("GET", current_url) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location", "").strip()
                    if not loc:
                        raise RuntimeError(f"HTTP redirect without Location from '{current_url}'")
                    current_url = urljoin(current_url, loc)
                    continue
                resp.raise_for_status()

                hasher = hashlib.sha256()
                total = 0
                with tempfile.NamedTemporaryFile(
                    dir=dest_path.parent, prefix=".dl-", suffix=".tmp", delete=False
                ) as tmp_f:
                    tmp_path = Path(tmp_f.name)
                    try:
                        for chunk in resp.iter_bytes(chunk_size=_ARCHIVE_STREAM_CHUNK_BYTES):
                            total += len(chunk)
                            if total > max_bytes:
                                raise ArchiveSecurityError(
                                    f"Downloaded archive exceeds max size ({max_bytes} bytes)"
                                )
                            hasher.update(chunk)
                            tmp_f.write(chunk)
                        tmp_f.flush()
                    except Exception:
                        tmp_path.unlink(missing_ok=True)
                        raise

                actual_sha = hasher.hexdigest()
                if expected_sha256 is not None and actual_sha.lower() != expected_sha256.lower():
                    tmp_path.unlink(missing_ok=True)
                    raise RuntimeError(
                        f"Downloaded archive SHA-256 mismatch for {current_url}: "
                        f"computed {actual_sha}, expected {expected_sha256}"
                    )
                os.replace(tmp_path, dest_path)
                return actual_sha

    raise RuntimeError(f"Exceeded max redirects ({max_redirects}) downloading '{url}'")


def _validate_zip_dir_entry(
    info: zipfile.ZipInfo,
    staging_dir: Path,
    seen_normalized: set[str],
) -> Path:
    raw_name = info.filename
    orig_name = getattr(info, "orig_filename", raw_name) or raw_name
    if not raw_name:
        raise ArchiveSecurityError("Empty directory entry rejected in zip archive.")
    if "\\" in raw_name or "\\" in orig_name:
        raise ArchiveSecurityError(f"Backslash rejected in zip directory '{orig_name}'.")
    if orig_name != raw_name:
        raise ArchiveSecurityError(f"Zip directory name divergence rejected: '{orig_name}'.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_name):
        raise ArchiveSecurityError(f"Control/NUL byte rejected in zip directory '{raw_name}'.")
    if raw_name.startswith(("/", "//")) or _DRIVE_PREFIX_RE.match(raw_name):
        raise ArchiveSecurityError(
            f"Path traversal attempt detected in zip directory: {raw_name}"
        )
    if ":" in raw_name:
        raise ArchiveSecurityError(
            f"Alternate Data Stream ':' rejected in zip directory: {raw_name}"
        )

    mode = (info.external_attr >> 16) & 0xFFFF
    dos_attrs = info.external_attr & 0xFFFF
    if (
        stat.S_ISLNK(mode)
        or stat.S_ISFIFO(mode)
        or stat.S_ISCHR(mode)
        or stat.S_ISBLK(mode)
        or stat.S_ISSOCK(mode)
        or bool(dos_attrs & FILE_ATTRIBUTE_REPARSE_POINT)
    ):
        raise ArchiveSecurityError(
            f"Links and special files prohibited in FIBO archive: {raw_name}"
        )

    trimmed = raw_name.rstrip("/")
    for seg in trimmed.split("/"):
        if seg in ("", ".", ".."):
            raise ArchiveSecurityError(
                f"Path traversal attempt detected in zip directory: {raw_name}"
            )
        if seg.endswith((".", " ")) or _WIN_ILLEGAL_CHARS_RE.search(seg):
            raise ArchiveSecurityError(f"Unsafe zip directory segment '{seg}' in '{raw_name}'.")
        lowered = seg.lower()
        stem = lowered.split(".", 1)[0]
        if lowered in WINDOWS_RESERVED_NAMES or stem in WINDOWS_RESERVED_NAMES:
            raise ArchiveSecurityError(f"Windows reserved device name in zip directory: {raw_name}")

    dest_path = (staging_dir / raw_name).resolve()
    if not dest_path.is_relative_to(staging_dir):
        raise ArchiveSecurityError(f"Path traversal attempt detected in zip directory: {raw_name}")
    clean_dir = PurePosixPath(*trimmed.split("/"))
    base_key = unicodedata.normalize("NFC", clean_dir.as_posix()).casefold()
    norm_key = f"dir:{base_key}"
    if norm_key in seen_normalized:
        raise ArchiveSecurityError(f"Duplicate/case-folded directory entry rejected: {raw_name}")
    if base_key in seen_normalized or any(
        unicodedata.normalize("NFC", p.as_posix()).casefold() in seen_normalized
        for p in clean_dir.parents
        if p != PurePosixPath(".")
    ):
        raise ArchiveSecurityError(f"Zip directory/file path collision rejected: {raw_name}")
    seen_normalized.add(norm_key)
    return dest_path


def _extract_tar_safely(archive_path: Path, staging_dir: Path, max_bytes: int) -> None:
    tar_disk_size = max(archive_path.stat().st_size, 1)
    with tarfile.open(archive_path, "r:*") as tar:
        members = tar.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ArchiveSecurityError(
                f"Archive member count ({len(members)}) exceeds limit: {MAX_ARCHIVE_MEMBERS}"
            )
        header_total = 0
        streamed_total = 0
        validated: list[tuple[tarfile.TarInfo, Path]] = []
        seen_tar_dirs: set[str] = set()
        seen_tar_files: set[str] = set()
        for member in members:
            member_name = str(member.name)
            if (
                not member_name
                or "\\" in member_name
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in member_name)
                or member_name.startswith(("/", "\\"))
                or PureWindowsPath(member_name).is_absolute()
                or _DRIVE_PREFIX_RE.match(member_name)
                or ":" in member_name
            ):
                raise ArchiveSecurityError(
                    f"Path traversal attempt detected in tar archive: {member_name}"
                )
            dest_path = (staging_dir / member_name).resolve()
            if not dest_path.is_relative_to(staging_dir):
                raise ArchiveSecurityError(
                    f"Path traversal attempt detected in tar archive: {member_name}"
                )
            if (
                member.issym()
                or member.islnk()
                or member.isdev()
                or member.ischr()
                or member.isblk()
                or member.isfifo()
            ):
                raise ArchiveSecurityError(
                    f"Links and special files prohibited in FIBO archive: {member.name}"
                )
            header_total += max(0, member.size)
            if header_total > max_bytes:
                raise ArchiveSecurityError(
                    f"Archive uncompressed size exceeds limit ({max_bytes} bytes)"
                )
            posix_parts = PurePosixPath(member_name).parts
            trimmed = member_name.rstrip("/") if member.isdir() else member_name
            raw_parts = trimmed.split("/")
            if not posix_parts or not raw_parts:
                raise ArchiveSecurityError(
                    f"Unsafe empty path in tar archive: {member_name}"
                )
            for seg in (*posix_parts, *raw_parts):
                if seg in ("", ".", ".."):
                    raise ArchiveSecurityError(
                        f"Path traversal attempt detected in tar archive: {member_name}"
                    )
                if seg.endswith((".", " ")) or _WIN_ILLEGAL_CHARS_RE.search(seg):
                    raise ArchiveSecurityError(
                        f"Unsafe tar segment '{seg}' in '{member_name}'."
                    )
                lowered = seg.lower()
                stem = lowered.split(".", 1)[0]
                if lowered in WINDOWS_RESERVED_NAMES or stem in WINDOWS_RESERVED_NAMES:
                    raise ArchiveSecurityError(
                        f"Windows reserved device name in tar archive: {member_name}"
                    )
            clean_posix = PurePosixPath(*posix_parts)
            norm_key = unicodedata.normalize("NFC", clean_posix.as_posix()).casefold()
            parent_collides = any(
                unicodedata.normalize("NFC", p.as_posix()).casefold() in seen_tar_files
                for p in clean_posix.parents
                if p != PurePosixPath(".")
            )
            if member.isdir():
                if norm_key in seen_tar_files or parent_collides:
                    raise ArchiveSecurityError(
                        f"Tar directory/file path collision rejected: {member_name}"
                    )
                seen_tar_dirs.add(norm_key)
            else:
                if norm_key in seen_tar_dirs or parent_collides:
                    raise ArchiveSecurityError(
                        f"Tar directory/file path collision rejected: {member_name}"
                    )
                seen_tar_files.add(norm_key)
            dest_path = (staging_dir / clean_posix).resolve()
            if not dest_path.is_relative_to(staging_dir):
                raise ArchiveSecurityError(
                    f"Path traversal attempt detected in tar archive: {member_name}"
                )
            if isinstance(member, tarfile.TarInfo) and hasattr(tarfile, "data_filter"):
                try:
                    member = tarfile.data_filter(member, str(staging_dir))
                except tarfile.FilterError as exc:
                    raise ArchiveSecurityError(
                        f"Tar data_filter rejected '{member_name}': {exc}"
                    ) from exc
            validated.append((member, dest_path))

        for member, dest_path in validated:
            lp_dest = _win_long_path(dest_path)
            if member.isdir():
                lp_dest.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isreg():
                raise ArchiveSecurityError(
                    f"Unsupported non-regular tar member '{member.name}'"
                )
            _win_long_path(dest_path.parent).mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                raise ArchiveSecurityError(f"Unreadable tar member '{member.name}'")
            with src, lp_dest.open("wb") as dst:
                while chunk := src.read(_ARCHIVE_STREAM_CHUNK_BYTES):
                    streamed_total += len(chunk)
                    if streamed_total > max_bytes:
                        raise ArchiveSecurityError(
                            f"Archive uncompressed size exceeds limit ({max_bytes} bytes)"
                        )
                    if (
                        streamed_total > RATIO_CHECK_FLOOR_BYTES
                        and (streamed_total / tar_disk_size) > MAX_ARCHIVE_COMPRESSION_RATIO
                    ):
                        raise ArchiveSecurityError(
                            f"Tar archive compression ratio exceeds "
                            f"{MAX_ARCHIVE_COMPRESSION_RATIO}x"
                        )
                    dst.write(chunk)


def _extract_zip_safely(archive_path: Path, staging_dir: Path, max_bytes: int) -> None:
    zip_disk_size = max(archive_path.stat().st_size, 1)
    with zipfile.ZipFile(archive_path, "r") as zf:
        infolist = zf.infolist()
        if len(infolist) > MAX_ARCHIVE_MEMBERS:
            raise ArchiveSecurityError(
                f"Archive member count ({len(infolist)}) exceeds limit: {MAX_ARCHIVE_MEMBERS}"
            )
        header_total = 0
        streamed_total = 0
        seen_normalized: set[str] = set()
        for info in infolist:
            if (
                info.filename.startswith(("/", "\\"))
                or PureWindowsPath(info.filename).is_absolute()
            ):
                raise ArchiveSecurityError(
                    f"Path traversal attempt detected in zip archive: {info.filename}"
                )
            dest_path = (staging_dir / info.filename).resolve()
            if not dest_path.is_relative_to(staging_dir):
                raise ArchiveSecurityError(
                    f"Path traversal attempt detected in zip archive: {info.filename}"
                )
            mode = info.external_attr >> 16
            dos_attrs = info.external_attr & 0xFFFF
            if (
                stat.S_ISLNK(mode)
                or stat.S_ISFIFO(mode)
                or stat.S_ISCHR(mode)
                or stat.S_ISBLK(mode)
                or stat.S_ISSOCK(mode)
                or bool(dos_attrs & FILE_ATTRIBUTE_REPARSE_POINT)
            ):
                raise ArchiveSecurityError(
                    f"Links and special files prohibited in FIBO archive: {info.filename}"
                )
            header_total += max(0, info.file_size)
            if header_total > max_bytes:
                raise ArchiveSecurityError(
                    f"Archive uncompressed size exceeds limit ({max_bytes} bytes)"
                )
            if info.is_dir():
                dir_dest = _validate_zip_dir_entry(info, staging_dir, seen_normalized)
                lp_dir = _win_long_path(dir_dest)
                if lp_dir.is_file() or any(
                    _win_long_path(staging_dir / Path(*p.parts)).is_file()
                    for p in PurePosixPath(info.filename.rstrip("/")).parents
                    if p != PurePosixPath(".")
                ):
                    raise ArchiveSecurityError(
                        f"Zip directory/file path collision rejected: {info.filename}"
                    )
                lp_dir.mkdir(parents=True, exist_ok=True)
                continue

            try:
                clean_rel = validate_archive_member(info, seen_normalized)
            except OpenDVError as exc:
                raise ArchiveSecurityError(str(exc)) from exc

            norm_rel = unicodedata.normalize("NFC", clean_rel.as_posix()).casefold()
            if f"dir:{norm_rel}" in seen_normalized or any(
                unicodedata.normalize("NFC", p.as_posix()).casefold() in seen_normalized
                for p in clean_rel.parents
                if p != PurePosixPath(".")
            ):
                raise ArchiveSecurityError(
                    f"Zip directory/file path collision rejected: {info.filename}"
                )

            file_dest = (staging_dir / Path(*clean_rel.parts)).resolve()
            if not file_dest.is_relative_to(staging_dir):
                raise ArchiveSecurityError(
                    f"Path traversal attempt detected in zip archive: {info.filename}"
                )
            lp_file = _win_long_path(file_dest)
            if lp_file.is_dir() or any(
                _win_long_path(staging_dir / Path(*p.parts)).is_file()
                for p in clean_rel.parents
                if p != PurePosixPath(".")
            ):
                raise ArchiveSecurityError(
                    f"Zip directory/file path collision rejected: {info.filename}"
                )
            _win_long_path(file_dest.parent).mkdir(parents=True, exist_ok=True)
            entry_bytes = 0
            comp_size = max(min(info.compress_size, zip_disk_size), 1)
            try:
                with zf.open(info, "r") as src, _win_long_path(file_dest).open("wb") as dst:
                    while chunk := src.read(_ARCHIVE_STREAM_CHUNK_BYTES):
                        entry_bytes += len(chunk)
                        streamed_total += len(chunk)
                        if streamed_total > max_bytes:
                            raise ArchiveSecurityError(
                                f"Archive uncompressed size exceeds limit ({max_bytes} bytes)"
                            )
                        if (
                            entry_bytes > RATIO_CHECK_FLOOR_BYTES
                            and (entry_bytes / comp_size) > MAX_ARCHIVE_COMPRESSION_RATIO
                        ) or (
                            streamed_total > RATIO_CHECK_FLOOR_BYTES
                            and (streamed_total / zip_disk_size) > MAX_ARCHIVE_COMPRESSION_RATIO
                        ):
                            raise ArchiveSecurityError(
                                f"Zip compression ratio bomb detected in '{info.filename}'"
                            )
                        dst.write(chunk)
            except zipfile.BadZipFile as exc:
                raise ArchiveSecurityError(
                    f"Corrupt or tampered zip archive entry {info.filename}: {exc}"
                ) from exc


def safe_extract_archive(archive_path: Path, target_dir: Path) -> Path:
    """Safely extract tar or zip archives, preventing traversal/symlink/bomb attacks."""
    max_bytes = _effective_uncompressed_limit()
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=target_dir.parent, prefix="s_") as temp_staging:
        staging_dir = (Path(temp_staging) / "c").resolve()
        staging_dir.mkdir(parents=True, exist_ok=True)
        if archive_path.name.endswith((".tar.gz", ".tgz", ".tar")):
            _extract_tar_safely(archive_path, staging_dir, max_bytes)
        elif archive_path.name.endswith(".zip"):
            _extract_zip_safely(archive_path, staging_dir, max_bytes)
        else:
            raise ValueError(
                f"Unsupported archive format for {archive_path}; expected .tar.gz, .tgz, or .zip"
            )
        if target_dir.exists():
            shutil.rmtree(_win_long_path(target_dir))
        shutil.move(str(_win_long_path(staging_dir)), str(_win_long_path(target_dir)))
    return target_dir


def _run_git(
    git_args: list[str],
    *,
    timeout: int = DEFAULT_GIT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    with tempfile.TemporaryDirectory(prefix="opendv-git-hooks-") as empty_hooks:
        cmd = [
            GIT_BIN,
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.https.allow=always",
            "-c",
            f"core.hooksPath={empty_hooks}",
            *git_args,
        ]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=timeout,
        )


def _acquire_via_git(
    *,
    cache_dir: Path,
    repo_url: str,
    tag: str,
    expected_commit: str,
) -> tuple[Path, str]:
    git_cache_dir = cache_dir / "git"
    git_manifest = cache_dir / "git_manifest.json"
    if git_cache_dir.is_dir() and (git_cache_dir / ".git" / "config").is_file():
        remote_proc = _run_git(["-C", str(git_cache_dir), "remote", "get-url", "origin"])
        if (
            remote_proc.returncode == 0
            and remote_proc.stdout.strip()
            and remote_proc.stdout.strip() != repo_url
        ):
            shutil.rmtree(git_cache_dir)

    if git_cache_dir.is_dir() and (git_cache_dir / ".git").is_dir():
        rev_cmd = ["-C", str(git_cache_dir), "rev-parse", "HEAD"]
        rev_proc = _run_git(rev_cmd)
        if rev_proc.returncode != 0:
            raise RuntimeError(f"Corrupted git cache in {git_cache_dir}: rev-parse HEAD failed")
        actual_head = rev_proc.stdout.strip()
        if actual_head != expected_commit:
            co_cmd = [
                "-C",
                str(git_cache_dir),
                "checkout",
                "--detach",
                expected_commit,
                "--",
            ]
            co_proc = _run_git(co_cmd)
            if co_proc.returncode != 0:
                _run_git(
                    ["-C", str(git_cache_dir), "fetch", "--depth", "1", "origin", expected_commit]
                )
                _run_git(co_cmd)
            rev2 = _run_git(rev_cmd)
            new_head = rev2.stdout.strip() if rev2.returncode == 0 else ""
            if new_head != expected_commit:
                raise RuntimeError(
                    f"Failed to check out expected commit {expected_commit} in {git_cache_dir}. "
                    f"Current HEAD is {new_head or 'unknown'}."
                )
            actual_head = new_head
        status_proc = _run_git(
            ["-C", str(git_cache_dir), "status", "--porcelain", "--untracked-files=all"]
        )
        if status_proc.returncode != 0 or status_proc.stdout.strip():
            raise RuntimeError(
                f"Cached git repository at {git_cache_dir} has uncommitted or untracked changes."
            )
        git_manifest.write_text(
            json.dumps(
                {
                    "cached_at": datetime.now(UTC).isoformat(),
                    "commit": actual_head,
                    "created_by": "opendv-vendor-fibo",
                    "tag": tag,
                }
            ),
            encoding="utf-8",
        )
        return git_cache_dir, actual_head

    if git_cache_dir.exists():
        shutil.rmtree(git_cache_dir)
    git_cache_dir.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=git_cache_dir.parent) as temp_clone_parent:
        staging_clone_dir = Path(temp_clone_parent) / "fibo_repo"
        clone_cmd = [
            "clone",
            "--depth",
            "1",
            "--branch",
            tag,
            "--",
            repo_url,
            str(staging_clone_dir),
        ]
        proc = _run_git(clone_cmd)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to clone FIBO repository:\n{proc.stderr}\n"
                "Provide --offline-archive, --source-dir, or --mock-fixture."
            )
        rev_proc = _run_git(["-C", str(staging_clone_dir), "rev-parse", "HEAD"])
        if rev_proc.returncode != 0:
            raise RuntimeError(f"git rev-parse HEAD failed on {staging_clone_dir}")
        verified_head = rev_proc.stdout.strip()
        if verified_head != expected_commit:
            raise RuntimeError(
                f"Cloned repository HEAD ({verified_head}) does not match expected "
                f"pinned commit ({expected_commit}) for tag '{tag}'."
            )
        staging_clone_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging_clone_dir), str(git_cache_dir))
        git_manifest.write_text(
            json.dumps(
                {
                    "cached_at": datetime.now(UTC).isoformat(),
                    "commit": verified_head,
                    "created_by": "opendv-vendor-fibo",
                    "head": verified_head,
                    "tag": tag,
                }
            ),
            encoding="utf-8",
        )
    return git_cache_dir, verified_head


def _release_zip_url(tag: str, *, allow_dev: bool) -> str:
    clean_tag = tag.strip()
    zip_tag = clean_tag[len("master_") :] if clean_tag.startswith("master_") else clean_tag
    template = DEFAULT_DEV_ZIP_URL_TEMPLATE if allow_dev else DEFAULT_PROD_ZIP_URL_TEMPLATE
    return template.format(tag=zip_tag)


def ensure_fibo_source(
    *,
    cache_dir: Path,
    offline_archive: Path | None = None,
    source_dir: Path | None = None,
    archive_sha256: str | None = None,
    repo_url: str = "https://github.com/edmcouncil/fibo.git",
    tag: str = "master_2026Q3",
    expected_commit: str = "2d73435a4419b66b7c9f44cb9763e35fe8ac7890",
    is_production: bool = False,
    pinned_archive_sha256: str | None = None,
    allow_dev: bool = False,
    source_mode: Literal["https_zip", "git"] = "https_zip",
    zip_url: str | None = None,
) -> tuple[Path, str]:
    """Ensure FIBO source files are locally available via source_dir, archive, HTTPS zip, or git."""
    if not re.match(r"^[0-9a-f]{40}$", expected_commit.lower()):
        raise ValueError(
            f"Invalid expected commit '{expected_commit}'; must be a 40-character hex SHA"
        )
    if tag.startswith("-"):
        raise ValueError(f"Invalid tag '{tag}'; cannot start with '-'")

    if source_dir is not None:
        resolved_dir = source_dir.resolve()
        if not resolved_dir.is_dir():
            raise FileNotFoundError(f"Source directory not found: {source_dir}")
        try:
            reject_symlinks_on_path_and_ancestors(resolved_dir)
        except OpenDVError as exc:
            raise ArchiveSecurityError(str(exc)) from exc
        if is_production:
            raise RuntimeError(
                "Cannot compile to production package from unverified --source-dir."
            )
        return resolved_dir, f"unverified-dir-{tag}"

    manifest_path = cache_dir / SCRIPT_CACHE_MARKER
    if cache_dir.exists() and any(cache_dir.iterdir()) and not manifest_path.is_file():
        raise RuntimeError(
            f"Refusing to mutate or overwrite unmanaged directory '{cache_dir}'. "
            f"Directory exists and does not contain manifest ({SCRIPT_CACHE_MARKER})."
        )

    cache_dir.mkdir(parents=True, exist_ok=True)
    if not manifest_path.is_file():
        manifest_path.write_text(json.dumps({"created_by": "opendv-vendor-fibo"}), encoding="utf-8")

    if offline_archive is not None:
        if not offline_archive.is_file():
            raise FileNotFoundError(f"Offline archive not found: {offline_archive}")
        actual_sha = hash_file_sha256(offline_archive)
        if archive_sha256 is not None and actual_sha.lower() != archive_sha256.lower():
            raise RuntimeError(
                f"Offline archive SHA-256 mismatch for {offline_archive}: "
                f"computed {actual_sha}, expected {archive_sha256}"
            )
        extract_dir = cache_dir / "offline" / f"archive_{actual_sha[:12]}"
        archive_manifest = extract_dir / ".opendv_archive_manifest.json"
        if is_production or not (extract_dir.is_dir() and archive_manifest.is_file()):
            safe_extract_archive(offline_archive, extract_dir)
            archive_manifest.write_text(
                json.dumps(
                    {
                        "archive_sha256": actual_sha,
                        "created_by": "opendv-vendor-fibo",
                        "extracted_at": datetime.now(UTC).isoformat(),
                        "source_archive": offline_archive.name,
                    }
                ),
                encoding="utf-8",
            )
        if pinned_archive_sha256 and actual_sha.lower() == pinned_archive_sha256.lower():
            return extract_dir, expected_commit
        if is_production:
            raise RuntimeError(
                "Cannot compile to production package from unverified offline archive; "
                "untrusted offline archives prohibited."
            )
        return extract_dir, f"unverified-{actual_sha[:12]}"

    if source_mode == "https_zip":
        effective_url = zip_url or _release_zip_url(tag, allow_dev=allow_dev)
        expected_zip_sha = archive_sha256 or (
            None if allow_dev else pinned_archive_sha256
        )
        if is_production and expected_zip_sha is None:
            raise RuntimeError(
                "Production HTTPS release download requires a pinned archive SHA-256."
            )
        zip_name = "dev.ttl.zip" if allow_dev else "prod.ttl.zip"
        cached_zip = cache_dir / "releases" / f"{tag}_{zip_name}"
        if (
            cached_zip.is_file()
            and expected_zip_sha is not None
            and hash_file_sha256(cached_zip).lower() == expected_zip_sha.lower()
        ):
            actual_sha = expected_zip_sha.lower()
        else:
            actual_sha = download_fibo_archive(
                effective_url,
                cached_zip,
                expected_sha256=expected_zip_sha,
            )
        extract_dir = cache_dir / "rel" / actual_sha[:8]
        manifest_file = extract_dir / ".opendv_archive_manifest.json"
        if is_production or not (extract_dir.is_dir() and manifest_file.is_file()):
            safe_extract_archive(cached_zip, extract_dir)
            manifest_file.write_text(
                json.dumps(
                    {
                        "archive_sha256": actual_sha,
                        "created_by": "opendv-vendor-fibo",
                        "extracted_at": datetime.now(UTC).isoformat(),
                        "source_url": effective_url,
                    }
                ),
                encoding="utf-8",
            )
        commit_ref = (
            expected_commit
            if (pinned_archive_sha256 and actual_sha.lower() == pinned_archive_sha256.lower())
            or (expected_zip_sha and actual_sha.lower() == expected_zip_sha.lower())
            else f"unverified-{actual_sha[:12]}"
        )
        return extract_dir, commit_ref

    return _acquire_via_git(
        cache_dir=cache_dir,
        repo_url=repo_url,
        tag=tag,
        expected_commit=expected_commit,
    )
