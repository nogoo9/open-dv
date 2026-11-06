"""Automated CLI Reference Documentation Generator.

Generates `docs/16-cli-reference.md` directly from `schemas/v1/cli-spec.json`.
Documents:
- Architecture & Two-CLI model (`opendv` standalone Python vs `opendvctl` Bun platform client)
- Global options
- Complete command inventory with options, defaults, choices, types, DTOs, and availability
- Pre-rendered examples for both CLIs
- Exit code and error map specification
"""

from __future__ import annotations

from typing import Any

from opendv_contracts.spec import load_cli_spec


def generate_cli_reference_markdown(spec: dict[str, Any] | None = None) -> str:
    """Generate canonical Markdown CLI reference from CLI specification dictionary."""
    cli_spec = spec or load_cli_spec()

    lines: list[str] = [
        "# Open-DV CLI Reference (`opendv` & `opendvctl`)",
        "",
        "<!-- AUTO-GENERATED FROM schemas/v1/cli-spec.json. DO NOT EDIT DIRECTLY. -->",
        "",
        "> Canonical reference documentation for Open-DV command-line interfaces.",
        f"> **Specification Version:** `{cli_spec.get('spec_version', '1.0.0')}` · "
        f"**Contract Version:** `{cli_spec.get('contract_version', '1.0.0')}`",
        "",
        "---",
        "",
        "## 1. Architectural Overview: The Two-CLI Model",
        "",
        "Open-DV adheres to a strict **Two-CLI Architecture** (Decisions D6 and D7):",
        "",
        "| Characteristic | `opendv` (In-Process Standalone) | `opendvctl` (Platform Client) |",
        "| :--- | :--- | :--- |",
        (
            "| **Implementation** | Pure Python 3.12+ package | "
            "Compiled single-file Bun executable |"
        ),
        (
            "| **Execution Engine** | `LocalExecutor` directly calling `opendv-service` | "
            "`RemoteExecutor` calling Platform REST & SSE API |"
        ),
        (
            "| **Target Audience** | Local developers, air-gapped CI/CD, symbolic verification | "
            "Platform users, Web UI agents, remote teams |"
        ),
        (
            "| **Runtimes Required** | Python 3.12+ with virtualenv | "
            "**None** (Self-contained binary; no Node, Bun, or Python) |"
        ),
        (
            "| **Network Ports** | Zero network sockets (strictly blocked) | "
            "Connects via `OPENDV_API_URL` / `--api-url` |"
        ),
        (
            "| **Output Determinism** | RFC 8785 JSON (`--json`) & pre-rendered text | "
            "RFC 8785 JSON (`--json`) & pre-rendered text |"
        ),
        (
            "| **Hidden Introspection** | `--dump-cli-tree` (Canonical JCS) | "
            "`--dump-cli-tree` (Canonical JCS) |"
        ),
        "",
        "---",
        "",
        "## 2. Global Options",
        "",
        "The following global options apply across all top-level commands in both CLIs:",
        "",
        "| Flag | Short | Type | Default | Choices / Env Var | Description |",
        "| :--- | :---: | :---: | :---: | :---: | :--- |",
    ]

    for opt in cli_spec.get("global_options", []):
        short = f"`{opt['short']}`" if opt.get("short") else "—"
        default = f"`{opt['default']}`" if opt.get("default") is not None else "—"
        extra: list[str] = []
        if opt.get("choices"):
            extra.append(f"Choices: {', '.join(opt['choices'])}")
        if opt.get("env_var"):
            extra.append(f"Env: `{opt['env_var']}`")
        extra_str = "; ".join(extra) if extra else "—"
        desc = opt.get("help") or opt.get("description", "")
        lines.append(
            f"| `{opt['flag']}` | {short} | `{opt['type']}` | {default} | {extra_str} | {desc} |"
        )

    # Add client-only --api-url global option
    lines.append(
        "| `--api-url` | — | `string` | — | Env: `OPENDV_API_URL` | "
        "Open-DV Platform REST/SSE API base endpoint (`opendvctl` only) |"
    )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 3. Command Reference",
            "",
        ]
    )

    for cmd in cli_spec.get("commands", []):
        cmd_name = cmd["name"]
        aliases = cmd.get("aliases", [])
        alias_str = f" (aliases: {', '.join(f'`{a}`' for a in aliases)})" if aliases else ""
        avail = cmd.get("availability", "both")
        avail_badge = (
            "*(Both CLIs)*"
            if avail == "both"
            else "*(Standalone `opendv` only)*"
            if avail == "standalone_only"
            else "*(Platform client `opendvctl` only)*"
        )

        lines.extend(
            [
                f"### `opendv {cmd_name}` / `opendvctl {cmd_name}`{alias_str}",
                "",
                f"**Availability:** {avail_badge}  ",
                f"**Description:** {cmd.get('description', '')}  ",
                f"**Summary:** {cmd.get('help', '')}",
                "",
            ]
        )

        subcommands = cmd.get("subcommands", [])
        if subcommands:
            lines.extend(
                [
                    f"#### Subcommands of `{cmd_name}`",
                    "",
                ]
            )
            for sub in subcommands:
                sub_name = sub["name"]
                sub_avail = sub.get("availability", "both")
                sub_badge = (
                    "*(Both CLIs)*"
                    if sub_avail == "both"
                    else "*(Standalone `opendv` only)*"
                    if sub_avail == "standalone_only"
                    else "*(Platform client `opendvctl` only)*"
                )

                lines.extend(
                    [
                        f"##### `{cmd_name} {sub_name}` {sub_badge}",
                        "",
                        f"{sub.get('description', '')}",
                        "",
                    ]
                )

                options = sub.get("options", [])
                if options:
                    lines.extend(
                        [
                            (
                                "| Option | Short | Type | Required | Default | "
                                "Inline Upload | Description |"
                            ),
                            "| :--- | :---: | :---: | :---: | :---: | :---: | :--- |",
                        ]
                    )
                    for opt in options:
                        short = f"`{opt['short']}`" if opt.get("short") else "—"
                        req = "Yes" if opt.get("required") else "No"
                        default = f"`{opt['default']}`" if opt.get("default") is not None else "—"
                        inline = "Yes" if opt.get("inline_upload") else "No"
                        lines.append(
                            f"| `{opt['flag']}` | {short} | `{opt['type']}` | {req} | "
                            f"{default} | {inline} | {opt.get('help', '')} |"
                        )
                    lines.append("")

                if sub.get("request_dto"):
                    lines.append(f"- **Request DTO:** `{sub['request_dto']}`")
                if sub.get("response_dto"):
                    lines.append(f"- **Response DTO:** `{sub['response_dto']}`")

                lines.extend(
                    [
                        "",
                        "**Example Invocations:**",
                        "```bash",
                        "# Standalone execution",
                        f"opendv {cmd_name} {sub_name} --help",
                        "",
                        "# Client execution",
                        f"opendvctl {cmd_name} {sub_name} --api-url $OPENDV_API_URL --json",
                        "```",
                        "",
                    ]
                )
        else:
            options = cmd.get("options", [])
            if options:
                lines.extend(
                    [
                        (
                            "| Option | Short | Type | Required | Default | "
                            "Inline Upload | Description |"
                        ),
                        "| :--- | :---: | :---: | :---: | :---: | :---: | :--- |",
                    ]
                )
                for opt in options:
                    short = f"`{opt['short']}`" if opt.get("short") else "—"
                    req = "Yes" if opt.get("required") else "No"
                    default = f"`{opt['default']}`" if opt.get("default") is not None else "—"
                    inline = "Yes" if opt.get("inline_upload") else "No"
                    lines.append(
                        f"| `{opt['flag']}` | {short} | `{opt['type']}` | {req} | "
                        f"{default} | {inline} | {opt.get('help', '')} |"
                    )
                lines.append("")

            if cmd.get("request_dto"):
                lines.append(f"- **Request DTO:** `{cmd['request_dto']}`")
            if cmd.get("response_dto"):
                lines.append(f"- **Response DTO:** `{cmd['response_dto']}`")

            lines.extend(
                [
                    "",
                    "**Example Invocations:**",
                    "```bash",
                    "# Standalone execution",
                    f"opendv {cmd_name} --help",
                    "",
                    "# Client execution",
                    f"opendvctl {cmd_name} --api-url $OPENDV_API_URL --json",
                    "```",
                    "",
                ]
            )

    lines.extend(
        [
            "---",
            "",
            "## 4. Exit Codes & Error Mapping",
            "",
            "Both CLIs adhere to normative exit code standards across all failure scenarios:",
            "",
            "- `0`: Operation succeeded (`overall_passed: true` or status `COMPLETED`).",
            "- `1`: Domain or verification rejection (`overall_passed: false` or gate failure).",
            "- `2`: Syntax, argument error, configuration error, or schema validation failure.",
            "",
            "### Domain Error Mapping Specification",
            "",
            "| Error Code | Exit Code | HTTP Status | Problem Title | Problem Type URI |",
            "| :--- | :---: | :---: | :--- | :--- |",
        ]
    )

    for entry in cli_spec.get("error_map", []):
        lines.append(
            f"| `{entry['error_code']}` | `{entry['exit_code']}` | `{entry['http_status']}` | "
            f"{entry['problem_title']} | `{entry['problem_type_uri']}` |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 5. Output Formats & Canonical Serialization",
            "",
            "Both CLIs support three uniform output modes configured via `--format` / `-f`:",
            "",
            (
                "1. `--json`: Emits bit-for-bit canonical JSON compliant with "
                "**RFC 8785 JSON Canonicalization Scheme (JCS)**."
            ),
            (
                "2. `--toon`: Emits compact diagnostic `@toon_v1` traces "
                "pre-rendered by `opendv-service`."
            ),
            (
                "3. `--format text`: Emits human-readable ANSI status tables "
                "pre-rendered by `opendv-service`."
            ),
            "",
        ]
    )

    return "\n".join(lines) + "\n"
