"""Merged Studio SSE journal and linked VaultLedgerEmitter execution log queries."""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from opendv_service.studio.agent_runner_helpers import _mask_pii_text
from opendv_service.studio.session_manager import StudioSessionManager

_MIN_UTC: Final[datetime] = datetime.min.replace(tzinfo=UTC)


def _parse_iso_utc(raw: Any) -> datetime:
    text = str(raw or "").strip()
    if not text:
        return _MIN_UTC
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    except ValueError:
        return _MIN_UTC


def normalize_iso_utc(raw: Any) -> str:
    """Normalize ISO-8601 UTC timestamp so 'Z' and '.ffffff+00:00' compare consistently."""
    text = str(raw or "").strip()
    if not text:
        return ""
    parsed = _parse_iso_utc(text)
    return parsed.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00") if parsed != _MIN_UTC else text

_STAGE_PILL_ALIASES: Final[dict[str, frozenset[str]]] = {
    "00": frozenset({"STAGE_00_ORCHESTRATION"}),
    "01": frozenset({"STAGE_01_SCOUT_SOURCE"}),
    "02": frozenset({"STAGE_02_SCOUT_UNSTRUCTURED"}),
    "03": frozenset({"STAGE_03_SHIP_VAULT_PASS1", "STAGE_04_SHIP_VAULT_PASS2"}),
    "04": frozenset({"STAGE_05_AXI_DV_STATIC_GATES"}),
    "05": frozenset({"STAGE_07_DETERMINISTIC_COMPILER"}),
    "06": frozenset({"STAGE_06_AXI_DV_DYNAMIC_SANDBOX"}),
}

_GZ_SEG_RE: Final[re.Pattern[str]] = re.compile(r"\.(\d+)\.gz$")


def _candidate_ledger_dirs(
    session_mgr: StudioSessionManager,
    extra_ledger_dirs: Sequence[Path | str] | None = None,
) -> list[Path]:
    candidates: list[Path] = []
    if extra_ledger_dirs:
        for d in extra_ledger_dirs:
            if d:
                candidates.append(Path(d))
    for attr in ("storage_root", "state_dir"):
        val = getattr(session_mgr, attr, None)
        if val:
            candidates.append(Path(val) / "ledgers")
    env_state = os.environ.get("OPENDV_STATE_DIR", "").strip()
    if env_state:
        candidates.append(Path(env_state) / "ledgers")
    sroot = getattr(session_mgr, "studio_root", None)
    if sroot:
        candidates.append(Path(sroot).parent / "state" / "ledgers")
    wsroot = getattr(session_mgr, "workspace_root", None)
    if wsroot:
        candidates.append(Path(wsroot) / ".opendv" / "state" / "ledgers")
        candidates.append(Path(wsroot).parent / "state" / "ledgers")
    unique: list[Path] = []
    seen: set[Path] = set()
    for c in candidates:
        res = c.resolve()
        if res not in seen and res.is_dir():
            seen.add(res)
            unique.append(res)
    return unique


def expand_stage_filter(stage_id: str | None) -> frozenset[str] | None:
    if not stage_id or not stage_id.strip():
        return None
    raw = stage_id.strip()
    if raw in _STAGE_PILL_ALIASES:
        return _STAGE_PILL_ALIASES[raw]
    upper = raw.upper()
    if upper in _STAGE_PILL_ALIASES:
        return _STAGE_PILL_ALIASES[upper]
    return frozenset({upper})


_expand_stage_filter = expand_stage_filter


def _scrub_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return _mask_pii_text(obj)
    if isinstance(obj, dict):
        return {k: _scrub_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_obj(v) for v in obj]
    return obj


def _normalize_journal_record(rec: dict[str, Any], default_job_id: str) -> dict[str, Any]:
    data = rec.get("data") if isinstance(rec.get("data"), dict) else {}
    return {
        "timestamp": str(rec.get("timestamp") or data.get("timestamp_utc") or ""),
        "raw_id": int(rec.get("id") or 0),
        "origin": "studio_journal",
        "event": str(rec.get("event") or "log"),
        "wizard_step": data.get("wizard_step"),
        "stage_id": data.get("stage_id"),
        "persona": data.get("persona") or data.get("agent_persona"),
        "source_id": data.get("source_id"),
        "job_id": str(data.get("job_id") or default_job_id),
        "category": str(data.get("category") or "studio"),
        "provider": data.get("provider"),
        "model_id": data.get("model_id"),
        "duration_ms": data.get("duration_ms"),
        "token_usage": data.get("token_usage"),
        "masked_prompt": _mask_pii_text(str(data["masked_prompt"]))
        if data.get("masked_prompt")
        else None,
        "structured_or_raw_response": data.get("structured_or_raw_response"),
        "tool_calls": data.get("tool_calls") or [],
        "toon_diagnostics": data.get("toon_diagnostics") or [],
        "status": data.get("status"),
        "headline": _mask_pii_text(str(data.get("headline") or "")),
        "data": _scrub_obj(data),
    }


def _extract_ledger_metrics(rec: dict[str, Any]) -> dict[str, Any]:
    layer2 = (
        rec.get("layer2_visual_payload")
        if isinstance(rec.get("layer2_visual_payload"), dict)
        else {}
    )
    l2_metrics = (
        layer2.get("summary_metrics") if isinstance(layer2.get("summary_metrics"), dict) else {}
    )
    top_metrics = rec.get("summary_metrics") if isinstance(rec.get("summary_metrics"), dict) else {}
    return {**l2_metrics, **top_metrics}


def _normalize_ledger_record(rec: dict[str, Any], idx: int) -> dict[str, Any]:
    layer1 = (
        rec.get("layer1_story_banner") if isinstance(rec.get("layer1_story_banner"), dict) else {}
    )
    metrics = _extract_ledger_metrics(rec)
    return {
        "timestamp": str(rec.get("timestamp_utc") or rec.get("timestamp") or ""),
        "raw_id": idx,
        "origin": "pipeline_ledger",
        "event": str(rec.get("event_type") or "ledger_event"),
        "wizard_step": metrics.get("wizard_step", 6),
        "stage_id": rec.get("stage_id"),
        "persona": rec.get("agent_persona") or rec.get("persona"),
        "source_id": metrics.get("source_id"),
        "job_id": rec.get("run_id"),
        "category": "execution",
        "provider": metrics.get("provider"),
        "model_id": metrics.get("model_id"),
        "duration_ms": rec.get("duration_ms"),
        "token_usage": rec.get("token_usage"),
        "masked_prompt": None,
        "structured_or_raw_response": None,
        "tool_calls": [],
        "toon_diagnostics": [],
        "status": rec.get("status"),
        "headline": _mask_pii_text(str(rec.get("headline") or layer1.get("headline") or "")),
        "data": _scrub_obj(rec),
    }


def _collect_all_session_logs(
    session_mgr: StudioSessionManager,
    session_id: str,
    *,
    extra_ledger_dirs: Sequence[Path | str] | None = None,
) -> list[dict[str, Any]]:
    sdir = session_mgr.get_session_dir(session_id)
    collected: list[dict[str, Any]] = []

    jobs_dir = sdir / "jobs"
    if jobs_dir.is_dir():
        active_files = sorted(jobs_dir.glob("*.events.jsonl"))
        seen_stems: set[str] = set()
        for active_path in active_files:
            prefix = active_path.name
            seen_stems.add(prefix)
            job_name = prefix.removesuffix(".events.jsonl")
            gz_segments = sorted(
                jobs_dir.glob(f"{prefix}.*.gz"),
                key=lambda p: int(m.group(1)) if (m := _GZ_SEG_RE.search(p.name)) else 0,
            )
            for seg in gz_segments:
                with (
                    contextlib.suppress(OSError, gzip.BadGzipFile),
                    gzip.open(seg, "rt", encoding="utf-8") as gz_fp,
                ):
                    for raw_line in gz_fp:
                        line = raw_line.strip()
                        if not line:
                            continue
                        with contextlib.suppress(json.JSONDecodeError):
                            rec = json.loads(line)
                            if isinstance(rec, dict):
                                collected.append(_normalize_journal_record(rec, job_name))
            with contextlib.suppress(OSError):
                for raw_line in active_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    with contextlib.suppress(json.JSONDecodeError):
                        rec = json.loads(line)
                        if isinstance(rec, dict):
                            collected.append(_normalize_journal_record(rec, job_name))

        # Handle orphan .gz segments where active .events.jsonl does not exist
        for gz_path in sorted(jobs_dir.glob("*.events.jsonl.*.gz")):
            base_name = re.sub(r"\.\d+\.gz$", "", gz_path.name)
            if base_name in seen_stems:
                continue
            job_name = base_name.removesuffix(".events.jsonl")
            with (
                contextlib.suppress(OSError, gzip.BadGzipFile),
                gzip.open(gz_path, "rt", encoding="utf-8") as gz_fp,
            ):
                for raw_line in gz_fp:
                    line = raw_line.strip()
                    if line:
                        with contextlib.suppress(json.JSONDecodeError):
                            rec = json.loads(line)
                            if isinstance(rec, dict):
                                collected.append(_normalize_journal_record(rec, job_name))

    for ldir in _candidate_ledger_dirs(session_mgr, extra_ledger_dirs=extra_ledger_dirs):
        for lfile in sorted(ldir.glob("*.jsonl")):
            with contextlib.suppress(OSError):
                for idx, raw_line in enumerate(
                    lfile.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                ):
                    line = raw_line.strip()
                    if not line:
                        continue
                    with contextlib.suppress(json.JSONDecodeError):
                        rec = json.loads(line)
                        if not isinstance(rec, dict):
                            continue
                        metrics = _extract_ledger_metrics(rec)
                        rec_sid = rec.get("session_id") or metrics.get("session_id")
                        if rec_sid == session_id:
                            collected.append(_normalize_ledger_record(rec, idx))

    collected.sort(
        key=lambda e: (
            _parse_iso_utc(e.get("timestamp")),
            0 if e.get("origin") == "studio_journal" else 1,
            int(e.get("raw_id") or 0),
        )
    )
    for seq, entry in enumerate(collected, 1):
        entry["seq"] = seq
    return collected


def query_session_logs(
    session_mgr: StudioSessionManager,
    session_id: str,
    *,
    stage_id: str | None = None,
    wizard_step: int | None = None,
    persona: str | None = None,
    source_id: str | None = None,
    job_id: str | None = None,
    category: str | None = None,
    limit: int = 500,
    offset: int = 0,
    min_seq: int = 0,
    extra_ledger_dirs: Sequence[Path | str] | None = None,
) -> list[dict[str, Any]]:
    """Query merged monotonic logs across Studio journals and linked execution ledgers."""
    entries = _collect_all_session_logs(
        session_mgr, session_id, extra_ledger_dirs=extra_ledger_dirs
    )
    allowed_stages = _expand_stage_filter(stage_id)

    filtered: list[dict[str, Any]] = []
    for entry in entries:
        if min_seq > 0 and int(entry.get("seq") or 0) <= min_seq:
            continue
        if allowed_stages is not None:
            st = str(entry.get("stage_id") or "").upper()
            if st not in allowed_stages:
                continue
        if wizard_step is not None and entry.get("wizard_step") != wizard_step:
            continue
        if persona and str(entry.get("persona") or "").lower() != persona.strip().lower():
            continue
        if source_id and str(entry.get("source_id") or "") != source_id.strip():
            continue
        if job_id and str(entry.get("job_id") or "") != job_id.strip():
            continue
        if category and str(entry.get("category") or "").lower() != category.strip().lower():
            continue
        filtered.append(entry)

    return filtered[offset : offset + max(1, limit)]


__all__ = ["expand_stage_filter", "normalize_iso_utc", "query_session_logs"]
