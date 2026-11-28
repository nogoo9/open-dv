"""Core Open-DV CLI subcommands: serve, doctor, agents, mcp, secrets, project."""

from __future__ import annotations

import os
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from pydantic import ValidationError

from opendv.cli._client import CliClient
from opendv.cli._output import (
    CLICK_EXCEPTION_TYPES,
    CliGlobalOptions,
    CliHandledError,
    coerce_cli_error,
    parse_csv_list,
    parse_json_arg,
    read_stdin_secret,
    read_text_arg,
    render_error,
    render_success,
)
from opendv.models import ErrorCode, ExternalMcpServerSpec, OpenDVError, ProjectInitReq

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_CLI_HANDLED_EXC_TYPES: tuple[type[BaseException], ...] = (
    CliHandledError,
    OpenDVError,
    ValidationError,
    httpx.HTTPError,
    ValueError,
    OSError,
    RuntimeError,
    TypeError,
    *CLICK_EXCEPTION_TYPES,
)

ProjectOpt = Annotated[str, typer.Option("--project", help="Project identifier.")]
SecretRefsOpt = Annotated[
    list[str] | None,
    typer.Option("--secret-ref", help="Secret key referenced by connector or MCP server."),
]


def cli_context(ctx: typer.Context) -> CliGlobalOptions:
    """Retrieve root `CliGlobalOptions` from Typer context."""
    obj = ctx.obj
    return obj if isinstance(obj, CliGlobalOptions) else CliGlobalOptions()


def make_client(ctx: typer.Context) -> CliClient:
    """Instantiate a `CliClient` bound to active global options."""
    return CliClient(cli_context(ctx))


def run_cli_action(
    ctx: typer.Context,
    action: Callable[[CliClient], Any],
    *,
    plain_formatter: Callable[[Any], str] | None = None,
) -> None:
    """Execute one CLI action under scoped `--env-first`, rendering `CliEnvelope` and exits."""
    from opendv.service.secrets import scoped_env_first

    opts = cli_context(ctx)
    client = make_client(ctx)
    try:
        with scoped_env_first(opts.env_first):
            result = action(client)
    except _CLI_HANDLED_EXC_TYPES as exc:
        err, data, instance = coerce_cli_error(exc)
        exit_code = render_error(err, json_mode=opts.json_mode, instance=instance, data=data)
        raise typer.Exit(code=exit_code) from exc

    plain_text = plain_formatter(result) if plain_formatter is not None else None
    render_success(result, json_mode=opts.json_mode, plain_text=plain_text)


def is_loopback_host(host: str) -> bool:
    """Return True only when `host` designates local loopback."""
    return host.strip().lower().strip("[]") in _LOOPBACK_HOSTS


def serve_command(
    ctx: typer.Context,
    host: Annotated[
        str, typer.Option("--host", help="Bind host (loopback by default).")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="HTTP listen port.")] = 8420,
    allow_remote_unauthenticated: Annotated[
        bool,
        typer.Option(
            "--allow-remote-unauthenticated",
            help="Explicit opt-in required before binding a non-loopback host.",
        ),
    ] = False,
    trust_workspace: Annotated[
        Path | None,
        typer.Option("--trust-workspace", help="Trust workspace agent overrides before serving."),
    ] = None,
) -> None:
    """Launch the local `:8420` FastAPI Open-DV Workbench server (fail closed on remote bind)."""
    opts = cli_context(ctx)
    if not is_loopback_host(host) and not allow_remote_unauthenticated:
        err = OpenDVError(
            f"Refusing to bind non-loopback host '{host}' without "
            "--allow-remote-unauthenticated.",
            error_code=ErrorCode.REMOTE_BIND_UNAUTHENTICATED,
        )
        raise typer.Exit(code=render_error(err, json_mode=opts.json_mode))

    if port < 1 or port > 65535:
        err = OpenDVError(
            f"Invalid port {port}; expected 1..65535.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
        raise typer.Exit(code=render_error(err, json_mode=opts.json_mode))

    def _run_serve(client: CliClient) -> None:
        if trust_workspace is not None:
            client.trust_workspace(trust_workspace)
        os.environ["OPENDV_BOUND_HOST"] = host
        os.environ["OPENDV_ALLOW_REMOTE_UNAUTHENTICATED"] = (
            "1" if allow_remote_unauthenticated else "0"
        )
        import uvicorn

        uvicorn.run("opendv.api.server:create_app", factory=True, host=host, port=port)

    run_cli_action(ctx, _run_serve, plain_formatter=lambda _: f"Serving on {host}:{port}")


def doctor_command(
    ctx: typer.Context,
    check_agents: Annotated[
        bool,
        typer.Option("--check-agents", help="Probe CLI harness binaries, packs, and secrets."),
    ] = False,
    check_mcp: Annotated[
        bool,
        typer.Option("--check-mcp", help="Inspect registered external MCP endpoints and secrets."),
    ] = False,
    probe: Annotated[
        bool,
        typer.Option(
            "--probe",
            help="Run live Tier-2 authentication probe on active harness_provider.",
        ),
    ] = False,
) -> None:
    """Run Layer-4 diagnostic checks across service health, agent harnesses, and MCP servers."""

    def _run(client: CliClient) -> dict[str, Any]:
        doc = client.run_doctor(
            check_agents=check_agents,
            check_mcp=check_mcp,
            probe=probe,
        )
        payload = doc.model_dump(mode="json")
        for key in ("agents", "mcp_servers"):
            if payload.get(key) is None:
                payload.pop(key, None)
        return payload

    run_cli_action(ctx, _run)


# ---- agents ----
agents_app = typer.Typer(
    name="agents", help="List and inspect Markdown agent packs.", no_args_is_help=True
)


@agents_app.command("list")
def agents_list(ctx: typer.Context) -> None:
    """List built-in and workspace agent packs."""
    run_cli_action(ctx, lambda c: c.list_agents())


@agents_app.command("inspect")
def agents_inspect(
    ctx: typer.Context,
    agent_id: Annotated[str, typer.Argument(help="Agent identifier to inspect.")],
) -> None:
    """Show full agent prompt metadata and workspace trust state."""
    run_cli_action(ctx, lambda c: c.get_agent(agent_id))


# ---- mcp ----
mcp_app = typer.Typer(
    name="mcp",
    help="Manage external MCP servers and stdio MCP bridge.",
    no_args_is_help=True,
)


@mcp_app.command("list")
def mcp_list(ctx: typer.Context) -> None:
    """List registered external MCP servers."""
    run_cli_action(ctx, lambda c: c.list_mcp_servers())


@mcp_app.command("register")
def mcp_register(
    ctx: typer.Context,
    server_id: Annotated[str, typer.Option("--id", help="Unique server identifier.")],
    transport: Annotated[str, typer.Option("--transport", help="stdio or streamable_http.")],
    command: Annotated[
        str | None, typer.Option("--command", help="JSON argv list for stdio transport.")
    ] = None,
    url: Annotated[
        str | None, typer.Option("--url", help="HTTPS URL for streamable_http transport.")
    ] = None,
    secret_ref: SecretRefsOpt = None,
    allow_scout_calls: Annotated[bool, typer.Option("--allow-scout-calls")] = False,
    allow_document_pages: Annotated[bool, typer.Option("--allow-document-pages")] = False,
) -> None:
    """Register or update a bounded external MCP server."""

    def _run(client: CliClient) -> Any:
        cmd_list: list[str] | None = None
        if command is not None:
            parsed = parse_json_arg(command, label="--command", expect_type=list)
            if not all(isinstance(part, str) for part in parsed):
                raise OpenDVError(
                    "--command JSON array must contain strings.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
            cmd_list = list(parsed)
        spec = ExternalMcpServerSpec(
            server_id=server_id,
            transport=transport,  # type: ignore[arg-type]
            command=cmd_list,
            url=url,
            secret_refs=list(secret_ref or []),
            allow_scout_calls=allow_scout_calls,
            allow_document_pages=allow_document_pages,
        )
        return client.register_mcp_server(spec)

    run_cli_action(ctx, _run)


@mcp_app.command("remove")
def mcp_remove(
    ctx: typer.Context,
    server_id: Annotated[str, typer.Option("--id", help="Server identifier to delete.")],
) -> None:
    """Unregister an external MCP server."""
    run_cli_action(ctx, lambda c: c.remove_mcp_server(server_id))


class McpProfileOpt(StrEnum):
    """Tool profile exposed by `opendv mcp serve`."""

    PROJECT = "project"
    PARSING = "parsing"
    ALL = "all"


@mcp_app.command("serve")
def mcp_serve(
    ctx: typer.Context,
    transport: Annotated[str, typer.Option("--transport", help="MCP transport (stdio).")] = "stdio",
    profile: Annotated[
        McpProfileOpt,
        typer.Option("--profile", help="Tool profile: project, parsing, or all."),
    ] = McpProfileOpt.PROJECT,
    project: Annotated[
        str | None,
        typer.Option(
            "--project",
            help="Project identifier (required for project and all profiles).",
        ),
    ] = None,
    allow_root: Annotated[
        list[Path] | None,
        typer.Option(
            "--allow-root",
            help="Repeatable allowed root directory for parsing MCP tools.",
        ),
    ] = None,
    allow_unredacted: Annotated[
        bool | None,
        typer.Option(
            "--allow-unredacted/--no-allow-unredacted",
            help="Allow unredacted parse_extract or parse_render_page calls.",
        ),
    ] = None,
    max_inline_bytes: Annotated[
        int | None,
        typer.Option(
            "--max-inline-bytes",
            help="Maximum inline MCP response bytes before spillover (>= 1).",
        ),
    ] = None,
    spill_dir: Annotated[
        Path | None,
        typer.Option(
            "--spill-dir",
            help="Directory for oversized MCP parsing payload spillover.",
        ),
    ] = None,
) -> None:
    """Serve the Open-DV lifecycle and/or stateless parsing MCP server over stdio."""
    opts = cli_context(ctx)

    def _fail_arg(message: str) -> None:
        err = OpenDVError(message, error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT)
        raise typer.Exit(code=render_error(err, json_mode=opts.json_mode))

    if transport != "stdio":
        _fail_arg(f"Unsupported MCP transport '{transport}'; only 'stdio' is supported.")
    if max_inline_bytes is not None and max_inline_bytes < 1:
        _fail_arg("--max-inline-bytes must be >= 1.")

    if profile == McpProfileOpt.PROJECT:
        if any(
            val is not None
            for val in (allow_root, allow_unredacted, max_inline_bytes, spill_dir)
        ):
            _fail_arg(
                "Parsing flags (--allow-root, --allow-unredacted, --max-inline-bytes, "
                "--spill-dir) cannot be used with --profile project."
            )
        if project is None or not project.strip():
            _fail_arg("--project is required for `opendv mcp serve`.")
    elif profile == McpProfileOpt.PARSING:
        if project is not None:
            _fail_arg("--project cannot be passed with --profile parsing.")
    else:
        if project is None or not project.strip():
            _fail_arg("--project is required for `opendv mcp serve --profile all`.")

    from opendv.cli.mcp_stdio import run_mcp_stdio_server
    from opendv.service.os_platform import resolve_user_state_dir
    from opendv.service.secrets import scoped_env_first

    eff_allow_roots: list[Path] | None = None
    eff_allow_unredacted = False
    eff_max_inline_bytes = 65_536
    eff_spill_dir: Path | None = None
    if profile in (McpProfileOpt.PARSING, McpProfileOpt.ALL):
        eff_allow_roots = list(allow_root) if allow_root is not None else [Path.cwd()]
        eff_allow_unredacted = bool(allow_unredacted) if allow_unredacted is not None else False
        eff_max_inline_bytes = max_inline_bytes if max_inline_bytes is not None else 65_536
        eff_spill_dir = (
            spill_dir if spill_dir is not None else (resolve_user_state_dir() / "parse_spill")
        )

    try:
        with scoped_env_first(opts.env_first):
            run_mcp_stdio_server(
                profile=profile.value,
                project_id=project,
                allow_roots=eff_allow_roots,
                allow_unredacted=eff_allow_unredacted,
                max_inline_bytes=eff_max_inline_bytes,
                spill_dir=eff_spill_dir,
                cli_env_first=opts.env_first,
            )
    except _CLI_HANDLED_EXC_TYPES as exc:
        err, data, instance = coerce_cli_error(exc)
        raise typer.Exit(
            code=render_error(err, json_mode=opts.json_mode, instance=instance, data=data)
        ) from exc


# ---- secrets ----
secrets_app = typer.Typer(
    name="secrets", help="Manage local OS keyring or headless secrets.", no_args_is_help=True
)


@secrets_app.command("set")
def secrets_set(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="Secret key name.")],
    from_stdin: Annotated[bool, typer.Option("--stdin", help="Read secret from stdin.")] = False,
    prompt_val: Annotated[
        bool, typer.Option("--prompt", help="Prompt interactively with masked input.")
    ] = False,
) -> None:
    """Store a secret value safely without echoing plaintext to stdout or argv."""

    def _run(client: CliClient) -> Any:
        if from_stdin == prompt_val:
            raise OpenDVError(
                "Specify exactly one of --stdin or --prompt.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        value = read_stdin_secret() if from_stdin else typer.prompt(
            f"Secret value for {key}", hide_input=True
        )
        return client.set_secret(key, value)

    run_cli_action(ctx, _run)


@secrets_app.command("list")
def secrets_list(
    ctx: typer.Context,
    keys: Annotated[
        str | None, typer.Option("--keys", help="Optional comma-separated secret keys to probe.")
    ] = None,
) -> None:
    """List secret presence and backend metadata (never exposes values)."""
    run_cli_action(ctx, lambda c: c.list_secrets(parse_csv_list(keys)))


@secrets_app.command("delete")
def secrets_delete(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="Secret key to delete.")],
) -> None:
    """Delete a stored secret key."""
    run_cli_action(ctx, lambda c: c.delete_secret(key))


# ---- project ----
project_app = typer.Typer(
    name="project",
    help="Create, inspect, export, and import Open-DV projects.",
    no_args_is_help=True,
)


@project_app.command("init")
def project_init(
    ctx: typer.Context,
    project: ProjectOpt,
    namespace: Annotated[str, typer.Option("--namespace", help="Domain namespace URI/slug.")],
    starter_pack: Annotated[str | None, typer.Option("--starter-pack")] = None,
    workspace_seed: Annotated[str | None, typer.Option("--workspace-seed")] = None,
    baseline: Annotated[
        str | None, typer.Option("--baseline", help="Baseline ontology file.")
    ] = None,
    baseline_format: Annotated[str, typer.Option("--baseline-format")] = "yaml",
) -> None:
    """Initialize a new Open-DV project."""

    def _run(client: CliClient) -> Any:
        baseline_content = read_text_arg(baseline, label="--baseline") if baseline else None
        req = ProjectInitReq(
            project_id=project,
            domain_namespace=namespace,
            starter_pack_id=starter_pack,
            workspace_seed_id=workspace_seed,
            baseline_content=baseline_content,
            baseline_format=baseline_format,  # type: ignore[arg-type]
        )
        return client.init_project(req)

    run_cli_action(ctx, _run)


@project_app.command("list")
def project_list(ctx: typer.Context) -> None:
    """List all projects in the workspace."""
    run_cli_action(ctx, lambda c: c.list_projects())


@project_app.command("show")
def project_show(ctx: typer.Context, project: ProjectOpt) -> None:
    """Show full metadata for one project."""
    run_cli_action(ctx, lambda c: c.get_project(project))


@project_app.command("delete")
def project_delete(ctx: typer.Context, project: ProjectOpt) -> None:
    """Delete a project cleanly."""
    run_cli_action(ctx, lambda c: c.delete_project(project))


@project_app.command("export")
def project_export(
    ctx: typer.Context,
    project: ProjectOpt,
    out: Annotated[Path, typer.Option("--out", help="Output `.opendv.zip` bundle path.")],
    full_demo: Annotated[
        bool, typer.Option("--full-demo", help="Include raw sources in export bundle.")
    ] = False,
) -> None:
    """Export a project bundle (`core_project_state` by default)."""
    run_cli_action(ctx, lambda c: c.export_project(project, out_path=out, full_demo=full_demo))


@project_app.command("import")
def project_import(
    ctx: typer.Context,
    bundle_zip: Annotated[Path, typer.Argument(help="Path to `.opendv.zip` archive.")],
    conflict: Annotated[str, typer.Option("--conflict", help="fail, rename, or replace.")] = "fail",
    trust_workspace: Annotated[
        bool, typer.Option("--trust-workspace", help="Opt-in to trust embedded workspace agents.")
    ] = False,
) -> None:
    """Import a signed `.opendv.zip` project bundle."""
    run_cli_action(
        ctx,
        lambda c: c.import_project(
            bundle_zip, conflict=conflict, trust_workspace=trust_workspace
        ),
    )
