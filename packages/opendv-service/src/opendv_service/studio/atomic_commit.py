"""Multi-file atomic commit protocol, two-phase promotion, crash recovery, and CAS GC.

Implements Task 4D.3 / Issue #210 and docs/19 §6.2, §6.2.1, §6.2.2, §6.3, §6.5:
- 6-step atomic commit protocol under Total Lock Hierarchy L5 session lock:
    1. Acquire session lock L5 under Total Lock Hierarchy.
    2. OCC, RBAC & SemVer check: verify strong If-Match ETag syntax, verify
       session.head_sha256 == delta.base_sha256, enforce exact SemVer bump via
       compute_next_semver, enforce admin role on destructive MAJOR deltas, and
       run ONT-EVO-001/002 GovernanceValidator when enabled.
    3. Write-ahead log PENDING_COMMIT entry to audit.jsonl signed via K_audit.
    4. Stage canonical JCS version artifacts in .staging-<u>/, then atomic directory
       rename via replace_with_retry to versions/vX.Y.Z with parent directory fsync.
    5. Atomic metadata commit on session.json via .session.json.<u>.tmp (Commit Point).
    6. Mark accepted proposal ACCEPTED, mark stale PENDING proposals STALE, and append
       COMMITTED entry to audit.jsonl signed via K_audit.
- Two-Phase Workspace Promotion Protocol (L4 -> L5):
    PROMOTION_INTENT -> staged fsync + replace_with_retry -> PROMOTION_COMMITTED / ABORTED.
- Startup Crash Recovery & Reconciliation under L5 session lock.
- Fail-closed CAS Mark-and-Sweep Garbage Collection under L5 session lock.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import inspect
import json
import logging
import os
import re
import secrets
import shutil
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import ValidationError

from opendv_contracts.dtos.ontology import (
    ALLOWED_PATH_A_PREDICATES,
    CONCEPT_MUTATING_OP_TYPES,
    PATH_A_GROUNDING_THRESHOLD,
    RELATIONSHIP_ENDPOINT_MUTATING_OP_TYPES,
    OntologyDelta,
    RemoveAttributeOp,
    RemoveConceptOp,
    RemoveRelationshipOp,
    StewardWaiverRecord,
    is_destructive_delta,
)
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.genesis import GENESIS_VERSION, create_genesis_ontology
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import (
    canonicalize_graph_a_dict,
    compute_ontology_sha256,
    dumps_jcs_bytes,
)
from opendv_service.ontology.applier import apply as apply_delta
from opendv_service.ontology.applier import compute_next_semver
from opendv_service.ontology.matching.verifier import get_default_grounding_verifier
from opendv_service.ontology.validator import (
    ContractViolationError,
    GovernanceValidator,
    InvalidGroundingWaiverError,
    SemVerCalculusError,
    StructuralValidator,
    UngroundedConceptError,
)
from opendv_service.studio.cas_storage import ContentAddressableStorage
from opendv_service.studio.locks import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    StudioLock,
    multi_studio_lock,
    reentrant_session_lock,
)
from opendv_service.studio.session_manager import (
    SessionMetadata,
    StudioSessionManager,
    TamperEvidentChainCorruptError,
    atomic_repair_audit_file,
    normalize_version_tag,
    sanitize_domain_namespace,
)
from opendv_service.studio.workspace_security import (
    InvalidWorkspacePathError,
    RemoteServerPathForbiddenError,
    WorkspaceSecurityManager,
    replace_with_retry,
    set_owner_only_permissions,
    validate_workspace_containment,
)

logger = logging.getLogger(__name__)

_SEMVER_REGEX: Final[re.Pattern[str]] = re.compile(
    r"\Av?(\d+)\.(\d+)\.(\d+)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z"
)
# Accepts strong RFC 9110 quoted ETag '"<64-lowercase-hex>"' or raw '<64-lowercase-hex>'
# while strictly rejecting weak ETags ('W/"..."'), wildcards ('*'), lists, and malformed hex.
_STRONG_ETAG_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'\A(?:"([0-9a-f]{64})"|([0-9a-f]{64}))\Z'
)
_IMPORT_ANCHOR_ACTIONS: Final[frozenset[str]] = frozenset({"SESSION_IMPORTED", "IMPORT_ANCHOR"})
DEFAULT_STAGING_GRACE_SECONDS: Final[float] = 3600.0


class ConcurrentModificationError(OpenDVError, RuntimeError):
    """Raised when optimistic concurrency control precondition fails (HTTP 412 / exit 5)."""

    error_code = ErrorCode.ONT_EVO_CONCURRENT_MODIFICATION

    def __init__(
        self,
        message: str = "Concurrent modification detected",
        error_code: str | ErrorCode = ErrorCode.ONT_EVO_CONCURRENT_MODIFICATION,
        status_code: int = 412,
        exit_code: int = 5,
    ) -> None:
        RuntimeError.__init__(self, message)
        self.message = message
        self.error_code = (  # type: ignore[assignment]
            error_code
            if isinstance(error_code, ErrorCode)
            else ErrorCode(error_code)
            if error_code in ErrorCode._value2member_map_
            else error_code
        )
        self.status_code = status_code
        self.http_status = status_code
        self.exit_code = exit_code
        self.details = {}


class InvalidPreconditionHeaderError(OpenDVError, ValueError):
    """Raised when an If-Match header is weak, wildcard, list, or malformed (HTTP 400 / exit 2)."""

    error_code = ErrorCode.INVALID_PRECONDITION_HEADER

    def __init__(
        self,
        message: str = "Invalid If-Match precondition header; strong 64-hex SHA-256 ETag required.",
        error_code: str | ErrorCode = ErrorCode.INVALID_PRECONDITION_HEADER,
        status_code: int = 400,
        exit_code: int = 2,
    ) -> None:
        code_enum = (
            error_code
            if isinstance(error_code, ErrorCode)
            else ErrorCode.INVALID_PRECONDITION_HEADER
        )
        OpenDVError.__init__(self, message, error_code=code_enum)
        self.status_code = status_code
        self.http_status = status_code
        self.exit_code = exit_code


class CommitValidationError(OpenDVError, ValueError):
    """Raised when commit payload or target version fails validation."""

    error_code = ErrorCode.ONT_EVO_CONTRACT_VIOLATION

    def __init__(
        self,
        message: str,
        error_code: str | ErrorCode = ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
        status_code: int = 422,
        exit_code: int = 4,
    ) -> None:
        ValueError.__init__(self, message)
        code_enum = (
            error_code
            if isinstance(error_code, ErrorCode)
            else ErrorCode(error_code)
            if error_code in ErrorCode._value2member_map_
            else ErrorCode.ONT_EVO_CONTRACT_VIOLATION
        )
        OpenDVError.__init__(self, message, error_code=code_enum)
        self.error_code = code_enum
        self.status_code = status_code
        self.http_status = status_code
        self.exit_code = exit_code


class ForbiddenAdminRequiredError(OpenDVError, PermissionError):
    """Raised when a destructive or privileged operation lacks admin role (HTTP 403 / exit 7)."""

    error_code = ErrorCode.FORBIDDEN_ADMIN_REQUIRED

    def __init__(
        self,
        message: str = "Administrator role is required for this operation.",
        error_code: str | ErrorCode = ErrorCode.FORBIDDEN_ADMIN_REQUIRED,
        status_code: int = 403,
        exit_code: int = 7,
    ) -> None:
        code_enum = (
            error_code if isinstance(error_code, ErrorCode) else ErrorCode.FORBIDDEN_ADMIN_REQUIRED
        )
        OpenDVError.__init__(self, message, error_code=code_enum)
        self.status_code = status_code
        self.http_status = status_code
        self.exit_code = exit_code


class SourceNotFoundError(OpenDVError, FileNotFoundError):
    """Raised when attempting to delete or inspect a non-existent source (HTTP 404 / exit 3)."""

    error_code = ErrorCode.SOURCE_NOT_FOUND

    def __init__(
        self,
        message: str,
        error_code: str | ErrorCode = ErrorCode.SOURCE_NOT_FOUND,
        status_code: int = 404,
        exit_code: int = 3,
    ) -> None:
        FileNotFoundError.__init__(self, message)
        code_enum = error_code if isinstance(error_code, ErrorCode) else ErrorCode.SOURCE_NOT_FOUND
        self.message = message
        self.error_code = code_enum
        self.status_code = status_code
        self.http_status = status_code
        self.exit_code = exit_code
        self.details = {}


class SourceReferencedByPendingProposalError(OpenDVError, RuntimeError):
    """Raised when source is cited by a pending/stale proposal (HTTP 422 / exit 4)."""

    error_code = ErrorCode.SOURCE_REFERENCED_BY_PENDING_PROPOSAL

    def __init__(
        self,
        message: str,
        error_code: str | ErrorCode = ErrorCode.SOURCE_REFERENCED_BY_PENDING_PROPOSAL,
        status_code: int = 422,
        exit_code: int = 4,
    ) -> None:
        code_enum = (
            error_code
            if isinstance(error_code, ErrorCode)
            else ErrorCode.SOURCE_REFERENCED_BY_PENDING_PROPOSAL
        )
        OpenDVError.__init__(self, message, error_code=code_enum)
        self.status_code = status_code
        self.http_status = status_code
        self.exit_code = exit_code


class SourceReferencedByCommittedVersionError(OpenDVError, RuntimeError):
    """Raised when source is cited by a committed version (HTTP 422 / exit 4)."""

    error_code = ErrorCode.SOURCE_REFERENCED_BY_COMMITTED_VERSION

    def __init__(
        self,
        message: str,
        error_code: str | ErrorCode = ErrorCode.SOURCE_REFERENCED_BY_COMMITTED_VERSION,
        status_code: int = 422,
        exit_code: int = 4,
    ) -> None:
        code_enum = (
            error_code
            if isinstance(error_code, ErrorCode)
            else ErrorCode.SOURCE_REFERENCED_BY_COMMITTED_VERSION
        )
        OpenDVError.__init__(self, message, error_code=code_enum)
        self.status_code = status_code
        self.http_status = status_code
        self.exit_code = exit_code


class PromotionRationaleRequiredError(OpenDVError, ValueError):
    """Raised when promotion rationale is missing or < 10 characters (HTTP 400 / exit 1)."""

    error_code = ErrorCode.PROMOTION_RATIONALE_REQUIRED

    def __init__(
        self,
        message: str = "Promotion rationale must be at least 10 characters.",
    ) -> None:
        OpenDVError.__init__(self, message, error_code=ErrorCode.PROMOTION_RATIONALE_REQUIRED)
        self.status_code = self.http_status


class HistoricalContractWarningUnacknowledgedError(OpenDVError, RuntimeError):
    """Raised when promoting a lower version over a higher workspace contract without ack."""

    error_code = ErrorCode.HISTORICAL_CONTRACT_WARNING_UNACKNOWLEDGED

    def __init__(
        self,
        message: str = (
            "Promoting an older version requires historical contract warning acknowledgement."
        ),
    ) -> None:
        OpenDVError.__init__(
            self, message, error_code=ErrorCode.HISTORICAL_CONTRACT_WARNING_UNACKNOWLEDGED
        )
        self.status_code = self.http_status


class HistoricalContractRationaleRequiredError(OpenDVError, ValueError):
    """Raised when historical contract rationale is missing or < 10 chars (HTTP 400 / exit 1)."""

    error_code = ErrorCode.HISTORICAL_CONTRACT_RATIONALE_REQUIRED

    def __init__(
        self,
        message: str = "Historical contract rationale must be at least 10 characters.",
    ) -> None:
        OpenDVError.__init__(
            self, message, error_code=ErrorCode.HISTORICAL_CONTRACT_RATIONALE_REQUIRED
        )
        self.status_code = self.http_status


class LineageRootMismatchError(OpenDVError, RuntimeError):
    """Raised when workspace contract lineage diverges from session lineage (HTTP 412 / exit 5)."""

    error_code = ErrorCode.LINEAGE_ROOT_MISMATCH

    def __init__(self, message: str = "Workspace ontology lineage root mismatch.") -> None:
        OpenDVError.__init__(self, message, error_code=ErrorCode.LINEAGE_ROOT_MISMATCH)
        self.status_code = self.http_status


class NoOpPromotionError(OpenDVError, ValueError):
    """Raised when target workspace already holds the exact head digest (HTTP 400 / exit 1)."""

    error_code = ErrorCode.NO_OP_DELTA

    def __init__(
        self, message: str = "Target workspace ontology already matches session head SHA-256."
    ) -> None:
        OpenDVError.__init__(self, message, error_code=ErrorCode.NO_OP_DELTA)
        self.status_code = self.http_status


@dataclass(frozen=True)
class VersionCommitResult:
    """Result of a successfully committed ontology version."""

    version: str
    target_sha256: str
    parent_sha256: str
    audit_head_sha256: str
    committed_at: str
    proposal_id: str | None = None


@dataclass(frozen=True)
class PromotionResult:
    """Result of a two-phase atomic workspace ontology promotion."""

    session_id: str
    promoted_version: str
    head_sha256: str
    workspace_sha256: str
    pre_workspace_sha256: str
    target_contract_path: Path
    promoted_at: str
    audit_head_sha256: str


class _LocalWaiverVerifier:
    """Adapter binding WorkspaceSecurityManager K_waiver verification to GovernanceValidator."""

    def __init__(self, security_manager: WorkspaceSecurityManager) -> None:
        self._sec = security_manager

    def verify_waiver(
        self,
        waiver: StewardWaiverRecord,
        delta_sha256: str,
        base_sha256: str,
    ) -> bool:
        if waiver.proposal_delta_sha256 != delta_sha256:
            return False
        if base_sha256 and waiver.base_sha256 != base_sha256:
            return False
        if waiver.expires_at is not None and waiver.expires_at <= datetime.now(UTC):
            return False
        payload_bytes = waiver.signing_payload().encode("utf-8")
        return self._sec.verify_waiver_signature(payload_bytes, waiver.signature_or_token)


def parse_strong_etag(if_match: str) -> str:
    """Validate and extract the 64-char lowercase hex digest from an `If-Match` value.

    Accepts strong HTTP ETag `'"<64-hex>"'` or raw Python `<64-hex>`, while strictly
    rejecting weak ETags (`W/"..."`), wildcards (`*`), comma-separated lists, and
    malformed strings with `InvalidPreconditionHeaderError` (HTTP 400 / exit 2).
    """
    if not isinstance(if_match, str):
        raise InvalidPreconditionHeaderError("If-Match precondition must be a string.")
    trimmed = if_match.strip()
    match = _STRONG_ETAG_PATTERN.fullmatch(trimmed)
    if not match:
        raise InvalidPreconditionHeaderError(
            f"Invalid If-Match precondition '{if_match}': must be a strong 64-character "
            "lowercase hex SHA-256 ETag (weak W/ ETags, '*', and lists are forbidden)."
        )
    return match.group(1) or match.group(2)


def parse_semver_tuple(version_str: str) -> tuple[int, int, int]:
    """Parse a semantic version string into a (major, minor, patch) integer tuple."""
    match = _SEMVER_REGEX.match(version_str.strip())
    if not match:
        raise ValueError(f"Invalid semantic version string: '{version_str}'")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def fsync_directory_best_effort(dir_path: Path) -> None:
    """Flush directory metadata buffers to physical storage."""
    if not dir_path.exists():
        return

    if sys.platform == "win32":
        try:
            import ctypes

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


def collect_touched_concept_uris(
    base_ontology: GraphAOntology,
    delta: OntologyDelta,
) -> set[str]:
    """Collect all post-delta concept URIs touched by the 18+2 concept-mutating operations."""
    touched: set[str] = set()
    rel_list = list(
        getattr(base_ontology, "canonical_relationships", None)
        or getattr(base_ontology, "relationships", ())
    )
    base_rels_by_uri = {
        r.relationship_uri: r for r in rel_list if getattr(r, "relationship_uri", None)
    }
    base_rels_by_name: dict[str, list[Any]] = {}
    for r in rel_list:
        r_name = getattr(r, "relationship_name", None)
        if r_name:
            base_rels_by_name.setdefault(str(r_name), []).append(r)
        r_uri = getattr(r, "relationship_uri", "") or ""
        if ":" in r_uri:
            slug = r_uri.rsplit(":", 1)[-1]
            if slug:
                base_rels_by_name.setdefault(slug, []).append(r)

    for op in delta.operations:
        op_type = getattr(op, "op_type", "")
        if op_type == "update_concept_urn":
            new_u = getattr(op, "new_concept_uri", None)
            old_u = getattr(op, "old_concept_uri", None)
            if new_u:
                touched.add(str(new_u))
            if old_u:
                touched.add(str(old_u))
        elif op_type in CONCEPT_MUTATING_OP_TYPES:
            c_uri = getattr(op, "concept_uri", None) or getattr(op, "target_uri", None)
            if c_uri:
                touched.add(str(c_uri))
        elif op_type in RELATIONSHIP_ENDPOINT_MUTATING_OP_TYPES:
            rel_uri = getattr(op, "relationship_uri", None)
            rel_name = getattr(op, "relationship_name", None)
            matched_rels: list[Any] = []
            if rel_uri and rel_uri in base_rels_by_uri:
                matched_rels.append(base_rels_by_uri[rel_uri])
            elif rel_name and str(rel_name) in base_rels_by_name:
                matched_rels.extend(base_rels_by_name[str(rel_name)])
            for rel in matched_rels:
                for ep in getattr(rel, "endpoints", ()) or ():
                    if ep:
                        touched.add(str(ep))
                if getattr(rel, "source_concept_uri", None):
                    touched.add(str(rel.source_concept_uri))
                if getattr(rel, "target_concept_uri", None):
                    touched.add(str(rel.target_concept_uri))

    return touched


def verify_ont_evo_002_post_state_grounding(
    base_ontology: GraphAOntology,
    delta: OntologyDelta,
    target_ontology: GraphAOntology,
    *,
    waivers: list[StewardWaiverRecord] | None = None,
    security_manager: WorkspaceSecurityManager | None = None,
    grounding_verifier: Any | None = None,
    explicit_touched_uris: set[str] | None = None,
) -> None:
    """Enforce ONT-EVO-002 across all 18+2 concept-mutating ops at threshold 0.65.

    - Requires at least one mapping with predicate in `ALLOWED_PATH_A_PREDICATES`
      (`skos:exactMatch` or `skos:closeMatch`; `skos:broadMatch` is strictly excluded)
      and `confidence >= PATH_A_GROUNDING_THRESHOLD` (`0.65`), OR an authorized,
      cryptographically verified `StewardWaiverRecord` (Path B).
    - Grandfathered concepts lose their exemption immediately when touched by any of
      the 18+2 concept-mutating operations.
    """
    touched_uris = (
        set(explicit_touched_uris)
        if explicit_touched_uris is not None
        else collect_touched_concept_uris(base_ontology, delta)
    )
    if not touched_uris:
        return

    target_concepts = {c.concept_uri: c for c in target_ontology.canonical_business_concepts}
    waiver_list = waivers or []
    waiver_verifier = _LocalWaiverVerifier(security_manager) if security_manager else None
    delta_sha = delta.canonical_sha256()

    for concept_uri in sorted(touched_uris):
        if concept_uri not in target_concepts:
            continue
        concept = target_concepts[concept_uri]
        mappings = list(concept.external_mappings or [])

        path_a_ok = False
        if grounding_verifier is not None:
            filtered = [
                m
                for m in mappings
                if (getattr(m, "predicate", None) or "") in ALLOWED_PATH_A_PREDICATES
            ]
            if filtered:
                res = grounding_verifier.verify_grounding(
                    concept_uri,
                    candidates=filtered,
                    concept_name=concept.concept_name,
                    synonyms=tuple(concept.synonyms or ()),
                )
                if res.is_grounded and res.score >= PATH_A_GROUNDING_THRESHOLD:
                    path_a_ok = True
        else:
            for m in mappings:
                pred = getattr(m, "predicate", None) or ""
                conf = getattr(m, "confidence", None)
                if (
                    pred in ALLOWED_PATH_A_PREDICATES
                    and conf is not None
                    and float(conf) >= PATH_A_GROUNDING_THRESHOLD
                ):
                    path_a_ok = True
                    break

        if path_a_ok:
            continue

        # Path B: Administrative steward waiver (admin-only per master-plan.md)
        path_b_ok = False
        for w in waiver_list:
            if concept_uri in w.target_concept_uris:
                if not w.rationale or len(w.rationale.strip()) < 10:
                    raise InvalidGroundingWaiverError(
                        "INVALID_GROUNDING_WAIVER: Waiver rationale must be at least 10 characters."
                    )
                if w.authorized_role != "admin":
                    raise InvalidGroundingWaiverError(
                        "INVALID_GROUNDING_WAIVER: Waiver authorized_role must be 'admin'."
                    )
                if waiver_verifier is not None and not waiver_verifier.verify_waiver(
                    w, delta_sha256=delta_sha, base_sha256=delta.base_sha256
                ):
                    raise InvalidGroundingWaiverError(
                        "INVALID_GROUNDING_WAIVER: Cryptographic waiver signature check failed."
                    )
                path_b_ok = True
                break

        if not path_b_ok:
            raise UngroundedConceptError(
                f"ONT_EVO_UNGROUNDED_CONCEPT: Touched concept '{concept_uri}' lacks verified "
                f"Path A reference grounding (skos:exactMatch / skos:closeMatch >= "
                f"{PATH_A_GROUNDING_THRESHOLD:.4f}) and lacks an authorized administrative waiver."
            )


def _compute_required_target_semver(
    base_version: str,
    delta: OntologyDelta,
    base_ontology: GraphAOntology,
) -> str:
    """Compute the exact required next SemVer without caller target_version over-bump overrides."""
    probe = delta.model_copy(update={"target_ontology_version": None})
    probe.__pydantic_fields_set__.discard("target_ontology_version")
    probe.__pydantic_fields_set__.discard("target_version")
    return compute_next_semver(base_version, probe, base_ontology)


def _load_grandfathered_concept_uris(session_dir: Path) -> set[str]:
    """Load grandfathered seed concept URIs from initial version meta.json / metadata.json."""
    versions_dir = session_dir / "versions"
    if not versions_dir.is_dir():
        return set()
    out: set[str] = set()
    for vdir in sorted(versions_dir.iterdir()):
        if not vdir.is_dir():
            continue
        for mname in ("meta.json", "metadata.json"):
            mpath = vdir / mname
            if mpath.is_file():
                try:
                    mdata = json.loads(mpath.read_text(encoding="utf-8"))
                    if isinstance(mdata, dict) and mdata.get("mode") == "seed":
                        uris = mdata.get("grandfathered_concept_uris", [])
                        if isinstance(uris, list):
                            out.update(str(u) for u in uris if u)
                except (OSError, json.JSONDecodeError):
                    continue
    return out


class AtomicCommitService:
    """Service implementing 6-step atomic commit, two-phase promotion, recovery, and CAS GC."""

    def __init__(
        self,
        session_manager: StudioSessionManager,
        security_manager: WorkspaceSecurityManager | None = None,
        *,
        governance_validator: GovernanceValidator | None = None,
        enforce_governance: bool = True,
    ) -> None:
        self.session_manager = session_manager
        self.security_manager = security_manager or session_manager.security_manager
        self.governance_validator = governance_validator
        self._enforce_governance = bool(enforce_governance)

    @property
    def enforce_governance(self) -> bool:
        """Whether ONT-EVO-001/002 governance enforcement is active by default."""
        return self._enforce_governance

    @enforce_governance.setter
    def enforce_governance(self, value: bool) -> None:
        self._enforce_governance = bool(value)

    def _load_version_ontology(
        self,
        session_dir: Path,
        session: SessionMetadata,
        version_str: str,
    ) -> GraphAOntology:
        """Load ontology artifact for `version_str`, falling back to GenesisDraft on `0.0.0`."""
        clean_ver = version_str.lstrip("v")
        vdir = session_dir / "versions" / f"v{clean_ver}"
        ont_json = vdir / "ontology.json"
        ont_yaml = vdir / "ontology.yaml"

        if ont_json.is_file():
            return GraphAOntology.model_validate_json(ont_json.read_text(encoding="utf-8"))
        if ont_yaml.is_file():
            ont_dict = yaml.safe_load(ont_yaml.read_text(encoding="utf-8"))
            return GraphAOntology.model_validate(ont_dict)
        if clean_ver == GENESIS_VERSION:
            clean_ns = sanitize_domain_namespace(session.domain_namespace)
            return create_genesis_ontology(
                domain_namespace=clean_ns,
                enterprise_owner=session.enterprise_owner or "Enterprise Architecture",
            )
        raise FileNotFoundError(f"Base ontology artifact missing for version 'v{clean_ver}'")

    def _mark_proposals_post_commit(
        self,
        session_dir: Path,
        accepted_proposal_id: str | None,
        target_sha256: str,
        now_iso: str,
        evaluation: dict[str, Any] | None = None,
        *,
        accepted_by: str | None = None,
        waivers: Sequence[StewardWaiverRecord] | None = None,
    ) -> None:
        """Atomically mark accepted proposal ACCEPTED and stale other active proposals."""
        proposals_dir = session_dir / "proposals"
        if not proposals_dir.is_dir():
            return

        serialized_waivers: list[dict[str, Any]] | None = None
        if waivers:
            serialized_waivers = [w.model_dump(mode="json", exclude_none=True) for w in waivers]
        elif evaluation and isinstance(evaluation.get("waivers"), list):
            serialized_waivers = list(evaluation["waivers"])
        elif evaluation and isinstance(evaluation.get("waiver"), dict):
            serialized_waivers = [dict(evaluation["waiver"])]

        for pfile in sorted(proposals_dir.glob("*.json")):
            try:
                pdata = json.loads(pfile.read_text(encoding="utf-8"))
                if not isinstance(pdata, dict):
                    continue
                pid = str(pdata.get("proposal_id") or pfile.stem)
                changed = False

                if accepted_proposal_id and (
                    pid == accepted_proposal_id or pfile.stem == accepted_proposal_id
                ):
                    pdata["status"] = "ACCEPTED"
                    pdata["updated_at"] = now_iso
                    pdata["resolved_at"] = now_iso
                    if accepted_by:
                        pdata["accepted_by"] = accepted_by
                    if evaluation and "waiver" in evaluation:
                        pdata["waiver"] = evaluation["waiver"]
                    if serialized_waivers is not None:
                        pdata["waivers"] = serialized_waivers
                    changed = True
                elif pdata.get("status") in (None, "PENDING", "PROPOSED"):
                    p_base = (
                        pdata.get("base_sha256")
                        or pdata.get("base_ontology_sha256")
                        or (
                            pdata.get("delta", {}).get("parent_sha256")
                            if isinstance(pdata.get("delta"), dict)
                            else None
                        )
                    )
                    if p_base != target_sha256:
                        pdata["status"] = "STALE"
                        pdata["updated_at"] = now_iso
                        changed = True

                if changed:
                    tmp_prop = proposals_dir / f".{pfile.stem}.{secrets.token_hex(4)}.tmp"
                    payload = json.dumps(pdata, indent=2).encode("utf-8")
                    fd = os.open(
                        str(tmp_prop),
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                        0o600,
                    )
                    try:
                        os.write(fd, payload)
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    set_owner_only_permissions(tmp_prop)
                    replace_with_retry(tmp_prop, pfile)
                    set_owner_only_permissions(pfile)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Failed to update proposal file %s: %s", pfile.name, exc)

    def commit_delta(
        self,
        session_id: str,
        delta: OntologyDelta,
        proposal_id: str | None = None,
        author: str = "steward",
        if_match: str | None = None,
        evaluation: dict[str, Any] | None = None,
        timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
        *,
        actor_role: str = "editor",
        run_governance: bool | None = None,
        enforce_governance: bool | None = None,
        governance_validator: GovernanceValidator | None = None,
        waivers: list[StewardWaiverRecord] | None = None,
        grandfathered_concept_uris: set[str] | frozenset[str] | None = None,
    ) -> VersionCommitResult:
        """Execute the 6-step atomic commit protocol under Total Lock Hierarchy L5 session lock."""
        session_dir = self.session_manager.get_session_dir(session_id)
        if not session_dir.exists() or not (session_dir / "session.json").exists():
            raise FileNotFoundError(f"Session '{session_id}' not found")

        # Validate strong If-Match syntax upfront (HTTP 400 / exit 2 on weak/wildcard/malformed)
        clean_if_match: str | None = None
        if if_match is not None:
            clean_if_match = parse_strong_etag(if_match)

        lock_path = session_dir / ".session.lock"
        with reentrant_session_lock(lock_path, session_id=session_id, timeout=timeout_sec):
            self.reconcile_and_recover_session(session_id)
            session = self.session_manager.get_session(session_id)

            # Step 2a: Strict OCC Precondition Verification (no unanchored 0.0.0 bypass)
            delta_base_sha = (delta.base_sha256 or delta.parent_sha256 or "").strip().lower()
            clean_delta_base_ver = (delta.base_version or "").strip().lstrip("v")
            clean_active_ver = session.active_version.strip().lstrip("v")

            if clean_if_match is not None and clean_if_match != session.head_sha256:
                raise ConcurrentModificationError(
                    f"If-Match precondition failed for session '{session_id}': "
                    f"header specifies '{clean_if_match}', but session head is "
                    f"'{session.head_sha256}'"
                )

            if clean_delta_base_ver != clean_active_ver:
                raise ConcurrentModificationError(
                    f"OCC check failed for session '{session_id}': active version is "
                    f"'{session.active_version}', but delta specifies base_version "
                    f"'{delta.base_version}'"
                )

            if clean_active_ver != GENESIS_VERSION:
                if not delta_base_sha or delta_base_sha != session.head_sha256:
                    raise ConcurrentModificationError(
                        f"OCC check failed for session '{session_id}': active head is "
                        f"'{session.head_sha256}', but delta specifies base '{delta_base_sha}'"
                    )
            elif delta_base_sha:
                if delta_base_sha != session.head_sha256:
                    raise ConcurrentModificationError(
                        f"OCC check failed for session '{session_id}': active head is "
                        f"'{session.head_sha256}', but delta specifies base '{delta_base_sha}'"
                    )
            elif clean_if_match is None:
                raise ConcurrentModificationError(
                    f"OCC check failed for session '{session_id}': delta base_sha256 is empty "
                    f"without an anchoring If-Match matching head '{session.head_sha256}'"
                )

            # Proposal resolution concurrency check
            if proposal_id:
                prop_file = session_dir / "proposals" / f"{proposal_id}.json"
                if prop_file.exists():
                    try:
                        curr_prop = json.loads(prop_file.read_text(encoding="utf-8"))
                        if curr_prop.get("status") in ("ACCEPTED", "REJECTED"):
                            raise ConcurrentModificationError(
                                f"Proposal '{proposal_id}' has already been resolved "
                                f"({curr_prop.get('status')})"
                            )
                    except ConcurrentModificationError:
                        raise
                    except (OSError, json.JSONDecodeError):
                        pass

            if delta.target_ontology_version is not None:
                try:
                    req_tuple = parse_semver_tuple(delta.target_ontology_version)
                except ValueError as err:
                    raise CommitValidationError(
                        f"Invalid target semantic version '{delta.target_ontology_version}': {err}",
                        error_code=ErrorCode.ONT_EVO_ENVELOPE_VIOLATION,
                        status_code=422,
                        exit_code=4,
                    ) from err
                if req_tuple <= parse_semver_tuple(session.head_version):
                    raise CommitValidationError(
                        f"Target version '{delta.target_ontology_version}' must exceed active "
                        f"version '{session.head_version}' in SemVer total order",
                        error_code=ErrorCode.ONT_EVO_ENVELOPE_VIOLATION,
                        status_code=422,
                        exit_code=4,
                    )

            if not delta.operations:
                raise CommitValidationError(
                    "Cannot commit an empty delta with zero operations.",
                    error_code=ErrorCode.NO_OP_DELTA,
                    status_code=400,
                    exit_code=1,
                )

            # Load authoritative base ontology (zero double-apply stub hacks!)
            base_ontology = self._load_version_ontology(session_dir, session, session.head_version)
            if delta.domain_namespace and base_ontology.ontology_version == GENESIS_VERSION:
                clean_delta_ns = sanitize_domain_namespace(delta.domain_namespace)
                if clean_delta_ns != base_ontology.domain_namespace:
                    base_ontology = base_ontology.model_copy(
                        update={"domain_namespace": clean_delta_ns}
                    )

            # Step 2b: RBAC check — destructive MAJOR deltas require admin role (P10::MAJ-02)
            if is_destructive_delta(delta, base_ontology) and actor_role != "admin":
                raise ForbiddenAdminRequiredError(
                    "Destructive (MAJOR SemVer) ontology revisions require administrator role."
                )

            # Step 2c: Exact SemVer calculus verification (P10::MAJ-03)
            if delta.base_ontology_version and delta.base_ontology_version.lstrip(
                "v"
            ) != session.head_version.lstrip("v"):
                raise CommitValidationError(
                    f"Delta base_version '{delta.base_ontology_version}' does not match "
                    f"session head version '{session.head_version}'.",
                    error_code=ErrorCode.ONT_EVO_ENVELOPE_VIOLATION,
                    status_code=422,
                    exit_code=4,
                )

            try:
                expected_semver = _compute_required_target_semver(
                    session.head_version, delta, base_ontology
                )
            except (SemVerCalculusError, ValueError) as err:
                raise CommitValidationError(
                    f"Invalid ontology SemVer transition: {err}",
                    error_code=ErrorCode.ONT_EVO_ENVELOPE_VIOLATION,
                    status_code=422,
                    exit_code=4,
                ) from err

            if delta.target_ontology_version is not None and delta.target_ontology_version.lstrip(
                "v"
            ) != expected_semver.lstrip("v"):
                raise CommitValidationError(
                    f"Target version '{delta.target_ontology_version}' violates required "
                    f"ONT-EVO-001 SemVer bump '{expected_semver}' from '{session.head_version}'.",
                    error_code=ErrorCode.ONT_EVO_ENVELOPE_VIOLATION,
                    status_code=422,
                    exit_code=4,
                )

            # Apply delta exactly once via structural applier
            try:
                normalized_delta = (
                    delta
                    if delta.parent_sha256 == session.head_sha256
                    else delta.model_copy(
                        update={
                            "parent_sha256": session.head_sha256,
                            "target_ontology_version": expected_semver,
                        }
                    )
                )
                target_ontology = apply_delta(
                    base_ontology, normalized_delta, validate_structural=True
                )
                target_ontology.parent_sha256 = session.head_sha256
            except (SemVerCalculusError, ValidationError, ValueError, OpenDVError) as err:
                if isinstance(err, OpenDVError) and not isinstance(err, SemVerCalculusError):
                    raise
                raise CommitValidationError(f"Invalid ontology delta transition: {err}") from err

            # Step 2d: GovernanceValidator & ONT-EVO-002 18+2 op grounding enforcement
            should_run_gov = bool(
                governance_validator is not None
                or run_governance is True
                or enforce_governance is True
                or (
                    run_governance is None
                    and enforce_governance is None
                    and self.enforce_governance
                )
            )
            if should_run_gov:
                effective_grandfathered = (
                    set(grandfathered_concept_uris)
                    if grandfathered_concept_uris is not None
                    else _load_grandfathered_concept_uris(session_dir)
                )
                gv = governance_validator or self.governance_validator
                if gv is None:
                    gv = GovernanceValidator(
                        waiver_verifier=_LocalWaiverVerifier(self.security_manager)
                    )
                elif gv.waiver_verifier is None:
                    gv = GovernanceValidator(
                        deployment_provider=getattr(gv, "deployment_provider", None),
                        grounding_verifier=gv.grounding_verifier,
                        waiver_verifier=_LocalWaiverVerifier(self.security_manager),
                    )

                occ_kwargs: dict[str, Any] = {
                    "session_head_sha256": session.head_sha256,
                    "if_match": if_match,
                }
                occ_sig = inspect.signature(gv.validate_occ)
                if "actual_base_version" in occ_sig.parameters:
                    occ_kwargs["actual_base_version"] = session.active_version
                try:
                    gv.validate_occ(normalized_delta, **occ_kwargs)
                except OpenDVError as occ_err:
                    if occ_err.error_code == ErrorCode.ONT_EVO_CONCURRENT_MODIFICATION:
                        raise ConcurrentModificationError(str(occ_err)) from occ_err
                    raise

                gv.validate_deployed_contracts(base_ontology, normalized_delta)
                gv.validate_grounding(
                    base_ontology,
                    normalized_delta,
                    target_ontology,
                    waivers=waivers,
                    grandfathered_concept_uris=effective_grandfathered,
                )
                verify_ont_evo_002_post_state_grounding(
                    base_ontology,
                    normalized_delta,
                    target_ontology,
                    waivers=waivers,
                    security_manager=self.security_manager,
                    grounding_verifier=gv.grounding_verifier or get_default_grounding_verifier(),
                )

            target_version_raw = target_ontology.ontology_version.lstrip("v")
            target_version_tag = normalize_version_tag(target_version_raw)

            if parse_semver_tuple(target_version_raw) <= parse_semver_tuple(session.head_version):
                raise CommitValidationError(
                    f"Target version '{target_version_raw}' must exceed active version "
                    f"'{session.head_version}' in SemVer total order"
                )

            # Canonical RFC 8785 JCS bytes: sha256(ontology.json) == target_sha256 bit-for-bit!
            canonical_dict = canonicalize_graph_a_dict(target_ontology)
            canonical_ont_bytes = dumps_jcs_bytes(canonical_dict)
            target_sha256 = compute_ontology_sha256(target_ontology)

            # Step 3: Write-ahead log PENDING_COMMIT entry signed via K_audit
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
                ont_json_path = staging_dir / "ontology.json"
                ont_json_path.write_bytes(canonical_ont_bytes)
                set_owner_only_permissions(ont_json_path)

                ont_yaml_path = staging_dir / "ontology.yaml"
                yaml_str = yaml.safe_dump(canonical_dict, sort_keys=False)
                ont_yaml_path.write_text(yaml_str, encoding="utf-8")
                set_owner_only_permissions(ont_yaml_path)

                delta_json_path = staging_dir / "delta.json"
                delta_json_path.write_text(
                    normalized_delta.model_dump_json(indent=2), encoding="utf-8"
                )
                set_owner_only_permissions(delta_json_path)

                meta_record: dict[str, Any] = {
                    "version": target_version_tag,
                    "target_version": target_version_tag,
                    "target_sha256": target_sha256,
                    "parent_sha256": session.head_sha256,
                    "proposal_id": proposal_id,
                    "created_at": now_iso,
                    "author": author,
                    "audit_chain_head_sha256": session.audit_chain_head_sha256,
                    "domain_namespace": target_ontology.domain_namespace,
                    "enterprise_owner": target_ontology.enterprise_owner,
                }
                if waivers:
                    meta_record["waivers"] = [
                        w.model_dump(mode="json", exclude_none=True) for w in waivers
                    ]
                meta_text = json.dumps(meta_record, indent=2)
                meta_json_path = staging_dir / "meta.json"
                meta_json_path.write_text(meta_text, encoding="utf-8")
                set_owner_only_permissions(meta_json_path)

                metadata_compat_path = staging_dir / "metadata.json"
                metadata_compat_path.write_text(meta_text, encoding="utf-8")
                set_owner_only_permissions(metadata_compat_path)

                eval_payload = dict(evaluation or {})
                if waivers and "waivers" not in eval_payload:
                    eval_payload["waivers"] = [
                        w.model_dump(mode="json", exclude_none=True) for w in waivers
                    ]
                eval_json_path = staging_dir / "evaluation.json"
                eval_json_path.write_text(json.dumps(eval_payload, indent=2), encoding="utf-8")
                set_owner_only_permissions(eval_json_path)

                staged_files: list[Path] = [
                    ont_json_path,
                    ont_yaml_path,
                    delta_json_path,
                    meta_json_path,
                    metadata_compat_path,
                    eval_json_path,
                ]
                if waivers:
                    waivers_json_path = staging_dir / "waivers.json"
                    waivers_json_path.write_text(
                        json.dumps(
                            [w.model_dump(mode="json", exclude_none=True) for w in waivers],
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    set_owner_only_permissions(waivers_json_path)
                    staged_files.append(waivers_json_path)

                for fpath in staged_files:
                    with fpath.open("r+b") as fp:
                        fp.flush()
                        os.fsync(fp.fileno())

                versions_parent = session_dir / "versions"
                versions_parent.mkdir(parents=True, exist_ok=True)
                target_version_dir = versions_parent / target_version_tag

                replace_with_retry(staging_dir, target_version_dir)
                fsync_directory_best_effort(versions_parent)
            finally:
                if staging_dir.exists():
                    shutil.rmtree(staging_dir, ignore_errors=True)

            # Step 5: Atomic metadata commit on session.json (The Commit Point)
            committed_list = [normalize_version_tag(v) for v in session.committed_versions]
            if target_version_tag not in committed_list:
                committed_list.append(target_version_tag)

            updated_metadata = session.model_copy(
                update={
                    "updated_at": now_iso,
                    "head_version": target_version_raw,
                    "head_sha256": target_sha256,
                    "domain_namespace": target_ontology.domain_namespace,
                    "committed_versions": committed_list,
                }
            )
            self._write_session_json(session_dir, updated_metadata)

            # Step 6: Post-commit proposal ACCEPTED/STALE transition + signed COMMITTED audit record
            self._mark_proposals_post_commit(
                session_dir=session_dir,
                accepted_proposal_id=proposal_id,
                target_sha256=target_sha256,
                now_iso=now_iso,
                evaluation=evaluation,
                accepted_by=author,
                waivers=waivers,
            )

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

            updated_metadata.audit_chain_head_sha256 = final_audit_head
            self._write_session_json(session_dir, updated_metadata)

            return VersionCommitResult(
                version=target_version_tag,
                target_sha256=target_sha256,
                parent_sha256=session.head_sha256,
                audit_head_sha256=final_audit_head,
                committed_at=now_iso,
                proposal_id=proposal_id,
            )

    def _collect_locally_waived_session_concepts(
        self,
        session_dir: Path,
        session: SessionMetadata,
    ) -> set[str]:
        """Collect concept URIs covered by locally-signed admin waivers in session history."""
        versions_dir = session_dir / "versions"
        if not versions_dir.is_dir():
            return set()

        waiver_verifier = _LocalWaiverVerifier(self.security_manager)
        version_entries: list[tuple[tuple[int, int, int], Path]] = []
        for vdir in versions_dir.iterdir():
            if not vdir.is_dir():
                continue
            v_clean = vdir.name.lstrip("v")
            try:
                v_tuple = parse_semver_tuple(v_clean)
            except ValueError:
                continue
            version_entries.append((v_tuple, vdir))

        version_entries.sort(key=lambda item: item[0])
        waived_uris: set[str] = set()
        for _v_tuple, vdir in version_entries:
            delta_path = vdir / "delta.json"
            if not delta_path.is_file():
                continue
            try:
                v_delta = OntologyDelta.model_validate_json(delta_path.read_text(encoding="utf-8"))
            except (OSError, ValidationError, ValueError):
                continue

            try:
                v_parent = self._load_version_ontology(
                    session_dir, session, v_delta.base_version or GENESIS_VERSION
                )
            except FileNotFoundError:
                v_parent = create_genesis_ontology()

            waived_uris -= collect_touched_concept_uris(v_parent, v_delta)

            raw_waiver_items: list[dict[str, Any]] = []
            wfile = vdir / "waivers.json"
            if wfile.is_file():
                with contextlib.suppress(OSError, json.JSONDecodeError):
                    wloaded = json.loads(wfile.read_text(encoding="utf-8"))
                    if isinstance(wloaded, list):
                        raw_waiver_items.extend(x for x in wloaded if isinstance(x, dict))
                    elif isinstance(wloaded, dict) and isinstance(wloaded.get("waivers"), list):
                        raw_waiver_items.extend(
                            x for x in wloaded["waivers"] if isinstance(x, dict)
                        )
            for fname in ("meta.json", "metadata.json", "evaluation.json"):
                fpath = vdir / fname
                if fpath.is_file():
                    with contextlib.suppress(OSError, json.JSONDecodeError):
                        floaded = json.loads(fpath.read_text(encoding="utf-8"))
                        if isinstance(floaded, dict):
                            if isinstance(floaded.get("waivers"), list):
                                raw_waiver_items.extend(
                                    x for x in floaded["waivers"] if isinstance(x, dict)
                                )
                            if isinstance(floaded.get("waiver"), dict):
                                raw_waiver_items.append(floaded["waiver"])

            v_delta_sha = v_delta.canonical_sha256()
            for raw_w in raw_waiver_items:
                with contextlib.suppress(ValidationError, ValueError):
                    w_rec = StewardWaiverRecord.model_validate(raw_w)
                    if (
                        w_rec.authorized_role == "admin"
                        and w_rec.rationale
                        and len(w_rec.rationale.strip()) >= 10
                        and waiver_verifier.verify_waiver(
                            w_rec,
                            delta_sha256=v_delta_sha,
                            base_sha256=v_delta.base_sha256,
                        )
                    ):
                        waived_uris.update(w_rec.target_concept_uris)

        return waived_uris

    def _verify_promotion_governance(
        self,
        session_dir: Path,
        session: SessionMetadata,
        existing_ws_ont: GraphAOntology | None,
        head_ont: GraphAOntology,
        *,
        acknowledge_historical_contract_warning: bool = False,
    ) -> None:
        """Enforce ONT-EVO-001 + ONT-EVO-002 on `head_ont` prior to workspace promotion."""
        StructuralValidator().validate(head_ont)

        ws_grandfathered: set[str] = set()
        if existing_ws_ont is not None:
            ws_concepts = {c.concept_uri: c for c in existing_ws_ont.canonical_business_concepts}
            head_concepts = {c.concept_uri: c for c in head_ont.canonical_business_concepts}
            ws_rels = {r.relationship_uri: r for r in existing_ws_ont.canonical_relationships}
            head_rels = {r.relationship_uri: r for r in head_ont.canonical_relationships}

            rel_touched_uris: set[str] = set()
            for r_uri, ws_r in ws_rels.items():
                head_r = head_rels.get(r_uri)
                if head_r is None or (
                    getattr(head_r, "deprecated", False) and not getattr(ws_r, "deprecated", False)
                ):
                    for ep in getattr(ws_r, "endpoints", ()) or ():
                        if ep:
                            rel_touched_uris.add(str(ep))
                    if getattr(ws_r, "source_concept_uri", None):
                        rel_touched_uris.add(str(ws_r.source_concept_uri))
                    if getattr(ws_r, "target_concept_uri", None):
                        rel_touched_uris.add(str(ws_r.target_concept_uri))

            for u, ws_c in ws_concepts.items():
                head_c = head_concepts.get(u)
                if (
                    head_c is not None
                    and u not in rel_touched_uris
                    and ws_c.model_dump(mode="json") == head_c.model_dump(mode="json")
                ):
                    ws_grandfathered.add(u)

            # ONT-EVO-001: Check destructive removals against deployed contracts & SemVer major bump
            diff_ops: list[Any] = []
            for removed_u in sorted(set(ws_concepts) - set(head_concepts)):
                diff_ops.append(
                    RemoveConceptOp.model_construct(op_type="remove_concept", concept_uri=removed_u)
                )
            for removed_r in sorted(set(ws_rels) - set(head_rels)):
                diff_ops.append(
                    RemoveRelationshipOp.model_construct(
                        op_type="remove_relationship",
                        relationship_uri=removed_r,
                        relationship_name=getattr(ws_rels[removed_r], "relationship_name", "")
                        or "",
                    )
                )
            for u in sorted(set(ws_concepts) & set(head_concepts)):
                ws_attrs = {a.attribute_name for a in (ws_concepts[u].attributes or [])}
                head_attrs = {a.attribute_name for a in (head_concepts[u].attributes or [])}
                for removed_attr in sorted(ws_attrs - head_attrs):
                    diff_ops.append(
                        RemoveAttributeOp.model_construct(
                            op_type="remove_attribute",
                            concept_uri=u,
                            attribute_name=removed_attr,
                        )
                    )

            if diff_ops:
                if (
                    self.governance_validator is not None
                    and getattr(self.governance_validator, "deployment_provider", None) is not None
                ):
                    synth_delta = OntologyDelta.model_construct(
                        delta_id="promo-ont-evo-001-check",
                        base_ontology_version=existing_ws_ont.ontology_version,
                        target_ontology_version=head_ont.ontology_version,
                        operations=diff_ops,
                    )
                    self.governance_validator.validate_deployed_contracts(
                        existing_ws_ont, synth_delta
                    )

                if not acknowledge_historical_contract_warning:
                    ws_major = parse_semver_tuple(existing_ws_ont.ontology_version)[0]
                    head_major = parse_semver_tuple(head_ont.ontology_version)[0]
                    if head_major <= ws_major:
                        raise ContractViolationError(
                            f"ONT_EVO_CONTRACT_VIOLATION: Promoting '{head_ont.ontology_version}' "
                            f"over '{existing_ws_ont.ontology_version}' removes existing contract "
                            "elements without a MAJOR SemVer bump."
                        )

        waived_in_session = self._collect_locally_waived_session_concepts(session_dir, session)
        concepts_needing_path_a = {
            c.concept_uri
            for c in head_ont.canonical_business_concepts
            if c.concept_uri not in ws_grandfathered and c.concept_uri not in waived_in_session
        }
        if concepts_needing_path_a:
            verifier = (
                self.governance_validator.grounding_verifier
                if self.governance_validator is not None
                and self.governance_validator.grounding_verifier is not None
                else get_default_grounding_verifier()
            )
            probe_delta = OntologyDelta(
                delta_id="promo-ont-evo-002-check",
                base_ontology_version="0.0.0",
                target_ontology_version="1.0.0",
                operations=[],
            )
            verify_ont_evo_002_post_state_grounding(
                create_genesis_ontology(),
                probe_delta,
                head_ont,
                security_manager=self.security_manager,
                grounding_verifier=verifier,
                explicit_touched_uris=concepts_needing_path_a,
            )

    def promote_to_workspace(
        self,
        session_id: str,
        target_workspace: Path | str,
        expected_workspace_sha256: str,
        *,
        expected_head_sha256: str | None = None,
        if_match: str | None = None,
        rationale: str = "",
        actor: str = "admin",
        actor_role: str = "editor",
        acknowledge_historical_contract_warning: bool = False,
        historical_contract_rationale: str | None = None,
        allowed_workspace_roots: Sequence[Path | str] | None = None,
        is_remote_request: bool = False,
        enforce_lineage: bool = True,
        allow_noop: bool = False,
        timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> PromotionResult:
        """Execute two-phase atomic workspace ontology promotion under L4 -> L5 locks."""
        if actor_role != "admin":
            raise ForbiddenAdminRequiredError(
                "Promoting session ontology to workspace requires administrator role."
            )

        if not rationale or len(rationale.strip()) < 10:
            raise PromotionRationaleRequiredError(
                "Promotion rationale must be at least 10 non-whitespace characters."
            )

        clean_if_match: str | None = None
        if if_match is not None:
            clean_if_match = parse_strong_etag(if_match)

        raw_target_ws = Path(target_workspace)
        if raw_target_ws.is_symlink():
            raise InvalidWorkspacePathError(
                f"Workspace promotion target '{raw_target_ws.name}' must not be a symlink."
            )

        resolved_ws = validate_workspace_containment(
            target_workspace,
            allowed_roots=allowed_workspace_roots,
            is_remote_request=is_remote_request,
        )

        if resolved_ws.suffix.lower() in (".yaml", ".yml", ".json"):
            target_contract_path = resolved_ws
            lock_dir = resolved_ws.parent
        else:
            lock_dir = resolved_ws
            target_contract_path = lock_dir / "graph_a_ontology.yaml"
            for candidate_name in (
                "graph_a_ontology.yaml",
                "graph_a.yaml",
                "ontology.yaml",
                "ontology.json",
            ):
                cand = lock_dir / candidate_name
                if cand.exists() or cand.is_symlink():
                    target_contract_path = cand
                    break

        if target_contract_path.is_symlink() or lock_dir.is_symlink():
            raise InvalidWorkspacePathError(
                f"Workspace contract path '{target_contract_path.name}' must not be a symlink."
            )

        session_dir = self.session_manager.get_session_dir(session_id)
        if not session_dir.exists() or not (session_dir / "session.json").exists():
            raise FileNotFoundError(f"Session '{session_id}' not found")

        lock_dir.mkdir(parents=True, exist_ok=True)
        ws_lock = StudioLock.workspace(
            lock_dir,
            resource_id=os.path.normcase(str(lock_dir)),
            timeout=timeout_sec,
        )
        sess_lock = StudioLock.session(
            session_dir,
            session_id=session_id,
            timeout=timeout_sec,
        )

        with multi_studio_lock([ws_lock, sess_lock], timeout=timeout_sec):
            # Re-verify containment and symlink absence inside [ws_lock, sess_lock] (TOCTOU guard)
            if (
                raw_target_ws.is_symlink()
                or lock_dir.is_symlink()
                or target_contract_path.is_symlink()
            ):
                raise InvalidWorkspacePathError(
                    f"Workspace contract path '{target_contract_path.name}' must not be a symlink."
                )
            validate_workspace_containment(
                target_contract_path,
                allowed_roots=allowed_workspace_roots,
                is_remote_request=is_remote_request,
            )

            self.reconcile_and_recover_session(session_id)
            session = self.session_manager.get_session(session_id)

            if clean_if_match is not None and clean_if_match != session.head_sha256:
                raise ConcurrentModificationError(
                    f"If-Match precondition '{clean_if_match}' does not match session head "
                    f"'{session.head_sha256}'."
                )

            if expected_head_sha256 is not None:
                clean_exp_head = expected_head_sha256.strip().strip('"').lower()
                if clean_exp_head != session.head_sha256:
                    raise ConcurrentModificationError(
                        f"expected_head_sha256 '{clean_exp_head}' does not match session head "
                        f"'{session.head_sha256}'."
                    )

            if session.head_version == GENESIS_VERSION:
                raise CommitValidationError(
                    "Cannot promote greenfield GenesisDraft (v0.0.0) without committed concepts.",
                    error_code=ErrorCode.ONTOLOGY_REQUIRES_CONCEPT,
                    status_code=422,
                    exit_code=4,
                )

            head_ont = self._load_version_ontology(session_dir, session, session.head_version)
            if not head_ont.canonical_business_concepts:
                raise CommitValidationError(
                    "Cannot promote ontology with zero canonical business concepts.",
                    error_code=ErrorCode.ONTOLOGY_REQUIRES_CONCEPT,
                    status_code=422,
                    exit_code=4,
                )

            computed_head_sha = compute_ontology_sha256(head_ont)
            if computed_head_sha != session.head_sha256:
                raise CommitValidationError(
                    "Head ontology digest mismatch against session metadata.",
                    error_code=ErrorCode.CORRUPT_SESSION_STATE,
                    status_code=500,
                    exit_code=1,
                )

            # Inspect existing workspace contract for OCC, downgrade warning & lineage
            exp_ws = (expected_workspace_sha256 or "").strip().strip('"').lower()
            pre_ws_canonical_sha = "0" * 64
            existing_ws_ont: GraphAOntology | None = None

            if target_contract_path.is_file():
                raw_ws_bytes = target_contract_path.read_bytes()
                raw_ws_sha = hashlib.sha256(raw_ws_bytes).hexdigest().lower()
                try:
                    if target_contract_path.suffix.lower() == ".json":
                        existing_ws_ont = GraphAOntology.model_validate_json(
                            raw_ws_bytes.decode("utf-8")
                        )
                    else:
                        existing_ws_ont = GraphAOntology.model_validate(
                            yaml.safe_load(raw_ws_bytes.decode("utf-8"))
                        )
                    pre_ws_canonical_sha = compute_ontology_sha256(existing_ws_ont)
                except (ValidationError, ValueError, TypeError, yaml.YAMLError) as exc:
                    raise CommitValidationError(
                        f"Existing workspace ontology contract is invalid: {exc}",
                        error_code=ErrorCode.INVALID_WORKSPACE_ONTOLOGY,
                        status_code=422,
                        exit_code=4,
                    ) from exc

                if exp_ws not in (pre_ws_canonical_sha, raw_ws_sha):
                    raise ConcurrentModificationError(
                        f"Workspace OCC conflict: expected '{exp_ws}', but workspace contract "
                        f"canonical SHA-256 is '{pre_ws_canonical_sha}'."
                    )

                if not allow_noop and pre_ws_canonical_sha == session.head_sha256:
                    raise NoOpPromotionError(
                        "Workspace contract already matches session head ontology SHA-256."
                    )

                if parse_semver_tuple(existing_ws_ont.ontology_version) > parse_semver_tuple(
                    session.head_version
                ):
                    if not acknowledge_historical_contract_warning:
                        raise HistoricalContractWarningUnacknowledgedError(
                            f"Workspace ontology version '{existing_ws_ont.ontology_version}' "
                            f"exceeds session head '{session.head_version}'; explicit warning "
                            "acknowledgement required."
                        )
                    if (
                        not historical_contract_rationale
                        or len(historical_contract_rationale.strip()) < 10
                    ):
                        raise HistoricalContractRationaleRequiredError(
                            "Historical contract rationale must be at least 10 characters."
                        )

                if enforce_lineage:
                    session_version_shas = self._collect_session_lineage_shas(session_dir)
                    if pre_ws_canonical_sha not in session_version_shas:
                        raise LineageRootMismatchError(
                            f"Workspace contract SHA-256 '{pre_ws_canonical_sha}' is not an "
                            f"ancestor in session '{session_id}' lineage."
                        )
            else:
                if exp_ws not in ("", "0" * 64):
                    raise ConcurrentModificationError(
                        f"Workspace OCC conflict: expected '{exp_ws}', but no workspace contract "
                        f"exists at '{target_contract_path.name}'."
                    )

            if self._enforce_governance:
                self._verify_promotion_governance(
                    session_dir=session_dir,
                    session=session,
                    existing_ws_ont=existing_ws_ont,
                    head_ont=head_ont,
                    acknowledge_historical_contract_warning=acknowledge_historical_contract_warning,
                )

            version_tag = normalize_version_tag(session.head_version)
            now_iso = datetime.now(UTC).isoformat()

            # Phase 1: Write signed PROMOTION_INTENT to audit.jsonl
            intent_details: dict[str, Any] = {
                "workspace_path": str(target_contract_path),
                "pre_workspace_sha256": pre_ws_canonical_sha,
                "target_head_sha256": session.head_sha256,
                "target_version": version_tag,
                "rationale": rationale.strip(),
            }
            if historical_contract_rationale:
                intent_details["historical_contract_rationale"] = (
                    historical_contract_rationale.strip()
                )

            intent_entry = self._append_raw_audit_entry_under_lock(
                session_dir=session_dir,
                session_id=session_id,
                action="PROMOTION_INTENT",
                target_version=version_tag,
                head_sha256=session.head_sha256,
                actor=actor,
                details=intent_details,
                prev_audit_sha=session.audit_chain_head_sha256,
            )
            session.audit_chain_head_sha256 = intent_entry["audit_sha256"]
            self._write_session_json(session_dir, session)

            # Phase 2: Stage canonical contract in workspace, fsync, atomic replace
            canonical_dict = canonicalize_graph_a_dict(head_ont)
            if target_contract_path.suffix.lower() == ".json":
                out_bytes = dumps_jcs_bytes(canonical_dict)
            else:
                out_bytes = yaml.safe_dump(canonical_dict, sort_keys=False).encode("utf-8")

            tmp_ws = lock_dir / f".graph_a.tmp.{secrets.token_hex(6)}"
            flags = (
                os.O_CREAT
                | os.O_EXCL
                | os.O_WRONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                fd = os.open(str(tmp_ws), flags, 0o600)
                try:
                    os.write(fd, out_bytes)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                set_owner_only_permissions(tmp_ws)
                replace_with_retry(tmp_ws, target_contract_path)
                fsync_directory_best_effort(lock_dir)
            except BaseException as exc:
                with contextlib.suppress(OSError):
                    if tmp_ws.exists():
                        tmp_ws.unlink()
                abort_entry = self._append_raw_audit_entry_under_lock(
                    session_dir=session_dir,
                    session_id=session_id,
                    action="PROMOTION_ABORTED",
                    target_version=version_tag,
                    head_sha256=session.head_sha256,
                    actor=actor,
                    details={
                        "workspace_path": str(target_contract_path),
                        "target_head_sha256": session.head_sha256,
                        "reason": str(exc),
                    },
                    prev_audit_sha=session.audit_chain_head_sha256,
                )
                session.audit_chain_head_sha256 = abort_entry["audit_sha256"]
                self._write_session_json(session_dir, session)
                raise

            # Record promotion.json and update SessionMetadata + PROMOTION_COMMITTED
            promo_record = {
                "session_id": session_id,
                "promoted_version": version_tag,
                "head_sha256": session.head_sha256,
                "workspace_sha256": session.head_sha256,
                "pre_workspace_sha256": pre_ws_canonical_sha,
                "workspace_path": str(target_contract_path),
                "rationale": rationale.strip(),
                "promoted_by": actor,
                "promoted_at": now_iso,
            }
            promo_tmp = session_dir / f".promotion.{secrets.token_hex(4)}.tmp"
            promo_path = session_dir / "promotion.json"
            promo_bytes = json.dumps(promo_record, indent=2).encode("utf-8")
            fd_p = os.open(
                str(promo_tmp),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
                0o600,
            )
            try:
                os.write(fd_p, promo_bytes)
                os.fsync(fd_p)
            finally:
                os.close(fd_p)
            set_owner_only_permissions(promo_tmp)
            replace_with_retry(promo_tmp, promo_path)
            set_owner_only_permissions(promo_path)

            promoted_list = [normalize_version_tag(v) for v in session.promoted_versions]
            if version_tag not in promoted_list:
                promoted_list.append(version_tag)

            committed_entry = self._append_raw_audit_entry_under_lock(
                session_dir=session_dir,
                session_id=session_id,
                action="PROMOTION_COMMITTED",
                target_version=version_tag,
                head_sha256=session.head_sha256,
                actor=actor,
                details={
                    "workspace_path": str(target_contract_path),
                    "workspace_sha256": session.head_sha256,
                    "pre_workspace_sha256": pre_ws_canonical_sha,
                    "target_version": version_tag,
                },
                prev_audit_sha=session.audit_chain_head_sha256,
            )
            final_audit_sha = committed_entry["audit_sha256"]

            updated_session = session.model_copy(
                update={
                    "updated_at": now_iso,
                    "promoted_versions": promoted_list,
                    "last_promoted_sha256": session.head_sha256,
                    "audit_chain_head_sha256": final_audit_sha,
                }
            )
            self._write_session_json(session_dir, updated_session)

            return PromotionResult(
                session_id=session_id,
                promoted_version=version_tag,
                head_sha256=session.head_sha256,
                workspace_sha256=session.head_sha256,
                pre_workspace_sha256=pre_ws_canonical_sha,
                target_contract_path=target_contract_path,
                promoted_at=now_iso,
                audit_head_sha256=final_audit_sha,
            )

    def _collect_session_lineage_shas(self, session_dir: Path) -> set[str]:
        """Collect all ontology SHA-256 digests recorded in session versions/."""
        shas: set[str] = set()
        versions_dir = session_dir / "versions"
        if not versions_dir.is_dir():
            return shas
        for vdir in versions_dir.iterdir():
            if not vdir.is_dir():
                continue
            for mname in ("meta.json", "metadata.json"):
                mpath = vdir / mname
                if mpath.is_file():
                    with contextlib.suppress(OSError, json.JSONDecodeError):
                        mdata = json.loads(mpath.read_text(encoding="utf-8"))
                        t_sha = mdata.get("target_sha256")
                        if isinstance(t_sha, str) and len(t_sha) == 64:
                            shas.add(t_sha.lower())
        return shas

    def reconcile_and_recover_session(
        self,
        session_id: str,
        *,
        staging_grace_seconds: float = DEFAULT_STAGING_GRACE_SECONDS,
        timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        """Execute deterministic crash recovery and reconciliation under L5 session lock."""
        session_dir = self.session_manager.get_session_dir(session_id)
        session_json_path = session_dir / "session.json"
        if not session_dir.exists() or not session_json_path.exists():
            return

        lock_path = session_dir / ".session.lock"
        with reentrant_session_lock(lock_path, session_id=session_id, timeout=timeout_sec):
            if not session_json_path.exists():
                return

            session = SessionMetadata.model_validate_json(
                session_json_path.read_text(encoding="utf-8")
            )
            audit_file = session_dir / "audit.jsonl"

            # 1. Audit Chain Integrity Verification
            tail_sha256 = self._verify_audit_chain_or_raise(session_id, session)
            if tail_sha256 != session.audit_chain_head_sha256:
                session.audit_chain_head_sha256 = tail_sha256
                self._write_session_json(session_dir, session)

            # 2. Missing Active Version Directory Recovery
            if session.head_version != GENESIS_VERSION:
                active_version_tag = normalize_version_tag(session.head_version)
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

            # 3. Staging Cleanup Safety Invariant (respecting staging_grace_seconds)
            committed_set = set(session.committed_versions)
            now_epoch = time.time()
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
                                or (t_ver and normalize_version_tag(t_ver) in committed_set)
                            ):
                                keep = True
                        except (OSError, json.JSONDecodeError):
                            pass

                    if not keep and staging_grace_seconds > 0:
                        try:
                            age = now_epoch - staging_dir.stat().st_mtime
                            if age < staging_grace_seconds:
                                keep = True
                        except OSError:
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
                    if v_clean == GENESIS_VERSION:
                        continue
                    try:
                        v_tuple = parse_semver_tuple(v_clean)
                    except ValueError:
                        continue

                    is_committed = (
                        v_name in committed_set
                        or v_clean in committed_set
                        or normalize_version_tag(v_clean) in committed_set
                    )
                    if is_committed:
                        continue

                    if v_tuple > parse_semver_tuple(session.head_version):
                        meta_path = (
                            v_dir / "meta.json"
                            if (v_dir / "meta.json").exists()
                            else v_dir / "metadata.json"
                        )
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
                                    now_iso = datetime.now(UTC).isoformat()
                                    norm_tag = normalize_version_tag(v_clean)
                                    session.head_version = v_clean
                                    session.head_sha256 = target_sha
                                    if norm_tag not in session.committed_versions:
                                        session.committed_versions.append(norm_tag)
                                    session.updated_at = now_iso
                                    committed_set.add(norm_tag)

                                    self._mark_proposals_post_commit(
                                        session_dir=session_dir,
                                        accepted_proposal_id=mdata.get("proposal_id"),
                                        target_sha256=target_sha,
                                        now_iso=now_iso,
                                    )

                                    rec_entry = self._append_raw_audit_entry_under_lock(
                                        session_dir=session_dir,
                                        session_id=session_id,
                                        action="COMMITTED",
                                        target_version=norm_tag,
                                        head_sha256=target_sha,
                                        actor="system:recovery",
                                        details={
                                            "target_version": norm_tag,
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
                            quarantine_dir = (
                                session_dir
                                / ".quarantine"
                                / f"{v_name}-{int(time.time())}-{secrets.token_hex(4)}"
                            )
                            quarantine_dir.parent.mkdir(parents=True, exist_ok=True)
                            replace_with_retry(v_dir, quarantine_dir)

            # 5 & 6. Incomplete PENDING_COMMIT / PROMOTION_INTENT reconciliation from audit tail
            if audit_file.exists():
                records = self._read_valid_audit_records(audit_file)
                if records:
                    last_rec = records[-1]
                    last_action = last_rec.get("action")

                    if last_action == "PENDING_COMMIT":
                        p_details = last_rec.get("details", {})
                        p_version = str(p_details.get("target_version") or "")
                        p_target_sha = p_details.get("target_sha256")
                        p_parent_sha = p_details.get("parent_sha256")
                        p_proposal_id = p_details.get("proposal_id")

                        clean_v = p_version.lstrip("v")
                        norm_tag = normalize_version_tag(clean_v) if clean_v else ""
                        v_dir = session_dir / "versions" / norm_tag if norm_tag else None

                        can_finalize = False
                        if (
                            v_dir is not None
                            and v_dir.exists()
                            and (v_dir / "ontology.json").exists()
                            and clean_v
                        ):
                            try:
                                v_tuple = parse_semver_tuple(clean_v)
                                head_tuple = parse_semver_tuple(session.head_version)
                                forward_ok = (
                                    p_parent_sha == session.head_sha256 and v_tuple > head_tuple
                                ) or (
                                    p_target_sha == session.head_sha256
                                    and clean_v == session.head_version
                                )
                                if forward_ok:
                                    ont_model = GraphAOntology.model_validate_json(
                                        (v_dir / "ontology.json").read_text(encoding="utf-8")
                                    )
                                    if compute_ontology_sha256(ont_model) == p_target_sha:
                                        can_finalize = True
                            except (OSError, json.JSONDecodeError, ValidationError, ValueError):
                                can_finalize = False

                        if can_finalize and norm_tag and isinstance(p_target_sha, str):
                            now_iso = datetime.now(UTC).isoformat()
                            session.head_version = clean_v
                            session.head_sha256 = p_target_sha
                            if norm_tag not in session.committed_versions:
                                session.committed_versions.append(norm_tag)
                            session.updated_at = now_iso

                            self._mark_proposals_post_commit(
                                session_dir=session_dir,
                                accepted_proposal_id=p_proposal_id,
                                target_sha256=p_target_sha,
                                now_iso=now_iso,
                            )

                            committed_entry = self._append_raw_audit_entry_under_lock(
                                session_dir=session_dir,
                                session_id=session_id,
                                action="COMMITTED",
                                target_version=norm_tag,
                                head_sha256=p_target_sha,
                                actor="system:recovery",
                                details=p_details,
                                prev_audit_sha=last_rec["audit_sha256"],
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
                                prev_audit_sha=last_rec["audit_sha256"],
                            )
                            session.audit_chain_head_sha256 = abort_entry["audit_sha256"]
                            self._write_session_json(session_dir, session)

                    elif last_action == "PROMOTION_INTENT":
                        p_details = last_rec.get("details", {})
                        ws_path_str = p_details.get("workspace_path")
                        target_head_sha = p_details.get("target_head_sha256")
                        target_ver = str(p_details.get("target_version") or session.head_version)
                        norm_ver = normalize_version_tag(target_ver)

                        promoted_ok = False
                        resolved_ws_file: Path | None = None
                        if isinstance(ws_path_str, str) and isinstance(target_head_sha, str):
                            raw_ws_file = Path(ws_path_str)
                            if not raw_ws_file.is_symlink():
                                try:
                                    env_roots = os.environ.get("OPENDV_WORKSPACE_ROOTS", "").strip()
                                    sroot = self.security_manager.studio_root
                                    default_ws_roots: list[Path | str] = [
                                        os.getcwd(),
                                        sroot.parent,
                                    ]
                                    if sroot.parent.name == ".opendv":
                                        default_ws_roots.append(sroot.parent.parent)
                                    recovery_roots: Sequence[Path | str] | None = (
                                        None if env_roots else default_ws_roots
                                    )
                                    resolved_ws_file = validate_workspace_containment(
                                        raw_ws_file,
                                        allowed_roots=recovery_roots,
                                    )
                                except (
                                    InvalidWorkspacePathError,
                                    RemoteServerPathForbiddenError,
                                    OSError,
                                ):
                                    resolved_ws_file = None

                            if (
                                resolved_ws_file is not None
                                and not resolved_ws_file.is_symlink()
                                and resolved_ws_file.is_file()
                            ):
                                try:
                                    raw_ws = resolved_ws_file.read_bytes()
                                    if resolved_ws_file.suffix.lower() == ".json":
                                        ws_ont = GraphAOntology.model_validate_json(
                                            raw_ws.decode("utf-8")
                                        )
                                    else:
                                        ws_ont = GraphAOntology.model_validate(
                                            yaml.safe_load(raw_ws.decode("utf-8"))
                                        )
                                    if compute_ontology_sha256(ws_ont) == target_head_sha:
                                        promoted_ok = True
                                except (OSError, ValidationError, ValueError, yaml.YAMLError):
                                    promoted_ok = False

                        now_iso = datetime.now(UTC).isoformat()
                        if promoted_ok and isinstance(target_head_sha, str):
                            promo_record = {
                                "session_id": session_id,
                                "promoted_version": norm_ver,
                                "head_sha256": target_head_sha,
                                "workspace_sha256": target_head_sha,
                                "pre_workspace_sha256": str(
                                    p_details.get("pre_workspace_sha256") or "0" * 64
                                ),
                                "workspace_path": str(resolved_ws_file or ws_path_str),
                                "rationale": str(p_details.get("rationale") or ""),
                                "promoted_by": str(last_rec.get("actor") or "system:recovery"),
                                "promoted_at": now_iso,
                                "reconciled": True,
                            }
                            promo_tmp = session_dir / f".promotion.{secrets.token_hex(4)}.tmp"
                            promo_path = session_dir / "promotion.json"
                            promo_bytes = json.dumps(promo_record, indent=2).encode("utf-8")
                            fd_p = os.open(
                                str(promo_tmp),
                                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
                                0o600,
                            )
                            try:
                                os.write(fd_p, promo_bytes)
                                os.fsync(fd_p)
                            finally:
                                os.close(fd_p)
                            set_owner_only_permissions(promo_tmp)
                            replace_with_retry(promo_tmp, promo_path)
                            set_owner_only_permissions(promo_path)

                            if norm_ver not in session.promoted_versions:
                                session.promoted_versions.append(norm_ver)
                            session.last_promoted_sha256 = target_head_sha
                            session.updated_at = now_iso
                            rec_promo = self._append_raw_audit_entry_under_lock(
                                session_dir=session_dir,
                                session_id=session_id,
                                action="PROMOTION_COMMITTED",
                                target_version=norm_ver,
                                head_sha256=session.head_sha256,
                                actor="system:recovery",
                                details={**p_details, "reconciled": True},
                                prev_audit_sha=last_rec["audit_sha256"],
                            )
                            session.audit_chain_head_sha256 = rec_promo["audit_sha256"]
                            self._write_session_json(session_dir, session)
                        else:
                            abort_promo = self._append_raw_audit_entry_under_lock(
                                session_dir=session_dir,
                                session_id=session_id,
                                action="PROMOTION_ABORTED",
                                target_version=norm_ver,
                                head_sha256=session.head_sha256,
                                actor="system:recovery",
                                details={**p_details, "reconciled": True},
                                prev_audit_sha=last_rec["audit_sha256"],
                            )
                            session.audit_chain_head_sha256 = abort_promo["audit_sha256"]
                            self._write_session_json(session_dir, session)

    def _verify_audit_chain_or_raise(self, session_id: str, session: SessionMetadata) -> str:
        """Verify the full HMAC and hash chaining of audit.jsonl, raising error if corrupt."""
        session_dir = self.session_manager.get_session_dir(session_id)
        audit_file = session_dir / "audit.jsonl"

        if not audit_file.exists():
            raise TamperEvidentChainCorruptError(f"Audit ledger missing for session '{session_id}'")

        raw_bytes = audit_file.read_bytes()
        if not raw_bytes:
            raise TamperEvidentChainCorruptError(
                f"Audit ledger is empty for session '{session_id}'"
            )

        anchor_sha = session.imported_audit_anchor_sha256
        if not raw_bytes.endswith(b"\n"):
            last_nl = raw_bytes.rfind(b"\n")
            prev_line_sha: str | None = None
            prefix_seen_anchor = anchor_sha is None
            if last_nl >= 0:
                prefix_lines = [
                    ln.strip()
                    for ln in raw_bytes[:last_nl].decode("utf-8", errors="replace").splitlines()
                    if ln.strip()
                ]
                for pline in prefix_lines:
                    try:
                        prec = json.loads(pline)
                    except json.JSONDecodeError as exc:
                        raise TamperEvidentChainCorruptError(
                            f"Corrupt JSON in audit ledger for session '{session_id}': {exc}"
                        ) from exc
                    if not isinstance(prec, dict) or not isinstance(prec.get("audit_sha256"), str):
                        raise TamperEvidentChainCorruptError(
                            f"Corrupt audit entry in session '{session_id}'"
                        )
                    prev_line_sha = prec["audit_sha256"]
                    if not prefix_seen_anchor and prev_line_sha == anchor_sha:
                        prefix_seen_anchor = True

            tail_fragment = raw_bytes[last_nl + 1 :]
            tail_is_valid = False
            try:
                tail_rec = json.loads(tail_fragment.decode("utf-8"))
                if isinstance(tail_rec, dict):
                    t_sha = tail_rec.get("audit_sha256")
                    t_sig = tail_rec.get("hmac_signature")
                    t_prev = tail_rec.get("prev_audit_sha256")
                    if isinstance(t_sha, str) and len(t_sha) == 64 and t_prev == prev_line_sha:
                        t_recon = {
                            k: v
                            for k, v in tail_rec.items()
                            if k not in ("audit_sha256", "hmac_signature")
                        }
                        t_canon = dumps_jcs_bytes(t_recon)
                        if hashlib.sha256(t_canon).hexdigest().lower() == t_sha:
                            need_hmac = prefix_seen_anchor or (t_sha == anchor_sha)
                            if not need_hmac or (
                                isinstance(t_sig, str)
                                and hmac.compare_digest(
                                    t_sig, self.security_manager.sign_audit_entry(t_canon)
                                )
                            ):
                                tail_is_valid = True
            except (UnicodeDecodeError, json.JSONDecodeError):
                tail_is_valid = False

            if tail_is_valid:
                raw_bytes = raw_bytes + b"\n"
                atomic_repair_audit_file(audit_file, raw_bytes)
            elif last_nl >= 0:
                raw_bytes = raw_bytes[: last_nl + 1]
                atomic_repair_audit_file(audit_file, raw_bytes)
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

        anchor_sha = session.imported_audit_anchor_sha256
        seen_anchor = anchor_sha is None
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

            if not isinstance(actual_sha, str) or prev_sha != expected_prev:
                raise TamperEvidentChainCorruptError(
                    f"Audit chain hash break in '{session_id}': expected prev '{expected_prev}', "
                    f"got '{prev_sha}'"
                )

            recon = {k: v for k, v in record.items() if k not in ("audit_sha256", "hmac_signature")}
            canonical_bytes = dumps_jcs_bytes(recon)
            computed_sha = hashlib.sha256(canonical_bytes).hexdigest().lower()
            if computed_sha != actual_sha:
                raise TamperEvidentChainCorruptError(
                    f"Audit entry SHA-256 digest mismatch in '{session_id}'"
                )

            if not seen_anchor and actual_sha == anchor_sha:
                seen_anchor = True

            if seen_anchor:
                expected_sig = self.security_manager.sign_audit_entry(canonical_bytes)
                if not hmac.compare_digest(str(actual_sig or ""), expected_sig):
                    raise TamperEvidentChainCorruptError(
                        f"Audit entry HMAC signature verification failed in '{session_id}'"
                    )

            chain_hashes.append(actual_sha)
            expected_prev = actual_sha

        if not seen_anchor:
            raise TamperEvidentChainCorruptError(
                f"Imported audit anchor '{anchor_sha}' missing from audit chain in '{session_id}'"
            )

        tail_sha256 = chain_hashes[-1]
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
        payload = metadata.model_dump_json(indent=2).encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(str(tmp_path), flags, 0o600)
            try:
                os.write(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
            set_owner_only_permissions(tmp_path)
            replace_with_retry(tmp_path, session_json_path)
            set_owner_only_permissions(session_json_path)
        finally:
            if tmp_path.exists():
                with contextlib.suppress(OSError):
                    tmp_path.unlink()


# ==============================================================================
# CAS Mark-and-Sweep Garbage Collection & Source Removal (docs/19 §6.5)
# ==============================================================================


def _iter_manifest_entries(m_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect source/file descriptor dicts from BOTH manifest['sources'] and manifest['files']."""
    entries: list[dict[str, Any]] = []
    for key in ("sources", "files"):
        raw = m_data.get(key)
        if isinstance(raw, dict):
            for v in raw.values():
                if isinstance(v, dict):
                    entries.append(v)
        elif isinstance(raw, list):
            for v in raw:
                if isinstance(v, dict):
                    entries.append(v)
    return entries


def mark_and_sweep_cas(
    session_dir: Path | str,
    dry_run: bool = False,
    timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> set[str]:
    """Execute fail-closed mark-and-sweep garbage collection over session inputs/cas/ under L5.

    Fail-closed invariant:
    - Acquires L5 `.session.lock` (reentrant if already held).
    - If `inputs/manifest.json` is missing, unreadable, or corrupt JSON, returns `set()`
      immediately with ZERO deletions.
    - Scans BOTH `manifest["sources"]` and `manifest["files"]`, `evidence/chunks.jsonl`,
      `evidence/tables.jsonl`, `clarifications/*.json`, proposals with status in
      `("PENDING", "STAGED", "DRAFT", "STALE")`, and
      `versions/*/{ontology,delta,meta,metadata,evaluation}.json`.
    """
    sdir = Path(session_dir).resolve()
    if not sdir.exists():
        return set()

    lock_path = sdir / ".session.lock"
    with reentrant_session_lock(lock_path, session_id=sdir.name, timeout=timeout_sec):
        cas = ContentAddressableStorage(root_dir=sdir / "inputs")
        all_blobs = set(cas.list_blobs())
        if not all_blobs:
            return set()

        # 1. Fail-closed manifest.json load (BOTH 'sources' and 'files')
        manifest_path = sdir / "inputs" / "manifest.json"
        if not manifest_path.is_file():
            logger.warning(
                "manifest.json missing in session '%s'; aborting CAS GC fail-closed.", sdir.name
            )
            return set()

        try:
            m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Corrupt/unreadable manifest.json for CAS GC (fail-closed): %s", exc)
            return set()

        if not isinstance(m_data, dict):
            logger.warning("Non-dict manifest.json for CAS GC (fail-closed).")
            return set()

        reachable: set[str] = set()
        tombstoned_source_ids: set[str] = set()

        for src in _iter_manifest_entries(m_data):
            sid = src.get("source_id") or src.get("id")
            is_tomb = bool(src.get("tombstoned") or src.get("deleted"))
            if is_tomb:
                if sid:
                    tombstoned_source_ids.add(str(sid))
            else:
                for key in ("sha256", "blob_sha256", "content_sha256", "source_sha256"):
                    val = src.get(key)
                    if (
                        isinstance(val, str)
                        and len(val) == 64
                        and re.fullmatch(r"[0-9a-fA-F]{64}", val)
                    ):
                        reachable.add(val.lower())

        # 2. Active citations in evidence/chunks.jsonl & evidence/tables.jsonl
        for ev_name in ("chunks.jsonl", "tables.jsonl"):
            ev_path = sdir / "evidence" / ev_name
            if ev_path.is_file():
                try:
                    for line in ev_path.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        rec = json.loads(line)
                        if not isinstance(rec, dict):
                            continue
                        rec_meta = (
                            rec.get("metadata") if isinstance(rec.get("metadata"), dict) else {}
                        )
                        src_id = str(
                            rec.get("source_id")
                            or rec.get("document_id")
                            or rec_meta.get("source_id")
                            or ""
                        )
                        is_rec_tomb = bool(rec.get("tombstoned") or rec_meta.get("tombstoned"))
                        if src_id in tombstoned_source_ids or is_rec_tomb:
                            continue
                        _extract_sha256_recursively(rec, reachable)
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning("Error reading %s for CAS GC (fail-closed): %s", ev_name, exc)
                    return set()

        # 3. Clarifications in clarifications/*.json
        clarifications_dir = sdir / "clarifications"
        if clarifications_dir.is_dir():
            for cfile in clarifications_dir.glob("*.json"):
                try:
                    cdata = json.loads(cfile.read_text(encoding="utf-8"))
                    _extract_sha256_recursively(cdata, reachable)
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning(
                        "Error reading clarification %s for CAS GC (fail-closed): %s",
                        cfile.name,
                        exc,
                    )
                    return set()

        # 4. Active and stale proposals in proposals/*.json
        proposals_dir = sdir / "proposals"
        if proposals_dir.is_dir():
            for pfile in proposals_dir.glob("*.json"):
                try:
                    pdata = json.loads(pfile.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning(
                        "Error reading proposal %s for CAS GC (fail-closed): %s",
                        pfile.name,
                        exc,
                    )
                    return set()
                if isinstance(pdata, dict) and pdata.get("status") in (
                    "PENDING",
                    "STAGED",
                    "DRAFT",
                    "STALE",
                    "PROPOSED",
                    None,
                ):
                    _extract_sha256_recursively(pdata, reachable)

        # 5. Committed versions in versions/*/
        versions_dir = sdir / "versions"
        if versions_dir.is_dir():
            for vdir in versions_dir.iterdir():
                if vdir.is_dir():
                    for fname in (
                        "ontology.json",
                        "delta.json",
                        "meta.json",
                        "metadata.json",
                        "evaluation.json",
                    ):
                        fpath = vdir / fname
                        if fpath.is_file():
                            try:
                                vdata = json.loads(fpath.read_text(encoding="utf-8"))
                            except (OSError, json.JSONDecodeError) as exc:
                                logger.warning(
                                    "Error reading %s/%s for CAS GC (fail-closed): %s",
                                    vdir.name,
                                    fname,
                                    exc,
                                )
                                return set()
                            _extract_sha256_recursively(vdata, reachable)

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
) -> set[str]:
    """Mark a source as tombstoned and purge its unreferenced blobs under L5 session lock.

    Raises:
        SourceNotFoundError: If `source_id` does not exist in `manifest.json` (HTTP 404 / exit 3).
        SourceReferencedByPendingProposalError: If cited by an active/stale proposal (exit 4).
        SourceReferencedByCommittedVersionError: If cited by a committed version (exit 4).
    """
    sdir = Path(session_dir).resolve()
    lock_path = sdir / ".session.lock"
    sid = sdir.name

    with reentrant_session_lock(lock_path, session_id=sid, timeout=timeout_sec):
        manifest_path = sdir / "inputs" / "manifest.json"
        if not manifest_path.is_file():
            raise SourceNotFoundError(f"Source '{source_id}' not found in session '{sid}'.")

        m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(m_data, dict):
            raise SourceNotFoundError(f"Source '{source_id}' not found in session '{sid}'.")

        # Locate active source entry across BOTH manifest["sources"] and manifest["files"]
        matched_entries: list[dict[str, Any]] = []
        for entry in _iter_manifest_entries(m_data):
            entry_id = entry.get("source_id") or entry.get("id")
            is_tomb = bool(entry.get("tombstoned") or entry.get("deleted"))
            if entry_id == source_id and not is_tomb:
                matched_entries.append(entry)

        if not matched_entries:
            raise SourceNotFoundError(f"Source '{source_id}' not found in session '{sid}'.")

        # Collect both source_id AND all associated SHA-256 digests to guard against deletion
        match_tokens: set[str] = {source_id}
        source_hashes: set[str] = set()
        for entry in matched_entries:
            for key in ("sha256", "blob_sha256", "content_sha256", "source_sha256"):
                val = entry.get(key)
                if isinstance(val, str) and len(val) == 64:
                    low = val.lower()
                    match_tokens.add(low)
                    source_hashes.add(low)

        # 1. Check active and stale proposals against BOTH source_id and source_sha256
        proposals_dir = sdir / "proposals"
        if proposals_dir.is_dir():
            for pfile in proposals_dir.glob("*.json"):
                try:
                    pdata = json.loads(pfile.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(pdata, dict) and pdata.get("status") in (
                    None,
                    "PENDING",
                    "STAGED",
                    "DRAFT",
                    "STALE",
                    "PROPOSED",
                ):
                    found_sources: set[str] = set()
                    for tok in match_tokens:
                        _extract_strings_matching(pdata, tok, found_sources)
                    if found_sources:
                        raise SourceReferencedByPendingProposalError(
                            f"Cannot delete source '{source_id}': cited by proposal "
                            f"'{pfile.stem}' ({pdata.get('status')})"
                        )

        # 2. Check committed versions against BOTH source_id and source_sha256
        versions_dir = sdir / "versions"
        if versions_dir.is_dir():
            for vdir in versions_dir.iterdir():
                if vdir.is_dir():
                    for fname in ("ontology.json", "delta.json", "evaluation.json"):
                        fpath = vdir / fname
                        if fpath.is_file():
                            try:
                                vdata = json.loads(fpath.read_text(encoding="utf-8"))
                            except (OSError, json.JSONDecodeError):
                                continue
                            found_sources = set()
                            for tok in match_tokens:
                                _extract_strings_matching(vdata, tok, found_sources)
                            if found_sources:
                                raise SourceReferencedByCommittedVersionError(
                                    f"Cannot delete source '{source_id}': cited in committed "
                                    f"version '{vdir.name}'"
                                )

        # 3. Atomically mark tombstoned in manifest.json (abort before GC if write fails!)
        for entry in matched_entries:
            entry["tombstoned"] = True

        remaining_active_hashes: set[str] = set()
        for entry in _iter_manifest_entries(m_data):
            if bool(entry.get("tombstoned") or entry.get("deleted")):
                continue
            for key in ("sha256", "blob_sha256", "content_sha256", "source_sha256"):
                val = entry.get(key)
                if isinstance(val, str) and len(val) == 64:
                    remaining_active_hashes.add(val.lower())

        tmp_man = sdir / "inputs" / f".manifest.{secrets.token_hex(4)}.tmp"
        man_bytes = json.dumps(m_data, indent=2).encode("utf-8")
        fd = os.open(
            str(tmp_man),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            os.write(fd, man_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        set_owner_only_permissions(tmp_man)
        replace_with_retry(tmp_man, manifest_path)
        set_owner_only_permissions(manifest_path)

        # 4. Atomically tombstone chunks & tables in evidence/{chunks,tables}.jsonl
        for ev_name in ("chunks.jsonl", "tables.jsonl"):
            ev_path = sdir / "evidence" / ev_name
            if ev_path.is_file():
                lines = ev_path.read_text(encoding="utf-8").splitlines()
                updated_lines: list[str] = []
                for line in lines:
                    line = line.strip()
                    if line:
                        c = json.loads(line)
                        c_meta = c.get("metadata") if isinstance(c.get("metadata"), dict) else {}
                        c_sid = str(
                            c.get("source_id")
                            or c.get("document_id")
                            or c_meta.get("source_id")
                            or ""
                        )
                        c_sha = str(
                            c.get("source_sha256")
                            or c.get("blob_sha256")
                            or c.get("sha256")
                            or c_meta.get("source_sha256")
                            or ""
                        ).lower()
                        if c_sid == source_id or (
                            not c_sid
                            and c_sha
                            and c_sha in source_hashes
                            and c_sha not in remaining_active_hashes
                        ):
                            c["tombstoned"] = True
                            if isinstance(c.get("metadata"), dict):
                                c["metadata"]["tombstoned"] = True
                        updated_lines.append(json.dumps(c))
                tmp_ev = sdir / "evidence" / f".{ev_name}.{secrets.token_hex(4)}.tmp"
                ev_bytes = ("\n".join(updated_lines) + ("\n" if updated_lines else "")).encode(
                    "utf-8"
                )
                fd_c = os.open(
                    str(tmp_ev),
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0),
                    0o600,
                )
                try:
                    os.write(fd_c, ev_bytes)
                    os.fsync(fd_c)
                finally:
                    os.close(fd_c)
                set_owner_only_permissions(tmp_ev)
                replace_with_retry(tmp_ev, ev_path)
                set_owner_only_permissions(ev_path)

        # 5. Run CAS mark-and-sweep GC
        return mark_and_sweep_cas(sdir, dry_run=False, timeout_sec=timeout_sec)


def _extract_strings_matching(obj: Any, target: str, out_set: set[str]) -> None:
    """Find any occurrence of target string in nested structures."""
    if isinstance(obj, str):
        val = obj.lower() if len(target) == 64 else obj
        if val == target or target in val:
            out_set.add(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _extract_strings_matching(v, target, out_set)
    elif isinstance(obj, list):
        for item in obj:
            _extract_strings_matching(item, target, out_set)
