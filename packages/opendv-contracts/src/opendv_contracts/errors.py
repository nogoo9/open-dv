from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel


class ErrorCode(StrEnum):
    """Canonical error classification across CLI exit codes and HTTP status codes."""

    # 0 Exit / 200 HTTP
    OK = "OK"

    # 1 Exit / 200 HTTP (Domain verification result)
    VERIFICATION_FAILED = "VERIFICATION_FAILED"

    # 2 Exit / 400 Bad Request
    PATH_CONTAINMENT_ERROR = "PATH_CONTAINMENT_ERROR"
    INVALID_REQUEST = "INVALID_REQUEST"

    # 2 Exit / 404 Not Found
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
    HOLD_NOT_FOUND = "HOLD_NOT_FOUND"
    ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"

    # 2 Exit / 413 Payload Too Large
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"

    # 2 Exit / 415 Unsupported Media Type
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"

    # 2 Exit / 422 Unprocessable Entity
    INPUT_VALIDATION_ERROR = "INPUT_VALIDATION_ERROR"
    SCHEMA_VALIDATION_ERROR = "SCHEMA_VALIDATION_ERROR"
    SOURCE_LOAD_ERROR = "SOURCE_LOAD_ERROR"
    SANDBOX_EXECUTION_ERROR = "SANDBOX_EXECUTION_ERROR"
    PROVIDER_CONFIG_ERROR = "PROVIDER_CONFIG_ERROR"
    DETERMINISTIC_PROVIDER_REJECTED = "DETERMINISTIC_PROVIDER_REJECTED"
    CONTRACT_VERSION_MISMATCH = "CONTRACT_VERSION_MISMATCH"
    ONTOLOGY_RECOMMENDER_UNAVAILABLE = "ONTOLOGY_RECOMMENDER_UNAVAILABLE"

    # 2 Exit / 500 Internal Error
    CAS_INTEGRITY_FAILURE = "CAS_INTEGRITY_FAILURE"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # 2 Exit / 503 Service Unavailable
    API_UNREACHABLE = "API_UNREACHABLE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"


class ErrorDetail(BaseContractModel):
    """Specific field or parameter validation error."""

    field: str = Field(..., description="Field path or parameter name")
    message: str = Field(..., description="Validation failure explanation")
    error_type: str = Field(default="value_error", description="Validator code or type")


class ProblemDetail(BaseContractModel):
    """RFC 9457 compliant error document (application/problem+json)."""

    type: str = Field(default="about:blank", description="URI identifying problem category")
    title: str = Field(..., description="Short, human-readable summary of problem type")
    status: int = Field(..., ge=200, le=599, description="HTTP status code")
    detail: str = Field(..., description="Human-readable explanation specific to this occurrence")
    instance: str | None = Field(
        default=None, description="URI reference identifying specific occurrence"
    )
    error_code: ErrorCode = Field(..., description="Machine-readable OpenDV ErrorCode")
    invalid_params: list[ErrorDetail] = Field(
        default_factory=list, description="Granular parameter failures"
    )
    extensions: dict[str, Any] = Field(
        default_factory=dict, description="Supplementary debugging context"
    )


class ErrorMappingEntry(BaseContractModel):
    """Entry describing the normative mapping from ErrorCode to exit code and HTTP status."""

    error_code: ErrorCode
    exit_code: int
    http_status: int
    problem_title: str
    problem_type_uri: str


ERROR_MAPPINGS: dict[ErrorCode, ErrorMappingEntry] = {
    ErrorCode.OK: ErrorMappingEntry(
        error_code=ErrorCode.OK,
        exit_code=0,
        http_status=200,
        problem_title="OK",
        problem_type_uri="urn:opendv:ok",
    ),
    ErrorCode.VERIFICATION_FAILED: ErrorMappingEntry(
        error_code=ErrorCode.VERIFICATION_FAILED,
        exit_code=1,
        http_status=200,
        problem_title="Verification Rejection",
        problem_type_uri="urn:opendv:verification-failed",
    ),
    ErrorCode.PATH_CONTAINMENT_ERROR: ErrorMappingEntry(
        error_code=ErrorCode.PATH_CONTAINMENT_ERROR,
        exit_code=2,
        http_status=400,
        problem_title="Path Containment Violation",
        problem_type_uri="urn:opendv:path-containment-error",
    ),
    ErrorCode.INVALID_REQUEST: ErrorMappingEntry(
        error_code=ErrorCode.INVALID_REQUEST,
        exit_code=2,
        http_status=400,
        problem_title="Bad Request",
        problem_type_uri="urn:opendv:invalid-request",
    ),
    ErrorCode.RESOURCE_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.RESOURCE_NOT_FOUND,
        exit_code=2,
        http_status=404,
        problem_title="Resource Not Found",
        problem_type_uri="urn:opendv:not-found",
    ),
    ErrorCode.RUN_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.RUN_NOT_FOUND,
        exit_code=2,
        http_status=404,
        problem_title="Run Not Found",
        problem_type_uri="urn:opendv:run-not-found",
    ),
    ErrorCode.PROJECT_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.PROJECT_NOT_FOUND,
        exit_code=2,
        http_status=404,
        problem_title="Project Not Found",
        problem_type_uri="urn:opendv:project-not-found",
    ),
    ErrorCode.HOLD_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.HOLD_NOT_FOUND,
        exit_code=2,
        http_status=404,
        problem_title="Hold Not Found",
        problem_type_uri="urn:opendv:hold-not-found",
    ),
    ErrorCode.ARTIFACT_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.ARTIFACT_NOT_FOUND,
        exit_code=2,
        http_status=404,
        problem_title="Artifact Not Found",
        problem_type_uri="urn:opendv:artifact-not-found",
    ),
    ErrorCode.PAYLOAD_TOO_LARGE: ErrorMappingEntry(
        error_code=ErrorCode.PAYLOAD_TOO_LARGE,
        exit_code=2,
        http_status=413,
        problem_title="Payload Too Large",
        problem_type_uri="urn:opendv:payload-too-large",
    ),
    ErrorCode.UNSUPPORTED_FORMAT: ErrorMappingEntry(
        error_code=ErrorCode.UNSUPPORTED_FORMAT,
        exit_code=2,
        http_status=415,
        problem_title="Unsupported Format",
        problem_type_uri="urn:opendv:unsupported-format",
    ),
    ErrorCode.INPUT_VALIDATION_ERROR: ErrorMappingEntry(
        error_code=ErrorCode.INPUT_VALIDATION_ERROR,
        exit_code=2,
        http_status=422,
        problem_title="Input Validation Error",
        problem_type_uri="urn:opendv:input-validation-error",
    ),
    ErrorCode.SCHEMA_VALIDATION_ERROR: ErrorMappingEntry(
        error_code=ErrorCode.SCHEMA_VALIDATION_ERROR,
        exit_code=2,
        http_status=422,
        problem_title="Schema Validation Error",
        problem_type_uri="urn:opendv:schema-validation-error",
    ),
    ErrorCode.SOURCE_LOAD_ERROR: ErrorMappingEntry(
        error_code=ErrorCode.SOURCE_LOAD_ERROR,
        exit_code=2,
        http_status=422,
        problem_title="Source Ingestion Error",
        problem_type_uri="urn:opendv:source-load-error",
    ),
    ErrorCode.SANDBOX_EXECUTION_ERROR: ErrorMappingEntry(
        error_code=ErrorCode.SANDBOX_EXECUTION_ERROR,
        exit_code=2,
        http_status=422,
        problem_title="Sandbox Execution Failure",
        problem_type_uri="urn:opendv:sandbox-error",
    ),
    ErrorCode.PROVIDER_CONFIG_ERROR: ErrorMappingEntry(
        error_code=ErrorCode.PROVIDER_CONFIG_ERROR,
        exit_code=2,
        http_status=422,
        problem_title="Provider Configuration Error",
        problem_type_uri="urn:opendv:provider-config-error",
    ),
    ErrorCode.DETERMINISTIC_PROVIDER_REJECTED: ErrorMappingEntry(
        error_code=ErrorCode.DETERMINISTIC_PROVIDER_REJECTED,
        exit_code=2,
        http_status=422,
        problem_title="Deterministic Provider Prohibited",
        problem_type_uri="urn:opendv:deterministic-rejected",
    ),
    ErrorCode.CONTRACT_VERSION_MISMATCH: ErrorMappingEntry(
        error_code=ErrorCode.CONTRACT_VERSION_MISMATCH,
        exit_code=2,
        http_status=422,
        problem_title="Contract Version Mismatch",
        problem_type_uri="urn:opendv:version-mismatch",
    ),
    ErrorCode.ONTOLOGY_RECOMMENDER_UNAVAILABLE: ErrorMappingEntry(
        error_code=ErrorCode.ONTOLOGY_RECOMMENDER_UNAVAILABLE,
        exit_code=2,
        http_status=422,
        problem_title="Ontology Recommender Unavailable",
        problem_type_uri="urn:opendv:ontology-recommender-unavailable",
    ),
    ErrorCode.CAS_INTEGRITY_FAILURE: ErrorMappingEntry(
        error_code=ErrorCode.CAS_INTEGRITY_FAILURE,
        exit_code=2,
        http_status=500,
        problem_title="CAS Digest Integrity Failure",
        problem_type_uri="urn:opendv:cas-integrity-failure",
    ),
    ErrorCode.INTERNAL_ERROR: ErrorMappingEntry(
        error_code=ErrorCode.INTERNAL_ERROR,
        exit_code=2,
        http_status=500,
        problem_title="Internal Server Error",
        problem_type_uri="urn:opendv:internal-error",
    ),
    ErrorCode.API_UNREACHABLE: ErrorMappingEntry(
        error_code=ErrorCode.API_UNREACHABLE,
        exit_code=2,
        http_status=503,
        problem_title="API Service Unreachable",
        problem_type_uri="urn:opendv:api-unreachable",
    ),
    ErrorCode.PROVIDER_UNAVAILABLE: ErrorMappingEntry(
        error_code=ErrorCode.PROVIDER_UNAVAILABLE,
        exit_code=2,
        http_status=503,
        problem_title="Harness Provider Unavailable",
        problem_type_uri="urn:opendv:provider-unavailable",
    ),
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
        return create_problem_detail(
            code=self.error_code,
            detail=self.message,
            instance=instance,
            extensions=self.details,
        )


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
