"""Spec-driven standalone CLI entrypoints (`opendv` and `axi-dv`) for Open-DV v6.0."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from opendv_cli.executor import LocalExecutor
from opendv_cli_common import (
    CommandExecutor,
    build_argument_parser,
    present_result,
    print_jcs,
)

SUBCOMMANDS = frozenset({"verify", "profile", "compile", "sandbox", "run", "hold", "pipeline"})
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
    }
)


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


def build_parser(prog: str = "opendv") -> argparse.ArgumentParser:
    """Construct the spec-driven ArgumentParser for `opendv` or `axi-dv`."""
    return build_argument_parser(prog=prog)


def _dispatch(
    args: argparse.Namespace,
    *,
    executor: CommandExecutor | None = None,
) -> int:
    """Dispatch parsed CLI arguments to LocalExecutor (or provided executor) and format output."""
    try:
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
    parser = build_parser(prog="opendv")
    if not raw_args:
        parser.print_help()
        return 0

    if not any(flag in raw_args for flag in ("-h", "--help")) and not _has_explicit_subcommand(
        raw_args
    ):
        raw_args = ["verify", *raw_args]

    try:
        args = parser.parse_args(raw_args)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 2
        if code != 0 and ("--json" in raw_args or "-f" in raw_args):
            print_jcs({"status": "ERROR", "detail": "Invalid CLI arguments."})
        return code

    return _dispatch(args, executor=executor)


def axi_dv_main(
    argv: list[str] | None = None,
    *,
    executor: CommandExecutor | None = None,
    **kwargs: Any,
) -> int:
    """Entrypoint for the `axi-dv` verification CLI binary (alias for `opendv verify`)."""
    raw_args = list(sys.argv[1:] if argv is None else argv)

    if any(flag in raw_args for flag in ("-h", "--help")):
        parser = build_parser(prog="axi-dv")
        parser.parse_args(raw_args)
        return 0

    if not _has_explicit_subcommand(raw_args):
        raw_args = ["verify", *raw_args]

    parser = build_parser(prog="axi-dv")
    try:
        args = parser.parse_args(raw_args)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 2
        if code != 0 and ("--json" in raw_args or "-f" in raw_args):
            print_jcs({"status": "ERROR", "detail": "Invalid CLI arguments."})
        return code

    return _dispatch(args, executor=executor)


main = app_main

if __name__ == "__main__":
    raise SystemExit(app_main())
