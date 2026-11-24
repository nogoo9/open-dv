"""Structured CliEnvelope formatting, exit-code mapping, and CLI input parsing."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
import typer
from pydantic import BaseModel, ValidationError

from opendv.models import CliEnvelope, CliExitCode, ErrorCode, OpenDVError


def _collect_click_exc(name: str) -> tuple[type[BaseException], ...]:
    mods: list[Any] = [click.exceptions]
    tc = getattr(typer, "_click", None)
    if tc is not None and hasattr(tc, "exceptions"):
        mods.append(tc.exceptions)
    out: list[type[BaseException]] = []
    for mod in mods:
        cls = getattr(mod, name, None)
        if isinstance(cls, type) and issubclass(cls, BaseException) and cls not in out:
            out.append(cls)
    return tuple(out)


CLICK_EXIT_TYPES: tuple[type[BaseException], ...] = (
    typer.Exit,
    *_collect_click_exc("Exit"),
)
CLICK_HELP_TYPES: tuple[type[BaseException], ...] = _collect_click_exc("NoArgsIsHelpError")
CLICK_ABORT_TYPES: tuple[type[BaseException], ...] = _collect_click_exc("Abort")
CLICK_USAGE_TYPES: tuple[type[BaseException], ...] = _collect_click_exc("UsageError")
CLICK_EXCEPTION_TYPES: tuple[type[BaseException], ...] = _collect_click_exc("ClickException")


@dataclass(slots=True)
class CliGlobalOptions:
    """Global flags captured by the root `opendv` Typer callback."""

    api_url: str | None = None
    json_mode: bool = False
    env_first: bool = False


class CliHandledError(RuntimeError):
    """Signal that a command raised a mapped `OpenDVError` with optional payload data."""

    def __init__(
        self, error: OpenDVError, *, data: Any = None, instance: str | None = None
    ) -> None:
        super().__init__(error.message)
        self.error = error
        self.data = data
        self.instance = instance


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def normalize_json_data(payload: Any) -> dict[str, Any] | list[Any] | None:
    """Normalize any service result into `CliEnvelope.data`-compatible JSON containers."""
    if payload is None:
        return None
    converted = _jsonable(payload)
    if isinstance(converted, dict | list):
        return converted
    return {"value": converted}


def render_success(
    payload: Any,
    *,
    json_mode: bool,
    plain_text: str | None = None,
) -> None:
    """Render a successful command response as `CliEnvelope` JSON or concise text."""
    if json_mode:
        env = CliEnvelope(
            ok=True,
            exit_code=int(CliExitCode.OK),
            error_code=None,
            problem=None,
            data=normalize_json_data(payload),
        )
        typer.echo(env.model_dump_json(indent=2))
        return

    if plain_text is not None:
        typer.echo(plain_text)
        return

    converted = _jsonable(payload)
    if isinstance(converted, dict | list):
        typer.echo(json.dumps(converted, indent=2))
    elif converted is not None:
        typer.echo(str(converted))


def render_error(
    error: OpenDVError,
    *,
    json_mode: bool,
    instance: str | None = None,
    data: Any = None,
) -> int:
    """Render an `OpenDVError` consistently and return its mapped `CliExitCode` integer."""
    problem = error.to_problem_details(instance=instance)
    exit_code = int(error.exit_code)
    if json_mode:
        env = CliEnvelope(
            ok=False,
            exit_code=exit_code,
            error_code=error.error_code,
            problem=problem,
            data=normalize_json_data(data) if data is not None else None,
        )
        typer.echo(env.model_dump_json(indent=2))
        return exit_code

    typer.echo(f"Error [{error.error_code.value}] (exit {exit_code}): {error.message}", err=True)
    for diag in error.diagnostics:
        typer.echo(f"  - [{diag.rule_id}] {diag.message}", err=True)
    return exit_code


def coerce_cli_error(exc: BaseException) -> tuple[OpenDVError, Any, str | None]:
    """Convert known CLI/validation/transport exceptions into an `OpenDVError` tuple."""
    if isinstance(exc, CliHandledError):
        return exc.error, exc.data, exc.instance
    if isinstance(exc, OpenDVError):
        return exc, None, None
    if isinstance(exc, ValidationError):
        return (
            OpenDVError(
                f"Invalid input payload: {exc}",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            ),
            None,
            None,
        )
    if isinstance(exc, (*CLICK_USAGE_TYPES, *CLICK_EXCEPTION_TYPES, ValueError)):
        msg = exc.format_message() if hasattr(exc, "format_message") else str(exc)
        return (
            OpenDVError(
                msg,
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            ),
            None,
            None,
        )
    if isinstance(exc, OSError):
        return (
            OpenDVError(
                str(exc),
                error_code=ErrorCode.AGENT_EXECUTION_FAILED,
            ),
            None,
            None,
        )
    return (
        OpenDVError(
            str(exc),
            error_code=ErrorCode.AGENT_EXECUTION_FAILED,
        ),
        None,
        None,
    )


def read_text_arg(raw_path: str, *, label: str) -> str:
    """Read UTF-8 file text for a CLI path argument, raising `RESOURCE_NOT_FOUND` on miss."""
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise OpenDVError(
            f"{label} file not found: '{path}'.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OpenDVError(
            f"Unable to read {label} file '{path}': {exc}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from exc


def parse_json_arg(
    raw_or_path: str | None,
    *,
    label: str,
    expect_type: type[dict[str, Any]] | type[list[Any]] | None = None,
) -> Any:
    """Parse JSON either directly or from a UTF-8 JSON file path."""
    if raw_or_path is None:
        return None
    stripped = raw_or_path.strip()
    if not stripped:
        raise OpenDVError(
            f"{label} JSON argument cannot be empty.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if stripped.startswith(("{", "[")):
        raw_text = stripped
    else:
        path = Path(stripped).expanduser()
        if path.is_file():
            raw_text = read_text_arg(str(path), label=label)
        elif stripped.endswith(".json") or "/" in stripped or "\\" in stripped:
            raise OpenDVError(
                f"{label} JSON file not found: '{path}'.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        else:
            raw_text = stripped

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise OpenDVError(
            f"Invalid JSON for {label}: {exc.msg}.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from exc

    if expect_type is not None and not isinstance(parsed, expect_type):
        expected_name = "JSON object" if expect_type is dict else "JSON array"
        raise OpenDVError(
            f"Expected {expected_name} for {label}, got {type(parsed).__name__}.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return parsed


def parse_csv_list(raw: str | None) -> list[str] | None:
    """Split a non-empty comma-separated CLI flag into stripped non-empty items."""
    if raw is None:
        return None
    items = [part.strip() for part in raw.split(",") if part.strip()]
    if not items:
        raise OpenDVError(
            "Expected at least one comma-separated item.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return items


def parse_int_csv(raw: str, *, label: str) -> list[int]:
    """Parse a comma-separated list of integers for CLI options such as `--pages`."""
    parts = parse_csv_list(raw) or []
    result: list[int] = []
    for part in parts:
        try:
            result.append(int(part))
        except ValueError as exc:
            raise OpenDVError(
                f"Invalid integer '{part}' in {label}.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            ) from exc
    return result


def parse_csv_or_json_list(raw: str | None, *, label: str) -> list[str]:
    """Parse a CLI option provided either as JSON array `[...]` or CSV string."""
    if raw is None:
        return []
    stripped = raw.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        parsed = parse_json_arg(stripped, label=label, expect_type=list)
        if not all(isinstance(item, str) for item in parsed):
            raise OpenDVError(
                f"Expected string list in {label}.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        return list(parsed)
    return parse_csv_list(stripped) or []


def format_run_summary(state: Any) -> str:
    """Return a concise single-line summary for `RunStateResp` objects."""
    return (
        f"run={state.run_id} step={state.step} status={state.status} "
        f"attempt={state.attempt} turns={state.budget_turns_used}/{state.effective_max_turns}"
    )


def read_stdin_secret() -> str:
    """Read a secret from stdin without echoing it to stdout."""
    value = sys.stdin.read().rstrip("\r\n")
    if not value:
        raise OpenDVError(
            "Empty secret received on stdin.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return value


def extract_json_error_payload(raw_text: str) -> dict[str, Any] | None:
    """Best-effort decode of a structured ProblemDetails response body."""
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def coerce_str_dict(raw: dict[str, Any] | None, *, label: str) -> dict[str, str]:
    """Coerce a parsed JSON object whose values represent string clarification answers."""
    if raw is None:
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str | int | float | bool):
            raise OpenDVError(
                f"Answer for '{key}' in {label} must be a scalar value.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        out[str(key)] = str(value)
    return out


def coerce_option_dict(
    raw: dict[str, Any] | None, *, label: str
) -> dict[str, str | int | bool]:
    """Validate connector options map values match `str | int | bool`."""
    if raw is None:
        return {}
    out: dict[str, str | int | bool] = {}
    for key, value in raw.items():
        if not isinstance(value, str | int | bool):
            raise OpenDVError(
                f"Connector option '{key}' in {label} must be str, int, or bool.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        out[str(key)] = value
    return out


def coerce_str_list(items: Sequence[str] | None) -> list[str]:
    """Return a clean list copy from optional string sequence."""
    return list(items) if items is not None else []
