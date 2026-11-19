"""Phase 2 TDD Tests: CLI `stdin`, Stage Context, Budget, & Preflight (Audit #2/#3/#10/#16/#17).

Covers:
1. Audit #3/#16: Prompts `> 24 KB` are piped via `stdin` (not `argv`) in `AgyCliHarnessAdapter`
   and `ClaudeCliHarnessAdapter`.
2. Audit #2: `execute_stage_task` formats structured stage context inline (preserving it through
   CLI prompt building) and propagates `ProviderConfigurationError` instead of swallowing it.
3. `HarnessBudget`: `BaseVaultAgentHarness.run_task()` enforces `TOKEN_BUDGET_EXCEEDED` and
   `COST_BUDGET_EXCEEDED`.
4. Audit #10/#17: Centralized `CREDENTIALS_REF_RE` in `opendv_core.config` accepts lowercase
   `env:my_var-1`, and `validation_preflight` uses centralized credentials ref + Windows-normcase
   `resolve_workspace_path`.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from opendv_agent_harness.adapters.agy_cli_adapter import AgyCliHarnessAdapter
from opendv_agent_harness.adapters.antigravity_adapter import ProviderConfigurationError
from opendv_agent_harness.adapters.claude_cli_adapter import ClaudeCliHarnessAdapter
from opendv_agent_harness.models import (
    AgentPersona,
    AgentTaskSpec,
    HarnessBudget,
    HarnessConfig,
    HarnessEventKind,
    HarnessStopReason,
    HarnessStreamEvent,
    ModelTier,
    PipelineStageId,
    ToolExecutionContext,
)
from opendv_agent_harness.pipeline.spec import (
    PipelineInputSpec,
    Stage00OrchestrationPlanOutput,
)
from opendv_agent_harness.pipeline.stages_agentic import execute_stage_task
from opendv_agent_harness.pipeline.validation_preflight import validate_pipeline_inputs
from opendv_agent_harness.protocol import BaseVaultAgentHarness


def _make_cli_cfg(
    tmp_path: Path, provider: str, runner_key: str, runner_fn: Any, bin_env: str
) -> HarnessConfig:
    fake_exe = tmp_path / f"fake_{provider}.exe"
    fake_exe.write_text("echo ok", encoding="utf-8")
    return HarnessConfig(
        provider=provider,  # type: ignore[arg-type]
        workspace_dir=tmp_path,
        state_dir=tmp_path / "state",
        app_data_dir=tmp_path / "app",
        save_dir=tmp_path / "save",
        env={bin_env: str(fake_exe)},
        extra_provider_options={runner_key: runner_fn},
    )


@pytest.mark.asyncio
async def test_cli_adapters_pipe_large_24kb_prompt_via_stdin_not_argv(tmp_path: Path) -> None:
    """Both `AgyCliHarnessAdapter` and `ClaudeCliHarnessAdapter` pipe >24KB prompts via `stdin`."""
    large_marker = "LARGE_PROMPT_PAYLOAD_" + ("X" * 26_000)

    for adapter_cls, prov, runner_key, bin_env in (
        (AgyCliHarnessAdapter, "agy", "agy_cli_runner", "OPENDV_AGY_CLI_BIN"),
        (ClaudeCliHarnessAdapter, "claude-cli", "claude_cli_runner", "OPENDV_CLAUDE_CLI_BIN"),
    ):
        captured: list[tuple[list[str], str | None]] = []

        def _capture_runner(
            cmd: list[str],
            *,
            env: dict[str, str],
            timeout: float,
            input_text: str | None = None,
            sink: list[tuple[list[str], str | None]] = captured,
        ) -> subprocess.CompletedProcess[str]:
            del env, timeout
            sink.append((list(cmd), input_text))
            payload = {
                "type": "result",
                "status": "SUCCESS",
                "structured_output": {"plan_summary": "ok", "inferred_action": "COLD_START_N1"},
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            }
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=json.dumps(payload), stderr=""
            )

        cfg = _make_cli_cfg(tmp_path, prov, runner_key, _capture_runner, bin_env)
        adapter = adapter_cls(cfg)
        spec = AgentTaskSpec(
            task_id=f"task-{prov}",
            run_id="run-stdin-01",
            trace_id="a" * 32,
            persona=AgentPersona.VAULT_FIRST_MATE,
            stage_id=PipelineStageId.STAGE_00_ORCHESTRATION,
            prompt=large_marker,
            system_instructions="Return JSON plan.",
            output_schema=Stage00OrchestrationPlanOutput,
        )
        res = await adapter.run_task(spec)
        assert res.stop_reason == HarnessStopReason.COMPLETED
        assert len(captured) == 1
        cmd, stdin_text = captured[0]
        assert all(len(arg) < 8000 for arg in cmd), (
            f"{prov} exceeded Windows argv safety: {[len(a) for a in cmd]}"
        )
        assert stdin_text is not None and large_marker in stdin_text, (
            f"{prov} did not pipe prompt via stdin"
        )


@pytest.mark.asyncio
async def test_execute_stage_task_preserves_inline_context_and_propagates_provider_error(
    tmp_path: Path,
) -> None:
    """`execute_stage_task` keeps inline context visible and raises `ProviderConfigurationError`."""
    captured_inputs: list[str] = []

    def _ctx_runner(
        cmd: list[str],
        *,
        env: dict[str, str],
        timeout: float,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del env, timeout
        captured_inputs.append(input_text if input_text is not None else " ".join(cmd))
        payload = {
            "type": "result",
            "status": "SUCCESS",
            "structured_output": {"plan_summary": "inline ok", "inferred_action": "COLD_START_N1"},
        }
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=json.dumps(payload), stderr=""
        )

    cfg = _make_cli_cfg(tmp_path, "agy", "agy_cli_runner", _ctx_runner, "OPENDV_AGY_CLI_BIN")
    adapter = AgyCliHarnessAdapter(cfg)

    metrics, out = await execute_stage_task(
        0,
        "STAGE_00_ORCHESTRATION",
        "vault-first-mate",
        harness=adapter,
        run_id="run-ctx-01",
        context={"domain_namespace": "treasury_ops", "sources": [{"table_name": "fx_trades"}]},
    )
    assert metrics.get("llm_structured_plan_used") is True
    assert out is not None
    assert captured_inputs
    assert "treasury_ops" in captured_inputs[0]
    assert "fx_trades" in captured_inputs[0]
    assert "__OPENDV_STAGE_CONTEXT_JSON__" not in captured_inputs[0]

    class _MissingCredsHarness:
        async def run_task(self, spec: AgentTaskSpec[Any], **kwargs: Any) -> Any:
            del spec, kwargs
            raise ProviderConfigurationError("No API key or CLI login available.")

    with pytest.raises(ProviderConfigurationError):
        await execute_stage_task(
            0,
            "STAGE_00_ORCHESTRATION",
            "vault-first-mate",
            harness=_MissingCredsHarness(),
            run_id="run-err-01",
        )


@pytest.mark.asyncio
async def test_harness_budget_enforces_token_and_cost_stop_reasons(tmp_path: Path) -> None:
    """`BaseVaultAgentHarness.run_task()` enforces token and cost budget stop reasons."""

    class _UsageHarness(BaseVaultAgentHarness):
        def __init__(self, config: HarnessConfig, usage_payload: dict[str, Any]) -> None:
            super().__init__(config)
            self._usage_payload = usage_payload

        @property
        def provider_name(self) -> str:
            return "agy"

        def resolve_model(self, tier: ModelTier, persona: AgentPersona | None = None) -> str:
            del tier, persona
            return "gemini-4-argon-high"

        async def _stream_provider_task(
            self, spec: AgentTaskSpec[Any], ctx: ToolExecutionContext, model_id: str
        ) -> AsyncIterator[HarnessStreamEvent]:
            del model_id
            yield self._make_event(
                spec,
                ctx,
                HarnessEventKind.TURN_COMPLETE,
                tool_result={"usage": self._usage_payload},
            )

    spec = AgentTaskSpec(
        task_id="task-budget",
        run_id="run-budget",
        trace_id="b" * 32,
        persona=AgentPersona.VAULT_FIRST_MATE,
        stage_id=PipelineStageId.STAGE_00_ORCHESTRATION,
        prompt="Compute budget test",
        system_instructions="Respond.",
    )

    tok_cfg = HarnessConfig(
        provider="agy",
        workspace_dir=tmp_path,
        state_dir=tmp_path / "s1",
        app_data_dir=tmp_path / "a1",
        save_dir=tmp_path / "v1",
        budget=HarnessBudget(max_total_tokens=2000, max_cost_usd=10.0),
    )
    tok_res = await _UsageHarness(
        tok_cfg, {"input_tokens": 1800, "output_tokens": 500, "total_tokens": 2300, "cost_usd": 0.1}
    ).run_task(spec)
    assert tok_res.stop_reason == HarnessStopReason.TOKEN_BUDGET_EXCEEDED

    cost_cfg = HarnessConfig(
        provider="agy",
        workspace_dir=tmp_path,
        state_dir=tmp_path / "s2",
        app_data_dir=tmp_path / "a2",
        save_dir=tmp_path / "v2",
        budget=HarnessBudget(max_total_tokens=100_000, max_cost_usd=1.0),
    )
    cost_res = await _UsageHarness(
        cost_cfg, {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150, "cost_usd": 2.5}
    ).run_task(spec)
    assert cost_res.stop_reason == HarnessStopReason.COST_BUDGET_EXCEEDED


def test_preflight_uses_centralized_credentials_ref_and_windows_normcase_paths(
    tmp_path: Path,
) -> None:
    """`validation_preflight` accepts lowercase `env:my_var-1` and case-normalized paths."""
    from opendv_core.config import CREDENTIALS_REF_RE, validate_credentials_ref

    assert CREDENTIALS_REF_RE.match("env:my_var-1")
    assert validate_credentials_ref("env:my_var-1") == "env:my_var-1"
    with pytest.raises(ValueError):
        validate_credentials_ref("plain-secret")

    ws = tmp_path / "WorkSpace"
    ws.mkdir()
    csv_file = ws / "orders.csv"
    csv_file.write_text("order_id,amount\nO-1,10.0\n", encoding="utf-8")
    ont_file = ws / "ont.yaml"
    ont_file.write_text(
        "ontology_version: '1.0.0'\ndomain_namespace: test_dom\n"
        "canonical_business_concepts:\n"
        "  - concept_uri: urn:ontology:entity:order\n"
        "    concept_name: Order\n"
        "    elm_archetype: WHAT_RESOURCE_EVENT\n"
        "    definition: Sales order\n",
        encoding="utf-8",
    )

    spec = PipelineInputSpec.model_validate(
        {
            "pipeline_id": "cred_ref_test",
            "project_id": "proj_cred",
            "domain_namespace": "test_dom",
            "graph_a_ontology_path": str(ont_file),
            "sources": [
                {
                    "feed_id": "orders",
                    "source_system": "erp",
                    "table_name": "orders",
                    "data_path": str(csv_file),
                    "business_key_columns": ["order_id"],
                    "credentials_ref": "env:my_var-1",
                }
            ],
        }
    )
    rep = validate_pipeline_inputs(spec, base_dir=ws, workspace_root=ws)
    assert rep.passed is True, f"Preflight rejected lowercase env:my_var-1: {rep.diagnostics}"
