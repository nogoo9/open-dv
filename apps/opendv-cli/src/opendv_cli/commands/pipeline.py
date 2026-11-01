"""`opendv pipeline` command handler (`validate`, `run`, `report`)."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import httpx

from opendv_agent_harness import AgenticPipelineReport
from opendv_cli.api_client import OpenDVCliApiClient
from opendv_platform_api.deps import (
    PipelineRunRequest,
    PipelineValidateRequest,
    PlatformStateContainer,
    RbacRole,
    normalize_rbac_role,
    redact_event_for_role,
)


def run_pipeline_command(
    args: argparse.Namespace,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, dict[str, Any]]:
    """Execute `opendv pipeline` actions (`validate`, `run`, `report`)."""
    api_url: str | None = getattr(args, "api_url", None)
    use_local: bool = bool(getattr(args, "local", False)) or not api_url
    action: str = getattr(args, "pipeline_action", None) or "run"
    role: str = getattr(args, "role", None) or "INTERNAL_MODELER"

    input_path: str | None = getattr(args, "input_path", None)
    harness_config: str | None = getattr(args, "harness_config", None)
    output_dir: str | None = getattr(args, "output_dir", None)
    provider: str | None = getattr(args, "provider", None)
    hold_policy: str | None = getattr(args, "hold_policy", None)
    run_id: str | None = getattr(args, "run_id", None)
    report_path: str | None = getattr(args, "report_path", None)

    if action in {"run", "validate"} and (input_path is None or not str(input_path).strip()):
        return 2, {
            "status": "ERROR",
            "error": (f"--input is required for 'pipeline {action}' (zero hardcoded sample paths)"),
        }

    if action == "report" and not run_id and not report_path:
        return 2, {
            "status": "ERROR",
            "error": "--run-id (or --report-path in local mode) is required for 'pipeline report'",
        }

    if not use_local and api_url:
        with OpenDVCliApiClient(base_url=api_url, role=role, transport=transport) as client:
            if action == "validate":
                assert input_path is not None
                val_payload: dict[str, Any] = {"input_spec_path": input_path}
                if harness_config:
                    val_payload["harness_config_path"] = harness_config
                if provider:
                    val_payload["provider_override"] = provider
                if hold_policy:
                    val_payload["steward_hold_policy"] = hold_policy
                res = client.validate_pipeline(val_payload)
                return (0 if res.get("passed", False) else 1), res

            if action == "run":
                assert input_path is not None
                run_payload: dict[str, Any] = {"input_spec_path": input_path}
                if harness_config:
                    run_payload["harness_config_path"] = harness_config
                if output_dir:
                    run_payload["output_dir"] = output_dir
                if provider:
                    run_payload["provider_override"] = provider
                if hold_policy:
                    run_payload["steward_hold_policy"] = hold_policy
                res = client.run_pipeline(run_payload)
                passed = bool(res.get("overall_passed", False)) or (
                    res.get("status") == "PAUSED_HOLD"
                )
                return (0 if passed else 1), res

            if not run_id:
                return 2, {
                    "status": "ERROR",
                    "error": "--run-id is required for remote 'pipeline report'",
                }
            res = client.get_run_report(run_id)
            return 0, res

    state = PlatformStateContainer()
    if action == "validate":
        assert input_path is not None
        val_req = PipelineValidateRequest(
            input_spec_path=input_path,
            harness_config_path=harness_config,
            provider_override=provider,
            steward_hold_policy=hold_policy,
        )
        res = state.validate_agentic_pipeline(val_req)
        return (0 if res.get("passed", False) else 1), res

    if action == "run":
        assert input_path is not None
        eff_role = normalize_rbac_role(role, default=RbacRole.INTERNAL_MODELER)
        run_req = PipelineRunRequest(
            input_spec_path=input_path,
            harness_config_path=harness_config,
            output_dir=output_dir,
            provider_override=provider,
            steward_hold_policy=hold_policy,
        )
        res = state.execute_agentic_pipeline_run(run_req, role=eff_role)
        passed = bool(res.get("overall_passed", False)) or (res.get("status") == "PAUSED_HOLD")
        return (0 if passed else 1), res

    eff_role = normalize_rbac_role(role, default=RbacRole.INTERNAL_MODELER)
    if report_path:
        rp = Path(report_path).resolve()
        if not rp.is_file():
            raise ValueError(f"Pipeline report file not found: '{report_path}'.")
        loaded = AgenticPipelineReport.model_validate_json(rp.read_text(encoding="utf-8"))
        rep_dict = loaded.model_dump(mode="json")
        rep_dict["observability_events"] = [
            redact_event_for_role(ev, eff_role) for ev in rep_dict.get("observability_events", [])
        ]
        return 0, rep_dict

    assert run_id is not None
    cand_file = Path(run_id)
    if cand_file.suffix.lower() == ".json" and cand_file.is_file():
        loaded = AgenticPipelineReport.model_validate_json(cand_file.read_text(encoding="utf-8"))
        rep_dict = loaded.model_dump(mode="json")
        rep_dict["observability_events"] = [
            redact_event_for_role(ev, eff_role) for ev in rep_dict.get("observability_events", [])
        ]
        return 0, rep_dict

    res = state.get_pipeline_report(run_id, role=eff_role)
    return 0, res
