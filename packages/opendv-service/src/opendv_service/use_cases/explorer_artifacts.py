"""Explorer artifact builders and visualizer payloads backed by real run artifacts.

Conforms to User Directive R9 (file size <500 LOC / <20 KB) and Decision D3.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from opendv_contracts.dtos.explorer import (
    ArtifactDTO,
    DvVizDTO,
)
from opendv_service.use_cases.dv_schema_helpers import resolve_run_artifact_sources
from opendv_service.use_cases.explorer_artifact_inputs import (
    _art,
    build_input_artifacts,
)
from opendv_service.use_cases.explorer_canonical import (
    build_canonical_doc_viz,
)


def _read_candidate_file(candidate_dirs: list[Path], filename: str) -> str:
    for c_dir in candidate_dirs:
        cand = c_dir / filename
        if cand.is_file():
            return cand.read_text(encoding="utf-8")
    return ""


def _build_ir_outline(ir_dict: dict[str, Any]) -> list[tuple[str, str]]:
    hubs = [str(h.get("name", "")) for h in (ir_dict.get("hubs") or []) if isinstance(h, dict)]
    links = [str(k.get("name", "")) for k in (ir_dict.get("links") or []) if isinstance(k, dict)]
    sats = [
        str(s.get("name", "")) for s in (ir_dict.get("satellites") or []) if isinstance(s, dict)
    ]
    outline: list[tuple[str, str]] = []
    if hubs:
        outline.append(("Hubs", ", ".join(hubs)))
    if links:
        outline.append(("Links", ", ".join(links)))
    if sats:
        outline.append(("Satellites", ", ".join(sats)))
    return outline


def build_bundle_artifacts(
    run_id: str,
    project_id: str,
    is_hold: bool,
    events_summary: list[tuple[str, str, str]],
    *,
    state: Any = None,
    report_dict: dict[str, Any] | None = None,
) -> dict[str, ArtifactDTO]:
    """Construct canonical Explorer artifacts from persisted run artifacts (no dummy stubs)."""
    doc_viz = build_canonical_doc_viz(is_hold=is_hold)
    all_w = ["W0", "W1", "W2", "W3"]

    sources: dict[str, Any] = {}
    if state is not None and run_id in getattr(state, "runs", {}):
        sources = resolve_run_artifact_sources(state, run_id)

    rep: dict[str, Any] = (
        dict(sources.get("report_dict") or {})
        if sources
        else (dict(report_dict) if isinstance(report_dict, dict) else {})
    )
    ir_dict: dict[str, Any] = dict(sources.get("ir_dict") or {})
    contract_dict: dict[str, Any] = dict(sources.get("contract_dict") or {})
    scorecard: dict[str, Any] = dict(sources.get("scorecard") or {})
    candidate_dirs: list[Path] = list(sources.get("candidate_dirs") or [])

    draft_raw = str(sources.get("draft_raw") or "")
    final_contract_raw = str(sources.get("contract_raw") or "")
    final_ir_raw = str(sources.get("ir_raw") or "")
    sql_raw = str(sources.get("sql_raw") or "")
    toon_raw = _read_candidate_file(candidate_dirs, "axi_diagnostic_trace.toon")
    ledger_raw = _read_candidate_file(candidate_dirs, "vault-ledger.jsonl")
    if not ledger_raw:
        ledger_raw = "\n".join(
            json.dumps({"stage": st, "message": msg, "status": status})
            for st, msg, status in events_summary
        )

    triage_entries = [
        e for e in (contract_dict.get("column_triage_ledger") or []) if isinstance(e, dict)
    ]
    triage_rows = [
        [
            str(e.get("bronze_table", "")),
            str(e.get("column_name", "")),
            str(e.get("elm_classification", "")),
            str(e.get("target_dv_entity", "")),
        ]
        for e in triage_entries
    ]
    triage_raw = json.dumps(triage_entries, indent=2) if triage_entries else ""

    hub_count = len(contract_dict.get("hubs") or ir_dict.get("hubs") or [])
    link_count = len(contract_dict.get("links") or ir_dict.get("links") or [])
    sat_count = len(contract_dict.get("satellites") or ir_dict.get("satellites") or [])
    contract_short = (
        f"{hub_count} Hubs, {link_count} Links, {sat_count} Sats"
        if (final_contract_raw or contract_dict)
        else "No contract compiled"
    )
    ir_short = (
        f"{len(ir_dict.get('hubs') or [])} Hubs · Canonical IR"
        if ir_dict
        else "No Canonical IR compiled"
    )
    sql_short = "Zones 0-3 DDL + DML" if sql_raw else "No SQL compiled"

    sandbox_summary = scorecard.get("sandbox_summary") or {}
    gate_checks = [
        (g, g, str(scorecard.get(g, "PENDING")))
        for g in ("G0", "G1", "G2", "G3", "G4")
        if g in scorecard
    ]
    report_raw = (
        json.dumps(rep, indent=2, default=str)
        if rep
        else json.dumps({"run_id": run_id, "project_id": project_id}, indent=2)
    )

    artifacts = build_input_artifacts(project_id=project_id, is_hold=is_hold)

    stage_outputs: dict[str, ArtifactDTO] = {
        "grounding": _art(
            "grounding",
            "concept_grounding_ledger.json",
            f"{hub_count} grounded concepts" if hub_count else "Ontology grounding",
            "Stage output",
            "ConceptGroundingLedger",
            "JSON",
            "table",
            "Bijection between physical source feeds and Graph A concepts.",
            json.dumps(rep.get("inference_plan") or {}, indent=2, default=str),
            all_w,
            0,
            cols=["entity", "concept_uri"],
            rows=[
                [str(h.get("name", "")), str(h.get("ontology_concept_uri", ""))]
                for h in (contract_dict.get("hubs") or [])
                if isinstance(h, dict)
            ],
        ),
        "profile": _art(
            "profile",
            "graph_b_profile.json",
            f"{len(triage_entries)} columns profiled",
            "Stage output",
            "RelationalProfileReport",
            "JSON",
            "table",
            "Information-theoretic column profiles and surrogate key quarantine.",
            json.dumps({"columns_triaged": len(triage_entries)}, indent=2),
            all_w,
            0,
            cols=["table", "column", "sql_type"],
            rows=[
                [
                    str(e.get("bronze_table", "")),
                    str(e.get("column_name", "")),
                    str(e.get("source_sql_type") or ""),
                ]
                for e in triage_entries
            ],
        ),
        "drift": _art(
            "drift",
            "schema_drift_report.json",
            f"{len(rep.get('drift_findings') or [])} drift changes classified",
            "Stage output",
            "SchemaDriftReport",
            "JSON",
            "table",
            "Algorithm A.9a intra-source schema evolution classification report.",
            json.dumps(rep.get("drift_findings") or [], indent=2, default=str),
            ["W2"],
            2,
            cols=["finding"],
            rows=[[json.dumps(d, default=str)] for d in (rep.get("drift_findings") or [])],
        ),
        "extract": _art(
            "extract",
            "unstructured_extractions.json",
            "Unstructured span extractions",
            "Stage output",
            "UnstructuredExtractionLedger",
            "JSON",
            "doc",
            "Algorithm A.9b verbatim span extractions and steward hold resolution.",
            json.dumps(doc_viz.model_dump(mode="json"), indent=2),
            ["W3"],
            3,
            doc=doc_viz,
        ),
        "triage": _art(
            "triage",
            "column_triage_ledger.json",
            f"{len(triage_entries)}/{len(triage_entries)} columns assigned",
            "Stage output",
            "ColumnTriageLedger",
            "JSON",
            "table",
            "Exhaustive column accounting ledger matching Gate 1 bijection.",
            triage_raw,
            all_w,
            0,
            cols=["bronze_table", "column_name", "elm_classification", "target_dv_entity"],
            rows=triage_rows,
        ),
        "draft_contract": _art(
            "draft_contract",
            "draft_contract.yaml",
            contract_short,
            "Stage output",
            "DataVaultModelContract",
            "YAML",
            "dv",
            "Initial declarative Data Vault 2.0 contract draft before symbolic gates.",
            draft_raw,
            all_w,
            0,
            dv=DvVizDTO(),
        ),
        "toon": _art(
            "toon",
            "axi_diagnostic_trace.toon",
            "Symbolic verifier trace",
            "Stage output",
            "AxiVerifierDiagnosticTrace",
            "TOON",
            "code",
            "Machine-readable symbolic verifier diagnostic trace for closed-loop repair.",
            toon_raw,
            all_w,
            0,
            outline=[("Gate 1", "Schema grain check"), ("Diagnostic", "@toon_v1 repair trace")],
        ),
        "gate_static": _art(
            "gate_static",
            "static_gates_scorecard.json",
            str(scorecard.get("overall_verdict", "PENDING")),
            "Stage output",
            "StaticGatesScorecard",
            "JSON",
            "checks",
            "Formal static verification scorecard across Gates 0 through 4.",
            json.dumps(scorecard, indent=2, default=str),
            all_w,
            0,
            checks=gate_checks,
        ),
        "final_contract": _art(
            "final_contract",
            "final_contract.yaml",
            contract_short,
            "Final output",
            "DataVaultModelContract",
            "YAML",
            "dv",
            "Verified, production-ready declarative Data Vault 2.0 contract.",
            final_contract_raw,
            all_w,
            0,
            dv=DvVizDTO(),
        ),
        "gate_sandbox": _art(
            "gate_sandbox",
            "sandbox_execution_scorecard.json",
            "4 zones compiled · PASS" if sandbox_summary.get("passed") else "Sandbox scorecard",
            "Stage output",
            "SandboxExecutionScorecard",
            "JSON",
            "checks",
            "Gate 5 DuckDB analytical sandbox execution verification scorecard.",
            json.dumps(sandbox_summary, indent=2, default=str),
            all_w,
            0,
            checks=[
                (
                    "G5",
                    "DuckDB Analytical Sandbox",
                    "PASS" if sandbox_summary.get("passed") else "PENDING",
                )
            ],
        ),
        "sandbox_report": _art(
            "sandbox_report",
            "sandbox_report.json",
            f"{sandbox_summary.get('passed_dynamic_checks', 0)} dynamic assertions PASS",
            "Stage output",
            "SandboxVerificationReport",
            "JSON",
            "checks",
            "DV-DYN-001..015 dynamic point-in-time and referential integrity report.",
            json.dumps(sandbox_summary, indent=2, default=str),
            all_w,
            0,
            checks=[],
        ),
        "final_ir": _art(
            "final_ir",
            "final_canonical_ir.json",
            ir_short,
            "Final output",
            "CanonicalDataVaultIR",
            "JSON",
            "code",
            "Deterministic Canonical Intermediate Representation.",
            final_ir_raw,
            all_w,
            0,
            outline=_build_ir_outline(ir_dict),
        ),
        "final_sql": _art(
            "final_sql",
            "final_four_zone.sql",
            sql_short,
            "Final output",
            "FourZoneDuckDBSQL",
            "SQL",
            "code",
            "Complete 4-zone DuckDB Data Vault 2.0 executable SQL pipeline.",
            sql_raw,
            all_w,
            0,
            outline=[
                ("Zone 0", "Raw Bronze view staging"),
                ("Zone 1", "Staging Views with hash keys"),
                ("Zone 2", "Raw Data Vault tables"),
                ("Zone 3", "Business Vault PIT tables"),
            ],
        ),
        "report": _art(
            "report",
            "pipeline_report.json",
            f"Run {run_id} execution report",
            "Final output",
            "AgenticPipelineReport",
            "JSON",
            "checks",
            "Complete end-to-end execution audit report.",
            report_raw,
            all_w,
            0,
            checks=[],
        ),
        "ledger": _art(
            "ledger",
            "vault-ledger.jsonl",
            "Observability event log",
            "Final output",
            "VaultObservabilityLedger",
            "JSONL",
            "events",
            "Canonical JSONL stream of all observability events emitted during run.",
            ledger_raw,
            all_w,
            0,
            events=events_summary,
        ),
    }

    artifacts.update(stage_outputs)
    return artifacts
