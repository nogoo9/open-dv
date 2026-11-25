"""Architecture test verifying V2 1-to-1 error mapping across CLI exit codes
and REST HTTP statuses (`schemas/v2/error-codes.json`).
"""

from __future__ import annotations

import json

import pytest

from opendv.models import errors as v2_errors
from tests.architecture.conftest import REPO_ROOT

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
