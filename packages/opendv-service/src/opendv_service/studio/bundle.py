"""Portable session bundle (.odvstudio.zip) export, import and security validation.

Implements Task 4D.3 / Issue #210, ADR-014, ADR-016, and docs/20 §7.3:
- Portable .odvstudio.zip bundle packaging and extraction under L5 session lock.
- Zero-Trust Windows Normalized Extraction Security:
    Rejects with ArchiveSecurityViolationError (HTTP 422 / exit 4):
    1. Path traversal attempts ('..').
    2. Leading slashes or backslashes.
    3. Absolute Windows drive prefixes ('C:\\', 'D:/').
    4. UNC paths ('\\\\host\\share', '//host/share').
    5. NTFS Alternate Data Streams (ADS) containing ':'.
    6. Windows reserved device names (CON, PRN, AUX, NUL, COM1..9, LPT1..9).
    7. Symlink, Windows reparse point (0x0400), hardlink, FIFO, or special device entries.
    8. Control characters, null bytes, empty segments, or single dot segments.
    9. Canonical collisions under Unicode NFC and casefold normalization.
- Streaming Decompression Guards:
    - Max uncompressed size: <= 250.0 MB.
    - Max entries: <= 500.
    - Max compression ratio: <= 100:1 on entries > 1.0 MB (with central-directory compress_size
      clamped to physical archive byte size).
- Full Delta Replay & Canonical JCS Digest Verification (IMPORT_DELTA_REPLAY_HASH_MISMATCH).
- Cross-Workspace Audit Chain Verification & Local K_audit Re-Anchoring (ADR-014).
- Effective Taint Propagation (T_effective = T_manifest U T_imported) & Confidentiality Gates.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import unicodedata
import zipfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from opendv_contracts.dtos.ontology import OntologyDelta, StewardWaiverRecord
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.genesis import GENESIS_VERSION, create_genesis_ontology
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256, dumps_jcs_bytes
from opendv_service.ontology.applier import apply as apply_delta
from opendv_service.ontology.matching.verifier import get_default_grounding_verifier
from opendv_service.ontology.validator import (
    InvalidGroundingWaiverError,
    StructuralValidator,
    UngroundedConceptError,
)
from opendv_service.studio.atomic_commit import (
    ForbiddenAdminRequiredError,
    TamperEvidentChainCorruptError,
    collect_touched_concept_uris,
    fsync_directory_best_effort,
    parse_semver_tuple,
    verify_ont_evo_002_post_state_grounding,
)
from opendv_service.studio.locks import (
    L5_SESSION,
    SESSION_FENCE_FILENAME,
    reentrant_session_lock,
    studio_lock,
    write_session_fence_marker,
)
from opendv_service.studio.session_manager import (
    _WINDOWS_RESERVED_DEVICES,
    SessionAlreadyExistsError,
    SessionMetadata,
    _atomic_write_session_json,
    append_signed_audit_record,
    normalize_version_tag,
    sanitize_domain_namespace,
    validate_session_id,
)
from opendv_service.studio.workspace_security import (
    InvalidWorkspacePathError,
    WorkspaceSecurityManager,
    replace_with_retry,
    set_owner_only_permissions,
    validate_workspace_containment,
)

MAX_BUNDLE_UNCOMPRESSED_BYTES: Final[int] = 250 * 1024 * 1024  # 250.0 MB
MAX_BUNDLE_ENTRIES: Final[int] = 500
MAX_COMPRESSION_RATIO: Final[float] = 100.0
RATIO_CHECK_THRESHOLD_BYTES: Final[int] = 1024 * 1024  # 1.0 MB
CHUNK_SIZE: Final[int] = 64 * 1024  # 64 KB
_WIN_FILE_ATTRIBUTE_REPARSE_POINT: Final[int] = 0x0400


class ArchiveSecurityViolationError(OpenDVError, ValueError):
    """Raised when an archive member violates normalized security rules (HTTP 422 / exit 4)."""

    error_code = ErrorCode.ARCHIVE_SECURITY_VIOLATION

    def __init__(
        self,
        message: str,
        error_code: str | ErrorCode = ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        status_code: int = 422,
        exit_code: int = 4,
    ) -> None:
        resolved = (
            error_code
            if isinstance(error_code, ErrorCode)
            else ErrorCode._value2member_map_.get(error_code, ErrorCode.ARCHIVE_SECURITY_VIOLATION)
        )
        OpenDVError.__init__(self, message, error_code=resolved)
        ValueError.__init__(self, message)
        self.error_code = resolved
        self.status_code, self.http_status, self.exit_code = status_code, status_code, exit_code


class ImportLineageBrokenError(ArchiveSecurityViolationError):
    """Raised when imported bundle version lineage is broken or missing delta.json (422 / 4)."""

    error_code = ErrorCode.IMPORT_LINEAGE_BROKEN

    def __init__(
        self,
        message: str,
        error_code: str | ErrorCode = ErrorCode.IMPORT_LINEAGE_BROKEN,
        status_code: int = 422,
        exit_code: int = 4,
    ) -> None:
        super().__init__(
            message, error_code=error_code, status_code=status_code, exit_code=exit_code
        )


class ImportProvisionalUriForbiddenError(ArchiveSecurityViolationError):
    """Raised when imported bundle ontology contains provisional URIs (422 / exit 4)."""

    error_code = ErrorCode.IMPORT_PROVISIONAL_URI_FORBIDDEN

    def __init__(
        self,
        message: str,
        error_code: str | ErrorCode = ErrorCode.IMPORT_PROVISIONAL_URI_FORBIDDEN,
        status_code: int = 422,
        exit_code: int = 4,
    ) -> None:
        super().__init__(
            message, error_code=error_code, status_code=status_code, exit_code=exit_code
        )


class BundleAuditChainCorruptError(ArchiveSecurityViolationError, TamperEvidentChainCorruptError):
    """Raised when an imported bundle's audit.jsonl hash-chain is tampered (HTTP 422 / exit 4)."""

    error_code = ErrorCode.TAMPER_EVIDENT_CHAIN_CORRUPT

    def __init__(
        self,
        message: str,
        error_code: str | ErrorCode = ErrorCode.TAMPER_EVIDENT_CHAIN_CORRUPT,
        status_code: int = 422,
        exit_code: int = 4,
    ) -> None:
        ArchiveSecurityViolationError.__init__(
            self,
            message,
            error_code=error_code,
            status_code=status_code,
            exit_code=exit_code,
        )


class ImportDeltaReplayHashMismatchError(ArchiveSecurityViolationError):
    """Raised when imported version ontology or delta replay SHA-256 mismatches (422 / exit 4)."""

    error_code = ErrorCode.IMPORT_DELTA_REPLAY_HASH_MISMATCH

    def __init__(
        self,
        message: str,
        error_code: str | ErrorCode = ErrorCode.IMPORT_DELTA_REPLAY_HASH_MISMATCH,
        status_code: int = 422,
        exit_code: int = 4,
    ) -> None:
        super().__init__(
            message, error_code=error_code, status_code=status_code, exit_code=exit_code
        )


class ImportGroundingViolationError(ArchiveSecurityViolationError):
    """Raised when imported bundle has unverified attribution or foreign waiver without ack."""

    error_code = ErrorCode.IMPORT_GROUNDING_VIOLATION

    def __init__(
        self,
        message: str,
        error_code: str | ErrorCode = ErrorCode.IMPORT_GROUNDING_VIOLATION,
        status_code: int = 422,
        exit_code: int = 4,
    ) -> None:
        super().__init__(
            message, error_code=error_code, status_code=status_code, exit_code=exit_code
        )


class ImportAttributionUnverifiedError(ImportGroundingViolationError):
    """Raised when an imported steward waiver fails local K_waiver HMAC verification."""


class ImportGroundingRationaleRequiredError(OpenDVError, ValueError):
    """Raised when attribution acknowledgement rationale is missing or < 10 chars (400 / exit 1)."""

    error_code = ErrorCode.IMPORT_GROUNDING_RATIONALE_REQUIRED

    def __init__(
        self,
        message: str = "Acknowledgement rationale must be at least 10 characters.",
        error_code: str | ErrorCode = ErrorCode.IMPORT_GROUNDING_RATIONALE_REQUIRED,
        status_code: int = 400,
        exit_code: int = 1,
    ) -> None:
        ValueError.__init__(self, message)
        self.message = message
        self.error_code = (  # type: ignore[assignment]
            error_code
            if isinstance(error_code, ErrorCode)
            else ErrorCode.IMPORT_GROUNDING_RATIONALE_REQUIRED
        )
        self.status_code = status_code
        self.http_status = status_code
        self.exit_code = exit_code
        self.details = {}


class ConfidentialSourceExportUnacknowledgedError(ArchiveSecurityViolationError):
    """Raised when exporting a tainted session without confidentiality acknowledgement (422 / 4)."""

    error_code = ErrorCode.CONFIDENTIAL_SOURCE_EXPORT_UNACKNOWLEDGED

    def __init__(
        self,
        message: str = (
            "Exporting tainted session requires explicit acknowledgement: "
            "'--acknowledge-confidential-source-export'"
        ),
        error_code: str | ErrorCode = ErrorCode.CONFIDENTIAL_SOURCE_EXPORT_UNACKNOWLEDGED,
        status_code: int = 422,
        exit_code: int = 4,
    ) -> None:
        super().__init__(
            message, error_code=error_code, status_code=status_code, exit_code=exit_code
        )


class ExportRationaleRequiredError(OpenDVError, ValueError):
    """Raised when exporting a tainted session without a >= 10 char rationale (400 / exit 1)."""

    error_code = ErrorCode.EXPORT_RATIONALE_REQUIRED

    def __init__(
        self,
        message: str = "Export rationale must be at least 10 characters for tainted sessions.",
        error_code: str | ErrorCode = ErrorCode.EXPORT_RATIONALE_REQUIRED,
        status_code: int = 400,
        exit_code: int = 1,
    ) -> None:
        ValueError.__init__(self, message)
        self.message = message
        self.error_code = (  # type: ignore[assignment]
            error_code if isinstance(error_code, ErrorCode) else ErrorCode.EXPORT_RATIONALE_REQUIRED
        )
        self.status_code = status_code
        self.http_status = status_code
        self.exit_code = exit_code
        self.details = {}


class AdminRoleRequiredError(ForbiddenAdminRequiredError):
    """Raised when non-admin principal attempts confidential bundle export (403 / exit 7)."""


def compute_effective_session_taint(
    session_dir: Path | str,
    session_data: dict[str, Any] | None = None,
    bundle_meta: dict[str, Any] | None = None,
) -> bool:
    """Compute effective confidentiality taint T_effective = T_manifest U T_imported.

    Inspects:
    1. `session_data` / `session.json` (`taint_classification == "TAINTED"`).
    2. `bundle_meta` / `bundle.json` (`is_tainted is True` or `taint_classification == "TAINTED"`).
    3. `taint_index.json` (`is_tainted` / `tainted` or non-empty tainted source sets).
    4. `inputs/manifest.json` (`sources` + `files` for `do_not_send_to_agent`, `tainted`,
       `confidential`, or `CONFIDENTIAL`/`RESTRICTED` taint classifications on active entries).
    """
    sdir = Path(session_dir)

    if session_data is None:
        sjson = sdir / "session.json"
        if sjson.is_file():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                loaded = json.loads(sjson.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    session_data = loaded

    if isinstance(session_data, dict) and session_data.get("taint_classification") == "TAINTED":
        return True

    if bundle_meta is None:
        bjson = sdir / "bundle.json"
        if bjson.is_file():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                loaded_b = json.loads(bjson.read_text(encoding="utf-8"))
                if isinstance(loaded_b, dict):
                    bundle_meta = loaded_b

    if isinstance(bundle_meta, dict) and (
        bundle_meta.get("is_tainted") is True
        or bundle_meta.get("taint_classification") == "TAINTED"
    ):
        return True

    taint_idx_path = sdir / "taint_index.json"
    if taint_idx_path.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            tdata = json.loads(taint_idx_path.read_text(encoding="utf-8"))
            if isinstance(tdata, dict):
                if tdata.get("is_tainted") is True or tdata.get("tainted") is True:
                    return True
                for key in (
                    "tainted_source_ids",
                    "tainted_sources",
                    "confidential_sources",
                    "do_not_send_to_agent",
                    "do_not_send_source_ids",
                    "do_not_send_sources",
                    "tainted_sha256s",
                    "sources",
                ):
                    coll = tdata.get(key)
                    if isinstance(coll, (list, dict)) and len(coll) > 0:
                        return True

    manifest_path = sdir / "inputs" / "manifest.json"
    if manifest_path.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            mdata = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(mdata, dict):
                entries: list[Any] = []
                for key in ("sources", "files"):
                    coll = mdata.get(key)
                    if isinstance(coll, dict):
                        entries.extend(coll.values())
                    elif isinstance(coll, list):
                        entries.extend(coll)
                for item in entries:
                    if not isinstance(item, dict):
                        continue
                    if item.get("tombstoned") or item.get("deleted"):
                        continue
                    raw_item_taint = (
                        str(item.get("taint") or item.get("taint_classification") or "")
                        .strip()
                        .upper()
                    )
                    if (
                        item.get("do_not_send_to_agent") is True
                        or item.get("tainted") is True
                        or item.get("confidential") is True
                        or raw_item_taint
                        in ("TAINTED", "CONFIDENTIAL", "RESTRICTED", "DO_NOT_SEND")
                    ):
                        return True

    return False


def validate_archive_member(
    info: zipfile.ZipInfo,
    seen_normalized_names: set[str],
) -> None:
    """Validate a single ZIP archive member against zero-trust normalized security rules."""
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

    # 7. Symlink, Windows reparse point (0x0400), FIFO, socket, or special device check
    mode = info.external_attr >> 16
    is_win_reparse = info.create_system == 0 and bool(
        info.external_attr & _WIN_FILE_ATTRIBUTE_REPARSE_POINT
    )
    if stat.S_ISLNK(mode) or is_win_reparse or (hasattr(info, "is_symlink") and info.is_symlink()):
        raise ArchiveSecurityViolationError(
            f"Archive member '{raw_name}' is a forbidden symlink, reparse point, or link entry"
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


def _reject_symlinks_on_path_and_ancestors(
    raw_path: Path,
    stop_roots: Sequence[Path | str],
    label: str,
) -> None:
    """Reject '..' segments and symlinks on `raw_path` and its ancestors up to `stop_roots`."""
    if ".." in raw_path.parts:
        raise InvalidWorkspacePathError(
            f"{label} '{raw_path}' must not contain '..' traversal segments."
        )
    if raw_path.is_symlink():
        raise InvalidWorkspacePathError(f"{label} '{raw_path.name}' must not be a symlink.")

    stop_norm: set[str] = set()
    for sr in stop_roots:
        sr_path = Path(sr)
        if sr_path.is_symlink():
            raise InvalidWorkspacePathError(
                f"Configured workspace root '{sr_path.name}' must not be a symlink."
            )
        with contextlib.suppress(OSError):
            stop_norm.add(os.path.normcase(str(sr_path.resolve())))

    cur = raw_path if raw_path.is_absolute() else (Path.cwd() / raw_path)
    while True:
        if cur.is_symlink():
            raise InvalidWorkspacePathError(f"{label} ancestor '{cur.name}' must not be a symlink.")
        with contextlib.suppress(OSError):
            if os.path.normcase(str(cur.resolve())) in stop_norm:
                break
        if cur.parent == cur:
            break
        cur = cur.parent


def export_bundle(
    session_dir: Path | str,
    output_zip_path: Path | str,
    admin: bool = True,
    acknowledge_confidential: bool = False,
    export_rationale: str | None = None,
    *,
    security_manager: WorkspaceSecurityManager | None = None,
    actor: str = "admin",
    overwrite: bool = False,
) -> Path:
    """Export a studio session CAS directory into a portable .odvstudio.zip archive under L5 lock.

    Excludes:
    - .session_secret
    - .session_fenced
    - *.lock (active lock files)
    - .staging and .staging-*
    - .quarantine*
    - .trash-*
    - *.tmp
    """
    raw_sdir = Path(session_dir)
    raw_out = Path(output_zip_path)

    env_roots = os.environ.get("OPENDV_WORKSPACE_ROOTS", "").strip()
    env_studio = os.environ.get("OPENDV_STUDIO_ROOT", "").strip()
    env_ws = os.environ.get("OPENDV_WORKSPACE_ROOT", "").strip()

    if env_roots:
        ws_root_list: list[Path | str] = [
            r.strip() for r in env_roots.split(os.pathsep) if r.strip()
        ]
        sess_allowed_roots: list[Path | str] = list(ws_root_list)
        if security_manager is not None:
            sess_allowed_roots.append(security_manager.studio_root)
        if env_studio:
            sess_allowed_roots.append(env_studio)

        out_allowed_roots: list[Path | str] = list(ws_root_list)
        if security_manager is not None:
            out_allowed_roots.append(security_manager.studio_root / ".scratch")
        if env_studio:
            out_allowed_roots.append(Path(env_studio) / ".scratch")
    else:
        sess_allowed_roots = [os.getcwd()]
        if security_manager is not None:
            sec_r = security_manager.studio_root
            sess_allowed_roots.extend([sec_r, sec_r.parent, sec_r.parent.parent])
        else:
            s_parent = (raw_sdir if raw_sdir.is_absolute() else (Path.cwd() / raw_sdir)).parent
            sess_allowed_roots.extend([s_parent, s_parent.parent])
        if env_studio:
            sess_allowed_roots.extend([env_studio, Path(env_studio).parent])
        if env_ws:
            sess_allowed_roots.extend([env_ws, Path(env_ws).parent])

        out_allowed_roots = list(sess_allowed_roots)
        if security_manager is not None:
            out_allowed_roots.append(security_manager.studio_root / ".scratch")

    _reject_symlinks_on_path_and_ancestors(raw_sdir, sess_allowed_roots, "Export session directory")
    sdir = validate_workspace_containment(raw_sdir, allowed_roots=sess_allowed_roots)
    if sdir.is_symlink():
        raise InvalidWorkspacePathError(
            f"Export session directory '{sdir.name}' must not be a symlink."
        )
    if not sdir.exists() or not (sdir / "session.json").exists():
        raise FileNotFoundError(f"Session directory '{sdir}' not found")
    if (sdir / "session.json").is_symlink():
        raise InvalidWorkspacePathError("Session metadata 'session.json' must not be a symlink.")

    _reject_symlinks_on_path_and_ancestors(raw_out, out_allowed_roots, "Export output path")
    raw_out_parent = raw_out.parent if str(raw_out.parent) not in ("", ".") else Path.cwd()
    validate_workspace_containment(raw_out_parent, allowed_roots=out_allowed_roots)
    out_path = validate_workspace_containment(raw_out, allowed_roots=out_allowed_roots)
    if out_path.is_symlink():
        raise InvalidWorkspacePathError(
            f"Export output file '{out_path.name}' must not be a symlink."
        )

    if out_path == sdir or out_path.is_relative_to(sdir):
        raise InvalidWorkspacePathError(
            f"Export output file '{out_path}' must not reside inside session directory '{sdir}'."
        )

    if (raw_out.exists() or out_path.exists()) and not overwrite:
        raise InvalidWorkspacePathError(
            f"Export output file already exists: '{out_path}' (pass overwrite=True to replace)"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)

    lock_file = sdir / ".session.lock"
    with reentrant_session_lock(lock_file, session_id=sdir.name):
        session_data = json.loads((sdir / "session.json").read_text(encoding="utf-8"))
        is_tainted = compute_effective_session_taint(sdir, session_data=session_data)

        if is_tainted:
            if not admin:
                raise AdminRoleRequiredError(
                    "Exporting a confidential/tainted session requires administrator role."
                )
            if not acknowledge_confidential:
                raise ConfidentialSourceExportUnacknowledgedError(
                    "Exporting tainted session requires explicit acknowledgement: "
                    "'--acknowledge-confidential-source-export'"
                )
            if not export_rationale or len(export_rationale.strip()) < 10:
                raise ExportRationaleRequiredError(
                    "Exporting a tainted session requires export_rationale >= 10 characters."
                )

            if security_manager is not None or (sdir.parent / ".odv_workspace_secret").is_file():
                append_signed_audit_record(
                    session_dir=sdir,
                    action="CONFIDENTIAL_BUNDLE_EXPORTED",
                    actor=actor,
                    details={
                        "export_rationale": export_rationale.strip(),
                        "archive_name": out_path.name,
                    },
                    security_manager=security_manager,
                )
                session_data = json.loads((sdir / "session.json").read_text(encoding="utf-8"))

        files_to_pack: list[tuple[Path, str]] = []
        total_uncompressed_bytes = 0

        for root, dirs, files in os.walk(sdir):
            dirs[:] = [
                d
                for d in dirs
                if d != ".staging"
                and not any(d.startswith(p) for p in (".staging-", ".quarantine", ".trash-"))
            ]

            for file_name in sorted(files):
                if file_name.endswith((".lock", ".tmp")):
                    continue
                if file_name in (
                    ".session_secret",
                    ".session.lock",
                    ".local_token.lock",
                    ".session_fenced",
                ):
                    continue

                full_path = Path(root) / file_name
                rel_path = full_path.relative_to(sdir).as_posix()
                if rel_path == "bundle.json":
                    continue

                try:
                    fsize = full_path.stat().st_size
                except OSError:
                    continue

                total_uncompressed_bytes += fsize
                files_to_pack.append((full_path, rel_path))

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
            "export_rationale": export_rationale.strip() if export_rationale else None,
        }
        manifest_bytes = json.dumps(bundle_manifest, indent=2).encode("utf-8")
        total_uncompressed_bytes += len(manifest_bytes)

        if len(files_to_pack) + 1 > MAX_BUNDLE_ENTRIES:
            raise ArchiveSecurityViolationError(
                f"Export exceeds entry limit: {len(files_to_pack) + 1} > {MAX_BUNDLE_ENTRIES}"
            )
        if total_uncompressed_bytes > MAX_BUNDLE_UNCOMPRESSED_BYTES:
            raise ArchiveSecurityViolationError(
                f"Export exceeds size limit: "
                f"{total_uncompressed_bytes} > {MAX_BUNDLE_UNCOMPRESSED_BYTES} bytes"
            )

        tmp_zip_path = out_path.parent / f".bundle.{secrets.token_hex(4)}.tmp"
        try:
            with zipfile.ZipFile(tmp_zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("bundle.json", manifest_bytes)
                for full_p, rel_p in files_to_pack:
                    zf.write(full_p, arcname=rel_p)

            with tmp_zip_path.open("r+b") as fp:
                fp.flush()
                os.fsync(fp.fileno())
            set_owner_only_permissions(tmp_zip_path)
            replace_with_retry(tmp_zip_path, out_path)
            set_owner_only_permissions(out_path)
        finally:
            if tmp_zip_path.exists():
                with contextlib.suppress(OSError):
                    tmp_zip_path.unlink()

    return out_path


def _parse_utc_timestamp(raw_val: Any) -> datetime | None:
    """Parse an ISO-8601 string or datetime into a timezone-aware UTC datetime."""
    if isinstance(raw_val, datetime):
        return raw_val.astimezone(UTC) if raw_val.tzinfo else raw_val.replace(tzinfo=UTC)
    if not isinstance(raw_val, str) or not raw_val.strip():
        return None
    txt = raw_val.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def _verify_audit_chain_records_for_timestamps(
    audit_path: Path,
    expected_tail_sha: str | None,
) -> list[dict[str, Any]] | None:
    """Return records from `audit_path` only if its JCS hash chain and tail digest verify."""
    if not audit_path.is_file() or audit_path.is_symlink():
        return None
    try:
        raw_lines = audit_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    valid_lines = [ln.strip() for ln in raw_lines if ln.strip()]
    if not valid_lines:
        return None

    expected_prev: str | None = None
    records: list[dict[str, Any]] = []
    for line in valid_lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(rec, dict):
            return None
        actual_sha = rec.get("audit_sha256")
        prev_sha = rec.get("prev_audit_sha256")
        if not isinstance(actual_sha, str) or len(actual_sha) != 64:
            return None
        if prev_sha != expected_prev:
            return None
        recon = {k: v for k, v in rec.items() if k not in ("audit_sha256", "hmac_signature")}
        computed_sha = hashlib.sha256(dumps_jcs_bytes(recon)).hexdigest().lower()
        if computed_sha != actual_sha:
            return None
        records.append(rec)
        expected_prev = actual_sha

    if expected_tail_sha and expected_tail_sha != "0" * 64 and expected_prev != expected_tail_sha:
        return None
    return records


def _extract_verified_commit_timestamps(
    staging_dir: Path,
    session_meta: SessionMetadata,
    bundle_meta: dict[str, Any] | None = None,
) -> dict[tuple[str, str], datetime]:
    """Extract verified `(version_tag, target_sha256) -> commit_timestamp` from audit chains."""
    expected_declared_tail = session_meta.audit_chain_head_sha256
    if isinstance(bundle_meta, dict) and bundle_meta.get("audit_chain_head_sha256"):
        expected_declared_tail = str(bundle_meta["audit_chain_head_sha256"])

    def _collect_from_chain(
        audit_path: Path, expected_tail: str | None, visited: set[str]
    ) -> list[dict[str, Any]]:
        norm_name = audit_path.name
        if norm_name in visited:
            return []
        visited.add(norm_name)
        recs = _verify_audit_chain_records_for_timestamps(audit_path, expected_tail)
        if recs is None:
            return []
        combined: list[dict[str, Any]] = []
        for rec in recs:
            if rec.get("action") == "SESSION_IMPORTED":
                det = rec.get("details")
                if isinstance(det, dict):
                    arch_name = str(det.get("archived_audit_file") or "").strip()
                    arch_tail = str(det.get("imported_audit_tail_sha256") or "").strip()
                    if arch_name and "/" not in arch_name and "\\" not in arch_name:
                        arch_path = staging_dir / arch_name
                        combined.extend(_collect_from_chain(arch_path, arch_tail or None, visited))
            combined.append(rec)
        return combined

    all_records = _collect_from_chain(staging_dir / "audit.jsonl", expected_declared_tail, set())
    commit_ts_map: dict[tuple[str, str], datetime] = {}
    for rec in all_records:
        if rec.get("action") != "COMMITTED":
            continue
        dt = _parse_utc_timestamp(rec.get("timestamp"))
        if dt is None:
            continue
        det = rec.get("details") if isinstance(rec.get("details"), dict) else {}
        raw_ver = str(rec.get("target_version") or det.get("target_version") or "").strip()
        raw_sha = str(det.get("target_sha256") or rec.get("head_sha256") or "").strip().lower()
        if raw_ver and len(raw_sha) == 64:
            commit_ts_map[(normalize_version_tag(raw_ver), raw_sha)] = dt
    return commit_ts_map


def _extract_version_waivers(
    vdir: Path,
    delta: OntologyDelta,
    expected_parent_sha: str,
    *,
    commit_timestamp: datetime | None = None,
) -> list[StewardWaiverRecord]:
    """Extract and structurally validate StewardWaiverRecords attached to an imported version."""
    raw_items: list[Any] = []
    for fname in ("waivers.json", "meta.json", "metadata.json", "evaluation.json"):
        fpath = vdir / fname
        if not fpath.is_file():
            continue
        try:
            vdata = json.loads(fpath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ImportGroundingViolationError(
                f"Corrupt waiver metadata file '{fname}' in '{vdir.name}': {exc}"
            ) from exc
        if isinstance(vdata, list):
            raw_items.extend(vdata)
        elif isinstance(vdata, dict):
            if isinstance(vdata.get("waivers"), list):
                raw_items.extend(vdata["waivers"])
            if isinstance(vdata.get("waiver"), dict):
                raw_items.append(vdata["waiver"])

    if not raw_items:
        return []

    # Compute allowed delta digests EXCLUSIVELY from the replayed delta.json object and its
    # deterministic pre/post-normalization envelope combinations (NEVER from untrusted meta.json!).
    parent_variants: list[str | None] = [delta.parent_sha256, "", None]
    if expected_parent_sha:
        parent_variants.append(expected_parent_sha)
    target_ver_variants: list[str | None] = [delta.target_ontology_version, None]

    allowed_delta_shas: set[str] = set()
    for p_val in parent_variants:
        for t_val in target_ver_variants:
            cand_delta = delta.model_copy(
                update={
                    "parent_sha256": p_val,
                    "target_ontology_version": t_val,
                }
            )
            allowed_delta_shas.add(cand_delta.canonical_sha256())

    seen_keys: set[tuple[str, tuple[str, ...], str]] = set()
    validated: list[StewardWaiverRecord] = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise ImportGroundingViolationError(
                f"Malformed non-object steward waiver entry in '{vdir.name}'."
            )
        try:
            w = StewardWaiverRecord.model_validate(item)
        except (ValidationError, ValueError, TypeError) as exc:
            raise ImportGroundingViolationError(
                f"Invalid StewardWaiverRecord in '{vdir.name}': {exc}"
            ) from exc

        dedup_key = (
            str(w.proposal_id),
            tuple(sorted(w.target_concept_uris)),
            w.signature_or_token,
        )
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        if w.authorized_role != "admin":
            raise ImportGroundingViolationError(
                f"Waiver in '{vdir.name}' requires authorized_role='admin', "
                f"got '{w.authorized_role}'."
            )
        if not w.rationale or len(w.rationale.strip()) < 10:
            raise ImportGroundingViolationError(
                f"Waiver rationale in '{vdir.name}' must be at least 10 characters."
            )
        if not (w.signature_or_token or "").strip():
            raise ImportGroundingViolationError(
                f"Waiver in '{vdir.name}' is missing cryptographic signature_or_token."
            )
        prop_sha = (w.proposal_delta_sha256 or "").strip().lower()
        delta_sha_matched = False
        for cand_sha in allowed_delta_shas:
            if hmac.compare_digest(prop_sha, cand_sha.strip().lower()):
                delta_sha_matched = True
        if not delta_sha_matched:
            raise ImportGroundingViolationError(
                f"Waiver proposal_delta_sha256 '{w.proposal_delta_sha256}' does not match "
                f"delta digest in '{vdir.name}'."
            )
        if expected_parent_sha and not hmac.compare_digest(
            (w.base_sha256 or "").strip().lower(),
            expected_parent_sha.strip().lower(),
        ):
            raise ImportGroundingViolationError(
                f"Waiver base_sha256 '{w.base_sha256}' does not match parent ontology SHA-256 "
                f"'{expected_parent_sha}' in '{vdir.name}'."
            )
        if w.expires_at is not None:
            if commit_timestamp is None:
                raise ImportGroundingViolationError(
                    f"Waiver '{w.waiver_id}' in '{vdir.name}' specifies expires_at "
                    "but version commit timestamp is missing or invalid."
                )
            issued_at = w.created_at.astimezone(UTC)
            ref_ts = commit_timestamp.astimezone(UTC)
            exp_ts = w.expires_at.astimezone(UTC)
            if not (issued_at <= ref_ts < exp_ts):
                raise ImportGroundingViolationError(
                    f"Waiver '{w.waiver_id}' in '{vdir.name}' was not active at commit time "
                    f"(issued_at={issued_at.isoformat()}, commit_ts={ref_ts.isoformat()}, "
                    f"expires_at={exp_ts.isoformat()})."
                )
        validated.append(w)

    return validated


def _scan_ontology_for_provisional_uris(
    ont: GraphAOntology,
    raw_ont_text: str,
    version_label: str,
) -> None:
    """Reject any provisional URNs in imported ontology snapshots."""
    for forbidden_prefix in ("urn:ontology:provisional:", "urn:opendv:provisional:"):
        if forbidden_prefix in raw_ont_text:
            raise ImportProvisionalUriForbiddenError(
                f"Imported ontology in '{version_label}' contains forbidden provisional URI "
                f"prefix '{forbidden_prefix}'."
            )
    sv = StructuralValidator()
    scan_fn = getattr(sv, "scan_provisional_tokens", None)
    if callable(scan_fn):
        try:
            scan_fn(ont)
        except Exception as exc:
            raise ImportProvisionalUriForbiddenError(
                f"Imported ontology in '{version_label}' failed provisional URI scan: {exc}"
            ) from exc


def _verify_versions_and_replay_deltas(
    staging_dir: Path,
    session_meta: SessionMetadata,
    *,
    security_manager: WorkspaceSecurityManager | None = None,
    acknowledge_unverified_import_attribution: bool = False,
    is_admin: bool = False,
    bundle_meta: dict[str, Any] | None = None,
) -> None:
    """Verify canonical JCS ontology digests, provisional URIs, delta lineage, and grounding."""
    verified_commit_ts = _extract_verified_commit_timestamps(
        staging_dir, session_meta, bundle_meta=bundle_meta
    )
    versions_dir = staging_dir / "versions"
    head_ver_clean = session_meta.head_version.lstrip("v")
    head_ver_tag = normalize_version_tag(head_ver_clean)
    head_ont_path = versions_dir / head_ver_tag / "ontology.json"

    if not versions_dir.is_dir() or not head_ont_path.is_file():
        raise ImportDeltaReplayHashMismatchError(
            f"Bundle is missing authoritative ontology.json for head version '{head_ver_tag}'."
        )

    ver_records: list[
        tuple[tuple[int, int, int], str, Path, GraphAOntology, str, dict[str, Any]]
    ] = []

    for vdir in sorted(versions_dir.iterdir()):
        if not vdir.is_dir():
            continue
        v_clean = vdir.name.lstrip("v")
        try:
            v_tuple = parse_semver_tuple(v_clean)
        except ValueError as exc:
            raise ArchiveSecurityViolationError(
                f"Invalid version directory name '{vdir.name}' in bundle."
            ) from exc

        ont_path = vdir / "ontology.json"
        if not ont_path.is_file():
            raise ImportDeltaReplayHashMismatchError(
                f"Version directory '{vdir.name}' is missing ontology.json."
            )

        try:
            raw_ont_bytes = ont_path.read_bytes()
            raw_ont_text = raw_ont_bytes.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ImportDeltaReplayHashMismatchError(
                f"Corrupt ontology.json in '{vdir.name}': {exc}"
            ) from exc

        if "urn:ontology:provisional:" in raw_ont_text or "urn:opendv:provisional:" in raw_ont_text:
            raise ImportProvisionalUriForbiddenError(
                f"Imported ontology in '{vdir.name}' contains forbidden provisional URIs."
            )

        try:
            ont = GraphAOntology.model_validate_json(raw_ont_text)
        except (ValidationError, ValueError) as exc:
            if "provisional" in str(exc).lower():
                raise ImportProvisionalUriForbiddenError(
                    f"Imported ontology in '{vdir.name}' contains forbidden provisional URI: {exc}"
                ) from exc
            raise ImportDeltaReplayHashMismatchError(
                f"Corrupt ontology.json in '{vdir.name}': {exc}"
            ) from exc

        _scan_ontology_for_provisional_uris(ont, raw_ont_text, vdir.name)
        computed_sha = compute_ontology_sha256(ont)

        meta_dict: dict[str, Any] = {}
        for mname in ("meta.json", "metadata.json"):
            mpath = vdir / mname
            if mpath.is_file():
                try:
                    loaded_m = json.loads(mpath.read_text(encoding="utf-8"))
                    if isinstance(loaded_m, dict):
                        meta_dict = loaded_m
                        break
                except (OSError, json.JSONDecodeError) as exc:
                    raise ImportDeltaReplayHashMismatchError(
                        f"Corrupt {mname} in '{vdir.name}': {exc}"
                    ) from exc

        recorded_sha = meta_dict.get("target_sha256")
        if recorded_sha is not None and recorded_sha != computed_sha:
            raise ImportDeltaReplayHashMismatchError(
                f"Version '{vdir.name}' ontology SHA-256 '{computed_sha}' mismatches "
                f"meta.json target_sha256 '{recorded_sha}'."
            )

        ver_records.append((v_tuple, v_clean, vdir, ont, computed_sha, meta_dict))

    ver_records.sort(key=lambda item: item[0])

    head_matches = [sha for _, vc, _, _, sha, _ in ver_records if vc == head_ver_clean]
    if not head_matches or head_matches[0] != session_meta.head_sha256:
        actual_head = head_matches[0] if head_matches else "missing"
        raise ImportDeltaReplayHashMismatchError(
            f"Head version '{head_ver_tag}' ontology SHA-256 '{actual_head}' does not match "
            f"session.json head_sha256 '{session_meta.head_sha256}'."
        )

    # Synthesize fallback genesis ontology for 0.0.0 parent resolution if v0.0.0 dir wasn't packed
    clean_ns = sanitize_domain_namespace(session_meta.domain_namespace)
    genesis_fallback = create_genesis_ontology(
        domain_namespace=clean_ns,
        enterprise_owner=session_meta.enterprise_owner or "Enterprise Architecture",
    )
    genesis_fallback_sha = compute_ontology_sha256(genesis_fallback)

    verified_ont_by_sha: dict[str, GraphAOntology] = {}
    active_grandfathered: set[str] = set()
    active_waived_uris: set[str] = set()
    grounding_verifier = get_default_grounding_verifier()

    root_ont = genesis_fallback
    root_sha256 = genesis_fallback_sha
    root_came_from_v0_dir = False
    start_replay_idx = 0

    if ver_records:
        _, first_vc, first_vdir, first_ont, first_sha, first_meta = ver_records[0]
        first_delta_file = first_vdir / "delta.json"
        first_mode = str(first_meta.get("mode") or "").strip().lower()

        if first_vc == GENESIS_VERSION or first_mode == "genesis":
            expected_genesis = create_genesis_ontology(
                domain_namespace=sanitize_domain_namespace(
                    first_ont.domain_namespace or session_meta.domain_namespace
                ),
                enterprise_owner=(
                    first_ont.enterprise_owner
                    or session_meta.enterprise_owner
                    or "Enterprise Architecture"
                ),
            )
            expected_genesis_sha = compute_ontology_sha256(expected_genesis)
            has_non_genesis_elements = bool(
                first_ont.canonical_business_concepts
                or first_ont.canonical_relationships
                or first_ont.reference_enumerations
                or getattr(first_ont, "properties", None)
                or getattr(first_ont, "shared_attributes", None)
            )
            if (
                first_vc != GENESIS_VERSION
                or has_non_genesis_elements
                or first_sha not in (genesis_fallback_sha, expected_genesis_sha)
            ):
                raise ImportDeltaReplayHashMismatchError(
                    f"Root genesis ontology in '{first_vdir.name}' (SHA-256 '{first_sha}') "
                    f"does not match genuine GenesisDraft digest ('{expected_genesis_sha}')."
                )

            root_ont = first_ont
            root_sha256 = first_sha
            root_came_from_v0_dir = True
            verified_ont_by_sha[first_sha] = first_ont
            start_replay_idx = 1

            if first_delta_file.is_file():
                try:
                    delta0 = OntologyDelta.model_validate_json(
                        first_delta_file.read_text(encoding="utf-8")
                    )
                except (OSError, ValidationError, ValueError) as exc:
                    raise ImportDeltaReplayHashMismatchError(
                        f"Invalid delta.json in '{first_vdir.name}': {exc}"
                    ) from exc
                d0_parent = (delta0.parent_sha256 or "").strip().lower()
                if d0_parent and d0_parent != root_sha256:
                    raise ImportDeltaReplayHashMismatchError(
                        f"Genesis 0.0.0 delta.parent_sha256 '{d0_parent}' does not match "
                        f"root ontology SHA-256 '{root_sha256}'."
                    )
                if delta0.operations:
                    raise ImportDeltaReplayHashMismatchError(
                        "Genesis version 'v0.0.0' cannot carry non-empty delta operations."
                    )
        elif not first_delta_file.is_file() and first_mode == "seed":
            root_ont = first_ont
            root_sha256 = first_sha
            root_came_from_v0_dir = True
            verified_ont_by_sha[first_sha] = first_ont
            start_replay_idx = 1
            root_cbcs = {c.concept_uri for c in first_ont.canonical_business_concepts}
            raw_gf = first_meta.get("grandfathered_concept_uris")
            if isinstance(raw_gf, list):
                active_grandfathered = {str(u) for u in raw_gf if str(u) in root_cbcs}
            else:
                active_grandfathered = set(root_cbcs)
        elif not first_delta_file.is_file():
            raise ImportLineageBrokenError(
                f"Initial version '{first_vdir.name}' is missing required delta.json and is not "
                "a valid genesis or workspace-seeded root."
            )
        else:
            verified_ont_by_sha[genesis_fallback_sha] = genesis_fallback

    for idx in range(start_replay_idx, len(ver_records)):
        _, v_clean, vdir, _, actual_sha, meta_dict = ver_records[idx]
        v_mode = str(meta_dict.get("mode") or "").strip().lower()

        if v_clean == GENESIS_VERSION or v_mode in ("seed", "genesis"):
            raise ImportLineageBrokenError(
                f"Non-root version '{vdir.name}' illegally declares mode='{v_mode}' or "
                "genesis version; only the single initial root version may skip delta replay."
            )

        delta_path = vdir / "delta.json"
        if not delta_path.is_file():
            raise ImportLineageBrokenError(
                f"Non-root version '{vdir.name}' is missing required delta.json for replay."
            )

        try:
            delta = OntologyDelta.model_validate_json(delta_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            raise ImportDeltaReplayHashMismatchError(
                f"Invalid delta.json in '{vdir.name}': {exc}"
            ) from exc

        raw_delta_parent = (delta.parent_sha256 or "").strip().lower()
        raw_meta_parent = str(meta_dict.get("parent_sha256") or "").strip().lower()
        if raw_delta_parent and raw_meta_parent and raw_delta_parent != raw_meta_parent:
            raise ImportDeltaReplayHashMismatchError(
                f"Delta parent_sha256 '{raw_delta_parent}' disagrees with meta.json "
                f"parent_sha256 '{raw_meta_parent}' in '{vdir.name}'."
            )

        # First child of root (idx == start_replay_idx) with non-empty parent_sha256 MUST match root
        if (
            idx == start_replay_idx
            and root_came_from_v0_dir
            and raw_delta_parent
            and raw_delta_parent != root_sha256
        ):
            raise ImportDeltaReplayHashMismatchError(
                f"First child delta '{vdir.name}' parent_sha256 '{raw_delta_parent}' does not "
                f"match root ontology SHA-256 '{root_sha256}'."
            )

        effective_parent_lookup = raw_delta_parent or raw_meta_parent
        parent_ont = (
            verified_ont_by_sha.get(effective_parent_lookup) if effective_parent_lookup else None
        )
        if (
            parent_ont is None
            and not effective_parent_lookup
            and (delta.base_ontology_version or "").lstrip("v") == GENESIS_VERSION
        ):
            parent_ont = root_ont

        if parent_ont is None:
            raise ImportDeltaReplayHashMismatchError(
                f"Cannot resolve verified parent ontology "
                f"(parent_sha256='{effective_parent_lookup}') for '{vdir.name}'."
            )

        expected_parent_sha = compute_ontology_sha256(parent_ont)
        if raw_delta_parent and raw_delta_parent != expected_parent_sha:
            raise ImportDeltaReplayHashMismatchError(
                f"Delta parent_sha256 '{raw_delta_parent}' in '{vdir.name}' does not match "
                f"verified parent ontology SHA-256 '{expected_parent_sha}'."
            )
        if raw_meta_parent and raw_meta_parent != expected_parent_sha:
            raise ImportDeltaReplayHashMismatchError(
                f"Metadata parent_sha256 '{raw_meta_parent}' in '{vdir.name}' does not match "
                f"verified parent ontology SHA-256 '{expected_parent_sha}'."
            )

        apply_base_ont = parent_ont
        if delta.domain_namespace and parent_ont.ontology_version == GENESIS_VERSION:
            c_ns = sanitize_domain_namespace(delta.domain_namespace)
            if c_ns != parent_ont.domain_namespace:
                apply_base_ont = parent_ont.model_copy(update={"domain_namespace": c_ns})

        try:
            apply_base_sha = compute_ontology_sha256(apply_base_ont)
            replay_delta = (
                delta
                if delta.parent_sha256 == apply_base_sha
                else delta.model_copy(update={"parent_sha256": apply_base_sha})
            )
            replayed_ont = apply_delta(apply_base_ont, replay_delta, validate_structural=True)
            replayed_ont.parent_sha256 = expected_parent_sha
            replayed_sha = compute_ontology_sha256(replayed_ont)
        except Exception as exc:
            raise ImportDeltaReplayHashMismatchError(
                f"Delta replay failed on version '{vdir.name}': {exc}"
            ) from exc

        if replayed_sha != actual_sha:
            raise ImportDeltaReplayHashMismatchError(
                f"IMPORT_DELTA_REPLAY_HASH_MISMATCH on '{vdir.name}': replayed delta digest "
                f"'{replayed_sha}' != stored ontology digest '{actual_sha}'."
            )

        # Enforce ONT-EVO-002 grounding on replayed post-root version
        touched_uris = collect_touched_concept_uris(apply_base_ont, delta)
        active_grandfathered -= touched_uris
        active_waived_uris -= touched_uris

        v_tag = normalize_version_tag(v_clean)
        commit_ts = verified_commit_ts.get((v_tag, actual_sha.lower()))
        if commit_ts is None:
            commit_ts = _parse_utc_timestamp(
                meta_dict.get("commit_timestamp")
                or meta_dict.get("committed_at")
                or meta_dict.get("created_at")
            )

        version_waivers = _extract_version_waivers(
            vdir, delta, expected_parent_sha, commit_timestamp=commit_ts
        )
        waived_this_version: set[str] = set()
        for w in version_waivers:
            sig_ok = security_manager is not None and security_manager.verify_waiver_signature(
                w.signing_payload().encode("utf-8"), w.signature_or_token
            )
            if not sig_ok and not (acknowledge_unverified_import_attribution and is_admin):
                if (staging_dir / "audit.jsonl").is_file():
                    _verify_imported_audit_chain_integrity(
                        staging_dir, session_meta, bundle_meta=bundle_meta
                    )
                raise ImportAttributionUnverifiedError(
                    f"Steward waiver '{w.waiver_id}' in '{vdir.name}' failed local K_waiver "
                    "HMAC verification. Explicit admin acknowledgement required: "
                    "acknowledge_unverified_import_attribution=true."
                )
            active_waived_uris.update(w.target_concept_uris)
            waived_this_version.update(w.target_concept_uris)

        non_exempt_uris = {
            c.concept_uri
            for c in replayed_ont.canonical_business_concepts
            if c.concept_uri not in active_grandfathered and c.concept_uri not in active_waived_uris
        }
        uris_to_verify = (touched_uris - waived_this_version) | non_exempt_uris
        if uris_to_verify:
            try:
                verify_ont_evo_002_post_state_grounding(
                    apply_base_ont,
                    delta,
                    replayed_ont,
                    waivers=[],
                    security_manager=security_manager,
                    grounding_verifier=grounding_verifier,
                    explicit_touched_uris=uris_to_verify,
                )
            except (UngroundedConceptError, InvalidGroundingWaiverError) as exc:
                raise ImportGroundingViolationError(
                    f"IMPORT_GROUNDING_VIOLATION on version '{vdir.name}': {exc}"
                ) from exc

        verified_ont_by_sha[actual_sha] = replayed_ont


def _verify_imported_audit_chain_integrity(
    staging_dir: Path,
    session_meta: SessionMetadata,
    *,
    bundle_meta: dict[str, Any] | None = None,
) -> None:
    """Verify JCS hash-chain and tail digest of imported `audit.jsonl`, raising on tampering."""
    audit_file = staging_dir / "audit.jsonl"
    if not audit_file.is_file():
        raise BundleAuditChainCorruptError("Bundle archive is missing authoritative 'audit.jsonl'.")
    raw_lines = audit_file.read_text(encoding="utf-8").splitlines()
    valid_lines = [line.strip() for line in raw_lines if line.strip()]
    if not valid_lines:
        raise BundleAuditChainCorruptError("Bundle archive 'audit.jsonl' is empty.")

    expected_prev: str | None = None
    for idx, line in enumerate(valid_lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BundleAuditChainCorruptError(
                f"Corrupt JSON on line {idx + 1} of imported audit.jsonl: {exc}"
            ) from exc
        if not isinstance(rec, dict):
            raise BundleAuditChainCorruptError(
                f"Non-object JSON on line {idx + 1} of imported audit.jsonl."
            )
        actual_sha = rec.get("audit_sha256")
        prev_sha = rec.get("prev_audit_sha256")
        if not isinstance(actual_sha, str) or len(actual_sha) != 64:
            raise BundleAuditChainCorruptError(
                f"Missing or invalid audit_sha256 on line {idx + 1} of imported audit.jsonl."
            )
        if prev_sha != expected_prev:
            raise BundleAuditChainCorruptError(
                f"Audit hash-chain break on line {idx + 1}: expected prev '{expected_prev}', "
                f"got '{prev_sha}'."
            )
        recon = {k: v for k, v in rec.items() if k not in ("audit_sha256", "hmac_signature")}
        computed_sha = hashlib.sha256(dumps_jcs_bytes(recon)).hexdigest().lower()
        if computed_sha != actual_sha:
            raise BundleAuditChainCorruptError(
                f"Audit entry JCS SHA-256 mismatch on line {idx + 1} of imported audit.jsonl."
            )
        expected_prev = actual_sha

    assert expected_prev is not None
    expected_declared_tail = session_meta.audit_chain_head_sha256
    if isinstance(bundle_meta, dict) and bundle_meta.get("audit_chain_head_sha256"):
        expected_declared_tail = str(bundle_meta["audit_chain_head_sha256"])
    if (
        expected_declared_tail
        and expected_declared_tail != "0" * 64
        and expected_prev != expected_declared_tail
    ):
        raise BundleAuditChainCorruptError(
            f"Imported audit.jsonl tail '{expected_prev}' does not match declared "
            f"audit_chain_head_sha256 '{expected_declared_tail}' (tail truncation detected)."
        )


def _verify_and_reanchor_audit_chain(
    staging_dir: Path,
    session_meta: SessionMetadata,
    original_session_id: str,
    target_session_id: str,
    bundle_meta: dict[str, Any] | None,
    security_manager: WorkspaceSecurityManager,
    imported_by: str,
) -> SessionMetadata:
    """Verify imported audit.jsonl hash-chain and re-anchor under local K_audit if needed."""
    audit_file = staging_dir / "audit.jsonl"
    if not audit_file.is_file():
        raise BundleAuditChainCorruptError("Bundle archive is missing authoritative 'audit.jsonl'.")

    raw_lines = audit_file.read_text(encoding="utf-8").splitlines()
    valid_lines = [line.strip() for line in raw_lines if line.strip()]
    if not valid_lines:
        raise BundleAuditChainCorruptError("Bundle archive 'audit.jsonl' is empty.")

    expected_prev: str | None = None
    all_local_hmacs_valid = True

    for idx, line in enumerate(valid_lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BundleAuditChainCorruptError(
                f"Corrupt JSON on line {idx + 1} of imported audit.jsonl: {exc}"
            ) from exc

        if not isinstance(rec, dict):
            raise BundleAuditChainCorruptError(
                f"Non-object JSON on line {idx + 1} of imported audit.jsonl."
            )

        actual_sha = rec.get("audit_sha256")
        prev_sha = rec.get("prev_audit_sha256")
        actual_sig = str(rec.get("hmac_signature") or "")

        if not isinstance(actual_sha, str) or len(actual_sha) != 64:
            raise BundleAuditChainCorruptError(
                f"Missing or invalid audit_sha256 on line {idx + 1} of imported audit.jsonl."
            )
        if prev_sha != expected_prev:
            raise BundleAuditChainCorruptError(
                f"Audit hash-chain break on line {idx + 1}: expected prev '{expected_prev}', "
                f"got '{prev_sha}'."
            )

        recon = {k: v for k, v in rec.items() if k not in ("audit_sha256", "hmac_signature")}
        canonical_bytes = dumps_jcs_bytes(recon)
        computed_sha = hashlib.sha256(canonical_bytes).hexdigest().lower()
        if computed_sha != actual_sha:
            raise BundleAuditChainCorruptError(
                f"Audit entry JCS SHA-256 mismatch on line {idx + 1} of imported audit.jsonl."
            )

        expected_local_sig = security_manager.sign_audit_entry(canonical_bytes)
        if not hmac.compare_digest(actual_sig, expected_local_sig):
            all_local_hmacs_valid = False

        expected_prev = actual_sha

    assert expected_prev is not None
    imported_tail_sha = expected_prev

    expected_declared_tail = session_meta.audit_chain_head_sha256
    if isinstance(bundle_meta, dict) and bundle_meta.get("audit_chain_head_sha256"):
        expected_declared_tail = str(bundle_meta["audit_chain_head_sha256"])
    if (
        expected_declared_tail
        and expected_declared_tail != "0" * 64
        and imported_tail_sha != expected_declared_tail
    ):
        raise BundleAuditChainCorruptError(
            f"Imported audit.jsonl tail '{imported_tail_sha}' does not match declared "
            f"audit_chain_head_sha256 '{expected_declared_tail}' (tail truncation detected)."
        )

    now_iso = datetime.now(UTC).isoformat()
    if not all_local_hmacs_valid or target_session_id != original_session_id:
        archived_audit = staging_dir / f"audit.imported.{imported_tail_sha[:8]}.jsonl"
        replace_with_retry(audit_file, archived_audit)
        set_owner_only_permissions(archived_audit)

        genesis_import_entry: dict[str, Any] = {
            "timestamp": now_iso,
            "session_id": target_session_id,
            "actor": imported_by,
            "action": "SESSION_IMPORTED",
            "target_version": session_meta.head_version,
            "head_sha256": session_meta.head_sha256,
            "details": {
                "original_session_id": original_session_id,
                "imported_audit_tail_sha256": imported_tail_sha,
                "archived_audit_file": archived_audit.name,
                "reanchored": True,
            },
            "prev_audit_sha256": None,
        }
        canon_bytes = dumps_jcs_bytes(genesis_import_entry)
        anchor_sha = hashlib.sha256(canon_bytes).hexdigest().lower()
        genesis_import_entry["audit_sha256"] = anchor_sha
        genesis_import_entry["hmac_signature"] = security_manager.sign_audit_entry(canon_bytes)

        with audit_file.open("w", encoding="utf-8") as fp:
            fp.write(json.dumps(genesis_import_entry) + "\n")
            fp.flush()
            os.fsync(fp.fileno())
        set_owner_only_permissions(audit_file)

        session_meta = session_meta.model_copy(
            update={
                "session_id": target_session_id,
                "updated_at": now_iso,
                "audit_chain_head_sha256": anchor_sha,
                "imported_audit_anchor_sha256": anchor_sha,
            }
        )
        _atomic_write_session_json(staging_dir, session_meta)
        return session_meta

    # Same workspace & same session_id: append locally signed SESSION_IMPORTED entry
    session_meta = session_meta.model_copy(
        update={
            "session_id": target_session_id,
            "audit_chain_head_sha256": imported_tail_sha,
        }
    )
    _atomic_write_session_json(staging_dir, session_meta)
    append_signed_audit_record(
        session_dir=staging_dir,
        action="SESSION_IMPORTED",
        actor=imported_by,
        details={
            "original_session_id": original_session_id,
            "imported_audit_tail_sha256": imported_tail_sha,
            "reanchored": False,
        },
        security_manager=security_manager,
    )
    return SessionMetadata.model_validate_json(
        (staging_dir / "session.json").read_text(encoding="utf-8")
    )


def _has_unverified_attribution_or_foreign_waiver(
    staging_dir: Path,
    bundle_meta: dict[str, Any] | None,
    security_manager: WorkspaceSecurityManager,
) -> bool:
    """Detect unverified attribution, self-acceptance, seeded CBCs, or foreign waivers."""
    if isinstance(bundle_meta, dict) and (
        bundle_meta.get("unverified_attribution") is True
        or bundle_meta.get("attribution_verified") is False
    ):
        return True

    proposals_dir = staging_dir / "proposals"
    if proposals_dir.is_dir():
        for pf in proposals_dir.glob("*.json"):
            try:
                pdata = json.loads(pf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return True
            if not isinstance(pdata, dict):
                continue
            proposer = pdata.get("proposer")
            accepted_by = pdata.get("accepted_by") or pdata.get("resolved_by")
            if (
                pdata.get("unverified_attribution") is True
                or pdata.get("attribution_verified") is False
                or ("proposer" in pdata and proposer in (None, "", "unknown", "agent:unknown"))
                or (
                    isinstance(proposer, str)
                    and proposer
                    and isinstance(accepted_by, str)
                    and proposer == accepted_by
                )
            ):
                return True

    versions_dir = staging_dir / "versions"
    if versions_dir.is_dir():
        sorted_vdirs: list[tuple[tuple[int, int, int], str, Path]] = []
        for vdir in versions_dir.iterdir():
            if not vdir.is_dir():
                continue
            vc = vdir.name.lstrip("v")
            with contextlib.suppress(ValueError):
                sorted_vdirs.append((parse_semver_tuple(vc), vc, vdir))
        sorted_vdirs.sort(key=lambda item: item[0])

        for v_idx, (_, vc, vdir) in enumerate(sorted_vdirs):
            v_mode = ""
            for fname in ("meta.json", "metadata.json", "evaluation.json", "waivers.json"):
                fpath = vdir / fname
                if not fpath.is_file():
                    continue
                try:
                    vdata = json.loads(fpath.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return True

                if isinstance(vdata, dict):
                    gf_uris = vdata.get("grandfathered_concept_uris")
                    if isinstance(gf_uris, list) and len(gf_uris) > 0:
                        return True
                    if fname in ("meta.json", "metadata.json") and not v_mode:
                        v_mode = str(vdata.get("mode") or "").strip().lower()

                raw_waivers: list[Any] = []
                if isinstance(vdata, list):
                    raw_waivers.extend(vdata)
                elif isinstance(vdata, dict):
                    if isinstance(vdata.get("waivers"), list):
                        raw_waivers.extend(vdata["waivers"])
                    if isinstance(vdata.get("waiver"), dict):
                        raw_waivers.append(vdata["waiver"])
                    if vdata.get("low_confidence_grounding_waiver") is True and not raw_waivers:
                        return True
                else:
                    continue

                for w_raw in raw_waivers:
                    if not isinstance(w_raw, dict):
                        return True
                    try:
                        waiver = StewardWaiverRecord.model_validate(w_raw)
                        payload_bytes = waiver.signing_payload().encode("utf-8")
                        if not security_manager.verify_waiver_signature(
                            payload_bytes, waiver.signature_or_token
                        ):
                            return True
                    except (ValidationError, ValueError, TypeError):
                        return True

            if v_idx == 0 and (
                v_mode == "seed"
                or (not (vdir / "delta.json").is_file() and vc != GENESIS_VERSION)
                or vc == GENESIS_VERSION
            ):
                ont_path = vdir / "ontology.json"
                if ont_path.is_file():
                    try:
                        root_ont = GraphAOntology.model_validate_json(
                            ont_path.read_text(encoding="utf-8")
                        )
                    except (OSError, ValidationError, ValueError):
                        return True
                    if len(root_ont.canonical_business_concepts) > 0:
                        return True
                    if v_mode == "seed" or (
                        not (vdir / "delta.json").is_file() and vc != GENESIS_VERSION
                    ):
                        s_ns = "general"
                        s_owner = "Enterprise Architecture"
                        sjson_p = staging_dir / "session.json"
                        if sjson_p.is_file():
                            with contextlib.suppress(OSError, ValidationError, ValueError):
                                smeta = SessionMetadata.model_validate_json(
                                    sjson_p.read_text(encoding="utf-8")
                                )
                                s_ns = smeta.domain_namespace
                                s_owner = smeta.enterprise_owner or "Enterprise Architecture"
                        genesis_fallback_sha = compute_ontology_sha256(
                            create_genesis_ontology(
                                domain_namespace=sanitize_domain_namespace(s_ns),
                                enterprise_owner=s_owner,
                            )
                        )
                        first_sha = compute_ontology_sha256(root_ont)
                        if first_sha != genesis_fallback_sha:
                            return True

    return False


def _validate_import_target_containment(
    target_path: Path | str,
    security_manager: WorkspaceSecurityManager,
) -> Path:
    """Validate that `target_path` is not a symlink and stays within workspace/studio roots."""
    raw_target = Path(target_path)
    if ".." in raw_target.parts:
        raise InvalidWorkspacePathError(
            f"Import target directory '{target_path}' must not contain '..' traversal segments."
        )
    if raw_target.is_symlink():
        raise InvalidWorkspacePathError(
            f"Import target directory '{raw_target.name}' must not be a symlink."
        )

    sec_root = security_manager.studio_root
    if sec_root.is_symlink():
        raise InvalidWorkspacePathError(f"Studio root '{sec_root.name}' must not be a symlink.")

    env_roots = os.environ.get("OPENDV_WORKSPACE_ROOTS", "").strip()
    if env_roots:
        allowed_roots: list[Path | str] | None = None
    else:
        ws_parent = sec_root.parent.parent if sec_root.parent.name == ".opendv" else sec_root.parent
        allowed_roots = [os.getcwd(), sec_root, ws_parent]

    resolved = validate_workspace_containment(target_path, allowed_roots=allowed_roots)
    if resolved.is_symlink():
        raise InvalidWorkspacePathError(
            f"Resolved import target '{resolved.name}' must not be a symlink."
        )
    return resolved


def _resign_acknowledged_version_waivers(
    staging_dir: Path,
    security_manager: WorkspaceSecurityManager,
) -> None:
    """Re-sign validated non-expired steward waivers in `staging_dir` under local `K_waiver`."""
    versions_dir = staging_dir / "versions"
    if not versions_dir.is_dir():
        return

    now_utc = datetime.now(UTC)
    for vdir in sorted(versions_dir.iterdir()):
        if not vdir.is_dir():
            continue

        resigned_by_key: dict[tuple[str, tuple[str, ...]], StewardWaiverRecord] = {}
        for fname in ("meta.json", "metadata.json", "evaluation.json", "waivers.json"):
            fpath = vdir / fname
            if not fpath.is_file():
                continue
            try:
                vdata = json.loads(fpath.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            changed = False
            if isinstance(vdata, list):
                new_list: list[Any] = []
                for item in vdata:
                    if isinstance(item, dict):
                        with contextlib.suppress(ValidationError, ValueError, TypeError):
                            w = StewardWaiverRecord.model_validate(item)
                            if w.expires_at is None or w.expires_at > now_utc:
                                sig = security_manager.sign_waiver(
                                    w.signing_payload().encode("utf-8")
                                )
                                w = w.model_copy(update={"signature_or_token": sig})
                                dkey = (str(w.proposal_id), tuple(sorted(w.target_concept_uris)))
                                resigned_by_key[dkey] = w
                                item = w.model_dump(mode="json")
                                changed = True
                    new_list.append(item)
                vdata = new_list
            elif isinstance(vdata, dict):
                if isinstance(vdata.get("waivers"), list):
                    new_waivers: list[Any] = []
                    for item in vdata["waivers"]:
                        if isinstance(item, dict):
                            with contextlib.suppress(ValidationError, ValueError, TypeError):
                                w = StewardWaiverRecord.model_validate(item)
                                if w.expires_at is None or w.expires_at > now_utc:
                                    sig = security_manager.sign_waiver(
                                        w.signing_payload().encode("utf-8")
                                    )
                                    w = w.model_copy(update={"signature_or_token": sig})
                                    dkey = (
                                        str(w.proposal_id),
                                        tuple(sorted(w.target_concept_uris)),
                                    )
                                    resigned_by_key[dkey] = w
                                    item = w.model_dump(mode="json")
                                    changed = True
                        new_waivers.append(item)
                    vdata["waivers"] = new_waivers
                if isinstance(vdata.get("waiver"), dict):
                    with contextlib.suppress(ValidationError, ValueError, TypeError):
                        w = StewardWaiverRecord.model_validate(vdata["waiver"])
                        if w.expires_at is None or w.expires_at > now_utc:
                            sig = security_manager.sign_waiver(w.signing_payload().encode("utf-8"))
                            w = w.model_copy(update={"signature_or_token": sig})
                            dkey = (str(w.proposal_id), tuple(sorted(w.target_concept_uris)))
                            resigned_by_key[dkey] = w
                            vdata["waiver"] = w.model_dump(mode="json")
                            changed = True

            if changed:
                fpath.write_text(json.dumps(vdata, indent=2), encoding="utf-8")
                set_owner_only_permissions(fpath)

        if resigned_by_key:
            wjson_path = vdir / "waivers.json"
            wjson_payload = [w.model_dump(mode="json") for w in resigned_by_key.values()]
            wjson_path.write_text(json.dumps(wjson_payload, indent=2), encoding="utf-8")
            set_owner_only_permissions(wjson_path)


def _resolve_studio_root_for_staging(
    base_dest: Path,
    security_manager: WorkspaceSecurityManager,
    new_session_id: str | None = None,
) -> Path:
    """Resolve the studio root so staging_dir is ALWAYS a sibling of dest_dir, never inside it."""
    sec_root = security_manager.studio_root.resolve()
    if base_dest == sec_root:
        return base_dest
    if (
        (base_dest / "session.json").exists()
        or base_dest.parent == sec_root
        or not base_dest.is_dir()
        or (new_session_id is not None and base_dest.name == new_session_id)
    ):
        return base_dest.parent
    return base_dest


def import_bundle(
    zip_path: Path | str,
    target_dir: Path | str,
    security_manager: WorkspaceSecurityManager,
    new_session_id: str | None = None,
    acknowledge_unverified_import_attribution: bool = False,
    acknowledgement_rationale: str | None = None,
    imported_by: str | None = None,
    force: bool = False,
    *,
    import_rationale: str | None = None,
    actor_role: str | None = None,
) -> SessionMetadata:
    """Import and verify a portable .odvstudio.zip archive into a target session directory."""
    if (
        actor_role is not None
        and actor_role.strip().lower() != "admin"
        and (acknowledge_unverified_import_attribution or force)
    ):
        raise ForbiddenAdminRequiredError(
            "Importing with force=True or acknowledge_unverified_import_attribution=True "
            "requires the 'admin' role."
        )

    zpath = Path(zip_path).resolve()
    if not zpath.exists() or not zpath.is_file():
        raise FileNotFoundError(f"Bundle archive '{zpath}' not found")

    raw_target = Path(target_dir)
    base_dest = _validate_import_target_containment(target_dir, security_manager)
    zpath_stat_size = zpath.stat().st_size
    studio_root = _resolve_studio_root_for_staging(
        base_dest, security_manager, new_session_id=new_session_id
    )
    if studio_root.is_symlink():
        raise InvalidWorkspacePathError(
            f"Import parent directory '{studio_root.name}' must not be a symlink."
        )
    studio_root.mkdir(parents=True, exist_ok=True)

    # Always stage in studio_root as a sibling, NEVER inside an existing session directory!
    staging_dir = studio_root / f".staging-import-{secrets.token_hex(6)}"
    staging_dir.mkdir(parents=True, exist_ok=True)
    set_owner_only_permissions(staging_dir)

    actor = imported_by or "admin"
    effective_rationale = (
        acknowledgement_rationale if acknowledgement_rationale is not None else import_rationale
    )

    try:
        with zipfile.ZipFile(zpath, mode="r") as zf:
            infolist = zf.infolist()

            if len(infolist) > MAX_BUNDLE_ENTRIES:
                raise ArchiveSecurityViolationError(
                    f"Archive exceeds entry count limit: {len(infolist)} > {MAX_BUNDLE_ENTRIES}"
                )

            seen_normalized: set[str] = set()
            total_uncompressed = 0

            # 1. Pre-validate all member names
            for info in infolist:
                validate_archive_member(info, seen_normalized)

            # 2. Streaming decompression with clamped ratio checks
            for info in infolist:
                if info.is_dir() or info.filename.endswith("/"):
                    out_entry = staging_dir / info.filename
                    out_entry.mkdir(parents=True, exist_ok=True)
                    set_owner_only_permissions(out_entry)
                    continue

                target_file = staging_dir / info.filename
                try:
                    target_file.resolve().relative_to(staging_dir.resolve())
                except ValueError:
                    raise ArchiveSecurityViolationError(
                        f"Path containment violation: '{info.filename}' escapes target root"
                    ) from None

                target_file.parent.mkdir(parents=True, exist_ok=True)

                entry_decompressed = 0
                compressed_size = max(min(info.compress_size, zpath_stat_size), 1)

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

        # 3. Validate session.json
        session_json_path = staging_dir / "session.json"
        if not session_json_path.exists():
            raise ArchiveSecurityViolationError(
                "Bundle archive is missing authoritative 'session.json'"
            )

        try:
            session_meta = SessionMetadata.model_validate_json(
                session_json_path.read_text(encoding="utf-8")
            )
        except (ValidationError, ValueError) as exc:
            raise ArchiveSecurityViolationError(
                f"Bundle archive contains invalid 'session.json': {exc}"
            ) from exc

        original_sid = session_meta.session_id
        target_sid = validate_session_id(new_session_id or original_sid)

        if raw_target.name == target_sid or base_dest.name == target_sid:
            dest_dir = base_dest
        elif base_dest == studio_root or (
            new_session_id and base_dest.parent == studio_root and base_dest.name != target_sid
        ):
            dest_dir = studio_root / target_sid
        else:
            dest_dir = base_dest / target_sid

        if dest_dir.is_symlink() or dest_dir.parent.is_symlink():
            raise InvalidWorkspacePathError(
                f"Import destination '{dest_dir.name}' must not be a symlink."
            )
        dest_dir = _validate_import_target_containment(dest_dir, security_manager)

        if dest_dir.exists() and not force:
            raise SessionAlreadyExistsError(f"Session '{target_sid}' already exists.")

        bundle_meta: dict[str, Any] | None = None
        bundle_json_path = staging_dir / "bundle.json"
        if bundle_json_path.is_file():
            try:
                loaded_b = json.loads(bundle_json_path.read_text(encoding="utf-8"))
                if isinstance(loaded_b, dict):
                    bundle_meta = loaded_b
            except (OSError, json.JSONDecodeError) as exc:
                raise ArchiveSecurityViolationError(f"Corrupt bundle.json: {exc}") from exc

        is_admin = actor_role is None or actor_role.strip().lower() == "admin"

        # 4. Full delta replay, provisional URI scan & canonical JCS SHA-256 + grounding checks
        _verify_versions_and_replay_deltas(
            staging_dir,
            session_meta,
            security_manager=security_manager,
            acknowledge_unverified_import_attribution=acknowledge_unverified_import_attribution,
            is_admin=is_admin,
            bundle_meta=bundle_meta,
        )

        # 5. Effective taint propagation (T_effective = T_manifest U T_imported)
        is_tainted = compute_effective_session_taint(
            staging_dir,
            session_data=session_meta.model_dump(mode="json"),
            bundle_meta=bundle_meta,
        )
        if is_tainted:
            session_meta = session_meta.model_copy(update={"taint_classification": "TAINTED"})
            taint_idx_path = staging_dir / "taint_index.json"
            existing_taint_idx: dict[str, Any] = {}
            if taint_idx_path.is_file():
                with contextlib.suppress(OSError, json.JSONDecodeError):
                    raw_ti = json.loads(taint_idx_path.read_text(encoding="utf-8"))
                    if isinstance(raw_ti, dict):
                        existing_taint_idx = raw_ti
            existing_taint_idx["is_tainted"] = True
            taint_idx_path.write_text(json.dumps(existing_taint_idx, indent=2), encoding="utf-8")
            set_owner_only_permissions(taint_idx_path)

        # Normalize committed_versions tags consistently
        norm_committed = [normalize_version_tag(v) for v in session_meta.committed_versions]
        session_meta = session_meta.model_copy(
            update={
                "session_id": target_sid,
                "committed_versions": norm_committed,
            }
        )

        # 6. Generate fresh local .session_secret
        session_secret_path = staging_dir / ".session_secret"
        session_secret_path.write_bytes(secrets.token_bytes(32))
        set_owner_only_permissions(session_secret_path)

        # 7. Verify imported audit.jsonl hash-chain and re-anchor under local K_audit
        session_meta = _verify_and_reanchor_audit_chain(
            staging_dir=staging_dir,
            session_meta=session_meta,
            original_session_id=original_sid,
            target_session_id=target_sid,
            bundle_meta=bundle_meta,
            security_manager=security_manager,
            imported_by=actor,
        )

        # 8. Attribution, seeded/grandfathered CBCs & foreign waiver verification
        if _has_unverified_attribution_or_foreign_waiver(
            staging_dir, bundle_meta, security_manager
        ):
            if not acknowledge_unverified_import_attribution:
                raise ImportGroundingViolationError(
                    "Bundle archive contains unverified proposer attribution, seeded/grandfathered "
                    "concepts, or foreign steward waivers. Explicit acknowledgement required: "
                    "acknowledge_unverified_import_attribution=true."
                )
            if not effective_rationale or len(effective_rationale.strip()) < 10:
                raise ImportGroundingRationaleRequiredError(
                    "Attribution acknowledgement rationale must be at least 10 characters."
                )
            _resign_acknowledged_version_waivers(staging_dir, security_manager)
            append_signed_audit_record(
                session_dir=staging_dir,
                action="IMPORT_ATTRIBUTION_ACKNOWLEDGED",
                actor=actor,
                details={"rationale": effective_rationale.strip()},
                security_manager=security_manager,
            )
            session_meta = SessionMetadata.model_validate_json(
                (staging_dir / "session.json").read_text(encoding="utf-8")
            )

        # 9. Atomic destination swap under parent-level L5 lifecycle + session locks.
        # Concurrency & Win32 rename safety invariant (P08a2::MAJ-02):
        # 1) `.lifecycle_<sid>.lock` (L5 resource_id="0_lifecycle") is held across the entire
        #    check, fence, backup swap, staging swap, and cleanup; all session accessors
        #    (`reentrant_session_lock` and `promote_to_workspace`) acquire `.lifecycle_<sid>.lock`
        #    before `.session.lock` ("1_session").
        # 2) On Windows, `.session.lock` inside `dest_dir` MUST be closed before `os.replace`
        #    can rename `dest_dir`. Right before releasing `.session.lock`, we write + fsync
        #    `.session_fenced` inside `dest_dir`, and `reentrant_session_lock` rejects any
        #    directory containing `.session_fenced` with `FileNotFoundError` (unlinked only if
        #    the swap fails and rolls `backup_dir` back to `dest_dir`).
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        parent_lock_path = dest_dir.parent / f".lifecycle_{target_sid}.lock"
        with studio_lock(
            parent_lock_path,
            level=L5_SESSION,
            session_id=target_sid,
            remove_on_release=False,
        ):
            if dest_dir.is_symlink():
                raise InvalidWorkspacePathError(
                    f"Import destination '{dest_dir.name}' must not be a symlink."
                )
            if dest_dir.exists():
                if not force:
                    raise SessionAlreadyExistsError(f"Session '{target_sid}' already exists.")
                with reentrant_session_lock(dest_dir / ".session.lock", session_id=target_sid):
                    existing_sjson = dest_dir / "session.json"
                    if not existing_sjson.is_file() or existing_sjson.is_symlink():
                        raise InvalidWorkspacePathError(
                            f"Refusing force=True overwrite of non-session directory "
                            f"'{dest_dir.name}' (missing authoritative session.json)."
                        )
                    write_session_fence_marker(dest_dir)
                backup_dir = dest_dir.parent / f".trash-import-{target_sid}-{secrets.token_hex(4)}"
                replace_with_retry(dest_dir, backup_dir)
                try:
                    replace_with_retry(staging_dir, dest_dir)
                except BaseException:
                    with contextlib.suppress(OSError):
                        replace_with_retry(backup_dir, dest_dir)
                        (dest_dir / SESSION_FENCE_FILENAME).unlink(missing_ok=True)
                    raise
                shutil.rmtree(backup_dir, ignore_errors=True)
            else:
                replace_with_retry(staging_dir, dest_dir)

            set_owner_only_permissions(dest_dir)
            fsync_directory_best_effort(dest_dir.parent)
        return session_meta
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
