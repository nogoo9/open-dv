"""Phase 2 TDD Tests: 8-Stage Internal Pipeline & Step 6 7-Pill Console Contracts (`FR-DVM-01`).

Covers:
1. All 8 canonical internal `PipelineStageId`s (`STAGE_00_ORCHESTRATION`..`STAGE_07_DETERMINISTIC_COMPILER`)
   and step-report compatibility in `AgenticVaultPipeline`.
2. `HarnessSetupConfig.stage_output_policy` defaults to `"strict"` while retaining `"llm_only"` and
   `"symbolic_completion"` compatibility (`FR-DVM-01` / `FR-AGT-01`).
3. Fail-closed execution when `stage_output_policy="strict"` and no real LLM harness is active.
4. Step 6 `00..06` console pill projection compatibility on `GET /api/v1/ontology/studio/sessions/{id}/logs`
   per `docs/28 §4.4` (`03` -> `STAGE_03+04`, `04` -> `STAGE_05`, `05` -> `STAGE_07`, `06` -> `STAGE_06`).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from opendv_agent_harness.models import PipelineStageId
from opendv_agent_harness.pipeline.config import HarnessSetupConfig
from opendv_agent_harness.pipeline.runner import run_agentic_vault_pipeline
from opendv_agent_harness.pipeline.spec import PipelineInputSpec
from opendv_platform_api import create_app
from opendv_service import OpenDVSettings
from opendv_testkit import build_deterministic_agy_sdk_shim

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"


def test_pipeline_stage_id_preserves_all_8_canonical_stages() -> None:
    """Internal engine preserves exact 8 canonical STAGE_00..STAGE_07 PipelineStageId values."""
    expected = [
        "STAGE_00_ORCHESTRATION",
        "STAGE_01_SCOUT_SOURCE",
        "STAGE_02_SCOUT_UNSTRUCTURED",
        "STAGE_03_SHIP_VAULT_PASS1",
        "STAGE_04_SHIP_VAULT_PASS2",
        "STAGE_05_AXI_DV_STATIC_GATES",
        "STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
        "STAGE_07_DETERMINISTIC_COMPILER",
    ]
    assert [s.value for s in PipelineStageId] == expected


def test_harness_setup_config_defaults_to_strict_policy_with_legacy_compat() -> None:
    """Default `stage_output_policy` must be `'strict'`, keeping `'llm_only'` and `'symbolic_completion'`."""
    default_cfg = HarnessSetupConfig()
    assert default_cfg.stage_output_policy == "strict"

    for policy in ("strict", "llm_only", "symbolic_completion"):
        cfg = HarnessSetupConfig(stage_output_policy=policy)  # type: ignore[arg-type]
        assert cfg.stage_output_policy == policy


def test_strict_policy_fails_closed_when_harness_unconfigured(tmp_path: Path) -> None:
    """With default `stage_output_policy='strict'` and `harness=None`, pipeline must fail closed."""
    env_keys = (
        "PATH",
        "OPENDV_AGY_CLI_BIN",
        "OPENDV_CLAUDE_CLI_BIN",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENDV_AGENT_PROVIDER",
    )
    saved_env = {k: os.environ.get(k) for k in env_keys}
    try:
        for key in env_keys:
            os.environ.pop(key, None)
        os.environ["PATH"] = ""

        spec = PipelineInputSpec.from_file(SAMPLE_DIR / "step_0_cold_start.yaml")
        default_setup = HarnessSetupConfig()
        assert default_setup.stage_output_policy == "strict"

        report = run_agentic_vault_pipeline(
            spec,
            default_setup,
            output_dir=tmp_path / "out_strict",
            base_dir=SAMPLE_DIR,
            harness=None,
        )
        assert report.status == "FAILED"
        assert report.overall_passed is False
        assert report.fallbacks_applied == []
    finally:
        for key, prev in saved_env.items():
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev


def test_symbolic_completion_emits_all_8_internal_stages(tmp_path: Path) -> None:
    """Explicit `symbolic_completion` emits all 8 canonical internal STAGE_00..07 step reports."""
    spec = PipelineInputSpec.from_file(SAMPLE_DIR / "step_0_cold_start.yaml")
    setup = HarnessSetupConfig(provider="agy", stage_output_policy="symbolic_completion")
    report = run_agentic_vault_pipeline(
        spec,
        setup,
        output_dir=tmp_path / "out_sym",
        base_dir=SAMPLE_DIR,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )
    assert report.status == "COMPLETED"
    emitted_ids = [step.stage_id for step in report.step_reports]
    for canonical in PipelineStageId:
        assert canonical.value in emitted_ids


def test_step6_7pill_console_projection_compatibility_on_logs_api(tmp_path: Path) -> None:
    """Step 6 console pill aliases (`00`..`06`) resolve cleanly onto `STAGE_00..07` on `/logs`."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    settings = OpenDVSettings.load(workspace_root=ws, state_dir=tmp_path / "state", is_server=True)
    app = create_app(settings=settings, workspace_root=ws, storage_root=tmp_path / "state")
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")

    sess_resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "sess-7pill-01", "domain_namespace": "test_domain"},
    )
    assert sess_resp.status_code in (200, 201), sess_resp.text

    expected_pill_mapping = {
        "00": {"STAGE_00_ORCHESTRATION"},
        "01": {"STAGE_01_SCOUT_SOURCE"},
        "02": {"STAGE_02_SCOUT_UNSTRUCTURED"},
        "03": {"STAGE_03_SHIP_VAULT_PASS1", "STAGE_04_SHIP_VAULT_PASS2"},
        "04": {"STAGE_05_AXI_DV_STATIC_GATES"},
        "05": {"STAGE_07_DETERMINISTIC_COMPILER"},
        "06": {"STAGE_06_AXI_DV_DYNAMIC_SANDBOX"},
    }
    for pill, expected_stages in expected_pill_mapping.items():
        resp = client.get(f"/api/v1/ontology/studio/sessions/sess-7pill-01/logs?stage_id={pill}")
        assert resp.status_code == 200, f"Pill '{pill}' failed: {resp.status_code} {resp.text}"
        body = resp.json()
        resolved = set(body.get("resolved_stage_ids") or [])
        assert expected_stages.issubset(resolved), (
            f"Pill '{pill}' expected {expected_stages}, got {resolved}"
        )
