"""Typed exception hierarchy for Open-DV data source and format adapters."""

from __future__ import annotations

from typing import Any

from opendv_contracts.errors import (
    ErrorCode,
    NotFoundError,
    OpenDVError,
    PayloadTooLargeError,
    ProblemDetail,
)
from opendv_core.paths import PathContainmentError


class SourceError(OpenDVError):
    """Base exception for all opendv-sources errors."""

    error_code: ErrorCode = ErrorCode.SOURCE_LOAD_ERROR
    exit_code: int = 2
    http_status: int = 422

    def to_problem_detail(self, *, instance: str | None = None) -> ProblemDetail:
        """Convert exception to an RFC 9457 ProblemDetail preserving instance http_status."""
        problem = super().to_problem_detail(instance=instance)
        if problem.status != self.http_status:
            return problem.model_copy(update={"status": self.http_status})
        return problem


class SourceNotFoundError(SourceError, NotFoundError):
    """Raised when an input source file or URI cannot be located."""

    error_code: ErrorCode = ErrorCode.RESOURCE_NOT_FOUND
    http_status: int = 404


class SourceLoadError(SourceError):
    """Raised when source data is corrupt, unparseable, or fails I/O."""

    error_code: ErrorCode = ErrorCode.SOURCE_LOAD_ERROR
    http_status: int = 422


class SourceEmptyError(SourceLoadError):
    """Raised when an input source relation or table is unexpectedly empty."""

    error_code: ErrorCode = ErrorCode.SOURCE_LOAD_ERROR
    http_status: int = 422


class SourceTableEmptyError(SourceEmptyError):
    """Raised when an input source relation or table is unexpectedly empty or missing."""


class FormatError(SourceError):
    """Raised when format decoding fails."""

    error_code: ErrorCode = ErrorCode.SOURCE_LOAD_ERROR
    http_status: int = 422


class UnsupportedFormatError(SourceError):
    """Raised when an unknown or uninstalled adapter or format is requested."""

    error_code: ErrorCode = ErrorCode.UNSUPPORTED_FORMAT
    http_status: int = 400

    def __init__(
        self,
        message: str,
        *,
        requested: str | None = None,
        available: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged_details = dict(details or {})
        if requested is not None:
            merged_details["requested"] = requested
        if available is not None:
            merged_details["available"] = available
        super().__init__(
            message,
            error_code=ErrorCode.UNSUPPORTED_FORMAT,
            details=merged_details,
        )


class DocumentExtractionError(SourceError):
    """Raised when document parsing or span extraction fails."""

    error_code: ErrorCode = ErrorCode.SOURCE_LOAD_ERROR
    http_status: int = 422


class InvalidDocumentError(DocumentExtractionError):
    """Raised when a document is corrupt, malformed, or cannot be parsed."""

    error_code: ErrorCode = ErrorCode.INPUT_VALIDATION_ERROR
    exit_code: int = 2
    http_status: int = 422

    def __init__(
        self,
        message: str,
        *,
        error_code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            error_code=error_code or ErrorCode.INPUT_VALIDATION_ERROR,
            details=details,
        )


class ArchiveSecurityViolationError(InvalidDocumentError, ValueError):
    """Raised when an archive or structured payload violates bomb, XXE, DDE, or SSRF guards."""

    error_code: ErrorCode = ErrorCode.ARCHIVE_SECURITY_VIOLATION
    exit_code: int = 4
    http_status: int = 422

    def __init__(
        self,
        message: str,
        *,
        error_code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            error_code=error_code or ErrorCode.ARCHIVE_SECURITY_VIOLATION,
            details=details,
        )


class DocumentPayloadTooLargeError(InvalidDocumentError, PayloadTooLargeError):
    """Raised when an input document or decompressed bomb exceeds configured byte limits."""

    error_code: ErrorCode = ErrorCode.PAYLOAD_TOO_LARGE
    exit_code: int = 2
    http_status: int = 413

    def __init__(
        self,
        message: str,
        *,
        error_code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            error_code=error_code or ErrorCode.PAYLOAD_TOO_LARGE,
            details=details,
        )


class OCRExecutionError(DocumentExtractionError):
    """Raised when an available OCR engine fails at runtime on a page image."""

    error_code: ErrorCode = ErrorCode.SOURCE_LOAD_ERROR
    exit_code: int = 2
    http_status: int = 422


class OCRUnavailableError(DocumentExtractionError):
    """Raised when an OCR engine is required (e.g. ocr_mode='force' on complex page)
    but is unavailable.
    """

    error_code: ErrorCode = ErrorCode.OCR_UNAVAILABLE
    exit_code: int = 1
    http_status: int = 501

    def __init__(
        self,
        message: str = "OCR_UNAVAILABLE",
        *,
        error_code: ErrorCode | str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        resolved_code = (
            ErrorCode(error_code)
            if error_code is not None and error_code in ErrorCode._value2member_map_
            else ErrorCode.OCR_UNAVAILABLE
        )
        super().__init__(
            message,
            error_code=resolved_code,
            details=details,
        )
        if error_code is not None and error_code not in ErrorCode._value2member_map_:
            self.error_code = error_code  # type: ignore[assignment]
        self.exit_code = 1
        self.http_status = 501


class OfficeConversionUnavailableError(DocumentExtractionError):
    """Raised when LibreOffice binary (`soffice`) is required for office document conversion
    but is not available on the host system.
    """

    error_code: ErrorCode = ErrorCode.OFFICE_CONVERSION_UNAVAILABLE
    exit_code: int = 1
    http_status: int = 501

    def __init__(
        self,
        message: str = "OFFICE_CONVERSION_UNAVAILABLE",
        *,
        error_code: ErrorCode | str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        resolved_code = (
            ErrorCode(error_code)
            if error_code is not None and error_code in ErrorCode._value2member_map_
            else ErrorCode.OFFICE_CONVERSION_UNAVAILABLE
        )
        super().__init__(
            message,
            error_code=resolved_code,
            details=details,
        )
        if error_code is not None and error_code not in ErrorCode._value2member_map_:
            self.error_code = error_code  # type: ignore[assignment]
        self.exit_code = 1
        self.http_status = 501


class AuthenticationError(SourceError):
    """Raised when credential resolution fails or secret reference is missing."""

    error_code: ErrorCode = ErrorCode.UNAUTHENTICATED
    exit_code: int = 7
    http_status: int = 401

    def __init__(
        self,
        message: str,
        *,
        error_code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            error_code=error_code or ErrorCode.UNAUTHENTICATED,
            details=details,
        )


__all__ = [
    "ArchiveSecurityViolationError",
    "AuthenticationError",
    "DocumentExtractionError",
    "DocumentPayloadTooLargeError",
    "FormatError",
    "InvalidDocumentError",
    "OCRExecutionError",
    "OCRUnavailableError",
    "OfficeConversionUnavailableError",
    "PathContainmentError",
    "PayloadTooLargeError",
    "SourceEmptyError",
    "SourceError",
    "SourceLoadError",
    "SourceNotFoundError",
    "SourceTableEmptyError",
    "UnsupportedFormatError",
]
