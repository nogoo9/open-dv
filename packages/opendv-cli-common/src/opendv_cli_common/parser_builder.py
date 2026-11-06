"""Dynamic ArgumentParser builder driven by cli-spec.json."""

from __future__ import annotations

import argparse
from typing import Any

from opendv_contracts.spec import load_cli_spec

# Common flag aliases for usability and legacy script compatibility
_FLAG_ALIASES: dict[str, list[str]] = {
    "--cutoff": ["--assertion-cutoff-ts"],
    "--query-type": ["--query-id"],
    "--candidate-id": ["--candidate"],
    "--input": ["--input-spec"],
    "--json": ["--json-output"],
}


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
    if short:
        flags.insert(0, short)
    for alias in _FLAG_ALIASES.get(flag, []):
        if alias not in flags:
            flags.append(alias)

    # Avoid duplicate option string conflict with parent parsers
    flags = [f for f in flags if f not in parser._option_string_actions]
    if not flags:
        return

    dest = name.replace("-", "_")

    if opt_type == "boolean":
        default_val = argparse.SUPPRESS if suppress_default else opt.get("default", False)
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
        default_val = argparse.SUPPRESS if suppress_default else opt.get("default")
        parser.add_argument(
            *flags,
            dest=dest,
            nargs="+",
            default=default_val,
            required=is_required,
            help=opt_help,
        )
    elif opt_type == "integer":
        default_val = argparse.SUPPRESS if suppress_default else opt.get("default")
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
        default_val = argparse.SUPPRESS if suppress_default else opt.get("default")
        parser.add_argument(
            *flags,
            dest=dest,
            type=str,
            default=default_val,
            choices=choices,
            required=is_required,
            help=opt_help,
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

    # 1. Shared parent parsers for global options
    root_parent = argparse.ArgumentParser(add_help=False)
    sub_parent = argparse.ArgumentParser(add_help=False)

    for opt in cli_spec.get("global_options", []):
        _add_option_to_parser(root_parent, opt, suppress_default=False)
        _add_option_to_parser(sub_parent, opt, suppress_default=True)

    # Hidden flag for RFC 8785 canonical command tree inspection (Decision D6, T15)
    root_parent.add_argument(
        "--dump-cli-tree",
        dest="dump_cli_tree",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )
    sub_parent.add_argument(
        "--dump-cli-tree",
        dest="dump_cli_tree",
        action="store_true",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )

    # 2. Root parser
    parser = argparse.ArgumentParser(
        prog=prog,
        description=description,
        parents=[root_parent],
    )

    # 3. Subcommands
    subparsers = parser.add_subparsers(dest="subcommand", title="subcommands")

    for cmd in cli_spec.get("commands", []):
        cmd_name = cmd["name"]
        cmd_aliases = cmd.get("aliases", [])
        cmd_help = cmd.get("help", cmd.get("description", ""))
        cmd_desc = cmd.get("description", cmd_help)

        subcommands = cmd.get("subcommands", [])
        if subcommands:
            # Nested command with sub-actions (e.g. 'pipeline', 'run', 'hold')
            cmd_parser = subparsers.add_parser(
                cmd_name,
                aliases=cmd_aliases,
                help=cmd_help,
                description=cmd_desc,
                parents=[sub_parent],
            )
            sub_subparsers = cmd_parser.add_subparsers(
                dest=f"{cmd_name}_action",
                title=f"{cmd_name} subcommands",
            )
            for sub in subcommands:
                sub_name = sub["name"]
                sub_aliases = sub.get("aliases", [])
                sub_help = sub.get("help", sub.get("description", ""))
                sub_desc = sub.get("description", sub_help)
                sub_parser = sub_subparsers.add_parser(
                    sub_name,
                    aliases=sub_aliases,
                    help=sub_help,
                    description=sub_desc,
                    parents=[sub_parent],
                )
                for opt in sub.get("options", []):
                    _add_option_to_parser(sub_parser, opt, suppress_default=False)
        else:
            # Flat command (e.g. 'verify', 'profile', 'compile', 'sandbox')
            cmd_parser = subparsers.add_parser(
                cmd_name,
                aliases=cmd_aliases,
                help=cmd_help,
                description=cmd_desc,
                parents=[sub_parent],
            )
            for opt in cmd.get("options", []):
                _add_option_to_parser(cmd_parser, opt, suppress_default=False)

    return parser


__all__ = ["build_argument_parser"]
