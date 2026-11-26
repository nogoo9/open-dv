"""Stage 4-7 handlers for Open-DV V2 workflows (vault build, explore, logs, bundle)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from opendv_api_client import OpenDVClient, find_repo_root, search_repo_docs

TERMINAL_VAULT_STATUSES = {
    "COMPLETED",
    "FAILED",
    "HOLD_CHECKPOINTED",
    "UNCERTIFIED_DRAFT",
}


def cmd_evolve_vault(client: OpenDVClient, args: argparse.Namespace) -> dict[str, Any]:
    """Run or resolve a V2 CEGAR vault build and compile Step 7 targets."""
    project_id = args.project_id
    run_id = args.run_id

    if run_id and args.answers_json:
        answers_payload = json.loads(Path(args.answers_json).read_text(encoding="utf-8"))
        run_state = client.post_json(
            f"/api/v1/projects/{project_id}/runs/{run_id}/decisions",
            {"answers": answers_payload},
        )
    elif run_id and args.horizon_action:
        run_state = client.post_json(
            f"/api/v1/projects/{project_id}/runs/{run_id}/horizon",
            {
                "action": args.horizon_action,
                "notes": args.horizon_notes or "",
            },
        )
    else:
        build_body: dict[str, Any] = {}
        if args.policy is not None:
            build_body["policy"] = args.policy
        run_state = client.post_json(
            f"/api/v1/projects/{project_id}/runs",
            build_body,
        )
        run_id = str(run_state.get("run_id") or "")

    if args.wait and run_id:
        run_state = client.poll_until(
            f"/api/v1/projects/{project_id}/runs/{run_id}",
            terminal_statuses=TERMINAL_VAULT_STATUSES,
            timeout_seconds=args.wait_seconds,
        )

    compiled: list[dict[str, Any]] = []
    if args.targets and str(run_state.get("status", "")).upper() in {"COMPLETED", "CERTIFIED"}:
        for target_id in [t.strip() for t in args.targets.split(",") if t.strip()]:
            compiled.append(
                client.post_json(
                    f"/api/v1/projects/{project_id}/storage/compile",
                    {
                        "target_id": target_id,
                        "dbt_sql_dialect": args.dbt_dialect,
                        "verify_runtime": target_id == "duckdb_local",
                    },
                )
            )

    return {
        "project_id": project_id,
        "run_state": run_state,
        "compiled_targets": compiled,
    }


def cmd_explore_schema(client: OpenDVClient, args: argparse.Namespace) -> dict[str, Any]:
    """Fetch VaultSchemaIR v2.0, compiled targets, and optionally run guarded read-only SQL."""
    project_id = args.project_id
    vault_ir = client.get_json(f"/api/v1/projects/{project_id}/vault/ir")
    targets = client.get_json(f"/api/v1/projects/{project_id}/storage/targets")
    query_result: dict[str, Any] | None = None
    if args.sql:
        query_result = client.post_json(
            f"/api/v1/projects/{project_id}/storage/query",
            {"sql": args.sql, "limit": args.limit},
        )
    return {
        "project_id": project_id,
        "vault_ir": vault_ir,
        "storage_targets": targets,
        "query_result": query_result,
    }


def cmd_export_logs_bundle(client: OpenDVClient, args: argparse.Namespace) -> dict[str, Any]:
    """Fetch persisted Run Explorer logs and optionally export portable `.opendv.zip`."""
    project_id = args.project_id
    run_id = args.run_id
    if not run_id:
        runs = client.get_json(f"/api/v1/projects/{project_id}/runs")
        items = runs.get("runs", runs) if isinstance(runs, dict) else runs
        if isinstance(items, list) and items:
            run_id = str(items[0].get("run_id") or "")

    logs: Any = None
    if run_id:
        logs = client.get_json(
            f"/api/v1/projects/{project_id}/runs/{run_id}/logs",
            params={"since_seq": args.since_seq, "limit": args.limit},
        )

    bundle_meta: dict[str, Any] | None = None
    if args.export_bundle_zip:
        profile = "full_demo" if args.full_demo else "core_project_state"
        raw_bytes = client.get_bytes(
            f"/api/v1/projects/{project_id}/bundle",
            params={"profile": profile},
        )
        dest = Path(args.export_bundle_zip)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw_bytes)
        bundle_meta = {
            "path": str(dest),
            "profile": profile,
            "bytes": len(raw_bytes),
        }

    return {
        "project_id": project_id,
        "run_id": run_id,
        "logs": logs,
        "bundle": bundle_meta,
    }


def cmd_search_docs(args: argparse.Namespace) -> dict[str, Any]:
    """Search checked-in V2 markdown docs and contracts/v2 specs."""
    repo_root = find_repo_root()
    hits = search_repo_docs(
        repo_root=repo_root,
        query=args.query,
        scope=args.scope,
        limit=args.limit,
    )
    return {
        "repo_root": str(repo_root),
        "query": args.query,
        "scope": args.scope,
        "hit_count": len(hits),
        "hits": hits,
    }
