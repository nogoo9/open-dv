"""Generator for the canonical language-neutral CLI specification (cli-spec.json).

Combines declarative command definitions with normative error mappings into a structured
dictionary conforming to cli-spec.schema.json.
"""

from __future__ import annotations

from typing import Any

from opendv_contracts.errors import ERROR_MAPPINGS
from opendv_service.cli_spec.definitions import (
    ALL_COMMANDS,
    CORE_COMMANDS,
    GLOBAL_OPTIONS,
)


def generate_cli_spec(
    *,
    include_seams: bool = False,
    spec_version: str = "1.0.0",
    contract_version: str = "1.0.0",
) -> dict[str, Any]:
    """Generate the complete, canonical CLI specification dictionary."""
    command_defs = ALL_COMMANDS if include_seams else CORE_COMMANDS

    error_map = [
        {
            "error_code": (
                entry.error_code.value
                if hasattr(entry.error_code, "value")
                else str(entry.error_code)
            ),
            "exit_code": entry.exit_code,
            "http_status": entry.http_status,
            "problem_title": entry.problem_title,
            "problem_type_uri": entry.problem_type_uri,
        }
        for entry in ERROR_MAPPINGS.values()
    ]

    spec: dict[str, Any] = {
        "commands": [cmd.to_dict() for cmd in command_defs],
        "contract_version": contract_version,
        "error_map": error_map,
        "global_options": [opt.to_dict() for opt in GLOBAL_OPTIONS],
        "spec_version": spec_version,
    }
    return spec
