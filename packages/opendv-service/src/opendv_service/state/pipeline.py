"""Agentic pipeline input validation, synchronization, and report inspection."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

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

        if req.input_spec is None and input_spec_path is None:
            raise ValueError(
                "Either 'input_spec' or 'input_spec_path' must be provided "
                "(zero hardcoded sample paths)."
            )

        if input_spec_path is not None:
            resolved_spec_path = self._resolve_repo_path(input_spec_path)
            if not resolved_spec_path.is_file():
                raise FileNotFoundError(f"Pipeline input spec file not found: '{input_spec_path}'.")
            base_dir = resolved_spec_path.parent
            raw_spec = _load_structured_mapping(resolved_spec_path, "pipeline input spec")
        else:
            assert req.input_spec is not None
            base_dir = self.repo_root
            raw_spec = dict(req.input_spec)

        if initial_contract_path is not None:
            resolved_init_path = resolve_workspace_path(
                str(initial_contract_path),
                workspace_root=self.repo_root,
                allow_temp=True,
            )
            raw_spec["initial_contract_path"] = str(resolved_init_path)

        init_contract_val = getattr(req, "initial_contract", None) or getattr(
            req, "initial_contract_content", None
        )
        if init_contract_val is not None:
            raw_spec["initial_contract"] = str(init_contract_val)

        target_wave = getattr(req, "wave_id", None)
        if target_wave is not None:
            raw_spec["wave_id"] = str(target_wave)

        if getattr(req, "run_id", None):
            raw_spec["run_id"] = req.run_id

        if harness_config_path is not None:
            resolved_cfg_path = self._resolve_repo_path(harness_config_path)
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
        except (FileNotFoundError, ValidationError) as exc:
            raise ValueError(str(exc)) from exc
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
        if req.input_spec is None and input_spec_path is None:
            raise ValueError(
                "Either 'input_spec' or 'input_spec_path' must be provided "
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
