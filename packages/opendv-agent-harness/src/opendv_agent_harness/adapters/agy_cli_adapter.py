"""Antigravity Coding CLI (`agy`) Adapter for the Open-DV Agent Harness.

Executes non-interactive agentic turns via the local logged-in Antigravity CLI (`agy -p`)
with `--output-format json`, `--json-schema <schema_path>`, `--model <model_id>`,
`--disable-slash-commands`, and `--sandbox`.

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
import tempfile
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from opendv_agent_harness.adapters.antigravity_adapter import (
    ProviderConfigurationError,
    _allow_mock_sdk_shim,
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
    """Remove internal base64/JSON stage context footers before dispatching to `agy` CLI."""
    cleaned = prompt
    for marker in _STAGE_CONTEXT_MARKERS:
        if marker in cleaned:
            cleaned, _ = cleaned.split(marker, 1)
    return cleaned.strip()


def _extract_json_from_stdout(raw_stdout: str) -> dict[str, Any]:
    """Extract the JSON result object emitted by `agy -p --output-format json`."""
    stripped = raw_stdout.strip()
    if not stripped:
        raise ProviderConfigurationError("Antigravity CLI ('agy') returned empty stdout.")

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
        f"Antigravity CLI ('agy') output was not valid JSON: {stripped[:400]}"
    )


class AgyCliHarnessAdapter(BaseVaultAgentHarness):
    """Production Adapter: Antigravity Coding CLI (`agy`) via active logged-in CLI session.

    Registered under `provider: 'agy'` and `provider: 'agy-cli'`.
    Invokes the local `agy` binary in non-interactive `--print` (`-p`) mode with
    `--model <model_id>`, `--output-format json`, `--json-schema <schema_file>`,
    `--disable-slash-commands`, and `--sandbox`.
    """

    def __init__(self, config: HarnessConfig, *, sdk_module: Any | None = None) -> None:
        super().__init__(config)
        self._sdk_override = sdk_module

    @property
    def provider_name(self) -> str:
        """Return the canonical provider identifier 'agy'."""
        return "agy"

    def resolve_model(
        self,
        tier: ModelTier,
        persona: AgentPersona | None = None,
    ) -> str:
        """Resolve logical ModelTier ('reasoning'/'fast') and AgentPersona to an `agy` model ID."""
        return resolve_provider_model(
            self.provider_name,
            tier,
            overrides=self._config.model_overrides,
            persona=persona,
            agent_models=self._config.agent_models,
        )

    def _resolve_agy_binary(self) -> str | None:
        """Locate the `agy` CLI executable from config, environment, PATH, or user install dirs."""
        for candidate in (
            self._config.env.get("OPENDV_AGY_CLI_BIN"),
            self._config.extra_provider_options.get("agy_binary"),
            os.environ.get("OPENDV_AGY_CLI_BIN"),
        ):
            if isinstance(candidate, str) and candidate.strip():
                cand_str = candidate.strip()
                if Path(cand_str).is_file() or shutil.which(cand_str):
                    return cand_str

        for bin_name in ("agy", "agy.exe", "agy.cmd"):
            found = shutil.which(bin_name)
            if found:
                return found

        fallback_paths = (
            Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.exe",
            Path.home() / ".local" / "bin" / "agy",
            Path("/usr/local/bin/agy"),
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

    def _build_cli_prompt(self, spec: AgentTaskSpec[Any], *, include_inline_schema: bool) -> str:
        """Construct the clean non-interactive prompt sent to `agy -p`."""
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

    def _invoke_agy_subprocess(
        self,
        cmd: list[str],
        env: dict[str, str],
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        """Run the `agy` CLI subprocess synchronously (called via `asyncio.to_thread`)."""
        custom_runner: Callable[..., subprocess.CompletedProcess[str]] | None = (
            self._config.extra_provider_options.get("agy_cli_runner")
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
        """Execute task via the local `agy` CLI (`agy -p`) and stream normalized events."""
        model_id = (
            spec.model_override or model_id or self.resolve_model(spec.model_tier, spec.persona)
        )
        subagent_models = self._resolve_subagent_models(spec)
        if self._sdk_override is not None:
            from opendv_agent_harness.adapters.antigravity_adapter import (
                AntigravityHarnessAdapter,
            )

            delegate = AntigravityHarnessAdapter(self._config, sdk_module=self._sdk_override)
            async for ev in delegate._stream_provider_task(spec, ctx, model_id):
                yield ev.model_copy(update={"provider": self.provider_name})
            return

        has_explicit_cli_override = (
            callable(self._config.extra_provider_options.get("agy_cli_runner"))
            or bool(self._config.env.get("OPENDV_AGY_CLI_BIN"))
            or bool(self._config.extra_provider_options.get("agy_binary"))
            or bool(os.environ.get("OPENDV_AGY_CLI_BIN"))
        )
        if not has_explicit_cli_override and _allow_mock_sdk_shim(self._config):
            from opendv_agent_harness.adapters.antigravity_adapter import (
                AntigravityHarnessAdapter,
            )
            from opendv_agent_harness.adapters.deterministic_adapter import (
                build_deterministic_agy_sdk_shim,
            )

            shim = build_deterministic_agy_sdk_shim()
            delegate = AntigravityHarnessAdapter(self._config, sdk_module=shim)
            async for ev in delegate._stream_provider_task(spec, ctx, model_id):
                yield ev.model_copy(update={"provider": self.provider_name})
            return

        agy_bin = self._resolve_agy_binary()
        if agy_bin is None:
            if _allow_mock_sdk_shim(self._config):
                from opendv_agent_harness.adapters.antigravity_adapter import (
                    AntigravityHarnessAdapter,
                )
                from opendv_agent_harness.adapters.deterministic_adapter import (
                    build_deterministic_agy_sdk_shim,
                )

                shim = build_deterministic_agy_sdk_shim()
                delegate = AntigravityHarnessAdapter(self._config, sdk_module=shim)
                async for ev in delegate._stream_provider_task(spec, ctx, model_id):
                    yield ev.model_copy(update={"provider": self.provider_name})
                return
            raise ProviderConfigurationError(
                "Antigravity CLI ('agy') executable was not found on PATH or in "
                "OPENDV_AGY_CLI_BIN. Install and log in to 'agy' or set OPENDV_AGY_CLI_BIN."
            )

        def make_event(kind: HarnessEventKind, **kwargs: Any) -> HarnessStreamEvent:
            return self._make_event(spec, ctx, kind, **kwargs)

        yield make_event(HarnessEventKind.SESSION_START)

        proc_env = dict(os.environ)
        proc_env.update(self._config.env)
        proc_env["NO_COLOR"] = "1"

        schema_file_path: Path | None = None
        try:
            if spec.output_schema is not None:
                self._config.state_dir.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    suffix=".schema.json",
                    prefix=f"agy_{spec.task_id}_",
                    dir=str(self._config.state_dir),
                    delete=False,
                ) as tmp_f:
                    json.dump(spec.output_schema.model_json_schema(), tmp_f)
                    schema_file_path = Path(tmp_f.name)

            prompt_text = self._build_cli_prompt(spec, include_inline_schema=False)
            cmd: list[str] = [
                agy_bin,
                "-p",
                prompt_text,
                "--model",
                model_id,
                "--output-format",
                "json",
                "--disable-slash-commands",
                "--sandbox",
            ]
            if schema_file_path is not None:
                cmd.extend(["--json-schema", str(schema_file_path)])
            if spec.resume_session_id:
                cmd.extend(["--conversation", spec.resume_session_id])

            completed = await asyncio.to_thread(
                self._invoke_agy_subprocess,
                cmd,
                proc_env,
                self._config.budget.timeout_seconds,
            )

            # Fallback without --json-schema flag if the CLI rejected a complex $defs schema
            if completed.returncode != 0 and schema_file_path is not None:
                fallback_prompt = self._build_cli_prompt(spec, include_inline_schema=True)
                fallback_cmd: list[str] = [
                    agy_bin,
                    "-p",
                    fallback_prompt,
                    "--model",
                    model_id,
                    "--output-format",
                    "json",
                    "--disable-slash-commands",
                    "--sandbox",
                ]
                if spec.resume_session_id:
                    fallback_cmd.extend(["--conversation", spec.resume_session_id])
                completed = await asyncio.to_thread(
                    self._invoke_agy_subprocess,
                    fallback_cmd,
                    proc_env,
                    self._config.budget.timeout_seconds,
                )

            if completed.returncode != 0 and not completed.stdout.strip():
                stderr_msg = completed.stderr.strip() or f"exit code {completed.returncode}"
                raise ProviderConfigurationError(
                    f"Antigravity CLI ('agy') failed ({stderr_msg}). "
                    "Ensure 'agy' is logged in and accessible."
                )

            payload = _extract_json_from_stdout(completed.stdout)
        finally:
            if schema_file_path is not None:
                schema_file_path.unlink(missing_ok=True)

        status_val = str(payload.get("status", "SUCCESS")).upper()
        if status_val not in {"SUCCESS", "COMPLETED", "OK"}:
            err_detail = str(payload.get("error") or payload.get("response") or status_val)
            raise ProviderConfigurationError(
                f"Antigravity CLI ('agy') returned non-success status '{status_val}': {err_detail}"
            )

        raw_response_text = str(payload.get("response") or "")
        raw_structured = payload.get("structured_output")
        if not raw_response_text and raw_structured is not None:
            raw_response_text = json.dumps(raw_structured)

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
            usage_dict.get("cache_read_tokens") or usage_dict.get("cached_input_tokens") or 0
        )
        out_tok = int(usage_dict.get("output_tokens") or 0)
        think_tok = int(
            usage_dict.get("thinking_tokens") or usage_dict.get("reasoning_tokens") or 0
        )
        tot_tok = int(usage_dict.get("total_tokens") or (in_tok + out_tok + think_tok))
        num_turns = max(1, int(payload.get("num_turns") or 1))

        turn_meta: dict[str, Any] = {
            "session_id": str(
                payload.get("conversation_id") or spec.resume_session_id or spec.task_id
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
                "cost_usd": float(usage_dict.get("cost_usd") or 0.0),
            },
        }
        yield make_event(HarnessEventKind.TURN_COMPLETE, tool_result=turn_meta)


AntigravityCliAdapter = AgyCliHarnessAdapter

__all__ = [
    "AgyCliHarnessAdapter",
    "AntigravityCliAdapter",
    "_extract_json_from_stdout",
    "_strip_internal_stage_context",
]
