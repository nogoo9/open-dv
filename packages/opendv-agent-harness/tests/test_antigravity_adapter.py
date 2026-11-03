"""Unit tests for `AntigravityHarnessAdapter`, `registry`, `personas`, and `StewardHoldManager`.

Conforms to docs/09-agnostic-agent-harness-and-sdk-spec.md (§4.1, §5).
"""

from __future__ import annotations

import asyncio
import types
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from opendv_agent_harness import (
    DEFAULT_AGY_MODEL_MAP,
    AgentPersona,
    AntigravityHarnessAdapter,
    HarnessConfig,
    HarnessEventKind,
    HarnessStopReason,
    HarnessStreamEvent,
    ModelTier,
    PipelineStageId,
    SandboxPolicy,
    StewardHoldManager,
    VaultAgentHarness,
    VizId,
    WorkbenchViewId,
    build_default_domain_tool_map,
    build_persona_subagent_spec,
    build_persona_task_spec,
    build_scout_source_subagent_spec,
    build_scout_source_task_spec,
    build_scout_unstructured_subagent_spec,
    build_scout_unstructured_task_spec,
    build_ship_vault_subagent_spec,
    build_ship_vault_task_spec,
    build_vault_first_mate_subagents,
    build_vault_first_mate_task_spec,
    create_agent_harness,
    list_available_harnesses,
    register_harness,
)
from opendv_agent_harness.registry import _HARNESS_REGISTRY

SAMPLE_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"


class _SynthesisContractResult(BaseModel):
    contract_id: str
    hub_count: int
    verdict: str


def _make_config(tmp_path: Path, **overrides: Any) -> HarnessConfig:
    ws = tmp_path / "workspace"
    st = tmp_path / "state"
    app = tmp_path / "app_data"
    save = tmp_path / "save"
    for p in (ws, st, app, save):
        p.mkdir(parents=True, exist_ok=True)
    defaults: dict[str, Any] = {
        "provider": "antigravity",
        "workspace_dir": ws.resolve(),
        "state_dir": st.resolve(),
        "app_data_dir": app.resolve(),
        "save_dir": save.resolve(),
    }
    defaults.update(overrides)
    return HarnessConfig(**defaults)


class _FakeAgyPolicyModule:
    """Fake `google.antigravity.hooks.policy` recording all policy rules and predicates."""

    @staticmethod
    def deny_all() -> dict[str, Any]:
        return {"rule": "deny_all"}

    @staticmethod
    def workspace_only(paths: list[str]) -> dict[str, Any]:
        return {"rule": "workspace_only", "paths": list(paths)}

    @staticmethod
    def allow(tool_name: str) -> dict[str, Any]:
        return {"rule": "allow", "tool": tool_name}

    @staticmethod
    def deny(
        tool_name: str,
        *,
        when: Any = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        return {"rule": "deny", "tool": tool_name, "when": when, "name": name}


def _build_fake_antigravity_sdk(
    *,
    trigger_hold: bool = False,
    trigger_tool_error: bool = False,
    hold_stream_open_event: asyncio.Event | None = None,
    include_budget_config_cls: bool = True,
) -> tuple[Any, dict[str, Any]]:
    """Build an in-memory mock `google.antigravity` SDK module."""
    captured: dict[str, Any] = {
        "configs": [],
        "prompts": [],
        "post_hooks": [],
        "error_hooks": [],
        "cancelled": False,
    }

    class _AgentBehavior:
        AUTONOMOUS = "AUTONOMOUS"

    class _KwRecord:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            for k, v in kwargs.items():
                setattr(self, k, v)

    class _HookDecorators:
        @staticmethod
        def post_tool_call(fn: Any) -> Any:
            captured["post_hooks"].append(fn)
            return fn

        @staticmethod
        def on_tool_error(fn: Any) -> Any:
            captured["error_hooks"].append(fn)
            return fn

    class _FakeResponse:
        def __init__(self, agent_cfg: _KwRecord) -> None:
            self._agent_cfg = agent_cfg
            self.usage = {
                "input_tokens": 900,
                "output_tokens": 350,
                "reasoning_tokens": 200,
                "total_tokens": 1450,
                "model_calls": 1,
                "cost_usd": 0.025,
            }

        async def cancel(self) -> None:
            captured["cancelled"] = True
            if hold_stream_open_event is not None:
                hold_stream_open_event.set()

        @property
        def thoughts(self) -> Any:
            async def _gen() -> Any:
                yield "Evaluating Graph A Core Business Concepts..."
                yield "Checking 3-Tier BKCC authority..."

            return _gen()

        @property
        def tool_calls(self) -> Any:
            async def _gen() -> Any:
                yield types.SimpleNamespace(
                    id="agy-call-1",
                    name="profile_source_feed",
                    args={
                        "source_system": "INVESTRAN_PE_ERP",
                        "table_or_path": "raw_lp_commitments",
                    },
                )
                if trigger_hold:
                    yield types.SimpleNamespace(
                        id="agy-call-2",
                        name="request_steward_decision",
                        args={
                            "escalation_reason": "TIER_U3_AMBIGUOUS_ENTITY",
                            "headline": (
                                "Ambiguous entity mention 'Apex IV' requires steward decision."
                            ),
                            "rule_id": "ALG-A9B",
                        },
                    )

            return _gen()

        def __aiter__(self) -> Any:
            async def _gen() -> Any:
                tools_by_name = {fn.__name__: fn for fn in self._agent_cfg.kwargs.get("tools", [])}
                if "profile_source_feed" in tools_by_name:
                    out_json = await tools_by_name["profile_source_feed"](
                        source_system="INVESTRAN_PE_ERP",
                        table_or_path="raw_lp_commitments",
                        sample_rows=[{"lp_id": "LP-101", "commitment_usd": 500_000_000}],
                    )
                    for hk in captured["post_hooks"]:
                        await hk({"tool": "profile_source_feed", "output": out_json})

                if "emit_observability_event" in tools_by_name:
                    emit_json = await tools_by_name["emit_observability_event"](
                        headline="Profiled INVESTRAN_PE_ERP raw_lp_commitments with zero drift.",
                        status="PASS",
                    )
                    for hk in captured["post_hooks"]:
                        await hk({"tool": "emit_observability_event", "output": emit_json})

                if trigger_hold and "request_steward_decision" in tools_by_name:
                    hold_json = await tools_by_name["request_steward_decision"](
                        escalation_reason="TIER_U3_AMBIGUOUS_ENTITY",
                        headline="Ambiguous entity mention 'Apex IV' requires steward decision.",
                        candidate_options=[{"hub_key": "APEX_IV", "score": 0.62}],
                        rule_id="ALG-A9B",
                    )
                    for hk in captured["post_hooks"]:
                        await hk({"tool": "request_steward_decision", "output": hold_json})

                if trigger_tool_error:
                    for err_hk in captured["error_hooks"]:
                        err_msg = await err_hk(RuntimeError("Simulated tool failure"))
                        captured["last_error_hook_return"] = err_msg

                if hold_stream_open_event is not None:
                    await hold_stream_open_event.wait()

                yield "Completed Pass 1 "
                yield "and Pass 2 synthesis."

            return _gen()

        async def structured_output(self) -> dict[str, Any]:
            return {
                "contract_id": "dv_pe_model_contract_v5",
                "hub_count": 4,
                "verdict": "PASS",
            }

    class _FakeAgent:
        def __init__(self, agent_cfg: _KwRecord) -> None:
            self.agent_cfg = agent_cfg
            self.conversation_id = agent_cfg.kwargs.get("conversation_id") or "conv-agy-session-001"
            captured["configs"].append(agent_cfg)

        async def __aenter__(self) -> _FakeAgent:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def chat(self, prompt: str) -> _FakeResponse:
            captured["prompts"].append(prompt)
            return _FakeResponse(self.agent_cfg)

    types_ns = types.SimpleNamespace(
        AgentBehavior=_AgentBehavior,
        SubagentCapabilities=_KwRecord,
        SubagentConfig=_KwRecord,
        TemplatedSystemInstructions=_KwRecord,
        CapabilitiesConfig=_KwRecord,
    )
    if include_budget_config_cls:
        types_ns.BudgetConfig = _KwRecord

    sdk = types.SimpleNamespace(
        types=types_ns,
        hooks=types.SimpleNamespace(
            hooks=_HookDecorators,
            policy=_FakeAgyPolicyModule,
        ),
        LocalAgentConfig=_KwRecord,
        Agent=_FakeAgent,
    )
    return sdk, captured


def test_antigravity_model_tier_resolution_and_overrides(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    adapter = AntigravityHarnessAdapter(cfg)
    assert isinstance(adapter, VaultAgentHarness)
    assert adapter.provider_name == "antigravity"

    assert (
        adapter.resolve_model(ModelTier.HIGH_REASONING, AgentPersona.VAULT_FIRST_MATE)
        == DEFAULT_AGY_MODEL_MAP[ModelTier.HIGH_REASONING]
    )
    assert adapter.resolve_model(ModelTier.HIGH_REASONING) == "gemini-4-argon-high"
    assert (
        adapter.resolve_model(ModelTier.FAST_TIER, AgentPersona.SCOUT_SOURCE)
        == "gemini-3.8-flash-high"
    )

    override_cfg = _make_config(
        tmp_path,
        model_overrides={
            ModelTier.HIGH_REASONING: "gemini-4-argon-ultra-custom",
            ModelTier.FAST_TIER: "gemini-3.8-flash-lite-custom",
        },
    )
    override_adapter = AntigravityHarnessAdapter(override_cfg)
    assert override_adapter.resolve_model(ModelTier.HIGH_REASONING) == "gemini-4-argon-ultra-custom"
    assert override_adapter.resolve_model(ModelTier.FAST_TIER) == "gemini-3.8-flash-lite-custom"
    assert (
        override_adapter.resolve_model(ModelTier.HIGH_REASONING, AgentPersona.VAULT_FIRST_MATE)
        == "gemini-4-argon-ultra-custom"
    )
    assert (
        override_adapter.resolve_model(ModelTier.HIGH_REASONING, AgentPersona.SHIP_VAULT)
        == "gemini-4-argon-ultra-custom"
    )
    assert (
        override_adapter.resolve_model(ModelTier.FAST_TIER, AgentPersona.SCOUT_SOURCE)
        == "gemini-3.8-flash-lite-custom"
    )
    assert (
        override_adapter.resolve_model(ModelTier.FAST_TIER, AgentPersona.SCOUT_UNSTRUCTURED)
        == "gemini-3.8-flash-lite-custom"
    )

    # Verify per-agent agent_models take precedence over model_overrides
    agent_override_cfg = _make_config(
        tmp_path,
        model_overrides={
            "reasoning": "gemini-4-argon-ultra-custom",
            "fast": "gemini-3.8-flash-lite-custom",
        },
        agent_models={
            "scout-unstructured": "custom-unstructured-v1",
            "ship-vault": "custom-ship-v1",
        },
    )
    agent_override_adapter = AntigravityHarnessAdapter(agent_override_cfg)
    assert (
        agent_override_adapter.resolve_model(
            ModelTier.HIGH_REASONING, AgentPersona.VAULT_FIRST_MATE
        )
        == "gemini-4-argon-ultra-custom"
    )
    assert (
        agent_override_adapter.resolve_model(ModelTier.FAST_TIER, AgentPersona.SCOUT_SOURCE)
        == "gemini-3.8-flash-lite-custom"
    )
    assert (
        agent_override_adapter.resolve_model(ModelTier.FAST_TIER, AgentPersona.SCOUT_UNSTRUCTURED)
        == "custom-unstructured-v1"
    )
    assert (
        agent_override_adapter.resolve_model(ModelTier.HIGH_REASONING, AgentPersona.SHIP_VAULT)
        == "custom-ship-v1"
    )


def test_antigravity_9_tier_safety_policies_readonly_writable_and_shell_guard(
    tmp_path: Path,
) -> None:
    tool_map = build_default_domain_tool_map()
    spec = build_scout_source_task_spec(
        prompt="Profile raw_lp_commitments",
        tools=[tool_map["profile_source_feed"]],
    )

    # 1. Default read-only workspace, shell disabled: 9 base rules + 1 domain tool rule = 10 rules
    ro_cfg = _make_config(
        tmp_path,
        sandbox=SandboxPolicy(read_only_workspace=True, allow_shell_execution=False),
    )
    ro_adapter = AntigravityHarnessAdapter(ro_cfg)
    ro_policies = ro_adapter._build_safety_policies(_FakeAgyPolicyModule, spec)

    assert ro_policies[0] == {"rule": "deny_all"}
    assert ro_policies[1] == {"rule": "workspace_only", "paths": [str(ro_cfg.workspace_dir)]}
    allowed_builtins = [p["tool"] for p in ro_policies[2:6]]
    assert allowed_builtins == ["view_file", "list_directory", "find_file", "search_directory"]
    assert ro_policies[6] == {"rule": "deny", "tool": "create_file", "when": None, "name": None}
    assert ro_policies[7] == {"rule": "deny", "tool": "edit_file", "when": None, "name": None}
    assert ro_policies[8] == {"rule": "deny", "tool": "run_command", "when": None, "name": None}
    assert ro_policies[9] == {"rule": "allow", "tool": "profile_source_feed"}

    # 2. Writable workspace + shell execution enabled with blocked_shell_substrings predicate
    rw_cfg = _make_config(
        tmp_path,
        sandbox=SandboxPolicy(read_only_workspace=False, allow_shell_execution=True),
    )
    rw_adapter = AntigravityHarnessAdapter(rw_cfg)
    rw_policies = rw_adapter._build_safety_policies(_FakeAgyPolicyModule, spec)

    assert rw_policies[6] == {"rule": "allow", "tool": "create_file"}
    assert rw_policies[7] == {"rule": "allow", "tool": "edit_file"}
    shell_deny_rule = rw_policies[8]
    assert shell_deny_rule["rule"] == "deny"
    assert shell_deny_rule["tool"] == "run_command"
    assert shell_deny_rule["name"] == "deny_dangerous_shell_commands"
    predicate = shell_deny_rule["when"]
    assert callable(predicate)
    assert predicate({"CommandLine": "rm -rf /tmp/data"}) is True
    assert predicate({"CommandLine": "git push origin main"}) is True
    assert predicate({"CommandLine": "git worktree remove slot1"}) is True
    assert predicate({"CommandLine": "treehouse return"}) is True
    assert predicate({"CommandLine": "no-mistakes daemon stop"}) is True
    assert predicate({"CommandLine": "pytest -q"}) is False
    assert rw_policies[9] == {"rule": "allow", "tool": "run_command"}


@pytest.mark.asyncio
async def test_antigravity_stream_and_run_task_with_subagents_and_structured_output(
    tmp_path: Path,
) -> None:
    fake_sdk, captured = _build_fake_antigravity_sdk(
        trigger_hold=False,
        trigger_tool_error=True,
        include_budget_config_cls=False,
    )
    cfg = _make_config(tmp_path, env={"OPENDV_ENV": "test"})
    adapter = AntigravityHarnessAdapter(cfg, sdk_module=fake_sdk)

    spec = build_vault_first_mate_task_spec(
        prompt="Synthesize Private Equity Data Vault 2.0 contract.",
        run_id="run-agy-001",
        trace_id=SAMPLE_TRACE_ID,
        output_schema=_SynthesisContractResult,
    )

    events: list[HarnessStreamEvent] = [ev async for ev in adapter.stream_task(spec)]
    kinds = [ev.kind for ev in events]

    assert kinds[0] == HarnessEventKind.SESSION_START
    assert HarnessEventKind.THOUGHT_DELTA in kinds
    assert HarnessEventKind.TOOL_CALL_START in kinds
    assert HarnessEventKind.TOOL_CALL_RESULT in kinds
    assert HarnessEventKind.TOOL_CALL_ERROR in kinds
    assert HarnessEventKind.OBSERVABILITY_EVENT in kinds
    assert HarnessEventKind.TEXT_DELTA in kinds
    assert kinds[-1] == HarnessEventKind.TURN_COMPLETE
    assert "[Tool Execution Error (RuntimeError):" in str(captured.get("last_error_hook_return"))

    # Inspect LocalAgentConfig constructed by adapter
    agent_cfg = captured["configs"][-1].kwargs
    assert agent_cfg["model"] == "gemini-4-argon-high"
    assert len(agent_cfg["subagents"]) == 3
    sub_names = [s.kwargs["name"] for s in agent_cfg["subagents"]]
    assert sub_names == ["scout-source", "scout-unstructured", "ship-vault"]
    # Verify ship-vault subagent has create_file & edit_file enabled since read_only=False
    ship_sub_caps = agent_cfg["subagents"][2].kwargs["capabilities"].kwargs
    assert "create_file" in ship_sub_caps["enabled_tools"]
    assert "edit_file" in ship_sub_caps["enabled_tools"]
    assert isinstance(agent_cfg["budget_config"], dict)

    # Verify run_task aggregation and structured_output validation
    run_res = await adapter.run_task(spec)
    assert run_res.stop_reason == HarnessStopReason.COMPLETED
    assert run_res.session_id == "conv-agy-session-001"
    assert run_res.model_used == "gemini-4-argon-high"
    assert "Completed Pass 1 and Pass 2 synthesis." in run_res.text_output
    assert isinstance(run_res.structured_output, _SynthesisContractResult)
    assert run_res.structured_output.contract_id == "dv_pe_model_contract_v5"
    assert run_res.structured_output.hub_count == 4
    assert run_res.usage.total_tokens == 1450


@pytest.mark.asyncio
async def test_antigravity_steward_hold_and_cancel_active_turn(tmp_path: Path) -> None:
    fake_sdk, _ = _build_fake_antigravity_sdk(trigger_hold=True)
    cfg = _make_config(tmp_path)
    adapter = AntigravityHarnessAdapter(cfg, sdk_module=fake_sdk)

    spec = build_vault_first_mate_task_spec(
        prompt="Evaluate Tier-U3 entity resolution hold.",
        run_id="run-agy-hold",
        trace_id=SAMPLE_TRACE_ID,
    )
    streamed = [ev async for ev in adapter.stream_task(spec)]
    assert any(ev.kind == HarnessEventKind.STEWARD_HOLD for ev in streamed)

    hold_result = await adapter.run_task(spec)
    assert hold_result.stop_reason == HarnessStopReason.STEWARD_HOLD
    assert any(ev.get("status") == "ASK-USER" for ev in hold_result.emitted_events)

    # Verify cancel_active_turn while streaming is active
    hold_open = asyncio.Event()
    cancel_sdk, cancel_captured = _build_fake_antigravity_sdk(hold_stream_open_event=hold_open)
    cancel_adapter = AntigravityHarnessAdapter(cfg, sdk_module=cancel_sdk)
    cancel_spec = build_scout_source_task_spec(
        prompt="Long running profile",
        task_id="task-cancel-agy-01",
    )

    async def _consume() -> list[HarnessStreamEvent]:
        collected: list[HarnessStreamEvent] = []
        async for ev in cancel_adapter.stream_task(cancel_spec):
            collected.append(ev)
            if ev.kind == HarnessEventKind.SESSION_START:
                asyncio.get_running_loop().call_later(
                    0.02,
                    lambda: asyncio.create_task(
                        cancel_adapter.cancel_active_turn("task-cancel-agy-01")
                    ),
                )
        return collected

    collected_events = await _consume()
    assert cancel_captured["cancelled"] is True
    assert collected_events[-1].kind == HarnessEventKind.TURN_COMPLETE


def test_registry_persona_builders_and_steward_hold_manager(tmp_path: Path) -> None:
    from pydantic import ValidationError

    assert list_available_harnesses() == [
        "agy",
        "agy-cli",
        "antigravity",
        "claude",
        "claude-cli",
        "claude-code",
    ]

    with pytest.raises(ValidationError):
        _make_config(tmp_path, provider="deterministic")
    with pytest.raises(ValidationError):
        _make_config(tmp_path, provider="deterministic-stub")

    fake_agy, _ = _build_fake_antigravity_sdk()
    agy_cfg = _make_config(tmp_path, provider="antigravity")
    h_agy = create_agent_harness(agy_cfg, sdk_module=fake_agy)
    assert isinstance(h_agy, AntigravityHarnessAdapter)

    unregistered_det_cfg = agy_cfg.model_copy(update={"provider": "deterministic"})
    with pytest.raises(ValueError, match="Unsupported harness provider 'deterministic'"):
        create_agent_harness(unregistered_det_cfg)

    # Register custom provider factory
    try:
        register_harness("custom-test", lambda c: AntigravityHarnessAdapter(c, sdk_module=fake_agy))
        assert "custom-test" in list_available_harnesses()
    finally:
        _HARNESS_REGISTRY.pop("custom-test", None)
    assert list_available_harnesses() == [
        "agy",
        "agy-cli",
        "antigravity",
        "claude",
        "claude-cli",
        "claude-code",
    ]

    # Verify all 4 persona builders & generic dispatchers
    vfm_spec = build_persona_task_spec(AgentPersona.VAULT_FIRST_MATE, "Orchestrate")
    assert vfm_spec.model_tier == ModelTier.HIGH_REASONING
    assert len(vfm_spec.subagents) == len(build_vault_first_mate_subagents()) == 3

    ss_spec = build_scout_source_task_spec("Profile")
    assert ss_spec.model_tier == ModelTier.FAST_TIER
    assert ss_spec.subagents == ()
    assert build_scout_source_subagent_spec().read_only is True

    su_spec = build_scout_unstructured_task_spec("Extract spans")
    assert su_spec.model_tier == ModelTier.FAST_TIER
    assert build_scout_unstructured_subagent_spec().read_only is True

    sv_spec = build_ship_vault_task_spec("Compile contract")
    assert sv_spec.model_tier == ModelTier.HIGH_REASONING
    assert build_ship_vault_subagent_spec().read_only is False
    assert build_persona_subagent_spec(AgentPersona.SHIP_VAULT).persona == AgentPersona.SHIP_VAULT

    # Verify StewardHoldManager lifecycle: record -> list -> resolve -> prepare_resumed_task_spec
    hold_mgr = StewardHoldManager(state_dir=agy_cfg.state_dir)
    rec = hold_mgr.record_hold(
        decision_id="dec-tier-u3-001",
        hold_id="hold-tier-u3-001",
        event_id="ev-tier-u3-001",
        run_id="run-pe-01",
        task_id=su_spec.task_id,
        session_id="sess-agy-u3-01",
        stage_id=PipelineStageId.STAGE_02_SCOUT_UNSTRUCTURED,
        workbench_view_id=WorkbenchViewId.WB_03,
        viz_id=VizId.VIZ_GATE_06,
        persona=AgentPersona.SCOUT_UNSTRUCTURED,
        headline="Ambiguous entity 'Apex IV' margin 0.08 < 0.15.",
        options=[
            {"candidate_id": "APEX_BO_IV", "label": "Apex Buyout Fund IV"},
            {"candidate_id": "APEX_GR_IV", "label": "Apex Growth Fund IV"},
        ],
    )
    assert rec.status == "PENDING"
    assert (agy_cfg.state_dir / "holds" / "dec-tier-u3-001.json").is_file()
    assert len(hold_mgr.list_pending_holds(run_id="run-pe-01")) == 1

    reloaded_mgr = StewardHoldManager(state_dir=agy_cfg.state_dir)
    assert reloaded_mgr.get_hold("dec-tier-u3-001").decision_id == "dec-tier-u3-001"
    assert reloaded_mgr.get_hold("hold-tier-u3-001").decision_id == "dec-tier-u3-001"
    assert reloaded_mgr.get_hold("ev-tier-u3-001").decision_id == "dec-tier-u3-001"
    assert len(reloaded_mgr.list_pending_holds(run_id="run-pe-01")) == 1

    with pytest.raises(ValueError, match="still PENDING"):
        reloaded_mgr.prepare_resumed_task_spec(su_spec, "dec-tier-u3-001")

    resolved = reloaded_mgr.resolve_hold(
        "dec-tier-u3-001",
        action="APPROVE",
        selected_option_id="APEX_BO_IV",
        resolution_notes="Confirmed with Fund Counsel.",
    )
    assert resolved.status == "APPROVED"
    assert resolved.selected_option_id == "APEX_BO_IV"
    assert len(reloaded_mgr.list_pending_holds(run_id="run-pe-01")) == 0

    resumed_spec = StewardHoldManager(state_dir=agy_cfg.state_dir).prepare_resumed_task_spec(
        su_spec,
        "dec-tier-u3-001",
    )
    assert resumed_spec.resume_session_id == "sess-agy-u3-01"
    assert "APEX_BO_IV" in resumed_spec.prompt
    assert "Confirmed with Fund Counsel." in resumed_spec.prompt


@pytest.mark.asyncio
async def test_wp04_antigravity_fail_closed_and_gemini_rest_api_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    import urllib.request

    from opendv_agent_harness.adapters.antigravity_adapter import GoogleAntigravityAdapter
    from opendv_agent_harness.adapters.deterministic_adapter import DeterministicHarnessAdapter

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    cfg_no_key = _make_config(tmp_path, env={})
    adapter_no_key = GoogleAntigravityAdapter(cfg_no_key, sdk_module=None)
    spec = build_vault_first_mate_task_spec(
        prompt="Synthesize contract via Gemini REST",
        run_id="run-agy-rest-01",
        trace_id=SAMPLE_TRACE_ID,
        output_schema=_SynthesisContractResult,
    )

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        await adapter_no_key.run_task(spec)

    # Now configure GEMINI_API_KEY and mock urllib.request.urlopen
    captured_requests: list[dict[str, Any]] = []

    class _FakeHttpResponse:
        def __init__(self, call_idx: int) -> None:
            self.call_idx = call_idx

        def __enter__(self) -> _FakeHttpResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            if self.call_idx == 0:
                payload = {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "thought": True,
                                        "text": "Reasoning over Graph A and Graph B...",
                                    },
                                    {
                                        "functionCall": {
                                            "id": "fc-gemini-01",
                                            "name": "emit_observability_event",
                                            "args": {
                                                "status": "PASS",
                                                "headline": "Gemini REST tool call",
                                                "business_impact": "Verifies REST tool loop.",
                                                "rule_ids_evaluated": ["HR-01"],
                                            },
                                        }
                                    },
                                ]
                            }
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 160,
                        "candidatesTokenCount": 40,
                        "thoughtsTokenCount": 20,
                        "totalTokenCount": 220,
                    },
                }
            else:
                payload = {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": json.dumps(
                                            {
                                                "contract_id": "dv_gemini_rest_v5",
                                                "hub_count": 3,
                                                "verdict": "PASS",
                                            }
                                        )
                                    }
                                ]
                            }
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 160,
                        "candidatesTokenCount": 45,
                        "thoughtsTokenCount": 25,
                        "totalTokenCount": 230,
                    },
                }
            return json.dumps(payload).encode("utf-8")

    def _fake_urlopen(req: urllib.request.Request, timeout: float = 60.0) -> _FakeHttpResponse:
        body = json.loads((req.data or b"{}").decode("utf-8"))
        call_idx = len(captured_requests)
        captured_requests.append(
            {
                "url": req.full_url,
                "headers": dict(req.header_items()),
                "body": body,
                "timeout": timeout,
            }
        )
        return _FakeHttpResponse(call_idx)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    cfg_with_key = _make_config(
        tmp_path,
        env={
            "GEMINI_API_KEY": "test-gemini-api-key-123",
            "GOOGLE_GEMINI_BASE_URL": "https://custom-gemini.example.com/v1beta/models",
        },
    )
    rest_adapter = GoogleAntigravityAdapter(cfg_with_key, sdk_module=None)
    rest_res = await rest_adapter.run_task(spec)

    assert rest_res.stop_reason == HarnessStopReason.COMPLETED
    assert isinstance(rest_res.structured_output, _SynthesisContractResult)
    assert rest_res.structured_output.contract_id == "dv_gemini_rest_v5"
    assert rest_res.structured_output.hub_count == 3
    assert rest_res.usage.total_tokens == 450
    assert rest_res.usage.model_calls == 2
    assert len(rest_res.emitted_events) == 1
    assert len(captured_requests) == 2
    assert captured_requests[0]["url"].startswith(
        "https://custom-gemini.example.com/v1beta/models/"
    )
    assert ":generateContent" in captured_requests[0]["url"]
    assert "tools" in captured_requests[0]["body"]
    gen_cfg = captured_requests[0]["body"]["generationConfig"]
    assert gen_cfg["responseMimeType"] == "application/json"
    assert "properties" in gen_cfg["responseSchema"]

    # Also verify DeterministicHarnessAdapter structured_output_factory as a test-only mock adapter
    det_cfg = _make_config(tmp_path, provider="agy")
    det_adapter = DeterministicHarnessAdapter(
        det_cfg,
        structured_output_factory=lambda s: _SynthesisContractResult(
            contract_id=f"det_{s.run_id}",
            hub_count=5,
            verdict="PASS",
        ),
    )
    det_res = await det_adapter.run_task(spec)
    assert isinstance(det_res.structured_output, _SynthesisContractResult)
    assert det_res.structured_output.contract_id == "det_run-agy-rest-01"
    assert det_res.structured_output.hub_count == 5


@pytest.mark.asyncio
async def test_wp11_agy_cli_harness_adapter_subprocess_and_fallback(
    tmp_path: Path,
) -> None:
    import json
    import subprocess

    from opendv_agent_harness.adapters.agy_cli_adapter import (
        AgyCliHarnessAdapter,
        _strip_internal_stage_context,
    )

    # 1. Verify internal stage context marker stripping
    prompt_with_ctx = 'Synthesize Pass 1 Triage\n__OPENDV_STAGE_CONTEXT_JSON__:{"secret": 123}'
    assert _strip_internal_stage_context(prompt_with_ctx) == "Synthesize Pass 1 Triage"

    # 2. Verify subprocess invocation via custom agy_cli_runner
    fake_agy_exe = tmp_path / "fake_agy.exe"
    fake_agy_exe.write_text("echo fake", encoding="utf-8")

    captured_cmds: list[list[str]] = []

    def _fake_cli_runner(
        cmd: list[str],
        *,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del env, timeout
        captured_cmds.append(list(cmd))
        out_payload = {
            "conversation_id": "conv-agy-cli-777",
            "status": "SUCCESS",
            "response": json.dumps(
                {
                    "contract_id": "dv_agy_cli_contract_v5",
                    "hub_count": 6,
                    "verdict": "PASS",
                }
            ),
            "duration_seconds": 4.2,
            "num_turns": 1,
            "structured_output": {
                "contract_id": "dv_agy_cli_contract_v5",
                "hub_count": 6,
                "verdict": "PASS",
            },
            "usage": {
                "input_tokens": 1200,
                "output_tokens": 180,
                "thinking_tokens": 90,
                "cache_read_tokens": 0,
                "total_tokens": 1470,
            },
        }
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=json.dumps(out_payload),
            stderr="",
        )

    cfg = _make_config(
        tmp_path,
        provider="agy",
        env={"OPENDV_AGY_CLI_BIN": str(fake_agy_exe)},
        extra_provider_options={"agy_cli_runner": _fake_cli_runner},
    )
    agy_cli_adapter = create_agent_harness(cfg)
    assert isinstance(agy_cli_adapter, AgyCliHarnessAdapter)
    assert agy_cli_adapter.provider_name == "agy"
    assert agy_cli_adapter.resolve_model(ModelTier.HIGH_REASONING) == "gemini-4-argon-high"
    assert agy_cli_adapter.resolve_model(ModelTier.FAST_TIER) == "gemini-3.8-flash-high"

    spec = build_vault_first_mate_task_spec(
        prompt=prompt_with_ctx,
        run_id="run-agy-cli-01",
        trace_id=SAMPLE_TRACE_ID,
        output_schema=_SynthesisContractResult,
    )
    res = await agy_cli_adapter.run_task(spec)
    assert res.stop_reason == HarnessStopReason.COMPLETED
    assert res.provider == "agy"
    assert res.session_id == "conv-agy-cli-777"
    assert res.model_used == "gemini-4-argon-high"
    assert isinstance(res.structured_output, _SynthesisContractResult)
    assert res.structured_output.contract_id == "dv_agy_cli_contract_v5"
    assert res.structured_output.hub_count == 6
    assert res.usage.total_tokens == 1470
    assert len(captured_cmds) == 1
    assert "--model" in captured_cmds[0]
    assert "gemini-4-argon-high" in captured_cmds[0]
    assert captured_cmds[0][captured_cmds[0].index("--output-format") + 1] == "stream-json"
    assert "--json-schema" in captured_cmds[0]
    assert "__OPENDV_STAGE_CONTEXT_JSON__" not in captured_cmds[0][2]

    # 3. Verify real-time NDJSON stream-json parsing (TEXT_DELTA & THOUGHT_DELTA)
    stream_cmds: list[list[str]] = []

    def _ndjson_stream_runner(
        cmd: list[str],
        *,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del env, timeout
        stream_cmds.append(list(cmd))
        ndjson_lines = [
            json.dumps({"type": "thought_delta", "thought": "Evaluating ontology..."}),
            json.dumps({"type": "text_delta", "text": '{"contract_id": "dv_stream_agy_v5", '}),
            json.dumps({"type": "delta", "delta": '"hub_count": 4, "verdict": "PASS"}'}),
            json.dumps(
                {
                    "type": "result",
                    "conversation_id": "conv-agy-ndjson-888",
                    "status": "SUCCESS",
                    "response": "",
                    "duration_seconds": 1.5,
                    "num_turns": 1,
                    "usage": {
                        "input_tokens": 500,
                        "output_tokens": 90,
                        "thinking_tokens": 30,
                        "total_tokens": 620,
                    },
                }
            ),
        ]
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="\n".join(ndjson_lines),
            stderr="",
        )

    stream_cfg = _make_config(
        tmp_path,
        provider="agy",
        env={"OPENDV_AGY_CLI_BIN": str(fake_agy_exe)},
        extra_provider_options={"agy_cli_runner": _ndjson_stream_runner},
    )
    stream_adapter = create_agent_harness(stream_cfg)
    streamed_events = [ev async for ev in stream_adapter.stream_task(spec)]

    thought_deltas = [
        ev.delta_text for ev in streamed_events if ev.kind == HarnessEventKind.THOUGHT_DELTA
    ]
    text_deltas = [
        ev.delta_text for ev in streamed_events if ev.kind == HarnessEventKind.TEXT_DELTA
    ]
    assert thought_deltas == ["Evaluating ontology..."]
    assert text_deltas == [
        '{"contract_id": "dv_stream_agy_v5", ',
        '"hub_count": 4, "verdict": "PASS"}',
    ]

    stream_res = await stream_adapter.run_task(spec)
    assert stream_res.stop_reason == HarnessStopReason.COMPLETED
    assert stream_res.session_id == "conv-agy-ndjson-888"
    assert isinstance(stream_res.structured_output, _SynthesisContractResult)
    assert stream_res.structured_output.contract_id == "dv_stream_agy_v5"
    assert stream_res.structured_output.hub_count == 4
    assert stream_res.usage.total_tokens == 620
    assert stream_cmds[0][stream_cmds[0].index("--output-format") + 1] == "stream-json"

    # 4. Single-line NDJSON text_delta extraction and non-zero exit with partial NDJSON stream
    from opendv_agent_harness.adapters.agy_cli_adapter import (
        ProviderConfigurationError,
        _extract_json_from_stdout,
    )

    assert _extract_json_from_stdout('{"type": "text_delta", "text": "{\\"ok\\": true}"}') == {
        "status": "SUCCESS",
        "response": '{"ok": true}',
    }

    def _partial_fail_runner(
        cmd: list[str],
        *,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del env, timeout
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout=json.dumps({"type": "text_delta", "text": '{"contract_id": "partial"'}),
            stderr="CLI session interrupted",
        )

    fail_cfg = _make_config(
        tmp_path,
        provider="agy",
        env={"OPENDV_AGY_CLI_BIN": str(fake_agy_exe)},
        extra_provider_options={"agy_cli_runner": _partial_fail_runner},
    )
    with pytest.raises(ProviderConfigurationError, match="CLI session interrupted"):
        await create_agent_harness(fail_cfg).run_task(spec)


@pytest.mark.asyncio
async def test_wp20_antigravity_and_agy_cli_agent_models_propagation_to_agents_and_subagents(
    tmp_path: Path,
) -> None:
    import json
    import subprocess

    fake_sdk, captured = _build_fake_antigravity_sdk()
    cfg = _make_config(
        tmp_path,
        model_overrides={
            "reasoning": "tier-reasoning-model",
            "fast": "tier-fast-model",
        },
        agent_models={
            "scout-unstructured": "custom-unstructured-v1",
            "ship-vault": "custom-ship-v1",
        },
    )
    adapter = AntigravityHarnessAdapter(cfg, sdk_module=fake_sdk)

    vfm_spec = build_vault_first_mate_task_spec(
        prompt="Orchestrate with fine-grained agent_models",
        run_id="run-wp20-agy-01",
        trace_id=SAMPLE_TRACE_ID,
        output_schema=_SynthesisContractResult,
    )
    vfm_res = await adapter.run_task(vfm_spec)
    assert vfm_res.model_used == "tier-reasoning-model"
    assert vfm_res.subagent_models == {
        "scout-source": "tier-fast-model",
        "scout-unstructured": "custom-unstructured-v1",
        "ship-vault": "custom-ship-v1",
    }

    local_cfg = captured["configs"][-1].kwargs
    assert local_cfg["model"] == "tier-reasoning-model"
    sub_models_by_name = {s.kwargs["name"]: s.kwargs["model"] for s in local_cfg["subagents"]}
    assert sub_models_by_name == {
        "scout-source": "tier-fast-model",
        "scout-unstructured": "custom-unstructured-v1",
        "ship-vault": "custom-ship-v1",
    }

    # Direct task execution for scout-unstructured and ship-vault uses agent_models override
    su_spec = build_scout_unstructured_task_spec(
        prompt="Extract spans",
        run_id="run-wp20-agy-02",
        trace_id=SAMPLE_TRACE_ID,
    )
    su_res = await adapter.run_task(su_spec)
    assert su_res.model_used == "custom-unstructured-v1"
    assert captured["configs"][-1].kwargs["model"] == "custom-unstructured-v1"

    sv_spec = build_ship_vault_task_spec(
        prompt="Compile contract",
        run_id="run-wp20-agy-03",
        trace_id=SAMPLE_TRACE_ID,
    )
    sv_res = await adapter.run_task(sv_spec)
    assert sv_res.model_used == "custom-ship-v1"
    assert captured["configs"][-1].kwargs["model"] == "custom-ship-v1"

    # Also verify AgyCliHarnessAdapter physically passes resolved model to `agy -p --model`
    fake_agy_exe = tmp_path / "fake_agy_wp20.exe"
    fake_agy_exe.write_text("echo fake", encoding="utf-8")
    cli_cmds: list[list[str]] = []

    def _runner(
        cmd: list[str],
        *,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del env, timeout
        cli_cmds.append(list(cmd))
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=json.dumps(
                {
                    "conversation_id": "conv-wp20",
                    "status": "SUCCESS",
                    "response": '{"contract_id":"c1","hub_count":1,"verdict":"PASS"}',
                }
            ),
            stderr="",
        )

    cli_cfg = cfg.model_copy(
        update={
            "provider": "agy",
            "env": {"OPENDV_AGY_CLI_BIN": str(fake_agy_exe)},
            "extra_provider_options": {"agy_cli_runner": _runner},
        }
    )
    cli_adapter = create_agent_harness(cli_cfg)
    cli_vfm_res = await cli_adapter.run_task(vfm_spec)
    assert cli_vfm_res.model_used == "tier-reasoning-model"
    assert cli_vfm_res.subagent_models == {
        "scout-source": "tier-fast-model",
        "scout-unstructured": "custom-unstructured-v1",
        "ship-vault": "custom-ship-v1",
    }
    model_idx = cli_cmds[-1].index("--model")
    assert cli_cmds[-1][model_idx + 1] == "tier-reasoning-model"
    assert "scout-unstructured: model=custom-unstructured-v1" in cli_cmds[-1][2]
    assert "ship-vault: model=custom-ship-v1" in cli_cmds[-1][2]

    cli_su_res = await cli_adapter.run_task(su_spec)
    assert cli_su_res.model_used == "custom-unstructured-v1"
    su_model_idx = cli_cmds[-1].index("--model")
    assert cli_cmds[-1][su_model_idx + 1] == "custom-unstructured-v1"
