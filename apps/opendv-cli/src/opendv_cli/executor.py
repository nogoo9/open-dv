"""In-process LocalExecutor delegating CLI commands to OpenDVService (Decision D6)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import opendv_contracts as _contracts_pkg
from opendv_cli_common import CommandExecutor, ExecutionResult
from opendv_contracts import (
    CompileRequest,
    OntologyDecisionRequest,
    OntologyDiffRequest,
    OntologySuggestionRequest,
    OntologyValidateDeltaRequest,
    PlatformConfigResponse,
    PlatformConfigUpdateRequest,
    PlatformConfigValidateRequest,
    PlatformConfigValidationResponse,
    ProfileRequest,
    RbacRole,
    SandboxQueryRequest,
    VerifyRequest,
)
from opendv_contracts.dtos.platform_config import (
    HarnessAuthConfigDTO,
    HarnessBudgetConfigDTO,
    HarnessConfigDTO,
    HarnessSandboxPolicyDTO,
)
from opendv_service.settings import (
    DEFAULT_CORS_ORIGINS,
    DEFAULT_SERVER_HOST,
    DEFAULT_SERVER_PORT,
)


def _load_platform_server_main() -> Any | None:
    """Resolve the registered `opendv-platform` console_scripts entrypoint."""
    from importlib.metadata import entry_points

    for ep in entry_points(group="console_scripts"):
        if ep.name == "opendv-platform":
            try:
                return ep.load()
            except ImportError:
                return None
    return None


for _attr_name, _attr_val in (
    ("HarnessAuthConfigDTO", HarnessAuthConfigDTO),
    ("HarnessBudgetConfigDTO", HarnessBudgetConfigDTO),
    ("HarnessConfigDTO", HarnessConfigDTO),
    ("HarnessSandboxPolicyDTO", HarnessSandboxPolicyDTO),
):
    if not hasattr(_contracts_pkg, _attr_name):
        setattr(_contracts_pkg, _attr_name, _attr_val)

from opendv_service import (  # noqa: E402
    AgenticPipelineReport,
    CaptainHoldDecisionRequest,
    CreateRunRequest,
    OpenDVError,
    OpenDVService,
    PipelineRunRequest,
    PipelineValidateRequest,
    normalize_rbac_role,
    redact_event_for_role,
)


class LocalExecutor(CommandExecutor):
    """Executes Open-DV use cases in-process via OpenDVService without HTTP or FastAPI."""

    def __init__(
        self,
        service: OpenDVService | None = None,
        *,
        workspace_root: Path | None = None,
    ) -> None:
        self.workspace_root = Path(
            workspace_root or os.environ.get("OPENDV_WORKSPACE_ROOT") or Path.cwd()
        ).resolve()
        self.service = service or OpenDVService(workspace_root=self.workspace_root)

    @property
    def _service(self) -> OpenDVService:
        return self.service

    @_service.setter
    def _service(self, value: OpenDVService) -> None:
        self.service = value

    def execute(self, command: str, args: argparse.Namespace) -> tuple[int, Any]:
        """Dispatch a parsed command to its in-process service implementation."""
        handler_map = {
            "verify": self._execute_verify,
            "profile": self._execute_profile,
            "compile": self._execute_compile,
            "sandbox": self._execute_sandbox,
            "run": self._execute_run,
            "hold": self._execute_hold,
            "pipeline": self._execute_pipeline,
            "ontology": self._execute_ontology,
            "config": self._handle_config,
            "serve": self._handle_serve,
        }
        handler = handler_map.get(command)
        if handler is None:
            return 2, {"status": "ERROR", "error": f"Unknown command: '{command}'"}
        try:
            return handler(args)
        except OpenDVError as exc:
            return exc.exit_code, {
                "status": "ERROR",
                "error_code": str(exc.error_code),
                "error": str(exc),
                "detail": str(exc),
            }
        except (ValueError, KeyError, FileNotFoundError) as exc:
            return 2, {
                "status": "ERROR",
                "error_code": "INPUT_VALIDATION_ERROR",
                "error": str(exc),
                "detail": str(exc),
            }

    # 1. verify
    def _execute_verify(self, args: argparse.Namespace) -> tuple[int, Any]:
        contract_arg: str | None = getattr(args, "contract", None)
        if not contract_arg:
            return 2, {"status": "ERROR", "error": "--contract is required"}

        compiled_sql_arg = getattr(args, "compiled_sql", None)
        compiled_sql_content = None
        if compiled_sql_arg:
            sql_p = Path(compiled_sql_arg)
            if sql_p.is_file():
                compiled_sql_content = sql_p.read_text(encoding="utf-8")
            else:
                compiled_sql_content = str(compiled_sql_arg)

        req = VerifyRequest(
            contract_path=contract_arg,
            graph_a_path=getattr(args, "graph_a", None),
            graph_b_path=getattr(args, "graph_b", None),
            prior_contract_path=getattr(args, "prior_contract", None),
            run_id=getattr(args, "run_id", None),
            compiled_sql_models=getattr(args, "compiled_sql_models", None),
            compiled_sql=compiled_sql_content,
            apply_codemods=getattr(args, "apply_codemods", True),
        )
        resp = self.service.verify_pipeline(req)
        payload = resp.model_dump(mode="json")
        payload["mode"] = "local"
        return (0 if resp.overall_passed else 1), payload

    # 2. profile
    def _execute_profile(self, args: argparse.Namespace) -> tuple[int, Any]:
        source_arg: str | None = getattr(args, "source", None)
        file_arg: str | None = getattr(args, "file", None)
        source_csv: str | None = getattr(args, "source_csv", None)
        graph_b: str | None = getattr(args, "graph_b", None)

        target_source: dict[str, Any] | None = None
        if source_arg:
            target_source = {"adapter": "file", "uri": source_arg}
        elif file_arg:
            target_source = {"adapter": "file", "uri": file_arg}

        if not target_source and not source_csv and not graph_b:
            return 2, {
                "status": "ERROR",
                "error": "Either --source, --file, --source-csv, or --graph-b is required",
            }

        project_id: str | None = getattr(args, "project_id", None)
        req = ProfileRequest(
            source=target_source,
            source_path=source_csv,
            graph_b_path=graph_b,
            project_id=project_id,
            source_system=getattr(args, "source_system", None) or "bronze_source",
            table_name=getattr(args, "table_name", None),
        )
        resp = self.service.profile_pipeline(req)
        payload = resp.model_dump(mode="json")
        payload["mode"] = "local"
        if project_id is not None:
            payload["project_id"] = project_id
        if getattr(resp, "source_path", None):
            payload["source_csv_path"] = resp.source_path
            if not (target_source or source_csv):
                payload["graph_b_path"] = resp.source_path
        return 0, payload

    # 3. compile
    def _execute_compile(self, args: argparse.Namespace) -> tuple[int, Any]:
        contract_arg: str | None = getattr(args, "contract", None)
        if not contract_arg:
            return 2, {"status": "ERROR", "error": "--contract is required"}

        req = CompileRequest(
            contract_path=contract_arg,
            canonical_ir_path=getattr(args, "canonical_ir", None),
            run_id=getattr(args, "run_id", None),
        )
        try:
            resp = self.service.compile_pipeline(req)
            payload = resp.model_dump(mode="json")
            payload["mode"] = "local"
            return 0, payload
        except OpenDVError as exc:
            return 1, {
                "status": "ERROR",
                "error_code": str(exc.error_code),
                "error": str(exc),
                "detail": str(exc),
            }

    # 4. sandbox
    def _execute_sandbox(self, args: argparse.Namespace) -> tuple[int, Any]:
        run_id: str | None = getattr(args, "run_id", None)
        contract_arg: str | None = getattr(args, "contract", None)
        ir_arg: str | None = getattr(args, "canonical_ir", None)
        if not run_id and not contract_arg and not ir_arg:
            return 2, {
                "status": "ERROR",
                "error": "At least one of --run-id, --canonical-ir, or --contract is required",
            }

        effective_run_id = run_id or "run-local-sandbox"
        query_type = getattr(args, "query_type", None) or "ADVERSARIAL_BATCHES_1_TO_4"
        cutoff = getattr(args, "cutoff", None) or "2026-11-15 23:59:59"

        resp = self.service.execute_sandbox_query(
            SandboxQueryRequest(
                run_id=effective_run_id,
                contract_path=contract_arg,
                canonical_ir_path=ir_arg,
                query_type=query_type,
                assertion_cutoff_ts=cutoff,
            )
        )
        payload = resp.model_dump(mode="json")
        payload["mode"] = "local"
        return (0 if resp.all_batches_passed else 1), payload

    # 5. run
    def _execute_run(self, args: argparse.Namespace) -> tuple[int, Any]:
        action = getattr(args, "run_action", None) or "get"
        project_id: str | None = getattr(args, "project_id", None)
        run_id: str | None = getattr(args, "run_id", None)
        role_raw: str = getattr(args, "role", None) or "INTERNAL_MODELER"
        harness: str = getattr(args, "harness", None) or "antigravity"

        if action == "list":
            runs = self.service.list_runs(project_id=project_id)
            items = [r.model_dump(mode="json") for r in runs]
            return 0, {"mode": "local", "items": items, "total": len(items)}

        if action == "create":
            if not project_id:
                return 2, {"status": "ERROR", "error": "--project-id is required for 'run create'"}
            initial_contract_path = getattr(args, "initial_contract_path", None) or getattr(
                args, "contract", None
            )
            initial_contract = getattr(args, "initial_contract", None) or getattr(
                args, "initial_contract_inline", None
            )
            wave_id = getattr(args, "wave_id", None)
            created = self.service.create_run(
                CreateRunRequest(
                    project_id=project_id,
                    harness_provider=harness,
                    initial_contract_path=initial_contract_path,
                    initial_contract=initial_contract,
                    wave_id=wave_id,
                )
            )
            payload = created.model_dump(mode="json")
            payload["mode"] = "local"
            return 0, payload

        if not run_id:
            return 2, {"status": "ERROR", "error": f"--run-id is required for 'run {action}'"}
        if action == "abort":
            payload = self.service.abort_run(run_id).model_dump(mode="json")
            payload["mode"] = "local"
            return 0, payload
        if action == "events":
            last_event_id: str | None = getattr(args, "last_event_id", None)
            raw_events = self.service.state.run_events.get(run_id, [])
            if last_event_id is not None:
                matched = [
                    i for i, ev in enumerate(raw_events) if str(ev.get("event_id")) == last_event_id
                ]
                if matched:
                    raw_events = raw_events[matched[0] + 1 :]
            events = [redact_event_for_role(ev, role_raw) for ev in raw_events]
            return 0, {
                "mode": "local",
                "run_id": run_id,
                "role": role_raw,
                "last_event_id": last_event_id,
                "event_count": len(events),
                "events": events,
            }
        if action == "explorer":
            wave_id = getattr(args, "wave_id", None)
            bundle = self.service.get_explorer_bundle(run_id, wave_id=wave_id, role=role_raw)
            payload = bundle.model_dump(mode="json")
            payload["mode"] = "local"
            return 0, payload
        if action == "artifact":
            artifact_id = getattr(args, "artifact_id", None)
            if not artifact_id:
                return 2, {
                    "status": "ERROR",
                    "error": "--artifact-id is required for 'run artifact'",
                }
            artifact = self.service.get_run_artifact(run_id, artifact_id, role=role_raw)
            payload = artifact.model_dump(mode="json")
            payload["mode"] = "local"
            return 0, payload
        payload = self.service.get_run(run_id).model_dump(mode="json")
        payload["mode"] = "local"
        return 0, payload

    # 6. hold
    def _execute_hold(self, args: argparse.Namespace) -> tuple[int, Any]:
        action = (getattr(args, "hold_action", None) or "list").upper()
        run_id: str | None = getattr(args, "run_id", None)
        if not run_id:
            return 2, {"status": "ERROR", "error": "--run-id is required for 'hold' subcommands"}

        if action == "LIST":
            holds = self.service.list_holds(run_id)
            return 0, {"mode": "local", "run_id": run_id, "active_holds": holds, "decisions": []}

        hold_id: str | None = getattr(args, "hold_id", None)
        event_id: str | None = getattr(args, "event_id", None)
        action_arg = getattr(args, "action", None)
        decision_val = (
            action_arg
            or getattr(args, "decision", None)
            or (action if action in {"APPROVE", "REJECT", "ANSWER"} else "APPROVE")
        ).upper()
        if decision_val == "RESOLVE":
            decision_val = "APPROVE"

        if not hold_id or not event_id:
            pending_recs = self.service.state.hold_managers.get(run_id)
            pending_list = pending_recs.list_pending_holds(run_id=run_id) if pending_recs else []
            if len(pending_list) == 1:
                single = pending_list[0]
                hold_id = hold_id or single.hold_id or single.decision_id
                event_id = event_id or single.event_id or hold_id
            else:
                return 2, {
                    "status": "ERROR",
                    "error": "--hold-id and --event-id are required when no single hold is pending",
                }

        req = CaptainHoldDecisionRequest(
            event_id=event_id,
            hold_id=hold_id,
            action=decision_val,
            selected_candidate_id=getattr(args, "candidate_id", None),
            resolution_notes=getattr(args, "notes", None) or "Resolved via opendv hold CLI.",
            steward_id=getattr(args, "steward_id", None) or "steward-cli",
        )
        resp = self.service.resolve_captain_hold(run_id, req)
        payload = resp.model_dump(mode="json")
        payload["mode"] = "local"
        return 0, payload

    # 7. pipeline
    def _execute_pipeline(self, args: argparse.Namespace) -> tuple[int, Any]:
        action = getattr(args, "pipeline_action", None) or "run"
        input_path: str | None = getattr(args, "input", None)
        project_id: str | None = getattr(args, "project_id", None)

        if action in {"run", "validate"} and not input_path:
            return 2, {"status": "ERROR", "error": f"--input is required for 'pipeline {action}'"}

        if action == "validate":
            val_req = PipelineValidateRequest(
                input_spec_path=input_path,
                harness_config_path=getattr(args, "harness_config", None),
                initial_contract_path=getattr(args, "initial_contract", None),
                provider_override=getattr(args, "provider", None),
                steward_hold_policy=getattr(args, "hold_policy", None),
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
                wave_id=getattr(args, "wave_id", None),
                output_dir=getattr(args, "output_dir", None),
                provider_override=getattr(args, "provider", None),
                steward_hold_policy=getattr(args, "hold_policy", None),
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

            def _on_start(wave_idx: int, stage_id: str, persona: str, model: str) -> None:
                short_st = _format_short_stage(stage_id)
                msg = (
                    f"[Wave {wave_idx}][{short_st}] Starting {stage_id} "
                    f"({persona}, model={model})..."
                )
                print(msg, file=target, flush=True)

            def _on_done(wave_idx: int, step_rep: Any) -> None:
                st_id = getattr(step_rep, "stage_id", "")
                p, m = getattr(step_rep, "persona", ""), getattr(step_rep, "model_used", "")
                st_s, sm = getattr(step_rep, "status", ""), getattr(step_rep, "summary", "")
                dur = getattr(step_rep, "duration_ms", 0)
                msg = (
                    f"[Wave {wave_idx}][{_format_short_stage(st_id)}] {st_id} ({p}, model={m}) -> "
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
            payload = dict(res)
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
    def _execute_ontology(self, args: argparse.Namespace) -> tuple[int, Any]:
        action = getattr(args, "ontology_action", None)
        if not action:
            return 2, {
                "status": "ERROR",
                "error": (
                    "Missing ontology subcommand (suggest, validate-delta, diff, accept, reject)"
                ),
            }

        action_norm = action.lower()

        if action_norm == "studio":
            from opendv_cli.commands.ontology_studio import run_ontology_studio_command

            return run_ontology_studio_command(args, executor=self)

        if action_norm == "suggest":
            req = OntologySuggestionRequest(
                base_ontology_path=getattr(args, "base", None),
                empirical_profile_path=getattr(args, "profile", None),
                bronze_source_path=getattr(args, "source", None),
                context_doc_path=getattr(args, "context_doc", None),
                mode=getattr(args, "mode", "DELTA"),
                domain_namespace=getattr(args, "domain_namespace", None),
                recommender_id=getattr(args, "recommender_id", None),
            )
            res = self.service.suggest_ontology(req)
            payload = res.model_dump(mode="json")
            payload["mode"] = "local"
            output_path = getattr(args, "output", None)
            if output_path:
                Path(output_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return 0, payload

        if action_norm == "validate-delta":
            delta_path = getattr(args, "delta", None)
            base_path = getattr(args, "base", None)
            if not delta_path or not base_path:
                return 2, {
                    "status": "ERROR",
                    "error": "--delta and --base are required for 'ontology validate-delta'",
                }
            req = OntologyValidateDeltaRequest(
                delta_path=delta_path,
                base_ontology_path=base_path,
                contract_path=getattr(args, "contract", None),
            )
            val = self.service.validate_ontology_delta(req)
            payload = val.model_dump(mode="json")
            payload["mode"] = "local"
            exit_code = 0 if payload.get("valid", False) else 1
            return exit_code, payload

        if action_norm == "diff":
            base_path = getattr(args, "base", None)
            target_path = getattr(args, "target", None)
            if not base_path or not target_path:
                return 2, {
                    "status": "ERROR",
                    "error": "--base and --target are required for 'ontology diff'",
                }
            req = OntologyDiffRequest(
                base_ontology_path=base_path,
                target_ontology_path=target_path,
            )
            diff_res = self.service.diff_ontology(req)
            payload = diff_res.model_dump(mode="json")
            payload["mode"] = "local"
            return 0, payload

        if action_norm == "accept":
            delta_path = getattr(args, "delta", None)
            base_path = getattr(args, "base", None)
            if not delta_path or not base_path:
                return 2, {
                    "status": "ERROR",
                    "error": "--delta and --base are required for 'ontology accept'",
                }
            op_id_arg = getattr(args, "op_id", None)
            op_ids = None
            if op_id_arg:
                op_ids = [op.strip() for op in op_id_arg.split(",") if op.strip()]

            dec_req = OntologyDecisionRequest(
                action="ACCEPT",
                delta_path=delta_path,
                base_ontology_path=base_path,
                contract_path=getattr(args, "contract", None),
                accepted_op_ids=op_ids,
                decided_by=getattr(args, "decided_by", None) or "steward",
                steward_notes=getattr(args, "notes", None) or "",
            )
            dec_res = self.service.decide_ontology(dec_req)
            payload = dec_res.model_dump(mode="json")
            payload["mode"] = "local"
            output_path = getattr(args, "output", None)
            if output_path and dec_res.resulting_ontology_yaml:
                Path(output_path).write_text(dec_res.resulting_ontology_yaml, encoding="utf-8")
            return 0, payload

        if action_norm == "reject":
            dec_req = OntologyDecisionRequest(
                action="REJECT",
                proposal_id=getattr(args, "proposal_id", None),
                delta_path=getattr(args, "delta", None),
                base_ontology_path=getattr(args, "base", None),
                decided_by=getattr(args, "decided_by", None) or "steward",
                steward_notes=getattr(args, "notes", None) or "",
            )
            dec_res = self.service.decide_ontology(dec_req)
            payload = dec_res.model_dump(mode="json")
            payload["mode"] = "local"
            return 0, payload

        return 2, {
            "status": "ERROR",
            "error": f"Unknown ontology subcommand: '{action}'",
        }

    # 9. config
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

    def _fallback_platform_config_response(
        self,
        *,
        harness_updates: dict[str, Any] | None = None,
        persist: bool = False,
    ) -> dict[str, Any]:
        settings = getattr(self._service, "settings", None)
        state_dir = Path(
            getattr(self._service, "storage_root", None)
            or (getattr(settings, "state_dir", None) if settings else None)
            or (self.workspace_root / ".opendv" / "state")
        ).resolve()
        persisted_path = state_dir / "platform_config.json"

        base_harness: dict[str, Any] = {}
        if persisted_path.is_file():
            try:
                loaded = json.loads(persisted_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    base_harness = dict(loaded.get("harness", loaded))
            except (ValueError, TypeError, KeyError, OSError):
                base_harness = {}

        if harness_updates:
            for k, v in harness_updates.items():
                if isinstance(v, dict) and isinstance(base_harness.get(k), dict):
                    base_harness[k] = {**base_harness[k], **v}
                else:
                    base_harness[k] = v

        harness_dto = HarnessConfigDTO.model_validate(base_harness)
        if persist:
            persisted_path.parent.mkdir(parents=True, exist_ok=True)
            persisted_path.write_text(
                json.dumps({"harness": harness_dto.model_dump(mode="json")}, indent=2),
                encoding="utf-8",
            )

        reasoning = harness_dto.model_overrides.get("reasoning", "gemini-4-argon-high")
        fast = harness_dto.model_overrides.get("fast", "gemini-3.8-flash-high")
        resolved_personas = {
            "vault-first-mate": harness_dto.agent_models.get("vault-first-mate", reasoning),
            "scout-source": harness_dto.agent_models.get("scout-source", fast),
            "scout-unstructured": harness_dto.agent_models.get("scout-unstructured", fast),
            "ship-vault": harness_dto.agent_models.get("ship-vault", reasoning),
        }

        resp = PlatformConfigResponse(
            workspace_root=str(self.workspace_root),
            state_dir=str(state_dir),
            cas_dir=str(state_dir / "cas"),
            ledgers_dir=str(state_dir / "ledgers"),
            server_host=(
                getattr(settings, "server_host", DEFAULT_SERVER_HOST)
                if settings
                else DEFAULT_SERVER_HOST
            ),
            server_port=(
                getattr(settings, "server_port", DEFAULT_SERVER_PORT)
                if settings
                else DEFAULT_SERVER_PORT
            ),
            max_inline_bytes=(
                getattr(settings, "max_inline_bytes", 10 * 1024 * 1024)
                if settings
                else 10 * 1024 * 1024
            ),
            cors_origins=(
                list(getattr(settings, "cors_origins", []))
                if settings
                else list(DEFAULT_CORS_ORIGINS)
            ),
            config_file_path=(
                str(settings.config_file_path)
                if settings and getattr(settings, "config_file_path", None)
                else None
            ),
            persisted_config_path=str(persisted_path),
            web_ui_bundled=False,
            harness=harness_dto,
            resolved_persona_models=resolved_personas,
            providers=[],
        )
        return resp.model_dump(mode="json")

    def _fallback_validate_platform_config(
        self,
        harness_candidate: dict[str, Any],
    ) -> dict[str, Any]:
        provider = str(harness_candidate.get("provider") or "agy")
        auth = harness_candidate.get("auth") or {}
        auth_mode = str(auth.get("auth_mode") or auth.get("mode") or "none")

        if provider in ("agy", "agy-cli") and auth_mode == "aws_bedrock":
            return PlatformConfigValidationResponse(
                passed=False,
                diagnostics=[
                    {
                        "code": "HARNESS_AUTH_INCOMPATIBLE",
                        "severity": "ERROR",
                        "target": "auth.auth_mode",
                        "message": f"Provider '{provider}' does not support aws_bedrock.",
                    }
                ],
                harness=None,
                resolved_persona_models={},
                providers=[],
            ).model_dump(mode="json")

        try:
            dto = HarnessConfigDTO.model_validate(harness_candidate)
            reasoning = dto.model_overrides.get("reasoning", "gemini-4-argon-high")
            fast = dto.model_overrides.get("fast", "gemini-3.8-flash-high")
            return PlatformConfigValidationResponse(
                passed=True,
                diagnostics=[],
                harness=dto,
                resolved_persona_models={
                    "vault-first-mate": dto.agent_models.get("vault-first-mate", reasoning),
                    "scout-source": dto.agent_models.get("scout-source", fast),
                    "scout-unstructured": dto.agent_models.get("scout-unstructured", fast),
                    "ship-vault": dto.agent_models.get("ship-vault", reasoning),
                },
                providers=[],
            ).model_dump(mode="json")
        except (ValueError, TypeError, KeyError) as exc:
            return PlatformConfigValidationResponse(
                passed=False,
                diagnostics=[
                    {
                        "code": "HARNESS_CONFIG_INVALID",
                        "severity": "ERROR",
                        "target": "harness",
                        "message": str(exc),
                    }
                ],
                harness=None,
                resolved_persona_models={},
                providers=[],
            ).model_dump(mode="json")

    def _handle_config(self, args: argparse.Namespace) -> ExecutionResult:
        sub = (
            getattr(args, "config_action", None)
            or getattr(args, "config_subcommand", None)
            or getattr(args, "subcommand_action", None)
            or "show"
        ).lower()

        if sub == "show":
            if hasattr(self._service, "get_platform_config"):
                res = self._service.get_platform_config()
                payload = res.model_dump(mode="json") if hasattr(res, "model_dump") else dict(res)
            else:
                payload = self._fallback_platform_config_response()
            payload["mode"] = "local"
            return 0, payload

        if sub == "set":
            harness_updates = self._build_harness_dict_from_args(args)
            req = PlatformConfigUpdateRequest(harness=harness_updates, persist=True)
            if hasattr(self._service, "update_platform_config"):
                res = self._service.update_platform_config(req)
                payload = res.model_dump(mode="json") if hasattr(res, "model_dump") else dict(res)
            else:
                payload = self._fallback_platform_config_response(
                    harness_updates=harness_updates,
                    persist=True,
                )
            payload["mode"] = "local"
            return 0, payload

        if sub == "validate":
            harness_candidate = self._build_harness_dict_from_args(args)
            req = PlatformConfigValidateRequest(harness=harness_candidate)
            if hasattr(self._service, "validate_platform_config"):
                res = self._service.validate_platform_config(req)
                payload = res.model_dump(mode="json") if hasattr(res, "model_dump") else dict(res)
            else:
                payload = self._fallback_validate_platform_config(harness_candidate)
            payload["mode"] = "local"
            passed = bool(payload.get("passed", False))
            return (0 if passed else 1), payload

        return 2, {
            "status": "ERROR",
            "error": f"Unknown config subcommand: '{sub}'",
        }

    _execute_config = _handle_config

    # 10. serve
    def _handle_serve(self, args: argparse.Namespace) -> ExecutionResult:
        """Launch the unified Platform Server & Web Explorer UI.

        Temporarily sets OPENDV_CONFIG and OPENDV_WORKSPACE_ROOT in os.environ
        for the server lifetime and restores the previous environment on exit.
        """
        import logging

        host = str(getattr(args, "host", None) or DEFAULT_SERVER_HOST)
        port = int(getattr(args, "port", None) or DEFAULT_SERVER_PORT)
        config_path = getattr(args, "config", None)
        workspace_path = getattr(args, "workspace", None)
        open_browser = bool(getattr(args, "open_browser", False) or getattr(args, "open", False))

        prev_config = os.environ.get("OPENDV_CONFIG")
        prev_workspace = os.environ.get("OPENDV_WORKSPACE_ROOT")
        try:
            if config_path:
                os.environ["OPENDV_CONFIG"] = str(Path(config_path).resolve())
            if workspace_path:
                os.environ["OPENDV_WORKSPACE_ROOT"] = str(Path(workspace_path).resolve())
            elif "OPENDV_WORKSPACE_ROOT" not in os.environ:
                os.environ["OPENDV_WORKSPACE_ROOT"] = str(self.workspace_root)

            cli_argv = ["--host", host, "--port", str(port)]
            if config_path:
                cli_argv.extend(["--config", str(config_path)])
            if workspace_path:
                cli_argv.extend(["--workspace", str(workspace_path)])
            if open_browser:
                cli_argv.append("--open")

            launcher_main = _load_platform_server_main()
            if callable(launcher_main):
                rc = launcher_main(cli_argv)
                exit_code = int(rc) if isinstance(rc, int) else 0
                return exit_code, {
                    "status": "OK" if exit_code == 0 else "ERROR",
                    "mode": "local",
                    "host": host,
                    "port": port,
                    "url": f"http://{host}:{port}",
                }

            logging.getLogger(__name__).debug(
                "opendv-platform entrypoint not found; falling back to uvicorn.run"
            )

            timer = None
            if open_browser:
                import threading
                import webbrowser

                timer = threading.Timer(0.5, webbrowser.open, args=[f"http://{host}:{port}"])
                timer.daemon = True
                timer.start()

            import uvicorn

            try:
                uvicorn.run("opendv_platform_api.main:app", host=host, port=port)
            finally:
                if timer is not None:
                    timer.cancel()
            return 0, {
                "status": "OK",
                "mode": "local",
                "host": host,
                "port": port,
                "url": f"http://{host}:{port}",
            }
        finally:
            if prev_config is None:
                os.environ.pop("OPENDV_CONFIG", None)
            else:
                os.environ["OPENDV_CONFIG"] = prev_config
            if prev_workspace is None:
                os.environ.pop("OPENDV_WORKSPACE_ROOT", None)
            else:
                os.environ["OPENDV_WORKSPACE_ROOT"] = prev_workspace

    _execute_serve = _handle_serve


__all__ = ["LocalExecutor"]
