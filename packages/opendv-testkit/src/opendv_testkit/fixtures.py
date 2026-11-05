"""Canonical and ephemeral test fixtures for Open-DV test suites.

Provides `seed_canonical_pe_demo(state)` to explicitly populate test containers with the
Apex Horizon PE Buyout Vault v5.0 project, CAS artifacts, and canonical events, as well as
ephemeral project and run factory helpers.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from opendv_agent_harness.hold_manager import StewardHoldManager
from opendv_service.models import (
    ArtifactMetadataResponse,
    CreateProjectRequest,
    CreateRunRequest,
    GateScorecardSummary,
    ProjectResponse,
    ProjectRunDTO,
    RunStatus,
)
from opendv_service.state.cas import store_cas_artifact
from opendv_telemetry import VaultLedgerEmitter
from opendv_testkit.pe_canonical_events import (
    PE_DEMO_CONTEXT_REFS,
    WORKBENCH_VIEW_TITLES,
    build_pe_canonical_events,
)


def seed_canonical_pe_demo(
    state: Any,
    project_id: str = "proj-apex-horizon-pe",
    run_id: str = "run-pe-apex-horizon-20261030",
) -> None:
    """Explicitly seed canonical PE project, CAS artifacts, and run events into `state`.

    `state` may be a `PlatformStateContainer`, `StateStore`, or `OpenDVService` instance.
    """
    container = getattr(state, "state", state)
    repo_root: Path = getattr(container, "repo_root", Path.cwd())

    if project_id not in container.projects:
        container.projects[project_id] = ProjectResponse(
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
    else:
        container.projects[project_id] = container.projects[project_id].model_copy(
            update={"latest_run_id": run_id}
        )

    seed_files: list[tuple[str, str, Path]] = [
        (
            "graph_a_sha256",
            "application/yaml",
            repo_root / "examples" / "private_equity" / "pe_graph_a_ontology.yaml",
        ),
        (
            "graph_b_sha256",
            "application/json",
            repo_root / "examples" / "private_equity" / "pe_graph_b_profile.json",
        ),
        (
            "contract_yaml_pre_sha256",
            "application/yaml",
            repo_root / "examples" / "private_equity" / "dv_pe_model_contract.yaml",
        ),
        (
            "contract_yaml_post_sha256",
            "application/yaml",
            repo_root / "examples" / "private_equity" / "dv_pe_model_contract.yaml",
        ),
        (
            "canonical_ir_sha256",
            "application/json",
            repo_root / "examples" / "private_equity" / "pe_canonical_ir.json",
        ),
    ]

    for ref_key, media_type, file_path in seed_files:
        if not file_path.is_file():
            continue
        raw_text = file_path.read_text(encoding="utf-8")
        raw_bytes = raw_text.encode("utf-8")
        actual_sha = store_cas_artifact(container.cas_root, raw_bytes)
        canonical_sha = PE_DEMO_CONTEXT_REFS[ref_key].upper()
        parsed_content: Any
        if media_type == "application/json":
            parsed_content = json.loads(raw_text)
        else:
            parsed_content = yaml.safe_load(raw_text)

        for sha_key in {actual_sha, actual_sha.lower(), canonical_sha, canonical_sha.lower()}:
            container.artifacts[sha_key] = ArtifactMetadataResponse(
                sha256=sha_key.upper(),
                byte_length=len(raw_bytes),
                media_type=media_type,
                artifact_name=file_path.name,
                content=parsed_content,
            )

    # Initialize ledger and canonical PE events
    canonical_9, all_workbench = build_pe_canonical_events(run_id=run_id)
    ledger_path = container.ledger_dir / f"{run_id}.jsonl"
    ledger_path.unlink(missing_ok=True)
    ledger = VaultLedgerEmitter(ledger_path=ledger_path, cas_store=container.cas)
    for evt in canonical_9:
        ledger.emit(evt)
    container.run_ledgers[run_id] = ledger

    # Record canonical Tier-U3 hold
    shutil.rmtree(container.state_dir / run_id / "holds", ignore_errors=True)
    hold_mgr = StewardHoldManager(state_dir=container.state_dir / run_id)
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
    container.hold_managers[run_id] = hold_mgr
    container.run_events[run_id] = canonical_9
    container.run_workbench_events[run_id] = all_workbench

    by_view: dict[str, list[dict[str, Any]]] = {f"WB-{i:02d}": [] for i in range(12)}
    for evt in [*canonical_9, *all_workbench]:
        vw = str(evt.get("workbench_view_id") or "").strip().upper()[:5]
        if vw in by_view:
            by_view[vw].append(evt)
    container.run_events_by_view[run_id] = by_view

    run_dto = ProjectRunDTO(
        run_id=run_id,
        project_id=project_id,
        harness_provider="antigravity",
        status=RunStatus.PAUSED_HOLD,
        started_at_utc="2026-10-30T09:00:00Z",
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
    container.runs[run_id] = run_dto
    if hasattr(container, "_build_default_gate_report"):
        container.run_gate_reports[run_id] = container._build_default_gate_report(run_id)
    container.run_sandbox_history[run_id] = []
    container.run_decisions[run_id] = []
    if project_id in container.projects:
        container.projects[project_id] = container.projects[project_id].model_copy(
            update={"latest_run_id": run_id}
        )


def create_ephemeral_project(
    state_or_service: Any,
    project_id: str | None = None,
    *,
    display_name: str | None = None,
    domain_namespace: str = "general_dv",
    **kwargs: Any,
) -> ProjectResponse:
    """Create an ephemeral project in the provided state or service via public request DTO."""
    container = getattr(state_or_service, "state", state_or_service)
    pid = project_id or f"proj-ephemeral-{uuid.uuid4().hex[:8]}"
    dname = display_name or f"Ephemeral Project ({pid})"
    req = CreateProjectRequest(
        project_id=pid,
        display_name=dname,
        domain_namespace=domain_namespace,
        **kwargs,
    )
    if hasattr(container, "create_project"):
        return container.create_project(req)
    # direct state injection fallback
    proj = ProjectResponse(
        project_id=pid,
        display_name=dname,
        domain_namespace=domain_namespace,
        created_at_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **kwargs,
    )
    container.projects[pid] = proj
    return proj


def create_ephemeral_run(
    state_or_service: Any,
    project_id: str,
    *,
    harness_provider: str = "antigravity",
    **kwargs: Any,
) -> ProjectRunDTO:
    """Create an ephemeral run in the provided state or service via public request DTO."""
    container = getattr(state_or_service, "state", state_or_service)
    req = CreateRunRequest(
        project_id=project_id,
        harness_provider=harness_provider,
        **kwargs,
    )
    if hasattr(container, "create_run"):
        return container.create_run(req)
    run_id = f"run-{project_id}-{uuid.uuid4().hex[:6]}"
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return container._init_run_artifacts(
        run_id=run_id,
        project_id=project_id,
        harness_provider=harness_provider,
        started_at_utc=now_iso,
    )


__all__ = [
    "PE_DEMO_CONTEXT_REFS",
    "WORKBENCH_VIEW_TITLES",
    "build_pe_canonical_events",
    "create_ephemeral_project",
    "create_ephemeral_run",
    "seed_canonical_pe_demo",
]
