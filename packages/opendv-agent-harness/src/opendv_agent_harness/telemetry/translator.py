"""Real-time translation of HarnessStreamEvent and tool outputs into VaultObservabilityEvent.

Conforms to docs/06-explainability-and-observability-spec.md (§1.1)
and schemas/vault-observability-event.schema.json.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from opendv_agent_harness.models import (
    AgentPersona,
    DataClassification,
    HarnessEventKind,
    HarnessStreamEvent,
    ObservabilityVerdict,
    PipelineStageId,
)
from opendv_core.models.observability import RULE_ID_RE, VaultObservabilityEvent
from opendv_core.schema_registry import validate_observability_event
from opendv_telemetry.ledger_emitter import VaultLedgerEmitter
from opendv_telemetry.pii_detector import validate_no_pii_leaks

# Alias for compatibility with callers referencing JsonlObservabilityLedger
JsonlObservabilityLedger = VaultLedgerEmitter

_RAW_HEX64_RE = re.compile(r"\b([0-9A-Fa-f]{64})\b")

_ALLOWED_CONTEXT_REF_KEYS: frozenset[str] = frozenset(
    {
        "graph_a_sha256",
        "graph_b_sha256",
        "contract_yaml_pre_sha256",
        "contract_yaml_post_sha256",
        "canonical_ir_sha256",
        "compiled_sql_sha256",
        "toon_trace_sha256",
    }
)

_HEX64_EXACT_RE = re.compile(r"^[0-9A-Fa-f]{64}$")


def _scrub_layer1_text(text: str, max_len: int) -> str:
    """Replace any raw 64-hex digests in Layer 1 text with compact hash pills and cap length."""
    scrubbed = _RAW_HEX64_RE.sub(
        lambda m: f"[HK: {m.group(1)[:4].upper()}..{m.group(1)[-4:].upper()}]",
        text.strip(),
    )
    if not scrubbed:
        scrubbed = "Deterministic agent harness telemetry event recorded."
    if len(scrubbed) > max_len:
        return scrubbed[: max_len - 3] + "..."
    return scrubbed


def _normalize_rule_ids(rule_ids: list[str] | None) -> list[str]:
    """Filter or default rule IDs so every entry conforms to RULE_ID_RE."""
    if not rule_ids:
        return ["DV-STR-001"]
    valid = [str(r) for r in rule_ids if RULE_ID_RE.match(str(r))]
    return valid if valid else ["DV-STR-001"]


def _normalize_context_refs(raw_refs: dict[str, Any] | None) -> dict[str, str] | None:
    """Filter context_refs to valid 64-hex SHA-256 artifact references."""
    if not raw_refs:
        return None
    cleaned: dict[str, str] = {}
    for k, v in raw_refs.items():
        if k in _ALLOWED_CONTEXT_REF_KEYS and isinstance(v, str) and _HEX64_EXACT_RE.match(v):
            cleaned[k] = v.upper()
    return cleaned if cleaned else None


def build_observability_event_dict(
    *,
    event_id: str,
    event_type: str | None = None,
    run_id: str,
    trace_id: str | None,
    span_id: str | None,
    parent_span_id: str | None = None,
    timestamp_utc: str | None = None,
    stage_id: PipelineStageId | str = PipelineStageId.STAGE_00_ORCHESTRATION,
    agent_persona: AgentPersona | str = AgentPersona.VAULT_FIRST_MATE,
    status: ObservabilityVerdict | str = ObservabilityVerdict.PASS,
    headline: str = "Agent harness telemetry checkpoint recorded.",
    business_impact: str = (
        "Maintains auditable W3C trace lineage across the neuro-symbolic pipeline."
    ),
    data_classification: DataClassification | str = DataClassification.INTERNAL,
    rule_ids_evaluated: list[str] | None = None,
    summary_metrics: dict[str, int | float | str | bool] | None = None,
    semantic_hash_pills: list[dict[str, Any]] | None = None,
    remediation: dict[str, Any] | None = None,
    context_refs: dict[str, Any] | None = None,
    layer3_formal_proof: dict[str, Any] | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    """Construct and validate a schema-compliant VaultObservabilityEvent dictionary."""
    status_str = str(ObservabilityVerdict(status).value)
    stage_str = str(PipelineStageId(stage_id).value)
    persona_str = str(AgentPersona(agent_persona).value)
    classification_str = str(DataClassification(data_classification).value)

    ts_str = timestamp_utc or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    safe_headline = _scrub_layer1_text(headline, 140)
    safe_impact = _scrub_layer1_text(business_impact, 200)

    clean_metrics: dict[str, int | float | str | bool] = {}
    if summary_metrics:
        for k, v in summary_metrics.items():
            if isinstance(v, bool | int | float | str):
                clean_metrics[str(k)] = v
            else:
                clean_metrics[str(k)] = str(v)

    event_payload: dict[str, Any] = {
        "event_id": event_id,
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "timestamp_utc": ts_str,
        "stage_id": stage_str,
        "agent_persona": persona_str,
        "status": status_str,
        "rule_ids_evaluated": _normalize_rule_ids(rule_ids_evaluated),
        "data_classification": classification_str,
        "layer1_story_banner": {
            "headline": safe_headline,
            "business_impact": safe_impact,
            "verdict_pill": status_str,
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": list(semantic_hash_pills or []),
            "summary_metrics": clean_metrics,
        },
    }

    if event_type is not None:
        event_payload["event_type"] = event_type

    if confidence is not None:
        event_payload["confidence"] = float(confidence)

    if status_str == ObservabilityVerdict.AUTO_REPAIRED.value:
        rem = remediation or {
            "codemod_type": "ALG-A1",
            "target_entity": "vault_contract",
            "patch_diff": (
                "--- a/contract.yaml\n+++ b/contract.yaml\n@@ -1 +1 @@\n-split: true\n+split: false"
            ),
            "action_description": "Applied deterministic AST codemod repair.",
        }
        event_payload["remediation"] = rem
    elif remediation is not None:
        event_payload["remediation"] = remediation

    cleaned_refs = _normalize_context_refs(context_refs)
    if cleaned_refs is not None:
        event_payload["context_refs"] = cleaned_refs

    if layer3_formal_proof is not None:
        allowed_l3_keys = {
            "algorithm_ref",
            "formula_latex",
            "toon_diagnostic_payload",
            "contract_yaml_diff",
            "sqlglot_ast_summary",
        }
        filtered_l3 = {
            k: str(v)
            for k, v in layer3_formal_proof.items()
            if k in allowed_l3_keys and v is not None
        }
        if filtered_l3:
            event_payload["layer3_formal_proof"] = filtered_l3

    # Validate against Pydantic model, Draft 2020-12 JSON Schema, and Layer 1 PII leak scanner
    VaultObservabilityEvent.model_validate(event_payload)
    validate_observability_event(event_payload)
    validate_no_pii_leaks(event_payload)
    return event_payload


def translate_stream_event_to_observability(
    stream_event: HarnessStreamEvent,
    *,
    ledger: VaultLedgerEmitter | Any | None = None,
    default_classification: DataClassification = DataClassification.INTERNAL,
) -> dict[str, Any]:
    """Translate a HarnessStreamEvent into a schema-valid VaultObservabilityEvent dict.

    If `ledger` is provided (e.g., `VaultLedgerEmitter` / `JsonlObservabilityLedger`),
    the validated record is immediately appended to the JSONL ledger.
    """
    if isinstance(stream_event.observability_event, dict) and stream_event.observability_event:
        raw = copy.deepcopy(stream_event.observability_event)
        banner = raw.get("layer1_story_banner") or {}
        layer2 = raw.get("layer2_visual_payload") or {}
        raw_status = raw.get(
            "status",
            ObservabilityVerdict.ASK_USER.value
            if stream_event.kind == HarnessEventKind.STEWARD_HOLD
            else ObservabilityVerdict.PASS.value,
        )
        validated = build_observability_event_dict(
            event_id=str(raw.get("event_id") or stream_event.event_id),
            run_id=str(raw.get("run_id") or stream_event.run_id),
            trace_id=str(raw.get("trace_id") or stream_event.trace_id),
            span_id=str(raw.get("span_id") or stream_event.span_id),
            parent_span_id=raw.get("parent_span_id", stream_event.parent_span_id),
            timestamp_utc=str(raw.get("timestamp_utc") or stream_event.timestamp_utc),
            stage_id=raw.get("stage_id") or stream_event.stage_id,
            agent_persona=raw.get("agent_persona") or stream_event.persona,
            status=raw_status,
            headline=str(
                banner.get("headline")
                or f"[{stream_event.persona.value}] {stream_event.kind.value} recorded."
            ),
            business_impact=str(
                banner.get("business_impact")
                or "Deterministic neuro-symbolic pipeline telemetry event."
            ),
            data_classification=raw.get("data_classification") or default_classification,
            rule_ids_evaluated=raw.get("rule_ids_evaluated"),
            summary_metrics=layer2.get("summary_metrics") or {},
            semantic_hash_pills=layer2.get("semantic_hash_pills") or [],
            remediation=raw.get("remediation"),
            context_refs=raw.get("context_refs"),
            layer3_formal_proof=raw.get("layer3_formal_proof"),
            confidence=raw.get("confidence"),
        )
    else:
        status = ObservabilityVerdict.PASS
        if stream_event.kind == HarnessEventKind.STEWARD_HOLD:
            status = ObservabilityVerdict.ASK_USER
        elif stream_event.kind == HarnessEventKind.TOOL_CALL_ERROR:
            status = ObservabilityVerdict.REJECT_WITH_TRACE
        elif isinstance(stream_event.tool_result, dict):
            res_status = stream_event.tool_result.get("status") or stream_event.tool_result.get(
                "verdict"
            )
            if isinstance(res_status, str) and res_status in {
                v.value for v in ObservabilityVerdict
            }:
                status = ObservabilityVerdict(res_status)

        if stream_event.kind == HarnessEventKind.TOOL_CALL_START:
            headline = (
                f"Agent '{stream_event.persona.value}' invoked domain tool "
                f"'{stream_event.tool_name or 'unknown'}'."
            )
            impact = "Executes deterministic domain verification or compilation step."
        elif stream_event.kind == HarnessEventKind.TOOL_CALL_RESULT:
            headline = (
                f"Domain tool '{stream_event.tool_name or 'tool'}' completed with "
                f"verdict {status.value}."
            )
            impact = "Propagates verified structured tool output into the pipeline context."
        elif stream_event.kind == HarnessEventKind.TOOL_CALL_ERROR:
            headline = f"Tool execution error encountered by '{stream_event.persona.value}'."
            impact = "Halts unverified mutation and triggers diagnostic error handling."
        elif stream_event.kind == HarnessEventKind.STEWARD_HOLD:
            headline = (
                f"Steward Captain-Hold requested by '{stream_event.persona.value}' "
                f"at {stream_event.stage_id.value}."
            )
            impact = "Parks pipeline execution until human steward resolves semantic ambiguity."
        elif stream_event.kind in {HarnessEventKind.TEXT_DELTA, HarnessEventKind.THOUGHT_DELTA}:
            snippet = (stream_event.delta_text or "").strip().replace("\n", " ")
            headline = (
                f"[{stream_event.kind.value}] {snippet[:90]}"
                if snippet
                else f"Agent '{stream_event.persona.value}' emitted {stream_event.kind.value}."
            )
            impact = "Streams real-time agent reasoning and synthesis progress to the workbench."
        else:
            headline = (
                f"Harness event {stream_event.kind.value} emitted by "
                f"'{stream_event.persona.value}' ({stream_event.provider})."
            )
            impact = "Records lifecycle state transition in the immutable observability ledger."

        metrics: dict[str, int | float | str | bool] = {
            "harness_event_kind": stream_event.kind.value,
            "provider": stream_event.provider,
            "task_id": stream_event.task_id,
        }
        if stream_event.tool_name:
            metrics["tool_name"] = stream_event.tool_name
        if stream_event.tool_call_id:
            metrics["tool_call_id"] = stream_event.tool_call_id

        validated = build_observability_event_dict(
            event_id=stream_event.event_id,
            run_id=stream_event.run_id,
            trace_id=stream_event.trace_id,
            span_id=stream_event.span_id,
            parent_span_id=stream_event.parent_span_id,
            timestamp_utc=stream_event.timestamp_utc,
            stage_id=stream_event.stage_id,
            agent_persona=stream_event.persona,
            status=status,
            headline=headline,
            business_impact=impact,
            data_classification=default_classification,
            summary_metrics=metrics,
        )

    if ledger is not None:
        if hasattr(ledger, "emit"):
            ledger.emit(validated)
        elif hasattr(ledger, "append"):
            ledger.append(validated)

    return validated


class HarnessTelemetryTranslator:
    """Stateful/stateless adapter translating HarnessStreamEvents into ledger records."""

    def __init__(
        self,
        ledger: VaultLedgerEmitter | Any | None = None,
        *,
        default_classification: DataClassification = DataClassification.INTERNAL,
    ) -> None:
        self.ledger = ledger
        self.default_classification = default_classification

    def translate(self, event: HarnessStreamEvent) -> dict[str, Any]:
        """Convert a HarnessStreamEvent into a validated VaultObservabilityEvent dict."""
        return translate_stream_event_to_observability(
            event,
            ledger=None,
            default_classification=self.default_classification,
        )

    def record(self, event: HarnessStreamEvent) -> dict[str, Any]:
        """Translate a HarnessStreamEvent and append it to the configured ledger if present."""
        return translate_stream_event_to_observability(
            event,
            ledger=self.ledger,
            default_classification=self.default_classification,
        )

    def record_many(self, events: Iterable[HarnessStreamEvent]) -> list[dict[str, Any]]:
        """Translate and record a sequence of HarnessStreamEvents."""
        return [self.record(ev) for ev in events]


__all__ = [
    "HarnessTelemetryTranslator",
    "JsonlObservabilityLedger",
    "build_observability_event_dict",
    "translate_stream_event_to_observability",
]
