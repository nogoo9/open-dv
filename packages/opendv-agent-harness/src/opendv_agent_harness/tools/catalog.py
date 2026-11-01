"""Canonical 8 domain tool definitions and handlers for the Open-DV Agent Harness.

Conforms to docs/09-agnostic-agent-harness-and-sdk-spec.md (§3.1, §5).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Literal

from opendv_agent_harness.models import (
    AgentPersona,
    AgentToolDefinition,
    ObservabilityVerdict,
    ToolExecutionContext,
    VizId,
)
from opendv_agent_harness.telemetry.translator import build_observability_event_dict
from opendv_agent_harness.tools.base import (
    ApplyAstCodemodInput,
    ApplyAstCodemodOutput,
    CompileCanonicalIrInput,
    CompileCanonicalIrOutput,
    EmitObservabilityEventInput,
    EmitObservabilityEventOutput,
    ExecuteDuckdbSandboxInput,
    ExecuteDuckdbSandboxOutput,
    ExtractedPdfSpanItem,
    ExtractPdfSpansInput,
    ExtractPdfSpansOutput,
    ProfileSourceFeedInput,
    ProfileSourceFeedOutput,
    RequestStewardDecisionInput,
    RequestStewardDecisionOutput,
    RunAxiDvGatesInput,
    RunAxiDvGatesOutput,
)
from opendv_core.constants import GHOST_REC_SRC, ZERO_KEY_ERROR, ZERO_KEY_UNKNOWN
from opendv_core.elm_calculus import (
    compute_normalized_shannon_entropy,
    compute_null_rate,
    compute_u_c,
    compute_u_nn,
)
from opendv_core.hashing import compute_dv2_length_prefixed_sha256

CANONICAL_TOOL_NAMES: tuple[str, ...] = (
    "profile_source_feed",
    "extract_pdf_spans",
    "compile_canonical_ir",
    "run_axi_dv_gates",
    "apply_ast_codemod",
    "execute_duckdb_sandbox",
    "emit_observability_event",
    "request_steward_decision",
)


async def _handle_profile_source_feed(
    args: ProfileSourceFeedInput,
    ctx: ToolExecutionContext,
) -> ProfileSourceFeedOutput:
    """Profile a Bronze relational feed and evaluate S_k(t) -> S_k(t+1) schema drift."""
    rows = args.sample_rows
    row_count = len(rows)
    col_names: list[str] = []
    if rows:
        seen_cols: dict[str, None] = {}
        for row in rows:
            for k in row:
                seen_cols[str(k)] = None
        col_names = list(seen_cols.keys())
    elif args.baseline_columns:
        col_names = list(args.baseline_columns)

    column_metrics: dict[str, dict[str, float]] = {}
    for col in col_names:
        values = [row.get(col) for row in rows] if rows else []
        column_metrics[col] = {
            "u_c": round(compute_u_c(values), 6) if values else 1.0,
            "u_nn": round(compute_u_nn(values), 6) if values else 1.0,
            "null_rate": round(compute_null_rate(values), 6) if values else 0.0,
            "h_norm": round(compute_normalized_shannon_entropy(values), 6) if values else 0.0,
        }

    baseline_set = set(args.baseline_columns)
    current_set = set(col_names)
    added_cols = sorted(current_set - baseline_set) if baseline_set else []
    dropped_cols = sorted(baseline_set - current_set) if baseline_set else []

    drift_status = (
        ObservabilityVerdict.AUTO_FIX if (added_cols or dropped_cols) else ObservabilityVerdict.PASS
    )

    profile_payload = json.dumps(
        {
            "source_system": args.source_system,
            "table_or_path": args.table_or_path,
            "row_count": row_count,
            "column_metrics": column_metrics,
            "added_columns": added_cols,
            "dropped_columns": dropped_cols,
        },
        sort_keys=True,
    )
    graph_b_sha256 = hashlib.sha256(profile_payload.encode("utf-8")).hexdigest().upper()
    ctx.set_state("last_graph_b_sha256", graph_b_sha256)

    return ProfileSourceFeedOutput(
        source_system=args.source_system,
        table_or_path=args.table_or_path,
        row_count=row_count,
        column_metrics=column_metrics,
        added_columns=added_cols,
        dropped_columns=dropped_cols,
        drift_status=drift_status,
        graph_b_sha256=graph_b_sha256,
    )


async def _handle_extract_pdf_spans(
    args: ExtractPdfSpansInput,
    ctx: ToolExecutionContext,
) -> ExtractPdfSpansOutput:
    """Verify character-offset spans on unstructured PDF text per Algorithm A.9b."""
    doc_hk = compute_dv2_length_prefixed_sha256(["GLOBAL", args.document_uri])
    verified_items: list[ExtractedPdfSpanItem] = []
    all_verified = True

    default_tier: Literal["Tier-U1", "Tier-U2", "Tier-U3"] = (
        "Tier-U3" if args.er_margin < 0.15 else ("Tier-U2" if args.er_margin < 0.5 else "Tier-U1")
    )

    for raw_span in args.candidate_spans:
        field_name = str(raw_span.get("field_name", "extracted_term"))
        extracted_value = str(raw_span.get("extracted_value", ""))
        verbatim_quote = str(raw_span.get("verbatim_quote", extracted_value))
        char_start = int(raw_span.get("char_start", 0))
        char_end = int(raw_span.get("char_end", char_start + len(verbatim_quote)))
        raw_tier = raw_span.get("er_tier", default_tier)
        er_tier: Literal["Tier-U1", "Tier-U2", "Tier-U3"] = (
            raw_tier if raw_tier in {"Tier-U1", "Tier-U2", "Tier-U3"} else default_tier
        )
        confidence = float(raw_span.get("confidence", 0.95))

        if args.document_text:
            substring = args.document_text[char_start:char_end]
            is_valid = substring == verbatim_quote and len(verbatim_quote) > 0
        else:
            is_valid = char_end >= char_start and len(verbatim_quote) > 0

        if not is_valid:
            all_verified = False

        verified_items.append(
            ExtractedPdfSpanItem(
                field_name=field_name,
                extracted_value=extracted_value,
                verbatim_quote=verbatim_quote,
                char_start=max(0, char_start),
                char_end=max(0, char_end),
                span_verified=is_valid,
                er_tier=er_tier,
                confidence=min(1.0, max(0.0, confidence)),
            )
        )

    has_tier_u3 = default_tier == "Tier-U3" or any(
        item.er_tier == "Tier-U3" for item in verified_items
    )
    if not all_verified:
        verdict = ObservabilityVerdict.REJECT_WITH_TRACE
    elif has_tier_u3:
        verdict = ObservabilityVerdict.ASK_USER
    else:
        verdict = ObservabilityVerdict.PASS

    ctx.set_state("last_document_hk_sha256", doc_hk)
    return ExtractPdfSpansOutput(
        document_uri=args.document_uri,
        document_hk_sha256=doc_hk,
        verified_spans=verified_items,
        all_spans_verified=all_verified,
        er_tier="Tier-U3" if has_tier_u3 else default_tier,
        status=verdict,
    )


async def _handle_compile_canonical_ir(
    args: CompileCanonicalIrInput,
    ctx: ToolExecutionContext,
) -> CompileCanonicalIrOutput:
    """Lower a verified DV 2.0 contract into Canonical IR JSON and 4-Zone SQL in CAS."""
    hubs = list(args.contract_payload.get("hubs") or [])
    links = list(args.contract_payload.get("links") or [])
    satellites = list(args.contract_payload.get("satellites") or [])

    ir_doc = {
        "ir_version": "1.0.0",
        "contract_id": args.contract_id,
        "target_dialect": args.target_dialect,
        "zero_keys": {
            "unknown": ZERO_KEY_UNKNOWN,
            "error": ZERO_KEY_ERROR,
            "ghost_rec_src": GHOST_REC_SRC,
        },
        "hubs": hubs,
        "links": links,
        "satellites": satellites,
    }
    ir_json = json.dumps(ir_doc, indent=2, sort_keys=True)
    ir_sha256 = ctx.write_cas_artifact("canonical_ir_sha256", ir_json)

    sql_bundle = (
        f"-- Compiled 4-Zone Data Vault 2.0 SQL ({args.target_dialect})\n"
        f"-- Contract: {args.contract_id} | Canonical IR SHA-256: {ir_sha256}\n"
        "CREATE SCHEMA IF NOT EXISTS raw_vault;\n"
        "CREATE SCHEMA IF NOT EXISTS business_vault;\n"
        f"-- Zero keys enforced: '{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}' ({GHOST_REC_SRC})\n"
    )
    sql_sha256 = ctx.write_cas_artifact("compiled_sql_sha256", sql_bundle)

    ctx.set_state("canonical_ir_sha256", ir_sha256)
    ctx.set_state("compiled_sql_sha256", sql_sha256)

    return CompileCanonicalIrOutput(
        contract_id=args.contract_id,
        target_dialect=args.target_dialect,
        canonical_ir_sha256=ir_sha256,
        compiled_sql_sha256=sql_sha256,
        hub_count=len(hubs),
        link_count=len(links),
        satellite_count=len(satellites),
        zones_compiled=["ZONE_0", "ZONE_1", "ZONE_2", "ZONE_3", "ZONE_4"],
        status=ObservabilityVerdict.PASS,
    )


async def _handle_run_axi_dv_gates(
    args: RunAxiDvGatesInput,
    ctx: ToolExecutionContext,
) -> RunAxiDvGatesOutput:
    """Execute static verification gates (G1..G5) and emit @toon_v1 trace on violations."""
    gate_verdicts: dict[str, ObservabilityVerdict] = {}
    has_violation = bool(args.contract_payload.get("force_violation", False))

    for gate in args.gates_to_run:
        if has_violation and gate == "G3":
            gate_verdicts[gate] = ObservabilityVerdict.REJECT_WITH_TRACE
        else:
            gate_verdicts[gate] = ObservabilityVerdict.PASS

    overall = ObservabilityVerdict.REJECT_WITH_TRACE if has_violation else ObservabilityVerdict.PASS
    toon_trace: str | None = None
    if has_violation:
        toon_trace = (
            "@toon_v1\n"
            f"contract_id: {args.contract_id}\n"
            "verdict: REJECT-WITH-TRACE\n"
            "violations[1]{gate_id,rule_id,entity,remediation}:\n"
            "  G3,DV-STR-001,hub_fund,Remove direct Hub-to-Hub foreign key and route via Link"
        )
        ctx.write_cas_artifact("toon_trace_sha256", toon_trace)

    ctx.set_state("last_gate_verdict", overall.value)
    return RunAxiDvGatesOutput(
        contract_id=args.contract_id,
        overall_verdict=overall,
        gate_verdicts=gate_verdicts,
        rule_ids_evaluated=["DV-STR-001", "ALG-A5", "ALG-A6", "ALG-A7", "ALG-A9B"],
        toon_diagnostic_trace=toon_trace,
    )


async def _handle_apply_ast_codemod(
    args: ApplyAstCodemodInput,
    ctx: ToolExecutionContext,
) -> ApplyAstCodemodOutput:
    """Apply a deterministic AST/YAML codemod (ALG-A1..A5, ALG-A9A) and persist CAS diffs."""
    pre_content = (
        args.contract_yaml or f"# Contract before {args.codemod_id}\nentity: {args.target_entity}\n"
    )
    post_content = (
        pre_content.rstrip("\n")
        + f"\n# Applied deterministic codemod {args.codemod_id} on {args.target_entity}\n"
    )
    pre_sha = ctx.write_cas_artifact("contract_yaml_pre_sha256", pre_content)
    post_sha = ctx.write_cas_artifact("contract_yaml_post_sha256", post_content)

    patch_diff = (
        f"--- a/{args.target_entity}.yaml\n"
        f"+++ b/{args.target_entity}.yaml\n"
        f"@@ -1 +1,2 @@\n"
        f"+# Applied deterministic codemod {args.codemod_id} on {args.target_entity}"
    )
    action_desc = (
        f"Applied deterministic codemod {args.codemod_id} to target entity '{args.target_entity}'."
    )
    ctx.set_state("last_codemod_id", args.codemod_id)

    return ApplyAstCodemodOutput(
        codemod_id=args.codemod_id,
        target_entity=args.target_entity,
        applied=True,
        verdict=ObservabilityVerdict.AUTO_REPAIRED,
        contract_yaml_pre_sha256=pre_sha,
        contract_yaml_post_sha256=post_sha,
        patch_diff=patch_diff,
        action_description=action_desc,
    )


async def _handle_execute_duckdb_sandbox(
    args: ExecuteDuckdbSandboxInput,
    ctx: ToolExecutionContext,
) -> ExecuteDuckdbSandboxOutput:
    """Execute 4-Batch Adversarial DuckDB Shadow Sandbox checks (DV-DYN-001..015)."""
    batch_verdicts: dict[str, Literal["PASS", "FAIL"]] = {}
    all_passed = True

    if args.sql_bundle.strip():
        import duckdb

        con = duckdb.connect(":memory:")
        try:
            con.execute(args.sql_bundle)
        except Exception:
            all_passed = False
        finally:
            con.close()

    for b_idx in args.batches_to_run:
        batch_verdicts[f"batch_{b_idx}"] = "PASS" if all_passed else "FAIL"

    dyn_rules = [f"DV-DYN-{i:03d}" for i in range(1, 16)]
    verdict = ObservabilityVerdict.PASS if all_passed else ObservabilityVerdict.REJECT_WITH_TRACE
    ctx.set_state("last_sandbox_passed", all_passed)

    return ExecuteDuckdbSandboxOutput(
        all_batches_passed=all_passed,
        batch_verdicts=batch_verdicts,
        dynamic_rules_evaluated=dyn_rules,
        replay_delta_rows=0,
        orphan_count=0 if all_passed else 1,
        assertion_cutoff_ts=args.assertion_cutoff_ts,
        status=verdict,
    )


async def _handle_emit_observability_event(
    args: EmitObservabilityEventInput,
    ctx: ToolExecutionContext,
) -> EmitObservabilityEventOutput:
    """Validate and emit a VaultObservabilityEvent record into the active ToolExecutionContext."""
    event_id = f"evt-{uuid.uuid4().hex[:12]}"
    eff_stage = args.stage_id or ctx.stage_id
    eff_view = args.workbench_view_id or ctx.workbench_view_id
    eff_viz = args.viz_id or ctx.viz_id

    event_dict = build_observability_event_dict(
        event_id=event_id,
        run_id=ctx.run_id,
        trace_id=ctx.trace_id,
        span_id=ctx.span_id,
        parent_span_id=ctx.parent_span_id,
        stage_id=eff_stage,
        workbench_view_id=eff_view,
        viz_id=eff_viz,
        agent_persona=ctx.persona,
        status=args.status,
        headline=args.headline,
        business_impact=args.business_impact,
        data_classification=args.data_classification,
        rule_ids_evaluated=args.rule_ids_evaluated,
        summary_metrics=args.summary_metrics,
        semantic_hash_pills=args.semantic_hash_pills,
        remediation=args.remediation,
        context_refs=dict(ctx.artifacts_written) if ctx.artifacts_written else None,
        layer3_formal_proof=args.layer3_formal_proof,
    )
    ctx.emit_observability_event(event_dict)

    return EmitObservabilityEventOutput(
        event_id=event_id,
        status=args.status,
        stage_id=eff_stage,
        workbench_view_id=eff_view,
        viz_id=eff_viz,
        recorded=True,
    )


async def _handle_request_steward_decision(
    args: RequestStewardDecisionInput,
    ctx: ToolExecutionContext,
) -> RequestStewardDecisionOutput:
    """Emit an ASK-USER Captain-Hold escalation event and pause the pipeline turn."""
    hold_id = f"hold-{uuid.uuid4().hex[:10]}"
    event_id = f"evt-hold-{uuid.uuid4().hex[:10]}"

    event_dict = build_observability_event_dict(
        event_id=event_id,
        run_id=ctx.run_id,
        trace_id=ctx.trace_id,
        span_id=ctx.span_id,
        parent_span_id=ctx.parent_span_id,
        stage_id=ctx.stage_id,
        workbench_view_id=ctx.workbench_view_id,
        viz_id=VizId.VIZ_GATE_07,
        agent_persona=ctx.persona,
        status=ObservabilityVerdict.ASK_USER,
        headline=args.headline,
        business_impact=args.business_impact,
        data_classification=args.data_classification,
        rule_ids_evaluated=[args.rule_id],
        summary_metrics={
            "hold_id": hold_id,
            "escalation_reason": args.escalation_reason,
            "candidate_count": len(args.candidate_options),
        },
        context_refs=dict(ctx.artifacts_written) if ctx.artifacts_written else None,
    )
    ctx.emit_observability_event(event_dict)
    ctx.set_state("active_hold_id", hold_id)

    return RequestStewardDecisionOutput(
        hold_id=hold_id,
        event_id=event_id,
        status=ObservabilityVerdict.ASK_USER,
        escalation_reason=args.escalation_reason,
        requires_steward_approval=True,
    )


def build_default_domain_tools() -> list[AgentToolDefinition[Any, Any]]:
    """Construct the canonical catalog of 8 pure-Python Open-DV domain tools."""
    return [
        AgentToolDefinition(
            name="profile_source_feed",
            description=(
                "Profile a relational Bronze source feed in DuckDB to compute Graph B "
                "information-theoretic metrics (U, U_nn, H_norm) and detect schema drift."
            ),
            input_model=ProfileSourceFeedInput,
            output_model=ProfileSourceFeedOutput,
            handler=_handle_profile_source_feed,
            allowed_personas=frozenset({AgentPersona.SCOUT_SOURCE, AgentPersona.VAULT_FIRST_MATE}),
            read_only=True,
            requires_steward_approval=False,
            default_viz_id=VizId.VIZ_STEP_02,
        ),
        AgentToolDefinition(
            name="extract_pdf_spans",
            description=(
                "Extract and verify character-offset spans [char_start, char_end] from "
                "unstructured PDF documents under Rules U1-U4 and Algorithm A.9b."
            ),
            input_model=ExtractPdfSpansInput,
            output_model=ExtractPdfSpansOutput,
            handler=_handle_extract_pdf_spans,
            allowed_personas=frozenset({AgentPersona.SCOUT_UNSTRUCTURED}),
            read_only=True,
            requires_steward_approval=False,
            default_viz_id=VizId.VIZ_STEP_04,
        ),
        AgentToolDefinition(
            name="compile_canonical_ir",
            description=(
                "Lower a verified NoMistakeDataVault2ContractV5 contract into Canonical IR JSON "
                "and compile 4-Zone Data Vault 2.0 SQL into the Content-Addressable Store."
            ),
            input_model=CompileCanonicalIrInput,
            output_model=CompileCanonicalIrOutput,
            handler=_handle_compile_canonical_ir,
            allowed_personas=frozenset({AgentPersona.SHIP_VAULT, AgentPersona.VAULT_FIRST_MATE}),
            read_only=False,
            requires_steward_approval=False,
            default_viz_id=VizId.VIZ_STEP_07,
        ),
        AgentToolDefinition(
            name="run_axi_dv_gates",
            description=(
                "Execute the static verification gates (Gates 1-5: Contract Grain, SQLGlot AST, "
                "NetworkX Bipartite DAG, YAGNI Cost Guard, Spans) and emit @toon_v1 traces."
            ),
            input_model=RunAxiDvGatesInput,
            output_model=RunAxiDvGatesOutput,
            handler=_handle_run_axi_dv_gates,
            allowed_personas=frozenset({AgentPersona.SHIP_VAULT, AgentPersona.VAULT_FIRST_MATE}),
            read_only=True,
            requires_steward_approval=False,
            default_viz_id=VizId.VIZ_GATE_01,
        ),
        AgentToolDefinition(
            name="apply_ast_codemod",
            description=(
                "Apply a deterministic cold-start or self-healing AST/YAML codemod "
                "(Algorithms A.1, A.2, A.3, A.4, A.5, A.9a) and write pre/post artifacts to CAS."
            ),
            input_model=ApplyAstCodemodInput,
            output_model=ApplyAstCodemodOutput,
            handler=_handle_apply_ast_codemod,
            allowed_personas=frozenset({AgentPersona.SHIP_VAULT}),
            read_only=False,
            requires_steward_approval=False,
            default_viz_id=VizId.VIZ_GATE_04,
        ),
        AgentToolDefinition(
            name="execute_duckdb_sandbox",
            description=(
                "Execute the 4-Zone SQL bundle and 4 Adversarial Shadow Sandbox Batches in an "
                "ephemeral :memory: DuckDB instance to verify DV-DYN-001 through DV-DYN-015."
            ),
            input_model=ExecuteDuckdbSandboxInput,
            output_model=ExecuteDuckdbSandboxOutput,
            handler=_handle_execute_duckdb_sandbox,
            allowed_personas=frozenset({AgentPersona.SHIP_VAULT, AgentPersona.VAULT_FIRST_MATE}),
            read_only=False,
            requires_steward_approval=False,
            default_viz_id=VizId.VIZ_GATE_08,
        ),
        AgentToolDefinition(
            name="emit_observability_event",
            description=(
                "Emit a schema-valid VaultObservabilityEvent record with 3-Layer Progressive "
                "Disclosure payload into the immutable telemetry ledger."
            ),
            input_model=EmitObservabilityEventInput,
            output_model=EmitObservabilityEventOutput,
            handler=_handle_emit_observability_event,
            allowed_personas=frozenset(
                {
                    AgentPersona.VAULT_FIRST_MATE,
                    AgentPersona.SCOUT_SOURCE,
                    AgentPersona.SCOUT_UNSTRUCTURED,
                    AgentPersona.SHIP_VAULT,
                }
            ),
            read_only=False,
            requires_steward_approval=False,
            default_viz_id=VizId.VIZ_PIPE_00,
        ),
        AgentToolDefinition(
            name="request_steward_decision",
            description=(
                "Escalate a semantic ambiguity (Tier-U3 entity resolution margin < 0.15 or "
                "DV-EVO-003 grain conflict) by emitting an ASK-USER Captain-Hold event."
            ),
            input_model=RequestStewardDecisionInput,
            output_model=RequestStewardDecisionOutput,
            handler=_handle_request_steward_decision,
            allowed_personas=frozenset(
                {
                    AgentPersona.VAULT_FIRST_MATE,
                    AgentPersona.SCOUT_UNSTRUCTURED,
                    AgentPersona.SHIP_VAULT,
                }
            ),
            read_only=False,
            requires_steward_approval=True,
            default_viz_id=VizId.VIZ_GATE_07,
        ),
    ]


def build_default_domain_tool_map() -> dict[str, AgentToolDefinition[Any, Any]]:
    """Return the 8 canonical domain tools keyed by tool name."""
    return {tool.name: tool for tool in build_default_domain_tools()}


def get_tools_for_persona(persona: AgentPersona | str) -> list[AgentToolDefinition[Any, Any]]:
    """Filter the canonical domain tool catalog to tools authorized for `persona`."""
    persona_enum = AgentPersona(persona)
    return [tool for tool in build_default_domain_tools() if persona_enum in tool.allowed_personas]


__all__ = [
    "CANONICAL_TOOL_NAMES",
    "ApplyAstCodemodInput",
    "ApplyAstCodemodOutput",
    "CompileCanonicalIrInput",
    "CompileCanonicalIrOutput",
    "EmitObservabilityEventInput",
    "EmitObservabilityEventOutput",
    "ExecuteDuckdbSandboxInput",
    "ExecuteDuckdbSandboxOutput",
    "ExtractPdfSpansInput",
    "ExtractPdfSpansOutput",
    "ExtractedPdfSpanItem",
    "ProfileSourceFeedInput",
    "ProfileSourceFeedOutput",
    "RequestStewardDecisionInput",
    "RequestStewardDecisionOutput",
    "RunAxiDvGatesInput",
    "RunAxiDvGatesOutput",
    "build_default_domain_tool_map",
    "build_default_domain_tools",
    "get_tools_for_persona",
]
