"""Sandbox use case: Execute analytical queries and multi-batch validation in DuckDB sandbox."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any

from opendv_contracts import (
    RenderedOutput,
    SandboxBatchResult,
    SandboxQueryRequest,
    SandboxQueryResponse,
)
from opendv_contracts.errors import ErrorCode
from opendv_core.paths import resolve_workspace_path
from opendv_duckdb_sandbox.sandbox_engine import DuckDbShadowSandbox, SafeSqlResult
from opendv_service.errors import InputValidationError
from opendv_service.rendering import render_sandbox_text


def execute_sandbox_query(
    request: SandboxQueryRequest,
    *,
    workspace_root: Path | None = None,
) -> SandboxQueryResponse:
    """Execute parameterized queries or 4-batch sequences in the DuckDB sandbox."""
    root = Path(workspace_root or os.environ.get("OPENDV_WORKSPACE_ROOT") or Path.cwd()).resolve()
    effective_run_id = request.run_id or "run-local-sandbox"
    query_type_str = (
        request.query_type.value
        if hasattr(request.query_type, "value")
        else str(request.query_type)
    )
    cutoff = request.assertion_cutoff_ts or "2026-11-15 23:59:59"

    # Branch 1: Dynamic contract / Canonical IR supplied
    has_ir = bool(
        (request.canonical_ir_path and request.canonical_ir_path.strip())
        or (request.canonical_ir_content and request.canonical_ir_content.strip())
    )
    has_contract = bool(
        (request.contract_path and request.contract_path.strip())
        or (request.contract_content and request.contract_content.strip())
    )

    if not (has_ir or has_contract):
        if query_type_str == "CUSTOM_SQL" and request.sql and request.sql.strip():
            with DuckDbShadowSandbox() as sandbox:
                custom_res = sandbox.execute_safe_sql(
                    request.sql,
                    max_rows=request.max_rows,
                    timeout_seconds=request.timeout_seconds,
                )
                res_dict: dict[str, Any] = {
                    "run_id": effective_run_id,
                    "query_type": query_type_str,
                    "assertion_cutoff_ts": cutoff,
                    "all_batches_passed": True,
                    "batch_verdicts": {},
                    "batches": [],
                    "dynamic_rules_passed": 0,
                    "dynamic_rules_total": 0,
                    "replay_delta_rows": 0,
                    "row_count": custom_res.row_count,
                    "rows": custom_res.rows,
                    "query_d10_rows": [],
                    "query_d10b_rows": [],
                    "columns": custom_res.columns,
                    "execution_stats": custom_res.execution_stats,
                    "batch_report": {
                        "all_passed": True,
                        "batches": [],
                    },
                    "dynamic_check_report": {
                        "passed_count": 0,
                        "failed_count": 0,
                        "checks": [],
                    },
                }
                rendered_text = render_sandbox_text(res_dict)
                res_dict["rendered"] = RenderedOutput(text=rendered_text)
                return SandboxQueryResponse(**res_dict)

        raise InputValidationError(
            "Neither contract nor canonical IR was provided for sandbox execution. "
            "Specify contract_path, contract_content, canonical_ir_path, or canonical_ir_content.",
            error_code=ErrorCode.INPUT_VALIDATION_ERROR,
        )

    tmp_path: Path | None = None
    custom_result: SafeSqlResult | None = None
    try:
        if request.canonical_ir_content and request.canonical_ir_content.strip():
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_file:
                tmp_file.write(request.canonical_ir_content.encode("utf-8"))
                tmp_file.flush()
                tmp_path = Path(tmp_file.name)
            source_path = tmp_path
        elif request.canonical_ir_path and request.canonical_ir_path.strip():
            source_path = resolve_workspace_path(
                request.canonical_ir_path, workspace_root=root, allow_temp=True
            )
        elif request.contract_content and request.contract_content.strip():
            with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tmp_file:
                tmp_file.write(request.contract_content.encode("utf-8"))
                tmp_file.flush()
                tmp_path = Path(tmp_file.name)
            source_path = tmp_path
        else:
            assert request.contract_path is not None
            source_path = resolve_workspace_path(
                request.contract_path, workspace_root=root, allow_temp=True
            )

        if not source_path.exists():
            raise InputValidationError(f"Sandbox input file not found: {source_path}")

        with DuckDbShadowSandbox() as sandbox:
            ir_model = sandbox.resolve_canonical_ir(source_path)
            report = sandbox.run_full_verification(
                ir_model,
                emit_telemetry=False,
                run_id=effective_run_id,
            )
            if query_type_str == "CUSTOM_SQL" and request.sql and request.sql.strip():
                custom_result = sandbox.execute_safe_sql(
                    request.sql,
                    max_rows=request.max_rows,
                    timeout_seconds=request.timeout_seconds,
                )
    finally:
        if tmp_path is not None and tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)

    d10_rows = [
        {
            **r.model_dump(mode="json"),
            "lp_natural_key": r.golden_lp_id,
            "commitment_amount_usd": str(r.committed_amount),
            "side_letter_discounted_fee_bps": r.side_letter_fee_discount_bps,
        }
        for r in report.query_d10_rows
    ]
    d10b_source = (
        report.query_d10b_oct_15_rows
        if cutoff.strip().startswith("2026-10")
        else report.query_d10b_nov_15_rows
    )
    d10b_rows = [
        {
            **r.model_dump(mode="json"),
            "portco_natural_key": r.lei_code,
        }
        for r in d10b_source
    ]

    if query_type_str in {"QUERY_D10_PIT", "QUERY_D10_LP_COMMITMENT_PIT"}:
        rows = d10_rows
    elif query_type_str in {"QUERY_D10B_VALUATION", "QUERY_D10B_PORTCO_VALUATION_PIT"}:
        rows = d10b_rows
    elif query_type_str == "CUSTOM_SQL":
        rows = custom_result.rows if custom_result else []
    elif query_type_str == "VERIFY_DYN_001_015":
        checks = report.dynamic_check_report.model_dump(mode="json")["checks"]
        rows = [
            {
                **chk,
                "rule_id": chk.get("check_id"),
                "status": "PASS" if chk.get("passed") else "FAIL",
            }
            for chk in checks
        ]
    else:
        rows = list(report.batch_report.model_dump(mode="json")["batches"])

    batch_verdicts = {
        f"batch_{b.batch_number}": ("PASS" if b.passed else "FAIL")
        for b in report.batch_report.batches
    }
    batches = [
        SandboxBatchResult(
            batch_number=b.batch_number,
            batch_name=getattr(b, "batch_name", f"Batch {b.batch_number}"),
            passed=b.passed,
            row_count=getattr(b, "total_rows", b.total_delta_rows),
            delta_rows=b.total_delta_rows,
        )
        for b in report.batch_report.batches
    ]
    replay_delta = (
        report.batch_report.by_number[2].total_delta_rows
        if 2 in report.batch_report.by_number
        else 0
    )

    res_dict: dict[str, Any] = {
        "run_id": effective_run_id,
        "query_type": query_type_str,
        "assertion_cutoff_ts": cutoff,
        "all_batches_passed": report.passed,
        "batch_verdicts": batch_verdicts,
        "batches": batches,
        "dynamic_rules_passed": report.dynamic_check_report.passed_count,
        "dynamic_rules_total": (
            report.dynamic_check_report.passed_count + report.dynamic_check_report.failed_count
        ),
        "replay_delta_rows": replay_delta,
        "row_count": len(rows),
        "rows": rows,
        "query_d10_rows": d10_rows,
        "query_d10b_rows": d10b_rows,
        "columns": custom_result.columns if custom_result else [],
        "execution_stats": custom_result.execution_stats if custom_result else {},
        "batch_report": {
            "all_passed": report.passed,
            "batches": [b.model_dump(mode="json") for b in batches],
        },
        "dynamic_check_report": {
            "passed_count": report.dynamic_check_report.passed_count,
            "failed_count": report.dynamic_check_report.failed_count,
            "checks": rows if query_type_str == "VERIFY_DYN_001_015" else [],
        },
    }
    rendered_text = render_sandbox_text(res_dict)
    res_dict["rendered"] = RenderedOutput(text=rendered_text)
    return SandboxQueryResponse(**res_dict)


__all__ = ["execute_sandbox_query"]
