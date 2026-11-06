"""CLI specification generator and exporter module."""

from opendv_service.cli_spec.definitions import (
    ALL_COMMANDS,
    CORE_COMMANDS,
    GLOBAL_OPTIONS,
    CommandDef,
    OptionDef,
)
from opendv_service.cli_spec.docs_generator import generate_cli_reference_markdown
from opendv_service.cli_spec.exporter import export_cli_spec, validate_cli_spec_dict
from opendv_service.cli_spec.generator import generate_cli_spec

__all__ = [
    "ALL_COMMANDS",
    "CORE_COMMANDS",
    "GLOBAL_OPTIONS",
    "CommandDef",
    "OptionDef",
    "export_cli_spec",
    "generate_cli_reference_markdown",
    "generate_cli_spec",
    "validate_cli_spec_dict",
]
