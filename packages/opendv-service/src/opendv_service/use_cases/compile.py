"""Compile pipeline use case: Lower contract to Canonical IR and 4-Zone SQL."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

from opendv_contracts import CompileRequest, CompileResponse, RenderedOutput
from opendv_contracts.errors import ErrorCode
from opendv_core import validate_canonical_ir, validate_contract_v5
from opendv_core.paths import resolve_workspace_path
from opendv_duckdb_sandbox.compiler_4zone import compile_canonical_ir_to_4zone_sql
from opendv_duckdb_sandbox.ir_lowerer import lower_contract_to_canonical_ir
from opendv_service.errors import InputValidationError
from opendv_service.rendering import render_compile_text


def compile_pipeline(
    request: CompileRequest,
    *,
    workspace_root: Path | None = None,
) -> CompileResponse:
    """Compile a Data Vault 2.0 contract into Canonical IR and 4-Zone SQL."""
    root = Path(workspace_root or os.environ.get("OPENDV_WORKSPACE_ROOT") or Path.cwd()).resolve()

    # 1. Resolve contract document
    resolved_contract_path: Path | None = None
    if request.contract_content is not None and request.contract_content.strip():
        try:
            contract_doc = yaml.safe_load(request.contract_content)
        except Exception as exc:
            raise InputValidationError(
                f"Failed to parse contract YAML: {exc}",
                error_code=ErrorCode.SCHEMA_VALIDATION_ERROR,
            ) from exc
    elif request.contract_path is not None and request.contract_path.strip():
        resolved_contract_path = resolve_workspace_path(
            request.contract_path, workspace_root=root, allow_temp=True
        )
        if not resolved_contract_path.exists():
            raise InputValidationError(f"Contract file not found: {resolved_contract_path}")
        try:
            contract_doc = yaml.safe_load(resolved_contract_path.read_text(encoding="utf-8"))
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

    try:
        validate_contract_v5(contract_doc)
    except Exception as exc:
        raise InputValidationError(
            f"Invalid contract schema: {exc}",
            error_code=ErrorCode.SCHEMA_VALIDATION_ERROR,
        ) from exc

    # 2. Resolve or lower to Canonical IR
    resolved_ir_path: Path | None = None
    if request.canonical_ir_content is not None and request.canonical_ir_content.strip():
        ir_raw = request.canonical_ir_content
        ir_doc: dict[str, Any] = json.loads(ir_raw)
        ir_obj = validate_canonical_ir(ir_doc)
    elif request.canonical_ir_path is not None and request.canonical_ir_path.strip():
        resolved_ir_path = resolve_workspace_path(
            request.canonical_ir_path, workspace_root=root, allow_temp=True
        )
        if not resolved_ir_path.exists():
            raise InputValidationError(f"Canonical IR file not found: {resolved_ir_path}")
        ir_raw = resolved_ir_path.read_text(encoding="utf-8")
        ir_doc = json.loads(ir_raw)
        ir_obj = validate_canonical_ir(ir_doc)
    else:
        ir_obj = lower_contract_to_canonical_ir(contract_doc)
        ir_doc = ir_obj.model_dump(mode="json", exclude_none=True)
        ir_raw = json.dumps(ir_doc, sort_keys=True)

    # 3. Compile to 4-Zone SQL
    compiled_bundle = compile_canonical_ir_to_4zone_sql(
        ir_obj,
        include_batch4_restatement_in_seed=request.include_batch4_restatement,
    )
    ir_sha256 = hashlib.sha256(ir_raw.encode("utf-8")).hexdigest().upper()
    sql_text = (
        "\n\n".join(compiled_bundle.zone_sql.values())
        if hasattr(compiled_bundle, "zone_sql")
        else ""
    )
    sql_sha256 = hashlib.sha256(sql_text.encode("utf-8")).hexdigest().upper() if sql_text else None

    hubs = ir_doc.get("hubs", [])
    links = ir_doc.get("links", [])
    satellites = ir_doc.get("satellites", [])

    res_dict: dict[str, Any] = {
        "run_id": request.run_id or "run-local-compile",
        "status": "PASS",
        "contract_path": (
            str(resolved_contract_path) if resolved_contract_path else request.contract_path
        ),
        "canonical_ir_path": (
            str(resolved_ir_path) if resolved_ir_path else request.canonical_ir_path
        ),
        "canonical_ir_sha256": ir_sha256,
        "compiled_sql_sha256": sql_sha256,
        "hubs_compiled": len(hubs),
        "links_compiled": len(links),
        "satellites_compiled": len(satellites),
        "topological_load_order": list(compiled_bundle.topological_load_order),
        "sql_zones": [
            "Zone 0: Bronze & Vault Schemas",
            "Zone 1: Hashed Staging Views (Algorithm A.5)",
            "Zone 2: Raw Vault Hubs, Links, Satellites, and SAL",
            "Zone 3: Business Vault PIT & Ghost Records",
            "Zone 4: Bi-Temporal Queries D.10 & D.10b",
        ],
        "rendered_sql": getattr(compiled_bundle, "zone_sql", None),
    }
    rendered_text = render_compile_text(res_dict)
    res_dict["rendered"] = RenderedOutput(text=rendered_text)
    return CompileResponse(**res_dict)


__all__ = ["compile_pipeline"]
