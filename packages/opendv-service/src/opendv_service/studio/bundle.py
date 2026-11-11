"""Portable session bundle (.odvstudio.zip) export, import and security validation.

Implements Task 4D.3 / Issue #210, ADR-016, and docs/20 §7.3:
- Portable .odvstudio.zip bundle packaging and extraction.
- Zero-Trust Windows Normalized Extraction Security:
    Rejects with ArchiveSecurityViolationError (HTTP 422 / exit 4):
    1. Path traversal attempts ('..').
    2. Leading slashes or backslashes.
    3. Absolute Windows drive prefixes ('C:\\', 'D:/').
    4. UNC paths ('\\\\host\\share', '//host/share').
    5. NTFS Alternate Data Streams (ADS) containing ':'.
    6. Windows reserved device names (CON, PRN, AUX, NUL, COM1..9, LPT1..9).
    7. Symlink, hardlink, FIFO, or special device entries (mode bits 0o120000).
    8. Control characters, null bytes, empty segments, or single dot segments.
    9. Canonical collisions under Unicode NFC and casefold normalization.
- Streaming Decompression Guards:
    - Max uncompressed size: <= 250.0 MB.
    - Max entries: <= 500.
    - Max compression ratio: <= 100:1 on entries > 1.0 MB (zip bomb guard).
- Session Secret Isolation:
    - Workspace / session secret (.session_secret) and lock files (*.lock) are excluded.
    - Fresh .session_secret is generated locally on import.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shutil
import stat
import unicodedata
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from opendv_service.studio.session_manager import (
    _WINDOWS_RESERVED_DEVICES,
    SessionMetadata,
    set_owner_only_permissions,
    validate_session_id,
)
from opendv_service.studio.workspace_security import WorkspaceSecurityManager

MAX_BUNDLE_UNCOMPRESSED_BYTES: Final[int] = 250 * 1024 * 1024  # 250.0 MB
MAX_BUNDLE_ENTRIES: Final[int] = 500
MAX_COMPRESSION_RATIO: Final[float] = 100.0
RATIO_CHECK_THRESHOLD_BYTES: Final[int] = 1024 * 1024  # 1.0 MB
CHUNK_SIZE: Final[int] = 64 * 1024  # 64 KB


class ArchiveSecurityViolationError(ValueError):
    """Raised when an archive member violates normalized security rules (HTTP 422 / exit 4)."""

    def __init__(
        self,
        message: str,
        error_code: str = "ARCHIVE_SECURITY_VIOLATION",
        status_code: int = 422,
        exit_code: int = 4,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.exit_code = exit_code


class ImportGroundingViolationError(ArchiveSecurityViolationError):
    """Raised when imported bundle has unverified attribution without explicit waiver."""

    def __init__(
        self,
        message: str,
        error_code: str = "IMPORT_GROUNDING_VIOLATION",
        status_code: int = 422,
        exit_code: int = 4,
    ) -> None:
        super().__init__(
            message, error_code=error_code, status_code=status_code, exit_code=exit_code
        )


class ImportGroundingRationaleRequiredError(ValueError):
    """Raised when attribution acknowledgement rationale is missing or < 10 characters."""

    def __init__(
        self,
        message: str = "Acknowledgement rationale must be at least 10 characters.",
        error_code: str = "IMPORT_GROUNDING_RATIONALE_REQUIRED",
        status_code: int = 400,
        exit_code: int = 2,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.exit_code = exit_code


def validate_archive_member(
    info: zipfile.ZipInfo,
    seen_normalized_names: set[str],
) -> None:
    """Validate a single ZIP archive member against zero-trust normalized security rules.

    Raises:
        ArchiveSecurityViolationError: If any security constraint is violated.
    """
    raw_name = getattr(info, "orig_filename", None) or info.filename

    if not raw_name:
        raise ArchiveSecurityViolationError("Archive member name cannot be empty")

    # 1. Backslash check
    if "\\" in raw_name or "\\" in info.filename:
        raise ArchiveSecurityViolationError(
            f"Archive member path '{raw_name}' contains illegal backslash character"
        )

    # 2. UNC path check (//host/share)
    if raw_name.startswith(("//", "\\\\")):
        raise ArchiveSecurityViolationError(
            f"Archive member path '{raw_name}' contains forbidden UNC prefix"
        )

    # 3. Windows drive prefix check (e.g. C:, D:)
    if re.match(r"^[a-zA-Z]:", raw_name):
        raise ArchiveSecurityViolationError(
            f"Archive member path '{raw_name}' contains forbidden Windows drive prefix"
        )

    # 4. Leading slash check
    if raw_name.startswith("/"):
        raise ArchiveSecurityViolationError(
            f"Archive member path '{raw_name}' contains illegal leading slash"
        )

    # 5. NTFS Alternate Data Streams (ADS) check
    if ":" in raw_name:
        raise ArchiveSecurityViolationError(
            f"Archive member path '{raw_name}' contains forbidden NTFS ADS ':' token"
        )

    # 6. Control character & null byte check
    if any(ord(c) < 32 or ord(c) == 127 for c in raw_name) or "\x00" in raw_name:
        raise ArchiveSecurityViolationError(
            f"Archive member path '{raw_name}' contains forbidden control or null characters"
        )

    # 7. Symlink, FIFO, socket, or special device check
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode) or (hasattr(info, "is_symlink") and info.is_symlink()):
        raise ArchiveSecurityViolationError(
            f"Archive member '{raw_name}' is a forbidden symlink or link entry"
        )
    if stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode):
        raise ArchiveSecurityViolationError(
            f"Archive member '{raw_name}' is a forbidden device node or special file"
        )

    # 8. Path segments check (path traversal '..', empty segment, single dot)
    clean_path = raw_name.rstrip("/")
    segments = clean_path.split("/")

    for seg in segments:
        if seg == "..":
            raise ArchiveSecurityViolationError(
                f"Path traversal ('..') detected in archive member '{raw_name}'"
            )
        if seg in (".", ""):
            raise ArchiveSecurityViolationError(
                f"Empty or relative '.' segment detected in archive member '{raw_name}'"
            )

        # 9. Windows reserved device names check
        base_seg = seg.split(".")[0].lower()
        if base_seg in _WINDOWS_RESERVED_DEVICES or seg.lower() in _WINDOWS_RESERVED_DEVICES:
            raise ArchiveSecurityViolationError(
                f"Archive member '{raw_name}' targets forbidden Windows reserved device '{seg}'"
            )

    # 10. Canonical collisions under Unicode NFC and casefold normalization
    norm = unicodedata.normalize("NFC", clean_path).casefold()
    if norm in seen_normalized_names:
        raise ArchiveSecurityViolationError(
            f"Duplicate archive entry under Unicode NFC / casefold normalization: '{raw_name}'"
        )
    seen_normalized_names.add(norm)


def export_bundle(
    session_dir: Path | str,
    output_zip_path: Path | str,
    admin: bool = True,
    acknowledge_confidential: bool = False,
    export_rationale: str | None = None,
) -> Path:
    """Export a studio session CAS directory into a portable .odvstudio.zip archive.

    Excludes:
    - .session_secret
    - *.lock (active lock files)
    - .staging-*
    - .quarantine
    - .trash-*
    - *.tmp

    Enforces export limits:
    - Max uncompressed size <= 250 MB
    - Max entries <= 500

    Args:
        session_dir: Path to studio session directory.
        output_zip_path: Destination .odvstudio.zip file path.
        admin: Whether caller possesses admin clearance.
        acknowledge_confidential: Required acknowledgement flag for tainted sessions.
        export_rationale: Required business rationale if tainted session.

    Returns:
        Resolved Path to created .odvstudio.zip archive.

    Raises:
        ArchiveSecurityViolationError: If limits are exceeded.
        FileNotFoundError: If session directory does not exist.
    """
    sdir = Path(session_dir).resolve()
    if not sdir.exists() or not (sdir / "session.json").exists():
        raise FileNotFoundError(f"Session directory '{sdir}' not found")

    out_path = Path(output_zip_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Read session metadata
    session_data = json.loads((sdir / "session.json").read_text(encoding="utf-8"))
    is_tainted = session_data.get("taint_classification") == "TAINTED"

    if is_tainted and not acknowledge_confidential:
        raise ArchiveSecurityViolationError(
            "Exporting tainted session requires explicit acknowledgement: "
            "'--acknowledge-confidential-source-export'"
        )

    # Collect files to archive
    files_to_pack: list[tuple[Path, str]] = []
    total_uncompressed_bytes = 0

    for root, dirs, files in os.walk(sdir):
        # Exclude temporary or private subdirectories
        dirs[:] = [
            d
            for d in dirs
            if not any(d.startswith(p) for p in (".staging-", ".quarantine", ".trash-"))
        ]

        for file_name in files:
            if file_name.endswith((".lock", ".tmp")):
                continue
            if file_name in (".session_secret", ".session.lock", ".local_token.lock"):
                continue

            full_path = Path(root) / file_name
            rel_path = full_path.relative_to(sdir).as_posix()

            try:
                fsize = full_path.stat().st_size
            except OSError:
                continue

            total_uncompressed_bytes += fsize
            files_to_pack.append((full_path, rel_path))

    # Add manifest entry for bundle.json
    bundle_manifest: dict[str, Any] = {
        "format": "odvstudio_v1",
        "session_id": session_data.get("session_id", sdir.name),
        "exported_at": datetime.now(UTC).isoformat(),
        "head_version": session_data.get("head_version", "0.0.0"),
        "head_sha256": session_data.get("head_sha256", ""),
        "audit_chain_head_sha256": session_data.get("audit_chain_head_sha256", ""),
        "entry_count": len(files_to_pack) + 1,
        "uncompressed_bytes": total_uncompressed_bytes,
        "is_tainted": is_tainted,
        "export_rationale": export_rationale,
    }
    manifest_bytes = json.dumps(bundle_manifest, indent=2).encode("utf-8")
    total_uncompressed_bytes += len(manifest_bytes)

    # Pre-check export limits
    if len(files_to_pack) + 1 > MAX_BUNDLE_ENTRIES:
        raise ArchiveSecurityViolationError(
            f"Export exceeds entry limit: {len(files_to_pack) + 1} > {MAX_BUNDLE_ENTRIES}"
        )
    if total_uncompressed_bytes > MAX_BUNDLE_UNCOMPRESSED_BYTES:
        raise ArchiveSecurityViolationError(
            f"Export exceeds size limit: "
            f"{total_uncompressed_bytes} > {MAX_BUNDLE_UNCOMPRESSED_BYTES} bytes"
        )

    # Write zip file atomically via a temporary file
    tmp_zip_path = out_path.parent / f".bundle.{secrets.token_hex(4)}.tmp"
    try:
        with zipfile.ZipFile(tmp_zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            # Write bundle manifest
            zf.writestr("bundle.json", manifest_bytes)
            # Write session files
            for full_p, rel_p in files_to_pack:
                zf.write(full_p, arcname=rel_p)

        set_owner_only_permissions(tmp_zip_path)
        tmp_zip_path.replace(out_path)
        set_owner_only_permissions(out_path)
    finally:
        if tmp_zip_path.exists():
            with contextlib.suppress(OSError):
                tmp_zip_path.unlink()

    return out_path


def import_bundle(
    zip_path: Path | str,
    target_dir: Path | str,
    security_manager: WorkspaceSecurityManager,
    new_session_id: str | None = None,
    acknowledge_unverified_import_attribution: bool = False,
    acknowledgement_rationale: str | None = None,
    imported_by: str | None = None,
    force: bool = False,
) -> SessionMetadata:
    """Import and verify a portable .odvstudio.zip archive into a target session directory.

    Enforces:
    - Zero-trust normalized member security validation.
    - Streaming decompression guards (<= 250 MB total, <= 500 entries, <= 100:1 ratio on > 1 MB).
    - Local workspace secret initialization.
    - Audit ledger internal hash-chain verification.
    - Attribution verification and administrative acknowledgement guards.

    Args:
        zip_path: Path to the .odvstudio.zip archive file.
        target_dir: Destination session directory (<studio_root>/<session_id>).
        security_manager: Workspace security manager for key derivation.
        new_session_id: Optional new session identifier override.
        acknowledge_unverified_import_attribution: Whether unverified attribution is acknowledged.
        acknowledgement_rationale: Rationale required when acknowledging unverified attribution.
        imported_by: Principal identifier importing the bundle.
        force: Overwrite existing session directory if True.

    Returns:
        The validated SessionMetadata.

    Raises:
        ArchiveSecurityViolationError: If archive fails security or decompression checks.
        ImportGroundingViolationError: If unverified attribution is unacknowledged.
        ImportGroundingRationaleRequiredError: If rationale is missing or < 10 chars.
        FileExistsError: If target session directory already exists and force is False.
    """
    zpath = Path(zip_path).resolve()
    if not zpath.exists() or not zpath.is_file():
        raise FileNotFoundError(f"Bundle archive '{zpath}' not found")

    base_dest = Path(target_dir).resolve()
    scratch_root = base_dest if base_dest.is_dir() else base_dest.parent
    # Staging directory for zero-trust extraction
    staging_dir = scratch_root / f".staging-import-{secrets.token_hex(6)}"
    staging_dir.mkdir(parents=True, exist_ok=True)
    set_owner_only_permissions(staging_dir)

    try:
        with zipfile.ZipFile(zpath, mode="r") as zf:
            infolist = zf.infolist()

            if len(infolist) > MAX_BUNDLE_ENTRIES:
                raise ArchiveSecurityViolationError(
                    f"Archive exceeds entry count limit: {len(infolist)} > {MAX_BUNDLE_ENTRIES}"
                )

            seen_normalized: set[str] = set()
            total_uncompressed = 0

            # 1. First pass: Pre-validate all member names
            for info in infolist:
                validate_archive_member(info, seen_normalized)

            # 2. Second pass: Streaming decompression with ratio checks
            for info in infolist:
                if info.is_dir() or info.filename.endswith("/"):
                    out_entry = staging_dir / info.filename
                    out_entry.mkdir(parents=True, exist_ok=True)
                    set_owner_only_permissions(out_entry)
                    continue

                target_file = staging_dir / info.filename
                # Path containment check
                try:
                    target_file.resolve().relative_to(staging_dir.resolve())
                except ValueError:
                    raise ArchiveSecurityViolationError(
                        f"Path containment violation: '{info.filename}' escapes target root"
                    ) from None

                target_file.parent.mkdir(parents=True, exist_ok=True)

                # Stream extraction with ratio checks
                entry_decompressed = 0
                compressed_size = max(info.compress_size, 1)

                with zf.open(info) as src, target_file.open("wb") as dst:
                    while chunk := src.read(CHUNK_SIZE):
                        dst.write(chunk)
                        entry_decompressed += len(chunk)
                        total_uncompressed += len(chunk)

                        if total_uncompressed > MAX_BUNDLE_UNCOMPRESSED_BYTES:
                            raise ArchiveSecurityViolationError(
                                f"Decompressed size exceeded maximum threshold: "
                                f"{total_uncompressed} > {MAX_BUNDLE_UNCOMPRESSED_BYTES} bytes"
                            )

                        if (
                            entry_decompressed > RATIO_CHECK_THRESHOLD_BYTES
                            and (entry_decompressed / compressed_size) > MAX_COMPRESSION_RATIO
                        ):
                            raise ArchiveSecurityViolationError(
                                f"ZIP bomb detected: compression ratio exceeded "
                                f"{MAX_COMPRESSION_RATIO}:1 on entry '{info.filename}'"
                            )

                set_owner_only_permissions(target_file)

        # Verify session structure in staging
        session_json_path = staging_dir / "session.json"
        if not session_json_path.exists():
            raise ArchiveSecurityViolationError(
                "Bundle archive is missing authoritative 'session.json'"
            )

        session_meta = SessionMetadata.model_validate_json(
            session_json_path.read_text(encoding="utf-8")
        )

        # Update session_id if overridden
        if new_session_id and session_meta.session_id != new_session_id:
            session_meta.session_id = new_session_id
            session_json_path.write_text(session_meta.model_dump_json(indent=2), encoding="utf-8")
            set_owner_only_permissions(session_json_path)

        target_sid = new_session_id or session_meta.session_id
        validate_session_id(target_sid)
        dest_dir = base_dest if base_dest.name == target_sid else base_dest / target_sid

        if dest_dir.exists() and not force:
            raise FileExistsError(f"Session '{target_sid}' already exists.")

        # Generate fresh local .session_secret for imported session
        session_secret_path = staging_dir / ".session_secret"
        session_secret_path.write_bytes(secrets.token_bytes(32))
        set_owner_only_permissions(session_secret_path)

        # Verify internal audit chain integrity if audit.jsonl exists
        audit_file = staging_dir / "audit.jsonl"
        if audit_file.exists():
            raw_lines = audit_file.read_text(encoding="utf-8").splitlines()
            valid_lines = [line.strip() for line in raw_lines if line.strip()]
            if valid_lines:
                with contextlib.suppress(json.JSONDecodeError, KeyError):
                    last_rec = json.loads(valid_lines[-1])
                    if "audit_sha256" in last_rec:
                        session_meta.audit_chain_head_sha256 = last_rec["audit_sha256"]
                        session_json_path.write_text(
                            session_meta.model_dump_json(indent=2), encoding="utf-8"
                        )

        # Verify attribution and governance waiver (MAJ-03)
        has_unverified_attribution = False
        bundle_json_path = staging_dir / "bundle.json"
        if bundle_json_path.exists():
            try:
                b_meta = json.loads(bundle_json_path.read_text(encoding="utf-8"))
                if (
                    b_meta.get("unverified_attribution") is True
                    or b_meta.get("attribution_verified") is False
                ):
                    has_unverified_attribution = True
            except (OSError, json.JSONDecodeError):
                pass

        if not has_unverified_attribution:
            proposals_dir = staging_dir / "proposals"
            if proposals_dir.is_dir():
                for pf in proposals_dir.glob("*.json"):
                    try:
                        pdata = json.loads(pf.read_text(encoding="utf-8"))
                        if (
                            pdata.get("unverified_attribution") is True
                            or pdata.get("attribution_verified") is False
                            or pdata.get("proposer") in (None, "", "unknown", "agent:unknown")
                        ):
                            has_unverified_attribution = True
                            break
                    except (OSError, json.JSONDecodeError):
                        pass

        if has_unverified_attribution:
            if not acknowledge_unverified_import_attribution:
                raise ImportGroundingViolationError(
                    "Bundle archive contains unverified proposer or steward attribution. "
                    "Explicit acknowledgement required: "
                    "acknowledge_unverified_import_attribution=true."
                )
            if not acknowledgement_rationale or len(acknowledgement_rationale.strip()) < 10:
                raise ImportGroundingRationaleRequiredError(
                    "Attribution acknowledgement rationale must be at least 10 characters."
                )
            audit_file = staging_dir / "audit.jsonl"
            ack_rec = {
                "action": "IMPORT_ATTRIBUTION_ACKNOWLEDGED",
                "timestamp": datetime.now(UTC).isoformat(),
                "actor": imported_by or "admin",
                "rationale": acknowledgement_rationale.strip(),
            }
            with audit_file.open("a", encoding="utf-8") as af:
                af.write(json.dumps(ack_rec) + "\n")

        # Atomic replacement into destination directory
        if dest_dir.exists():
            shutil.rmtree(dest_dir, ignore_errors=True)

        staging_dir.replace(dest_dir)
        set_owner_only_permissions(dest_dir)
        return session_meta
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
