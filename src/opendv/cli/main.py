"""Root Typer application wiring all 13 Open-DV CLI entries (11 groups + 2 commands)."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from typing import Annotated

import httpx
import typer
from pydantic import ValidationError

from opendv.cli._commands_parse import parse_app
from opendv.cli._commands_settings import project_settings_app, settings_app
from opendv.cli._commands_sources import source_app
from opendv.cli._output import (
    CLICK_ABORT_TYPES,
    CLICK_EXIT_TYPES,
    CLICK_HELP_TYPES,
    CLICK_USAGE_TYPES,
    CliGlobalOptions,
    CliHandledError,
    coerce_cli_error,
    render_error,
)
from opendv.cli.commands_core import (
    agents_app,
    doctor_command,
    mcp_app,
    project_app,
    secrets_app,
    serve_command,
)
from opendv.cli.commands_model import model_app, register_settings_project_set
from opendv.cli.commands_workflows import (
    ontology_app,
    storage_app,
    vault_app,
)
from opendv.models import CliExitCode, ErrorCode, OpenDVError

project_app.add_typer(project_settings_app, name="settings")
register_settings_project_set(settings_app)


def build_cli_app() -> typer.Typer:
    """Construct the unified `opendv` Typer application with 11 Typer groups and 2 commands."""
    cli = typer.Typer(
        name="opendv",
        help="Open-DV V2 CLI for ontology discovery, Data Vault 2.0 synthesis, and storage.",
        no_args_is_help=True,
        pretty_exceptions_enable=False,
    )

    @cli.callback()
    def _root_options(
        ctx: typer.Context,
        api_url: Annotated[
            str | None,
            typer.Option(
                "--api-url",
                envvar="OPENDV_API_URL",
                help="Route commands to a running `:8420` server instead of in-process mode.",
            ),
        ] = None,
        json_mode: Annotated[
            bool,
            typer.Option("--json", help="Emit structured `CliEnvelope` JSON payloads."),
        ] = False,
        env_first: Annotated[
            bool,
            typer.Option(
                "--env-first",
                help="Prefer process environment variables ahead of keyring/headless secrets.",
            ),
        ] = False,
    ) -> None:
        cleaned_url = (api_url or os.environ.get("OPENDV_API_URL", "")).strip() or None
        ctx.obj = CliGlobalOptions(
            api_url=cleaned_url,
            json_mode=json_mode,
            env_first=env_first,
        )

    cli.command("serve")(serve_command)
    cli.command("doctor")(doctor_command)
    cli.add_typer(settings_app, name="settings")
    cli.add_typer(agents_app, name="agents")
    cli.add_typer(mcp_app, name="mcp")
    cli.add_typer(secrets_app, name="secrets")
    cli.add_typer(project_app, name="project")
    cli.add_typer(source_app, name="source")
    cli.add_typer(ontology_app, name="ontology")
    cli.add_typer(vault_app, name="vault")
    cli.add_typer(storage_app, name="storage")
    cli.add_typer(model_app, name="model")
    cli.add_typer(parse_app, name="parse")
    return cli


app: typer.Typer = build_cli_app()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the `opendv` CLI returning a typed `CliExitCode` integer."""
    args = list(argv) if argv is not None else sys.argv[1:]
    json_flag = "--json" in args
    try:
        rv = app(args=args, prog_name="opendv", standalone_mode=False)
        if isinstance(rv, int):
            return rv
    except CLICK_HELP_TYPES:
        return int(CliExitCode.OK)
    except CLICK_EXIT_TYPES as exc:
        return int(getattr(exc, "exit_code", CliExitCode.OK))
    except CLICK_ABORT_TYPES:
        return int(CliExitCode.EXECUTION_ERROR)
    except CLICK_USAGE_TYPES as exc:
        msg = exc.format_message() if hasattr(exc, "format_message") else str(exc)
        return render_error(
            OpenDVError(msg, error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT),
            json_mode=json_flag,
        )
    except (
        CliHandledError,
        OpenDVError,
        ValidationError,
        httpx.HTTPError,
        ValueError,
        OSError,
        RuntimeError,
        TypeError,
    ) as exc:
        err, data, instance = coerce_cli_error(exc)
        return render_error(err, json_mode=json_flag, instance=instance, data=data)
    return int(CliExitCode.OK)
