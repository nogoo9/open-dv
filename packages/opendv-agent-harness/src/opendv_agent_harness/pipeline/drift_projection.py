"""Stage 01-02 structured UF-1..UF-5 schema drift projection & pause-context persistence."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from opendv_agent_harness.models import (
    HarnessEventKind,
    HarnessStreamEvent,
    ObservabilityVerdict,
    PipelineStageId,
)
from opendv_agent_harness.pipeline.config import HarnessSetupConfig
from opendv_agent_harness.telemetry.translator import build_observability_event_dict
from opendv_axi_verifier.codemods.alg_a9a_schema_drift import (
    detect_intra_source_schema_drift,
    reconstruct_baseline_metadata_from_contract,
)

_CATEGORY_TO_UF: dict[str, tuple[str, str, str]] = {
    "ADDITIVE": ("UF-1", "ADDITIVE_COLUMN", "SPLIT_SATELLITE"),
    "TYPE_WIDENED": ("UF-1", "TYPE_WIDENED", "WIDEN_IN_ZONE1"),
    "RENAMED": ("UF-2", "COLUMN_RENAME", "ALIAS_IN_ZONE1"),
    "SOFT_DEPRECATED": ("UF-3", "COLUMN_REMOVAL", "SOFT_DEPRECATE_NULL"),
    "KEY_DRIFT": ("UF-4", "KEY_DRIFT", "ASK_STEWARD_KEY_DRIFT"),
}


def project_stage_drift_findings(
    curr_contract: dict[str, Any] | None,
    discovered_feeds: list[Any] | tuple[Any, ...],
) -> list[dict[str, Any]]:
    """Detect structured UF-1..UF-5 drift findings comparing feeds against ``curr_contract``."""
    if not curr_contract or not discovered_feeds:
        return []

    projected: list[dict[str, Any]] = []
    for feed in discovered_feeds:
        table_name = str(
            feed.get("table_name") if isinstance(feed, dict) else getattr(feed, "table_name", "")
        ).strip()
        source_sys = str(
            feed.get("source_system")
            if isinstance(feed, dict)
            else getattr(feed, "source_system", "")
        ).strip()
        if not table_name:
            continue
        qualified = f"{source_sys}.{table_name}" if source_sys else table_name

        col_descs = (
            feed.get("column_descriptors", [])
            if isinstance(feed, dict)
            else getattr(feed, "column_descriptors", []) or []
        )
        cand_cols: list[str] = []
        cand_types: dict[str, str] = {}
        for desc in col_descs:
            if not isinstance(desc, dict):
                continue
            c_name = str(desc.get("name") or desc.get("column_name") or "").strip()
            if c_name:
                cand_cols.append(c_name)
                p_type = desc.get("physical_type") or desc.get("data_type")
                if p_type:
                    cand_types[c_name] = str(p_type)
        if not cand_cols:
            pks = (
                feed.get("primary_keys", [])
                if isinstance(feed, dict)
                else getattr(feed, "primary_keys", []) or []
            )
            cand_cols = [str(pk) for pk in pks if pk]

        dep_cols, old_types, key_cols = reconstruct_baseline_metadata_from_contract(
            curr_contract,
            table_name,
            source_system=source_sys or None,
        )
        if not dep_cols:
            projected.append(
                {
                    "code": "UF-5",
                    "drift_type": "UNMAPPED_SOURCE_FEED",
                    "suggested_op": "ONBOARD_NEW_FEED",
                    "category": "UNMAPPED_FEED",
                    "source_table": qualified,
                    "target_entity": f"sat_{table_name}",
                    "column_name": cand_cols[0] if cand_cols else None,
                    "action": "AUTO-FIX",
                    "message": (
                        f"Unmapped source feed '{qualified}' scheduled for Step 4 onboarding."
                    ),
                }
            )
            continue

        raw_findings = detect_intra_source_schema_drift(
            deployed_columns=dep_cols,
            candidate_columns=cand_cols,
            source_table=qualified,
            target_entity=f"sat_{table_name}",
            key_columns=key_cols,
            old_types=old_types,
            new_types=cand_types,
        )
        for finding in raw_findings:
            fd = finding.to_dict()
            uf_code, drift_type, suggested_op = _CATEGORY_TO_UF.get(
                finding.category,
                ("UF-1", finding.category, "SPLIT_SATELLITE"),
            )
            fd["code"] = uf_code
            fd["drift_type"] = drift_type
            fd["suggested_op"] = suggested_op
            projected.append(fd)

    return projected


def persist_resumable_run_context(
    runner: Any,
    *,
    paused_stage_id: str,
    paused_reason: str,
) -> Path | None:
    """Persist serialized run inputs when a run enters ``PAUSED_HOLD`` for background resume."""
    from opendv_agent_harness.pipeline.sandbox_compiler import _resolve_runner_state_dir

    run_id = str(getattr(runner, "run_id", "") or "").strip()
    if not run_id:
        return None
    payload = {
        "run_id": run_id,
        "paused_stage_id": paused_stage_id,
        "paused_reason": paused_reason,
        "input_spec": (
            runner.input_spec.model_dump(mode="json", exclude_none=True)
            if hasattr(runner.input_spec, "model_dump")
            else {}
        ),
        "harness_setup": (
            runner.harness_setup.model_dump(mode="json")
            if hasattr(runner.harness_setup, "model_dump")
            else {}
        ),
        "base_dir": str(runner.base_dir),
        "output_dir": str(runner.output_dir),
        "workspace_root": str(runner.workspace_root),
        "is_server": bool(getattr(runner, "is_server", False)),
        "allowed_hosts": getattr(runner, "allowed_hosts", None),
        "uses_deterministic_shim": bool(
            getattr(runner, "sdk_module", None) is not None
            or getattr(runner, "harness", None) is None
        ),
    }
    raw_json = json.dumps(payload, indent=2)

    out_file = Path(runner.output_dir) / "resume_context.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(raw_json, encoding="utf-8", newline="\n")

    state_dir = _resolve_runner_state_dir(runner)
    if state_dir is not None:
        resume_dir = state_dir / "resumable_runs"
        resume_dir.mkdir(parents=True, exist_ok=True)
        resume_file = resume_dir / f"{run_id}.json"
        resume_file.write_text(raw_json, encoding="utf-8", newline="\n")
        return resume_file
    return out_file


def resolve_runner_harness(
    harness_setup: HarnessSetupConfig,
    *,
    workspace_root: Path,
    output_dir: Path,
    sdk_module: Any | None,
) -> Any | None:
    """Instantiate the configured harness adapter safely during pipeline initialization."""
    try:
        from opendv_agent_harness.registry import get_harness_adapter

        h_cfg = harness_setup.to_harness_config(
            workspace_dir=workspace_root,
            state_dir=output_dir / "state",
            app_data_dir=output_dir / "app_data",
            save_dir=output_dir / "save",
        )
        return get_harness_adapter(h_cfg, sdk_module=sdk_module)
    except (ImportError, ValueError, KeyError, OSError, RuntimeError):
        return None


def materialize_intermediate_contract_artifact(
    output_dir: Path,
    cas_store: Any | None,
    contract_dict: dict[str, Any] | None,
) -> None:
    """Persist intermediate contract YAML to `output_dir` and CAS store."""
    if not contract_dict:
        return
    fc_bytes = yaml.safe_dump(contract_dict, sort_keys=False).encode("utf-8")
    (output_dir / "final_contract.yaml").write_bytes(fc_bytes)
    if cas_store is not None:
        cas_store.put(fc_bytes)


def record_runner_fallback(
    runner: Any,
    wave_index: int,
    stage_id: Any,
    details: dict[str, Any] | None = None,
    fallback_key: str = "stage_output_policy.symbolic_completion",
) -> None:
    """Append fallback metadata and telemetry event onto ``runner``."""
    st_id = PipelineStageId(stage_id)
    runner.fallbacks_applied.append(
        {
            "key": fallback_key,
            "applied_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "stage_id": st_id.value,
            "details": details or {},
        }
    )
    runner.observability_events.append(
        build_observability_event_dict(
            event_id=f"evt-{runner.run_id}-{uuid.uuid4().hex[:8]}",
            event_type="FALLBACK_APPLIED",
            run_id=runner.run_id,
            trace_id=uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
            stage_id=st_id,
            status=ObservabilityVerdict.AUTO_REPAIRED,
            rule_ids_evaluated=["DV-OPT-001"],
            summary_metrics={"wave_index": wave_index, "fallback_key": fallback_key},
        )
    )


def emit_runner_stream_event(
    runner: Any,
    text: str,
    stage_id: str = "STAGE_00_ORCHESTRATION",
    persona: str = "vault-first-mate",
) -> None:
    """Emit a normalized TEXT_DELTA stream event if ``runner.on_stream_event`` is set."""
    if runner.on_stream_event is None:
        return
    runner.on_stream_event(
        HarnessStreamEvent(
            run_id=runner.run_id,
            task_id=f"task-{runner.run_id}",
            trace_id=uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
            provider=getattr(runner.harness_setup, "provider", "agy"),
            persona=persona,
            stage_id=stage_id,
            kind=HarnessEventKind.TEXT_DELTA,
            delta_text=text,
        )
    )


def enrich_drift_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure every drift event dict includes structured UF-1..UF-5 projection keys."""
    enriched: list[dict[str, Any]] = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        fd = dict(item)
        cat = str(fd.get("category") or "")
        default_uf = (
            "UF-5" if cat == "UNMAPPED_FEED" else "UF-1",
            cat or "ADDITIVE_COLUMN",
            "SPLIT_SATELLITE",
        )
        uf_code, drift_type, suggested_op = _CATEGORY_TO_UF.get(cat, default_uf)
        fd.setdefault("code", uf_code)
        fd.setdefault("drift_type", drift_type)
        fd.setdefault("suggested_op", suggested_op)
        enriched.append(fd)
    return enriched


__all__ = [
    "emit_runner_stream_event",
    "enrich_drift_findings",
    "materialize_intermediate_contract_artifact",
    "persist_resumable_run_context",
    "project_stage_drift_findings",
    "record_runner_fallback",
    "resolve_runner_harness",
]
