"""Spec-driven standalone CLI entrypoints (`opendv` and `axi-dv`) for Open-DV v6.0."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from opendv_cli.executor import LocalExecutor
from opendv_cli_common import (
    CommandExecutor,
    build_argument_parser,
    dump_cli_tree,
    present_result,
    print_jcs,
)

SUBCOMMANDS = frozenset(
    {
        "verify",
        "profile",
        "compile",
        "sandbox",
        "run",
        "hold",
        "pipeline",
        "ontology",
        "config",
        "serve",
    }
)
_BOOLEAN_FLAGS = frozenset(
    {
        "-h",
        "--help",
        "--json",
        "--toon",
        "--verbose",
        "-v",
        "--version",
        "--stream",
        "--stream-tokens",
        "--dump-cli-tree",
        "--open",
    }
)
_NO_SUBCOMMAND_EXEMPT_FLAGS: tuple[str, ...] = ("-h", "--help", "--version")


def _has_explicit_subcommand(raw_args: list[str]) -> bool:
    """Return True if raw_args contains an explicit positional subcommand token."""
    skip_next = False
    for tok in raw_args:
        if skip_next:
            skip_next = False
            continue
        if tok == "--":
            break
        if tok.startswith("-"):
            if "=" not in tok and tok not in _BOOLEAN_FLAGS:
                skip_next = True
            continue
        return tok in SUBCOMMANDS
    return False


def _wants_json(raw_args: list[str]) -> bool:
    """Return True if CLI arguments indicate JSON output was requested."""
    for i, tok in enumerate(raw_args):
        if tok in ("--json", "--json-output"):
            return True
        if tok in ("-f", "--format") and i + 1 < len(raw_args) and raw_args[i + 1] == "json":
            return True
        if tok.startswith(("-f=", "--format=")) and tok.split("=", 1)[1] == "json":
            return True
    return False


def _has_cli_flag(raw_args: list[str], long_flag: str, short_flag: str | None = None) -> bool:
    """Return True if an option flag was explicitly passed in CLI `raw_args` before `--`."""
    for tok in raw_args:
        if tok == "--":
            break
        if tok == long_flag or tok.startswith(f"{long_flag}="):
            return True
        if short_flag and (tok == short_flag or tok.startswith(f"{short_flag}=")):
            return True
    return False


def _annotate_explicit_cli_flags(args: argparse.Namespace, raw_args: list[str]) -> None:
    """Record which CLI flags with spec defaults were explicitly passed by the user."""
    args._explicit_role = args.role if _has_cli_flag(raw_args, "--role") else None
    is_studio_create = (
        getattr(args, "subcommand", None) == "ontology"
        and getattr(args, "ontology_action", None) == "studio"
        and getattr(args, "studio_action", None) == "create"
    )
    if is_studio_create:
        args._explicit_domain_namespace = _has_cli_flag(raw_args, "--domain-namespace", "-n")
        args._explicit_enterprise_owner = _has_cli_flag(raw_args, "--enterprise-owner", "-o")


def build_parser(prog: str = "opendv") -> argparse.ArgumentParser:
    """Construct the spec-driven ArgumentParser for `opendv` or `axi-dv`."""
    return build_argument_parser(prog=prog)


def _dispatch(
    args: argparse.Namespace,
    *,
    prog: str = "opendv",
    executor: CommandExecutor | None = None,
) -> int:
    """Dispatch parsed CLI arguments to LocalExecutor (or provided executor) and format output."""
    try:
        if getattr(args, "dump_cli_tree", False):
            sys.stdout.write(dump_cli_tree(prog=prog, mode="standalone") + "\n")
            return 0
        if getattr(args, "subcommand", None) is None and getattr(args, "version", False):
            from opendv_contracts.spec import load_cli_spec

            spec = load_cli_spec()
            spec_ver = spec.get("spec_version", "1.0.0")
            contract_ver = spec.get("contract_version", "1.0.0")
            sys.stdout.write(f"{prog} {spec_ver} (contract {contract_ver})\n")
            return 0
        sub = getattr(args, "subcommand", None) or "verify"
        exec_inst = executor or LocalExecutor()
        code, payload = exec_inst.execute(sub, args)
        present_result(payload, command=sub, args=args)
        return code
    except Exception as exc:  # noqa: BLE001 - CLI main top-level execution boundary
        if getattr(args, "json", False) or getattr(args, "format", None) == "json":
            print_jcs(
                {
                    "status": "ERROR",
                    "error_code": "INTERNAL_ERROR",
                    "error": str(exc),
                    "detail": str(exc),
                }
            )
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2


def app_main(
    argv: list[str] | None = None,
    *,
    executor: CommandExecutor | None = None,
    **kwargs: Any,
) -> int:
    """Primary entrypoint for the `opendv` standalone in-process CLI binary."""
    raw_args = list(sys.argv[1:] if argv is None else argv)

    if "--dump-cli-tree" in raw_args:
        sys.stdout.write(dump_cli_tree(prog="opendv", mode="standalone") + "\n")
        return 0

    parser = build_parser(prog="opendv")
    if not raw_args:
        parser.print_help()
        return 0

    if not any(
        flag in raw_args for flag in _NO_SUBCOMMAND_EXEMPT_FLAGS
    ) and not _has_explicit_subcommand(raw_args):
        raw_args = ["verify", *raw_args]

    try:
        args = parser.parse_args(raw_args)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 2
        if code != 0 and _wants_json(raw_args):
            print_jcs(
                {
                    "detail": "Invalid CLI arguments.",
                    "error_code": "SYNTAX_INVALID_ARGUMENT",
                    "status": "ERROR",
                }
            )
        return code

    _annotate_explicit_cli_flags(args, raw_args)

    return _dispatch(args, prog="opendv", executor=executor)


def axi_dv_main(
    argv: list[str] | None = None,
    *,
    executor: CommandExecutor | None = None,
    **kwargs: Any,
) -> int:
    """Entrypoint for the `axi-dv` verification CLI binary (alias for `opendv verify`)."""
    raw_args = list(sys.argv[1:] if argv is None else argv)

    if "--dump-cli-tree" in raw_args:
        sys.stdout.write(dump_cli_tree(prog="axi-dv", mode="standalone") + "\n")
        return 0

    if any(flag in raw_args for flag in ("-h", "--help")):
        parser = build_parser(prog="axi-dv")
        parser.parse_args(raw_args)
        return 0

    if not any(
        flag in raw_args for flag in _NO_SUBCOMMAND_EXEMPT_FLAGS
    ) and not _has_explicit_subcommand(raw_args):
        raw_args = ["verify", *raw_args]

    parser = build_parser(prog="axi-dv")
    try:
        args = parser.parse_args(raw_args)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 2
        if code != 0 and _wants_json(raw_args):
            print_jcs(
                {
                    "detail": "Invalid CLI arguments.",
                    "error_code": "SYNTAX_INVALID_ARGUMENT",
                    "status": "ERROR",
                }
            )
        return code

    _annotate_explicit_cli_flags(args, raw_args)

    return _dispatch(args, prog="axi-dv", executor=executor)


main = app_main

if __name__ == "__main__":
    raise SystemExit(app_main())
