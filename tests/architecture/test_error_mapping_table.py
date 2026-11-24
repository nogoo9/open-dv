"""Architecture test verifying 1-to-1 error mapping across CLI exit codes
and REST statuses (T13).
"""

from __future__ import annotations

import json

import pytest

from opendv.models import errors as v2_errors
from opendv_contracts.errors import (
    VALID_EXIT_CODES,
    ErrorCode,
    ErrorMappingEntry,
    ProblemDetail,
    create_problem_detail,
    get_error_entry,
    get_error_mapping,
)
from tests.architecture.conftest import REPO_ROOT


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


_V2_CATEGORIES = {"Syntax", "Preconditions", "Resolution", "Verification", "Execution"}


@pytest.mark.architecture
@pytest.mark.parametrize("code", list(v2_errors.ErrorCode))
def test_v2_every_error_code_has_normative_mapping(code: v2_errors.ErrorCode) -> None:
    """Assert every V2 ErrorCode has a valid category, exit_code, http_status, and URI."""
    exit_code, http_status = v2_errors.get_error_mapping(code)
    assert exit_code in v2_errors.VALID_EXIT_CODES
    if exit_code == 40:
        assert code == v2_errors.ErrorCode.REMOTE_BIND_UNAUTHENTICATED
        assert http_status is None
    else:
        assert isinstance(http_status, int)
        assert 200 <= http_status <= 599

    entry = v2_errors.get_error_entry(code)
    assert entry.error_code == code
    assert entry.category in _V2_CATEGORIES
    assert entry.problem_title
    expected_uri = (
        "urn:opendv:ok"
        if code == v2_errors.ErrorCode.OK
        else f"urn:opendv:error:{code.lower().replace('_', '-')}"
    )
    assert entry.problem_type_uri == expected_uri


@pytest.mark.architecture
def test_v2_error_catalog_json_parity() -> None:
    """Assert 34 V2 ErrorCodes, 8 CliExitCodes, and 1-to-1 parity with schemas/v2/error-codes.json."""
    assert len(v2_errors.ErrorCode) == 34
    assert len(v2_errors.CliExitCode) == 8
    expected_exit_codes = frozenset({0, 1, 2, 3, 4, 5, 40, 50})
    assert expected_exit_codes == v2_errors.VALID_EXIT_CODES

    catalog_path = REPO_ROOT / "schemas" / "v2" / "error-codes.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert catalog["catalog_version"] == "2.0.0"
    assert catalog["total_error_codes"] == 34
    assert len(catalog["error_codes"]) == 34
    assert catalog == v2_errors.export_error_codes_catalog()

    by_code = {row["code"]: row for row in catalog["error_codes"]}
    assert set(by_code) == {str(c) for c in v2_errors.ErrorCode}

    for code in v2_errors.ErrorCode:
        entry = v2_errors.get_error_entry(code)
        row = by_code[str(code)]
        assert row["category"] == entry.category
        assert row["http_status"] == entry.http_status
        assert row["exit_code"] == int(entry.exit_code)
        assert row["problem_title"] == entry.problem_title
        assert row["problem_type_uri"] == entry.problem_type_uri
        assert row["waivable"] == entry.waivable
        assert row["rationale_required"] == entry.rationale_required
        assert row["description"] == entry.description


@pytest.mark.architecture
def test_v2_problem_details_http_and_cli_only_codes() -> None:
    """Assert RFC 9457 ProblemDetails construction for HTTP and CLI-only V2 codes."""
    http_problem = v2_errors.create_problem_details(
        v2_errors.ErrorCode.RESOURCE_NOT_FOUND,
        detail="Project 'am-ibor-2026' not found",
        instance="/api/v1/projects/am-ibor-2026",
    )
    assert isinstance(http_problem, v2_errors.ProblemDetails)
    assert http_problem.status == 404
    assert http_problem.type == "urn:opendv:error:resource-not-found"
    assert http_problem.title == "Resource Not Found"
    assert http_problem.category == "Resolution"

    cli_only_problem = v2_errors.create_problem_details(
        v2_errors.ErrorCode.REMOTE_BIND_UNAUTHENTICATED,
        detail="Refusing non-loopback bind without --allow-remote-unauthenticated",
    )
    assert cli_only_problem.status is None
    assert cli_only_problem.error_code == v2_errors.ErrorCode.REMOTE_BIND_UNAUTHENTICATED
    assert cli_only_problem.category == "Preconditions"
