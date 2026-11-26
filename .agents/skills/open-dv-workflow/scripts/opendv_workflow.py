"""CLI entrypoint for end-to-end Open-DV V2 workflows via `/api/v1/projects`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from opendv_api_client import (
    DEFAULT_API_URL,
    OpenDVClient,
    slug_id,
    write_json_output,
)
from opendv_stage_commands import (
    cmd_evolve_vault,
    cmd_explore_schema,
    cmd_export_logs_bundle,
    cmd_search_docs,
)

TERMINAL_ONTOLOGY_STATUSES = {
    "COMPLETED",
    "FAILED",
    "HOLD_CHECKPOINTED",
    "UNCERTIFIED_DRAFT",
}


def cmd_doctor(client: OpenDVClient, args: argparse.Namespace) -> dict[str, Any]:
    """Run read-only `/api/v1/system/doctor`, optionally probing the active harness first."""
    probed: dict[str, Any] | None = None
    if args.probe:
        settings = client.get_json("/api/v1/settings")
        probed = dict(
            client.post_json(
                "/api/v1/agents/readiness/test",
                {"provider": settings["harness_provider"]},
            )
        )
    doc = dict(client.get_json("/api/v1/system/doctor"))
    if probed is not None:
        providers = doc["readiness"]["providers"]
        orig_rec = next((p for p in providers if p["provider"] == probed["provider"]), None)
        doc["readiness"]["providers"] = [
            probed if p["provider"] == probed["provider"] else p for p in providers
        ]
        if probed["provider"] == doc["harness_provider"]:
            remediations = list(doc.get("remediation") or [])
            if orig_rec and orig_rec.get("remediation") and remediations:
                remediations = remediations[1:]
            if probed.get("remediation"):
                remediations.insert(0, probed["remediation"])
            doc["remediation"] = remediations
    return {"api_url": client.api_url, **doc}


def cmd_configure_harness(client: OpenDVClient, args: argparse.Namespace) -> dict[str, Any]:
    """Inspect or update V2 workspace harness settings."""
    current = dict(client.get_json("/api/v1/settings"))
    if args.provider:
        current["harness_provider"] = args.provider
    if args.cegar_policy:
        current["cegar_policy"] = args.cegar_policy
    if args.v7_critic_tier:
        current["v7_critic_tier"] = args.v7_critic_tier
    if args.dbt_dialect:
        current["dbt_sql_dialect"] = args.dbt_dialect
    if args.max_turns is not None:
        current["max_turns"] = args.max_turns
    if args.max_tokens is not None:
        current["max_tokens"] = args.max_tokens
    if args.timeout_seconds is not None:
        current["timeout_seconds"] = args.timeout_seconds
    if args.dry_run:
        return {"dry_run": True, "proposed_settings": current}
    updated = client.put_json("/api/v1/settings", current)
    return {"dry_run": False, "settings": updated}


def cmd_list_contracts(client: OpenDVClient, args: argparse.Namespace) -> dict[str, Any]:
    """List Open-DV V2 projects and optional run timelines."""
    projects = client.get_json("/api/v1/projects")
    items = projects.get("projects", projects) if isinstance(projects, dict) else projects
    if not isinstance(items, list):
        items = []
    if args.project_id:
        items = [p for p in items if p.get("project_id") == args.project_id]
    limited = items[: args.limit]
    runs_by_project: dict[str, Any] = {}
    for proj in limited:
        pid = str(proj.get("project_id") or "")
        if pid:
            runs_by_project[pid] = client.get_json(f"/api/v1/projects/{pid}/runs")
    return {"projects": limited, "runs_by_project": runs_by_project}


def cmd_ingest_parse(client: OpenDVClient, args: argparse.Namespace) -> dict[str, Any]:
    """Initialize/load a V2 project and attach structured or unstructured evidence sources."""
    project_id = args.project_id or slug_id(args.domain_namespace)
    existing = client.get_json("/api/v1/projects")
    raw_list = existing.get("projects", existing) if isinstance(existing, dict) else existing or []
    existing_ids = {
        str(p.get("project_id"))
        for p in raw_list
        if isinstance(p, dict)
    }
    if project_id not in existing_ids:
        init_body: dict[str, Any] = {
            "project_id": project_id,
            "domain_namespace": args.domain_namespace,
        }
        if args.starter_pack_id:
            init_body["starter_pack_id"] = args.starter_pack_id
        if args.workspace_seed_id:
            init_body["workspace_seed_id"] = args.workspace_seed_id
        client.post_json("/api/v1/projects", init_body)

    attached: list[Any] = []
    for uri in args.uri:
        kind = args.kind or Path(uri).suffix.lstrip(".").lower() or "csv"
        attached.append(
            client.post_json(
                f"/api/v1/projects/{project_id}/sources",
                {"kind": kind, "uri_or_filename": uri},
            )
        )

    for file_path in args.file:
        path_obj = Path(file_path)
        with path_obj.open("rb") as handle:
            attached.append(
                client.request(
                    "POST",
                    f"/api/v1/projects/{project_id}/sources/upload",
                    files={"file": (path_obj.name, handle, "application/octet-stream")},
                )
            )

    if args.note:
        attached.append(
            client.request(
                "POST",
                f"/api/v1/projects/{project_id}/sources/upload",
                files={"file": ("inline_note.md", args.note.encode("utf-8"), "text/markdown")},
            )
        )

    tables_index = client.get_json(f"/api/v1/projects/{project_id}/evidence/tables/index")
    doc_map = client.get_json(f"/api/v1/projects/{project_id}/evidence/documents/map")
    return {
        "project_id": project_id,
        "attached": attached[: args.limit],
        "tables_index": tables_index,
        "document_map": doc_map,
    }


def cmd_discover_commit(client: OpenDVClient, args: argparse.Namespace) -> dict[str, Any]:
    """Run ontology discovery, optional Ask FIBO Q&A, CAS commit, and ontology export."""
    project_id = args.project_id
    construct_body: dict[str, Any] = {}
    if args.discovery_mode is not None:
        construct_body["discovery_mode"] = args.discovery_mode
    if args.hint:
        construct_body["hint"] = args.hint
    run_state = client.post_json(
        f"/api/v1/projects/{project_id}/ontology/construct",
        construct_body,
    )
    run_id = str(run_state.get("run_id") or "")
    if args.wait and run_id:
        run_state = client.poll_until(
            f"/api/v1/projects/{project_id}/ontology/runs/{run_id}",
            terminal_statuses=TERMINAL_ONTOLOGY_STATUSES,
            timeout_seconds=args.wait_seconds,
        )

    fibo_result: Any = None
    if args.fibo_question:
        fibo_result = client.post_json(
            f"/api/v1/projects/{project_id}/ontology/fibo-qa",
            {"query": args.fibo_question, "top_k": 5},
        )

    exported = client.get_json(
        f"/api/v1/projects/{project_id}/ontology/export",
        params={"format": args.export_format},
    )
    return {
        "project_id": project_id,
        "ontology_run": run_state,
        "fibo_qa": fibo_result,
        "exported": exported,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open-DV V2 workflow automation helper")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--rps", type=float, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p_doc = sub.add_parser("doctor")
    p_doc.add_argument("--probe", action="store_true")
    p_doc.add_argument("--output")

    p_cfg = sub.add_parser("configure-harness")
    p_cfg.add_argument("--provider")
    p_cfg.add_argument("--cegar-policy")
    p_cfg.add_argument("--v7-critic-tier")
    p_cfg.add_argument("--dbt-dialect")
    p_cfg.add_argument("--max-turns", type=int)
    p_cfg.add_argument("--max-tokens", type=int)
    p_cfg.add_argument("--timeout-seconds", type=int)
    p_cfg.add_argument("--dry-run", action="store_true")
    p_cfg.add_argument("--output")

    p_list = sub.add_parser("list-contracts")
    p_list.add_argument("--project-id")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--output")

    p_ing = sub.add_parser("ingest-parse")
    p_ing.add_argument("--project-id")
    p_ing.add_argument("--domain-namespace", default="asset_management")
    p_ing.add_argument("--starter-pack-id")
    p_ing.add_argument("--workspace-seed-id")
    p_ing.add_argument("--kind")
    p_ing.add_argument("--uri", action="append", default=[])
    p_ing.add_argument("--file", action="append", default=[])
    p_ing.add_argument("--note")
    p_ing.add_argument("--limit", type=int, default=20)
    p_ing.add_argument("--output")

    p_disc = sub.add_parser("discover-commit")
    p_disc.add_argument("--project-id", required=True)
    p_disc.add_argument("--discovery-mode", default=None)
    p_disc.add_argument("--hint", default="")
    p_disc.add_argument("--fibo-question")
    p_disc.add_argument("--export-format", default="yaml", choices=["yaml", "json", "ttl"])
    p_disc.add_argument("--wait", action="store_true", default=True)
    p_disc.add_argument("--wait-seconds", type=float, default=180.0)
    p_disc.add_argument("--output")

    p_vault = sub.add_parser("evolve-vault")
    p_vault.add_argument("--project-id", required=True)
    p_vault.add_argument("--policy", default=None)
    p_vault.add_argument("--targets", default="")
    p_vault.add_argument("--dbt-dialect", default="postgres")
    p_vault.add_argument("--run-id")
    p_vault.add_argument("--answers-json")
    p_vault.add_argument("--horizon-action")
    p_vault.add_argument("--horizon-notes")
    p_vault.add_argument("--wait", action="store_true", default=True)
    p_vault.add_argument("--wait-seconds", type=float, default=240.0)
    p_vault.add_argument("--output")

    p_exp = sub.add_parser("explore-schema")
    p_exp.add_argument("--project-id", required=True)
    p_exp.add_argument("--sql")
    p_exp.add_argument("--limit", type=int, default=50)
    p_exp.add_argument("--output")

    p_logs = sub.add_parser("export-logs-bundle")
    p_logs.add_argument("--project-id", required=True)
    p_logs.add_argument("--run-id")
    p_logs.add_argument("--since-seq", type=int, default=0)
    p_logs.add_argument("--limit", type=int, default=200)
    p_logs.add_argument("--export-bundle-zip")
    p_logs.add_argument("--full-demo", action="store_true")
    p_logs.add_argument("--output")

    p_search = sub.add_parser("search-docs")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--scope", default="all", choices=["all", "docs", "cli", "api", "design"])
    p_search.add_argument("--limit", type=int, default=8)
    p_search.add_argument("--output")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "search-docs":
        write_json_output(args.output, cmd_search_docs(args))
        return 0

    client = OpenDVClient(args.api_url, rps=args.rps)
    try:
        dispatch = {
            "doctor": lambda: cmd_doctor(client, args),
            "configure-harness": lambda: cmd_configure_harness(client, args),
            "list-contracts": lambda: cmd_list_contracts(client, args),
            "ingest-parse": lambda: cmd_ingest_parse(client, args),
            "discover-commit": lambda: cmd_discover_commit(client, args),
            "evolve-vault": lambda: cmd_evolve_vault(client, args),
            "explore-schema": lambda: cmd_explore_schema(client, args),
            "export-logs-bundle": lambda: cmd_export_logs_bundle(client, args),
        }
        payload = dispatch[args.command]()
        write_json_output(args.output, payload)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
