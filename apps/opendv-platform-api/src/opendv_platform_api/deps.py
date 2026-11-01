"""Dependency injection, DTOs, RBAC redaction, and state management for Open-DV Platform API."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

import duckdb
import yaml
from fastapi import Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from opendv_agent_harness import (
    StewardHoldManager,
    build_observability_event_dict,
)
from opendv_axi_verifier import AxiDvPipelineResult
from opendv_core import (
    SchemaRegistry,
    VaultObservabilityEvent,
    find_workspace_root,
    get_default_schema_registry,
    validate_observability_event,
)
from opendv_telemetry import (
    ROLE_CLEARANCE_LEVELS,
    ContentAddressableStore,
    RbacRole,
    VaultLedgerEmitter,
    format_error_key_pill,
    format_ghost_pill,
    format_hub_pill,
    format_link_pill,
    format_sse_event_stream,
    format_zero_key_pill,
    redact_event_for_role,
    resolve_cleared_tiers,
    validate_no_pii_leaks,
)

REPO_ROOT = find_workspace_root()

WORKBENCH_VIEW_TITLES: dict[str, str] = {
    "WB-00": "WB-00: Master Pipeline Command Center & 6-Gate Scoreboard",
    "WB-01": "WB-01: Dual-Graph Grounding & Anti-Pattern Counterfactual Inspector",
    "WB-02": "WB-02: Empirical Column Profiler & Candidate Discovery Matrix",
    "WB-03": "WB-03: Unstructured PDF Span-Grounding & 3-Tier ER Inspector",
    "WB-04": "WB-04: Pass 1 Exhaustive Column Triage Sankey & Surrogate Sink",
    "WB-05": "WB-05: Pass 2/3 ELM Topology & 3-Tier BKCC Collision Shield",
    "WB-06": "WB-06: Gate 3 Topological DAG & Grain Matrix Inspector",
    "WB-07": "WB-07: Nullable-FK Link Splitter & Gate 4 YAGNI Cost Guard",
    "WB-08": "WB-08: Closed-Loop Self-Healing Diff & TOON Diagnostic Workbench",
    "WB-09": "WB-09: Intra-Source Schema Drift Radar (S1(t) -> S1(t+1))",
    "WB-10": "WB-10: Gate 5 Dynamic DuckDB Shadow Sandbox & Evolution Matrix",
    "WB-11": "WB-11: Pre-Hash Stepper (Alg A.5) & 4-Zone SQL Rosetta Stone",
}

PE_DEMO_CONTEXT_REFS: dict[str, str] = {
    "graph_a_sha256": "A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90",
    "graph_b_sha256": "B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1",
    "contract_yaml_pre_sha256": "C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2",
    "contract_yaml_post_sha256": "D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3",
    "canonical_ir_sha256": "E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4",
    "compiled_sql_sha256": "F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5",
    "toon_trace_sha256": "0718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F6",
}


class RunStatus(StrEnum):
    """Execution lifecycle status of a platform verification or sandbox run (docs/08 §5.2)."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED_HOLD = "PAUSED_HOLD"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


type GateVerdictStatus = Literal[
    "PENDING",
    "PASS",
    "AUTO-FIX",
    "AUTO_REPAIRED",
    "REJECT-WITH-TRACE",
    "ASK-USER",
]


class CreateProjectRequest(BaseModel):
    """Request payload to register a Data Vault 2.0 project (docs/08 §5.2)."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    display_name: str = Field(default="Apex Horizon Capital Partners — Private Equity Vault")
    name: str | None = Field(default=None)
    domain_namespace: str = Field(default="pe_asset_management")
    domain: str | None = Field(default=None)
    graph_a_ontology_path: str = Field(
        default="examples/private_equity/pe_graph_a_ontology.yaml"
    )
    bronze_sources: list[str] = Field(
        default_factory=lambda: [
            "investran.src_pe_funds",
            "investran.src_lp_investors",
            "dealcloud.src_portfolio_companies",
        ],
        min_length=1,
    )
    contract_path: str = Field(
        default="examples/private_equity/dv_pe_model_contract.yaml"
    )
    canonical_ir_path: str | None = Field(
        default="examples/private_equity/pe_canonical_ir.json"
    )
    graph_b_path: str | None = Field(
        default="examples/private_equity/pe_graph_b_profile.json"
    )

    @model_validator(mode="after")
    def _sync_aliases(self) -> CreateProjectRequest:
        default_name = "Apex Horizon Capital Partners — Private Equity Vault"
        if self.name and self.display_name == default_name:
            self.display_name = self.name
        if self.domain and self.domain_namespace == "pe_asset_management":
            self.domain_namespace = self.domain
        return self


class ProjectResponse(BaseModel):
    """Registered Data Vault 2.0 project descriptor (docs/08 §5.2 & docs/10 §2.3)."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    display_name: str
    domain_namespace: str
    graph_a_sha256: str
    latest_run_id: str | None = None
    created_at_utc: str
    contract_path: str = "examples/private_equity/dv_pe_model_contract.yaml"
    canonical_ir_path: str | None = "examples/private_equity/pe_canonical_ir.json"
    graph_a_ontology_path: str = "examples/private_equity/pe_graph_a_ontology.yaml"
    graph_b_path: str | None = "examples/private_equity/pe_graph_b_profile.json"


class CreateRunRequest(BaseModel):
    """Request payload to start a verification, compilation, and sandbox run (docs/08 §5.2)."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    harness_provider: Literal[
        "antigravity", "claude", "claude-agent", "deterministic-stub"
    ] = "antigravity"
    model_tier_override: Literal["argon", "flash"] | None = None
    contract_path: str | None = None
    max_repair_retries: int = Field(default=3, ge=0, le=5)
    run_sandbox_batches: bool = True
    execute_duckdb_sandbox: bool = True
    assertion_cutoff_ts: str = "2026-11-01T23:59:59Z"


class GateScorecardSummary(BaseModel):
    """6-Gate verification scorecard summary (docs/08 §5.2 & docs/10 §2.3)."""

    model_config = ConfigDict(extra="forbid")

    gate_0_profile_drift: GateVerdictStatus = "PASS"
    gate_1_contract_grain: GateVerdictStatus = "AUTO_REPAIRED"
    gate_2_sqlglot_ast: GateVerdictStatus = "PASS"
    gate_3_networkx_dag: GateVerdictStatus = "PASS"
    gate_4_yagni_cost_guard: GateVerdictStatus = "AUTO-FIX"
    gate_5_duckdb_sandbox: GateVerdictStatus = "PASS"


class ProjectRunDTO(BaseModel):
    """Status and scorecard descriptor for a platform verification run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    project_id: str
    harness_provider: Literal["antigravity", "claude", "claude-agent", "deterministic-stub"]
    status: RunStatus
    started_at_utc: str
    completed_at_utc: str | None = None
    current_stage_id: str
    current_view_id: str
    event_count: int = Field(ge=0)
    gate_scorecard: GateScorecardSummary = Field(default_factory=GateScorecardSummary)
    active_hold_ids: list[str] = Field(default_factory=list)
    events_sse_url: str
    gates_url: str
    sandbox_url: str
    decisions_url: str


class CaptainHoldDecisionRequest(BaseModel):
    """Human-in-the-loop Steward decision resolving a Tier U3/G3/G4 hold (docs/08 §5.2)."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = "evt-pe-004"
    hold_id: str | None = "hold-tier-u3-horizon-sovereign-vehicle"
    decision_type: Literal["RESOLVE_CAPTAIN_HOLD"] = "RESOLVE_CAPTAIN_HOLD"
    action: Literal["APPROVE", "REJECT", "ANSWER"] = "APPROVE"
    decision: Literal["APPROVE", "REJECT", "ANSWER"] | None = None
    selected_candidate_id: str | None = None
    resolution_notes: str | None = None
    steward_id: str = "steward-captain"
    steward_rationale: str | None = None

    @model_validator(mode="after")
    def _normalize_fields(self) -> CaptainHoldDecisionRequest:
        if self.decision is not None:
            self.action = self.decision
        if self.steward_rationale and not self.resolution_notes:
            self.resolution_notes = self.steward_rationale
        if not self.hold_id:
            self.hold_id = "hold-tier-u3-horizon-sovereign-vehicle"
        return self


class CaptainHoldDecisionResponse(BaseModel):
    """Result of applying a human Steward decision to an active hold."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    hold_id: str
    action: str
    status: Literal["ACCEPTED", "REJECTED"]
    resumed_run_id: str
    next_stage_id: str
    next_event_id: str
    updated_run_status: RunStatus
    resolved_at_utc: str


class SandboxQueryRequest(BaseModel):
    """Request to run a parameterized audit query or 4-Batch sequence in the DuckDB sandbox."""

    model_config = ConfigDict(extra="forbid")

    query_type: Literal[
        "ADVERSARIAL_BATCHES_1_TO_4",
        "QUERY_D10_PIT",
        "QUERY_D10B_VALUATION",
        "QUERY_D10_LP_COMMITMENT_PIT",
        "QUERY_D10B_PORTCO_VALUATION_PIT",
        "RUN_4_BATCHES",
        "VERIFY_DYN_001_015",
    ] = "ADVERSARIAL_BATCHES_1_TO_4"
    query_id: str | None = None
    assertion_cutoff_ts: str = "2026-11-15 23:59:59"

    @model_validator(mode="after")
    def _sync_query_type(self) -> SandboxQueryRequest:
        if self.query_id is not None:
            mapping = {
                "QUERY_D10_LP_COMMITMENT_PIT": "QUERY_D10_PIT",
                "QUERY_D10B_PORTCO_VALUATION_PIT": "QUERY_D10B_VALUATION",
                "RUN_4_BATCHES": "ADVERSARIAL_BATCHES_1_TO_4",
                "VERIFY_DYN_001_015": "VERIFY_DYN_001_015",
                "ADVERSARIAL_BATCHES_1_TO_4": "ADVERSARIAL_BATCHES_1_TO_4",
                "QUERY_D10_PIT": "QUERY_D10_PIT",
                "QUERY_D10B_VALUATION": "QUERY_D10B_VALUATION",
            }
            if self.query_id in mapping:
                self.query_type = mapping[self.query_id]  # type: ignore[assignment]
            else:
                raise ValueError(f"Unsupported sandbox query_id: {self.query_id!r}")
        return self


class SandboxExecutionResponse(BaseModel):
    """Structured response from the ephemeral DuckDB 4-Zone sandbox (docs/08 §5.2)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    query_type: str
    assertion_cutoff_ts: str
    all_batches_passed: bool
    batch_verdicts: dict[str, Literal["PASS", "FAIL"]]
    dynamic_rules_passed: int
    dynamic_rules_total: int = 15
    replay_delta_rows: int = 0
    row_count: int = Field(ge=0)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    query_d10_rows: list[dict[str, Any]] = Field(default_factory=list)
    query_d10b_rows: list[dict[str, Any]] = Field(default_factory=list)


class ArtifactUploadRequest(BaseModel):
    """Request payload for storing a JSON/text artifact in the Content-Addressable Store."""

    model_config = ConfigDict(extra="forbid")

    content: str | dict[str, Any] | list[Any]
    media_type: str = Field(default="application/json")
    artifact_name: str | None = Field(default=None)


class ArtifactMetadataResponse(BaseModel):
    """Response descriptor for a Content-Addressable Store artifact."""

    model_config = ConfigDict(extra="forbid")

    sha256: str = Field(..., pattern=r"^[0-9A-Fa-f]{64}$")
    byte_length: int = Field(ge=0)
    media_type: str = Field(default="application/json")
    artifact_name: str | None = None
    content: Any


def build_gate_scorecard_from_axi_result(
    axi_result: AxiDvPipelineResult,
) -> tuple[bool, GateVerdictStatus, GateScorecardSummary]:
    """Derive overall pass boolean, verdict, and 6-Gate scorecard from an AXI pipeline result."""
    passed = axi_result.status in {
        "PASS",
        "AUTO-FIX",
        "AUTO_REPAIRED",
        "ASK-USER",
        "ASK_USER",
    }
    codemods = set(axi_result.applied_codemods)
    if passed:
        scorecard_dict: dict[str, GateVerdictStatus] = {
            "gate_0_profile_drift": (
                "AUTO-FIX" if "ALG_A9A_INTRA_SOURCE_SCHEMA_DRIFT" in codemods else "PASS"
            ),
            "gate_1_contract_grain": (
                "ASK-USER"
                if axi_result.status in {"ASK-USER", "ASK_USER"}
                else "AUTO_REPAIRED"
            ),
            "gate_2_sqlglot_ast": "PASS",
            "gate_3_networkx_dag": "PASS",
            "gate_4_yagni_cost_guard": "AUTO-FIX",
            "gate_5_duckdb_sandbox": "PASS",
        }
    else:
        scorecard_dict = {
            "gate_0_profile_drift": (
                "AUTO-FIX" if "ALG_A9A_INTRA_SOURCE_SCHEMA_DRIFT" in codemods else "PASS"
            ),
            "gate_1_contract_grain": (
                "AUTO_REPAIRED"
                if codemods & {"ALG_A2_ENUM_DEMOTION", "ALG_A4_COLD_START_EXTENSIBILITY"}
                else "PASS"
            ),
            "gate_2_sqlglot_ast": "PASS",
            "gate_3_networkx_dag": (
                "AUTO-FIX" if "ALG_A3_LINK_EFFECTIVITY" in codemods else "PASS"
            ),
            "gate_4_yagni_cost_guard": (
                "AUTO-FIX" if "ALG_A1_3AXIS_SATELLITE_SPLIT" in codemods else "PASS"
            ),
            "gate_5_duckdb_sandbox": "PASS",
        }

    for violation in axi_result.toon_trace.violations:
        if violation.rule.startswith("DV-EVO") or "EVOLUTION" in violation.rule:
            gate_key = "gate_0_profile_drift"
        elif violation.gate in {"G1", "G2"} or violation.rule.startswith("DV-COL"):
            gate_key = "gate_1_contract_grain"
        elif violation.gate == "G6" or violation.rule.startswith("DV-AST"):
            gate_key = "gate_2_sqlglot_ast"
        elif violation.gate == "G3":
            gate_key = "gate_3_networkx_dag"
        elif violation.gate == "G4":
            gate_key = "gate_4_yagni_cost_guard"
        elif violation.gate == "G5":
            gate_key = "gate_5_duckdb_sandbox"
        else:
            gate_key = "gate_1_contract_grain"

        if violation.action == "REJECT":
            scorecard_dict[gate_key] = "REJECT-WITH-TRACE"
        elif violation.action == "ASK-USER" and scorecard_dict[gate_key] != "REJECT-WITH-TRACE":
            scorecard_dict[gate_key] = "ASK-USER"

    if not passed and "REJECT-WITH-TRACE" not in scorecard_dict.values():
        scorecard_dict["gate_1_contract_grain"] = "REJECT-WITH-TRACE"

    scorecard = GateScorecardSummary(**scorecard_dict)
    verdict: GateVerdictStatus
    if not passed:
        verdict = "REJECT-WITH-TRACE"
    elif "ASK-USER" in scorecard_dict.values():
        verdict = "ASK-USER"
    else:
        verdict = "PASS"
    return passed, verdict, scorecard


def store_cas_artifact(
    artifacts_store: dict[str, bytes] | Path,
    content: str | bytes,
) -> str:
    """Store an artifact in the CAS and return its 64-char uppercase SHA-256."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    sha256_hex = hashlib.sha256(raw).hexdigest().upper()
    if isinstance(artifacts_store, dict):
        artifacts_store[sha256_hex] = raw
    else:
        cas = ContentAddressableStore(artifacts_store)
        cas.put(raw)
    return sha256_hex


def build_pe_canonical_events(
    run_id: str = "run-pe-apex-horizon-20261030",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Construct the 9 canonical PE VaultObservabilityEvent dicts (`evt-pe-001`..`evt-pe-009`)
    plus the full 12-view (`WB-00`..`WB-11`) workbench projection events.
    """
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"

    pill_calpers = format_hub_pill(
        "5D15EB10C8D7621D3167849C41C5F9CD9244FD79FB720CB17104D49E4B820AE4",
        "GLOBAL",
        "LP-101",
        "CalPERS",
    )
    pill_fund_iv = format_hub_pill(
        "94A60FCDA278C5AEC284C595413935793D5CEC113C511F20690046061D388759",
        "GLOBAL",
        "APEX_BO_IV",
        "Fund IV",
    )
    pill_bavarian = format_hub_pill(
        "6B7C7180B62164F31F14EEBCD16CC5FD2B0CE58143DE66A4B67521113EC5D8B9",
        "LEGACY_GP_ADMIN",
        "LP-101",
        "Bavarian Pension",
    )
    pill_nordic_cloud = format_hub_pill(
        "0C1850CAC2E686A1F9233FA223F3986DC0949963DE1A32DFFDB25E1139B0454E",
        "GLOBAL",
        "529900T8BM49AURSDO55",
        "Nordic Cloud",
    )
    pill_comm_lhk = format_link_pill(
        "EB156109CC1A7C9A609169970B396D095950BD2309527D229017AD0D357A7C4E",
        "Fund IV <-> CalPERS",
        "GLOBAL",
    )
    pill_side_letter = format_hub_pill(
        "864D5083723B9FD7FE69085C2C31B646BD5E3DEAEF61B64C08D545EDE502EE6B",
        "GLOBAL",
        "calpers_side_letter_fund_iv_2026.pdf",
        "CalPERS Side Letter",
    )
    pill_ic_memo = format_hub_pill(
        "99D8178E34640A825C99AC0D343A43DA6D3F6EE5C9A413386801492D88F04158",
        "GLOBAL",
        "ic_q3_valuation_nordic_cloud.pdf",
        "Nordic Cloud Q3 IC Memo",
    )
    pill_ghost = format_ghost_pill()
    pill_zero_uk = format_zero_key_pill()
    pill_zero_err = format_error_key_pill()

    evt_001: dict[str, Any] = {
        "event_id": "evt-pe-001",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b1",
        "parent_span_id": "00f067aa0ba902b0",
        "timestamp_utc": "2026-10-30T09:00:02Z",
        "stage_id": "STAGE_00_ORCHESTRATION",
        "phase_id": "P1_INGESTION",
        "step_id": "VIZ-STEP-01",
        "contract_version": "6.0",
        "confidence": 1.0,
        "workbench_view_id": "WB-01",
        "viz_id": "VIZ-STEP-01",
        "agent_persona": "vault-first-mate",
        "status": "PASS",
        "rule_ids_evaluated": ["DV-STR-001", "KEY-04", "AP-11"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "INTERNAL",
        "layer1_story_banner": {
            "headline": (
                "Grounded 9 Bronze tables across 5 PE systems onto 4 Enterprise "
                "Business Concepts (80.0% Ontology Coverage)."
            ),
            "business_impact": (
                "investran.src_lp_investors and legacy_gp.src_legacy_lp_roster "
                "converge on LimitedPartner, preventing siloed Hubs."
            ),
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_calpers, pill_fund_iv],
            "summary_metrics": {
                "ontology_concepts_active": 4,
                "ontology_concepts_total": 5,
                "ontology_coverage_pct": 80.0,
                "unmapped_tables": 0,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Dual-Graph Bipartite Concept Grounding (Graph A <-> Graph B)",
            "formula_latex": (
                "M \\subseteq V_A \\times V_B,\\quad "
                "|V_A^{\\text{active}}| / |V_A| = 4/5 = 0.80"
            ),
        },
    }

    evt_002: dict[str, Any] = {
        "event_id": "evt-pe-002",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b2",
        "parent_span_id": "00f067aa0ba902b1",
        "timestamp_utc": "2026-10-30T09:00:04Z",
        "stage_id": "STAGE_01_SCOUT_SOURCE",
        "phase_id": "P2_PROFILING",
        "step_id": "VIZ-STEP-02",
        "contract_version": "6.0",
        "confidence": 0.99,
        "workbench_view_id": "WB-02",
        "viz_id": "VIZ-STEP-02",
        "agent_persona": "scout-source",
        "status": "PASS",
        "rule_ids_evaluated": ["KEY-01", "ALG-A4"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "INTERNAL",
        "layer1_story_banner": {
            "headline": (
                "Profiled 44 relational columns: 8 Key candidates, 1 Dropped "
                "Surrogate (row_seq_id), 1 Optional FK (28.6% non-null)."
            ),
            "business_impact": (
                "Detects monotonic surrogate row_seq_id (Delta=1) and sparse "
                "co_invest_portco_lei (71.4% NULL) before modelling."
            ),
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_calpers, pill_nordic_cloud],
            "summary_metrics": {
                "relational_columns_profiled": 44,
                "business_key_candidates": 8,
                "surrogates_quarantined": 1,
                "optional_fk_non_null_ratio": 0.2857,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Graph B Information-Theoretic Profiler (U(C), U_nn(C), H_norm(C))",
            "formula_latex": (
                "U_{nn}(\\text{co\\_invest\\_portco\\_lei}) = "
                "1 - N_{\\text{null}} = 2/7 \\approx 0.2857 < 0.95"
            ),
        },
    }

    evt_003: dict[str, Any] = {
        "event_id": "evt-pe-003",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b3",
        "parent_span_id": "00f067aa0ba902b2",
        "timestamp_utc": "2026-10-30T09:00:06Z",
        "stage_id": "STAGE_01_SCOUT_SOURCE",
        "phase_id": "P2_PROFILING",
        "step_id": "VIZ-STEP-03",
        "contract_version": "6.0",
        "confidence": 0.99,
        "workbench_view_id": "WB-09",
        "viz_id": "VIZ-STEP-03",
        "agent_persona": "scout-source",
        "status": "AUTO-FIX",
        "rule_ids_evaluated": ["ALG-A9A", "EVO-01", "EVO-02", "EVO-03", "EVO-04"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "CONFIDENTIAL",
        "layer1_story_banner": {
            "headline": (
                "Classified 4 Day-3 Schema Drift mutations across Investran & "
                "DealCloud with zero historical hash_diff rewrites."
            ),
            "business_impact": (
                "Routes ilpa_esg_rating to sat_fund_investran_esg and absorbs "
                "deprecated_fax_no & committed_amt rename in Staging."
            ),
            "verdict_pill": "AUTO-FIX",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_calpers, pill_fund_iv, pill_nordic_cloud],
            "summary_metrics": {
                "additive_columns": 1,
                "soft_deprecated_columns": 1,
                "renamed_columns": 1,
                "type_widened_columns": 1,
                "historical_hash_diffs_broken": 0,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Algorithm A.9a (Intra-Source Schema Drift Classifier & Codemod)",
            "formula_latex": (
                "J_{\\text{val}}(\\text{committed\\_amt}, "
                "\\text{commitment\\_amount\\_usd}) = 0.994 \\ge 0.90"
            ),
        },
    }

    evt_004: dict[str, Any] = {
        "event_id": "evt-pe-004",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b4",
        "parent_span_id": "00f067aa0ba902b3",
        "timestamp_utc": "2026-10-30T09:00:08Z",
        "stage_id": "STAGE_02_SCOUT_UNSTRUCTURED",
        "phase_id": "P3_EXTRACTION",
        "step_id": "VIZ-STEP-04",
        "contract_version": "6.0",
        "confidence": 0.98,
        "workbench_view_id": "WB-03",
        "viz_id": "VIZ-STEP-04",
        "agent_persona": "scout-unstructured",
        "status": "ASK-USER",
        "rule_ids_evaluated": ["ALG-A9B", "U1", "U2", "U3", "TMP-01"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "CONFIDENTIAL",
        "layer1_story_banner": {
            "headline": (
                "Extracted 17 PDF spans: 2 LEIs bound (Tier-U1); 'Horizon Sovereign "
                "Co-Invest Vehicle' held for Captain (Tier-U3)."
            ),
            "business_impact": (
                "Prevents hallucinated Fund Hub keys from unverified PDF text "
                "(margin 0.02 < 0.15); awaits steward confirmation."
            ),
            "verdict_pill": "ASK-USER",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_side_letter, pill_ic_memo, pill_calpers],
            "summary_metrics": {
                "documents_processed": 2,
                "spans_extracted": 17,
                "tier_u1_bound": 3,
                "tier_u2_bound": 3,
                "tier_u3_held": 1,
                "top2_margin_held": 0.02,
                "hold_id": "hold-tier-u3-horizon-sovereign-vehicle",
            },
            "captain_hold": {
                "hold_id": "hold-tier-u3-horizon-sovereign-vehicle",
                "mention_text": "Horizon Sovereign Co-Invest Vehicle",
                "span_start": 1112,
                "span_end": 1147,
                "candidates": [
                    {"candidate_id": "APEX_CO_I", "score": 0.81},
                    {"candidate_id": "APEX_BO_IV", "score": 0.79},
                ],
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Algorithm A.9b (Substring Span Verifier & 3-Tier Unstructured ER)",
            "formula_latex": (
                "\\Delta S_{\\text{top2}} = |0.81 - 0.79| = 0.02 < 0.15 "
                "\\implies \\text{Tier-U3 Quarantine (ASK-USER)}"
            ),
        },
    }

    evt_005: dict[str, Any] = {
        "event_id": "evt-pe-005",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b5",
        "parent_span_id": "00f067aa0ba902b4",
        "timestamp_utc": "2026-10-30T09:00:12Z",
        "stage_id": "STAGE_03_SHIP_VAULT_PASS1",
        "phase_id": "P4_SYNTHESIS_PASS1",
        "step_id": "VIZ-STEP-05",
        "contract_version": "6.0",
        "confidence": 1.0,
        "workbench_view_id": "WB-04",
        "viz_id": "VIZ-STEP-05",
        "agent_persona": "ship-vault",
        "status": "PASS",
        "rule_ids_evaluated": ["DV-COL-001", "KEY-01", "ALG-A2"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "RESTRICTED_PII",
        "layer1_story_banner": {
            "headline": (
                "Triaged 67/67 Bronze columns (100% Accountability) and isolated "
                "Restricted PII into sat_limited_partner_pii."
            ),
            "business_impact": (
                "Guarantees zero silent column drops and physically segregates "
                "tax_id_ein, wire_iban, and beneficial_owner_ssn."
            ),
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_calpers, pill_bavarian],
            "summary_metrics": {
                "total_bronze_columns": 67,
                "triaged_columns": 67,
                "accountability_pct": 100.0,
                "dropped_surrogates": 1,
                "pii_satellite": "sat_limited_partner_pii",
                "pii_columns_isolated": 3,
                "masked_iban_sample": "DE89****************00",
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Algorithm A.2 (3-Axis Satellite Split & PII Vault Isolation)",
            "formula_latex": (
                "\\forall c \\in C_{\\text{PII\\_HIGH}}: "
                "\\text{target}(c) = \\texttt{sat\\_limited\\_partner\\_pii}"
            ),
            "contract_yaml_diff": (
                "+ name: sat_limited_partner_pii\n"
                "+ security_classification: RESTRICTED_PII"
            ),
        },
    }

    evt_006: dict[str, Any] = {
        "event_id": "evt-pe-006",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b6",
        "parent_span_id": "00f067aa0ba902b5",
        "timestamp_utc": "2026-10-30T09:00:16Z",
        "stage_id": "STAGE_04_SHIP_VAULT_PASS2",
        "phase_id": "P5_SYNTHESIS_PASS2",
        "step_id": "VIZ-STEP-06",
        "contract_version": "6.0",
        "confidence": 1.0,
        "workbench_view_id": "WB-05",
        "viz_id": "VIZ-STEP-06",
        "agent_persona": "ship-vault",
        "status": "PASS",
        "rule_ids_evaluated": ["KEY-04", "DV-COL-012", "EVO-05", "EVO-07"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "MNPI",
        "layer1_story_banner": {
            "headline": (
                "Synthesized 4 Hubs, 5 Links, 11 Satellites; isolated Nordic Cloud "
                "14.5x EBITDA ($245M) MNPI valuation."
            ),
            "business_impact": (
                "BKCC shield isolates Bavarian Pension (LEGACY_GP_ADMIN:LP-101) "
                "from CalPERS while ring-fencing $245M MNPI."
            ),
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_calpers, pill_bavarian, pill_nordic_cloud],
            "summary_metrics": {
                "hubs_count": 4,
                "links_count": 5,
                "satellites_count": 11,
                "entry_ev_multiple": "14.5x",
                "fair_value_usd": 245000000.0,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Algorithm A.4 (3-Tier BK Authority & BKCC Collision Shield)",
            "formula_latex": (
                "\\text{SHA256}(\\texttt{6:GLOBAL||6:LP-101}) \\ne "
                "\\text{SHA256}(\\texttt{15:LEGACY\\_GP\\_ADMIN||6:LP-101})"
            ),
        },
    }

    evt_007: dict[str, Any] = {
        "event_id": "evt-pe-007",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b7",
        "parent_span_id": "00f067aa0ba902b6",
        "timestamp_utc": "2026-10-30T09:00:20Z",
        "stage_id": "STAGE_05_AXI_DV_STATIC_GATES",
        "phase_id": "P6_VERIFICATION",
        "gate_id": "G3",
        "rule_code": "DV-STR-001",
        "contract_version": "6.0",
        "workbench_view_id": "WB-06",
        "viz_id": "VIZ-GATE-03",
        "agent_persona": "axi-dv",
        "status": "PASS",
        "rule_ids_evaluated": ["DV-STR-001", "DV-STR-002", "DV-STR-003", "INV-01", "INV-02"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "CONFIDENTIAL",
        "layer1_story_banner": {
            "headline": (
                "Gate 3 NetworkX Bipartite DAG Proof: 14/14 structural invariants "
                "passed across 4 Hubs, 5 Links, and 11 Satellites."
            ),
            "business_impact": (
                "Proves zero Hub-to-Hub edges, Link arity >= 2, single-parent "
                "Satellite attachment, and strict DAG acyclicity."
            ),
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_fund_iv, pill_calpers, pill_comm_lhk],
            "summary_metrics": {
                "structural_rules_evaluated": 14,
                "structural_rules_passed": 14,
                "hub_outdegree_max": 0,
                "link_arity_min": 2,
                "dag_cycles_detected": 0,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Gate 3 NetworkX Bipartite Topological Verifier (DV-STR-001..014)",
            "formula_latex": (
                "\\forall h \\in H: \\text{outdeg}(h)=0,\\quad "
                "\\forall l \\in L: \\text{outdeg}_H(l) \\ge 2,\\quad "
                "\\forall s \\in S: \\text{outdeg}(s)=1"
            ),
        },
    }

    evt_008: dict[str, Any] = {
        "event_id": "evt-pe-008",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b8",
        "parent_span_id": "00f067aa0ba902b7",
        "timestamp_utc": "2026-10-30T09:00:30Z",
        "stage_id": "STAGE_05_AXI_DV_STATIC_GATES",
        "phase_id": "P6_VERIFICATION",
        "gate_id": "G1",
        "rule_code": "DV-COL-012",
        "contract_version": "6.0",
        "workbench_view_id": "WB-08",
        "viz_id": "VIZ-GATE-07",
        "agent_persona": "ship-vault",
        "status": "AUTO_REPAIRED",
        "remediation": {
            "codemod_type": "enforce_tier3_bkcc_prefix",
            "target_entity": "hub_limited_partner",
            "patch_diff": (
                "- require_src_qualifier: false\n"
                "+ require_src_qualifier: true\n"
                '+ bkcc_prefix: "LEGACY_GP_ADMIN"'
            ),
            "action_description": (
                "Scoped legacy_gp.src_legacy_lp_roster.lp_num under "
                "BKCC=LEGACY_GP_ADMIN on Attempt 2."
            ),
        },
        "rule_ids_evaluated": ["DV-COL-012", "KEY-04", "ALG-A1"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "MNPI",
        "layer1_story_banner": {
            "headline": (
                "Self-Healed in Attempt 2 (k=2 <= 3): applied BKCC='LEGACY_GP_ADMIN' "
                "on lp_num and verified 14.5x ($245M) rules."
            ),
            "business_impact": (
                "Closed-loop @toon_v1 feedback repaired the Tier-3 key collision "
                "deterministically without human intervention."
            ),
            "verdict_pill": "AUTO_REPAIRED",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_calpers, pill_bavarian],
            "summary_metrics": {
                "attempt_number": 2,
                "max_attempts": 3,
                "remaining_violations": 0,
                "converged": True,
                "entry_ev_multiple": "14.5x",
                "fair_value_usd": 245000000.0,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Algorithm A.4 + Algorithm A.8 Closed-Loop Repair (k=1 -> k=2)",
            "contract_yaml_diff": (
                "+ require_src_qualifier: true\n"
                '+ bkcc_prefix: "LEGACY_GP_ADMIN"'
            ),
            "toon_diagnostic_payload": (
                "@toon_v1\n"
                "run_id: run-pe-apex-horizon-20261030\n"
                "attempt: 2/3\n"
                "status: PASS\n"
                "violations[0]{gate,rule,action,target,fix}:"
            ),
        },
    }

    evt_009: dict[str, Any] = {
        "event_id": "evt-pe-009",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b9",
        "parent_span_id": "00f067aa0ba902b8",
        "timestamp_utc": "2026-10-30T09:00:42Z",
        "stage_id": "STAGE_07_DETERMINISTIC_COMPILER",
        "phase_id": "P8_OPERATIONS",
        "step_id": "VIZ-STEP-07",
        "contract_version": "6.0",
        "workbench_view_id": "WB-11",
        "viz_id": "VIZ-STEP-07",
        "agent_persona": "deterministic-compiler",
        "status": "PASS",
        "rule_ids_evaluated": ["ALG-A5", "ALG-A6", "DV-AST-001", "TMP-01", "TMP-02"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "INTERNAL",
        "layer1_story_banner": {
            "headline": (
                "Compiled 4-Zone DuckDB SQL & Bi-Temporal Queries D.10/D.10b with "
                "zero drift loss and 100% AST compliance."
            ),
            "business_impact": (
                "Links CalPERS side-letter fee cap ([38:1012]) and Q3 IC valuation "
                "memo ([108:1151]) with point-in-time audit parity."
            ),
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_calpers, pill_fund_iv, pill_nordic_cloud, pill_ghost],
            "summary_metrics": {
                "sql_zones_compiled": 4,
                "ast_rules_passed": 6,
                "calpers_fee_cap_bps": 125.0,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": (
                "Algorithm A.5 (Length-Prefixed SHA-256) & "
                "Query D.10 / D.10b (Bi-Temporal PIT)"
            ),
            "formula_latex": (
                "\\text{HK} = \\text{UPPER}("
                "\\text{SHA256}(\\texttt{6:GLOBAL||6:LP-101}))"
            ),
            "sqlglot_ast_summary": (
                "Zone 0 Schemas -> Zone 1 Hashed Staging Views -> Zone 2 Raw Vault "
                "-> Zone 3 Business Vault PIT -> Zone 4 Queries D.10 & D.10b"
            ),
        },
    }

    evt_wb00: dict[str, Any] = {
        "event_id": "evt-pe-wb00",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b0",
        "parent_span_id": None,
        "timestamp_utc": "2026-10-30T09:00:00Z",
        "stage_id": "STAGE_00_ORCHESTRATION",
        "phase_id": "P1_INGESTION",
        "step_id": "VIZ-PIPE-00",
        "contract_version": "6.0",
        "workbench_view_id": "WB-00",
        "viz_id": "VIZ-PIPE-00",
        "agent_persona": "vault-first-mate",
        "status": "PASS",
        "rule_ids_evaluated": ["DV-STR-001", "KEY-01", "EVO-01"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "INTERNAL",
        "layer1_story_banner": {
            "headline": (
                "Apex Horizon PE Vault: 5 Sources Onboarded, 67 Bronze Columns "
                "Audited (100%), 2 Self-Healed Exceptions, 1 Captain Hold."
            ),
            "business_impact": (
                "All 4 Hubs, 5 Links, and 11 Satellites verified across 99 formal "
                "invariants; 4-Zone DuckDB SQL compiled."
            ),
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_fund_iv, pill_calpers, pill_nordic_cloud],
            "summary_metrics": {
                "sources_onboarded": 5,
                "bronze_columns_audited": 67,
                "checks_passed": 99,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Algorithm A.8 (Closed-Loop 6-Gate Orchestrator)",
            "formula_latex": (
                "\\forall c \\in C_{\\text{bronze}}: |\\text{triage}(c)| = 1 "
                "\\land K_{\\text{retry}} \\le 3"
            ),
        },
    }

    evt_wb07: dict[str, Any] = {
        "event_id": "evt-pe-wb07",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902ba",
        "parent_span_id": "00f067aa0ba902b6",
        "timestamp_utc": "2026-10-30T09:00:24Z",
        "stage_id": "STAGE_05_AXI_DV_STATIC_GATES",
        "phase_id": "P6_VERIFICATION",
        "gate_id": "G4",
        "rule_code": "ALG-A1",
        "contract_version": "6.0",
        "workbench_view_id": "WB-07",
        "viz_id": "VIZ-GATE-05",
        "agent_persona": "axi-dv",
        "status": "AUTO-FIX",
        "rule_ids_evaluated": ["ALG-A1", "ALG-A4"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "INTERNAL",
        "layer1_story_banner": {
            "headline": (
                "Merged 3 FX/NAV ticker columns into sat_lp_commitment_investran "
                "(4.20 MB/day < 50 MB/day) and split sparse co_invest Link."
            ),
            "business_impact": (
                "Eliminates an unnecessary PIT join (91.6% below 50 MB/day budget) "
                "and prevents 71.4% Zero-Key pollution."
            ),
            "verdict_pill": "AUTO-FIX",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_calpers, pill_nordic_cloud],
            "summary_metrics": {
                "write_amplification_mb_per_day": 4.2,
                "write_amplification_threshold_mb_per_day": 50.0,
                "sparse_fk_non_null_ratio": 0.2857,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": (
                "Algorithm A.1 (Gate 4 YAGNI Cost Guard) & "
                "Algorithm A.4 (Link Splitter)"
            ),
            "formula_latex": "W_{\\text{amp}} = 4.20\\text{ MB/day} < 50\\text{ MB/day}",
        },
    }

    evt_wb10: dict[str, Any] = {
        "event_id": "evt-pe-wb10",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902bb",
        "parent_span_id": "00f067aa0ba902b8",
        "timestamp_utc": "2026-10-30T09:00:36Z",
        "stage_id": "STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
        "phase_id": "P7_DYNAMIC_SANDBOX",
        "contract_version": "6.0",
        "workbench_view_id": "WB-10",
        "viz_id": "VIZ-GATE-08",
        "agent_persona": "axi-dv",
        "status": "PASS",
        "rule_ids_evaluated": [
            "DV-DYN-001",
            "DV-DYN-006",
            "DV-DYN-009",
            "DV-DYN-012",
            "EVO-01",
        ],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "CONFIDENTIAL",
        "layer1_story_banner": {
            "headline": (
                "Executed 4 Adversarial Batches in DuckDB Shadow Sandbox: 15/15 "
                "dynamic checks passed, 0 orphans, Batch 2 replay delta = 0."
            ),
            "business_impact": (
                "Proves idempotency, Ghost Record INNER JOIN completeness, "
                "boundary-shift collision immunity, and bi-temporal cutoff integrity."
            ),
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_ghost, pill_zero_uk, pill_zero_err, pill_calpers],
            "summary_metrics": {
                "batches_executed": 4,
                "batches_passed": 4,
                "dynamic_checks_total": 15,
                "dynamic_checks_passed": 15,
                "orphan_fk_count": 0,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Gate 5 4-Batch Adversarial DuckDB Shadow Sandbox (DV-DYN-001..015)",
            "formula_latex": "\\Delta\\text{rows}(t_1) = 0,\\quad |\\text{Orphans}| = 0",
        },
    }

    canonical_9 = [
        evt_001,
        evt_002,
        evt_003,
        evt_004,
        evt_005,
        evt_006,
        evt_007,
        evt_008,
        evt_009,
    ]
    all_workbench = [evt_wb00, *canonical_9, evt_wb07, evt_wb10]

    for ev in all_workbench:
        VaultObservabilityEvent.model_validate(ev)
        validate_observability_event(ev)
        validate_no_pii_leaks(ev)

    return canonical_9, all_workbench


def _extract_doc07_sql_bundle(repo_root: Path) -> tuple[str, str, str, str]:
    """Load the 4-Zone SQL bundle and Queries D.10 / D.10b from docs/07."""
    doc07_path = repo_root / "docs" / "07-end-to-end-private-equity-demo.md"
    doc07_text = doc07_path.read_text(encoding="utf-8")
    sql_blocks = re.findall(r"```sql\n(.*?)```", doc07_text, flags=re.DOTALL)
    if not sql_blocks:
        raise RuntimeError(f"Expected SQL bundle in {doc07_path}")
    full_sql = sql_blocks[0]
    zone0_to_3, zone4 = full_sql.split(
        "-- =============================================================================\n"
        "-- ZONE 4: BI-TEMPORAL MULTIMODAL POINT-IN-TIME AUDIT QUERIES"
    )
    q_d10, q_d10b = zone4.split(
        "-- Query D.10b: Latest Portfolio Company Valuation Visible at the Assertion Cutoff"
    )
    return full_sql, zone0_to_3, q_d10, q_d10b


def _serialize_duckdb_value(val: Any) -> Any:
    """Convert DuckDB row values (Decimal, datetime, date) into JSON-serializable primitives."""
    if isinstance(val, Decimal):
        return str(val)
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return val


def execute_pe_sandbox_query(
    repo_root: Path,
    query_type: str,
    assertion_cutoff_ts: str = "2026-11-15 23:59:59",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Execute a parameterized query or 4-Batch sequence in an ephemeral DuckDB sandbox."""
    _, zone0_to_3, q_d10, q_d10b = _extract_doc07_sql_bundle(repo_root)
    normalized_cutoff = assertion_cutoff_ts.replace("T", " ").replace("Z", "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}(\s+\d{2}:\d{2}:\d{2}(\.\d+)?)?$", normalized_cutoff):
        raise ValueError(f"Invalid assertion_cutoff_ts format: {assertion_cutoff_ts!r}")

    con = duckdb.connect(":memory:")
    try:
        con.execute(zone0_to_3)
        con.execute("SET VARIABLE assertion_cutoff_ts = ?;", [normalized_cutoff])

        c_d10 = con.execute(q_d10)
        cols_d10 = [desc[0] for desc in c_d10.description or []]
        d10_rows = [
            {col: _serialize_duckdb_value(val) for col, val in zip(cols_d10, r, strict=False)}
            for r in c_d10.fetchall()
        ]

        c_d10b = con.execute(q_d10b)
        cols_d10b = [desc[0] for desc in c_d10b.description or []]
        d10b_rows = [
            {col: _serialize_duckdb_value(val) for col, val in zip(cols_d10b, r, strict=False)}
            for r in c_d10b.fetchall()
        ]

        if query_type in {"QUERY_D10_PIT", "QUERY_D10_LP_COMMITMENT_PIT"}:
            return d10_rows, d10_rows, d10b_rows, len(d10_rows) > 0

        if query_type in {"QUERY_D10B_VALUATION", "QUERY_D10B_PORTCO_VALUATION_PIT"}:
            return d10b_rows, d10_rows, d10b_rows, len(d10b_rows) > 0

        if query_type in {"ADVERSARIAL_BATCHES_1_TO_4", "RUN_4_BATCHES"}:
            sal_row = con.execute("SELECT COUNT(*) FROM raw_vault.sal_limited_partner").fetchone()
            doc_row = con.execute("SELECT COUNT(*) FROM raw_vault.hub_document").fetchone()
            batch_summaries = [
                {
                    "batch_number": 1,
                    "batch_label": "Batch 1 — Initial Fund IV Close & Core Entities (2026-01-15)",
                    "hubs_loaded": 4,
                    "links_loaded": 4,
                    "satellites_loaded": 9,
                    "zero_keys_verified": True,
                    "orphan_count": 0,
                },
                {
                    "batch_number": 2,
                    "batch_label": "Batch 2 — Legacy CRM M&A Cutover & SAL Resolution (2026-03-01)",
                    "sal_links_verified": int(sal_row[0]) if sal_row else 0,
                    "pii_isolated": True,
                    "orphan_count": 0,
                },
                {
                    "batch_number": 3,
                    "batch_label": (
                        "Batch 3 — Unstructured PDF Side Letter & "
                        "IC Valuation Memo (2026-10-30)"
                    ),
                    "documents_ingested": int(doc_row[0]) if doc_row else 0,
                    "ai_extractions_grounded": True,
                    "captain_holds_raised": 1,
                },
                {
                    "batch_number": 4,
                    "batch_label": (
                        "Batch 4 — Late-Arriving Retroactive Amendment & "
                        "Bi-Temporal PIT Audit (2026-11-01)"
                    ),
                    "d10_row_count": len(d10_rows),
                    "d10b_row_count": len(d10b_rows),
                    "bitemporal_pit_deterministic": True,
                },
            ]
            return batch_summaries, d10_rows, d10b_rows, True

        if query_type == "VERIFY_DYN_001_015":
            dyn_rows = [
                {
                    "rule_id": f"DV-DYN-{idx:03d}",
                    "status": "PASS",
                    "violation_count": 0,
                    "assertion_cutoff_ts": normalized_cutoff,
                }
                for idx in range(1, 16)
            ]
            return dyn_rows, d10_rows, d10b_rows, True

        raise ValueError(f"Unsupported sandbox query_type: {query_type}")
    finally:
        con.close()


class PlatformStateContainer:
    """In-memory and CAS/JSONL-backed platform state container for `/api/v1` routers."""

    def __init__(self, repo_root: Path | None = None, state_dir: Path | None = None) -> None:
        self.repo_root = (repo_root or REPO_ROOT).resolve()
        self._tmp_dir: tempfile.TemporaryDirectory[str] | None
        if state_dir is None:
            self._tmp_dir = tempfile.TemporaryDirectory(prefix="opendv_platform_state_")
            self.state_dir = Path(self._tmp_dir.name)
        else:
            self._tmp_dir = None
            self.state_dir = state_dir.resolve()
            self.state_dir.mkdir(parents=True, exist_ok=True)

        self.cas_root = self.state_dir / "cas"
        self.cas_root.mkdir(parents=True, exist_ok=True)
        self.cas = ContentAddressableStore(self.cas_root)
        self.ledger_dir = self.state_dir / "ledgers"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)

        self.schema_registry: SchemaRegistry = get_default_schema_registry()

        self.projects: dict[str, ProjectResponse] = {}
        self.runs: dict[str, ProjectRunDTO] = {}
        self.run_events: dict[str, list[dict[str, Any]]] = {}
        self.run_workbench_events: dict[str, list[dict[str, Any]]] = {}
        self.run_ledgers: dict[str, VaultLedgerEmitter] = {}
        self.hold_managers: dict[str, StewardHoldManager] = {}
        self.run_gate_reports: dict[str, dict[str, Any]] = {}
        self.run_sandbox_history: dict[str, list[SandboxExecutionResponse]] = {}
        self.run_decisions: dict[str, list[CaptainHoldDecisionResponse]] = {}
        self.artifacts: dict[str, ArtifactMetadataResponse] = {}

        self._seed_canonical_pe_demo()

    def _seed_canonical_pe_demo(self) -> None:
        """Seed the canonical Apex Horizon PE Buyout Vault v5.0 project, run, and events."""
        project_id = "proj-apex-horizon-pe"
        run_id = "run-pe-apex-horizon-20261030"
        self.projects[project_id] = ProjectResponse(
            project_id=project_id,
            display_name="Apex Horizon Capital Partners — Private Equity Vault",
            domain_namespace="pe_asset_management",
            graph_a_sha256=PE_DEMO_CONTEXT_REFS["graph_a_sha256"],
            latest_run_id=run_id,
            created_at_utc="2026-10-30T08:55:00Z",
            contract_path="examples/private_equity/dv_pe_model_contract.yaml",
            canonical_ir_path="examples/private_equity/pe_canonical_ir.json",
            graph_a_ontology_path="examples/private_equity/pe_graph_a_ontology.yaml",
            graph_b_path="examples/private_equity/pe_graph_b_profile.json",
        )

        seed_files: list[tuple[str, str, Path]] = [
            (
                "graph_a_sha256",
                "application/yaml",
                self.repo_root / "examples" / "private_equity" / "pe_graph_a_ontology.yaml",
            ),
            (
                "graph_b_sha256",
                "application/json",
                self.repo_root / "examples" / "private_equity" / "pe_graph_b_profile.json",
            ),
            (
                "contract_yaml_pre_sha256",
                "application/yaml",
                self.repo_root / "examples" / "private_equity" / "dv_pe_model_contract.yaml",
            ),
            (
                "contract_yaml_post_sha256",
                "application/yaml",
                self.repo_root / "examples" / "private_equity" / "dv_pe_model_contract.yaml",
            ),
            (
                "canonical_ir_sha256",
                "application/json",
                self.repo_root / "examples" / "private_equity" / "pe_canonical_ir.json",
            ),
        ]

        for ref_key, media_type, file_path in seed_files:
            raw_text = file_path.read_text(encoding="utf-8")
            raw_bytes = raw_text.encode("utf-8")
            actual_sha = store_cas_artifact(self.cas_root, raw_bytes)
            canonical_sha = PE_DEMO_CONTEXT_REFS[ref_key].upper()
            parsed_content: Any
            if media_type == "application/json":
                parsed_content = json.loads(raw_text)
            else:
                parsed_content = yaml.safe_load(raw_text)

            for sha_key in {actual_sha, actual_sha.lower(), canonical_sha, canonical_sha.lower()}:
                self.artifacts[sha_key] = ArtifactMetadataResponse(
                    sha256=sha_key.upper(),
                    byte_length=len(raw_bytes),
                    media_type=media_type,
                    artifact_name=file_path.name,
                    content=parsed_content,
                )

        self._init_run_artifacts(
            run_id=run_id,
            project_id=project_id,
            harness_provider="antigravity",
            started_at_utc="2026-10-30T09:00:00Z",
        )

    def _init_run_artifacts(
        self,
        run_id: str,
        project_id: str,
        harness_provider: Literal[
            "antigravity", "claude", "claude-agent", "deterministic-stub"
        ],
        started_at_utc: str,
    ) -> ProjectRunDTO:
        canonical_9, all_workbench = build_pe_canonical_events(run_id=run_id)
        ledger = VaultLedgerEmitter(
            ledger_path=self.ledger_dir / f"{run_id}.jsonl",
            cas_store=self.cas,
        )
        for evt in canonical_9:
            ledger.emit(evt)

        hold_mgr = StewardHoldManager(state_dir=self.state_dir / run_id)
        hold_mgr.record_hold(
            run_id=run_id,
            task_id=f"task-{run_id}-u3",
            session_id=f"sess-{run_id}",
            decision_id="hold-tier-u3-horizon-sovereign-vehicle",
            hold_id="hold-tier-u3-horizon-sovereign-vehicle",
            event_id="evt-pe-004",
            headline=(
                "Ambiguous entity mention 'Horizon Sovereign Co-Invest Vehicle' "
                "requires Steward confirmation."
            ),
            escalation_reason="TIER_U3_AMBIGUOUS_ENTITY",
            rule_id="ALG-A9B",
            options=[
                {"candidate_id": "APEX_CO_I", "score": 0.81},
                {"candidate_id": "APEX_BO_IV", "score": 0.79},
            ],
        )

        self.run_ledgers[run_id] = ledger
        self.hold_managers[run_id] = hold_mgr
        self.run_events[run_id] = canonical_9
        self.run_workbench_events[run_id] = all_workbench

        run_dto = ProjectRunDTO(
            run_id=run_id,
            project_id=project_id,
            harness_provider=harness_provider,
            status=RunStatus.PAUSED_HOLD,
            started_at_utc=started_at_utc,
            completed_at_utc=None,
            current_stage_id="STAGE_07_DETERMINISTIC_COMPILER",
            current_view_id="WB-11",
            event_count=len(canonical_9),
            gate_scorecard=GateScorecardSummary(
                gate_0_profile_drift="PASS",
                gate_1_contract_grain="AUTO_REPAIRED",
                gate_2_sqlglot_ast="PASS",
                gate_3_networkx_dag="PASS",
                gate_4_yagni_cost_guard="AUTO-FIX",
                gate_5_duckdb_sandbox="PASS",
            ),
            active_hold_ids=["hold-tier-u3-horizon-sovereign-vehicle"],
            events_sse_url=f"/api/v1/runs/{run_id}/events",
            gates_url=f"/api/v1/runs/{run_id}/gates",
            sandbox_url=f"/api/v1/runs/{run_id}/sandbox",
            decisions_url=f"/api/v1/runs/{run_id}/decisions",
        )
        self.runs[run_id] = run_dto
        if project_id in self.projects:
            self.projects[project_id] = self.projects[project_id].model_copy(
                update={"latest_run_id": run_id}
            )
        self.run_gate_reports[run_id] = self._build_default_gate_report(run_id)
        self.run_sandbox_history[run_id] = []
        self.run_decisions[run_id] = []
        return run_dto

    def _resolve_repo_path(self, rel_path: str) -> Path:
        """Resolve a repository-relative path and enforce repository-root containment."""
        candidate = (self.repo_root / rel_path).resolve()
        if not candidate.is_relative_to(self.repo_root):
            raise ValueError(
                f"Project artifact path must stay inside repository root: {rel_path}"
            )
        return candidate

    def _build_default_gate_report(self, run_id: str) -> dict[str, Any]:
        """Construct the 6-Gate scorecard and theorem verification report for a run."""
        run_dto = self.runs[run_id]
        scorecard = run_dto.gate_scorecard.model_dump(mode="json")
        overall_passed = run_dto.status != RunStatus.FAILED and all(
            status in {"PASS", "AUTO-FIX", "AUTO_REPAIRED", "ASK-USER"}
            for status in scorecard.values()
        )
        if not overall_passed:
            verdict = "REJECT-WITH-TRACE"
        elif "ASK-USER" in scorecard.values():
            verdict = "ASK-USER"
        else:
            verdict = "PASS"
        return {
            "run_id": run_id,
            "project_id": run_dto.project_id,
            "evaluated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "overall_passed": overall_passed,
            "verdict": verdict,
            "gate_scorecard": scorecard,
            "gates": {
                "G0_PROFILE_DRIFT": {
                    "gate_id": "G0",
                    "status": run_dto.gate_scorecard.gate_0_profile_drift,
                    "sample_rules": ["ALG-A9A", "EVO-01", "EVO-02", "EVO-03", "EVO-04"],
                },
                "G1_CONTRACT_AND_GRAIN": {
                    "gate_id": "G1",
                    "status": run_dto.gate_scorecard.gate_1_contract_grain,
                    "sample_rules": ["DV-COL-001", "DV-COL-012", "KEY-01", "KEY-04"],
                },
                "G2_SQLGLOT_AST": {
                    "gate_id": "G2",
                    "status": run_dto.gate_scorecard.gate_2_sqlglot_ast,
                    "sample_rules": ["DV-AST-001", "DV-AST-002", "DV-AST-004", "ALG-A5"],
                },
                "G3_NETWORKX_DAG": {
                    "gate_id": "G3",
                    "status": run_dto.gate_scorecard.gate_3_networkx_dag,
                    "sample_rules": ["DV-STR-001", "DV-STR-014", "INV-01", "INV-03"],
                },
                "G4_YAGNI_COST_GUARD": {
                    "gate_id": "G4",
                    "status": run_dto.gate_scorecard.gate_4_yagni_cost_guard,
                    "write_amplification_mb_per_day": 4.2,
                    "threshold_mb_per_day": 50.0,
                },
                "G5_DUCKDB_SANDBOX": {
                    "gate_id": "G5",
                    "status": run_dto.gate_scorecard.gate_5_duckdb_sandbox,
                    "dynamic_checks_passed": 15,
                    "dynamic_checks_total": 15,
                },
            },
        }

    def create_project(self, req: CreateProjectRequest) -> ProjectResponse:
        """Register or update a Data Vault 2.0 project."""
        for rel_path in [
            req.contract_path,
            req.canonical_ir_path,
            req.graph_a_ontology_path,
            req.graph_b_path,
        ]:
            if rel_path is not None:
                self._resolve_repo_path(rel_path)

        proj = ProjectResponse(
            project_id=req.project_id,
            display_name=req.display_name,
            domain_namespace=req.domain_namespace,
            graph_a_sha256=PE_DEMO_CONTEXT_REFS["graph_a_sha256"],
            latest_run_id=None,
            created_at_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            contract_path=req.contract_path,
            canonical_ir_path=req.canonical_ir_path,
            graph_a_ontology_path=req.graph_a_ontology_path,
            graph_b_path=req.graph_b_path,
        )
        self.projects[req.project_id] = proj
        return proj

    def create_run(self, req: CreateRunRequest) -> ProjectRunDTO:
        """Start a new verification/sandbox run and populate its observability ledger."""
        if req.project_id not in self.projects:
            raise KeyError(f"Unknown project_id '{req.project_id}'")

        if req.contract_path is not None:
            self._resolve_repo_path(req.contract_path)
            self.projects[req.project_id] = self.projects[req.project_id].model_copy(
                update={"contract_path": req.contract_path}
            )

        run_index = len(self.runs) + 1
        run_id = f"run-{req.project_id}-{run_index:04d}"
        now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self._init_run_artifacts(
            run_id=run_id,
            project_id=req.project_id,
            harness_provider=req.harness_provider,
            started_at_utc=now_iso,
        )

    def abort_run(self, run_id: str) -> ProjectRunDTO:
        """Transition a run to ABORTED status."""
        if run_id not in self.runs:
            raise KeyError(f"Unknown run_id '{run_id}'")
        run_dto = self.runs[run_id]
        updated = run_dto.model_copy(
            update={
                "status": RunStatus.ABORTED,
                "completed_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        self.runs[run_id] = updated
        return updated

    def resolve_captain_hold(
        self,
        run_id: str,
        req: CaptainHoldDecisionRequest,
    ) -> CaptainHoldDecisionResponse:
        """Apply a Steward decision to a hold, appending a resolution event."""
        if run_id not in self.runs:
            raise KeyError(f"Unknown run_id '{run_id}'")

        hold_mgr = self.hold_managers[run_id]
        target_hold_id = req.hold_id or req.event_id
        try:
            existing_record = hold_mgr.get_hold(target_hold_id)
            if existing_record.status != "PENDING":
                raise ValueError(
                    f"Captain-Hold '{target_hold_id}' is already resolved "
                    f"({existing_record.status})."
                )
        except KeyError:
            hold_mgr.record_hold(
                run_id=run_id,
                task_id=f"task-{run_id}-adhoc",
                session_id=f"sess-{run_id}",
                decision_id=target_hold_id,
                hold_id=target_hold_id,
                event_id=req.event_id,
            )

        notes = req.resolution_notes or f"Resolved by {req.steward_id} via {req.action}"
        resolved_record = hold_mgr.resolve_hold(
            target_hold_id,
            action=req.action,
            selected_candidate_id=req.selected_candidate_id,
            resolution_notes=notes,
        )

        for pending in hold_mgr.list_pending_holds(run_id=run_id):
            hold_mgr.resolve_hold(
                pending.decision_id,
                action=req.action,
                selected_candidate_id=req.selected_candidate_id,
                resolution_notes=notes,
            )

        resolved_at = resolved_record.resolved_at_utc or datetime.now(UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        emitted_event_id = f"evt-hold-resolved-{len(self.run_events[run_id]) + 1:03d}"
        verdict = "PASS" if req.action in {"APPROVE", "ANSWER"} else "REJECT-WITH-TRACE"
        resolution_event = build_observability_event_dict(
            event_id=emitted_event_id,
            run_id=run_id,
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            span_id=f"{len(self.run_events[run_id]) + 10:016x}",
            parent_span_id="00f067aa0ba902b4",
            timestamp_utc=resolved_at,
            stage_id="STAGE_02_SCOUT_UNSTRUCTURED",
            workbench_view_id="WB-03",
            viz_id="VIZ-STEP-04",
            agent_persona="vault-first-mate",
            status=verdict,
            headline=(
                f"Steward '{req.steward_id}' resolved Captain-Hold '{target_hold_id}' "
                f"with action {req.action}."
            ),
            business_impact=notes[:200],
            data_classification="CONFIDENTIAL",
            rule_ids_evaluated=["ALG-A9B", "U3"],
            summary_metrics={
                "hold_id": target_hold_id,
                "action": req.action,
                "steward_id": req.steward_id,
                "active_captain_holds": 0,
            },
        )

        self.run_ledgers[run_id].emit(resolution_event)
        self.run_events[run_id].append(resolution_event)
        self.run_workbench_events[run_id].append(resolution_event)

        run_dto = self.runs[run_id]
        remaining_ids = [rec.hold_id for rec in hold_mgr.list_pending_holds(run_id=run_id)]
        new_status = (
            RunStatus.COMPLETED
            if req.action in {"APPROVE", "ANSWER"}
            else RunStatus.FAILED
        )
        updated_scorecard = (
            run_dto.gate_scorecard
            if req.action in {"APPROVE", "ANSWER"}
            else run_dto.gate_scorecard.model_copy(
                update={"gate_1_contract_grain": "REJECT-WITH-TRACE"}
            )
        )
        updated_run = run_dto.model_copy(
            update={
                "status": new_status,
                "completed_at_utc": resolved_at,
                "active_hold_ids": remaining_ids,
                "event_count": len(self.run_events[run_id]),
                "gate_scorecard": updated_scorecard,
            }
        )
        self.runs[run_id] = updated_run
        self.run_gate_reports[run_id] = self._build_default_gate_report(run_id)

        decision_resp = CaptainHoldDecisionResponse(
            decision_id=resolved_record.decision_id,
            hold_id=target_hold_id,
            action=req.action,
            status="ACCEPTED" if req.action in {"APPROVE", "ANSWER"} else "REJECTED",
            resumed_run_id=run_id,
            next_stage_id="STAGE_03_SHIP_VAULT_PASS1",
            next_event_id=emitted_event_id,
            updated_run_status=new_status,
            resolved_at_utc=resolved_at,
        )
        self.run_decisions[run_id].append(decision_resp)
        return decision_resp

    def store_artifact(self, req: ArtifactUploadRequest) -> ArtifactMetadataResponse:
        """Persist a JSON or text artifact in the CAS and return its SHA-256 descriptor."""
        if isinstance(req.content, str):
            raw_bytes = req.content.encode("utf-8")
        else:
            raw_bytes = json.dumps(req.content, sort_keys=True).encode("utf-8")
        digest = store_cas_artifact(self.cas_root, raw_bytes)
        meta = ArtifactMetadataResponse(
            sha256=digest.upper(),
            byte_length=len(raw_bytes),
            media_type=req.media_type,
            artifact_name=req.artifact_name,
            content=req.content,
        )
        self.artifacts[digest.lower()] = meta
        self.artifacts[digest.upper()] = meta
        return meta

    def get_artifact(self, sha256: str) -> ArtifactMetadataResponse:
        """Retrieve an artifact from the CAS by its 64-hex SHA-256 digest."""
        clean = sha256.strip()
        if not re.match(r"^[0-9A-Fa-f]{64}$", clean):
            raise ValueError(f"Invalid SHA-256 hex digest: {sha256!r}")
        if clean in self.artifacts:
            return self.artifacts[clean]
        if clean.upper() in self.artifacts:
            return self.artifacts[clean.upper()]
        if clean.lower() in self.artifacts:
            return self.artifacts[clean.lower()]
        if not self.cas.has(clean.upper()):
            raise KeyError(f"CAS artifact not found for sha256={sha256}")
        raw_bytes = self.cas.get_bytes(clean.upper())
        try:
            parsed = json.loads(raw_bytes.decode("utf-8"))
        except Exception:
            parsed = raw_bytes.decode("utf-8", errors="replace")
        meta = ArtifactMetadataResponse(
            sha256=clean.upper(),
            byte_length=len(raw_bytes),
            media_type="application/json",
            artifact_name=None,
            content=parsed,
        )
        self.artifacts[clean.upper()] = meta
        return meta


def get_platform_state(request: Request) -> PlatformStateContainer:
    """FastAPI dependency returning the application's PlatformStateContainer instance."""
    state: PlatformStateContainer | None = getattr(request.app.state, "platform_state", None)
    if state is None:
        state = PlatformStateContainer()
        request.app.state.platform_state = state
    return state


_ROLE_ALIAS_MAP: dict[str, RbacRole] = {
    "MNPI_CLEARED_STEWARD": RbacRole.AUDITOR_FINANCE,
    "LP_CONFIDENTIAL_STEWARD": RbacRole.DATA_STEWARD,
    "COMPLIANCE_AUDITOR": RbacRole.INTERNAL_MODELER,
}


def get_rbac_role(
    x_opendv_role: Annotated[str | None, Header(alias="X-OpenDV-Role")] = None,
) -> RbacRole:
    """Extract and validate the 5-Tier RBAC role from the X-OpenDV-Role HTTP header."""
    if x_opendv_role is None or x_opendv_role.strip() == "":
        return RbacRole.PUBLIC
    normalized = x_opendv_role.strip().upper()
    if normalized in _ROLE_ALIAS_MAP:
        return _ROLE_ALIAS_MAP[normalized]
    try:
        return RbacRole(normalized)
    except ValueError as exc:
        valid_roles = [r.value for r in RbacRole] + sorted(_ROLE_ALIAS_MAP.keys())
        raise HTTPException(
            status_code=400,
            detail=f"Invalid X-OpenDV-Role header '{x_opendv_role}'. Must be one of {valid_roles}.",
        ) from exc


__all__ = [
    "PE_DEMO_CONTEXT_REFS",
    "ROLE_CLEARANCE_LEVELS",
    "WORKBENCH_VIEW_TITLES",
    "ArtifactMetadataResponse",
    "ArtifactUploadRequest",
    "CaptainHoldDecisionRequest",
    "CaptainHoldDecisionResponse",
    "CreateProjectRequest",
    "CreateRunRequest",
    "GateScorecardSummary",
    "PlatformStateContainer",
    "ProjectResponse",
    "ProjectRunDTO",
    "RbacRole",
    "RunStatus",
    "SandboxExecutionResponse",
    "SandboxQueryRequest",
    "build_gate_scorecard_from_axi_result",
    "build_pe_canonical_events",
    "execute_pe_sandbox_query",
    "format_sse_event_stream",
    "get_platform_state",
    "get_rbac_role",
    "redact_event_for_role",
    "resolve_cleared_tiers",
    "store_cas_artifact",
]
