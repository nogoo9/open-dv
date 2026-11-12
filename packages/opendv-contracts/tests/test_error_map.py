"""Unit tests verifying error taxonomy and RFC 9457 ProblemDetail mapping."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from opendv_contracts.errors import (
    VALID_EXIT_CODES,
    ErrorCode,
    ErrorDetail,
    OpenDVError,
    ProblemDetail,
    create_problem_detail,
    get_error_entry,
    get_error_mapping,
)


def test_every_error_code_has_normative_mapping() -> None:
    for code in ErrorCode:
        exit_code, http_status = get_error_mapping(code)
        assert exit_code in VALID_EXIT_CODES
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
    assert get_error_mapping(ErrorCode.UNSUPPORTED_FORMAT) == (2, 400)
    assert get_error_mapping(ErrorCode.INPUT_VALIDATION_ERROR) == (2, 422)
    assert get_error_mapping(ErrorCode.CAS_INTEGRITY_FAILURE) == (2, 500)
    assert get_error_mapping(ErrorCode.INTERNAL_ERROR) == (2, 500)
    assert get_error_mapping(ErrorCode.API_UNREACHABLE) == (2, 503)
    assert get_error_mapping(ErrorCode.PROVIDER_UNAVAILABLE) == (2, 503)

    # Studio & Sources capability / governance / RBAC mappings
    assert get_error_mapping(ErrorCode.OCR_UNAVAILABLE) == (1, 501)
    assert get_error_mapping(ErrorCode.OFFICE_CONVERSION_UNAVAILABLE) == (1, 501)
    assert get_error_mapping(ErrorCode.JOB_ABANDONED_SERVER_CRASH) == (1, 500)
    assert get_error_mapping(ErrorCode.REFERENCE_ONTOLOGY_CORRUPT) == (1, 500)
    assert get_error_mapping(ErrorCode.INVALID_PRECONDITION_HEADER) == (2, 400)
    assert get_error_mapping(ErrorCode.REMOTE_SERVER_PATH_FORBIDDEN) == (2, 400)
    assert get_error_mapping(ErrorCode.FILE_NOT_FOUND) == (3, 404)
    assert get_error_mapping(ErrorCode.SOURCE_NOT_FOUND) == (3, 404)
    assert get_error_mapping(ErrorCode.QUESTION_NOT_FOUND) == (3, 404)
    assert get_error_mapping(ErrorCode.IMPORT_DELTA_REPLAY_HASH_MISMATCH) == (4, 422)
    assert get_error_mapping(ErrorCode.UNGROUNDED_SEED_MAPPING) == (4, 422)
    assert get_error_mapping(ErrorCode.TAXONOMY_CYCLE_DETECTED) == (4, 422)
    assert get_error_mapping(ErrorCode.CANNOT_DELETE_PROMOTED_SESSION) == (6, 409)
    assert get_error_mapping(ErrorCode.INSECURE_TRANSPORT_REFUSED) == (7, 403)
    assert get_error_mapping(ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN) == (7, 403)
    assert get_error_mapping(ErrorCode.PROPOSAL_SELF_ACCEPTANCE_FORBIDDEN) == (7, 403)


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


def test_opendv_error_to_problem_detail_honors_instance_http_status() -> None:
    err = OpenDVError("OCR engine missing", error_code=ErrorCode.OCR_UNAVAILABLE)
    assert err.exit_code == 1
    assert err.http_status == 501
    assert err.to_problem_detail().status == 501

    custom_err = OpenDVError("Custom status override", error_code=ErrorCode.INVALID_REQUEST)
    custom_err.http_status = 422
    assert custom_err.to_problem_detail().status == 422


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


def test_error_codes_json_exit_2_doc_and_three_way_parity() -> None:
    """Regression P02a::MAJ-01 & P02c::MAJ-01: Verify exit 2 documentation & 3-way parity."""
    import json
    from pathlib import Path

    from opendv_contracts.errors import ERROR_MAPPINGS

    repo_root = Path(__file__).resolve().parents[3]
    catalog = json.loads((repo_root / "schemas" / "v1" / "error-codes.json").read_text("utf-8"))
    exit_2_doc = catalog["taxonomy_rules"]["exit_codes"]["2"]
    assert "Frozen Wave-1 Legacy Contract Failure" in exit_2_doc
    assert "404" in exit_2_doc
    assert "cli-spec.json" in exit_2_doc

    json_entries = {e["code"]: e for e in catalog["error_codes"]}
    assert set(json_entries) == {c.value for c in ErrorCode} == {c.value for c in ERROR_MAPPINGS}
    assert json_entries["UNSUPPORTED_FORMAT"]["exit_code"] == 2
    assert json_entries["UNSUPPORTED_FORMAT"]["http_status"] == 400
    assert "UNSUPPORTED_FORMAT" in (ErrorCode.__doc__ or "")
