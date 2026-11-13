"""Studio session manager and CAS directory layout for Open-DV Ontology Studio.

Implements Task 4D.1 / Issue #208:
- Isolates studio sessions under `<studio_root>/<session_id>/`.
- Creates normative CAS layout:
  session.json, inputs/, versions/, proposals/, audit.jsonl, jobs/, .staging/.
- Strictly validates session IDs via `^[a-z0-9][a-z0-9_-]{0,63}$`, rejecting uppercase,
  path traversal, and Windows reserved device names (CON, PRN, AUX, NUL, COM1..9, LPT1..9)
  with typed INVALID_SESSION_IDENTIFIER (HTTP 400 / exit 2).
- Uses authoritative GenesisDraft (`create_genesis_ontology`, `GENESIS_DRAFT_SHA256`) with
  zero synthetic CBCs and bit-for-bit JCS SHA-256 parity on `versions/v0.0.0/ontology.json`.
- Appends write-ahead audit ledger entries HMAC-chained under K_audit using RFC 8785 JCS
  and synchronized under L5 `.session.lock` (`StudioLock`).
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
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.genesis import (
    GENESIS_DRAFT_SHA256 as GENESIS_DRAFT_SHA256,
)
from opendv_core.genesis import (
    GENESIS_VERSION as GENESIS_VERSION,
)
from opendv_core.genesis import (
    create_genesis_ontology,
)
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import (
    canonicalize_graph_a_dict,
    compute_ontology_sha256,
    dumps_jcs_bytes,
)
from opendv_service.studio.cas_storage import ContentAddressableStorage
from opendv_service.studio.locks import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    L5_SESSION,
    reentrant_session_lock,
    studio_lock,
    write_session_fence_marker,
)
from opendv_service.studio.workspace_security import (
    DEFAULT_STUDIO_DIR,
    WorkspaceSecurityManager,
    replace_with_retry,
    set_owner_only_permissions,
)
from opendv_service.studio.workspace_security import (
    WINDOWS_RESERVED_DEVICES as _WINDOWS_RESERVED_DEVICES,
)

_SESSION_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}\Z")
_SEMVER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\Av?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z"
)
_PROVISIONAL_URI_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?i)urn:opendv:provisional:")

_RESERVED_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"bundle", "init", "sessions", "health", "auth", "admin", "prune", "import", "gc"}
)

_IMPORT_ANCHOR_ACTIONS: Final[frozenset[str]] = frozenset({"SESSION_IMPORTED", "IMPORT_ANCHOR"})


def sanitize_domain_namespace(domain_namespace: str) -> str:
    """Normalize a domain namespace string to match `^[a-z][a-z0-9_]*$`."""
    clean_ns = re.sub(r"[^a-z0-9_]+", "_", (domain_namespace or "").strip().lower()).strip("_")
    if not clean_ns:
        return "general"
    if not clean_ns[0].isalpha():
        return f"d_{clean_ns}"
    return clean_ns


def normalize_version_tag(version_str: str) -> str:
    """Normalize a version tag to canonical `vX.Y.Z` form."""
    clean = version_str.strip().lstrip("v")
    return f"v{clean}"


class InvalidSessionIdentifierError(OpenDVError, ValueError):
    """Raised when session identifier fails validation (HTTP 400 / exit 2)."""

    error_code = ErrorCode.INVALID_SESSION_IDENTIFIER

    def __init__(
        self,
        message: str,
        error_code: str | ErrorCode = ErrorCode.INVALID_SESSION_IDENTIFIER,
        status_code: int = 400,
        exit_code: int = 2,
    ) -> None:
        code_enum = (
            error_code
            if isinstance(error_code, ErrorCode)
            else ErrorCode.INVALID_SESSION_IDENTIFIER
        )
        OpenDVError.__init__(self, message, error_code=code_enum)
        self.status_code = status_code
        self.http_status = status_code
        self.exit_code = exit_code


class InvalidOntologyPayloadError(OpenDVError, ValueError):
    """Raised when initial ontology payload or version fails validation (HTTP 422 / exit 2)."""

    error_code = ErrorCode.INVALID_WORKSPACE_ONTOLOGY

    def __init__(
        self,
        message: str,
        error_code: str | ErrorCode = ErrorCode.INVALID_WORKSPACE_ONTOLOGY,
        status_code: int = 422,
        exit_code: int = 2,
    ) -> None:
        ValueError.__init__(self, message)
        code_enum = (
            error_code
            if isinstance(error_code, ErrorCode)
            else ErrorCode(error_code)
            if error_code in ErrorCode._value2member_map_
            else ErrorCode.INVALID_WORKSPACE_ONTOLOGY
        )
        OpenDVError.__init__(self, message, error_code=code_enum)
        self.error_code = code_enum
        self.status_code = status_code
        self.http_status = status_code
        self.exit_code = exit_code


class SessionAlreadyExistsError(OpenDVError, FileExistsError):
    """Raised when attempting to create a session that already exists (HTTP 409 / exit 6)."""

    error_code = ErrorCode.SESSION_ALREADY_EXISTS

    def __init__(
        self, message: str, error_code: str | ErrorCode = ErrorCode.SESSION_ALREADY_EXISTS
    ) -> None:
        FileExistsError.__init__(self, message)
        code_enum = (
            error_code if isinstance(error_code, ErrorCode) else ErrorCode.SESSION_ALREADY_EXISTS
        )
        self.message = message
        self.error_code = code_enum
        self.status_code = 409
        self.http_status = 409
        self.exit_code = 6
        self.details = {}


class SessionNotFoundError(OpenDVError, FileNotFoundError):
    """Raised when a requested session does not exist (HTTP 404 / exit 3)."""

    error_code = ErrorCode.SESSION_NOT_FOUND

    def __init__(
        self, message: str, error_code: str | ErrorCode = ErrorCode.SESSION_NOT_FOUND
    ) -> None:
        FileNotFoundError.__init__(self, message)
        code_enum = error_code if isinstance(error_code, ErrorCode) else ErrorCode.SESSION_NOT_FOUND
        self.message = message
        self.error_code = code_enum
        self.status_code = 404
        self.http_status = 404
        self.exit_code = 3
        self.details = {}


class CannotDeletePromotedSessionError(OpenDVError, RuntimeError):
    """Raised when deleting a promoted session without force=True (409 / exit 6)."""

    error_code = ErrorCode.CANNOT_DELETE_PROMOTED_SESSION

    def __init__(
        self,
        message: str,
        error_code: str | ErrorCode = ErrorCode.CANNOT_DELETE_PROMOTED_SESSION,
        status_code: int = 409,
        exit_code: int = 6,
    ) -> None:
        code_enum = (
            error_code
            if isinstance(error_code, ErrorCode)
            else ErrorCode.CANNOT_DELETE_PROMOTED_SESSION
        )
        OpenDVError.__init__(self, message, error_code=code_enum)
        self.status_code = status_code
        self.http_status = status_code
        self.exit_code = exit_code


class AuditChainConflictError(OpenDVError, RuntimeError):
    """Raised when an OCC conflict occurs on audit chain head (HTTP 409 / exit 6)."""

    error_code = ErrorCode.CONCURRENCY_CONFLICT

    def __init__(
        self,
        message: str,
        error_code: str | ErrorCode = ErrorCode.CONCURRENCY_CONFLICT,
    ) -> None:
        RuntimeError.__init__(self, message)
        code_enum = (
            error_code
            if isinstance(error_code, ErrorCode)
            else ErrorCode(error_code)
            if error_code in ErrorCode._value2member_map_
            else ErrorCode.CONCURRENCY_CONFLICT
        )
        OpenDVError.__init__(self, message, error_code=code_enum)
        self.error_code = code_enum
        self.status_code = 409
        self.http_status = 409
        self.exit_code = 6


class TamperEvidentChainCorruptError(OpenDVError, RuntimeError):
    """Raised when audit.jsonl HMAC or forward SHA-256 chain fails verification."""

    error_code = ErrorCode.TAMPER_EVIDENT_CHAIN_CORRUPT

    def __init__(
        self,
        message: str = "TAMPER_EVIDENT_CHAIN_CORRUPT",
        error_code: str | ErrorCode = ErrorCode.TAMPER_EVIDENT_CHAIN_CORRUPT,
        status_code: int = 422,
        exit_code: int = 4,
    ) -> None:
        RuntimeError.__init__(self, message)
        code_enum = (
            error_code
            if isinstance(error_code, ErrorCode)
            else ErrorCode(error_code)
            if error_code in ErrorCode._value2member_map_
            else ErrorCode.TAMPER_EVIDENT_CHAIN_CORRUPT
        )
        OpenDVError.__init__(self, message, error_code=code_enum)
        self.error_code = code_enum
        self.status_code = status_code
        self.http_status = status_code
        self.exit_code = exit_code


def validate_session_id(session_id: str) -> str:
    """Validate a session identifier against strict normative constraints."""
    if not isinstance(session_id, str):
        raise InvalidSessionIdentifierError(
            f"Session identifier must be a string, got {type(session_id).__name__}"
        )

    if not session_id:
        raise InvalidSessionIdentifierError("Session identifier cannot be empty")

    if len(session_id) > 64:
        raise InvalidSessionIdentifierError(
            f"Session identifier '{session_id}' exceeds maximum length of 64 characters "
            f"(len={len(session_id)})"
        )

    if "/" in session_id or "\\" in session_id or ".." in session_id:
        raise InvalidSessionIdentifierError(
            f"Session identifier '{session_id}' contains forbidden path traversal "
            "or separator characters"
        )

    if any(ord(c) < 32 or ord(c) == 127 or c in "\r\n\t\x00" for c in session_id):
        raise InvalidSessionIdentifierError(
            "Session identifier contains forbidden control or whitespace characters"
        )

    if any(c.isupper() for c in session_id):
        raise InvalidSessionIdentifierError(
            f"Session identifier '{session_id}' contains uppercase characters; "
            "strictly lowercase alphanumeric start required without silent normalization"
        )

    if session_id.startswith(("-", "_")):
        raise InvalidSessionIdentifierError(
            f"Session identifier '{session_id}' cannot start with a hyphen or underscore"
        )

    if not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise InvalidSessionIdentifierError(
            f"Session identifier '{session_id}' fails regex pattern requirement: "
            "must match '^[a-z0-9][a-z0-9_-]{{0,63}}$'"
        )

    base_name = session_id.split(".")[0].lower()
    if session_id.lower() in _WINDOWS_RESERVED_DEVICES or base_name in _WINDOWS_RESERVED_DEVICES:
        raise InvalidSessionIdentifierError(
            f"Session identifier '{session_id}' is a Windows reserved device name"
        )

    if session_id.lower() in _RESERVED_KEYWORDS:
        raise InvalidSessionIdentifierError(
            f"Session identifier '{session_id}' is a reserved route keyword"
        )

    return session_id


class SessionMetadata(BaseModel):
    """Authoritative session state persisted in session.json."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    session_id: str
    created_at: str
    updated_at: str
    head_version: str = "0.0.0"
    head_sha256: str
    audit_chain_head_sha256: str
    status: Literal["ACTIVE", "ARCHIVED", "CLOSED"] = "ACTIVE"
    taint_classification: Literal["UNTAINTED", "TAINTED"] = "UNTAINTED"
    domain_namespace: str = "general"
    enterprise_owner: str = "Enterprise Architecture"
    committed_versions: list[str] = Field(default_factory=list)
    promoted_versions: list[str] = Field(default_factory=list)
    last_promoted_sha256: str | None = None
    imported_audit_anchor_sha256: str | None = None

    @property
    def active_version(self) -> str:
        """Alias returning the active session head SemVer string."""
        return self.head_version


def _atomic_write_session_json(session_dir: Path, metadata: SessionMetadata) -> None:
    """Atomically write and fsync `session.json` via temporary file and `replace_with_retry`."""
    session_json_path = session_dir / "session.json"
    temp_json_path = session_dir / f".session.json.{secrets.token_hex(4)}.tmp"
    payload = metadata.model_dump_json(indent=2).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(str(temp_json_path), flags, 0o600)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        set_owner_only_permissions(temp_json_path)
        replace_with_retry(temp_json_path, session_json_path)
        set_owner_only_permissions(session_json_path)
    finally:
        if temp_json_path.exists():
            with contextlib.suppress(OSError):
                temp_json_path.unlink()


def atomic_repair_audit_file(audit_file: Path, repaired_bytes: bytes) -> None:
    """Atomically write repaired `audit.jsonl` content via sibling temp file + fsync + swap."""
    tmp_path = audit_file.parent / f".audit.jsonl.repair.{os.getpid()}.tmp"
    if tmp_path.is_symlink():
        raise TamperEvidentChainCorruptError(
            f"TAMPER_EVIDENT_CHAIN_CORRUPT: Refusing symlinked repair temp '{tmp_path.name}'"
        )
    if tmp_path.exists():
        with contextlib.suppress(OSError):
            tmp_path.unlink()
    flags = (
        os.O_CREAT
        | os.O_EXCL
        | os.O_WRONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        fd = os.open(str(tmp_path), flags, 0o600)
        try:
            os.write(fd, repaired_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        set_owner_only_permissions(tmp_path)
        replace_with_retry(tmp_path, audit_file)
        set_owner_only_permissions(audit_file)
    finally:
        if tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()


def _is_valid_audit_record_bytes(
    line_bytes: bytes,
    *,
    expected_prev_sha: str | None,
    sec_mgr: WorkspaceSecurityManager,
    require_hmac: bool = True,
) -> tuple[bool, str | None]:
    """Validate a single JSONL audit record line without trailing newline."""
    try:
        rec = json.loads(line_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return (False, None)
    if not isinstance(rec, dict):
        return (False, None)
    actual_sha = rec.get("audit_sha256")
    actual_sig = rec.get("hmac_signature")
    prev_sha = rec.get("prev_audit_sha256")
    if not isinstance(actual_sha, str) or len(actual_sha) != 64:
        return (False, None)
    if prev_sha != expected_prev_sha:
        return (False, None)
    recon = {k: v for k, v in rec.items() if k not in ("audit_sha256", "hmac_signature")}
    canonical_bytes = dumps_jcs_bytes(recon)
    computed_sha = hashlib.sha256(canonical_bytes).hexdigest().lower()
    if computed_sha != actual_sha:
        return (False, None)
    if require_hmac:
        if not isinstance(actual_sig, str):
            return (False, None)
        expected_sig = sec_mgr.sign_audit_entry(canonical_bytes)
        if not hmac.compare_digest(actual_sig, expected_sig):
            return (False, None)
    return (True, actual_sha)


def _fsync_dir_best_effort(dir_path: Path) -> None:
    """Best-effort directory fsync on POSIX platforms."""
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        fd = os.open(str(dir_path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _validate_single_audit_record_obj(
    rec: Any,
    *,
    sid: str,
    expected_prev: str | None,
    anchor_sha: str | None,
    seen_anchor: bool,
    sec_mgr: WorkspaceSecurityManager,
) -> tuple[str, bool, bool]:
    """Validate hash-chain link, RFC 8785 JCS SHA-256, and K_audit HMAC for one audit record."""
    if not isinstance(rec, dict):
        raise TamperEvidentChainCorruptError(
            f"TAMPER_EVIDENT_CHAIN_CORRUPT: Non-object audit record in '{sid}'."
        )
    actual_sha = rec.get("audit_sha256")
    actual_sig = rec.get("hmac_signature")
    prev_sha = rec.get("prev_audit_sha256")

    if not isinstance(actual_sha, str) or len(actual_sha) != 64 or prev_sha != expected_prev:
        raise TamperEvidentChainCorruptError(
            f"Audit chain hash break in '{sid}': expected prev '{expected_prev}', got '{prev_sha}'"
        )

    recon = {k: v for k, v in rec.items() if k not in ("audit_sha256", "hmac_signature")}
    canonical_bytes = dumps_jcs_bytes(recon)
    computed_sha = hashlib.sha256(canonical_bytes).hexdigest().lower()
    if computed_sha != actual_sha:
        raise TamperEvidentChainCorruptError(f"Audit entry SHA-256 digest mismatch in '{sid}'")

    if not seen_anchor and actual_sha == anchor_sha:
        seen_anchor = True

    expected_sig = sec_mgr.sign_audit_entry(canonical_bytes)
    hmac_ok = isinstance(actual_sig, str) and hmac.compare_digest(actual_sig, expected_sig)
    if seen_anchor and not hmac_ok:
        raise TamperEvidentChainCorruptError(
            f"Audit entry HMAC signature verification failed in '{sid}'"
        )

    return (actual_sha, seen_anchor, hmac_ok)


def reconcile_or_verify_audit_ledger(
    session_dir: Path,
    metadata: SessionMetadata,
    sec_mgr: WorkspaceSecurityManager,
    *,
    mutate: bool,
) -> str | None:
    """Validate `audit.jsonl` against `metadata.audit_chain_head_sha256` and reconcile WAL state.

    Supports:
    - Torn incomplete non-newline-terminated trailing fragment after record `k` (where record `k`
      matches `declared_head`): verifies `0..k`, and truncates back to end of record `k` when
      `mutate=True` under L5 lock. (If the non-newline-terminated line IS record `k` itself matching
      `declared_head`, repairs the missing trailing `\\n` when `mutate=True`.)
    - Single complete newline-terminated write-ahead record `k+1` after `declared_head`: when
      `record[k+1]` chains from `declared_head`, matches canonical JCS SHA-256, and verifies under
      local `K_audit`, treats `k+1` as committed WAL state (advancing `session.json` under L5 lock
      when `mutate=True`, and returning `record[k+1]["audit_sha256"]` in read-only mode).
    - Rejects tail truncation, unsigned/forged write-ahead records, mismatched `prev_audit_sha256`,
      and multi-record (>= 2) uncommitted divergence with `TamperEvidentChainCorruptError`.
    """
    sid = session_dir.name
    declared_head = (metadata.audit_chain_head_sha256 or "").strip()
    anchor_sha = metadata.imported_audit_anchor_sha256
    audit_file = session_dir / "audit.jsonl"

    if not audit_file.is_file():
        if declared_head:
            raise TamperEvidentChainCorruptError(
                f"TAMPER_EVIDENT_CHAIN_CORRUPT: Audit ledger missing for session '{sid}' "
                f"while session.json declares head '{declared_head}'."
            )
        return None

    raw_bytes = audit_file.read_bytes()
    if not raw_bytes:
        if declared_head:
            raise TamperEvidentChainCorruptError(
                f"TAMPER_EVIDENT_CHAIN_CORRUPT: Audit ledger is empty for session '{sid}' "
                f"while session.json declares head '{declared_head}'."
            )
        return None

    has_unterminated_tail = not raw_bytes.endswith(b"\n")
    if has_unterminated_tail:
        last_nl = raw_bytes.rfind(b"\n")
        prefix_bytes = raw_bytes[: last_nl + 1] if last_nl >= 0 else b""
        tail_fragment = raw_bytes[last_nl + 1 :]
    else:
        last_nl = len(raw_bytes) - 1
        prefix_bytes = raw_bytes
        tail_fragment = b""

    try:
        prefix_text = prefix_bytes.decode("utf-8")
    except UnicodeDecodeError as err:
        raise TamperEvidentChainCorruptError(
            f"TAMPER_EVIDENT_CHAIN_CORRUPT: Invalid UTF-8 in audit ledger for '{sid}'."
        ) from err

    prefix_lines = [ln.strip() for ln in prefix_text.split("\n") if ln.strip()]
    seen_anchor = anchor_sha is None
    expected_prev: str | None = None
    chain_hashes: list[str] = []
    records: list[dict[str, Any]] = []
    hmac_flags: list[bool] = []

    for line in prefix_lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TamperEvidentChainCorruptError(
                f"Corrupt JSON in audit ledger for session '{sid}': {exc}"
            ) from exc
        actual_sha, seen_anchor, hmac_ok = _validate_single_audit_record_obj(
            rec,
            sid=sid,
            expected_prev=expected_prev,
            anchor_sha=anchor_sha,
            seen_anchor=seen_anchor,
            sec_mgr=sec_mgr,
        )
        chain_hashes.append(actual_sha)
        records.append(rec)
        hmac_flags.append(hmac_ok)
        expected_prev = actual_sha

    if has_unterminated_tail:
        # Check if the unterminated line is itself the committed head record k missing only '\n'
        committed_unterminated_ok = False
        try:
            tail_rec = json.loads(tail_fragment.decode("utf-8"))
            t_sha, t_seen_anchor, t_hmac_ok = _validate_single_audit_record_obj(
                tail_rec,
                sid=sid,
                expected_prev=expected_prev,
                anchor_sha=anchor_sha,
                seen_anchor=seen_anchor,
                sec_mgr=sec_mgr,
            )
            if (not declared_head and not chain_hashes and t_hmac_ok) or (
                declared_head and t_sha == declared_head
            ):
                committed_unterminated_ok = True
                chain_hashes.append(t_sha)
                records.append(tail_rec)
                hmac_flags.append(t_hmac_ok)
                seen_anchor = t_seen_anchor
                expected_prev = t_sha
                if mutate:
                    atomic_repair_audit_file(audit_file, prefix_bytes + tail_fragment + b"\n")
        except (UnicodeDecodeError, json.JSONDecodeError, TamperEvidentChainCorruptError):
            committed_unterminated_ok = False

        if not committed_unterminated_ok:
            if not chain_hashes or last_nl < 0:
                raise TamperEvidentChainCorruptError(
                    f"Audit ledger for session '{sid}' contains unparseable torn bytes"
                )
            if not seen_anchor:
                raise TamperEvidentChainCorruptError(
                    f"Imported audit anchor '{anchor_sha}' missing from audit chain in '{sid}'"
                )
            if chain_hashes[-1] != declared_head:
                raise TamperEvidentChainCorruptError(
                    f"TAMPER_EVIDENT_CHAIN_CORRUPT: Audit ledger prefix tail '{chain_hashes[-1]}' "
                    f"before torn fragment disagrees with session anchor '{declared_head}' "
                    f"missing from audit chain in session '{sid}'."
                )
            if mutate:
                atomic_repair_audit_file(audit_file, prefix_bytes)

    if not chain_hashes:
        if declared_head:
            raise TamperEvidentChainCorruptError(
                f"TAMPER_EVIDENT_CHAIN_CORRUPT: Audit ledger contains no valid lines for "
                f"session '{sid}' while session.json declares head '{declared_head}'."
            )
        return None

    if not seen_anchor:
        raise TamperEvidentChainCorruptError(
            f"Imported audit anchor '{anchor_sha}' missing from audit chain in '{sid}'"
        )

    if not declared_head:
        if len(chain_hashes) == 1 and hmac_flags[0]:
            if mutate:
                metadata.audit_chain_head_sha256 = chain_hashes[0]
                _atomic_write_session_json(session_dir, metadata)
            return chain_hashes[0]
        raise TamperEvidentChainCorruptError(
            f"TAMPER_EVIDENT_CHAIN_CORRUPT: Session '{sid}' has empty audit_chain_head_sha256 "
            f"with {len(chain_hashes)} ledger entries."
        )

    if chain_hashes[-1] == declared_head:
        return declared_head

    # Single complete newline-terminated K_audit-signed write-ahead record k+1 after declared_head
    if (
        len(chain_hashes) >= 2
        and chain_hashes[-2] == declared_head
        and records[-1].get("prev_audit_sha256") == declared_head
        and hmac_flags[-1]
    ):
        wal_head = chain_hashes[-1]
        if mutate:
            metadata.audit_chain_head_sha256 = wal_head
            _atomic_write_session_json(session_dir, metadata)
        return wal_head

    if declared_head not in chain_hashes:
        raise TamperEvidentChainCorruptError(
            f"Session anchor '{declared_head}' missing from audit chain "
            f"in session '{sid}' (tail is '{chain_hashes[-1]}')"
        )

    raise TamperEvidentChainCorruptError(
        f"TAMPER_EVIDENT_CHAIN_CORRUPT: Uncommitted multi-record audit divergence after "
        f"'{declared_head}' in session '{sid}' (tail is '{chain_hashes[-1]}')."
    )


def append_signed_audit_record(
    session_dir: Path | str,
    action: str,
    actor: str = "system",
    details: dict[str, Any] | None = None,
    *,
    security_manager: WorkspaceSecurityManager | None = None,
    target_version: str | None = None,
    head_sha256: str | None = None,
    expected_audit_head: str | None = None,
    timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Unified helper appending an RFC 8785 JCS + K_audit HMAC-signed record to `audit.jsonl`.

    Synchronizes under L5 `.session.lock` (reentrant when already inside an active L5 critical
    section for the same session), repairs torn trailing bytes or rolls forward a single
    uncommitted signed WAL entry atomically, verifies full chain integrity against
    `session.json.audit_chain_head_sha256`, fsyncs `audit.jsonl`, and atomically advances
    `session.json.audit_chain_head_sha256`.
    """
    sdir = Path(session_dir).resolve()
    session_json_path = sdir / "session.json"
    sid = sdir.name

    if not sdir.exists() or not session_json_path.exists():
        raise SessionNotFoundError(f"Session '{sid}' not found")

    sec_mgr = security_manager or WorkspaceSecurityManager(studio_root=sdir.parent)
    lock_file = sdir / ".session.lock"

    with reentrant_session_lock(lock_file, session_id=sid, timeout=timeout_sec):
        if not session_json_path.exists():
            raise SessionNotFoundError(f"Session '{sid}' not found")

        metadata = SessionMetadata.model_validate_json(
            session_json_path.read_text(encoding="utf-8")
        )
        prev_audit_sha = reconcile_or_verify_audit_ledger(
            sdir,
            metadata,
            sec_mgr,
            mutate=True,
        )
        effective_head = (metadata.audit_chain_head_sha256 or "").strip()

        if expected_audit_head is not None and effective_head != expected_audit_head:
            raise AuditChainConflictError(
                f"Audit chain head conflict for session '{sid}': "
                f"expected '{expected_audit_head}', found '{effective_head}'"
            )

        audit_file = sdir / "audit.jsonl"

        now_iso = datetime.now(UTC).isoformat()
        entry: dict[str, Any] = {
            "timestamp": now_iso,
            "session_id": metadata.session_id,
            "actor": actor,
            "action": action,
            "target_version": target_version
            if target_version is not None
            else metadata.head_version,
            "head_sha256": head_sha256 if head_sha256 is not None else metadata.head_sha256,
            "details": details or {},
            "prev_audit_sha256": prev_audit_sha,
        }

        canonical_entry_bytes = dumps_jcs_bytes(entry)
        entry_sha256 = hashlib.sha256(canonical_entry_bytes).hexdigest().lower()
        hmac_sig = sec_mgr.sign_audit_entry(canonical_entry_bytes)

        entry["audit_sha256"] = entry_sha256
        entry["hmac_signature"] = hmac_sig

        with audit_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())
        set_owner_only_permissions(audit_file)

        metadata.audit_chain_head_sha256 = entry_sha256
        metadata.updated_at = now_iso
        if action in _IMPORT_ANCHOR_ACTIONS and metadata.imported_audit_anchor_sha256 is None:
            metadata.imported_audit_anchor_sha256 = entry_sha256
        _atomic_write_session_json(sdir, metadata)

        return entry


class StudioSessionManager:
    """Manages studio CAS session directory layout and session lifecycle."""

    def __init__(
        self,
        studio_root: Path | str | None = None,
        security_manager: WorkspaceSecurityManager | None = None,
    ) -> None:
        if studio_root is None:
            configured = os.environ.get("OPENDV_STUDIO_ROOT", DEFAULT_STUDIO_DIR)
            self.studio_root = Path(configured).resolve()
        else:
            self.studio_root = Path(studio_root).resolve()

        self.studio_root.mkdir(parents=True, exist_ok=True)
        set_owner_only_permissions(self.studio_root)

        self.security_manager = security_manager or WorkspaceSecurityManager(
            studio_root=self.studio_root
        )
        self.keys = self.security_manager.get_keys()

    def get_session_dir(self, session_id: str) -> Path:
        """Resolve and validate the absolute directory for a session."""
        valid_id = validate_session_id(session_id)
        return self.studio_root / valid_id

    def create_session(
        self,
        session_id: str | None = None,
        initial_ontology: GraphAOntology | dict[str, Any] | None = None,
        domain_namespace: str = "general",
        enterprise_owner: str = "Enterprise Architecture",
    ) -> SessionMetadata:
        """Create a new CAS studio session with full directory layout."""
        sid = f"s-{secrets.token_hex(8)}" if session_id is None else validate_session_id(session_id)
        session_dir = self.studio_root / sid

        try:
            session_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            raise SessionAlreadyExistsError(f"Session '{sid}' already exists") from None

        try:
            return self._initialize_session_contents(
                sid=sid,
                session_dir=session_dir,
                initial_ontology=initial_ontology,
                domain_namespace=domain_namespace,
                enterprise_owner=enterprise_owner,
            )
        except Exception:
            shutil.rmtree(session_dir, ignore_errors=True)
            raise

    def _initialize_session_contents(
        self,
        sid: str,
        session_dir: Path,
        initial_ontology: GraphAOntology | dict[str, Any] | None,
        domain_namespace: str,
        enterprise_owner: str,
    ) -> SessionMetadata:
        """Internal helper to populate session directory hierarchy and metadata."""
        set_owner_only_permissions(session_dir)

        inputs_dir = session_dir / "inputs"
        cas_dir = inputs_dir / "cas"
        evidence_dir = session_dir / "evidence"
        clarifications_dir = session_dir / "clarifications"
        versions_dir = session_dir / "versions"
        proposals_dir = session_dir / "proposals"
        jobs_dir = session_dir / "jobs"
        staging_dir = session_dir / ".staging"

        for directory in [
            inputs_dir,
            cas_dir,
            evidence_dir,
            clarifications_dir,
            versions_dir,
            proposals_dir,
            jobs_dir,
            staging_dir,
        ]:
            directory.mkdir(parents=True, exist_ok=True)
            set_owner_only_permissions(directory)

        committed_versions: list[str] = []
        now_iso = datetime.now(UTC).isoformat()

        if initial_ontology is not None:
            try:
                if isinstance(initial_ontology, GraphAOntology):
                    raw_dict = initial_ontology.model_dump(mode="json", exclude_none=True)
                elif isinstance(initial_ontology, dict):
                    raw_dict = initial_ontology
                else:
                    tname = type(initial_ontology).__name__
                    raise InvalidOntologyPayloadError(
                        f"initial_ontology must be GraphAOntology or dict, got {tname}"
                    )

                serialized_payload = json.dumps(raw_dict)
                if (
                    _PROVISIONAL_URI_PATTERN.search(serialized_payload)
                    or ":provisional:" in serialized_payload
                ):
                    raise InvalidOntologyPayloadError(
                        "Seed ontology contains forbidden provisional URI; seed ontologies "
                        "must be fully committed and non-provisional."
                    )

                validated_ont = GraphAOntology.model_validate(raw_dict)
            except ValidationError as err:
                raise InvalidOntologyPayloadError(
                    f"Invalid initial ontology payload: {err}"
                ) from err

            if domain_namespace == "general" and validated_ont.domain_namespace:
                domain_namespace = validated_ont.domain_namespace
            if enterprise_owner == "Enterprise Architecture" and validated_ont.enterprise_owner:
                enterprise_owner = validated_ont.enterprise_owner

            clean_ns = sanitize_domain_namespace(domain_namespace)
            raw_version = str(validated_ont.ontology_version).strip()
            is_valid_semver = (
                _SEMVER_PATTERN.fullmatch(raw_version)
                and "/" not in raw_version
                and "\\" not in raw_version
            )
            if not is_valid_semver:
                raise InvalidOntologyPayloadError(
                    f"Invalid ontology version '{raw_version}': must follow strict SemVer"
                )

            head_sha256 = compute_ontology_sha256(validated_ont)
            head_version = raw_version.lstrip("v")
            version_tag = normalize_version_tag(head_version)
            committed_versions.append(version_tag)

            version_dir = (versions_dir / version_tag).resolve()
            if not version_dir.is_relative_to(versions_dir.resolve()):
                raise InvalidOntologyPayloadError(
                    f"Version directory path '{head_version}' escapes versions root"
                )

            version_dir.mkdir(parents=True, exist_ok=True)
            set_owner_only_permissions(version_dir)

            canonical_dict = canonicalize_graph_a_dict(validated_ont)
            canonical_jcs = dumps_jcs_bytes(canonical_dict)
            ont_json_file = version_dir / "ontology.json"
            ont_json_file.write_bytes(canonical_jcs)
            set_owner_only_permissions(ont_json_file)

            yaml_content = yaml.safe_dump(canonical_dict, sort_keys=False)
            ont_yaml_file = version_dir / "ontology.yaml"
            ont_yaml_file.write_text(yaml_content, encoding="utf-8")
            set_owner_only_permissions(ont_yaml_file)

            grandfathered_uris = sorted(
                c.concept_uri for c in validated_ont.canonical_business_concepts
            )
            meta_record: dict[str, Any] = {
                "version": version_tag,
                "target_version": version_tag,
                "target_sha256": head_sha256,
                "parent_sha256": None,
                "commit_timestamp": now_iso,
                "created_at": now_iso,
                "author": "system:seed",
                "mode": "seed",
                "domain_namespace": clean_ns,
                "enterprise_owner": enterprise_owner,
                "grandfathered_concept_uris": grandfathered_uris,
            }
            meta_json_text = json.dumps(meta_record, indent=2)
            for fname in ("meta.json", "metadata.json"):
                mfile = version_dir / fname
                mfile.write_text(meta_json_text, encoding="utf-8")
                set_owner_only_permissions(mfile)
            effective_ns = clean_ns
        else:
            clean_ns = sanitize_domain_namespace(domain_namespace)
            head_version = GENESIS_VERSION
            version_tag = normalize_version_tag(head_version)
            version_dir = (versions_dir / version_tag).resolve()
            version_dir.mkdir(parents=True, exist_ok=True)
            set_owner_only_permissions(version_dir)

            genesis_ont = create_genesis_ontology(
                domain_namespace=clean_ns,
                enterprise_owner=enterprise_owner,
            )
            canonical_dict = canonicalize_graph_a_dict(genesis_ont)
            canonical_jcs = dumps_jcs_bytes(canonical_dict)
            head_sha256 = compute_ontology_sha256(genesis_ont)

            ont_json_file = version_dir / "ontology.json"
            ont_json_file.write_bytes(canonical_jcs)
            set_owner_only_permissions(ont_json_file)

            yaml_content = yaml.safe_dump(canonical_dict, sort_keys=False)
            ont_yaml_file = version_dir / "ontology.yaml"
            ont_yaml_file.write_text(yaml_content, encoding="utf-8")
            set_owner_only_permissions(ont_yaml_file)

            meta_record = {
                "version": version_tag,
                "target_version": version_tag,
                "target_sha256": head_sha256,
                "parent_sha256": None,
                "commit_timestamp": now_iso,
                "created_at": now_iso,
                "author": "system:genesis",
                "mode": "genesis",
                "domain_namespace": clean_ns,
                "enterprise_owner": enterprise_owner,
                "grandfathered_concept_uris": [],
            }
            meta_json_text = json.dumps(meta_record, indent=2)
            for fname in ("meta.json", "metadata.json"):
                mfile = version_dir / fname
                mfile.write_text(meta_json_text, encoding="utf-8")
                set_owner_only_permissions(mfile)
            effective_ns = clean_ns

        genesis_audit_entry: dict[str, Any] = {
            "timestamp": now_iso,
            "session_id": sid,
            "actor": "system",
            "action": "SESSION_CREATED",
            "target_version": head_version,
            "head_sha256": head_sha256,
            "details": {
                "initial_ontology": bool(initial_ontology),
                "domain_namespace": effective_ns,
                "enterprise_owner": enterprise_owner,
            },
            "prev_audit_sha256": None,
        }

        canonical_entry_bytes = dumps_jcs_bytes(genesis_audit_entry)
        entry_sha256 = hashlib.sha256(canonical_entry_bytes).hexdigest().lower()
        hmac_sig = self.security_manager.sign_audit_entry(canonical_entry_bytes)

        genesis_audit_entry["audit_sha256"] = entry_sha256
        genesis_audit_entry["hmac_signature"] = hmac_sig

        audit_file = session_dir / "audit.jsonl"
        with audit_file.open("x", encoding="utf-8") as f:
            f.write(json.dumps(genesis_audit_entry) + "\n")
            f.flush()
            os.fsync(f.fileno())
        set_owner_only_permissions(audit_file)

        metadata = SessionMetadata(
            session_id=sid,
            created_at=now_iso,
            updated_at=now_iso,
            head_version=head_version,
            head_sha256=head_sha256,
            audit_chain_head_sha256=entry_sha256,
            status="ACTIVE",
            taint_classification="UNTAINTED",
            domain_namespace=effective_ns,
            enterprise_owner=enterprise_owner,
            committed_versions=committed_versions,
        )
        _atomic_write_session_json(session_dir, metadata)
        return metadata

    def get_session(self, session_id: str) -> SessionMetadata:
        """Retrieve metadata for an existing studio session."""
        session_dir = self.get_session_dir(session_id)
        session_json_path = session_dir / "session.json"

        if not session_dir.exists() or not session_json_path.exists():
            raise SessionNotFoundError(f"Session '{session_id}' not found")

        content = session_json_path.read_text(encoding="utf-8")
        return SessionMetadata.model_validate_json(content)

    def list_sessions(self) -> list[SessionMetadata]:
        """List all studio sessions present under studio root."""
        if not self.studio_root.exists():
            return []

        sessions: list[SessionMetadata] = []
        for child in sorted(self.studio_root.iterdir()):
            if child.is_dir() and (child / "session.json").is_file():
                try:
                    meta = SessionMetadata.model_validate_json(
                        (child / "session.json").read_text(encoding="utf-8")
                    )
                    sessions.append(meta)
                except (ValidationError, ValueError, OSError, TypeError):
                    continue

        return sessions

    def delete_session(self, session_id: str, force: bool = False) -> None:
        """Delete an existing studio session directory under L5 lifecycle + session locks."""
        session_dir = self.get_session_dir(session_id)
        lifecycle_lock_path = session_dir.parent / f".lifecycle_{session_id}.lock"

        with studio_lock(
            lifecycle_lock_path,
            level=L5_SESSION,
            session_id=session_id,
            remove_on_release=False,
        ):
            if not session_dir.exists() or session_dir.is_symlink():
                raise SessionNotFoundError(f"Session '{session_id}' not found")

            lock_path = session_dir / ".session.lock"
            with reentrant_session_lock(lock_path, session_id=session_id):
                session_json = session_dir / "session.json"
                if session_json.is_file():
                    try:
                        meta = SessionMetadata.model_validate_json(
                            session_json.read_text(encoding="utf-8")
                        )
                    except (ValidationError, ValueError, OSError):
                        meta = None
                    has_promotion = (
                        meta is not None
                        and bool(meta.promoted_versions or meta.last_promoted_sha256)
                    ) or (session_dir / "promotion.json").is_file()
                    if has_promotion and not force:
                        raise CannotDeletePromotedSessionError(
                            f"Cannot delete session '{session_id}' with promoted ontology versions "
                            "without force=True."
                        )
                    with contextlib.suppress(OSError):
                        session_json.unlink()

                with contextlib.suppress(OSError):
                    write_session_fence_marker(session_dir)

            tombstone = self.studio_root / f".trash-{session_id}-{uuid.uuid4().hex[:8]}"
            try:
                replace_with_retry(session_dir, tombstone)
                shutil.rmtree(tombstone)
            except OSError:
                shutil.rmtree(session_dir)

    def clone_session(
        self,
        source_session_id: str,
        new_session_id: str | None = None,
        *,
        actor: str = "system",
        actor_role: str,
    ) -> SessionMetadata:
        """Clone an existing verified local Studio session into a new isolated session."""
        norm_role = (actor_role or "").strip().lower()
        if norm_role == "automated" or norm_role.startswith("agent:"):
            err = OpenDVError(
                "Automated agents are not permitted to clone Studio sessions.",
                error_code=ErrorCode.AGENT_OPERATION_FORBIDDEN,
            )
            err.status_code = err.http_status
            raise err
        if norm_role not in ("editor", "admin"):
            err = OpenDVError(
                f"Role '{actor_role}' is not permitted to clone Studio sessions "
                "(requires 'editor' or 'admin').",
                error_code=ErrorCode.FORBIDDEN_ADMIN_REQUIRED,
            )
            err.status_code = err.http_status
            raise err
        is_admin = norm_role == "admin"

        validate_session_id(source_session_id)
        if new_session_id is not None:
            new_sid = validate_session_id(new_session_id)
        else:
            src_part = source_session_id[:51].rstrip("-_")
            new_sid = validate_session_id(f"clone-{src_part}-{secrets.token_hex(3)}")

        sdir = self.get_session_dir(source_session_id)
        new_dir = self.get_session_dir(new_sid)

        if not sdir.exists() or not (sdir / "session.json").is_file():
            raise SessionNotFoundError(f"Session '{source_session_id}' not found")
        if new_dir.exists():
            raise SessionAlreadyExistsError(f"Session '{new_sid}' already exists")

        from opendv_service.studio.bundle import compute_effective_session_taint

        scratch_dir = self.studio_root / ".scratch"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        set_owner_only_permissions(scratch_dir)
        staging_dir = scratch_dir / f".staging_clone_{new_sid}_{secrets.token_hex(6)}"

        try:
            try:
                with reentrant_session_lock(sdir / ".session.lock", session_id=source_session_id):
                    if not (sdir / "session.json").is_file():
                        raise SessionNotFoundError(f"Session '{source_session_id}' not found")

                    if not self.verify_audit_chain(source_session_id):
                        raise TamperEvidentChainCorruptError(
                            f"TAMPER_EVIDENT_CHAIN_CORRUPT: Source session '{source_session_id}' "
                            "failed cryptographic audit chain verification."
                        )

                    is_tainted = compute_effective_session_taint(sdir)
                    if is_tainted and not is_admin:
                        err = OpenDVError(
                            "Cloning a session containing confidential/tainted sources "
                            "requires the 'admin' role.",
                            error_code=ErrorCode.FORBIDDEN_ADMIN_REQUIRED,
                        )
                        err.status_code = err.http_status
                        raise err

                    has_unverified_import_ack = False
                    src_audit_file = sdir / "audit.jsonl"
                    if src_audit_file.is_file():
                        for raw_line in src_audit_file.read_text(encoding="utf-8").splitlines():
                            clean_line = raw_line.strip()
                            if not clean_line:
                                continue
                            with contextlib.suppress(json.JSONDecodeError):
                                rec_obj = json.loads(clean_line)
                                if isinstance(rec_obj, dict):
                                    act = str(rec_obj.get("action") or "")
                                    det = rec_obj.get("details")
                                    if act == "IMPORT_ATTRIBUTION_ACKNOWLEDGED" or (
                                        act in ("BUNDLE_IMPORTED", "SESSION_CLONED")
                                        and isinstance(det, dict)
                                        and bool(det.get("unverified_attribution_acknowledged"))
                                    ):
                                        has_unverified_import_ack = True
                                        break

                    if has_unverified_import_ack and not is_admin:
                        err = OpenDVError(
                            "Cloning a session imported under an unverified "
                            "attribution waiver requires the 'admin' role.",
                            error_code=ErrorCode.FORBIDDEN_ADMIN_REQUIRED,
                        )
                        err.status_code = err.http_status
                        raise err

                    source_meta = SessionMetadata.model_validate_json(
                        (sdir / "session.json").read_text(encoding="utf-8")
                    )

                    shutil.copytree(
                        sdir,
                        staging_dir,
                        symlinks=False,
                        ignore=shutil.ignore_patterns(
                            ".session.lock*",
                            ".session_fenced",
                            "*.lock",
                            "*.tmp",
                            "jobs",
                            "promotion.json",
                            "audit.jsonl",
                            "audit.imported.*",
                            ".scratch*",
                            ".staging*",
                            ".bundle*",
                            ".quarantine*",
                        ),
                    )
                    (staging_dir / "waivers.json").unlink(missing_ok=True)
            except FileNotFoundError as exc:
                if isinstance(exc, SessionNotFoundError):
                    raise
                raise SessionNotFoundError(f"Session '{source_session_id}' not found") from exc

            secret_file = staging_dir / ".session_secret"
            with secret_file.open("wb") as fp:
                fp.write(secrets.token_bytes(32))
                fp.flush()
                os.fsync(fp.fileno())
            set_owner_only_permissions(secret_file)

            proposals_dir = staging_dir / "proposals"
            if proposals_dir.is_dir():
                for prop_f in proposals_dir.glob("*.json"):
                    with contextlib.suppress(OSError, json.JSONDecodeError):
                        p_data = json.loads(prop_f.read_text(encoding="utf-8"))
                        if isinstance(p_data, dict):
                            p_data["session_id"] = new_sid
                            encoded = json.dumps(p_data, indent=2).encode("utf-8")
                            with prop_f.open("wb") as fp:
                                fp.write(encoded)
                                fp.flush()
                                os.fsync(fp.fileno())
                            set_owner_only_permissions(prop_f)

            now_iso = datetime.now(UTC).isoformat()
            clone_details: dict[str, Any] = {
                "source_session_id": source_session_id,
                "new_session_id": new_sid,
            }
            if source_meta.imported_audit_anchor_sha256:
                clone_details["source_imported_audit_anchor_sha256"] = (
                    source_meta.imported_audit_anchor_sha256
                )
            if has_unverified_import_ack:
                clone_details["unverified_attribution_acknowledged"] = True

            genesis_audit_entry: dict[str, Any] = {
                "timestamp": now_iso,
                "session_id": new_sid,
                "actor": actor,
                "action": "SESSION_CLONED",
                "target_version": source_meta.head_version,
                "head_sha256": source_meta.head_sha256,
                "details": clone_details,
                "prev_audit_sha256": None,
            }
            canonical_entry_bytes = dumps_jcs_bytes(genesis_audit_entry)
            entry_sha256 = hashlib.sha256(canonical_entry_bytes).hexdigest().lower()
            hmac_sig = self.security_manager.sign_audit_entry(canonical_entry_bytes)
            genesis_audit_entry["audit_sha256"] = entry_sha256
            genesis_audit_entry["hmac_signature"] = hmac_sig

            audit_file = staging_dir / "audit.jsonl"
            with audit_file.open("w", encoding="utf-8") as fp:
                fp.write(json.dumps(genesis_audit_entry) + "\n")
                fp.flush()
                os.fsync(fp.fileno())
            set_owner_only_permissions(audit_file)

            effective_taint: Literal["UNTAINTED", "TAINTED"] = (
                "TAINTED" if is_tainted else source_meta.taint_classification
            )
            clone_meta = source_meta.model_copy(
                update={
                    "session_id": new_sid,
                    "created_at": now_iso,
                    "updated_at": now_iso,
                    "taint_classification": effective_taint,
                    "promoted_versions": [],
                    "last_promoted_sha256": None,
                    "audit_chain_head_sha256": entry_sha256,
                    "imported_audit_anchor_sha256": None,
                }
            )
            _atomic_write_session_json(staging_dir, clone_meta)

            for root_str, _dirs, files in os.walk(staging_dir):
                root_path = Path(root_str)
                for fname in files:
                    fpath = root_path / fname
                    with contextlib.suppress(OSError), fpath.open("r+b") as fp:
                        fp.flush()
                        os.fsync(fp.fileno())
                _fsync_dir_best_effort(root_path)

            set_owner_only_permissions(staging_dir)

            with studio_lock(
                new_dir.parent / f".lifecycle_{new_sid}.lock",
                level=L5_SESSION,
                session_id=new_sid,
                remove_on_release=False,
            ):
                if new_dir.exists():
                    raise SessionAlreadyExistsError(f"Session '{new_sid}' already exists")
                new_dir.parent.mkdir(parents=True, exist_ok=True)
                replace_with_retry(staging_dir, new_dir)
                set_owner_only_permissions(new_dir)
                _fsync_dir_best_effort(new_dir.parent)

            return clone_meta
        finally:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)

    def get_cas(self, session_id: str) -> ContentAddressableStorage:
        """Get the ContentAddressableStorage instance for a session's inputs/cas."""
        session_dir = self.get_session_dir(session_id)
        if not session_dir.exists():
            raise SessionNotFoundError(f"Session '{session_id}' not found")
        return ContentAddressableStorage(root_dir=session_dir / "inputs")

    def append_audit_entry(
        self,
        session_id: str,
        action: str,
        target_version: str,
        head_sha256: str,
        actor: str = "system",
        details: dict[str, Any] | None = None,
        expected_audit_head: str | None = None,
    ) -> dict[str, Any]:
        """Append an entry to audit.jsonl HMAC-chained under K_audit and advance audit head hash."""
        session_dir = self.get_session_dir(session_id)
        return append_signed_audit_record(
            session_dir=session_dir,
            action=action,
            actor=actor,
            details=details,
            security_manager=self.security_manager,
            target_version=target_version,
            head_sha256=head_sha256,
            expected_audit_head=expected_audit_head,
        )

    def verify_audit_chain(self, session_id: str) -> bool:
        """Verify the cryptographic integrity of the entire audit ledger and session head anchor.

        Checks:
        1. Strict hash chaining: `prev_audit_sha256` matches preceding `audit_sha256`.
        2. Each entry's `audit_sha256` matches SHA-256 of its canonical RFC 8785 JCS bytes.
        3. Each local entry's HMAC signature verifies under K_audit (and for imported sessions
           anchored at `imported_audit_anchor_sha256`, the anchor and all subsequent records
           must verify under local K_audit).
        4. The verified physical tail matches `session.audit_chain_head_sha256` (or holds at
           most a single locally K_audit-signed write-ahead record immediately following
           `session.audit_chain_head_sha256`, rejecting tail truncation and multi-record
           divergence).
        """
        session = self.get_session(session_id)
        session_dir = self.get_session_dir(session_id)
        audit_file = session_dir / "audit.jsonl"
        if not audit_file.exists():
            raise SessionNotFoundError(f"Session '{session_id}' not found")

        try:
            tail_sha = reconcile_or_verify_audit_ledger(
                session_dir,
                session,
                self.security_manager,
                mutate=False,
            )
        except TamperEvidentChainCorruptError:
            return False

        return tail_sha is not None
