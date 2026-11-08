"""Tests for PlatformConfigStateMixin, OpenDVService platform config, and pipeline inheritance."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from opendv_contracts import (
    PipelineValidateRequest,
    PlatformConfigResponse,
    PlatformConfigUpdateRequest,
    PlatformConfigValidateRequest,
    PlatformConfigValidationResponse,
)
from opendv_service import OpenDVService, OpenDVSettings, PlatformStateContainer


def test_default_platform_config_and_provider_readiness_no_secret_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify default platform config, persona model resolution, and zero raw secret leakage."""
    secret_gemini = "AIzaSy-SUPER-SECRET-DO-NOT-LEAK-12345"
    secret_claude = "sk-ant-api03-SUPER-SECRET-DO-NOT-LEAK-67890"
    monkeypatch.setenv("GEMINI_API_KEY", secret_gemini)
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret_claude)

    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    state_dir = tmp_path / "state"

    service = OpenDVService(workspace_root=ws_root, storage_root=state_dir)
    cfg = service.get_platform_config()

    assert isinstance(cfg, PlatformConfigResponse)
    assert cfg.workspace_root == str(ws_root.resolve())
    assert cfg.state_dir == str(state_dir.resolve())
    assert cfg.harness.provider == "agy"
    assert cfg.harness.steward_hold_policy == "auto_approve_top_candidate"
    assert cfg.harness.ontology_policy == "off"
    assert cfg.harness.stage_output_policy == "llm_only"
    assert cfg.harness.allow_cli_schema_omission_retry is False

    # Verify all 4 canonical personas are resolved
    assert set(cfg.resolved_persona_models.keys()) == {
        "vault-first-mate",
        "scout-source",
        "scout-unstructured",
        "ship-vault",
    }
    assert cfg.resolved_persona_models["vault-first-mate"] == "gemini-4-argon-high"
    assert cfg.resolved_persona_models["scout-source"] == "gemini-3.8-flash-high"
    assert cfg.resolved_persona_models["scout-unstructured"] == "gemini-3.8-flash-high"
    assert cfg.resolved_persona_models["ship-vault"] == "gemini-4-argon-high"

    # Verify all 6 providers are probed
    by_provider = {p.provider: p for p in cfg.providers}
    assert set(by_provider.keys()) == {
        "agy",
        "agy-cli",
        "antigravity",
        "claude",
        "claude-cli",
        "claude-code",
    }
    assert by_provider["agy"].execution_mode == "cli"
    assert by_provider["agy"].binary_name == "agy"
    assert by_provider["antigravity"].execution_mode == "sdk"
    assert by_provider["antigravity"].binary_name is None
    assert by_provider["antigravity"].credentials_present is True
    assert by_provider["claude"].execution_mode == "sdk"
    assert by_provider["claude"].api_key_env_var == "ANTHROPIC_API_KEY"
    assert by_provider["claude"].credentials_present is True
    assert by_provider["claude-cli"].execution_mode == "cli"
    assert by_provider["claude-cli"].binary_name == "claude"

    # Never expose raw secret values in serialized payload
    dumped_json = json.dumps(cfg.model_dump(mode="json"))
    assert secret_gemini not in dumped_json
    assert secret_claude not in dumped_json


def test_validate_platform_config_valid_and_invalid(tmp_path: Path) -> None:
    """Verify deterministic validation of candidate harness configurations."""
    service = OpenDVService(workspace_root=tmp_path, storage_root=tmp_path / "state")

    # 1. Valid claude-cli configuration with persona override and CLI retry
    valid_res = service.validate_platform_config(
        PlatformConfigValidateRequest(
            harness={
                "provider": "claude-cli",
                "agent_models": {"vault-first-mate": "claude-opus-custom"},
                "steward_hold_policy": "pause",
                "ontology_policy": "recommend",
                "stage_output_policy": "symbolic_completion",
                "allow_cli_schema_omission_retry": True,
                "budget": {"max_turns": 25, "timeout_seconds": 600.0},
                "sandbox": {"read_only_workspace": True, "allow_network_access": True},
            }
        )
    )
    assert isinstance(valid_res, PlatformConfigValidationResponse)
    assert valid_res.passed is True
    assert valid_res.diagnostics == []
    assert valid_res.harness is not None
    assert valid_res.harness.provider == "claude-cli"
    assert valid_res.harness.allow_cli_schema_omission_retry is True
    assert valid_res.harness.sandbox.allow_network_access is True
    assert valid_res.resolved_persona_models["vault-first-mate"] == "claude-opus-custom"
    assert valid_res.resolved_persona_models["scout-source"] == "claude-haiku-4-5"

    # 2. Invalid provider
    bad_provider = service.validate_platform_config(
        {"harness": {"provider": "unknown-provider"}}
    )
    assert bad_provider.passed is False
    assert len(bad_provider.diagnostics) >= 1
    assert bad_provider.diagnostics[0].code == "HARNESS_CONFIG_INVALID"

    # 3. Invalid legacy model_overrides key
    bad_tier = service.validate_platform_config(
        {"harness": {"model_overrides": {"argon": "gemini-4-argon-high"}}}
    )
    assert bad_tier.passed is False
    assert any("argon" in d.message for d in bad_tier.diagnostics)

    # 4. Invalid raw secret passed as api_key_env_var
    bad_env_var = service.validate_platform_config(
        {"harness": {"auth": {"api_key_env_var": "sk-ant-raw-secret-value"}}}
    )
    assert bad_env_var.passed is False
    assert any("api_key_env_var" in d.message for d in bad_env_var.diagnostics)


def test_update_platform_config_persistence_and_yaml_merge(tmp_path: Path) -> None:
    """Verify opendv.config.yaml loading, runtime updates, and persistence across restarts."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    state_dir = tmp_path / "state"

    # Seed opendv.config.yaml with initial harness config
    cfg_yaml = ws_root / "opendv.config.yaml"
    cfg_yaml.write_text(
        yaml.safe_dump(
            {
                "workspace_root": str(ws_root),
                "harness": {
                    "provider": "antigravity",
                    "steward_hold_policy": "reject_on_hold",
                },
            }
        ),
        encoding="utf-8",
    )

    settings = OpenDVSettings.load(
        workspace_root=ws_root,
        state_dir=state_dir,
        config_file=cfg_yaml,
    )
    service = OpenDVService(
        workspace_root=ws_root,
        storage_root=state_dir,
        settings=settings,
    )

    initial_cfg = service.get_platform_config()
    assert initial_cfg.harness.provider == "antigravity"
    assert initial_cfg.harness.steward_hold_policy == "reject_on_hold"
    assert initial_cfg.config_file_path == str(cfg_yaml.resolve())

    # Update platform config and persist
    updated_cfg = service.update_platform_config(
        PlatformConfigUpdateRequest(
            harness={
                "provider": "claude",
                "steward_hold_policy": "pause",
                "ontology_policy": "suggest_only",
                "allow_cli_schema_omission_retry": True,
                "agent_models": {"ship-vault": "claude-opus-5-5-high"},
            },
            max_inline_bytes=20 * 1024 * 1024,
            cors_origins=["http://localhost:8420"],
            persist=True,
        )
    )
    assert updated_cfg.harness.provider == "claude"
    assert updated_cfg.harness.steward_hold_policy == "pause"
    assert updated_cfg.harness.ontology_policy == "recommend"
    assert updated_cfg.harness.allow_cli_schema_omission_retry is True
    assert updated_cfg.max_inline_bytes == 20 * 1024 * 1024
    assert updated_cfg.cors_origins == ["http://localhost:8420"]
    assert (state_dir / "platform_config.json").is_file()

    # Re-instantiate PlatformStateContainer on same state_dir and verify persisted values win
    reloaded_state = PlatformStateContainer(workspace_root=ws_root, storage_root=state_dir)
    reloaded_cfg = PlatformConfigResponse.model_validate(reloaded_state.get_platform_config())
    assert reloaded_cfg.harness.provider == "claude"
    assert reloaded_cfg.harness.steward_hold_policy == "pause"
    assert reloaded_cfg.harness.allow_cli_schema_omission_retry is True
    assert reloaded_cfg.max_inline_bytes == 20 * 1024 * 1024

    # Invalid update raises ValueError
    with pytest.raises(ValueError):
        service.update_platform_config({"harness": {"provider": "not-a-provider"}})


def test_pipeline_resolves_input_spec_content_and_inherits_active_harness(
    tmp_path: Path,
) -> None:
    """Verify inline `input_spec_content` YAML parsing and active harness inheritance."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    state_dir = tmp_path / "state"
    state = PlatformStateContainer(workspace_root=ws_root, storage_root=state_dir)

    state.update_platform_config(
        {
            "harness": {
                "provider": "claude-cli",
                "steward_hold_policy": "pause",
                "ontology_policy": "suggest_only",
                "allow_cli_schema_omission_retry": True,
            },
            "persist": False,
        }
    )

    inline_yaml = yaml.safe_dump(
        {
            "pipeline_id": "pipe-inline-test",
            "project_id": "proj-inline-test",
            "graph_a_ontology_path": "ontology/graph_a.yaml",
            "waves": [
                {
                    "wave_index": 0,
                    "wave_name": "W0",
                    "wave_type": "COLD_START",
                    "sources": [],
                }
            ],
        }
    )

    req = PipelineValidateRequest(input_spec_content=inline_yaml)
    raw_spec, raw_setup, base_dir = state._resolve_pipeline_raw_inputs(req)

    assert base_dir == ws_root.resolve()
    assert raw_spec["pipeline_id"] == "pipe-inline-test"
    assert raw_setup["provider"] == "claude-cli"
    assert raw_setup["steward_hold_policy"] == "pause"
    assert raw_setup["ontology_policy"] == "suggest_only"
    assert raw_setup["extra_provider_options"]["allow_cli_schema_omission_retry"] is True
