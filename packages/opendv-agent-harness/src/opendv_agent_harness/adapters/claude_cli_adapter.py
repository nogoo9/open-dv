"""Claude Code CLI (`claude`) Adapter for the Open-DV Agent Harness.

Executes non-interactive agentic turns via the local logged-in Claude Code CLI (`claude -p`)
with `--output-format json`, `--json-schema <schema_json>`, `--model <model_id>`,
`--system-prompt <instructions>`, `--agents <subagents_json>`, `--disable-slash-commands`,
and `--tools ""`.

Conforms to docs/03-verification-and-agentic-architecture.md,
docs/06-explainability-and-observability-spec.md,
and docs/09-agnostic-agent-harness-and-sdk-spec.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from opendv_agent_harness.adapters.antigravity_adapter import (
    ProviderConfigurationError,
    _strip_markdown_json_fences,
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
from opendv_agent_harness.routing import resolve_provider_model

_STAGE_CONTEXT_MARKERS: tuple[str, ...] = (
    "\n__OPENDV_STAGE_CONTEXT_JSON__:",
    "<!-- OPENDV_STAGE_CTX_B64:",
)


def _strip_internal_stage_context(prompt: str) -> str:
    """Remove internal base64/JSON stage context footers before dispatching to `claude` CLI."""
    cleaned = prompt
    for marker in _STAGE_CONTEXT_MARKERS:
        if marker in cleaned:
            cleaned, _ = cleaned.split(marker, 1)
    return cleaned.strip()


def _extract_json_from_stdout(raw_stdout: str) -> dict[str, Any]:
    """Extract the JSON result object emitted by `claude -p --output-format json`."""
    stripped = raw_stdout.strip()
    if not stripped:
        raise ProviderConfigurationError("Claude Code CLI ('claude') returned empty stdout.")

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    for line in reversed(stripped.splitlines()):
        candidate = line.strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            try:
                parsed_line = json.loads(candidate)
                if isinstance(parsed_line, dict):
                    return parsed_line
            except json.JSONDecodeError:
                continue

    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if 0 <= first_brace < last_brace:
        try:
            parsed_slice = json.loads(stripped[first_brace : last_brace + 1])
            if isinstance(parsed_slice, dict):
                return parsed_slice
        except json.JSONDecodeError:
            pass

    raise ProviderConfigurationError(
        f"Claude Code CLI ('claude') output was not valid JSON: {stripped[:400]}"
    )


class ClaudeCliHarnessAdapter(BaseVaultAgentHarness):
    """Production Adapter: Claude Code CLI (`claude`) via active logged-in CLI session.

    Registered under `provider: 'claude-cli'` and `provider: 'claude-code'`.
    Invokes the local `claude` binary in non-interactive `--print` (`-p`) mode with
    `--model <model_id>`, `--output-format json`, `--json-schema <schema_json>`,
    `--system-prompt <instructions>`, `--agents <subagents_json>`,
    `--disable-slash-commands`, and `--tools ""`.
    """

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
        """Locate the `claude` CLI executable from config, environment, PATH, or user dirs."""
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
                if Path(cand_str).is_file() or shutil.which(cand_str):
                    return cand_str
        if has_explicit:
            return None

        for bin_name in ("claude", "claude.exe", "claude.cmd"):
            found = shutil.which(bin_name)
            if found:
                return found

        if getattr(shutil.which, "__module__", "") != "shutil":
            return None

        fallback_paths = (
            Path.home() / ".local" / "bin" / "claude",
            Path.home() / ".claude" / "local" / "claude",
            Path("/usr/local/bin/claude"),
            Path.home() / "AppData" / "Local" / "Programs" / "claude" / "claude.exe",
        )
        for fb_path in fallback_paths:
            if fb_path.is_file():
                return str(fb_path)
        return None

    def _resolve_subagent_models(self, spec: AgentTaskSpec[Any]) -> dict[str, str]:
        """Resolve concrete model IDs for all subagents attached to `spec`."""
        return {
            sub.name: (sub.model_override or self.resolve_model(sub.model_tier, sub.persona))
            for sub in spec.subagents
        }

    def _build_agents_json(self, spec: AgentTaskSpec[Any]) -> str | None:
        """Build the JSON mapping for `claude --agents <json>` with resolved subagent models."""
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
        """Construct the clean non-interactive prompt sent to `claude -p`."""
        clean_prompt = _strip_internal_stage_context(spec.prompt)
        parts: list[str] = [
            spec.system_instructions.strip(),
            (
                "EXECUTION DIRECTIVE: All required ontology, statistical profile, and sample "
                "data are provided inline below. Do NOT invoke filesystem, shell, or search "
                "tools. Respond immediately in a single turn with only the required JSON object."
            ),
            clean_prompt,
        ]
        if spec.subagents:
            subagent_models = self._resolve_subagent_models(spec)
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
        full_prompt = "\n\n".join(p for p in parts if p)
        if len(full_prompt) > 24000:
            full_prompt = full_prompt[:23500] + (
                "\n\n[Truncated for CLI argument length limit. "
                "Respond immediately in Turn 1 with valid JSON only.]"
            )
        return full_prompt

    def _invoke_claude_subprocess(
        self,
        cmd: list[str],
        env: dict[str, str],
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        """Run the `claude` CLI subprocess synchronously (called via `asyncio.to_thread`)."""
        custom_runner: Callable[..., subprocess.CompletedProcess[str]] | None = (
            self._config.extra_provider_options.get("claude_cli_runner")
        )
        if callable(custom_runner):
            return custom_runner(cmd, env=env, timeout=timeout_seconds)
        self._config.state_dir.mkdir(parents=True, exist_ok=True)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(self._config.state_dir),
            timeout=timeout_seconds,
            check=False,
        )

    async def _stream_provider_task(
        self,
        spec: AgentTaskSpec[OutputT],
        ctx: ToolExecutionContext,
        model_id: str,
    ) -> AsyncIterator[HarnessStreamEvent]:
        """Execute task via the local `claude` CLI (`claude -p`) and stream normalized events."""
        model_id = (
            spec.model_override or model_id or self.resolve_model(spec.model_tier, spec.persona)
        )
        subagent_models = self._resolve_subagent_models(spec)
        if self._sdk_override is not None:
            from opendv_agent_harness.adapters.claude_adapter import (
                ClaudeAgentHarnessAdapter,
            )

            delegate = ClaudeAgentHarnessAdapter(self._config, sdk_module=self._sdk_override)
            async for ev in delegate._stream_provider_task(spec, ctx, model_id):
                yield ev.model_copy(update={"provider": self.provider_name})
            return

        claude_bin = self._resolve_claude_binary()
        if claude_bin is None:
            raise ProviderConfigurationError(
                "Claude Code CLI ('claude') executable was not found on PATH or in "
                "OPENDV_CLAUDE_CLI_BIN. Install and log in to 'claude' or set "
                "OPENDV_CLAUDE_CLI_BIN."
            )

        def make_event(kind: HarnessEventKind, **kwargs: Any) -> HarnessStreamEvent:
            return self._make_event(spec, ctx, kind, **kwargs)

        yield make_event(HarnessEventKind.SESSION_START)

        proc_env = dict(os.environ)
        proc_env.update(self._config.env)
        proc_env["NO_COLOR"] = "1"

        sys_prompt = spec.system_instructions.strip()
        agents_json = self._build_agents_json(spec)
        schema_json_str: str | None = None
        if spec.output_schema is not None:
            schema_json_str = json.dumps(
                spec.output_schema.model_json_schema(),
                separators=(",", ":"),
            )

        prompt_text = self._build_cli_prompt(spec, include_inline_schema=False)
        cmd: list[str] = [
            claude_bin,
            "-p",
            prompt_text,
            "--model",
            model_id,
            "--output-format",
            "json",
            "--disable-slash-commands",
            "--tools",
            "",
        ]
        if sys_prompt:
            cmd.extend(["--system-prompt", sys_prompt])
        if agents_json is not None:
            cmd.extend(["--agents", agents_json])
        if schema_json_str is not None:
            cmd.extend(["--json-schema", schema_json_str])
        if spec.resume_session_id:
            cmd.extend(["--resume", spec.resume_session_id])

        completed = await asyncio.to_thread(
            self._invoke_claude_subprocess,
            cmd,
            proc_env,
            self._config.budget.timeout_seconds,
        )

        # Fallback without --json-schema flag if the CLI rejected a complex $defs schema
        if completed.returncode != 0 and schema_json_str is not None:
            fallback_prompt = self._build_cli_prompt(spec, include_inline_schema=True)
            fallback_cmd: list[str] = [
                claude_bin,
                "-p",
                fallback_prompt,
                "--model",
                model_id,
                "--output-format",
                "json",
                "--disable-slash-commands",
                "--tools",
                "",
            ]
            if sys_prompt:
                fallback_cmd.extend(["--system-prompt", sys_prompt])
            if agents_json is not None:
                fallback_cmd.extend(["--agents", agents_json])
            if spec.resume_session_id:
                fallback_cmd.extend(["--resume", spec.resume_session_id])
            completed = await asyncio.to_thread(
                self._invoke_claude_subprocess,
                fallback_cmd,
                proc_env,
                self._config.budget.timeout_seconds,
            )

        if completed.returncode != 0 and not completed.stdout.strip():
            stderr_msg = completed.stderr.strip() or f"exit code {completed.returncode}"
            raise ProviderConfigurationError(
                f"Claude Code CLI ('claude') failed ({stderr_msg}). "
                "Ensure 'claude' is logged in and accessible."
            )

        payload = _extract_json_from_stdout(completed.stdout)

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
        if not raw_response_text and raw_structured is not None:
            raw_response_text = (
                raw_structured if isinstance(raw_structured, str) else json.dumps(raw_structured)
            )

        if raw_response_text:
            yield make_event(HarnessEventKind.TEXT_DELTA, delta_text=raw_response_text)

        for obs_ev in ctx.pop_unstreamed_events():
            yield make_event(HarnessEventKind.OBSERVABILITY_EVENT, obs_event=obs_ev)
            if obs_ev.get("status") == "ASK-USER":
                yield make_event(HarnessEventKind.STEWARD_HOLD, obs_event=obs_ev)

        validated_structured: Any = None
        if spec.output_schema is not None:
            if raw_structured is not None:
                if isinstance(raw_structured, dict):
                    validated_structured = spec.output_schema.model_validate(raw_structured)
                elif isinstance(raw_structured, str):
                    validated_structured = spec.output_schema.model_validate_json(
                        _strip_markdown_json_fences(raw_structured)
                    )
            elif raw_response_text.strip():
                cleaned_text = _strip_markdown_json_fences(raw_response_text)
                validated_structured = spec.output_schema.model_validate_json(cleaned_text)
        else:
            validated_structured = raw_structured

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

        turn_meta: dict[str, Any] = {
            "session_id": str(
                payload.get("session_id")
                or payload.get("conversation_id")
                or spec.resume_session_id
                or spec.task_id
            ),
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
        yield make_event(HarnessEventKind.TURN_COMPLETE, tool_result=turn_meta)


ClaudeCodeCliAdapter = ClaudeCliHarnessAdapter

__all__ = [
    "ClaudeCliHarnessAdapter",
    "ClaudeCodeCliAdapter",
    "_extract_json_from_stdout",
    "_strip_internal_stage_context",
]
