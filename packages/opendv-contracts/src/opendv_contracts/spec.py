"""CLI specification loader, typed models, and validator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import jsonschema  # type: ignore[import-untyped]
from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel


class OptionSpec(BaseContractModel):
    """Specification of a CLI command option or flag."""

    name: str
    flag: str
    short: str | None = None
    type: str
    required: bool = False
    default: Any = None
    choices: list[str] | None = None
    env_var: str | None = None
    inline_upload: bool = False
    help: str


class CommandSpec(BaseContractModel):
    """Specification of a CLI command or subcommand."""

    name: str
    description: str
    help: str
    aliases: list[str] = Field(default_factory=list)
    availability: str = "both"
    request_dto: str | None = None
    response_dto: str | None = None
    options: list[OptionSpec] = Field(default_factory=list)
    subcommands: list[CommandSpec] = Field(default_factory=list)


class ErrorMapEntrySpec(BaseContractModel):
    """Mapping entry from an ErrorCode to CLI exit code and RFC 9457 problem fields."""

    error_code: str
    exit_code: int
    http_status: int
    problem_title: str
    problem_type_uri: str


class CliSpec(BaseContractModel):
    """Full language-neutral CLI specification model."""

    spec_version: str
    contract_version: str
    global_options: list[OptionSpec] = Field(default_factory=list)
    commands: list[CommandSpec]
    error_map: list[ErrorMapEntrySpec]


def _get_schemas_dir() -> Path:
    """Return the schemas directory inside opendv-contracts package."""
    pkg_root = Path(__file__).resolve().parent.parent.parent
    schemas_dir = pkg_root / "schemas"
    if schemas_dir.is_dir():
        return schemas_dir
    colocated = Path(__file__).resolve().parent / "schemas"
    if colocated.is_dir():
        return colocated
    raise FileNotFoundError(
        f"Could not locate schemas directory in {pkg_root} or {Path(__file__).parent}"
    )


def load_cli_spec(path: Path | str | None = None) -> dict[str, Any]:
    """Load the parsed cli-spec.json dictionary from specified path or default schemas."""
    if path is not None:
        target = Path(path)
        with open(target, encoding="utf-8") as f:
            return cast(dict[str, Any], json.load(f))

    # Try standard package path first
    default_path = _get_schemas_dir() / "cli-spec.json"
    if default_path.is_file():
        with open(default_path, encoding="utf-8") as f:
            return cast(dict[str, Any], json.load(f))

    # Try repository root schemas/v1 or schemas/
    pkg_root = Path(__file__).resolve().parent.parent.parent
    repo_root = pkg_root.parent.parent
    for candidate in [
        repo_root / "schemas" / "v1" / "cli-spec.json",
        repo_root / "schemas" / "cli-spec.json",
    ]:
        if candidate.is_file():
            with open(candidate, encoding="utf-8") as f:
                return cast(dict[str, Any], json.load(f))

    raise FileNotFoundError("Could not find cli-spec.json in package or repository schemas.")


def load_cli_spec_model(path: Path | str | None = None) -> CliSpec:
    """Load and validate the CLI specification as a typed Pydantic CliSpec model."""
    raw = load_cli_spec(path)
    return CliSpec.model_validate(raw)


def load_cli_spec_schema() -> dict[str, Any]:
    """Load the Draft 2020-12 cli-spec.schema.json dictionary."""
    path = _get_schemas_dir() / "cli-spec.schema.json"
    with open(path, encoding="utf-8") as f:
        return cast(dict[str, Any], json.load(f))


def validate_cli_spec(spec_data: dict[str, Any] | None = None) -> None:
    """Validate cli-spec.json against cli-spec.schema.json using Draft202012Validator."""
    schema = load_cli_spec_schema()
    spec = spec_data if spec_data is not None else load_cli_spec()
    validator = jsonschema.Draft202012Validator(schema)
    validator.validate(spec)
