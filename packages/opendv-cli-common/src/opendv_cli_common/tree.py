"""Spec-derived CLI command tree generator and RFC 8785 canonical serializer."""

from __future__ import annotations

from typing import Any

from opendv_contracts.jcs import dumps_jcs
from opendv_contracts.spec import load_cli_spec


def build_cli_tree(
    spec: dict[str, Any] | None = None,
    *,
    prog: str = "opendv",
    mode: str = "standalone",
) -> dict[str, Any]:
    """Build the canonical effective command tree for the specified execution mode.

    Args:
        spec: Raw cli-spec dictionary (defaults to load_cli_spec()).
        prog: Executable name (default: "opendv").
        mode: Execution mode, either "standalone" (Python in-process) or "client" (Bun opendvctl).

    Returns:
        Structured dictionary representing the effective command tree.
    """
    cli_spec = spec or load_cli_spec()

    # Filter global options
    global_options = [dict(opt) for opt in cli_spec.get("global_options", [])]

    # Exclude entries that belong exclusively to the other mode
    excluded_availability = "client_only" if mode == "standalone" else "standalone_only"

    commands_tree: dict[str, Any] = {}
    for cmd in cli_spec.get("commands", []):
        cmd_avail = cmd.get("availability", "both")
        if cmd_avail == excluded_availability:
            continue

        cmd_name = cmd["name"]
        cmd_node: dict[str, Any] = {
            "name": cmd_name,
            "description": cmd.get("description", ""),
            "help": cmd.get("help", cmd.get("description", "")),
            "aliases": sorted(cmd.get("aliases", [])),
            "availability": cmd_avail,
        }

        subcommands = cmd.get("subcommands", [])
        if subcommands:
            sub_dict: dict[str, Any] = {}
            for sub in subcommands:
                sub_avail = sub.get("availability", "both")
                if sub_avail == excluded_availability:
                    continue
                sub_name = sub["name"]
                sub_node: dict[str, Any] = {
                    "name": sub_name,
                    "description": sub.get("description", ""),
                    "help": sub.get("help", sub.get("description", "")),
                    "aliases": sorted(sub.get("aliases", [])),
                    "availability": sub_avail,
                    "options": [dict(opt) for opt in sub.get("options", [])],
                }
                if "request_dto" in sub:
                    sub_node["request_dto"] = sub["request_dto"]
                if "response_dto" in sub:
                    sub_node["response_dto"] = sub["response_dto"]
                sub_dict[sub_name] = sub_node
            cmd_node["subcommands"] = sub_dict
        else:
            cmd_node["options"] = [dict(opt) for opt in cmd.get("options", [])]
            if "request_dto" in cmd:
                cmd_node["request_dto"] = cmd["request_dto"]
            if "response_dto" in cmd:
                cmd_node["response_dto"] = cmd["response_dto"]

        commands_tree[cmd_name] = cmd_node

    return {
        "commands": commands_tree,
        "contract_version": cli_spec.get("contract_version", "1.0.0"),
        "error_map": cli_spec.get("error_map", []),
        "global_options": global_options,
        "prog": prog,
        "spec_version": cli_spec.get("spec_version", "1.0.0"),
    }


def dump_cli_tree(
    spec: dict[str, Any] | None = None,
    *,
    prog: str = "opendv",
    mode: str = "standalone",
) -> str:
    """Serialize the effective command tree to RFC 8785 canonical JSON string."""
    tree = build_cli_tree(spec, prog=prog, mode=mode)
    return dumps_jcs(tree)


__all__ = ["build_cli_tree", "dump_cli_tree"]
