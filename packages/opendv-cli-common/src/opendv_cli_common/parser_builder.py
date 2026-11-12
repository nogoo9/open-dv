"""Dynamic ArgumentParser builder driven by cli-spec.json."""

from __future__ import annotations

import argparse
import os
from typing import Any

from opendv_contracts.spec import load_cli_spec

# Common flag aliases for usability and legacy script compatibility
_FLAG_ALIASES: dict[str, list[str]] = {
    "--cutoff": ["--assertion-cutoff-ts"],
    "--query-type": ["--query-id"],
    "--candidate-id": ["--candidate"],
    "--input": ["--input-spec"],
    "--json": ["--json-output"],
    "--project-id": ["--project"],
}


def _resolve_default(opt: dict[str, Any], *, suppress_default: bool) -> Any:
    """Resolve option default value, respecting declared env_var when non-empty."""
    if suppress_default:
        return argparse.SUPPRESS

    opt_type = opt.get("type", "string")
    default_val = opt.get("default", False if opt_type == "boolean" else None)
    env_var = opt.get("env_var")
    if env_var:
        env_raw = os.environ.get(env_var)
        if env_raw is not None and env_raw.strip() != "":
            choices = opt.get("choices")
            if opt_type == "boolean":
                return env_raw.strip().lower() in ("1", "true", "yes")
            if opt_type == "integer":
                try:
                    parsed_int = int(env_raw.strip())
                    if not choices or parsed_int in choices:
                        return parsed_int
                except ValueError:
                    pass
            elif not choices or env_raw.strip() in choices:
                return env_raw.strip()
    return default_val


def _add_option_to_parser(
    parser: argparse.ArgumentParser,
    opt: dict[str, Any],
    *,
    suppress_default: bool = False,
) -> None:
    """Attach a single declarative option specification to an ArgumentParser."""
    flag = opt["flag"]
    short = opt.get("short")
    name = opt["name"]
    opt_type = opt.get("type", "string")
    opt_help = opt.get("help", "")
    choices = opt.get("choices")
    is_required = bool(opt.get("required", False)) and not suppress_default

    flags: list[str] = [flag]
    if short and short not in flags:
        flags.insert(0, short)
    for alias in _FLAG_ALIASES.get(flag, []):
        if alias not in flags:
            flags.append(alias)

    flags = [f for f in flags if f not in parser._option_string_actions]
    if not flags:
        return

    dest = name.replace("-", "_")
    default_val = _resolve_default(opt, suppress_default=suppress_default)

    if opt_type == "boolean":
        parser.add_argument(
            *flags,
            dest=dest,
            action="store_true",
            default=default_val,
            help=opt_help,
        )
        if opt.get("default") is True:
            no_flag = f"--no-{name.replace('_', '-')}"
            if no_flag not in parser._option_string_actions:
                parser.add_argument(
                    no_flag,
                    dest=dest,
                    action="store_false",
                    default=default_val,
                    help=f"Disable {name}",
                )
    elif opt_type == "array":
        parser.add_argument(
            *flags,
            dest=dest,
            nargs="+",
            default=default_val,
            required=is_required,
            help=opt_help,
        )
    elif opt_type == "integer":
        parser.add_argument(
            *flags,
            dest=dest,
            type=int,
            default=default_val,
            choices=choices,
            required=is_required,
            help=opt_help,
        )
    else:
        # string or path
        parser.add_argument(
            *flags,
            dest=dest,
            type=str,
            default=default_val,
            choices=choices,
            required=is_required,
            help=opt_help,
        )


def _attach_command_node(
    subparsers_action: argparse._SubParsersAction[argparse.ArgumentParser],
    cmd: dict[str, Any],
    *,
    global_options: list[dict[str, Any]],
    depth: int,
    parent_name: str = "",
) -> None:
    """Recursively attach a command/subcommand node and any nested subcommands."""
    cmd_name = cmd["name"]
    cmd_aliases = cmd.get("aliases", [])
    cmd_help = cmd.get("help", cmd.get("description", ""))
    cmd_desc = cmd.get("description", cmd_help)

    cmd_parser = subparsers_action.add_parser(
        cmd_name,
        aliases=cmd_aliases,
        help=cmd_help,
        description=cmd_desc,
        allow_abbrev=False,
    )

    # 1. Register command-specific options first so they claim their short flags
    for opt in cmd.get("options", []):
        _add_option_to_parser(cmd_parser, opt, suppress_default=False)

    # 2. Register inherited global options second (skipping any short/long flag already claimed)
    for g_opt in global_options:
        _add_option_to_parser(cmd_parser, g_opt, suppress_default=True)

    if "--dump-cli-tree" not in cmd_parser._option_string_actions:
        cmd_parser.add_argument(
            "--dump-cli-tree",
            dest="dump_cli_tree",
            action="store_true",
            default=argparse.SUPPRESS,
            help=argparse.SUPPRESS,
        )

    nested_subs = cmd.get("subcommands", [])
    if nested_subs:
        sub_dest = f"{cmd_name}_action" if depth <= 2 else f"{parent_name}_{cmd_name}_action"
        nested_action = cmd_parser.add_subparsers(
            dest=sub_dest,
            title=f"{cmd_name} subcommands",
        )
        next_parent_name = cmd_name if depth == 2 else f"{parent_name}_{cmd_name}"
        for sub in nested_subs:
            _attach_command_node(
                nested_action,
                sub,
                global_options=global_options,
                depth=depth + 1,
                parent_name=next_parent_name if depth >= 2 else cmd_name,
            )


def build_argument_parser(
    spec: dict[str, Any] | None = None,
    *,
    prog: str = "opendv",
) -> argparse.ArgumentParser:
    """Generate an argparse.ArgumentParser tree directly from cli-spec.json.

    Args:
        spec: Parsed CLI spec dictionary (defaults to loading cli-spec.json).
        prog: Executable program name displayed in help and usage (default: 'opendv').

    Returns:
        Configured ArgumentParser with all global options, subcommands, and options.
    """
    cli_spec = spec or load_cli_spec()
    description = cli_spec.get("description", "Open-DV Standalone In-Process Engine CLI")
    global_options: list[dict[str, Any]] = list(cli_spec.get("global_options", []))

    # 1. Root parent parser for top-level global options
    root_parent = argparse.ArgumentParser(add_help=False, allow_abbrev=False)

    for opt in global_options:
        _add_option_to_parser(root_parent, opt, suppress_default=False)

    # Hidden flag for RFC 8785 canonical command tree inspection (Decision D6, T15)
    root_parent.add_argument(
        "--dump-cli-tree",
        dest="dump_cli_tree",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )

    # 2. Root parser
    parser = argparse.ArgumentParser(
        prog=prog,
        description=description,
        parents=[root_parent],
        allow_abbrev=False,
    )

    # 3. Recursive subcommands
    subparsers = parser.add_subparsers(dest="subcommand", title="subcommands")

    for cmd in cli_spec.get("commands", []):
        _attach_command_node(
            subparsers,
            cmd,
            global_options=global_options,
            depth=1,
        )

    return parser


__all__ = ["build_argument_parser"]
