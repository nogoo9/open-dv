"""Schema and structural definitions for OpenDV CLI specification."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

OptionType = Literal["string", "integer", "boolean", "path", "array"]
Availability = Literal["both", "standalone_only", "client_only"]


@dataclass(frozen=True)
class OptionDef:
    """Declarative definition of a command option/flag."""

    name: str
    flag: str
    type: OptionType
    help: str
    short: str | None = None
    required: bool = False
    default: Any = None
    choices: list[str] | None = None
    env_var: str | None = None
    inline_upload: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert option to dictionary conforming to cli-spec.schema.json."""
        data: dict[str, Any] = {
            "name": self.name,
            "flag": self.flag,
            "short": self.short,
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "choices": self.choices,
            "env_var": self.env_var,
            "inline_upload": self.inline_upload,
            "help": self.help,
        }
        return data


@dataclass(frozen=True)
class CommandDef:
    """Declarative definition of a command or subcommand."""

    name: str
    description: str
    help: str
    availability: Availability = "both"
    aliases: list[str] = field(default_factory=list)
    request_dto: str | None = None
    response_dto: str | None = None
    options: list[OptionDef] = field(default_factory=list)
    subcommands: list[CommandDef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert command to dictionary conforming to cli-spec.schema.json."""
        data: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "help": self.help,
            "aliases": list(self.aliases),
            "availability": self.availability,
        }
        if self.subcommands:
            data["subcommands"] = [sub.to_dict() for sub in self.subcommands]
        else:
            data["request_dto"] = self.request_dto
            data["response_dto"] = self.response_dto
            data["options"] = [opt.to_dict() for opt in self.options]
        return data


# --- Global CLI Options ---
GLOBAL_OPTIONS: list[OptionDef] = [
    OptionDef(
        name="format",
        flag="--format",
        short="-f",
        type="string",
        required=False,
        default="toon",
        choices=["toon", "json", "text"],
        help="Output formatting style (diagnostic TOON, machine RFC 8785 JSON, or human table)",
    ),
    OptionDef(
        name="verbose",
        flag="--verbose",
        short="-v",
        type="boolean",
        required=False,
        default=False,
        help="Enable verbose diagnostic logs",
    ),
    OptionDef(
        name="version",
        flag="--version",
        type="boolean",
        required=False,
        default=False,
        help="Show OpenDV CLI version",
    ),
    OptionDef(
        name="json",
        flag="--json",
        type="boolean",
        required=False,
        default=False,
        help="Format command output as deterministic RFC 8785 canonical JSON",
    ),
    OptionDef(
        name="toon",
        flag="--toon",
        type="boolean",
        required=False,
        default=False,
        help="Print compact diagnostic @toon_v1 trace output",
    ),
    OptionDef(
        name="role",
        flag="--role",
        type="string",
        required=False,
        default="INTERNAL_MODELER",
        choices=[
            "PUBLIC",
            "INTERNAL_MODELER",
            "DATA_STEWARD",
            "AUDITOR_FINANCE",
            "RESTRICTED_PII_CUSTODIAN",
            "ADMIN",
            "EDITOR",
            "VIEWER",
            "AUTOMATED",
            "admin",
            "editor",
            "viewer",
            "automated",
        ],
        env_var="OPENDV_ROLE",
        help="5-Tier RBAC role clearance context",
    ),
]
