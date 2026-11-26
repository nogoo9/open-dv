"""Host CLI subprocess runner for agy (1.5.14) and claude-cli (2.1.318)."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from opendv.agents.cli_subprocess_base import (
    _MAX_CLI_FLAG_CHARS,
    IsolatedTempCwd,
    build_isolated_child_env,
    build_stateless_turn_prompt,
    count_cl100k_tokens,
    extract_json_from_stdout,
    is_auth_error,
    parse_ndjson_step_events,
    parse_ndjson_stream_line,
    redact_telemetry_text,
    stream_cli_subprocess_lines,
    unwrap_windows_npm_shim,
    validate_cli_structured_output,
)
from opendv.agents.ports import (
    AgentHarnessPort,
    AgentTurnRequest,
    AgentTurnResult,
    RoleKind,
    RunEventSink,
    ScoutToolDispatcher,
    merge_model_overrides,
    resolve_model_for_agent,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import RunStep, RunStreamEvent
from opendv.models.project import HarnessProvider

CliProvider = Literal["agy", "claude-cli"]
SubprocessStreamFn = Callable[..., AsyncIterator[str]]


def build_empty_mcp_config_json() -> str:
    """Return canonical empty MCP config JSON for Step 6 zero-tool isolation."""
    return json.dumps({"mcpServers": {}}, separators=(",", ":"))


def build_broker_mcp_config_json(broker_url: str, *, role_kind: RoleKind) -> str:
    """Build Step 4 MCP broker JSON referencing the role-scoped bearer env var."""
    if role_kind == "none":
        return build_empty_mcp_config_json()
    token_env = "OPENDV_MCP_COORD_TOKEN" if role_kind == "coord" else "OPENDV_MCP_SCOUT_TOKEN"
    return json.dumps(
        {
            "mcpServers": {
                "opendv_broker": {
                    "type": "http",
                    "url": broker_url,
                    "headers": {"Authorization": f"Bearer ${{{token_env}}}"},
                }
            }
        },
        separators=(",", ":"),
    )


_redact_telemetry_text = redact_telemetry_text
_build_turn_prompt = build_stateless_turn_prompt


def build_agy_command(
    binary: str,
    *,
    model_id: str,
    prompt: str,
    json_schema_path: str | None = None,
    settings_path: str | None = None,
    sandbox: bool = True,
    effort: str | None = None,
) -> tuple[list[str], str]:
    """Build agy 1.5.14 CLI argv and stdin text (no unsupported mcp/tools flags)."""
    cmd: list[str] = [
        binary,
        "--input-format",
        "text",
        "--output-format",
        "stream-json",
        "--model",
        model_id,
        "--disable-slash-commands",
    ]
    if sandbox:
        cmd.append("--sandbox")
    if effort:
        cmd.extend(["--effort", effort])
    if json_schema_path:
        cmd.extend(["--json-schema", json_schema_path])
    if settings_path:
        cmd.extend(["--settings", settings_path])

    if len(prompt) <= _MAX_CLI_FLAG_CHARS:
        cmd.append(f"--print={prompt}")
        stdin_text = ""
    else:
        # agy 1.5.14 ignores stdin when --print=<text> is set; omit it to read stdin.
        stdin_text = prompt
    return cmd, stdin_text


def build_claude_cli_command(
    binary: str,
    *,
    model_id: str,
    step: RunStep,
    mcp_config_path: str,
    prompt: str,
    system_prompt: str | None = None,
    json_schema_arg: str | None = None,
    effort: str = "low",
) -> tuple[list[str], str]:
    """Build claude-cli 2.1.318 argv and stdin text with strict MCP & tool isolation."""
    cmd: list[str] = [
        binary,
        "-p",
        "--model",
        model_id,
        "--output-format",
        "stream-json",
        "--verbose",
        "--disable-slash-commands",
        "--effort",
        effort,
        "--tools",
        "",
        "--mcp-config",
        mcp_config_path,
        "--strict-mcp-config",
    ]
    if step == "step4":
        cmd.extend(["--allowedTools", "mcp__opendv_broker__*"])

    extra_prefix_parts: list[str] = []
    if system_prompt and system_prompt.strip():
        sys_txt = system_prompt.strip()
        if len(sys_txt) <= _MAX_CLI_FLAG_CHARS:
            cmd.extend(["--system-prompt", sys_txt])
        else:
            extra_prefix_parts.append(sys_txt)
    if json_schema_arg and len(json_schema_arg) <= _MAX_CLI_FLAG_CHARS:
        cmd.extend(["--json-schema", json_schema_arg])
    elif json_schema_arg:
        extra_prefix_parts.append(
            f"Return ONLY valid JSON conforming to schema:\n{json_schema_arg}"
        )

    stdin_text = "\n\n".join([*extra_prefix_parts, prompt]) if extra_prefix_parts else prompt
    return cmd, stdin_text


def resolve_cli_binary(
    provider: CliProvider,
    *,
    explicit_bin: str | None = None,
    env: Mapping[str, str] | None = None,
    which_fn: Callable[[str], str | None] | None = None,
    is_file_fn: Callable[[str], bool] | None = None,
) -> str:
    """Locate agy or claude binary, unwrapping Windows npm .cmd shims."""
    active_env = os.environ if env is None else env
    if which_fn is not None:
        lookup: Callable[[str], str | None] = which_fn
    elif env is not None:
        env_path = active_env.get("PATH", "")

        def _lookup(cmd: str) -> str | None:
            return shutil.which(cmd, path=env_path) if env_path else None

        lookup = _lookup
    else:
        lookup = shutil.which
    check_file = os.path.isfile if is_file_fn is None else is_file_fn

    env_var = "OPENDV_AGY_CLI_BIN" if provider == "agy" else "OPENDV_CLAUDE_CLI_BIN"
    candidates = [explicit_bin, active_env.get(env_var)]
    has_explicit = False
    for cand in candidates:
        if isinstance(cand, str) and cand.strip():
            has_explicit = True
            c_str = cand.strip()
            if check_file(c_str):
                return unwrap_windows_npm_shim(c_str, is_file_fn=check_file)
            resolved = lookup(c_str)
            if resolved:
                return unwrap_windows_npm_shim(resolved, is_file_fn=check_file)

    if not has_explicit:
        names = (
            ("agy", "agy.exe", "agy.cmd")
            if provider == "agy"
            else ("claude", "claude.exe", "claude.cmd")
        )
        for name in names:
            found = lookup(name)
            if found:
                return unwrap_windows_npm_shim(found, is_file_fn=check_file)

    raise OpenDVError(
        f"CLI executable for provider '{provider}' was not found.",
        error_code=ErrorCode.DRIVER_MISSING,
    )


def _extract_cli_error_text(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    for key in ("error", "message", "result", "response"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, Mapping):
            nested = _extract_cli_error_text(val)
            if nested:
                return nested
    return ""


class SubprocessHarnessRunner(AgentHarnessPort):
    """Production CLI subprocess implementation of AgentHarnessPort for agy and claude-cli."""

    def __init__(
        self,
        provider: CliProvider = "agy",
        *,
        binary_path: str | None = None,
        api_key: str | None = None,
        model_overrides: Mapping[str, str] | None = None,
        env: Mapping[str, str] | None = None,
        stream_fn: SubprocessStreamFn | None = None,
        which_fn: Callable[[str], str | None] | None = None,
    ) -> None:
        self._provider: CliProvider = provider
        self._binary_path = binary_path
        self._api_key = api_key
        self._model_overrides = dict(model_overrides or {})
        self._env = dict(os.environ if env is None else env)
        self._stream_fn: SubprocessStreamFn = stream_fn or stream_cli_subprocess_lines
        self._which_fn = which_fn

    @property
    def provider_name(self) -> HarnessProvider:
        return self._provider

    def _emit(
        self,
        sink: RunEventSink | None,
        seq_ref: list[int],
        request: AgentTurnRequest,
        event_type: Literal[
            "SESSION_START",
            "THOUGHT_DELTA",
            "TEXT_DELTA",
            "TOOL_CALL",
            "TOOL_RESULT",
        ],
        payload: dict[str, Any],
    ) -> None:
        if sink is None:
            return
        current_seq_fn = getattr(sink, "current_seq", None)
        if callable(current_seq_fn):
            seq_ref[0] = max(seq_ref[0], int(current_seq_fn(request.run_id)))
        seq_ref[0] += 1
        sink.emit(
            RunStreamEvent(
                seq=seq_ref[0],
                run_id=request.run_id,
                ts=datetime.now(tz=UTC).isoformat(),
                event_type=event_type,
                step=request.step,
                payload=payload,
            )
        )

    async def run_turn(
        self,
        request: AgentTurnRequest,
        *,
        event_sink: RunEventSink | None = None,
        scout_dispatcher: ScoutToolDispatcher | None = None,
    ) -> AgentTurnResult:
        _ = scout_dispatcher
        binary = resolve_cli_binary(
            self._provider,
            explicit_bin=self._binary_path,
            env=self._env,
            which_fn=self._which_fn,
        )
        resolved = resolve_model_for_agent(
            self._provider,
            request.model_tier,
            agent_id=request.agent_id,
            model_overrides=merge_model_overrides(self._model_overrides, request),
            cegar_policy=request.cegar_policy,
            env=self._env,
        )
        role_kind: RoleKind = request.role_kind if request.step == "step4" else "none"
        child_env = build_isolated_child_env(
            provider=self._provider,
            step=request.step,
            role_kind=role_kind,
            role_token=request.role_token,
            api_key=self._api_key,
            parent_env=self._env,
        )
        schema_json = (
            json.dumps(request.output_schema.model_json_schema(), separators=(",", ":"))
            if request.output_schema is not None
            else None
        )

        seq_ref = [int(request.checkpoint.last_seq) if request.checkpoint else 0]
        self._emit(
            event_sink,
            seq_ref,
            request,
            "SESSION_START",
            {"provider": self._provider, "model_id": resolved.model_id},
        )

        thought_deltas: list[str] = []
        text_deltas: list[str] = []
        tool_events: list[dict[str, Any]] = []
        run_state: dict[str, Any] = {"returncode": 0, "stdout": "", "stderr": ""}
        last_result_payload: dict[str, Any] | None = None
        seen_fine = False

        with IsolatedTempCwd() as temp_cwd:
            schema_file: Path | None = None
            inline_agy_schema: str | None = None
            if schema_json and self._provider == "agy":
                is_complex_schema = (
                    len(schema_json) > 4096
                    or '"$ref":"#/$defs/ArrayType"' in schema_json
                    or '"$ref":"#/$defs/MapType"' in schema_json
                )
                if is_complex_schema:
                    inline_agy_schema = schema_json
                else:
                    schema_file = temp_cwd / "schema.json"
                    schema_file.write_text(schema_json, encoding="utf-8")

            if self._provider == "agy":
                full_prompt = _build_turn_prompt(request, inline_schema_json=inline_agy_schema)
                cmd, stdin_text = build_agy_command(
                    binary,
                    model_id=resolved.model_id,
                    prompt=full_prompt,
                    json_schema_path=str(schema_file) if schema_file else None,
                )
            else:
                mcp_path = temp_cwd / "mcp_config.json"
                if request.step == "step4" and request.broker_url:
                    mcp_json = build_broker_mcp_config_json(request.broker_url, role_kind=role_kind)
                else:
                    mcp_json = build_empty_mcp_config_json()
                mcp_path.write_text(mcp_json, encoding="utf-8")
                turn_prompt = build_stateless_turn_prompt(request, include_system=False)
                effort = str(self._env.get("OPENDV_CLAUDE_EFFORT") or "low").strip().lower()
                cmd, stdin_text = build_claude_cli_command(
                    binary,
                    model_id=resolved.model_id,
                    step=request.step,
                    mcp_config_path=str(mcp_path),
                    prompt=turn_prompt,
                    system_prompt=request.system_instructions,
                    json_schema_arg=schema_json,
                    effort=effort or "low",
                )

            async for line in self._stream_fn(
                cmd,
                input_text=stdin_text,
                env=child_env,
                cwd=temp_cwd,
                timeout_seconds=float(request.budget.timeout_seconds),
                state_out=run_state,
                cli_label=f"{self._provider} CLI",
            ):
                for tev in parse_ndjson_step_events(line):
                    tool_events.append(tev)
                    if tev["kind"] == "TOOL_CALL":
                        self._emit(
                            event_sink,
                            seq_ref,
                            request,
                            "TOOL_CALL",
                            {
                                "tool_name": tev["tool_name"],
                                "args_redacted": _redact_telemetry_text(
                                    json.dumps(tev["tool_args"], separators=(",", ":"))
                                ),
                            },
                        )
                    elif tev["kind"] == "TOOL_RESULT":
                        self._emit(
                            event_sink,
                            seq_ref,
                            request,
                            "TOOL_RESULT",
                            {
                                "tool_name": tev["tool_name"],
                                "ok": tev["ok"],
                                "summary": _redact_telemetry_text(str(tev["summary"])),
                            },
                        )
                t_chunks, c_chunks, res_obj, is_fine = parse_ndjson_stream_line(
                    line,
                    seen_fine_grained_deltas=seen_fine,
                )
                if is_fine:
                    seen_fine = True
                for tc in t_chunks:
                    thought_deltas.append(tc)
                    self._emit(
                        event_sink,
                        seq_ref,
                        request,
                        "THOUGHT_DELTA",
                        {"delta_text": _redact_telemetry_text(tc)},
                    )
                for cc in c_chunks:
                    text_deltas.append(cc)
                    self._emit(
                        event_sink,
                        seq_ref,
                        request,
                        "TEXT_DELTA",
                        {"delta_text": _redact_telemetry_text(cc)},
                    )
                if res_obj is not None:
                    last_result_payload = dict(res_obj)

        if int(run_state.get("returncode") or 0) != 0:
            raw_stderr = str(run_state.get("stderr") or "").strip()
            payload_err = _extract_cli_error_text(last_result_payload)
            raw_stdout = str(run_state.get("stdout") or "").strip()
            parts = [p for p in (raw_stderr, payload_err) if p]
            err_msg = (
                " | ".join(parts)
                if parts
                else (raw_stdout or f"exit {run_state['returncode']}")
            )
            err_code = (
                ErrorCode.AGENT_NOT_CONFIGURED
                if is_auth_error(err_msg, provider=self._provider)
                else ErrorCode.AGENT_EXECUTION_FAILED
            )
            raise OpenDVError(
                f"{self._provider} CLI failed: {_redact_telemetry_text(err_msg)[:300]}",
                error_code=err_code,
                run_id=request.run_id,
            )

        payload = last_result_payload or extract_json_from_stdout(
            str(run_state.get("stdout") or ""),
            cli_label=f"{self._provider} CLI",
            primary_text_key="result" if self._provider == "claude-cli" else "response",
        )
        if payload.get("is_error") is True:
            raw_err_msg = _extract_cli_error_text(payload) or "CLI returned error payload."
            err_code = (
                ErrorCode.AGENT_NOT_CONFIGURED
                if is_auth_error(raw_err_msg, provider=self._provider)
                else ErrorCode.AGENT_EXECUTION_FAILED
            )
            raise OpenDVError(
                f"{self._provider} CLI returned error payload: "
                f"{_redact_telemetry_text(raw_err_msg)[:300]}",
                error_code=err_code,
                run_id=request.run_id,
            )

        raw_structured = payload.get("structured_output")
        raw_res = (
            payload.get("result") if payload.get("result") is not None else payload.get("response")
        )
        if raw_structured is None and isinstance(raw_res, dict):
            raw_structured = raw_res
        text_output = raw_res if isinstance(raw_res, str) else "".join(text_deltas)
        if not text_output and raw_structured is not None:
            text_output = (
                raw_structured if isinstance(raw_structured, str) else json.dumps(raw_structured)
            )

        validated = validate_cli_structured_output(
            request.output_schema,
            raw_structured=raw_structured,
            raw_response_text=text_output,
            cli_label=f"{self._provider} CLI",
        )
        raw_usage = payload.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        in_tok = int(usage.get("input_tokens") or 0)
        out_tok = int(usage.get("output_tokens") or 0)
        tot_tok = int(usage.get("total_tokens") or (in_tok + out_tok))
        if tot_tok > 0:
            tok_source: Literal["reported", "estimated"] = "reported"
        else:
            in_tok = count_cl100k_tokens(request.prompt)
            out_tok = count_cl100k_tokens(text_output)
            tot_tok = in_tok + out_tok
            tok_source = "estimated"

        return AgentTurnResult(
            agent_id=request.agent_id,
            provider=self._provider,
            model_id=resolved.model_id,
            text_output=text_output,
            structured_output=validated,
            turns_used=max(1, int(payload.get("num_turns") or 1)),
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=tot_tok,
            token_count_source=tok_source,
            thought_deltas=thought_deltas,
            text_deltas=text_deltas,
            tool_events=tool_events,
        )


__all__ = [
    "CliProvider",
    "SubprocessHarnessRunner",
    "SubprocessStreamFn",
    "build_agy_command",
    "build_broker_mcp_config_json",
    "build_claude_cli_command",
    "build_empty_mcp_config_json",
    "resolve_cli_binary",
]
