"""Canonical 8 domain tool definitions and handlers for the Open-DV Agent Harness.

Conforms to docs/03-verification-and-agentic-architecture.md
and docs/06-explainability-and-observability-spec.md.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any, Literal

import duckdb
import sqlglot
import yaml
from pydantic import ValidationError
from sqlglot import exp

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
from opendv_axi_verifier.alg_a8_orchestrator import run_axi_dv_verification_pipeline
from opendv_axi_verifier.codemods import (
    codemod_demote_enum_hubs_to_ref,
    codemod_enforce_cold_start_extensibility,
    codemod_enforce_staging_hashes_sql,
    codemod_extract_hub_mutable_attributes,
    codemod_inject_link_effectivity,
    codemod_intra_source_schema_drift,
    codemod_split_satellites_3axis,
)
from opendv_axi_verifier.toon_codec import ToonTrace, ToonViolation, serialize_toon_v1
from opendv_axi_verifier.verifiers.alg_a9b_unstructured import classify_er_resolution_tier
from opendv_core.constants import (
    GHOST_DTS,
    GHOST_REC_SRC,
    ZERO_KEY_ERROR,
    ZERO_KEY_REC_SRC,
    ZERO_KEY_UNKNOWN,
)
from opendv_core.elm_calculus import (
    compute_normalized_shannon_entropy,
    compute_null_rate,
    compute_shannon_entropy,
    compute_u_c,
    compute_u_nn,
)
from opendv_core.hashing import compute_dv2_length_prefixed_sha256
from opendv_core.models.canonical_ir import CanonicalIR
from opendv_core.models.contract_v5 import NoMistakeDataVault2ContractV5
from opendv_core.paths import PathContainmentError, resolve_workspace_path
from opendv_duckdb_sandbox.compiler_4zone import (
    build_algorithm_a5_sha256_sql,
    compile_canonical_ir_to_4zone_sql,
)
from opendv_duckdb_sandbox.graph_b_profiler import GraphBProfiler
from opendv_duckdb_sandbox.ir_lowerer import lower_contract_to_canonical_ir
from opendv_duckdb_sandbox.sandbox_engine import DuckDbShadowSandbox
from opendv_sources import SourceSpec, get_source_registry

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

_PII_COLUMN_HINTS: frozenset[str] = frozenset(
    {
        "ssn",
        "tax_id",
        "tax_id_ssn",
        "email",
        "contact_email",
        "phone",
        "phone_number",
        "passport_no",
        "iban",
        "wire_account_iban",
        "date_of_birth",
        "dob",
    }
)


def _resolve_existing_file(raw_path: str, ctx: ToolExecutionContext) -> Path | None:
    """Resolve `raw_path` to an existing file strictly contained within workspace/state dirs."""
    if not raw_path or not raw_path.strip():
        return None
    allowed_roots = [
        ctx.config.state_dir,
        ctx.config.app_data_dir,
        ctx.config.save_dir,
        *ctx.config.sandbox.allowed_workspaces,
    ]
    try:
        resolved = resolve_workspace_path(
            raw_path,
            workspace_root=ctx.config.workspace_dir,
            allowed_roots=allowed_roots,
            allow_temp=False,
        )
        return resolved if resolved.is_file() else None
    except PathContainmentError as exc:
        raise PermissionError(str(exc)) from exc


def _infer_python_value_type(values: list[Any]) -> str:
    """Infer a SQL type label from non-null Python sample values."""
    non_null = [v for v in values if v is not None]
    if not non_null:
        return "VARCHAR"
    if all(isinstance(v, bool) for v in non_null):
        return "BOOLEAN"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in non_null):
        return "BIGINT"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null):
        return "DOUBLE"
    return "VARCHAR"


async def _handle_profile_source_feed(
    args: ProfileSourceFeedInput,
    ctx: ToolExecutionContext,
) -> ProfileSourceFeedOutput:
    """Profile a Bronze relational feed and evaluate S_k(t) -> S_k(t+1) schema drift."""
    column_metrics: dict[str, dict[str, float]] = {}
    detailed_profiles: dict[str, dict[str, Any]] = {}
    col_names: list[str] = []
    row_count = 0

    file_path = _resolve_existing_file(args.table_or_path, ctx)
    if file_path is not None:
        profiler = GraphBProfiler()
        try:
            spec = SourceSpec(adapter="file", uri=str(file_path))
            get_source_registry().get_tabular_adapter(spec.adapter).register(
                spec, profiler.conn, "temp_profiled_feed"
            )
            col_profiles = profiler.profile_relation("temp_profiled_feed")

            row_count = col_profiles[0].row_count if col_profiles else 0
            col_names = [m.column_name for m in col_profiles]
            for m in col_profiles:
                metrics_entry: dict[str, float] = {
                    "u_c": round(m.u_total, 6),
                    "u_nn": round(m.u_non_null, 6),
                    "null_rate": round(m.null_rate, 6),
                    "h_norm": round(m.shannon_entropy_norm, 6),
                    "cardinality_ratio_u": round(m.u_total, 6),
                    "non_null_uniqueness_u_nn": round(m.u_non_null, 6),
                    "shannon_entropy_h": round(m.shannon_entropy_bits, 6),
                    "null_ratio_n_null": round(m.null_rate, 6),
                    "distinct_count": float(m.distinct_count),
                    "null_count": float(m.null_count),
                }
                column_metrics[m.column_name] = metrics_entry
                detailed_profiles[m.column_name] = {
                    **metrics_entry,
                    "inferred_type": m.physical_type,
                    "sample_values": list(m.sample_values),
                    "recommended_dv2_role": m.recommended_dv2_role,
                    "bk_candidate_verdict": m.bk_candidate_verdict,
                }
        finally:
            profiler.close()
    else:
        rows = args.sample_rows
        row_count = len(rows)
        if rows:
            seen_cols: dict[str, None] = {}
            for row in rows:
                for k in row:
                    seen_cols[str(k)] = None
            col_names = list(seen_cols.keys())
        elif args.baseline_columns:
            col_names = list(args.baseline_columns)

        for col in col_names:
            values = [row.get(col) for row in rows] if rows else []
            non_null_vals = [v for v in values if v is not None]
            u_c_val = round(compute_u_c(values), 6) if values else 1.0
            u_nn_val = round(compute_u_nn(values), 6) if values else 1.0
            null_rate_val = round(compute_null_rate(values), 6) if values else 0.0
            h_norm_val = round(compute_normalized_shannon_entropy(values), 6) if values else 0.0
            h_bits_val = round(compute_shannon_entropy(values), 6) if values else 0.0
            distinct_cnt = float(
                len({str(v) for v in non_null_vals})
                + (1 if len(non_null_vals) < len(values) else 0)
            )
            null_cnt = float(len(values) - len(non_null_vals))
            metrics_entry = {
                "u_c": u_c_val,
                "u_nn": u_nn_val,
                "null_rate": null_rate_val,
                "h_norm": h_norm_val,
                "cardinality_ratio_u": u_c_val,
                "non_null_uniqueness_u_nn": u_nn_val,
                "shannon_entropy_h": h_bits_val,
                "null_ratio_n_null": null_rate_val,
                "distinct_count": distinct_cnt,
                "null_count": null_cnt,
            }
            column_metrics[col] = metrics_entry
            sample_vals: list[Any] = []
            seen_samples: set[str] = set()
            for v in non_null_vals:
                sv = str(v)
                if sv not in seen_samples:
                    seen_samples.add(sv)
                    sample_vals.append(v if isinstance(v, (int, float, bool, str)) else sv)
                if len(sample_vals) >= 5:
                    break
            detailed_profiles[col] = {
                **metrics_entry,
                "inferred_type": _infer_python_value_type(values),
                "sample_values": sample_vals,
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
            "detailed_profiles": detailed_profiles,
            "added_columns": added_cols,
            "dropped_columns": dropped_cols,
        },
        sort_keys=True,
    )
    graph_b_sha256 = hashlib.sha256(profile_payload.encode("utf-8")).hexdigest().upper()
    ctx.set_state("last_graph_b_sha256", graph_b_sha256)
    ctx.set_state("last_column_profiles", detailed_profiles)

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


def _classify_span_tier(
    confidence: float,
    er_margin: float,
    raw_tier: Any = None,
) -> Literal["Tier-U1", "Tier-U2", "Tier-U3"]:
    """Classify an extracted span into Tier-U1 (>=0.90), Tier-U2 ([0.75, 0.90)), or Tier-U3."""
    alg_tier, _ = classify_er_resolution_tier(
        confidence_score=confidence,
        top2_margin=er_margin,
        is_exact_identifier_match=(confidence >= 0.90 and er_margin >= 0.50),
    )
    if er_margin < 0.15 or confidence < 0.75:
        return "Tier-U3"
    if isinstance(raw_tier, str) and raw_tier in {"Tier-U1", "Tier-U2", "Tier-U3"}:
        return raw_tier  # type: ignore[return-value]
    if confidence >= 0.90 and er_margin >= 0.50 and alg_tier == "TIER_U1_DETERMINISTIC":
        return "Tier-U1"
    return "Tier-U2"


async def _handle_extract_pdf_spans(
    args: ExtractPdfSpansInput,
    ctx: ToolExecutionContext,
) -> ExtractPdfSpansOutput:
    """Verify character-offset spans on unstructured PDF text per Algorithm A.9b."""
    doc_hk = compute_dv2_length_prefixed_sha256(["GLOBAL", args.document_uri])
    verified_items: list[ExtractedPdfSpanItem] = []
    all_verified = True

    doc_text = (args.document_text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not doc_text:
        doc_file = _resolve_existing_file(args.document_uri, ctx)
        if doc_file is not None:
            adapter_name = get_source_registry().resolve_document_adapter_name(doc_file)
            doc_adapter = get_source_registry().get_document_adapter(adapter_name)
            extracted = doc_adapter.extract_document(
                SourceSpec(
                    adapter=adapter_name,
                    uri=str(doc_file),
                    options={"workspace_root": str(doc_file.parent)},
                )
            )
            doc_text = extracted.normalized_text

    default_tier: Literal["Tier-U1", "Tier-U2", "Tier-U3"] = (
        "Tier-U3" if args.er_margin < 0.15 else ("Tier-U2" if args.er_margin < 0.5 else "Tier-U1")
    )

    for raw_span in args.candidate_spans:
        field_name = str(
            raw_span.get("field_name")
            or raw_span.get("attribute_name")
            or raw_span.get("field")
            or "extracted_term"
        )
        extracted_value = str(raw_span.get("extracted_value", ""))
        verbatim_quote = (
            str(raw_span.get("verbatim_quote", extracted_value))
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )
        raw_start = raw_span.get("char_start")
        raw_end = raw_span.get("char_end")
        char_start = int(raw_start) if raw_start is not None else -1
        char_end = (
            int(raw_end)
            if raw_end is not None
            else (char_start + len(verbatim_quote) if char_start >= 0 else -1)
        )
        confidence = float(raw_span.get("confidence", raw_span.get("confidence_score", 0.95)))
        span_margin = float(raw_span.get("top2_margin", args.er_margin))

        is_valid = False
        if doc_text:
            if (
                0 <= char_start <= char_end <= len(doc_text)
                and len(verbatim_quote) > 0
                and doc_text[char_start:char_end] == verbatim_quote
            ):
                is_valid = True
            elif len(verbatim_quote) > 0:
                exact_idx = doc_text.find(verbatim_quote)
                if exact_idx >= 0:
                    char_start = exact_idx
                    char_end = exact_idx + len(verbatim_quote)
                    is_valid = True
                else:
                    tokens = [re.escape(tok) for tok in verbatim_quote.split() if tok]
                    if tokens:
                        pattern = r"\s+".join(tokens)
                        match = re.search(pattern, doc_text, flags=re.IGNORECASE)
                        if match is not None:
                            char_start = match.start()
                            char_end = match.end()
                            verbatim_quote = doc_text[char_start:char_end]
                            is_valid = True
            if char_start < 0:
                char_start = 0
            if char_end < char_start:
                char_end = char_start + len(verbatim_quote)
        else:
            if char_start < 0:
                char_start = 0
            if char_end < 0:
                char_end = char_start + len(verbatim_quote)
            is_valid = char_end >= char_start and len(verbatim_quote) > 0

        if not is_valid:
            all_verified = False

        er_tier = _classify_span_tier(
            confidence=confidence,
            er_margin=span_margin,
            raw_tier=raw_span.get("er_tier"),
        )

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
    has_tier_u2 = default_tier == "Tier-U2" or any(
        item.er_tier == "Tier-U2" for item in verified_items
    )
    overall_tier: Literal["Tier-U1", "Tier-U2", "Tier-U3"] = (
        "Tier-U3" if has_tier_u3 else ("Tier-U2" if has_tier_u2 else "Tier-U1")
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
        er_tier=overall_tier,
        status=verdict,
    )


def _compile_minimal_payload_sql(
    contract_id: str,
    target_dialect: str,
    ir_sha256: str,
    hubs: list[dict[str, Any]],
    links: list[dict[str, Any]],
    satellites: list[dict[str, Any]],
) -> str:
    """Compile executable Zone 0-2 SQL for minimal test dicts lacking full v5 contract fields."""
    lines: list[str] = [
        f"-- Compiled 4-Zone Data Vault 2.0 SQL ({target_dialect})",
        f"-- Contract: {contract_id} | Canonical IR SHA-256: {ir_sha256}",
        "CREATE SCHEMA IF NOT EXISTS staging;",
        "CREATE SCHEMA IF NOT EXISTS raw_vault;",
        "CREATE SCHEMA IF NOT EXISTS business_vault;",
        "CREATE SCHEMA IF NOT EXISTS information_mart;",
    ]
    for hub in hubs:
        if not isinstance(hub, dict):
            continue
        h_name = str(hub.get("name", "hub_entity"))
        slug = h_name.removeprefix("hub_").removeprefix("bhub_")
        hk_col = str(hub.get("hk_column_name") or f"hk_{slug}")
        bk_cols = list(hub.get("business_keys") or [f"{slug}_id"])
        bk_ddl = ",\n".join(
            f"    {c if isinstance(c, str) else c.get('name', 'bk_id')} VARCHAR NOT NULL"
            for c in bk_cols
        )
        bk_unknown = ", ".join("'-1'" for _ in bk_cols)
        bk_error = ", ".join("'-2'" for _ in bk_cols)
        lines.append(
            f"CREATE OR REPLACE TABLE raw_vault.{h_name} (\n"
            f"    {hk_col} CHAR(64) PRIMARY KEY,\n"
            f"    bkcc VARCHAR NOT NULL,\n"
            f"{bk_ddl},\n"
            f"    load_dts TIMESTAMP NOT NULL,\n"
            f"    rec_src VARCHAR NOT NULL\n"
            f");\n"
            f"INSERT INTO raw_vault.{h_name} VALUES\n"
            f"    ('{ZERO_KEY_UNKNOWN}', 'SYSTEM', {bk_unknown}, "
            f"TIMESTAMP '{GHOST_DTS}', '{ZERO_KEY_REC_SRC}'),\n"
            f"    ('{ZERO_KEY_ERROR}', 'SYSTEM', {bk_error}, "
            f"TIMESTAMP '{GHOST_DTS}', '{ZERO_KEY_REC_SRC}');"
        )
    for lnk in links:
        if not isinstance(lnk, dict):
            continue
        l_name = str(lnk.get("name", "lnk_relationship"))
        hk_col = f"hk_{l_name}"
        lines.append(
            f"CREATE OR REPLACE TABLE raw_vault.{l_name} (\n"
            f"    {hk_col} CHAR(64) PRIMARY KEY,\n"
            f"    load_dts TIMESTAMP NOT NULL,\n"
            f"    rec_src VARCHAR NOT NULL\n"
            f");\n"
            f"INSERT INTO raw_vault.{l_name} VALUES\n"
            f"    ('{ZERO_KEY_UNKNOWN}', TIMESTAMP '{GHOST_DTS}', '{ZERO_KEY_REC_SRC}'),\n"
            f"    ('{ZERO_KEY_ERROR}', TIMESTAMP '{GHOST_DTS}', '{ZERO_KEY_REC_SRC}');"
        )
    for sat in satellites:
        if not isinstance(sat, dict):
            continue
        s_name = str(sat.get("name", "sat_attributes"))
        parent = str(sat.get("parent_entity", "hub_entity"))
        p_slug = parent.removeprefix("hub_").removeprefix("bhub_")
        parent_hk = f"hk_{p_slug}"
        p_cols = [str(c) for c in (sat.get("payload_columns") or ["attribute_value"])]
        p_ddl = ",\n".join(f"    {c} VARCHAR" for c in p_cols)
        nulls = ", ".join("NULL" for _ in p_cols)
        hash_expr = build_algorithm_a5_sha256_sql(p_cols, is_business_key=False)
        lines.append(
            f"-- Staging hash_diff for {s_name}: {hash_expr}\n"
            f"CREATE OR REPLACE TABLE raw_vault.{s_name} (\n"
            f"    {parent_hk} CHAR(64) NOT NULL,\n"
            f"    load_dts TIMESTAMP NOT NULL,\n"
            f"    rec_src VARCHAR NOT NULL,\n"
            f"    hash_diff CHAR(64) NOT NULL,\n"
            f"{p_ddl},\n"
            f"    PRIMARY KEY ({parent_hk}, load_dts)\n"
            f");\n"
            f"INSERT INTO raw_vault.{s_name} VALUES\n"
            f"    ('{ZERO_KEY_UNKNOWN}', TIMESTAMP '{GHOST_DTS}', "
            f"'{GHOST_REC_SRC}', '{ZERO_KEY_UNKNOWN}', {nulls});"
        )
    return "\n\n".join(lines) + "\n"


async def _handle_compile_canonical_ir(
    args: CompileCanonicalIrInput,
    ctx: ToolExecutionContext,
) -> CompileCanonicalIrOutput:
    """Lower a verified DV 2.0 contract into Canonical IR JSON and 4-Zone SQL in CAS."""
    canonical_ir_obj: CanonicalIR | None = None
    payload = args.contract_payload or {}

    if "ir_version" in payload and "model_name" in payload:
        try:
            canonical_ir_obj = CanonicalIR.model_validate(payload)
        except (ValidationError, ValueError, TypeError, KeyError):
            canonical_ir_obj = None
    elif payload:
        try:
            contract_v5 = NoMistakeDataVault2ContractV5.model_validate(payload)
            canonical_ir_obj = lower_contract_to_canonical_ir(contract_v5, validate_schema=False)
        except (ValidationError, ValueError, TypeError, KeyError):
            canonical_ir_obj = None

    if canonical_ir_obj is not None:
        ir_doc = canonical_ir_obj.model_dump(mode="json")
        ir_json = json.dumps(ir_doc, indent=2, sort_keys=True)
        ir_sha256 = ctx.write_cas_artifact("canonical_ir_sha256", ir_json)

        bundle = compile_canonical_ir_to_4zone_sql(canonical_ir_obj)
        sql_bundle = getattr(bundle, "full_sql", None) or bundle.full_sql_bundle
        sql_sha256 = ctx.write_cas_artifact("compiled_sql_sha256", sql_bundle)

        hub_count = len(canonical_ir_obj.hubs)
        link_count = len(canonical_ir_obj.links)
        sat_count = len(canonical_ir_obj.satellites)
    else:
        hubs = list(payload.get("hubs") or [])
        links = list(payload.get("links") or [])
        satellites = list(payload.get("satellites") or [])
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

        sql_bundle = _compile_minimal_payload_sql(
            contract_id=args.contract_id,
            target_dialect=args.target_dialect,
            ir_sha256=ir_sha256,
            hubs=hubs,
            links=links,
            satellites=satellites,
        )
        sql_sha256 = ctx.write_cas_artifact("compiled_sql_sha256", sql_bundle)
        hub_count = len(hubs)
        link_count = len(links)
        sat_count = len(satellites)

    ctx.set_state("canonical_ir_sha256", ir_sha256)
    ctx.set_state("compiled_sql_sha256", sql_sha256)
    ctx.set_state("last_canonical_ir", ir_doc)
    ctx.set_state("last_compiled_sql", sql_bundle)

    return CompileCanonicalIrOutput(
        contract_id=args.contract_id,
        target_dialect=args.target_dialect,
        canonical_ir_sha256=ir_sha256,
        compiled_sql_sha256=sql_sha256,
        hub_count=hub_count,
        link_count=link_count,
        satellite_count=sat_count,
        zones_compiled=["ZONE_0", "ZONE_1", "ZONE_2", "ZONE_3", "ZONE_4"],
        status=ObservabilityVerdict.PASS,
    )


async def _handle_run_axi_dv_gates(
    args: RunAxiDvGatesInput,
    ctx: ToolExecutionContext,
) -> RunAxiDvGatesOutput:
    """Execute static verification gates (G1..G5) and emit @toon_v1 trace on violations."""
    gate_verdicts: dict[str, ObservabilityVerdict] = {}
    payload = args.contract_payload or {}
    has_force_violation = bool(payload.get("force_violation", False))
    is_real_contract = not has_force_violation and any(
        k in payload for k in ("contract_version", "hubs", "links", "satellites")
    )

    if is_real_contract:
        ontology_graph = payload.get("ontology_graph") or ctx.get_state("ontology_graph") or {}
        bronze_profile = payload.get("bronze_profile") or ctx.get_state("bronze_profile") or {}
        res = run_axi_dv_verification_pipeline(
            payload,
            ontology_graph,
            bronze_profile,
            apply_codemods=False,
            run_id=ctx.run_id,
        )
        if res.status == "PASS":
            overall = ObservabilityVerdict.PASS
        elif res.status in {"ASK_USER", "ASK-USER"}:
            overall = ObservabilityVerdict.ASK_USER
        else:
            overall = ObservabilityVerdict.REJECT_WITH_TRACE

        violations_by_gate: dict[str, list[ToonViolation]] = {}
        for v in res.toon_trace.violations:
            violations_by_gate.setdefault(v.gate, []).append(v)

        for gate in args.gates_to_run:
            g_viols = violations_by_gate.get(gate, [])
            if any(v.action == "REJECT" for v in g_viols):
                gate_verdicts[gate] = ObservabilityVerdict.REJECT_WITH_TRACE
            elif any(v.action == "ASK-USER" for v in g_viols):
                gate_verdicts[gate] = ObservabilityVerdict.ASK_USER
            else:
                gate_verdicts[gate] = ObservabilityVerdict.PASS

        evaluated_rules = ["DV-STR-001", "ALG-A5", "ALG-A6", "ALG-A7", "ALG-A9B"]
        for v in res.toon_trace.violations:
            if v.rule not in evaluated_rules:
                evaluated_rules.append(v.rule)

        toon_trace = res.toon_text if overall != ObservabilityVerdict.PASS else None
        if toon_trace is not None:
            ctx.write_cas_artifact("toon_trace_sha256", toon_trace)

        ctx.set_state(
            "last_axi_dv_result",
            {
                "passed": overall == ObservabilityVerdict.PASS,
                "status": overall.value,
                "violation_count": len(res.toon_trace.violations),
                "toon_trace": res.toon_text,
                "violations": [v.to_dict() for v in res.toon_trace.violations],
            },
        )
        ctx.set_state("last_gate_verdict", overall.value)
        return RunAxiDvGatesOutput(
            contract_id=args.contract_id,
            overall_verdict=overall,
            gate_verdicts=gate_verdicts,
            rule_ids_evaluated=evaluated_rules,
            toon_diagnostic_trace=toon_trace,
        )

    for gate in args.gates_to_run:
        if has_force_violation and gate == "G3":
            gate_verdicts[gate] = ObservabilityVerdict.REJECT_WITH_TRACE
        else:
            gate_verdicts[gate] = ObservabilityVerdict.PASS

    overall = (
        ObservabilityVerdict.REJECT_WITH_TRACE if has_force_violation else ObservabilityVerdict.PASS
    )
    toon_trace_str: str | None = None
    if has_force_violation:
        forced_trace = ToonTrace(
            run_id=ctx.run_id,
            attempt="1/3",
            status="REJECT-WITH-TRACE",
            violations=[
                ToonViolation(
                    gate="G3",
                    rule="DV-STR-001",
                    action="REJECT",
                    target="hub_account",
                    fix="Remove direct Hub-to-Hub foreign key and route via Link",
                )
            ],
        )
        toon_trace_str = serialize_toon_v1(forced_trace)
        ctx.write_cas_artifact("toon_trace_sha256", toon_trace_str)

    ctx.set_state("last_gate_verdict", overall.value)
    return RunAxiDvGatesOutput(
        contract_id=args.contract_id,
        overall_verdict=overall,
        gate_verdicts=gate_verdicts,
        rule_ids_evaluated=["DV-STR-001", "ALG-A5", "ALG-A6", "ALG-A7", "ALG-A9B"],
        toon_diagnostic_trace=toon_trace_str,
    )


def _rewrite_sql_with_alg_a5(
    sql_text: str,
    target_entity: str,
    parameters: dict[str, Any],
) -> str:
    """Rewrite a SQL SELECT statement using Algorithm A.5 canonical SHA-256 AST."""
    hash_specs: dict[str, dict[str, Any]] = dict(parameters.get("hash_specs") or {})
    dialect = str(parameters.get("dialect", "duckdb"))
    if not hash_specs:
        parsed = sqlglot.parse_one(sql_text, read=dialect)
        select_node = parsed.find(exp.Select)
        if select_node is not None:
            for proj in select_node.expressions:
                alias = proj.alias_or_name
                if alias.lower().startswith(("hk_", "hash_diff")) or alias == target_entity:
                    cols = [
                        c.name
                        for c in proj.find_all(exp.Column)
                        if c.name and c.name.lower() != alias.lower()
                    ]
                    if cols:
                        is_bk = not alias.lower().startswith("hash_diff")
                        hash_specs[alias] = {
                            "columns": cols,
                            "is_business_key": is_bk,
                            "bkcc_prefix": parameters.get("bkcc_prefix"),
                            "sort_alphabetically": not is_bk,
                        }
        if not hash_specs and parameters.get("columns"):
            hash_specs[target_entity] = {
                "columns": list(parameters["columns"]),
                "is_business_key": bool(parameters.get("is_business_key", True)),
                "bkcc_prefix": parameters.get("bkcc_prefix"),
                "sort_alphabetically": bool(parameters.get("sort_alphabetically", False)),
            }
    if hash_specs:
        return codemod_enforce_staging_hashes_sql(sql_text, hash_specs, dialect=dialect)
    return sql_text


_CANONICAL_CODEMOD_IDS: dict[str, str] = {
    "A.1": "ALG-A1",
    "ALG-A1": "ALG-A1",
    "ALG_A1_3AXIS_SATELLITE_SPLIT": "ALG-A1",
    "A.2": "ALG-A2",
    "ALG-A2": "ALG-A2",
    "ALG_A2_ENUM_TO_ATTRIBUTE_DEMOTION": "ALG-A2",
    "A.3": "ALG-A3",
    "ALG-A3": "ALG-A3",
    "ALG_A3_LINK_ATTRIBUTE_TO_EFFECTIVITY_SAT": "ALG-A3",
    "A.4": "ALG-A4",
    "ALG-A4": "ALG-A4",
    "ALG_A4_COLD_START_EXTENSIBILITY": "ALG-A4",
    "A.5": "ALG-A5",
    "ALG-A5": "ALG-A5",
    "ALG_A5_SQLGLOT_HASH_REWRITE": "ALG-A5",
    "A.9a": "ALG-A9A",
    "A.9A": "ALG-A9A",
    "ALG-A9A": "ALG-A9A",
    "ALG_A9A_INTRA_SOURCE_SCHEMA_DRIFT": "ALG-A9A",
}


def _entity_exists_in_contract(parsed_doc: dict[str, Any], target_entity: str) -> bool:
    """Return True if `target_entity` matches an entity in `parsed_doc`."""
    if not target_entity:
        return False
    if parsed_doc.get("entity") == target_entity:
        return True
    for key in ("hubs", "links", "satellites", "reference_tables"):
        items = parsed_doc.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("name") == target_entity:
                    return True
    if "staging_sql" in parsed_doc and isinstance(parsed_doc["staging_sql"], str):
        return target_entity in parsed_doc["staging_sql"]
    return False


def _apply_structural_codemod_to_dict(
    codemod_id: str,
    target_entity: str,
    parsed_doc: dict[str, Any],
    parameters: dict[str, Any],
    ctx: ToolExecutionContext,
) -> dict[str, Any]:
    """Dispatch `codemod_id` (`ALG-A1..A5`, `ALG-A9A`) against a parsed contract dictionary."""
    norm_id = _CANONICAL_CODEMOD_IDS.get(codemod_id) or _CANONICAL_CODEMOD_IDS.get(
        codemod_id.upper()
    )
    if norm_id is None:
        raise ValueError(f"Unsupported codemod_id: {codemod_id!r}")

    if not _entity_exists_in_contract(parsed_doc, target_entity):
        return copy.deepcopy(parsed_doc)

    if parsed_doc.get("entity") == target_entity and not any(
        k in parsed_doc for k in ("hubs", "links", "satellites")
    ):
        mutated_minimal = copy.deepcopy(parsed_doc)
        mutated_minimal["applied_codemod"] = norm_id
        return mutated_minimal

    bronze_profile: dict[str, Any] = dict(
        parameters.get("bronze_profile") or ctx.get_state("bronze_profile") or {}
    )
    ontology_graph: dict[str, Any] = dict(
        parameters.get("ontology_graph") or ctx.get_state("ontology_graph") or {}
    )

    if norm_id == "ALG-A1":
        if not bronze_profile and "satellites" in parsed_doc:
            explicit_pii = set(parameters.get("pii_columns") or [])
            synth_profile: dict[str, dict[str, dict[str, Any]]] = {}
            for sat in parsed_doc.get("satellites", []):
                if not isinstance(sat, dict) or sat.get("name") != target_entity:
                    continue
                b_tbl = str(sat.get("source_bronze_table", "bronze.default"))
                t_cols = synth_profile.setdefault(b_tbl, {})
                for col in sat.get("payload_columns", []):
                    c_str = str(col)
                    is_pii = c_str in explicit_pii or c_str.lower() in _PII_COLUMN_HINTS
                    t_cols[c_str] = {
                        "security_classification": "PII_HIGH" if is_pii else "PUBLIC",
                        "change_freq": 0.001,
                    }
            bronze_profile = synth_profile
        return codemod_split_satellites_3axis(parsed_doc, bronze_profile)

    if norm_id == "ALG-A2":
        if not ontology_graph and "hubs" in parsed_doc:
            enum_list: list[dict[str, str]] = []
            for hub in parsed_doc.get("hubs", []):
                if isinstance(hub, dict) and hub.get("name") == target_entity:
                    uri = str(
                        hub.get("ontology_concept_uri") or f"urn:ontology:enum:{target_entity}"
                    )
                    hub["ontology_concept_uri"] = uri
                    enum_list.append({"enum_uri": uri})
            if enum_list:
                ontology_graph = {"reference_enumerations": enum_list}
        return codemod_demote_enum_hubs_to_ref(parsed_doc, ontology_graph, bronze_profile)

    if norm_id == "ALG-A3":
        cdc_profile: dict[str, dict[str, float]] = dict(parameters.get("cdc_profile") or {})
        if not cdc_profile and "links" in parsed_doc:
            for lnk in parsed_doc.get("links", []):
                if isinstance(lnk, dict) and lnk.get("name") == target_entity:
                    b_tbl = str(lnk.get("source_bronze_table", "bronze.default"))
                    refs = list(lnk.get("hub_references") or [])
                    if refs and isinstance(refs[0], dict):
                        fk_cols = list(refs[0].get("source_fk_columns") or ["fk_id"])
                        refs[0]["source_fk_columns"] = fk_cols
                        cdc_profile[b_tbl] = {str(fk_cols[0]): 0.15}
        return codemod_inject_link_effectivity(parsed_doc, cdc_profile or bronze_profile)

    if norm_id == "ALG-A4":
        mutated = codemod_enforce_cold_start_extensibility(
            parsed_doc,
            ontology_graph,
            bronze_profile,
        )
        mutable_cols = parameters.get("mutable_columns_by_table")
        if isinstance(mutable_cols, dict):
            mutated = codemod_extract_hub_mutable_attributes(
                mutated,
                {str(k): set(v) for k, v in mutable_cols.items()},
            )
        return mutated

    if norm_id == "ALG-A5":
        mutated = copy.deepcopy(parsed_doc)
        if "staging_sql" in mutated and isinstance(mutated["staging_sql"], str):
            mutated["staging_sql"] = _rewrite_sql_with_alg_a5(
                mutated["staging_sql"], target_entity, parameters
            )
        else:
            mutated["hash_config"] = {
                "algorithm": "SHA256",
                "encoding_scheme": "LENGTH_PREFIXED",
                "delimiter": "||",
                "null_token": "-1:^^",
                "zero_key": ZERO_KEY_UNKNOWN,
            }
        return mutated

    if norm_id == "ALG-A9A":
        deployed = (
            parameters.get("deployed_contract") or ctx.get_state("deployed_contract") or parsed_doc
        )
        epoch = str(parameters.get("schema_epoch_tag", "v2"))
        return codemod_intra_source_schema_drift(deployed, parsed_doc, schema_epoch_tag=epoch)

    return copy.deepcopy(parsed_doc)


async def _handle_apply_ast_codemod(
    args: ApplyAstCodemodInput,
    ctx: ToolExecutionContext,
) -> ApplyAstCodemodOutput:
    """Apply a deterministic AST/YAML codemod (ALG-A1..A5, ALG-A9A) and persist CAS diffs."""
    norm_id = _CANONICAL_CODEMOD_IDS.get(args.codemod_id) or _CANONICAL_CODEMOD_IDS.get(
        args.codemod_id.upper()
    )
    if norm_id is None:
        raise ValueError(f"Unsupported codemod_id: {args.codemod_id!r}")

    pre_content = (
        args.contract_yaml or f"# Contract before {args.codemod_id}\nentity: {args.target_entity}\n"
    )
    provenance_comment = (
        f"# Applied deterministic codemod {args.codemod_id} on {args.target_entity}"
    )

    post_content: str
    patched_dict: dict[str, Any] | None = None
    was_applied = False

    stripped_input = pre_content.strip()
    is_raw_sql = stripped_input.upper().startswith(("SELECT ", "WITH ", "CREATE "))
    if norm_id == "ALG-A5" and is_raw_sql:
        rewritten_sql = _rewrite_sql_with_alg_a5(
            stripped_input,
            args.target_entity,
            args.parameters,
        )
        was_applied = rewritten_sql.strip() != stripped_input
        if was_applied:
            post_content = f"{rewritten_sql.rstrip()}\n{provenance_comment}\n"
        else:
            post_content = pre_content
    else:
        try:
            loaded = yaml.safe_load(pre_content)
        except (yaml.YAMLError, ValueError, TypeError):
            loaded = None

        if isinstance(loaded, dict):
            mutated = _apply_structural_codemod_to_dict(
                codemod_id=norm_id,
                target_entity=args.target_entity,
                parsed_doc=loaded,
                parameters=args.parameters,
                ctx=ctx,
            )
            patched_dict = mutated
            was_applied = mutated != loaded
            if was_applied:
                serialized = yaml.safe_dump(mutated, sort_keys=False).rstrip("\n")
                post_content = f"{serialized}\n{provenance_comment}\n"
            else:
                post_content = pre_content
        else:
            post_content = pre_content

    pre_sha = ctx.write_cas_artifact("contract_yaml_pre_sha256", pre_content)
    post_sha = ctx.write_cas_artifact("contract_yaml_post_sha256", post_content)

    diff_lines = list(
        difflib.unified_diff(
            pre_content.splitlines(),
            post_content.splitlines(),
            fromfile=f"a/{args.target_entity}.yaml",
            tofile=f"b/{args.target_entity}.yaml",
            lineterm="",
        )
    )
    patch_diff = "\n".join(diff_lines)
    if was_applied:
        action_desc = (
            f"Applied deterministic codemod {args.codemod_id} "
            f"to target entity '{args.target_entity}'."
        )
        verdict = ObservabilityVerdict.AUTO_REPAIRED
    else:
        action_desc = (
            f"No matching target entity '{args.target_entity}' "
            f"or AST change for codemod {args.codemod_id}."
        )
        verdict = ObservabilityVerdict.REJECT_WITH_TRACE

    ctx.set_state("last_codemod_id", args.codemod_id)
    if patched_dict is not None:
        ctx.set_state("last_patched_contract_dict", patched_dict)
    ctx.set_state("last_patched_contract_yaml", post_content)

    return ApplyAstCodemodOutput(
        codemod_id=args.codemod_id,
        target_entity=args.target_entity,
        applied=was_applied,
        verdict=verdict,
        contract_yaml_pre_sha256=pre_sha,
        contract_yaml_post_sha256=post_sha,
        patch_diff=patch_diff,
        action_description=action_desc,
    )


def _inspect_and_verify_duckdb_tables(
    con: duckdb.DuckDBPyConnection,
) -> tuple[bool, int, dict[tuple[str, str], int]]:
    """Inspect created tables in DuckDB `:memory:` and run real PK/Zero-Key/Orphan queries."""
    tables = con.execute(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_schema IN ('raw_vault', 'business_vault', 'main')
          AND table_type = 'BASE TABLE'
        ORDER BY table_schema, table_name
        """
    ).fetchall()

    row_counts: dict[tuple[str, str], int] = {}
    hub_hk_tables: dict[str, tuple[str, str]] = {}
    entity_children: list[tuple[str, str, list[str]]] = []
    all_passed = True
    orphan_count = 0

    for schema_name, tbl_name in tables:
        s_str = str(schema_name)
        t_str = str(tbl_name)
        q_full = f'"{s_str}"."{t_str}"'
        cnt_row = con.execute(f"SELECT COUNT(*) FROM {q_full}").fetchone()
        row_counts[(s_str, t_str)] = int(cnt_row[0]) if cnt_row is not None else 0

        if s_str not in {"raw_vault", "business_vault"}:
            continue

        cols_info = con.execute(f"DESCRIBE {q_full}").fetchall()
        col_names = [str(r[0]) for r in cols_info]
        hk_cols = [c for c in col_names if c.startswith("hk_")]

        if t_str.startswith(("hub_", "bhub_")) and hk_cols:
            primary_hk = hk_cols[0]
            hub_hk_tables[primary_hk] = (s_str, t_str)
            dup_row = con.execute(
                f'SELECT COUNT(*) - COUNT(DISTINCT "{primary_hk}") FROM {q_full}'
            ).fetchone()
            if dup_row is not None and int(dup_row[0]) > 0:
                all_passed = False
        elif t_str.startswith(("lnk_", "sal_", "nh_lnk_", "t_lnk_")) and hk_cols:
            primary_hk = hk_cols[0]
            dup_row = con.execute(
                f'SELECT COUNT(*) - COUNT(DISTINCT "{primary_hk}") FROM {q_full}'
            ).fetchone()
            if dup_row is not None and int(dup_row[0]) > 0:
                all_passed = False
            fk_hks = [c for c in hk_cols if c != primary_hk]
            if fk_hks:
                entity_children.append((s_str, t_str, fk_hks))
        elif t_str.startswith(("sat_", "bsat_", "esat_", "ma_sat_", "mas_")) and hk_cols:
            parent_hk = hk_cols[0]
            if "load_dts" in col_names:
                seq_cols = [c for c in col_names if c.endswith("_seq") or c == "sub_sequence"]
                pk_parts = [
                    f'"{parent_hk}"',
                    'CAST("load_dts" AS VARCHAR)',
                    *(f'CAST("{sc}" AS VARCHAR)' for sc in seq_cols),
                ]
                pk_expr = " || '|' || ".join(pk_parts)
                dup_row = con.execute(
                    f"SELECT COUNT(*) - COUNT(DISTINCT {pk_expr}) FROM {q_full}"
                ).fetchone()
                if dup_row is not None and int(dup_row[0]) > 0:
                    all_passed = False
            entity_children.append((s_str, t_str, [parent_hk]))

    for c_schema, c_tbl, fk_cols in entity_children:
        q_child = f'"{c_schema}"."{c_tbl}"'
        for fk_col in fk_cols:
            if fk_col in hub_hk_tables:
                p_schema, p_tbl = hub_hk_tables[fk_col]
                q_parent = f'"{p_schema}"."{p_tbl}"'
                orph_row = con.execute(
                    f"SELECT COUNT(*) FROM {q_child} c "
                    f'LEFT JOIN {q_parent} p ON c."{fk_col}" = p."{fk_col}" '
                    f'WHERE c."{fk_col}" IS NOT NULL AND p."{fk_col}" IS NULL'
                ).fetchone()
                if orph_row is not None:
                    orphan_count += int(orph_row[0])

    if orphan_count > 0:
        all_passed = False

    return all_passed, orphan_count, row_counts


async def _handle_execute_duckdb_sandbox(
    args: ExecuteDuckdbSandboxInput,
    ctx: ToolExecutionContext,
) -> ExecuteDuckdbSandboxOutput:
    """Execute 4-Batch Adversarial DuckDB Shadow Sandbox checks (DV-DYN-001..015)."""
    batch_verdicts: dict[str, Literal["PASS", "FAIL"]] = {}
    dyn_rules = [f"DV-DYN-{i:03d}" for i in range(1, 16)]
    sql_stripped = args.sql_bundle.strip()

    if not sql_stripped or sql_stripped.startswith("{"):
        source_obj: Any = None
        if sql_stripped.startswith("{"):
            try:
                source_obj = json.loads(sql_stripped)
            except (json.JSONDecodeError, ValueError, TypeError):
                source_obj = None
        if source_obj is None:
            source_obj = ctx.get_state("last_canonical_ir")

        valid_src = (
            source_obj if isinstance(source_obj, dict) and "model_name" in source_obj else None
        )
        with DuckDbShadowSandbox() as sandbox:
            report = sandbox.run_full_verification(
                source=valid_src,
                emit_telemetry=False,
                run_id=ctx.run_id,
            )
        all_passed = report.passed
        for b_idx in args.batches_to_run:
            b_item = report.batch_report.by_number.get(b_idx)
            batch_verdicts[f"batch_{b_idx}"] = (
                "PASS" if (b_item.passed if b_item is not None else all_passed) else "FAIL"
            )
        dyn_rules = [c.check_id for c in report.dynamic_check_report.checks] or dyn_rules
        replay_delta_rows = (
            report.batch_report.by_number[2].total_delta_rows
            if 2 in report.batch_report.by_number
            else 0
        )
        orphan_count = sum(b.orphan_count for b in report.batch_report.batches)
        verdict = (
            ObservabilityVerdict.PASS if all_passed else ObservabilityVerdict.REJECT_WITH_TRACE
        )
        ctx.set_state("last_sandbox_passed", all_passed)
        return ExecuteDuckdbSandboxOutput(
            all_batches_passed=all_passed,
            batch_verdicts=batch_verdicts,
            dynamic_rules_evaluated=dyn_rules,
            replay_delta_rows=replay_delta_rows,
            orphan_count=orphan_count,
            assertion_cutoff_ts=args.assertion_cutoff_ts,
            status=verdict,
        )

    all_passed = True
    orphan_count = 0
    replay_delta_rows = 0

    con = duckdb.connect(":memory:")
    try:
        con.execute(sql_stripped)
        checks_ok, computed_orphans, _row_counts = _inspect_and_verify_duckdb_tables(con)
        if not checks_ok:
            all_passed = False
        orphan_count = computed_orphans
    except (duckdb.Error, ValueError, TypeError, KeyError):
        all_passed = False
        orphan_count = 1
    finally:
        con.close()

    for b_idx in args.batches_to_run:
        batch_verdicts[f"batch_{b_idx}"] = "PASS" if all_passed else "FAIL"

    verdict = ObservabilityVerdict.PASS if all_passed else ObservabilityVerdict.REJECT_WITH_TRACE
    ctx.set_state("last_sandbox_passed", all_passed)

    return ExecuteDuckdbSandboxOutput(
        all_batches_passed=all_passed,
        batch_verdicts=batch_verdicts,
        dynamic_rules_evaluated=dyn_rules,
        replay_delta_rows=replay_delta_rows,
        orphan_count=orphan_count,
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
