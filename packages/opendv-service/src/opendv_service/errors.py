"""Domain exception hierarchy and error code mappings for opendv-service."""

from __future__ import annotations

from typing import Any

from opendv_contracts.errors import (
    ErrorCode,
    HoldNotFoundError,
    InputValidationError,
    NotFoundError,
    OntologyRecommenderUnavailableError,
    OpenDVError,
)


class PathContainmentError(OpenDVError, ValueError):
    """Raised when a path violates workspace directory containment or attempts path traversal."""

    error_code = ErrorCode.PATH_CONTAINMENT_ERROR


class PayloadTooLargeError(OpenDVError):
    """Raised when an uploaded payload exceeds the configured maximum byte size."""

    error_code = ErrorCode.PAYLOAD_TOO_LARGE
    exit_code = 2
    http_status = 413


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


class PipelineAbortedError(OpenDVError):
    """Raised when an active asynchronous pipeline run is aborted by user."""

    error_code = ErrorCode.INTERNAL_ERROR
    exit_code = 130
    http_status = 409


__all__ = [
    "CasIntegrityError",
    "ConflictError",
    "ErrorCode",
    "HoldNotFoundError",
    "InputValidationError",
    "NotFoundError",
    "OntologyRecommenderUnavailableError",
    "OpenDVError",
    "PathContainmentError",
    "PayloadTooLargeError",
    "PipelineAbortedError",
    "PipelineExecutionError",
    "ProviderUnavailableError",
    "RolePermissionError",
    "UnsupportedFormatError",
]
