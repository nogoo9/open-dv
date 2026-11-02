"""Comprehensive unit tests for opendv-agent-harness Part 1 (WP-06).

Verifies:
1. Hexagonal protocol (`VaultAgentHarness`, `BaseVaultAgentHarness`) & domain models (`models.py`)
2. Model-tier routing (`argon` / `flash`) & `PERSONA_CAPABILITY_MATRIX` (`routing.py`)
3. All 8 canonical domain tools, Pydantic v2 validation, CAS writes, and RBAC persona gating
4. Dual-SDK Tool Bridge (`to_antigravity_tool_callable` & `build_claude_sdk_mcp_bundle`)
5. Real-time telemetry translation (`HarnessTelemetryTranslator`, `VaultLedgerEmitter`)
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pydantic
import pytest
from pydantic import BaseModel

from opendv_agent_harness import (
    CANONICAL_TOOL_NAMES,
    DEFAULT_AGY_MODEL_MAP,
    DEFAULT_CLAUDE_MODEL_MAP,
    PERSONA_CAPABILITY_MATRIX,
    AgentPersona,
    AgentTaskSpec,
    BaseVaultAgentHarness,
    DataClassification,
    HarnessBudget,
    HarnessConfig,
    HarnessEventKind,
    HarnessStopReason,
    HarnessStreamEvent,
    HarnessTelemetryTranslator,
    JsonlObservabilityLedger,
    ModelTier,
    ObservabilityVerdict,
    PipelineStageId,
    ToolExecutionContext,
    VaultAgentHarness,
    VizId,
    WorkbenchViewId,
    build_claude_sdk_mcp_bundle,
    build_default_domain_tool_map,
    build_default_domain_tools,
    build_google_docstring,
    get_persona_capability,
    get_tools_for_persona,
    is_tool_authorized_for_persona,
    resolve_default_tier_for_persona,
    resolve_provider_model,
    to_antigravity_tool_callable,
    translate_stream_event_to_observability,
)
from opendv_core.schema_registry import validate_observability_event

SAMPLE_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
SAMPLE_SPAN_ID = "00f067aa0ba902b7"


def _make_harness_config(tmp_path: Path, **overrides: Any) -> HarnessConfig:
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


def _make_tool_context(
    tmp_path: Path,
    persona: AgentPersona = AgentPersona.VAULT_FIRST_MATE,
    stage_id: PipelineStageId = PipelineStageId.STAGE_00_ORCHESTRATION,
    view_id: WorkbenchViewId = WorkbenchViewId.WB_00,
    viz_id: VizId = VizId.VIZ_PIPE_00,
) -> ToolExecutionContext:
    cfg = _make_harness_config(tmp_path)
    return ToolExecutionContext(
        run_id="run-test-wp06",
        trace_id=SAMPLE_TRACE_ID,
        span_id=SAMPLE_SPAN_ID,
        parent_span_id=None,
        persona=persona,
        stage_id=stage_id,
        workbench_view_id=view_id,
        viz_id=viz_id,
        config=cfg,
    )


# ---------------------------------------------------------------------------
# 1. Domain Models, Absolute Path Validation & Routing Matrix Tests
# ---------------------------------------------------------------------------


def test_harness_config_requires_absolute_paths(tmp_path: Path) -> None:
    cfg = _make_harness_config(tmp_path)
    assert cfg.workspace_dir.is_absolute()

    with pytest.raises(pydantic.ValidationError, match="must be an expanded absolute path"):
        HarnessConfig(
            workspace_dir=Path("relative/workspace"),
            state_dir=cfg.state_dir,
            app_data_dir=cfg.app_data_dir,
            save_dir=cfg.save_dir,
        )


def test_model_tier_routing_and_persona_capability_matrix() -> None:
    assert ModelTier.HIGH_REASONING.value == "argon"
    assert ModelTier.FAST_TIER.value == "flash"

    assert DEFAULT_AGY_MODEL_MAP[ModelTier.HIGH_REASONING] == "gemini-4-argon-high"
    assert DEFAULT_AGY_MODEL_MAP[ModelTier.FAST_TIER] == "gemini-3.8-flash-high"
    assert DEFAULT_CLAUDE_MODEL_MAP[ModelTier.HIGH_REASONING] == "claude-opus-5-5-high"
    assert DEFAULT_CLAUDE_MODEL_MAP[ModelTier.FAST_TIER] == "claude-sonnet-5-5-medium"

    assert resolve_provider_model("antigravity", ModelTier.HIGH_REASONING) == "gemini-4-argon-high"
    assert resolve_provider_model("claude", ModelTier.FAST_TIER) == "claude-sonnet-5-5-medium"
    assert (
        resolve_provider_model(
            "antigravity",
            ModelTier.FAST_TIER,
            overrides={ModelTier.FAST_TIER: "custom-flash-v2"},
        )
        == "custom-flash-v2"
    )

    assert (
        resolve_default_tier_for_persona(AgentPersona.VAULT_FIRST_MATE) == ModelTier.HIGH_REASONING
    )
    assert resolve_default_tier_for_persona(AgentPersona.SCOUT_SOURCE) == ModelTier.FAST_TIER
    assert resolve_default_tier_for_persona(AgentPersona.SCOUT_UNSTRUCTURED) == ModelTier.FAST_TIER
    assert resolve_default_tier_for_persona(AgentPersona.SHIP_VAULT) == ModelTier.HIGH_REASONING

    vfm = get_persona_capability(AgentPersona.VAULT_FIRST_MATE)
    assert vfm.sandbox_policy.read_only_workspace is True
    assert vfm.allowed_subagents == ("scout-source", "scout-unstructured", "ship-vault")
    assert vfm["model_tier"] == ModelTier.HIGH_REASONING

    ship = get_persona_capability(AgentPersona.SHIP_VAULT)
    assert ship.sandbox_policy.read_only_workspace is False
    assert ship.allowed_subagents == ()
    assert is_tool_authorized_for_persona(AgentPersona.SHIP_VAULT, "apply_ast_codemod") is True
    assert is_tool_authorized_for_persona(AgentPersona.SCOUT_SOURCE, "apply_ast_codemod") is False


# ---------------------------------------------------------------------------
# 2. Canonical 8 Domain Tools & RBAC Persona Gating Tests
# ---------------------------------------------------------------------------


def test_all_8_domain_tools_registered_with_valid_identifiers_and_schemas() -> None:
    tools = build_default_domain_tools()
    assert len(tools) == 8
    names = tuple(t.name for t in tools)
    assert names == CANONICAL_TOOL_NAMES

    for tool in tools:
        assert tool.name.isidentifier()
        schema = tool.json_schema()
        assert isinstance(schema, dict)
        assert schema.get("type") == "object"
        assert "properties" in schema
        assert tool.default_viz_id is not None

    # Verify exact parity between PERSONA_CAPABILITY_MATRIX and tool allowed_personas
    for persona, profile in PERSONA_CAPABILITY_MATRIX.items():
        persona_tool_names = frozenset(t.name for t in get_tools_for_persona(persona))
        assert persona_tool_names == profile.authorized_tools


@pytest.mark.asyncio
async def test_rbac_persona_gating_across_all_8_tools(tmp_path: Path) -> None:
    tool_map = build_default_domain_tool_map()

    # Unauthorized: scout-source attempting apply_ast_codemod (ship-vault only)
    scout_src_ctx = _make_tool_context(tmp_path, persona=AgentPersona.SCOUT_SOURCE)
    with pytest.raises(PermissionError, match="not authorized to invoke tool 'apply_ast_codemod'"):
        await tool_map["apply_ast_codemod"].invoke(
            {"codemod_id": "ALG-A1", "target_entity": "sat_lp_commitment_fx"},
            scout_src_ctx,
        )

    # Unauthorized: scout-source attempting extract_pdf_spans (scout-unstructured only)
    with pytest.raises(PermissionError, match="not authorized to invoke tool 'extract_pdf_spans'"):
        await tool_map["extract_pdf_spans"].invoke(
            {"document_uri": "calpers_side_letter.pdf"},
            scout_src_ctx,
        )

    # Unauthorized: scout-source attempting request_steward_decision
    with pytest.raises(
        PermissionError, match="not authorized to invoke tool 'request_steward_decision'"
    ):
        await tool_map["request_steward_decision"].invoke(
            {"headline": "Ambiguous entity needs steward review."},
            scout_src_ctx,
        )

    # Authorized executions across all 8 tools:
    # 1. profile_source_feed
    prof_out = await tool_map["profile_source_feed"].invoke(
        {
            "source_system": "INVESTRAN_PE_ERP",
            "table_or_path": "raw_lp_commitments",
            "sample_rows": [
                {"lp_id": "LP-101", "commitment_usd": 500_000_000},
                {"lp_id": "LP-102", "commitment_usd": 250_000_000},
            ],
            "baseline_columns": ["lp_id"],
        },
        scout_src_ctx,
    )
    assert prof_out.row_count == 2
    assert prof_out.added_columns == ["commitment_usd"]
    assert prof_out.drift_status == ObservabilityVerdict.AUTO_FIX
    assert len(prof_out.graph_b_sha256) == 64

    # 2. extract_pdf_spans
    scout_uns_ctx = _make_tool_context(tmp_path, persona=AgentPersona.SCOUT_UNSTRUCTURED)
    pdf_text = "CalPERS Side Letter grants MFN fee discount of 125.0000 bps on Fund IV."
    pdf_out = await tool_map["extract_pdf_spans"].invoke(
        {
            "document_uri": "calpers_side_letter_fund_iv_2026.pdf",
            "document_text": pdf_text,
            "candidate_spans": [
                {
                    "field_name": "mfn_fee_discount_bps",
                    "extracted_value": "125.0000",
                    "verbatim_quote": "125.0000",
                    "char_start": 47,
                    "char_end": 55,
                }
            ],
            "er_margin": 0.85,
        },
        scout_uns_ctx,
    )
    assert pdf_out.all_spans_verified is True
    assert pdf_out.status == ObservabilityVerdict.PASS

    # 3. compile_canonical_ir (writes CAS artifacts)
    ship_ctx = _make_tool_context(tmp_path, persona=AgentPersona.SHIP_VAULT)
    ir_out = await tool_map["compile_canonical_ir"].invoke(
        {
            "contract_id": "dv_pe_model_contract_v5",
            "target_dialect": "duckdb",
            "contract_payload": {"hubs": [{"name": "hub_fund"}], "links": [], "satellites": []},
        },
        ship_ctx,
    )
    assert ir_out.hub_count == 1
    assert "canonical_ir_sha256" in ship_ctx.artifacts_written
    assert "compiled_sql_sha256" in ship_ctx.artifacts_written
    assert (ship_ctx.config.state_dir / "artifacts" / ir_out.canonical_ir_sha256).is_file()

    # 4. run_axi_dv_gates
    gates_out = await tool_map["run_axi_dv_gates"].invoke(
        {"contract_id": "dv_pe_model_contract_v5", "gates_to_run": ["G1", "G2", "G3"]},
        ship_ctx,
    )
    assert gates_out.overall_verdict == ObservabilityVerdict.PASS

    # 5. apply_ast_codemod
    codemod_out = await tool_map["apply_ast_codemod"].invoke(
        {
            "codemod_id": "ALG-A1",
            "target_entity": "sat_lp_commitment_fx",
            "contract_yaml": "entity: sat_lp_commitment_fx\n",
        },
        ship_ctx,
    )
    assert codemod_out.applied is True
    assert codemod_out.verdict == ObservabilityVerdict.AUTO_REPAIRED
    post_blob = ship_ctx.config.state_dir / "artifacts" / codemod_out.contract_yaml_post_sha256
    assert post_blob.is_file()

    # 6. execute_duckdb_sandbox
    sbx_out = await tool_map["execute_duckdb_sandbox"].invoke(
        {
            "sql_bundle": "CREATE TABLE test_tbl (id INT); INSERT INTO test_tbl VALUES (1);",
            "batches_to_run": [1, 2, 3, 4],
        },
        ship_ctx,
    )
    assert sbx_out.all_batches_passed is True
    assert sbx_out.replay_delta_rows == 0
    assert len(sbx_out.dynamic_rules_evaluated) == 15

    # 7. emit_observability_event
    emit_out = await tool_map["emit_observability_event"].invoke(
        {
            "headline": "Verified 6-Gate static checks and 4-Zone SQL compilation.",
            "business_impact": "Guarantees zero hub-to-hub shortcuts and deterministic keys.",
            "status": "PASS",
            "rule_ids_evaluated": ["DV-STR-001", "ALG-A5"],
            "summary_metrics": {"gates_passed": 5},
        },
        ship_ctx,
    )
    assert emit_out.recorded is True
    assert len(ship_ctx.emitted_events) == 1
    validate_observability_event(ship_ctx.emitted_events[0])

    # 8. request_steward_decision
    hold_out = await tool_map["request_steward_decision"].invoke(
        {
            "escalation_reason": "TIER_U3_AMBIGUOUS_ENTITY",
            "headline": "Ambiguous entity mention 'Apex IV' requires Captain decision.",
            "candidate_options": [{"hub_key": "APEX_BO_IV", "score": 0.61}],
            "rule_id": "ALG-A9B",
        },
        ship_ctx,
    )
    assert hold_out.status == ObservabilityVerdict.ASK_USER
    assert ship_ctx.emitted_events[-1]["status"] == "ASK-USER"
    validate_observability_event(ship_ctx.emitted_events[-1])


# ---------------------------------------------------------------------------
# 3. Dual-SDK Tool Bridge Tests (`to_antigravity_tool_callable` & `build_claude_sdk_mcp_bundle`)
# ---------------------------------------------------------------------------


class _MockAgyToolContext:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self.store: dict[str, Any] = {"opendv_state": dict(initial or {})}

    def get_state(self, key: str, default: Any = None) -> Any:
        return self.store.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        self.store[key] = value


@pytest.mark.asyncio
async def test_to_antigravity_tool_callable_signature_docstring_and_state_sync(
    tmp_path: Path,
) -> None:
    ctx = _make_tool_context(tmp_path, persona=AgentPersona.SCOUT_SOURCE)
    tools = build_default_domain_tools()

    for tool_def in tools:
        fn = to_antigravity_tool_callable(tool_def, ctx)
        assert fn.__name__ == tool_def.name
        assert fn.__annotations__["return"] is str
        assert fn.__doc__ is not None
        assert "Args:" in fn.__doc__

        sig = inspect.signature(fn)
        assert sig.return_annotation is str
        expected_fields = list(tool_def.input_model.model_fields.keys())
        assert list(sig.parameters.keys()) == expected_fields
        for param_name, param in sig.parameters.items():
            assert param.kind is inspect.Parameter.KEYWORD_ONLY
            field_info = tool_def.input_model.model_fields[param_name]
            if field_info.is_required():
                assert param.default is inspect.Parameter.empty
            assert param_name in fn.__doc__

    # Verify ToolContext state synchronization on `profile_source_feed`
    profile_tool = build_default_domain_tool_map()["profile_source_feed"]
    agy_callable = to_antigravity_tool_callable(profile_tool, ctx)
    mock_agy_ctx = _MockAgyToolContext(initial={"upstream_marker": "stage_00_ok"})

    raw_json = await agy_callable(
        source_system="DEALCLOUD_PE_CRM",
        table_or_path="raw_portco_valuations",
        sample_rows=[{"portco_lei": "529900T8BM49AURSDO55", "ev_usd": 245_000_000}],
        ctx=mock_agy_ctx,
    )
    parsed = json.loads(raw_json)
    assert parsed["source_system"] == "DEALCLOUD_PE_CRM"
    assert ctx.get_state("upstream_marker") == "stage_00_ok"
    assert "last_graph_b_sha256" in mock_agy_ctx.store["opendv_state"]


@pytest.mark.asyncio
async def test_build_claude_sdk_mcp_bundle_and_handler_invocation(tmp_path: Path) -> None:
    tools = build_default_domain_tools()
    ctx = _make_tool_context(tmp_path, persona=AgentPersona.VAULT_FIRST_MATE)
    mcp_servers, qualified_names, raw_specs = build_claude_sdk_mcp_bundle(tools, ctx)

    assert "opendv" in mcp_servers
    assert len(qualified_names) == 8
    assert qualified_names == [f"mcp__opendv__{name}" for name in CANONICAL_TOOL_NAMES]
    assert set(raw_specs.keys()) == set(qualified_names)

    # 1. Valid input on authorized tool (`mcp__opendv__profile_source_feed`)
    prof_handler = raw_specs["mcp__opendv__profile_source_feed"]["handler"]
    ok_res = await prof_handler({"source_system": "INVESTRAN_PE_ERP", "table_or_path": "raw_funds"})
    assert ok_res["is_error"] is False
    assert ok_res["content"][0]["type"] == "text"
    payload = json.loads(ok_res["content"][0]["text"])
    assert payload["source_system"] == "INVESTRAN_PE_ERP"

    # 2. Unauthorized persona error (`vault-first-mate` invoking `apply_ast_codemod`)
    codemod_handler = raw_specs["mcp__opendv__apply_ast_codemod"]["handler"]
    unauth_res = await codemod_handler(
        {"codemod_id": "ALG-A1", "target_entity": "sat_fund_investran"}
    )
    assert unauth_res["is_error"] is True
    err_data = json.loads(unauth_res["content"][0]["text"])
    assert err_data["error"] == "PermissionError"

    # 3. Validation error (missing required field `source_system`)
    val_err_res = await prof_handler({})
    assert val_err_res["is_error"] is True
    val_err_data = json.loads(val_err_res["content"][0]["text"])
    assert "ValidationError" in val_err_data["error"]


def test_build_google_docstring_formatting() -> None:
    tool_def = build_default_domain_tool_map()["profile_source_feed"]
    doc = build_google_docstring(tool_def.description, tool_def.input_model.model_fields)
    assert "Args:" in doc
    assert "source_system:" in doc
    assert "table_or_path:" in doc


# ---------------------------------------------------------------------------
# 4. BaseVaultAgentHarness.run_task & Telemetry Translator Tests
# ---------------------------------------------------------------------------


class _StructuredSynthesisOutput(BaseModel):
    contract_id: str
    hub_count: int
    verdict: str


class _MockVaultHarness(BaseVaultAgentHarness):
    def __init__(
        self,
        config: HarnessConfig,
        *,
        trigger_hold: bool = False,
        delay_seconds: float = 0.0,
    ) -> None:
        super().__init__(config)
        self._trigger_hold = trigger_hold
        self._delay_seconds = delay_seconds
        self.cancelled_tasks: list[str] = []

    @property
    def provider_name(self) -> str:
        return "mock-harness"

    def resolve_model(
        self,
        tier: ModelTier,
        persona: AgentPersona = AgentPersona.VAULT_FIRST_MATE,
    ) -> str:
        del persona
        return DEFAULT_AGY_MODEL_MAP[tier]

    async def _stream_provider_task(
        self,
        spec: AgentTaskSpec[Any],
        ctx: ToolExecutionContext,
        model_id: str,
    ) -> AsyncIterator[HarnessStreamEvent]:
        del model_id

        async def _cancel() -> None:
            self.cancelled_tasks.append(spec.task_id)

        self._active_cancels[spec.task_id] = _cancel
        try:
            if self._delay_seconds > 0:
                await asyncio.sleep(self._delay_seconds)

            yield self._make_event(spec, ctx, HarnessEventKind.SESSION_START)
            yield self._make_event(
                spec,
                ctx,
                HarnessEventKind.TEXT_DELTA,
                delta_text="Synthesizing ",
            )
            yield self._make_event(
                spec,
                ctx,
                HarnessEventKind.TOOL_CALL_START,
                tool_name="profile_source_feed",
                tool_call_id="call-1",
                tool_args={"source_system": "INVESTRAN"},
            )
            yield self._make_event(
                spec,
                ctx,
                HarnessEventKind.TEXT_DELTA,
                delta_text="Data Vault 2.0 contract.",
            )

            if self._trigger_hold:
                hold_tool = build_default_domain_tool_map()["request_steward_decision"]
                yield self._make_event(
                    spec,
                    ctx,
                    HarnessEventKind.TOOL_CALL_START,
                    tool_name="request_steward_decision",
                    tool_call_id="call-2",
                )
                await hold_tool.invoke(
                    {
                        "escalation_reason": "TIER_U3_AMBIGUOUS_ENTITY",
                        "headline": "Steward confirmation required for ambiguous entity.",
                        "rule_id": "ALG-A9B",
                    },
                    ctx,
                )
                for obs_ev in ctx.pop_unstreamed_events():
                    yield self._make_event(
                        spec,
                        ctx,
                        HarnessEventKind.OBSERVABILITY_EVENT,
                        obs_event=obs_ev,
                    )

            ctx.write_cas_artifact("canonical_ir_sha256", '{"ir_version": "1.0.0"}')
            yield self._make_event(
                spec,
                ctx,
                HarnessEventKind.TURN_COMPLETE,
                tool_result={
                    "session_id": "sess-mock-001",
                    "structured_output": {
                        "contract_id": "pe_fund_iv_v5",
                        "hub_count": 4,
                        "verdict": "PASS",
                    },
                    "usage": {
                        "input_tokens": 1200,
                        "output_tokens": 450,
                        "reasoning_tokens": 300,
                        "total_tokens": 1950,
                        "model_calls": 2,
                        "cost_usd": 0.042,
                    },
                },
            )
        finally:
            self._active_cancels.pop(spec.task_id, None)


@pytest.mark.asyncio
async def test_base_vault_agent_harness_run_task_and_steward_hold(tmp_path: Path) -> None:
    cfg = _make_harness_config(tmp_path)
    harness = _MockVaultHarness(cfg, trigger_hold=False)
    assert isinstance(harness, VaultAgentHarness)

    spec = AgentTaskSpec[_StructuredSynthesisOutput](
        run_id="run-pe-001",
        trace_id=SAMPLE_TRACE_ID,
        persona=AgentPersona.VAULT_FIRST_MATE,
        stage_id=PipelineStageId.STAGE_00_ORCHESTRATION,
        workbench_view_id=WorkbenchViewId.WB_00,
        viz_id=VizId.VIZ_PIPE_00,
        prompt="Synthesize PE contract",
        system_instructions="You are vault-first-mate.",
        model_tier=ModelTier.HIGH_REASONING,
        output_schema=_StructuredSynthesisOutput,
    )

    result = await harness.run_task(spec)
    assert result.stop_reason == HarnessStopReason.COMPLETED
    assert result.text_output == "Synthesizing Data Vault 2.0 contract."
    assert result.usage.tool_calls == 1
    assert result.usage.total_tokens == 1950
    assert isinstance(result.structured_output, _StructuredSynthesisOutput)
    assert result.structured_output.contract_id == "pe_fund_iv_v5"
    assert result.structured_output.hub_count == 4
    assert "canonical_ir_sha256" in result.artifacts_written

    # Now test STEWARD_HOLD transition when request_steward_decision emits status == "ASK-USER"
    hold_harness = _MockVaultHarness(cfg, trigger_hold=True)
    hold_result = await hold_harness.run_task(spec)
    assert hold_result.stop_reason == HarnessStopReason.STEWARD_HOLD
    assert hold_result.usage.tool_calls == 2
    assert len(hold_result.emitted_events) == 1
    assert hold_result.emitted_events[0]["status"] == "ASK-USER"


@pytest.mark.asyncio
async def test_telemetry_translator_and_jsonl_ledger_integration(tmp_path: Path) -> None:
    cfg = _make_harness_config(tmp_path)
    ledger_path = cfg.state_dir / "vault-ledger.jsonl"
    ledger = JsonlObservabilityLedger(ledger_path=ledger_path)
    translator = HarnessTelemetryTranslator(
        ledger=ledger,
        default_classification=DataClassification.INTERNAL,
    )

    harness = _MockVaultHarness(cfg, trigger_hold=True)
    spec = AgentTaskSpec[Any](
        run_id="run-pe-telemetry",
        trace_id=SAMPLE_TRACE_ID,
        persona=AgentPersona.SCOUT_UNSTRUCTURED,
        stage_id=PipelineStageId.STAGE_02_SCOUT_UNSTRUCTURED,
        workbench_view_id=WorkbenchViewId.WB_03,
        viz_id=VizId.VIZ_STEP_04,
        prompt="Extract PDF spans",
        system_instructions="You are scout-unstructured.",
        model_tier=ModelTier.FAST_TIER,
    )

    events: list[HarnessStreamEvent] = [ev async for ev in harness.stream_task(spec)]
    records = translator.record_many(events)
    assert len(records) == len(events)

    persisted = ledger.read_all_events()
    assert len(persisted) == len(events)
    for rec in persisted:
        validate_observability_event(rec)

    # Verify direct helper function with raw 64-hex scrubbing in Layer 1
    raw_hex = "A" * 64
    ev_with_hex = HarnessStreamEvent(
        run_id="run-scrub-test",
        task_id="task-scrub",
        trace_id=SAMPLE_TRACE_ID,
        span_id=SAMPLE_SPAN_ID,
        provider="antigravity",
        persona=AgentPersona.VAULT_FIRST_MATE,
        stage_id=PipelineStageId.STAGE_00_ORCHESTRATION,
        workbench_view_id=WorkbenchViewId.WB_00,
        viz_id=VizId.VIZ_PIPE_00,
        kind=HarnessEventKind.TEXT_DELTA,
        delta_text=f"Computed hash key {raw_hex} for CalPERS.",
    )
    translated = translate_stream_event_to_observability(ev_with_hex)
    assert raw_hex not in translated["layer1_story_banner"]["headline"]
    assert "[HK: AAAA..AAAA]" in translated["layer1_story_banner"]["headline"]
    validate_observability_event(translated)


@pytest.mark.asyncio
async def test_harness_timeout_and_cancel_active_turn(tmp_path: Path) -> None:
    cfg = _make_harness_config(
        tmp_path,
        budget=HarnessBudget(timeout_seconds=1.1),
    )
    slow_harness = _MockVaultHarness(cfg, delay_seconds=2.5)
    spec = AgentTaskSpec[Any](
        run_id="run-timeout",
        trace_id=SAMPLE_TRACE_ID,
        persona=AgentPersona.VAULT_FIRST_MATE,
        stage_id=PipelineStageId.STAGE_00_ORCHESTRATION,
        workbench_view_id=WorkbenchViewId.WB_00,
        viz_id=VizId.VIZ_PIPE_00,
        prompt="Timeout check",
        system_instructions="System",
    )
    res = await slow_harness.run_task(spec)
    assert res.stop_reason == HarnessStopReason.MAX_TURNS_EXCEEDED


# ---------------------------------------------------------------------------
# 5. WP-01 Real Neuro-Symbolic Domain Tool Execution Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wp01_real_profile_source_feed_from_csv_file(tmp_path: Path) -> None:
    tool_map = build_default_domain_tool_map()
    ctx = _make_tool_context(tmp_path, persona=AgentPersona.SCOUT_SOURCE)
    csv_file = ctx.config.workspace_dir / "customers.csv"
    csv_file.write_text(
        "customer_id,email,tier,spend_usd\n"
        "CUST-001,alice@example.com,VIP,1250.50\n"
        "CUST-002,bob@example.com,STANDARD,340.00\n"
        "CUST-003,carol@example.com,VIP,890.25\n"
        "CUST-004,,STANDARD,120.00\n",
        encoding="utf-8",
    )

    with pytest.raises(PermissionError, match="Path traversal"):
        await tool_map["profile_source_feed"].invoke(
            {
                "source_system": "SHOPIFY_COMMERCE",
                "table_or_path": "../../../etc/passwd",
            },
            ctx,
        )

    outside_file = tmp_path / "outside.csv"
    outside_file.write_text("id\n1\n", encoding="utf-8")
    with pytest.raises(PermissionError, match="outside allowed workspace"):
        await tool_map["profile_source_feed"].invoke(
            {
                "source_system": "SHOPIFY_COMMERCE",
                "table_or_path": str(outside_file),
            },
            ctx,
        )

    out = await tool_map["profile_source_feed"].invoke(
        {
            "source_system": "SHOPIFY_COMMERCE",
            "table_or_path": str(csv_file),
            "baseline_columns": ["customer_id", "email", "tier"],
        },
        ctx,
    )
    assert out.row_count == 4
    assert out.added_columns == ["spend_usd"]
    assert out.drift_status == ObservabilityVerdict.AUTO_FIX

    cust_metrics = out.column_metrics["customer_id"]
    assert cust_metrics["cardinality_ratio_u"] == 1.0
    assert cust_metrics["non_null_uniqueness_u_nn"] == 1.0
    assert cust_metrics["null_ratio_n_null"] == 0.0
    assert cust_metrics["shannon_entropy_h"] > 1.5

    email_metrics = out.column_metrics["email"]
    assert email_metrics["null_ratio_n_null"] == 0.25
    assert email_metrics["non_null_uniqueness_u_nn"] == 1.0

    detailed = ctx.get_state("last_column_profiles")
    assert isinstance(detailed, dict)
    assert "inferred_type" in detailed["customer_id"]
    assert "CUST-001" in detailed["customer_id"]["sample_values"]


@pytest.mark.asyncio
async def test_wp01_real_extract_pdf_spans_offset_discovery_and_tiers(tmp_path: Path) -> None:
    tool_map = build_default_domain_tool_map()
    ctx = _make_tool_context(tmp_path, persona=AgentPersona.SCOUT_UNSTRUCTURED)
    doc_text = (
        "Section 4.2: Limited Partner CalPERS is granted a fee rebate of 125.0000 bps "
        "effective 2026-01-01.\nSection 5.1: Co-invest right applies to Apex IV."
    )

    out = await tool_map["extract_pdf_spans"].invoke(
        {
            "document_uri": "side_letter.md",
            "document_text": doc_text,
            "candidate_spans": [
                {
                    "field_name": "rebate_bps",
                    "extracted_value": "125.0000 bps",
                    "verbatim_quote": "fee rebate of 125.0000 bps",
                    "confidence": 0.96,
                },
                {
                    "field_name": "normalized_ws_clause",
                    "extracted_value": "effective 2026-01-01",
                    "verbatim_quote": "125.0000 bps   effective 2026-01-01.",
                    "confidence": 0.82,
                },
            ],
            "er_margin": 0.65,
        },
        ctx,
    )
    assert out.all_spans_verified is True
    assert out.verified_spans[0].span_verified is True
    assert doc_text[out.verified_spans[0].char_start : out.verified_spans[0].char_end] == (
        "fee rebate of 125.0000 bps"
    )
    assert out.verified_spans[0].er_tier == "Tier-U1"
    assert out.verified_spans[1].span_verified is True
    assert out.verified_spans[1].er_tier == "Tier-U2"
    assert out.er_tier == "Tier-U2"
    assert out.status == ObservabilityVerdict.PASS

    # Low confidence (< 0.75) triggers Tier-U3 and ASK-USER
    low_conf_out = await tool_map["extract_pdf_spans"].invoke(
        {
            "document_uri": "side_letter.md",
            "document_text": doc_text,
            "candidate_spans": [
                {
                    "field_name": "ambiguous_fund",
                    "extracted_value": "Apex IV",
                    "verbatim_quote": "Apex IV",
                    "confidence": 0.65,
                }
            ],
            "er_margin": 0.65,
        },
        ctx,
    )
    assert low_conf_out.all_spans_verified is True
    assert low_conf_out.verified_spans[0].er_tier == "Tier-U3"
    assert low_conf_out.status == ObservabilityVerdict.ASK_USER


@pytest.mark.asyncio
async def test_wp01_real_compile_gates_codemods_and_sandbox(tmp_path: Path) -> None:
    from opendv_core.schema_registry import get_examples_dir, load_structured_file

    tool_map = build_default_domain_tool_map()
    ship_ctx = _make_tool_context(tmp_path, persona=AgentPersona.SHIP_VAULT)

    pe_ir_path = get_examples_dir() / "private_equity" / "pe_canonical_ir.json"
    pe_ir_dict = load_structured_file(pe_ir_path)

    # 1. compile_canonical_ir with real CanonicalIR
    ir_out = await tool_map["compile_canonical_ir"].invoke(
        {
            "contract_id": "pe_canonical_v5",
            "target_dialect": "duckdb",
            "contract_payload": pe_ir_dict,
        },
        ship_ctx,
    )
    assert ir_out.hub_count == len(pe_ir_dict["hubs"])
    assert ir_out.link_count == len(pe_ir_dict["links"])
    assert ir_out.satellite_count == len(pe_ir_dict["satellites"])
    compiled_sql = ship_ctx.get_state("last_compiled_sql")
    assert "CREATE SCHEMA IF NOT EXISTS raw_vault;" in compiled_sql
    assert "raw_vault.hub_fund" in compiled_sql

    # 2. run_axi_dv_gates with a failing contract dictionary
    bad_contract = {
        "contract_version": "5.0",
        "domain_namespace": "commerce",
        "hubs": [
            {
                "name": "hub_customer",
                "grain_statement": "Too short",
                "ontology_concept_uri": "urn:ontology:concept:customer",
                "business_key_sources": [
                    {
                        "bronze_table": "bronze.customers",
                        "record_source_code": "CRM",
                        "key_columns": ["customer_id"],
                        "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                        "require_src_qualifier": False,
                    }
                ],
            }
        ],
        "links": [],
        "satellites": [],
        "reference_tables": [],
        "column_triage_ledger": [],
    }
    gates_fail = await tool_map["run_axi_dv_gates"].invoke(
        {
            "contract_id": "bad_commerce_contract",
            "gates_to_run": ["G1", "G2", "G3"],
            "contract_payload": bad_contract,
        },
        ship_ctx,
    )
    assert gates_fail.overall_verdict == ObservabilityVerdict.REJECT_WITH_TRACE
    assert gates_fail.toon_diagnostic_trace is not None
    assert "@toon_v1" in gates_fail.toon_diagnostic_trace

    # 3. apply_ast_codemod with real ALG-A1 PII satellite split & ALG-A5 SQL rewrite
    sat_yaml = (
        "satellites:\n"
        "  - name: sat_customer_core\n"
        "    parent_entity: hub_customer\n"
        "    satellite_type: STANDARD_SATELLITE\n"
        "    source_bronze_table: bronze.customers\n"
        "    security_classification: PUBLIC\n"
        "    volatility_tier: STANDARD\n"
        "    payload_columns:\n"
        "      - customer_segment\n"
        "      - loyalty_tier\n"
        "      - email\n"
        "      - tax_id_ssn\n"
    )
    a1_out = await tool_map["apply_ast_codemod"].invoke(
        {
            "codemod_id": "ALG-A1",
            "target_entity": "sat_customer_core",
            "contract_yaml": sat_yaml,
        },
        ship_ctx,
    )
    assert a1_out.applied is True
    patched_dict = ship_ctx.get_state("last_patched_contract_dict")
    sat_names = [s["name"] for s in patched_dict["satellites"]]
    assert "sat_customer_core_pii" in sat_names

    noop_out = await tool_map["apply_ast_codemod"].invoke(
        {
            "codemod_id": "ALG-A1",
            "target_entity": "sat_nonexistent_entity",
            "contract_yaml": sat_yaml,
        },
        ship_ctx,
    )
    assert noop_out.applied is False
    assert noop_out.verdict == ObservabilityVerdict.REJECT_WITH_TRACE

    a5_out = await tool_map["apply_ast_codemod"].invoke(
        {
            "codemod_id": "ALG-A5",
            "target_entity": "hk_customer",
            "contract_yaml": "SELECT MD5(customer_id) AS hk_customer FROM bronze.customers",
        },
        ship_ctx,
    )
    assert a5_out.applied is True
    patched_sql = ship_ctx.get_state("last_patched_contract_yaml")
    assert "SHA2" in patched_sql or "OCTET_LENGTH" in patched_sql

    # 4. execute_duckdb_sandbox detecting orphan FKs in raw_vault
    orphan_sql = (
        "CREATE SCHEMA IF NOT EXISTS raw_vault;\n"
        "CREATE TABLE raw_vault.hub_customer (hk_customer CHAR(64) PRIMARY KEY, rec_src VARCHAR);\n"
        "CREATE TABLE raw_vault.sat_customer_core ("
        "hk_customer CHAR(64), load_dts TIMESTAMP, rec_src VARCHAR, hash_diff CHAR(64));\n"
        "INSERT INTO raw_vault.sat_customer_core VALUES "
        "('orphan_hk_123', TIMESTAMP '2026-01-01 00:00:00', 'CRM', 'diff_123');\n"
    )
    sbx_fail = await tool_map["execute_duckdb_sandbox"].invoke(
        {"sql_bundle": orphan_sql, "batches_to_run": [1, 2]},
        ship_ctx,
    )
    assert sbx_fail.all_batches_passed is False
    assert sbx_fail.orphan_count == 1
    assert sbx_fail.status == ObservabilityVerdict.REJECT_WITH_TRACE
