"""Unit tests for `ClaudeAgentHarnessAdapter`, MCP prefix stripping, and bash guard hooks.

Conforms to docs/09-agnostic-agent-harness-and-sdk-spec.md (§4.2, §5).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from opendv_agent_harness import (
    DEFAULT_CLAUDE_MODEL_MAP,
    AgentPersona,
    ClaudeAgentHarnessAdapter,
    HarnessConfig,
    HarnessEventKind,
    HarnessStopReason,
    HarnessStreamEvent,
    ModelTier,
    SandboxPolicy,
    StewardHoldManager,
    VaultAgentHarness,
    build_scout_unstructured_task_spec,
    build_vault_first_mate_task_spec,
    create_agent_harness,
)

SAMPLE_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"


class _ClaudeStructuredResult(BaseModel):
    contract_id: str
    verified_gates: int
    status: str


def _make_claude_config(tmp_path: Path, **overrides: Any) -> HarnessConfig:
    ws = tmp_path / "workspace"
    st = tmp_path / "state"
    app = tmp_path / "app_data"
    save = tmp_path / "save"
    for p in (ws, st, app, save):
        p.mkdir(parents=True, exist_ok=True)
    defaults: dict[str, Any] = {
        "provider": "claude",
        "workspace_dir": ws.resolve(),
        "state_dir": st.resolve(),
        "app_data_dir": app.resolve(),
        "save_dir": save.resolve(),
    }
    defaults.update(overrides)
    return HarnessConfig(**defaults)


def _build_fake_claude_sdk(
    *,
    trigger_hold: bool = False,
    trigger_error_block: bool = False,
    hold_stream_open_event: asyncio.Event | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Construct an in-memory mock `claude_agent_sdk` module."""
    captured: dict[str, Any] = {
        "options": [],
        "queries": [],
        "interrupted": False,
    }

    class ThinkingBlock:
        def __init__(self, thinking: str) -> None:
            self.thinking = thinking

    class TextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class ToolUseBlock:
        def __init__(self, call_id: str, name: str, tool_input: dict[str, Any]) -> None:
            self.id = call_id
            self.name = name
            self.input = tool_input

    class ToolResultBlock:
        def __init__(
            self,
            tool_use_id: str,
            content: Any,
            *,
            is_error: bool = False,
        ) -> None:
            self.tool_use_id = tool_use_id
            self.content = content
            self.is_error = is_error

    class AssistantMessage:
        def __init__(self, content: list[Any]) -> None:
            self.content = content

    class UserMessage:
        def __init__(self, content: list[Any]) -> None:
            self.content = content

    class ResultMessage:
        session_id = "sess-claude-9001"
        structured_output: ClassVar[dict[str, Any]] = {
            "contract_id": "dv_pe_model_contract_v5",
            "verified_gates": 6,
            "status": "PASS",
        }
        usage: ClassVar[dict[str, Any]] = {
            "input_tokens": 1100,
            "cached_input_tokens": 200,
            "output_tokens": 400,
            "reasoning_tokens": 250,
            "total_tokens": 1750,
            "model_calls": 1,
            "cost_usd": 0.038,
        }

    class AgentDefinition:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            for k, v in kwargs.items():
                setattr(self, k, v)

    class HookMatcher:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            for k, v in kwargs.items():
                setattr(self, k, v)

    class ClaudeAgentOptions:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            for k, v in kwargs.items():
                setattr(self, k, v)
            captured["options"].append(kwargs)

    class ClaudeSDKClient:
        def __init__(self, options: ClaudeAgentOptions) -> None:
            self.options = options

        async def __aenter__(self) -> ClaudeSDKClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def interrupt(self) -> None:
            captured["interrupted"] = True
            if hold_stream_open_event is not None:
                hold_stream_open_event.set()

        async def query(self, prompt: str) -> None:
            captured["queries"].append(prompt)
            handlers = self.options.kwargs["mcp_servers"]["opendv"]["handlers"]
            if "mcp__opendv__profile_source_feed" in handlers:
                await handlers["mcp__opendv__profile_source_feed"](
                    {
                        "source_system": "INVESTRAN_PE_ERP",
                        "table_or_path": "raw_lp_commitments",
                    }
                )
            if "mcp__opendv__emit_observability_event" in handlers:
                await handlers["mcp__opendv__emit_observability_event"](
                    {
                        "headline": "Claude adapter verified 6-Gate contract invariants.",
                        "status": "PASS",
                    }
                )
            if trigger_hold and "mcp__opendv__request_steward_decision" in handlers:
                await handlers["mcp__opendv__request_steward_decision"](
                    {
                        "escalation_reason": "TIER_U3_AMBIGUOUS_ENTITY",
                        "headline": (
                            "Tier-U3 entity resolution margin 0.09 requires steward review."
                        ),
                        "candidate_options": [{"hub_key": "APEX_IV", "score": 0.59}],
                        "rule_id": "ALG-A9B",
                    }
                )

        async def receive_response(self) -> Any:
            yield AssistantMessage(
                [
                    ThinkingBlock("Analyzing Graph A ontology and Graph B statistical profile..."),
                    ToolUseBlock(
                        "tu-claude-01",
                        "mcp__opendv__profile_source_feed",
                        {
                            "source_system": "INVESTRAN_PE_ERP",
                            "table_or_path": "raw_lp_commitments",
                        },
                    ),
                    TextBlock("Synthesized verified "),
                ]
            )
            yield UserMessage(
                [
                    ToolResultBlock(
                        "tu-claude-01",
                        {"row_count": 2, "drift_status": "PASS"},
                        is_error=False,
                    ),
                    *(
                        [
                            ToolResultBlock(
                                "tu-claude-err",
                                "Tool failed validation",
                                is_error=True,
                            )
                        ]
                        if trigger_error_block
                        else []
                    ),
                ]
            )
            if hold_stream_open_event is not None:
                await hold_stream_open_event.wait()
            yield AssistantMessage([TextBlock("Data Vault 2.0 contract.")])
            yield ResultMessage()

    class _FakeClaudeSdkModule:
        pass

    sdk = _FakeClaudeSdkModule()
    sdk.AgentDefinition = AgentDefinition  # type: ignore[attr-defined]
    sdk.HookMatcher = HookMatcher  # type: ignore[attr-defined]
    sdk.ClaudeAgentOptions = ClaudeAgentOptions  # type: ignore[attr-defined]
    sdk.ClaudeSDKClient = ClaudeSDKClient  # type: ignore[attr-defined]
    return sdk, captured


def test_claude_model_tier_resolution_and_strip_mcp_prefix(tmp_path: Path) -> None:
    cfg = _make_claude_config(tmp_path)
    adapter = ClaudeAgentHarnessAdapter(cfg)
    assert isinstance(adapter, VaultAgentHarness)
    assert adapter.provider_name == "claude"

    assert (
        adapter.resolve_model(ModelTier.HIGH_REASONING, AgentPersona.VAULT_FIRST_MATE)
        == DEFAULT_CLAUDE_MODEL_MAP[ModelTier.HIGH_REASONING]
    )
    assert adapter.resolve_model(ModelTier.HIGH_REASONING) == "claude-opus-5-5-high"
    assert (
        adapter.resolve_model(ModelTier.FAST_TIER, AgentPersona.SCOUT_SOURCE)
        == "claude-sonnet-5-5-medium"
    )

    override_cfg = _make_claude_config(
        tmp_path,
        model_overrides={
            ModelTier.HIGH_REASONING: "claude-opus-custom",
            ModelTier.FAST_TIER: "claude-sonnet-custom",
        },
    )
    override_adapter = ClaudeAgentHarnessAdapter(override_cfg)
    assert override_adapter.resolve_model(ModelTier.HIGH_REASONING) == "claude-opus-custom"
    assert override_adapter.resolve_model(ModelTier.FAST_TIER) == "claude-sonnet-custom"
    assert (
        override_adapter.resolve_model(ModelTier.HIGH_REASONING, AgentPersona.VAULT_FIRST_MATE)
        == "claude-opus-custom"
    )
    assert (
        override_adapter.resolve_model(ModelTier.HIGH_REASONING, AgentPersona.SHIP_VAULT)
        == "claude-opus-custom"
    )
    assert (
        override_adapter.resolve_model(ModelTier.FAST_TIER, AgentPersona.SCOUT_SOURCE)
        == "claude-sonnet-custom"
    )
    assert (
        override_adapter.resolve_model(ModelTier.FAST_TIER, AgentPersona.SCOUT_UNSTRUCTURED)
        == "claude-sonnet-custom"
    )

    # Verify per-agent agent_models take precedence over model_overrides
    agent_override_cfg = _make_claude_config(
        tmp_path,
        model_overrides={
            "reasoning": "claude-opus-custom",
            "fast": "claude-sonnet-custom",
        },
        agent_models={
            "scout-unstructured": "custom-unstructured-v1",
            "ship-vault": "custom-ship-v1",
        },
    )
    agent_override_adapter = ClaudeAgentHarnessAdapter(agent_override_cfg)
    assert (
        agent_override_adapter.resolve_model(
            ModelTier.HIGH_REASONING, AgentPersona.VAULT_FIRST_MATE
        )
        == "claude-opus-custom"
    )
    assert (
        agent_override_adapter.resolve_model(ModelTier.FAST_TIER, AgentPersona.SCOUT_SOURCE)
        == "claude-sonnet-custom"
    )
    assert (
        agent_override_adapter.resolve_model(ModelTier.FAST_TIER, AgentPersona.SCOUT_UNSTRUCTURED)
        == "custom-unstructured-v1"
    )
    assert (
        agent_override_adapter.resolve_model(ModelTier.HIGH_REASONING, AgentPersona.SHIP_VAULT)
        == "custom-ship-v1"
    )

    # Verify strip_mcp_prefix
    assert (
        ClaudeAgentHarnessAdapter.strip_mcp_prefix("mcp__opendv__profile_source_feed")
        == "profile_source_feed"
    )
    assert ClaudeAgentHarnessAdapter.strip_mcp_prefix("Read") == "Read"


@pytest.mark.asyncio
async def test_claude_options_subagents_bash_hook_and_streaming(tmp_path: Path) -> None:
    fake_sdk, captured = _build_fake_claude_sdk(
        trigger_hold=False,
        trigger_error_block=True,
    )
    cfg = _make_claude_config(
        tmp_path,
        sandbox=SandboxPolicy(read_only_workspace=False, allow_shell_execution=True),
    )
    adapter = create_agent_harness(cfg, sdk_module=fake_sdk)
    assert isinstance(adapter, ClaudeAgentHarnessAdapter)

    spec = build_vault_first_mate_task_spec(
        prompt="Orchestrate PE Data Vault synthesis with Claude SDK.",
        run_id="run-claude-001",
        trace_id=SAMPLE_TRACE_ID,
        output_schema=_ClaudeStructuredResult,
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

    # Verify ToolUseBlock had `mcp__opendv__` stripped on TOOL_CALL_START
    tool_starts = [ev for ev in events if ev.kind == HarnessEventKind.TOOL_CALL_START]
    assert len(tool_starts) == 1
    assert tool_starts[0].tool_name == "profile_source_feed"
    assert tool_starts[0].tool_call_id == "tu-claude-01"

    # Inspect ClaudeAgentOptions
    opts = captured["options"][-1]
    assert opts["model"] == "claude-opus-5-5-high"
    assert opts["permission_mode"] == "dontAsk"
    assert {"Read", "Glob", "Grep", "Write", "Edit", "Bash", "Agent"} <= set(opts["allowed_tools"])
    assert "mcp__opendv__profile_source_feed" in opts["allowed_tools"]
    assert opts["output_format"]["type"] == "json_schema"
    assert "contract_id" in opts["output_format"]["schema"]["properties"]

    # Verify AgentDefinition subagents (`scout-source`, `scout-unstructured`, `ship-vault`)
    agents_map = opts["agents"]
    assert set(agents_map.keys()) == {"scout-source", "scout-unstructured", "ship-vault"}
    assert agents_map["scout-source"].kwargs["model"] == "claude-sonnet-5-5-medium"
    assert "Write" not in agents_map["scout-source"].kwargs["tools"]
    assert agents_map["ship-vault"].kwargs["model"] == "claude-opus-5-5-high"
    assert {"Write", "Edit"} <= set(agents_map["ship-vault"].kwargs["tools"])
    assert "mcp__opendv__apply_ast_codemod" in agents_map["ship-vault"].kwargs["tools"]

    # Verify PreToolUse Bash guard hook blocks dangerous shell substrings
    pre_hooks = opts["hooks"]["PreToolUse"]
    assert len(pre_hooks) == 1
    assert pre_hooks[0].kwargs["matcher"] == "Bash"
    bash_guard = pre_hooks[0].kwargs["hooks"][0]

    for blocked_cmd in (
        "rm -rf /workspace",
        "git push --force",
        "git worktree prune",
        "treehouse remove",
        "no-mistakes daemon restart",
    ):
        denied = await bash_guard({"tool_input": {"command": blocked_cmd}})
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"

    allowed = await bash_guard({"tool_input": {"command": "uv run pytest -q"}})
    assert allowed == {}

    # Verify read-only workspace configuration disables Write, Edit, and Bash
    ro_cfg = _make_claude_config(
        tmp_path,
        sandbox=SandboxPolicy(read_only_workspace=True, allow_shell_execution=False),
    )
    ro_adapter = ClaudeAgentHarnessAdapter(ro_cfg, sdk_module=fake_sdk)
    run_res = await ro_adapter.run_task(spec)
    ro_opts = captured["options"][-1]
    assert {"Write", "Edit", "Bash"} <= set(ro_opts["disallowed_tools"])
    assert ro_opts["hooks"] == {}

    assert run_res.stop_reason == HarnessStopReason.COMPLETED
    assert run_res.session_id == "sess-claude-9001"
    assert run_res.text_output == "Synthesized verified Data Vault 2.0 contract."
    assert isinstance(run_res.structured_output, _ClaudeStructuredResult)
    assert run_res.structured_output.verified_gates == 6
    assert run_res.usage.total_tokens == 1750
    assert run_res.usage.cached_input_tokens == 200


@pytest.mark.asyncio
async def test_claude_steward_hold_resumption_and_cancel_active_turn(tmp_path: Path) -> None:
    fake_sdk, _captured = _build_fake_claude_sdk(trigger_hold=True)
    cfg = _make_claude_config(tmp_path)
    adapter = ClaudeAgentHarnessAdapter(cfg, sdk_module=fake_sdk)

    spec = build_scout_unstructured_task_spec(
        prompt="Extract PDF spans from calpers_side_letter.pdf",
        run_id="run-claude-hold-01",
        trace_id=SAMPLE_TRACE_ID,
    )
    streamed = [ev async for ev in adapter.stream_task(spec)]
    hold_events = [ev for ev in streamed if ev.kind == HarnessEventKind.STEWARD_HOLD]
    assert len(hold_events) >= 1

    hold_result = await adapter.run_task(spec)
    assert hold_result.stop_reason == HarnessStopReason.STEWARD_HOLD

    # Record hold from run_task result and resume with StewardHoldManager
    mgr = StewardHoldManager(state_dir=cfg.state_dir)
    hold_rec = mgr.record_from_run_result(
        spec,
        hold_result,
        options=[{"candidate_id": "APEX_IV", "score": 0.59}],
    )
    assert hold_rec.status == "PENDING"
    mgr.resolve_decision(
        hold_rec.decision_id,
        action="APPROVE",
        selected_candidate_id="APEX_IV",
        resolution_notes="Approved by Data Steward.",
    )
    resumed_spec = mgr.build_resumed_task_spec(spec, hold_rec.decision_id)
    assert resumed_spec.resume_session_id == "sess-claude-9001"

    # Run resumed spec and verify `resume` option passed to ClaudeAgentOptions
    ok_sdk, ok_captured = _build_fake_claude_sdk(trigger_hold=False)
    ok_adapter = ClaudeAgentHarnessAdapter(cfg, sdk_module=ok_sdk)
    resumed_res = await ok_adapter.run_task(resumed_spec)
    assert resumed_res.stop_reason == HarnessStopReason.COMPLETED
    assert ok_captured["options"][-1]["resume"] == "sess-claude-9001"

    # Verify cancel_active_turn invokes client.interrupt()
    hold_open = asyncio.Event()
    cancel_sdk, cancel_captured = _build_fake_claude_sdk(hold_stream_open_event=hold_open)
    cancel_adapter = ClaudeAgentHarnessAdapter(cfg, sdk_module=cancel_sdk)
    cancel_spec = build_scout_unstructured_task_spec(
        prompt="Interruptible stream",
        task_id="task-cancel-claude-01",
    )

    async def _consume() -> list[HarnessStreamEvent]:
        items: list[HarnessStreamEvent] = []
        async for ev in cancel_adapter.stream_task(cancel_spec):
            items.append(ev)
            if ev.kind == HarnessEventKind.SESSION_START:
                asyncio.get_running_loop().call_later(
                    0.02,
                    lambda: asyncio.create_task(
                        cancel_adapter.cancel_active_turn("task-cancel-claude-01")
                    ),
                )
        return items

    collected = await _consume()
    assert cancel_captured["interrupted"] is True
    assert collected[-1].kind == HarnessEventKind.TURN_COMPLETE


@pytest.mark.asyncio
async def test_wp04_claude_fail_closed_and_anthropic_rest_api_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    import urllib.request

    from opendv_agent_harness.adapters.claude_adapter import ClaudeAgentSdkAdapter

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    cfg_no_key = _make_claude_config(tmp_path, env={})
    adapter_no_key = ClaudeAgentSdkAdapter(cfg_no_key, sdk_module=None)
    spec = build_vault_first_mate_task_spec(
        prompt="Synthesize contract via Anthropic Messages REST",
        run_id="run-claude-rest-01",
        trace_id=SAMPLE_TRACE_ID,
        output_schema=_ClaudeStructuredResult,
    )

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        await adapter_no_key.run_task(spec)

    captured_requests: list[dict[str, Any]] = []

    class _FakeAnthropicHttpResponse:
        def __init__(self, call_idx: int) -> None:
            self.call_idx = call_idx

        def __enter__(self) -> _FakeAnthropicHttpResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            if self.call_idx == 0:
                payload = {
                    "id": "msg_anthropic_rest_000",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "Evaluating DV 2.0 invariants..."},
                        {
                            "type": "tool_use",
                            "id": "toolu_rest_01",
                            "name": "emit_observability_event",
                            "input": {
                                "status": "PASS",
                                "headline": "Anthropic REST tool call",
                                "business_impact": "Verifies Anthropic REST tool loop.",
                                "rule_ids_evaluated": ["HR-01"],
                            },
                        },
                    ],
                    "usage": {
                        "input_tokens": 140,
                        "cache_read_input_tokens": 20,
                        "output_tokens": 45,
                    },
                }
            else:
                payload = {
                    "id": "msg_anthropic_rest_001",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "contract_id": "dv_claude_rest_v5",
                                    "verified_gates": 6,
                                    "status": "PASS",
                                }
                            ),
                        },
                    ],
                    "usage": {
                        "input_tokens": 140,
                        "cache_read_input_tokens": 20,
                        "output_tokens": 45,
                    },
                }
            return json.dumps(payload).encode("utf-8")

    def _fake_urlopen(
        req: urllib.request.Request,
        timeout: float = 60.0,
    ) -> _FakeAnthropicHttpResponse:
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
        return _FakeAnthropicHttpResponse(call_idx)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    cfg_with_key = _make_claude_config(
        tmp_path,
        env={
            "ANTHROPIC_API_KEY": "sk-ant-test-key-456",
            "ANTHROPIC_BASE_URL": "https://custom-anthropic.example.com",
        },
    )
    rest_adapter = ClaudeAgentSdkAdapter(cfg_with_key, sdk_module=None)
    rest_res = await rest_adapter.run_task(spec)

    assert rest_res.stop_reason == HarnessStopReason.COMPLETED
    assert rest_res.session_id == "msg_anthropic_rest_001"
    assert isinstance(rest_res.structured_output, _ClaudeStructuredResult)
    assert rest_res.structured_output.contract_id == "dv_claude_rest_v5"
    assert rest_res.structured_output.verified_gates == 6
    assert rest_res.usage.total_tokens == 410
    assert rest_res.usage.model_calls == 2
    assert len(rest_res.emitted_events) == 1
    assert len(captured_requests) == 2
    assert captured_requests[0]["url"] == "https://custom-anthropic.example.com/v1/messages"
    assert "system" in captured_requests[0]["body"]
    assert "tools" in captured_requests[0]["body"]
    assert captured_requests[0]["body"]["messages"][0]["role"] == "user"


@pytest.mark.asyncio
async def test_wp20_claude_agent_models_propagation_to_options_and_agent_definitions(
    tmp_path: Path,
) -> None:
    fake_sdk, captured = _build_fake_claude_sdk()
    cfg = _make_claude_config(
        tmp_path,
        model_overrides={
            "reasoning": "claude-tier-reasoning",
            "fast": "claude-tier-fast",
        },
        agent_models={
            "scout-unstructured": "custom-unstructured-v1",
            "ship-vault": "custom-ship-v1",
        },
    )
    adapter = ClaudeAgentHarnessAdapter(cfg, sdk_module=fake_sdk)

    vfm_spec = build_vault_first_mate_task_spec(
        prompt="Orchestrate with Claude agent_models",
        run_id="run-wp20-claude-01",
        trace_id=SAMPLE_TRACE_ID,
        output_schema=_ClaudeStructuredResult,
    )
    vfm_res = await adapter.run_task(vfm_spec)
    assert vfm_res.model_used == "claude-tier-reasoning"
    assert vfm_res.subagent_models == {
        "scout-source": "claude-tier-fast",
        "scout-unstructured": "custom-unstructured-v1",
        "ship-vault": "custom-ship-v1",
    }

    opts = captured["options"][-1]
    assert opts["model"] == "claude-tier-reasoning"
    agents_map = opts["agents"]
    assert agents_map["scout-source"].kwargs["model"] == "claude-tier-fast"
    assert agents_map["scout-unstructured"].kwargs["model"] == "custom-unstructured-v1"
    assert agents_map["ship-vault"].kwargs["model"] == "custom-ship-v1"

    su_spec = build_scout_unstructured_task_spec(
        prompt="Extract PDF spans with custom agent model",
        run_id="run-wp20-claude-02",
        trace_id=SAMPLE_TRACE_ID,
    )
    su_res = await adapter.run_task(su_spec)
    assert su_res.model_used == "custom-unstructured-v1"
    assert captured["options"][-1]["model"] == "custom-unstructured-v1"
