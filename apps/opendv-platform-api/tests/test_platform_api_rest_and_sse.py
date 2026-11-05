"""Comprehensive integration tests for `opendv-platform-api` REST + SSE endpoints and RBAC."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import yaml
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from opendv_core import VaultObservabilityEvent, validate_observability_event
from opendv_platform_api import (
    PE_DEMO_CONTEXT_REFS,
    PlatformStateContainer,
    RbacRole,
    RunStatus,
    create_app,
    export_openapi_to_file,
    format_sse_event_stream,
    generate_openapi_spec,
    redact_event_for_role,
    store_cas_artifact,
)


def _run_async(coro: Any) -> Any:
    """Execute an async coroutine synchronously in standard pytest functions."""
    return asyncio.run(coro)


def _parse_sse_frames(raw_sse: str) -> list[dict[str, str]]:
    """Parse W3C `text/event-stream` text into a list of frame dicts."""
    frames: list[dict[str, str]] = []
    for block in raw_sse.strip().split("\n\n"):
        if not block.strip():
            continue
        frame: dict[str, str] = {}
        for line in block.splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                frame[key.strip()] = val.strip()
        if frame:
            frames.append(frame)
    return frames


def test_health_and_capabilities_endpoints(tmp_path: Path) -> None:
    """Verify `/api/v1/health` and `/api/v1/platform/capabilities`."""
    state = PlatformStateContainer(state_dir=tmp_path / "state")
    app = create_app(platform_state=state)

    async def _scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res_health = await client.get("/api/v1/health")
            assert res_health.status_code == 200
            body_health = res_health.json()
            assert body_health["status"] == "ok"
            assert body_health["api_version"] == "v1"
            assert body_health["contract_version"] == "6.0"

            res_cap = await client.get("/api/v1/platform/capabilities")
            assert res_cap.status_code == 200
            body_cap = res_cap.json()
            assert body_cap["harness_providers"] == [
                "agy",
                "agy-cli",
                "antigravity",
                "claude",
                "claude-cli",
                "claude-code",
            ]
            assert "deterministic" not in body_cap["harness_providers"]
            assert "deterministic-stub" not in body_cap["harness_providers"]
            assert len(body_cap["rbac_roles"]) == len(RbacRole)
            assert "PII_STEWARD" in body_cap["rbac_roles"]
            assert len(body_cap["workbench_views"]) == 12
            assert body_cap["gates"] == ["G0", "G1", "G2", "G3", "G4", "G5"]

    _run_async(_scenario())


def test_projects_and_runs_lifecycle(tmp_path: Path) -> None:
    """Verify `/api/v1/projects` and `/api/v1/runs` CRUD and abort transitions."""
    state = PlatformStateContainer(state_dir=tmp_path / "state")
    app = create_app(platform_state=state)

    async def _scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. List pre-seeded PE project
            res_list = await client.get("/api/v1/projects")
            assert res_list.status_code == 200
            projects_data = res_list.json()
            assert isinstance(projects_data, list)
            assert len(projects_data) >= 1
            pe_proj = next(p for p in projects_data if p["project_id"] == "proj-apex-horizon-pe")
            assert pe_proj["latest_run_id"] == "run-pe-apex-horizon-20261030"

            # 2. Get specific project
            res_get = await client.get("/api/v1/projects/proj-apex-horizon-pe")
            assert res_get.status_code == 200
            assert res_get.json()["domain_namespace"] == "pe_asset_management"

            # 3. Create new project (verifying non-hardcoded defaults)
            res_create = await client.post(
                "/api/v1/projects",
                json={
                    "project_id": "proj-nordic-co-invest",
                    "display_name": "Nordic Co-Invest Secondary Vault",
                    "domain_namespace": "pe_co_investment",
                },
            )
            assert res_create.status_code == 201
            created_proj = res_create.json()
            assert created_proj["project_id"] == "proj-nordic-co-invest"
            assert created_proj["contract_path"] is None
            assert created_proj["graph_a_ontology_path"] is None
            assert created_proj["graph_b_path"] is None
            assert created_proj["canonical_ir_path"] is None

            # 4. Path traversal in project registration must be rejected (400/422 problem+json)
            res_bad_path = await client.post(
                "/api/v1/projects",
                json={
                    "project_id": "proj-traversal",
                    "contract_path": "../../etc/passwd",
                },
            )
            assert res_bad_path.status_code in {400, 422}
            assert "application/problem+json" in res_bad_path.headers["content-type"]

            res_sibling_prefix = await client.post(
                "/api/v1/projects",
                json={
                    "project_id": "proj-sibling-prefix",
                    "contract_path": f"../{state.repo_root.name}-other/secret.yaml",
                },
            )
            assert res_sibling_prefix.status_code == 400
            assert "application/problem+json" in res_sibling_prefix.headers["content-type"]

            # 5. Start a new run on the new project
            res_run_create = await client.post(
                "/api/v1/runs",
                json={
                    "project_id": "proj-nordic-co-invest",
                    "harness_provider": "claude",
                },
            )
            assert res_run_create.status_code == 201
            created_run = res_run_create.json()
            new_run_id = created_run["run_id"]
            assert created_run["harness_provider"] == "claude"
            assert created_run["status"] == RunStatus.PAUSED_HOLD.value
            assert created_run["event_count"] == 0

            res_no_contract_gates = await client.post(
                f"/api/v1/runs/{new_run_id}/gates",
                json={"recheck": True},
            )
            assert res_no_contract_gates.status_code == 400
            assert "application/problem+json" in res_no_contract_gates.headers["content-type"]

            # 6. Abort the newly created run
            res_abort = await client.post(f"/api/v1/runs/{new_run_id}/abort")
            assert res_abort.status_code == 200
            assert res_abort.json()["status"] == RunStatus.ABORTED.value

            # 7. Unknown project or run returns 404 RFC 9457 Problem Details
            res_missing = await client.get("/api/v1/runs/run-does-not-exist")
            assert res_missing.status_code == 404
            assert "application/problem+json" in res_missing.headers["content-type"]
            problem = res_missing.json()
            assert problem["status"] == 404
            assert "not found" in problem["detail"].lower()

    _run_async(_scenario())


def test_sse_event_streaming_and_last_event_id_replay(tmp_path: Path) -> None:
    """Verify `/api/v1/runs/{run_id}/events` W3C SSE streaming and `Last-Event-ID` cursor."""
    state = PlatformStateContainer(state_dir=tmp_path / "state")
    app = create_app(platform_state=state)
    run_id = "run-pe-apex-horizon-20261030"

    async def _scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # Full stream with RESTRICTED_PII_CUSTODIAN role
            res_full = await client.get(
                f"/api/v1/runs/{run_id}/events",
                headers={"X-OpenDV-Role": "RESTRICTED_PII_CUSTODIAN"},
            )
            assert res_full.status_code == 200
            assert "text/event-stream" in res_full.headers["content-type"]
            frames = _parse_sse_frames(res_full.text)
            # 9 canonical events + 1 run_complete frame = 10 frames
            assert len(frames) == 10
            assert frames[-1].get("event") == "run_complete"
            assert json.loads(frames[-1]["data"]) == {"status": "COMPLETED"}

            event_ids = [f["id"] for f in frames[:-1]]
            assert event_ids == [f"evt-pe-00{i}" for i in range(1, 10)]

            for frame in frames[:-1]:
                ev_obj = json.loads(frame["data"])
                VaultObservabilityEvent.model_validate(ev_obj)
                validate_observability_event(ev_obj)

            # Reconnect with Last-Event-ID: evt-pe-005 -> only stream evt-pe-006..009 + run_complete
            res_resume = await client.get(
                f"/api/v1/runs/{run_id}/events",
                headers={
                    "X-OpenDV-Role": "RESTRICTED_PII_CUSTODIAN",
                    "Last-Event-ID": "evt-pe-005",
                },
            )
            assert res_resume.status_code == 200
            resumed_frames = _parse_sse_frames(res_resume.text)
            assert [f["id"] for f in resumed_frames[:-1]] == [
                "evt-pe-006",
                "evt-pe-007",
                "evt-pe-008",
                "evt-pe-009",
            ]
            assert resumed_frames[-1].get("event") == "run_complete"

    _run_async(_scenario())


def test_five_tier_rbac_server_side_redaction(tmp_path: Path) -> None:
    """Verify server-side 5-Tier RBAC redaction across SSE and Workbench endpoints."""
    state = PlatformStateContainer(state_dir=tmp_path / "state")
    app = create_app(platform_state=state)
    run_id = "run-pe-apex-horizon-20261030"

    async def _scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. PUBLIC role: INTERNAL, CONFIDENTIAL, MNPI, and RESTRICTED_PII are all masked
            res_public = await client.get(
                f"/api/v1/runs/{run_id}/events",
                headers={"X-OpenDV-Role": "PUBLIC"},
            )
            public_frames = _parse_sse_frames(res_public.text)
            for frame in public_frames[:-1]:
                ev = json.loads(frame["data"])
                assert "[REDACTED" in ev["layer1_story_banner"]["headline"]
                if ev.get("data_classification") in {
                    "CONFIDENTIAL",
                    "MNPI",
                    "RESTRICTED_PII",
                }:
                    for pill in ev["layer2_visual_payload"]["semantic_hash_pills"]:
                        assert pill["business_key"] == "[REDACTED:CONFIDENTIAL]"
                        assert pill["entity_label"] == "[REDACTED:CONFIDENTIAL]"
                        assert "[REDACTED:CONFIDENTIAL]" in pill["pill_label"]

            # 2. DATA_STEWARD role: CONFIDENTIAL (WB-03 / evt-pe-004) is visible,
            #    but MNPI (WB-05 / evt-pe-006) and RESTRICTED_PII (WB-04 / evt-pe-005) are masked
            wb03_steward = (
                await client.get(
                    f"/api/v1/runs/{run_id}/workbench/WB-03",
                    headers={"X-OpenDV-Role": "DATA_STEWARD"},
                )
            ).json()
            assert not wb03_steward["event"]["layer1_story_banner"]["headline"].startswith(
                "[REDACTED"
            )

            wb05_steward = (
                await client.get(
                    f"/api/v1/runs/{run_id}/workbench/WB-05",
                    headers={"X-OpenDV-Role": "DATA_STEWARD"},
                )
            ).json()
            assert "[REDACTED:MNPI]" in wb05_steward["event"]["layer1_story_banner"]["headline"]
            assert "245000000" not in json.dumps(wb05_steward["event"])

            # 3. PII_STEWARD role: RESTRICTED_PII (WB-04) is visible, MNPI (WB-05) is masked
            wb04_pii_steward = (
                await client.get(
                    f"/api/v1/runs/{run_id}/workbench/WB-04",
                    headers={"X-OpenDV-Role": "PII_STEWARD"},
                )
            ).json()
            assert not wb04_pii_steward["event"]["layer1_story_banner"]["headline"].startswith(
                "[REDACTED"
            )
            wb05_pii_steward = (
                await client.get(
                    f"/api/v1/runs/{run_id}/workbench/WB-05",
                    headers={"X-OpenDV-Role": "PII_STEWARD"},
                )
            ).json()
            assert "[REDACTED:MNPI]" in wb05_pii_steward["event"]["layer1_story_banner"]["headline"]

            # 4. AUDITOR_FINANCE role: MNPI (WB-05) is visible ($245M / 14.5x),
            #    but RESTRICTED_PII (WB-04) remains masked
            wb05_auditor = (
                await client.get(
                    f"/api/v1/runs/{run_id}/workbench/WB-05",
                    headers={"X-OpenDV-Role": "AUDITOR_FINANCE"},
                )
            ).json()
            assert (
                wb05_auditor["event"]["layer2_visual_payload"]["summary_metrics"]["fair_value_usd"]
                == 245000000.0
            )

            wb04_auditor = (
                await client.get(
                    f"/api/v1/runs/{run_id}/workbench/WB-04",
                    headers={"X-OpenDV-Role": "AUDITOR_FINANCE"},
                )
            ).json()
            assert wb04_auditor["event"]["layer1_story_banner"]["headline"].startswith(
                "[REDACTED RESTRICTED_PII]"
            )

            # 4. RESTRICTED_PII_CUSTODIAN role: WB-04 is unmasked
            wb04_custodian = (
                await client.get(
                    f"/api/v1/runs/{run_id}/workbench/WB-04",
                    headers={"X-OpenDV-Role": "RESTRICTED_PII_CUSTODIAN"},
                )
            ).json()
            assert (
                wb04_custodian["event"]["layer2_visual_payload"]["summary_metrics"]["pii_satellite"]
                == "sat_limited_partner_pii"
            )

            # 5. Invalid role header returns HTTP 400 Problem Details
            res_bad_role = await client.get(
                f"/api/v1/runs/{run_id}/workbench/WB-00",
                headers={"X-OpenDV-Role": "SUPER_HACKER"},
            )
            assert res_bad_role.status_code == 400
            assert "application/problem+json" in res_bad_role.headers["content-type"]

    _run_async(_scenario())


def test_all_12_workbench_views_and_gates_endpoints(tmp_path: Path, repo_root: Path) -> None:
    """Verify `WB-00`..`WB-11` workbench endpoints and `/api/v1/runs/{run_id}/gates`."""
    state = PlatformStateContainer(state_dir=tmp_path / "state", workspace_root=repo_root)
    app = create_app(platform_state=state, workspace_root=repo_root)
    run_id = "run-pe-apex-horizon-20261030"

    async def _scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            for idx in range(12):
                view_id = f"WB-{idx:02d}"
                res = await client.get(
                    f"/api/v1/runs/{run_id}/workbench/{view_id}",
                    headers={"X-OpenDV-Role": "RESTRICTED_PII_CUSTODIAN"},
                )
                assert res.status_code == 200
                body = res.json()
                assert body["view_id"] == view_id
                assert body["event"]["workbench_view_id"] == view_id
                assert len(body["events"]) >= 1

            # Invalid workbench view returns 404
            res_bad_wb = await client.get(f"/api/v1/runs/{run_id}/workbench/WB-99")
            assert res_bad_wb.status_code == 404

            # GET and POST gates
            res_gates = await client.get(f"/api/v1/runs/{run_id}/gates")
            assert res_gates.status_code == 200
            assert res_gates.json()["overall_passed"] is True
            assert res_gates.json()["gate_scorecard"]["gate_1_contract_grain"] == "AUTO_REPAIRED"

            res_eval_gates = await client.post(
                f"/api/v1/runs/{run_id}/gates",
                json={"recheck": True},
            )
            assert res_eval_gates.status_code == 200
            eval_body = res_eval_gates.json()
            assert eval_body["overall_passed"] is True
            assert eval_body["verdict"] == "PASS"
            assert "@toon_v1" in eval_body["toon_text"]

            # Missing contract path returns 404 ProblemDetails (not 500 FileNotFoundError)
            res_missing_contract = await client.post(
                f"/api/v1/runs/{run_id}/gates",
                json={"contract_path": "examples/private_equity/does_not_exist.yaml"},
            )
            assert res_missing_contract.status_code == 404
            assert "application/problem+json" in res_missing_contract.headers["content-type"]

            res_missing_graph_a = await client.post(
                f"/api/v1/runs/{run_id}/gates",
                json={"graph_a_ontology_path": "examples/private_equity/does_not_exist.yaml"},
            )
            assert res_missing_graph_a.status_code == 404
            assert "application/problem+json" in res_missing_graph_a.headers["content-type"]

            res_missing_graph_b = await client.post(
                f"/api/v1/runs/{run_id}/gates",
                json={"graph_b_path": "examples/private_equity/does_not_exist.json"},
            )
            assert res_missing_graph_b.status_code == 404
            assert "application/problem+json" in res_missing_graph_b.headers["content-type"]

            # Rejected contract evaluation sets overall_passed=False and REJECT-WITH-TRACE
            bad_repo = tmp_path / "bad_repo"
            bad_repo.mkdir(parents=True, exist_ok=True)
            for sub in ("examples/private_equity", "schemas", "docs"):
                (bad_repo / sub).mkdir(parents=True, exist_ok=True)
            for fname in (
                "dv_pe_model_contract.yaml",
                "pe_graph_a_ontology.yaml",
                "pe_graph_b_profile.json",
                "pe_canonical_ir.json",
            ):
                src = repo_root / "examples" / "private_equity" / fname
                (bad_repo / "examples" / "private_equity" / fname).write_text(
                    src.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            bad_contract_rel = "examples/private_equity/broken_contract.yaml"
            (bad_repo / bad_contract_rel).write_text(
                "contract_version: '5.0'\n"
                "hubs:\n"
                "  - name: hub_broken\n"
                "    target_table: raw_vault.hub_broken\n"
                "    grain_statement: 'short grain'\n"
                "    business_keys: []\n"
                "links: []\n"
                "satellites: []\n",
                encoding="utf-8",
            )
            bad_state = PlatformStateContainer(repo_root=bad_repo, state_dir=tmp_path / "bad_state")
            bad_app = create_app(platform_state=bad_state)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=bad_app),
                base_url="http://testserver",
            ) as bad_client:
                res_reject = await bad_client.post(
                    f"/api/v1/runs/{run_id}/gates",
                    json={"contract_path": bad_contract_rel},
                )
                assert res_reject.status_code == 200
                reject_body = res_reject.json()
                assert reject_body["overall_passed"] is False
                assert reject_body["verdict"] == "REJECT-WITH-TRACE"
                assert reject_body["gate_scorecard"]["gate_1_contract_grain"] == "REJECT-WITH-TRACE"
                assert len(reject_body["diagnostics"]) >= 1
                assert "REJECT-WITH-TRACE" in reject_body["toon_text"]
                assert bad_state.runs[run_id].status == RunStatus.FAILED

                res_recover = await bad_client.post(
                    f"/api/v1/runs/{run_id}/gates",
                    json={"contract_path": "examples/private_equity/dv_pe_model_contract.yaml"},
                )
                assert res_recover.status_code == 200
                assert res_recover.json()["overall_passed"] is True
                assert bad_state.runs[run_id].status == RunStatus.PAUSED_HOLD

    _run_async(_scenario())


def test_captain_hold_decision_resolution_and_conflict(tmp_path: Path) -> None:
    """Verify `/api/v1/runs/{run_id}/decisions` resolves Tier-U3 hold and prevents replay."""
    state = PlatformStateContainer(state_dir=tmp_path / "state")
    app = create_app(platform_state=state)
    run_id = "run-pe-apex-horizon-20261030"

    async def _scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Initially 1 active hold (`hold-tier-u3-horizon-sovereign-vehicle`)
            res_list = await client.get(f"/api/v1/runs/{run_id}/decisions")
            assert res_list.status_code == 200
            assert len(res_list.json()["active_holds"]) == 1

            # 2. Resolve the hold with APPROVE
            res_resolve = await client.post(
                f"/api/v1/runs/{run_id}/decisions",
                json={
                    "event_id": "evt-pe-004",
                    "hold_id": "hold-tier-u3-horizon-sovereign-vehicle",
                    "decision_type": "RESOLVE_CAPTAIN_HOLD",
                    "action": "APPROVE",
                    "selected_candidate_id": "APEX_CO_I",
                    "resolution_notes": "Confirmed co-invest vehicle mapping to APEX_CO_I.",
                    "steward_id": "steward-jane-doe",
                },
            )
            assert res_resolve.status_code == 200
            decision_body = res_resolve.json()
            assert decision_body["status"] == "ACCEPTED"
            assert decision_body["updated_run_status"] == RunStatus.COMPLETED.value
            assert decision_body["next_event_id"].startswith("evt-hold-resolved-")

            # 3. Run status is now COMPLETED with 0 active holds and 10 events
            res_run = await client.get(f"/api/v1/runs/{run_id}")
            assert res_run.status_code == 200
            assert res_run.json()["status"] == RunStatus.COMPLETED.value
            assert res_run.json()["active_hold_ids"] == []
            assert res_run.json()["event_count"] == 10

            # 4. Attempting to resolve the same hold again returns 409 Conflict
            res_conflict = await client.post(
                f"/api/v1/runs/{run_id}/decisions",
                json={
                    "event_id": "evt-pe-004",
                    "hold_id": "hold-tier-u3-horizon-sovereign-vehicle",
                    "action": "APPROVE",
                },
            )
            assert res_conflict.status_code == 409
            assert "application/problem+json" in res_conflict.headers["content-type"]

    _run_async(_scenario())


def test_duckdb_sandbox_4_batches_and_bitemporal_pit_queries(tmp_path: Path) -> None:
    """Verify `/api/v1/runs/{run_id}/sandbox` executes 4-Batch sandbox and Queries D.10/D.10b."""
    state = PlatformStateContainer(state_dir=tmp_path / "state")
    app = create_app(platform_state=state)
    run_id = "run-pe-apex-horizon-20261030"

    async def _scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Execute 4-Batch adversarial sandbox
            res_batches = await client.post(
                f"/api/v1/runs/{run_id}/sandbox",
                json={
                    "query_type": "ADVERSARIAL_BATCHES_1_TO_4",
                    "assertion_cutoff_ts": "2026-11-15 23:59:59",
                },
            )
            assert res_batches.status_code == 200
            batches_body = res_batches.json()
            assert batches_body["all_batches_passed"] is True
            assert batches_body["dynamic_rules_passed"] == 15
            assert batches_body["row_count"] == 4
            assert len(batches_body["query_d10_rows"]) >= 1
            assert len(batches_body["query_d10b_rows"]) == 3

            # 2. Bi-Temporal Query D.10 & D.10b before vs after Batch 4 (2026-11-14 16:30:00)
            res_d10 = await client.post(
                f"/api/v1/runs/{run_id}/sandbox",
                json={
                    "query_type": "QUERY_D10_PIT",
                    "assertion_cutoff_ts": "2026-11-15 23:59:59",
                },
            )
            assert res_d10.status_code == 200
            calpers_row = next(
                r
                for r in res_d10.json()["rows"]
                if r["fund_code"] == "APEX_BO_IV" and r["lp_natural_key"] == "LP-101"
            )
            assert float(calpers_row["commitment_amount_usd"]) == 500000000.0
            assert float(calpers_row["side_letter_discounted_fee_bps"]) == 125.0

            res_oct_val = await client.post(
                f"/api/v1/runs/{run_id}/sandbox",
                json={
                    "query_type": "QUERY_D10B_VALUATION",
                    "assertion_cutoff_ts": "2026-10-31 23:59:59",
                },
            )
            assert res_oct_val.status_code == 200
            nordic_oct = next(
                r
                for r in res_oct_val.json()["rows"]
                if r["portco_natural_key"] == "529900T8BM49AURSDO55"
            )
            assert float(nordic_oct["ev_ebitda_multiple"]) == 12.80

            res_nov_val = await client.post(
                f"/api/v1/runs/{run_id}/sandbox",
                json={
                    "query_type": "QUERY_D10B_VALUATION",
                    "assertion_cutoff_ts": "2026-11-15 23:59:59",
                },
            )
            assert res_nov_val.status_code == 200
            nordic_nov = next(
                r
                for r in res_nov_val.json()["rows"]
                if r["portco_natural_key"] == "529900T8BM49AURSDO55"
            )
            assert float(nordic_nov["ev_ebitda_multiple"]) == 14.50
            assert float(nordic_nov["enterprise_value_usd"]) == 245000000.0

            # 3. GET sandbox state returns execution history
            res_get_sbx = await client.get(f"/api/v1/runs/{run_id}/sandbox")
            assert res_get_sbx.status_code == 200
            assert len(res_get_sbx.json()["history"]) == 4

    _run_async(_scenario())


def test_cas_artifacts_and_openapi_v1_schema_freshness(tmp_path: Path, repo_root: Path) -> None:
    """Verify `/api/v1/artifacts/{sha256}` CAS storage and `schemas/openapi-v1.json` freshness."""
    state = PlatformStateContainer(state_dir=tmp_path / "state", workspace_root=repo_root)
    app = create_app(platform_state=state, workspace_root=repo_root)

    async def _scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Pre-seeded canonical context_refs SHA-256 resolves cleanly
            graph_a_sha = PE_DEMO_CONTEXT_REFS["graph_a_sha256"]
            res_seeded = await client.get(f"/api/v1/artifacts/{graph_a_sha}")
            assert res_seeded.status_code == 200
            assert res_seeded.json()["sha256"] == graph_a_sha

            # 2. Upload a custom JSON artifact and retrieve by returned SHA-256
            res_upload = await client.post(
                "/api/v1/artifacts",
                json={
                    "content": {"hello": "open-dv-cas", "version": 6},
                    "media_type": "application/json",
                    "artifact_name": "test-cas.json",
                },
            )
            assert res_upload.status_code == 201
            uploaded_sha = res_upload.json()["sha256"]

            res_fetch = await client.get(f"/api/v1/artifacts/{uploaded_sha}")
            assert res_fetch.status_code == 200
            assert res_fetch.json()["content"] == {"hello": "open-dv-cas", "version": 6}

            # 3. Invalid SHA-256 format returns 400
            res_invalid = await client.get("/api/v1/artifacts/not-a-sha256")
            assert res_invalid.status_code == 400

    _run_async(_scenario())

    # 4. Direct helper unit tests for store_cas_artifact & format_sse_event_stream
    mem_store: dict[str, bytes] = {}
    digest = store_cas_artifact(mem_store, "test-payload")
    assert len(digest) == 64
    assert mem_store[digest] == b"test-payload"

    # 5. Verify schemas/openapi-v1.json is valid OpenAPI 3.1.0 + Draft 2020-12 and matches generator
    openapi_path = repo_root / "schemas" / "openapi-v1.json"
    assert openapi_path.exists()
    on_disk_spec = json.loads(openapi_path.read_text(encoding="utf-8"))
    generated_spec = generate_openapi_spec()
    assert on_disk_spec["openapi"] == "3.1.0"
    Draft202012Validator.check_schema(on_disk_spec)
    assert on_disk_spec == generated_spec

    # Also test exporting to a temporary path
    tmp_openapi = export_openapi_to_file(tmp_path / "openapi-v1.json")
    assert tmp_openapi.exists()
    assert json.loads(tmp_openapi.read_text(encoding="utf-8")) == generated_spec

    # Verify redact_event_for_role enum compatibility
    evt005 = state.run_events["run-pe-apex-horizon-20261030"][4]
    redacted = redact_event_for_role(evt005, RbacRole.PUBLIC)
    assert "[REDACTED" in redacted["layer1_story_banner"]["headline"]
    assert (
        redacted["layer2_visual_payload"]["semantic_hash_pills"][0]["business_key"]
        == "[REDACTED:CONFIDENTIAL]"
    )

    # Verify format_sse_event_stream async generator directly
    chunks = _run_async(
        _collect_async_iter(
            format_sse_event_stream(
                state.run_events["run-pe-apex-horizon-20261030"],
                role=RbacRole.INTERNAL_MODELER,
                last_event_id="evt-pe-008",
            )
        )
    )
    assert len(chunks) == 2
    assert chunks[0].startswith("id: evt-pe-009\n")
    assert chunks[1].startswith("event: run_complete\n")


def test_agentic_pipeline_validate_run_report_sse_and_workbench_endpoints(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    """Verify `/api/v1/pipelines/validate`, `/api/v1/pipelines/runs`,
    `/api/v1/runs/{run_id}/report`, and SSE + WB-00..WB-11 integration.
    """
    from opendv_agent_harness.adapters.deterministic_adapter import (
        build_deterministic_agy_sdk_shim,
    )
    from opendv_agent_harness.pipeline import (
        run_agentic_vault_pipeline as orig_run_pipeline,
    )
    from opendv_service import set_pipeline_runner_override

    def shim_runner(*args, **kwargs):
        return orig_run_pipeline(
            *args,
            **{**kwargs, "sdk_module": build_deterministic_agy_sdk_shim()},
        )

    set_pipeline_runner_override(shim_runner)
    state = PlatformStateContainer(state_dir=tmp_path / "state", workspace_root=repo_root)
    app = create_app(platform_state=state, workspace_root=repo_root)
    sample_input = "examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml"
    sample_harness = "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"

    async def _scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Fail-closed when input_spec/input_spec_path are omitted or path traversal is used
            res_empty = await client.post("/api/v1/pipelines/validate", json={})
            assert res_empty.status_code in {400, 422}
            assert "application/problem+json" in res_empty.headers["content-type"]

            res_traversal = await client.post(
                "/api/v1/pipelines/validate",
                json={"input_spec_path": "../../etc/passwd"},
            )
            assert res_traversal.status_code in {400, 422}
            assert "application/problem+json" in res_traversal.headers["content-type"]

            # 2. Pre-flight deterministic input validation (200 DeterministicValidationReport)
            res_val_bad_schema = await client.post(
                "/api/v1/pipelines/validate",
                json={"input_spec": {"pipeline_id": "invalid-spec"}},
            )
            assert res_val_bad_schema.status_code == 200
            val_bad_body = res_val_bad_schema.json()
            assert val_bad_body["stage"] == "INPUT_VALIDATION"
            assert val_bad_body["passed"] is False
            assert val_bad_body["diagnostics"][0]["code"] == "INPUT_SPEC_SCHEMA_INVALID"

            # Negative check: provider_override="deterministic" fails with HARNESS_SETUP_INVALID
            res_val_det = await client.post(
                "/api/v1/pipelines/validate",
                json={
                    "input_spec_path": sample_input,
                    "harness_config_path": sample_harness,
                    "provider_override": "deterministic",
                },
            )
            assert res_val_det.status_code == 200
            val_det_body = res_val_det.json()
            assert val_det_body["passed"] is False
            assert any(d["code"] == "HARNESS_SETUP_INVALID" for d in val_det_body["diagnostics"])

            # Negative check: POST /api/v1/runs with harness_provider="deterministic" returns 422
            res_run_det_422 = await client.post(
                "/api/v1/runs",
                json={
                    "pipeline_input_path": sample_input,
                    "harness_config_path": sample_harness,
                    "harness_provider": "deterministic",
                },
            )
            assert res_run_det_422.status_code == 422

            res_val = await client.post(
                "/api/v1/pipelines/validate",
                json={
                    "input_spec_path": sample_input,
                    "harness_config_path": sample_harness,
                    "provider_override": "agy",
                    "steward_hold_policy": "auto_approve_top_candidate",
                },
            )
            assert res_val.status_code == 200
            val_body = res_val.json()
            assert val_body["stage"] == "INPUT_VALIDATION"
            assert val_body["passed"] is True
            assert val_body["checked_items_count"] >= 10

            res_val_claude_cli = await client.post(
                "/api/v1/pipelines/validate",
                json={
                    "input_spec_path": sample_input,
                    "harness_config_path": (
                        "examples/pipeline_samples/global_commerce_vault/"
                        "harness_config_claude_cli.yaml"
                    ),
                    "provider_override": "claude-cli",
                    "steward_hold_policy": "auto_approve_top_candidate",
                },
            )
            assert res_val_claude_cli.status_code == 200
            assert res_val_claude_cli.json()["passed"] is True

            # 3. Execute 4-wave agentic pipeline via POST /api/v1/pipelines/runs
            out_dir = tmp_path / "pipeline_out"
            res_run = await client.post(
                "/api/v1/pipelines/runs",
                headers={"X-OpenDV-Role": "INTERNAL_MODELER"},
                json={
                    "input_spec_path": sample_input,
                    "harness_config_path": sample_harness,
                    "provider_override": "agy",
                    "steward_hold_policy": "auto_approve_top_candidate",
                    "output_dir": str(out_dir),
                },
            )
            assert res_run.status_code == 201
            rep_body = res_run.json()
            run_id = rep_body["run_id"]
            assert rep_body["status"] == "COMPLETED"
            assert rep_body["overall_passed"] is True
            assert rep_body["input_validation"]["passed"] is True
            assert rep_body["output_validation"]["passed"] is True
            assert len(rep_body["wave_reports"]) == 4
            assert len(rep_body["step_reports"]) >= 25
            assert len(rep_body["intermediate_artifacts"]) >= 20
            assert "final_contract" in rep_body["final_artifacts"]

            # Project and run are registered in PlatformStateContainer with resolved paths
            assert rep_body["project_id"] in state.projects
            proj_obj = state.projects[rep_body["project_id"]]
            assert proj_obj.graph_a_ontology_path is not None
            assert proj_obj.graph_b_path is not None
            assert run_id in state.runs
            assert run_id in state.pipeline_reports
            assert run_id in state.gate_reports

            # POST /api/v1/runs/{run_id}/gates succeeds on pipeline run with resolved Graph A/B
            res_pipe_gates = await client.post(
                f"/api/v1/runs/{run_id}/gates",
                json={"recheck": True},
            )
            assert res_pipe_gates.status_code == 200
            assert res_pipe_gates.json()["overall_passed"] is True

            # 4. GET /api/v1/runs/{run_id}/report enforces 5-Tier RBAC redaction
            res_rep = await client.get(f"/api/v1/runs/{run_id}/report")
            assert res_rep.status_code == 200
            assert res_rep.json()["run_id"] == run_id
            assert res_rep.json()["overall_passed"] is True
            assert len(res_rep.json()["wave_reports"]) == 4

            res_rep_bad_role = await client.get(
                f"/api/v1/runs/{run_id}/report",
                headers={"X-OpenDV-Role": "INVALID_ROLE"},
            )
            assert res_rep_bad_role.status_code == 400
            assert "application/problem+json" in res_rep_bad_role.headers["content-type"]

            # Pre-seeded run synthesizes an RBAC-redacted summary report; unknown run returns 404
            res_seeded_rep = await client.get("/api/v1/runs/run-pe-apex-horizon-20261030/report")
            assert res_seeded_rep.status_code == 200
            assert res_seeded_rep.json()["run_id"] == "run-pe-apex-horizon-20261030"
            assert "245000000" not in json.dumps(res_seeded_rep.json()["observability_events"])
            assert res_seeded_rep.json()["observability_events"][0]["layer1_story_banner"][
                "headline"
            ].startswith("[REDACTED")

            res_seeded_cust = await client.get(
                "/api/v1/runs/run-pe-apex-horizon-20261030/report",
                headers={"X-OpenDV-Role": "RESTRICTED_PII_CUSTODIAN"},
            )
            assert res_seeded_cust.status_code == 200
            assert "245000000" in json.dumps(res_seeded_cust.json()["observability_events"])
            assert not res_seeded_cust.json()["observability_events"][0]["layer1_story_banner"][
                "headline"
            ].startswith("[REDACTED")

            res_missing_rep = await client.get("/api/v1/runs/run-unknown-404/report")
            assert res_missing_rep.status_code == 404
            assert "application/problem+json" in res_missing_rep.headers["content-type"]

            # 5. SSE stream (/api/v1/runs/{run_id}/events) is populated for the pipeline run
            res_sse = await client.get(
                f"/api/v1/runs/{run_id}/events",
                headers={"X-OpenDV-Role": "RESTRICTED_PII_CUSTODIAN"},
            )
            assert res_sse.status_code == 200
            sse_frames = _parse_sse_frames(res_sse.text)
            assert len(sse_frames) == len(rep_body["observability_events"]) + 1
            assert sse_frames[-1].get("event") == "run_complete"
            for frame in sse_frames[:-1]:
                ev_obj = json.loads(frame["data"])
                VaultObservabilityEvent.model_validate(ev_obj)
                validate_observability_event(ev_obj)

            # 6. All 12 Workbench views (WB-00..WB-11) are populated for the pipeline run
            for idx in range(12):
                view_id = f"WB-{idx:02d}"
                res_wb = await client.get(
                    f"/api/v1/runs/{run_id}/workbench/{view_id}",
                    headers={"X-OpenDV-Role": "RESTRICTED_PII_CUSTODIAN"},
                )
                assert res_wb.status_code == 200
                wb_body = res_wb.json()
                assert wb_body["run_id"] == run_id
                assert wb_body["view_id"] == view_id
                assert wb_body["event_count"] >= 1
                assert wb_body["event"] is not None
                assert wb_body["event"]["workbench_view_id"] == view_id

            # 7. Intermediate & final artifacts are stored in CAS and retrievable via API
            final_contract_sha = rep_body["final_artifacts"]["final_contract"]["sha256"]
            res_art = await client.get(f"/api/v1/artifacts/{final_contract_sha}")
            assert res_art.status_code == 200
            assert res_art.json()["sha256"] == final_contract_sha.upper()

            # 8. POST /api/v1/runs with pipeline_input_path triggers execute_agentic_pipeline_run
            res_via_runs = await client.post(
                "/api/v1/runs",
                json={
                    "pipeline_input_path": sample_input,
                    "harness_config_path": sample_harness,
                    "harness_provider": "agy",
                },
            )
            assert res_via_runs.status_code == 201
            via_runs_dto = res_via_runs.json()
            assert via_runs_dto["status"] == RunStatus.COMPLETED.value
            assert via_runs_dto["run_id"] in state.pipeline_reports

            # 9. PAUSED_HOLD pipeline run synchronizes report on hold resolution and abort
            res_paused = await client.post(
                "/api/v1/pipelines/runs",
                headers={"X-OpenDV-Role": "DATA_STEWARD"},
                json={
                    "input_spec_path": sample_input,
                    "harness_config_path": sample_harness,
                    "provider_override": "agy",
                    "steward_hold_policy": "pause",
                    "output_dir": str(tmp_path / "paused_out"),
                },
            )
            assert res_paused.status_code == 201
            paused_body = res_paused.json()
            paused_run_id = paused_body["run_id"]
            assert paused_body["status"] == "PAUSED_HOLD"
            assert paused_body["overall_passed"] is False
            assert len(paused_body["active_holds"]) >= 1

            res_resolve_hold = await client.post(
                f"/api/v1/runs/{paused_run_id}/decisions",
                json={
                    "action": "APPROVE",
                    "selected_candidate_id": "hub_customer",
                    "steward_id": "steward-commerce",
                },
            )
            assert res_resolve_hold.status_code == 200
            res_synced_rep = await client.get(
                f"/api/v1/runs/{paused_run_id}/report",
                headers={"X-OpenDV-Role": "DATA_STEWARD"},
            )
            assert res_synced_rep.status_code == 200
            synced_body = res_synced_rep.json()
            assert synced_body["status"] == "COMPLETED"
            assert synced_body["overall_passed"] is True
            assert synced_body["active_holds"] == []
            assert len(synced_body["resolved_holds"]) >= 1
            assert not synced_body["observability_events"][-1]["layer1_story_banner"][
                "headline"
            ].startswith("[REDACTED")

            res_synced_public = await client.get(f"/api/v1/runs/{paused_run_id}/report")
            assert res_synced_public.status_code == 200
            assert res_synced_public.json()["observability_events"][-1]["layer1_story_banner"][
                "headline"
            ].startswith("[REDACTED")

            # 10. Stage 00 failure defaults to STAGE_00_ORCHESTRATION, WB-00, and REJECT-WITH-TRACE
            bad_spec = yaml.safe_load((repo_root / sample_input).read_text(encoding="utf-8"))
            bad_spec["graph_a_ontology_path"] = "ontology/does_not_exist.yaml"
            res_s0_fail = await client.post(
                "/api/v1/pipelines/runs",
                json={
                    "input_spec": bad_spec,
                    "provider_override": "agy",
                    "output_dir": str(tmp_path / "s0_fail_out"),
                },
            )
            assert res_s0_fail.status_code == 201
            s0_fail_body = res_s0_fail.json()
            s0_fail_id = s0_fail_body["run_id"]
            assert s0_fail_body["status"] == "FAILED"
            s0_run_dto = state.runs[s0_fail_id]
            assert s0_run_dto.current_stage_id == "STAGE_00_ORCHESTRATION"
            assert s0_run_dto.current_view_id == "WB-00"
            assert s0_run_dto.gate_scorecard.gate_0_profile_drift == "REJECT-WITH-TRACE"
            assert s0_run_dto.gate_scorecard.gate_5_duckdb_sandbox == "REJECT-WITH-TRACE"

    try:
        _run_async(_scenario())
    finally:
        set_pipeline_runner_override(None)


async def _collect_async_iter(aiter: Any) -> list[str]:
    return [item async for item in aiter]


def test_wp06_sdk_resolution_and_dynamic_project_run(
    tmp_path: Path,
) -> None:
    """Verify WP-06 dynamic project runs from bronze_sources with test-injected SDK shim."""
    from opendv_agent_harness.adapters.deterministic_adapter import (
        build_deterministic_agy_sdk_shim,
    )
    from opendv_agent_harness.pipeline import (
        run_agentic_vault_pipeline as orig_run_pipeline,
    )
    from opendv_service import set_pipeline_runner_override

    def shim_runner(*args, **kwargs):
        return orig_run_pipeline(
            *args,
            **{**kwargs, "sdk_module": build_deterministic_agy_sdk_shim()},
        )

    set_pipeline_runner_override(shim_runner)
    state = PlatformStateContainer(state_dir=tmp_path / "state")
    app = create_app(platform_state=state)
    ont_rel = (
        "examples/pipeline_samples/global_commerce_vault/ontology/commerce_graph_a_ontology.yaml"
    )
    src_cust_rel = (
        "examples/pipeline_samples/global_commerce_vault/sources/wave0_cold_start/crm_customers.csv"
    )
    src_ord_rel = (
        "examples/pipeline_samples/global_commerce_vault/sources/wave0_cold_start/erp_orders.csv"
    )

    async def _scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res_proj = await client.post(
                "/api/v1/projects",
                json={
                    "project_id": "proj-dynamic-commerce",
                    "display_name": "Dynamic Commerce Vault",
                    "domain_namespace": "global_commerce_vault",
                    "graph_a_ontology_path": ont_rel,
                    "bronze_sources": [src_cust_rel, src_ord_rel],
                },
            )
            assert res_proj.status_code == 201
            proj_body = res_proj.json()
            assert proj_body["project_id"] == "proj-dynamic-commerce"
            assert proj_body["graph_a_sha256"] != PE_DEMO_CONTEXT_REFS["graph_a_sha256"]

            res_run = await client.post(
                "/api/v1/runs",
                json={
                    "project_id": "proj-dynamic-commerce",
                    "harness_provider": "agy",
                },
            )
            assert res_run.status_code == 201
            run_body = res_run.json()
            dyn_run_id = run_body["run_id"]
            assert run_body["status"] == RunStatus.COMPLETED.value
            assert dyn_run_id in state.pipeline_reports
            assert state.pipeline_reports[dyn_run_id]["overall_passed"] is True

    try:
        _run_async(_scenario())
    finally:
        set_pipeline_runner_override(None)
