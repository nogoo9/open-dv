"""Typed exception hierarchy for Open-DV data source and format adapters."""

from __future__ import annotations

from typing import Any

from opendv_contracts.errors import ErrorCode, NotFoundError, OpenDVError
from opendv_core.paths import PathContainmentError


class SourceError(OpenDVError):
    """Base exception for all opendv-sources errors."""

    error_code: ErrorCode = ErrorCode.SOURCE_LOAD_ERROR
    exit_code: int = 2
    http_status: int = 422


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
    http_status: int = 415

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


class AuthenticationError(SourceError):
    """Raised when credential resolution fails or secret reference is missing."""

    error_code: ErrorCode = ErrorCode.INVALID_REQUEST
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
            error_code=error_code or ErrorCode.INVALID_REQUEST,
            details=details,
        )
        self.http_status = 401


__all__ = [
    "AuthenticationError",
    "DocumentExtractionError",
    "FormatError",
    "PathContainmentError",
    "SourceEmptyError",
    "SourceError",
    "SourceLoadError",
    "SourceNotFoundError",
    "SourceTableEmptyError",
    "UnsupportedFormatError",
]
