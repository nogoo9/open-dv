"""Empirical Stress Test Harness for Phase P2 Contracts & Standalone CLI.

Covers:
1. opendv-contracts:
   - RFC 8785 JCS canonicalization with complex nested dicts, arrays, unicode, float representations, empty maps, key ordering.
   - Float oracle against Bun/JavaScript JSON.stringify.
   - ErrorCode taxonomy and ProblemDetail generation across all 22 error codes.
   - Pydantic DTOs with malformed, boundary, and extra inputs (extra="forbid", frozen=True, D1, D2, D3).
   - Validation of cli-spec.json against JSON Schema Draft 2020-12.
2. opendv-cli & opendv-cli-common:
   - In-process execution of all subcommands via LocalExecutor.
   - Strict RFC 8785 JCS validation of --json outputs.
   - Presenter formatting (--toon, text).
   - Adversarial error handling (missing flags, bad paths, syntax errors, forbidden options).
   - Subprocess execution asserting zero instances of fastapi, starlette, opendv_platform_api, httpx.
"""

from __future__ import annotations

import io
import json
import math
import random
import shutil
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from opendv_cli import app_main, axi_dv_main, build_parser
from opendv_cli.executor import LocalExecutor
from opendv_cli_common import (
    CommandExecutor,
    format_jcs,
    format_presenter_output,
    present_result,
    print_jcs,
)
from opendv_contracts import (
    ERROR_MAPPINGS,
    ArtifactMetadataResponse,
    ArtifactUploadRequest,
    CompileRequest,
    CompileResponse,
    CreateProjectRequest,
    ErrorCode,
    ErrorDetail,
    EventType,
    GateScorecardSummary,
    GateVerdict,
    HoldAction,
    HoldDecisionRequest,
    HoldDecisionResponse,
    HoldOptionCandidate,
    HoldRequest,
    HoldResponse,
    HoldStatus,
    PipelineEvent,
    PipelineRunHandle,
    PipelineRunRequest,
    PipelineValidateRequest,
    ProblemDetail,
    ProfileRequest,
    ProfileResponse,
    ProjectRunDTO,
    RbacRole,
    RenderedOutput,
    RuleEvaluationDTO,
    RunStatus,
    SandboxBatchResult,
    SandboxQueryRequest,
    SandboxQueryResponse,
    VerifyRequest,
    VerifyResponse,
    create_problem_detail,
    dumps_jcs,
    dumps_jcs_bytes,
    get_error_entry,
    get_error_mapping,
    load_cli_spec,
    load_cli_spec_schema,
    loads_jcs,
    validate_cli_spec,
)
from opendv_contracts.jcs import FloatDomainError, IntegerDomainError
from opendv_core import VaultObservabilityEvent
from opendv_service import set_pipeline_runner_override


@pytest.fixture(autouse=True)
def _clean_pipeline_runner_override() -> Generator[None, None, None]:
    yield
    set_pipeline_runner_override(None)


# ==============================================================================
# SUITE 1: RFC 8785 JCS CANONICALIZATION STRESS
# ==============================================================================


def test_jcs_float_oracle_against_bun() -> None:
    """Stress test float serialization against Bun/JS JSON.stringify oracle across 500+ floats."""
    if shutil.which("bun") is None:
        pytest.skip("bun is required for float oracle comparison")
    random.seed(1337)
    test_floats: list[float] = [
        0.0,
        -0.0,
        1.0,
        -1.0,
        1.5,
        -1.5,
        12.8,
        -12.8,
        100.0,
        -100.0,
        1e20,
        -1e20,
        1e21,
        -1e21,
        1e-5,
        -1e-5,
        1e-6,
        -1e-6,
        1e-7,
        -1e-7,
        1.5e-5,
        -1.5e-5,
        1.5e-6,
        -1.5e-6,
        1.5e-7,
        -1.5e-7,
        1.23456789e18,
        -1.23456789e18,
        1.23456789e-5,
        1.23456789e-6,
        1.23456789e-7,
        0.1,
        0.2,
        0.3,
        0.1 + 0.2,
        1 / 3,
        1 / 7,
        math.pi,
        math.e,
        2.2250738585072014e-308,
        1.7976931348623157e308,
        5e-324,
        1.0000000000000002e16,
        1.0000000000000002e17,
        1.0000000000000002e18,
        1.0000000000000002e19,
        1.0000000000000002e20,
    ]

    # Exponents from -300 to 300
    for exp in range(-300, 305, 10):
        for m in [1.0, 1.25, 9.5, 4.321, 1.0000000000000002]:
            test_floats.append(float(f"{m}e{exp}"))
            test_floats.append(float(f"{-m}e{exp}"))

    # Random floats
    for _ in range(150):
        test_floats.append(random.uniform(-1e10, 1e10))
        test_floats.append(random.uniform(-1e-10, 1e-10))

    # Compute Python JCS serialization
    py_results = [dumps_jcs(x) for x in test_floats]

    # Oracle query to Bun
    bun_payload = json.dumps(test_floats)
    proc = subprocess.run(
        [
            "bun",
            "-e",
            "console.log(JSON.stringify(JSON.parse(process.argv[1]).map(x => JSON.stringify(x))))",
            bun_payload,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    js_results = json.loads(proc.stdout.strip())

    mismatches: list[tuple[float, str, str]] = []
    for f, py_s, js_s in zip(test_floats, py_results, js_results):
        if py_s != js_s:
            mismatches.append((f, py_s, js_s))

    assert not mismatches, f"JCS float mismatches against Bun: {mismatches[:5]}"


def test_jcs_integer_range_and_domain_errors() -> None:
    """Stress test integer range: [-(2^53 - 1), 2^53 - 1] and domain errors outside."""
    max_safe = 2**53 - 1
    min_safe = -(2**53 - 1)

    assert dumps_jcs(max_safe) == "9007199254740991"
    assert dumps_jcs(min_safe) == "-9007199254740991"
    assert dumps_jcs(0) == "0"
    assert dumps_jcs(1) == "1"
    assert dumps_jcs(-1) == "-1"

    # Out of domain integers
    with pytest.raises(IntegerDomainError):
        dumps_jcs(max_safe + 1)
    with pytest.raises(IntegerDomainError):
        dumps_jcs(min_safe - 1)
    with pytest.raises(IntegerDomainError):
        dumps_jcs(10**50)
    with pytest.raises(IntegerDomainError):
        dumps_jcs(-(10**50))

    # Float domain errors
    with pytest.raises(FloatDomainError):
        dumps_jcs(float("nan"))
    with pytest.raises(FloatDomainError):
        dumps_jcs(float("inf"))
    with pytest.raises(FloatDomainError):
        dumps_jcs(float("-inf"))


def test_jcs_unicode_escaping_and_surrogates() -> None:
    """Stress test unicode non-escaping and control character escaping per RFC 8785."""
    # Control chars (0x00 to 0x1F) must be escaped
    for code in range(32):
        ch = chr(code)
        s = f"prefix_{ch}_suffix"
        serialized = dumps_jcs(s)
        # Verify valid JSON
        assert json.loads(serialized) == s

    # Escaping \b, \f, \n, \r, \t, \\, \"
    assert dumps_jcs('\b\f\n\r\t\\"') == '"\\b\\f\\n\\r\\t\\\\\\""'

    # Forward slash MUST NOT be escaped per RFC 8785
    assert (
        dumps_jcs("https://example.com/api/v1/runs?query=a/b")
        == '"https://example.com/api/v1/runs?query=a/b"'
    )

    # Non-ASCII unicode must NOT be escaped
    multilingual = "中文, 日本語, 한국어, עברית, العربية, Русский, Français, Español, 🚀, 👨‍👩‍👦"
    assert dumps_jcs(multilingual) == f'"{multilingual}"'


def test_jcs_utf16_code_unit_key_sorting() -> None:
    """Stress test key sorting according to UTF-16 code units (RFC 8785 §3.2.3)."""
    # Supplementary character U+10000 (0xD800 0xDC00 in UTF-16) vs BMP character U+FFFF (0xFFFF)
    # In UTF-16: 0xD800 < 0xFFFF, so U+10000 must sort BEFORE U+FFFF!
    # In UTF-8: U+10000 is \xF0\x90\x80\x80, U+FFFF is \xEF\xBF\xBF. In UTF-8, \xEF < \xF0, so U+FFFF would be first.
    # JCS requires UTF-16 order!
    d = {"\uffff": 1, "\U00010000": 2}
    expected = '{"\U00010000":2,"\uffff":1}'
    assert dumps_jcs(d) == expected

    # Test key sorting oracle against Bun/JS
    keys = [
        "z",
        "a",
        "1",
        "10",
        "2",
        "_",
        "A",
        "b",
        "\u00e9",  # é
        "\u4e2d",  # 中
        "\U0001f600",  # 😀
        "\ufffe",
        "\u0000",
        "\u007f",
        "alpha",
        "alphabet",
        "alp",
    ]
    test_dict = {k: idx for idx, k in enumerate(keys)}
    py_jcs = dumps_jcs(test_dict)

    # Bun oracle for key sorting
    if shutil.which("bun") is None:
        pytest.skip("bun is required for utf16 key sorting comparison")
    proc = subprocess.run(
        [
            "bun",
            "-e",
            """
            const keys = JSON.parse(process.argv[1]);
            const d = {};
            for (let i = 0; i < keys.length; i++) d[keys[i]] = i;
            // Sort keys by UTF-16 code units
            const sortedKeys = [...keys].sort();
            const pairs = sortedKeys.map(k => JSON.stringify(k) + ':' + d[k]);
            process.stdout.write('{' + pairs.join(',') + '}');
            """,
            json.dumps(keys),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    js_jcs = proc.stdout.strip()
    assert py_jcs == js_jcs


def test_jcs_deep_nesting_and_roundtrip() -> None:
    """Stress test deep nesting (100 levels) and complex object collections."""
    curr: dict[str, Any] = {"leaf": [1, True, False, None, "value", 12.5, {}]}
    for i in range(100):
        curr = {f"level_{i:03d}": curr, "sibling": []}

    jcs_str = dumps_jcs(curr)
    assert jcs_str.startswith("{")
    assert jcs_str.endswith("}")
    # Roundtrip
    parsed = loads_jcs(jcs_str)
    jcs_again = dumps_jcs(parsed)
    assert jcs_str == jcs_again

    # Bytes roundtrip
    jcs_bytes = dumps_jcs_bytes(curr)
    assert isinstance(jcs_bytes, bytes)
    assert loads_jcs(jcs_bytes) == parsed


# ==============================================================================
# SUITE 2: ERRORCODE TAXONOMY & RFC 9457 PROBLEMDETAIL STRESS
# ==============================================================================


def test_error_code_taxonomy_exhaustive_coverage() -> None:
    """Verify all ErrorCode enum members have complete and valid mappings."""
    assert len(ErrorCode) == len(ERROR_MAPPINGS)

    valid_exit_codes = {0, 1, 2}
    valid_http_statuses = {200, 400, 404, 413, 415, 422, 500, 503}

    for code in ErrorCode:
        # 1. Present in ERROR_MAPPINGS
        assert code in ERROR_MAPPINGS, f"ErrorCode.{code.name} missing from ERROR_MAPPINGS"
        entry = get_error_entry(code)
        assert entry.error_code == code
        assert entry.exit_code in valid_exit_codes, (
            f"Invalid exit code {entry.exit_code} for {code}"
        )
        assert entry.http_status in valid_http_statuses, (
            f"Invalid HTTP status {entry.http_status} for {code}"
        )
        assert entry.problem_title, f"Missing title for {code}"
        assert entry.problem_type_uri.startswith("urn:opendv:"), (
            f"Bad type URI {entry.problem_type_uri} for {code}"
        )

        # 2. get_error_mapping helper
        exit_code, http_status = get_error_mapping(code)
        assert exit_code == entry.exit_code
        assert http_status == entry.http_status

        # 3. create_problem_detail helper
        pd = create_problem_detail(
            code,
            detail=f"Stress test error detail for {code.value}",
            instance=f"/test/{code.value.lower()}",
            invalid_params=[
                ErrorDetail(field="param1", message="invalid value", error_type="value_error")
            ],
            extensions={"debug_trace": "trace_12345", "counter": 42},
        )
        assert pd.error_code == code
        assert pd.status == entry.http_status
        assert pd.title == entry.problem_title
        assert pd.type == entry.problem_type_uri
        assert pd.instance == f"/test/{code.value.lower()}"
        assert len(pd.invalid_params) == 1
        assert pd.extensions["counter"] == 42

        # 4. Canonical JCS serialization of ProblemDetail
        jcs_out = dumps_jcs(pd)
        assert f'"error_code":"{code.value}"' in jcs_out
        assert f'"status":{entry.http_status}' in jcs_out

        # 5. Roundtrip validation
        pd_deser = ProblemDetail.model_validate_json(jcs_out)
        assert pd_deser == pd


def test_problem_detail_boundary_and_extra_forbid() -> None:
    """Stress test boundary validation and extra="forbid" on ProblemDetail."""
    # Extra fields must be rejected
    with pytest.raises(ValidationError):
        ProblemDetail(
            title="Error",
            status=400,
            detail="Detail",
            error_code=ErrorCode.INVALID_REQUEST,
            unauthorized_field="malicious",  # type: ignore
        )

    # Status bounds: ge=200, le=599
    with pytest.raises(ValidationError):
        ProblemDetail(
            title="Error",
            status=199,
            detail="Detail",
            error_code=ErrorCode.INVALID_REQUEST,
        )
    with pytest.raises(ValidationError):
        ProblemDetail(
            title="Error",
            status=600,
            detail="Detail",
            error_code=ErrorCode.INVALID_REQUEST,
        )


# ==============================================================================
# SUITE 3: PYDANTIC DTOS MALFORMED & BOUNDARY STRESS
# ==============================================================================


def test_dtos_extra_field_forbid_across_all_models() -> None:
    """Assert extra='forbid' raises ValidationError on every contract DTO."""
    models_and_valid_args: list[tuple[type, dict[str, Any]]] = [
        (ProfileRequest, {"source_system": "ERP", "table_name": "cust"}),
        (
            ProfileResponse,
            {
                "status": "PASS",
                "profile_id": "p-1",
                "table_count": 1,
                "column_count": 2,
                "source_system": "ERP",
                "table_name": "cust",
            },
        ),
        (CompileRequest, {"contract_path": "contract.yaml"}),
        (
            CompileResponse,
            {
                "run_id": "r-1",
                "status": "PASS",
                "canonical_ir_sha256": "0" * 64,
                "hubs_compiled": 1,
                "links_compiled": 1,
            },
        ),
        (VerifyRequest, {"contract_path": "contract.yaml"}),
        (
            VerifyResponse,
            {
                "run_id": "r-1",
                "status": "PASS",
                "verdict": GateVerdict.PASS,
                "overall_passed": True,
                "gate_scorecard": GateScorecardSummary(),
            },
        ),
        (SandboxQueryRequest, {"contract_path": "c.yaml"}),
        (
            SandboxQueryResponse,
            {
                "run_id": "r-1",
                "query_type": "TEST",
                "assertion_cutoff_ts": "2026-11-04 00:00:00",
                "all_batches_passed": True,
            },
        ),
        (PipelineRunRequest, {"input_spec_path": "spec.yaml"}),
        (
            PipelineRunHandle,
            {
                "run_id": "r-1",
                "status": RunStatus.RUNNING,
                "created_at_utc": "2026-11-04T00:00:00Z",
                "event_stream_url": "/events",
                "status_url": "/status",
            },
        ),
        (
            PipelineEvent,
            {
                "event_id": "e-1",
                "run_id": "r-1",
                "timestamp_utc": "2026-11-04T00:00:00Z",
                "stage_id": "S1",
                "event_type": EventType.STAGE_STARTED,
            },
        ),
        (HoldRequest, {"run_id": "r-1", "stage_id": "S1", "reason": "test"}),
        (
            HoldDecisionRequest,
            {
                "run_id": "r-1",
                "hold_id": "h-1",
                "action": HoldAction.APPROVE,
                "resolution_notes": "notes",
                "steward_id": "steward-1",
            },
        ),
        (CreateProjectRequest, {"project_id": "p-1", "name": "Project"}),
        (
            ArtifactMetadataResponse,
            {
                "artifact_id": "a-1",
                "run_id": "r-1",
                "artifact_type": "ir",
                "filename": "ir.json",
                "size_bytes": 100,
                "sha256": "f" * 64,
                "created_at_utc": "2026-11-04T00:00:00Z",
            },
        ),
    ]

    for model_cls, valid_args in models_and_valid_args:
        # Normal instantiation succeeds
        inst = model_cls(**valid_args)
        assert inst is not None

        # Instantiation with extra field must raise ValidationError
        bad_args = dict(valid_args)
        bad_args["__forbidden_extra_attribute__"] = "illegal_value"
        with pytest.raises(ValidationError, match="extra_forbidden"):
            model_cls(**bad_args)


def test_dtos_immutability_frozen() -> None:
    """Assert frozen=True prevents attribute reassignment on contract models."""
    req = ProfileRequest(source_system="ERP", table_name="cust")
    with pytest.raises(ValidationError):
        req.source_system = "NEW_ERP"  # type: ignore

    resp = CompileResponse(
        run_id="r-1",
        status="PASS",
        canonical_ir_sha256="a" * 64,
        hubs_compiled=1,
        links_compiled=1,
    )
    with pytest.raises(ValidationError):
        resp.hubs_compiled = 10  # type: ignore


def test_dtos_legacy_identifiers_purged_d3() -> None:
    """Assert Decision D3: legacy view identifiers are rejected across models."""
    forbidden_keys = ["work" + "bench_view_id", "viz" + "_id"]

    for model_cls in [
        PipelineEvent,
        PipelineRunHandle,
        VerifyResponse,
        ProfileResponse,
        CompileResponse,
        ProjectRunDTO,
        VaultObservabilityEvent,
    ]:
        for forbidden in forbidden_keys:
            assert forbidden not in model_cls.model_fields, (
                f"{forbidden} found in {model_cls.__name__}"
            )

    assert "current_view_id" not in ProjectRunDTO.model_fields


def test_dtos_inline_payloads_d1() -> None:
    """Assert Decision D1: models accept inline file contents and client_path."""
    prof = ProfileRequest(
        file_content="a,b,c\n1,2,3",
        client_path="/custom/path/file.csv",
        source_system="ERP",
    )
    assert prof.file_content == "a,b,c\n1,2,3"
    assert prof.client_path == "/custom/path/file.csv"

    comp = CompileRequest(contract_content="version: 1.0\nhubs: []")
    assert comp.contract_content == "version: 1.0\nhubs: []"

    ver = VerifyRequest(
        contract_content="contract yaml",
        graph_a_content="ontology yaml",
        graph_b_content="profile json",
    )
    assert ver.contract_content == "contract yaml"
    assert ver.graph_a_content == "ontology yaml"
    assert ver.graph_b_content == "profile json"


def test_dtos_sha256_validation() -> None:
    """Assert 64-hex SHA-256 pattern validation on CompileResponse and ArtifactMetadataResponse."""
    # Valid 64-hex
    valid_sha = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    comp = CompileResponse(
        run_id="r-1",
        status="PASS",
        canonical_ir_sha256=valid_sha,
        hubs_compiled=1,
        links_compiled=1,
    )
    assert comp.canonical_ir_sha256 == valid_sha

    # Invalid SHA: too short
    with pytest.raises(ValidationError):
        CompileResponse(
            run_id="r-1",
            status="PASS",
            canonical_ir_sha256="abc123",
            hubs_compiled=1,
            links_compiled=1,
        )

    # Invalid SHA: non-hex characters
    with pytest.raises(ValidationError):
        CompileResponse(
            run_id="r-1",
            status="PASS",
            canonical_ir_sha256="g" * 64,
            hubs_compiled=1,
            links_compiled=1,
        )


# ==============================================================================
# SUITE 4: CLI SPECIFICATION & JSON SCHEMA DRAFT 2020-12
# ==============================================================================


def test_cli_spec_draft202012_schema_validation() -> None:
    """Validate cli-spec.json against Draft 2020-12 JSON Schema."""
    # Must validate cleanly without exception
    validate_cli_spec()

    spec = load_cli_spec()
    schema = load_cli_spec_schema()
    assert spec["spec_version"] == "1.0.0"
    assert spec["contract_version"] == "1.0.0"

    # Verify command inventory
    cmd_names = {c["name"] for c in spec["commands"]}
    expected_cmds = {"profile", "compile", "verify", "sandbox", "pipeline", "run", "hold"}
    assert expected_cmds.issubset(cmd_names)

    # Verify Decision D6: --api-url is NOT present
    for g_opt in spec.get("global_options", []):
        assert g_opt["flag"] != "--api-url", "--api-url found in global_options!"
    for cmd in spec.get("commands", []):
        for opt in cmd.get("options", []):
            assert opt["flag"] != "--api-url", f"--api-url found in command {cmd['name']}!"

    # Verify all error codes are mapped in spec error_map array
    error_map_entries = spec.get("error_map", [])
    assert len(error_map_entries) == len(ErrorCode)
    mapped_codes = {entry["error_code"] for entry in error_map_entries}
    for code in ErrorCode:
        assert code.value in mapped_codes, (
            f"ErrorCode.{code.value} not found in cli-spec.json error_map"
        )


# ==============================================================================
# SUITE 5: STANDALONE CLI EXECUTION & SUBPROCESS ISOLATION
# ==============================================================================


def test_standalone_cli_in_process_local_executor(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Execute standalone opendv commands in-process and verify RFC 8785 JSON output."""
    pe_contract = "examples/private_equity/dv_pe_model_contract.yaml"
    pe_graph_a = "examples/private_equity/pe_graph_a_ontology.yaml"
    pe_graph_b = "examples/private_equity/pe_graph_b_profile.json"
    pe_ir = "examples/private_equity/pe_canonical_ir.json"

    # 1. verify --json
    rc = app_main(
        [
            "verify",
            "--contract",
            pe_contract,
            "--graph-a",
            pe_graph_a,
            "--graph-b",
            pe_graph_b,
            "--json",
        ]
    )
    assert rc == 0
    raw_out = capsys.readouterr().out.strip()
    parsed = json.loads(raw_out)
    assert raw_out == dumps_jcs(parsed), "--json output does not strictly match RFC 8785 JCS!"
    assert parsed["overall_passed"] is True
    assert parsed["mode"] == "local"

    # 2. compile --json
    rc = app_main(["compile", "--contract", pe_contract, "--canonical-ir", pe_ir, "--json"])
    assert rc == 0
    raw_out = capsys.readouterr().out.strip()
    parsed = json.loads(raw_out)
    assert raw_out == dumps_jcs(parsed)
    assert parsed["status"] == "PASS"
    assert parsed["hubs_compiled"] >= 4

    # 3. profile --json with sample CSV
    csv_file = tmp_path / "test_deals.csv"
    csv_file.write_text("deal_id,deal_name,amount\nD-1,Acquisition,500000\n", encoding="utf-8")
    rc = app_main(
        [
            "profile",
            "--source-csv",
            str(csv_file),
            "--source-system",
            "deals_crm",
            "--table-name",
            "deals",
            "--json",
        ]
    )
    assert rc == 0
    raw_out = capsys.readouterr().out.strip()
    parsed = json.loads(raw_out)
    assert raw_out == dumps_jcs(parsed)
    assert parsed["table_count"] == 1
    assert parsed["column_count"] == 3

    # 4. sandbox --json
    rc = app_main(
        [
            "sandbox",
            "--contract",
            pe_contract,
            "--canonical-ir",
            pe_ir,
            "--query-type",
            "ADVERSARIAL_BATCHES_1_TO_4",
            "--json",
        ]
    )
    assert rc == 0
    raw_out = capsys.readouterr().out.strip()
    parsed = json.loads(raw_out)
    assert raw_out == dumps_jcs(parsed)
    assert parsed["all_batches_passed"] is True

    # 5. pipeline validate --json
    sample_spec = tmp_path / "input_spec.yaml"
    sample_spec.write_text(
        f"contract_path: {pe_contract}\ngraph_a_path: {pe_graph_a}\ngraph_b_path: {pe_graph_b}\n",
        encoding="utf-8",
    )
    rc = app_main(["pipeline", "validate", "--input", str(sample_spec), "--json"])
    assert rc in {0, 1}
    raw_out = capsys.readouterr().out.strip()
    parsed = json.loads(raw_out)
    assert raw_out == dumps_jcs(parsed)

    # 6. run list --json
    rc = app_main(["run", "list", "--project-id", "proj-apex-horizon-pe", "--json"])
    assert rc == 0
    raw_out = capsys.readouterr().out.strip()
    parsed = json.loads(raw_out)
    assert raw_out == dumps_jcs(parsed)
    assert parsed["total"] == 0

    # 7. hold list --json (run not found in clean unseeded state -> exit code 2)
    rc = app_main(["hold", "list", "--run-id", "run-pe-apex-horizon-20261030", "--json"])
    assert rc == 2
    raw_out = capsys.readouterr().out.strip()
    parsed = json.loads(raw_out)
    assert raw_out == dumps_jcs(parsed)
    assert parsed.get("error_code") == "RESOURCE_NOT_FOUND"


def test_presenter_formatting(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify human-readable presenter formatting (--toon, explicit --format text summary)."""
    pe_contract = "examples/private_equity/dv_pe_model_contract.yaml"
    pe_graph_a = "examples/private_equity/pe_graph_a_ontology.yaml"
    pe_graph_b = "examples/private_equity/pe_graph_b_profile.json"

    # --toon flag (explicit)
    rc = app_main(
        [
            "verify",
            "--contract",
            pe_contract,
            "--graph-a",
            pe_graph_a,
            "--graph-b",
            pe_graph_b,
            "--toon",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert "@toon_v1" in out

    # Default output (default format is toon per cli-spec.json)
    rc = app_main(
        ["verify", "--contract", pe_contract, "--graph-a", pe_graph_a, "--graph-b", pe_graph_b]
    )
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert "@toon_v1" in out

    # Explicit --format text
    rc = app_main(
        [
            "verify",
            "--contract",
            pe_contract,
            "--graph-a",
            pe_graph_a,
            "--graph-b",
            pe_graph_b,
            "--format",
            "text",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert "[opendv verify]" in out
    assert "status=" in out


def test_adversarial_cli_error_handling(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stress test adversarial inputs: missing flags, bad paths, syntax errors, forbidden flags."""
    # 1. Missing required flag --contract -> exit code 2
    rc = app_main(["verify", "--json"])
    assert rc == 2
    raw = capsys.readouterr().out.strip()
    parsed = json.loads(raw)
    assert raw == dumps_jcs(parsed)
    assert parsed["status"] == "ERROR"

    # 2. Non-existent file path -> exit code 2
    rc = app_main(["verify", "--contract", "does_not_exist_987654.yaml", "--json"])
    assert rc == 2
    raw = capsys.readouterr().out.strip()
    parsed = json.loads(raw)
    assert raw == dumps_jcs(parsed)
    assert parsed["status"] == "ERROR"

    # 3. Malformed YAML file -> exit code 2
    bad_yaml = tmp_path / "corrupt.yaml"
    bad_yaml.write_text("contract: [unclosed list", encoding="utf-8")
    rc = app_main(["verify", "--contract", str(bad_yaml), "--json"])
    assert rc == 2
    raw = capsys.readouterr().out.strip()
    parsed = json.loads(raw)
    assert parsed["status"] == "ERROR"

    # 4. Unknown subcommand -> exit code 2
    rc = app_main(["nonexistent_subcommand", "--json"])
    assert rc == 2
    raw = capsys.readouterr().out.strip()
    parsed = json.loads(raw)
    assert parsed["status"] == "ERROR"

    # 5. Rejected / failing contract -> exit code 1
    failing_contract = tmp_path / "failing_contract.yaml"
    failing_contract.write_text(
        "contract_version: '5.0'\nhubs:\n  - name: broken\n    target_table: raw_vault.hub_broken\n    grain_statement: 'short'\n    business_keys: []\n",
        encoding="utf-8",
    )
    rc = app_main(["verify", "--contract", str(failing_contract), "--json"])
    assert rc == 1  # Domain rejection exit code 1
    raw = capsys.readouterr().out.strip()
    parsed = json.loads(raw)
    assert raw == dumps_jcs(parsed)
    assert parsed["overall_passed"] is False


def test_subprocess_standalone_zero_dependency_isolation() -> None:
    """Subprocess test asserting zero fastapi/opendv_platform_api/httpx in sys.modules (Decision D6)."""
    probe_script = (
        "import sys\n"
        "from opendv_cli.main import app_main\n"
        "rc = app_main([\n"
        "    'verify',\n"
        "    '--contract', 'examples/private_equity/dv_pe_model_contract.yaml',\n"
        "    '--json',\n"
        "])\n"
        "assert rc == 0, f'verify failed with rc={rc}'\n"
        "banned = {'fastapi', 'starlette', 'opendv_platform_api', 'httpx'}\n"
        "loaded = banned.intersection(sys.modules.keys())\n"
        "assert not loaded, f'Forbidden modules loaded in sys.modules: {loaded}'\n"
    )
    res = subprocess.run([sys.executable, "-c", probe_script], capture_output=True, text=True)
    assert res.returncode == 0, (
        f"Subprocess isolation failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )


def test_jcs_unsupported_types_raise_canonicalization_error() -> None:
    """Verify CanonicalizationError is raised when attempting to serialize unsupported types."""
    from opendv_contracts.jcs import CanonicalizationError

    # Set
    with pytest.raises(CanonicalizationError):
        dumps_jcs({1, 2, 3})

    # Bytes
    with pytest.raises(CanonicalizationError):
        dumps_jcs(b"binary_data")

    # Custom object without model_dump
    class CustomObj:
        pass

    with pytest.raises(CanonicalizationError):
        dumps_jcs(CustomObj())

    # Complex number
    with pytest.raises(CanonicalizationError):
        dumps_jcs(complex(1, 2))

    # Generator
    with pytest.raises(CanonicalizationError):
        dumps_jcs((x for x in range(5)))


def test_jcs_nested_float_domain_errors() -> None:
    """Verify FloatDomainError is raised even when NaN or Inf is deeply buried in structures."""
    with pytest.raises(FloatDomainError):
        dumps_jcs({"a": [1, 2, {"b": float("nan")}]})

    with pytest.raises(FloatDomainError):
        dumps_jcs({"a": [1, 2, {"b": float("inf")}]})

    with pytest.raises(FloatDomainError):
        dumps_jcs([{"level1": [{"level2": float("-inf")}]}])


def test_dto_enum_strict_validation() -> None:
    """Verify strict validation and rejection of invalid enum values on contract DTOs."""
    # RbacRole
    assert RbacRole("INTERNAL_MODELER") == RbacRole.INTERNAL_MODELER
    with pytest.raises(ValueError):
        RbacRole("UNAUTHORIZED_ROLE")

    # GateVerdict
    assert GateVerdict("PASS") == GateVerdict.PASS
    assert GateVerdict("AUTO-FIX") == GateVerdict.AUTO_FIX
    with pytest.raises(ValueError):
        GateVerdict("UNKNOWN_VERDICT")

    # EventType
    assert EventType("STAGE_STARTED") == EventType.STAGE_STARTED
    with pytest.raises(ValueError):
        EventType("UNREGISTERED_EVENT")

    # RunStatus
    assert RunStatus("RUNNING") == RunStatus.RUNNING
    with pytest.raises(ValueError):
        RunStatus("FLOATING")

    # HoldAction
    assert HoldAction("APPROVE") == HoldAction.APPROVE
    with pytest.raises(ValueError):
        HoldAction("BYPASS")


def test_json_flag_precedence_over_toon(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify --json flag takes strict precedence over --toon when both flags are supplied."""
    pe_contract = "examples/private_equity/dv_pe_model_contract.yaml"
    pe_graph_a = "examples/private_equity/pe_graph_a_ontology.yaml"
    pe_graph_b = "examples/private_equity/pe_graph_b_profile.json"

    rc = app_main(
        [
            "verify",
            "--contract",
            pe_contract,
            "--graph-a",
            pe_graph_a,
            "--graph-b",
            pe_graph_b,
            "--json",
            "--toon",
        ]
    )
    assert rc == 0
    raw = capsys.readouterr().out.strip()
    # Output must be pure RFC 8785 JSON, NOT toon
    assert raw.startswith("{")
    assert raw.endswith("}")
    parsed = json.loads(raw)
    assert raw == dumps_jcs(parsed)


def test_all_subcommands_subprocess_isolation() -> None:
    """Verify isolated subprocess execution across all subcommands leaves sys.modules clean."""
    pe_contract = "examples/private_equity/dv_pe_model_contract.yaml"
    pe_graph_a = "examples/private_equity/pe_graph_a_ontology.yaml"
    pe_graph_b = "examples/private_equity/pe_graph_b_profile.json"
    pe_ir = "examples/private_equity/pe_canonical_ir.json"

    commands_to_probe: list[tuple[list[str], int]] = [
        (
            [
                "verify",
                "--contract",
                pe_contract,
                "--graph-a",
                pe_graph_a,
                "--graph-b",
                pe_graph_b,
                "--json",
            ],
            0,
        ),
        (["compile", "--contract", pe_contract, "--canonical-ir", pe_ir, "--json"], 0),
        (["profile", "--graph-b", pe_graph_b, "--json"], 0),
        (
            [
                "sandbox",
                "--contract",
                pe_contract,
                "--canonical-ir",
                pe_ir,
                "--query-type",
                "ADVERSARIAL_BATCHES_1_TO_4",
                "--json",
            ],
            0,
        ),
        (["run", "list", "--project-id", "proj-apex-horizon-pe", "--json"], 0),
        (["hold", "list", "--run-id", "run-pe-apex-horizon-20261030", "--json"], 2),
        (
            [
                "pipeline",
                "validate",
                "--input",
                "examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml",
                "--json",
            ],
            0,
        ),
    ]

    for cmd_args, expected_rc in commands_to_probe:
        probe_code = (
            "import sys\n"
            "from opendv_cli.main import app_main\n"
            f"rc = app_main({cmd_args!r})\n"
            f"assert rc == {expected_rc}, f'Command failed with rc={{rc}}, expected {expected_rc}'\n"
            "banned = {'fastapi', 'starlette', 'opendv_platform_api', 'httpx'}\n"
            "loaded = banned.intersection(sys.modules.keys())\n"
            "assert not loaded, f'Forbidden modules loaded in sys.modules: {loaded}'\n"
        )
        res = subprocess.run([sys.executable, "-c", probe_code], capture_output=True, text=True)
        assert res.returncode == 0, (
            f"Subprocess isolation failed for cmd {cmd_args[0]}:\nstderr: {res.stderr}"
        )


def test_adversarial_subcommand_edge_cases(capsys: pytest.CaptureFixture[str]) -> None:
    """Stress test missing parameters across run, hold, and pipeline subcommands."""
    # run create without --project-id -> exit code 2
    rc = app_main(["run", "create", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out.strip())
    assert out["status"] == "ERROR"

    # run get without --run-id -> exit code 2
    rc = app_main(["run", "get", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out.strip())
    assert out["status"] == "ERROR"

    # run abort without --run-id -> exit code 2
    rc = app_main(["run", "abort", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out.strip())
    assert out["status"] == "ERROR"

    # hold without --run-id -> exit code 2
    rc = app_main(["hold", "list", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out.strip())
    assert out["status"] == "ERROR"

    # pipeline validate without --input -> exit code 2
    rc = app_main(["pipeline", "validate", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out.strip())
    assert out["status"] == "ERROR"

    # pipeline report without --run-id and without --report-path -> exit code 2
    rc = app_main(["pipeline", "report", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out.strip())
    assert out["status"] == "ERROR"

    # pipeline report with non-existent --report-path -> exit code 2
    rc = app_main(["pipeline", "report", "--report-path", "non_existent_report_123.json", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out.strip())
    assert out["status"] == "ERROR"
