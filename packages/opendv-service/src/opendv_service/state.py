"""Domain StateStore and CAS utilities for opendv-service (decoupled from web framework)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from opendv_agent_harness import (
    AgenticPipelineReport,
    DeterministicValidationReport,
    HarnessSetupConfig,
    HarnessStreamEvent,
    PipelineInputSpec,
    PipelineStepReport,
    StewardHoldManager,
    build_observability_event_dict,
    validate_pipeline_inputs,
)
from opendv_core import (
    SchemaRegistry,
    find_workspace_root,
    get_default_schema_registry,
)
from opendv_core.paths import PathContainmentError, resolve_workspace_path
from opendv_service.legacy_demo import (
    PE_DEMO_CONTEXT_REFS,
    WORKBENCH_VIEW_TITLES,
    build_pe_canonical_events,
)
from opendv_service.models import (
    ArtifactMetadataResponse,
    ArtifactUploadRequest,
    CaptainHoldDecisionRequest,
    CaptainHoldDecisionResponse,
    CreateProjectRequest,
    CreateRunRequest,
    GateScorecardSummary,
    GateVerdictStatus,
    PipelineRunRequest,
    PipelineValidateRequest,
    ProjectResponse,
    ProjectRunDTO,
    RunStatus,
    SandboxExecutionResponse,
)
from opendv_service.rbac import (
    RbacRole,
    normalize_rbac_role,
    redact_event_for_role,
)
from opendv_service.seams import PipelineRunner, get_default_pipeline_runner
from opendv_telemetry import (
    ContentAddressableStore,
    VaultLedgerEmitter,
)

REPO_ROOT = find_workspace_root()


def resolve_safe_repo_path(
    first: Path | str,
    second: str | Path | None = None,
    *,
    repo_root: Path | None = None,
) -> Path:
    """Resolve a repository-relative path and enforce repository-root containment.

    Uses canonical resolve_workspace_path.
    """
    if second is not None:
        root = Path(first).resolve()
        raw_rel = str(second)
    else:
        root = (repo_root or REPO_ROOT).resolve()
        raw_rel = str(first)

    try:
        return resolve_workspace_path(raw_rel, workspace_root=root, allow_temp=True)
    except PathContainmentError as exc:
        raise ValueError(str(exc)) from exc


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


def _load_structured_mapping(path: Path, label: str) -> dict[str, Any]:
    """Load a YAML or JSON mapping from `path`, raising `ValueError` on syntax or type errors."""
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Failed to parse {label} file '{path}': {exc}") from exc


class PlatformStateContainer:
    """In-memory and CAS/JSONL-backed platform state container for `/api/v1` routers."""

    def __init__(
        self,
        repo_root: Path | None = None,
        state_dir: Path | None = None,
        *,
        pipeline_runner: PipelineRunner | None = None,
        seed_demo_project: bool = True,
    ) -> None:
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
        self.cas_store = self.cas
        self.ledger_dir = self.state_dir / "ledgers"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)

        self.schema_registry: SchemaRegistry = get_default_schema_registry()

        self.projects: dict[str, ProjectResponse] = {}
        self.project_bronze_sources: dict[str, list[str]] = {}
        self.runs: dict[str, ProjectRunDTO] = {}
        self.run_events: dict[str, list[dict[str, Any]]] = {}
        self.run_workbench_events: dict[str, list[dict[str, Any]]] = {}
        self.run_events_by_view: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self.run_ledgers: dict[str, VaultLedgerEmitter] = {}
        self.hold_managers: dict[str, StewardHoldManager] = {}
        self.run_gate_reports: dict[str, dict[str, Any]] = {}
        self.gate_reports: dict[str, dict[str, Any]] = self.run_gate_reports
        self.run_sandbox_history: dict[str, list[SandboxExecutionResponse]] = {}
        self.run_decisions: dict[str, list[CaptainHoldDecisionResponse]] = {}
        self.artifacts: dict[str, ArtifactMetadataResponse] = {}
        self.pipeline_reports: dict[str, dict[str, Any]] = {}
        self._pipeline_run_seq: int = 0

        if seed_demo_project:
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
            "agy",
            "agy-cli",
            "antigravity",
            "claude",
            "claude-cli",
            "claude-code",
        ],
        started_at_utc: str,
    ) -> ProjectRunDTO:
        proj = self.projects.get(project_id)
        if project_id == "proj-apex-horizon-pe" or (
            proj is not None and proj.contract_path is not None
        ):
            canonical_9, all_workbench = build_pe_canonical_events(run_id=run_id)
        else:
            canonical_9, all_workbench = [], []
        ledger_path = self.ledger_dir / f"{run_id}.jsonl"
        ledger_path.unlink(missing_ok=True)
        ledger = VaultLedgerEmitter(
            ledger_path=ledger_path,
            cas_store=self.cas,
        )
        for evt in canonical_9:
            ledger.emit(evt)

        shutil.rmtree(self.state_dir / run_id / "holds", ignore_errors=True)
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
        by_view: dict[str, list[dict[str, Any]]] = {f"WB-{i:02d}": [] for i in range(12)}
        for evt in [*canonical_9, *all_workbench]:
            vw = str(evt.get("workbench_view_id") or "").strip().upper()[:5]
            if vw in by_view:
                by_view[vw].append(evt)
        self.run_events_by_view[run_id] = by_view

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
        return resolve_safe_repo_path(self.repo_root, rel_path)

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
            *req.bronze_sources,
        ]:
            if rel_path is not None:
                self._resolve_repo_path(rel_path)

        graph_a_sha = PE_DEMO_CONTEXT_REFS["graph_a_sha256"]
        if req.graph_a_ontology_path is not None:
            resolved_ont = self._resolve_repo_path(req.graph_a_ontology_path)
            if resolved_ont.is_file():
                raw_bytes = resolved_ont.read_bytes()
                graph_a_sha = store_cas_artifact(self.cas_root, raw_bytes).upper()
                try:
                    parsed_ont: Any = yaml.safe_load(raw_bytes.decode("utf-8"))
                except Exception:
                    parsed_ont = raw_bytes.decode("utf-8", errors="replace")
                art_meta = ArtifactMetadataResponse(
                    sha256=graph_a_sha,
                    byte_length=len(raw_bytes),
                    media_type="application/yaml",
                    artifact_name=resolved_ont.name,
                    content=parsed_ont,
                )
                self.artifacts[graph_a_sha] = art_meta
                self.artifacts[graph_a_sha.lower()] = art_meta

        self.project_bronze_sources[req.project_id] = list(req.bronze_sources)
        proj = ProjectResponse(
            project_id=req.project_id,
            display_name=req.display_name,
            domain_namespace=req.domain_namespace,
            graph_a_sha256=graph_a_sha,
            latest_run_id=None,
            created_at_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            contract_path=req.contract_path,
            canonical_ir_path=req.canonical_ir_path,
            graph_a_ontology_path=req.graph_a_ontology_path,
            graph_b_path=req.graph_b_path,
        )
        self.projects[req.project_id] = proj
        return proj

    def _resolve_pipeline_raw_inputs(
        self,
        req: PipelineValidateRequest,
    ) -> tuple[dict[str, Any], dict[str, Any], Path]:
        """Resolve raw `input_spec` and `harness_config` mappings and `base_dir` from `req`."""
        input_spec_path = (
            req.input_spec_path.strip()
            if req.input_spec_path is not None and req.input_spec_path.strip()
            else None
        )
        harness_config_path = (
            req.harness_config_path.strip()
            if req.harness_config_path is not None and req.harness_config_path.strip()
            else None
        )
        initial_contract_path = (
            req.initial_contract_path.strip()
            if req.initial_contract_path is not None and req.initial_contract_path.strip()
            else None
        )
        provider_override = (
            req.provider_override.strip()
            if req.provider_override is not None and req.provider_override.strip()
            else None
        )
        steward_hold_policy = (
            req.steward_hold_policy.strip()
            if req.steward_hold_policy is not None and req.steward_hold_policy.strip()
            else None
        )

        if req.input_spec is None and input_spec_path is None:
            raise ValueError(
                "Either 'input_spec' or 'input_spec_path' must be provided "
                "(zero hardcoded sample paths)."
            )

        if input_spec_path is not None:
            resolved_spec_path = resolve_safe_repo_path(self.repo_root, input_spec_path)
            if not resolved_spec_path.is_file():
                raise FileNotFoundError(f"Pipeline input spec file not found: '{input_spec_path}'.")
            base_dir = resolved_spec_path.parent
            raw_spec = _load_structured_mapping(resolved_spec_path, "pipeline input spec")
        else:
            assert req.input_spec is not None
            base_dir = self.repo_root
            raw_spec = dict(req.input_spec)

        if initial_contract_path is not None:
            resolved_init_path = resolve_safe_repo_path(self.repo_root, initial_contract_path)
            raw_spec["initial_contract_path"] = str(resolved_init_path)

        if harness_config_path is not None:
            resolved_cfg_path = resolve_safe_repo_path(self.repo_root, harness_config_path)
            if not resolved_cfg_path.is_file():
                raise FileNotFoundError(f"Harness config file not found: '{harness_config_path}'.")
            raw_setup = _load_structured_mapping(resolved_cfg_path, "harness config")
        elif req.harness_config is not None:
            raw_setup = dict(req.harness_config)
        else:
            raw_setup = {}

        if provider_override is not None:
            raw_setup["provider"] = provider_override
        if steward_hold_policy is not None:
            raw_setup["steward_hold_policy"] = steward_hold_policy

        return raw_spec, raw_setup, base_dir

    def _resolve_pipeline_inputs(
        self,
        req: PipelineValidateRequest,
    ) -> tuple[PipelineInputSpec, HarnessSetupConfig, Path]:
        """Resolve `PipelineInputSpec`, `HarnessSetupConfig`, and `base_dir` from `req`."""
        try:
            raw_spec, raw_setup, base_dir = self._resolve_pipeline_raw_inputs(req)
            spec_obj = PipelineInputSpec.model_validate(raw_spec)
            setup_obj = (
                HarnessSetupConfig.model_validate(raw_setup) if raw_setup else HarnessSetupConfig()
            )
        except (FileNotFoundError, ValidationError) as exc:
            raise ValueError(str(exc)) from exc
        return spec_obj, setup_obj, base_dir

    def validate_agentic_pipeline(self, req: PipelineValidateRequest) -> dict[str, Any]:
        """Deterministically validate pipeline inputs and harness setup configuration."""
        input_spec_path = (
            req.input_spec_path.strip()
            if req.input_spec_path is not None and req.input_spec_path.strip()
            else None
        )
        harness_config_path = (
            req.harness_config_path.strip()
            if req.harness_config_path is not None and req.harness_config_path.strip()
            else None
        )
        if req.input_spec is None and input_spec_path is None:
            raise ValueError(
                "Either 'input_spec' or 'input_spec_path' must be provided "
                "(zero hardcoded sample paths)."
            )
        if input_spec_path is not None:
            resolve_safe_repo_path(self.repo_root, input_spec_path)
        if harness_config_path is not None:
            resolve_safe_repo_path(self.repo_root, harness_config_path)
        if req.initial_contract_path is not None and req.initial_contract_path.strip():
            resolve_safe_repo_path(self.repo_root, req.initial_contract_path.strip())

        try:
            raw_spec, raw_setup, base_dir = self._resolve_pipeline_raw_inputs(req)
        except (FileNotFoundError, ValueError) as exc:
            msg = str(exc)
            is_harness_err = "harness config" in msg.lower()
            val_report = DeterministicValidationReport(
                stage="INPUT_VALIDATION",
                passed=False,
                checked_items_count=2 if is_harness_err else 1,
                diagnostics=[
                    {
                        "code": (
                            "HARNESS_SETUP_INVALID"
                            if is_harness_err
                            else "INPUT_SPEC_SCHEMA_INVALID"
                        ),
                        "severity": "ERROR",
                        "target": ("harness_config_path" if is_harness_err else "input_spec_path"),
                        "message": msg,
                    }
                ],
            )
            return val_report.model_dump(mode="json")

        val_report = validate_pipeline_inputs(
            raw_spec,
            raw_setup,
            base_dir=base_dir,
        )
        return val_report.model_dump(mode="json")

    def execute_agentic_pipeline_run(
        self,
        req: PipelineRunRequest,
        role: RbacRole | str = RbacRole.PUBLIC,
        *,
        pipeline_runner: PipelineRunner | None = None,
        on_stage_start: Callable[[int, str, str, str], None] | None = None,
        on_stage_complete: Callable[[int, PipelineStepReport], None] | None = None,
        on_stream_event: Callable[[HarnessStreamEvent], None] | None = None,
    ) -> dict[str, Any]:
        """Execute an end-to-end 8-stage agentic pipeline run and register state in platform."""
        eff_role = normalize_rbac_role(role, default=RbacRole.PUBLIC)
        spec_obj, setup_obj, base_dir = self._resolve_pipeline_inputs(req)

        output_dir_str = (
            req.output_dir.strip()
            if req.output_dir is not None and req.output_dir.strip()
            else None
        )
        if output_dir_str is not None:
            out_dir: Path | None = resolve_safe_repo_path(self.repo_root, output_dir_str)
        elif spec_obj.output_dir is not None:
            out_dir = None
        else:
            self._pipeline_run_seq += 1
            out_dir = self.state_dir / "pipeline_runs" / f"run_{self._pipeline_run_seq:04d}"

        if out_dir is not None:
            shutil.rmtree(out_dir / "state" / "holds", ignore_errors=True)
            (out_dir / "vault-ledger.jsonl").unlink(missing_ok=True)
        elif spec_obj.output_dir is not None:
            spec_out_cand = Path(spec_obj.output_dir)
            if not spec_out_cand.is_absolute():
                spec_out_cand = (base_dir / spec_out_cand).resolve()
            shutil.rmtree(spec_out_cand / "state" / "holds", ignore_errors=True)
            (spec_out_cand / "vault-ledger.jsonl").unlink(missing_ok=True)

        runner: PipelineRunner = pipeline_runner or get_default_pipeline_runner()
        report: AgenticPipelineReport = runner(
            spec_obj,
            setup_obj,
            output_dir=out_dir,
            base_dir=base_dir,
            cas_store=self.cas_store,
            on_stage_start=on_stage_start,
            on_stage_complete=on_stage_complete,
            on_stream_event=on_stream_event,
        )

        # Store all intermediate and final artifacts in self.cas_store and self.artifacts
        for ref in [*report.intermediate_artifacts, *report.final_artifacts.values()]:
            raw_bytes: bytes | None = None
            if ref.file_path and Path(ref.file_path).is_file():
                raw_bytes = Path(ref.file_path).read_bytes()
                self.cas_store.put(raw_bytes)
            elif self.cas_store.has(ref.sha256):
                raw_bytes = self.cas_store.get_bytes(ref.sha256)

            if raw_bytes is not None:
                parsed_content: Any
                try:
                    text_val = raw_bytes.decode("utf-8")
                    if ref.media_type == "application/json":
                        parsed_content = json.loads(text_val)
                    elif ref.media_type in {"application/yaml", "application/x-yaml"}:
                        parsed_content = yaml.safe_load(text_val)
                    else:
                        parsed_content = text_val
                except Exception:
                    parsed_content = raw_bytes.decode("utf-8", errors="replace")

                meta = ArtifactMetadataResponse(
                    sha256=ref.sha256.upper(),
                    byte_length=ref.byte_length,
                    media_type=ref.media_type,
                    artifact_name=ref.name,
                    content=parsed_content,
                )
                self.artifacts[ref.sha256.upper()] = meta
                self.artifacts[ref.sha256.lower()] = meta

        ont_art_ref = next(
            (
                a
                for a in report.intermediate_artifacts
                if a.artifact_id == "art-stage00-graph-a-ontology"
            ),
            None,
        )
        ont_sha = (
            ont_art_ref.sha256.upper()
            if ont_art_ref is not None
            else PE_DEMO_CONTEXT_REFS["graph_a_sha256"]
        )
        last_gb_ref = next(
            (
                a
                for a in reversed(report.intermediate_artifacts)
                if a.artifact_id.endswith("-stage01-graph-b-profile") and a.file_path
            ),
            None,
        )
        raw_ont_path = Path(spec_obj.graph_a_ontology_path)
        if raw_ont_path.is_absolute():
            cand_ont = raw_ont_path.resolve()
        else:
            cand_base_ont = (base_dir / raw_ont_path).resolve()
            cand_repo_ont = (self.repo_root / raw_ont_path).resolve()
            cand_ont = cand_base_ont if cand_base_ont.exists() else cand_repo_ont
        if cand_ont.exists() and cand_ont.is_relative_to(self.repo_root):
            resolved_graph_a_path = str(cand_ont.relative_to(self.repo_root))
        elif ont_art_ref is not None and ont_art_ref.file_path:
            resolved_graph_a_path = ont_art_ref.file_path
        elif cand_ont.is_relative_to(self.repo_root):
            resolved_graph_a_path = str(cand_ont.relative_to(self.repo_root))
        else:
            resolved_graph_a_path = str(cand_ont)

        final_contract_ref = report.final_artifacts.get("final_contract")
        final_ir_ref = report.final_artifacts.get("final_canonical_ir")

        if spec_obj.project_id not in self.projects:
            self.projects[spec_obj.project_id] = ProjectResponse(
                project_id=spec_obj.project_id,
                display_name=spec_obj.display_name,
                domain_namespace=spec_obj.domain_namespace,
                graph_a_sha256=ont_sha,
                latest_run_id=report.run_id,
                created_at_utc=report.started_at,
                contract_path=final_contract_ref.file_path if final_contract_ref else None,
                canonical_ir_path=final_ir_ref.file_path if final_ir_ref else None,
                graph_a_ontology_path=resolved_graph_a_path,
                graph_b_path=last_gb_ref.file_path if last_gb_ref else None,
            )
        else:
            existing_proj = self.projects[spec_obj.project_id]
            proj_updates: dict[str, Any] = {
                "latest_run_id": report.run_id,
                "graph_a_sha256": ont_sha,
                "graph_a_ontology_path": resolved_graph_a_path,
                "contract_path": (
                    final_contract_ref.file_path
                    if final_contract_ref and final_contract_ref.file_path
                    else existing_proj.contract_path
                ),
                "canonical_ir_path": (
                    final_ir_ref.file_path
                    if final_ir_ref and final_ir_ref.file_path
                    else existing_proj.canonical_ir_path
                ),
                "graph_b_path": (
                    last_gb_ref.file_path
                    if last_gb_ref and last_gb_ref.file_path
                    else existing_proj.graph_b_path
                ),
            }
            self.projects[spec_obj.project_id] = existing_proj.model_copy(update=proj_updates)

        events_list = list(report.observability_events)
        self.run_events[report.run_id] = events_list
        by_view: dict[str, list[dict[str, Any]]] = {f"WB-{i:02d}": [] for i in range(12)}
        for evt in events_list:
            vw = str(evt.get("workbench_view_id") or "").strip().upper()[:5]
            if vw in by_view:
                by_view[vw].append(evt)

        workbench_events_list = list(events_list)
        trace_id = (
            str(events_list[0].get("trace_id"))
            if events_list
            else "4bf92f3577b34da6a3ce929d0e0e4736"
        )
        for idx in range(12):
            vw_code = f"WB-{idx:02d}"
            if not by_view[vw_code]:
                synth_evt = build_observability_event_dict(
                    event_id=f"evt-{report.run_id}-{vw_code.lower()}-synth",
                    run_id=report.run_id,
                    trace_id=trace_id,
                    span_id=f"{idx + 100:016x}",
                    parent_span_id=None,
                    timestamp_utc=report.completed_at,
                    stage_id="STAGE_00_ORCHESTRATION",
                    workbench_view_id=vw_code,
                    viz_id="VIZ-PIPE-00",
                    agent_persona="vault-first-mate",
                    status="PASS" if report.overall_passed else "REJECT-WITH-TRACE",
                    headline=f"{WORKBENCH_VIEW_TITLES[vw_code]} projection ready.",
                    business_impact=(
                        f"Deterministic projection for {vw_code} in "
                        f"pipeline '{report.pipeline_id}'."
                    ),
                    data_classification="INTERNAL",
                    rule_ids_evaluated=["HR-01"],
                    summary_metrics={"wave_count": len(report.wave_reports)},
                )
                by_view[vw_code].append(synth_evt)
                workbench_events_list.append(synth_evt)

        self.run_events_by_view[report.run_id] = by_view
        self.run_workbench_events[report.run_id] = workbench_events_list

        ledger_file = self.ledger_dir / f"{report.run_id}.jsonl"
        ledger_file.unlink(missing_ok=True)
        ledger = VaultLedgerEmitter(
            ledger_path=ledger_file,
            cas_store=self.cas,
        )
        for evt in events_list:
            ledger.emit(evt)
        self.run_ledgers[report.run_id] = ledger

        shutil.rmtree(self.state_dir / report.run_id / "holds", ignore_errors=True)
        hold_mgr = StewardHoldManager(state_dir=self.state_dir / report.run_id)
        active_hold_ids: list[str] = []
        for h in report.active_holds:
            hid = str(h.get("hold_id") or h.get("decision_id") or f"hold-{report.run_id}")
            active_hold_ids.append(hid)
            hold_mgr.record_hold(
                run_id=report.run_id,
                task_id=str(h.get("task_id") or f"task-{report.run_id}"),
                session_id=str(h.get("session_id") or f"sess-{report.run_id}"),
                stage_id=str(h.get("stage_id") or "STAGE_02_SCOUT_UNSTRUCTURED"),
                workbench_view_id=str(h.get("workbench_view_id") or "WB-03"),
                viz_id=str(h.get("viz_id") or "VIZ-GATE-07"),
                persona=str(h.get("persona") or "vault-first-mate"),
                decision_id=str(h.get("decision_id") or hid),
                hold_id=hid,
                event_id=str(
                    h.get("event_id") or (events_list[-1]["event_id"] if events_list else "evt-0")
                ),
                headline=str(h.get("headline") or "Steward hold pending resolution."),
                escalation_reason=str(h.get("escalation_reason") or "TIER_U3_AMBIGUOUS_ENTITY"),
                rule_id=str(h.get("rule_id") or "ALG-A9B"),
                options=list(h.get("options") or []),
            )
        for h in report.resolved_holds:
            hid = str(h.get("hold_id") or h.get("decision_id") or f"hold-{report.run_id}")
            rec = hold_mgr.record_hold(
                run_id=report.run_id,
                task_id=str(h.get("task_id") or f"task-{report.run_id}"),
                session_id=str(h.get("session_id") or f"sess-{report.run_id}"),
                stage_id=str(h.get("stage_id") or "STAGE_02_SCOUT_UNSTRUCTURED"),
                workbench_view_id=str(h.get("workbench_view_id") or "WB-03"),
                viz_id=str(h.get("viz_id") or "VIZ-GATE-07"),
                persona=str(h.get("persona") or "vault-first-mate"),
                decision_id=str(h.get("decision_id") or hid),
                hold_id=hid,
                event_id=str(
                    h.get("event_id") or (events_list[-1]["event_id"] if events_list else "evt-0")
                ),
                headline=str(h.get("headline") or "Steward hold resolved."),
                escalation_reason=str(h.get("escalation_reason") or "TIER_U3_AMBIGUOUS_ENTITY"),
                rule_id=str(h.get("rule_id") or "ALG-A9B"),
                options=list(h.get("options") or []),
            )
            hold_mgr.resolve_hold(
                rec.decision_id,
                action="REJECT" if str(h.get("status")) == "REJECTED" else "APPROVE",
                selected_option_id=h.get("selected_option_id"),
                resolution_notes=h.get("resolution_notes"),
            )
        self.hold_managers[report.run_id] = hold_mgr
        self.run_decisions[report.run_id] = []
        self.run_sandbox_history[report.run_id] = []

        all_codemods = {c for w in report.wave_reports for c in w.applied_codemods}
        if report.wave_reports:
            last_w = report.wave_reports[-1]
            w_sc = last_w.gate_scorecard
            g0_stat: GateVerdictStatus = (
                "AUTO-FIX"
                if "ALG_A9A_INTRA_SOURCE_SCHEMA_DRIFT" in all_codemods
                else (
                    "REJECT-WITH-TRACE"
                    if w_sc.get("G0_JSON_SCHEMA") == "REJECT-WITH-TRACE"
                    else "PASS"
                )
            )
            g1_raw = w_sc.get("G1_ONTOLOGY_AND_GRAIN", "PASS")
            g1_stat: GateVerdictStatus = (
                "REJECT-WITH-TRACE"
                if g1_raw == "REJECT-WITH-TRACE"
                else (
                    "ASK-USER"
                    if g1_raw == "ASK-USER" or report.status == "PAUSED_HOLD"
                    else (
                        "AUTO_REPAIRED"
                        if any(w.self_healing_attempts > 1 for w in report.wave_reports)
                        else "PASS"
                    )
                )
            )
            g2_stat: GateVerdictStatus = (
                "REJECT-WITH-TRACE"
                if w_sc.get("G2_KEY_AND_FK_DISCIPLINE") == "REJECT-WITH-TRACE"
                else "PASS"
            )
            g3_stat: GateVerdictStatus = (
                "REJECT-WITH-TRACE"
                if w_sc.get("G3_NETWORKX_BIPARTITE_DAG") == "REJECT-WITH-TRACE"
                else "PASS"
            )
            g4_raw = w_sc.get("G4_YAGNI_AND_COLUMN_LEDGER", "PASS")
            g4_stat: GateVerdictStatus = (
                "REJECT-WITH-TRACE"
                if g4_raw == "REJECT-WITH-TRACE"
                else ("AUTO-FIX" if "ALG_A1_3AXIS_SATELLITE_SPLIT" in all_codemods else "PASS")
            )
            g5_stat: GateVerdictStatus = (
                "PASS" if last_w.sandbox_summary.get("passed", False) else "REJECT-WITH-TRACE"
            )
            scorecard = GateScorecardSummary(
                gate_0_profile_drift=g0_stat,
                gate_1_contract_grain=g1_stat,
                gate_2_sqlglot_ast=g2_stat,
                gate_3_networkx_dag=g3_stat,
                gate_4_yagni_cost_guard=g4_stat,
                gate_5_duckdb_sandbox=g5_stat,
            )
        else:
            scorecard = GateScorecardSummary(
                gate_0_profile_drift="REJECT-WITH-TRACE",
                gate_1_contract_grain="REJECT-WITH-TRACE",
                gate_2_sqlglot_ast="REJECT-WITH-TRACE",
                gate_3_networkx_dag="REJECT-WITH-TRACE",
                gate_4_yagni_cost_guard="REJECT-WITH-TRACE",
                gate_5_duckdb_sandbox="REJECT-WITH-TRACE",
            )

        run_status = RunStatus(report.status)
        last_step = report.step_reports[-1] if report.step_reports else None
        last_evt = events_list[-1] if events_list else None
        run_dto = ProjectRunDTO(
            run_id=report.run_id,
            project_id=spec_obj.project_id,
            harness_provider=setup_obj.provider,
            status=run_status,
            started_at_utc=report.started_at,
            completed_at_utc=(None if run_status == RunStatus.PAUSED_HOLD else report.completed_at),
            current_stage_id=(last_step.stage_id if last_step else "STAGE_00_ORCHESTRATION"),
            current_view_id=(
                str(last_evt.get("workbench_view_id", "WB-00")) if last_evt else "WB-00"
            ),
            event_count=len(events_list),
            gate_scorecard=scorecard,
            active_hold_ids=active_hold_ids,
            events_sse_url=f"/api/v1/runs/{report.run_id}/events",
            gates_url=f"/api/v1/runs/{report.run_id}/gates",
            sandbox_url=f"/api/v1/runs/{report.run_id}/sandbox",
            decisions_url=f"/api/v1/runs/{report.run_id}/decisions",
        )
        self.runs[report.run_id] = run_dto

        gate_rep = self._build_default_gate_report(report.run_id)
        if report.wave_reports:
            gate_rep["wave_gate_scorecard"] = dict(report.wave_reports[-1].gate_scorecard)
        self.run_gate_reports[report.run_id] = gate_rep

        report_dict = report.model_dump(mode="json")
        self.pipeline_reports[report.run_id] = report_dict
        synced = self._sync_pipeline_report(report.run_id)
        redacted_rep = dict(synced)
        redacted_rep["observability_events"] = [
            redact_event_for_role(ev, eff_role) for ev in synced.get("observability_events", [])
        ]
        return redacted_rep

    def _sync_pipeline_report(self, run_id: str) -> dict[str, Any]:
        """Synchronize stored `pipeline_reports[run_id]` with current run, hold, and event state."""
        if run_id not in self.pipeline_reports:
            return {}
        rep = dict(self.pipeline_reports[run_id])
        run_dto = self.runs.get(run_id)
        if run_dto is not None:
            status_lit: Literal["COMPLETED", "PAUSED_HOLD", "FAILED"] = (
                "COMPLETED"
                if run_dto.status == RunStatus.COMPLETED
                else ("PAUSED_HOLD" if run_dto.status == RunStatus.PAUSED_HOLD else "FAILED")
            )
            rep["status"] = status_lit
            if run_dto.completed_at_utc:
                rep["completed_at"] = run_dto.completed_at_utc
            events = list(self.run_events.get(run_id, rep.get("observability_events", [])))
            rep["observability_events"] = events
            in_passed = bool(rep.get("input_validation", {}).get("passed", False))
            out_val = dict(rep.get("output_validation", {}))
            out_val["verified_events_count"] = len(events)
            rep["output_validation"] = out_val
            out_passed = bool(out_val.get("passed", False))
            rep["overall_passed"] = bool(in_passed and out_passed and status_lit == "COMPLETED")
            hold_mgr = self.hold_managers.get(run_id)
            if hold_mgr is not None:
                rep["active_holds"] = [
                    h.model_dump(mode="json")
                    for h in hold_mgr.list_holds(run_id=run_id, status="PENDING")
                ]
                rep["resolved_holds"] = [
                    h.model_dump(mode="json")
                    for h in hold_mgr.list_holds(run_id=run_id)
                    if h.status != "PENDING"
                ]
        self.pipeline_reports[run_id] = rep
        return rep

    def get_pipeline_report(
        self,
        run_id: str,
        role: RbacRole | str = RbacRole.PUBLIC,
    ) -> dict[str, Any]:
        """Return the RBAC-redacted `AgenticPipelineReport` for `run_id`."""
        eff_role = normalize_rbac_role(role, default=RbacRole.PUBLIC)
        if run_id in self.pipeline_reports:
            synced = self._sync_pipeline_report(run_id)
            redacted_rep = dict(synced)
            redacted_rep["observability_events"] = [
                redact_event_for_role(ev, eff_role) for ev in synced.get("observability_events", [])
            ]
            return redacted_rep
        if run_id not in self.runs:
            raise KeyError(f"Run '{run_id}' not found.")

        run_dto = self.runs[run_id]
        proj = self.projects.get(run_dto.project_id)
        events = [
            redact_event_for_role(ev, eff_role)
            for ev in self.run_events.get(run_id, [])
        ]
        status_lit: Literal["COMPLETED", "PAUSED_HOLD", "FAILED"] = (
            "COMPLETED"
            if run_dto.status == RunStatus.COMPLETED
            else ("PAUSED_HOLD" if run_dto.status == RunStatus.PAUSED_HOLD else "FAILED")
        )
        hold_mgr = self.hold_managers.get(run_id)
        if hold_mgr is not None:
            active_holds = [
                h.model_dump(mode="json")
                for h in hold_mgr.list_holds(run_id=run_id, status="PENDING")
            ]
            resolved_holds = [
                h.model_dump(mode="json")
                for h in hold_mgr.list_holds(run_id=run_id)
                if h.status != "PENDING"
            ]
        else:
            active_holds = [
                {"hold_id": hid, "status": "PENDING"} for hid in run_dto.active_hold_ids
            ]
            resolved_holds = [d.model_dump(mode="json") for d in self.run_decisions.get(run_id, [])]
        synthesized = AgenticPipelineReport(
            run_id=run_dto.run_id,
            pipeline_id=f"pipe-{run_dto.project_id}",
            project_id=run_dto.project_id,
            domain_namespace=proj.domain_namespace if proj else "general",
            status=status_lit,
            overall_passed=run_dto.status == RunStatus.COMPLETED,
            started_at=run_dto.started_at_utc,
            completed_at=run_dto.completed_at_utc or run_dto.started_at_utc,
            total_duration_ms=0.0,
            harness_setup={"provider": run_dto.harness_provider},
            input_validation=DeterministicValidationReport(
                stage="INPUT_VALIDATION",
                passed=True,
                checked_items_count=1,
            ),
            output_validation=DeterministicValidationReport(
                stage="OUTPUT_VALIDATION",
                passed=run_dto.status != RunStatus.FAILED,
                checked_items_count=len(events),
                verified_events_count=len(events),
            ),
            wave_reports=[],
            step_reports=[],
            intermediate_artifacts=[],
            final_artifacts={},
            active_holds=active_holds,
            resolved_holds=resolved_holds,
            observability_events=events,
            markdown_summary=(
                f"# Run Report: {run_dto.run_id}\n\n"
                f"- **Project ID**: `{run_dto.project_id}`\n"
                f"- **Status**: `{run_dto.status.value}`\n"
            ),
        )
        return synthesized.model_dump(mode="json")

    def create_run(self, req: CreateRunRequest) -> ProjectRunDTO:
        """Start a new verification/sandbox run and populate its observability ledger."""
        provider_override = (
            req.harness_provider
            if (req.harness_config_path is None or req.harness_provider != "antigravity")
            else None
        )
        if req.pipeline_input_path is not None:
            rep_dict = self.execute_agentic_pipeline_run(
                PipelineRunRequest(
                    input_spec_path=req.pipeline_input_path,
                    harness_config_path=req.harness_config_path,
                    provider_override=provider_override,
                )
            )
            return self.runs[str(rep_dict["run_id"])]

        if req.project_id is None or req.project_id not in self.projects:
            raise KeyError(f"Unknown project_id '{req.project_id}'")

        if req.contract_path is not None:
            self._resolve_repo_path(req.contract_path)
            self.projects[req.project_id] = self.projects[req.project_id].model_copy(
                update={"contract_path": req.contract_path}
            )

        proj = self.projects[req.project_id]
        bronze_sources = self.project_bronze_sources.get(req.project_id, [])
        if bronze_sources and proj.graph_a_ontology_path is not None:
            source_feeds: list[dict[str, Any]] = []
            unstructured_documents: list[dict[str, Any]] = []
            for idx, src_rel in enumerate(bronze_sources):
                resolved_src = self._resolve_repo_path(src_rel)
                ext = resolved_src.suffix.lower()
                if ext in {".txt", ".md", ".pdf"}:
                    unstructured_documents.append(
                        {
                            "doc_id": f"doc_{idx + 1:02d}_{resolved_src.stem}",
                            "document_path": str(resolved_src),
                        }
                    )
                else:
                    source_feeds.append(
                        {
                            "feed_id": f"feed_{idx + 1:02d}_{resolved_src.stem}",
                            "source_system": resolved_src.parent.name or "BRONZE_SOURCE",
                            "table_name": resolved_src.stem,
                            "file_path": str(resolved_src),
                        }
                    )
            dynamic_input_spec: dict[str, Any] = {
                "pipeline_id": f"pipe-{req.project_id}",
                "project_id": req.project_id,
                "display_name": proj.display_name,
                "domain_namespace": proj.domain_namespace,
                "graph_a_ontology_path": str(self._resolve_repo_path(proj.graph_a_ontology_path)),
                "waves": [
                    {
                        "wave_index": 0,
                        "wave_name": "Dynamic Project Discovery Wave",
                        "wave_type": "COLD_START",
                        "sources": source_feeds,
                        "unstructured_documents": unstructured_documents,
                    }
                ],
            }
            rep_dict = self.execute_agentic_pipeline_run(
                PipelineRunRequest(
                    input_spec=dynamic_input_spec,
                    harness_config_path=req.harness_config_path,
                    provider_override=provider_override,
                    steward_hold_policy="auto_approve_top_candidate",
                )
            )
            return self.runs[str(rep_dict["run_id"])]

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
        self._sync_pipeline_report(run_id)
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
        resolved_hold_id = req.hold_id
        resolved_event_id = req.event_id
        if not resolved_hold_id or not resolved_event_id:
            pending_holds = hold_mgr.list_pending_holds(run_id=run_id)
            if len(pending_holds) == 1:
                resolved_hold_id = resolved_hold_id or pending_holds[0].hold_id
                resolved_event_id = resolved_event_id or pending_holds[0].event_id
            elif not resolved_hold_id and not resolved_event_id:
                raise KeyError("Hold ID is required when no single active hold is pending.")
            else:
                candidate_lookup = resolved_hold_id or resolved_event_id or ""
                try:
                    existing = hold_mgr.get_hold(candidate_lookup)
                    resolved_hold_id = resolved_hold_id or existing.hold_id
                    resolved_event_id = resolved_event_id or existing.event_id
                except KeyError as exc:
                    raise KeyError(
                        "Hold ID is required when no single active hold is pending."
                    ) from exc

        target_hold_id = resolved_hold_id or resolved_event_id
        if not target_hold_id:
            raise KeyError("Hold ID is required when no single active hold is pending.")

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
                event_id=resolved_event_id or target_hold_id,
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
        self.run_events_by_view.setdefault(run_id, {}).setdefault("WB-03", []).append(
            resolution_event
        )

        run_dto = self.runs[run_id]
        remaining_ids = [rec.hold_id for rec in hold_mgr.list_pending_holds(run_id=run_id)]
        new_status = (
            RunStatus.COMPLETED if req.action in {"APPROVE", "ANSWER"} else RunStatus.FAILED
        )
        updated_scorecard = (
            run_dto.gate_scorecard.model_copy(
                update={
                    "gate_1_contract_grain": (
                        "AUTO_REPAIRED"
                        if run_dto.gate_scorecard.gate_1_contract_grain == "ASK-USER"
                        else run_dto.gate_scorecard.gate_1_contract_grain
                    )
                }
            )
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
        self._sync_pipeline_report(run_id)

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



# Domain aliases
StateStore = PlatformStateContainer

__all__ = [
    "PlatformStateContainer",
    "StateStore",
    "store_cas_artifact",
]
