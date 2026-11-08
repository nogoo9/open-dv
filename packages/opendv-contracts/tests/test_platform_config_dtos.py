"""Unit tests for Platform & Agent Harness configuration contract DTOs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from opendv_contracts.dtos.pipeline import PipelineRunRequest, PipelineValidateRequest
from opendv_contracts.dtos.platform_config import (
    ConfigValidationDiagnosticDTO,
    HarnessAuthConfigDTO,
    HarnessBudgetConfigDTO,
    HarnessConfigDTO,
    PlatformConfigResponse,
    PlatformConfigUpdateRequest,
    PlatformConfigValidateRequest,
    PlatformConfigValidationResponse,
    ProviderReadinessDTO,
)
from opendv_contracts.spec import load_cli_spec


def test_harness_config_dto_defaults_and_validation() -> None:
    """Verify HarnessConfigDTO default values and strict schema validation."""
    cfg = HarnessConfigDTO()
    assert cfg.provider == "agy"
    assert cfg.model_overrides["reasoning"] == "gemini-4-argon-high"
    assert cfg.model_overrides["fast"] == "gemini-3.8-flash-high"
    assert cfg.steward_hold_policy == "auto_approve_top_candidate"
    assert cfg.ontology_policy == "off"
    assert cfg.stage_output_policy == "llm_only"
    assert cfg.budget.max_turns == 30
    assert cfg.sandbox.denied_builtin_tools == ["run_command"]

    with pytest.raises(ValidationError):
        HarnessConfigDTO(provider="invalid-provider")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        HarnessConfigDTO(ontology_policy="invalid-policy")  # type: ignore[arg-type]


def test_harness_budget_and_auth_boundary_validation() -> None:
    """Verify HarnessBudgetConfigDTO bounds and HarnessAuthConfigDTO alias synchronization."""
    with pytest.raises(ValidationError):
        HarnessBudgetConfigDTO(max_turns=0)
    with pytest.raises(ValidationError):
        HarnessBudgetConfigDTO(max_turns=201)
    with pytest.raises(ValidationError):
        HarnessBudgetConfigDTO(timeout_seconds=-1)
    with pytest.raises(ValidationError):
        HarnessBudgetConfigDTO(max_input_tokens=3_000_000)

    auth1 = HarnessAuthConfigDTO(auth_mode="api_key", require_credentials_present=True)
    assert auth1.mode == "api_key"
    assert auth1.require_preflight is True

    auth2 = HarnessAuthConfigDTO(mode="vertex_adc", require_preflight=True)
    assert auth2.auth_mode == "vertex_adc"
    assert auth2.require_credentials_present is True


def test_platform_config_response_serialization() -> None:
    """Verify PlatformConfigResponse and ProviderReadinessDTO round-trip cleanly."""
    readiness = ProviderReadinessDTO(
        provider="agy-cli",
        execution_mode="cli",
        available=True,
        binary_name="agy",
        binary_found=True,
        sdk_installed=False,
        auth_mode="cli_session",
        api_key_env_var="GEMINI_API_KEY",
        credentials_present=True,
        default_reasoning_model="gemini-4-argon-high",
        default_fast_model="gemini-3.8-flash-high",
        status_message="Ready (CLI binary found on PATH)",
    )
    resp = PlatformConfigResponse(
        workspace_root="/workspace",
        state_dir="/workspace/.opendv/state",
        cas_dir="/workspace/.opendv/state/cas",
        ledgers_dir="/workspace/.opendv/state/ledgers",
        persisted_config_path="/workspace/.opendv/state/platform_config.json",
        web_ui_bundled=True,
        harness=HarnessConfigDTO(provider="agy-cli"),
        resolved_persona_models={
            "vault-first-mate": "gemini-4-argon-high",
            "scout-source": "gemini-3.8-flash-high",
        },
        providers=[readiness],
    )
    dumped = resp.model_dump(mode="json")
    assert dumped["web_ui_bundled"] is True
    assert dumped["harness"]["provider"] == "agy-cli"
    assert dumped["providers"][0]["binary_found"] is True

    resp_with_creds = resp.model_copy(
        update={
            "harness": resp.harness.model_copy(
                update={
                    "auth": HarnessAuthConfigDTO(
                        auth_mode="vertex_adc",
                        gcp_credentials_path="/secret/gcp-sa.json",
                    )
                }
            )
        }
    )
    redacted_modeler = resp_with_creds.redact_for_role("INTERNAL_MODELER")
    assert redacted_modeler.harness.auth.gcp_credentials_path == "[REDACTED]"
    assert redacted_modeler.workspace_root == "/workspace"

    redacted_public = resp_with_creds.redact_for_role("PUBLIC")
    assert redacted_public.harness.auth.gcp_credentials_path == "[REDACTED]"
    assert redacted_public.workspace_root == "[REDACTED]"


def test_platform_config_update_and_validate_requests() -> None:
    """Verify PlatformConfigUpdateRequest and PlatformConfigValidationResponse."""
    upd = PlatformConfigUpdateRequest(
        harness={"provider": "claude-cli", "steward_hold_policy": "pause"},
        max_inline_bytes=5 * 1024 * 1024,
        persist=True,
    )
    assert upd.harness == {"provider": "claude-cli", "steward_hold_policy": "pause"}

    val_req = PlatformConfigValidateRequest(harness={"provider": "claude"})
    assert val_req.harness["provider"] == "claude"

    val_resp = PlatformConfigValidationResponse(
        passed=True,
        diagnostics=[
            ConfigValidationDiagnosticDTO(
                code="PROVIDER_READY",
                severity="INFO",
                target="harness.provider",
                message="Provider claude ready",
            )
        ],
        harness=HarnessConfigDTO(provider="claude"),
        resolved_persona_models={"ship-vault": "claude-opus-5-5-high"},
        providers=[],
    )
    assert val_resp.passed is True
    assert val_resp.diagnostics[0].code == "PROVIDER_READY"


def test_pipeline_request_supports_inline_spec_content() -> None:
    """Verify PipelineValidateRequest and PipelineRunRequest accept input_spec_content."""
    yaml_str = "pipeline_id: p1\nproject_id: proj1\n"
    v_req = PipelineValidateRequest(input_spec_content=yaml_str, steward_hold_policy="pause")
    assert v_req.input_spec_content == yaml_str

    r_req = PipelineRunRequest(input_spec_content=yaml_str, dry_run=False)
    assert r_req.input_spec_content == yaml_str


def test_cli_spec_includes_config_and_serve_commands() -> None:
    """Verify cli-spec.json includes the config (show, set, validate) and serve commands."""
    spec = load_cli_spec()
    commands = {cmd["name"]: cmd for cmd in spec["commands"]}
    assert "config" in commands
    assert "serve" in commands
    config_sub = {s["name"] for s in commands["config"]["subcommands"]}
    assert config_sub == {"show", "set", "validate"}
    assert commands["serve"]["availability"] == "standalone_only"
