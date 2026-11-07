"""Unit tests for fail-fast validation and CLI binary resolution (Task P3.4 / #80)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from opendv_agent_harness.adapters.agy_cli_adapter import AgyCliHarnessAdapter
from opendv_agent_harness.adapters.antigravity_adapter import ProviderConfigurationError
from opendv_agent_harness.adapters.claude_cli_adapter import ClaudeCliHarnessAdapter
from opendv_agent_harness.models import (
    AgentPersona,
    HarnessConfig,
)
from opendv_agent_harness.pipeline import _resolve_hub_and_uri, _resolve_relationship_uri
from opendv_contracts.errors import ErrorCode, InputValidationError
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    GraphAOntology,
)


class _TaskResult(BaseModel):
    status: str = "PASS"


def _make_harness_config(tmp_path: Path, **overrides: object) -> HarnessConfig:
    ws = tmp_path / "workspace"
    st = tmp_path / "state"
    app = tmp_path / "app_data"
    save = tmp_path / "save"
    for p in (ws, st, app, save):
        p.mkdir(parents=True, exist_ok=True)
    defaults: dict[str, object] = {
        "provider": "agy",
        "workspace_dir": ws.resolve(),
        "state_dir": st.resolve(),
        "app_data_dir": app.resolve(),
        "save_dir": save.resolve(),
    }
    defaults.update(overrides)
    return HarnessConfig(**defaults)  # type: ignore[arg-type]


def test_agy_cli_binary_ignores_os_and_home_paths(tmp_path: Path) -> None:
    """Verify CFG-06: _resolve_agy_binary ignores binaries in home and OS directories."""
    fake_home = tmp_path / "fake_home"
    old_fallback_bin = fake_home / ".local" / "bin" / "agy"
    old_fallback_bin.parent.mkdir(parents=True, exist_ok=True)
    old_fallback_bin.write_text("#!/bin/sh\necho agy\n")

    cfg = _make_harness_config(tmp_path)
    adapter = AgyCliHarnessAdapter(cfg)

    with (
        patch("pathlib.Path.home", return_value=fake_home),
        patch("shutil.which", return_value=None),
    ):
        resolved = adapter._resolve_agy_binary()
        assert resolved is None, "Agy binary in home/.local must NOT be resolved"


def test_claude_cli_binary_ignores_os_and_home_paths(tmp_path: Path) -> None:
    """Verify CFG-06: _resolve_claude_binary ignores binaries in home and OS directories."""
    fake_home = tmp_path / "fake_home"
    old_fallback_bin = fake_home / ".local" / "bin" / "claude"
    old_fallback_bin.parent.mkdir(parents=True, exist_ok=True)
    old_fallback_bin.write_text("#!/bin/sh\necho claude\n")

    cfg = _make_harness_config(tmp_path, provider="claude")
    adapter = ClaudeCliHarnessAdapter(cfg)

    with (
        patch("pathlib.Path.home", return_value=fake_home),
        patch("shutil.which", return_value=None),
    ):
        resolved = adapter._resolve_claude_binary()
        assert resolved is None, "Claude binary in home/.local must NOT be resolved"


def test_agy_cli_binary_resolution_precedence(tmp_path: Path) -> None:
    """Verify CFG-06: binary resolution respects options, env, and PATH without fallbacks."""
    valid_bin = tmp_path / "bin" / "agy"
    valid_bin.parent.mkdir(parents=True, exist_ok=True)
    valid_bin.write_text("#!/bin/sh\necho agy\n")

    # 1. From extra_provider_options
    cfg_opts = _make_harness_config(tmp_path, extra_provider_options={"agy_binary": str(valid_bin)})
    adapter_opts = AgyCliHarnessAdapter(cfg_opts)
    assert adapter_opts._resolve_agy_binary() == str(valid_bin)

    # 2. From config env
    cfg_env = _make_harness_config(tmp_path, env={"OPENDV_AGY_CLI_BIN": str(valid_bin)})
    adapter_env = AgyCliHarnessAdapter(cfg_env)
    assert adapter_env._resolve_agy_binary() == str(valid_bin)

    # 3. Invalid explicit option does NOT silently fall back to PATH
    invalid_bin_path = str(tmp_path / "nonexistent" / "agy")
    cfg_inv = _make_harness_config(
        tmp_path, extra_provider_options={"agy_binary": invalid_bin_path}
    )
    adapter_inv = AgyCliHarnessAdapter(cfg_inv)
    with patch(
        "shutil.which",
        side_effect=lambda cmd: (
            "/bin/agy_on_path" if cmd in ("agy", "agy.exe", "agy.cmd") else None
        ),
    ):
        assert adapter_inv._resolve_agy_binary() is None


def test_claude_cli_binary_resolution_precedence(tmp_path: Path) -> None:
    """Verify CFG-06: claude resolution respects options, env, and PATH without fallbacks."""
    valid_bin = tmp_path / "bin" / "claude"
    valid_bin.parent.mkdir(parents=True, exist_ok=True)
    valid_bin.write_text("#!/bin/sh\necho claude\n")

    cfg_opts = _make_harness_config(
        tmp_path, provider="claude", extra_provider_options={"claude_binary": str(valid_bin)}
    )
    adapter_opts = ClaudeCliHarnessAdapter(cfg_opts)
    assert adapter_opts._resolve_claude_binary() == str(valid_bin)

    invalid_bin_path = str(tmp_path / "nonexistent" / "claude")
    cfg_inv = _make_harness_config(
        tmp_path, provider="claude", extra_provider_options={"claude_binary": invalid_bin_path}
    )
    adapter_inv = ClaudeCliHarnessAdapter(cfg_inv)
    with patch(
        "shutil.which",
        side_effect=lambda cmd: (
            "/bin/claude_on_path" if cmd in ("claude", "claude.exe", "claude.cmd") else None
        ),
    ):
        assert adapter_inv._resolve_claude_binary() is None


@pytest.mark.asyncio
async def test_missing_binary_error_message_lists_all_sources(tmp_path: Path) -> None:
    """Verify CFG-06: error message on missing CLI binary lists all three checked sources."""
    cfg_agy = _make_harness_config(tmp_path, provider="agy-cli")
    adapter_agy = AgyCliHarnessAdapter(cfg_agy)

    from opendv_agent_harness import build_persona_task_spec

    spec = build_persona_task_spec(
        AgentPersona.VAULT_FIRST_MATE,
        "Synthesize contract",
    )

    with patch.object(adapter_agy, "_resolve_agy_binary", return_value=None):
        with pytest.raises(ProviderConfigurationError) as exc_agy:
            async for _ in adapter_agy.stream_task(spec):
                pass
        msg = str(exc_agy.value)
        assert "extra_provider_options.agy_binary" in msg
        assert "OPENDV_AGY_CLI_BIN" in msg
        assert "PATH" in msg

    cfg_claude = _make_harness_config(tmp_path, provider="claude-cli")
    adapter_claude = ClaudeCliHarnessAdapter(cfg_claude)

    with patch.object(adapter_claude, "_resolve_claude_binary", return_value=None):
        with pytest.raises(ProviderConfigurationError) as exc_claude:
            async for _ in adapter_claude.stream_task(spec):
                pass
        msg = str(exc_claude.value)
        assert "extra_provider_options.claude_binary" in msg
        assert "OPENDV_CLAUDE_CLI_BIN" in msg
        assert "PATH" in msg


def test_resolve_hub_and_uri_fail_fast_on_unmapped_concept() -> None:
    """Verify AUDIT-01: unmapped concept raises InputValidationError (no provisional URI)."""
    ont = GraphAOntology(
        domain_namespace="private_equity",
        enterprise_owner="Apex Horizon",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:portfolio_company",
                concept_name="PORTFOLIO_COMPANY",
                elm_archetype="HUB",
                definition="A target company invested in or acquired by a PE fund.",
                target_hub="hub_portfolio_company",
            ),
        ],
    )

    # Valid concept resolves
    hub_name, uri = _resolve_hub_and_uri("PORTFOLIO_COMPANY", ont)
    assert hub_name == "hub_portfolio_company"
    assert uri == "urn:ontology:entity:portfolio_company"

    # Unmapped concept raises InputValidationError (no provisional URI synthesized)
    with pytest.raises(InputValidationError) as exc_info:
        _resolve_hub_and_uri("UNKNOWN_GHOST_CONCEPT", ont)

    exc = exc_info.value
    assert exc.error_code == ErrorCode.INPUT_VALIDATION_ERROR
    assert "Unmapped ontology concept 'UNKNOWN_GHOST_CONCEPT'" in exc.message
    assert "urn:ontology:provisional:entity" not in exc.message


def test_resolve_relationship_uri_fail_fast_on_unmapped_link() -> None:
    """Verify AUDIT-01: unmapped relationship raises InputValidationError."""
    ont = GraphAOntology(
        domain_namespace="private_equity",
        enterprise_owner="Apex Horizon",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:fund",
                concept_name="FUND",
                elm_archetype="HUB",
                definition="PE investment fund entity.",
                target_hub="hub_fund",
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:deal",
                concept_name="DEAL",
                elm_archetype="HUB",
                definition="PE acquisition deal entity.",
                target_hub="hub_deal",
            ),
        ],
        canonical_relationships=[
            CanonicalRelationship(
                relationship_uri="urn:ontology:relationship:fund_deal",
                relationship_name="FUND_DEAL",
                target_link="lnk_fund_deal",
                endpoints=["hub_fund", "hub_deal"],
                grain_semantics="One fund may participate in multiple deals.",
            ),
        ],
    )

    # Valid link resolves
    uri = _resolve_relationship_uri("lnk_fund_deal", ["hub_fund", "hub_deal"], ont)
    assert uri == "urn:ontology:relationship:fund_deal"

    # Unmapped link raises InputValidationError
    with pytest.raises(InputValidationError) as exc_info:
        _resolve_relationship_uri("lnk_ghost_link", ["hub_unknown_1", "hub_unknown_2"], ont)

    exc = exc_info.value
    assert exc.error_code == ErrorCode.INPUT_VALIDATION_ERROR
    assert "Unmapped ontology relationship for link 'lnk_ghost_link'" in exc.message
