"""Unit tests for opendv-sources exception hierarchy and RFC 9457 mapping."""

from __future__ import annotations

from opendv_contracts.errors import ErrorCode
from opendv_sources.exceptions import (
    AuthenticationError,
    PathContainmentError,
    SourceError,
    SourceNotFoundError,
    UnsupportedFormatError,
)


def test_source_error_defaults() -> None:
    """Verify base SourceError has 422 HTTP status and SOURCE_LOAD_ERROR code."""
    err = SourceError("General source failure")
    assert err.error_code == ErrorCode.SOURCE_LOAD_ERROR
    assert err.http_status == 422
    assert err.exit_code == 2

    problem = err.to_problem_detail()
    assert problem.status == 422
    assert problem.error_code == ErrorCode.SOURCE_LOAD_ERROR
    assert problem.detail == "General source failure"


def test_unsupported_format_error() -> None:
    """Verify UnsupportedFormatError maps to 415 HTTP status and UNSUPPORTED_FORMAT code."""
    err = UnsupportedFormatError(
        "Unsupported source adapter 'kafka'. Installed: ['file', 'inline']",
        requested="kafka",
        available=["file", "inline"],
    )
    assert err.error_code == ErrorCode.UNSUPPORTED_FORMAT
    assert err.http_status == 415
    assert err.exit_code == 2
    assert err.details["requested"] == "kafka"
    assert err.details["available"] == ["file", "inline"]

    problem = err.to_problem_detail()
    assert problem.status == 415
    assert problem.error_code == ErrorCode.UNSUPPORTED_FORMAT
    assert problem.extensions["requested"] == "kafka"


def test_source_not_found_error() -> None:
    """Verify SourceNotFoundError maps to 404 HTTP status and RESOURCE_NOT_FOUND code."""
    err = SourceNotFoundError("Source file 'feed.csv' does not exist")
    assert err.error_code == ErrorCode.RESOURCE_NOT_FOUND
    assert err.http_status == 404
    assert err.exit_code == 2

    problem = err.to_problem_detail()
    assert problem.status == 404
    assert problem.error_code == ErrorCode.RESOURCE_NOT_FOUND


def test_authentication_error() -> None:
    """Verify AuthenticationError maps to 401 HTTP status and INVALID_REQUEST code."""
    err = AuthenticationError("Credential reference 'secret:db_pass' not found in vault")
    assert err.error_code == ErrorCode.INVALID_REQUEST
    assert err.http_status == 401
    assert err.exit_code == 2


def test_path_containment_error_reexport() -> None:
    """Verify PathContainmentError is re-exported from opendv_core.paths."""
    from opendv_core.paths import PathContainmentError as CorePathError

    assert PathContainmentError is CorePathError
