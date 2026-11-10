"""Studio session manager and CAS directory layout for Open-DV Ontology Studio.

Implements Task 4D.1 / Issue #208:
- Isolates studio sessions under `<studio_root>/<session_id>/`.
- Creates normative CAS layout:
  session.json, inputs/, versions/, proposals/, audit.jsonl, jobs/, .staging/.
- Strictly validates session IDs via `^[a-z0-9][a-z0-9_-]{0,63}$`, rejecting uppercase,
  path traversal, and Windows reserved device names (CON, PRN, AUX, NUL, COM1..9, LPT1..9)
  with typed INVALID_SESSION_IDENTIFIER.
- Appends write-ahead audit ledger entries HMAC-chained under K_audit using RFC 8785 JCS.
- Synchronizes audit appends under .session.lock with optimistic concurrency control (OCC).
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
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256, dumps_jcs_bytes
from opendv_service.studio.cas_storage import ContentAddressableStorage
from opendv_service.studio.workspace_security import (
    DEFAULT_STUDIO_DIR,
    WorkspaceSecurityManager,
    set_owner_only_permissions,
)

GENESIS_DRAFT_SHA256: Final[str] = (
    "99d4aa051a49f40596c53210592853fde09ee6995db5b1ff0ca8e57170dffa8e"
)
GENESIS_VERSION: Final[str] = "0.0.0"

_SESSION_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}\Z")
_SEMVER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\Av?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z"
)
_PROVISIONAL_URI_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?i)urn:opendv:provisional:")

_WINDOWS_RESERVED_DEVICES: Final[frozenset[str]] = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        "com1",
        "com2",
        "com3",
        "com4",
        "com5",
        "com6",
        "com7",
        "com8",
        "com9",
        "lpt1",
        "lpt2",
        "lpt3",
        "lpt4",
        "lpt5",
        "lpt6",
        "lpt7",
        "lpt8",
        "lpt9",
    }
)

_RESERVED_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"bundle", "init", "sessions", "health", "auth", "admin", "prune", "import", "gc"}
)


class InvalidSessionIdentifierError(ValueError):
    """Raised when session identifier fails validation (HTTP 422 / exit 2)."""

    def __init__(
        self,
        message: str,
        error_code: str = "INVALID_SESSION_IDENTIFIER",
        status_code: int = 422,
        exit_code: int = 2,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.exit_code = exit_code


class InvalidOntologyPayloadError(ValueError):
    """Raised when initial ontology payload or version fails validation (HTTP 422 / exit 2)."""

    def __init__(
        self,
        message: str,
        error_code: str = "INVALID_ONTOLOGY_PAYLOAD",
        status_code: int = 422,
        exit_code: int = 2,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.exit_code = exit_code


class SessionAlreadyExistsError(FileExistsError):
    """Raised when attempting to create a session that already exists (HTTP 409 / exit 6)."""

    def __init__(self, message: str, error_code: str = "SESSION_ALREADY_EXISTS") -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = 409
        self.exit_code = 6


class SessionNotFoundError(FileNotFoundError):
    """Raised when a requested session does not exist (HTTP 404 / exit 3)."""

    def __init__(self, message: str, error_code: str = "SESSION_NOT_FOUND") -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = 404
        self.exit_code = 3


class AuditChainConflictError(RuntimeError):
    """Raised when an OCC conflict occurs on audit chain head (HTTP 409 / exit 6)."""

    def __init__(self, message: str, error_code: str = "AUDIT_CHAIN_CONFLICT") -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = 409
        self.exit_code = 6


@contextlib.contextmanager
def session_lock_guard(lock_path: Path, timeout_sec: float = 30.0) -> Iterator[None]:
    """Cross-platform advisory file lock guard enforcing total order over .session.lock.

    MAJ-01: Does NOT create parent directories if they do not exist, preventing orphan creation.
    """
    if not lock_path.parent.exists():
        raise FileNotFoundError(f"Session directory '{lock_path.parent.name}' does not exist")

    f = lock_path.open("a+")
    set_owner_only_permissions(lock_path)
    start_time = time.monotonic()
    acquired = False
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (OSError, BlockingIOError):
                if time.monotonic() - start_time >= timeout_sec:
                    raise TimeoutError(
                        f"Lock timeout after {timeout_sec}s on '{lock_path.name}'"
                    ) from None
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            with contextlib.suppress(OSError):
                if os.name == "nt":
                    import msvcrt

                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()


def validate_session_id(session_id: str) -> str:
    """Validate a session identifier against strict normative constraints.

    Constraints:
    - Must match regex ^[a-z0-9][a-z0-9_-]{0,63}$
    - Strictly lowercase alphanumeric start, length 1..64 chars.
    - Reject uppercase characters without silent normalization.
    - Reject leading hyphen or underscore.
    - Reject path traversal characters (/, \\, ..).
    - Reject Windows reserved device names (CON, PRN, AUX, NUL, COM1..9, LPT1..9).
    - Reject reserved route keywords (bundle, init, sessions, health, etc.).

    Raises:
        InvalidSessionIdentifierError: If validation fails.
    """
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

    # Path traversal check
    if "/" in session_id or "\\" in session_id or ".." in session_id:
        raise InvalidSessionIdentifierError(
            f"Session identifier '{session_id}' contains forbidden path traversal "
            "or separator characters"
        )

    # MAJ-01: Control character, newline, and whitespace check
    if any(ord(c) < 32 or ord(c) == 127 or c in "\r\n\t\x00" for c in session_id):
        raise InvalidSessionIdentifierError(
            "Session identifier contains forbidden control or whitespace characters"
        )

    # Uppercase check: strictly reject without silent lowercase coercion
    if any(c.isupper() for c in session_id):
        raise InvalidSessionIdentifierError(
            f"Session identifier '{session_id}' contains uppercase characters; "
            "strictly lowercase alphanumeric start required without silent normalization"
        )

    # Leading hyphen or underscore check
    if session_id.startswith(("-", "_")):
        raise InvalidSessionIdentifierError(
            f"Session identifier '{session_id}' cannot start with a hyphen or underscore"
        )

    # Regex pattern check with strict \A...\Z fullmatch
    if not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise InvalidSessionIdentifierError(
            f"Session identifier '{session_id}' fails regex pattern requirement: "
            "must match '^[a-z0-9][a-z0-9_-]{{0,63}}$'"
        )

    # Windows reserved device names check (case-insensitive, with or without extension)
    base_name = session_id.split(".")[0].lower()
    if session_id.lower() in _WINDOWS_RESERVED_DEVICES or base_name in _WINDOWS_RESERVED_DEVICES:
        raise InvalidSessionIdentifierError(
            f"Session identifier '{session_id}' is a Windows reserved device name"
        )

    # Reserved keywords check
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
        """Create a new CAS studio session with full directory layout.

        Args:
            session_id: Optional user-supplied identifier. If None, auto-generates a valid ID.
            initial_ontology: Optional seed ontology. If None, initializes greenfield genesis draft.
            domain_namespace: Domain namespace identifier.
            enterprise_owner: Owner organizational unit or team.

        Returns:
            Initialized SessionMetadata.

        Raises:
            InvalidSessionIdentifierError: If session_id fails validation.
            SessionAlreadyExistsError: If a session with this ID already exists.
        """
        sid = f"s-{secrets.token_hex(8)}" if session_id is None else validate_session_id(session_id)
        session_dir = self.studio_root / sid

        # MAJ-04: Atomic check-and-create without race conditions
        try:
            session_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            raise SessionAlreadyExistsError(f"Session '{sid}' already exists") from None

        # MIN-05: Clean up newly created session directory on failure
        # to prevent orphaned directories
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

        committed_versions: list[str] = []
        now_iso = datetime.now(UTC).isoformat()

        if initial_ontology is not None:
            # MAJ-01 & MIN-04: Deep scan raw payload for provisional URIs before model validation
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

            # MIN-05: Adopt domain_namespace and enterprise_owner from seed ontology if available
            if domain_namespace == "general" and validated_ont.domain_namespace:
                domain_namespace = validated_ont.domain_namespace
            if enterprise_owner == "Enterprise Architecture" and validated_ont.enterprise_owner:
                enterprise_owner = validated_ont.enterprise_owner

            raw_version = str(validated_ont.ontology_version).strip()
            # Strict SemVer validation (no path traversal separators)
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
            committed_versions.append(head_version)

            # Persist initial version in versions/vX.Y.Z
            version_dir = (versions_dir / f"v{head_version}").resolve()
            # MAJ-01: Assert containment strictly inside versions_dir
            if not version_dir.is_relative_to(versions_dir.resolve()):
                raise InvalidOntologyPayloadError(
                    f"Version directory path '{head_version}' escapes versions root"
                )

            version_dir.mkdir(parents=True, exist_ok=True)
            set_owner_only_permissions(version_dir)

            ont_dict = validated_ont.model_dump(mode="json", exclude_none=True)

            canonical_jcs = dumps_jcs_bytes(ont_dict)
            ont_json_file = version_dir / "ontology.json"
            ont_json_file.write_bytes(canonical_jcs)
            set_owner_only_permissions(ont_json_file)

            yaml_content = yaml.safe_dump(ont_dict, sort_keys=False)
            ont_yaml_file = version_dir / "ontology.yaml"
            ont_yaml_file.write_text(yaml_content, encoding="utf-8")
            set_owner_only_permissions(ont_yaml_file)

            meta_record = {
                "target_sha256": head_sha256,
                "parent_sha256": None,
                "commit_timestamp": now_iso,
                "author": "system:seed",
                "mode": "seed",
                "domain_namespace": domain_namespace,
                "enterprise_owner": enterprise_owner,
            }
            meta_file = version_dir / "metadata.json"
            meta_file.write_text(json.dumps(meta_record, indent=2), encoding="utf-8")
            set_owner_only_permissions(meta_file)
        else:
            head_sha256 = GENESIS_DRAFT_SHA256
            head_version = GENESIS_VERSION

        # MIN-01: Audit entry hashing using RFC 8785 JCS
        genesis_audit_entry: dict[str, Any] = {
            "timestamp": now_iso,
            "session_id": sid,
            "actor": "system",
            "action": "SESSION_CREATED",
            "target_version": head_version,
            "head_sha256": head_sha256,
            "details": {
                "initial_ontology": bool(initial_ontology),
                "domain_namespace": domain_namespace,
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

        audit_chain_head_sha256 = entry_sha256

        # Atomically persist session.json
        metadata = SessionMetadata(
            session_id=sid,
            created_at=now_iso,
            updated_at=now_iso,
            head_version=head_version,
            head_sha256=head_sha256,
            audit_chain_head_sha256=audit_chain_head_sha256,
            status="ACTIVE",
            taint_classification="UNTAINTED",
            domain_namespace=domain_namespace,
            enterprise_owner=enterprise_owner,
            committed_versions=committed_versions,
        )

        session_json_path = session_dir / "session.json"
        temp_json_path = session_dir / f".session.json.{secrets.token_hex(4)}.tmp"
        try:
            temp_json_path.write_text(metadata.model_dump_json(indent=2), encoding="utf-8")
            set_owner_only_permissions(temp_json_path)
            temp_json_path.replace(session_json_path)
            set_owner_only_permissions(session_json_path)
        finally:
            if temp_json_path.exists():
                with contextlib.suppress(OSError):
                    temp_json_path.unlink()

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

    def delete_session(self, session_id: str) -> None:
        """Delete an existing studio session directory and all its artifacts."""
        session_dir = self.get_session_dir(session_id)

        if not session_dir.exists():
            raise SessionNotFoundError(f"Session '{session_id}' not found")

        lock_path = session_dir / ".session.lock"
        if lock_path.exists():
            with session_lock_guard(lock_path):
                # MIN-03: Remove session.json under lock so concurrent operations fail immediately
                session_json = session_dir / "session.json"
                with contextlib.suppress(OSError):
                    session_json.unlink()

        # MIN-03: Atomically rename to a tombstone after releasing lock handle, then rmtree
        tombstone = self.studio_root / f".trash-{session_id}-{uuid.uuid4().hex[:8]}"
        try:
            session_dir.rename(tombstone)
            shutil.rmtree(tombstone, ignore_errors=True)
        except OSError:
            shutil.rmtree(session_dir, ignore_errors=True)

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
        """Append an entry to audit.jsonl HMAC-chained under K_audit and advance audit head hash.

        Synchronized under .session.lock with optional OCC verification.

        Args:
            session_id: Session identifier.
            action: Action tag (e.g. PROPOSAL_STAGED, VERSION_COMMITTED).
            target_version: Version associated with the action.
            head_sha256: Ontology head SHA-256 digest at time of event.
            actor: Actor initiating the event.
            details: Action-specific metadata payload.
            expected_audit_head: Optional OCC anchor asserting previous audit head.

        Returns:
            The persisted and signed audit entry dict.

        Raises:
            SessionNotFoundError: If session does not exist.
            AuditChainConflictError: If expected_audit_head does not match active audit head.
        """
        session_dir = self.get_session_dir(session_id)
        session_json_path = session_dir / "session.json"

        # MAJ-01: Verify session existence BEFORE locking to prevent orphan directory creation
        if not session_dir.exists() or not session_json_path.exists():
            raise SessionNotFoundError(f"Session '{session_id}' not found")

        lock_file = session_dir / ".session.lock"

        # MAJ-04: Synchronize append and metadata update under .session.lock
        with session_lock_guard(lock_file):
            metadata = self.get_session(session_id)
            audit_file = session_dir / "audit.jsonl"

            # MIN-01: Check for torn tail before appending
            if audit_file.exists():
                raw_bytes = audit_file.read_bytes()
                if raw_bytes and not raw_bytes.endswith(b"\n"):
                    last_nl = raw_bytes.rfind(b"\n")
                    if last_nl >= 0:
                        audit_file.write_bytes(raw_bytes[: last_nl + 1])
                    else:
                        raise AuditChainConflictError(
                            f"Audit ledger for session '{session_id}' contains torn content"
                        )

            # MIN-01: Reconcile and verify audit head from last valid line of audit.jsonl
            if audit_file.exists():
                raw_lines = audit_file.read_text(encoding="utf-8").splitlines()
                lines = [line.strip() for line in raw_lines if line.strip()]
                if lines:
                    try:
                        last_record = json.loads(lines[-1])
                        if "audit_sha256" in last_record and "hmac_signature" in last_record:
                            # Re-verify HMAC of last record before trusting it as audit head
                            recon = {
                                k: v
                                for k, v in last_record.items()
                                if k not in ("audit_sha256", "hmac_signature")
                            }
                            expected_sig = self.security_manager.sign_audit_entry(
                                dumps_jcs_bytes(recon)
                            )
                            if hmac.compare_digest(last_record["hmac_signature"], expected_sig):
                                metadata.audit_chain_head_sha256 = last_record["audit_sha256"]
                            else:
                                raise AuditChainConflictError(
                                    f"Audit ledger tail signature corrupted for '{session_id}'"
                                )
                    except (json.JSONDecodeError, KeyError) as err:
                        raise AuditChainConflictError(
                            f"Failed to parse audit ledger tail for session '{session_id}': {err}"
                        ) from err

            # OCC check on audit chain head
            if (
                expected_audit_head is not None
                and metadata.audit_chain_head_sha256 != expected_audit_head
            ):
                raise AuditChainConflictError(
                    f"Audit chain head conflict for session '{session_id}': "
                    f"expected '{expected_audit_head}', found '{metadata.audit_chain_head_sha256}'"
                )

            now_iso = datetime.now(UTC).isoformat()
            entry: dict[str, Any] = {
                "timestamp": now_iso,
                "session_id": session_id,
                "actor": actor,
                "action": action,
                "target_version": target_version,
                "head_sha256": head_sha256,
                "details": details or {},
                "prev_audit_sha256": metadata.audit_chain_head_sha256,
            }

            # MIN-01: RFC 8785 canonical bytes for hash and HMAC signature
            canonical_entry_bytes = dumps_jcs_bytes(entry)
            entry_sha256 = hashlib.sha256(canonical_entry_bytes).hexdigest().lower()
            hmac_sig = self.security_manager.sign_audit_entry(canonical_entry_bytes)

            entry["audit_sha256"] = entry_sha256
            entry["hmac_signature"] = hmac_sig

            with audit_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
                f.flush()
                os.fsync(f.fileno())

            # Advance session metadata audit_chain_head_sha256 atomically
            metadata.audit_chain_head_sha256 = entry_sha256
            metadata.updated_at = now_iso
            temp_json_path = session_dir / f".session.json.{secrets.token_hex(4)}.tmp"
            try:
                temp_json_path.write_text(metadata.model_dump_json(indent=2), encoding="utf-8")
                set_owner_only_permissions(temp_json_path)
                temp_json_path.replace(session_json_path)
                set_owner_only_permissions(session_json_path)
            finally:
                if temp_json_path.exists():
                    with contextlib.suppress(OSError):
                        temp_json_path.unlink()

            return entry

    def verify_audit_chain(self, session_id: str) -> bool:
        """Verify the cryptographic integrity of the entire audit ledger for a session.

        Checks:
        1. Each entry's HMAC signature under K_audit over RFC 8785 canonical bytes.
        2. Strict hash chaining: prev_audit_sha256 matches preceding audit_sha256.
        3. Each entry's audit_sha256 matches SHA-256 of its canonical JCS bytes.
        """
        session_dir = self.get_session_dir(session_id)
        audit_file = session_dir / "audit.jsonl"
        if not audit_file.exists():
            raise SessionNotFoundError(f"Session '{session_id}' not found")

        raw_lines = audit_file.read_text(encoding="utf-8").splitlines()
        lines = [line.strip() for line in raw_lines if line.strip()]
        if not lines:
            return False

        expected_prev_sha: str | None = None
        for record_str in lines:
            try:
                record = json.loads(record_str)
            except json.JSONDecodeError:
                return False

            actual_sha = record.get("audit_sha256")
            actual_sig = record.get("hmac_signature")
            prev_sha = record.get("prev_audit_sha256")

            if prev_sha != expected_prev_sha:
                return False

            recon = {
                k: v for k, v in record.items() if k not in ("audit_sha256", "hmac_signature")
            }
            canonical_bytes = dumps_jcs_bytes(recon)
            computed_sha = hashlib.sha256(canonical_bytes).hexdigest().lower()
            if computed_sha != actual_sha:
                return False

            computed_sig = self.security_manager.sign_audit_entry(canonical_bytes)
            if not hmac.compare_digest(actual_sig or "", computed_sig):
                return False

            expected_prev_sha = actual_sha

        return True
