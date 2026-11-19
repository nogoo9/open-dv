"""Domain exceptions, result dataclasses, and atomic write helpers for AtomicCommitService."""

from __future__ import annotations

import contextlib
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_service.studio.workspace_security import (
    replace_with_retry,
    set_owner_only_permissions,
)

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


def atomic_write_bytes(
    target_path: Path,
    payload: bytes,
    *,
    tmp_name: str | None = None,
    truncate: bool = False,
    nofollow: bool = False,
) -> None:
    """Atomically write bytes via temp file + fsync + replace_with_retry with 0600 perms."""
    tmp_path = target_path.parent / (
        tmp_name or f".{target_path.name}.{secrets.token_hex(4)}.tmp"
    )
    mode_flag = os.O_TRUNC if truncate else os.O_EXCL
    flags = os.O_WRONLY | os.O_CREAT | mode_flag | getattr(os, "O_BINARY", 0)
    if nofollow:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(tmp_path), flags, 0o600)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        set_owner_only_permissions(tmp_path)
        replace_with_retry(tmp_path, target_path)
        set_owner_only_permissions(target_path)
    finally:
        if tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()


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
