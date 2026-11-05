"""Domain exception hierarchy and error code mappings for opendv-service."""

from __future__ import annotations

from typing import Any

from opendv_contracts.errors import (
    ErrorCode,
    ProblemDetail,
    create_problem_detail,
    get_error_mapping,
)


class OpenDVError(Exception):
    """Base exception for all domain errors within opendv-service."""

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
        return create_problem_detail(
            code=self.error_code,
            detail=self.message,
            instance=instance,
            extensions=self.details,
        )


class InputValidationError(OpenDVError):
    """Raised when client input fails syntactic or structural schema validation."""

    error_code = ErrorCode.INPUT_VALIDATION_ERROR


class NotFoundError(OpenDVError):
    """Raised when a requested resource (project, run, hold, artifact) does not exist."""

    error_code = ErrorCode.RESOURCE_NOT_FOUND


class PathContainmentError(OpenDVError, ValueError):
    """Raised when a path violates workspace directory containment or attempts path traversal."""

    error_code = ErrorCode.PATH_CONTAINMENT_ERROR


class PayloadTooLargeError(OpenDVError):
    """Raised when an uploaded payload exceeds the configured maximum byte size."""

    error_code = ErrorCode.PAYLOAD_TOO_LARGE


class RolePermissionError(OpenDVError):
    """Raised when an operation is prohibited for the caller's RBAC role clearance."""

    error_code = ErrorCode.INVALID_REQUEST
    exit_code = 2
    http_status = 403


class ProviderUnavailableError(OpenDVError):
    """Raised when a requested LLM harness provider or credentials are unavailable."""

    error_code = ErrorCode.PROVIDER_UNAVAILABLE


class PipelineExecutionError(OpenDVError):
    """Raised when a pipeline run execution fails critically."""

    error_code = ErrorCode.INTERNAL_ERROR
    exit_code = 1
    http_status = 500


class UnsupportedFormatError(OpenDVError):
    """Raised when an unsupported file format or MIME type is supplied."""

    error_code = ErrorCode.UNSUPPORTED_FORMAT


class CasIntegrityError(OpenDVError):
    """Raised when content addressable storage hash verification fails."""

    error_code = ErrorCode.CAS_INTEGRITY_FAILURE


class ConflictError(OpenDVError):
    """Raised when an operation conflicts with current resource state (e.g. hold resolved)."""

    error_code = ErrorCode.INVALID_REQUEST
    exit_code = 2
    http_status = 409

    def __init__(
        self,
        message: str,
        *,
        error_code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, error_code=error_code, details=details)
        self.http_status = 409

