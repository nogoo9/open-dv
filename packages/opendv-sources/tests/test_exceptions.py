"""Unit tests for opendv-sources exception hierarchy and RFC 9457 mapping."""

from __future__ import annotations

from opendv_contracts.errors import ErrorCode, get_error_mapping
from opendv_sources.exceptions import (
    AuthenticationError,
    InvalidDocumentError,
    OCRExecutionError,
    OCRUnavailableError,
    OfficeConversionUnavailableError,
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
    """Verify UnsupportedFormatError maps to 400 HTTP status and UNSUPPORTED_FORMAT code."""
    err = UnsupportedFormatError(
        "Unsupported source adapter 'kafka'. Installed: ['file', 'inline']",
        requested="kafka",
        available=["file", "inline"],
    )
    expected_exit, expected_http = get_error_mapping(ErrorCode.UNSUPPORTED_FORMAT)
    assert UnsupportedFormatError.http_status == 400
    assert err.error_code == ErrorCode.UNSUPPORTED_FORMAT
    assert err.http_status == expected_http == 400
    assert err.exit_code == expected_exit
    assert err.details["requested"] == "kafka"
    assert err.details["available"] == ["file", "inline"]

    problem = err.to_problem_detail()
    assert problem.status == expected_http
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
    """Verify AuthenticationError maps to 401 HTTP status, exit 7, and UNAUTHENTICATED code."""
    err = AuthenticationError("Credential reference 'secret:db_pass' not found in vault")
    assert err.error_code == ErrorCode.UNAUTHENTICATED
    assert err.http_status == 401
    assert err.exit_code == 7

    problem = err.to_problem_detail()
    assert problem.status == 401
    assert problem.error_code == ErrorCode.UNAUTHENTICATED


def test_ocr_and_office_conversion_unavailable_errors() -> None:
    """Verify OCRUnavailableError and OfficeConversionUnavailableError map to HTTP 501 / exit 1."""
    ocr_err = OCRUnavailableError("Tesseract binary missing")
    assert ocr_err.error_code == ErrorCode.OCR_UNAVAILABLE
    assert ocr_err.exit_code == 1
    assert ocr_err.http_status == 501
    ocr_problem = ocr_err.to_problem_detail()
    assert ocr_problem.status == 501
    assert ocr_problem.error_code == ErrorCode.OCR_UNAVAILABLE

    office_err = OfficeConversionUnavailableError("soffice binary missing")
    assert office_err.error_code == ErrorCode.OFFICE_CONVERSION_UNAVAILABLE
    assert office_err.exit_code == 1
    assert office_err.http_status == 501
    office_problem = office_err.to_problem_detail()
    assert office_problem.status == 501
    assert office_problem.error_code == ErrorCode.OFFICE_CONVERSION_UNAVAILABLE


def test_invalid_document_and_ocr_execution_errors() -> None:
    """Verify InvalidDocumentError and OCRExecutionError map to HTTP 422 / exit 2."""
    doc_err = InvalidDocumentError("Corrupt YAML payload")
    assert doc_err.error_code == ErrorCode.INPUT_VALIDATION_ERROR
    assert doc_err.exit_code == 2
    assert doc_err.http_status == 422
    assert doc_err.to_problem_detail().status == 422

    ocr_exec_err = OCRExecutionError("Tesseract crashed with exit code 1")
    assert ocr_exec_err.error_code == ErrorCode.SOURCE_LOAD_ERROR
    assert ocr_exec_err.exit_code == 2
    assert ocr_exec_err.http_status == 422
    assert ocr_exec_err.to_problem_detail().status == 422


def test_path_containment_error_reexport() -> None:
    """Verify PathContainmentError is re-exported from opendv_core.paths."""
    from opendv_core.paths import PathContainmentError as CorePathError

    assert PathContainmentError is CorePathError
