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
    section for the same session), repairs torn trailing bytes atomically, verifies tail integrity
    against `session.json.audit_chain_head_sha256`, fsyncs `audit.jsonl`, and atomically advances
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
        declared_head = (metadata.audit_chain_head_sha256 or "").strip()

        if expected_audit_head is not None and declared_head != expected_audit_head:
            raise AuditChainConflictError(
                f"Audit chain head conflict for session '{sid}': "
                f"expected '{expected_audit_head}', found '{declared_head}'"
            )

        audit_file = sdir / "audit.jsonl"
        if not audit_file.is_file():
            if declared_head:
                raise TamperEvidentChainCorruptError(
                    f"TAMPER_EVIDENT_CHAIN_CORRUPT: Audit ledger missing for session '{sid}' "
                    f"while session.json declares head '{declared_head}'."
                )
            prev_audit_sha: str | None = None
        else:
            raw_bytes = audit_file.read_bytes()
            if raw_bytes and not raw_bytes.endswith(b"\n"):
                last_nl = raw_bytes.rfind(b"\n")
                prev_line_sha: str | None = None
                if last_nl >= 0:
                    prefix_lines = [
                        ln.strip()
                        for ln in raw_bytes[:last_nl].decode("utf-8", errors="replace").splitlines()
                        if ln.strip()
                    ]
                    if prefix_lines:
                        try:
                            prev_rec = json.loads(prefix_lines[-1])
                            if isinstance(prev_rec, dict) and isinstance(
                                prev_rec.get("audit_sha256"), str
                            ):
                                prev_line_sha = prev_rec["audit_sha256"]
                        except json.JSONDecodeError as err:
                            raise TamperEvidentChainCorruptError(
                                "TAMPER_EVIDENT_CHAIN_CORRUPT: "
                                f"Corrupt audit JSON in '{sid}': {err}"
                            ) from err

                tail_fragment = raw_bytes[last_nl + 1 :]
                valid_tail, tail_sha = _is_valid_audit_record_bytes(
                    tail_fragment,
                    expected_prev_sha=prev_line_sha,
                    sec_mgr=sec_mgr,
                    require_hmac=True,
                )
                if valid_tail and (not declared_head or tail_sha == declared_head):
                    raw_bytes = raw_bytes + b"\n"
                    atomic_repair_audit_file(audit_file, raw_bytes)
                elif last_nl >= 0:
                    raw_bytes = raw_bytes[: last_nl + 1]
                    atomic_repair_audit_file(audit_file, raw_bytes)
                else:
                    raise TamperEvidentChainCorruptError(
                        f"TAMPER_EVIDENT_CHAIN_CORRUPT: Audit ledger for session '{sid}' "
                        "contains torn initial content."
                    )

            try:
                raw_lines = raw_bytes.decode("utf-8").splitlines()
            except UnicodeDecodeError as err:
                raise TamperEvidentChainCorruptError(
                    f"TAMPER_EVIDENT_CHAIN_CORRUPT: Invalid UTF-8 in audit ledger for '{sid}'."
                ) from err

            lines = [line.strip() for line in raw_lines if line.strip()]
            if not lines:
                if declared_head:
                    raise TamperEvidentChainCorruptError(
                        f"TAMPER_EVIDENT_CHAIN_CORRUPT: Audit ledger is empty for session '{sid}' "
                        f"while session.json declares head '{declared_head}'."
                    )
                prev_audit_sha = None
            else:
                try:
                    last_record = json.loads(lines[-1])
                    if not isinstance(last_record, dict):
                        raise TamperEvidentChainCorruptError(
                            f"TAMPER_EVIDENT_CHAIN_CORRUPT: Non-object tail record in '{sid}'."
                        )
                    actual_sha = last_record["audit_sha256"]
                    actual_sig = last_record["hmac_signature"]
                    if not isinstance(actual_sha, str) or not isinstance(actual_sig, str):
                        raise TamperEvidentChainCorruptError(
                            f"TAMPER_EVIDENT_CHAIN_CORRUPT: Invalid tail digest fields in '{sid}'."
                        )
                    recon = {
                        k: v
                        for k, v in last_record.items()
                        if k not in ("audit_sha256", "hmac_signature")
                    }
                    canonical_tail = dumps_jcs_bytes(recon)
                    computed_sha = hashlib.sha256(canonical_tail).hexdigest().lower()
                    if computed_sha != actual_sha:
                        raise TamperEvidentChainCorruptError(
                            f"TAMPER_EVIDENT_CHAIN_CORRUPT: Audit ledger tail SHA-256 corrupted "
                            f"for '{sid}'."
                        )
                    expected_sig = sec_mgr.sign_audit_entry(canonical_tail)
                    if not hmac.compare_digest(actual_sig, expected_sig):
                        raise TamperEvidentChainCorruptError(
                            f"TAMPER_EVIDENT_CHAIN_CORRUPT: Audit ledger tail signature corrupted "
                            f"for '{sid}'."
                        )
                    if declared_head and actual_sha != declared_head:
                        raise TamperEvidentChainCorruptError(
                            f"TAMPER_EVIDENT_CHAIN_CORRUPT: Audit ledger tail '{actual_sha}' "
                            "disagrees with session.json audit_chain_head_sha256 "
                            f"'{declared_head}' for session '{sid}'."
                        )
                    prev_audit_sha = actual_sha
                except (json.JSONDecodeError, KeyError, TypeError) as err:
                    raise TamperEvidentChainCorruptError(
                        f"TAMPER_EVIDENT_CHAIN_CORRUPT: Failed to parse audit ledger tail for "
                        f"session '{sid}': {err}"
                    ) from err

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
        lifecycle_lock_path = self.studio_root / f".lifecycle_{session_id}.lock"

        with studio_lock(
            lifecycle_lock_path,
            level=L5_SESSION,
            session_id=session_id,
            remove_on_release=True,
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

            tombstone = self.studio_root / f".trash-{session_id}-{uuid.uuid4().hex[:8]}"
            try:
                replace_with_retry(session_dir, tombstone)
                shutil.rmtree(tombstone)
            except OSError:
                shutil.rmtree(session_dir)

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
        4. The verified physical tail `audit_sha256` equals `session.audit_chain_head_sha256`
           (detecting tail-truncation rollback).
        """
        session = self.get_session(session_id)
        session_dir = self.get_session_dir(session_id)
        audit_file = session_dir / "audit.jsonl"
        if not audit_file.exists():
            raise SessionNotFoundError(f"Session '{session_id}' not found")

        raw_lines = audit_file.read_text(encoding="utf-8").splitlines()
        lines = [line.strip() for line in raw_lines if line.strip()]
        if not lines:
            return False

        anchor_sha = session.imported_audit_anchor_sha256
        seen_anchor = anchor_sha is None
        expected_prev_sha: str | None = None

        for record_str in lines:
            try:
                record = json.loads(record_str)
            except json.JSONDecodeError:
                return False

            if not isinstance(record, dict):
                return False

            actual_sha = record.get("audit_sha256")
            actual_sig = record.get("hmac_signature")
            prev_sha = record.get("prev_audit_sha256")

            if not isinstance(actual_sha, str) or prev_sha != expected_prev_sha:
                return False

            recon = {k: v for k, v in record.items() if k not in ("audit_sha256", "hmac_signature")}
            canonical_bytes = dumps_jcs_bytes(recon)
            computed_sha = hashlib.sha256(canonical_bytes).hexdigest().lower()
            if computed_sha != actual_sha:
                return False

            if not seen_anchor and actual_sha == anchor_sha:
                seen_anchor = True

            if seen_anchor:
                computed_sig = self.security_manager.sign_audit_entry(canonical_bytes)
                if not hmac.compare_digest(str(actual_sig or ""), computed_sig):
                    return False

            expected_prev_sha = actual_sha

        if not seen_anchor:
            return False

        return expected_prev_sha == session.audit_chain_head_sha256
