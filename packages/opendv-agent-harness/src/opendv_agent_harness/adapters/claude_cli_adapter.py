"""Claude Code CLI (`claude`) Adapter for the Open-DV Agent Harness.

Executes non-interactive agentic turns via the local logged-in Claude Code CLI (`claude -p`)
piping prompts over stdin with `--output-format stream-json`, `--json-schema <schema_json>`,
`--model <model_id>`, `--system-prompt <instructions>`, `--agents <subagents_json>`,
`--disable-slash-commands`, and `--tools ""`.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from opendv_agent_harness.adapters.antigravity_adapter import ProviderConfigurationError
from opendv_agent_harness.adapters.cli_subprocess_base import (
    build_cli_prompt,
    extract_json_from_stdout,
    make_cli_turn_meta,
    parse_ndjson_stream_line,
    stream_cli_subprocess_lines,
    validate_cli_structured_output,
)
from opendv_agent_harness.models import (
    AgentPersona,
    AgentTaskSpec,
    HarnessConfig,
    HarnessEventKind,
    HarnessStreamEvent,
    ModelTier,
    OutputT,
    ToolExecutionContext,
)
from opendv_agent_harness.protocol import BaseVaultAgentHarness
from opendv_agent_harness.routing import (
    is_cli_schema_omission_retry_allowed,
    resolve_provider_model,
)
from opendv_contracts.fallbacks import FallbackKey

_MAX_CLI_FLAG_CHARS = 7500
_AGY_ONLY_CLAUDE_MODEL_MAP: dict[str, str] = {
    "claude-opus-5-5-high": "claude-opus-4-6",
    "claude-sonnet-5-5-medium": "claude-sonnet-4-6",
}


def _unwrap_windows_npm_shim(bin_path: str) -> str:
    """On Windows, bypass npm's claude.cmd shim to avoid cmd.exe 8KB/escaping bugs."""
    if os.name == "nt" and bin_path.lower().endswith((".cmd", ".bat")):
        exe = (
            Path(bin_path).parent
            / "node_modules"
            / "@anthropic-ai"
            / "claude-code"
            / "bin"
            / "claude.exe"
        )
        if exe.is_file():
            return str(exe)
    return bin_path


def _extract_json_from_stdout(raw_stdout: str) -> dict[str, Any]:
    """Extract the JSON result object emitted by ``claude -p --output-format stream-json``."""
    return extract_json_from_stdout(
        raw_stdout,
        cli_label="Claude Code CLI ('claude')",
        primary_text_key="result",
    )


class ClaudeCliHarnessAdapter(BaseVaultAgentHarness):
    """Production Adapter: Claude Code CLI (`claude`) via active logged-in CLI session."""

    def __init__(self, config: HarnessConfig, *, sdk_module: Any | None = None) -> None:
        super().__init__(config)
        self._sdk_override = sdk_module

    @property
    def provider_name(self) -> str:
        """Return the canonical provider identifier 'claude-cli'."""
        return "claude-cli"

    def resolve_model(
        self,
        tier: ModelTier,
        persona: AgentPersona | None = None,
    ) -> str:
        """Resolve logical ModelTier ('reasoning'/'fast') and AgentPersona to a Claude model ID."""
        return resolve_provider_model(
            self.provider_name,
            tier,
            overrides=self._config.model_overrides,
            persona=persona,
            agent_models=self._config.agent_models,
        )

    def _resolve_claude_binary(self) -> str | None:
        """Locate the `claude` CLI executable from config, environment, or PATH (CFG-06)."""
        explicit_candidates = (
            self._config.env.get("OPENDV_CLAUDE_CLI_BIN"),
            self._config.extra_provider_options.get("claude_binary"),
            os.environ.get("OPENDV_CLAUDE_CLI_BIN"),
        )
        has_explicit = False
        for candidate in explicit_candidates:
            if isinstance(candidate, str) and candidate.strip():
                has_explicit = True
                cand_str = candidate.strip()
                if Path(cand_str).is_file():
                    return _unwrap_windows_npm_shim(cand_str)
                which_cand = shutil.which(cand_str)
                if which_cand:
                    return _unwrap_windows_npm_shim(which_cand)
        if has_explicit:
            return None
        for bin_name in ("claude", "claude.exe", "claude.cmd"):
            found = shutil.which(bin_name)
            if found:
                return _unwrap_windows_npm_shim(found)
        return None

    def _resolve_subagent_models(self, spec: AgentTaskSpec[Any]) -> dict[str, str]:
        return {
            sub.name: (sub.model_override or self.resolve_model(sub.model_tier, sub.persona))
            for sub in spec.subagents
        }

    def _build_agents_json(self, spec: AgentTaskSpec[Any]) -> str | None:
        if not spec.subagents:
            return None
        subagent_models = self._resolve_subagent_models(spec)
        agents_payload: dict[str, dict[str, str]] = {}
        for sub in spec.subagents:
            agents_payload[sub.name] = {
                "description": sub.description,
                "prompt": sub.system_instructions,
                "model": subagent_models[sub.name],
            }
        return json.dumps(agents_payload, separators=(",", ":"))

    def _build_cli_prompt(self, spec: AgentTaskSpec[Any], *, include_inline_schema: bool) -> str:
        """Construct the full non-interactive stdin prompt sent to ``claude -p``."""
        return build_cli_prompt(
            spec,
            subagent_models=self._resolve_subagent_models(spec),
            include_inline_schema=include_inline_schema,
        )

    async def _run_and_consume(
        self,
        cmd: list[str],
        *,
        input_text: str,
        proc_env: dict[str, str],
        run_state: dict[str, Any],
        make_event: Any,
        streamed_text_chunks: list[str],
    ) -> AsyncIterator[HarnessStreamEvent]:
        custom_runner = self._config.extra_provider_options.get("claude_cli_runner")
        seen_fine_grained = False
        last_result_payload: dict[str, Any] | None = None
        async for raw_line in stream_cli_subprocess_lines(
            cmd,
            input_text=input_text,
            env=proc_env,
            cwd=self._config.state_dir,
            timeout_seconds=self._config.budget.timeout_seconds,
            state_out=run_state,
            cli_label="Claude Code CLI ('claude')",
            custom_runner=custom_runner,
        ):
            thought_deltas, text_deltas, result_obj, is_fine = parse_ndjson_stream_line(
                raw_line,
                seen_fine_grained_deltas=seen_fine_grained,
            )
            if is_fine:
                seen_fine_grained = True
            for t_chunk in thought_deltas:
                yield make_event(HarnessEventKind.THOUGHT_DELTA, delta_text=t_chunk)
            for c_chunk in text_deltas:
                streamed_text_chunks.append(c_chunk)
                yield make_event(HarnessEventKind.TEXT_DELTA, delta_text=c_chunk)
            if result_obj is not None:
                last_result_payload = dict(result_obj)
        run_state["last_result_payload"] = last_result_payload

    async def _stream_provider_task(
        self,
        spec: AgentTaskSpec[OutputT],
        ctx: ToolExecutionContext,
        model_id: str,
    ) -> AsyncIterator[HarnessStreamEvent]:
        """Execute task via local `claude` CLI piping prompt over stdin."""
        model_id = (
            spec.model_override or model_id or self.resolve_model(spec.model_tier, spec.persona)
        )
        subagent_models = self._resolve_subagent_models(spec)
        if self._sdk_override is not None:
            from opendv_agent_harness.adapters.claude_adapter import ClaudeAgentHarnessAdapter

            delegate = ClaudeAgentHarnessAdapter(self._config, sdk_module=self._sdk_override)
            async for ev in delegate._stream_provider_task(spec, ctx, model_id):
                yield ev.model_copy(update={"provider": self.provider_name})
            return

        claude_bin = self._resolve_claude_binary()
        if claude_bin is None:
            raise ProviderConfigurationError(
                "Claude Code CLI ('claude') executable was not found. Checked sources: "
                "1) extra_provider_options.claude_binary, "
                "2) env var OPENDV_CLAUDE_CLI_BIN, 3) PATH."
            )

        def make_event(kind: HarnessEventKind, **kwargs: Any) -> HarnessStreamEvent:
            return self._make_event(spec, ctx, kind, **kwargs)

        yield make_event(HarnessEventKind.SESSION_START)
        proc_env = dict(os.environ)
        proc_env.update(self._config.env)
        proc_env["NO_COLOR"] = "1"

        sys_prompt = spec.system_instructions.strip()
        agents_json = self._build_agents_json(spec)
        schema_json_str = (
            json.dumps(spec.output_schema.model_json_schema(), separators=(",", ":"))
            if spec.output_schema is not None
            else None
        )
        inline_schema = schema_json_str is not None and len(schema_json_str) > _MAX_CLI_FLAG_CHARS
        prompt_text = self._build_cli_prompt(spec, include_inline_schema=inline_schema)

        cli_model_id = _AGY_ONLY_CLAUDE_MODEL_MAP.get(model_id, model_id)
        effort = (
            str(
                self._config.extra_provider_options.get("claude_effort")
                or self._config.env.get("OPENDV_CLAUDE_EFFORT")
                or os.environ.get("OPENDV_CLAUDE_EFFORT")
                or "low"
            )
            .strip()
            .lower()
        )
        cmd: list[str] = [
            claude_bin,
            "-p",
            "--model",
            cli_model_id,
            "--output-format",
            "stream-json",
            "--verbose",
            "--disable-slash-commands",
            "--tools",
            "",
        ]
        if effort:
            cmd.extend(["--effort", effort])
        if sys_prompt and len(sys_prompt) <= _MAX_CLI_FLAG_CHARS:
            cmd.extend(["--system-prompt", sys_prompt])
        if agents_json is not None and len(agents_json) <= _MAX_CLI_FLAG_CHARS:
            cmd.extend(["--agents", agents_json])
        if schema_json_str is not None and not inline_schema:
            cmd.extend(["--json-schema", schema_json_str])
        if spec.resume_session_id:
            cmd.extend(["--resume", spec.resume_session_id])

        streamed_text_chunks: list[str] = []
        run_state: dict[str, Any] = {"returncode": 0, "stdout": "", "stderr": ""}
        async for ev in self._run_and_consume(
            cmd,
            input_text=prompt_text,
            proc_env=proc_env,
            run_state=run_state,
            make_event=make_event,
            streamed_text_chunks=streamed_text_chunks,
        ):
            yield ev
        last_result_payload = run_state.get("last_result_payload")

        allow_schema_retry = is_cli_schema_omission_retry_allowed(self._config)
        stderr_lower = str(run_state.get("stderr") or "").lower()
        is_schema_rejection = any(
            term in stderr_lower for term in ("schema", "--json-schema", "$defs", "validation")
        )
        if (
            allow_schema_retry
            and run_state["returncode"] != 0
            and schema_json_str is not None
            and is_schema_rejection
        ):
            yield make_event(
                HarnessEventKind.OBSERVABILITY_EVENT,
                observability_event={
                    "event_type": "FALLBACK_APPLIED",
                    "fallback_key": str(FallbackKey.CLI_SCHEMA_OMISSION_RETRY),
                    "reason": "cli_schema_rejection",
                    "detail": stderr_lower[:200],
                },
            )
            streamed_text_chunks.clear()
            fallback_prompt = self._build_cli_prompt(spec, include_inline_schema=True)
            fallback_cmd: list[str] = [
                claude_bin,
                "-p",
                "--model",
                cli_model_id,
                "--output-format",
                "stream-json",
                "--verbose",
                "--disable-slash-commands",
                "--tools",
                "",
            ]
            if effort:
                fallback_cmd.extend(["--effort", effort])
            if sys_prompt and len(sys_prompt) <= _MAX_CLI_FLAG_CHARS:
                fallback_cmd.extend(["--system-prompt", sys_prompt])
            if agents_json is not None and len(agents_json) <= _MAX_CLI_FLAG_CHARS:
                fallback_cmd.extend(["--agents", agents_json])
            if spec.resume_session_id:
                fallback_cmd.extend(["--resume", spec.resume_session_id])
            run_state = {"returncode": 0, "stdout": "", "stderr": ""}
            async for ev in self._run_and_consume(
                fallback_cmd,
                input_text=fallback_prompt,
                proc_env=proc_env,
                run_state=run_state,
                make_event=make_event,
                streamed_text_chunks=streamed_text_chunks,
            ):
                yield ev
            last_result_payload = run_state.get("last_result_payload")

        if run_state["returncode"] != 0:
            stderr_msg = str(run_state["stderr"]).strip()
            if not stderr_msg and last_result_payload is not None:
                stderr_msg = str(
                    last_result_payload.get("error")
                    or last_result_payload.get("result")
                    or last_result_payload.get("response")
                    or ""
                ).strip()
            if not stderr_msg:
                stderr_msg = f"exit code {run_state['returncode']}"
            raise ProviderConfigurationError(
                f"Claude Code CLI ('claude') failed ({stderr_msg}). "
                "Ensure 'claude' is logged in and accessible."
            )

        if last_result_payload is not None:
            payload = last_result_payload
            if not (payload.get("result") or payload.get("response")) and streamed_text_chunks:
                payload["result"] = "".join(streamed_text_chunks)
        else:
            payload = _extract_json_from_stdout(str(run_state["stdout"]))

        status_raw = payload.get("status") or payload.get("subtype") or "SUCCESS"
        status_val = str(status_raw).upper()
        if payload.get("is_error") is True or status_val not in {"SUCCESS", "COMPLETED", "OK"}:
            err_detail = str(
                payload.get("error")
                or payload.get("result")
                or payload.get("response")
                or status_val
            )
            raise ProviderConfigurationError(
                f"Claude Code CLI ('claude') returned error status '{status_val}': {err_detail}"
            )

        raw_structured = payload.get("structured_output")
        raw_result = (
            payload.get("result") if payload.get("result") is not None else payload.get("response")
        )
        if raw_structured is None and isinstance(raw_result, dict):
            raw_structured = raw_result
        raw_response_text = str(raw_result) if isinstance(raw_result, str) else ""
        if not raw_response_text and streamed_text_chunks:
            raw_response_text = "".join(streamed_text_chunks)
        if not raw_response_text and raw_structured is not None:
            raw_response_text = (
                raw_structured if isinstance(raw_structured, str) else json.dumps(raw_structured)
            )
        if raw_response_text and not streamed_text_chunks:
            yield make_event(HarnessEventKind.TEXT_DELTA, delta_text=raw_response_text)

        for obs_ev in ctx.pop_unstreamed_events():
            yield make_event(HarnessEventKind.OBSERVABILITY_EVENT, obs_event=obs_ev)
            if obs_ev.get("status") == "ASK-USER":
                yield make_event(HarnessEventKind.STEWARD_HOLD, obs_event=obs_ev)

        validated = validate_cli_structured_output(
            spec,
            raw_structured=raw_structured,
            raw_response_text=raw_response_text,
            cli_label="Claude Code CLI ('claude')",
            denied_actions=payload.get("denied_actions"),
        )
        turn_meta = make_cli_turn_meta(
            spec,
            payload,
            session_id=str(
                payload.get("session_id")
                or payload.get("conversation_id")
                or spec.resume_session_id
                or spec.task_id
            ),
            validated_structured=validated,
            subagent_models=subagent_models,
        )
        yield make_event(HarnessEventKind.TURN_COMPLETE, tool_result=turn_meta)


ClaudeCodeCliAdapter = ClaudeCliHarnessAdapter

__all__ = [
    "ClaudeCliHarnessAdapter",
    "ClaudeCodeCliAdapter",
    "_extract_json_from_stdout",
]
