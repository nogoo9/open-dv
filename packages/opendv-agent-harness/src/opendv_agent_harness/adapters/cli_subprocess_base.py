"""Shared NDJSON stream parsing and stdin-piped subprocess execution for CLI adapters."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import subprocess
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from opendv_agent_harness.adapters.antigravity_adapter import (
    ProviderConfigurationError,
    _strip_markdown_json_fences,
)
from opendv_agent_harness.models import AgentTaskSpec, HarnessStreamEvent

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
        "stream_event",
    }
)


def is_non_result_stream_event(obj: dict[str, Any]) -> bool:
    """Return True if ``obj`` is an intermediate NDJSON stream event, not a terminal result."""
    target = (
        obj["event"]
        if str(obj.get("type") or "").lower() == "stream_event"
        and isinstance(obj.get("event"), dict)
        else obj
    )
    ev_type = str(target.get("type") or "").lower()
    return ev_type in _NON_RESULT_STREAM_TYPES


def parse_ndjson_stream_line(
    raw_line: str,
    *,
    seen_fine_grained_deltas: bool = False,
) -> tuple[list[str], list[str], dict[str, Any] | None, bool]:
    """Parse a single NDJSON line from ``agy -p`` or ``claude -p`` ``stream-json`` output.

    Returns ``(thought_deltas, text_deltas, result_payload, is_fine_grained_delta)``.
    """
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

    target: dict[str, Any] = (
        obj["event"]
        if str(obj.get("type") or "").lower() == "stream_event"
        and isinstance(obj.get("event"), dict)
        else obj
    )
    ev_type = str(target.get("type") or "").lower()

    if ev_type == "result" or (
        ev_type not in _NON_RESULT_STREAM_TYPES
        and any(
            k in target
            for k in (
                "status",
                "response",
                "result",
                "structured_output",
                "is_error",
                "error",
                "subtype",
            )
        )
    ):
        return ([], [], target, False)

    thought_deltas: list[str] = []
    text_deltas: list[str] = []
    is_fine_grained = False

    if ev_type in {"thought_delta", "thinking_delta", "thinking", "thought"}:
        chunk = (
            target.get("thinking")
            or target.get("thought")
            or target.get("delta")
            or target.get("text")
            or target.get("delta_text")
        )
        if isinstance(chunk, str) and chunk:
            thought_deltas.append(chunk)
            is_fine_grained = True
    elif ev_type in {"text_delta", "delta"}:
        delta_val = target.get("delta")
        if isinstance(delta_val, dict):
            d_type = str(delta_val.get("type") or "").lower()
            if d_type in {"thinking_delta", "thought_delta", "thinking", "thought"}:
                t_c = delta_val.get("thinking") or delta_val.get("thought") or delta_val.get("text")
                if isinstance(t_c, str) and t_c:
                    thought_deltas.append(t_c)
                    is_fine_grained = True
            else:
                c_c = (
                    delta_val.get("text") or delta_val.get("delta") or delta_val.get("partial_json")
                )
                if isinstance(c_c, str) and c_c:
                    text_deltas.append(c_c)
                    is_fine_grained = True
        else:
            chunk = (
                target.get("text")
                or target.get("delta")
                or target.get("delta_text")
                or target.get("content")
            )
            if isinstance(chunk, str) and chunk:
                text_deltas.append(chunk)
                is_fine_grained = True
    elif ev_type == "content_block_delta":
        delta_obj = target.get("delta")
        if isinstance(delta_obj, dict):
            d_type = str(delta_obj.get("type") or "").lower()
            if d_type in {"thinking_delta", "thought_delta", "thinking", "thought"}:
                t_c = delta_obj.get("thinking") or delta_obj.get("thought") or delta_obj.get("text")
                if isinstance(t_c, str) and t_c:
                    thought_deltas.append(t_c)
                    is_fine_grained = True
            else:
                c_c = (
                    delta_obj.get("text") or delta_obj.get("delta") or delta_obj.get("partial_json")
                )
                if isinstance(c_c, str) and c_c:
                    text_deltas.append(c_c)
                    is_fine_grained = True
        elif isinstance(delta_obj, str) and delta_obj:
            text_deltas.append(delta_obj)
            is_fine_grained = True
    elif ev_type == "assistant":
        if not seen_fine_grained_deltas:
            msg = target.get("message")
            content_list = msg.get("content") if isinstance(msg, dict) else target.get("content")
            if isinstance(content_list, list):
                for block in content_list:
                    if isinstance(block, dict):
                        b_type = str(block.get("type") or "text").lower()
                        if b_type in {"thinking", "thought"}:
                            t_val = (
                                block.get("thinking") or block.get("thought") or block.get("text")
                            )
                            if isinstance(t_val, str) and t_val:
                                thought_deltas.append(t_val)
                        elif b_type == "text":
                            c_val = block.get("text")
                            if isinstance(c_val, str) and c_val:
                                text_deltas.append(c_val)
            elif isinstance(content_list, str) and content_list:
                text_deltas.append(content_list)
    else:
        dt = target.get("delta_text")
        if isinstance(dt, str) and dt:
            if ev_type in {"thought", "thinking"}:
                thought_deltas.append(dt)
            else:
                text_deltas.append(dt)
            is_fine_grained = True

    return (thought_deltas, text_deltas, None, is_fine_grained)


def extract_json_from_stdout(
    raw_stdout: str,
    *,
    cli_label: str,
    primary_text_key: str = "response",
) -> dict[str, Any]:
    """Extract the terminal JSON result object from ``stream-json`` or JSON stdout."""
    stripped = raw_stdout.strip()
    if not stripped:
        raise ProviderConfigurationError(f"{cli_label} returned empty stdout.")

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
        if (
            not (last_result.get("result") or last_result.get("response"))
            and streamed_text_chunks
        ):
            last_result[primary_text_key] = "".join(streamed_text_chunks)
        return last_result

    if streamed_text_chunks:
        return {"status": "SUCCESS", primary_text_key: "".join(streamed_text_chunks)}

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict) and not is_non_result_stream_event(parsed):
            return parsed
    except json.JSONDecodeError:
        pass

    for line in reversed(stripped.splitlines()):
        candidate = line.strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            try:
                parsed_line = json.loads(candidate)
                if isinstance(parsed_line, dict) and not is_non_result_stream_event(parsed_line):
                    return parsed_line
            except json.JSONDecodeError:
                continue

    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if 0 <= first_brace < last_brace:
        try:
            parsed_slice = json.loads(stripped[first_brace : last_brace + 1])
            if isinstance(parsed_slice, dict) and not is_non_result_stream_event(parsed_slice):
                return parsed_slice
        except json.JSONDecodeError:
            pass

    raise ProviderConfigurationError(f"{cli_label} output was not valid JSON: {stripped[:400]}")


def build_cli_prompt(
    spec: AgentTaskSpec[Any],
    *,
    subagent_models: dict[str, str],
    include_inline_schema: bool,
) -> str:
    """Build full non-interactive stdin prompt preserving all inline context intact."""
    parts: list[str] = [
        spec.system_instructions.strip(),
        (
            "EXECUTION DIRECTIVE: All required ontology, statistical profile, and sample "
            "data are provided inline below. Do NOT invoke filesystem, shell, or search "
            "tools. Respond immediately in a single turn with only the required JSON object."
        ),
        spec.prompt.strip(),
    ]
    if subagent_models:
        sub_lines = [
            f"- {sub_name}: model={sub_model}"
            for sub_name, sub_model in subagent_models.items()
        ]
        parts.append("DELEGATED SUBAGENT MODELS:\n" + "\n".join(sub_lines))
    if include_inline_schema and spec.output_schema is not None:
        schema_json = json.dumps(
            spec.output_schema.model_json_schema(),
            separators=(",", ":"),
        )
        parts.append(
            "Return ONLY a valid JSON object conforming to this JSON Schema "
            f"(no markdown fences or prose):\n{schema_json}"
        )
    parts.append(
        "IMPORTANT: Do NOT call any tools, read_file, run_command, or search tools. "
        "Produce your structured JSON response immediately in Turn 1."
    )
    return "\n\n".join(p for p in parts if p)


def _accepts_kwarg(fn: Callable[..., Any], kwarg_name: str) -> bool:
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        return False
    return kwarg_name in sig.parameters or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )


def invoke_cli_subprocess_sync(
    cmd: list[str],
    *,
    input_text: str,
    env: dict[str, str],
    cwd: Path,
    timeout_seconds: float,
    custom_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run CLI synchronously, piping ``input_text`` over stdin with runner signature safety."""
    if callable(custom_runner):
        if _accepts_kwarg(custom_runner, "input_text"):
            return custom_runner(cmd, input_text=input_text, env=env, timeout=timeout_seconds)
        if _accepts_kwarg(custom_runner, "input"):
            return custom_runner(cmd, input=input_text, env=env, timeout=timeout_seconds)
        return custom_runner(cmd, env=env, timeout=timeout_seconds)

    cwd.mkdir(parents=True, exist_ok=True)
    if getattr(subprocess.run, "__module__", "subprocess") != "subprocess" and not _accepts_kwarg(
        subprocess.run, "input"
    ):
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(cwd),
            timeout=timeout_seconds,
            check=False,
        )
    return subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(cwd),
        timeout=timeout_seconds,
        check=False,
    )


async def stream_cli_subprocess_lines(
    cmd: list[str],
    *,
    input_text: str,
    env: dict[str, str],
    cwd: Path,
    timeout_seconds: float,
    state_out: dict[str, Any],
    cli_label: str,
    custom_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> AsyncIterator[str]:
    """Execute CLI with ``input_text`` piped via stdin and yield stdout lines incrementally."""
    if (
        callable(custom_runner)
        or getattr(subprocess.run, "__module__", "subprocess") != "subprocess"
    ):
        completed = await asyncio.to_thread(
            invoke_cli_subprocess_sync,
            cmd,
            input_text=input_text,
            env=env,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            custom_runner=custom_runner,
        )
        state_out["returncode"] = completed.returncode
        state_out["stdout"] = completed.stdout or ""
        state_out["stderr"] = completed.stderr or ""
        for line in (completed.stdout or "").splitlines():
            yield line
        return

    cwd.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=str(cwd),
        limit=16 * 1024 * 1024,
    )
    stdout_lines: list[str] = []
    stderr_bytes = b""
    returncode = -1
    try:
        try:
            async with asyncio.timeout(timeout_seconds):
                if proc.stdin is not None:
                    proc.stdin.write(input_text.encode("utf-8"))
                    await proc.stdin.drain()
                    proc.stdin.close()
                assert proc.stdout is not None
                while True:
                    raw_bytes = await proc.stdout.readline()
                    if not raw_bytes:
                        break
                    decoded_line = raw_bytes.decode("utf-8", errors="replace")
                    stdout_lines.append(decoded_line)
                    yield decoded_line.rstrip("\r\n")
                stderr_bytes = await proc.stderr.read() if proc.stderr is not None else b""
                returncode = await proc.wait()
        except TimeoutError as exc:
            raise ProviderConfigurationError(
                f"{cli_label} timed out after {timeout_seconds}s."
            ) from exc
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()

    state_out["returncode"] = returncode
    state_out["stdout"] = "".join(stdout_lines)
    state_out["stderr"] = stderr_bytes.decode("utf-8", errors="replace")


def validate_cli_structured_output(
    spec: AgentTaskSpec[Any],
    *,
    raw_structured: Any,
    raw_response_text: str,
) -> Any:
    """Validate structured JSON output against ``spec.output_schema`` when present."""
    if spec.output_schema is None:
        return raw_structured
    if raw_structured is not None:
        if isinstance(raw_structured, dict):
            return spec.output_schema.model_validate(raw_structured)
        if isinstance(raw_structured, str):
            return spec.output_schema.model_validate_json(
                _strip_markdown_json_fences(raw_structured)
            )
        return raw_structured
    if raw_response_text.strip():
        return spec.output_schema.model_validate_json(
            _strip_markdown_json_fences(raw_response_text)
        )
    return None


def make_cli_turn_meta(
    spec: AgentTaskSpec[Any],
    payload: dict[str, Any],
    *,
    session_id: str,
    validated_structured: Any,
    subagent_models: dict[str, str],
) -> dict[str, Any]:
    """Normalize usage metrics and completion metadata for CLI adapters."""
    raw_usage = payload.get("usage")
    usage_dict: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    in_tok = int(usage_dict.get("input_tokens") or 0)
    cached_tok = int(
        usage_dict.get("cache_read_input_tokens")
        or usage_dict.get("cache_read_tokens")
        or usage_dict.get("cached_input_tokens")
        or 0
    )
    out_tok = int(usage_dict.get("output_tokens") or 0)
    think_tok = int(
        usage_dict.get("thinking_tokens") or usage_dict.get("reasoning_tokens") or 0
    )
    tot_tok = int(usage_dict.get("total_tokens") or (in_tok + out_tok + think_tok))
    num_turns = max(1, int(payload.get("num_turns") or usage_dict.get("num_turns") or 1))
    cost_usd = float(
        payload.get("total_cost_usd")
        or payload.get("cost_usd")
        or usage_dict.get("total_cost_usd")
        or usage_dict.get("cost_usd")
        or 0.0
    )
    return {
        "session_id": session_id or spec.resume_session_id or spec.task_id,
        "structured_output": validated_structured,
        "subagent_models": subagent_models,
        "usage": {
            "input_tokens": in_tok,
            "cached_input_tokens": cached_tok,
            "output_tokens": out_tok,
            "reasoning_tokens": think_tok,
            "total_tokens": tot_tok,
            "model_calls": num_turns,
            "cost_usd": cost_usd,
        },
    }


__all__ = [
    "HarnessStreamEvent",
    "build_cli_prompt",
    "extract_json_from_stdout",
    "invoke_cli_subprocess_sync",
    "is_non_result_stream_event",
    "make_cli_turn_meta",
    "parse_ndjson_stream_line",
    "stream_cli_subprocess_lines",
    "validate_cli_structured_output",
]
