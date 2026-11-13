"""Architecture test verifying 1-to-1 error mapping across CLI exit codes
and REST statuses (T13).
"""

from __future__ import annotations

import pytest

from opendv_contracts.errors import (
    VALID_EXIT_CODES,
    ErrorCode,
    ErrorMappingEntry,
    ProblemDetail,
    create_problem_detail,
    get_error_entry,
    get_error_mapping,
)


@pytest.mark.architecture
def test_every_error_code_has_normative_mapping_entry() -> None:
    """Assert that every defined ErrorCode has an authoritative mapping entry."""
    for code in ErrorCode:
        exit_code, http_status = get_error_mapping(code)
        assert exit_code in VALID_EXIT_CODES, f"Invalid exit code {exit_code} for {code}"
        assert 200 <= http_status <= 599, f"Invalid HTTP status {http_status} for {code}"

        entry = get_error_entry(code)
        assert isinstance(entry, ErrorMappingEntry)
        assert entry.error_code == code
        assert entry.exit_code == exit_code
        assert entry.http_status == http_status
        assert entry.problem_title, f"Missing problem title for {code}"
        assert entry.problem_type_uri.startswith("urn:opendv:"), (
            f"Invalid problem type URI: {entry.problem_type_uri}"
        )


@pytest.mark.architecture
def test_authoritative_error_mappings() -> None:
    """Assert key normative error mappings against architectural specification."""
    expected_mappings = {
        ErrorCode.OK: (0, 200),
        ErrorCode.VERIFICATION_FAILED: (1, 200),
        ErrorCode.PATH_CONTAINMENT_ERROR: (2, 400),
        ErrorCode.INVALID_REQUEST: (2, 400),
        ErrorCode.RESOURCE_NOT_FOUND: (2, 404),
        ErrorCode.PAYLOAD_TOO_LARGE: (2, 413),
        ErrorCode.UNSUPPORTED_FORMAT: (2, 400),
        ErrorCode.OCR_UNAVAILABLE: (1, 501),
        ErrorCode.OFFICE_CONVERSION_UNAVAILABLE: (1, 501),
        ErrorCode.CANNOT_DELETE_PROMOTED_SESSION: (6, 409),
        ErrorCode.INPUT_VALIDATION_ERROR: (2, 422),
        ErrorCode.CAS_INTEGRITY_FAILURE: (2, 500),
        ErrorCode.INTERNAL_ERROR: (2, 500),
        ErrorCode.API_UNREACHABLE: (2, 503),
        ErrorCode.PROVIDER_UNAVAILABLE: (2, 503),
    }

    for code, (expected_exit, expected_http) in expected_mappings.items():
        exit_code, http_status = get_error_mapping(code)
        assert (exit_code, http_status) == (expected_exit, expected_http), (
            f"Error mapping for {code} was ({exit_code}, {http_status}), "
            f"expected ({expected_exit}, {expected_http})"
        )


@pytest.mark.architecture
def test_rfc9457_problem_detail_generation() -> None:
    """Assert deterministic RFC 9457 ProblemDetail generation from error codes."""
    problem = create_problem_detail(
        code=ErrorCode.PATH_CONTAINMENT_ERROR,
        detail="Path traversal forbidden",
        instance="/api/v1/verify",
    )
    assert isinstance(problem, ProblemDetail)
    assert problem.status == 400
    assert problem.type == "urn:opendv:path-containment-error"
    assert problem.title == "Path Containment Violation"
    assert problem.error_code == ErrorCode.PATH_CONTAINMENT_ERROR
    assert problem.detail == "Path traversal forbidden"
    assert problem.instance == "/api/v1/verify"
