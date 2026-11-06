"""SSE event follower and stream renderer for RemoteExecutor (GAP-06, Decision D2)."""

from __future__ import annotations

import json
from typing import Any


def format_short_stage(stage_id: str) -> str:
    """Format full stage ID like STAGE_00_ORCHESTRATION into short STAGE 00."""
    parts = stage_id.split("_")
    if len(parts) >= 2 and parts[0].upper() == "STAGE":
        return f"STAGE {parts[1]}"
    return stage_id


def consume_sse_stream(
    client: Any,
    events_url: str,
    headers: dict[str, str],
    *,
    stream_enabled: bool,
    tokens_enabled: bool,
    target: Any,
    max_reconnects: int = 5,
) -> None:
    """Consume Server-Sent Events from the platform API, printing live updates."""
    last_event_id: str | None = None
    terminal_received = False
    reconnect_attempts = 0

    while not terminal_received and reconnect_attempts < max_reconnects:
        stream_hdrs = dict(headers)
        if last_event_id:
            stream_hdrs["Last-Event-ID"] = last_event_id

        try:
            with client.stream("GET", events_url, headers=stream_hdrs) as sse_stream:
                if sse_stream.status_code != 200:
                    break

                cur_event = ""
                cur_id = ""
                cur_data_lines: list[str] = []

                for raw_line in sse_stream.iter_lines():
                    line = raw_line.strip()
                    if not line:
                        if cur_data_lines:
                            d_str = "\n".join(cur_data_lines)
                            try:
                                d_obj = json.loads(d_str)
                            except (json.JSONDecodeError, ValueError):
                                d_obj = d_str

                            if cur_id:
                                last_event_id = cur_id

                            ev_upper = cur_event.upper()
                            if cur_event in {"run_complete", "hold"} or ev_upper in {
                                "RUN_COMPLETE",
                                "HOLD",
                            }:
                                terminal_received = True
                                break
                            elif ev_upper in {"STAGE_STARTED"} or (
                                isinstance(d_obj, dict)
                                and str(d_obj.get("event_type", "")).upper() == "STAGE_STARTED"
                            ):
                                if stream_enabled and isinstance(d_obj, dict):
                                    p_data = d_obj.get("payload", d_obj)
                                    w = p_data.get("wave_index", 0)
                                    st = p_data.get("stage_id", "")
                                    p = p_data.get("agent_persona") or p_data.get("persona", "")
                                    m = p_data.get("model", "")
                                    msg = (
                                        f"[Wave {w}][{format_short_stage(st)}] "
                                        f"Starting {st} ({p}, model={m})..."
                                    )
                                    print(msg, file=target, flush=True)
                            elif ev_upper in {"STAGE_COMPLETED", "STAGE_FAILED"} or (
                                isinstance(d_obj, dict)
                                and str(d_obj.get("event_type", "")).upper()
                                in {"STAGE_COMPLETED", "STAGE_FAILED"}
                            ):
                                if stream_enabled and isinstance(d_obj, dict):
                                    p_data = d_obj.get("payload", d_obj)
                                    w = p_data.get("wave_index", 0)
                                    st = p_data.get("stage_id", "")
                                    p = p_data.get("agent_persona") or p_data.get("persona", "")
                                    m = p_data.get("model") or p_data.get("model_used", "")
                                    st_s = p_data.get("status", "")
                                    sm = p_data.get("summary", "")
                                    dur = p_data.get("duration_ms", 0)
                                    msg = (
                                        f"[Wave {w}][{format_short_stage(st)}] "
                                        f"{st} ({p}, model={m}) -> {st_s}: {sm} ({dur}ms)"
                                    )
                                    print(msg, file=target, flush=True)
                            elif ev_upper in {"TOKEN_DELTA"} or (
                                isinstance(d_obj, dict)
                                and str(d_obj.get("event_type", "")).upper() == "TOKEN_DELTA"
                            ):
                                if tokens_enabled and isinstance(d_obj, dict):
                                    p_data = d_obj.get("payload", d_obj)
                                    tok = p_data.get("delta") or p_data.get("delta_text", "")
                                    if tok:
                                        print(tok, end="", file=target, flush=True)

                        cur_event = ""
                        cur_id = ""
                        cur_data_lines = []
                        continue

                    if line.startswith(":"):
                        continue
                    if ":" in line:
                        fld, val = line.split(":", 1)
                        fld = fld.strip()
                        val = val.lstrip()
                        if fld == "event":
                            cur_event = val
                        elif fld == "id":
                            cur_id = val
                        elif fld == "data":
                            cur_data_lines.append(val)
        except Exception as exc:
            import importlib

            try:
                mod = importlib.import_module("httpx")
                err_types = (mod.TransportError, mod.ReadTimeout, mod.RemoteProtocolError)
                is_net = isinstance(exc, err_types)
            except (ImportError, AttributeError):
                is_net = False
            if is_net or isinstance(exc, (ConnectionError, TimeoutError, OSError)):
                reconnect_attempts += 1
                continue
            raise
        else:
            break


__all__ = ["consume_sse_stream", "format_short_stage"]
