"""Open-DV end-to-end workflow CLI helper covering UI/API, contract continuation, and Q&A."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib import parse as urllib_parse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from opendv_api_client import DEFAULT_API_URL, OpenDVClient, search_repo_docs  # noqa: E402
from opendv_stage_commands import (  # noqa: E402
    run_discover_commit,
    run_evolve_vault,
    run_ingest_parse,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
PROVIDERS = ["agy", "agy-cli", "antigravity", "claude", "claude-cli", "claude-code"]


def _write_json(path_str: str, payload: Any) -> Path:
    out = Path(path_str)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Success! Result written to: {out}")
    return out


def _client_from_args(args: argparse.Namespace) -> OpenDVClient:
    return OpenDVClient(api_url=args.api_url, rps=args.rps, timeout=args.timeout)


def cmd_doctor(args: argparse.Namespace) -> None:
    """Verify server health, bundled UI, agent readiness, and CLI binaries."""
    client = _client_from_args(args)
    health = client.request_json("GET", "/api/v1/health")
    caps = client.request_json("GET", "/api/v1/platform/capabilities")
    cfg = client.request_json("GET", "/api/v1/platform/config")
    providers = cfg.get("providers", [])
    ready = [p["provider"] for p in providers if p.get("available")]
    active = (cfg.get("harness") or {}).get("provider")
    ctl_bin = REPO_ROOT / "apps" / "opendvctl" / "dist" / "release" / "opendvctl-windows-x64.exe"
    _write_json(
        args.output,
        {
            "api_url": client.api_url,
            "health": health,
            "web_ui_bundled": cfg.get("web_ui_bundled"),
            "remote_unauthenticated_mode": cfg.get("remote_unauthenticated_mode"),
            "active_provider": active,
            "active_provider_available": active in ready,
            "available_providers": ready,
            "harness": cfg.get("harness"),
            "capabilities": caps,
            "cli_binaries": {
                "uv": shutil.which("uv"),
                "opendv": shutil.which("opendv"),
                "opendvctl": str(ctl_bin) if ctl_bin.is_file() else shutil.which("opendvctl"),
            },
            "workbench_urls": {
                "wizard": f"{client.api_url}/",
                "dv_schema": f"{client.api_url}/dv-schema",
                "explorer": f"{client.api_url}/explorer",
                "settings": f"{client.api_url}/settings",
            },
        },
    )


def cmd_configure_harness(args: argparse.Namespace) -> None:
    """Validate and optionally persist Agent Harness provider/model/budget config."""
    client = _client_from_args(args)
    cfg = client.request_json("GET", "/api/v1/platform/config")
    harness = dict(cfg.get("harness") or {})
    if args.provider:
        harness["provider"] = args.provider
    overrides = dict(harness.get("model_overrides") or {})
    if args.fast_model:
        overrides["fast"] = args.fast_model
    if args.reasoning_model:
        overrides["reasoning"] = args.reasoning_model
    harness["model_overrides"] = overrides
    budget = dict(harness.get("budget") or {})
    if args.max_turns is not None:
        budget["max_turns"] = args.max_turns
    if args.max_cost_usd is not None:
        budget["max_cost_usd"] = args.max_cost_usd
    if args.timeout_seconds is not None:
        budget["timeout_seconds"] = args.timeout_seconds
    harness["budget"] = budget

    validation = client.request_json(
        "POST", "/api/v1/platform/config/validate", json_body={"harness": harness}
    )
    updated = None
    if not args.dry_run:
        updated = client.request_json(
            "PUT", "/api/v1/platform/config", json_body={"harness": harness, "persist": True}
        )
    _write_json(args.output, {"validation": validation, "updated_config": updated})


def cmd_list_contracts(args: argparse.Namespace) -> None:
    """List Wizard sessions and prior certified DV 2.0 runs for continuation."""
    client = _client_from_args(args)
    sess_resp = client.request_json(
        "GET",
        "/api/v1/ontology/studio/sessions",
        query={"wizard_only": "true", "limit": args.limit},
    )
    runs = client.request_json("GET", "/api/v1/runs")
    certified = [
        {
            "run_id": r.get("run_id"),
            "session_id": r.get("session_id"),
            "status": r.get("status"),
            "started_at_utc": r.get("started_at_utc"),
            "completed_at_utc": r.get("completed_at_utc"),
            "has_initial_contract": r.get("has_initial_contract"),
            "initial_contract_path": r.get("initial_contract_path"),
            "dv_schema_url": f"{client.api_url}/dv-schema?run={r.get('run_id')}",
        }
        for r in (runs or [])
        if str(r.get("status", "")).upper() == "COMPLETED"
        and (not args.session_id or r.get("session_id") == args.session_id)
    ][: args.limit]
    _write_json(
        args.output,
        {
            "wizard_sessions": sess_resp.get("sessions", [])[: args.limit],
            "legacy_session_count": sess_resp.get("legacy_session_count", 0),
            "continuable_certified_runs": certified,
        },
    )


def cmd_explore_schema(args: argparse.Namespace) -> None:
    """Inspect DV Schema Explorer projection, wave diffs, table rows & Safe SQL."""
    client = _client_from_args(args)
    q_rid = urllib_parse.quote(args.run_id)
    schema = client.request_json(
        "GET", f"/api/v1/runs/{q_rid}/dv-schema", query={"compare": args.compare_run_id}
    )
    rows = None
    if args.table:
        q_tbl = urllib_parse.quote(args.table)
        rows = client.request_json(
            "GET",
            f"/api/v1/runs/{q_rid}/dv-schema/tables/{q_tbl}/rows",
            query={"limit": args.limit, "offset": 0},
        )
    sql_result = None
    if args.sql:
        sql_result = client.request_json(
            "POST",
            f"/api/v1/runs/{q_rid}/dv-schema/query",
            json_body={"sql": args.sql, "max_rows": args.limit},
        )
    _write_json(
        args.output,
        {
            "run_id": args.run_id,
            "compare_run_id": args.compare_run_id,
            "dv_schema": schema,
            "table_rows": rows,
            "sql_result": sql_result,
        },
    )


def cmd_export_logs_bundle(args: argparse.Namespace) -> None:
    """Query stage/step-filtered logs and export .jsonl or .odvstudio.zip."""
    client = _client_from_args(args)
    q_sid = urllib_parse.quote(args.session_id)
    logs = client.request_json(
        "GET",
        f"/api/v1/ontology/studio/sessions/{q_sid}/logs",
        query={
            "limit": args.limit,
            "wizard_step": args.wizard_step,
            "stage_id": args.stage_id,
            "persona": args.persona,
        },
    )
    jsonl_out = None
    if args.export_logs_jsonl:
        _, _, raw_jsonl = client.request_raw(
            "GET", f"/api/v1/ontology/studio/sessions/{q_sid}/logs/export"
        )
        jp = Path(args.export_logs_jsonl)
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_bytes(raw_jsonl)
        jsonl_out = str(jp)

    bundle_out = None
    if args.export_bundle_zip:
        _, _, raw_zip = client.request_raw(
            "POST",
            f"/api/v1/ontology/studio/sessions/{q_sid}/bundle",
            json_body={
                "acknowledge_confidential_source_export": bool(args.ack_confidential),
                "export_rationale": args.export_rationale or "Workflow bundle export",
            },
        )
        zp = Path(args.export_bundle_zip)
        zp.parent.mkdir(parents=True, exist_ok=True)
        zp.write_bytes(raw_zip)
        bundle_out = str(zp)

    _write_json(
        args.output,
        {
            "session_id": args.session_id,
            "logs": logs,
            "jsonl_path": jsonl_out,
            "bundle_path": bundle_out,
        },
    )


def cmd_search_docs(args: argparse.Namespace) -> None:
    """Search Open-DV markdown specs and CLI/OpenAPI schemas for Q&A citations."""
    root = Path(args.repo_root).resolve() if args.repo_root else REPO_ROOT
    hits = search_repo_docs(root, args.query, args.scope, args.limit)
    _write_json(
        args.output,
        {"repo_root": str(root), "query": args.query, "scope": args.scope, "hits": hits},
    )


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--api-url", default=DEFAULT_API_URL, help="Open-DV API base URL")
    common.add_argument("--rps", type=float, default=None, help="Requests-per-second ceiling")
    common.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds")

    parser = argparse.ArgumentParser(description="Open-DV unified workflow CLI helper")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doc = sub.add_parser("doctor", parents=[common], help="Verify server, UI, CLI & agent")
    p_doc.add_argument("--output", required=True)
    p_doc.set_defaults(func=cmd_doctor)

    p_cfg = sub.add_parser("configure-harness", parents=[common], help="Configure LLM harness")
    p_cfg.add_argument("--provider", choices=PROVIDERS)
    p_cfg.add_argument("--fast-model")
    p_cfg.add_argument("--reasoning-model")
    p_cfg.add_argument("--max-turns", type=int)
    p_cfg.add_argument("--max-cost-usd", type=float)
    p_cfg.add_argument("--timeout-seconds", type=float)
    p_cfg.add_argument("--dry-run", action="store_true")
    p_cfg.add_argument("--output", required=True)
    p_cfg.set_defaults(func=cmd_configure_harness)

    p_lst = sub.add_parser("list-contracts", parents=[common], help="List sessions & contracts")
    p_lst.add_argument("--limit", type=int, required=True)
    p_lst.add_argument("--session-id")
    p_lst.add_argument("--output", required=True)
    p_lst.set_defaults(func=cmd_list_contracts)

    p_ing = sub.add_parser("ingest-parse", parents=[common], help="Steps 1-3 ingest & parse")
    p_ing.add_argument("--session-id")
    p_ing.add_argument("--domain-namespace", default="general")
    p_ing.add_argument("--enterprise-owner", default="Enterprise Architecture")
    p_ing.add_argument("--from-workspace", nargs="?", const=".", default=None)
    p_ing.add_argument("--import-ontology")
    p_ing.add_argument("--import-format", choices=["yaml", "json", "ttl"])
    p_ing.add_argument("--pack-ids")
    p_ing.add_argument("--connector", action="append")
    p_ing.add_argument("--file", action="append")
    p_ing.add_argument("--note", action="append")
    p_ing.add_argument("--ocr-mode", default="auto", choices=["auto", "force", "off"])
    p_ing.add_argument("--do-not-send", action="store_true")
    p_ing.add_argument("--parse", action="store_true")
    p_ing.add_argument("--limit", type=int, required=True)
    p_ing.add_argument("--output", required=True)
    p_ing.set_defaults(
        func=lambda a: _write_json(a.output, run_ingest_parse(_client_from_args(a), a))
    )

    p_disc = sub.add_parser("discover-commit", parents=[common], help="Steps 4-5 draft & commit")
    p_disc.add_argument("--session-id", required=True)
    p_disc.add_argument("--steering")
    p_disc.add_argument("--skip-construct", action="store_true")
    p_disc.add_argument("--fibo-question")
    p_disc.add_argument("--answer-json")
    p_disc.add_argument("--accept-proposal-id")
    p_disc.add_argument("--include-breaking", action="store_true")
    p_disc.add_argument("--commit-rationale")
    p_disc.add_argument("--export-format", choices=["yaml", "json", "ttl"])
    p_disc.add_argument("--output", required=True)
    p_disc.set_defaults(
        func=lambda a: _write_json(a.output, run_discover_commit(_client_from_args(a), a))
    )

    p_ev = sub.add_parser("evolve-vault", parents=[common], help="Step 6 build or continue DV 2")
    p_ev.add_argument("--session-id")
    p_ev.add_argument(
        "--baseline-mode",
        required=True,
        choices=["latest-session", "run-id", "contract-file", "cold-start"],
    )
    p_ev.add_argument("--baseline-run-id")
    p_ev.add_argument("--deployed-contract-file")
    p_ev.add_argument("--dialects", default="duckdb,databricks")
    p_ev.add_argument("--run-id")
    p_ev.add_argument("--hold-id")
    p_ev.add_argument("--hold-action", choices=["APPROVE", "REJECT", "ANSWER"])
    p_ev.add_argument("--candidate-id")
    p_ev.add_argument("--hold-notes")
    p_ev.add_argument("--output", required=True)
    p_ev.set_defaults(
        func=lambda a: _write_json(a.output, run_evolve_vault(_client_from_args(a), a))
    )

    p_exp = sub.add_parser("explore-schema", parents=[common], help="Explore DV 2 schema & SQL")
    p_exp.add_argument("--run-id", required=True)
    p_exp.add_argument("--compare-run-id")
    p_exp.add_argument("--table")
    p_exp.add_argument("--sql")
    p_exp.add_argument("--limit", type=int, required=True)
    p_exp.add_argument("--output", required=True)
    p_exp.set_defaults(func=cmd_explore_schema)

    p_log = sub.add_parser("export-logs-bundle", parents=[common], help="Logs & .odvstudio.zip")
    p_log.add_argument("--session-id", required=True)
    p_log.add_argument("--wizard-step", type=int)
    p_log.add_argument("--stage-id")
    p_log.add_argument("--persona")
    p_log.add_argument("--limit", type=int, required=True)
    p_log.add_argument("--export-logs-jsonl")
    p_log.add_argument("--export-bundle-zip")
    p_log.add_argument("--ack-confidential", action="store_true")
    p_log.add_argument("--export-rationale")
    p_log.add_argument("--output", required=True)
    p_log.set_defaults(func=cmd_export_logs_bundle)

    p_q = sub.add_parser("search-docs", parents=[common], help="Search Open-DV specs for Q&A")
    p_q.add_argument("--query", required=True)
    p_q.add_argument("--scope", default="all", choices=["all", "docs", "cli", "api", "design"])
    p_q.add_argument("--repo-root")
    p_q.add_argument("--limit", type=int, required=True)
    p_q.add_argument("--output", required=True)
    p_q.set_defaults(func=cmd_search_docs)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Error ({args.command}): {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
