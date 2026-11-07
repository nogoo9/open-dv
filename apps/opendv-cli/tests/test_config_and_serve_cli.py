"""Unit and integration tests for `opendv config` and `opendv serve` CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from opendv_cli import app_main
from opendv_cli.executor import LocalExecutor


class _MockConfigService:
    """Mock service implementing Track A platform config methods."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.storage_root = workspace_root / ".opendv" / "state"
        self.last_update_req: Any = None
        self.last_validate_req: Any = None

    def get_platform_config(self) -> dict[str, Any]:
        return {
            "workspace_root": str(self.workspace_root),
            "state_dir": str(self.storage_root),
            "cas_dir": str(self.storage_root / "cas"),
            "ledgers_dir": str(self.storage_root / "ledgers"),
            "server_host": "127.0.0.1",
            "server_port": 8420,
            "max_inline_bytes": 10485760,
            "cors_origins": ["http://127.0.0.1:8420"],
            "config_file_path": None,
            "persisted_config_path": str(self.storage_root / "platform_config.json"),
            "web_ui_bundled": True,
            "harness": {
                "provider": "agy",
                "model_overrides": {
                    "reasoning": "gemini-4-argon-high",
                    "fast": "gemini-3.8-flash-high",
                },
                "steward_hold_policy": "auto_approve_top_candidate",
                "ontology_policy": "off",
                "stage_output_policy": "llm_only",
            },
            "resolved_persona_models": {
                "vault-first-mate": "gemini-4-argon-high",
                "scout-source": "gemini-3.8-flash-high",
                "scout-unstructured": "gemini-3.8-flash-high",
                "ship-vault": "gemini-4-argon-high",
            },
            "providers": [],
        }

    def update_platform_config(self, req: Any) -> dict[str, Any]:
        self.last_update_req = req
        harness = dict(req.harness or {})
        overrides = dict(harness.get("model_overrides") or {})
        reasoning = overrides.get("reasoning", "claude-opus-4-6")
        fast = overrides.get("fast", "claude-haiku-4-5")
        return {
            "workspace_root": str(self.workspace_root),
            "state_dir": str(self.storage_root),
            "cas_dir": str(self.storage_root / "cas"),
            "ledgers_dir": str(self.storage_root / "ledgers"),
            "server_host": "127.0.0.1",
            "server_port": 8420,
            "max_inline_bytes": 10485760,
            "cors_origins": ["http://127.0.0.1:8420"],
            "config_file_path": None,
            "persisted_config_path": str(self.storage_root / "platform_config.json"),
            "web_ui_bundled": True,
            "harness": harness,
            "resolved_persona_models": {
                "vault-first-mate": reasoning,
                "scout-source": fast,
                "scout-unstructured": fast,
                "ship-vault": reasoning,
            },
            "providers": [],
        }

    def validate_platform_config(self, req: Any) -> dict[str, Any]:
        self.last_validate_req = req
        harness = dict(req.harness or {})
        auth = dict(harness.get("auth") or {})
        if harness.get("provider") == "agy" and auth.get("auth_mode") == "aws_bedrock":
            return {
                "passed": False,
                "diagnostics": [
                    {
                        "code": "HARNESS_AUTH_INCOMPATIBLE",
                        "severity": "ERROR",
                        "target": "auth.auth_mode",
                        "message": "agy does not support aws_bedrock",
                    }
                ],
                "harness": None,
                "resolved_persona_models": {},
                "providers": [],
            }
        return {
            "passed": True,
            "diagnostics": [],
            "harness": harness,
            "resolved_persona_models": {},
            "providers": [],
        }


class _FallbackOnlyService:
    """Minimal service object without Track A methods to verify clean fallback."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.storage_root = workspace_root / ".opendv" / "state"
        self.settings = None


def test_opendv_config_show_with_service_and_fallback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify `opendv config show` and default `opendv config` in-process execution."""
    mock_svc = _MockConfigService(tmp_path)
    executor = LocalExecutor(service=mock_svc, workspace_root=tmp_path)  # type: ignore[arg-type]

    rc = app_main(["config", "show", "--json"], executor=executor)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "local"
    assert out["server_port"] == 8420
    assert out["harness"]["provider"] == "agy"

    # Also test fallback service when Track A methods are absent
    fb_svc = _FallbackOnlyService(tmp_path)
    fb_executor = LocalExecutor(service=fb_svc, workspace_root=tmp_path)  # type: ignore[arg-type]
    rc_fb = app_main(["config", "show", "--json"], executor=fb_executor)
    assert rc_fb == 0
    out_fb = json.loads(capsys.readouterr().out)
    assert out_fb["mode"] == "local"
    assert out_fb["harness"]["provider"] == "agy"


def test_opendv_config_set_with_flags_and_harness_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify `opendv config set` builds PlatformConfigUpdateRequest from flags and YAML."""
    mock_svc = _MockConfigService(tmp_path)
    executor = LocalExecutor(service=mock_svc, workspace_root=tmp_path)  # type: ignore[arg-type]

    rc = app_main(
        [
            "config",
            "set",
            "--provider",
            "claude-cli",
            "--reasoning-model",
            "claude-opus-4-6",
            "--fast-model",
            "claude-haiku-4-5",
            "--auth-mode",
            "cli_session",
            "--api-key-env-var",
            "ANTHROPIC_API_KEY",
            "--hold-policy",
            "pause",
            "--ontology-policy",
            "recommend",
            "--stage-output-policy",
            "symbolic_completion",
            "--max-turns",
            "40",
            "--timeout-seconds",
            "360",
            "--json",
        ],
        executor=executor,
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "local"
    assert out["harness"]["provider"] == "claude-cli"
    assert out["harness"]["steward_hold_policy"] == "pause"
    assert out["harness"]["ontology_policy"] == "recommend"
    assert out["harness"]["stage_output_policy"] == "symbolic_completion"
    assert out["harness"]["model_overrides"]["reasoning"] == "claude-opus-4-6"
    assert out["harness"]["model_overrides"]["fast"] == "claude-haiku-4-5"
    assert out["harness"]["auth"]["auth_mode"] == "cli_session"
    assert out["harness"]["auth"]["api_key_env_var"] == "ANTHROPIC_API_KEY"
    assert out["harness"]["budget"]["max_turns"] == 40
    assert out["harness"]["budget"]["timeout_seconds"] == 360.0

    # Test with --harness-config YAML file on fallback service (persists to state_dir)
    harness_yaml = tmp_path / "custom_harness.yaml"
    harness_yaml.write_text(
        yaml.safe_dump(
            {
                "provider": "antigravity",
                "model_overrides": {
                    "reasoning": "gemini-4-argon-high",
                    "fast": "gemini-3.8-flash-high",
                },
                "steward_hold_policy": "reject_on_hold",
            }
        ),
        encoding="utf-8",
    )

    fb_svc = _FallbackOnlyService(tmp_path)
    fb_executor = LocalExecutor(service=fb_svc, workspace_root=tmp_path)  # type: ignore[arg-type]
    rc_yaml = app_main(
        [
            "config",
            "set",
            "--harness-config",
            str(harness_yaml),
            "--json",
        ],
        executor=fb_executor,
    )
    assert rc_yaml == 0
    out_yaml = json.loads(capsys.readouterr().out)
    assert out_yaml["harness"]["provider"] == "antigravity"
    assert out_yaml["harness"]["steward_hold_policy"] == "reject_on_hold"
    assert Path(out_yaml["persisted_config_path"]).is_file()


def test_opendv_config_validate_pass_and_fail(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify `opendv config validate` returns exit code 0 on pass and 1 on fail."""
    mock_svc = _MockConfigService(tmp_path)
    executor = LocalExecutor(service=mock_svc, workspace_root=tmp_path)  # type: ignore[arg-type]

    rc_pass = app_main(
        ["config", "validate", "--provider", "claude-cli", "--json"],
        executor=executor,
    )
    assert rc_pass == 0
    out_pass = json.loads(capsys.readouterr().out)
    assert out_pass["passed"] is True

    bad_yaml = tmp_path / "bad_harness.yaml"
    bad_yaml.write_text(
        yaml.safe_dump(
            {
                "provider": "agy",
                "auth": {"auth_mode": "aws_bedrock"},
            }
        ),
        encoding="utf-8",
    )

    rc_fail = app_main(
        ["config", "validate", "--harness-config", str(bad_yaml), "--json"],
        executor=executor,
    )
    assert rc_fail == 1
    out_fail = json.loads(capsys.readouterr().out)
    assert out_fail["passed"] is False
    assert out_fail["diagnostics"][0]["code"] == "HARNESS_AUTH_INCOMPATIBLE"

    # Also test fallback validation failure
    fb_svc = _FallbackOnlyService(tmp_path)
    fb_executor = LocalExecutor(service=fb_svc, workspace_root=tmp_path)  # type: ignore[arg-type]
    rc_fb_fail = app_main(
        ["config", "validate", "--harness-config", str(bad_yaml), "--json"],
        executor=fb_executor,
    )
    assert rc_fb_fail == 1
    out_fb_fail = json.loads(capsys.readouterr().out)
    assert out_fb_fail["passed"] is False


def test_opendv_serve_invokes_platform_cli_or_uvicorn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify `opendv serve` dispatches to opendv_platform_api.cli.main or uvicorn.run."""
    fb_svc = _FallbackOnlyService(tmp_path)
    executor = LocalExecutor(service=fb_svc, workspace_root=tmp_path)  # type: ignore[arg-type]

    captured_cli_args: list[list[str]] = []

    def _fake_cli_main(argv: list[str] | None = None) -> int:
        captured_cli_args.append(list(argv or []))
        return 0

    import opendv_cli.executor as cli_executor

    monkeypatch.setattr(cli_executor, "_load_platform_server_main", lambda: _fake_cli_main)

    cfg_file = tmp_path / "opendv.config.yaml"
    cfg_file.write_text(f"workspace_root: {tmp_path}\n", encoding="utf-8")

    rc = app_main(
        [
            "serve",
            "--host",
            "0.0.0.0",
            "--port",
            "9420",
            "--config",
            str(cfg_file),
            "--workspace",
            str(tmp_path),
            "--open",
            "--json",
        ],
        executor=executor,
    )
    assert rc == 0
    assert len(captured_cli_args) == 1
    assert "--host" in captured_cli_args[0]
    assert "0.0.0.0" in captured_cli_args[0]
    assert "--port" in captured_cli_args[0]
    assert "9420" in captured_cli_args[0]
    assert "--open" in captured_cli_args[0]

    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "OK"
    assert out["port"] == 9420
    assert out["url"] == "http://0.0.0.0:9420"
