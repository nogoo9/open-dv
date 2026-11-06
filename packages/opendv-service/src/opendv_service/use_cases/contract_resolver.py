"""Contract resolution and document loading helper for pipeline use cases."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from opendv_contracts.errors import ErrorCode
from opendv_core.paths import resolve_workspace_path
from opendv_service.errors import InputValidationError, NotFoundError


def resolve_and_load_contract_doc(
    *,
    contract_path: str | None = None,
    contract_content: str | None = None,
    workspace_root: Path | None = None,
    allow_temp: bool = True,
    not_found_as_not_found_error: bool = False,
) -> tuple[dict[str, Any], Path | None]:
    """Resolve and load a contract YAML document from path or inline content.

    Returns:
        tuple[dict[str, Any], Path | None]: The parsed contract document and resolved file path
            (if any).

    Raises:
        InputValidationError: If parsing fails, document is not a mapping, or input is missing.
        NotFoundError: If contract_path does not exist and not_found_as_not_found_error is True.
    """
    root = Path(workspace_root or os.environ.get("OPENDV_WORKSPACE_ROOT") or Path.cwd()).resolve()

    resolved_contract_path: Path | None = None
    if contract_content is not None and contract_content.strip():
        try:
            contract_doc = yaml.safe_load(contract_content)
        except Exception as exc:
            raise InputValidationError(
                f"Failed to parse contract YAML: {exc}",
                error_code=ErrorCode.SCHEMA_VALIDATION_ERROR,
            ) from exc
    elif contract_path is not None and contract_path.strip():
        resolved_contract_path = resolve_workspace_path(
            contract_path, workspace_root=root, allow_temp=allow_temp
        )
        if not resolved_contract_path.exists() or not resolved_contract_path.is_file():
            if not_found_as_not_found_error:
                raise NotFoundError(f"Contract file not found: {resolved_contract_path}")
            raise InputValidationError(f"Contract file not found: {resolved_contract_path}")
        try:
            raw_text = resolved_contract_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise InputValidationError(
                f"Failed to read contract file: {exc}",
                error_code=ErrorCode.SCHEMA_VALIDATION_ERROR,
            ) from exc
        try:
            contract_doc = yaml.safe_load(raw_text)
        except Exception as exc:
            raise InputValidationError(
                f"Failed to parse contract YAML: {exc}",
                error_code=ErrorCode.SCHEMA_VALIDATION_ERROR,
            ) from exc
    else:
        raise InputValidationError("Either contract_path or contract_content must be provided.")

    if not isinstance(contract_doc, dict):
        raise InputValidationError(
            "Contract document must be a YAML mapping object.",
            error_code=ErrorCode.SCHEMA_VALIDATION_ERROR,
        )

    return contract_doc, resolved_contract_path
