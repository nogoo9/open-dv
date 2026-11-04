"""Pre-rendered presentation string generation (rendered.toon and rendered.text) for D6/D7."""

from __future__ import annotations

from typing import Any


def render_verify_text(result: dict[str, Any]) -> str:
    """Render human-readable verification summary and gate scorecard table."""
    status = result.get("status", "UNKNOWN")
    verdict = result.get("verdict", "UNKNOWN")
    run_id = result.get("run_id", "")
    scorecard = result.get("gate_scorecard", {})

    lines: list[str] = [
        "================================================================================",
        f"Open-DV Verification Report — Run: {run_id}",
        f"Status: {status} | Verdict: {verdict}",
        "--------------------------------------------------------------------------------",
        "Gate Scorecard (6-Gate AXI Verification):",
    ]
    if isinstance(scorecard, dict):
        for gate, outcome in scorecard.items():
            lines.append(f"  • {gate:<35} : {outcome}")
    applied = result.get("applied_codemods", [])
    if applied:
        lines.append("--------------------------------------------------------------------------------")
        lines.append(f"Applied Codemods ({len(applied)}):")
        for cm in applied:
            lines.append(f"  • {cm}")
    diagnostics = result.get("diagnostics", [])
    if diagnostics:
        lines.append("--------------------------------------------------------------------------------")
        lines.append(f"Diagnostics ({len(diagnostics)}):")
        for diag in diagnostics:
            lines.append(f"  • {diag}")
    lines.append("================================================================================")
    return "\n".join(lines)


def render_compile_text(result: dict[str, Any]) -> str:
    """Render human-readable compilation summary."""
    status = result.get("status", "PASS")
    hubs = result.get("hubs_compiled", 0)
    links = result.get("links_compiled", 0)
    sats = result.get("satellites_compiled", 0)
    order = result.get("topological_load_order", [])

    lines = [
        "================================================================================",
        "Open-DV 4-Zone SQL Compilation Report",
        f"Status: {status}",
        f"Compiled Constructs: {hubs} Hubs, {links} Links, {sats} Satellites",
        "--------------------------------------------------------------------------------",
        f"Topological Load Order ({len(order)} tables):",
    ]
    for tbl in order:
        lines.append(f"  1. {tbl}")
    lines.append("================================================================================")
    return "\n".join(lines)


def render_profile_text(result: dict[str, Any]) -> str:
    """Render human-readable profiling summary."""
    profile_id = result.get("profile_id", "")
    tables = result.get("table_count", 0)
    columns = result.get("column_count", 0)
    source_sys = result.get("source_system", "")
    table_name = result.get("table_name", "")

    lines = [
        "================================================================================",
        "Open-DV Statistical Graph B Profile",
        f"Profile ID: {profile_id}",
        f"Tables Profiled: {tables} | Columns Profiled: {columns}",
    ]
    if source_sys:
        lines.append(f"Source System: {source_sys} | Primary Table: {table_name}")
    lines.append("================================================================================")
    return "\n".join(lines)


def render_sandbox_text(result: dict[str, Any]) -> str:
    """Render human-readable sandbox execution summary."""
    q_type = result.get("query_type", "")
    passed = result.get("all_batches_passed", False)
    rows = result.get("rows", [])

    lines = [
        "================================================================================",
        f"Open-DV DuckDB Sandbox Query: {q_type}",
        f"Result: {'PASS' if passed else 'FAIL'} | Rows Returned: {len(rows)}",
        "--------------------------------------------------------------------------------",
    ]
    if rows and isinstance(rows[0], dict):
        cols = list(rows[0].keys())[:6]
        header = " | ".join(f"{c:<16}" for c in cols)
        lines.append(header)
        lines.append("-" * len(header))
        for r in rows[:10]:
            row_str = " | ".join(f"{str(r.get(c, ''))[:16]:<16}" for c in cols)
            lines.append(row_str)
        if len(rows) > 10:
            lines.append(f"... ({len(rows) - 10} more rows)")
    lines.append("================================================================================")
    return "\n".join(lines)


def render_pipeline_text(result: dict[str, Any]) -> str:
    """Render human-readable pipeline run summary."""
    run_id = result.get("run_id", "")
    status = result.get("status", "UNKNOWN")
    passed = result.get("passed", False)
    steps = result.get("steps", [])

    lines = [
        "================================================================================",
        f"Open-DV Pipeline Execution: {run_id}",
        f"Status: {status} | Passed: {passed}",
        "--------------------------------------------------------------------------------",
        f"Stages Completed ({len(steps)}):",
    ]
    for step in steps:
        if isinstance(step, dict):
            s_name = step.get("stage", step.get("stage_id", ""))
            s_passed = step.get("passed", True)
            lines.append(f"  • {s_name:<30} : {'PASS' if s_passed else 'FAIL'}")
    lines.append("================================================================================")
    return "\n".join(lines)


def render_hold_text(result: dict[str, Any]) -> str:
    """Render human-readable hold decision summary."""
    run_id = result.get("run_id", "")
    hold_id = result.get("hold_id", "")
    decision = result.get("decision", "")
    status = result.get("status", "")

    return (
        f"Captain Hold Decision Resolved: Run={run_id} Hold={hold_id} "
        f"Decision={decision} Status={status}"
    )
