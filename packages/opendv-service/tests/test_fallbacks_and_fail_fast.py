"""Tests for explicit opt-in fallback registry and fail-fast validation (Task P3.4 / #80)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from opendv_contracts import SandboxQueryRequest
from opendv_contracts.errors import ErrorCode, InputValidationError, NotFoundError
from opendv_service import (
    FALLBACK_REGISTRY,
    ConfigurationError,
    FallbackDefinition,
    FallbackKey,
    FallbackRecord,
    HoldDecisionRequest,
    OpenDVService,
    OpenDVSettings,
    PlatformStateContainer,
    get_fallback_definition,
    is_fallback_enabled,
)
from opendv_service.models import CreateProjectRequest, CreateRunRequest
from opendv_service.use_cases.sandbox import execute_sandbox_query
from opendv_testkit import seed_canonical_pe_demo


def test_fallback_registry_defaults_disabled() -> None:
    """Verify that every registered fallback definition has default_enabled == False."""
    assert len(FALLBACK_REGISTRY) >= 3
    for key, definition in FALLBACK_REGISTRY.items():
        assert isinstance(key, FallbackKey)
        assert isinstance(definition, FallbackDefinition)
        assert definition.default_enabled is False, (
            f"Fallback {key} must have default_enabled == False"
        )
        assert definition.description.strip()
        assert definition.valid_use_case.strip()

    # Query helpers
    for key in FallbackKey:
        defn = get_fallback_definition(key)
        assert defn.key == key
        assert is_fallback_enabled(key) is False

    with pytest.raises(KeyError):
        get_fallback_definition("NONEXISTENT_FALLBACK")  # type: ignore[arg-type]


def test_fallback_record_and_run_dto(tmp_path: Path) -> None:
    """Verify FallbackRecord structure and default empty fallbacks_applied on ProjectRunDTO."""
    record = FallbackRecord(
        key=FallbackKey.STAGE_OUTPUT_SYMBOLIC,
        applied_at_utc="2026-11-05T00:00:00Z",
        stage_id="STAGE_03_LOGICAL_RAW_VAULT",
        details={"reason": "Test opt-in fallback"},
    )
    assert record.key == FallbackKey.STAGE_OUTPUT_SYMBOLIC
    assert record.details["reason"] == "Test opt-in fallback"
    rec_dict = record.to_dict()
    assert rec_dict["key"] == str(FallbackKey.STAGE_OUTPUT_SYMBOLIC)

    store = PlatformStateContainer(storage_root=tmp_path / "state")
    service = OpenDVService(state_store=store, storage_root=tmp_path / "state")
    service.create_project(CreateProjectRequest(project_id="proj-p34", display_name="P34"))
    run_dto = service.create_run(
        CreateRunRequest(project_id="proj-p34", harness_provider="antigravity")
    )
    assert run_dto.fallbacks_applied == []


def test_project_without_ontology_has_none_sha256(tmp_path: Path) -> None:
    """Verify AUDIT-02: projects without ontology have graph_a_sha256 = None."""
    store = PlatformStateContainer(storage_root=tmp_path / "state")
    service = OpenDVService(state_store=store, storage_root=tmp_path / "state")

    proj = service.create_project(
        CreateProjectRequest(
            project_id="proj-no-ont",
            display_name="Project Without Ontology",
            graph_a_ontology_path=None,
        )
    )
    assert proj.graph_a_ontology_path is None
    assert proj.graph_a_sha256 is None


def test_execute_sandbox_query_fail_fast_missing_inputs() -> None:
    """Verify AUDIT-03: execute_sandbox_query raises InputValidationError on missing input."""
    req = SandboxQueryRequest(query_type="ADVERSARIAL_BATCHES_1_TO_4")
    with pytest.raises(InputValidationError) as exc_info:
        execute_sandbox_query(req)

    exc = exc_info.value
    assert exc.error_code == ErrorCode.INPUT_VALIDATION_ERROR
    assert exc.exit_code == 2
    assert exc.http_status == 422
    assert "Neither contract nor canonical IR was provided" in exc.message


def test_get_pipeline_report_fail_fast_unknown_run(tmp_path: Path) -> None:
    """Verify AUDIT-04: get_pipeline_report raises NotFoundError on unknown run."""
    store = PlatformStateContainer(storage_root=tmp_path / "state")
    service = OpenDVService(state_store=store, storage_root=tmp_path / "state")

    with pytest.raises(NotFoundError) as exc_info:
        service.get_pipeline_report("run-nonexistent-12345")

    exc = exc_info.value
    assert exc.error_code == ErrorCode.RESOURCE_NOT_FOUND
    assert exc.http_status == 404
    assert "not found" in exc.message.lower()


def test_resolve_captain_hold_unknown_hold_raises_not_found(tmp_path: Path) -> None:
    """Verify AUDIT-05: resolving unknown hold raises NotFoundError with HOLD_NOT_FOUND."""
    store = PlatformStateContainer(storage_root=tmp_path / "state")
    service = OpenDVService(state_store=store, storage_root=tmp_path / "state")

    service.create_project(CreateProjectRequest(project_id="proj-01", display_name="Project 01"))
    run = service.create_run(CreateRunRequest(project_id="proj-01", harness_provider="antigravity"))

    req = HoldDecisionRequest(
        hold_id="hold-nonexistent-9999",
        action="APPROVE",
        steward_id="steward-tester",
        resolution_notes="Approving unknown hold should fail fast",
    )
    with pytest.raises(NotFoundError) as exc_info:
        service.resolve_captain_hold(run.run_id, req)

    exc = exc_info.value
    assert exc.error_code == ErrorCode.HOLD_NOT_FOUND
    assert exc.http_status == 404
    assert "not found" in exc.message.lower()


def test_resolve_captain_hold_generates_dynamic_trace_id(tmp_path: Path) -> None:
    """Verify AUDIT-06: resolving a real hold emits an event with a UUIDv4 trace ID."""
    store = PlatformStateContainer(storage_root=tmp_path / "state")
    service = OpenDVService(state_store=store, storage_root=tmp_path / "state")
    seed_canonical_pe_demo(service)

    pe_run_id = "run-pe-apex-horizon-20261030"
    holds = service.list_holds(pe_run_id)
    assert len(holds) >= 1
    active_hold = holds[0]

    req = HoldDecisionRequest(
        hold_id=active_hold["hold_id"],
        action="APPROVE",
        steward_id="steward-tester",
        selected_candidate_id=active_hold.get("candidate_ids", ["cand-01"])[0],
        resolution_notes="Resolved in automated test",
    )
    resolved_resp = service.resolve_captain_hold(pe_run_id, req)
    assert resolved_resp.status == "ACCEPTED"

    # Inspect emitted resolution event in ledger
    events = service.state.run_events[pe_run_id]
    assert len(events) >= 1
    ev = events[-1]
    assert ev.get("event_id", "").startswith("evt-hold-resolved")
    trace_id = ev.get("trace_id")
    assert trace_id is not None
    assert trace_id != "4bf92f3577b34da6a3ce929d0e0e4736", "Static trace ID must not be used"
    # Ensure it parses as a valid 32-char UUID hex
    parsed_uuid = uuid.UUID(hex=trace_id)
    assert parsed_uuid.version == 4


def test_default_gate_scorecard_has_no_hardcoded_metrics(tmp_path: Path) -> None:
    """Verify AUDIT-07: default gate report does not inject hardcoded PE demo metrics."""
    store = PlatformStateContainer(storage_root=tmp_path / "state")
    service = OpenDVService(state_store=store, storage_root=tmp_path / "state")

    service.create_project(
        CreateProjectRequest(project_id="proj-clean", display_name="Clean Project")
    )
    run = service.create_run(
        CreateRunRequest(project_id="proj-clean", harness_provider="antigravity")
    )

    report = service.get_gate_report(run.run_id)
    metrics = report.get("summary_metrics", {})
    assert (
        "write_amplification_mb_per_day" not in metrics
        or metrics["write_amplification_mb_per_day"] is None
    )
    assert "dynamic_checks_passed" not in metrics or metrics["dynamic_checks_passed"] is None


def test_settings_load_null_workspace_root_fails_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify Action Item 8: config with workspace_root: null raises ConfigurationError."""
    monkeypatch.delenv("OPENDV_WORKSPACE_ROOT", raising=False)
    cfg_file = tmp_path / "opendv.config.yaml"
    cfg_file.write_text(
        "workspace_root: null\nstorage_root: null\nstate_dir: null\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError) as exc_info:
        OpenDVSettings.load(config_file=cfg_file, is_server=True)
    assert "Missing required configuration 'workspace_root'" in str(exc_info.value)
    assert "config file key 'workspace_root' (null)" in str(exc_info.value)
