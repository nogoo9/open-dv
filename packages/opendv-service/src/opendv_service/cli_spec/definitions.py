"""Declarative taxonomy and definitions of OpenDV CLI commands, subcommands, and options.

This module provides the single source of truth for the language-neutral CLI specification
(REQ-CLI-01, REQ-CLI-02, docs/18 §3C).
"""

from __future__ import annotations

from opendv_service.cli_spec.base_commands import (
    COMPILE_COMMAND,
    ONTOLOGY_COMMAND,
    PROFILE_COMMAND,
    SANDBOX_COMMAND,
    SOURCE_COMMAND,
    VERIFY_COMMAND,
)
from opendv_service.cli_spec.pipeline_commands import (
    HOLD_COMMAND,
    PIPELINE_COMMAND,
    RUN_COMMAND,
)
from opendv_service.cli_spec.platform_commands import (
    CONFIG_COMMAND,
    SERVE_COMMAND,
)
from opendv_service.cli_spec.schema import (
    GLOBAL_OPTIONS,
    Availability,
    CommandDef,
    OptionDef,
    OptionType,
)
from opendv_service.cli_spec.studio_commands import STUDIO_COMMAND

CORE_COMMANDS: list[CommandDef] = [
    PROFILE_COMMAND,
    COMPILE_COMMAND,
    VERIFY_COMMAND,
    SANDBOX_COMMAND,
    PIPELINE_COMMAND,
    RUN_COMMAND,
    HOLD_COMMAND,
    ONTOLOGY_COMMAND,
    CONFIG_COMMAND,
    SERVE_COMMAND,
    SOURCE_COMMAND,
]

ALL_COMMANDS: list[CommandDef] = [
    *CORE_COMMANDS,
]

__all__ = [
    "ALL_COMMANDS",
    "COMPILE_COMMAND",
    "CONFIG_COMMAND",
    "CORE_COMMANDS",
    "GLOBAL_OPTIONS",
    "HOLD_COMMAND",
    "ONTOLOGY_COMMAND",
    "PIPELINE_COMMAND",
    "PROFILE_COMMAND",
    "RUN_COMMAND",
    "SANDBOX_COMMAND",
    "SERVE_COMMAND",
    "SOURCE_COMMAND",
    "STUDIO_COMMAND",
    "VERIFY_COMMAND",
    "Availability",
    "CommandDef",
    "OptionDef",
    "OptionType",
]
