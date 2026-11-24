#!/usr/bin/env python3
"""Archive security and upstream source acquisition helpers for scripts/vendor_fibo.py."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

GIT_BIN: str = shutil.which("git") or "git"

MAX_ARCHIVE_MEMBERS: int = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES: int = 200 * 1024 * 1024  # 200 MB
FILE_ATTRIBUTE_REPARSE_POINT: int = 0x400
_ARCHIVE_STREAM_CHUNK_BYTES: int = 65_536
SCRIPT_CACHE_MARKER: str = ".opendv_cache_manifest.json"


class ArchiveSecurityError(ValueError):
    """Raised when an archive violates traversal, symlink/reparse, or decompression budgets."""


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


def _extract_tar_safely(archive_path: Path, staging_dir: Path, max_bytes: int) -> None:
    with tarfile.open(archive_path, "r:*") as tar:
        members = tar.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ArchiveSecurityError(
                f"Archive member count ({len(members)}) exceeds limit: {MAX_ARCHIVE_MEMBERS}"
            )
        total_bytes = 0
        for member in members:
            member_name = str(member.name)
            if (
                member_name.startswith(("/", "\\"))
                or PureWindowsPath(member_name).is_absolute()
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
            total_bytes += max(0, member.size)
            if total_bytes > max_bytes:
                raise ArchiveSecurityError(
                    f"Archive uncompressed size exceeds limit ({max_bytes} bytes)"
                )
        if hasattr(tarfile, "data_filter"):
            tar.extractall(staging_dir, filter="data")
        else:
            tar.extractall(staging_dir)


def _extract_zip_safely(archive_path: Path, staging_dir: Path, max_bytes: int) -> None:
    with zipfile.ZipFile(archive_path, "r") as zf:
        infolist = zf.infolist()
        if len(infolist) > MAX_ARCHIVE_MEMBERS:
            raise ArchiveSecurityError(
                f"Archive member count ({len(infolist)}) exceeds limit: {MAX_ARCHIVE_MEMBERS}"
            )
        header_total = 0
        streamed_total = 0
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
                dest_path.mkdir(parents=True, exist_ok=True)
                continue
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info, "r") as src, dest_path.open("wb") as dst:
                    while chunk := src.read(_ARCHIVE_STREAM_CHUNK_BYTES):
                        streamed_total += len(chunk)
                        if streamed_total > max_bytes:
                            raise ArchiveSecurityError(
                                f"Archive uncompressed size exceeds limit ({max_bytes} bytes)"
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

    with tempfile.TemporaryDirectory(dir=target_dir.parent) as temp_staging:
        staging_dir = (Path(temp_staging) / "contents").resolve()
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
            shutil.rmtree(target_dir)
        shutil.move(str(staging_dir), str(target_dir))
    return target_dir


def ensure_fibo_source(
    *,
    cache_dir: Path,
    offline_archive: Path | None = None,
    archive_sha256: str | None = None,
    repo_url: str = "https://github.com/edmcouncil/fibo.git",
    tag: str = "master_2024Q4",
    expected_commit: str = "69dbbe7a6a4e32049ba3f3988b48877bc954f061",
    is_production: bool = False,
    pinned_archive_sha256: str | None = None,
) -> tuple[Path, str]:
    """Ensure FIBO source files are locally available and verify git provenance."""
    if not re.match(r"^[0-9a-f]{40}$", expected_commit.lower()):
        raise ValueError(
            f"Invalid expected commit '{expected_commit}'; must be a 40-character hex SHA"
        )
    if tag.startswith("-"):
        raise ValueError(f"Invalid tag '{tag}'; cannot start with '-'")

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

    git_cache_dir = cache_dir / "git"
    git_manifest = cache_dir / "git_manifest.json"
    if git_cache_dir.is_dir() and (git_cache_dir / ".git" / "config").is_file():
        remote_proc = subprocess.run(
            [GIT_BIN, "-C", str(git_cache_dir), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        )
        if (
            remote_proc.returncode == 0
            and remote_proc.stdout.strip()
            and remote_proc.stdout.strip() != repo_url
        ):
            shutil.rmtree(git_cache_dir)

    if git_cache_dir.is_dir() and (git_cache_dir / ".git").is_dir():
        rev_cmd = [GIT_BIN, "-C", str(git_cache_dir), "rev-parse", "HEAD"]
        rev_proc = subprocess.run(rev_cmd, capture_output=True, text=True, check=False)
        if rev_proc.returncode != 0:
            raise RuntimeError(f"Corrupted git cache in {git_cache_dir}: rev-parse HEAD failed")
        actual_head = rev_proc.stdout.strip()
        if actual_head != expected_commit:
            co_cmd = [
                GIT_BIN,
                "-C",
                str(git_cache_dir),
                "checkout",
                "--detach",
                expected_commit,
                "--",
            ]
            co_proc = subprocess.run(co_cmd, capture_output=True, text=True, check=False)
            if co_proc.returncode != 0:
                fetch_cmd = [
                    GIT_BIN,
                    "-C",
                    str(git_cache_dir),
                    "fetch",
                    "--depth",
                    "1",
                    "origin",
                    expected_commit,
                ]
                subprocess.run(fetch_cmd, capture_output=True, text=True, check=False)
                subprocess.run(co_cmd, capture_output=True, text=True, check=False)
            rev2 = subprocess.run(rev_cmd, capture_output=True, text=True, check=False)
            new_head = rev2.stdout.strip() if rev2.returncode == 0 else ""
            if new_head != expected_commit:
                raise RuntimeError(
                    f"Failed to check out expected commit {expected_commit} in {git_cache_dir}. "
                    f"Current HEAD is {new_head or 'unknown'}."
                )
            actual_head = new_head
        status_proc = subprocess.run(
            [GIT_BIN, "-C", str(git_cache_dir), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            check=False,
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
            GIT_BIN,
            "clone",
            "--depth",
            "1",
            "--branch",
            tag,
            "--",
            repo_url,
            str(staging_clone_dir),
        ]
        proc = subprocess.run(clone_cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to clone FIBO repository:\n{proc.stderr}\n"
                "Provide --offline-archive or --mock-fixture."
            )
        rev_proc = subprocess.run(
            [GIT_BIN, "-C", str(staging_clone_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
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
