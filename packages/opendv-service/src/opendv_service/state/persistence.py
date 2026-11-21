"""Atomic .opendv/state/ persistence, rehydration, PII redaction, and mid-run ledger flush."""

from __future__ import annotations

import contextlib
import copy
import json
import os
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from opendv_agent_harness import (
    StewardHoldManager,
    build_observability_event_dict,
)
from opendv_contracts import EventType
from opendv_service.models import (
    ArtifactMetadataResponse,
    CaptainHoldDecisionResponse,
    ProjectResponse,
    ProjectRunDTO,
    SandboxExecutionResponse,
)
from opendv_service.state.pii_redaction import (
    redact_event_pii,
    redact_pii_text,
    sanitize_layer1_summary,
)
from opendv_telemetry import VaultLedgerEmitter

if TYPE_CHECKING:
    from opendv_service.state.base import BaseStateContainer

STATE_SNAPSHOT_FILENAME: Final[str] = "platform_state.json"


def _snapshot_container(container: BaseStateContainer | Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "pipeline_run_seq": int(getattr(container, "_pipeline_run_seq", 0)),
        "projects": {k: v.model_dump(mode="json") for k, v in container.projects.items()},
        "project_bronze_sources": {k: list(v) for k, v in container.project_bronze_sources.items()},
        "runs": {k: v.model_dump(mode="json") for k, v in container.runs.items()},
        "run_session_ids": {
            str(k): str(v) for k, v in getattr(container, "run_session_ids", {}).items() if v
        },
        "explicit_hold_runs": sorted(
            str(r) for r in getattr(container, "explicit_hold_runs", set())
        ),
        "run_gate_reports": copy.deepcopy(container.run_gate_reports),
        "run_sandbox_history": {
            k: [item.model_dump(mode="json") for item in v]
            for k, v in container.run_sandbox_history.items()
        },
        "run_decisions": {
            k: [item.model_dump(mode="json") for item in v]
            for k, v in container.run_decisions.items()
        },
        "pipeline_reports": copy.deepcopy(container.pipeline_reports),
        "artifacts": {k: v.model_dump(mode="json") for k, v in container.artifacts.items()},
    }


def save_platform_state(container: BaseStateContainer | Any) -> Path:
    """Atomically persist PlatformStateContainer state to `<state_dir>/platform_state.json`."""
    state_dir = Path(container.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / STATE_SNAPSHOT_FILENAME
    lock = getattr(container, "_state_lock", None)

    def _do_write() -> None:
        payload = json.dumps(_snapshot_container(container), indent=2, default=str)
        tmp = state_dir / f".{STATE_SNAPSHOT_FILENAME}.{secrets.token_hex(4)}.tmp"
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                with contextlib.suppress(OSError):
                    tmp.unlink()

    if lock is not None:
        with lock:
            _do_write()
    else:
        _do_write()
    return target


def load_platform_state(container: BaseStateContainer | Any) -> bool:
    """Rehydrate PlatformStateContainer state from `<state_dir>/platform_state.json` if present."""
    target = Path(container.state_dir) / STATE_SNAPSHOT_FILENAME
    if not target.is_file():
        return False
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(raw, dict):
        return False

    lock = getattr(container, "_state_lock", None)

    def _apply() -> None:
        container._pipeline_run_seq = int(raw.get("pipeline_run_seq", 0) or 0)
        for pid, pdata in (raw.get("projects") or {}).items():
            with contextlib.suppress(Exception):
                container.projects[pid] = ProjectResponse.model_validate(pdata)
        for pid, srcs in (raw.get("project_bronze_sources") or {}).items():
            if isinstance(srcs, list):
                container.project_bronze_sources[pid] = [str(s) for s in srcs]
        for rid, rdata in (raw.get("runs") or {}).items():
            with contextlib.suppress(Exception):
                container.runs[rid] = ProjectRunDTO.model_validate(rdata)
        for rid, sid_val in (raw.get("run_session_ids") or {}).items():
            if sid_val and hasattr(container, "run_session_ids"):
                container.run_session_ids[str(rid)] = str(sid_val)
        for rid in raw.get("explicit_hold_runs") or []:
            if rid and hasattr(container, "explicit_hold_runs"):
                container.explicit_hold_runs.add(str(rid))
        for rid, greport in (raw.get("run_gate_reports") or {}).items():
            if isinstance(greport, dict):
                container.run_gate_reports[rid] = dict(greport)
        for rid, s_hist in (raw.get("run_sandbox_history") or {}).items():
            container.run_sandbox_history[rid] = [
                SandboxExecutionResponse.model_validate(item) for item in (s_hist or [])
            ]
        for rid, decs in (raw.get("run_decisions") or {}).items():
            container.run_decisions[rid] = [
                CaptainHoldDecisionResponse.model_validate(item) for item in (decs or [])
            ]
        for rid, rep in (raw.get("pipeline_reports") or {}).items():
            if isinstance(rep, dict):
                container.pipeline_reports[rid] = dict(rep)
        for aid, adata in (raw.get("artifacts") or {}).items():
            with contextlib.suppress(Exception):
                container.artifacts[aid] = ArtifactMetadataResponse.model_validate(adata)

        for rid in container.runs:
            ledger_file = Path(container.ledger_dir) / f"{rid}.jsonl"
            if rid not in container.run_ledgers:
                emitter = VaultLedgerEmitter(ledger_path=ledger_file, cas_store=container.cas)
                container.run_ledgers[rid] = emitter
            if rid not in container.run_events:
                container.run_events[rid] = (
                    container.run_ledgers[rid].read_all_events() if ledger_file.is_file() else []
                )
            if hasattr(container, "run_session_ids") and rid not in container.run_session_ids:
                for ev in container.run_events.get(rid, ()):
                    if not isinstance(ev, dict):
                        continue
                    l2 = (
                        ev.get("layer2_visual_payload")
                        if isinstance(ev.get("layer2_visual_payload"), dict)
                        else {}
                    )
                    l2_m = (
                        l2.get("summary_metrics")
                        if isinstance(l2.get("summary_metrics"), dict)
                        else {}
                    )
                    top_m = (
                        ev.get("summary_metrics")
                        if isinstance(ev.get("summary_metrics"), dict)
                        else {}
                    )
                    ev_sid = (
                        ev.get("session_id") or l2_m.get("session_id") or top_m.get("session_id")
                    )
                    if ev_sid:
                        container.run_session_ids[rid] = str(ev_sid)
                        break
            if rid not in container.hold_managers:
                container.hold_managers[rid] = StewardHoldManager(
                    state_dir=Path(container.state_dir) / rid
                )

    if lock is not None:
        with lock:
            _apply()
    else:
        _apply()
    return True


def register_run_record(
    container: BaseStateContainer | Any, run_dto: ProjectRunDTO
) -> ProjectRunDTO:
    """Thread-safely register `run_dto` in `container.runs` and persist state."""
    lock = getattr(container, "_state_lock", None)
    if lock is not None:
        with lock:
            container.runs[run_dto.run_id] = run_dto
            container.run_events.setdefault(run_dto.run_id, [])
            container.run_decisions.setdefault(run_dto.run_id, [])
            container.run_sandbox_history.setdefault(run_dto.run_id, [])
    else:
        container.runs[run_dto.run_id] = run_dto
    save_platform_state(container)
    return run_dto


def emit_incremental_ledger_event(
    container: BaseStateContainer | Any,
    run_id: str,
    evt: dict[str, Any],
) -> None:
    """Redact PII, record in `container.run_events`, and flush to VaultLedgerEmitter."""
    clean_evt = redact_event_pii(evt)
    lock = getattr(container, "_state_lock", None)

    def _get_or_create_ledger() -> VaultLedgerEmitter:
        container.run_events.setdefault(run_id, []).append(clean_evt)
        ledger = container.run_ledgers.get(run_id)
        if ledger is None:
            ledger = VaultLedgerEmitter(
                ledger_path=Path(container.ledger_dir) / f"{run_id}.jsonl",
                cas_store=container.cas,
            )
            container.run_ledgers[run_id] = ledger
        return ledger

    if lock is not None:
        with lock:
            ledger = _get_or_create_ledger()
    else:
        ledger = _get_or_create_ledger()

    with contextlib.suppress(ValueError, TypeError, OSError):
        ledger.emit(clean_evt)


def register_report_artifacts(container: BaseStateContainer | Any, report: Any) -> None:
    """Persist intermediate and final pipeline report artifacts in CAS and container registry."""
    import yaml as _yaml

    for ref in [*report.intermediate_artifacts, *report.final_artifacts.values()]:
        raw_bytes: bytes | None = None
        if ref.file_path and Path(ref.file_path).is_file():
            raw_bytes = Path(ref.file_path).read_bytes()
            container.cas_store.put(raw_bytes)
        elif container.cas_store.has(ref.sha256):
            raw_bytes = container.cas_store.get_bytes(ref.sha256)

        if raw_bytes is not None:
            parsed_content: Any
            try:
                text_val = raw_bytes.decode("utf-8")
                if ref.media_type == "application/json":
                    parsed_content = json.loads(text_val)
                elif ref.media_type in {"application/yaml", "application/x-yaml"}:
                    parsed_content = _yaml.safe_load(text_val)
                else:
                    parsed_content = text_val
            except (json.JSONDecodeError, _yaml.YAMLError, UnicodeDecodeError):
                parsed_content = raw_bytes.decode("utf-8", errors="replace")

            meta = ArtifactMetadataResponse(
                sha256=ref.sha256.upper(),
                byte_length=ref.byte_length,
                media_type=ref.media_type or "application/octet-stream",
                artifact_name=ref.name,
                content=parsed_content,
            )
            lock = getattr(container, "_state_lock", None)
            with lock if lock is not None else contextlib.nullcontext():
                container.artifacts[ref.sha256.upper()] = meta
                container.artifacts[ref.sha256.lower()] = meta


def wrap_pipeline_callbacks_with_ledger_flush(
    container: BaseStateContainer | Any,
    run_id: str | None,
    *,
    session_id: str | None = None,
    on_stage_start: Callable[[int, str, str, str], None] | None = None,
    on_stage_complete: Callable[[int, Any], None] | None = None,
    on_stream_event: Callable[[Any], None] | None = None,
) -> tuple[
    Callable[[int, str, str, str], None],
    Callable[[int, Any], None],
    Callable[[Any], None],
]:
    """Wrap pipeline callbacks so stage/stream events flush incrementally mid-run."""
    eff_run_id = run_id or "run-live"

    def _with_session(metrics: dict[str, Any]) -> dict[str, Any]:
        if session_id:
            metrics["session_id"] = session_id
        return metrics

    def _wrapped_start(wave_idx: int, stage_id: str, persona: str, model: str) -> None:
        if on_stage_start is not None:
            on_stage_start(wave_idx, stage_id, persona, model)
            return
        evt = build_observability_event_dict(
            event_id=f"evt-{eff_run_id}-{stage_id.lower()}-start-{uuid.uuid4().hex[:6]}",
            event_type=EventType.STAGE_STARTED.value,
            run_id=eff_run_id,
            trace_id=uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
            timestamp_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            stage_id=stage_id,
            agent_persona=persona,
            status="PASS",
            headline=f"Stage {stage_id} started by {persona}.",
            business_impact=f"Executing wave {wave_idx} with model {model}.",
            summary_metrics=_with_session({"wave_index": wave_idx, "model": model}),
        )
        emit_incremental_ledger_event(container, eff_run_id, evt)

    def _wrapped_complete(wave_idx: int, step_report: Any) -> None:
        if on_stage_complete is not None:
            on_stage_complete(wave_idx, step_report)
            return
        st_id = getattr(step_report, "stage_id", "STAGE_00_ORCHESTRATION")
        status_raw = str(getattr(step_report, "status", "PASS"))
        passed = status_raw in {"PASS", "COMPLETED", "AUTO-FIX", "AUTO_REPAIRED"}
        safe_summary = sanitize_layer1_summary(str(getattr(step_report, "summary", "")), 200)
        evt = build_observability_event_dict(
            event_id=f"evt-{eff_run_id}-{st_id.lower()}-done-{uuid.uuid4().hex[:6]}",
            event_type=(
                EventType.STAGE_COMPLETED.value if passed else EventType.STAGE_FAILED.value
            ),
            run_id=eff_run_id,
            trace_id=uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
            timestamp_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            stage_id=st_id,
            agent_persona=getattr(step_report, "persona", "vault-first-mate"),
            status="PASS" if passed else "REJECT-WITH-TRACE",
            headline=f"Stage {st_id} completed: {status_raw}.",
            business_impact=safe_summary or f"Wave {wave_idx} stage complete.",
            summary_metrics=_with_session(
                {
                    "wave_index": wave_idx,
                    "duration_ms": int(getattr(step_report, "duration_ms", 0)),
                }
            ),
        )
        emit_incremental_ledger_event(container, eff_run_id, evt)

    def _wrapped_stream(harness_event: Any) -> None:
        if on_stream_event is not None:
            on_stream_event(harness_event)
            return
        delta_txt = getattr(harness_event, "delta_text", None) or getattr(
            harness_event, "token_delta", None
        )
        thought_txt = getattr(harness_event, "thought_delta", None)
        obs = getattr(harness_event, "observability_event", None)
        if delta_txt or thought_txt or obs:
            summary_txt = str(thought_txt or delta_txt or "Harness stream checkpoint").strip()
            safe_impact = sanitize_layer1_summary(summary_txt, 100)
            stage_val = getattr(harness_event, "stage_id", None)
            stage_str = (
                stage_val.value
                if hasattr(stage_val, "value")
                else str(stage_val or "STAGE_00_ORCHESTRATION")
            )
            evt = build_observability_event_dict(
                event_id=f"evt-{eff_run_id}-stream-{uuid.uuid4().hex[:6]}",
                event_type=(
                    EventType.THOUGHT_DELTA.value if thought_txt else EventType.TOKEN_DELTA.value
                ),
                run_id=eff_run_id,
                trace_id=uuid.uuid4().hex,
                span_id=uuid.uuid4().hex[:16],
                timestamp_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                stage_id=stage_str,
                agent_persona="vault-first-mate",
                status="PASS",
                headline="Stream delta checkpoint.",
                business_impact=safe_impact or "LLM stream activity.",
                summary_metrics=_with_session({"delta_length": len(summary_txt)}),
            )
            emit_incremental_ledger_event(container, eff_run_id, evt)

    return _wrapped_start, _wrapped_complete, _wrapped_stream


def finalize_run_ledger(
    container: BaseStateContainer | Any,
    *,
    report_run_id: str,
    target_run_id: str | None,
    cb_run_id: str,
    target_session_id: str | None,
    report_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge events, embed session_id in layer2 summary_metrics, and flush atomically."""
    lock = getattr(container, "_state_lock", None)
    events_list = list(report_events)
    with lock if lock is not None else contextlib.nullcontext():
        if not events_list:
            cand = (
                container.run_events.get(report_run_id)
                or (container.run_events.get(target_run_id) if target_run_id else None)
                or container.run_events.get(cb_run_id)
            )
            if cand:
                events_list = list(cand)
        if cb_run_id not in (report_run_id, target_run_id):
            container.run_events.pop(cb_run_id, None)
            container.run_ledgers.pop(cb_run_id, None)

    if cb_run_id not in (report_run_id, target_run_id):
        with contextlib.suppress(OSError):
            (Path(container.ledger_dir) / f"{cb_run_id}.jsonl").unlink(missing_ok=True)

    normalized: list[dict[str, Any]] = []
    for raw_evt in events_list:
        if not isinstance(raw_evt, dict):
            continue
        evt = dict(raw_evt)
        if evt.get("run_id") == cb_run_id and cb_run_id != report_run_id:
            evt["run_id"] = report_run_id
        if target_session_id:
            l2 = evt.get("layer2_visual_payload")
            if isinstance(l2, dict):
                new_l2 = dict(l2)
                metrics = dict(new_l2.get("summary_metrics") or {})
                metrics.setdefault("session_id", target_session_id)
                new_l2["summary_metrics"] = metrics
                evt["layer2_visual_payload"] = new_l2
        normalized.append(evt)

    lock = getattr(container, "_state_lock", None)
    with lock if lock is not None else contextlib.nullcontext():
        container.run_events[report_run_id] = normalized
        if target_run_id and target_run_id != report_run_id:
            container.run_events[target_run_id] = normalized
        if target_session_id and hasattr(container, "run_session_ids"):
            container.run_session_ids[report_run_id] = str(target_session_id)
            if target_run_id and target_run_id != report_run_id:
                container.run_session_ids[target_run_id] = str(target_session_id)

    ledger_dir = Path(container.ledger_dir)
    ledger_file = ledger_dir / f"{report_run_id}.jsonl"
    if normalized:
        tmp_file = ledger_dir / f".{report_run_id}.{secrets.token_hex(4)}.jsonl.tmp"
        tmp_emitter = VaultLedgerEmitter(ledger_path=tmp_file, cas_store=container.cas)
        emitted = 0
        for evt in normalized:
            with contextlib.suppress(ValueError, TypeError, OSError):
                tmp_emitter.emit(evt)
                emitted += 1
        if emitted > 0 and tmp_file.is_file():
            os.replace(tmp_file, ledger_file)
        else:
            with contextlib.suppress(OSError):
                tmp_file.unlink(missing_ok=True)
    emitter = VaultLedgerEmitter(ledger_path=ledger_file, cas_store=container.cas)
    with lock if lock is not None else contextlib.nullcontext():
        container.run_ledgers[report_run_id] = emitter
    return normalized


__all__ = [
    "STATE_SNAPSHOT_FILENAME",
    "emit_incremental_ledger_event",
    "finalize_run_ledger",
    "load_platform_state",
    "redact_event_pii",
    "redact_pii_text",
    "register_report_artifacts",
    "register_run_record",
    "save_platform_state",
    "wrap_pipeline_callbacks_with_ledger_flush",
]
