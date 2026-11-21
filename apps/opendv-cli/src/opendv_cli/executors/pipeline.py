"""Pipeline execution mixin for LocalExecutor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from opendv_contracts import RbacRole
from opendv_contracts.dtos.pipeline import PipelineRunRequest, PipelineValidateRequest
from opendv_service import (
    AgenticPipelineReport,
    normalize_rbac_role,
    redact_event_for_role,
)


class PipelineExecutionMixin:
    """Mixin providing pipeline execution capabilities for LocalExecutor."""

    def _execute_pipeline(self, args: argparse.Namespace) -> tuple[int, Any]:
        action = getattr(args, "pipeline_action", None) or "run"
        input_path: str | None = getattr(args, "input", None)
        project_id: str | None = getattr(args, "project_id", None)
        blueprint_arg = getattr(args, "blueprint", None)
        ontology_arg = getattr(args, "ontology", None)
        contract_arg = getattr(args, "contract", None)
        source_arg = getattr(args, "source", None)
        dialect_arg = getattr(args, "dialect", None) or "duckdb"

        is_single_run = bool(
            blueprint_arg
            or ontology_arg
            or (source_arg and not input_path)
            or (contract_arg and not input_path)
        )

        if (
            action in {"run", "validate"}
            and not input_path
            and not blueprint_arg
            and not ontology_arg
        ):
            err_text = (
                f"One of --input, --blueprint, or --ontology is required for 'pipeline {action}'"
            )
            return 2, {
                "status": "ERROR",
                "error_code": "SYNTAX_INVALID_ARGUMENT",
                "error": err_text,
                "detail": err_text,
            }

        if is_single_run:
            import yaml

            from opendv_contracts.dtos.pipeline import PipelineInputDTO
            from opendv_contracts.dtos.sources import SourceFeedDTO

            sources_list: list[SourceFeedDTO] = []
            if source_arg:
                raw_sources = source_arg if isinstance(source_arg, list) else [source_arg]
                flat_sources: list[str] = []
                for s in raw_sources:
                    if isinstance(s, list):
                        flat_sources.extend(str(item) for item in s)
                    else:
                        flat_sources.append(str(s))
                for idx, s_str in enumerate(flat_sources):
                    stem = Path(s_str.split("://")[-1]).stem or f"source_{idx + 1}"
                    sources_list.append(
                        SourceFeedDTO(
                            feed_id=f"feed-{idx + 1}",
                            source_system=stem,
                            uri=s_str,
                            target_dialect=dialect_arg,
                        )
                    )

            ont_dict: dict[str, Any] | None = None
            if ontology_arg:
                ont_p = Path(ontology_arg).resolve()
                if ont_p.is_file():
                    loaded = yaml.safe_load(ont_p.read_text(encoding="utf-8"))
                    ont_dict = loaded if isinstance(loaded, dict) else None
                elif isinstance(ontology_arg, dict):
                    ont_dict = ontology_arg
                else:
                    loaded = yaml.safe_load(str(ontology_arg))
                    ont_dict = loaded if isinstance(loaded, dict) else None

            harness_cfg_arg = getattr(args, "harness_config", None)
            provider_arg = getattr(args, "provider", None)
            hold_policy_arg = getattr(args, "hold_policy", None)
            output_dir_arg = getattr(args, "output_dir", None)
            unstructured_only_arg = getattr(args, "unstructured_only", False)

            gov_policy = (
                {"steward_hold_policy": hold_policy_arg} if hold_policy_arg else {}
            )
            dto_kwargs: dict[str, Any] = {
                "blueprint_path": str(Path(blueprint_arg).resolve()) if blueprint_arg else None,
                "ontology_source": ont_dict,
                "initial_contract_path": (
                    str(Path(contract_arg).resolve()) if contract_arg else None
                ),
                "sources": sources_list,
                "target_dialect": dialect_arg,
                "governance_policy": gov_policy,
            }
            if provider_arg:
                dto_kwargs["harness_provider"] = provider_arg
            single_run_input = PipelineInputDTO(**dto_kwargs)

            if action == "validate":
                from opendv_service.use_cases.pipeline import validate_pipeline

                res = validate_pipeline(
                    single_run_input,
                    state=getattr(self.service, "state", None),
                    base_dir=getattr(self.service, "workspace_root", None),
                )
                payload = res.model_dump(mode="json") if hasattr(res, "model_dump") else dict(res)
                payload["mode"] = "local"
                is_success = bool(payload.get("passed", False))
                return (0 if is_success else 1), payload

            if action == "run":
                from opendv_service.use_cases.pipeline import (
                    DualBlueprintIngressNormalizer,
                    execute_pipeline_run_sync,
                )

                role_raw = getattr(args, "role", None) or "INTERNAL_MODELER"
                eff_role = normalize_rbac_role(role_raw, default=RbacRole.INTERNAL_MODELER)
                stream_enabled = bool(getattr(args, "stream", True))
                is_structured = bool(getattr(args, "json", False) or getattr(args, "toon", False))
                stream_tokens = getattr(args, "stream_tokens", None)
                tokens_enabled = (
                    bool(stream_tokens)
                    if stream_tokens is not None
                    else bool(stream_enabled and not is_structured)
                )
                target = sys.stderr if is_structured else sys.stdout

                def _format_short_stage(stage_id: str) -> str:
                    parts = stage_id.split("_")
                    if len(parts) >= 2 and parts[0].upper() == "STAGE":
                        return f"STAGE {parts[1]}"
                    return stage_id

                def _on_start(_step_idx: int, stage_id: str, persona: str, model: str) -> None:
                    short_st = _format_short_stage(stage_id)
                    msg = (
                        f"[{short_st}] Starting {stage_id} "
                        f"({persona}, model={model})..."
                    )
                    print(msg, file=target, flush=True)

                def _on_done(_step_idx: int, step_rep: Any) -> None:
                    st_id = getattr(step_rep, "stage_id", "")
                    p, m = getattr(step_rep, "persona", ""), getattr(step_rep, "model_used", "")
                    st_s, sm = getattr(step_rep, "status", ""), getattr(step_rep, "summary", "")
                    dur = getattr(step_rep, "duration_ms", 0)
                    short_st = _format_short_stage(st_id)
                    msg = (
                        f"[{short_st}] {st_id} ({p}, model={m}) -> "
                        f"{st_s}: {sm} ({dur}ms)"
                    )
                    print(msg, file=target, flush=True)

                def _on_tok(ev: Any) -> None:
                    if hasattr(ev, "delta_text") and ev.delta_text:
                        print(ev.delta_text, end="", file=target, flush=True)

                norm_bp, _, _, _ = DualBlueprintIngressNormalizer.normalize(
                    {
                        "blueprint": blueprint_arg,
                        "ontology": ont_dict or ontology_arg,
                        "contract": contract_arg,
                        "sources": sources_list,
                    },
                    base_dir=getattr(self.service, "workspace_root", None),
                )
                bp_dict: dict[str, Any] | None = None
                if norm_bp is not None:
                    bp_dict = (
                        norm_bp.model_dump(mode="json")
                        if hasattr(norm_bp, "model_dump")
                        else norm_bp
                    )
                elif ont_dict:
                    bp_dict = {
                        "domain_namespace": ont_dict.get("domain_namespace") or "general",
                        "ontology": ont_dict,
                        "contract": contract_arg,
                    }

                init_contract_p = (
                    str(Path(contract_arg).resolve()) if contract_arg else None
                )
                ont_path_p = (
                    str(Path(ontology_arg).resolve())
                    if ontology_arg and Path(ontology_arg).is_file()
                    else None
                )
                spec_dict: dict[str, Any] = {
                    "graph_a_source": ont_dict,
                    "graph_a_ontology_path": ont_path_p,
                    "blueprint": bp_dict,
                    "sources": [s.model_dump(mode="json") for s in sources_list],
                    "target_dialect": dialect_arg,
                }
                if hold_policy_arg:
                    spec_dict["governance_policy"] = {"steward_hold_policy": hold_policy_arg}
                    spec_dict["steward_hold_policy"] = hold_policy_arg
                if unstructured_only_arg:
                    spec_dict["unstructured_only"] = True
                if harness_cfg_arg:
                    spec_dict["harness_config_path"] = harness_cfg_arg
                if output_dir_arg:
                    spec_dict["output_dir"] = output_dir_arg

                run_req = PipelineRunRequest(
                    blueprint=bp_dict,
                    blueprint_path=str(Path(blueprint_arg).resolve()) if blueprint_arg else None,
                    graph_a_source=ont_dict,
                    initial_contract_path=init_contract_p,
                    sources=sources_list,
                    target_dialect=dialect_arg,
                    harness_config_path=harness_cfg_arg,
                    provider_override=provider_arg,
                    steward_hold_policy=hold_policy_arg,
                    output_dir=output_dir_arg,
                    unstructured_only=unstructured_only_arg,
                    input_spec=spec_dict,
                )

                res = execute_pipeline_run_sync(
                    self.service.state,
                    run_req,
                    role=eff_role,
                    pipeline_runner=getattr(self.service, "pipeline_runner", None),
                    on_stage_start=_on_start if stream_enabled else None,
                    on_stage_complete=_on_done if stream_enabled else None,
                    on_stream_event=_on_tok if (stream_enabled and tokens_enabled) else None,
                )
                status_val = (
                    res.get("status") if isinstance(res, dict) else getattr(res, "status", None)
                )
                passed = bool(
                    res.get("overall_passed")
                    if isinstance(res, dict)
                    else getattr(res, "overall_passed", False)
                )
                is_paused_hold = status_val == "PAUSED_HOLD"
                payload = res.model_dump(mode="json") if hasattr(res, "model_dump") else dict(res)
                payload["mode"] = "local"
                return (0 if (passed or is_paused_hold) else 1), payload

        if action == "validate":
            val_req = PipelineValidateRequest(
                input_spec_path=input_path,
                harness_config_path=getattr(args, "harness_config", None),
                initial_contract_path=getattr(args, "initial_contract", None),
                provider_override=getattr(args, "provider", None),
                steward_hold_policy=getattr(args, "hold_policy", None),
                unstructured_only=getattr(args, "unstructured_only", False),
            )
            res = self.service.validate_pipeline(val_req)
            payload = res.model_dump(mode="json") if hasattr(res, "model_dump") else dict(res)
            payload["mode"] = "local"
            return (0 if payload.get("passed", False) else 1), payload

        if action == "run":
            role_raw = getattr(args, "role", None) or "INTERNAL_MODELER"
            eff_role = normalize_rbac_role(role_raw, default=RbacRole.INTERNAL_MODELER)
            run_req = PipelineRunRequest(
                project_id=project_id,
                input_spec_path=input_path,
                harness_config_path=getattr(args, "harness_config", None),
                initial_contract_path=getattr(args, "initial_contract_path", None)
                or getattr(args, "initial_contract", None),
                initial_contract=getattr(args, "initial_contract_inline", None)
                or getattr(args, "initial_contract_content", None),
                output_dir=getattr(args, "output_dir", None),
                provider_override=getattr(args, "provider", None),
                steward_hold_policy=getattr(args, "hold_policy", None),
                unstructured_only=getattr(args, "unstructured_only", False),
            )
            stream_enabled = bool(getattr(args, "stream", True))
            is_structured = bool(getattr(args, "json", False) or getattr(args, "toon", False))
            stream_tokens = getattr(args, "stream_tokens", None)
            tokens_enabled = (
                bool(stream_tokens)
                if stream_tokens is not None
                else bool(stream_enabled and not is_structured)
            )
            target = sys.stderr if is_structured else sys.stdout

            def _format_short_stage(stage_id: str) -> str:
                parts = stage_id.split("_")
                if len(parts) >= 2 and parts[0].upper() == "STAGE":
                    return f"STAGE {parts[1]}"
                return stage_id

            def _on_start(_step_idx: int, stage_id: str, persona: str, model: str) -> None:
                short_st = _format_short_stage(stage_id)
                msg = (
                    f"[{short_st}] Starting {stage_id} "
                    f"({persona}, model={model})..."
                )
                print(msg, file=target, flush=True)

            def _on_done(_step_idx: int, step_rep: Any) -> None:
                st_id = getattr(step_rep, "stage_id", "")
                p, m = getattr(step_rep, "persona", ""), getattr(step_rep, "model_used", "")
                st_s, sm = getattr(step_rep, "status", ""), getattr(step_rep, "summary", "")
                dur = getattr(step_rep, "duration_ms", 0)
                msg = (
                    f"[{_format_short_stage(st_id)}] {st_id} ({p}, model={m}) -> "
                    f"{st_s}: {sm} ({dur}ms)"
                )
                print(msg, file=target, flush=True)

            def _on_tok(ev: Any) -> None:
                if hasattr(ev, "delta_text") and ev.delta_text:
                    print(ev.delta_text, end="", file=target, flush=True)

            res = self.service.execute_pipeline_run_sync(
                run_req,
                role=eff_role,
                on_stage_start=_on_start if stream_enabled else None,
                on_stage_complete=_on_done if stream_enabled else None,
                on_stream_event=_on_tok if (stream_enabled and tokens_enabled) else None,
            )
            passed = bool(res.get("overall_passed", False)) or (res.get("status") == "PAUSED_HOLD")
            payload = res.model_dump(mode="json") if hasattr(res, "model_dump") else dict(res)
            payload["mode"] = "local"
            return (0 if passed else 1), payload

        # action == "report"
        run_id: str | None = getattr(args, "run_id", None)
        report_path: str | None = getattr(args, "report_path", None)
        if not run_id and not report_path:
            return 2, {"status": "ERROR", "error": "--run-id or --report-path is required"}

        role_raw = getattr(args, "role", None) or "INTERNAL_MODELER"
        eff_role = normalize_rbac_role(role_raw, default=RbacRole.INTERNAL_MODELER)
        if report_path:
            rp = Path(report_path).resolve()
            if not rp.is_file():
                return 2, {"status": "ERROR", "error": f"Report not found: {report_path}"}
            loaded = AgenticPipelineReport.model_validate_json(rp.read_text(encoding="utf-8"))
            d = loaded.model_dump(mode="json")
            d["observability_events"] = [
                redact_event_for_role(ev, eff_role) for ev in d.get("observability_events", [])
            ]
            d["mode"] = "local"
            return 0, d

        assert run_id is not None
        rep = self.service.get_pipeline_report(run_id, role=eff_role)
        payload = dict(rep)
        payload["mode"] = "local"
        return 0, payload

    # 8. ontology (Phase P7)

    @staticmethod
    def _build_harness_dict_from_args(args: argparse.Namespace) -> dict[str, Any]:
        import yaml

        harness: dict[str, Any] = {}
        cfg_arg = getattr(args, "harness_config", None)
        if cfg_arg:
            cfg_p = Path(cfg_arg).resolve()
            if not cfg_p.is_file():
                raise FileNotFoundError(f"Harness config not found: {cfg_arg}")
            raw_text = cfg_p.read_text(encoding="utf-8")
            loaded = (
                json.loads(raw_text)
                if cfg_p.suffix.lower() == ".json"
                else (yaml.safe_load(raw_text) or {})
            )
            if isinstance(loaded, dict):
                if isinstance(loaded.get("harness"), dict):
                    harness = dict(loaded["harness"])
                else:
                    harness = dict(loaded)

        if getattr(args, "provider", None):
            harness["provider"] = str(args.provider)

        if getattr(args, "reasoning_model", None) or getattr(args, "fast_model", None):
            overrides = dict(harness.get("model_overrides") or {})
            if getattr(args, "reasoning_model", None):
                overrides["reasoning"] = str(args.reasoning_model)
            if getattr(args, "fast_model", None):
                overrides["fast"] = str(args.fast_model)
            harness["model_overrides"] = overrides

        if getattr(args, "auth_mode", None) or getattr(args, "api_key_env_var", None):
            auth = dict(harness.get("auth") or {})
            if getattr(args, "auth_mode", None):
                auth["auth_mode"] = str(args.auth_mode)
                auth["mode"] = str(args.auth_mode)
            if getattr(args, "api_key_env_var", None):
                auth["api_key_env_var"] = str(args.api_key_env_var)
            harness["auth"] = auth

        if getattr(args, "hold_policy", None):
            harness["steward_hold_policy"] = str(args.hold_policy)
        if getattr(args, "ontology_policy", None):
            harness["ontology_policy"] = str(args.ontology_policy)
        if getattr(args, "stage_output_policy", None):
            harness["stage_output_policy"] = str(args.stage_output_policy)

        max_turns = getattr(args, "max_turns", None)
        timeout_seconds = getattr(args, "timeout_seconds", None)
        if max_turns is not None or timeout_seconds is not None:
            budget = dict(harness.get("budget") or {})
            if max_turns is not None:
                budget["max_turns"] = int(max_turns)
            if timeout_seconds is not None:
                budget["timeout_seconds"] = float(timeout_seconds)
            harness["budget"] = budget

        return harness


__all__ = ["PipelineExecutionMixin"]
