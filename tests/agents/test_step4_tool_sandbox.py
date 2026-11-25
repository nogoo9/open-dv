"""Phase V2-5 Milestone M1 tests: UAT-V25-AGT-01..05 (Packs, ModelTier, Subprocess/SDK, Step 4 Hold)."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from opendv.agents import (
    _MAX_CLI_FLAG_CHARS,
    AGENT_PACK_IDS,
    IsolatedTempCwd,
    SdkHarnessRunner,
    SubprocessHarnessRunner,
    build_agy_command,
    build_broker_mcp_config_json,
    build_claude_cli_command,
    build_empty_mcp_config_json,
    build_isolated_child_env,
    compute_workspace_agents_sha256,
    count_cl100k_tokens,
    dispatch_scouts_concurrently,
    enforce_v7_tier_lock,
    list_agent_packs,
    load_agent_pack,
    load_agent_pack_with_config,
    reap_process_tree,
    resolve_model_for_agent,
    unwrap_windows_npm_shim,
)
from opendv.agents.cli_subprocess_base import (
    _attach_windows_job_object,
    _configure_win32_kernel32,
    extract_json_from_stdout,
    redact_telemetry_text,
    stream_cli_subprocess_lines,
    strip_markdown_json_fences,
    validate_cli_structured_output,
)
from opendv.agents.ports import AgentTurnRequest
from opendv.models.diagnostics import ClarificationQuestion
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import ControlSignalRecord, RunCheckpointRecord
from opendv.models.policy import STEP4_DEFAULT_BUDGET
from opendv.models.project import TrustedWorkspaceSpec
from opendv.models.vault_ir import DraftShardIR, DraftVaultIR
from opendv.models.wire_dtos_discovery import HoldAnswersReq, ScoutDigest
from opendv.models.wire_dtos_vault import HorizonCriticVerdict, SemanticCriticVerdict
from opendv.sources.profiler import profile_source
from tests.helpers import (
    DeterministicMockHarness,
    InMemoryControlSignalReader,
    InMemoryRunCheckpointStore,
    InMemoryRunEventSink,
    InMemoryScoutToolDispatcher,
    MockTurnScript,
    RecordsSourceAdapter,
    synthesize_minimal_schema_instance,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_AM = REPO_ROOT / "data" / "public_am"


def test_uat_v25_agt_01_pack_loader_and_trusted_workspace_overrides(tmp_path: Path) -> None:
    """UAT-V25-AGT-01: Load 6 single-format Markdown packs and enforce workspace trust digest."""
    summaries = list_agent_packs()
    assert [s.agent_id for s in summaries] == list(AGENT_PACK_IDS)
    assert len(summaries) == 6
    assert all(s.source == "builtin" for s in summaries)

    expected_meta = {
        "ontology-first-mate": ("step4", "STANDARD_WORKER"),
        "table-cluster-scout": ("step4", "STANDARD_WORKER"),
        "page-range-scout": ("step4", "STANDARD_WORKER"),
        "ship-vault": ("step6", "STANDARD_WORKER"),
        "dv-semantic-critic": ("step6", "FRONTIER_CRITIC"),
        "dv-horizon-critic": ("step6", "FRONTIER_CRITIC"),
    }
    for agent_id, (stage, tier) in expected_meta.items():
        detail, cfg = load_agent_pack_with_config(agent_id)
        assert detail.summary.source == "builtin"
        assert detail.summary.stage == stage
        assert detail.model_tier == tier
        assert detail.trusted is True
        assert len(detail.summary.sha256) == 64
        assert "<instructions>" not in detail.markdown_body
        assert "<role>" not in detail.markdown_body
        if agent_id == "ontology-first-mate":
            assert "dispatch_scout" in cfg.allowed_tools
            assert "inspect_table_cluster" in cfg.disallowed_tools
            assert "Agent" in cfg.disallowed_tools
        if stage == "step6":
            assert cfg.allowed_tools == []

    with pytest.raises(OpenDVError) as exc_unknown:
        load_agent_pack("nonexistent-agent")
    assert exc_unknown.value.error_code == ErrorCode.RESOURCE_NOT_FOUND

    override_dir = tmp_path / ".opendv" / "agents" / "ontology-first-mate"
    override_dir.mkdir(parents=True)
    override_file = override_dir / "SKILL.md"
    override_file.write_text(
        "---\n"
        "agent_id: ontology-first-mate\n"
        "name: Custom First Mate\n"
        "role: Step 4 Coordinator Override\n"
        "stage: step4\n"
        "model_tier: STANDARD_WORKER\n"
        "allowed_tools:\n"
        "  - dispatch_scout\n"
        "disallowed_tools:\n"
        "  - Agent\n"
        "---\n\n"
        "# Custom Override Body\n\nFollow Hard Rule 1.\n",
        encoding="utf-8",
    )

    with pytest.raises(OpenDVError) as exc_untrusted:
        load_agent_pack("ontology-first-mate", workspace_root=tmp_path)
    assert exc_untrusted.value.error_code == ErrorCode.UNTRUSTED_WORKSPACE_OVERRIDE

    with pytest.raises(OpenDVError) as exc_wrong_digest:
        load_agent_pack(
            "ontology-first-mate",
            workspace_root=tmp_path,
            trusted_workspaces=[
                TrustedWorkspaceSpec(
                    workspace_root=str(tmp_path.resolve()),
                    agent_pack_sha256="0" * 64,
                    trusted_at="2026-11-23T00:00:00Z",
                )
            ],
        )
    assert exc_wrong_digest.value.error_code == ErrorCode.UNTRUSTED_WORKSPACE_OVERRIDE

    ws_digest = compute_workspace_agents_sha256(tmp_path)
    assert ws_digest is not None
    trusted_detail = load_agent_pack(
        "ontology-first-mate",
        workspace_root=tmp_path,
        trusted_workspaces=[
            TrustedWorkspaceSpec(
                workspace_root=str(tmp_path.resolve()),
                agent_pack_sha256=ws_digest,
                trusted_at="2026-11-23T00:00:00Z",
            )
        ],
    )
    assert trusted_detail.summary.source == "workspace_override"
    assert trusted_detail.summary.name == "Custom First Mate"
    assert trusted_detail.trusted is True

    # Tamper after trusting -> fails closed
    override_file.write_text(override_file.read_text(encoding="utf-8") + "\nTampered.\n", encoding="utf-8")
    with pytest.raises(OpenDVError) as exc_tampered:
        list_agent_packs(
            workspace_root=tmp_path,
            trusted_workspaces=[
                TrustedWorkspaceSpec(
                    workspace_root=str(tmp_path.resolve()),
                    agent_pack_sha256=ws_digest,
                    trusted_at="2026-11-23T00:00:00Z",
                )
            ],
        )
    assert exc_tampered.value.error_code == ErrorCode.UNTRUSTED_WORKSPACE_OVERRIDE


def test_uat_v25_agt_02_model_tier_matrix_and_zero_legacy_remap() -> None:
    """UAT-V25-AGT-02: Native (provider, ModelTier) defaults, env overrides, strict V7 lock."""
    expected_defaults = {
        ("agy", "FRONTIER_CRITIC"): "gemini-4-argon-high",
        ("agy", "STANDARD_WORKER"): "gemini-3.8-flash-high",
        ("gemini", "FRONTIER_CRITIC"): "gemini-2.5-pro",
        ("gemini", "STANDARD_WORKER"): "gemini-2.5-flash",
        ("claude-cli", "FRONTIER_CRITIC"): "claude-opus-4-6",
        ("claude-cli", "STANDARD_WORKER"): "claude-sonnet-4-6",
        ("claude", "FRONTIER_CRITIC"): "claude-opus-4-6",
        ("claude", "STANDARD_WORKER"): "claude-sonnet-4-6",
    }
    for (prov, tier), expected_id in expected_defaults.items():
        res = resolve_model_for_agent(prov, tier, env={})
        assert res.model_id == expected_id
        assert res.source == "default"

    env_map = {
        "OPENDV_AGY_HIGH_MODEL": "agy-custom-high",
        "OPENDV_AGY_FAST_MODEL": "agy-custom-fast",
        "OPENDV_GEMINI_HIGH_MODEL": "gemini-custom-pro",
        "OPENDV_GEMINI_FAST_MODEL": "gemini-custom-flash",
        "OPENDV_CLAUDE_HIGH_MODEL": "claude-custom-opus",
        "OPENDV_CLAUDE_FAST_MODEL": "claude-custom-sonnet",
    }
    assert resolve_model_for_agent("agy", "FRONTIER_CRITIC", env=env_map).model_id == "agy-custom-high"
    assert resolve_model_for_agent("agy", "STANDARD_WORKER", env=env_map).model_id == "agy-custom-fast"
    assert resolve_model_for_agent("gemini", "FRONTIER_CRITIC", env=env_map).model_id == "gemini-custom-pro"
    assert resolve_model_for_agent("gemini", "STANDARD_WORKER", env=env_map).model_id == "gemini-custom-flash"
    assert resolve_model_for_agent("claude-cli", "FRONTIER_CRITIC", env=env_map).model_id == "claude-custom-opus"
    assert resolve_model_for_agent("claude", "STANDARD_WORKER", env=env_map).model_id == "claude-custom-sonnet"

    # Strict policy locks V7 to FRONTIER_CRITIC; V8 is always FRONTIER_CRITIC
    strict_v7 = resolve_model_for_agent(
        "claude-cli",
        "STANDARD_WORKER",
        agent_id="dv-semantic-critic",
        cegar_policy="strict",
        env={},
    )
    assert strict_v7.tier == "FRONTIER_CRITIC"
    assert strict_v7.model_id == "claude-opus-4-6"

    v8_locked = resolve_model_for_agent(
        "agy",
        "STANDARD_WORKER",
        agent_id="dv-horizon-critic",
        cegar_policy="balanced",
        env={},
    )
    assert v8_locked.tier == "FRONTIER_CRITIC"
    assert v8_locked.model_id == "gemini-4-argon-high"

    with pytest.raises(OpenDVError) as exc_lock:
        enforce_v7_tier_lock("strict", "STANDARD_WORKER")
    assert exc_lock.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Zero _AGY_ONLY_CLAUDE_MODEL_MAP anywhere in src/opendv/agents/
    agents_dir = REPO_ROOT / "src" / "opendv" / "agents"
    for py_file in agents_dir.rglob("*.py"):
        assert "_AGY_ONLY_CLAUDE_MODEL_MAP" not in py_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_uat_v25_agt_03_subprocess_flags_env_allowlist_unwrap_and_reaping(
    tmp_path: Path,
) -> None:
    """UAT-V25-AGT-03: agy/claude-cli argv, _MAX_CLI_FLAG_CHARS=7500, env allowlist, .cmd unwrap."""
    assert _MAX_CLI_FLAG_CHARS == 7500

    # 1. agy command contract (short vs oversized > 7500, no unsupported mcp/tools flags)
    agy_short_cmd, agy_short_stdin = build_agy_command(
        "agy.exe",
        model_id="gemini-3.8-flash-high",
        prompt="short prompt",
    )
    assert "--print=short prompt" in agy_short_cmd
    assert agy_short_stdin == ""
    for unsupported in ("--mcp-config", "--strict-mcp-config", "--tools", "--allowedTools", "--allowed-tools", "--resume", "--conversation"):
        assert unsupported not in agy_short_cmd

    long_prompt = "X" * (_MAX_CLI_FLAG_CHARS + 50)
    agy_long_cmd, agy_long_stdin = build_agy_command(
        "agy.exe",
        model_id="gemini-3.8-flash-high",
        prompt=long_prompt,
    )
    assert "--input-format" in agy_long_cmd
    assert not any(arg.startswith("--print") for arg in agy_long_cmd)
    assert agy_long_stdin == long_prompt

    # 2. claude-cli command contract Step 4 vs Step 6
    c4_cmd, c4_stdin = build_claude_cli_command(
        "claude.exe",
        model_id="claude-sonnet-4-6",
        step="step4",
        mcp_config_path=str(tmp_path / "coord.json"),
        prompt="Discover concepts",
    )
    assert "--tools" in c4_cmd
    assert c4_cmd[c4_cmd.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in c4_cmd
    assert "--allowedTools" in c4_cmd
    assert "mcp__opendv_broker__*" in c4_cmd
    assert c4_stdin == "Discover concepts"
    assert "--resume" not in c4_cmd and "--conversation" not in c4_cmd

    c6_cmd, _ = build_claude_cli_command(
        "claude.exe",
        model_id="claude-opus-4-6",
        step="step6",
        mcp_config_path=str(tmp_path / "empty.json"),
        prompt="Synthesize DraftVaultIR",
    )
    assert "--allowedTools" not in c6_cmd
    assert build_empty_mcp_config_json() == '{"mcpServers":{}}'
    broker_json = json.loads(build_broker_mcp_config_json("http://broker.local/mcp/1/coord", role_kind="coord"))
    assert broker_json["mcpServers"]["opendv_broker"]["headers"]["Authorization"] == "Bearer ${OPENDV_MCP_COORD_TOKEN}"

    # 3. Windows npm .cmd -> claude.exe unwrap
    npm_dir = tmp_path / "npm"
    shim_path = npm_dir / "claude.cmd"
    exe_path = npm_dir / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    exe_path.parent.mkdir(parents=True)
    shim_path.write_text("@echo off\n", encoding="utf-8")
    exe_path.write_text("binary", encoding="utf-8")
    unwrapped = unwrap_windows_npm_shim(str(shim_path), os_name="nt", is_file_fn=lambda p: Path(p).is_file())
    assert Path(unwrapped) == exe_path

    # 4. Minimal allowlisted child env + single role token in Step 4, 0 tokens in Step 6
    dirty_parent = {
        "PATH": "/usr/bin",
        "LANG": "en_US.UTF-8",
        "HOME": str(tmp_path),
        "GEMINI_API_KEY": "gem-secret",
        "ANTHROPIC_API_KEY": "ant-secret",
        "DATABRICKS_TOKEN": "dapi-leak",
        "AWS_SECRET_ACCESS_KEY": "aws-leak",
        "OPENDV_MCP_COORD_TOKEN": "coord-tok",
        "OPENDV_MCP_SCOUT_TOKEN": "scout-tok",
    }
    coord_env = build_isolated_child_env(
        provider="claude-cli",
        step="step4",
        role_kind="coord",
        role_token="coord-tok",
        parent_env=dirty_parent,
    )
    assert coord_env["ANTHROPIC_API_KEY"] == "ant-secret"
    assert coord_env["OPENDV_MCP_COORD_TOKEN"] == "coord-tok"
    assert "OPENDV_MCP_SCOUT_TOKEN" not in coord_env
    assert "GEMINI_API_KEY" not in coord_env
    assert "DATABRICKS_TOKEN" not in coord_env
    assert "AWS_SECRET_ACCESS_KEY" not in coord_env

    scout_env = build_isolated_child_env(
        provider="agy",
        step="step4",
        role_kind="scout",
        role_token="scout-tok",
        parent_env=dirty_parent,
    )
    assert scout_env["GEMINI_API_KEY"] == "gem-secret"
    assert scout_env["OPENDV_MCP_SCOUT_TOKEN"] == "scout-tok"
    assert "OPENDV_MCP_COORD_TOKEN" not in scout_env
    assert "ANTHROPIC_API_KEY" not in scout_env

    step6_env = build_isolated_child_env(
        provider="claude-cli",
        step="step6",
        role_kind="coord",
        role_token="coord-tok",
        parent_env=dirty_parent,
    )
    assert "OPENDV_MCP_COORD_TOKEN" not in step6_env
    assert "OPENDV_MCP_SCOUT_TOKEN" not in step6_env

    # 5. SubprocessHarnessRunner empty temp cwd + NDJSON stream + <MASKED_PII> telemetry redaction
    captured_cwds: list[Path] = []
    captured_cmds: list[list[str]] = []

    async def fake_stream(
        cmd: list[str],
        *,
        input_text: str,
        env: dict[str, str],
        cwd: Path,
        timeout_seconds: float,
        state_out: dict[str, Any],
        cli_label: str,
    ) -> AsyncIterator[str]:
        _ = input_text, env, timeout_seconds, cli_label
        captured_cmds.append(cmd)
        captured_cwds.append(cwd)
        assert list(cwd.iterdir()) == [] or {p.name for p in cwd.iterdir()} <= {"schema.json", "mcp_config.json"}
        verdict = {"score": 93, "findings": ["Contains <MASKED_PII> check"], "accepted_waiver_ids": []}
        lines = [
            json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "<MASKED_PII> ok"}}}),
            json.dumps({"type": "result", "subtype": "success", "structured_output": verdict, "usage": {"input_tokens": 50, "output_tokens": 25}}),
        ]
        state_out["returncode"] = 0
        state_out["stdout"] = "\n".join(lines)
        state_out["stderr"] = ""
        for ln in lines:
            yield ln

    sink = InMemoryRunEventSink()
    runner = SubprocessHarnessRunner(
        "claude-cli",
        binary_path=str(shim_path),
        env=dirty_parent,
        stream_fn=fake_stream,
    )
    cp = RunCheckpointRecord(
        run_id="run-sub-1",
        step="step6",
        phase="S6-05",
        status="HOLD_CHECKPOINTED",
        last_seq=1,
        sources_fingerprint="abc",
        budget_turns_used=1,
        budget_tokens_used=100,
        token_count_source="estimated",
        resume_payload={"answer": "uti_id"},
    )
    res = await runner.run_turn(
        AgentTurnRequest(
            run_id="run-sub-1",
            agent_id="dv-semantic-critic",
            step="step6",
            prompt="Audit IR",
            output_schema=SemanticCriticVerdict,
            model_tier="FRONTIER_CRITIC",
            checkpoint=cp,
        ),
        event_sink=sink,
    )
    assert isinstance(res.structured_output, SemanticCriticVerdict)
    assert res.structured_output.score == 93
    assert res.token_count_source == "reported"
    assert not any("--resume" in c or "--conversation" in c for c in captured_cmds)
    assert all("<MASKED_PII>" not in json.dumps(ev.payload) for ev in sink.events)

    # 6. IsolatedTempCwd & real OS process-tree reaping
    with IsolatedTempCwd() as tmp_cwd:
        assert tmp_cwd.is_dir() and list(tmp_cwd.iterdir()) == []
    assert not tmp_cwd.exists()

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(30)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await reap_process_tree(proc)
    assert proc.returncode is not None


@pytest.mark.asyncio
async def test_uat_v25_agt_04_sdk_runner_fail_closed_tool_scoping_and_tokens() -> None:
    """UAT-V25-AGT-04: DRIVER_MISSING, AGENT_NOT_CONFIGURED, Step 4/6 tool scope, tiktoken fallback."""
    def missing_loader(name: str) -> Any:
        raise ModuleNotFoundError(f"No module named {name!r}")

    for prov in ("claude", "gemini"):
        runner_missing = SdkHarnessRunner(prov, module_loader=missing_loader)
        with pytest.raises(OpenDVError) as exc_drv:
            await runner_missing.run_turn(
                AgentTurnRequest(run_id="r-sdk", agent_id="ship-vault", step="step6", prompt="hi")
            )
        assert exc_drv.value.error_code == ErrorCode.DRIVER_MISSING

        runner_nokey = SdkHarnessRunner(prov, env={}, module_loader=lambda _n: object())
        with pytest.raises(OpenDVError) as exc_key:
            await runner_nokey.run_turn(
                AgentTurnRequest(run_id="r-sdk", agent_id="ship-vault", step="step6", prompt="hi")
            )
        assert exc_key.value.error_code == ErrorCode.AGENT_NOT_CONFIGURED

    captured_calls: list[dict[str, Any]] = []

    class _FakeClaudeClient:
        class _Messages:
            async def create(self, **kwargs: Any) -> dict[str, Any]:
                captured_calls.append(kwargs)
                return {
                    "text": json.dumps({"score": 91, "findings": [], "accepted_waiver_ids": []}),
                    "usage": {},
                    "scout_calls": [{"scout_kind": "table_cluster", "target_ids": ["c0", "c1"]}],
                }

        messages = _Messages()

    class _FakeGeminiClient:
        class _Models:
            def generate_content(self, **kwargs: Any) -> dict[str, Any]:
                captured_calls.append(kwargs)
                return {
                    "text": json.dumps({"score": 94, "findings": [], "accepted_waiver_ids": []}),
                    "usage": {"prompt_token_count": 40, "candidates_token_count": 20},
                }

        models = _Models()

    dispatcher = InMemoryScoutToolDispatcher()
    claude_runner = SdkHarnessRunner(
        "claude",
        env={"ANTHROPIC_API_KEY": "sk-test"},
        module_loader=lambda _n: object(),
        client_factory=lambda _p, _m, _k: _FakeClaudeClient(),
    )
    res_step4 = await claude_runner.run_turn(
        AgentTurnRequest(
            run_id="r-sdk-4",
            agent_id="ontology-first-mate",
            step="step4",
            role_kind="coord",
            prompt="Coordinate discovery",
        ),
        scout_dispatcher=dispatcher,
    )
    assert "tools" in captured_calls[-1]
    assert len(captured_calls[-1]["tools"]) == 5
    assert res_step4.token_count_source == "estimated"
    assert res_step4.total_tokens == (
        count_cl100k_tokens("Coordinate discovery") + count_cl100k_tokens(res_step4.text_output)
    )
    assert len(dispatcher.calls) == 1

    # Also test concurrent asyncio.gather scout dispatch
    gathered = await dispatch_scouts_concurrently(
        dispatcher,
        [("table_cluster", ["c0"]), ("page_range", ["doc1:1-2"])],
        run_id="r-sdk-4",
    )
    assert len(gathered) == 2

    gemini_runner = SdkHarnessRunner(
        "gemini",
        env={"GEMINI_API_KEY": "gm-test"},
        module_loader=lambda _n: object(),
        client_factory=lambda _p, _m, _k: _FakeGeminiClient(),
    )
    res_step6 = await gemini_runner.run_turn(
        AgentTurnRequest(
            run_id="r-sdk-6",
            agent_id="dv-semantic-critic",
            step="step6",
            role_kind="none",
            prompt="Critique IR",
            output_schema=SemanticCriticVerdict,
        )
    )
    assert "tools" not in captured_calls[-1]["config"]
    assert res_step6.token_count_source == "reported"
    assert res_step6.total_tokens == 60
    assert isinstance(res_step6.structured_output, SemanticCriticVerdict)


@pytest.mark.asyncio
async def test_uat_v25_agt_05_step4_brokered_hold_drain_and_stateless_resume() -> None:
    """UAT-V25-AGT-05: Step 4 scout drain, HOLD_CHECKPOINTED, pause, stateless resume."""
    otc_prof = profile_source("src_w2_otc", "csv", str(PUBLIC_AM / "wave2_ambiguous_otc_collateral.csv"))
    otc_cols = {c.col_name: c for c in otc_prof.tables[0].columns}
    assert otc_cols["counterparty_aba_raw"].sample_values == ["<MASKED_PII>"] * 5

    # Verify V2 RecordsSourceAdapter and synthesize_minimal_schema_instance
    rec_prof = RecordsSourceAdapter().to_source_profile(
        "mem_src",
        "otc_mem",
        [{"trade_id": "T1", "email": "alice@example.com"}],
        pk_cols=["trade_id"],
    )
    assert rec_prof.tables[0].columns[1].sample_values == ["<MASKED_PII>"]
    synth_verdict = synthesize_minimal_schema_instance(SemanticCriticVerdict)
    assert isinstance(synth_verdict, SemanticCriticVerdict)

    started = asyncio.Event()
    release = asyncio.Event()
    dispatcher = InMemoryScoutToolDispatcher(
        digests_by_target={
            "cluster_otc": ScoutDigest(
                scout_kind="table_cluster",
                target_id="cluster_otc",
                candidate_concepts=["CollateralTrade"],
                key_columns=["uti_id", "isda_master_id"],
                pii_columns=["counterparty_aba_raw"],
            )
        },
        started_event=started,
        release_event=release,
    )
    sink = InMemoryRunEventSink()
    cp_store = InMemoryRunCheckpointStore()
    ctrl_reader = InMemoryControlSignalReader()
    cp_store.acquire_lease("run-step4-1")

    harness = DeterministicMockHarness(
        scripts_by_agent={
            "ontology-first-mate": [
                MockTurnScript(
                    structured_output={"clarify": True},
                    scout_calls=[("table_cluster", ["cluster_otc"])],
                )
            ]
        }
    )

    turn_task = asyncio.create_task(
        harness.run_turn(
            AgentTurnRequest(
                run_id="run-step4-1",
                agent_id="ontology-first-mate",
                step="step4",
                role_kind="coord",
                prompt="Discover OTC concepts",
            ),
            event_sink=sink,
            scout_dispatcher=dispatcher,
        )
    )
    await started.wait()
    assert dispatcher.active_count == 1
    ctrl_reader.push(
        ControlSignalRecord(
            signal_id="sig-pause-1",
            run_id="run-step4-1",
            action="pause",
            created_at="2026-11-23T00:00:00Z",
        )
    )
    # Drain active scouts before committing HOLD_CHECKPOINTED
    release.set()
    turn_res = await turn_task
    assert dispatcher.active_count == 0
    assert dispatcher.completed_count == 1
    assert len(turn_res.tool_events) == 1

    # Commit pause checkpoint with pending_questions=[] and release lease
    signals = ctrl_reader.poll_signals("run-step4-1")
    assert signals[0].action == "pause"
    cp = RunCheckpointRecord(
        run_id="run-step4-1",
        step="step4",
        phase="S4-03",
        status="HOLD_CHECKPOINTED",
        last_seq=len(sink.events),
        sources_fingerprint=otc_prof.schema_fingerprint,
        pending_questions=[],
        budget_turns_used=turn_res.turns_used,
        budget_tokens_used=turn_res.total_tokens,
        token_count_source=turn_res.token_count_source,
        resume_payload={"scout_digests": ["cluster_otc"]},
    )
    cp_store.save_checkpoint(cp)
    cp_store.release_active_lease("run-step4-1")
    assert cp_store.lease_held["run-step4-1"] is False

    # Stateless resume with HoldAnswersReq({}) does not inflate HarnessBudget
    loaded_cp = cp_store.load_checkpoint("run-step4-1")
    assert loaded_cp is not None
    assert loaded_cp.pending_questions == []
    resume_answers = HoldAnswersReq(answers={})
    resumed_res = await harness.run_turn(
        AgentTurnRequest(
            run_id="run-step4-1",
            agent_id="ontology-first-mate",
            step="step4",
            role_kind="coord",
            prompt=f"Resume with answers={resume_answers.answers}",
            budget=STEP4_DEFAULT_BUDGET,
            checkpoint=loaded_cp,
        ),
        event_sink=sink,
    )
    assert loaded_cp.budget_turns_used == 1
    assert resumed_res.turns_used == 1
    # Also verify ClarificationQuestion round-trip
    q = ClarificationQuestion(
        question_id="q-otc-key",
        concept_name="CollateralTrade",
        title="Competing natural keys",
        prompt="Select uti_id or isda_master_id",
        sample_rows=["uti_id=U1"],
        impact_if_locked="Locks Hub BK",
    )
    assert q.question_id == "q-otc-key"


def test_resolve_model_for_agent_rejects_invalid_model_tier() -> None:
    """Unknown ModelTier raises SYNTAX_INVALID_ARGUMENT upfront (even on locked agents)."""
    with pytest.raises(OpenDVError) as exc_plain:
        resolve_model_for_agent("agy", "UNKNOWN_TIER", env={})  # type: ignore[arg-type]
    assert exc_plain.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    with pytest.raises(OpenDVError) as exc_v8:
        resolve_model_for_agent(
            "agy",
            "BOGUS_TIER",  # type: ignore[arg-type]
            agent_id="dv-horizon-critic",
            env={},
        )
    assert exc_v8.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    with pytest.raises(OpenDVError) as exc_v7:
        resolve_model_for_agent(
            "claude-cli",
            "BOGUS_TIER",  # type: ignore[arg-type]
            agent_id="dv-semantic-critic",
            cegar_policy="strict",
            env={},
        )
    assert exc_v7.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_runners_scrub_raw_pii_honor_metadata_overrides_and_sdk_resume(
    tmp_path: Path,
) -> None:
    """Regression: raw PII scrubbing, metadata['model_overrides'], SDK checkpoint & APIError."""
    raw_pii_markers = (
        "123-45-6789",
        "alice@example.com",
        "021000021",
        "12-3456789",
        "(555) 123-4567",
        "DE89370400440532013000",
        "4532-0151-1283-0366",
        "<MASKED_PII>",
    )
    pii_blob = (
        "SSN 123-45-6789 email alice@example.com ABA routing 021000021 "
        "EIN 12-3456789 phone (555) 123-4567 IBAN DE89370400440532013000 "
        "card 4532-0151-1283-0366 <MASKED_PII>"
    )

    fake_bin = tmp_path / "agy.exe"
    fake_bin.write_text("exe", encoding="utf-8")

    async def pii_stream(
        cmd: list[str],
        *,
        input_text: str,
        env: dict[str, str],
        cwd: Path,
        timeout_seconds: float,
        state_out: dict[str, Any],
        cli_label: str,
    ) -> AsyncIterator[str]:
        _ = cmd, input_text, env, cwd, timeout_seconds, cli_label
        lines = [
            json.dumps(
                {
                    "event": "step_update",
                    "step_update": {
                        "step_type": "tool",
                        "state": "ACTIVE",
                        "step_index": 1,
                        "tool_name": "read_profile",
                        "tool_info": {"parameters": {"val": pii_blob}},
                    },
                }
            ),
            json.dumps(
                {
                    "event": "step_update",
                    "step_update": {
                        "step_type": "tool",
                        "state": "DONE",
                        "step_index": 1,
                        "tool_name": "read_profile",
                        "tool_info": {"output": f"res {pii_blob}"},
                    },
                }
            ),
            json.dumps({"type": "thinking_delta", "thinking": f"think {pii_blob}"}),
            json.dumps({"type": "text_delta", "text": f"text {pii_blob}"}),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "response": json.dumps({"score": 92, "findings": [], "accepted_waiver_ids": []}),
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }
            ),
        ]
        state_out["returncode"] = 0
        state_out["stdout"] = "\n".join(lines)
        state_out["stderr"] = ""
        for ln in lines:
            yield ln

    sub_sink = InMemoryRunEventSink()
    sub_runner = SubprocessHarnessRunner(
        "agy",
        binary_path=str(fake_bin),
        env={"GEMINI_API_KEY": "gm-key"},
        stream_fn=pii_stream,
    )
    sub_res = await sub_runner.run_turn(
        AgentTurnRequest(
            run_id="r-pii-sub",
            agent_id="ship-vault",
            step="step6",
            prompt="Synthesize",
            output_schema=SemanticCriticVerdict,
            metadata={"model_overrides": {"ship-vault": "custom-metadata-model"}},
        ),
        event_sink=sub_sink,
    )
    assert sub_res.model_id == "custom-metadata-model"
    ev_types = {ev.event_type for ev in sub_sink.events}
    assert {"SESSION_START", "TOOL_CALL", "TOOL_RESULT", "THOUGHT_DELTA", "TEXT_DELTA"} <= ev_types
    dumped_sub = json.dumps([ev.payload for ev in sub_sink.events])
    for marker in raw_pii_markers:
        assert marker not in dumped_sub
    assert "[REDACTED]" in dumped_sub

    # Stderr error message must also be scrubbed
    async def err_stream(
        cmd: list[str],
        *,
        input_text: str,
        env: dict[str, str],
        cwd: Path,
        timeout_seconds: float,
        state_out: dict[str, Any],
        cli_label: str,
    ) -> AsyncIterator[str]:
        _ = cmd, input_text, env, cwd, timeout_seconds, cli_label
        state_out["returncode"] = 1
        state_out["stdout"] = ""
        state_out["stderr"] = f"boom {pii_blob}"
        if False:
            yield ""

    err_runner = SubprocessHarnessRunner(
        "agy",
        binary_path=str(fake_bin),
        env={"GEMINI_API_KEY": "gm-key"},
        stream_fn=err_stream,
    )
    with pytest.raises(OpenDVError) as exc_sub_err:
        await err_runner.run_turn(
            AgentTurnRequest(run_id="r-pii-err", agent_id="ship-vault", step="step6", prompt="p")
        )
    for marker in raw_pii_markers:
        assert marker not in str(exc_sub_err.value)

    # SdkHarnessRunner: metadata['model_overrides'], checkpoint resume context, PII scrub, APIError
    sdk_calls: list[dict[str, Any]] = []

    class _FakeSdkMod:
        class APIError(Exception):
            pass

        class AuthenticationError(Exception):
            pass

    class _FakeClaudePiiClient:
        class _Messages:
            async def create(self, **kwargs: Any) -> dict[str, Any]:
                sdk_calls.append(kwargs)
                return {"text": f"out {pii_blob}", "usage": {"input_tokens": 5, "output_tokens": 5}}

        messages = _Messages()

    class _FakeGeminiPiiClient:
        class _Models:
            def generate_content(self, **kwargs: Any) -> dict[str, Any]:
                sdk_calls.append(kwargs)
                return {"text": f"out {pii_blob}", "usage": {"prompt_token_count": 5, "candidates_token_count": 5}}

        models = _Models()

    cp = RunCheckpointRecord(
        run_id="r-sdk-cp",
        step="step6",
        phase="S6-06",
        status="HOLD_CHECKPOINTED",
        last_seq=4,
        sources_fingerprint="fp",
        budget_turns_used=1,
        budget_tokens_used=10,
        token_count_source="estimated",
        resume_payload={"answer": "uti_id"},
    )
    sdk_sink = InMemoryRunEventSink()
    sdk_claude = SdkHarnessRunner(
        "claude",
        env={"ANTHROPIC_API_KEY": "sk-test"},
        module_loader=lambda _n: _FakeSdkMod,
        client_factory=lambda _p, _m, _k: _FakeClaudePiiClient(),
    )
    res_c = await sdk_claude.run_turn(
        AgentTurnRequest(
            run_id="r-sdk-cp",
            agent_id="ship-vault",
            step="step6",
            prompt="Resume task",
            checkpoint=cp,
            metadata={"model_overrides": {"ship-vault": "custom-sdk-meta-model"}},
        ),
        event_sink=sdk_sink,
    )
    assert res_c.model_id == "custom-sdk-meta-model"
    claude_msg = sdk_calls[-1]["messages"][0]["content"]
    assert "STATELESS HOLD RESUME CONTEXT" in claude_msg
    assert "uti_id" in claude_msg
    assert sdk_sink.events[0].seq == 5

    sdk_gemini = SdkHarnessRunner(
        "gemini",
        env={"GEMINI_API_KEY": "gm-test"},
        module_loader=lambda _n: _FakeSdkMod,
        client_factory=lambda _p, _m, _k: _FakeGeminiPiiClient(),
    )
    res_g = await sdk_gemini.run_turn(
        AgentTurnRequest(
            run_id="r-sdk-cp",
            agent_id="ship-vault",
            step="step6",
            prompt="Resume gemini",
            checkpoint=cp,
            metadata={"model_overrides": {"ship-vault": "custom-gemini-meta"}},
        ),
        event_sink=sdk_sink,
    )
    assert res_g.model_id == "custom-gemini-meta"
    assert "STATELESS HOLD RESUME CONTEXT" in sdk_calls[-1]["contents"]
    assert "uti_id" in sdk_calls[-1]["contents"]

    dumped_sdk = json.dumps([ev.payload for ev in sdk_sink.events])
    for marker in raw_pii_markers:
        assert marker not in dumped_sdk

    # SDK APIError & AuthenticationError mapping
    class _FailingAuthClient:
        class _Messages:
            async def create(self, **kwargs: Any) -> dict[str, Any]:
                _ = kwargs
                raise _FakeSdkMod.AuthenticationError("invalid auth alice@example.com")

        messages = _Messages()

    class _FailingApiClient:
        class _Messages:
            async def create(self, **kwargs: Any) -> dict[str, Any]:
                _ = kwargs
                raise _FakeSdkMod.APIError("upstream rate limit 123-45-6789")

        messages = _Messages()

    auth_runner = SdkHarnessRunner(
        "claude",
        env={"ANTHROPIC_API_KEY": "sk-test"},
        module_loader=lambda _n: _FakeSdkMod,
        client_factory=lambda _p, _m, _k: _FailingAuthClient(),
    )
    with pytest.raises(OpenDVError) as exc_auth:
        await auth_runner.run_turn(
            AgentTurnRequest(run_id="r-auth", agent_id="ship-vault", step="step6", prompt="p")
        )
    assert exc_auth.value.error_code == ErrorCode.AGENT_NOT_CONFIGURED
    assert "alice@example.com" not in str(exc_auth.value)

    api_runner = SdkHarnessRunner(
        "claude",
        env={"ANTHROPIC_API_KEY": "sk-test"},
        module_loader=lambda _n: _FakeSdkMod,
        client_factory=lambda _p, _m, _k: _FailingApiClient(),
    )
    with pytest.raises(OpenDVError) as exc_api:
        await api_runner.run_turn(
            AgentTurnRequest(run_id="r-api", agent_id="ship-vault", step="step6", prompt="p")
        )
    assert exc_api.value.error_code == ErrorCode.AGENT_EXECUTION_FAILED
    assert "123-45-6789" not in str(exc_api.value)

    # Win32 ctypes argtypes/restype hardening check
    k32 = _configure_win32_kernel32()
    if k32 is not None:
        import ctypes

        assert k32.CreateJobObjectW.restype is ctypes.c_void_p
        assert k32.OpenProcess.restype is ctypes.c_void_p
        assert len(k32.SetInformationJobObject.argtypes) == 4


@pytest.mark.asyncio
async def test_pii_error_scrubbing_underscore_keys_phone_iban_and_win32_tree_reap(
    tmp_path: Path,
) -> None:
    """Regression: _cli_ndjson OpenDVError PII scrub, snake_case ABA/(555)123-4567/spaced IBAN, Win32 tree reap."""
    # 1. Underscore JSON keys, (555)123-4567, spaced IBAN, and non-PII numeric preservation
    scrubbed_json = redact_telemetry_text(
        '{"aba_routing":"021000021","routing_number":"021000021","bank_aba":"121000358"} '
        "call (555)123-4567 iban DE89 3704 0044 0532 0130 00 "
        "cluster_id=10001 dt=2026-10-20 order_id=987654321 "
        "non_luhn=4532015112830367 year_id=202600005"
    )
    for leaked in ("021000021", "121000358", "(555)123-4567", "DE89 3704 0044 0532 0130 00"):
        assert leaked not in scrubbed_json
    for kept in ("10001", "2026-10-20", "987654321", "4532015112830367", "202600005"):
        assert kept in scrubbed_json

    # TOOL_CALL args_redacted must scrub underscore ABA keys via SubprocessHarnessRunner
    fake_bin = tmp_path / "agy.exe"
    fake_bin.write_text("exe", encoding="utf-8")

    async def snake_aba_stream(
        cmd: list[str],
        *,
        input_text: str,
        env: dict[str, str],
        cwd: Path,
        timeout_seconds: float,
        state_out: dict[str, Any],
        cli_label: str,
    ) -> AsyncIterator[str]:
        _ = cmd, input_text, env, cwd, timeout_seconds, cli_label
        lines = [
            json.dumps(
                {
                    "event": "step_update",
                    "step_update": {
                        "step_type": "tool",
                        "state": "ACTIVE",
                        "step_index": 1,
                        "tool_name": "lookup_bank",
                        "tool_info": {
                            "parameters": {
                                "aba_routing": "021000021",
                                "routing_number": "021000021",
                                "bank_aba": "121000358",
                                "phone": "(555)123-4567",
                                "iban": "DE89 3704 0044 0532 0130 00",
                            }
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "response": json.dumps({"score": 92, "findings": [], "accepted_waiver_ids": []}),
                    "usage": {"input_tokens": 5, "output_tokens": 5},
                }
            ),
        ]
        state_out["returncode"] = 0
        state_out["stdout"] = "\n".join(lines)
        state_out["stderr"] = ""
        for ln in lines:
            yield ln

    sink = InMemoryRunEventSink()
    await SubprocessHarnessRunner(
        "agy",
        binary_path=str(fake_bin),
        env={"GEMINI_API_KEY": "gm-key"},
        stream_fn=snake_aba_stream,
    ).run_turn(
        AgentTurnRequest(
            run_id="r-snake-aba",
            agent_id="ship-vault",
            step="step6",
            prompt="Synthesize",
            output_schema=SemanticCriticVerdict,
        ),
        event_sink=sink,
    )
    tool_ev = next(ev for ev in sink.events if ev.event_type == "TOOL_CALL")
    args_redacted = str(tool_ev.payload["args_redacted"])
    for leaked in ("021000021", "121000358", "(555)123-4567", "DE89 3704 0044 0532 0130 00"):
        assert leaked not in args_redacted

    # 2. _cli_ndjson OpenDVError messages must scrub raw PII & <MASKED_PII>
    raw_bad = (
        "NOT_JSON SSN 123-45-6789 email alice@example.com ABA routing 021000021 "
        "EIN 12-3456789 phone (555)123-4567 IBAN DE89 3704 0044 0532 0130 00 "
        "card 4532-0151-1283-0366 <MASKED_PII> id=10001 dt=2026-10-20"
    )
    with pytest.raises(OpenDVError) as exc_bad_json:
        extract_json_from_stdout(raw_bad, cli_label="agy CLI")
    err_bad = str(exc_bad_json.value)
    for leaked in (
        "123-45-6789",
        "alice@example.com",
        "021000021",
        "12-3456789",
        "(555)123-4567",
        "DE89 3704 0044 0532 0130 00",
        "4532-0151-1283-0366",
        "<MASKED_PII>",
    ):
        assert leaked not in err_bad
    assert "10001" in err_bad and "2026-10-20" in err_bad

    with pytest.raises(OpenDVError) as exc_val:
        validate_cli_structured_output(
            SemanticCriticVerdict,
            raw_structured=None,
            raw_response_text='{"score":"not-int-123-45-6789","findings":["<MASKED_PII>"],"accepted_waiver_ids":[]}',
            cli_label="claude SDK",
        )
    assert "123-45-6789" not in str(exc_val.value)
    assert "<MASKED_PII>" not in str(exc_val.value)

    # 3. reap_process_tree kills pre-attach grandchild before closing Job Object once
    import os

    gc_pid_file = tmp_path / "gc_pid.txt"
    parent_script = (
        "import subprocess, sys, time, pathlib; "
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        f"pathlib.Path({str(gc_pid_file)!r}).write_text(str(p.pid), encoding='utf-8'); "
        "time.sleep(30)"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        parent_script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    for _ in range(50):
        if gc_pid_file.is_file() and gc_pid_file.read_text(encoding="utf-8").strip():
            break
        await asyncio.sleep(0.05)
    assert gc_pid_file.is_file(), "Grandchild process did not write its PID in time"
    gc_pid = int(gc_pid_file.read_text(encoding="utf-8").strip())

    # Attach Job Object *after* grandchild spawned so grandchild is outside Job Object
    job_handle = _attach_windows_job_object(proc.pid) if os.name == "nt" and proc.pid else None
    await reap_process_tree(proc, job_handle=job_handle)
    assert proc.returncode is not None

    if os.name == "nt":
        import ctypes

        k32 = _configure_win32_kernel32()
        assert k32 is not None
        k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        k32.GetExitCodeProcess.restype = ctypes.c_int
        for _ in range(20):
            h_gc = k32.OpenProcess(0x1000, False, gc_pid)
            if not h_gc:
                break
            code = ctypes.c_uint32(0)
            ok = k32.GetExitCodeProcess(ctypes.c_void_p(h_gc), ctypes.byref(code))
            k32.CloseHandle(ctypes.c_void_p(h_gc))
            if not ok or code.value != 259:
                break
            await asyncio.sleep(0.05)
        h_gc_final = k32.OpenProcess(0x1000, False, gc_pid)
        if h_gc_final:
            code_final = ctypes.c_uint32(0)
            ok_final = k32.GetExitCodeProcess(ctypes.c_void_p(h_gc_final), ctypes.byref(code_final))
            k32.CloseHandle(ctypes.c_void_p(h_gc_final))
            assert not ok_final or code_final.value != 259
    else:
        with pytest.raises(OSError):
            os.kill(gc_pid, 0)


@pytest.mark.asyncio
async def test_pii_escaped_newlines_wide_aba_iban_cc_overlap_and_non_dict_validation(
    tmp_path: Path,
) -> None:
    """Regression: escaped \\n/\\t/\\r & _ boundaries, wide ABA window, IBAN+CC overlap, list validation."""
    # 1. Escaped \n/\t/\r in json.dumps & non-PII preservation
    escaped_json = json.dumps(
        {
            "note": "line1\n123-45-6789\nDE89 3704 0044 0532 0130 00\n12-3456789\nalice@example.com",
            "aba_note": "routing\t021000021",
            "cr": "x\r123-45-6789",
            "keep": "x\n10001\n2026-10-20\n987654321\n4532015112830367\n202600005",
        },
        separators=(",", ":"),
    )
    scrubbed_escaped = redact_telemetry_text(escaped_json)
    for leaked in ("123-45-6789", "DE89", "12-3456789", "alice@example.com", "021000021"):
        assert leaked not in scrubbed_escaped
    for kept in ("10001", "2026-10-20", "987654321", "4532015112830367", "202600005"):
        assert kept in scrubbed_escaped

    # 2. Long snake_case ABA key & Pydantic extra_forbidden ABA (>32 chars) + _-prefixed IBAN
    long_key_scrub = redact_telemetry_text(
        '{"aba_routing_for_domestic_wire_settlement_account":"021000021","order_id":"987654321"}'
    )
    assert "021000021" not in long_key_scrub
    assert "987654321" in long_key_scrub

    with pytest.raises(OpenDVError) as exc_hzn:
        validate_cli_structured_output(
            HorizonCriticVerdict,
            raw_structured={
                "score": 90,
                "classification": "INVALID_DE89 3704 0044 0532 0130 00",
                "aba_routing": "021000021",
            },
            raw_response_text="",
            cli_label="agy CLI",
        )
    err_hzn = str(exc_hzn.value)
    assert "021000021" not in err_hzn
    assert "DE89 3704 0044 0532 0130 00" not in err_hzn

    # Also via SdkHarnessRunner on extra_forbidden + escaped \n in ValidationError
    class _FakeSdk:
        class APIError(Exception):
            pass

    class _BadHorizonClient:
        class _Messages:
            async def create(self, **kwargs: Any) -> dict[str, Any]:
                _ = kwargs
                return {
                    "text": "",
                    "structured_output": {
                        "score": "x\n123-45-6789",
                        "classification": "INVALID_DE89 3704 0044 0532 0130 00",
                        "aba_routing": "021000021",
                    },
                }

        messages = _Messages()

    sdk_bad = SdkHarnessRunner(
        "claude",
        env={"ANTHROPIC_API_KEY": "sk-test"},
        module_loader=lambda _n: _FakeSdk,
        client_factory=lambda _p, _m, _k: _BadHorizonClient(),
    )
    with pytest.raises(OpenDVError) as exc_sdk_bad:
        await sdk_bad.run_turn(
            AgentTurnRequest(
                run_id="r-sdk-bad",
                agent_id="dv-horizon-critic",
                step="step6",
                prompt="Evaluate",
                output_schema=HorizonCriticVerdict,
            )
        )
    for leaked in ("123-45-6789", "DE89 3704 0044 0532 0130 00", "021000021"):
        assert leaked not in str(exc_sdk_bad.value)

    # 3. _RE_IBAN underscore prefix & adjacent hyphenated / space-separated CC PAN
    assert redact_telemetry_text("INVALID_DE89 3704 0044 0532 0130 00") == "INVALID_[REDACTED]"
    iban_hyphen_cc = redact_telemetry_text("DE89 3704 0044 0532 0130 00 4532-0151-1283-0366")
    for leaked in ("DE89", "4532", "0151-1283-0366"):
        assert leaked not in iban_hyphen_cc
    assert iban_hyphen_cc == "[REDACTED] [REDACTED]"

    iban_space_cc = redact_telemetry_text("GB29 NWBK 6016 1331 9268 19 4532 0151 1283 0366")
    for leaked in ("GB29", "4532", "0151", "1283", "0366"):
        assert leaked not in iban_space_cc
    assert iban_space_cc == "[REDACTED] [REDACTED]"

    # 4. Non-dict / non-str raw_structured must still be validated and raise scrubbed OpenDVError
    with pytest.raises(OpenDVError) as exc_list:
        validate_cli_structured_output(
            SemanticCriticVerdict,
            raw_structured=["123-45-6789"],
            raw_response_text="",
            cli_label="agy CLI",
        )
    assert "123-45-6789" not in str(exc_list.value)

    with pytest.raises(OpenDVError):
        validate_cli_structured_output(
            SemanticCriticVerdict,
            raw_structured=42,
            raw_response_text="",
            cli_label="claude SDK",
        )

    # Also verify SubprocessHarnessRunner TOOL_CALL args_redacted scrubs escaped \n/\t
    fake_bin = tmp_path / "agy.exe"
    fake_bin.write_text("exe", encoding="utf-8")

    async def escaped_tool_stream(
        cmd: list[str],
        *,
        input_text: str,
        env: dict[str, str],
        cwd: Path,
        timeout_seconds: float,
        state_out: dict[str, Any],
        cli_label: str,
    ) -> AsyncIterator[str]:
        _ = cmd, input_text, env, cwd, timeout_seconds, cli_label
        lines = [
            json.dumps(
                {
                    "event": "step_update",
                    "step_update": {
                        "step_type": "tool",
                        "state": "ACTIVE",
                        "step_index": 1,
                        "tool_name": "read_note",
                        "tool_info": {
                            "parameters": {
                                "note": "line1\n123-45-6789\nDE89 3704 0044 0532 0130 00\n12-3456789",
                                "aba_note": "routing\t021000021",
                            }
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "response": json.dumps({"score": 92, "findings": [], "accepted_waiver_ids": []}),
                    "usage": {"input_tokens": 5, "output_tokens": 5},
                }
            ),
        ]
        state_out["returncode"] = 0
        state_out["stdout"] = "\n".join(lines)
        state_out["stderr"] = ""
        for ln in lines:
            yield ln

    sink = InMemoryRunEventSink()
    await SubprocessHarnessRunner(
        "agy",
        binary_path=str(fake_bin),
        env={"GEMINI_API_KEY": "gm-key"},
        stream_fn=escaped_tool_stream,
    ).run_turn(
        AgentTurnRequest(
            run_id="r-escaped-tool",
            agent_id="ship-vault",
            step="step6",
            prompt="Synthesize",
            output_schema=SemanticCriticVerdict,
        ),
        event_sink=sink,
    )
    tool_ev = next(ev for ev in sink.events if ev.event_type == "TOOL_CALL")
    args_redacted = str(tool_ev.payload["args_redacted"])
    for leaked in ("123-45-6789", "DE89", "12-3456789", "021000021"):
        assert leaked not in args_redacted


def test_pii_phone_iban_overlap_synthetic_iban_and_camelcase_aba() -> None:
    """Regression: _RE_PHONE separator/ordering vs _RE_IBAN, non-mod-97 IBAN+CC, camelCase ABA, \\b/\\f."""
    # 1. Valid NO93 IBAN + hyphenated/spaced CC PAN & phone-like spaced DE89 IBAN
    assert redact_telemetry_text("NO93 8601 1117 947 4532-0151-1283-0366") == "[REDACTED] [REDACTED]"
    assert redact_telemetry_text("NO93 8601 1117 947 4532 0151 1283 0366") == "[REDACTED] [REDACTED]"
    assert redact_telemetry_text("DE89 123 456 7890 1234 5678") == "[REDACTED]"
    for phone in (
        "(555)123-4567",
        "(555) 123-4567",
        "555-123-4567",
        "+1-555-123-4567",
        "1-555-123-4567",
        "1 555 123 4567",
    ):
        assert redact_telemetry_text(phone) == "[REDACTED]"

    # 2. Synthetic (non-mod-97) spaced IBAN + spaced CC PAN overlap vs standalone synthetic IBAN
    assert (
        redact_telemetry_text("US12 3456 7890 1234 5678 4532 0151 1283 0366")
        == "[REDACTED] [REDACTED]"
    )
    assert (
        redact_telemetry_text("DE99 3704 0044 0532 0130 00 4532 0151 1283 0366")
        == "[REDACTED] [REDACTED]"
    )
    assert redact_telemetry_text("US12 3456 7890 1234 5678") == "[REDACTED]"

    # 3. camelCase ABA/routing keys scrubbed without false positives on ordinary words
    camel_json = json.dumps(
        {
            "abaRouting": "021000021",
            "routingNumber": "121000358",
            "bankAba": "021000021",
            "myRoutingNumber": "121000358",
            "wireRtn": "021000021",
        },
        separators=(",", ":"),
    )
    scrubbed_camel = redact_telemetry_text(camel_json)
    assert "021000021" not in scrubbed_camel
    assert "121000358" not in scrubbed_camel

    non_aba = (
        "database 021000021 bar 021000021 abacus 021000021 "
        "ABACUS 021000021 sprouting 121000358 partner 021000021"
    )
    assert redact_telemetry_text(non_aba) == non_aba

    # 4. Escaped \b / \f in json.dumps
    bf_json = json.dumps(
        {"b": "x\b123-45-6789", "f": "x\f123-45-6789", "e": "x\balice@example.com"},
        separators=(",", ":"),
    )
    scrubbed_bf = redact_telemetry_text(bf_json)
    assert "123-45-6789" not in scrubbed_bf
    assert "alice@example.com" not in scrubbed_bf
    assert json.loads(scrubbed_bf) == {
        "b": "x\b[REDACTED]",
        "f": "x\f[REDACTED]",
        "e": "x\b[REDACTED]",
    }


@pytest.mark.asyncio
async def test_pii_multi_iban_acronym_aba_and_iban_amex_overlap(tmp_path: Path) -> None:
    """Regression: multi-IBAN re-scanning, acronym+TitleCase ABA keys, and IBAN+Amex PAN overlap."""
    # 1. Adjacent space-separated IBANs (2 and 3+ in a row, valid and synthetic)
    assert (
        redact_telemetry_text("DE89 3704 0044 0532 0130 00 GB29 NWBK 6016 1331 9268 19")
        == "[REDACTED] [REDACTED]"
    )
    assert (
        redact_telemetry_text("NO93 8601 1117 947 GB29 NWBK 6016 1331 9268 19")
        == "[REDACTED] [REDACTED]"
    )
    assert (
        redact_telemetry_text("DE89 3704 0044 0532 0130 00 US12 3456 7890 1234 5678")
        == "[REDACTED] [REDACTED]"
    )
    assert (
        redact_telemetry_text("US12 3456 7890 1234 5678 DE89 3704 0044 0532 0130 00")
        == "[REDACTED] [REDACTED]"
    )
    assert redact_telemetry_text("NO93 8601 1117 947 NO93 8601 1117 947") == "[REDACTED] [REDACTED]"
    assert (
        redact_telemetry_text(
            "US12 3456 7890 1234 5678 DE89 3704 0044 0532 0130 00 GB29 NWBK 6016 1331 9268 19"
        )
        == "[REDACTED] [REDACTED] [REDACTED]"
    )

    # 2. IBAN + trailing 15-digit Amex PAN (even when synthetic IBAN + prefix passes mod-97)
    assert redact_telemetry_text("US12 3456 7890 1234 5678 3782 822463 10005") == "[REDACTED] [REDACTED]"
    assert redact_telemetry_text("DE89 3704 0044 0532 0130 00 3782 822463 10005") == "[REDACTED] [REDACTED]"

    # 3. Uppercase-acronym + TitleCase ABA/RTN keys + ABACUS negative control
    acronym_keys = (
        "ABACode",
        "ABARouting",
        "ABANumber",
        "RTNCode",
        "bankABARouting",
        "bankABANumber",
        "wireRTNCode",
        "ROUTINGCode",
    )
    for key in acronym_keys:
        scrubbed_key = redact_telemetry_text(json.dumps({key: "021000021"}, separators=(",", ":")))
        assert "021000021" not in scrubbed_key, f"Leaked ABA for key {key}: {scrubbed_key}"
    assert redact_telemetry_text("ABACUS 021000021") == "ABACUS 021000021"

    # 4. E2E SubprocessHarnessRunner TOOL_CALL args_redacted scrubs all 3 patterns
    fake_bin = tmp_path / "agy.exe"
    fake_bin.write_text("exe", encoding="utf-8")

    async def multi_pii_stream(
        cmd: list[str],
        *,
        input_text: str,
        env: dict[str, str],
        cwd: Path,
        timeout_seconds: float,
        state_out: dict[str, Any],
        cli_label: str,
    ) -> AsyncIterator[str]:
        _ = cmd, input_text, env, cwd, timeout_seconds, cli_label
        lines = [
            json.dumps(
                {
                    "event": "step_update",
                    "step_update": {
                        "step_type": "tool",
                        "state": "ACTIVE",
                        "step_index": 1,
                        "tool_name": "inspect_wire",
                        "tool_info": {
                            "parameters": {
                                "ABACode": "021000021",
                                "bankABANumber": "121000358",
                                "ibans": "DE89 3704 0044 0532 0130 00 GB29 NWBK 6016 1331 9268 19",
                                "amex_after_iban": "US12 3456 7890 1234 5678 3782 822463 10005",
                            }
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "response": json.dumps({"score": 95, "findings": [], "accepted_waiver_ids": []}),
                    "usage": {"input_tokens": 5, "output_tokens": 5},
                }
            ),
        ]
        state_out["returncode"] = 0
        state_out["stdout"] = "\n".join(lines)
        state_out["stderr"] = ""
        for ln in lines:
            yield ln

    sink = InMemoryRunEventSink()
    await SubprocessHarnessRunner(
        "agy",
        binary_path=str(fake_bin),
        env={"GEMINI_API_KEY": "gm-key"},
        stream_fn=multi_pii_stream,
    ).run_turn(
        AgentTurnRequest(
            run_id="r-multi-pii",
            agent_id="ship-vault",
            step="step6",
            prompt="Synthesize",
            output_schema=SemanticCriticVerdict,
        ),
        event_sink=sink,
    )
    tool_ev = next(ev for ev in sink.events if ev.event_type == "TOOL_CALL")
    args_redacted = str(tool_ev.payload["args_redacted"])
    for leaked in ("021000021", "121000358", "DE89", "GB29", "US12", "3782", "822463", "10005"):
        assert leaked not in args_redacted


def test_pii_cc_pan_adjacent_digits_and_repr_hex_control_escapes() -> None:
    """Regression: CC PAN adjacent digit runs, IBAN+PAN+suffix, and repr() \\x08/\\x0c/\\x0b escapes."""
    cc_cases = {
        "5678 3782 822463 10005": "5678 [REDACTED]",
        "order 123 4532 0151 1283 0366": "order 123 [REDACTED]",
        "12-4532-0151-1283-0366": "12-[REDACTED]",
        "9 4532 0151 1283 0366": "9 [REDACTED]",
        "3782 822463 10005 1234": "[REDACTED] 1234",
        "4532-0151-1283-0366 99": "[REDACTED] 99",
        "123 4532 0151 1283 0366 99": "123 [REDACTED] 99",
        "123456 4532 0151 1283 0366": "123456 [REDACTED]",
        "4532 0151 1283 0366 123456": "[REDACTED] 123456",
        "4532 0151 1283 0366 3782 822463 10005": "[REDACTED] [REDACTED]",
        "US12 3456 7890 1234 5678 4532 0151 1283 0366 99": "[REDACTED] [REDACTED] 99",
        "DE89 3704 0044 0532 0130 00 4532 0151 1283 0366 99": "[REDACTED] [REDACTED] 99",
    }
    for raw_text, expected in cc_cases.items():
        assert redact_telemetry_text(raw_text) == expected

    preserved_cases = (
        "4532015112830367",
        "12345678901234567890",
        "order_id=987654321 cluster_id=10001 2026-10-20 202600005",
        r"C:\Users\alice\repo\021000021\file.py",
    )
    for safe_text in preserved_cases:
        assert redact_telemetry_text(safe_text) == safe_text

    # Python repr() hex control-char escapes (\b -> \x08, \f -> \x0c, \v -> \x0b)
    assert redact_telemetry_text(repr("\balice@example.com")) == r"'\x08[REDACTED]'"
    assert redact_telemetry_text(repr("x\falice@example.com")) == r"'x\x0c[REDACTED]'"
    assert redact_telemetry_text(repr("x\valice@example.com")) == r"'x\x0b[REDACTED]'"
    assert redact_telemetry_text(repr("\b555-123-4567")) == r"'\x08[REDACTED]'"
    assert redact_telemetry_text(repr("\b4532-0151-1283-0366")) == r"'\x08[REDACTED]'"

    repr_payload = repr(
        {
            "ssn_note": "x\b123-45-6789",
            "ein_note": "x\f12-3456789",
            "iban_note": "x\vDE89 3704 0044 0532 0130 00",
            "ABACode": "\b021000021",
            "email_note": "\balice@example.com",
            "phone_note": "\b555-123-4567",
            "card_note": "123 4532 0151 1283 0366 99",
        }
    )
    scrubbed_repr = redact_telemetry_text(repr_payload)
    for leaked in (
        "123-45-6789",
        "12-3456789",
        "DE89",
        "021000021",
        "alice@example.com",
        "555-123-4567",
        "4532",
        "0151",
        "1283",
        "0366",
    ):
        assert leaked not in scrubbed_repr
    assert r"\x08[REDACTED]" in scrubbed_repr
    assert r"\x0c[REDACTED]" in scrubbed_repr
    assert r"\x0b[REDACTED]" in scrubbed_repr
    assert "123 [REDACTED] 99" in scrubbed_repr


@pytest.mark.asyncio
async def test_pii_pan_prefix_collisions_json_unicode_escapes_validation_trunc_and_broken_pipe(
    tmp_path: Path,
) -> None:
    """Regression: prefix+PAN collisions, JSON \\u00XX escapes, ValidationError truncation, BrokenPipeError."""
    # 1. Prefix+PAN Luhn collision cases must scrub full PAN without breaking trailing-suffix preservation
    prefix_pan_cases = (
        "1000 4532 0151 1283 0366",
        "12340 4532 0151 1283 0366",
        "123451 4532 0151 1283 0366",
        "12345670 4532 0151 1283 0366",
        "123456781 4532 0151 1283 0366",
        "1234567893 4532 0151 1283 0366",
    )
    for raw_pan in prefix_pan_cases:
        redacted = redact_telemetry_text(raw_pan)
        assert redacted.endswith("[REDACTED]"), f"Prefix collision leak: {raw_pan} -> {redacted}"
        for tok in ("4532", "0151", "1283", "0366"):
            assert tok not in redacted

    for disc_pan in ("42-6011-0009-9013-9424-99", "43-6011-0009-9013-9424-99"):
        redacted = redact_telemetry_text(disc_pan)
        assert redacted.endswith("[REDACTED]-99"), f"Discover prefix leak: {disc_pan} -> {redacted}"
        for tok in ("6011", "0009", "9013", "9424"):
            assert tok not in redacted

    assert redact_telemetry_text("4532 0151 1283 0366 123456") == "[REDACTED] 123456"
    assert redact_telemetry_text("4532 0151 1283 0366 3782 822463 10005") == "[REDACTED] [REDACTED]"
    assert redact_telemetry_text("4532015112830367") == "4532015112830367"
    assert redact_telemetry_text("12345678901234567890") == "12345678901234567890"

    # 2. JSON \u0000-\u001f control escapes across all 7 PII types without \u00XX corruption
    for code in range(0x20):
        ch = chr(code)
        js = json.dumps(
            {
                "ssn": f"x{ch}123-45-6789",
                "ein": f"x{ch}12-3456789",
                "iban": f"x{ch}DE89 3704 0044 0532 0130 00",
                "ABACode": f"{ch}021000021",
                "email": f"{ch}alice@example.com",
                "phone": f"{ch}555-123-4567",
                "card_sp": f"{ch} 4532 0151 1283 0366",
                "card_dash": f"{ch}4532-0151-1283-0366",
            },
            separators=(",", ":"),
        )
        scrubbed = redact_telemetry_text(js)
        for leaked in (
            "123-45-6789",
            "12-3456789",
            "DE89",
            "021000021",
            "alice@example.com",
            "555-123-4567",
            "4532",
            "0151",
            "1283",
            "0366",
            r"\u[REDACTED]",
            r"\[REDACTED]",
        ):
            assert leaked not in scrubbed, f"0x{code:02x} leaked/corrupted {leaked}: {scrubbed}"
        parsed = json.loads(scrubbed)
        assert parsed["email"] == f"{ch}[REDACTED]"
        assert parsed["card_sp"] == f"{ch} [REDACTED]"
        assert parsed["card_dash"] == f"{ch}[REDACTED]"

    # E2E SubprocessHarnessRunner TOOL_CALL args_redacted & extract_json_from_stdout with \u000b/\u0002/\u0010
    fake_bin = tmp_path / "agy.exe"
    fake_bin.write_text("exe", encoding="utf-8")

    async def unicode_ctl_stream(
        cmd: list[str],
        *,
        input_text: str,
        env: dict[str, str],
        cwd: Path,
        timeout_seconds: float,
        state_out: dict[str, Any],
        cli_label: str,
    ) -> AsyncIterator[str]:
        _ = cmd, input_text, env, cwd, timeout_seconds, cli_label
        lines = [
            json.dumps(
                {
                    "event": "step_update",
                    "step_update": {
                        "step_type": "tool",
                        "state": "ACTIVE",
                        "step_index": 1,
                        "tool_name": "read_wire",
                        "tool_info": {
                            "parameters": {
                                "ABACode": "\x0b021000021",
                                "ssn": "\x0b123-45-6789",
                                "email": "\x0balice@example.com",
                                "card": "\x02 4532 0151 1283 0366",
                                "card2": "\x10 4532 0151 1283 0366",
                            }
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "response": json.dumps({"score": 93, "findings": [], "accepted_waiver_ids": []}),
                    "usage": {"input_tokens": 5, "output_tokens": 5},
                }
            ),
        ]
        state_out["returncode"] = 0
        state_out["stdout"] = "\n".join(lines)
        state_out["stderr"] = ""
        for ln in lines:
            yield ln

    sink = InMemoryRunEventSink()
    await SubprocessHarnessRunner(
        "agy",
        binary_path=str(fake_bin),
        env={"GEMINI_API_KEY": "gm-key"},
        stream_fn=unicode_ctl_stream,
    ).run_turn(
        AgentTurnRequest(
            run_id="r-unicode-ctl",
            agent_id="ship-vault",
            step="step6",
            prompt="Synthesize",
            output_schema=SemanticCriticVerdict,
        ),
        event_sink=sink,
    )
    tool_ev = next(ev for ev in sink.events if ev.event_type == "TOOL_CALL")
    args_redacted = str(tool_ev.payload["args_redacted"])
    for leaked in (
        "021000021",
        "123-45-6789",
        "alice@example.com",
        "4532",
        "0151",
        "1283",
        "0366",
        r"\u[REDACTED]",
        r"\[REDACTED]",
    ):
        assert leaked not in args_redacted

    bad_stdout = json.dumps(
        {
            "ABACode": "\x0b021000021",
            "ssn": "\x0b123-45-6789",
            "email": "\x0balice@example.com",
            "card": "\x02 4532 0151 1283 0366",
        },
        separators=(",", ":"),
    ).rstrip("}")
    with pytest.raises(OpenDVError) as exc_extract:
        extract_json_from_stdout(f"NOT_JSON {bad_stdout}", cli_label="agy CLI")
    for leaked in ("021000021", "123-45-6789", "alice@example.com", "4532", "0151", "1283", "0366"):
        assert leaked not in str(exc_extract.value)

    # 3. validate_cli_structured_output must never leak PII via pydantic_core '...' truncation
    trunc_cases: list[tuple[type[Any], Any, str]] = [
        (SemanticCriticVerdict, {"card": "4532 0151 1283 0366", "ABACode": "\x08021000021"}, ""),
        (HorizonCriticVerdict, {"iban": "DE89 3704 0044 0532 0130 00", "phone": "555-123-4567"}, ""),
        (DraftShardIR, {"card": "4532 0151 1283 0366", "pad": "x" * 60}, ""),
        (DraftVaultIR, {"card": "4532 0151 1283 0366", "pad": "x" * 60}, ""),
        (DraftVaultIR, None, '{"card": "4532 0151 1283 0366", "pad": "' + "x" * 60),
    ]
    for schema, raw_s, raw_t in trunc_cases:
        with pytest.raises(OpenDVError) as exc_trunc:
            validate_cli_structured_output(
                schema,
                raw_structured=raw_s,
                raw_response_text=raw_t,
                cli_label="claude CLI",
            )
        msg = str(exc_trunc.value)
        for leaked in ("4532", "0151", "1283", "0366", "021000021", "DE89", "3704", "555-123-4567"):
            assert leaked not in msg, f"ValidationError leaked {leaked} for {schema.__name__}: {msg}"

    # 4. Early-exit child closing without reading > 150 KB stdin raises OpenDVError, not BrokenPipeError
    import os

    early_py = tmp_path / "early_exit_auth.py"
    early_py.write_text(
        "import sys; sys.stderr.write('Error: not logged in\\n'); sys.stderr.flush(); sys.exit(1)\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        shim = tmp_path / "early_claude.cmd"
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{early_py}"\r\n', encoding="utf-8")
    else:
        shim = tmp_path / "early_claude"
        shim.write_text(f'#!/usr/bin/env "{sys.executable}"\nimport sys; sys.stderr.write("Error: not logged in\\n"); sys.exit(1)\n', encoding="utf-8")
        shim.chmod(0o755)

    with pytest.raises(OpenDVError) as exc_pipe:
        await SubprocessHarnessRunner(
            "claude-cli",
            binary_path=str(shim),
            env={"ANTHROPIC_API_KEY": "sk-test"},
        ).run_turn(
            AgentTurnRequest(
                run_id="r-broken-pipe",
                agent_id="ship-vault",
                step="step6",
                prompt="X" * 160000,
                output_schema=SemanticCriticVerdict,
            )
        )
    assert exc_pipe.value.error_code == ErrorCode.AGENT_NOT_CONFIGURED
    assert "not logged in" in str(exc_pipe.value).lower()


@pytest.mark.asyncio
async def test_blocked_stdin_timeout_pan_overlaps_cut_boundary_and_del_escape(
    tmp_path: Path,
) -> None:
    """Regression: blocked-stdin timeout, SSN/Phone/DP PAN overlaps, >64KB cut boundary, \\x7f."""
    # 1. Blocked large stdin must honor asyncio.timeout without waiting for hung child exit
    state_out: dict[str, Any] = {}
    t0 = time.perf_counter()
    with pytest.raises(OpenDVError) as exc_to:
        async for _ in stream_cli_subprocess_lines(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            input_text="X" * 250000,
            env={},
            cwd=tmp_path / "hung_stdin_cwd",
            timeout_seconds=0.3,
            state_out=state_out,
            cli_label="test CLI",
        ):
            pass
    elapsed = time.perf_counter() - t0
    assert exc_to.value.error_code == ErrorCode.AGENT_EXECUTION_FAILED
    assert "timed out" in str(exc_to.value).lower()
    assert elapsed < 1.5, f"Blocked stdin timeout hung for {elapsed:.2f}s"

    # 2a. 3+2 / 3+3 SSN/Phone prefixes must not bisect downstream valid CC PANs
    assert redact_telemetry_text("752 534 4532 0151 1283 0366") == "752 534 [REDACTED]"
    assert redact_telemetry_text("719-85-4532-0151-1283-0366") == "719-85-[REDACTED]"
    assert redact_telemetry_text("506716-726-84-4532-0151-1283-0366") == "506716-726-84-[REDACTED]"
    assert redact_telemetry_text("4989-134-52-4532-0151-1283-0366") == "4989-134-52-[REDACTED]"
    assert redact_telemetry_text("210-709-214-4532-0151-1283-0366") == "210-709-214-[REDACTED]"
    assert redact_telemetry_text("123-45-6789 4532-0151-1283-0366") == "[REDACTED] [REDACTED]"

    # 2b. Unequal-weight DP overlaps must merge plausible downstream PANs without breaking controls
    unequal_dp_cases = (
        "4005 3782 8224 6310 005",
        "4000 4024 0071 0000 7",
        "4004 4000 4001 4532 01511283 0366",
        "4000 4000 45320151 12830366",
        "4001 5000 0000 0000 0009",
        "4005 9123 4567 8901 2348",
        "4000 1234 5674 3782 822463 10005",
        "4000 1234 5674 4111 2222 3333 1",
        "400 00008 45320151 12830366",
    )
    for raw_pan in unequal_dp_cases:
        assert redact_telemetry_text(raw_pan) == "[REDACTED]", f"Failed DP merge: {raw_pan}"

    assert redact_telemetry_text("4532 0151 1283 0366 123456") == "[REDACTED] 123456"
    assert redact_telemetry_text("4532015112830367") == "4532015112830367"
    assert redact_telemetry_text("12345678901234567890") == "12345678901234567890"
    safe_ids = "order_id=987654321 cluster_id=10001 2026-10-20 202600005"
    assert redact_telemetry_text(safe_ids) == safe_ids

    # 3. extract_json_from_stdout > 65536 boundary must not sever space-separated PANs/IBANs
    cut_cases: list[tuple[str, tuple[str, ...]]] = [
        (
            "a" * 3938 + "@example.com " + "x" * 130 + "4532 0151 1283 0366 " + "y" * 63000,
            ("4532", "0151", "1283", "0366"),
        ),
        (
            "a" * 4084 + "@b.com 4532 0151 1283 0366 " + "z" * 65000,
            ("4532", "0151", "1283", "0366"),
        ),
        (
            "a" * 3938
            + "@example.com "
            + "x" * 130
            + " DE89 3704 0044 0532 0130 00 "
            + "y" * 63000,
            ("DE89", "3704", "0532", "0130"),
        ),
        (
            "a" * 65520 + "@b.com 4532 0151 1283 0366 " + "z" * 10000,
            ("4532", "0151", "1283", "0366"),
        ),
        (
            "a" * 65520 + "@b.com DE89 3704 0044 0532 0130 00 " + "z" * 10000,
            ("DE89", "3704", "0532", "0130"),
        ),
    ]
    for payload, banned_tokens in cut_cases:
        with pytest.raises(OpenDVError) as exc_cut:
            extract_json_from_stdout(payload, cli_label="agy CLI")
        msg = str(exc_cut.value)
        for tok in banned_tokens:
            assert tok not in msg, f"Cut boundary leaked {tok}: {msg}"

    # 4. Python repr() \x7f (DEL) escape scrubbing + literal \\n preservation
    assert "123-45-6789" not in redact_telemetry_text(repr("x\x7f123-45-6789"))
    assert "021000021" not in redact_telemetry_text(repr("ABACode \x7f021000021"))
    assert "alice@example.com" not in redact_telemetry_text(repr("\x7falice@example.com"))
    assert "4532" not in redact_telemetry_text(repr("x\x7f4532-0151-1283-0366"))
    assert "555-123-4567" not in redact_telemetry_text(repr("x\x7f555-123-4567"))
    assert redact_telemetry_text(json.dumps({"p": "\nalice@example.com"})) == '{"p": "\\n[REDACTED]"}'
    assert (
        redact_telemetry_text(json.dumps({"p": "\\nalice@example.com"}))
        == '{"p": "\\\\n[REDACTED]"}'
    )


@pytest.mark.asyncio
async def test_adjacent_ssn_phone_pan_diners_3095_and_tool_result_pre_truncation_redaction(
    tmp_path: Path,
) -> None:
    """Regression: adjacent SSN/Phone+PAN, Diners 3095 (4,6,4), and TOOL_RESULT [:300] redaction."""
    import random
    from opendv.agents._cli_ndjson import parse_ndjson_step_events

    # 1. Exact adjacent SSN+PAN, Phone+PAN, PAN+Phone, Phone+Phone, and 3+2/3+3 controls
    assert redact_telemetry_text("123-45-6785 4532-0151-1283-0366") == "[REDACTED] [REDACTED]"
    assert redact_telemetry_text("123-45-6785-4532-0151-1283-0366") == "[REDACTED]-[REDACTED]"
    assert redact_telemetry_text("311-306-2274 4891-7756-6205-6341") == "[REDACTED] [REDACTED]"
    assert redact_telemetry_text("4532-0151-1283-4111 555-123-4562") == "[REDACTED] [REDACTED]"
    assert redact_telemetry_text("555-123-6789 555-123-4568") == "[REDACTED] [REDACTED]"
    assert redact_telemetry_text("719-85-4532-0151-1283-0366") == "719-85-[REDACTED]"
    assert redact_telemetry_text("752 534 4532 0151 1283 0366") == "752 534 [REDACTED]"

    # 2. Diners Club International 3095 (4,6,4) 14-digit under prefix collision
    out_3095 = redact_telemetry_text("4000 40 3095 102776 9742")
    assert out_3095 in ("[REDACTED]", "4000 40 [REDACTED]")
    assert "9742" not in out_3095

    # 3. Randomized SSN+PAN, Phone+PAN, 3+2 prefix+PAN, and Diners 3095 collision fuzzing (0 leaks)
    def _luhn_cd(prefix: str) -> str:
        total = 0
        for idx, ch in enumerate(reversed(prefix)):
            val = int(ch)
            total += (val * 2 - 9 if val * 2 > 9 else val * 2) if idx % 2 == 0 else val
        return str((10 - (total % 10)) % 10)

    def _make_pan(pfx: str, length: int, rng: random.Random) -> str:
        body = pfx + "".join(str(rng.randrange(10)) for _ in range(length - len(pfx) - 1))
        return body + _luhn_cd(body)

    rng = random.Random(20261124)
    for _ in range(300):
        ssn = f"{rng.randint(100, 999)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}"
        phone = f"{rng.randint(200, 999)}-{rng.randint(200, 999)}-{rng.randint(1000, 9999)}"
        pfx, plen = rng.choice([("4", 16), ("52", 16), ("37", 15), ("3095", 14), ("6011", 16), ("3528", 16)])
        pan = _make_pan(pfx, plen, rng)
        groups = (
            [pan[:4], pan[4:10], pan[10:]]
            if plen in (14, 15)
            else [pan[:4], pan[4:8], pan[8:12], pan[12:]]
        )
        for sep in ("-", " "):
            fpan = sep.join(groups)
            assert redact_telemetry_text(f"{ssn} {fpan}") == "[REDACTED] [REDACTED]"
            assert redact_telemetry_text(f"{phone} {fpan}") == "[REDACTED] [REDACTED]"
        p32 = f"{rng.randint(100, 999)}-{rng.randint(10, 99)}-{'-'.join(groups)}"
        out_p32 = redact_telemetry_text(p32)
        for grp in groups:
            assert grp not in out_p32
        d3095 = _make_pan("3095", 14, rng)
        d3095_fmt = f"{d3095[:4]} {d3095[4:10]} {d3095[10:]}"
        out_col = redact_telemetry_text(f"4000 40 {d3095_fmt}")
        for grp in d3095_fmt.split(" "):
            assert grp not in out_col

    # 4. parse_ndjson_step_events & SubprocessHarnessRunner TOOL_RESULT [:300] cut-boundary redaction
    straddle_outputs = [
        (("x" * 290) + " 4532 0151 1283 0366", ("4532", "0151", "1283", "0366")),
        (("a" * 292) + "4532-0151-1283-0366", ("4532", "0151", "1283", "0366")),
        (("x" * 285) + " alice@example.com", ("alice@example", "alice@")),
        (("x" * 292) + " 123-45-6789", ("123-45", "6789")),
        (("x" * 288) + " DE89 3704 0044 0532 0130 00", ("DE89", "3704")),
        (("a" * 65530) + " 4532 0151 1283 0366", ("4532", "0151")),
    ]
    for out_str, banned in straddle_outputs:
        ndjson_line = json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "tool",
                    "state": "DONE",
                    "step_index": 1,
                    "tool_name": "inspect_table_profile",
                    "tool_info": {"output": out_str},
                },
            }
        )
        evs = parse_ndjson_step_events(ndjson_line)
        assert len(evs) == 1
        summary = str(evs[0]["summary"])
        assert len(summary) <= 300
        for tok in banned:
            assert tok not in summary

    fake_bin = tmp_path / "agy.exe"
    fake_bin.write_text("exe", encoding="utf-8")

    async def tool_straddle_stream(
        cmd: list[str],
        *,
        input_text: str,
        env: dict[str, str],
        cwd: Path,
        timeout_seconds: float,
        state_out: dict[str, Any],
        cli_label: str,
    ) -> AsyncIterator[str]:
        _ = cmd, input_text, env, cwd, timeout_seconds, cli_label
        lines = [
            json.dumps(
                {
                    "event": "step_update",
                    "step_update": {
                        "step_type": "tool",
                        "state": "DONE",
                        "step_index": 1,
                        "tool_name": "inspect_table_profile",
                        "tool_info": {"output": ("x" * 290) + " 4532 0151 1283 0366"},
                    },
                }
            ),
            json.dumps(
                {
                    "event": "step_update",
                    "step_update": {
                        "step_type": "tool",
                        "state": "DONE",
                        "step_index": 2,
                        "tool_name": "inspect_table_profile",
                        "tool_info": {"output": ("x" * 285) + " alice@example.com"},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "response": json.dumps({"score": 93, "findings": [], "accepted_waiver_ids": []}),
                    "usage": {"input_tokens": 5, "output_tokens": 5},
                }
            ),
        ]
        state_out["returncode"] = 0
        state_out["stdout"] = "\n".join(lines)
        state_out["stderr"] = ""
        for ln in lines:
            yield ln

    sink = InMemoryRunEventSink()
    await SubprocessHarnessRunner(
        "agy",
        binary_path=str(fake_bin),
        env={"GEMINI_API_KEY": "gm-key"},
        stream_fn=tool_straddle_stream,
    ).run_turn(
        AgentTurnRequest(
            run_id="r-tool-straddle",
            agent_id="ship-vault",
            step="step6",
            prompt="Synthesize",
            output_schema=SemanticCriticVerdict,
        ),
        event_sink=sink,
    )
    tool_summaries = [str(ev.payload["summary"]) for ev in sink.events if ev.event_type == "TOOL_RESULT"]
    assert len(tool_summaries) == 2
    for banned in ("4532", "0151", "alice@example"):
        assert all(banned not in s for s in tool_summaries)


def test_pan_ending_in_one_with_phone_and_linear_scaling() -> None:
    """Regression: PAN ending in '1' + US phone leaks & O(N) scaling on 50-70KB dense collisions."""
    from opendv.agents._cli_ndjson import _redact_preview

    # 1. (4,4,4,1) and (4,4,4,4,1) PAN ending in '1' followed by space/dash + US phone
    assert redact_telemetry_text("4111-9423-7594-1 835 774 1110") == "[REDACTED] [REDACTED]"
    assert redact_telemetry_text("4111-9423-7594-1-835-774-1110") == "[REDACTED]-[REDACTED]"
    assert redact_telemetry_text("4111-9423-7594-1 (835) 774-1110") == "[REDACTED] [REDACTED]"
    assert redact_telemetry_text("4111-9423-7594-1-(835) 774-1110") == "[REDACTED]-[REDACTED]"
    assert redact_telemetry_text("5000-0957-1709-0142-1-835-774-1110") == "[REDACTED]-[REDACTED]"
    assert redact_telemetry_text("5000-0957-1709-0142-1 835.774.1110") == "[REDACTED] [REDACTED]"
    assert (
        redact_telemetry_text("4735-4980-9437-1 805 860 1824 295-633-7507")
        == "[REDACTED] [REDACTED] [REDACTED]"
    )
    assert (
        redact_telemetry_text("4279 0718 6183 1 820-991-6595 419-774-1187")
        == "[REDACTED] [REDACTED] [REDACTED]"
    )

    # 2. 3+2 / 3+3 prefix+PAN and trailing numeric ID controls remain intact
    assert redact_telemetry_text("719-85-4532-0151-1283-0366") == "719-85-[REDACTED]"
    assert redact_telemetry_text("752 534 4532 0151 1283 0366") == "752 534 [REDACTED]"
    assert redact_telemetry_text("1-752-534-4532-0151-1283-0366") == "1-752-534-[REDACTED]"
    assert redact_telemetry_text("4532 0151 1283 0366 123456") == "[REDACTED] 123456"
    assert (
        redact_telemetry_text("4532-0151-1283-6785 555-123-4562 4532-0151-1283-0366")
        == "[REDACTED] [REDACTED] [REDACTED]"
    )

    # 3. Linear O(N) runtime (< 0.5s) on 50-70KB dense colliding SSN+PAN and Phone+PAN payloads
    for payload in (
        ("123-45-6785 4532-0151-1283-0366, " * 1515).strip(", "),
        ("123-45-6785 4532-0151-1283-0366 " * 2000).strip(),
        ("311-306-2274 4891-7756-6205-6341, " * 1515).strip(", "),
        ("311-306-2274 4891-7756-6205-6341 " * 2000).strip(),
    ):
        t0 = time.perf_counter()
        scrubbed = redact_telemetry_text(payload)
        dt = time.perf_counter() - t0
        assert dt < 0.5, f"Dense colliding redaction took {dt:.3f}s for {len(payload)} chars"
        assert not any(ch.isdigit() for ch in scrubbed)

    s70 = ("123-45-6785 4532-0151-1283-0366 " * 2200).strip()
    t0 = time.perf_counter()
    prev = _redact_preview(s70)
    dt_prev = time.perf_counter() - t0
    assert dt_prev < 0.5, f"_redact_preview took {dt_prev:.3f}s on 70KB"
    assert not any(ch.isdigit() for ch in prev)


def test_adjacent_pii_pairs_symmetric_left_context_and_scaling() -> None:
    """Regression: symmetric left-context _ends_with_independent_pii across 9 pairs, PAN+Phone+SSN, and 60KB scaling."""
    import random

    # 1. Exact colliding Phone+SSN, (paren) Phone+SSN, EIN+SSN, Phone+EIN, IBAN+SSN, and PAN+Phone+SSN
    exact_pairs = [
        ("973 391 5999 277-33-4922", "[REDACTED] [REDACTED]"),
        ("541-398-6275-564-79-1291", "[REDACTED]-[REDACTED]"),
        ("(835) 774-1110 123-45-6789", "[REDACTED] [REDACTED]"),
        ("23-5172361 589-82-5393", "[REDACTED] [REDACTED]"),
        ("67-2256449-210-18-3972", "[REDACTED]-[REDACTED]"),
        ("835 774 1110 12-3456789", "[REDACTED] [REDACTED]"),
        ("811 871 9641-54-6496444", "[REDACTED]-[REDACTED]"),
        ("4532-0151-1283-0366 835 774 1110 123-45-6789", "[REDACTED] [REDACTED] [REDACTED]"),
        ("4111-9423-7594-1 835 774 1110 123-45-6789", "[REDACTED] [REDACTED] [REDACTED]"),
        ("DE89 3704 0044 0532 0130 00 123-45-6789", "[REDACTED] [REDACTED]"),
        ("411-45-1008 81-128-45-6789-0123", "[REDACTED] [REDACTED]"),
    ]
    for raw, expected in exact_pairs:
        assert redact_telemetry_text(raw) == expected, f"Mismatch for {raw!r}"

    # 2. All 9 {SSN, EIN, Phone} x {SSN, EIN, Phone} pairs across ' ' and '-' + (paren) Phone+SSN + PAN+Phone+SSN
    rng = random.Random(20261124)

    def _ssn() -> str:
        return f"{rng.randint(100, 999)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}"

    def _ein() -> str:
        return f"{rng.randint(10, 99)}-{rng.randint(1000000, 9999999)}"

    def _phone() -> str:
        s = rng.choice([" ", "-"])
        return f"{rng.randint(200, 999)}{s}{rng.randint(200, 999)}{s}{rng.randint(1000, 9999)}"

    makers = {"SSN": _ssn, "EIN": _ein, "PHONE": _phone}
    for left_kind, left_fn in makers.items():
        for right_kind, right_fn in makers.items():
            for sep in (" ", "-"):
                for _ in range(150):
                    sample = f"{left_fn()}{sep}{right_fn()}"
                    assert redact_telemetry_text(sample) == f"[REDACTED]{sep}[REDACTED]", (
                        f"Leaked {left_kind}+{right_kind} ({sep!r}): {sample!r}"
                    )

    for _ in range(250):
        paren_sample = f"({rng.randint(200, 999)}) {rng.randint(200, 999)}-{rng.randint(1000, 9999)} {_ssn()}"
        assert redact_telemetry_text(paren_sample) == "[REDACTED] [REDACTED]"
        pan16_sample = f"4532-0151-1283-0366 {_phone()} {_ssn()}"
        assert redact_telemetry_text(pan16_sample) == "[REDACTED] [REDACTED] [REDACTED]"
        pan1_sample = f"4111-9423-7594-1 {_phone()} {_ssn()}"
        assert redact_telemetry_text(pan1_sample) == "[REDACTED] [REDACTED] [REDACTED]"

    # 3. 3+2 / 3+3 / trailing-ID / Diners-3095 controls remain intact
    assert redact_telemetry_text("719-85-4532-0151-1283-0366") == "719-85-[REDACTED]"
    assert redact_telemetry_text("752 534 4532 0151 1283 0366") == "752 534 [REDACTED]"
    assert redact_telemetry_text("1-752-534-4532-0151-1283-0366") == "1-752-534-[REDACTED]"
    assert redact_telemetry_text("4532 0151 1283 0366 123456") == "[REDACTED] 123456"
    assert redact_telemetry_text("4111-9423-7594-1 123456") == "[REDACTED] 123456"
    assert redact_telemetry_text("4000 40 3095 102776 9742") == "4000 40 [REDACTED]"

    # 4. 50-70KB dense colliding scaling (< 0.5s, linear O(N), 0 leaked digits)
    for unit in (
        "973 391 5999 277-33-4922 ",
        "23-5172361 589-82-5393 ",
        "835 774 1110 12-3456789 ",
        "(835) 774-1110 123-45-6789 ",
        "4111-9423-7594-1 835 774 1110 123-45-6789 ",
    ):
        payload = (unit * (60000 // len(unit))).strip()
        t0 = time.perf_counter()
        scrubbed = redact_telemetry_text(payload)
        dt = time.perf_counter() - t0
        assert dt < 0.5, f"Dense colliding redaction took {dt:.3f}s for {len(payload)} chars"
        assert not any(ch.isdigit() for ch in scrubbed)


def test_cli_json_fence_and_prose_stripping_fail_closed() -> None:
    """strip_markdown_json_fences extracts fenced/raw JSON from prose while malformed JSON fails closed."""
    payload = {
        "question_id": "q-bk-1",
        "concept_name": "Account",
        "title": "Confirm BK",
        "prompt": "Choose account_id or acct_num",
        "sample_rows": ["account_id=A1"],
        "impact_if_locked": "Locks hub_account BK",
    }
    prose_fenced = f"Sure, here is the JSON:\n```json\n{json.dumps(payload, indent=2)}\n```\nDone."
    stripped_fenced = strip_markdown_json_fences(prose_fenced)
    parsed_fenced = validate_cli_structured_output(
        ClarificationQuestion,
        raw_structured=None,
        raw_response_text=stripped_fenced,
        cli_label="agy CLI",
    )
    assert isinstance(parsed_fenced, ClarificationQuestion)
    assert parsed_fenced.question_id == "q-bk-1"

    prose_raw = f"Preamble text.\n{json.dumps(payload)}\nTrailing note."
    stripped_raw = strip_markdown_json_fences(prose_raw)
    parsed_raw = validate_cli_structured_output(
        ClarificationQuestion,
        raw_structured=None,
        raw_response_text=stripped_raw,
        cli_label="agy CLI",
    )
    assert isinstance(parsed_raw, ClarificationQuestion)
    assert parsed_raw.concept_name == "Account"

    with pytest.raises(OpenDVError) as exc_bad:
        validate_cli_structured_output(
            ClarificationQuestion,
            raw_structured=None,
            raw_response_text=strip_markdown_json_fences('Here you go: {"question_id": broken_json}'),
            cli_label="agy CLI",
        )
    assert exc_bad.value.error_code == ErrorCode.AGENT_EXECUTION_FAILED

