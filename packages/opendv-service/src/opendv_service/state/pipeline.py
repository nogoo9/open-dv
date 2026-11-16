"""Agentic pipeline input validation, synchronization, and report inspection."""

from __future__ import annotations

import contextlib
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

        if req.input_spec is None and input_spec_path is None and input_spec_content_str is None:
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
        else:
            assert isinstance(req.input_spec, dict)
            base_dir = self.repo_root
            raw_spec = dict(req.input_spec)

        ont_p = raw_spec.get("graph_a_ontology_path") or raw_spec.get("ontology_path")
        if ont_p and isinstance(ont_p, str) and not Path(ont_p).is_absolute():
            cand = base_dir / ont_p
            if not cand.is_file():
                matches = list(self.repo_root.glob(f"**/{ont_p}"))
                if matches and matches[0].is_file():
                    base_dir = matches[0].resolve().parent.parent

        if raw_spec.get("ontology_source") and not raw_spec.get("graph_a_source"):
            raw_spec["graph_a_source"] = raw_spec.get("ontology_source")

        if getattr(req, "graph_a_source", None) is not None and not raw_spec.get("graph_a_source"):
            raw_spec["graph_a_source"] = dict(req.graph_a_source)  # type: ignore[arg-type]

        cas_store = getattr(self, "cas_store", None)
        if cas_store is not None and hasattr(cas_store, "base_dir"):
            cas_base = Path(cas_store.base_dir).resolve()
            sources_list = raw_spec.get("sources") or getattr(req, "sources", None)
            if sources_list and isinstance(sources_list, list):
                resolved_sources: list[dict[str, Any]] = []
                for s in sources_list:
                    s_dict = (
                        dict(s)
                        if isinstance(s, dict)
                        else (s.model_dump(mode="json") if hasattr(s, "model_dump") else dict(s))
                    )
                    s_uri = str(s_dict.get("uri") or "").strip()
                    if s_uri.startswith("cas://"):
                        raw_target = s_uri[len("cas://") :].split("?")[0].split("#")[0].strip()
                        c_hash = raw_target.split("/")[0].strip().lower()
                        cands = [cas_base / c_hash.upper(), cas_base / c_hash.lower()]
                        cand_p = next((c for c in cands if c.is_file()), None)
                        if cand_p is not None:
                            is_sqlite = False
                            with contextlib.suppress(OSError), open(cand_p, "rb") as bf:
                                if bf.read(16).startswith(b"SQLite format 3\x00"):
                                    is_sqlite = True
                            s_sys = s_dict.get("source_system") or "cas_store"
                            s_opts = dict(s_dict.get("options") or {})
                            s_opts["source_system"] = s_sys
                            s_dict["options"] = s_opts
                            s_dict["source_system"] = s_sys
                            if is_sqlite:
                                s_dict["uri"] = f"sqlite:///{cand_p.as_posix()}"
                                s_dict["adapter"] = "sqlite"
                            else:
                                s_dict["uri"] = cand_p.as_posix()
                                s_dict.setdefault("adapter", "file")
                    resolved_sources.append(s_dict)
                raw_spec["sources"] = resolved_sources

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
        if req.input_spec is None and input_spec_path is None and input_spec_content_str is None:
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
