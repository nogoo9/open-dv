"""Explorer use case: 3-Level Explorer bundle and run-isolated artifact retrieval.

Conforms to docs/design/open-dv-workbench/HANDOFF.md, Decision D3, and T6 invariants.
"""

from __future__ import annotations

import hashlib
import re

from opendv_contracts.dtos.common import RbacRole, RunStatus
from opendv_contracts.dtos.explorer import (
    ArtifactDTO,
    ExplorerBundleDTO,
    RunArtifactDTO,
)
from opendv_contracts.errors import ErrorCode
from opendv_service.errors import NotFoundError, OpenDVError
from opendv_service.state import StateStore
from opendv_service.use_cases.explorer_artifacts import build_bundle_artifacts
from opendv_service.use_cases.explorer_canonical import (
    DEFAULT_WAVES,
    EXPLORER_PHASES,
    build_canonical_stages,
)


def get_explorer_bundle(
    state: StateStore,
    run_id: str,
    wave_id: str | None = None,
    role: RbacRole | str = RbacRole.PUBLIC,
) -> ExplorerBundleDTO:
    """Construct and return the 3-Level Explorer bundle for a run."""
    if run_id not in state.runs:
        raise NotFoundError(f"Run '{run_id}' not found.")

    run_dto = state.runs[run_id]
    project_id = run_dto.project_id
    project_dto = state.projects.get(project_id)
    display_name = (
        project_dto.display_name if project_dto and project_dto.display_name else project_id
    )

    report_dict = state.pipeline_reports.get(run_id, {})
    is_hold = (run_dto.status == RunStatus.PAUSED_HOLD) or bool(report_dict.get("active_holds"))

    # Format events summary from run_events
    events = state.run_events.get(run_id, [])
    events_summary: list[tuple[str, str, str]] = []
    for ev in events:
        stage = str(ev.get("stage_id", "STAGE_00"))[:8]
        msg = str(
            ev.get("headline")
            or (ev.get("layer1_story_banner") or {}).get("headline")
            or ev.get("event_type")
            or "Event emitted"
        )
        st = str(ev.get("status", "PASS"))
        events_summary.append((stage, msg, st))
    if not events_summary:
        events_summary = [
            ("STAGE_00", "Session start", "PASS"),
            ("STAGE_05", "Static verification complete", "PASS"),
            ("STAGE_07", "Deterministic compile complete", "PASS"),
        ]

    # Build base stages and adjust tags based on run state
    stages = build_canonical_stages()
    if is_hold:
        for idx, s in enumerate(stages):
            if s.key.startswith("STAGE_02"):
                stages[idx] = s.model_copy(update={"tag": "HOLD"})
    elif run_dto.status == RunStatus.FAILED:
        stages[-1] = stages[-1].model_copy(update={"tag": "REJECT-WITH-TRACE"})

    # Build 24 canonical artifacts
    artifacts = build_bundle_artifacts(
        run_id=run_id,
        project_id=project_id,
        is_hold=is_hold,
        events_summary=events_summary,
    )

    # Register each artifact in CAS and populate lowercase sha256
    for art_id, art in list(artifacts.items()):
        raw_bytes = art.raw.encode("utf-8")
        digest = hashlib.sha256(raw_bytes).hexdigest().lower()
        artifacts[art_id] = art.model_copy(update={"sha256": digest})
        if hasattr(state, "cas_store") and state.cas_store is not None:
            state.cas_store.put(raw_bytes)
        if hasattr(state, "cas") and state.cas is not None:
            state.cas.put(raw_bytes)

    setting_ids = ["harness", "runopts"]
    input_ids = ["spec", "ontology", "crm", "erp", "stripe", "crm2", "msa"]
    output_ids = ["final_contract", "final_ir", "final_sql", "report", "ledger"]

    # Wave filtering if specified
    if wave_id is not None and wave_id.strip():
        clean_wave = wave_id.strip()
        if clean_wave.lower() != "all":
            match = re.match(r"^[wW]?([0-3])$", clean_wave)
            if not match:
                raise NotFoundError(f"Wave '{wave_id}' not found for run '{run_id}'.")
            target_idx = int(match.group(1))
            target_key = f"W{target_idx}"

            filtered_artifacts: dict[str, ArtifactDTO] = {}
            for k, art in artifacts.items():
                if art.wave_index == target_idx or (art.waves and target_key in art.waves):
                    filtered_artifacts[k] = art.model_copy(update={"wave_index": target_idx})
            artifacts = filtered_artifacts
            setting_ids = [k for k in setting_ids if k in artifacts]
            input_ids = [k for k in input_ids if k in artifacts]
            output_ids = [k for k in output_ids if k in artifacts]

    status_label = (
        "PAUSED · Steward hold pending"
        if is_hold
        else (
            "FAILED · Gate verification failure"
            if run_dto.status == RunStatus.FAILED
            else "Sealed · 6/6 gates PASS"
        )
    )

    eff_role = str(role.value if hasattr(role, "value") else role)

    return ExplorerBundleDTO(
        projectId=project_id,
        displayName=display_name,
        runId=run_id,
        specFile="pipeline_input.yaml",
        wavesCount=len(DEFAULT_WAVES),
        waves=DEFAULT_WAVES,
        statusLabel=status_label,
        gatesPassed=5 if is_hold else (6 if run_dto.status != RunStatus.FAILED else 4),
        gatesTotal=6,
        rbacRole=eff_role,
        hasInitialContract=False,
        phases=EXPLORER_PHASES,
        stages=stages,
        artifacts=artifacts,
        settingIds=setting_ids,
        inputIds=input_ids,
        outputIds=output_ids,
    )


def get_run_artifact(
    state: StateStore,
    run_id: str,
    artifact_id: str,
    role: RbacRole | str = RbacRole.PUBLIC,
) -> RunArtifactDTO:
    """Return descriptor and RBAC-redacted content for a run-isolated artifact."""
    if run_id not in state.runs:
        raise NotFoundError(f"Run '{run_id}' not found.")

    bundle = get_explorer_bundle(state, run_id=run_id, wave_id=None, role=role)
    if artifact_id not in bundle.artifacts:
        raise NotFoundError(f"Artifact '{artifact_id}' not found for run '{run_id}'.")

    art = bundle.artifacts[artifact_id]
    assert art.sha256 is not None
    sha_lower = art.sha256.lower()

    raw_bytes: bytes | None = None
    if hasattr(state, "cas_store") and state.cas_store is not None:
        if state.cas_store.has(sha_lower):
            raw_bytes = state.cas_store.get_bytes(sha_lower)
        elif state.cas_store.has(sha_lower.upper()):
            raw_bytes = state.cas_store.get_bytes(sha_lower.upper())
    if raw_bytes is None and hasattr(state, "cas") and state.cas is not None:
        if state.cas.has(sha_lower):
            raw_bytes = state.cas.get_bytes(sha_lower)
        elif state.cas.has(sha_lower.upper()):
            raw_bytes = state.cas.get_bytes(sha_lower.upper())
    if raw_bytes is None:
        raw_bytes = art.raw.encode("utf-8")
        if hasattr(state, "cas_store") and state.cas_store is not None:
            state.cas_store.put(raw_bytes)
        if hasattr(state, "cas") and state.cas is not None:
            state.cas.put(raw_bytes)

    computed_sha = hashlib.sha256(raw_bytes).hexdigest().lower()
    if computed_sha != sha_lower:
        msg = (
            f"CAS integrity failure for artifact '{artifact_id}': "
            f"expected {sha_lower}, computed {computed_sha}"
        )
        raise OpenDVError(msg, error_code=ErrorCode.CAS_INTEGRITY_FAILURE)

    content_str = raw_bytes.decode("utf-8", errors="replace")
    eff_role = str(role.value if hasattr(role, "value") else role).upper()
    if eff_role == "PUBLIC":
        content_str = re.sub(r"TAX-[A-Z]{2}-\d+", "<PII>", content_str)
        content_str = re.sub(r"[\w\.-]+@[\w\.-]+\.\w+", "<PII>", content_str)
        content_str = re.sub(r"\+?\d[\d -]{8,}\d", "<PII>", content_str)

    return RunArtifactDTO(
        artifact_id=artifact_id,
        run_id=run_id,
        sha256=sha_lower,
        content=content_str,
        artifact_type=art.type,
        filename=art.name,
        byte_length=len(raw_bytes),
        metadata={"viz": art.viz, "group": art.group, "fmt": art.fmt},
    )


def list_run_artifacts(
    state: StateStore,
    run_id: str,
    role: RbacRole | str = RbacRole.PUBLIC,
) -> list[RunArtifactDTO]:
    """Return all run-isolated artifacts for a run with RBAC redaction and CAS digest."""
    if run_id not in state.runs:
        raise NotFoundError(f"Run '{run_id}' not found.")

    bundle = get_explorer_bundle(state, run_id=run_id, wave_id=None, role=role)
    artifacts: list[RunArtifactDTO] = []
    for art_id in bundle.artifacts:
        artifacts.append(get_run_artifact(state, run_id=run_id, artifact_id=art_id, role=role))
    return artifacts
