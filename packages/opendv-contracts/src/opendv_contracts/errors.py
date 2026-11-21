"""Canonical ErrorCode, Exit Code, HTTP Status, and RFC 9457 Problem Details Registry."""

from __future__ import annotations

from typing import Any

from opendv_contracts.error_mappings_core import ERROR_MAPPINGS_CORE
from opendv_contracts.error_mappings_governance import ERROR_MAPPINGS_GOVERNANCE
from opendv_contracts.error_mappings_studio import ERROR_MAPPINGS_STUDIO
from opendv_contracts.error_models import (
    VALID_EXIT_CODES,
    ErrorCode,
    ErrorDetail,
    ErrorMappingEntry,
    ProblemDetail,
)

ERROR_MAPPINGS: dict[ErrorCode, ErrorMappingEntry] = {
    **ERROR_MAPPINGS_CORE,
    **ERROR_MAPPINGS_STUDIO,
    **ERROR_MAPPINGS_GOVERNANCE,
}
ERROR_MAP = ERROR_MAPPINGS

def get_error_mapping(code: ErrorCode) -> tuple[int, int]:
    """Returns (exit_code, http_status) for the given ErrorCode."""
    entry = ERROR_MAPPINGS.get(code)
    if entry is None:
        return (2, 500)
    return (entry.exit_code, entry.http_status)


def get_error_entry(code: ErrorCode) -> ErrorMappingEntry:
    """Returns the full ErrorMappingEntry for the given ErrorCode."""
    if code in ERROR_MAPPINGS:
        return ERROR_MAPPINGS[code]
    return ErrorMappingEntry(
        error_code=code,
        exit_code=2,
        http_status=500,
        problem_title="Internal Server Error",
        problem_type_uri="urn:opendv:internal-error",
    )


def create_problem_detail(
    code: ErrorCode,
    detail: str,
    *,
    instance: str | None = None,
    invalid_params: list[ErrorDetail] | None = None,
    extensions: dict[str, Any] | None = None,
) -> ProblemDetail:
    """Helper to construct an RFC 9457 ProblemDetail from an ErrorCode."""
    entry = get_error_entry(code)
    return ProblemDetail(
        type=entry.problem_type_uri,
        title=entry.problem_title,
        status=entry.http_status,
        detail=detail,
        instance=instance,
        error_code=code,
        invalid_params=invalid_params or [],
        extensions=extensions or {},
    )


class OpenDVError(Exception):
    """Base exception for all domain errors within OpenDV."""

    error_code: ErrorCode = ErrorCode.INTERNAL_ERROR
    exit_code: int = 2
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        error_code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if error_code is not None:
            self.error_code = error_code
        self.exit_code, self.http_status = get_error_mapping(self.error_code)
        self.details = details or {}

    def to_problem_detail(self, *, instance: str | None = None) -> ProblemDetail:
        """Convert exception to an RFC 9457 ProblemDetail DTO."""
        problem = create_problem_detail(
            code=self.error_code,
            detail=self.message,
            instance=instance,
            extensions=self.details,
        )
        if problem.status != self.http_status:
            return problem.model_copy(update={"status": self.http_status})
        return problem


class InputValidationError(OpenDVError):
    """Raised when client input fails syntactic, structural, or ontology validation."""

    error_code = ErrorCode.INPUT_VALIDATION_ERROR


class NotFoundError(OpenDVError):
    """Raised when a requested resource (project, run, hold, artifact) does not exist."""

    error_code = ErrorCode.RESOURCE_NOT_FOUND


class HoldNotFoundError(NotFoundError):
    """Raised when a requested steward hold does not exist."""

    error_code = ErrorCode.HOLD_NOT_FOUND


class OntologyRecommenderUnavailableError(OpenDVError):
    """Raised when an ontology recommendation policy is active but no
    recommender module is installed.
    """

    error_code = ErrorCode.ONTOLOGY_RECOMMENDER_UNAVAILABLE


DEFAULT_MAX_INLINE_BYTES: int = 50 * 1024 * 1024


class PayloadTooLargeError(OpenDVError):
    """Raised when an uploaded payload or request body exceeds the configured maximum byte size."""

    error_code = ErrorCode.PAYLOAD_TOO_LARGE
    exit_code = 2
    http_status = 413


__all__ = [
    "DEFAULT_MAX_INLINE_BYTES",
    "ERROR_MAP",
    "ERROR_MAPPINGS",
    "VALID_EXIT_CODES",
    "ErrorCode",
    "ErrorDetail",
    "ErrorMappingEntry",
    "HoldNotFoundError",
    "InputValidationError",
    "NotFoundError",
    "OntologyRecommenderUnavailableError",
    "OpenDVError",
    "PayloadTooLargeError",
    "ProblemDetail",
    "create_problem_detail",
    "get_error_entry",
    "get_error_mapping",
]
