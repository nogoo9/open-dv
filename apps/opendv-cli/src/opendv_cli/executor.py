"""In-process LocalExecutor delegating CLI commands to OpenDVService (Decision D6)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from opendv_cli_common import CommandExecutor
from opendv_contracts import (
    CompileRequest,
    ProfileRequest,
    RbacRole,
    SandboxQueryRequest,
    VerifyRequest,
)
from opendv_service import (
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
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.service = service or OpenDVService(workspace_root=self.workspace_root)

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
        except Exception as exc:
            return 2, {
                "status": "ERROR",
                "error_code": "INTERNAL_ERROR",
                "error": str(exc),
                "detail": str(exc),
            }

    # 1. verify
    def _execute_verify(self, args: argparse.Namespace) -> tuple[int, Any]:
        contract_arg: str | None = getattr(args, "contract", None)
        if not contract_arg:
            return 2, {"status": "ERROR", "error": "--contract is required"}

        req = VerifyRequest(
            contract_path=contract_arg,
            graph_a_path=getattr(args, "graph_a", None),
            graph_b_path=getattr(args, "graph_b", None),
            prior_contract_path=getattr(args, "prior_contract", None),
            run_id=getattr(args, "run_id", None),
        )
        resp = self.service.verify_pipeline(req)
        verdict_str = resp.verdict.value if hasattr(resp.verdict, "value") else str(resp.verdict)
        return (0 if resp.overall_passed else 1), {
            "mode": "local",
            "run_id": resp.run_id,
            "status": resp.status,
            "verdict": verdict_str,
            "overall_passed": resp.overall_passed,
            "contract_path": resp.contract_path,
            "applied_codemods": list(resp.applied_codemods),
            "diagnostics": list(resp.diagnostics),
            "toon_text": resp.toon_text,
            "gate_scorecard": resp.gate_scorecard.model_dump(mode="json"),
        }

    # 2. profile
    def _execute_profile(self, args: argparse.Namespace) -> tuple[int, Any]:
        source_csv: str | None = getattr(args, "source_csv", None)
        graph_b: str | None = getattr(args, "graph_b", None)
        if not source_csv and not graph_b:
            return 2, {"status": "ERROR", "error": "Either --graph-b or --source-csv is required"}

        project_id: str | None = getattr(args, "project_id", None)
        req = ProfileRequest(
            source_path=source_csv,
            graph_b_path=graph_b,
            project_id=project_id,
            source_system=getattr(args, "source_system", None) or "bronze_source",
            table_name=getattr(args, "table_name", None),
        )
        resp = self.service.profile_pipeline(req)
        base: dict[str, Any] = {
            "mode": "local",
            "project_id": project_id,
            "status": "PASS",
            "profile_id": resp.profile_id,
            "table_count": resp.table_count,
            "column_count": resp.column_count,
        }
        if source_csv:
            base.update({
                "source_system": resp.source_system,
                "table_name": resp.table_name,
                "source_csv_path": resp.source_path,
                "graph_b_profile": resp.graph_b_profile,
            })
        else:
            base["graph_b_path"] = resp.source_path
        return 0, base

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
        resp = self.service.compile_pipeline(req)
        return 0, {
            "mode": "local",
            "run_id": resp.run_id,
            "status": resp.status,
            "contract_path": resp.contract_path,
            "canonical_ir_path": resp.canonical_ir_path,
            "canonical_ir_sha256": resp.canonical_ir_sha256,
            "hubs_compiled": resp.hubs_compiled,
            "links_compiled": resp.links_compiled,
            "satellites_compiled": resp.satellites_compiled,
            "topological_load_order": resp.topological_load_order,
            "sql_zones": resp.sql_zones,
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
        result: dict[str, Any] = {
            "mode": "local",
            "run_id": resp.run_id,
            "query_type": resp.query_type,
            "assertion_cutoff_ts": resp.assertion_cutoff_ts,
            "all_batches_passed": resp.all_batches_passed,
            "batch_verdicts": resp.batch_verdicts,
            "batch_report": {
                "all_passed": resp.all_batches_passed,
                "batches": [b.model_dump(mode="json") for b in resp.batches],
            },
            "dynamic_check_report": {
                "passed_count": resp.dynamic_rules_passed,
                "failed_count": resp.dynamic_rules_total - resp.dynamic_rules_passed,
                "checks": resp.rows if resp.query_type == "VERIFY_DYN_001_015" else [],
            },
            "dynamic_rules_passed": resp.dynamic_rules_passed,
            "dynamic_rules_total": resp.dynamic_rules_total,
            "replay_delta_rows": resp.replay_delta_rows,
            "row_count": resp.row_count,
            "rows": resp.rows,
            "query_d10_rows": resp.query_d10_rows,
            "query_d10b_rows": resp.query_d10b_rows,
        }
        return (0 if resp.all_batches_passed else 1), result

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
            created = self.service.create_run(
                CreateRunRequest(project_id=project_id, harness_provider=harness)
            )
            return 0, created.model_dump(mode="json")

        if not run_id:
            return 2, {"status": "ERROR", "error": f"--run-id is required for 'run {action}'"}
        if action == "abort":
            return 0, self.service.abort_run(run_id).model_dump(mode="json")
        if action == "events":
            last_event_id: str | None = getattr(args, "last_event_id", None)
            raw_events = self.service.state.run_events.get(run_id, [])
            if last_event_id is not None:
                matched = [
                    i for i, ev in enumerate(raw_events)
                    if str(ev.get("event_id")) == last_event_id
                ]
                if matched:
                    raw_events = raw_events[matched[0] + 1:]
            events = [redact_event_for_role(ev, role_raw) for ev in raw_events]
            return 0, {
                "mode": "local",
                "run_id": run_id,
                "role": role_raw,
                "last_event_id": last_event_id,
                "event_count": len(events),
                "events": events,
            }
        return 0, self.service.get_run(run_id).model_dump(mode="json")

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
        decision_val = (
            getattr(args, "decision", None)
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
        return 0, resp.model_dump(mode="json")

    # 7. pipeline
    def _execute_pipeline(self, args: argparse.Namespace) -> tuple[int, Any]:
        action = getattr(args, "pipeline_action", None) or "run"
        input_path: str | None = getattr(args, "input", None)

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
            return (0 if res.get("passed", False) else 1), res

        if action == "run":
            role_raw = getattr(args, "role", None) or "INTERNAL_MODELER"
            eff_role = normalize_rbac_role(role_raw, default=RbacRole.INTERNAL_MODELER)
            run_req = PipelineRunRequest(
                input_spec_path=input_path,
                harness_config_path=getattr(args, "harness_config", None),
                initial_contract_path=getattr(args, "initial_contract", None),
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
            return (0 if passed else 1), res

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
            return 0, d

        assert run_id is not None
        rep = self.service.get_pipeline_report(run_id, role=eff_role)
        return 0, rep


__all__ = ["LocalExecutor"]
