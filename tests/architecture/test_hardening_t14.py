"""Static scans for paths, endpoints, PE identifiers, and T14 behavioral invariants.

Enforces:
1. No hardcoded paths (/tmp, home, expanduser, OS install paths, find_workspace_root, REPO_ROOT).
2. No unparameterized 127.0.0.1 / localhost literals in production.
3. No PE domain identifiers in production packages (CFG-07 sandbox exempted pending P5.2).
4. Strictly two noqa: BLE001 boundary exemptions (CLI main and API global handler).
5. T14 Behavioral Invariants:
   - Out-of-repo execution with spaces and non-ASCII paths.
   - Missing configuration fail-fast on server startup listing checked sources.
   - No transport fallback on unavailable transport.
   - Fault-injection visibility (CAS corruption -> CAS_INTEGRITY_FAILURE).
"""

from __future__ import annotations

import ast
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from starlette.testclient import TestClient

from opendv_agent_harness import HarnessConfig, SandboxPolicy
from opendv_agent_harness.models import (
    HarnessRunResult,
    HarnessStopReason,
    HarnessStreamEvent,
)
from opendv_agent_harness.pipeline import (
    HarnessSetupConfig,
    PipelineInputSpec,
    run_agentic_vault_pipeline,
)
from opendv_agent_harness.protocol import VaultAgentHarness
from opendv_agent_harness.adapters.antigravity_adapter import (
    AntigravityHarnessAdapter,
)
from opendv_agent_harness.adapters.antigravity_adapter import (
    ProviderConfigurationError as AgyProviderConfigError,
)
from opendv_agent_harness.adapters.claude_adapter import (
    ClaudeAgentHarnessAdapter,
)
from opendv_agent_harness.adapters.claude_adapter import (
    ProviderConfigurationError as ClaudeProviderConfigError,
)
from opendv_cli.executor import LocalExecutor
from opendv_cli.main import build_parser
from opendv_contracts.errors import ErrorCode
from opendv_platform_api.main import create_app
from opendv_service import (
    ConfigurationError,
    CreateProjectRequest,
    CreateRunRequest,
    OpenDVError,
    OpenDVService,
    OpenDVSettings,
    PlatformStateContainer,
)
from opendv_service.models import ArtifactUploadRequest
from tests.architecture.conftest import REPO_ROOT
from tests.architecture.test_mock_guards import PRODUCTION_PY_DIRS

BANNED_PATH_LITERALS = [
    "/tmp",
    "/usr",
    "/etc",
    "C:\\Program Files",
    "C:\\Windows",
    "find_workspace_root",
    "REPO_ROOT",
    "expanduser",
]

BANNED_RELATIVE_PREFIXES = ["examples/", "docs/"]

BANNED_PE_IDENTIFIERS = [
    "hub_fund",
    "hub_limited_partner",
    "lp-101",
    "proj-apex",
    "run-pe-apex",
    "calpers",
    "is_pe_domain",
]

ALLOWLISTED_BLE001_BOUNDARIES = {
    "apps/opendv-cli/src/opendv_cli/main.py": "CLI main top-level execution boundary",
    "apps/opendv-platform-api/src/opendv_platform_api/main.py": (
        "API global exception handler boundary"
    ),
}


def _extract_docstring_nodes(tree: ast.AST) -> set[ast.Constant]:
    """Identify and collect all docstring AST Constant nodes in a module."""
    docstrings: set[ast.Constant] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)
        ) and (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            docstrings.add(node.body[0].value)
    return docstrings


@pytest.mark.architecture
@pytest.mark.hardening
@pytest.mark.parametrize("rel_dir", PRODUCTION_PY_DIRS)
def test_no_hardcoded_paths_in_production(rel_dir: str) -> None:
    """Assert zero hardcoded paths, OS dirs, home dirs, or repo roots in production."""
    target_dir = REPO_ROOT / rel_dir
    assert target_dir.exists(), f"Target directory does not exist: {target_dir}"

    violations: list[str] = []
    for py_file in target_dir.rglob("*.py"):
        # Exclude settings and offline export utility
        if py_file.name in {"settings.py", "export_openapi.py"}:
            continue

        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value
                for banned in BANNED_PATH_LITERALS:
                    if banned in val:
                        rel = py_file.relative_to(REPO_ROOT)
                        violations.append(f"{rel}:{node.lineno}: banned path literal '{val}'")
                for pref in BANNED_RELATIVE_PREFIXES:
                    # Allow schema registry normative example path keys in validate_all_examples
                    if (
                        (val.startswith(pref) or f"/{pref}" in val)
                        and "schema_registry.py" not in py_file.name
                    ):
                        rel = py_file.relative_to(REPO_ROOT)
                        violations.append(f"{rel}:{node.lineno}: banned prefix '{val}'")
            elif isinstance(node, ast.Attribute) and node.attr in {"home", "expanduser"}:
                rel = py_file.relative_to(REPO_ROOT)
                violations.append(f"{rel}:{node.lineno}: banned method call '{node.attr}'")

    assert not violations, (
        f"Production source directory '{rel_dir}' contains hardcoded paths:\n"
        + "\n".join(violations)
    )


@pytest.mark.architecture
@pytest.mark.hardening
@pytest.mark.parametrize("rel_dir", PRODUCTION_PY_DIRS)
def test_no_unparameterized_endpoints_in_production(rel_dir: str) -> None:
    """Assert zero unparameterized localhost/127.0.0.1 literals in production sources."""
    target_dir = REPO_ROOT / rel_dir
    assert target_dir.exists(), f"Target directory does not exist: {target_dir}"

    violations: list[str] = []
    for py_file in target_dir.rglob("*.py"):
        if py_file.name == "settings.py":
            continue

        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val_lower = node.value.lower()
                if "127.0.0.1" in val_lower or "localhost" in val_lower:
                    rel = py_file.relative_to(REPO_ROOT)
                    violations.append(
                        f"{rel}:{node.lineno}: unparameterized endpoint '{node.value}'"
                    )

    assert not violations, (
        f"Production source directory '{rel_dir}' contains hardcoded endpoints:\n"
        + "\n".join(violations)
    )


@pytest.mark.architecture
@pytest.mark.hardening
@pytest.mark.parametrize("rel_dir", PRODUCTION_PY_DIRS)
def test_no_pe_identifiers_in_production(rel_dir: str) -> None:
    """Assert zero PE dataset identifiers in production code (CFG-07 completed in P5.2)."""
    target_dir = REPO_ROOT / rel_dir
    assert target_dir.exists(), f"Target directory does not exist: {target_dir}"

    violations: list[str] = []
    for py_file in target_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        docstring_nodes = _extract_docstring_nodes(tree)

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node in docstring_nodes:
                    continue  # Domain identifiers in docs are allowed per T14 specification
                val_lower = node.value.lower()
                for banned in BANNED_PE_IDENTIFIERS:
                    if banned in val_lower:
                        rel = py_file.relative_to(REPO_ROOT)
                        violations.append(f"{rel}:{node.lineno}: PE string literal '{node.value}'")
                if "investran" in val_lower:
                    rel = py_file.relative_to(REPO_ROOT)
                    violations.append(f"{rel}:{node.lineno}: PE string literal '{node.value}'")
            elif isinstance(node, ast.Name):
                name_lower = node.id.lower()
                for banned in BANNED_PE_IDENTIFIERS:
                    if banned in name_lower:
                        rel = py_file.relative_to(REPO_ROOT)
                        violations.append(f"{rel}:{node.lineno}: PE identifier Name '{node.id}'")
            elif isinstance(node, ast.Attribute):
                attr_lower = node.attr.lower()
                for banned in BANNED_PE_IDENTIFIERS:
                    if banned in attr_lower:
                        rel = py_file.relative_to(REPO_ROOT)
                        violations.append(f"{rel}:{node.lineno}: PE attribute '{node.attr}'")

    assert not violations, (
        f"Production directory '{rel_dir}' contains PE dataset identifiers:\n"
        + "\n".join(violations)
    )


@pytest.mark.architecture
@pytest.mark.hardening
def test_noqa_ble001_strictly_limited_to_allowlisted_boundaries() -> None:
    """Assert noqa: BLE001 in production is strictly limited to allowlisted boundaries."""
    found_exemptions: dict[str, str] = {}
    violations: list[str] = []

    for rel_dir in PRODUCTION_PY_DIRS:
        target_dir = REPO_ROOT / rel_dir
        for py_file in target_dir.rglob("*.py"):
            lines = py_file.read_text(encoding="utf-8").splitlines()
            for lineno, line in enumerate(lines, start=1):
                if "# noqa: BLE001" in line or "# noqa:BLE001" in line:
                    rel_path = str(py_file.relative_to(REPO_ROOT)).replace("\\", "/")
                    found_exemptions[rel_path] = line.strip()
                    if rel_path not in ALLOWLISTED_BLE001_BOUNDARIES:
                        violations.append(
                            f"{rel_path}:{lineno}: unallowlisted noqa: BLE001: '{line.strip()}'"
                        )
                    else:
                        expected_reason = ALLOWLISTED_BLE001_BOUNDARIES[rel_path]
                        if expected_reason not in line:
                            violations.append(
                                f"{rel_path}:{lineno}: missing required rationale "
                                f"'{expected_reason}' in '{line.strip()}'"
                            )

    assert not violations, (
        "Forbidden or undocumented noqa: BLE001 exemptions detected:\n" + "\n".join(violations)
    )
    assert len(found_exemptions) == 2, (
        f"Expected exactly 2 allowlisted noqa: BLE001 boundaries, found {len(found_exemptions)}: "
        f"{list(found_exemptions.keys())}"
    )


@pytest.mark.architecture
@pytest.mark.hardening
def test_out_of_repo_run(tmp_path: Path) -> None:
    """T14 Invariant: CLI and API run in arbitrary directories with spaces and non-ASCII chars."""
    out_of_repo = tmp_path / "custom test ws with spaces and ünicöde"
    ws_dir = out_of_repo / "workspace"
    state_dir = out_of_repo / "state"
    ws_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)

    # 1. State container and service operate in external directory
    container = PlatformStateContainer(workspace_root=ws_dir, storage_root=state_dir)
    service = OpenDVService(state_store=container, workspace_root=ws_dir, storage_root=state_dir)

    proj = service.create_project(
        CreateProjectRequest(
            project_id="proj-ext-01",
            display_name="External Project",
        )
    )
    assert proj.project_id == "proj-ext-01"

    run = service.create_run(
        CreateRunRequest(
            project_id="proj-ext-01",
            harness_provider="antigravity",
        )
    )
    assert run.project_id == "proj-ext-01"
    assert run.run_id

    # 2. LocalExecutor runs in external directory
    executor = LocalExecutor(service=service, workspace_root=ws_dir)
    parser = build_parser("opendv")
    parsed_args = parser.parse_args(["run", "list", "--project", "proj-ext-01", "--json"])
    code, payload = executor.execute("run", parsed_args)
    assert code == 0
    assert payload.get("total") == 1

    # 3. FastAPI test client runs in external directory
    app = create_app(platform_state=container, workspace_root=ws_dir)
    client = TestClient(app)
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


@pytest.mark.architecture
@pytest.mark.hardening
def test_missing_config_fail_fast_server_startup() -> None:
    """T14 Invariant: Server startup fails fast when workspace_root is omitted, listing sources."""
    env_vars = (
        "OPENDV_WORKSPACE_ROOT",
        "OPENDV_STORAGE_ROOT",
        "OPENDV_STATE_DIR",
        "OPENDV_CONFIG",
    )
    saved = {v: os.environ.pop(v) for v in env_vars if v in os.environ}
    try:
        with pytest.raises(ConfigurationError) as exc_info:
            OpenDVSettings.load(is_server=True, workspace_root=None)
    finally:
        os.environ.update(saved)

    msg = str(exc_info.value)
    assert "workspace_root" in msg
    assert "Checked sources:" in msg
    assert "--workspace" in msg
    assert "OPENDV_WORKSPACE_ROOT" in msg
    assert "opendv.config.yaml" in msg


@pytest.mark.architecture
@pytest.mark.hardening
def test_no_transport_fallback(tmp_path: Path) -> None:
    """T14 Invariant: Requesting transport='sdk' when SDK is missing fails fast with no fallback."""
    ws = tmp_path / "workspace"
    st = tmp_path / "state"
    app = tmp_path / "app_data"
    save = tmp_path / "save"
    for p in (ws, st, app, save):
        p.mkdir(parents=True, exist_ok=True)

    cfg = HarnessConfig(
        workspace_dir=ws.resolve(),
        state_dir=st.resolve(),
        app_data_dir=app.resolve(),
        save_dir=save.resolve(),
        sandbox=SandboxPolicy(),
        extra_provider_options={"transport": "sdk"},
    )
    agy_adapter = AntigravityHarnessAdapter(config=cfg, sdk_module=None)
    with pytest.raises(AgyProviderConfigError) as exc_agy:
        agy_adapter._load_sdk()
    assert "transport 'sdk' is unavailable" in str(exc_agy.value)

    claude_adapter = ClaudeAgentHarnessAdapter(config=cfg, sdk_module=None)
    with pytest.raises(ClaudeProviderConfigError) as exc_claude:
        claude_adapter._load_sdk()
    assert "transport 'sdk' is unavailable" in str(exc_claude.value)


@pytest.mark.architecture
@pytest.mark.hardening
def test_fault_injection_visible(tmp_path: Path) -> None:
    """T14 Invariant: Injected CAS corruption surfaces loudly as CAS_INTEGRITY_FAILURE."""
    ws = tmp_path / "ws"
    state = tmp_path / "state"
    ws.mkdir(parents=True)
    state.mkdir(parents=True)

    container = PlatformStateContainer(workspace_root=ws, storage_root=state)
    meta = container.store_artifact(
        ArtifactUploadRequest(
            artifact_name="vault_model.json",
            media_type="application/json",
            content={"hubs": ["hub_sample"]},
        )
    )
    digest = meta.sha256

    # Verify healthy retrieval initially
    healthy = container.get_artifact(digest)
    assert healthy.sha256 == digest

    # Corrupt artifact bytes on disk
    cas_file = container.cas_root / digest
    assert cas_file.exists(), f"CAS artifact file expected at {cas_file}"
    cas_file.write_bytes(b"tampered corrupt content")

    # Bypass in-memory cache to trigger disk read
    container.artifacts.clear()

    with pytest.raises(OpenDVError) as exc_info:
        container.get_artifact(digest)

    exc = exc_info.value
    assert exc.error_code == ErrorCode.CAS_INTEGRITY_FAILURE


class _FailingLlmHarness(VaultAgentHarness):
    """Harness adapter that simulates an LLM returning no structured output."""

    def resolve_model(self, tier: Any, persona: Any) -> str:
        return "failing-llm-stub"

    async def run_task(self, spec: Any) -> HarnessRunResult:
        return HarnessRunResult(
            task_id=spec.task_id,
            run_id=getattr(spec, "run_id", "run-t14-test"),
            session_id=f"sess-{spec.task_id}",
            trace_id=getattr(spec, "trace_id", "0" * 32),
            span_id=getattr(spec, "span_id", "0" * 16),
            provider="test-failing-provider",
            model_used="failing-llm-stub",
            stop_reason=HarnessStopReason.COMPLETED,
            text_output="Raw unstructured text from LLM.",
            structured_output=None,
        )

    async def run_streaming_task(self, spec: Any) -> AsyncIterator[HarnessStreamEvent]:
        yield HarnessStreamEvent(
            event_type="completed",
            text="Raw unstructured text from LLM.",
            result=await self.run_task(spec),
        )


@pytest.mark.architecture
@pytest.mark.hardening
def test_t14_no_synthesized_llm_output_by_default(tmp_path: Path) -> None:
    """T14 Invariant: Missing LLM output fails fast under default stage_output_policy='llm_only'."""
    sample_dir = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"
    input_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")
    single_wave_spec = input_spec.model_copy(update={"waves": [input_spec.waves[0]]})

    out_dir = tmp_path / "out_default"
    setup = HarnessSetupConfig(stage_output_policy="llm_only")
    failing_harness = _FailingLlmHarness()

    report = run_agentic_vault_pipeline(
        single_wave_spec,
        setup,
        output_dir=out_dir,
        base_dir=sample_dir,
        harness=failing_harness,
    )

    assert report.status == "FAILED"
    assert report.overall_passed is False
    assert report.fallbacks_applied == []
    assert "contract" not in report.final_artifacts
    assert "final_contract" not in report.final_artifacts


@pytest.mark.architecture
@pytest.mark.hardening
def test_t14_opt_in_fallback_visible(tmp_path: Path) -> None:
    """T14 Invariant: Explicit opt-in stage_output_policy='symbolic_completion' is fully auditable."""
    sample_dir = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"
    input_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")
    single_wave_spec = input_spec.model_copy(update={"waves": [input_spec.waves[0]]})

    out_dir = tmp_path / "out_opt_in"
    setup = HarnessSetupConfig(stage_output_policy="symbolic_completion")
    failing_harness = _FailingLlmHarness()

    report = run_agentic_vault_pipeline(
        single_wave_spec,
        setup,
        output_dir=out_dir,
        base_dir=sample_dir,
        harness=failing_harness,
    )

    assert report.status in {"COMPLETED", "PAUSED_HOLD"}
    assert len(report.fallbacks_applied) > 0
    assert all(
        fb.get("key") == "stage_output_policy.symbolic_completion"
        for fb in report.fallbacks_applied
    )
    fallback_events = [
        ev
        for ev in report.observability_events
        if ev.get("event_type") == "FALLBACK_APPLIED"
        or (
            isinstance(ev.get("layer2_visual_payload"), dict)
            and isinstance(ev["layer2_visual_payload"].get("summary_metrics"), dict)
            and ev["layer2_visual_payload"]["summary_metrics"].get("event_type")
            == "FALLBACK_APPLIED"
        )
    ]
    schema_path = REPO_ROOT / "schemas" / "vault-observability-event.schema.json"
    event_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    for ev in fallback_events:
        jsonschema.validate(instance=ev, schema=event_schema)


class _Stage04FailingLlmHarness(VaultAgentHarness):
    """Harness that succeeds through Stage 03, but returns None for Stage 04."""

    def __init__(self, tmp_path: Path) -> None:
        from opendv_agent_harness import HarnessConfig
        from opendv_testkit import DeterministicHarnessAdapter

        cfg = HarnessConfig(
            provider="antigravity",
            workspace_dir=tmp_path / "ws",
            state_dir=tmp_path / "state",
            app_data_dir=tmp_path / "app_data",
            save_dir=tmp_path / "save",
        )
        self._inner = DeterministicHarnessAdapter(cfg)

    def resolve_model(self, tier: Any, persona: Any) -> str:
        return self._inner.resolve_model(tier, persona)

    async def run_task(self, spec: Any) -> HarnessRunResult:
        from opendv_agent_harness.pipeline import PipelineStageId

        stage_id = getattr(spec, "stage_id", None)
        if stage_id == PipelineStageId.STAGE_04_SHIP_VAULT_PASS2:
            return HarnessRunResult(
                task_id=spec.task_id,
                run_id=getattr(spec, "run_id", "run-s4-fail"),
                session_id=f"sess-{spec.task_id}",
                trace_id=getattr(spec, "trace_id", "0" * 32),
                span_id=getattr(spec, "span_id", "0" * 16),
                provider="test-stage4-failing-provider",
                model_used="stage4-failing-llm-stub",
                stop_reason=HarnessStopReason.COMPLETED,
                text_output="Raw unstructured text from LLM.",
                structured_output=None,
            )
        return await self._inner.run_task(spec)

    async def run_streaming_task(self, spec: Any) -> AsyncIterator[HarnessStreamEvent]:
        res = await self.run_task(spec)
        yield HarnessStreamEvent(
            event_type="completed",
            text=res.text_output or "",
            result=res,
        )


@pytest.mark.architecture
@pytest.mark.hardening
def test_t14_stage04_fails_fast_under_llm_only(tmp_path: Path) -> None:
    """T14 Invariant: Stage 04 invalid LLM output fails fast under default stage_output_policy='llm_only'."""
    from opendv_agent_harness.pipeline import PipelineStageId

    sample_dir = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"
    input_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")
    single_wave_spec = input_spec.model_copy(update={"waves": [input_spec.waves[0]]})

    out_dir = tmp_path / "out_s4_fail"
    setup = HarnessSetupConfig(stage_output_policy="llm_only")
    harness = _Stage04FailingLlmHarness(tmp_path)

    report = run_agentic_vault_pipeline(
        single_wave_spec,
        setup,
        output_dir=out_dir,
        base_dir=sample_dir,
        harness=harness,
    )

    assert report.status == "FAILED"
    assert report.overall_passed is False
    assert report.fallbacks_applied == []
    s4_steps = [
        step
        for step in report.step_reports
        if step.stage_id == PipelineStageId.STAGE_04_SHIP_VAULT_PASS2.value
    ]
    assert len(s4_steps) == 1
    assert s4_steps[0].status == "ERROR"
    assert "Stage 04" in s4_steps[0].summary


@pytest.mark.architecture
@pytest.mark.hardening
def test_t14_fallback_registry_completeness() -> None:
    """T14 Invariant: All fallbacks in FALLBACK_REGISTRY are opt-in (default_enabled is False)."""
    from opendv_service import FALLBACK_REGISTRY, FallbackKey

    assert len(FALLBACK_REGISTRY) >= 3
    for fb_key, defn in FALLBACK_REGISTRY.items():
        assert isinstance(fb_key, FallbackKey)
        assert defn.default_enabled is False
        assert isinstance(defn.description, str) and len(defn.description) > 0
        assert isinstance(defn.valid_use_case, str) and len(defn.valid_use_case) > 0

    assert FallbackKey.STAGE_OUTPUT_SYMBOLIC.value == "stage_output_policy.symbolic_completion"
    assert FallbackKey.STAGE_OUTPUT_SYMBOLIC in FALLBACK_REGISTRY
