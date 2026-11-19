"""Antigravity Coding CLI (`agy`) Adapter for the Open-DV Agent Harness.

Executes non-interactive agentic turns via the local logged-in Antigravity CLI (`agy -p`)
piping prompts over stdin with `--output-format stream-json`, `--json-schema <schema_path>`,
`--model <model_id>`, `--disable-slash-commands`, and `--sandbox`.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from opendv_agent_harness.adapters.antigravity_adapter import ProviderConfigurationError
from opendv_agent_harness.adapters.cli_subprocess_base import (
    build_cli_prompt,
    extract_json_from_stdout,
    make_cli_turn_meta,
    stream_cli_subprocess_lines,
    validate_cli_structured_output,
)
from opendv_agent_harness.adapters.cli_subprocess_base import (
    is_non_result_stream_event as _is_non_result_stream_event,
)
from opendv_agent_harness.adapters.cli_subprocess_base import (
    parse_ndjson_stream_line as _parse_ndjson_stream_line,
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


def _extract_json_from_stdout(raw_stdout: str) -> dict[str, Any]:
    """Extract the JSON result object emitted by ``agy -p --output-format stream-json``."""
    return extract_json_from_stdout(
        raw_stdout,
        cli_label="Antigravity CLI ('agy')",
        primary_text_key="response",
    )


class AgyCliHarnessAdapter(BaseVaultAgentHarness):
    """Production Adapter: Antigravity Coding CLI (`agy`) via active logged-in CLI session."""

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
        """Locate the `agy` CLI executable from config, environment, or PATH (CFG-06)."""
        explicit_candidates = (
            self._config.env.get("OPENDV_AGY_CLI_BIN"),
            self._config.extra_provider_options.get("agy_binary"),
            os.environ.get("OPENDV_AGY_CLI_BIN"),
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
        for bin_name in ("agy", "agy.exe", "agy.cmd"):
            found = shutil.which(bin_name)
            if found:
                return found
        return None

    def _resolve_subagent_models(self, spec: AgentTaskSpec[Any]) -> dict[str, str]:
        return {
            sub.name: (sub.model_override or self.resolve_model(sub.model_tier, sub.persona))
            for sub in spec.subagents
        }

    def _build_cli_prompt(self, spec: AgentTaskSpec[Any], *, include_inline_schema: bool) -> str:
        """Construct the full non-interactive stdin prompt sent to ``agy -p``."""
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
        custom_runner = self._config.extra_provider_options.get("agy_cli_runner")
        seen_fine_grained = False
        last_result_payload: dict[str, Any] | None = None
        async for raw_line in stream_cli_subprocess_lines(
            cmd,
            input_text=input_text,
            env=proc_env,
            cwd=self._config.state_dir,
            timeout_seconds=self._config.budget.timeout_seconds,
            state_out=run_state,
            cli_label="Antigravity CLI ('agy')",
            custom_runner=custom_runner,
        ):
            thought_deltas, text_deltas, result_obj, is_fine = _parse_ndjson_stream_line(
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
        """Execute task via local `agy` CLI piping prompt over stdin."""
        model_id = (
            spec.model_override or model_id or self.resolve_model(spec.model_tier, spec.persona)
        )
        subagent_models = self._resolve_subagent_models(spec)
        if self._sdk_override is not None:
            from opendv_agent_harness.adapters.antigravity_adapter import AntigravityHarnessAdapter

            delegate = AntigravityHarnessAdapter(self._config, sdk_module=self._sdk_override)
            async for ev in delegate._stream_provider_task(spec, ctx, model_id):
                yield ev.model_copy(update={"provider": self.provider_name})
            return

        agy_bin = self._resolve_agy_binary()
        if agy_bin is None:
            raise ProviderConfigurationError(
                "Antigravity CLI ('agy') executable was not found. Checked sources: "
                "1) extra_provider_options.agy_binary, 2) env var OPENDV_AGY_CLI_BIN, 3) PATH."
            )

        def make_event(kind: HarnessEventKind, **kwargs: Any) -> HarnessStreamEvent:
            return self._make_event(spec, ctx, kind, **kwargs)

        yield make_event(HarnessEventKind.SESSION_START)
        proc_env = dict(os.environ)
        proc_env.update(self._config.env)
        proc_env["NO_COLOR"] = "1"

        streamed_text_chunks: list[str] = []
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
                "--input-format",
                "text",
                "--model",
                model_id,
                "--output-format",
                "stream-json",
                "--disable-slash-commands",
                "--sandbox",
            ]
            if schema_file_path is not None:
                cmd.extend(["--json-schema", str(schema_file_path)])
            if spec.resume_session_id:
                cmd.extend(["--conversation", spec.resume_session_id])

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
                and schema_file_path is not None
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
                fallback_cmd = [
                    agy_bin,
                    "--input-format",
                    "text",
                    "--model",
                    model_id,
                    "--output-format",
                    "stream-json",
                    "--disable-slash-commands",
                    "--sandbox",
                ]
                if spec.resume_session_id:
                    fallback_cmd.extend(["--conversation", spec.resume_session_id])
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
                        or last_result_payload.get("response")
                        or ""
                    ).strip()
                if not stderr_msg:
                    stderr_msg = f"exit code {run_state['returncode']}"
                raise ProviderConfigurationError(
                    f"Antigravity CLI ('agy') failed ({stderr_msg}). "
                    "Ensure 'agy' is logged in and accessible."
                )

            if last_result_payload is not None:
                payload = last_result_payload
                if not payload.get("response") and streamed_text_chunks:
                    payload["response"] = "".join(streamed_text_chunks)
            else:
                payload = _extract_json_from_stdout(str(run_state["stdout"]))
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
        if not raw_response_text and streamed_text_chunks:
            raw_response_text = "".join(streamed_text_chunks)
        if not raw_response_text and raw_structured is not None:
            raw_response_text = json.dumps(raw_structured)
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
        )
        turn_meta = make_cli_turn_meta(
            spec,
            payload,
            session_id=str(
                payload.get("conversation_id") or spec.resume_session_id or spec.task_id
            ),
            validated_structured=validated,
            subagent_models=subagent_models,
        )
        yield make_event(HarnessEventKind.TURN_COMPLETE, tool_result=turn_meta)


AntigravityCliAdapter = AgyCliHarnessAdapter

__all__ = [
    "AgyCliHarnessAdapter",
    "AntigravityCliAdapter",
    "_extract_json_from_stdout",
    "_is_non_result_stream_event",
    "_parse_ndjson_stream_line",
]
