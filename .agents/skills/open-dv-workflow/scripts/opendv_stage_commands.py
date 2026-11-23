"""Wizard lifecycle and DV 2.0 contract continuation subcommands for Open-DV workflow CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib import parse as urllib_parse

from opendv_api_client import OpenDVClient, partition_session_sources

BREAKING_OP_TYPES = {
    "remove_concept",
    "remove_relationship",
    "remove_attribute",
    "update_concept_urn",
}


def _parse_connector_spec(raw: str) -> dict[str, Any]:
    raw_s = raw.strip()
    if raw_s.startswith("{"):
        return dict(json.loads(raw_s))
    item: dict[str, Any] = {}
    for pair in raw_s.split(","):
        if "=" not in pair:
            raise ValueError(f"Connector spec '{pair}' must be key=value or JSON.")
        key, val = pair.split("=", 1)
        item[key.strip()] = val.strip()
    if "adapter" not in item:
        raise ValueError("Connector spec missing required 'adapter=...' field.")
    return item


def _extract_list(raw: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get(key), list):
        return raw[key]
    return []


def run_ingest_parse(client: OpenDVClient, args: argparse.Namespace) -> dict[str, Any]:
    """Execute Steps 1-3: create/import session, apply starter packs, attach sources & parse."""
    session_id = args.session_id
    created: Any = None
    if not session_id:
        if args.import_ontology:
            imp = Path(args.import_ontology)
            fmt = args.import_format or imp.suffix.lstrip(".").lower() or "yaml"
            created = client.request_json(
                "POST",
                "/api/v1/ontology/studio/sessions/import-ontology",
                json_body={
                    "content": imp.read_text(encoding="utf-8"),
                    "format": fmt,
                    "domain_namespace": args.domain_namespace,
                    "enterprise_owner": args.enterprise_owner,
                },
            )
        else:
            sess_body: dict[str, Any] = {
                "domain_namespace": args.domain_namespace,
                "enterprise_owner": args.enterprise_owner,
            }
            if args.from_workspace:
                sess_body["from_workspace"] = args.from_workspace
            created = client.request_json(
                "POST", "/api/v1/ontology/studio/sessions", json_body=sess_body
            )
        session_id = created["session_id"]

    q_sid = urllib_parse.quote(str(session_id))
    if args.pack_ids:
        sha = client.request_json("GET", f"/api/v1/ontology/studio/sessions/{q_sid}/status")[
            "head_sha256"
        ]
        packs = [p.strip() for p in args.pack_ids.split(",") if p.strip()]
        client.request_json(
            "POST",
            f"/api/v1/ontology/studio/sessions/{q_sid}/starter-packs/apply",
            headers={"If-Match": sha},
            json_body={
                "pack_ids": packs,
                "base_sha256": sha,
                "domain_namespace": args.domain_namespace,
            },
        )

    attached = []
    for raw_conn in args.connector or []:
        attached.append(
            client.request_json(
                "POST",
                f"/api/v1/ontology/studio/sessions/{q_sid}/sources/connectors",
                json_body=_parse_connector_spec(raw_conn),
            )
        )
    for fpath in args.file or []:
        attached.append(
            client.upload_file(
                str(session_id),
                Path(fpath),
                ocr_mode=args.ocr_mode,
                do_not_send=args.do_not_send,
            )
        )
    for idx, note in enumerate(args.note or [], 1):
        attached.append(
            client.request_json(
                "POST",
                f"/api/v1/ontology/studio/sessions/{q_sid}/sources/text",
                json_body={
                    "text": note,
                    "source_name": f"inline_note_{idx}.md",
                    "format": "markdown",
                    "do_not_send_to_agent": args.do_not_send,
                },
            )
        )

    parse_job = None
    if args.parse and attached:
        job = client.request_json(
            "POST",
            f"/api/v1/ontology/studio/sessions/{q_sid}/parse",
            json_body={"ocr_mode": args.ocr_mode},
        )
        parse_job = client.poll_studio_job(str(session_id), job["job_id"])

    raw_tables = client.request_json(
        "GET", f"/api/v1/ontology/studio/sessions/{q_sid}/evidence/tables"
    )
    raw_chunks = client.request_json(
        "GET", f"/api/v1/ontology/studio/sessions/{q_sid}/evidence/chunks"
    )
    tables = _extract_list(raw_tables, "tables")
    chunks = _extract_list(raw_chunks, "chunks")
    return {
        "session_id": session_id,
        "created": created,
        "attached_count": len(attached),
        "parse_job": parse_job,
        "evidence_tables": tables[: args.limit],
        "evidence_chunks": chunks[: args.limit],
    }


def run_discover_commit(client: OpenDVClient, args: argparse.Namespace) -> dict[str, Any]:
    """Execute Steps 4-5: real-agent discovery, Ask FIBO, answers, commit & export."""
    q_sid = urllib_parse.quote(args.session_id)
    status_now = client.request_json("GET", f"/api/v1/ontology/studio/sessions/{q_sid}/status")
    head_sha = status_now["head_sha256"]

    construct_job = None
    if not args.skip_construct:
        body: dict[str, Any] = {
            "mode": "agent",
            "base_sha256": head_sha,
            "base_version": status_now.get("active_version"),
        }
        if args.steering:
            body["steering_prompt"] = args.steering
        job = client.request_json(
            "POST",
            f"/api/v1/ontology/studio/sessions/{q_sid}/construct",
            headers={"If-Match": head_sha},
            json_body=body,
        )
        construct_job = client.poll_studio_job(args.session_id, job["job_id"])

    fibo_qa = None
    if args.fibo_question:
        fibo_qa = client.request_json(
            "POST",
            f"/api/v1/ontology/studio/sessions/{q_sid}/fibo-qa",
            json_body={"question": args.fibo_question},
        )

    ans_resp = None
    if args.answer_json:
        ans_payload = json.loads(Path(args.answer_json).read_text(encoding="utf-8"))
        ans_resp = client.request_json(
            "POST",
            f"/api/v1/ontology/studio/sessions/{q_sid}/answers",
            json_body=ans_payload,
        )

    proposals = client.request_json("GET", f"/api/v1/ontology/studio/sessions/{q_sid}/proposals")
    questions = client.request_json(
        "GET", f"/api/v1/ontology/studio/sessions/{q_sid}/clarifications"
    )

    accepted = None
    if args.accept_proposal_id:
        prop = next(
            (p for p in (proposals or []) if p.get("proposal_id") == args.accept_proposal_id),
            None,
        )
        if not prop:
            raise ValueError(
                f"Proposal '{args.accept_proposal_id}' not found in session '{args.session_id}'."
            )
        ops = (prop.get("delta") or {}).get("operations") or []
        breaking = [op.get("op_type") for op in ops if op.get("op_type") in BREAKING_OP_TYPES]
        if breaking and not args.include_breaking:
            raise RuntimeError(
                f"Proposal '{args.accept_proposal_id}' has MAJOR breaking ops {breaking}. "
                "Pass --include-breaking only if explicitly intended (D7)."
            )
        head_sha = client.request_json(
            "GET", f"/api/v1/ontology/studio/sessions/{q_sid}/status"
        )["head_sha256"]
        accept_body: dict[str, Any] = {"base_sha256": head_sha}
        if args.commit_rationale:
            accept_body["grounding_rationale"] = args.commit_rationale
        q_pid = urllib_parse.quote(args.accept_proposal_id)
        accepted = client.request_json(
            "POST",
            f"/api/v1/ontology/studio/sessions/{q_sid}/proposals/{q_pid}/accept",
            headers={"If-Match": head_sha},
            json_body=accept_body,
        )

    exported_text = None
    if args.export_format:
        _, _, raw_exp = client.request_raw(
            "GET",
            f"/api/v1/ontology/studio/sessions/{q_sid}/export",
            query={"export_format": args.export_format},
        )
        exported_text = raw_exp.decode("utf-8")

    latest_status = client.request_json("GET", f"/api/v1/ontology/studio/sessions/{q_sid}/status")
    return {
        "session_id": args.session_id,
        "status": latest_status,
        "construct_job": construct_job,
        "fibo_qa": fibo_qa,
        "answers_submitted": ans_resp,
        "clarification_questions": questions,
        "proposals": proposals,
        "accepted": accepted,
        "exported_ontology": exported_text,
    }


def _resolve_baseline(
    client: OpenDVClient, args: argparse.Namespace
) -> tuple[str | None, str | None]:
    mode = args.baseline_mode
    if mode == "cold-start":
        return None, None
    if mode == "contract-file":
        if not args.deployed_contract_file:
            raise ValueError("--deployed-contract-file is required for contract-file mode.")
        return (
            Path(args.deployed_contract_file).read_text(encoding="utf-8"),
            args.deployed_contract_file,
        )
    if mode == "run-id":
        if not args.baseline_run_id:
            raise ValueError("--baseline-run-id is required for run-id mode.")
        content = client.extract_baseline_contract(args.baseline_run_id)
        if not content:
            raise RuntimeError(f"No final_contract.yaml found on run '{args.baseline_run_id}'.")
        return content, args.baseline_run_id

    runs = client.request_json("GET", "/api/v1/runs")
    matches = [
        r
        for r in (runs or [])
        if str(r.get("status", "")).upper() == "COMPLETED"
        and (not args.session_id or r.get("session_id") == args.session_id)
    ]
    matches.sort(
        key=lambda r: str(r.get("completed_at_utc") or r.get("started_at_utc") or ""),
        reverse=True,
    )
    if not matches:
        return None, None
    latest_id = str(matches[0]["run_id"])
    return client.extract_baseline_contract(latest_id), latest_id


def run_evolve_vault(client: OpenDVClient, args: argparse.Namespace) -> dict[str, Any]:
    """Execute Step 6: build or evolve a DV 2.0 contract, or resolve a Steward Hold."""
    if args.hold_id:
        if not args.run_id or not args.hold_action:
            raise ValueError("Resolving a Steward Hold needs --run-id, --hold-id, --hold-action.")
        body: dict[str, Any] = {"hold_id": args.hold_id, "action": args.hold_action}
        if args.candidate_id:
            body["selected_candidate_id"] = args.candidate_id
        if args.hold_notes:
            body["resolution_notes"] = args.hold_notes
        q_rid = urllib_parse.quote(args.run_id)
        res = client.request_json("POST", f"/api/v1/runs/{q_rid}/decisions", json_body=body)
        return {"hold_resolution": res, "run": client.poll_pipeline_run(args.run_id)}

    if not args.session_id:
        raise ValueError("--session-id is required when launching or continuing a pipeline run.")
    q_sid = urllib_parse.quote(args.session_id)
    initial_contract, resolved_from = _resolve_baseline(client, args)
    bp_resp = client.request_json(
        "POST", f"/api/v1/ontology/studio/sessions/{q_sid}/export-blueprint", json_body={}
    )
    srcs = client.request_json("GET", f"/api/v1/ontology/studio/sessions/{q_sid}/sources")
    raw_chunks = client.request_json(
        "GET", f"/api/v1/ontology/studio/sessions/{q_sid}/evidence/chunks"
    )
    feeds, docs = partition_session_sources(
        _extract_list(srcs, "sources"), _extract_list(raw_chunks, "chunks")
    )
    dialects = [d.strip() for d in args.dialects.split(",") if d.strip()]
    first_dialect = dialects[0] if dialects else "duckdb"
    target_dialect = "databricks" if "databricks" in dialects else first_dialect
    run_payload: dict[str, Any] = {
        "session_id": args.session_id,
        "target_dialect": target_dialect,
        "sources": feeds,
    }
    if docs:
        run_payload["unstructured_documents"] = docs
    if isinstance(bp_resp, dict) and bp_resp.get("blueprint"):
        run_payload["blueprint"] = bp_resp["blueprint"]
    if initial_contract:
        run_payload["initial_contract_content"] = initial_contract

    started = client.request_json("POST", "/api/v1/pipelines/runs", json_body=run_payload)
    run_id = started["run_id"]
    settled = client.poll_pipeline_run(run_id)
    q_rid = urllib_parse.quote(run_id)
    report = client.request_json("GET", f"/api/v1/runs/{q_rid}/report")
    decisions = None
    if str(settled.get("status", "")).upper() in {"PAUSED_HOLD", "HOLD"}:
        decisions = client.request_json("GET", f"/api/v1/runs/{q_rid}/decisions")
    return {
        "run_id": run_id,
        "baseline_mode": args.baseline_mode,
        "resolved_baseline_from": resolved_from,
        "target_dialect": target_dialect,
        "run": settled,
        "decisions": decisions,
        "drift_findings": (report or {}).get("drift_findings"),
        "dv_schema_url": f"{client.api_url}/dv-schema?run={run_id}",
    }
