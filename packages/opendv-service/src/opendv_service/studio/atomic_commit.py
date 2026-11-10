"""Multi-file atomic commit protocol, crash recovery, and CAS GC for Open-DV Ontology Studio.

Implements Task 4D.3 / Issue #210 and docs/19 §6.2, §6.2.1, §6.3, §6.5:
- 6-step atomic commit protocol under Total Lock Hierarchy L5 session lock:
    1. Acquire session lock L5 under Total Lock Hierarchy.
    2. OCC check: verify session.head_sha256 == delta.base_sha256 (also accepts If-Match header).
       Mismatch raises ConcurrentModificationError (HTTP 412 / exit 5).
    3. Write-ahead log PENDING_COMMIT entry to audit.jsonl signed via K_audit.
    4. Stage version artifacts in .staging-<u>/, then atomic directory rename via
       os.replace to versions/vX.Y.Z with parent directory fsync (FlushFileBuffers on Windows).
    5. Atomic metadata commit on session.json via .session.json.<u>.tmp with exponential
       backoff on Windows WinError 32 (10ms..250ms).
    6. Append COMMITTED entry to audit.jsonl signed via K_audit, advancing session audit head.
- Startup Crash Recovery & Reconciliation:
    1. Detect incomplete staging directories (.staging-*) and clean them up safely.
    2. Verify forward-only SHA-256 HMAC audit chain signed via K_audit. Tampering or corruption
       raises TamperEvidentChainCorruptError("TAMPER_EVIDENT_CHAIN_CORRUPT") (HTTP 422 / exit 4).
    3. Incomplete commit reconciliation: if PENDING_COMMIT is logged but session.json was not
       committed before a crash: if versions/vX.Y.Z exists and is valid, finalize metadata commit;
       if not, rollback and append COMMIT_ABORTED.
    4. Orphan version directory reconciliation: uncommitted forward directories with matching
       digests are finalized; corrupt/mismatched uncommitted directories are quarantined.
- CAS Mark-and-Sweep Garbage Collection:
    - Identify reachability roots: active sources in manifest.json, active citations in
      chunks.jsonl, pending proposals, and committed versions.
    - Purge unreferenced orphan blobs from inputs/cas/.
    - Protect sources referenced by pending proposals (SourceReferencedByPendingProposalError)
      and committed versions (SourceReferencedByCommittedVersionError).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import random
import re
import secrets
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import ValidationError

from opendv_contracts.dtos.ontology import AddConceptOp, OntologyDelta
from opendv_core.models.graph_a import CanonicalBusinessConcept, GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256, dumps_jcs_bytes
from opendv_service.ontology.applier import apply as apply_delta
from opendv_service.ontology.validator import SemVerCalculusError
from opendv_service.studio.cas_storage import ContentAddressableStorage
from opendv_service.studio.locks import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    L5_SESSION,
    studio_lock,
)
from opendv_service.studio.session_manager import (
    GENESIS_VERSION,
    SessionMetadata,
    StudioSessionManager,
    set_owner_only_permissions,
)
from opendv_service.studio.workspace_security import WorkspaceSecurityManager

logger = logging.getLogger(__name__)

_SEMVER_REGEX: Final[re.Pattern[str]] = re.compile(
    r"\Av?(\d+)\.(\d+)\.(\d+)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z"
)


class ConcurrentModificationError(RuntimeError):
    """Raised when optimistic concurrency control precondition fails (HTTP 412 / exit 5)."""

    def __init__(
        self,
        message: str = "Concurrent modification detected",
        error_code: str = "ONT_EVO_CONCURRENT_MODIFICATION",
        status_code: int = 412,
        exit_code: int = 5,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.exit_code = exit_code


class TamperEvidentChainCorruptError(RuntimeError):
    """Raised when the forward-only HMAC audit chain is corrupt (HTTP 422 / exit 4)."""

    def __init__(
        self,
        message: str = "TAMPER_EVIDENT_CHAIN_CORRUPT",
        error_code: str = "TAMPER_EVIDENT_CHAIN_CORRUPT",
        status_code: int = 422,
        exit_code: int = 4,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.exit_code = exit_code


class CommitValidationError(ValueError):
    """Raised when commit payload or target version fails validation (HTTP 422 / exit 2)."""

    def __init__(
        self,
        message: str,
        error_code: str = "COMMIT_VALIDATION_ERROR",
        status_code: int = 422,
        exit_code: int = 2,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.exit_code = exit_code


class SourceReferencedByPendingProposalError(RuntimeError):
    """Raised when source is cited by a pending proposal (HTTP 422 / exit 4)."""

    def __init__(
        self,
        message: str,
        error_code: str = "SOURCE_REFERENCED_BY_PENDING_PROPOSAL",
        status_code: int = 422,
        exit_code: int = 4,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.exit_code = exit_code


class SourceReferencedByCommittedVersionError(RuntimeError):
    """Raised when source is cited by a committed version (HTTP 422 / exit 4)."""

    def __init__(
        self,
        message: str,
        error_code: str = "SOURCE_REFERENCED_BY_COMMITTED_VERSION",
        status_code: int = 422,
        exit_code: int = 4,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.exit_code = exit_code


@dataclass(frozen=True)
class VersionCommitResult:
    """Result of a successfully committed ontology version."""

    version: str
    target_sha256: str
    parent_sha256: str
    audit_head_sha256: str
    committed_at: str
    proposal_id: str | None = None


def parse_semver_tuple(version_str: str) -> tuple[int, int, int]:
    """Parse a semantic version string into a (major, minor, patch) integer tuple."""
    match = _SEMVER_REGEX.match(version_str.strip())
    if not match:
        raise ValueError(f"Invalid semantic version string: '{version_str}'")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def fsync_directory_best_effort(dir_path: Path) -> None:
    """Flush directory metadata buffers to physical storage.

    On Windows, uses kernel32.CreateFileW with FILE_FLAG_BACKUP_SEMANTICS and FlushFileBuffers.
    On POSIX, opens the directory file descriptor and executes os.fsync.
    Non-fatal errors are logged as warnings, relying on filesystem journaling.
    """
    if not dir_path.exists():
        return

    if sys.platform == "win32":
        try:
            import ctypes

            # FILE_FLAG_BACKUP_SEMANTICS (0x02000000) is required to open directory handles
            # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE = 7
            # OPEN_EXISTING = 3
            # GENERIC_READ | GENERIC_WRITE = 0xC0000000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateFileW(
                str(dir_path),
                0xC0000000,
                7,
                None,
                3,
                0x02000000,
                None,
            )
            if handle not in (-1, 0):
                kernel32.FlushFileBuffers(handle)
                kernel32.CloseHandle(handle)
        except OSError as exc:
            logger.warning("Windows directory FlushFileBuffers on %s failed: %s", dir_path, exc)
    else:
        try:
            fd = os.open(str(dir_path), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            logger.warning("POSIX directory fsync on %s failed: %s", dir_path, exc)


def replace_with_retry(
    src: Path,
    dst: Path,
    max_timeout: float = 5.0,
    initial_delay: float = 0.010,
    max_delay: float = 0.250,
) -> None:
    """Atomically replace dst with src, retrying transient Windows sharing violations.

    On Windows, concurrent indexing, antivirus, or file handles can raise WinError 32
    (ERROR_SHARING_VIOLATION) or WinError 5 (ERROR_ACCESS_DENIED).
    Implements truncated exponential backoff with jitter between 10ms and 250ms.
    """
    start_time = time.monotonic()
    delay = initial_delay

    while True:
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            is_transient = sys.platform == "win32" and winerror in (32, 5)
            if not is_transient or (time.monotonic() - start_time >= max_timeout):
                raise
            jitter = random.uniform(0.005, 0.020)
            time.sleep(delay + jitter)
            delay = min(delay * 2.0, max_delay)



class AtomicCommitService:
    """Service implementing 6-step multi-file atomic commit, crash recovery, and CAS GC."""

    def __init__(
        self,
        session_manager: StudioSessionManager,
        security_manager: WorkspaceSecurityManager | None = None,
    ) -> None:
        self.session_manager = session_manager
        self.security_manager = security_manager or session_manager.security_manager

    def commit_delta(
        self,
        session_id: str,
        delta: OntologyDelta,
        proposal_id: str | None = None,
        author: str = "steward",
        if_match: str | None = None,
        evaluation: dict[str, Any] | None = None,
        timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> VersionCommitResult:
        """Execute the 6-step atomic commit protocol under Total Lock Hierarchy L5 session lock.

        Protocol Steps:
        1. Acquire session lock L5 under Total Lock Hierarchy (.session.lock).
        2. OCC check: verify session.head_sha256 == delta.base_sha256 (also accept If-Match header).
           Mismatch raises ConcurrentModificationError (HTTP 412 / exit 5).
        3. Write-ahead log PENDING_COMMIT entry to audit.jsonl signed via K_audit.
        4. Stage version artifacts in .staging-<u>/, then atomic directory rename via os.replace
           to versions/vX.Y.Z with parent directory fsync.
        5. Atomic metadata commit on session.json via .session.json.<u>.tmp with exponential
           backoff on Windows WinError 32. If proposal_id is set, mark proposals/<pid>.json
           ACCEPTED.
        6. Append COMMITTED entry to audit.jsonl signed via K_audit, advancing session audit head.
        7. Release L5 lock.
        """
        session_dir = self.session_manager.get_session_dir(session_id)
        if not session_dir.exists() or not (session_dir / "session.json").exists():
            raise FileNotFoundError(f"Session '{session_id}' not found")

        lock_path = session_dir / ".session.lock"

        # Step 1: Acquire L5 session lock
        with studio_lock(lock_path, level=L5_SESSION, session_id=session_id, timeout=timeout_sec):
            # Execute per-operation startup / crash recovery under L5 before mutation
            self.reconcile_and_recover_session(session_id)

            session = self.session_manager.get_session(session_id)

            # Step 2: OCC Precondition Verification
            delta_base_sha = delta.parent_sha256
            if session.head_version == GENESIS_VERSION:
                # Greenfield genesis session
                if delta_base_sha and delta_base_sha != session.head_sha256:
                    raise ConcurrentModificationError(
                        f"OCC check failed for session '{session_id}': genesis head is "
                        f"'{session.head_sha256}', but delta specifies base '{delta_base_sha}'"
                    )
            elif delta_base_sha != session.head_sha256:
                raise ConcurrentModificationError(
                    f"OCC check failed for session '{session_id}': active head is "
                    f"'{session.head_sha256}', but delta specifies base '{delta_base_sha}'"
                )

            # If-Match header verification
            if if_match is not None:
                clean_if_match = if_match.strip().strip('"')
                if clean_if_match.startswith("W/"):
                    clean_if_match = clean_if_match[2:].strip('"')
                if clean_if_match != session.head_sha256:
                    raise ConcurrentModificationError(
                        f"If-Match precondition failed for session '{session_id}': "
                        f"header specifies '{clean_if_match}', but session head is "
                        f"'{session.head_sha256}'"
                    )

            # Determine base ontology and target version
            try:
                if session.head_version == GENESIS_VERSION:
                    # Greenfield genesis session: derive initial ontology from genesis delta
                    add_concepts = [op for op in delta.operations if isinstance(op, AddConceptOp)]
                    if not add_concepts:
                        raise CommitValidationError(
                            "Genesis commit requires at least one AddConceptOp"
                        )
                    first_op = add_concepts[0]
                    c_name = (
                        first_op.concept_name
                        or first_op.concept_uri.split(":")[-1].replace("_", " ").title()
                    )
                    defn = (
                        first_op.definition
                        or first_op.description
                        or f"Canonical business concept: {c_name}."
                    )
                    effective_ns = (
                        delta.domain_namespace or session.domain_namespace or "general"
                    )
                    temp_base = GraphAOntology(
                        ontology_version="0.0.0",
                        domain_namespace=effective_ns,
                        enterprise_owner=session.enterprise_owner or "Enterprise Architecture",
                        canonical_business_concepts=[
                            CanonicalBusinessConcept(
                                concept_uri=first_op.concept_uri,
                                concept_name=c_name,
                                elm_archetype=first_op.elm_archetype or "CORE_CONCEPT",
                                definition=defn,
                            )
                        ],
                        canonical_relationships=[],
                        reference_enumerations=[],
                    )
                    target_ontology = apply_delta(temp_base, delta, validate_structural=True)
                    target_ontology.parent_sha256 = None
                else:
                    base_version_dir = (
                        session_dir / "versions" / f"v{session.head_version.lstrip('v')}"
                    )
                    ont_path = base_version_dir / "ontology.json"
                    if not ont_path.exists():
                        ont_path = base_version_dir / "ontology.yaml"
                        if not ont_path.exists():
                            ver_tag = f"v{session.head_version}"
                            raise FileNotFoundError(
                                f"Base ontology artifact missing for version '{ver_tag}'"
                            )
                        ont_dict = yaml.safe_load(ont_path.read_text(encoding="utf-8"))
                        base_ontology = GraphAOntology.model_validate(ont_dict)
                    else:
                        base_ontology = GraphAOntology.model_validate_json(
                            ont_path.read_text(encoding="utf-8")
                        )
                    target_ontology = apply_delta(base_ontology, delta, validate_structural=True)
            except (SemVerCalculusError, ValidationError, ValueError) as err:
                raise CommitValidationError(f"Invalid ontology delta transition: {err}") from err

            target_version_raw = target_ontology.ontology_version.lstrip("v")
            target_version_tag = f"v{target_version_raw}"

            # Verify target version is strictly greater than active head version
            if parse_semver_tuple(target_version_raw) <= parse_semver_tuple(session.head_version):
                raise CommitValidationError(
                    f"Target version '{target_version_raw}' must exceed active version "
                    f"'{session.head_version}' in SemVer total order"
                )

            # Compute canonical RFC 8785 SHA-256 digest
            target_sha256 = compute_ontology_sha256(target_ontology)
            canonical_ont_bytes = dumps_jcs_bytes(
                target_ontology.model_dump(mode="json", exclude_none=True)
            )

            # Step 3: Write-ahead log PENDING_COMMIT entry to audit.jsonl signed via K_audit
            now_iso = datetime.now(UTC).isoformat()
            pending_audit_details: dict[str, Any] = {
                "target_version": target_version_tag,
                "target_sha256": target_sha256,
                "parent_sha256": session.head_sha256,
                "proposal_id": proposal_id,
            }
            pending_entry = self._append_raw_audit_entry_under_lock(
                session_dir=session_dir,
                session_id=session_id,
                action="PENDING_COMMIT",
                target_version=target_version_tag,
                head_sha256=target_sha256,
                actor=author,
                details=pending_audit_details,
                prev_audit_sha=session.audit_chain_head_sha256,
            )
            session.audit_chain_head_sha256 = pending_entry["audit_sha256"]

            # Step 4: Stage version artifacts in .staging-<u>/
            staging_token = secrets.token_hex(6)
            staging_dir = session_dir / f".staging-{staging_token}"
            staging_dir.mkdir(parents=True, exist_ok=True)
            set_owner_only_permissions(staging_dir)

            try:
                # 4.1 Write ontology.json
                ont_json_path = staging_dir / "ontology.json"
                ont_json_path.write_bytes(canonical_ont_bytes)
                set_owner_only_permissions(ont_json_path)

                # 4.2 Write ontology.yaml
                ont_yaml_path = staging_dir / "ontology.yaml"
                yaml_str = yaml.safe_dump(
                    target_ontology.model_dump(mode="json", exclude_none=True),
                    sort_keys=False,
                )
                ont_yaml_path.write_text(yaml_str, encoding="utf-8")
                set_owner_only_permissions(ont_yaml_path)

                # 4.3 Write delta.json
                delta_json_path = staging_dir / "delta.json"
                delta_json_path.write_text(delta.model_dump_json(indent=2), encoding="utf-8")
                set_owner_only_permissions(delta_json_path)

                # 4.4 Write meta.json
                meta_record: dict[str, Any] = {
                    "version": target_version_tag,
                    "target_version": target_version_tag,
                    "target_sha256": target_sha256,
                    "parent_sha256": session.head_sha256,
                    "proposal_id": proposal_id,
                    "created_at": now_iso,
                    "author": author,
                    "audit_chain_head_sha256": session.audit_chain_head_sha256,
                    "domain_namespace": session.domain_namespace,
                    "enterprise_owner": session.enterprise_owner,
                }
                meta_json_path = staging_dir / "meta.json"
                meta_json_path.write_text(json.dumps(meta_record, indent=2), encoding="utf-8")
                set_owner_only_permissions(meta_json_path)

                # 4.5 Write evaluation.json
                eval_json_path = staging_dir / "evaluation.json"
                eval_json_path.write_text(
                    json.dumps(evaluation or {}, indent=2), encoding="utf-8"
                )
                set_owner_only_permissions(eval_json_path)

                # Flush and fsync all staged files
                for fpath in (
                    ont_json_path,
                    ont_yaml_path,
                    delta_json_path,
                    meta_json_path,
                    eval_json_path,
                ):
                    with fpath.open("r+b") as fp:
                        fp.flush()
                        os.fsync(fp.fileno())

                # Atomic directory rename to versions/vX.Y.Z
                versions_parent = session_dir / "versions"
                versions_parent.mkdir(parents=True, exist_ok=True)
                target_version_dir = versions_parent / target_version_tag

                replace_with_retry(staging_dir, target_version_dir)
                fsync_directory_best_effort(versions_parent)
            finally:
                if staging_dir.exists():
                    shutil.rmtree(staging_dir, ignore_errors=True)

            # Step 5: Atomic metadata commit on session.json (The Commit Point)
            # If proposal_id is set, atomically update proposals/<pid>.json to ACCEPTED
            if proposal_id:
                prop_file = session_dir / "proposals" / f"{proposal_id}.json"
                if prop_file.exists():
                    try:
                        prop_data = json.loads(prop_file.read_text(encoding="utf-8"))
                        prop_data["status"] = "ACCEPTED"
                        prop_data["updated_at"] = now_iso
                        tmp_prop = (
                            session_dir
                            / "proposals"
                            / f".{proposal_id}.{secrets.token_hex(4)}.tmp"
                        )
                        tmp_prop.write_text(json.dumps(prop_data, indent=2), encoding="utf-8")
                        set_owner_only_permissions(tmp_prop)
                        replace_with_retry(tmp_prop, prop_file)
                    except (OSError, json.JSONDecodeError) as exc:
                        logger.warning(
                            "Failed to update proposal %s to ACCEPTED: %s", proposal_id, exc
                        )

            # Update SessionMetadata fields
            committed_list = list(session.committed_versions)
            if target_version_tag not in committed_list:
                committed_list.append(target_version_tag)

            updated_metadata = SessionMetadata(
                session_id=session.session_id,
                created_at=session.created_at,
                updated_at=now_iso,
                head_version=target_version_raw,
                head_sha256=target_sha256,
                audit_chain_head_sha256=session.audit_chain_head_sha256,
                status=session.status,
                taint_classification=session.taint_classification,
                domain_namespace=session.domain_namespace,
                enterprise_owner=session.enterprise_owner,
                committed_versions=committed_list,
            )

            # Atomic session.json write via .session.json.<u>.tmp with exponential backoff
            session_json_path = session_dir / "session.json"
            tmp_session_path = session_dir / f".session.json.{secrets.token_hex(4)}.tmp"
            try:
                tmp_session_path.write_text(
                    updated_metadata.model_dump_json(indent=2), encoding="utf-8"
                )
                set_owner_only_permissions(tmp_session_path)
                with tmp_session_path.open("r+b") as fp:
                    fp.flush()
                    os.fsync(fp.fileno())
                replace_with_retry(tmp_session_path, session_json_path)
                set_owner_only_permissions(session_json_path)
            finally:
                if tmp_session_path.exists():
                    with contextlib.suppress(OSError):
                        tmp_session_path.unlink()

            # Step 6: Append COMMITTED entry to audit.jsonl signed via K_audit
            committed_audit_details: dict[str, Any] = {
                "target_version": target_version_tag,
                "target_sha256": target_sha256,
                "parent_sha256": session.head_sha256,
                "proposal_id": proposal_id,
            }
            committed_entry = self._append_raw_audit_entry_under_lock(
                session_dir=session_dir,
                session_id=session_id,
                action="COMMITTED",
                target_version=target_version_tag,
                head_sha256=target_sha256,
                actor=author,
                details=committed_audit_details,
                prev_audit_sha=session.audit_chain_head_sha256,
            )
            final_audit_head = committed_entry["audit_sha256"]

            # Advance audit_chain_head_sha256 in session.json
            updated_metadata.audit_chain_head_sha256 = final_audit_head
            tmp_session_path2 = session_dir / f".session.json.{secrets.token_hex(4)}.tmp"
            try:
                tmp_session_path2.write_text(
                    updated_metadata.model_dump_json(indent=2), encoding="utf-8"
                )
                set_owner_only_permissions(tmp_session_path2)
                replace_with_retry(tmp_session_path2, session_json_path)
            finally:
                if tmp_session_path2.exists():
                    with contextlib.suppress(OSError):
                        tmp_session_path2.unlink()

            return VersionCommitResult(
                version=target_version_tag,
                target_sha256=target_sha256,
                parent_sha256=session.head_sha256,
                audit_head_sha256=final_audit_head,
                committed_at=now_iso,
                proposal_id=proposal_id,
            )

    def reconcile_and_recover_session(self, session_id: str) -> None:
        """Execute deterministic crash recovery and reconciliation under L5 session lock.

        Follows docs/19 §6.2.1:
        1. Verify audit chain HMAC integrity and tail consistency.
        2. Reconcile missing active version directory from .staging-* directories.
        3. Prune abandoned staging directories (safety invariant: never delete matching versions).
        4. Reconcile orphan version directories or finalize interrupted commits.
        5. Reconcile incomplete PENDING_COMMIT audit logs (commit or rollback).
        """
        session_dir = self.session_manager.get_session_dir(session_id)
        session_json_path = session_dir / "session.json"
        if not session_dir.exists() or not session_json_path.exists():
            return

        session = SessionMetadata.model_validate_json(session_json_path.read_text(encoding="utf-8"))
        audit_file = session_dir / "audit.jsonl"

        # 1. Audit Chain Integrity Verification
        tail_sha256 = self._verify_audit_chain_or_raise(session_id, session)

        # Reconcile tail if session anchor is ancestor of physical tail
        if tail_sha256 != session.audit_chain_head_sha256:
            session.audit_chain_head_sha256 = tail_sha256
            self._write_session_json(session_dir, session)

        # 2. Missing Active Version Directory Recovery
        if session.head_version != GENESIS_VERSION:
            active_version_tag = f"v{session.head_version.lstrip('v')}"
            active_version_dir = session_dir / "versions" / active_version_tag
            if not active_version_dir.exists():
                repaired = False
                for staging_dir in session_dir.glob(".staging-*"):
                    if staging_dir.is_dir():
                        meta_file = staging_dir / "meta.json"
                        if meta_file.exists():
                            try:
                                mdata = json.loads(meta_file.read_text(encoding="utf-8"))
                                if mdata.get("target_sha256") == session.head_sha256:
                                    active_version_dir.parent.mkdir(parents=True, exist_ok=True)
                                    replace_with_retry(staging_dir, active_version_dir)
                                    fsync_directory_best_effort(active_version_dir.parent)
                                    repaired = True
                                    break
                            except (OSError, json.JSONDecodeError):
                                continue
                if not repaired:
                    logger.warning(
                        "Active version directory '%s' missing and could not be recovered",
                        active_version_tag,
                    )

        # 3. Staging Cleanup Safety Invariant
        committed_set = set(session.committed_versions)
        for staging_dir in list(session_dir.glob(".staging-*")):
            if staging_dir.is_dir():
                meta_file = staging_dir / "meta.json"
                keep = False
                if meta_file.exists():
                    try:
                        mdata = json.loads(meta_file.read_text(encoding="utf-8"))
                        t_sha = mdata.get("target_sha256")
                        t_ver = mdata.get("version") or mdata.get("target_version")
                        if (
                            t_sha == session.head_sha256
                            or (t_ver and t_ver in committed_set)
                            or (t_ver and t_ver.lstrip("v") in committed_set)
                        ):
                            keep = True
                    except (OSError, json.JSONDecodeError):
                        pass
                if not keep:
                    shutil.rmtree(staging_dir, ignore_errors=True)

        # 4. Orphan Version Directory Reconciliation
        versions_root = session_dir / "versions"
        if versions_root.exists():
            for v_dir in sorted(versions_root.iterdir()):
                if not v_dir.is_dir():
                    continue
                v_name = v_dir.name
                v_clean = v_name.lstrip("v")
                try:
                    v_tuple = parse_semver_tuple(v_clean)
                except ValueError:
                    continue

                is_committed = (
                    v_name in committed_set
                    or v_clean in committed_set
                    or f"v{v_clean}" in committed_set
                )
                if is_committed:
                    continue  # Committed versions in lineage are immutable and NEVER modified

                # Check if this forward directory is orphan
                if v_tuple > parse_semver_tuple(session.head_version):
                    meta_path = v_dir / "meta.json"
                    ont_path = v_dir / "ontology.json"
                    reconciled = False

                    if meta_path.exists() and ont_path.exists():
                        try:
                            mdata = json.loads(meta_path.read_text(encoding="utf-8"))
                            ont_model = GraphAOntology.model_validate_json(
                                ont_path.read_text(encoding="utf-8")
                            )
                            computed_sha = compute_ontology_sha256(ont_model)
                            parent_sha = mdata.get("parent_sha256")
                            target_sha = mdata.get("target_sha256")

                            if parent_sha == session.head_sha256 and computed_sha == target_sha:
                                # Supervisor completes the interrupted commit!
                                session.head_version = v_clean
                                session.head_sha256 = target_sha
                                if v_name not in session.committed_versions:
                                    session.committed_versions.append(v_name)
                                session.updated_at = datetime.now(UTC).isoformat()

                                pid = mdata.get("proposal_id")
                                if pid:
                                    prop_file = session_dir / "proposals" / f"{pid}.json"
                                    if prop_file.exists():
                                        try:
                                            p_data = json.loads(
                                                prop_file.read_text(encoding="utf-8")
                                            )
                                            p_data["status"] = "ACCEPTED"
                                            p_data["updated_at"] = session.updated_at
                                            prop_file.write_text(
                                                json.dumps(p_data, indent=2), encoding="utf-8"
                                            )
                                        except (OSError, json.JSONDecodeError):
                                            pass

                                # Append recovery audit record
                                rec_entry = self._append_raw_audit_entry_under_lock(
                                    session_dir=session_dir,
                                    session_id=session_id,
                                    action="COMMITTED",
                                    target_version=v_name,
                                    head_sha256=target_sha,
                                    actor="system:recovery",
                                    details={
                                        "target_version": v_name,
                                        "target_sha256": target_sha,
                                        "parent_sha256": parent_sha,
                                        "reconciled": True,
                                    },
                                    prev_audit_sha=session.audit_chain_head_sha256,
                                )
                                session.audit_chain_head_sha256 = rec_entry["audit_sha256"]
                                self._write_session_json(session_dir, session)
                                reconciled = True
                        except (OSError, json.JSONDecodeError, ValidationError) as exc:
                            logger.warning(
                                "Failed to validate forward version %s for recovery: %s",
                                v_name,
                                exc,
                            )

                    if not reconciled:
                        # Move corrupt/mismatched uncommitted directory to quarantine
                        quarantine_dir = (
                            session_dir
                            / ".quarantine"
                            / f"{v_name}-{int(time.time())}-{secrets.token_hex(4)}"
                        )
                        quarantine_dir.parent.mkdir(parents=True, exist_ok=True)
                        replace_with_retry(v_dir, quarantine_dir)

        # 5. Incomplete PENDING_COMMIT reconciliation from audit ledger tail
        if audit_file.exists():
            records = self._read_valid_audit_records(audit_file)
            if records and records[-1].get("action") == "PENDING_COMMIT":
                last_pending = records[-1]
                p_details = last_pending.get("details", {})
                p_version = p_details.get("target_version")
                p_target_sha = p_details.get("target_sha256")

                # Check if p_version exists and matches p_target_sha
                v_dir = session_dir / "versions" / str(p_version)
                is_version_valid = False
                if v_dir.exists() and (v_dir / "ontology.json").exists():
                    try:
                        ont_model = GraphAOntology.model_validate_json(
                            (v_dir / "ontology.json").read_text(encoding="utf-8")
                        )
                        if compute_ontology_sha256(ont_model) == p_target_sha:
                            is_version_valid = True
                    except (OSError, json.JSONDecodeError, ValidationError):
                        pass

                if is_version_valid:
                    clean_v = str(p_version).lstrip("v")
                    session.head_version = clean_v
                    session.head_sha256 = str(p_target_sha)
                    if str(p_version) not in session.committed_versions:
                        session.committed_versions.append(str(p_version))
                    session.updated_at = datetime.now(UTC).isoformat()

                    committed_entry = self._append_raw_audit_entry_under_lock(
                        session_dir=session_dir,
                        session_id=session_id,
                        action="COMMITTED",
                        target_version=str(p_version),
                        head_sha256=str(p_target_sha),
                        actor="system:recovery",
                        details=p_details,
                        prev_audit_sha=records[-1]["audit_sha256"],
                    )
                    session.audit_chain_head_sha256 = committed_entry["audit_sha256"]
                    self._write_session_json(session_dir, session)
                else:
                    abort_entry = self._append_raw_audit_entry_under_lock(
                        session_dir=session_dir,
                        session_id=session_id,
                        action="COMMIT_ABORTED",
                        target_version=str(p_version or "unknown"),
                        head_sha256=session.head_sha256,
                        actor="system:recovery",
                        details={"aborted_pending_sha256": p_target_sha},
                        prev_audit_sha=records[-1]["audit_sha256"],
                    )
                    session.audit_chain_head_sha256 = abort_entry["audit_sha256"]
                    self._write_session_json(session_dir, session)

    def _verify_audit_chain_or_raise(
        self, session_id: str, session: SessionMetadata
    ) -> str:
        """Verify the full HMAC and hash chaining of audit.jsonl, raising error if corrupt."""
        session_dir = self.session_manager.get_session_dir(session_id)
        audit_file = session_dir / "audit.jsonl"

        if not audit_file.exists():
            raise TamperEvidentChainCorruptError(
                f"Audit ledger missing for session '{session_id}'"
            )

        raw_bytes = audit_file.read_bytes()
        if not raw_bytes:
            raise TamperEvidentChainCorruptError(
                f"Audit ledger is empty for session '{session_id}'"
            )

        # Repair torn tail if uncompleted write occurred
        if not raw_bytes.endswith(b"\n"):
            last_nl = raw_bytes.rfind(b"\n")
            if last_nl >= 0:
                raw_bytes = raw_bytes[: last_nl + 1]
                audit_file.write_bytes(raw_bytes)
            else:
                raise TamperEvidentChainCorruptError(
                    f"Audit ledger for session '{session_id}' contains unparseable torn bytes"
                )

        raw_lines = raw_bytes.decode("utf-8").splitlines()
        lines = [line.strip() for line in raw_lines if line.strip()]
        if not lines:
            raise TamperEvidentChainCorruptError(
                f"Audit ledger contains no valid lines for session '{session_id}'"
            )

        expected_prev: str | None = None
        chain_hashes: list[str] = []

        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TamperEvidentChainCorruptError(
                    f"Corrupt JSON in audit ledger for session '{session_id}': {exc}"
                ) from exc

            actual_sha = record.get("audit_sha256")
            actual_sig = record.get("hmac_signature")
            prev_sha = record.get("prev_audit_sha256")

            if prev_sha != expected_prev:
                raise TamperEvidentChainCorruptError(
                    f"Audit chain hash break in '{session_id}': expected prev '{expected_prev}', "
                    f"got '{prev_sha}'"
                )

            recon = {
                k: v for k, v in record.items() if k not in ("audit_sha256", "hmac_signature")
            }
            canonical_bytes = dumps_jcs_bytes(recon)
            computed_sha = hashlib.sha256(canonical_bytes).hexdigest().lower()
            if computed_sha != actual_sha:
                raise TamperEvidentChainCorruptError(
                    f"Audit entry SHA-256 digest mismatch in '{session_id}'"
                )

            expected_sig = self.security_manager.sign_audit_entry(canonical_bytes)
            if not hmac.compare_digest(actual_sig or "", expected_sig):
                raise TamperEvidentChainCorruptError(
                    f"Audit entry HMAC signature verification failed in '{session_id}'"
                )

            chain_hashes.append(actual_sha)
            expected_prev = actual_sha

        tail_sha256 = chain_hashes[-1]

        # Verify session anchor: session.audit_chain_head_sha256 must appear in the chain
        if session.audit_chain_head_sha256 not in chain_hashes:
            raise TamperEvidentChainCorruptError(
                f"Session anchor '{session.audit_chain_head_sha256}' missing from audit chain "
                f"in session '{session_id}' (tail is '{tail_sha256}')"
            )

        return tail_sha256

    def _read_valid_audit_records(self, audit_file: Path) -> list[dict[str, Any]]:
        """Read and parse all valid JSON lines from audit.jsonl."""
        records: list[dict[str, Any]] = []
        for line in audit_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                with contextlib.suppress(json.JSONDecodeError):
                    records.append(json.loads(line))
        return records

    def _append_raw_audit_entry_under_lock(
        self,
        session_dir: Path,
        session_id: str,
        action: str,
        target_version: str,
        head_sha256: str,
        actor: str,
        details: dict[str, Any],
        prev_audit_sha: str | None,
    ) -> dict[str, Any]:
        """Format, sign with K_audit, and append an audit entry to audit.jsonl under lock."""
        now_iso = datetime.now(UTC).isoformat()
        entry: dict[str, Any] = {
            "timestamp": now_iso,
            "session_id": session_id,
            "actor": actor,
            "action": action,
            "target_version": target_version,
            "head_sha256": head_sha256,
            "details": details,
            "prev_audit_sha256": prev_audit_sha,
        }

        canonical_bytes = dumps_jcs_bytes(entry)
        entry_sha256 = hashlib.sha256(canonical_bytes).hexdigest().lower()
        hmac_sig = self.security_manager.sign_audit_entry(canonical_bytes)

        entry["audit_sha256"] = entry_sha256
        entry["hmac_signature"] = hmac_sig

        audit_file = session_dir / "audit.jsonl"
        with audit_file.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(entry) + "\n")
            fp.flush()
            os.fsync(fp.fileno())

        set_owner_only_permissions(audit_file)
        return entry

    def _write_session_json(self, session_dir: Path, metadata: SessionMetadata) -> None:
        """Atomically overwrite session.json using a temporary file with retry."""
        session_json_path = session_dir / "session.json"
        tmp_path = session_dir / f".session.json.{secrets.token_hex(4)}.tmp"
        try:
            tmp_path.write_text(metadata.model_dump_json(indent=2), encoding="utf-8")
            set_owner_only_permissions(tmp_path)
            with tmp_path.open("r+b") as fp:
                fp.flush()
                os.fsync(fp.fileno())
            replace_with_retry(tmp_path, session_json_path)
            set_owner_only_permissions(session_json_path)
        finally:
            if tmp_path.exists():
                with contextlib.suppress(OSError):
                    tmp_path.unlink()


# ==============================================================================
# CAS Mark-and-Sweep Garbage Collection & Source Removal (docs/19 §6.5)
# ==============================================================================


def mark_and_sweep_cas(session_dir: Path | str, dry_run: bool = False) -> set[str]:
    """Execute mark-and-sweep garbage collection over session inputs/cas/.

    Reachability Roots:
    1. Active (non-tombstoned) sources in inputs/manifest.json.
    2. Active citations in evidence/chunks.jsonl from non-tombstoned sources.
    3. EvidenceRef targets / source citations in pending proposals (proposals/*.json).
    4. Citations and source hashes in committed versions (versions/*/ontology.json).

    Any blob in inputs/cas/ not reachable from these roots is purged.

    Args:
        session_dir: Studio session directory path.
        dry_run: If True, identifies orphan blobs without deleting them.

    Returns:
        Set of orphan SHA-256 digests purged (or identified for purge).
    """
    sdir = Path(session_dir)
    cas = ContentAddressableStorage(root_dir=sdir / "inputs")
    all_blobs = set(cas.list_blobs())
    if not all_blobs:
        return set()

    reachable: set[str] = set()

    # 1. Active sources in inputs/manifest.json
    manifest_path = sdir / "inputs" / "manifest.json"
    tombstoned_source_ids: set[str] = set()

    if manifest_path.exists():
        try:
            m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            sources = m_data.get("sources", [])
            if isinstance(sources, dict):
                sources = list(sources.values())
            for src in sources:
                if isinstance(src, dict):
                    sid = src.get("source_id") or src.get("id")
                    is_tomb = bool(src.get("tombstoned") or src.get("deleted"))
                    if is_tomb:
                        if sid:
                            tombstoned_source_ids.add(str(sid))
                    else:
                        for key in ("sha256", "blob_sha256", "content_sha256", "source_sha256"):
                            val = src.get(key)
                            if val and isinstance(val, str) and len(val) == 64:
                                reachable.add(val.lower())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Error reading manifest.json for CAS GC: %s", exc)

    # 2. Active citations in evidence/chunks.jsonl
    chunks_path = sdir / "evidence" / "chunks.jsonl"
    if chunks_path.exists():
        try:
            for line in chunks_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    with contextlib.suppress(json.JSONDecodeError):
                        chunk = json.loads(line)
                        src_id = str(chunk.get("source_id") or "")
                        if src_id in tombstoned_source_ids or chunk.get("tombstoned"):
                            continue
                        for k in ("sha256", "source_sha256", "blob_sha256"):
                            val = chunk.get(k)
                            if val and isinstance(val, str) and len(val) == 64:
                                reachable.add(val.lower())
        except OSError as exc:
            logger.warning("Error reading chunks.jsonl for CAS GC: %s", exc)

    # 3. Pending proposals in proposals/*.json
    proposals_dir = sdir / "proposals"
    if proposals_dir.exists():
        for pfile in proposals_dir.glob("*.json"):
            try:
                pdata = json.loads(pfile.read_text(encoding="utf-8"))
                if pdata.get("status") in ("PENDING", "STAGED", "DRAFT"):
                    _extract_sha256_recursively(pdata, reachable)
            except (OSError, json.JSONDecodeError):
                continue

    # 4. Committed versions in versions/*/ontology.json
    versions_dir = sdir / "versions"
    if versions_dir.exists():
        for vdir in versions_dir.iterdir():
            if vdir.is_dir():
                for fpath in (vdir / "ontology.json", vdir / "delta.json", vdir / "meta.json"):
                    if fpath.exists():
                        try:
                            vdata = json.loads(fpath.read_text(encoding="utf-8"))
                            _extract_sha256_recursively(vdata, reachable)
                        except (OSError, json.JSONDecodeError):
                            continue

    orphans = all_blobs - reachable
    if not dry_run:
        for orphan_sha in orphans:
            cas.delete(orphan_sha)

    return orphans


def _extract_sha256_recursively(obj: Any, out_set: set[str]) -> None:
    """Recursively discover 64-character hex strings in nested structures."""
    if isinstance(obj, str):
        if len(obj) == 64 and re.fullmatch(r"[0-9a-fA-F]{64}", obj):
            out_set.add(obj.lower())
    elif isinstance(obj, dict):
        for v in obj.values():
            _extract_sha256_recursively(v, out_set)
    elif isinstance(obj, list):
        for item in obj:
            _extract_sha256_recursively(item, out_set)


def delete_source(
    session_dir: Path | str,
    source_id: str,
    timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> None:
    """Mark a source as tombstoned and purge its unreferenced blobs under L5 session lock.

    Raises:
        SourceReferencedByPendingProposalError: If cited by a pending proposal (exit 4).
        SourceReferencedByCommittedVersionError: If cited by a committed version (exit 4).
    """
    sdir = Path(session_dir)
    lock_path = sdir / ".session.lock"
    sid = sdir.name

    with studio_lock(lock_path, level=L5_SESSION, session_id=sid, timeout=timeout_sec):
        # 1. Check pending proposals
        proposals_dir = sdir / "proposals"
        if proposals_dir.exists():
            for pfile in proposals_dir.glob("*.json"):
                try:
                    pdata = json.loads(pfile.read_text(encoding="utf-8"))
                    if pdata.get("status") in ("PENDING", "STAGED", "DRAFT"):
                        found_sources: set[str] = set()
                        _extract_strings_matching(pdata, source_id, found_sources)
                        if found_sources:
                            raise SourceReferencedByPendingProposalError(
                                f"Cannot delete source '{source_id}': cited by pending proposal "
                                f"'{pfile.stem}'"
                            )
                except SourceReferencedByPendingProposalError:
                    raise
                except (OSError, json.JSONDecodeError):
                    continue

        # 2. Check committed versions
        versions_dir = sdir / "versions"
        if versions_dir.exists():
            for vdir in versions_dir.iterdir():
                if vdir.is_dir():
                    for fpath in (vdir / "ontology.json", vdir / "delta.json"):
                        if fpath.exists():
                            try:
                                vdata = json.loads(fpath.read_text(encoding="utf-8"))
                                found_sources = set()
                                _extract_strings_matching(vdata, source_id, found_sources)
                                if found_sources:
                                    raise SourceReferencedByCommittedVersionError(
                                        f"Cannot delete source '{source_id}': cited in committed "
                                        f"version '{vdir.name}'"
                                    )
                            except SourceReferencedByCommittedVersionError:
                                raise
                            except (OSError, json.JSONDecodeError):
                                continue

        # 3. Mark tombstoned in manifest.json
        manifest_path = sdir / "inputs" / "manifest.json"
        if manifest_path.exists():
            try:
                m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                sources = m_data.get("sources", [])
                if isinstance(sources, list):
                    for src in sources:
                        if src.get("source_id") == source_id or src.get("id") == source_id:
                            src["tombstoned"] = True
                elif isinstance(sources, dict) and source_id in sources:
                    sources[source_id]["tombstoned"] = True

                tmp_man = sdir / "inputs" / f".manifest.{secrets.token_hex(4)}.tmp"
                tmp_man.write_text(json.dumps(m_data, indent=2), encoding="utf-8")
                set_owner_only_permissions(tmp_man)
                replace_with_retry(tmp_man, manifest_path)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Failed to update manifest.json for tombstoning: %s", exc)

        # 4. Tombstone chunks in evidence/chunks.jsonl
        chunks_path = sdir / "evidence" / "chunks.jsonl"
        if chunks_path.exists():
            try:
                lines = chunks_path.read_text(encoding="utf-8").splitlines()
                updated_lines: list[str] = []
                for line in lines:
                    line = line.strip()
                    if line:
                        with contextlib.suppress(json.JSONDecodeError):
                            c = json.loads(line)
                            if str(c.get("source_id")) == source_id:
                                c["tombstoned"] = True
                            updated_lines.append(json.dumps(c))
                tmp_chunks = sdir / "evidence" / f".chunks.{secrets.token_hex(4)}.tmp"
                tmp_chunks.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
                set_owner_only_permissions(tmp_chunks)
                replace_with_retry(tmp_chunks, chunks_path)
            except OSError as exc:
                logger.warning("Failed to update chunks.jsonl for tombstoning: %s", exc)

        # 5. Run CAS mark-and-sweep GC
        mark_and_sweep_cas(sdir, dry_run=False)


def _extract_strings_matching(obj: Any, target: str, out_set: set[str]) -> None:
    """Find any occurrence of target string in nested structures."""
    if isinstance(obj, str):
        if obj == target or target in obj:
            out_set.add(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _extract_strings_matching(v, target, out_set)
    elif isinstance(obj, list):
        for item in obj:
            _extract_strings_matching(item, target, out_set)
