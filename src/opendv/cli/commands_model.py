"""Stateless DV2.0 CLI subcommands: `opendv model {spec, profile, verify, run}`."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

import typer
from pydantic import BaseModel, ValidationError

from opendv.cli._client import resolve_cli_workspace_root
from opendv.cli._output import (
    CLICK_EXCEPTION_TYPES,
    CliGlobalOptions,
    CliHandledError,
    coerce_cli_error,
    normalize_json_data,
    render_error,
    render_success,
)
from opendv.cli.commands_core import ProjectOpt, run_cli_action
from opendv.models import (
    ErrorCode,
    ModelingPreferences,
    OpenDVError,
    ProjectSettingsUpdateReq,
)

if TYPE_CHECKING:
    from opendv.cli._client import CliClient

_VALID_SPEC_PARTS = frozenset({"all", "schema", "guide", "preferences"})
_STATELESS_HANDLED_EXC_TYPES: tuple[type[BaseException], ...] = (
    CliHandledError,
    OpenDVError,
    ValidationError,
    ValueError,
    OSError,
    RuntimeError,
    TypeError,
    *CLICK_EXCEPTION_TYPES,
)

model_app = typer.Typer(
    name="model",
    help="Stateless DV2.0 specification, source profiling, verification, and CEGAR runs.",
    no_args_is_help=True,
)


def read_workspace_safe_text(
    raw_path: Path,
    *,
    workspace_root: Path,
    label: str,
    max_bytes: int = 262_144,
) -> tuple[Path, str]:
    """Validate containment + symlinks and read UTF-8 text via `O_NOFOLLOW`."""
    from opendv.service.bundles.zip_guard import (
        reject_symlinks_on_path_and_ancestors,
        safe_open_no_follow,
    )
    from opendv.service.os_platform import validate_workspace_containment

    resolved = Path(raw_path)
    env_roots = os.environ.get("OPENDV_WORKSPACE_ROOTS", "").strip()
    roots = (
        [Path(p.strip()) for p in env_roots.split(os.pathsep) if p.strip()]
        if env_roots
        else [Path.cwd(), workspace_root]
    )
    reject_symlinks_on_path_and_ancestors(resolved, stop_roots=roots)
    resolved = validate_workspace_containment(
        resolved, allowed_roots=None if env_roots else roots
    )
    if not resolved.is_file():
        raise OpenDVError(
            f"{label} file not found: '{resolved}'.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    fd = safe_open_no_follow(resolved, os.O_RDONLY)
    with os.fdopen(fd, "rb") as fh:
        raw_bytes = fh.read(max_bytes + 1)
    if len(raw_bytes) > max_bytes:
        raise OpenDVError(
            f"{label} exceeds maximum byte size ({max_bytes} bytes).",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    try:
        return resolved, raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OpenDVError(
            f"{label} must be valid UTF-8 text: {exc}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from exc


def parse_modeling_pref_updates(
    items: Sequence[str],
    *,
    base: ModelingPreferences,
) -> ModelingPreferences:
    """Parse repeatable `key=value` `--pref` flags onto a `ModelingPreferences` model."""
    updates: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise OpenDVError(
                f"Invalid --pref '{item}'; expected key=value.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        raw_key, raw_val = item.split("=", 1)
        key = raw_key.strip().replace("-", "_")
        if not key or key not in ModelingPreferences.model_fields:
            raise OpenDVError(
                f"Unknown modeling preference '{raw_key.strip()}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        val_str = raw_val.strip()
        if key == "disabled_v6b_blocks":
            if not val_str:
                updates[key] = []
            elif val_str.startswith("["):
                try:
                    updates[key] = json.loads(val_str)
                except json.JSONDecodeError as exc:
                    raise OpenDVError(
                        f"Invalid JSON for --pref '{key}': {exc.msg}.",
                        error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                    ) from exc
            else:
                updates[key] = [part.strip() for part in val_str.split(",") if part.strip()]
            continue
        if val_str.startswith(("[", "{", '"')):
            try:
                updates[key] = json.loads(val_str)
            except json.JSONDecodeError as exc:
                raise OpenDVError(
                    f"Invalid JSON value for --pref '{key}': {exc.msg}.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                ) from exc
        elif val_str.lower() in ("true", "false"):
            updates[key] = val_str.lower() == "true"
        elif val_str.lower() in ("null", "none"):
            updates[key] = None
        elif key == "max_survey_questions":
            try:
                updates[key] = int(val_str)
            except ValueError as exc:
                raise OpenDVError(
                    f"Invalid integer for --pref 'max_survey_questions': '{val_str}'.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                ) from exc
        else:
            updates[key] = val_str
    return ModelingPreferences.model_validate({**base.model_dump(mode="python"), **updates})


def apply_project_settings_cli(
    client: CliClient,
    project: str,
    *,
    pref: Sequence[str] | None,
    hints_file: Path | None,
    hints_text: str | None,
    proving_sample_rows: int | None,
    real_row_proving: bool | None,
) -> Any:
    """Validate flags, safe-read hints file, and apply partial project settings update."""
    if hints_file is not None and hints_text is not None:
        raise OpenDVError(
            "Specify at most one of --hints-file or --hints-text.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if (
        not pref
        and hints_file is None
        and hints_text is None
        and proving_sample_rows is None
        and real_row_proving is None
    ):
        raise OpenDVError(
            "At least one project setting flag must be provided.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    updates: dict[str, Any] = {}
    if pref:
        base_prefs = client.get_project(project).settings_snapshot.modeling_preferences
        updates["modeling_preferences"] = parse_modeling_pref_updates(pref, base=base_prefs)
    if hints_file is not None:
        _, loaded_hints = read_workspace_safe_text(
            hints_file, workspace_root=client.workspace_root, label="--hints-file"
        )
        updates["business_context_hints"] = loaded_hints
    elif hints_text is not None:
        updates["business_context_hints"] = None if hints_text == "" else hints_text
    if proving_sample_rows is not None:
        updates["proving_sample_rows"] = proving_sample_rows
    if real_row_proving is not None:
        updates["real_row_proving"] = real_row_proving

    return client.update_project_settings(project, ProjectSettingsUpdateReq(**updates))


def _load_json_dict(path: Path, *, label: str) -> dict[str, Any]:
    resolved = Path(path)
    if not resolved.is_file():
        raise OpenDVError(
            f"{label} file not found: '{resolved}'.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    try:
        raw_text = resolved.read_text(encoding="utf-8")
        parsed = json.loads(raw_text)
    except OSError as exc:
        raise OpenDVError(
            f"Unable to read {label} file '{resolved}': {exc}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from exc
    except json.JSONDecodeError as exc:
        raise OpenDVError(
            f"Invalid JSON in {label} file '{resolved}': {exc.msg}.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from exc
    if not isinstance(parsed, dict):
        raise OpenDVError(
            f"Expected JSON object in {label} file '{resolved}', got {type(parsed).__name__}.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return parsed


def _write_out_json(out_path: Path | None, payload: Any) -> None:
    if out_path is None:
        return
    from opendv.service.os_platform import atomic_write_text

    target = out_path.resolve()
    if isinstance(payload, BaseModel):
        serialized = payload.model_dump_json(indent=2) + "\n"
    else:
        serialized = json.dumps(normalize_json_data(payload), indent=2) + "\n"
    atomic_write_text(target, serialized)


def _extract_status(result: Any) -> str | None:
    if isinstance(result, dict):
        raw = result.get("status")
        return str(raw) if raw is not None else None
    raw_attr = getattr(result, "status", None)
    return str(raw_attr) if raw_attr is not None else None


def _raise_if_non_certified(result: Any, *, json_mode: bool, label: str) -> None:
    status = _extract_status(result)
    if status in ("CERTIFIED", "CERTIFIED_FAST", None):
        return
    if status == "HOLD_CHECKPOINTED":
        code = ErrorCode.HOLD_CHECKPOINTED
        msg = f"{label} paused at HOLD_CHECKPOINTED."
    elif status == "UNCERTIFIED_DRAFT":
        code = ErrorCode.UNCERTIFIED_DRAFT
        msg = f"{label} ended in UNCERTIFIED_DRAFT."
    else:
        code = ErrorCode.AGENT_EXECUTION_FAILED
        msg = f"{label} failed with status '{status}'."
    if not json_mode:
        typer.echo(json.dumps(normalize_json_data(result), indent=2))
    raise CliHandledError(OpenDVError(msg, error_code=code), data=result)


def _run_stateless_action(
    ctx: typer.Context,
    action: Callable[[CliGlobalOptions], Any],
    *,
    plain_formatter: Callable[[Any], str] | None = None,
) -> None:
    from opendv.service.secrets import scoped_env_first

    obj = ctx.obj
    opts = obj if isinstance(obj, CliGlobalOptions) else CliGlobalOptions()
    try:
        with scoped_env_first(opts.env_first):
            result = action(opts)
    except _STATELESS_HANDLED_EXC_TYPES as exc:
        err, data, instance = coerce_cli_error(exc)
        exit_code = render_error(err, json_mode=opts.json_mode, instance=instance, data=data)
        raise typer.Exit(code=exit_code) from exc

    plain_text = plain_formatter(result) if plain_formatter is not None else None
    render_success(result, json_mode=opts.json_mode, plain_text=plain_text)


def _format_profile_bundle(bundle: Any) -> str:
    profiles = getattr(bundle, "profiles", ())
    doc_maps = getattr(bundle, "document_maps", ())
    tables = sum(len(getattr(p, "tables", ())) for p in profiles)
    questions = getattr(bundle, "questions", ())
    return (
        f"profiles={len(profiles)} tables={tables} "
        f"document_maps={len(doc_maps)} questions={len(questions)}"
    )


@model_app.command("spec")
def model_spec(
    ctx: typer.Context,
    part: Annotated[
        str,
        typer.Option("--part", help="all, schema, guide, or preferences."),
    ] = "all",
) -> None:
    """Emit annotated DV2.0 schemas, constraints, modeling guide, and preferences."""

    def _run(_opts: CliGlobalOptions) -> Any:
        if part not in _VALID_SPEC_PARTS:
            raise OpenDVError(
                f"Invalid --part '{part}'; expected all, schema, guide, or preferences.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        from opendv.service.workflows.modeling_stateless import get_model_spec

        typed_part: Literal["all", "schema", "guide", "preferences"] = part  # type: ignore[assignment]
        return get_model_spec(part=typed_part)

    _run_stateless_action(ctx, _run)


@model_app.command("profile")
def model_profile(
    ctx: typer.Context,
    source: Annotated[
        list[str] | None,
        typer.Option("--source", help="Repeatable <kind>:<uri> source specification."),
    ] = None,
    sources_file: Annotated[
        Path | None,
        typer.Option("--sources-file", help="Path to JSON or YAML source manifest."),
    ] = None,
    sample_rows: Annotated[
        int,
        typer.Option("--sample-rows", help="Max proving rows sampled per table (1..5000)."),
    ] = 200,
    no_real_row_proving: Annotated[
        bool,
        typer.Option("--no-real-row-proving", help="Disable real-row proving capture."),
    ] = False,
    max_questions: Annotated[
        int,
        typer.Option(
            "--max-questions",
            help="Max clarification survey questions (0 returns all remaining).",
        ),
    ] = 5,
    questions_offset: Annotated[
        int,
        typer.Option("--questions-offset", help="Zero-based pagination offset for questions."),
    ] = 0,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write ProfileBundle JSON to file."),
    ] = None,
) -> None:
    """Statelessly profile sources, compute PK containment, and survey modeling hints."""

    def _run(_opts: CliGlobalOptions) -> Any:
        if not source and sources_file is None:
            raise OpenDVError(
                "Specify at least one --source or --sources-file.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if sample_rows < 1 or sample_rows > 5000:
            raise OpenDVError(
                "--sample-rows must be between 1 and 5000.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if max_questions < 0:
            raise OpenDVError(
                "--max-questions must be >= 0.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if questions_offset < 0:
            raise OpenDVError(
                "--questions-offset must be >= 0.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        resolved_manifest: Path | None = None
        if sources_file is not None:
            resolved_manifest = Path(sources_file)
            if not resolved_manifest.is_file():
                raise OpenDVError(
                    f"--sources-file not found: '{resolved_manifest}'.",
                    error_code=ErrorCode.RESOURCE_NOT_FOUND,
                )

        from opendv.service.workflows.modeling_stateless import profile_sources_stateless

        bundle = profile_sources_stateless(
            sources=list(source or []),
            sources_file=resolved_manifest,
            proving_sample_rows=sample_rows,
            real_row_proving=not no_real_row_proving,
            max_questions=None if max_questions == 0 else max_questions,
            offset=questions_offset,
        )
        _write_out_json(out, bundle)
        return bundle

    _run_stateless_action(ctx, _run, plain_formatter=_format_profile_bundle)


@model_app.command("verify")
def model_verify(
    ctx: typer.Context,
    input_path: Annotated[
        Path,
        typer.Option("--input", help="Path to modeling request JSON."),
    ],
    draft_ir_path: Annotated[
        Path,
        typer.Option("--draft-ir", help="Path to DraftVaultIR JSON."),
    ],
    profile_path: Annotated[
        Path | None,
        typer.Option("--profile", help="Optional ProfileBundle JSON from `opendv model profile`."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Validate schemas and PII tokenization without proving."),
    ] = False,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write verification result JSON to file."),
    ] = None,
) -> None:
    """Mode-1 zero-LLM deterministic verification (L1 + V1-V6 + tiered V6b)."""

    def _run(opts: CliGlobalOptions) -> Any:
        req_payload = _load_json_dict(input_path, label="--input")
        draft_payload = _load_json_dict(draft_ir_path, label="--draft-ir")
        profile_payload = (
            _load_json_dict(profile_path, label="--profile")
            if profile_path is not None
            else None
        )

        from opendv.service.workflows.modeling_stateless import verify_draft_stateless

        result = verify_draft_stateless(
            req_payload,
            draft_payload,
            profile_bundle=profile_payload,
            dry_run=dry_run,
        )
        _write_out_json(out, result)
        if not dry_run:
            _raise_if_non_certified(
                result,
                json_mode=opts.json_mode,
                label="DraftVaultIR verification",
            )
        return result

    _run_stateless_action(ctx, _run)


@model_app.command("run")
def model_run(
    ctx: typer.Context,
    input_path: Annotated[
        Path,
        typer.Option("--input", help="Path to DVModelingRequest JSON."),
    ],
    profile_path: Annotated[
        Path | None,
        typer.Option("--profile", help="Optional ProfileBundle JSON from `opendv model profile`."),
    ] = None,
    guide: Annotated[
        Path | None,
        typer.Option("--guide", help="Optional workspace dv2-modeling-guide.md path."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Validate request, guide trust, and PII only."),
    ] = False,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write modeling result JSON to file."),
    ] = None,
) -> None:
    """Mode-2 stateless managed CEGAR modeling run (with optional --guide and --profile)."""

    def _run(opts: CliGlobalOptions) -> Any:
        req_payload = _load_json_dict(input_path, label="--input")
        profile_payload = (
            _load_json_dict(profile_path, label="--profile")
            if profile_path is not None
            else None
        )
        ws_root = resolve_cli_workspace_root()
        resolved_guide: Path | None = None
        if guide is not None:
            resolved_guide, _ = read_workspace_safe_text(
                guide, workspace_root=ws_root, label="--guide"
            )

        from opendv.service.workflows.modeling_stateless import run_model_stateless

        result = asyncio.run(
            run_model_stateless(
                req_payload,
                profile_bundle=profile_payload,
                guide_path=resolved_guide,
                dry_run=dry_run,
                workspace_root=ws_root,
            )
        )
        _write_out_json(out, result)
        if not dry_run:
            _raise_if_non_certified(
                result,
                json_mode=opts.json_mode,
                label="Stateless modeling run",
            )
        return result

    _run_stateless_action(ctx, _run)


def register_settings_project_set(settings_app: typer.Typer) -> None:
    """Register `opendv settings project-set` onto settings_app."""

    @settings_app.command("project-set")
    def settings_project_set(
        ctx: typer.Context,
        project: ProjectOpt,
        pref: Annotated[
            list[str] | None,
            typer.Option("--pref", help="Repeatable ModelingPreferences key=value update."),
        ] = None,
        hints_file: Annotated[
            Path | None,
            typer.Option("--hints-file", help="Path to business context hints file."),
        ] = None,
        hints_text: Annotated[
            str | None,
            typer.Option("--hints-text", help="Inline business context hints text."),
        ] = None,
        proving_sample_rows: Annotated[
            int | None,
            typer.Option("--proving-sample-rows", help="Proving sample row limit (1..5000)."),
        ] = None,
        real_row_proving: Annotated[
            bool | None,
            typer.Option(
                "--real-row-proving/--no-real-row-proving",
                help="Toggle Step 2/3 real-row proving capture.",
            ),
        ] = None,
    ) -> None:
        """Partially update per-project modeling preferences, hints, and proving settings."""
        run_cli_action(
            ctx,
            lambda c: apply_project_settings_cli(
                c,
                project,
                pref=pref,
                hints_file=hints_file,
                hints_text=hints_text,
                proving_sample_rows=proving_sample_rows,
                real_row_proving=real_row_proving,
            ),
        )


__all__ = [
    "apply_project_settings_cli",
    "model_app",
    "parse_modeling_pref_updates",
    "read_workspace_safe_text",
    "register_settings_project_set",
]
