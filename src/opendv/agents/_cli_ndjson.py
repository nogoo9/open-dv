"""NDJSON stream parsing and structured-output validation for CLI/SDK agent runners."""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from opendv.agents._pii_redact import redact_telemetry_text
from opendv.models.errors import ErrorCode, OpenDVError

_NON_RESULT_STREAM_TYPES: frozenset[str] = frozenset(
    {
        "text_delta",
        "delta",
        "thought_delta",
        "thinking_delta",
        "thinking",
        "thought",
        "content_block_delta",
        "assistant",
        "message_start",
        "message_delta",
        "message_stop",
        "content_block_start",
        "content_block_stop",
        "system",
        "init",
        "step_update",
        "stream_event",
    }
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def strip_markdown_json_fences(raw_text: str) -> str:
    """Remove optional ```json ... ``` wrapper fences around structured output."""
    stripped = raw_text.strip()
    match = _FENCE_RE.match(stripped)
    return match.group(1).strip() if match else stripped


def is_non_result_stream_event(obj: dict[str, Any]) -> bool:
    """Return True if obj is an intermediate NDJSON stream event rather than a terminal result."""
    if str(obj.get("event") or "").lower() in {"init", "step_update"}:
        return True
    raw_ev = obj.get("event")
    target = (
        raw_ev
        if str(obj.get("type") or "").lower() == "stream_event" and isinstance(raw_ev, dict)
        else obj
    )
    return str(target.get("type") or "").lower() in _NON_RESULT_STREAM_TYPES


def _redact_preview(raw_text: str, *, max_len: int = 300) -> str:
    """Redact PII before truncating to max_len, bounding work on >64KB inputs."""
    if not raw_text:
        return ""
    if len(raw_text) <= 65536:
        return redact_telemetry_text(raw_text)[:max_len]
    cut = 65536
    scrubbed = ""
    while True:
        limit = min(len(raw_text), cut + 8192)
        while cut < limit and (
            raw_text[cut].isalnum()
            or raw_text[cut] in "-_@.+"
            or (
                raw_text[cut] == " "
                and cut > 0
                and raw_text[cut - 1].isalnum()
                and cut + 1 < limit
                and raw_text[cut + 1].isalnum()
            )
        ):
            cut += 1
        scrubbed = redact_telemetry_text(raw_text[:cut])
        if len(scrubbed) >= 600 or cut >= len(raw_text):
            break
        cut = min(len(raw_text), cut + 65536)
    return scrubbed[:max_len]


def parse_ndjson_step_events(raw_line: str) -> list[dict[str, Any]]:
    """Extract tool call/result and step usage dicts from agy step_update NDJSON lines."""
    candidate = raw_line.strip()
    if not (candidate.startswith("{") and candidate.endswith("}")):
        return []
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, dict) or obj.get("event") != "step_update":
        return []
    su = obj.get("step_update")
    if not isinstance(su, dict):
        return []

    events: list[dict[str, Any]] = []
    step_type = str(su.get("step_type") or "").lower()
    state = str(su.get("state") or "").upper()
    if step_type == "tool":
        raw_info = su.get("tool_info")
        t_info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
        tool_name = str(su.get("tool_name") or t_info.get("name") or "tool")
        call_id = f"step-{su.get('step_index', 0)}"
        if state == "ACTIVE":
            params = t_info.get("parameters")
            events.append(
                {
                    "kind": "TOOL_CALL",
                    "tool_name": tool_name,
                    "tool_call_id": call_id,
                    "tool_args": params if isinstance(params, dict) else {},
                }
            )
        elif state == "DONE":
            events.append(
                {
                    "kind": "TOOL_RESULT",
                    "tool_name": tool_name,
                    "tool_call_id": call_id,
                    "ok": True,
                    "summary": _redact_preview(str(t_info.get("output") or "")),
                }
            )
    raw_usage = su.get("usage")
    if isinstance(raw_usage, dict):
        events.append({"kind": "STEP_USAGE", "usage": dict(raw_usage)})
    return events


def _extract_block_deltas(delta_obj: dict[str, Any]) -> tuple[list[str], list[str], bool]:
    thoughts: list[str] = []
    texts: list[str] = []
    d_type = str(delta_obj.get("type") or "").lower()
    if d_type in {"thinking_delta", "thought_delta", "thinking", "thought"}:
        chunk = delta_obj.get("thinking") or delta_obj.get("thought") or delta_obj.get("text")
        if isinstance(chunk, str) and chunk:
            thoughts.append(chunk)
            return thoughts, texts, True
    else:
        chunk = delta_obj.get("text") or delta_obj.get("delta") or delta_obj.get("partial_json")
        if isinstance(chunk, str) and chunk:
            texts.append(chunk)
            return thoughts, texts, True
    return thoughts, texts, False


def parse_ndjson_stream_line(
    raw_line: str,
    *,
    seen_fine_grained_deltas: bool = False,
) -> tuple[list[str], list[str], dict[str, Any] | None, bool]:
    """Parse one NDJSON line from agy or claude stream-json output."""
    candidate = raw_line.strip()
    if not (candidate.startswith("{") and candidate.endswith("}")):
        return ([], [], None, False)
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        return ([], [], None, False)
    if not isinstance(obj, dict):
        return ([], [], None, False)

    if obj.get("event") == "result" and isinstance(obj.get("result"), dict):
        return ([], [], dict(obj["result"]), False)

    if obj.get("event") == "step_update" and isinstance(obj.get("step_update"), dict):
        su = obj["step_update"]
        su_t: list[str] = []
        su_c: list[str] = []
        su_fine = False
        t_chunk = (
            su.get("thought_delta")
            or su.get("thinking_delta")
            or su.get("thinking")
            or su.get("thought")
        )
        if isinstance(t_chunk, str) and t_chunk:
            su_t.append(t_chunk)
            su_fine = True
        c_chunk = su.get("text_delta") or su.get("delta_text") or su.get("text")
        if isinstance(c_chunk, str) and c_chunk:
            su_c.append(c_chunk)
            su_fine = True
        return (su_t, su_c, None, su_fine)

    raw_ev = obj.get("event")
    target: dict[str, Any] = (
        raw_ev
        if str(obj.get("type") or "").lower() == "stream_event" and isinstance(raw_ev, dict)
        else obj
    )
    ev_type = str(target.get("type") or "").lower()
    result_keys = (
        "status",
        "response",
        "result",
        "structured_output",
        "is_error",
        "error",
        "subtype",
    )
    if ev_type == "result" or (
        ev_type not in _NON_RESULT_STREAM_TYPES and any(k in target for k in result_keys)
    ):
        return ([], [], target, False)

    thoughts: list[str] = []
    texts: list[str] = []
    fine = False
    if ev_type in {"thought_delta", "thinking_delta", "thinking", "thought"}:
        chunk = (
            target.get("thinking")
            or target.get("thought")
            or target.get("delta")
            or target.get("text")
            or target.get("delta_text")
        )
        if isinstance(chunk, str) and chunk:
            thoughts.append(chunk)
            fine = True
    elif ev_type in {"text_delta", "delta", "content_block_delta"}:
        delta_val = target.get("delta")
        if isinstance(delta_val, dict):
            thoughts, texts, fine = _extract_block_deltas(delta_val)
        else:
            chunk = (
                target.get("text")
                or delta_val
                or target.get("delta_text")
                or target.get("content")
            )
            if isinstance(chunk, str) and chunk:
                texts.append(chunk)
                fine = True
    elif ev_type == "assistant" and not seen_fine_grained_deltas:
        msg = target.get("message")
        content_list = msg.get("content") if isinstance(msg, dict) else target.get("content")
        if isinstance(content_list, list):
            for block in content_list:
                if not isinstance(block, dict):
                    continue
                b_type = str(block.get("type") or "text").lower()
                if b_type in {"thinking", "thought"}:
                    t_val = block.get("thinking") or block.get("thought") or block.get("text")
                    if isinstance(t_val, str) and t_val:
                        thoughts.append(t_val)
                elif b_type == "text" and isinstance(block.get("text"), str) and block["text"]:
                    texts.append(block["text"])
        elif isinstance(content_list, str) and content_list:
            texts.append(content_list)
    else:
        dt = target.get("delta_text")
        if isinstance(dt, str) and dt:
            (thoughts if ev_type in {"thought", "thinking"} else texts).append(dt)
            fine = True

    return (thoughts, texts, None, fine)


def extract_json_from_stdout(
    raw_stdout: str,
    *,
    cli_label: str,
    primary_text_key: str = "response",
) -> dict[str, Any]:
    """Extract terminal JSON payload from stream-json or raw JSON stdout."""
    stripped = raw_stdout.strip()
    if not stripped:
        raise OpenDVError(
            f"{cli_label} returned empty stdout.",
            error_code=ErrorCode.AGENT_EXECUTION_FAILED,
        )

    last_result: dict[str, Any] | None = None
    streamed_text_chunks: list[str] = []
    seen_fine = False
    for raw_line in stripped.splitlines():
        _, text_deltas, result_obj, is_fine = parse_ndjson_stream_line(
            raw_line,
            seen_fine_grained_deltas=seen_fine,
        )
        if is_fine:
            seen_fine = True
        if text_deltas:
            streamed_text_chunks.extend(text_deltas)
        if result_obj is not None:
            last_result = dict(result_obj)

    if last_result is not None:
        if not (last_result.get("result") or last_result.get("response")) and streamed_text_chunks:
            last_result[primary_text_key] = "".join(streamed_text_chunks)
        return last_result

    if streamed_text_chunks:
        return {"status": "SUCCESS", primary_text_key: "".join(streamed_text_chunks)}

    with contextlib.suppress(json.JSONDecodeError):
        parsed = json.loads(stripped)
        if isinstance(parsed, dict) and not is_non_result_stream_event(parsed):
            return parsed

    for line in reversed(stripped.splitlines()):
        candidate = line.strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            with contextlib.suppress(json.JSONDecodeError):
                parsed_line = json.loads(candidate)
                if isinstance(parsed_line, dict) and not is_non_result_stream_event(parsed_line):
                    return parsed_line

    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if 0 <= first_brace < last_brace:
        with contextlib.suppress(json.JSONDecodeError):
            parsed_slice = json.loads(stripped[first_brace : last_brace + 1])
            if isinstance(parsed_slice, dict) and not is_non_result_stream_event(parsed_slice):
                return parsed_slice

    scrubbed_preview = _redact_preview(stripped)
    raise OpenDVError(
        f"{cli_label} output was not valid JSON: {scrubbed_preview}",
        error_code=ErrorCode.AGENT_EXECUTION_FAILED,
    )


def _format_validation_error(exc: ValidationError) -> str:
    err_items = [
        {
            "type": err.get("type"),
            "loc": ".".join(str(part) for part in err.get("loc", ())),
            "msg": str(err.get("msg", "")),
        }
        for err in exc.errors(include_url=False, include_input=False)
    ]
    return json.dumps(err_items, separators=(",", ":"))


def validate_cli_structured_output(
    output_schema: type[BaseModel] | None,
    *,
    raw_structured: Any,
    raw_response_text: str,
    cli_label: str,
) -> Any:
    """Validate structured JSON output against output_schema when required."""
    if output_schema is None:
        return raw_structured
    try:
        if raw_structured is not None:
            if isinstance(raw_structured, output_schema):
                return raw_structured
            if isinstance(raw_structured, str):
                cleaned = strip_markdown_json_fences(raw_structured)
                return output_schema.model_validate_json(cleaned)
            return output_schema.model_validate(raw_structured)
        if raw_response_text.strip():
            cleaned = strip_markdown_json_fences(raw_response_text)
            return output_schema.model_validate_json(cleaned)
    except ValidationError as exc:
        scrubbed_err = redact_telemetry_text(_format_validation_error(exc))
        raise OpenDVError(
            f"{cli_label} returned invalid structured output for "
            f"{output_schema.__name__}: {scrubbed_err}",
            error_code=ErrorCode.AGENT_EXECUTION_FAILED,
        ) from exc
    except (ValueError, json.JSONDecodeError) as exc:
        scrubbed_err = redact_telemetry_text(str(exc))
        raise OpenDVError(
            f"{cli_label} returned invalid structured output for "
            f"{output_schema.__name__}: {scrubbed_err}",
            error_code=ErrorCode.AGENT_EXECUTION_FAILED,
        ) from exc
    raise OpenDVError(
        f"{cli_label} returned an empty response when structured output "
        f"({output_schema.__name__}) was required.",
        error_code=ErrorCode.AGENT_EXECUTION_FAILED,
    )


__all__ = [
    "extract_json_from_stdout",
    "is_non_result_stream_event",
    "parse_ndjson_step_events",
    "parse_ndjson_stream_line",
    "redact_telemetry_text",
    "strip_markdown_json_fences",
    "validate_cli_structured_output",
]
