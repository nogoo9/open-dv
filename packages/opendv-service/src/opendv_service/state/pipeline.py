"""Agentic pipeline input validation, synchronization, and report inspection."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from opendv_agent_harness import (
    DeterministicValidationReport,
    HarnessSetupConfig,
    PipelineInputSpec,
    validate_pipeline_inputs,
)
from opendv_core.paths import resolve_workspace_path
from opendv_service.errors import NotFoundError
from opendv_service.models import (
    PipelineValidateRequest,
    RunStatus,
)
from opendv_service.rbac import (
    RbacRole,
    normalize_rbac_role,
    redact_event_for_role,
)
from opendv_service.state.base import _load_structured_mapping

if TYPE_CHECKING:
    from opendv_service.state.base import BaseStateContainer


_PRUNED_WALK_DIRS = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        ".git",
        ".moon",
        "dist",
        "worktrees",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".opendv",
        "playwright-report",
        "test-results",
    }
)


def _find_ontology_base_dir(repo_root: Path, rel_ont_path: str) -> Path | None:
    """Bounded search for a workspace-relative ontology file while pruning heavy build/env dirs."""
    search_roots = []
    examples_dir = repo_root / "examples"
    if examples_dir.is_dir():
        search_roots.append(examples_dir)
    search_roots.append(repo_root)

    seen: set[Path] = set()
    for root in search_roots:
        for dirpath, dirnames, _ in os.walk(root, topdown=True):
            dirnames[:] = sorted(d for d in dirnames if d not in _PRUNED_WALK_DIRS)
            if root == repo_root and dirpath == str(repo_root):
                dirnames[:] = [d for d in dirnames if d != "examples"]
            p = Path(dirpath)
            if p in seen:
                continue
            seen.add(p)
            cand = p / rel_ont_path
            if cand.is_file():
                # Preserve relative base_dir resolution (`base_dir / ont_p == cand`).
                return p.resolve()
    return None


class PipelineStateMixin:
    """Agentic pipeline input resolution, validation, and report inspection mixin."""

    def _resolve_pipeline_raw_inputs(
        self: BaseStateContainer,
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

        input_spec_content = getattr(req, "input_spec_content", None)
        input_spec_content_str = (
            input_spec_content.strip()
            if isinstance(input_spec_content, str) and input_spec_content.strip()
            else None
        )

        has_direct_inputs = any(
            getattr(req, field, None)
            for field in (
                "blueprint",
                "blueprint_content",
                "sources",
                "unstructured_documents",
            )
        )
        if (
            req.input_spec is None
            and input_spec_path is None
            and input_spec_content_str is None
            and not has_direct_inputs
        ):
            raise ValueError(
                "Either 'input_spec', 'input_spec_content', or 'input_spec_path' must be provided "
                "(zero hardcoded sample paths)."
            )

        if input_spec_path is not None:
            resolved_spec_path = self._resolve_repo_path(input_spec_path)
            if not resolved_spec_path.is_file():
                raise FileNotFoundError(f"Pipeline input spec file not found: '{input_spec_path}'.")
            base_dir = resolved_spec_path.parent
            raw_spec = _load_structured_mapping(resolved_spec_path, "pipeline input spec")
        elif input_spec_content_str is not None:
            base_dir = self.repo_root
            try:
                loaded_spec = yaml.safe_load(input_spec_content_str)
            except yaml.YAMLError as exc:
                raise ValueError(
                    f"Failed to parse inline input_spec_content YAML/JSON: {exc}"
                ) from exc
            if not isinstance(loaded_spec, dict):
                raise ValueError(
                    "Expected mapping object in 'input_spec_content', "
                    f"got {type(loaded_spec).__name__}."
                )
            raw_spec = dict(loaded_spec)
        elif isinstance(req.input_spec, str) and req.input_spec.strip():
            base_dir = self.repo_root
            try:
                loaded_spec = yaml.safe_load(req.input_spec)
            except yaml.YAMLError as exc:
                raise ValueError(f"Failed to parse inline input_spec YAML/JSON: {exc}") from exc
            if not isinstance(loaded_spec, dict):
                raise ValueError(
                    f"Expected mapping object in 'input_spec', got {type(loaded_spec).__name__}."
                )
            raw_spec = dict(loaded_spec)
        elif isinstance(req.input_spec, dict):
            base_dir = self.repo_root
            raw_spec = dict(req.input_spec)
        else:
            base_dir = self.repo_root
            raw_spec = req.model_dump(mode="json", exclude_none=True)

        ont_p = raw_spec.get("graph_a_ontology_path") or raw_spec.get("ontology_path")
        if ont_p and isinstance(ont_p, str) and not Path(ont_p).is_absolute():
            cand = base_dir / ont_p
            if not cand.is_file():
                matched_base = _find_ontology_base_dir(self.repo_root, ont_p)
                if matched_base is not None:
                    base_dir = matched_base

        if raw_spec.get("ontology_source") and not raw_spec.get("graph_a_source"):
            raw_spec["graph_a_source"] = raw_spec.get("ontology_source")

        if getattr(req, "graph_a_source", None) is not None and not raw_spec.get("graph_a_source"):
            raw_spec["graph_a_source"] = dict(req.graph_a_source)  # type: ignore[arg-type]

        eff_sid = getattr(req, "session_id", None) or raw_spec.get("session_id")
        cas_store = getattr(self, "cas_store", None)
        sources_list = raw_spec.get("sources") or getattr(req, "sources", None)
        docs_list = raw_spec.get("unstructured_documents") or getattr(
            req, "unstructured_documents", None
        )
        if (sources_list and isinstance(sources_list, list)) or (
            docs_list and isinstance(docs_list, list)
        ):
            from opendv_service.use_cases.pipeline_preflight import (
                resolve_cas_sources_list,
                resolve_cas_unstructured_docs_list,
            )

            if sources_list and isinstance(sources_list, list):
                raw_spec["sources"] = resolve_cas_sources_list(
                    sources_list,
                    cas_store,
                    session_id=eff_sid,
                    state=self,
                )
            if docs_list and isinstance(docs_list, list):
                raw_spec["unstructured_documents"] = resolve_cas_unstructured_docs_list(
                    docs_list,
                    cas_store,
                    session_id=eff_sid,
                    state=self,
                )

        if initial_contract_path is not None:
            resolved_init_path = resolve_workspace_path(
                str(initial_contract_path),
                workspace_root=self.repo_root,
                allow_temp=True,
            )
            raw_spec["initial_contract_path"] = str(resolved_init_path)

        if getattr(req, "initial_contract_content", None) is not None:
            raw_spec["initial_contract_content"] = str(req.initial_contract_content)
        elif getattr(req, "initial_contract", None) is not None:
            init_contract_val = req.initial_contract
            if isinstance(init_contract_val, str):
                raw_spec["initial_contract_content"] = init_contract_val
            else:
                raw_spec["initial_contract"] = init_contract_val

        has_explicit_baseline = bool(
            raw_spec.get("initial_contract")
            or raw_spec.get("initial_contract_path")
            or raw_spec.get("initial_contract_content")
            or raw_spec.get("blueprint")
            or raw_spec.get("blueprint_path")
            or raw_spec.get("blueprint_content")
        )
        waves_val = raw_spec.get("waves")
        explicit_cold_wave = bool(
            isinstance(waves_val, list)
            and waves_val
            and isinstance(waves_val[0], dict)
            and str(waves_val[0].get("wave_type") or "").upper() == "COLD_START"
        )
        proj_id = str(raw_spec.get("project_id") or "").strip()
        if (
            not has_explicit_baseline
            and not explicit_cold_wave
            and proj_id
            and hasattr(self, "projects")
        ):
            proj = self.projects.get(proj_id)
            if proj is not None and proj.contract_path:
                cand_contract = Path(proj.contract_path)
                if cand_contract.is_file():
                    raw_spec["initial_contract_path"] = str(cand_contract.resolve())

        if getattr(req, "run_id", None):
            raw_spec["run_id"] = req.run_id

        if harness_config_path is not None:
            resolved_cfg_path = self._resolve_repo_path(harness_config_path)
            if not resolved_cfg_path.is_file():
                raise FileNotFoundError(f"Harness config file not found: '{harness_config_path}'.")
            raw_setup = _load_structured_mapping(resolved_cfg_path, "harness config")
        elif req.harness_config is not None:
            raw_setup = dict(req.harness_config)
        elif hasattr(self, "get_active_harness_setup_dict"):
            raw_setup = dict(self.get_active_harness_setup_dict())
        else:
            raw_setup = {}

        if (
            raw_spec.get("ontology_policy") in (None, "off")
            and raw_setup.get("ontology_policy")
            and raw_setup.get("ontology_policy") != "off"
        ):
            raw_setup["ontology_policy"] = str(raw_setup["ontology_policy"])

        if provider_override is not None:
            raw_setup["provider"] = provider_override
        gov_pol = raw_spec.get("governance_policy")
        if isinstance(gov_pol, dict):
            if gov_pol.get("stage_output_policy"):
                raw_setup["stage_output_policy"] = str(gov_pol["stage_output_policy"])
            if steward_hold_policy is None and gov_pol.get("steward_hold_policy"):
                steward_hold_policy = str(gov_pol["steward_hold_policy"])
        elif steward_hold_policy is None and raw_spec.get("steward_hold_policy"):
            steward_hold_policy = str(raw_spec["steward_hold_policy"])
        if steward_hold_policy is not None:
            raw_setup["steward_hold_policy"] = steward_hold_policy

        target_dialect = (
            getattr(req, "target_dialect", None)
            or raw_spec.get("target_dialect")
            or raw_spec.get("dialect")
        )
        if target_dialect:
            raw_spec["target_dialect"] = str(target_dialect)

        return raw_spec, raw_setup, base_dir

    def _resolve_pipeline_inputs(
        self: BaseStateContainer,
        req: PipelineValidateRequest,
    ) -> tuple[PipelineInputSpec, HarnessSetupConfig, Path]:
        """Resolve `PipelineInputSpec`, `HarnessSetupConfig`, and `base_dir` from `req`."""
        try:
            raw_spec, raw_setup, base_dir = self._resolve_pipeline_raw_inputs(req)
            spec_obj = PipelineInputSpec.model_validate(raw_spec)
            setup_obj = (
                HarnessSetupConfig.model_validate(raw_setup) if raw_setup else HarnessSetupConfig()
            )
        except FileNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        except ValidationError as exc:
            err_details = exc.errors(include_input=False)
            raise ValueError(f"Validation error: {err_details}") from exc
        return spec_obj, setup_obj, base_dir

    def validate_agentic_pipeline(
        self: BaseStateContainer,
        req: PipelineValidateRequest,
    ) -> dict[str, Any]:
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
        input_spec_content = getattr(req, "input_spec_content", None)
        input_spec_content_str = (
            input_spec_content.strip()
            if isinstance(input_spec_content, str) and input_spec_content.strip()
            else None
        )
        has_direct_inputs = any(
            getattr(req, field, None)
            for field in (
                "blueprint",
                "blueprint_content",
                "sources",
                "unstructured_documents",
            )
        )
        if (
            req.input_spec is None
            and input_spec_path is None
            and input_spec_content_str is None
            and not has_direct_inputs
        ):
            raise ValueError(
                "Either 'input_spec', 'input_spec_content', or 'input_spec_path' must be provided "
                "(zero hardcoded sample paths)."
            )
        if input_spec_path is not None:
            self._resolve_repo_path(input_spec_path)
        if harness_config_path is not None:
            self._resolve_repo_path(harness_config_path)
        if req.initial_contract_path is not None and req.initial_contract_path.strip():
            self._resolve_repo_path(req.initial_contract_path.strip())

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

    def _sync_pipeline_report(
        self: BaseStateContainer,
        run_id: str,
    ) -> dict[str, Any]:
        """Synchronize stored `pipeline_reports[run_id]` with current run, hold, and event state."""
        if run_id not in self.pipeline_reports:
            return {}
        rep = dict(self.pipeline_reports[run_id])
        run_dto = self.runs.get(run_id)
        if run_dto is not None:
            if run_dto.status == RunStatus.COMPLETED:
                status_lit = "COMPLETED"
            elif run_dto.status == RunStatus.PAUSED_HOLD:
                status_lit = "PAUSED_HOLD"
            elif run_dto.status == RunStatus.FAILED:
                status_lit = "FAILED"
            else:
                status_lit = str(rep.get("status") or run_dto.status.value)
            rep["status"] = status_lit
            if run_dto.completed_at_utc:
                rep["completed_at"] = run_dto.completed_at_utc
            if run_dto.gate_scorecard:
                sc_dump = run_dto.gate_scorecard.model_dump(mode="json")
                if "gate_scorecard" not in rep:
                    rep["gate_scorecard"] = sc_dump
                elif (
                    isinstance(rep["gate_scorecard"], dict)
                    and "shadow_passed" not in rep["gate_scorecard"]
                ):
                    rep["gate_scorecard"].update(sc_dump)
                    g5_pass = run_dto.gate_scorecard.gate_5_duckdb_sandbox in ("PASS", "AUTO-FIX")
                    rep["gate_scorecard"].setdefault("shadow_passed", g5_pass)
                    rep["gate_scorecard"].setdefault("sandbox_passed", g5_pass)
                    rep["gate_scorecard"].setdefault("four_zone_passed", g5_pass)
            events = list(self.run_events.get(run_id, rep.get("observability_events", [])))
            rep["observability_events"] = events
            in_passed = bool((rep.get("input_validation") or {}).get("passed", False))
            out_val = dict(rep.get("output_validation") or {})
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
        self: BaseStateContainer,
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
            raise NotFoundError(f"Run '{run_id}' not found.")

        raise NotFoundError(f"Pipeline report for run '{run_id}' not found.")
