"""In-process LocalExecutor delegating CLI commands to OpenDVService (Decision D6)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from opendv_cli.executors.ontology import OntologyExecutionMixin
from opendv_cli.executors.pipeline import PipelineExecutionMixin
from opendv_cli.executors.platform import PlatformExecutionMixin, _load_platform_server_main
from opendv_cli.executors.source import SourceExecutionMixin
from opendv_cli_common import CommandExecutor, ExecutionResult
from opendv_contracts import (
    CompileRequest,
    ProfileRequest,
    SandboxQueryRequest,
    VerifyRequest,
)
from opendv_contracts.errors import OpenDVError
from opendv_service import (
    CaptainHoldDecisionRequest,
    CreateRunRequest,
    OpenDVService,
    redact_event_for_role,
)
from opendv_service.studio.workspace_security import mint_launch_nonce


class LocalExecutor(
    PipelineExecutionMixin,
    OntologyExecutionMixin,
    SourceExecutionMixin,
    PlatformExecutionMixin,
    CommandExecutor,
):
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
            "source": self._execute_source,
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
        sql_arg: str | None = getattr(args, "sql", None)
        if not run_id and not contract_arg and not ir_arg and not sql_arg:
            return 2, {
                "status": "ERROR",
                "error": (
                    "At least one of --run-id, --canonical-ir, --contract, or --sql is required"
                ),
            }

        effective_run_id = run_id or "run-local-sandbox"
        if sql_arg and sql_arg.strip():
            query_type = "CUSTOM_SQL"
        else:
            query_type = getattr(args, "query_type", None) or "ADVERSARIAL_BATCHES_1_TO_4"
        cutoff = getattr(args, "cutoff", None) or "2026-11-15 23:59:59"

        resp = self.service.execute_sandbox_query(
            SandboxQueryRequest(
                run_id=effective_run_id,
                contract_path=contract_arg,
                canonical_ir_path=ir_arg,
                query_type=query_type,
                assertion_cutoff_ts=cutoff,
                sql=sql_arg,
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
            created = self.service.create_run(
                CreateRunRequest(
                    project_id=project_id,
                    harness_provider=harness,
                    initial_contract_path=initial_contract_path,
                    initial_contract=initial_contract,
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
        if action in {"dv-schema", "dv_schema"}:
            return self._execute_dv_schema(run_id, args)
        if action == "explorer":
            bundle = self.service.get_explorer_bundle(run_id, role=role_raw)
            payload = bundle.model_dump(mode="json", by_alias=True)
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

    def _execute_dv_schema(self, run_id: str, args: argparse.Namespace) -> tuple[int, Any]:
        from opendv_cli.commands.ontology_studio_common import _err_from_opendv_error

        compare_run = getattr(args, "compare", None)
        table = getattr(args, "table", None)
        sql = getattr(args, "sql", None)
        limit = int(getattr(args, "limit", None) or 50)
        offset = int(getattr(args, "offset", None) or 0)
        try:
            if sql:
                res = self.service.query_dv_schema(run_id, sql=str(sql), max_rows=limit)
            elif table:
                res = self.service.get_dv_schema_table_rows(
                    run_id, str(table), limit=limit, offset=offset
                )
            else:
                res = self.service.get_dv_schema(run_id, compare_run_id=compare_run)
            return 0, res.model_dump(mode="json") if hasattr(res, "model_dump") else res
        except OpenDVError as exc:
            return _err_from_opendv_error(exc, wizard_mode=True)
        except ValueError as exc:
            return 40, {
                "status": "ERROR",
                "error_code": "SYNTAX_INVALID_ARGUMENT",
                "detail": str(exc),
            }

    # 6. hold
    def _execute_hold(self, args: argparse.Namespace) -> tuple[int, Any]:
        action = (getattr(args, "hold_action", None) or "list").upper()
        run_id: str | None = getattr(args, "run_id", None)
        if not run_id:
            return 2, {"status": "ERROR", "error": "--run-id is required for 'hold' subcommands"}

        if action == "LIST":
            payload = dict(self.service.list_run_decisions(run_id))
            payload["mode"] = "local"
            return 0, payload

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
            selected_candidate_id=getattr(args, "candidate_id", None)
            or getattr(args, "selected_candidate_id", None),
            resolution_notes=getattr(args, "notes", None)
            or getattr(args, "resolution_notes", None)
            or "Resolved via opendv hold CLI.",
            steward_id=getattr(args, "steward_id", None) or "steward-cli",
        )
        resp = self.service.resolve_captain_hold(run_id, req)
        payload = resp.model_dump(mode="json")
        payload["mode"] = "local"
        return 0, payload

    # 7. pipeline


__all__ = [
    "CommandExecutor",
    "ExecutionResult",
    "LocalExecutor",
    "_load_platform_server_main",
    "mint_launch_nonce",
]
