"""Unit tests verifying error taxonomy and RFC 9457 ProblemDetail mapping."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from opendv_contracts.errors import (
    ErrorCode,
    ErrorDetail,
    ProblemDetail,
    create_problem_detail,
    get_error_entry,
    get_error_mapping,
)


def test_every_error_code_has_normative_mapping() -> None:
    for code in ErrorCode:
        exit_code, http_status = get_error_mapping(code)
        assert exit_code in (0, 1, 2)
        assert 200 <= http_status <= 599

        entry = get_error_entry(code)
        assert entry.error_code == code
        assert entry.exit_code == exit_code
        assert entry.http_status == http_status
        assert entry.problem_title
        assert entry.problem_type_uri.startswith("urn:opendv:")


def test_specific_normative_mappings() -> None:
    assert get_error_mapping(ErrorCode.OK) == (0, 200)
    assert get_error_mapping(ErrorCode.VERIFICATION_FAILED) == (1, 200)
    assert get_error_mapping(ErrorCode.PATH_CONTAINMENT_ERROR) == (2, 400)
    assert get_error_mapping(ErrorCode.INVALID_REQUEST) == (2, 400)
    assert get_error_mapping(ErrorCode.RESOURCE_NOT_FOUND) == (2, 404)
    assert get_error_mapping(ErrorCode.PAYLOAD_TOO_LARGE) == (2, 413)
    assert get_error_mapping(ErrorCode.UNSUPPORTED_FORMAT) == (2, 415)
    assert get_error_mapping(ErrorCode.INPUT_VALIDATION_ERROR) == (2, 422)
    assert get_error_mapping(ErrorCode.CAS_INTEGRITY_FAILURE) == (2, 500)
    assert get_error_mapping(ErrorCode.INTERNAL_ERROR) == (2, 500)
    assert get_error_mapping(ErrorCode.API_UNREACHABLE) == (2, 503)
    assert get_error_mapping(ErrorCode.PROVIDER_UNAVAILABLE) == (2, 503)


def test_create_problem_detail() -> None:
    problem = create_problem_detail(
        code=ErrorCode.INPUT_VALIDATION_ERROR,
        detail="Attribute 'grain_keys' cannot be empty for Hub entities",
        instance="/api/v1/verify",
        invalid_params=[
            ErrorDetail(
                field="grain_keys", message="Empty list not permitted", error_type="value_error"
            )
        ],
        extensions={"rule_id": "DV-GRAIN-001"},
    )

    assert problem.status == 422
    assert problem.type == "urn:opendv:input-validation-error"
    assert problem.title == "Input Validation Error"
    assert problem.error_code == ErrorCode.INPUT_VALIDATION_ERROR
    assert len(problem.invalid_params) == 1
    assert problem.invalid_params[0].field == "grain_keys"
    assert problem.extensions["rule_id"] == "DV-GRAIN-001"


def test_problem_detail_forbid_extra() -> None:
    with pytest.raises(ValidationError):
        ProblemDetail(
            type="about:blank",
            title="Bad",
            status=400,
            detail="Error",
            error_code=ErrorCode.INVALID_REQUEST,
            unrecognized_field="illegal",  # extra='forbid'
        )
