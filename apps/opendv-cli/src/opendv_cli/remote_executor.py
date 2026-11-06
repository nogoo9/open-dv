"""RemoteExecutor delegating CLI commands to Open-DV Platform API over HTTP & SSE (D1, D2, D6)."""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from opendv_cli.remote_sse import consume_sse_stream
from opendv_cli_common import CommandExecutor


def _handle_api_res(
    res: Any,
    *,
    ok_pred: Callable[[dict[str, Any]], bool] | None = None,
) -> tuple[int, Any]:
    if res.status_code >= 400:
        if "application/json" in res.headers.get("content-type", ""):
            return 2, res.json()
        return 2, {"status": "ERROR", "detail": res.text}
    payload = res.json()
    payload["mode"] = "remote"
    rc = 0 if (ok_pred is None or ok_pred(payload)) else 1
    return rc, payload


class RemoteExecutor(CommandExecutor):
    """Executes Open-DV commands against a remote Open-DV Platform API server."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        client: Any = None,
        role: str = "INTERNAL_MODELER",
        timeout: float = 60.0,
    ) -> None:
        if base_url is None:
            import os

            api_host = os.environ.get("OPENDV_API_HOST", "0.0.0.0")
            api_port = os.environ.get("OPENDV_API_PORT", "8000")
            base_url = os.environ.get("OPENDV_API_URL", f"http://{api_host}:{api_port}")
        self.base_url = base_url.rstrip("/")
        self._external_client = client
        self._owned_client: Any = None
        self.role = role
        self.timeout = timeout

    def _get_client(self) -> Any:
        if self._external_client is not None:
            return self._external_client
        if self._owned_client is None:
            import importlib

            httpx_mod = importlib.import_module("httpx")
            self._owned_client = httpx_mod.Client(base_url=self.base_url, timeout=self.timeout)
        return self._owned_client

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()
            self._owned_client = None

    def __enter__(self) -> RemoteExecutor:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def execute(self, command: str, args: argparse.Namespace) -> tuple[int, Any]:
        """Dispatch a parsed command to its remote REST/SSE implementation."""
        target_fn = getattr(self, f"_execute_{command}", None)
        if target_fn is None or command.startswith("_"):
            return 2, {"status": "ERROR", "error": f"Unknown command: '{command}'"}
        try:
            return target_fn(args)
        except Exception as exc:
            import importlib

            try:
                mod_httpx = importlib.import_module("httpx")
                is_net_err = isinstance(exc, mod_httpx.HTTPError)
            except (ImportError, AttributeError):
                is_net_err = False
            if is_net_err:
                msg = str(exc)
                return 2, {
                    "status": "ERROR",
                    "error_code": "NETWORK_ERROR",
                    "error": msg,
                    "detail": msg,
                }
            if isinstance(exc, (ValueError, KeyError, FileNotFoundError)):
                msg = str(exc)
                return 2, {
                    "status": "ERROR",
                    "error_code": "INPUT_VALIDATION_ERROR",
                    "error": msg,
                    "detail": msg,
                }
            raise

    # 1. verify
    def _execute_verify(self, args: argparse.Namespace) -> tuple[int, Any]:
        contract_arg: str | None = getattr(args, "contract", None)
        if not contract_arg:
            return 2, {"status": "ERROR", "error": "--contract is required"}

        client = self._get_client()
        body: dict[str, Any] = {"contract_path": contract_arg}
        p = Path(contract_arg)
        if p.is_file():
            body["contract_content"] = p.read_text(encoding="utf-8")

        for key, field in [
            ("graph_a", "graph_a_path"),
            ("graph_b", "graph_b_path"),
            ("prior_contract", "prior_contract_path"),
            ("run_id", "run_id"),
        ]:
            val = getattr(args, key, None)
            if val is not None:
                body[field] = val
                vp = Path(val)
                if vp.is_file():
                    content_field = field.replace("_path", "_content")
                    with contextlib.suppress(Exception):
                        body[content_field] = vp.read_text(encoding="utf-8")

        role_raw = getattr(args, "role", None) or self.role
        res = client.post("/api/v1/verify", json=body, headers={"X-OpenDV-Role": role_raw})
        return _handle_api_res(res, ok_pred=lambda d: bool(d.get("overall_passed", False)))

    # 2. profile
    def _execute_profile(self, args: argparse.Namespace) -> tuple[int, Any]:
        src_uri = getattr(args, "source", None) or getattr(args, "file", None)
        target_source = {"adapter": "file", "uri": src_uri} if src_uri else None
        source_csv = getattr(args, "source_csv", None)
        graph_b = getattr(args, "graph_b", None)

        if not any((target_source, source_csv, graph_b)):
            return 2, {
                "status": "ERROR",
                "error": "Either --source, --file, --source-csv, or --graph-b is required",
            }

        client = self._get_client()
        body: dict[str, Any] = {
            "source": target_source,
            "source_path": source_csv,
            "graph_b_path": graph_b,
            "project_id": getattr(args, "project_id", None),
            "source_system": getattr(args, "source_system", None) or "bronze_source",
            "table_name": getattr(args, "table_name", None),
        }
        res = client.post("/api/v1/profile", json=body)
        return _handle_api_res(res)

    # 3. compile
    def _execute_compile(self, args: argparse.Namespace) -> tuple[int, Any]:
        contract_arg: str | None = getattr(args, "contract", None)
        if not contract_arg:
            return 2, {"status": "ERROR", "error": "--contract is required"}

        client = self._get_client()
        body: dict[str, Any] = {
            "contract_path": contract_arg,
            "canonical_ir_path": getattr(args, "canonical_ir", None),
            "run_id": getattr(args, "run_id", None),
        }
        p = Path(contract_arg)
        if p.is_file():
            body["contract_content"] = p.read_text(encoding="utf-8")

        res = client.post("/api/v1/compile", json=body)
        return _handle_api_res(res)

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

        client = self._get_client()
        body: dict[str, Any] = {
            "run_id": run_id or "run-remote-sandbox",
            "contract_path": contract_arg,
            "canonical_ir_path": ir_arg,
            "query_type": getattr(args, "query_type", None) or "ADVERSARIAL_BATCHES_1_TO_4",
            "assertion_cutoff_ts": getattr(args, "cutoff", None) or "2026-11-15 23:59:59",
        }
        endpoint = f"/api/v1/runs/{run_id}/sandbox" if run_id else "/api/v1/sandbox"
        res = client.post(endpoint, json=body)
        return _handle_api_res(res, ok_pred=lambda d: bool(d.get("all_batches_passed", False)))

    # 5. run
    def _execute_run(self, args: argparse.Namespace) -> tuple[int, Any]:
        action = getattr(args, "run_action", None) or "get"
        project_id: str | None = getattr(args, "project_id", None)
        run_id: str | None = getattr(args, "run_id", None)
        client = self._get_client()

        if action == "list":
            params = {}
            if project_id:
                params["project_id"] = project_id
            res = client.get("/api/v1/runs", params=params)
            items = res.json()
            return 0, {"mode": "remote", "items": items, "total": len(items)}

        if action == "create":
            if not project_id:
                return 2, {"status": "ERROR", "error": "--project-id is required for 'run create'"}
            harness = getattr(args, "harness", None) or "antigravity"
            body: dict[str, Any] = {
                "project_id": project_id,
                "harness_provider": harness,
                "initial_contract_path": getattr(args, "initial_contract_path", None)
                or getattr(args, "contract", None),
                "initial_contract": getattr(args, "initial_contract", None)
                or getattr(args, "initial_contract_inline", None),
                "wave_id": getattr(args, "wave_id", None),
            }
            res = client.post(
                "/api/v1/runs",
                json=body,
            )
            return _handle_api_res(res)

        if not run_id:
            return 2, {"status": "ERROR", "error": f"--run-id is required for 'run {action}'"}
        if action == "abort":
            res = client.post(f"/api/v1/runs/{run_id}/abort")
            return _handle_api_res(res)

        res = client.get(f"/api/v1/runs/{run_id}")
        return _handle_api_res(res)

    # 6. hold
    def _execute_hold(self, args: argparse.Namespace) -> tuple[int, Any]:
        action = (getattr(args, "hold_action", None) or "list").upper()
        run_id: str | None = getattr(args, "run_id", None)
        if not run_id:
            return 2, {"status": "ERROR", "error": "--run-id is required for 'hold' subcommands"}

        client = self._get_client()
        if action == "LIST":
            res = client.get(f"/api/v1/runs/{run_id}/decisions")
            return _handle_api_res(res)

        decision_val = (
            getattr(args, "decision", None)
            or (action if action in {"APPROVE", "REJECT", "ANSWER"} else "APPROVE")
        ).upper()
        if decision_val == "RESOLVE":
            decision_val = "APPROVE"

        body = {
            "hold_id": getattr(args, "hold_id", None),
            "event_id": getattr(args, "event_id", None),
            "action": decision_val,
            "selected_candidate_id": getattr(args, "candidate_id", None),
            "resolution_notes": getattr(args, "notes", None) or "Resolved via remote CLI.",
            "steward_id": getattr(args, "steward_id", None) or "steward-cli",
        }
        res = client.post(f"/api/v1/runs/{run_id}/decisions", json=body)
        return _handle_api_res(res)

    # 7. pipeline
    def _execute_pipeline(self, args: argparse.Namespace) -> tuple[int, Any]:
        action = getattr(args, "pipeline_action", None) or "run"
        input_path: str | None = getattr(args, "input", None)
        client = self._get_client()
        role_raw = getattr(args, "role", None) or self.role
        headers = {"X-OpenDV-Role": role_raw}

        if action in {"run", "validate"} and not input_path:
            return 2, {"status": "ERROR", "error": f"--input is required for 'pipeline {action}'"}

        if action == "validate":
            body: dict[str, Any] = {"input_spec_path": input_path}
            p = Path(input_path)
            if p.is_file():
                with contextlib.suppress(Exception):
                    body["input_spec"] = yaml.safe_load(p.read_text(encoding="utf-8"))
            for k in ("harness_config", "initial_contract", "provider", "hold_policy"):
                v = getattr(args, k, None)
                if v is not None:
                    target_k = (
                        "provider_override"
                        if k == "provider"
                        else ("steward_hold_policy" if k == "hold_policy" else f"{k}_path")
                    )
                    body[target_k] = v
            res = client.post("/api/v1/pipelines/validate", json=body, headers=headers)
            return _handle_api_res(res, ok_pred=lambda d: bool(d.get("passed", False)))

        if action == "report":
            run_id = getattr(args, "run_id", None)
            if not run_id:
                return 2, {"status": "ERROR", "error": "--run-id is required for remote report"}
            res = client.get(f"/api/v1/runs/{run_id}/report", headers=headers)
            return _handle_api_res(res)

        return self._follow_pipeline_run(client, args, headers)

    def _follow_pipeline_run(
        self,
        client: Any,
        args: argparse.Namespace,
        headers: dict[str, str],
    ) -> tuple[int, Any]:
        input_path: str = getattr(args, "input", "")
        req_body: dict[str, Any] = {"input_spec_path": input_path}
        p = Path(input_path)
        if p.is_file():
            with contextlib.suppress(Exception):
                req_body["input_spec"] = yaml.safe_load(p.read_text(encoding="utf-8"))

        if getattr(args, "harness_config", None):
            hp = Path(args.harness_config)
            if hp.is_file():
                with contextlib.suppress(Exception):
                    req_body["harness_config"] = yaml.safe_load(hp.read_text(encoding="utf-8"))
                if "harness_config" not in req_body:
                    req_body["harness_config_path"] = args.harness_config
            else:
                req_body["harness_config_path"] = args.harness_config

        if getattr(args, "initial_contract", None):
            req_body["initial_contract_path"] = args.initial_contract
        if getattr(args, "initial_contract_path", None):
            req_body["initial_contract_path"] = args.initial_contract_path
        if getattr(args, "initial_contract_inline", None):
            req_body["initial_contract"] = args.initial_contract_inline
        if getattr(args, "wave_id", None):
            req_body["wave_id"] = args.wave_id
        if getattr(args, "output_dir", None):
            req_body["output_dir"] = args.output_dir
        if getattr(args, "provider", None):
            req_body["provider_override"] = args.provider
        if getattr(args, "hold_policy", None):
            req_body["steward_hold_policy"] = args.hold_policy

        # Step 1: Submit POST /api/v1/pipelines/runs -> 202 Accepted with PipelineRunHandle
        res_submit = client.post("/api/v1/pipelines/runs", json=req_body, headers=headers)
        if res_submit.status_code not in {201, 202}:
            if "application/json" in res_submit.headers.get("content-type", ""):
                return 2, res_submit.json()
            return 2, {"status": "ERROR", "detail": res_submit.text}

        handle = res_submit.json()
        run_id = handle["run_id"]
        events_url = (
            handle.get("events_sse_url")
            or handle.get("event_stream_url")
            or f"/api/v1/runs/{run_id}/events"
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

        # Step 2: Follow SSE event stream
        consume_sse_stream(
            client=client,
            events_url=events_url,
            headers=headers,
            stream_enabled=stream_enabled,
            tokens_enabled=tokens_enabled,
            target=target,
        )

        # Step 3: Fetch final report
        res_rep = client.get(f"/api/v1/runs/{run_id}/report", headers=headers)
        if res_rep.status_code == 200:
            report = res_rep.json()
            report["mode"] = "remote"
            passed = bool(report.get("overall_passed", False)) or (
                report.get("status") == "PAUSED_HOLD"
            )
            return (0 if passed else 1), report

        poll_run = client.get(f"/api/v1/runs/{run_id}")
        if poll_run.status_code == 200:
            rd = poll_run.json()
            rd["mode"] = "remote"
            st = rd.get("status", "COMPLETED")
            passed = st in {"COMPLETED", "PAUSED_HOLD"}
            return (0 if passed else 1), rd
        return 2, {
            "status": "ERROR",
            "error": f"Failed to retrieve final report for run '{run_id}'",
        }


__all__ = ["RemoteExecutor"]
