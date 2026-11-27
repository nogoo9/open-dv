"""Phase 1 Layer 1 DTO tests: readiness, doctor, remediation, patch, and budget bounds."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import opendv.models as models
from opendv.agents.pack_loader import AGENT_PACK_IDS as PACK_LOADER_IDS
from opendv.models import (
    AGENT_PACK_IDS,
    VALID_MODEL_OVERRIDE_TARGETS,
    DoctorResponse,
    ErrorCode,
    ErrorRemediation,
    ModelOption,
    OntologyConstructReq,
    OpenDVError,
    ProblemDetails,
    ProjectSettingsPatchReq,
    ProjectSettingsSnapshot,
    ProviderReadinessRecord,
    ReadinessResponse,
    ReadinessTestReq,
    RemediationAction,
    RevokeTrustedWorkspaceReq,
    RunControlReq,
    VaultBuildReq,
    WorkspaceSettings,
    create_problem_details,
)


def test_remediation_dtos_and_problem_details_forwarding() -> None:
    """RemediationAction, ErrorRemediation, ProblemDetails, and OpenDVError preserve 34 codes."""
    assert len(ErrorCode) == 34

    default_pd = create_problem_details(ErrorCode.SYNTAX_INVALID_ARGUMENT, "bad input")
    assert default_pd.remediation is None

    action = RemediationAction(
        actor="agent",
        label="Install SDK extra",
        command="uv sync --extra sdk",
        requires_tty=False,
    )
    rem = ErrorRemediation(summary="Install missing SDK or switch provider.", actions=[action])
    pd = create_problem_details(
        ErrorCode.DRIVER_MISSING,
        "missing anthropic",
        remediation=rem,
    )
    assert pd.remediation == rem

    err = OpenDVError(
        "missing anthropic",
        error_code=ErrorCode.DRIVER_MISSING,
        run_id="run-1",
        remediation=rem,
    )
    err_pd = err.to_problem_details(instance="/api/v1/projects/p1/runs")
    assert err_pd.remediation == rem
    assert err_pd.run_id == "run-1"
    assert err_pd.instance == "/api/v1/projects/p1/runs"

    with pytest.raises(ValidationError):
        RemediationAction(
            actor="agent",
            label="x",
            extra_field="nope",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        ErrorRemediation(summary="x", extra_field="nope")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ProblemDetails.model_validate({**pd.model_dump(), "extra_field": 1})


def test_agent_pack_ids_and_model_override_targets_consistency() -> None:
    """AGENT_PACK_IDS and VALID_MODEL_OVERRIDE_TARGETS stay consistent across Layers 1 and 3."""
    assert len(AGENT_PACK_IDS) == 6
    assert PACK_LOADER_IDS == AGENT_PACK_IDS
    assert models.AGENT_PACK_IDS == AGENT_PACK_IDS
    assert frozenset(
        {"FRONTIER_CRITIC", "STANDARD_WORKER", *AGENT_PACK_IDS}
    ) == VALID_MODEL_OVERRIDE_TARGETS
    assert len(VALID_MODEL_OVERRIDE_TARGETS) == 8


def test_workspace_and_project_settings_bounds_and_model_overrides() -> None:
    """WorkspaceSettings, ProjectSettingsSnapshot, and RunControlReq enforce bounds & overrides."""
    ws = WorkspaceSettings(
        max_turns=500,
        timeout_seconds=7200,
        model_overrides={"ship-vault": "  claude-sonnet-4-6  "},
    )
    assert ws.model_overrides == {"ship-vault": "claude-sonnet-4-6"}

    snap = ProjectSettingsSnapshot(
        max_turns=1,
        timeout_seconds=1,
        model_overrides={"FRONTIER_CRITIC": " claude-opus-4-6 "},
    )
    assert snap.model_overrides == {"FRONTIER_CRITIC": "claude-opus-4-6"}
    assert ProjectSettingsSnapshot().model_overrides == {}

    for cls in (WorkspaceSettings, ProjectSettingsSnapshot):
        with pytest.raises(ValidationError):
            cls(max_turns=501)
        with pytest.raises(ValidationError):
            cls(timeout_seconds=7201)
        with pytest.raises(ValidationError):
            cls(model_overrides={"unknown-pack": "claude-sonnet-4-6"})
        with pytest.raises(ValidationError):
            cls(model_overrides={"ship-vault": "   "})

    rc_ok = RunControlReq(action="raise-budget", extra_turns=500, extra_timeout_seconds=7200)
    assert rc_ok.extra_turns == 500
    assert rc_ok.extra_timeout_seconds == 7200
    with pytest.raises(ValidationError):
        RunControlReq(action="raise-budget", extra_turns=501)
    with pytest.raises(ValidationError):
        RunControlReq(action="raise-budget", extra_timeout_seconds=7201)

    assert OntologyConstructReq().discovery_mode is None
    assert VaultBuildReq().policy is None


def test_project_settings_patch_req_rfc7396_semantics() -> None:
    """ProjectSettingsPatchReq enforces RFC 7396 null resets, non-nullable fields, and locks."""
    with pytest.raises(ValidationError):
        ProjectSettingsPatchReq.model_validate({})
    with pytest.raises(ValidationError):
        ProjectSettingsPatchReq.model_validate({"model_overrides": {}})

    empty_map_with_other = ProjectSettingsPatchReq.model_validate(
        {"max_turns": 10, "model_overrides": {}}
    )
    assert empty_map_with_other.max_turns == 10
    assert empty_map_with_other.model_overrides == {}

    reset_req = ProjectSettingsPatchReq.model_validate({"model_overrides": None})
    assert "model_overrides" in reset_req.model_fields_set
    assert reset_req.model_overrides is None
    assert reset_req.model_dump(mode="json", exclude_unset=True) == {"model_overrides": None}

    for nullable_field in ("max_turns", "max_tokens", "timeout_seconds", "model_overrides"):
        req = ProjectSettingsPatchReq.model_validate({nullable_field: None})
        assert req.model_dump(mode="json", exclude_unset=True) == {nullable_field: None}

    for non_nullable in (
        "harness_provider",
        "cegar_policy",
        "v7_critic_tier",
        "dbt_sql_dialect",
        "secret_precedence",
        "discovery_mode",
    ):
        with pytest.raises(ValidationError):
            ProjectSettingsPatchReq.model_validate({non_nullable: None})

    per_key = ProjectSettingsPatchReq.model_validate(
        {
            "model_overrides": {
                "ship-vault": "  claude-sonnet-4-6 ",
                "dv-semantic-critic": None,
            }
        }
    )
    assert per_key.model_overrides == {
        "ship-vault": "claude-sonnet-4-6",
        "dv-semantic-critic": None,
    }

    with pytest.raises(ValidationError):
        ProjectSettingsPatchReq.model_validate({"model_overrides": {"bad-target": "m1"}})
    with pytest.raises(ValidationError):
        ProjectSettingsPatchReq.model_validate({"model_overrides": {"ship-vault": "  "}})
    with pytest.raises(ValidationError):
        ProjectSettingsPatchReq.model_validate(
            {"cegar_policy": "strict", "v7_critic_tier": "STANDARD_WORKER"}
        )
    with pytest.raises(ValidationError):
        ProjectSettingsPatchReq.model_validate({"max_turns": 501})
    with pytest.raises(ValidationError):
        ProjectSettingsPatchReq.model_validate({"timeout_seconds": 7201})
    with pytest.raises(ValidationError):
        ProjectSettingsPatchReq.model_validate({"max_turns": 10, "unknown_key": 1})


def test_wire_dtos_readiness_and_revoke_validation() -> None:
    """Readiness, Doctor, and RevokeTrustedWorkspaceReq enforce strict schemas and UNC/ctl guards."""
    opt = ModelOption(id="claude-sonnet-4-6", label="claude-sonnet-4-6 (Standard Worker Default)")
    assert opt.source == "default"

    rec = ProviderReadinessRecord(
        provider="claude",
        status="CONFIGURED_UNVERIFIED",
        credential_source="store",
        available_models=[opt],
    )
    assert rec.missing_prereqs == []
    assert rec.active_run is False
    assert rec.remediation is None

    resp = ReadinessResponse(providers=[rec])
    test_req = ReadinessTestReq(provider="claude")
    assert test_req.provider == "claude"
    with pytest.raises(ValidationError):
        ReadinessTestReq.model_validate({"provider": "claude", "extra": 1})

    req_ok = RevokeTrustedWorkspaceReq(workspace_root="  C:\\work\\project  ")
    assert req_ok.workspace_root == "C:\\work\\project"

    for bad_root in (
        "",
        "   ",
        "C:\\ws\x00",
        "C:\\ws\n",
        "C:\\ws\r",
        "C:\\ws\t",
        "C:\\ws\x7f",
        "\\\\server\\share",
        "//server/share",
        "\\\\?\\C:\\ws",
        "//./pipe/x",
    ):
        with pytest.raises(ValidationError):
            RevokeTrustedWorkspaceReq(workspace_root=bad_root)

    doc = DoctorResponse(
        version="2.0.0",
        workspace_root="C:\\work",
        harness_provider="agy",
        cegar_policy="balanced",
        secret_precedence="store_first",
        readiness=resp,
    )
    assert doc.status == "ok"
    assert doc.agents is None
    assert doc.mcp_servers is None
    assert doc.remediation == []
    roundtrip = DoctorResponse.model_validate_json(doc.model_dump_json())
    assert roundtrip == doc
    with pytest.raises(ValidationError):
        DoctorResponse.model_validate({**doc.model_dump(), "extra_field": 1})
